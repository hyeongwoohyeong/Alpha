"""Logic Auditor — 의사결정 기록 + 성과 추적 + 결과 분류.

핵심 책임:
1. **record_decision()** — Daily Brief / Discovery / Promoted / Avoid / Watchlist / High Alpha
   등 Alpha 가 매일 내린 모든 주요 판단을 decision_log 에 기록.
2. **update_performance_tracking()** — 각 결정 이후 1D / 1W / 2W / 1M / 3M / 6M / 12M
   시점에 절대 수익률 + SPY / QQQ / QLD 대비 초과수익을 계산해 performance_tracking 에 저장.
3. **classify_decision_outcome()** — 절대/상대 성과 + Action Tag 의 의도와 비교해
   Correct / Wrong / Too Early / Avoid Wrong / Watchlist Missed / Thesis Broken 분류.

설계 원칙:
- **자동 적용 금지** — 본 모듈은 데이터 수집 / 분류만 함. 가중치 변경 / 로직 변경은
  별도 logic_auditor.py 에서 사용자 승인 후에만 적용.
- **QLD 대비 평가 필수** — 개별주를 QLD 대신 보유했을 때 의미 있었는지가 사용자 기준.
- **None 안전성** — 가격 데이터 누락 시 None 으로 저장 (분석 단계에서 필터).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from . import database as db
from .market_data import fetch_max_history
from .utils import get_logger, safe_float, today_kst

log = get_logger("auditor")


# ---------------------------------------------------------------------------
# 설정 — Holding period (일자 환산) + hit status 임계값
# ---------------------------------------------------------------------------

HOLDING_PERIODS_DAYS: dict[str, int] = {
    "1D": 1,
    "1W": 7,
    "2W": 14,
    "1M": 30,
    "3M": 91,
    "6M": 182,
    "12M": 365,
}

# 벤치마크 ticker — yfinance
BENCHMARKS = ("SPY", "QQQ", "QLD")

# 성과 분류 임계값
# - "유의미한 outperform" 기준 = QQQ 대비 +3% 또는 QLD 대비 +5%
THRESH_OUTPERFORM_QQQ = 0.03
THRESH_OUTPERFORM_QLD = 0.05
THRESH_UNDERPERFORM = -0.05   # 절대 -5% 이상 손실 = wrong 후보
THRESH_AVOID_WRONG = 0.10     # Avoid 인데 +10% 이상 상승 = avoid wrong


# ---------------------------------------------------------------------------
# Step 1 — 의사결정 기록
# ---------------------------------------------------------------------------

# 자동 기록 대상 Action Tag / 조건
RECORDED_ACTION_TAGS: set[str] = {
    "Research Now",
    "Quality Dislocation",
    "Watchlist",
    "Wait for Entry",
    "Need Thesis Check",
    "Too Crowded",
    "Avoid",
}


def _row_get(row: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return default


def record_decisions_for_run(
    conn,
    *,
    run_id: str,
    date_iso: str,
    rows: Iterable[dict[str, Any]],
    research_map: dict[str, dict[str, Any]] | None = None,
    benchmark_prices: dict[str, float] | None = None,
    logic_version: str = "v1.0",
) -> int:
    """run_research 한 사이클 분의 결정을 일괄 기록.

    rows: scoring + action_tag 까지 완료된 row 리스트 (core + promoted 모두 포함)
    research_map: ticker → stock_research dict (alpha_score / earnings_quality 포함, 선택)
    benchmark_prices: {"SPY": float, "QQQ": float, "QLD": float} — fetch 실패 시 None

    Returns: 기록된 decision 수.
    """
    research_map = research_map or {}
    benchmark_prices = benchmark_prices or {}
    spy_p = safe_float(benchmark_prices.get("SPY"))
    qqq_p = safe_float(benchmark_prices.get("QQQ"))
    qld_p = safe_float(benchmark_prices.get("QLD"))

    n = 0
    for row in rows:
        ticker = (row.get("ticker") or "").upper()
        if not ticker:
            continue
        action_tag = row.get("action_tag")
        if action_tag not in RECORDED_ACTION_TAGS:
            continue

        md = row.get("market_data") or {}
        scores = row.get("scores") or {}
        entry_price = safe_float(md.get("current_price"))
        company_name = (
            row.get("name_ko") or row.get("name_en") or ""
        ).strip()

        # research 데이터에서 alpha_score / queue_type / thesis 추출
        research = research_map.get(ticker) or {}
        alpha = research.get("alpha_score") or {}
        eq = research.get("earnings_quality") or {}

        alpha_value = safe_float(alpha.get("alpha_score"))
        # Alpha Score 80+ 도 자동 기록 (Action Tag 가 Research Now 가 아니어도)
        # 또는 70+ 인데 Watchlist 인 경우도 — 사용자가 분석하도록
        # (RECORDED_ACTION_TAGS 에 이미 Watchlist 포함됨)

        # queue_type — discovery_scores 에서 매칭하거나 _promoted 메타에서
        queue_type = row.get("queue_type") or row.get("_queue_type")
        # _promoted 면 promoted_candidate 큐 표시 가능 (선택)

        thesis_type = row.get("company_type")
        core_thesis_text = (research.get("core_thesis") or row.get("core_thesis") or "")[:1500]
        key_risks = research.get("key_risks") or []
        if isinstance(key_risks, list):
            key_risks_list = [str(x)[:300] for x in key_risks[:8]]
        else:
            key_risks_list = []

        # follow_up_items — research_quality 또는 별도 키
        rq = research.get("research_quality") or {}
        follow_up = rq.get("follow_up_items") or research.get("follow_up_items") or []
        if isinstance(follow_up, list):
            follow_up_list = [str(x)[:300] for x in follow_up[:8]]
        else:
            follow_up_list = []

        data_confidence = alpha.get("data_confidence") or "Medium"

        reason_obj = {
            "components": alpha.get("components"),
            "alpha_rating_en": alpha.get("alpha_rating_en"),
            "alpha_rating_ko": alpha.get("alpha_rating_ko"),
            "is_provisional": alpha.get("is_provisional"),
            "earnings_durability_score": eq.get("earnings_durability_score"),
            "earnings_durability_tier": eq.get("earnings_durability_tier"),
            "is_curated": eq.get("is_curated"),
            "is_auto_profiled": eq.get("is_auto_profiled"),
            "scores_snapshot": {k: scores.get(k) for k in (
                "thesis_strength", "evidence_strength", "price_opportunity",
                "event_freshness", "financial_quality", "risk_control",
                "final_score",
            )},
        }

        try:
            db.record_alpha_decision(
                conn,
                run_id=run_id,
                date_iso=date_iso,
                ticker=ticker,
                company_name=company_name,
                action_tag=action_tag,
                final_score=safe_float(scores.get("final_score")),
                alpha_score=alpha_value,
                alpha_rating=alpha.get("alpha_rating_en"),
                queue_type=queue_type,
                thesis_type=thesis_type,
                core_thesis=core_thesis_text,
                key_risks=key_risks_list,
                follow_up_items=follow_up_list,
                entry_price=entry_price,
                spy_price=spy_p,
                qqq_price=qqq_p,
                qld_price=qld_p,
                data_confidence=data_confidence,
                reason=reason_obj,
                logic_version=logic_version,
            )
            n += 1
        except Exception as e:
            log.warning("[%s] decision record failed: %s", ticker, e)

    log.info("Auditor: recorded %d decisions for %s (run %s)", n, date_iso, run_id)
    return n


# ---------------------------------------------------------------------------
# Step 2 — 가격 lookup helper
# ---------------------------------------------------------------------------

def _price_on_or_after(history_df, target_date: _dt.date):
    """history (yfinance Adj Close DataFrame) 에서 target_date 이후 첫 거래일 종가."""
    if history_df is None or history_df.empty:
        return None
    try:
        # history index 는 보통 timezone-aware DatetimeIndex
        idx = history_df.index
        if hasattr(idx, "tz_localize") and idx.tz is not None:
            idx = idx.tz_localize(None)
        target_ts = _dt.datetime.combine(target_date, _dt.time.min)
        mask = idx >= target_ts
        if not mask.any():
            return None
        first_idx = mask.argmax()  # bool array — first True index
        # Adj Close 컬럼 우선, 없으면 Close
        if "Adj Close" in history_df.columns:
            return float(history_df["Adj Close"].iloc[first_idx])
        if "Close" in history_df.columns:
            return float(history_df["Close"].iloc[first_idx])
        return None
    except Exception:
        return None


def _price_on_or_before(history_df, target_date: _dt.date):
    """history 에서 target_date 이전 마지막 거래일 종가."""
    if history_df is None or history_df.empty:
        return None
    try:
        idx = history_df.index
        if hasattr(idx, "tz_localize") and idx.tz is not None:
            idx = idx.tz_localize(None)
        target_ts = _dt.datetime.combine(target_date, _dt.time.max)
        mask = idx <= target_ts
        if not mask.any():
            return None
        # 마지막 True
        last_pos = len(mask) - 1 - mask[::-1].argmax()
        if "Adj Close" in history_df.columns:
            return float(history_df["Adj Close"].iloc[last_pos])
        if "Close" in history_df.columns:
            return float(history_df["Close"].iloc[last_pos])
        return None
    except Exception:
        return None


def _max_drawdown_max_gain(history_df, start_date: _dt.date, end_date: _dt.date):
    """기간 내 max drawdown (음수) + max gain (양수). 둘 다 entry 대비 비율."""
    if history_df is None or history_df.empty:
        return None, None, None
    try:
        idx = history_df.index
        if hasattr(idx, "tz_localize") and idx.tz is not None:
            idx = idx.tz_localize(None)
        start_ts = _dt.datetime.combine(start_date, _dt.time.min)
        end_ts = _dt.datetime.combine(end_date, _dt.time.max)
        mask = (idx >= start_ts) & (idx <= end_ts)
        if not mask.any():
            return None, None, None
        col = "Adj Close" if "Adj Close" in history_df.columns else "Close"
        prices = history_df[col].iloc[mask]
        if prices.empty:
            return None, None, None
        entry = float(prices.iloc[0])
        if entry <= 0:
            return None, None, None
        rels = (prices.values / entry) - 1.0
        max_gain = float(max(rels))
        max_dd = float(min(rels))
        # 변동성 = 일별 수익률 표준편차 × sqrt(252) 단순 추정
        try:
            import numpy as _np
            diffs = (prices.values[1:] / prices.values[:-1]) - 1.0
            vol = float(_np.std(diffs) * (252 ** 0.5))
        except Exception:
            vol = None
        return max_dd, max_gain, vol
    except Exception:
        return None, None, None


# ---------------------------------------------------------------------------
# Step 3 — 단일 결정의 holding period 별 성과 계산
# ---------------------------------------------------------------------------

def _benchmark_history_cache() -> dict[str, Any]:
    """SPY/QQQ/QLD 의 max history 를 한 번만 fetch — 모든 결정에서 재사용."""
    out: dict[str, Any] = {}
    for sym in BENCHMARKS:
        try:
            hist = fetch_max_history(sym)
            out[sym] = hist
        except Exception as e:
            log.warning("benchmark %s history fetch failed: %s", sym, e)
            out[sym] = None
    return out


def _ticker_history(ticker: str):
    try:
        return fetch_max_history(ticker)
    except Exception as e:
        log.warning("[%s] history fetch failed: %s", ticker, e)
        return None


def _classify_hit(
    *,
    action_tag: str,
    abs_return: float | None,
    excess_qqq: float | None,
    excess_qld: float | None,
    holding_period: str,
    days_elapsed: int,
) -> str:
    """절대/상대 수익률 + Action Tag 의도로 hit_status 분류.

    분류 우선순위:
        1. 데이터 부족 → Inconclusive
        2. holding period 가 아직 짧음 (< 75% of target) → Working
        3. Action Tag = Avoid 계열 + 종목이 크게 상승 → Avoid Wrong
        4. Action Tag = Avoid + 종목 하락 → Avoid Correct
        5. Action Tag = Watchlist + 종목이 크게 상승 + QLD 도 outperform → Watchlist Missed
        6. Action Tag = Research Now / Quality Dislocation:
           - QQQ 또는 QLD outperform → Correct
           - 절대 -10%+ 손실 → Thesis Broken (Wrong)
           - 그 사이 → Too Early (1M 미만) 또는 Wrong (3M+)
        7. 그 외 → Inconclusive
    """
    if abs_return is None:
        return "Inconclusive"

    target_days = HOLDING_PERIODS_DAYS.get(holding_period, 30)
    if days_elapsed < target_days * 0.75:
        return "Working"

    # Avoid 계열
    if action_tag == "Avoid":
        if abs_return >= THRESH_AVOID_WRONG:
            return "Avoid Wrong"
        if abs_return < 0:
            return "Avoid Correct"
        return "Inconclusive"

    # Watchlist / Wait — 진입 안 함 → 종목이 크게 오르면 missed
    if action_tag in ("Watchlist", "Wait for Entry"):
        if abs_return >= 0.15 and (excess_qld is not None and excess_qld > 0):
            return "Watchlist Missed"
        if abs_return >= 0.10:
            return "Watchlist Missed"
        return "Inconclusive"

    # Need Thesis Check — 진입 보류 → 그 사이 큰 손실이면 thesis broken
    if action_tag == "Need Thesis Check":
        if abs_return <= -0.20:
            return "Thesis Broken"
        if abs_return >= 0.15 and (excess_qld is not None and excess_qld > 0):
            return "Watchlist Missed"
        return "Inconclusive"

    # Too Crowded — 진입 안 함 → 큰 추가 상승 시 missed, 큰 하락 시 correct
    if action_tag == "Too Crowded":
        if abs_return <= -0.15:
            return "Avoid Correct"  # 과열 회피가 옳았음
        if abs_return >= 0.15:
            return "Watchlist Missed"
        return "Inconclusive"

    # Research Now / Quality Dislocation — 적극 진입 의도
    if action_tag in ("Research Now", "Quality Dislocation"):
        # Thesis broken — 큰 손실 + QQQ 대비도 부진
        if abs_return <= -0.15 and (excess_qqq is None or excess_qqq < -0.05):
            return "Thesis Broken"
        # Outperform 명확
        if (excess_qqq is not None and excess_qqq >= THRESH_OUTPERFORM_QQQ) or \
           (excess_qld is not None and excess_qld >= THRESH_OUTPERFORM_QLD):
            return "Correct"
        # Underperform but not catastrophic
        if abs_return <= THRESH_UNDERPERFORM:
            if days_elapsed < HOLDING_PERIODS_DAYS["1M"]:
                return "Too Early"
            return "Wrong"
        # 절대 수익은 양수지만 QLD 못 이김
        if abs_return > 0 and (excess_qld is not None and excess_qld < -THRESH_OUTPERFORM_QLD):
            return "Wrong"  # 기회비용 발생
        return "Working"

    return "Inconclusive"


def update_performance_for_decision(
    conn,
    decision: dict | Any,
    *,
    bench_history: dict[str, Any],
    today: _dt.date | None = None,
) -> int:
    """단일 결정에 대해 holding period 별 성과 1 row 씩 upsert.

    decision: decision_log 행 (dict 또는 sqlite3.Row 호환)
    Returns: 추가/갱신된 holding period 수.
    """
    today = today or _dt.date.today()
    decision_id = int(decision["decision_id"])
    ticker = decision["ticker"]
    action_tag = decision["action_tag"] or ""
    decision_date_str = decision["date"]
    try:
        decision_date = _dt.date.fromisoformat(decision_date_str)
    except Exception:
        log.warning("[%s] invalid decision date: %s", ticker, decision_date_str)
        return 0

    entry_price = safe_float(decision["entry_price"]) or safe_float(decision["price"])
    if entry_price is None or entry_price <= 0:
        return 0

    spy_entry = safe_float(decision["spy_price"])
    qqq_entry = safe_float(decision["qqq_price"])
    qld_entry = safe_float(decision["qld_price"])

    # Ticker 가격 history (max)
    hist = _ticker_history(ticker)

    n = 0
    for period_label, n_days in HOLDING_PERIODS_DAYS.items():
        target_date = decision_date + _dt.timedelta(days=n_days)
        days_elapsed = (today - decision_date).days
        # 아직 도달 안 한 holding period — Working 으로만 기록
        if target_date > today:
            metrics = {
                "holding_period": period_label,
                "entry_price": entry_price,
                "current_price": None,
                "absolute_return": None,
                "spy_return": None, "qqq_return": None, "qld_return": None,
                "excess_return_vs_spy": None, "excess_return_vs_qqq": None,
                "excess_return_vs_qld": None,
                "max_drawdown_since_decision": None,
                "max_gain_since_decision": None,
                "volatility": None,
                "hit_status": "Working",
            }
            try:
                db.upsert_performance(conn, decision_id, target_date.isoformat(), metrics)
                n += 1
            except Exception as e:
                log.warning("[%s] upsert performance failed (%s): %s", ticker, period_label, e)
            continue

        # target_date 도달 — 가격 lookup
        ticker_price = _price_on_or_after(hist, target_date)
        if ticker_price is None:
            # target_date 이후 가격 없으면 가장 마지막 가격 시도
            ticker_price = _price_on_or_before(hist, today)

        spy_price = _price_on_or_after(bench_history.get("SPY"), target_date) or \
                    _price_on_or_before(bench_history.get("SPY"), today)
        qqq_price = _price_on_or_after(bench_history.get("QQQ"), target_date) or \
                    _price_on_or_before(bench_history.get("QQQ"), today)
        qld_price = _price_on_or_after(bench_history.get("QLD"), target_date) or \
                    _price_on_or_before(bench_history.get("QLD"), today)

        abs_return = None if ticker_price is None else (ticker_price / entry_price - 1.0)
        spy_return = None
        qqq_return = None
        qld_return = None
        if spy_price is not None and spy_entry:
            spy_return = spy_price / spy_entry - 1.0
        if qqq_price is not None and qqq_entry:
            qqq_return = qqq_price / qqq_entry - 1.0
        if qld_price is not None and qld_entry:
            qld_return = qld_price / qld_entry - 1.0

        excess_spy = (abs_return - spy_return) if (abs_return is not None and spy_return is not None) else None
        excess_qqq = (abs_return - qqq_return) if (abs_return is not None and qqq_return is not None) else None
        excess_qld = (abs_return - qld_return) if (abs_return is not None and qld_return is not None) else None

        max_dd, max_gain, vol = _max_drawdown_max_gain(hist, decision_date, min(target_date, today))

        hit = _classify_hit(
            action_tag=action_tag,
            abs_return=abs_return,
            excess_qqq=excess_qqq,
            excess_qld=excess_qld,
            holding_period=period_label,
            days_elapsed=days_elapsed,
        )

        metrics = {
            "holding_period": period_label,
            "entry_price": entry_price,
            "current_price": ticker_price,
            "absolute_return": abs_return,
            "spy_return": spy_return,
            "qqq_return": qqq_return,
            "qld_return": qld_return,
            "excess_return_vs_spy": excess_spy,
            "excess_return_vs_qqq": excess_qqq,
            "excess_return_vs_qld": excess_qld,
            "max_drawdown_since_decision": max_dd,
            "max_gain_since_decision": max_gain,
            "volatility": vol,
            "hit_status": hit,
            # 하위 호환
            "return_1w": abs_return if period_label == "1W" else None,
            "return_1m": abs_return if period_label == "1M" else None,
            "return_3m": abs_return if period_label == "3M" else None,
            "return_6m": abs_return if period_label == "6M" else None,
            "relative_return_spy": excess_spy,
            "relative_return_qqq": excess_qqq,
            "outcome_tag": hit,
        }
        try:
            db.upsert_performance(conn, decision_id, target_date.isoformat(), metrics)
            n += 1
        except Exception as e:
            log.warning("[%s] upsert performance failed (%s): %s", ticker, period_label, e)
    return n


def update_performance_tracking_all(
    conn,
    *,
    today: _dt.date | None = None,
    since_date: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Auto-recorded 결정 전체에 대해 holding period 별 성과 갱신.

    since_date: 'YYYY-MM-DD' — 그 이후의 결정만 재계산 (cron 부하 절감).
                 미지정 시 365일 이내 결정 전체.
    """
    today = today or _dt.date.today()
    if since_date is None:
        since_date = (today - _dt.timedelta(days=365)).isoformat()

    decisions = db.fetch_decisions_for_period(conn, since_date)
    if limit:
        decisions = decisions[:limit]

    log.info("Auditor: updating performance for %d decisions (since %s)",
             len(decisions), since_date)

    bench_hist = _benchmark_history_cache()

    updated = 0
    failed = 0
    for d in decisions:
        try:
            d_dict = dict(d) if not isinstance(d, dict) else d
            n = update_performance_for_decision(conn, d_dict,
                                                bench_history=bench_hist, today=today)
            if n > 0:
                updated += 1
        except Exception as e:
            log.warning("decision %s update failed: %s",
                        d_dict.get("decision_id") if isinstance(d_dict, dict) else "?", e)
            failed += 1

    return {"decisions": len(decisions), "updated": updated, "failed": failed}


# ---------------------------------------------------------------------------
# 외부에서 가져다 쓰는 helper (벤치마크 가격 fetch — run_research 에서 record 시 사용)
# ---------------------------------------------------------------------------

def fetch_benchmark_prices() -> dict[str, float | None]:
    """SPY / QQQ / QLD 현재가를 fetch — record_decisions_for_run 의 entry 가격 인자."""
    out: dict[str, float | None] = {}
    try:
        from .market_data import fetch_one
        for sym in BENCHMARKS:
            try:
                md = fetch_one(sym)
                if md.get("available"):
                    out[sym] = safe_float(md.get("current_price"))
                else:
                    out[sym] = None
            except Exception as e:
                log.warning("benchmark %s fetch_one failed: %s", sym, e)
                out[sym] = None
    except Exception as e:
        log.warning("fetch_benchmark_prices failed: %s", e)
    return out
