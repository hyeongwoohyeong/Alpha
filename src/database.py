"""SQLite 영속 계층.

run_research.py 가 결과를 쓰고, app.py 는 fetch_*() 만 호출하는 단일 진실 출처.

스키마:
    universe / runs / price_snapshot / news_raw / events /
    scores / stock_research / daily_brief / decision_log / performance_tracking

원칙:
- (date, ticker) 같은 자연 키는 PRIMARY KEY 로 UPSERT.
- 모든 분석 결과 테이블에 run_id 기록 → 재현 / 디버깅.
- JSON 컬럼은 TEXT 로 저장하고 dump_json / load_json 으로 직렬화.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from .utils import DATA_DIR, ensure_data_dir, get_logger, now_iso, today_kst

log = get_logger("db")

DB_PATH = DATA_DIR / "alpha.db"


# ---------------------------------------------------------------------------
# Open / Close
# ---------------------------------------------------------------------------

def open_db(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """DB 파일을 열고 외래키/스키마를 보장한다.

    DB 가 손상('database disk image is malformed' 등)된 경우 손상 파일
    (+ -wal/-shm/-journal)을 제거하고 빈 DB 를 재생성한다(자가복구) — 앱이
    손상 DB 때문에 죽지 않게. 데이터는 다음 파이프라인 실행이 다시 채운다.
    """
    ensure_data_dir()
    p = str(path or DB_PATH)

    def _connect_raw() -> sqlite3.Connection:
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        c.execute("PRAGMA journal_mode = WAL;")
        return c

    try:
        conn = _connect_raw()
    except sqlite3.DatabaseError as e:
        log.error("DB 손상 감지 (%s): %s — 손상 파일 제거 후 빈 DB 재생성", p, e)
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(p + suffix)
            except FileNotFoundError:
                pass
            except Exception as rm_e:
                log.warning("손상 파일 제거 실패 %s%s: %s", p, suffix, rm_e)
        conn = _connect_raw()
    init_schema(conn)
    return conn


@contextmanager
def db_session(path: str | os.PathLike | None = None) -> Iterator[sqlite3.Connection]:
    conn = open_db(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS universe (
        ticker          TEXT PRIMARY KEY,
        name_ko         TEXT,
        name_en         TEXT,
        sector          TEXT,
        industry        TEXT,
        theme           TEXT,
        category        TEXT,
        company_type    TEXT,
        is_active       INTEGER NOT NULL DEFAULT 1,
        updated_at      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id          TEXT PRIMARY KEY,
        started_at      TEXT NOT NULL,
        finished_at     TEXT,
        status          TEXT,
        universe_count  INTEGER,
        success_count   INTEGER,
        error_summary   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_snapshot (
        run_id          TEXT,
        date            TEXT NOT NULL,
        ticker          TEXT NOT NULL,
        current_price   REAL,
        previous_close  REAL,
        daily_return    REAL,
        return_5d       REAL,
        return_1m       REAL,
        return_3m       REAL,
        return_6m       REAL,
        return_1y       REAL,
        high_52w        REAL,
        low_52w         REAL,
        drawdown_from_52w_high REAL,
        volume          INTEGER,
        avg_volume_30d  REAL,
        market_cap      REAL,
        pe              REAL,
        forward_pe      REAL,
        pbr             REAL,
        psr             REAL,
        ev_ebitda       REAL,
        roe             REAL,
        fcf_yield       REAL,
        revenue_growth  REAL,
        operating_margin REAL,
        gross_margin    REAL,
        available       INTEGER NOT NULL DEFAULT 0,
        error           TEXT,
        fetched_at      TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_raw (
        news_id          TEXT PRIMARY KEY,
        run_id           TEXT,
        ticker           TEXT NOT NULL,
        title            TEXT,
        source           TEXT,
        published_at     TEXT,
        link             TEXT,
        summary          TEXT,
        importance_score REAL,
        source_quality   TEXT,
        staleness        TEXT,
        event_status_kw  TEXT,
        is_urgent        INTEGER DEFAULT 0,
        fetched_at       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id         TEXT PRIMARY KEY,
        run_id           TEXT,
        ticker           TEXT NOT NULL,
        event_title      TEXT,
        event_type       TEXT,
        event_date       TEXT,
        last_updated     TEXT,
        event_status     TEXT,
        source_quality   TEXT,
        source_count     INTEGER,
        confidence_level TEXT,
        staleness_flag   TEXT,
        thesis_impact    TEXT,
        summary          TEXT,
        investment_implication TEXT,
        check_items      TEXT,
        source_links     TEXT,
        member_news_ids  TEXT,
        is_curated       INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scores (
        run_id            TEXT,
        date              TEXT NOT NULL,
        ticker            TEXT NOT NULL,
        thesis_strength   REAL,
        evidence_strength REAL,
        price_opportunity REAL,
        financial_quality REAL,
        event_freshness   REAL,
        risk_control      REAL,
        final_score       REAL,
        company_type      TEXT,
        action_tag        TEXT,
        rationale         TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_research (
        run_id           TEXT,
        date             TEXT NOT NULL,
        ticker           TEXT NOT NULL,
        easy_explanation TEXT,
        core_thesis      TEXT,
        key_points       TEXT,
        key_risks        TEXT,
        check_items      TEXT,
        anti_thesis      TEXT,
        final_view       TEXT,
        research_quality_json TEXT,
        created_at       TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_brief (
        date             TEXT PRIMARY KEY,
        run_id           TEXT,
        headline         TEXT,
        market_environment TEXT,
        macro_issues     TEXT,
        top_stocks       TEXT,
        alerts           TEXT,
        check_items      TEXT,
        created_at       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_log (
        decision_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        date             TEXT NOT NULL,
        ticker           TEXT NOT NULL,
        action_tag       TEXT,
        final_score      REAL,
        price            REAL,
        reason           TEXT,
        user_note        TEXT,
        created_at       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS performance_tracking (
        decision_id        INTEGER NOT NULL,
        check_date         TEXT NOT NULL,
        return_1w          REAL,
        return_1m          REAL,
        return_3m          REAL,
        return_6m          REAL,
        relative_return_spy REAL,
        relative_return_qqq REAL,
        outcome_tag        TEXT,
        PRIMARY KEY (decision_id, check_date),
        FOREIGN KEY (decision_id) REFERENCES decision_log(decision_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_scores (
        run_id           TEXT,
        date             TEXT NOT NULL,
        ticker           TEXT NOT NULL,
        queue_type       TEXT NOT NULL,
        score            REAL,
        rank             INTEGER,
        signal_summary   TEXT,
        key_metrics_json TEXT,
        created_at       TEXT,
        PRIMARY KEY (date, ticker, queue_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promotion_candidates (
        run_id                TEXT,
        date                  TEXT NOT NULL,
        ticker                TEXT NOT NULL,
        name                  TEXT,
        queue_type            TEXT,
        discovery_score       REAL,
        promotion_score       REAL,
        reason                TEXT,
        latest_event_summary  TEXT,
        thesis_impact         TEXT,
        action_recommendation TEXT,
        promoted_to_deep_dive INTEGER NOT NULL DEFAULT 0,
        created_at            TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS article_summaries (
        article_url_hash      TEXT PRIMARY KEY,
        article_url           TEXT,
        title                 TEXT,
        source                TEXT,
        published_at          TEXT,
        ticker                TEXT,
        content_availability  TEXT,
        detailed_summary_ko   TEXT,
        investment_implication_ko TEXT,
        follow_up_items_ko    TEXT,
        thesis_impact         TEXT,
        confidence_level      TEXT,
        model_used            TEXT,
        token_estimate        INTEGER,
        created_at            TEXT,
        updated_at            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_ticker_date ON news_raw(ticker, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker, event_date)",
    "CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(date)",
    "CREATE INDEX IF NOT EXISTS idx_research_ticker_date ON stock_research(ticker, date)",
    "CREATE INDEX IF NOT EXISTS idx_decision_date ON decision_log(date, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_discovery_date ON discovery_scores(date, queue_type)",
    "CREATE INDEX IF NOT EXISTS idx_promotion_date ON promotion_candidates(date, promoted_to_deep_dive)",
    "CREATE INDEX IF NOT EXISTS idx_article_url ON article_summaries(article_url)",
    # ── Logic Auditor — 로직 버전 / 실험 / 개선 제안 ──────────────────
    """
    CREATE TABLE IF NOT EXISTS logic_versions (
        version_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        version_name      TEXT NOT NULL,
        effective_date    TEXT NOT NULL,
        change_summary    TEXT,
        score_weights_json TEXT,
        queue_rules_json  TEXT,
        filters_json      TEXT,
        created_by        TEXT,
        approved_by_user  INTEGER NOT NULL DEFAULT 0,
        rollback_available INTEGER NOT NULL DEFAULT 1,
        status            TEXT NOT NULL DEFAULT 'pending',
        parent_version_id INTEGER,
        created_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logic_experiments (
        experiment_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date        TEXT NOT NULL,
        end_date          TEXT,
        hypothesis        TEXT,
        change_applied    TEXT,
        control_version   TEXT,
        test_version      TEXT,
        target_metric     TEXT,
        result_json       TEXT,
        decision          TEXT,
        created_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logic_improvements (
        improvement_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        proposal_date     TEXT NOT NULL,
        category          TEXT NOT NULL,
        problem           TEXT,
        evidence_json     TEXT,
        proposed_change   TEXT,
        expected_effect   TEXT,
        risk              TEXT,
        auto_apply        INTEGER NOT NULL DEFAULT 0,
        approval_required INTEGER NOT NULL DEFAULT 1,
        status            TEXT NOT NULL DEFAULT 'pending',
        applied_version_id INTEGER,
        created_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logic_audit_reports (
        report_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date       TEXT NOT NULL,
        report_type       TEXT NOT NULL,
        body_json         TEXT,
        created_at        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_logic_versions_status ON logic_versions(status, effective_date)",
    "CREATE INDEX IF NOT EXISTS idx_logic_experiments_date ON logic_experiments(start_date)",
    "CREATE INDEX IF NOT EXISTS idx_logic_improvements_status ON logic_improvements(status, proposal_date)",
    "CREATE INDEX IF NOT EXISTS idx_logic_audit_type_date ON logic_audit_reports(report_type, report_date)",
    # ── Auto-Curation — LLM 기반 자동 큐레이션 캐시 ──────────────────
    """
    CREATE TABLE IF NOT EXISTS auto_curation (
        ticker            TEXT PRIMARY KEY,
        generated_at      TEXT NOT NULL,
        fields_json       TEXT NOT NULL,
        model_used        TEXT,
        token_input       INTEGER,
        token_output      INTEGER,
        cost_estimate_usd REAL,
        sources_json      TEXT,
        sec_filing_date   TEXT,
        data_confidence   TEXT,
        uncertainty_flags_json TEXT,
        version           INTEGER NOT NULL DEFAULT 1,
        created_at        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auto_curation_generated ON auto_curation(generated_at)",
    # ── 매크로 이슈 자동 생성 캐시 (RSS + LLM) ────────────────────────
    """
    CREATE TABLE IF NOT EXISTS macro_issues_auto (
        date              TEXT PRIMARY KEY,
        issues_json       TEXT NOT NULL,
        sources_count     INTEGER,
        model_used        TEXT,
        token_input       INTEGER,
        token_output      INTEGER,
        cost_estimate_usd REAL,
        generated_at      TEXT
    )
    """,
    # ── 시장 환경 3 블록 자동 생성 캐시 ───────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS market_env_auto (
        date              TEXT PRIMARY KEY,
        blocks_json       TEXT NOT NULL,
        model_used        TEXT,
        token_input       INTEGER,
        token_output      INTEGER,
        cost_estimate_usd REAL,
        generated_at      TEXT
    )
    """,
    # ── 전날 글로벌 브리핑 자동 생성 캐시 (Google News RSS + LLM) ──────
    """
    CREATE TABLE IF NOT EXISTS overnight_briefing_auto (
        date              TEXT PRIMARY KEY,
        briefing_json     TEXT NOT NULL,
        sources_count     INTEGER,
        model_used        TEXT,
        token_input       INTEGER,
        token_output      INTEGER,
        cost_estimate_usd REAL,
        generated_at      TEXT
    )
    """,
    # ── 금일 핵심 판단 LLM 합성 캐시 (룰 템플릿 대체) ──────────────────
    """
    CREATE TABLE IF NOT EXISTS daily_judgment_auto (
        date              TEXT PRIMARY KEY,
        judgment          TEXT NOT NULL,
        model_used        TEXT,
        token_input       INTEGER,
        token_output      INTEGER,
        cost_estimate_usd REAL,
        generated_at      TEXT
    )
    """,
    # ── Portfolio Regime — 일일 시장 국면 / Overheat Score ──────────────
    """
    CREATE TABLE IF NOT EXISTS market_regime (
        date                       TEXT PRIMARY KEY,
        market_overheat_score      REAL,
        current_regime             TEXT,
        valuation_stretch_score    REAL,
        sentiment_speculation_score REAL,
        market_concentration_score REAL,
        liquidity_credit_score     REAL,
        earnings_revision_risk_score REAL,
        technical_extension_score  REAL,
        cycle_psychology_score     REAL,
        buffett_opportunity_score  REAL,
        portfolio_mode             TEXT,
        recommended_beta_level     TEXT,
        commentary_ko              TEXT,
        created_at                 TEXT
    )
    """,
    # ── Portfolio Regime — Nasdaq 하락 단계별 투입 계획 ─────────────────
    """
    CREATE TABLE IF NOT EXISTS crash_deployment_plan (
        date                    TEXT PRIMARY KEY,
        qqq_drawdown_from_high   REAL,
        deployment_zone          TEXT,
        recommended_instrument   TEXT,
        suggested_action         TEXT,
        credit_stress_status     TEXT,
        liquidity_status         TEXT,
        commentary_ko            TEXT,
        created_at               TEXT
    )
    """,
    # ── Capital Efficiency (Phase 2) — 종목별 자본효율 점수 ─────────────
    """
    CREATE TABLE IF NOT EXISTS capital_efficiency_scores (
        date                       TEXT NOT NULL,
        ticker                      TEXT NOT NULL,
        capital_efficiency_score    REAL,
        expected_return_potential   REAL,
        time_to_target_probability  REAL,
        downside_risk_score         REAL,
        catalyst_visibility_score   REAL,
        qld_relative_score          REAL,
        liquidity_exit_score        REAL,
        qld_relative_view           TEXT,
        commentary_ko               TEXT,
        created_at                  TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    # ── Capital Efficiency (Phase 2) — 보유 종목 익절 필요성 ────────────
    """
    CREATE TABLE IF NOT EXISTS profit_protection (
        date                        TEXT NOT NULL,
        ticker                       TEXT NOT NULL,
        current_gain                 REAL,
        leverage_flag                INTEGER,
        valuation_stretch_score      REAL,
        technical_extension_score    REAL,
        narrative_crowding_score     REAL,
        profit_protection_score      REAL,
        suggested_action             TEXT,
        commentary_ko                TEXT,
        created_at                   TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    # ── Capital Efficiency (Phase 2) — 방어적 파킹 후보 ─────────────────
    """
    CREATE TABLE IF NOT EXISTS parking_candidates (
        date                            TEXT NOT NULL,
        ticker                           TEXT NOT NULL,
        name                             TEXT,
        parking_score                    REAL,
        beta                             REAL,
        drawdown_resilience_score        REAL,
        earnings_stability_score         REAL,
        valuation_reasonableness_score   REAL,
        dividend_buyback_score           REAL,
        technical_support_score          REAL,
        why_parking_ko                   TEXT,
        risk_ko                          TEXT,
        created_at                       TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    # ── Phase 4-A — 백테스트: 시장 일봉 캐시 ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS market_price_history (
        date        TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        adj_close   REAL,
        volume      INTEGER,
        source      TEXT,
        created_at  TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    # ── Phase 4-A — 백테스트: 전략별 성과 ──────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS backtest_results (
        strategy_name   TEXT NOT NULL,
        asset           TEXT NOT NULL,
        start_date      TEXT,
        end_date        TEXT,
        cagr            REAL,
        total_return    REAL,
        max_drawdown    REAL,
        sharpe          REAL,
        sortino         REAL,
        calmar          REAL,
        win_rate        REAL,
        recovery_time   REAL,
        details_json    TEXT,
        updated_at      TEXT,
        PRIMARY KEY (strategy_name, asset)
    )
    """,
    # ── Phase 4-A — 백테스트: regime/overheat 별 forward return ─────────
    """
    CREATE TABLE IF NOT EXISTS regime_forward_returns (
        date            TEXT NOT NULL,
        regime          TEXT NOT NULL,
        overheat_score  REAL,
        asset           TEXT NOT NULL,
        forward_1w      REAL,
        forward_1m      REAL,
        forward_3m      REAL,
        forward_6m      REAL,
        forward_12m     REAL,
        mdd_1m          REAL,
        mdd_3m          REAL,
        mdd_6m          REAL,
        updated_at      TEXT,
        PRIMARY KEY (date, regime, asset)
    )
    """,
    # ── Phase 4-B — Decision Journal: 사용자 결정 사후 채점 ─────────────
    """
    CREATE TABLE IF NOT EXISTS decision_grades (
        decision_id          TEXT NOT NULL,
        milestone            TEXT NOT NULL,
        graded_date          TEXT,
        price_at_decision    REAL,
        price_at_milestone   REAL,
        return_pct           REAL,
        benchmark_return_pct REAL,
        relative_pct         REAL,
        grade                TEXT,
        grade_note           TEXT,
        created_at           TEXT,
        PRIMARY KEY (decision_id, milestone)
    )
    """,
    # ── 보유 종목 브리핑 — 사용자 보유 종목 일일 LLM 리서치 브리핑 ────────
    """
    CREATE TABLE IF NOT EXISTS holdings_briefing (
        date              TEXT NOT NULL,
        ticker            TEXT NOT NULL,
        name              TEXT,
        exposure_theme    TEXT,
        summary_ko        TEXT,
        key_drivers_ko    TEXT,
        risks_ko          TEXT,
        portfolio_note_ko TEXT,
        model_used        TEXT,
        created_at        TEXT,
        PRIMARY KEY (date, ticker)
    )
    """,
    # ── 백테스트 기반 오늘의 대응 — 퀀트 처방 일일 행 ───────────────────
    """
    CREATE TABLE IF NOT EXISTS backtest_solution (
        date        TEXT PRIMARY KEY,
        headline    TEXT,
        data_mode   TEXT,
        items_json  TEXT,
        caveat      TEXT,
        created_at  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mph_ticker_date ON market_price_history(ticker, date)",
    "CREATE INDEX IF NOT EXISTS idx_rfr_regime ON regime_forward_returns(regime, asset)",
    "CREATE INDEX IF NOT EXISTS idx_dg_decision ON decision_grades(decision_id)",
)


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for stmt in _SCHEMA_STATEMENTS:
        cur.execute(stmt)
    # 새 컬럼 idempotent 추가 (기존 DB도 호환)
    _ALTER_STATEMENTS = (
        "ALTER TABLE news_raw ADD COLUMN detailed_summary_ko TEXT",
        "ALTER TABLE news_raw ADD COLUMN investment_implication_ko TEXT",
        "ALTER TABLE news_raw ADD COLUMN thesis_impact_ko TEXT",
        "ALTER TABLE news_raw ADD COLUMN confidence_level_ko TEXT",
        "ALTER TABLE news_raw ADD COLUMN body_excerpt TEXT",
        "ALTER TABLE news_raw ADD COLUMN key_points_ko TEXT",  # legacy: JSON list
        "ALTER TABLE news_raw ADD COLUMN follow_up_items_ko TEXT",  # JSON list
        "ALTER TABLE news_raw ADD COLUMN content_availability TEXT",
        # stock_research — Earnings Quality / Moat / Strategic Lens
        "ALTER TABLE stock_research ADD COLUMN earnings_quality_json TEXT",
        "ALTER TABLE stock_research ADD COLUMN strategic_lens_json TEXT",
        # stock_research — Alpha Score 통합 점수
        "ALTER TABLE stock_research ADD COLUMN alpha_score_json TEXT",
        # stock_research — Bottleneck Thesis (해당 종목만)
        "ALTER TABLE stock_research ADD COLUMN bottleneck_thesis_json TEXT",
        # ── Logic Auditor — decision_log 확장 ─────────────────────
        # 기존 decision_log 는 사용자 수동 결정 저장용 — Auditor 는 동일 테이블에
        # Alpha 자동 판단도 함께 기록 (auto_recorded=1 로 구분)
        "ALTER TABLE decision_log ADD COLUMN run_id TEXT",
        "ALTER TABLE decision_log ADD COLUMN company_name TEXT",
        "ALTER TABLE decision_log ADD COLUMN alpha_score REAL",
        "ALTER TABLE decision_log ADD COLUMN alpha_rating TEXT",
        "ALTER TABLE decision_log ADD COLUMN queue_type TEXT",
        "ALTER TABLE decision_log ADD COLUMN thesis_type TEXT",
        "ALTER TABLE decision_log ADD COLUMN core_thesis TEXT",
        "ALTER TABLE decision_log ADD COLUMN key_risks_json TEXT",
        "ALTER TABLE decision_log ADD COLUMN follow_up_items_json TEXT",
        "ALTER TABLE decision_log ADD COLUMN entry_price REAL",
        "ALTER TABLE decision_log ADD COLUMN spy_price REAL",
        "ALTER TABLE decision_log ADD COLUMN qqq_price REAL",
        "ALTER TABLE decision_log ADD COLUMN qld_price REAL",
        "ALTER TABLE decision_log ADD COLUMN data_confidence TEXT",
        "ALTER TABLE decision_log ADD COLUMN reason_json TEXT",
        "ALTER TABLE decision_log ADD COLUMN auto_recorded INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE decision_log ADD COLUMN logic_version TEXT",
        # ── Logic Auditor — performance_tracking 확장 ─────────────
        "ALTER TABLE performance_tracking ADD COLUMN holding_period TEXT",
        "ALTER TABLE performance_tracking ADD COLUMN entry_price REAL",
        "ALTER TABLE performance_tracking ADD COLUMN current_price REAL",
        "ALTER TABLE performance_tracking ADD COLUMN absolute_return REAL",
        "ALTER TABLE performance_tracking ADD COLUMN spy_return REAL",
        "ALTER TABLE performance_tracking ADD COLUMN qqq_return REAL",
        "ALTER TABLE performance_tracking ADD COLUMN qld_return REAL",
        "ALTER TABLE performance_tracking ADD COLUMN excess_return_vs_spy REAL",
        "ALTER TABLE performance_tracking ADD COLUMN excess_return_vs_qqq REAL",
        "ALTER TABLE performance_tracking ADD COLUMN excess_return_vs_qld REAL",
        "ALTER TABLE performance_tracking ADD COLUMN max_drawdown_since_decision REAL",
        "ALTER TABLE performance_tracking ADD COLUMN max_gain_since_decision REAL",
        "ALTER TABLE performance_tracking ADD COLUMN volatility REAL",
        "ALTER TABLE performance_tracking ADD COLUMN hit_status TEXT",
    )
    for s in _ALTER_STATEMENTS:
        try:
            cur.execute(s)
        except sqlite3.OperationalError:
            pass  # 이미 컬럼이 존재
    conn.commit()


# ---------------------------------------------------------------------------
# JSON 헬퍼
# ---------------------------------------------------------------------------

def dump_json(value) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def load_json(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# run_id 관리
# ---------------------------------------------------------------------------

def create_run_id() -> str:
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


def log_run_start(conn: sqlite3.Connection, run_id: str, universe_count: int = 0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, started_at, status, universe_count) "
        "VALUES (?, ?, 'in_progress', ?)",
        (run_id, now_iso(), universe_count),
    )
    conn.commit()


def log_run_finish(
    conn: sqlite3.Connection,
    run_id: str,
    status: str = "success",
    success_count: int | None = None,
    error_summary: str | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, success_count=?, error_summary=? WHERE run_id=?",
        (now_iso(), status, success_count, error_summary, run_id),
    )
    conn.commit()


def fetch_latest_successful_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT * FROM runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1"
    )
    return cur.fetchone()


def fetch_latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def upsert_universe(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO universe (ticker, name_ko, name_en, sector, industry,
                                  theme, category, company_type, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name_ko=excluded.name_ko,
                name_en=excluded.name_en,
                sector=excluded.sector,
                industry=excluded.industry,
                theme=excluded.theme,
                category=excluded.category,
                company_type=excluded.company_type,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (
                r["ticker"],
                r.get("name_ko"),
                r.get("name_en"),
                r.get("sector"),
                r.get("industry"),
                r.get("theme"),
                r.get("category"),
                r.get("company_type"),
                int(r.get("is_active", 1)),
                now_iso(),
            ),
        )
        n += 1
    conn.commit()
    return n


def fetch_universe(conn: sqlite3.Connection, only_active: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM universe"
    if only_active:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY ticker"
    return list(conn.execute(sql))


def seed_universe_from_csv(
    conn: sqlite3.Connection,
    csv_path: str | os.PathLike | None = None,
) -> int:
    """data/universe.csv 의 41 종목을 universe 테이블에 시드.

    company_type 은 curated.COMPANY_TYPE_BY_TICKER 에서 결합.
    """
    from .universe import load_universe as _load_csv
    from .curated import COMPANY_TYPE_BY_TICKER

    rows = _load_csv()  # [{ticker, name_ko, name_en, theme, category}, ...]
    payload = []
    for r in rows:
        payload.append(
            {
                **r,
                "sector": r.get("category"),       # 사용자 명세에 sector 컬럼 — category 매핑
                "industry": r.get("theme"),
                "company_type": COMPANY_TYPE_BY_TICKER.get(r["ticker"], "Structural Growth"),
                "is_active": 1,
            }
        )
    return upsert_universe(conn, payload)


# ---------------------------------------------------------------------------
# price_snapshot
# ---------------------------------------------------------------------------

_PRICE_COLS = (
    "current_price", "previous_close", "daily_return",
    "return_5d", "return_1m", "return_3m", "return_6m", "return_1y",
    "high_52w", "low_52w", "drawdown_from_52w_high",
    "volume", "avg_volume_30d", "market_cap",
    "pe", "forward_pe", "pbr", "psr", "ev_ebitda",
    "roe", "fcf_yield", "revenue_growth",
    "operating_margin", "gross_margin",
)


def upsert_price_snapshot(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    snapshots: Iterable[dict[str, Any]],
) -> int:
    """snapshots: list of {"ticker", "available", "error", + market_data fields}.

    market_data 의 키 매핑:
      "5d_return" → return_5d 등 (밑줄 시작 못하므로)
    """
    key_alias = {
        "5d_return": "return_5d",
        "1m_return": "return_1m",
        "3m_return": "return_3m",
        "6m_return": "return_6m",
        "1y_return": "return_1y",
        "52w_high": "high_52w",
        "52w_low": "low_52w",
        "trailing_pe": "pe",
    }

    cols = ["run_id", "date", "ticker", "available", "error", "fetched_at"] + list(_PRICE_COLS)
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker"))

    sql = (
        f"INSERT INTO price_snapshot ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )

    n = 0
    for s in snapshots:
        # market_data 키 → DB 컬럼 키 매핑
        mapped: dict[str, Any] = {}
        for k, v in s.items():
            mapped[key_alias.get(k, k)] = v

        params = [
            run_id,
            date_iso,
            mapped.get("ticker"),
            int(bool(mapped.get("available"))),
            mapped.get("error"),
            mapped.get("fetched_at") or now_iso(),
        ]
        for c in _PRICE_COLS:
            params.append(mapped.get(c))
        conn.execute(sql, params)
        n += 1
    conn.commit()
    return n


def fetch_latest_price_snapshot(
    conn: sqlite3.Connection,
    ticker: str | None = None,
    date: str | None = None,
) -> list[sqlite3.Row]:
    if not date:
        cur = conn.execute("SELECT MAX(date) AS d FROM price_snapshot")
        date_row = cur.fetchone()
        date = date_row["d"] if date_row and date_row["d"] else None
    if not date:
        return []
    if ticker:
        return list(
            conn.execute(
                "SELECT * FROM price_snapshot WHERE date=? AND ticker=?",
                (date, ticker),
            )
        )
    return list(conn.execute("SELECT * FROM price_snapshot WHERE date=?", (date,)))


# ---------------------------------------------------------------------------
# news_raw
# ---------------------------------------------------------------------------

def make_news_id(ticker: str, link: str | None, title: str | None, published_at: str | None) -> str:
    base = f"{ticker}|{link or ''}|{title or ''}|{published_at or ''}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def upsert_news(
    conn: sqlite3.Connection,
    run_id: str,
    news_items: Iterable[dict[str, Any]],
) -> int:
    n = 0
    for it in news_items:
        nid = it.get("news_id") or make_news_id(
            it.get("ticker", "?"),
            it.get("link"),
            it.get("title"),
            it.get("published_at"),
        )
        conn.execute(
            """
            INSERT INTO news_raw (
                news_id, run_id, ticker, title, source, published_at, link, summary,
                importance_score, source_quality, staleness, event_status_kw, is_urgent,
                fetched_at,
                detailed_summary_ko, investment_implication_ko, thesis_impact_ko,
                confidence_level_ko, body_excerpt, key_points_ko,
                follow_up_items_ko, content_availability
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(news_id) DO UPDATE SET
                run_id=excluded.run_id,
                title=excluded.title,
                source=excluded.source,
                published_at=excluded.published_at,
                link=excluded.link,
                summary=excluded.summary,
                importance_score=excluded.importance_score,
                source_quality=excluded.source_quality,
                staleness=excluded.staleness,
                event_status_kw=excluded.event_status_kw,
                is_urgent=excluded.is_urgent,
                fetched_at=excluded.fetched_at,
                detailed_summary_ko=COALESCE(excluded.detailed_summary_ko, news_raw.detailed_summary_ko),
                investment_implication_ko=COALESCE(excluded.investment_implication_ko, news_raw.investment_implication_ko),
                thesis_impact_ko=COALESCE(excluded.thesis_impact_ko, news_raw.thesis_impact_ko),
                confidence_level_ko=COALESCE(excluded.confidence_level_ko, news_raw.confidence_level_ko),
                body_excerpt=COALESCE(excluded.body_excerpt, news_raw.body_excerpt),
                key_points_ko=COALESCE(excluded.key_points_ko, news_raw.key_points_ko),
                follow_up_items_ko=COALESCE(excluded.follow_up_items_ko, news_raw.follow_up_items_ko),
                content_availability=COALESCE(excluded.content_availability, news_raw.content_availability)
            """,
            (
                nid,
                run_id,
                it.get("ticker"),
                it.get("title"),
                it.get("source"),
                it.get("published_at"),
                it.get("link"),
                it.get("summary"),
                it.get("importance_score"),
                it.get("source_quality"),
                it.get("staleness"),
                it.get("event_status"),
                int(bool(it.get("is_urgent"))),
                now_iso(),
                it.get("detailed_summary_ko"),
                it.get("investment_implication_ko"),
                it.get("thesis_impact_ko") or it.get("thesis_impact"),
                it.get("confidence_level_ko") or it.get("confidence_level") or it.get("confidence"),
                it.get("body_excerpt"),
                dump_json(it.get("key_points_ko")) if it.get("key_points_ko") else None,
                dump_json(it.get("follow_up_items_ko")) if it.get("follow_up_items_ko") else None,
                it.get("content_availability"),
            ),
        )
        n += 1
    conn.commit()
    return n


def update_news_summary(
    conn: sqlite3.Connection,
    news_id: str,
    summary_payload: dict[str, Any],
) -> None:
    """이미 저장된 news_raw 행에 한국어 요약 필드만 갱신."""
    conn.execute(
        """
        UPDATE news_raw SET
            detailed_summary_ko = ?,
            investment_implication_ko = ?,
            thesis_impact_ko = ?,
            confidence_level_ko = ?,
            body_excerpt = ?,
            key_points_ko = ?,
            follow_up_items_ko = ?,
            content_availability = ?
        WHERE news_id = ?
        """,
        (
            summary_payload.get("detailed_summary_ko"),
            summary_payload.get("investment_implication_ko"),
            summary_payload.get("thesis_impact_ko"),
            summary_payload.get("confidence_level_ko"),
            summary_payload.get("body_excerpt"),
            dump_json(summary_payload.get("key_points_ko")) if summary_payload.get("key_points_ko") else None,
            dump_json(summary_payload.get("follow_up_items_ko")) if summary_payload.get("follow_up_items_ko") else None,
            summary_payload.get("content_availability"),
            news_id,
        ),
    )


def fetch_news_by_id(conn: sqlite3.Connection, news_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM news_raw WHERE news_id = ?", (news_id,)).fetchone()


def fetch_news_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    limit: int = 5,
    since_days: int | None = 90,
) -> list[sqlite3.Row]:
    if since_days is not None:
        cutoff = (_dt.date.today() - _dt.timedelta(days=since_days)).isoformat()
        return list(
            conn.execute(
                "SELECT * FROM news_raw WHERE ticker=? AND published_at >= ? "
                "ORDER BY published_at DESC LIMIT ?",
                (ticker, cutoff, limit),
            )
        )
    return list(
        conn.execute(
            "SELECT * FROM news_raw WHERE ticker=? ORDER BY published_at DESC LIMIT ?",
            (ticker, limit),
        )
    )


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def make_event_id(ticker: str, event_title: str, event_date: str | None) -> str:
    base = f"{ticker}|{event_title or ''}|{event_date or ''}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:12]


def upsert_event(conn: sqlite3.Connection, run_id: str, ev: dict[str, Any]) -> str:
    eid = ev.get("event_id") or make_event_id(
        ev.get("ticker", "?"), ev.get("event_title") or ev.get("type", ""), ev.get("event_date") or ev.get("date")
    )
    conn.execute(
        """
        INSERT INTO events (
            event_id, run_id, ticker, event_title, event_type, event_date,
            last_updated, event_status, source_quality, source_count,
            confidence_level, staleness_flag, thesis_impact,
            summary, investment_implication, check_items, source_links,
            member_news_ids, is_curated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            run_id=excluded.run_id,
            event_title=excluded.event_title,
            event_type=excluded.event_type,
            event_date=excluded.event_date,
            last_updated=excluded.last_updated,
            event_status=excluded.event_status,
            source_quality=excluded.source_quality,
            source_count=excluded.source_count,
            confidence_level=excluded.confidence_level,
            staleness_flag=excluded.staleness_flag,
            thesis_impact=excluded.thesis_impact,
            summary=excluded.summary,
            investment_implication=excluded.investment_implication,
            check_items=excluded.check_items,
            source_links=excluded.source_links,
            member_news_ids=excluded.member_news_ids,
            is_curated=excluded.is_curated
        """,
        (
            eid, run_id, ev.get("ticker"),
            ev.get("event_title") or ev.get("type"),
            ev.get("event_type") or ev.get("type"),
            ev.get("event_date") or ev.get("date"),
            ev.get("last_updated"),
            ev.get("event_status") or ev.get("status"),
            ev.get("source_quality"),
            ev.get("source_count") or len(ev.get("sources") or []) or 1,
            ev.get("confidence_level") or ev.get("confidence"),
            ev.get("staleness_flag") or ev.get("staleness_label") or ev.get("staleness"),
            ev.get("thesis_impact"),
            ev.get("summary"),
            ev.get("investment_implication") or ev.get("impact"),
            dump_json(ev.get("check_items") or ev.get("check")),
            dump_json(ev.get("source_links") or ev.get("sources") or []),
            dump_json(ev.get("member_news_ids") or []),
            int(bool(ev.get("is_curated"))),
        ),
    )
    conn.commit()
    return eid


def fetch_events_for_ticker(
    conn: sqlite3.Connection, ticker: str, limit: int = 10
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM events WHERE ticker=? ORDER BY event_date DESC, last_updated DESC LIMIT ?",
            (ticker, limit),
        )
    )


# ---------------------------------------------------------------------------
# scores
# ---------------------------------------------------------------------------

def upsert_score(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    ticker: str,
    scores: dict[str, Any],
    company_type: str,
    action_tag: str,
    rationale: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO scores (run_id, date, ticker, thesis_strength, evidence_strength,
                            price_opportunity, financial_quality, event_freshness,
                            risk_control, final_score, company_type, action_tag, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, ticker) DO UPDATE SET
            run_id=excluded.run_id,
            thesis_strength=excluded.thesis_strength,
            evidence_strength=excluded.evidence_strength,
            price_opportunity=excluded.price_opportunity,
            financial_quality=excluded.financial_quality,
            event_freshness=excluded.event_freshness,
            risk_control=excluded.risk_control,
            final_score=excluded.final_score,
            company_type=excluded.company_type,
            action_tag=excluded.action_tag,
            rationale=excluded.rationale
        """,
        (
            run_id, date_iso, ticker,
            scores.get("thesis"), scores.get("evidence"),
            scores.get("price"), scores.get("financial"),
            scores.get("event"), scores.get("risk"),
            scores.get("final_score"),
            company_type, action_tag, rationale,
        ),
    )


def fetch_scores_for_date(
    conn: sqlite3.Connection, date_iso: str | None = None
) -> list[sqlite3.Row]:
    if not date_iso:
        cur = conn.execute("SELECT MAX(date) AS d FROM scores")
        r = cur.fetchone()
        date_iso = r["d"] if r and r["d"] else None
    if not date_iso:
        return []
    return list(conn.execute("SELECT * FROM scores WHERE date=?", (date_iso,)))


def fetch_score_for_ticker(
    conn: sqlite3.Connection, ticker: str, date_iso: str | None = None
) -> sqlite3.Row | None:
    if not date_iso:
        cur = conn.execute(
            "SELECT * FROM scores WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM scores WHERE date=? AND ticker=?",
            (date_iso, ticker),
        )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# stock_research
# ---------------------------------------------------------------------------

def upsert_stock_research(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    ticker: str,
    research: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO stock_research (run_id, date, ticker, easy_explanation, core_thesis,
                                    key_points, key_risks, check_items, anti_thesis,
                                    final_view, research_quality_json, created_at,
                                    earnings_quality_json, strategic_lens_json,
                                    alpha_score_json, bottleneck_thesis_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, ticker) DO UPDATE SET
            run_id=excluded.run_id,
            easy_explanation=excluded.easy_explanation,
            core_thesis=excluded.core_thesis,
            key_points=excluded.key_points,
            key_risks=excluded.key_risks,
            check_items=excluded.check_items,
            anti_thesis=excluded.anti_thesis,
            final_view=excluded.final_view,
            research_quality_json=excluded.research_quality_json,
            created_at=excluded.created_at,
            earnings_quality_json=excluded.earnings_quality_json,
            strategic_lens_json=excluded.strategic_lens_json,
            alpha_score_json=excluded.alpha_score_json,
            bottleneck_thesis_json=excluded.bottleneck_thesis_json
        """,
        (
            run_id, date_iso, ticker,
            research.get("easy_explanation"),
            research.get("core_thesis"),
            dump_json(research.get("key_points")),
            dump_json(research.get("key_risks")),
            dump_json(research.get("check_items")),
            dump_json(research.get("anti_thesis")),
            research.get("final_view"),
            dump_json(research.get("research_quality")),
            now_iso(),
            dump_json(research.get("earnings_quality")),
            dump_json(research.get("strategic_lens")),
            dump_json(research.get("alpha_score")),
            dump_json(research.get("bottleneck_thesis")),
        ),
    )


def fetch_stock_research(
    conn: sqlite3.Connection, ticker: str, date_iso: str | None = None
) -> sqlite3.Row | None:
    if date_iso:
        cur = conn.execute(
            "SELECT * FROM stock_research WHERE date=? AND ticker=?",
            (date_iso, ticker),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM stock_research WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
    return cur.fetchone()


def fetch_latest_stock_research_all(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """가장 최신 일자의 모든 stock_research 행 반환 (alpha_score 비교 / Outsider 선정용).

    같은 종목이 여러 날 저장돼 있을 경우 가장 최근 일자 한 행만 선택.
    """
    sql = """
        SELECT s.* FROM stock_research s
        INNER JOIN (
            SELECT ticker, MAX(date) AS max_date
            FROM stock_research
            GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql)
    return list(cur.fetchall())


# ---------------------------------------------------------------------------
# daily_brief
# ---------------------------------------------------------------------------

def upsert_daily_brief(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    brief: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO daily_brief (date, run_id, headline, market_environment,
                                 macro_issues, top_stocks, alerts, check_items, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            run_id=excluded.run_id,
            headline=excluded.headline,
            market_environment=excluded.market_environment,
            macro_issues=excluded.macro_issues,
            top_stocks=excluded.top_stocks,
            alerts=excluded.alerts,
            check_items=excluded.check_items,
            created_at=excluded.created_at
        """,
        (
            date_iso, run_id,
            brief.get("headline"),
            dump_json(brief.get("market_environment")),
            dump_json(brief.get("overnight_briefing")),
            dump_json(brief.get("top_stocks")),
            dump_json(brief.get("alerts")),
            dump_json(brief.get("check_items")),
            now_iso(),
        ),
    )


def fetch_latest_brief(conn: sqlite3.Connection) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM daily_brief ORDER BY date DESC LIMIT 1")
    return cur.fetchone()


# ---------------------------------------------------------------------------
# decision_log / performance_tracking
# ---------------------------------------------------------------------------

def insert_decision(
    conn: sqlite3.Connection,
    ticker: str,
    action_tag: str,
    final_score: float | None,
    price: float | None,
    reason: str = "",
    user_note: str = "",
    date_iso: str | None = None,
) -> int:
    """[하위 호환] 사용자 수동 결정 저장. Auditor 자동 기록은 record_alpha_decision 사용."""
    cur = conn.execute(
        """
        INSERT INTO decision_log (date, ticker, action_tag, final_score, price,
                                  reason, user_note, auto_recorded, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            date_iso or today_kst(),
            ticker,
            action_tag,
            final_score,
            price,
            reason,
            user_note,
            now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def record_alpha_decision(
    conn: sqlite3.Connection,
    *,
    run_id: str | None,
    date_iso: str,
    ticker: str,
    company_name: str | None,
    action_tag: str | None,
    final_score: float | None,
    alpha_score: float | None,
    alpha_rating: str | None,
    queue_type: str | None,
    thesis_type: str | None,
    core_thesis: str | None,
    key_risks: list[str] | None,
    follow_up_items: list[str] | None,
    entry_price: float | None,
    spy_price: float | None,
    qqq_price: float | None,
    qld_price: float | None,
    data_confidence: str | None,
    reason: dict[str, Any] | None,
    logic_version: str | None = "v1.0",
) -> int:
    """Alpha 가 매일 내린 자동 판단 1 건을 decision_log 에 기록.

    같은 (date, ticker, queue_type, action_tag) 조합은 하루에 1회만 — 중복 저장 방지.
    """
    # 중복 방지 — 같은 날 같은 종목 같은 액션은 1회
    existing = conn.execute(
        "SELECT decision_id FROM decision_log "
        "WHERE date=? AND ticker=? AND COALESCE(action_tag,'')=COALESCE(?,'') "
        "AND COALESCE(queue_type,'')=COALESCE(?,'') AND auto_recorded=1",
        (date_iso, ticker, action_tag or "", queue_type or ""),
    ).fetchone()
    if existing:
        return int(existing[0])

    cur = conn.execute(
        """
        INSERT INTO decision_log (
            date, ticker, action_tag, final_score, price, reason, user_note,
            run_id, company_name, alpha_score, alpha_rating, queue_type,
            thesis_type, core_thesis, key_risks_json, follow_up_items_json,
            entry_price, spy_price, qqq_price, qld_price, data_confidence,
            reason_json, auto_recorded, logic_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            date_iso, ticker, action_tag, final_score,
            entry_price,           # price (legacy column)
            (core_thesis or "")[:500],  # reason (legacy column)
            run_id, company_name, alpha_score, alpha_rating, queue_type,
            thesis_type, core_thesis,
            dump_json(key_risks), dump_json(follow_up_items),
            entry_price, spy_price, qqq_price, qld_price,
            data_confidence, dump_json(reason),
            logic_version, now_iso(),
        ),
    )
    return int(cur.lastrowid)


def fetch_decisions(
    conn: sqlite3.Connection,
    limit: int = 100,
    *,
    auto_only: bool | None = None,
    since_date: str | None = None,
    ticker: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM decision_log WHERE 1=1"
    args: list[Any] = []
    if auto_only is True:
        sql += " AND auto_recorded=1"
    elif auto_only is False:
        sql += " AND auto_recorded=0"
    if since_date:
        sql += " AND date >= ?"
        args.append(since_date)
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker)
    sql += " ORDER BY date DESC, decision_id DESC LIMIT ?"
    args.append(limit)
    return list(conn.execute(sql, args))


def fetch_decisions_for_period(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str | None = None,
) -> list[sqlite3.Row]:
    """기간 내 자동 기록 의사결정 전체."""
    if end_date:
        return list(conn.execute(
            "SELECT * FROM decision_log WHERE auto_recorded=1 "
            "AND date BETWEEN ? AND ? ORDER BY date, ticker",
            (start_date, end_date),
        ))
    return list(conn.execute(
        "SELECT * FROM decision_log WHERE auto_recorded=1 AND date >= ? "
        "ORDER BY date, ticker",
        (start_date,),
    ))


def upsert_performance(
    conn: sqlite3.Connection,
    decision_id: int,
    check_date: str,
    metrics: dict[str, Any],
) -> None:
    """holding_period 별 성과 1 건 upsert.

    primary key = (decision_id, check_date) — check_date 가 holding period 의
    실제 측정일을 의미.
    """
    conn.execute(
        """
        INSERT INTO performance_tracking (decision_id, check_date,
            return_1w, return_1m, return_3m, return_6m,
            relative_return_spy, relative_return_qqq, outcome_tag,
            holding_period, entry_price, current_price, absolute_return,
            spy_return, qqq_return, qld_return,
            excess_return_vs_spy, excess_return_vs_qqq, excess_return_vs_qld,
            max_drawdown_since_decision, max_gain_since_decision, volatility,
            hit_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id, check_date) DO UPDATE SET
            return_1w=excluded.return_1w,
            return_1m=excluded.return_1m,
            return_3m=excluded.return_3m,
            return_6m=excluded.return_6m,
            relative_return_spy=excluded.relative_return_spy,
            relative_return_qqq=excluded.relative_return_qqq,
            outcome_tag=excluded.outcome_tag,
            holding_period=excluded.holding_period,
            entry_price=excluded.entry_price,
            current_price=excluded.current_price,
            absolute_return=excluded.absolute_return,
            spy_return=excluded.spy_return,
            qqq_return=excluded.qqq_return,
            qld_return=excluded.qld_return,
            excess_return_vs_spy=excluded.excess_return_vs_spy,
            excess_return_vs_qqq=excluded.excess_return_vs_qqq,
            excess_return_vs_qld=excluded.excess_return_vs_qld,
            max_drawdown_since_decision=excluded.max_drawdown_since_decision,
            max_gain_since_decision=excluded.max_gain_since_decision,
            volatility=excluded.volatility,
            hit_status=excluded.hit_status
        """,
        (
            decision_id, check_date,
            metrics.get("return_1w"),
            metrics.get("return_1m"),
            metrics.get("return_3m"),
            metrics.get("return_6m"),
            metrics.get("relative_return_spy"),
            metrics.get("relative_return_qqq"),
            metrics.get("outcome_tag"),
            metrics.get("holding_period"),
            metrics.get("entry_price"),
            metrics.get("current_price"),
            metrics.get("absolute_return"),
            metrics.get("spy_return"),
            metrics.get("qqq_return"),
            metrics.get("qld_return"),
            metrics.get("excess_return_vs_spy"),
            metrics.get("excess_return_vs_qqq"),
            metrics.get("excess_return_vs_qld"),
            metrics.get("max_drawdown_since_decision"),
            metrics.get("max_gain_since_decision"),
            metrics.get("volatility"),
            metrics.get("hit_status"),
        ),
    )


def fetch_performance_join_decisions(
    conn: sqlite3.Connection,
    *,
    holding_period: str | None = None,
    queue_type: str | None = None,
    action_tag: str | None = None,
    since_date: str | None = None,
) -> list[sqlite3.Row]:
    """decision_log JOIN performance_tracking — Auditor 분석용."""
    sql = """
        SELECT d.*,
               p.holding_period, p.check_date AS perf_check_date,
               p.absolute_return, p.spy_return, p.qqq_return, p.qld_return,
               p.excess_return_vs_spy, p.excess_return_vs_qqq, p.excess_return_vs_qld,
               p.max_drawdown_since_decision, p.max_gain_since_decision,
               p.volatility, p.hit_status, p.outcome_tag
        FROM decision_log d
        JOIN performance_tracking p ON d.decision_id = p.decision_id
        WHERE d.auto_recorded = 1
    """
    args: list[Any] = []
    if holding_period:
        sql += " AND p.holding_period = ?"
        args.append(holding_period)
    if queue_type:
        sql += " AND d.queue_type = ?"
        args.append(queue_type)
    if action_tag:
        sql += " AND d.action_tag = ?"
        args.append(action_tag)
    if since_date:
        sql += " AND d.date >= ?"
        args.append(since_date)
    sql += " ORDER BY d.date DESC, d.decision_id DESC"
    return list(conn.execute(sql, args))


# ---------------------------------------------------------------------------
# Logic Auditor — versions / experiments / improvements / reports
# ---------------------------------------------------------------------------

def insert_logic_version(
    conn: sqlite3.Connection,
    *,
    version_name: str,
    effective_date: str,
    change_summary: str | None = None,
    score_weights: dict | None = None,
    queue_rules: dict | None = None,
    filters: dict | None = None,
    created_by: str = "auditor",
    approved_by_user: bool = False,
    parent_version_id: int | None = None,
    status: str = "pending",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO logic_versions (version_name, effective_date, change_summary,
            score_weights_json, queue_rules_json, filters_json,
            created_by, approved_by_user, status, parent_version_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_name, effective_date, change_summary,
            dump_json(score_weights), dump_json(queue_rules), dump_json(filters),
            created_by, 1 if approved_by_user else 0, status,
            parent_version_id, now_iso(),
        ),
    )
    return int(cur.lastrowid)


def fetch_logic_versions(
    conn: sqlite3.Connection, status: str | None = None,
) -> list[sqlite3.Row]:
    if status:
        return list(conn.execute(
            "SELECT * FROM logic_versions WHERE status=? ORDER BY effective_date DESC",
            (status,),
        ))
    return list(conn.execute(
        "SELECT * FROM logic_versions ORDER BY effective_date DESC",
    ))


def update_logic_version_status(
    conn: sqlite3.Connection, version_id: int, status: str,
    approved_by_user: bool | None = None,
) -> None:
    if approved_by_user is None:
        conn.execute(
            "UPDATE logic_versions SET status=? WHERE version_id=?",
            (status, version_id),
        )
    else:
        conn.execute(
            "UPDATE logic_versions SET status=?, approved_by_user=? WHERE version_id=?",
            (status, 1 if approved_by_user else 0, version_id),
        )


def insert_logic_experiment(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str | None,
    hypothesis: str,
    change_applied: str,
    control_version: str,
    test_version: str,
    target_metric: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO logic_experiments (start_date, end_date, hypothesis,
            change_applied, control_version, test_version, target_metric,
            created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (start_date, end_date, hypothesis, change_applied,
         control_version, test_version, target_metric, now_iso()),
    )
    return int(cur.lastrowid)


def update_logic_experiment_result(
    conn: sqlite3.Connection, experiment_id: int,
    *, result: dict, decision: str, end_date: str | None = None,
) -> None:
    if end_date:
        conn.execute(
            "UPDATE logic_experiments SET result_json=?, decision=?, end_date=? "
            "WHERE experiment_id=?",
            (dump_json(result), decision, end_date, experiment_id),
        )
    else:
        conn.execute(
            "UPDATE logic_experiments SET result_json=?, decision=? "
            "WHERE experiment_id=?",
            (dump_json(result), decision, experiment_id),
        )


def fetch_logic_experiments(
    conn: sqlite3.Connection, decision: str | None = None,
) -> list[sqlite3.Row]:
    if decision:
        return list(conn.execute(
            "SELECT * FROM logic_experiments WHERE decision=? ORDER BY start_date DESC",
            (decision,),
        ))
    return list(conn.execute(
        "SELECT * FROM logic_experiments ORDER BY start_date DESC",
    ))


def insert_logic_improvement(
    conn: sqlite3.Connection,
    *,
    proposal_date: str,
    category: str,
    problem: str,
    evidence: dict,
    proposed_change: str,
    expected_effect: str,
    risk: str,
    auto_apply: bool = False,
    approval_required: bool = True,
) -> int:
    # 같은 날 같은 카테고리 + 동일 problem 은 중복 방지
    existing = conn.execute(
        "SELECT improvement_id FROM logic_improvements "
        "WHERE proposal_date=? AND category=? AND problem=?",
        (proposal_date, category, problem),
    ).fetchone()
    if existing:
        return int(existing[0])

    cur = conn.execute(
        """
        INSERT INTO logic_improvements (proposal_date, category, problem,
            evidence_json, proposed_change, expected_effect, risk,
            auto_apply, approval_required, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (proposal_date, category, problem, dump_json(evidence),
         proposed_change, expected_effect, risk,
         1 if auto_apply else 0, 1 if approval_required else 0, now_iso()),
    )
    return int(cur.lastrowid)


def fetch_logic_improvements(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 200,
) -> list[sqlite3.Row]:
    if status:
        return list(conn.execute(
            "SELECT * FROM logic_improvements WHERE status=? "
            "ORDER BY proposal_date DESC, improvement_id DESC LIMIT ?",
            (status, limit),
        ))
    return list(conn.execute(
        "SELECT * FROM logic_improvements "
        "ORDER BY proposal_date DESC, improvement_id DESC LIMIT ?",
        (limit,),
    ))


def update_logic_improvement_status(
    conn: sqlite3.Connection, improvement_id: int, status: str,
    applied_version_id: int | None = None,
) -> None:
    if applied_version_id is not None:
        conn.execute(
            "UPDATE logic_improvements SET status=?, applied_version_id=? "
            "WHERE improvement_id=?",
            (status, applied_version_id, improvement_id),
        )
    else:
        conn.execute(
            "UPDATE logic_improvements SET status=? WHERE improvement_id=?",
            (status, improvement_id),
        )


def insert_audit_report(
    conn: sqlite3.Connection,
    *,
    report_date: str,
    report_type: str,   # 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'challenge'
    body: dict,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO logic_audit_reports (report_date, report_type, body_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (report_date, report_type, dump_json(body), now_iso()),
    )
    return int(cur.lastrowid)


def fetch_audit_reports(
    conn: sqlite3.Connection, report_type: str | None = None, limit: int = 50,
) -> list[sqlite3.Row]:
    if report_type:
        return list(conn.execute(
            "SELECT * FROM logic_audit_reports WHERE report_type=? "
            "ORDER BY report_date DESC LIMIT ?",
            (report_type, limit),
        ))
    return list(conn.execute(
        "SELECT * FROM logic_audit_reports ORDER BY report_date DESC LIMIT ?",
        (limit,),
    ))


def fetch_performance_for_decision(
    conn: sqlite3.Connection, decision_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM performance_tracking WHERE decision_id=? ORDER BY check_date DESC",
            (decision_id,),
        )
    )


# ---------------------------------------------------------------------------
# Auto-Curation — LLM 기반 자동 큐레이션 캐시
# ---------------------------------------------------------------------------

def upsert_auto_curation(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    fields: dict[str, Any],
    model_used: str,
    token_input: int = 0,
    token_output: int = 0,
    cost_estimate_usd: float = 0.0,
    sources: dict[str, Any] | None = None,
    sec_filing_date: str | None = None,
    data_confidence: str = "Medium",
    uncertainty_flags: list[str] | None = None,
) -> None:
    """LLM 이 생성한 큐레이션 데이터를 ticker key 로 upsert.

    fields 는 12 항목 dict (easy_explanation / core_thesis / thesis_pillars /
    core_kpis / key_risks / anti_thesis / earnings_quality / moat_map /
    alpha_judgment / strategic_lens 등).
    """
    # 기존 row 의 version 을 +1 (재생성 시)
    existing = conn.execute(
        "SELECT version FROM auto_curation WHERE ticker=?", (ticker,)
    ).fetchone()
    new_version = (existing["version"] + 1) if existing else 1

    conn.execute(
        """
        INSERT INTO auto_curation (ticker, generated_at, fields_json, model_used,
            token_input, token_output, cost_estimate_usd, sources_json,
            sec_filing_date, data_confidence, uncertainty_flags_json,
            version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            generated_at=excluded.generated_at,
            fields_json=excluded.fields_json,
            model_used=excluded.model_used,
            token_input=excluded.token_input,
            token_output=excluded.token_output,
            cost_estimate_usd=excluded.cost_estimate_usd,
            sources_json=excluded.sources_json,
            sec_filing_date=excluded.sec_filing_date,
            data_confidence=excluded.data_confidence,
            uncertainty_flags_json=excluded.uncertainty_flags_json,
            version=excluded.version,
            created_at=excluded.created_at
        """,
        (
            ticker, today_kst(), dump_json(fields), model_used,
            token_input, token_output, cost_estimate_usd, dump_json(sources),
            sec_filing_date, data_confidence, dump_json(uncertainty_flags),
            new_version, now_iso(),
        ),
    )


def fetch_auto_curation(
    conn: sqlite3.Connection, ticker: str
) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT * FROM auto_curation WHERE ticker=?", (ticker,)
    )
    return cur.fetchone()


def fetch_all_auto_curation(
    conn: sqlite3.Connection, limit: int = 1000
) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM auto_curation ORDER BY generated_at DESC LIMIT ?",
        (limit,),
    ))


def auto_curation_is_fresh(
    conn: sqlite3.Connection, ticker: str, max_age_days: int = 60
) -> bool:
    """ticker 의 auto_curation 이 max_age_days 이내인지."""
    import datetime as _dt
    row = fetch_auto_curation(conn, ticker)
    if not row:
        return False
    try:
        gen_date = _dt.date.fromisoformat(row["generated_at"])
        age = (_dt.date.today() - gen_date).days
        return age <= max_age_days
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Portfolio Regime — market_regime / crash_deployment_plan
# ---------------------------------------------------------------------------

_MARKET_REGIME_COLS: tuple[str, ...] = (
    "market_overheat_score", "current_regime",
    "valuation_stretch_score", "sentiment_speculation_score",
    "market_concentration_score", "liquidity_credit_score",
    "earnings_revision_risk_score", "technical_extension_score",
    "cycle_psychology_score", "buffett_opportunity_score",
    "portfolio_mode", "recommended_beta_level", "commentary_ko",
)


def upsert_market_regime(
    conn: sqlite3.Connection, date_iso: str, fields: dict[str, Any]
) -> None:
    """market_regime 일일 행 upsert (date PK). 없는 키는 NULL 로 저장."""
    cols = ["date"] + list(_MARKET_REGIME_COLS) + ["created_at"]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    sql = (
        f"INSERT INTO market_regime ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {update_set}"
    )
    params = [date_iso]
    for c in _MARKET_REGIME_COLS:
        params.append(fields.get(c))
    params.append(now_iso())
    conn.execute(sql, params)
    conn.commit()


def fetch_latest_market_regime(
    conn: sqlite3.Connection, date_iso: str | None = None
) -> sqlite3.Row | None:
    """가장 최신 (또는 지정일) market_regime 행."""
    if date_iso:
        cur = conn.execute("SELECT * FROM market_regime WHERE date=?", (date_iso,))
        return cur.fetchone()
    cur = conn.execute("SELECT * FROM market_regime ORDER BY date DESC LIMIT 1")
    return cur.fetchone()


_CRASH_PLAN_COLS: tuple[str, ...] = (
    "qqq_drawdown_from_high", "deployment_zone", "recommended_instrument",
    "suggested_action", "credit_stress_status", "liquidity_status",
    "commentary_ko",
)


def upsert_crash_deployment_plan(
    conn: sqlite3.Connection, date_iso: str, fields: dict[str, Any]
) -> None:
    """crash_deployment_plan 일일 행 upsert (date PK)."""
    cols = ["date"] + list(_CRASH_PLAN_COLS) + ["created_at"]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    sql = (
        f"INSERT INTO crash_deployment_plan ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {update_set}"
    )
    params = [date_iso]
    for c in _CRASH_PLAN_COLS:
        params.append(fields.get(c))
    params.append(now_iso())
    conn.execute(sql, params)
    conn.commit()


def fetch_latest_crash_deployment_plan(
    conn: sqlite3.Connection, date_iso: str | None = None
) -> sqlite3.Row | None:
    """가장 최신 (또는 지정일) crash_deployment_plan 행."""
    if date_iso:
        cur = conn.execute(
            "SELECT * FROM crash_deployment_plan WHERE date=?", (date_iso,)
        )
        return cur.fetchone()
    cur = conn.execute(
        "SELECT * FROM crash_deployment_plan ORDER BY date DESC LIMIT 1"
    )
    return cur.fetchone()


def upsert_backtest_solution(
    conn: sqlite3.Connection, date_iso: str, fields: dict[str, Any]
) -> None:
    """backtest_solution 일일 행 upsert (date PK).

    fields 의 'items' 리스트는 items_json 으로 JSON 인코딩해 저장한다.
    """
    items_json = dump_json(fields.get("items"))
    sql = (
        "INSERT INTO backtest_solution "
        "(date, headline, data_mode, items_json, caveat, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET "
        "headline=excluded.headline, data_mode=excluded.data_mode, "
        "items_json=excluded.items_json, caveat=excluded.caveat, "
        "created_at=excluded.created_at"
    )
    conn.execute(sql, (
        date_iso,
        fields.get("headline"),
        fields.get("data_mode"),
        items_json,
        fields.get("caveat"),
        now_iso(),
    ))
    conn.commit()


def fetch_latest_backtest_solution(
    conn: sqlite3.Connection, date_iso: str | None = None
) -> sqlite3.Row | None:
    """가장 최신 (또는 지정일) backtest_solution 행."""
    if date_iso:
        cur = conn.execute(
            "SELECT * FROM backtest_solution WHERE date=?", (date_iso,)
        )
        return cur.fetchone()
    cur = conn.execute(
        "SELECT * FROM backtest_solution ORDER BY date DESC LIMIT 1"
    )
    return cur.fetchone()


def fetch_recent_market_regimes(
    conn: sqlite3.Connection, limit: int = 2
) -> list[sqlite3.Row]:
    """최근 N개 market_regime 행 (date DESC). 전날 대비 비교용."""
    cur = conn.execute(
        "SELECT * FROM market_regime ORDER BY date DESC LIMIT ?", (int(limit),)
    )
    return cur.fetchall()


def fetch_recent_crash_deployment_plans(
    conn: sqlite3.Connection, limit: int = 2
) -> list[sqlite3.Row]:
    """최근 N개 crash_deployment_plan 행 (date DESC). 전날 대비 비교용."""
    cur = conn.execute(
        "SELECT * FROM crash_deployment_plan ORDER BY date DESC LIMIT ?",
        (int(limit),),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Phase 4-A — 백테스트: market_price_history / backtest_results /
#             regime_forward_returns
# ---------------------------------------------------------------------------

_MPH_COLS: tuple[str, ...] = (
    "date", "ticker", "open", "high", "low", "close",
    "adj_close", "volume", "source", "created_at",
)


def upsert_market_price_history(
    conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]
) -> int:
    """일봉 batch upsert ((date, ticker) PK). rows 는 _MPH_COLS 키를 가진 dict."""
    placeholders = ", ".join("?" for _ in _MPH_COLS)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in _MPH_COLS if c not in ("date", "ticker")
    )
    sql = (
        f"INSERT INTO market_price_history ({', '.join(_MPH_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )
    n = 0
    for r in rows:
        try:
            conn.execute(sql, [r.get(c) for c in _MPH_COLS])
            n += 1
        except Exception as e:
            log.debug("market_price_history upsert 실패 %s: %s", r.get("ticker"), e)
    conn.commit()
    return n


def fetch_market_price_history(
    conn: sqlite3.Connection, ticker: str,
    start_date: str | None = None, end_date: str | None = None,
) -> list[sqlite3.Row]:
    """단일 티커 일봉 (날짜 오름차순). 날짜 범위 옵션."""
    sql = "SELECT * FROM market_price_history WHERE ticker=?"
    params: list[Any] = [ticker]
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY date"
    return list(conn.execute(sql, params))


def fetch_price_history_tickers(conn: sqlite3.Connection) -> list[str]:
    """market_price_history 에 데이터가 있는 티커 목록."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM market_price_history ORDER BY ticker"
        ).fetchall()
        return [r["ticker"] if hasattr(r, "keys") else r[0] for r in rows]
    except Exception:
        return []


_BACKTEST_COLS: tuple[str, ...] = (
    "strategy_name", "asset", "start_date", "end_date", "cagr",
    "total_return", "max_drawdown", "sharpe", "sortino", "calmar",
    "win_rate", "recovery_time", "details_json", "updated_at",
)


def upsert_backtest_result(
    conn: sqlite3.Connection, fields: dict[str, Any]
) -> None:
    """backtest_results 행 upsert ((strategy_name, asset) PK)."""
    placeholders = ", ".join("?" for _ in _BACKTEST_COLS)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in _BACKTEST_COLS
        if c not in ("strategy_name", "asset")
    )
    sql = (
        f"INSERT INTO backtest_results ({', '.join(_BACKTEST_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(strategy_name, asset) DO UPDATE SET {update_set}"
    )
    params: list[Any] = []
    for c in _BACKTEST_COLS:
        v = fields.get(c)
        if c == "details_json" and v is not None and not isinstance(v, str):
            v = dump_json(v)
        if c == "updated_at" and v is None:
            v = now_iso()
        params.append(v)
    conn.execute(sql, params)
    conn.commit()


def fetch_backtest_results(
    conn: sqlite3.Connection, strategy_name: str | None = None
) -> list[sqlite3.Row]:
    """backtest_results 전체 또는 특정 전략."""
    if strategy_name:
        return list(conn.execute(
            "SELECT * FROM backtest_results WHERE strategy_name=? "
            "ORDER BY asset", (strategy_name,)
        ))
    return list(conn.execute(
        "SELECT * FROM backtest_results ORDER BY strategy_name, asset"
    ))


_RFR_COLS: tuple[str, ...] = (
    "date", "regime", "overheat_score", "asset",
    "forward_1w", "forward_1m", "forward_3m", "forward_6m", "forward_12m",
    "mdd_1m", "mdd_3m", "mdd_6m", "updated_at",
)


def upsert_regime_forward_returns(
    conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]
) -> int:
    """regime_forward_returns batch upsert ((date, regime, asset) PK)."""
    placeholders = ", ".join("?" for _ in _RFR_COLS)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in _RFR_COLS
        if c not in ("date", "regime", "asset")
    )
    sql = (
        f"INSERT INTO regime_forward_returns ({', '.join(_RFR_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, regime, asset) DO UPDATE SET {update_set}"
    )
    n = 0
    ts = now_iso()
    for r in rows:
        try:
            params = []
            for c in _RFR_COLS:
                v = r.get(c)
                if c == "updated_at" and v is None:
                    v = ts
                params.append(v)
            conn.execute(sql, params)
            n += 1
        except Exception as e:
            log.debug("regime_forward_returns upsert 실패: %s", e)
    conn.commit()
    return n


def fetch_regime_forward_returns(
    conn: sqlite3.Connection, regime: str | None = None
) -> list[sqlite3.Row]:
    """regime_forward_returns 전체 또는 특정 regime."""
    if regime:
        return list(conn.execute(
            "SELECT * FROM regime_forward_returns WHERE regime=? "
            "ORDER BY date", (regime,)
        ))
    return list(conn.execute(
        "SELECT * FROM regime_forward_returns ORDER BY date"
    ))


# ---------------------------------------------------------------------------
# Phase 4-B — Decision Journal: decision_grades
# ---------------------------------------------------------------------------

_DECISION_GRADE_COLS: tuple[str, ...] = (
    "decision_id", "milestone", "graded_date", "price_at_decision",
    "price_at_milestone", "return_pct", "benchmark_return_pct",
    "relative_pct", "grade", "grade_note", "created_at",
)


def upsert_decision_grade(
    conn: sqlite3.Connection, fields: dict[str, Any]
) -> None:
    """decision_grades 행 upsert ((decision_id, milestone) PK)."""
    placeholders = ", ".join("?" for _ in _DECISION_GRADE_COLS)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in _DECISION_GRADE_COLS
        if c not in ("decision_id", "milestone")
    )
    sql = (
        f"INSERT INTO decision_grades ({', '.join(_DECISION_GRADE_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(decision_id, milestone) DO UPDATE SET {update_set}"
    )
    params: list[Any] = []
    for c in _DECISION_GRADE_COLS:
        v = fields.get(c)
        if c == "created_at" and v is None:
            v = now_iso()
        params.append(v)
    conn.execute(sql, params)
    conn.commit()


def fetch_decision_grades(
    conn: sqlite3.Connection, decision_id: str | None = None
) -> list[sqlite3.Row]:
    """decision_grades 전체 또는 특정 결정의 채점 행 (milestone 순)."""
    if decision_id:
        return list(conn.execute(
            "SELECT * FROM decision_grades WHERE decision_id=? "
            "ORDER BY milestone", (decision_id,)
        ))
    return list(conn.execute(
        "SELECT * FROM decision_grades ORDER BY decision_id, milestone"
    ))


def fetch_all_decision_grades_map(
    conn: sqlite3.Connection
) -> dict[str, list[sqlite3.Row]]:
    """모든 채점 행을 decision_id 별로 묶어 반환."""
    out: dict[str, list[sqlite3.Row]] = {}
    try:
        for r in fetch_decision_grades(conn):
            did = r["decision_id"]
            out.setdefault(did, []).append(r)
    except Exception as e:
        log.debug("fetch_all_decision_grades_map 실패: %s", e)
    return out


# ---------------------------------------------------------------------------
# 통합 조회 — UI 가 한 번에 종목 상세 데이터 받아오기
# ---------------------------------------------------------------------------

def fetch_full_stock_view(
    conn: sqlite3.Connection,
    ticker: str,
    date_iso: str | None = None,
) -> dict[str, Any]:
    """price + score + research + events + recent news 를 한 번에."""
    out: dict[str, Any] = {"ticker": ticker}
    # universe
    u = conn.execute("SELECT * FROM universe WHERE ticker=?", (ticker,)).fetchone()
    out["universe"] = dict(u) if u else None
    # latest price
    prices = fetch_latest_price_snapshot(conn, ticker, date_iso)
    out["price"] = dict(prices[0]) if prices else None
    # score
    s = fetch_score_for_ticker(conn, ticker, date_iso)
    out["score"] = dict(s) if s else None
    # research
    r = fetch_stock_research(conn, ticker, date_iso)
    out["research"] = dict(r) if r else None
    # events
    out["events"] = [dict(e) for e in fetch_events_for_ticker(conn, ticker, limit=5)]
    # news
    out["news"] = [dict(n) for n in fetch_news_for_ticker(conn, ticker, limit=5)]
    return out


def fetch_dislocation_candidates(
    conn: sqlite3.Connection, date_iso: str | None = None, limit: int = 15
) -> list[sqlite3.Row]:
    if not date_iso:
        cur = conn.execute("SELECT MAX(date) AS d FROM scores")
        r = cur.fetchone()
        date_iso = r["d"] if r and r["d"] else None
    if not date_iso:
        return []
    return list(
        conn.execute(
            """
            SELECT s.*, p.drawdown_from_52w_high, p.current_price, p.daily_return,
                   u.name_ko, u.name_en, u.theme, u.category
            FROM scores s
            LEFT JOIN price_snapshot p
              ON p.date = s.date AND p.ticker = s.ticker
            LEFT JOIN universe u ON u.ticker = s.ticker
            WHERE s.date = ?
              AND s.action_tag = 'Quality Dislocation'
              AND s.final_score IS NOT NULL
            ORDER BY s.final_score DESC
            LIMIT ?
            """,
            (date_iso, limit),
        )
    )


# ---------------------------------------------------------------------------
# Discovery scores / promotion candidates
# ---------------------------------------------------------------------------

def upsert_discovery_score(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    ticker: str,
    queue_type: str,
    score: float,
    rank: int,
    signal_summary: str,
    key_metrics: dict | None,
) -> None:
    conn.execute(
        """
        INSERT INTO discovery_scores
            (run_id, date, ticker, queue_type, score, rank, signal_summary, key_metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, ticker, queue_type) DO UPDATE SET
            run_id=excluded.run_id,
            score=excluded.score,
            rank=excluded.rank,
            signal_summary=excluded.signal_summary,
            key_metrics_json=excluded.key_metrics_json
        """,
        (
            run_id, date_iso, ticker, queue_type, score, rank, signal_summary,
            dump_json(key_metrics) if key_metrics else None,
            now_iso(),
        ),
    )


def fetch_discovery_scores(
    conn: sqlite3.Connection,
    date_iso: str | None = None,
    queue_type: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if not date_iso:
        r = conn.execute("SELECT MAX(date) AS d FROM discovery_scores").fetchone()
        date_iso = r["d"] if r and r["d"] else None
    if not date_iso:
        return []
    if queue_type:
        return list(conn.execute(
            "SELECT * FROM discovery_scores WHERE date=? AND queue_type=? "
            "ORDER BY rank ASC, score DESC LIMIT ?",
            (date_iso, queue_type, limit),
        ))
    return list(conn.execute(
        "SELECT * FROM discovery_scores WHERE date=? ORDER BY score DESC LIMIT ?",
        (date_iso, limit),
    ))


def upsert_promotion_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    date_iso: str,
    ticker: str,
    payload: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO promotion_candidates
            (run_id, date, ticker, name, queue_type, discovery_score, promotion_score,
             reason, latest_event_summary, thesis_impact, action_recommendation,
             promoted_to_deep_dive, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, ticker) DO UPDATE SET
            run_id=excluded.run_id,
            queue_type=excluded.queue_type,
            discovery_score=excluded.discovery_score,
            promotion_score=excluded.promotion_score,
            reason=excluded.reason,
            latest_event_summary=excluded.latest_event_summary,
            thesis_impact=excluded.thesis_impact,
            action_recommendation=excluded.action_recommendation,
            promoted_to_deep_dive=excluded.promoted_to_deep_dive
        """,
        (
            run_id, date_iso, ticker,
            payload.get("name"),
            payload.get("queue_type"),
            payload.get("discovery_score"),
            payload.get("promotion_score"),
            payload.get("reason"),
            payload.get("latest_event_summary"),
            payload.get("thesis_impact"),
            payload.get("action_recommendation"),
            int(bool(payload.get("promoted_to_deep_dive"))),
            now_iso(),
        ),
    )


def fetch_promotion_candidates(
    conn: sqlite3.Connection,
    date_iso: str | None = None,
    promoted_only: bool = False,
    limit: int = 50,
) -> list[sqlite3.Row]:
    if not date_iso:
        r = conn.execute("SELECT MAX(date) AS d FROM promotion_candidates").fetchone()
        date_iso = r["d"] if r and r["d"] else None
    if not date_iso:
        return []
    if promoted_only:
        return list(conn.execute(
            "SELECT * FROM promotion_candidates WHERE date=? AND promoted_to_deep_dive=1 "
            "ORDER BY promotion_score DESC LIMIT ?",
            (date_iso, limit),
        ))
    return list(conn.execute(
        "SELECT * FROM promotion_candidates WHERE date=? ORDER BY promotion_score DESC LIMIT ?",
        (date_iso, limit),
    ))


# ---------------------------------------------------------------------------
# Article summary cache (URL hash 기준)
# ---------------------------------------------------------------------------

def make_article_url_hash(url: str | None) -> str | None:
    if not url:
        return None
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()


def fetch_article_summary(
    conn: sqlite3.Connection, url: str | None
) -> sqlite3.Row | None:
    h = make_article_url_hash(url)
    if not h:
        return None
    return conn.execute(
        "SELECT * FROM article_summaries WHERE article_url_hash=?", (h,)
    ).fetchone()


def upsert_article_summary(
    conn: sqlite3.Connection,
    *,
    url: str | None,
    title: str | None,
    source: str | None,
    published_at: str | None,
    ticker: str | None,
    content_availability: str | None,
    detailed_summary_ko: str | None,
    investment_implication_ko: str | None,
    follow_up_items_ko: list | None,
    thesis_impact: str | None,
    confidence_level: str | None,
    model_used: str | None,
    token_estimate: int | None = None,
) -> None:
    h = make_article_url_hash(url)
    if not h:
        return
    conn.execute(
        """
        INSERT INTO article_summaries
            (article_url_hash, article_url, title, source, published_at, ticker,
             content_availability, detailed_summary_ko, investment_implication_ko,
             follow_up_items_ko, thesis_impact, confidence_level, model_used,
             token_estimate, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_url_hash) DO UPDATE SET
            title=COALESCE(excluded.title, article_summaries.title),
            source=COALESCE(excluded.source, article_summaries.source),
            published_at=COALESCE(excluded.published_at, article_summaries.published_at),
            ticker=COALESCE(excluded.ticker, article_summaries.ticker),
            content_availability=COALESCE(excluded.content_availability, article_summaries.content_availability),
            detailed_summary_ko=COALESCE(excluded.detailed_summary_ko, article_summaries.detailed_summary_ko),
            investment_implication_ko=COALESCE(excluded.investment_implication_ko, article_summaries.investment_implication_ko),
            follow_up_items_ko=COALESCE(excluded.follow_up_items_ko, article_summaries.follow_up_items_ko),
            thesis_impact=COALESCE(excluded.thesis_impact, article_summaries.thesis_impact),
            confidence_level=COALESCE(excluded.confidence_level, article_summaries.confidence_level),
            model_used=COALESCE(excluded.model_used, article_summaries.model_used),
            token_estimate=COALESCE(excluded.token_estimate, article_summaries.token_estimate),
            updated_at=excluded.updated_at
        """,
        (
            h, url, title, source, published_at, ticker,
            content_availability,
            detailed_summary_ko, investment_implication_ko,
            dump_json(follow_up_items_ko) if follow_up_items_ko else None,
            thesis_impact, confidence_level, model_used, token_estimate,
            now_iso(), now_iso(),
        ),
    )


# ---------------------------------------------------------------------------
# Capital Efficiency (Phase 2) — capital_efficiency_scores / profit_protection /
# parking_candidates
# ---------------------------------------------------------------------------

_CAPITAL_EFFICIENCY_COLS: tuple[str, ...] = (
    "capital_efficiency_score", "expected_return_potential",
    "time_to_target_probability", "downside_risk_score",
    "catalyst_visibility_score", "qld_relative_score", "liquidity_exit_score",
    "qld_relative_view", "commentary_ko",
)


def upsert_capital_efficiency_score(
    conn: sqlite3.Connection, date_iso: str, ticker: str, fields: dict[str, Any]
) -> None:
    """capital_efficiency_scores 행 upsert ((date, ticker) PK). 없는 키는 NULL."""
    cols = ["date", "ticker"] + list(_CAPITAL_EFFICIENCY_COLS) + ["created_at"]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker")
    )
    sql = (
        f"INSERT INTO capital_efficiency_scores ({', '.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )
    params: list[Any] = [date_iso, ticker]
    for c in _CAPITAL_EFFICIENCY_COLS:
        params.append(fields.get(c))
    params.append(now_iso())
    conn.execute(sql, params)


def fetch_capital_efficiency_score(
    conn: sqlite3.Connection, ticker: str, date_iso: str | None = None
) -> sqlite3.Row | None:
    """종목별 최신 (또는 지정일) capital_efficiency_scores 행."""
    if date_iso:
        cur = conn.execute(
            "SELECT * FROM capital_efficiency_scores WHERE date=? AND ticker=?",
            (date_iso, ticker),
        )
        return cur.fetchone()
    cur = conn.execute(
        "SELECT * FROM capital_efficiency_scores WHERE ticker=? "
        "ORDER BY date DESC LIMIT 1",
        (ticker,),
    )
    return cur.fetchone()


_PROFIT_PROTECTION_COLS: tuple[str, ...] = (
    "current_gain", "leverage_flag", "valuation_stretch_score",
    "technical_extension_score", "narrative_crowding_score",
    "profit_protection_score", "suggested_action", "commentary_ko",
)


def upsert_profit_protection(
    conn: sqlite3.Connection, date_iso: str, ticker: str, fields: dict[str, Any]
) -> None:
    """profit_protection 행 upsert ((date, ticker) PK). 없는 키는 NULL."""
    cols = ["date", "ticker"] + list(_PROFIT_PROTECTION_COLS) + ["created_at"]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker")
    )
    sql = (
        f"INSERT INTO profit_protection ({', '.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )
    params: list[Any] = [date_iso, ticker]
    for c in _PROFIT_PROTECTION_COLS:
        v = fields.get(c)
        if c == "leverage_flag" and v is not None:
            v = int(bool(v))
        params.append(v)
    params.append(now_iso())
    conn.execute(sql, params)


def fetch_profit_protection(
    conn: sqlite3.Connection, ticker: str, date_iso: str | None = None
) -> sqlite3.Row | None:
    """종목별 최신 (또는 지정일) profit_protection 행."""
    if date_iso:
        cur = conn.execute(
            "SELECT * FROM profit_protection WHERE date=? AND ticker=?",
            (date_iso, ticker),
        )
        return cur.fetchone()
    cur = conn.execute(
        "SELECT * FROM profit_protection WHERE ticker=? "
        "ORDER BY date DESC LIMIT 1",
        (ticker,),
    )
    return cur.fetchone()


_PARKING_COLS: tuple[str, ...] = (
    "name", "parking_score", "beta", "drawdown_resilience_score",
    "earnings_stability_score", "valuation_reasonableness_score",
    "dividend_buyback_score", "technical_support_score",
    "why_parking_ko", "risk_ko",
)


def upsert_parking_candidate(
    conn: sqlite3.Connection, date_iso: str, ticker: str, fields: dict[str, Any]
) -> None:
    """parking_candidates 행 upsert ((date, ticker) PK). 없는 키는 NULL."""
    cols = ["date", "ticker"] + list(_PARKING_COLS) + ["created_at"]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker")
    )
    sql = (
        f"INSERT INTO parking_candidates ({', '.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )
    params: list[Any] = [date_iso, ticker]
    for c in _PARKING_COLS:
        params.append(fields.get(c))
    params.append(now_iso())
    conn.execute(sql, params)


def fetch_parking_candidates(
    conn: sqlite3.Connection, date_iso: str | None = None, limit: int = 30
) -> list[sqlite3.Row]:
    """최신 (또는 지정일) parking_candidates — parking_score 내림차순."""
    if not date_iso:
        r = conn.execute("SELECT MAX(date) AS d FROM parking_candidates").fetchone()
        date_iso = r["d"] if r and r["d"] else None
    if not date_iso:
        return []
    return list(conn.execute(
        "SELECT * FROM parking_candidates WHERE date=? "
        "ORDER BY parking_score DESC LIMIT ?",
        (date_iso, limit),
    ))


# ---------------------------------------------------------------------------
# 보유 종목 브리핑 — holdings_briefing
# ---------------------------------------------------------------------------

_HOLDINGS_BRIEFING_COLS: tuple[str, ...] = (
    "date", "ticker", "name", "exposure_theme", "summary_ko",
    "key_drivers_ko", "risks_ko", "portfolio_note_ko", "model_used",
    "created_at",
)


def upsert_holding_briefing(
    conn: sqlite3.Connection, date_iso: str, ticker: str, fields: dict[str, Any]
) -> None:
    """holdings_briefing 행 upsert ((date, ticker) PK).

    key_drivers_ko 가 list 면 JSON 문자열로 직렬화해 저장한다.
    """
    placeholders = ", ".join("?" for _ in _HOLDINGS_BRIEFING_COLS)
    update_set = ", ".join(
        f"{c}=excluded.{c}" for c in _HOLDINGS_BRIEFING_COLS
        if c not in ("date", "ticker")
    )
    sql = (
        f"INSERT INTO holdings_briefing ({', '.join(_HOLDINGS_BRIEFING_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date, ticker) DO UPDATE SET {update_set}"
    )
    params: list[Any] = []
    for c in _HOLDINGS_BRIEFING_COLS:
        if c == "date":
            params.append(date_iso)
        elif c == "ticker":
            params.append(ticker)
        elif c == "key_drivers_ko":
            v = fields.get("key_drivers_ko")
            params.append(dump_json(v) if isinstance(v, (list, tuple)) else v)
        elif c == "created_at":
            params.append(fields.get("created_at") or now_iso())
        else:
            params.append(fields.get(c))
    conn.execute(sql, params)
    conn.commit()


def fetch_holdings_briefings(
    conn: sqlite3.Connection, date_iso: str | None = None
) -> list[sqlite3.Row]:
    """지정일(또는 최신 가용일)의 holdings_briefing 행 목록."""
    if not date_iso:
        try:
            r = conn.execute(
                "SELECT MAX(date) AS d FROM holdings_briefing"
            ).fetchone()
            date_iso = r["d"] if r and r["d"] else None
        except Exception as e:
            log.debug("holdings_briefing 최신일 조회 실패: %s", e)
            return []
    if not date_iso:
        return []
    try:
        return list(conn.execute(
            "SELECT * FROM holdings_briefing WHERE date=? ORDER BY ticker",
            (date_iso,),
        ))
    except Exception as e:
        log.debug("holdings_briefing 조회 실패: %s", e)
        return []


def fetch_briefing_dates(conn: sqlite3.Connection) -> list[str]:
    """holdings_briefing 의 distinct date 목록 (내림차순)."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM holdings_briefing ORDER BY date DESC"
        ).fetchall()
        return [r["date"] if hasattr(r, "keys") else r[0] for r in rows]
    except Exception as e:
        log.debug("fetch_briefing_dates 실패: %s", e)
        return []
