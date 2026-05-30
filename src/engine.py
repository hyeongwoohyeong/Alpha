"""파이프라인 오케스트레이터.

1) 유니버스 → yf.download() 배치 가격 수집
2) 종목별 뉴스 수집 (Google News RSS)
3) 스코어링 + Action Tag
4) (옵션) daily_snapshots.csv 저장
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from .market_data import (
    fetch_market_proxies,
    fetch_one,
    fetch_universe,
    market_summary_ko,
)
from .news_fetcher import (
    aggregate_importance,
    fetch_ticker_news,
)
from .scoring import assign_action_tag, classify_company_type, compute_scores
from .universe import load_universe
from .curated import recent_events as _curated_recent_events
from .event_processor import enrich_curated_event, thesis_impact_from
from .utils import (
    DAILY_SNAPSHOTS_CSV,
    DECISION_LOG_CSV,
    ensure_data_dir,
    get_logger,
    now_iso,
    today_kst,
)

log = get_logger("engine")


def build_rows(
    progress_cb: Callable[[int, int, str], None] | None = None,
    fetch_news: bool = True,
    universe: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """유니버스 전체에 대해 row 리스트를 만든다."""
    uni = universe if universe is not None else load_universe()
    tickers = [r["ticker"] for r in uni]
    total = len(uni)

    if progress_cb:
        progress_cb(0, total, "주가 데이터 배치 수집")

    md_map = fetch_universe(tickers)

    # 뉴스 fetch 병렬화 — Google News RSS 가 종목당 2~5s 라 순차 처리하면
    # 42 종목 × 3s ≈ 2분 이상 걸렸음. ThreadPoolExecutor 로 동시 ~10 개 fetch
    # 하면 전체가 10~20s 수준으로 줄어든다. RSS 는 I/O bound 라 GIL 영향 거의 없음.
    news_map: dict[str, list[dict[str, Any]]] = {}
    if fetch_news:
        if progress_cb:
            progress_cb(0, total, "뉴스 병렬 수집")
        eligible = [r for r in uni
                    if (md_map.get(r["ticker"]) or {}).get("available")]
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(
                    fetch_ticker_news,
                    r["ticker"], r.get("name_en"), 5,
                ): r["ticker"]
                for r in eligible
            }
            # 전체 안전망 — 어떤 future 가 정체돼도 60s 안에 빠져나옴.
            # 개별 future 에도 .result(timeout=15) 로 hang 차단 (RSS 는 _google_news
            # 가 이미 requests timeout=8 걸어두지만 belt+suspenders).
            try:
                completed_iter = as_completed(futures, timeout=60)
                for fut in completed_iter:
                    t = futures[fut]
                    try:
                        news_map[t] = fut.result(timeout=15)
                    except Exception as e:
                        log.warning("[%s] 뉴스 수집 실패: %s", t, e)
                        news_map[t] = []
            except Exception as e:
                # as_completed timeout — 일부 미완료. 미완료는 빈 뉴스로 처리.
                log.warning("뉴스 병렬 수집 전체 timeout — 미완료 종목 skip: %s", e)
                for fut, t in futures.items():
                    if t not in news_map:
                        news_map[t] = []

    rows: list[dict[str, Any]] = []
    for i, row in enumerate(uni, start=1):
        ticker = row["ticker"]
        if progress_cb:
            progress_cb(i, total, ticker)
        md = md_map.get(ticker) or fetch_one(ticker)
        news = news_map.get(ticker, [])
        agg = aggregate_importance(news)

        # 큐레이션 이벤트 enrich (status/staleness/thesis_impact 자동)
        curated_evs_raw = _curated_recent_events(ticker)
        curated_evs: list[dict[str, Any]] = []
        for ev in curated_evs_raw:
            enriched = enrich_curated_event(ev)
            # thesis_impact 가 큐레이션에 없으면 자동 추론
            if not enriched.get("thesis_impact"):
                cls = ev.get("classification", "needs_check")
                cls_to_score = {"strengthen": 1.5, "weaken": -1.5, "needs_check": 0,
                                "new_risk": -2.0, "noise": 0}
                enriched["thesis_impact"] = thesis_impact_from(
                    enriched.get("status", "확인 필요"),
                    cls_to_score.get(cls, 0),
                    is_urgent=False,
                    staleness=enriched.get("staleness", "fresh"),
                )
            curated_evs.append(enriched)

        # 스코어링용 row_context (curated_events 포함)
        row_context = {
            "ticker": ticker,
            "theme": row["theme"],
            "category": row["category"],
            "name_ko": row.get("name_ko", ""),
            "curated_events": curated_evs,
            "news_agg": agg,
            "market_data": md,
        }
        # company_type 동적 결정
        ctype = classify_company_type(row_context)
        row_context["company_type"] = ctype

        scores = compute_scores(md, row["theme"], agg, row_context=row_context)
        tag = assign_action_tag(md, scores, agg, row["category"], row_context=row_context)
        rows.append(
            {
                **row,
                "market_data": md,
                "news": news,
                "news_agg": agg,
                "curated_events": curated_evs,
                "company_type": ctype,
                "scores": scores,
                "action_tag": tag,
            }
        )
    return rows


def fetch_market_context() -> tuple[dict[str, dict[str, Any]], str]:
    proxies = fetch_market_proxies()
    return proxies, market_summary_ko(proxies)


def diagnose_data_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """데이터 수집 진단 요약.

    UI에서 모든 종목이 Data Unavailable일 때 원인 surfacing 용.
    """
    total = len(rows)
    if total == 0:
        return {"total": 0, "available": 0, "errors": {}, "all_failed": True}
    avail = sum(1 for r in rows if (r.get("market_data") or {}).get("available"))
    errors: dict[str, int] = {}
    for r in rows:
        md = r.get("market_data") or {}
        if md.get("available"):
            continue
        e = (md.get("error") or "unknown")[:120]
        errors[e] = errors.get(e, 0) + 1
    sample_error = max(errors.items(), key=lambda kv: kv[1])[0] if errors else None
    return {
        "total": total,
        "available": avail,
        "errors": errors,
        "sample_error": sample_error,
        "all_failed": avail == 0,
    }


# ---------------------------------------------------------------------------
# Snapshot / Decision 저장
# ---------------------------------------------------------------------------

def append_snapshot(rows: list[dict[str, Any]]) -> int:
    ensure_data_dir()
    today = today_kst()
    new_lines = 0
    write_header = not DAILY_SNAPSHOTS_CSV.exists() or DAILY_SNAPSHOTS_CSV.stat().st_size == 0

    with DAILY_SNAPSHOTS_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(
                [
                    "date",
                    "ticker",
                    "price",
                    "drawdown",
                    "final_score",
                    "action_tag",
                    "top_news_titles",
                    "created_at",
                ]
            )
        for r in rows:
            md = r.get("market_data") or {}
            sc = r.get("scores") or {}
            agg = r.get("news_agg") or {}
            w.writerow(
                [
                    today,
                    r["ticker"],
                    md.get("current_price") if md.get("available") else "",
                    md.get("drawdown_from_52w_high") if md.get("available") else "",
                    sc.get("final_score") if sc.get("available") else "",
                    r.get("action_tag", ""),
                    " | ".join(agg.get("top_titles", []) or []),
                    now_iso(),
                ]
            )
            new_lines += 1
    return new_lines


def append_decision(row: dict[str, Any], reason: str | None = None) -> None:
    ensure_data_dir()
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    today = today_kst()
    write_header = not DECISION_LOG_CSV.exists() or DECISION_LOG_CSV.stat().st_size == 0
    with DECISION_LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["date", "ticker", "action_tag", "reason", "price", "final_score"])
        w.writerow(
            [
                today,
                row["ticker"],
                row.get("action_tag", ""),
                reason or "",
                md.get("current_price") if md.get("available") else "",
                sc.get("final_score") if sc.get("available") else "",
            ]
        )
