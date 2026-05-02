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
    """DB 파일을 열고 외래키/스키마를 보장한다."""
    ensure_data_dir()
    p = str(path or DB_PATH)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
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
            dump_json(brief.get("macro_issues")),
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
    cur = conn.execute(
        """
        INSERT INTO decision_log (date, ticker, action_tag, final_score, price,
                                  reason, user_note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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


def fetch_decisions(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM decision_log ORDER BY date DESC, decision_id DESC LIMIT ?",
            (limit,),
        )
    )


def upsert_performance(
    conn: sqlite3.Connection,
    decision_id: int,
    check_date: str,
    metrics: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO performance_tracking (decision_id, check_date,
            return_1w, return_1m, return_3m, return_6m,
            relative_return_spy, relative_return_qqq, outcome_tag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id, check_date) DO UPDATE SET
            return_1w=excluded.return_1w,
            return_1m=excluded.return_1m,
            return_3m=excluded.return_3m,
            return_6m=excluded.return_6m,
            relative_return_spy=excluded.relative_return_spy,
            relative_return_qqq=excluded.relative_return_qqq,
            outcome_tag=excluded.outcome_tag
        """,
        (
            decision_id,
            check_date,
            metrics.get("return_1w"),
            metrics.get("return_1m"),
            metrics.get("return_3m"),
            metrics.get("return_6m"),
            metrics.get("relative_return_spy"),
            metrics.get("relative_return_qqq"),
            metrics.get("outcome_tag"),
        ),
    )


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
