"""Alpha 리서치 파이프라인 진입점.

매일 한 번 실행하면 universe 전체에 대해 다음을 수행:
    1. universe 로드/시드
    2. 가격·재무 batch fetch
    3. 뉴스 종목별 fetch
    4. 이벤트 클러스터링 (curated + 뉴스)
    5~8. 이벤트 enrich (status / source_quality / staleness / thesis_impact)
    9. 6요소 스코어링
    10. 종목 리서치 본문 생성
    11. Daily Brief 생성
    12. performance_tracking 업데이트

모든 결과는 SQLite (data/alpha.db) 에 저장.
app.py 는 DB 만 조회한다.

사용법:
    python run_research.py                      # 전체
    python run_research.py --ticker NFLX        # 특정 종목만
    python run_research.py --skip-news          # 가격만 빠르게
    python run_research.py --dry-run            # DB 안 쓰고 출력만
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import database as db
from src.brief_generator import (
    daily_alerts,
    daily_check_items,
    daily_judgment,
    macro_issues,
    market_environment_blocks,
    select_top_picks,
)
from src.curated import recent_events as _curated_recent_events
from src.event_processor import (
    cluster_news_by_event,
    enrich_curated_event,
    thesis_impact_from,
)
from src.market_data import (
    fetch_market_proxies,
    fetch_universe as fetch_market_universe,
    market_summary_ko,
)
from src.news_fetcher import aggregate_importance, fetch_ticker_news
from src.scoring import assign_action_tag, classify_company_type, compute_scores
from src.stock_research import build_stock_research, short_rationale
from src.universe import load_universe
from src.utils import get_logger, today_kst

log = get_logger("run_research")


# ---------------------------------------------------------------------------
# 단계별 함수
# ---------------------------------------------------------------------------

def step_load_universe(conn, run_id: str, only_tickers: list[str] | None = None) -> list[dict]:
    db.seed_universe_from_csv(conn)
    rows = [dict(r) for r in db.fetch_universe(conn)]
    if only_tickers:
        rows = [r for r in rows if r["ticker"] in only_tickers]
    log.info("[1/12] universe loaded: %d", len(rows))
    return rows


def step_fetch_market_data(conn, run_id: str, universe: list[dict], date_iso: str) -> dict[str, dict]:
    tickers = [u["ticker"] for u in universe]
    log.info("[2/12] fetching market data for %d tickers...", len(tickers))
    md_map = fetch_market_universe(tickers)
    # DB 저장
    snapshots = []
    for t, md in md_map.items():
        snap = {**md, "ticker": t}
        snapshots.append(snap)
    db.upsert_price_snapshot(conn, run_id, date_iso, snapshots)
    avail = sum(1 for m in md_map.values() if m.get("available"))
    log.info("[2/12] price ok=%d / total=%d", avail, len(md_map))
    return md_map


def step_fetch_market_proxies(conn, run_id: str, date_iso: str) -> tuple[dict, str]:
    log.info("[2b/12] fetching market proxies (SPY/QQQ/...)")
    proxies = fetch_market_proxies()
    summary = market_summary_ko(proxies)
    return proxies, summary


def step_fetch_news(conn, run_id: str, universe: list[dict]) -> dict[str, list[dict]]:
    log.info("[3/12] fetching news for %d tickers...", len(universe))
    news_map: dict[str, list[dict]] = {}
    for u in universe:
        ticker = u["ticker"]
        try:
            news = fetch_ticker_news(ticker, name_en=u.get("name_en"), limit=5)
        except Exception as e:
            log.warning("[%s] news fetch failed: %s", ticker, e)
            news = []
        news_map[ticker] = news
        # DB 저장 (enrich는 aggregate_importance에서)
        if news:
            for n in news:
                n["ticker"] = ticker
            db.upsert_news(conn, run_id, news)
    return news_map


def step_build_events(
    conn, run_id: str, universe: list[dict], news_map: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """4~8 이벤트 처리 통합.

    - 큐레이션 이벤트 enrich
    - 뉴스 클러스터링 (현재는 종목별 가장 최신 이벤트 1개만 events 테이블 저장)
    - thesis_impact 자동 추론
    """
    log.info("[4-8/12] building events from curated + news clusters...")
    events_map: dict[str, list[dict]] = {}
    for u in universe:
        ticker = u["ticker"]
        ticker_events: list[dict] = []

        # (a) 큐레이션 이벤트
        for ev in _curated_recent_events(ticker):
            enriched = enrich_curated_event(ev)
            if not enriched.get("thesis_impact"):
                cls = ev.get("classification", "needs_check")
                cls_to_score = {
                    "strengthen": 1.5, "weaken": -1.5, "needs_check": 0,
                    "new_risk": -2.0, "noise": 0,
                }
                enriched["thesis_impact"] = thesis_impact_from(
                    enriched.get("status", "확인 필요"),
                    cls_to_score.get(cls, 0),
                    is_urgent=False,
                    staleness=enriched.get("staleness", "fresh"),
                )
            enriched["ticker"] = ticker
            enriched["is_curated"] = True
            enriched["event_title"] = ev.get("type", "")
            enriched["event_type"] = ev.get("type", "")
            enriched["event_date"] = ev.get("date")
            ticker_events.append(enriched)
            try:
                db.upsert_event(conn, run_id, enriched)
            except Exception as e:
                log.warning("[%s] curated event upsert failed: %s", ticker, e)

        # (b) 뉴스 클러스터 (대표 1개)
        clusters = cluster_news_by_event(news_map.get(ticker) or [], ticker=ticker)
        for c in clusters[:3]:  # 최대 3 클러스터
            ev_news = {
                "ticker": ticker,
                "event_title": c["title"][:200],
                "event_type": ", ".join(c.get("topics") or []),
                "event_date": c["latest"].get("published_at"),
                "last_updated": c["latest"].get("published_at"),
                "event_status": c["status"],
                "source_quality": c["source_quality"],
                "source_count": len(c["members"]),
                "confidence_level": c["confidence"],
                "staleness_flag": c["staleness"],
                "thesis_impact": c["thesis_impact"],
                "summary": c["title"][:300],
                "investment_implication": "",
                "check_items": "",
                "source_links": [m.get("link", "") for m in c["members"][:5]],
                "member_news_ids": [
                    db.make_news_id(ticker, m.get("link"), m.get("title"), m.get("published_at"))
                    for m in c["members"]
                ],
                "is_curated": False,
            }
            try:
                db.upsert_event(conn, run_id, ev_news)
            except Exception as e:
                log.debug("[%s] news event upsert failed: %s", ticker, e)
        events_map[ticker] = ticker_events
    log.info("[4-8/12] events built")
    return events_map


def step_calculate_scores(
    conn, run_id: str, date_iso: str,
    universe: list[dict], md_map: dict, news_map: dict, events_map: dict,
) -> dict[str, dict]:
    log.info("[9/12] calculating scores for %d tickers...", len(universe))
    score_map: dict[str, dict] = {}
    for u in universe:
        ticker = u["ticker"]
        md = md_map.get(ticker) or {}
        news = news_map.get(ticker) or []
        agg = aggregate_importance(news)
        curated_evs = events_map.get(ticker) or []

        row_context = {
            "ticker": ticker,
            "theme": u.get("theme"),
            "category": u.get("category"),
            "name_ko": u.get("name_ko"),
            "curated_events": curated_evs,
            "news_agg": agg,
            "market_data": md,
        }
        ctype = classify_company_type(row_context)
        row_context["company_type"] = ctype
        scores = compute_scores(md, u.get("theme", ""), agg, row_context=row_context)
        tag = assign_action_tag(md, scores, agg, u.get("category", ""), row_context=row_context)

        # rationale
        rationale = short_rationale({
            **u, **row_context, "scores": scores, "action_tag": tag, "company_type": ctype,
        })

        # DB 저장
        try:
            db.upsert_score(conn, run_id, date_iso, ticker, scores, ctype, tag, rationale)
        except Exception as e:
            log.warning("[%s] score upsert failed: %s", ticker, e)

        score_map[ticker] = {
            **scores, "company_type": ctype, "action_tag": tag, "rationale": rationale,
            "row_context": row_context,
        }
    log.info("[9/12] scores done")
    return score_map


def step_generate_stock_research(conn, run_id: str, date_iso: str, score_map: dict) -> int:
    log.info("[10/12] generating stock research bodies...")
    n = 0
    for ticker, s in score_map.items():
        rc = s["row_context"]
        if not (rc.get("market_data") or {}).get("available"):
            continue
        rc["scores"] = {k: v for k, v in s.items() if k not in ("row_context", "rationale")}
        rc["action_tag"] = s["action_tag"]
        rc["company_type"] = s["company_type"]
        try:
            research = build_stock_research(rc)
            db.upsert_stock_research(conn, run_id, date_iso, ticker, research)
            n += 1
        except Exception as e:
            log.warning("[%s] stock research failed: %s", ticker, e)
    log.info("[10/12] research done: %d", n)
    return n


def step_generate_daily_brief(
    conn, run_id: str, date_iso: str,
    universe: list[dict], score_map: dict, market_summary: str,
) -> dict:
    log.info("[11/12] generating daily brief...")

    # rows 형태로 재구성 (brief_generator 호환)
    rows: list[dict] = []
    for u in universe:
        ticker = u["ticker"]
        s = score_map.get(ticker) or {}
        rc = s.get("row_context") or {}
        if not rc:
            continue
        rows.append({
            **u, **rc,
            "scores": {k: v for k, v in s.items() if k not in ("row_context", "rationale")},
            "action_tag": s.get("action_tag", "Watchlist"),
            "company_type": s.get("company_type"),
            "news": rc.get("news") or [],
        })

    picks = select_top_picks(rows, n=5)
    judgment = daily_judgment(rows, picks)
    blocks = market_environment_blocks(market_summary)
    macros = macro_issues()
    alerts = daily_alerts(rows, n=3)
    checks = daily_check_items(picks, n=3)

    brief = {
        "headline": judgment,
        "market_environment": blocks,
        "macro_issues": macros,
        "top_stocks": [{"ticker": p["ticker"], "company_type": p.get("company_type"),
                        "action_tag": p.get("action_tag")} for p in picks],
        "alerts": alerts,
        "check_items": checks,
    }
    try:
        db.upsert_daily_brief(conn, run_id, date_iso, brief)
    except Exception as e:
        log.warning("daily_brief upsert failed: %s", e)
    log.info("[11/12] brief done: %d picks", len(picks))
    return brief


def step_update_performance_tracking(conn, run_id: str, today: _dt.date) -> int:
    """지난 결정들에 대해 1w/1m/3m 수익률 계산.

    구현: decision_log 의 결정 시점 가격 vs 현재가 비교.
    SPY/QQQ 상대 수익률은 향후 보강 (지금은 단순 수익률만).
    """
    log.info("[12/12] updating performance tracking...")
    decisions = db.fetch_decisions(conn, limit=200)
    n = 0
    today_iso = today.isoformat()
    for d in decisions:
        decision_id = d["decision_id"]
        ticker = d["ticker"]
        decision_date = d["date"]
        entry_price = d["price"]
        if not entry_price:
            continue
        # 현재가는 최신 price_snapshot에서
        latest = db.fetch_latest_price_snapshot(conn, ticker)
        if not latest or not latest[0]["current_price"]:
            continue
        current_price = latest[0]["current_price"]
        try:
            d_date = _dt.date.fromisoformat(decision_date)
        except Exception:
            continue
        days_held = (today - d_date).days
        if days_held <= 0:
            continue

        ret_total = (current_price / entry_price) - 1.0
        metrics = {
            "return_1w": ret_total if days_held <= 7 else None,
            "return_1m": ret_total if days_held <= 30 else None,
            "return_3m": ret_total if days_held <= 90 else None,
            "return_6m": ret_total if days_held <= 180 else None,
            "outcome_tag": (
                "맞음" if (
                    (d["action_tag"] in ("Research Now", "Quality Dislocation") and ret_total >= 0.05)
                    or (d["action_tag"] == "Avoid" and ret_total <= -0.05)
                ) else (
                    "틀림" if (
                        (d["action_tag"] in ("Research Now", "Quality Dislocation") and ret_total <= -0.10)
                        or (d["action_tag"] == "Avoid" and ret_total >= 0.10)
                    ) else "진행 중"
                )
            ),
        }
        try:
            db.upsert_performance(conn, decision_id, today_iso, metrics)
            n += 1
        except Exception as e:
            log.debug("perf upsert failed for decision %s: %s", decision_id, e)
    log.info("[12/12] perf tracking updated: %d decisions", n)
    return n


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def run_research(
    only_tickers: list[str] | None = None,
    fetch_news: bool = True,
    dry_run: bool = False,
    today: _dt.date | None = None,
) -> dict[str, Any]:
    today = today or _dt.date.today()
    date_iso = today.isoformat()

    if dry_run:
        log.info("=== DRY RUN — DB 안 씀 ===")
        # dry-run은 in-memory DB
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db.init_schema(conn)
    else:
        conn = db.open_db()

    run_id = db.create_run_id()
    db.log_run_start(conn, run_id)

    summary: dict[str, Any] = {
        "run_id": run_id, "date": date_iso, "ok": False,
        "universe": 0, "price_ok": 0, "news_ok": 0, "scores": 0, "research": 0,
    }
    try:
        unis = step_load_universe(conn, run_id, only_tickers=only_tickers)
        summary["universe"] = len(unis)

        md_map = step_fetch_market_data(conn, run_id, unis, date_iso)
        summary["price_ok"] = sum(1 for m in md_map.values() if m.get("available"))

        proxies, market_summary = step_fetch_market_proxies(conn, run_id, date_iso)

        if fetch_news:
            news_map = step_fetch_news(conn, run_id, unis)
            summary["news_ok"] = sum(1 for n in news_map.values() if n)
        else:
            news_map = {u["ticker"]: [] for u in unis}

        events_map = step_build_events(conn, run_id, unis, news_map)
        score_map = step_calculate_scores(
            conn, run_id, date_iso, unis, md_map, news_map, events_map
        )
        summary["scores"] = len(score_map)

        n_research = step_generate_stock_research(conn, run_id, date_iso, score_map)
        summary["research"] = n_research

        step_generate_daily_brief(conn, run_id, date_iso, unis, score_map, market_summary)

        step_update_performance_tracking(conn, run_id, today)

        db.log_run_finish(
            conn, run_id, status="success",
            success_count=summary["price_ok"],
            error_summary=None,
        )
        summary["ok"] = True
    except Exception as e:
        log.error("run_research failed: %s", e)
        traceback.print_exc()
        db.log_run_finish(conn, run_id, status="failed", error_summary=str(e))
        summary["error"] = str(e)
    finally:
        if not dry_run:
            conn.commit()
        conn.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Alpha 리서치 파이프라인")
    p.add_argument("--ticker", action="append", help="특정 종목만 처리 (반복 사용 가능)")
    p.add_argument("--skip-news", action="store_true", help="뉴스 fetch 생략 (가격만 빠르게)")
    p.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 출력만")
    args = p.parse_args(argv)

    res = run_research(
        only_tickers=args.ticker,
        fetch_news=not args.skip_news,
        dry_run=args.dry_run,
    )
    print("\n=== run_research 완료 ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
