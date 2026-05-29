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
import os
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
    overnight_briefing,
    market_environment_blocks,
    select_top_picks,
)
from src.config import load_config, make_budget
from src.curated import recent_events as _curated_recent_events
from src.discovery import run_discovery, select_discovery_candidates
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
from src.news_summarizer import summarize_news_to_korean
from src.promotion import run_promotion, select_promoted
from src.scoring import assign_action_tag, classify_company_type, compute_scores
from src.stock_research import build_stock_research, short_rationale
from src.universe import load_universe, load_wide_universe
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
    log.info("[1/15] core universe loaded: %d", len(rows))
    return rows


def step_load_wide_universe(conn, run_id: str, limit: int = 1500) -> list[dict]:
    """Wide universe (Russell 3000 sample) 로드."""
    rows = load_wide_universe()
    if limit and len(rows) > limit:
        rows = rows[:limit]
    log.info("[2/15] wide universe loaded: %d", len(rows))
    return rows


def step_fetch_wide_market_data(
    conn, run_id: str, wide_universe: list[dict], date_iso: str,
    skip: bool = False,
    chunk_size: int = 40,
    chunk_sleep_sec: float = 2.0,
) -> dict[str, dict]:
    """Wide universe 가격/재무 batch fetch — chunked + retry.

    yfinance 가 데이터센터 IP 의 대량 요청을 차단하는 경우가 많아 chunk 로 끊어 호출.
    skip=True 면 빈 dict (개발/테스트용).
    """
    import time as _time
    if skip or not wide_universe:
        log.info("[3/15] wide market data skipped")
        return {}
    tickers = [u["ticker"] for u in wide_universe]
    n = len(tickers)
    n_chunks = (n + chunk_size - 1) // chunk_size
    log.info(
        "[3/15] fetching wide market data for %d tickers (%d chunks of %d)...",
        n, n_chunks, chunk_size,
    )
    md_map: dict[str, dict] = {}
    for i in range(0, n, chunk_size):
        chunk = tickers[i:i + chunk_size]
        chunk_idx = i // chunk_size + 1
        try:
            # enrich=True 로 시총/PER 등 메타데이터까지 보강 (필터링용)
            res = fetch_market_universe(chunk, period="1y", enrich=True)
            md_map.update(res)
            avail = sum(1 for m in res.values() if m.get("available"))
            log.info(
                "[3/15] chunk %d/%d: ok=%d/%d (cumulative %d/%d)",
                chunk_idx, n_chunks, avail, len(chunk), len(md_map), n,
            )
        except Exception as e:
            log.warning("[3/15] chunk %d/%d failed: %s", chunk_idx, n_chunks, e)
            for t in chunk:
                md_map.setdefault(t, {"available": False, "error": str(e)})
        # rate-limit 회피
        if chunk_idx < n_chunks:
            _time.sleep(chunk_sleep_sec)

    avail = sum(1 for m in md_map.values() if m.get("available"))
    log.info("[3/15] wide market ok=%d / total=%d", avail, len(md_map))
    return md_map


def step_run_discovery(
    conn, run_id: str, date_iso: str,
    wide_universe: list[dict], md_map: dict[str, dict], top_k: int = 80,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Wide Scan → Discovery Candidate (큐별 시그널 + 통합 선정) + DB 저장."""
    if not wide_universe or not md_map:
        log.info("[4/15] discovery skipped (no wide universe or md_map)")
        return {}, []
    log.info("[4/15] running discovery on %d wide tickers...", len(wide_universe))

    by_queue = run_discovery(wide_universe, md_map)
    discovery_candidates = select_discovery_candidates(by_queue, top_k=top_k)

    # discovery_scores 저장
    for queue_type, items in by_queue.items():
        for it in items[:50]:  # 큐별 상위 50개만 저장 (DB 부담 줄임)
            try:
                db.upsert_discovery_score(
                    conn, run_id, date_iso,
                    ticker=it["ticker"],
                    queue_type=queue_type,
                    score=it["score"],
                    rank=it.get("rank") or 0,
                    signal_summary=it.get("signal_summary") or "",
                    key_metrics=it.get("key_metrics"),
                )
            except Exception as e:
                log.debug("[%s] discovery_score upsert failed: %s", it["ticker"], e)
    conn.commit()
    log.info("[4/15] discovery done: candidates=%d", len(discovery_candidates))
    return by_queue, discovery_candidates


def step_run_promotion(
    conn, run_id: str, date_iso: str,
    discovery_candidates: list[dict],
    md_map: dict[str, dict],
    core_tickers: set[str],
    cfg,
    promote_k: int = 15,
) -> list[dict]:
    """Promotion — 뉴스 fetch + 요약 + Promotion Score + 상위 K Promoted Candidate."""
    if not discovery_candidates:
        log.info("[5-7/15] promotion skipped — no discovery candidates")
        return []
    log.info("[5-7/15] promoting %d discovery candidates (core 제외)...", len(discovery_candidates))

    # 캐시-인지 summarize_fn (article_summaries 사용)
    def _summarize_with_conn(news_item, stock_context=None, budget=None, cfg=None):
        return summarize_news_to_korean(
            news_item, stock_context=stock_context, budget=budget, cfg=cfg, conn=conn,
        )

    promo = run_promotion(
        discovery_candidates,
        md_map=md_map,
        core_tickers=core_tickers,
        cfg=cfg,
        fetch_news_fn=fetch_ticker_news,
        summarize_fn=_summarize_with_conn,
        news_per_ticker=cfg.news_per_discovery_ticker,
    )
    promoted = select_promoted(promo, k=promote_k)

    # promotion_candidates 저장 — 상위 50개까지
    promoted_set = {p["ticker"] for p in promoted}
    for r in promo[:50]:
        flag = r["ticker"] in promoted_set
        try:
            db.upsert_promotion_candidate(conn, run_id, date_iso, r["ticker"], {
                **r, "promoted_to_deep_dive": flag,
            })
        except Exception as e:
            log.debug("[%s] promotion_candidate upsert failed: %s", r["ticker"], e)
    conn.commit()
    log.info("[5-7/15] promotion done: scored=%d promoted=%d", len(promo), len(promoted))
    return promoted


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
    log.info("[9/15] fetching news for %d deep-dive tickers...", len(universe))
    news_map: dict[str, list[dict]] = {}
    summarized = 0
    cfg = load_config()
    budget = make_budget(cfg)  # run 단위 LLM 호출 한도 공유
    for u in universe:
        ticker = u["ticker"]
        try:
            news = fetch_ticker_news(ticker, name_en=u.get("name_en"), limit=5)
        except Exception as e:
            log.warning("[%s] news fetch failed: %s", ticker, e)
            news = []
        news_map[ticker] = news

        if not news:
            continue

        for n in news:
            n["ticker"] = ticker
            n["news_id"] = db.make_news_id(
                ticker, n.get("link"), n.get("title"), n.get("published_at")
            )
            try:
                summary_payload = summarize_news_to_korean(
                    n, stock_context=u, budget=budget, cfg=cfg, conn=conn,
                )
                n.update({
                    "detailed_summary_ko": summary_payload.get("detailed_summary_ko"),
                    "investment_implication_ko": summary_payload.get("investment_implication_ko"),
                    "thesis_impact_ko": summary_payload.get("thesis_impact_ko"),
                    "confidence_level_ko": summary_payload.get("confidence_level_ko"),
                    "body_excerpt": summary_payload.get("body_excerpt"),
                    "key_points_ko": summary_payload.get("key_points_ko"),
                    "follow_up_items_ko": summary_payload.get("follow_up_items_ko"),
                    "content_availability": summary_payload.get("content_availability"),
                })
                summarized += 1
            except Exception as e:
                log.debug("[%s] summarize failed: %s", ticker, e)

        db.upsert_news(conn, run_id, news)
    log.info("[9/15] news ok=%d summarized=%d budget_used=%d/%d",
             sum(1 for v in news_map.values() if v), summarized,
             budget.used, budget.max_calls)
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


def step_generate_stock_research(
    conn, run_id: str, date_iso: str, score_map: dict
) -> tuple[int, dict[str, dict]]:
    """stock_research 빌드 + DB 저장 + 메모리 dict 반환 (Auditor 가 alpha_score 사용).

    Returns: (n_built, research_map: ticker → research dict)
    """
    log.info("[10/12] generating stock research bodies...")
    n = 0
    research_map: dict[str, dict] = {}
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
            research_map[ticker] = research
            n += 1
        except Exception as e:
            log.warning("[%s] stock research failed: %s", ticker, e)
    log.info("[10/12] research done: %d", n)
    return n, research_map


def step_auto_curate(
    conn,
    run_id: str,
    date_iso: str,
    *,
    promoted: list[dict],
    score_map: dict,
    research_map: dict[str, dict],
    cfg,
    budget,
    max_age_days: int = 60,
    max_calls_per_run: int = 5,
) -> dict[str, int]:
    """Auto-Curation — 큐레이션 미등록 종목의 자동 LLM 큐레이션 생성.

    선정 우선순위 (사용자 권장안 A — Promoted Candidate 5 종목/일):
        1. Promoted Candidate (Discovery → Promotion 통과한 종목 — wide universe)
        2. (확장 시) Daily Brief Top picks 중 큐레이션 미등록
        3. (확장 시) Alpha Score >= 70 인데 큐레이션 미등록

    캐시 정책:
        - max_age_days (default 60일) 이내 fresh 한 종목 skip
        - max_calls_per_run (default 5) 으로 LLM 호출 상한
        - 전역 LLM budget (cfg.max_llm_calls_per_run) 도 동시 적용

    Returns: {"candidates": int, "cache_hits": int, "generated": int, "failed": int,
              "total_cost_usd": float}
    """
    log.info("[AC] auto-curation 시작 (max_age=%d일, budget=%d)",
             max_age_days, max_calls_per_run)

    if not cfg.llm_enabled:
        log.info("[AC] LLM 비활성화 — auto-curation skip")
        return {"candidates": 0, "cache_hits": 0, "generated": 0, "failed": 0,
                "total_cost_usd": 0.0}

    if not os.environ.get("OPENAI_API_KEY"):
        log.info("[AC] OPENAI_API_KEY 미설정 — auto-curation skip")
        return {"candidates": 0, "cache_hits": 0, "generated": 0, "failed": 0,
                "total_cost_usd": 0.0}

    # 1) 큐레이션 대상 종목 추리기 — 우선순위 순서로
    from src.curated import EARNINGS_QUALITY_KO, INVESTMENT_THESIS_KO
    from src.auto_curation import generate_auto_curation

    curated_set = set(EARNINGS_QUALITY_KO.keys()) | set(INVESTMENT_THESIS_KO.keys())

    candidates: list[tuple[str, str | None]] = []  # (ticker, queue_type)
    seen: set[str] = set()

    # 1-1. Promoted Candidate
    for p in promoted or []:
        t = (p.get("ticker") or "").upper()
        if not t or t in seen or t in curated_set:
            continue
        seen.add(t)
        candidates.append((t, p.get("queue_type")))

    # 1-2. (보강) Top picks / High Alpha 추후 추가 — 지금은 Promoted 만

    log.info("[AC] %d candidates (curated 제외, 중복 제거)", len(candidates))

    # 2) 각 후보에 대해 캐시 hit / fresh 검사 + 신규 생성
    cache_hits = 0
    generated = 0
    failed = 0
    total_cost = 0.0

    calls_used = 0
    for ticker, queue_type in candidates:
        if calls_used >= max_calls_per_run:
            log.info("[AC] max_calls_per_run %d 도달 — 나머지 %d 종목 다음 run 으로",
                     max_calls_per_run, len(candidates) - calls_used)
            break

        # 캐시 fresh ?
        if db.auto_curation_is_fresh(conn, ticker, max_age_days=max_age_days):
            cache_hits += 1
            log.debug("[AC] %s cache hit (skip)", ticker)
            continue

        # budget 확인
        if not budget.can_call():
            log.info("[AC] global LLM budget 소진 — 나머지 %d 종목 다음 run 으로",
                     len(candidates) - calls_used)
            break

        # 시장 데이터에서 market_cap 조회 (있으면)
        mc = None
        if ticker in score_map:
            md = (score_map[ticker].get("row_context") or {}).get("market_data") or {}
            mc = md.get("market_cap")

        # 생성 시도
        try:
            log.info("[AC] generating curation for %s (queue=%s)", ticker, queue_type)
            result = generate_auto_curation(
                conn, ticker, market_cap=mc, force=False, max_age_days=max_age_days,
            )
            calls_used += 1
            budget.record()
            if result is None:
                failed += 1
                continue
            generated += 1
            # 비용 가져오기
            row = db.fetch_auto_curation(conn, ticker)
            if row:
                total_cost += float(row["cost_estimate_usd"] or 0)
        except Exception as e:
            log.warning("[AC] %s 생성 실패: %s", ticker, e)
            failed += 1

    summary = {
        "candidates": len(candidates),
        "cache_hits": cache_hits,
        "generated": generated,
        "failed": failed,
        "total_cost_usd": round(total_cost, 4),
    }
    log.info("[AC] done: %s", summary)
    return summary


def step_record_alpha_decisions(
    conn, run_id: str, date_iso: str,
    universe: list[dict], score_map: dict,
    research_map: dict[str, dict],
    promoted_queue_map: dict[str, str] | None = None,
) -> int:
    """Logic Auditor — Alpha 의 매일 자동 판단을 decision_log 에 기록.

    벤치마크 (SPY/QQQ/QLD) 가격을 fetch 해 entry 시점 가격으로 함께 저장.
    """
    from src.performance_tracker import (
        record_decisions_for_run,
        fetch_benchmark_prices,
    )
    log.info("[Auditor] recording alpha decisions...")

    # rows 재구성 (brief_generator 재사용 형태)
    rows: list[dict] = []
    promoted_queue_map = promoted_queue_map or {}
    for u in universe:
        ticker = u["ticker"]
        s = score_map.get(ticker) or {}
        rc = s.get("row_context") or {}
        if not rc:
            continue
        action_tag = s.get("action_tag", "Watchlist")
        rows.append({
            **u, **rc,
            "scores": {k: v for k, v in s.items() if k not in ("row_context", "rationale")},
            "action_tag": action_tag,
            "company_type": s.get("company_type"),
            "queue_type": promoted_queue_map.get(ticker),
        })

    bench_prices = fetch_benchmark_prices()
    n = record_decisions_for_run(
        conn,
        run_id=run_id,
        date_iso=date_iso,
        rows=rows,
        research_map=research_map,
        benchmark_prices=bench_prices,
        logic_version="v1.0",
    )
    log.info("[Auditor] %d decisions recorded (SPY=%s, QQQ=%s, QLD=%s)",
             n, bench_prices.get("SPY"), bench_prices.get("QQQ"), bench_prices.get("QLD"))
    return n


def step_generate_daily_brief(
    conn, run_id: str, date_iso: str,
    universe: list[dict], score_map: dict, market_summary: str,
    proxies: dict | None = None,
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

    # 금일 핵심 판단 — LLM 합성 (그날 picks + 시장 데이터 근거). 실패 시 daily_judgment
    # 가 룰 기반 템플릿으로 자동 fallback.
    try:
        from src.market_env_summarizer import generate_daily_judgment
        avoid_count = sum(1 for r in rows if r.get("action_tag") == "Avoid")
        generate_daily_judgment(conn, date_iso, picks, proxies, avoid_count=avoid_count)
    except Exception as e:
        log.warning("금일 핵심 판단 LLM 합성 실패: %s", e)

    judgment = daily_judgment(rows, picks)
    blocks = market_environment_blocks(market_summary)
    overnight = overnight_briefing()
    alerts = daily_alerts(rows, n=3)
    checks = daily_check_items(picks, n=3)

    brief = {
        "headline": judgment,
        "market_environment": blocks,
        "overnight_briefing": overnight,
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


def step_market_regime(conn, run_id: str, date_iso: str) -> dict:
    """Portfolio Regime — 매크로/시장 데이터 → Overheat Score → regime →
    portfolio mode → crash deployment plan → DB 저장.

    외부 데이터(FRED/yfinance) 실패해도 파이프라인 전체가 죽지 않게 try/except.
    부분 실패 시 sub-score 는 '확인 필요'(None) 로 저장된다.
    """
    log.info("[Regime] portfolio regime 평가 시작...")
    try:
        from src.macro_data import collect_regime_inputs
        from src.market_regime import build_market_regime
        from src.beta_allocation import classify_portfolio_mode
        from src.crash_deployment import generate_deployment_plan

        data = collect_regime_inputs()
        regime = build_market_regime(data)

        mode = classify_portfolio_mode(
            regime.get("current_regime"),
            regime.get("market_overheat_score"),
        )

        # Phase 3 — Howard Marks 사이클 심리 + Buffett 기회 필터
        cycle_psych: dict = {}
        buffett: dict = {}
        try:
            from src.marks_cycle import evaluate_cycle_psychology
            cycle_psych = evaluate_cycle_psychology(regime)
        except Exception as e:
            log.warning("[Regime] 사이클 심리 평가 실패 — skip: %s", e)
        try:
            from src.buffett_filter import evaluate_buffett_opportunity
            buffett = evaluate_buffett_opportunity(regime)
        except Exception as e:
            log.warning("[Regime] Buffett 기회 필터 평가 실패 — skip: %s", e)

        # crash deployment — QQQ 가격이력 사용
        qqq_hist = ((data.get("etf") or {}).get("QQQ") or {}).get("history")
        from src.crash_deployment import calculate_nasdaq_drawdown_from_high
        dd = calculate_nasdaq_drawdown_from_high(qqq_hist)
        if dd is None:
            dd = regime.get("qqq_drawdown_from_high")
        # 실증 데이터 사다리 — entry_timing_buckets 가 있으면 권장 수단을 데이터로.
        # 데이터가 없으면 cycle_rec=None 으로 graceful fallback (zone 의 하드코드).
        cycle_rec = None
        try:
            from src.market_cycle_analyzer import recommend_current_entry
            cycle_rec = recommend_current_entry(conn, "QQQ")
        except Exception as e:
            log.debug("[Regime] recommend_current_entry 실패 — fallback: %s", e)
        plan = generate_deployment_plan(
            dd, regime.get("credit_stress_status"), cycle_recommendation=cycle_rec)

        # DB 저장 — market_regime
        db.upsert_market_regime(conn, date_iso, {
            "market_overheat_score": regime.get("market_overheat_score"),
            "current_regime": regime.get("current_regime"),
            "valuation_stretch_score": regime.get("valuation_stretch_score"),
            "sentiment_speculation_score": regime.get("sentiment_speculation_score"),
            "market_concentration_score": regime.get("market_concentration_score"),
            "liquidity_credit_score": regime.get("liquidity_credit_score"),
            "earnings_revision_risk_score": regime.get("earnings_revision_risk_score"),
            "technical_extension_score": regime.get("technical_extension_score"),
            # cycle_psychology / buffett_opportunity — Phase 3
            "cycle_psychology_score": cycle_psych.get("cycle_psychology_score"),
            "buffett_opportunity_score": buffett.get("buffett_opportunity_score"),
            "portfolio_mode": mode.get("portfolio_mode"),
            "recommended_beta_level": mode.get("recommended_beta_level"),
            "commentary_ko": regime.get("commentary_ko"),
        })

        # DB 저장 — crash_deployment_plan
        db.upsert_crash_deployment_plan(conn, date_iso, {
            "qqq_drawdown_from_high": plan.get("qqq_drawdown_from_high"),
            "deployment_zone": plan.get("deployment_zone"),
            "recommended_instrument": plan.get("recommended_instrument"),
            "suggested_action": plan.get("suggested_action"),
            "credit_stress_status": plan.get("credit_stress_status"),
            "liquidity_status": plan.get("liquidity_status"),
            "commentary_ko": plan.get("commentary_ko"),
        })

        log.info(
            "[Regime] regime=%s overheat=%s mode=%s zone=%s cycle=%s buffett=%s",
            regime.get("current_regime"), regime.get("market_overheat_score"),
            mode.get("portfolio_mode"), plan.get("deployment_zone"),
            cycle_psych.get("cycle_psychology_score"),
            buffett.get("buffett_opportunity_score"),
        )
        return {
            "ok": True,
            "regime": regime.get("current_regime"),
            "overheat": regime.get("market_overheat_score"),
            "portfolio_mode": mode.get("portfolio_mode"),
            "cycle_psychology": cycle_psych.get("cycle_psychology_score"),
            "buffett_opportunity": buffett.get("buffett_opportunity_score"),
        }
    except Exception as e:
        log.warning("[Regime] portfolio regime 평가 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_capital_efficiency(
    conn, run_id: str, date_iso: str,
    universe: list[dict], md_map: dict[str, dict],
) -> dict:
    """Capital Efficiency 시스템 (Phase 2) — parking 후보 스크리닝 +
    watchlist/portfolio 종목 profit protection + capital efficiency 계산 → DB 저장.

    외부 데이터(yfinance) 실패해도 파이프라인 전체가 죽지 않게 try/except.
    """
    log.info("[CapEff] Capital Efficiency 시스템 평가 시작...")
    try:
        from src.capital_efficiency import calculate_capital_efficiency_score
        from src.profit_protection import calculate_profit_protection_score
        from src.parking_strategy import screen_parking_candidates

        result = {"parking": 0, "profit_protection": 0, "capital_efficiency": 0}

        # ── QLD 컨텍스트 (capital efficiency 의 QLD 상대비교용) ──────────
        qld_ctx: dict | None = None
        try:
            from src.market_data import fetch_universe as _fetch_uni
            qld_map = _fetch_uni(["QLD"], period="2y", enrich=True)
            qld_md = (qld_map or {}).get("QLD")
            if qld_md and qld_md.get("available"):
                qld_ctx = {"market_data": qld_md}
        except Exception as e:
            log.debug("[CapEff] QLD 컨텍스트 fetch 실패: %s", e)

        # ── 1) parking 후보 스크리닝 (regime-aware 유니버스 도출) ────────
        try:
            # 최신 regime 행 — screen 의 가중치 조정 + 단기채 ETF bonus 입력
            try:
                _pk_regime = db.fetch_latest_market_regime(conn)
            except Exception as _e:
                log.debug("[CapEff] parking 용 regime fetch 실패: %s", _e)
                _pk_regime = None
            candidates = screen_parking_candidates(regime=_pk_regime)
            for c in candidates:
                try:
                    db.upsert_parking_candidate(conn, date_iso, c.get("ticker"), c)
                    result["parking"] += 1
                except Exception as e:
                    log.debug("[CapEff] parking upsert 실패 %s: %s", c.get("ticker"), e)
            conn.commit()
        except Exception as e:
            log.warning("[CapEff] parking 스크리닝 실패: %s", e)

        # ── 2) watchlist + portfolio 종목 — profit protection / cap eff ──
        # watchlist: 이번 run 의 deep-dive universe (md_map 보유)
        seen: set[str] = set()
        for u in universe:
            ticker = (u.get("ticker") or "").upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            md = md_map.get(u.get("ticker")) or md_map.get(ticker) or {}
            if not md.get("available"):
                continue
            stock = {"ticker": ticker, "market_data": md,
                     "scores": u.get("scores"), "curated_events": u.get("curated_events")}
            # Capital Efficiency
            try:
                ce = calculate_capital_efficiency_score(stock, qld_ctx=qld_ctx)
                db.upsert_capital_efficiency_score(conn, date_iso, ticker, ce)
                result["capital_efficiency"] += 1
            except Exception as e:
                log.debug("[CapEff] %s capital efficiency 실패: %s", ticker, e)
            # Profit Protection
            try:
                pp = calculate_profit_protection_score({"ticker": ticker,
                                                        "market_data": md})
                db.upsert_profit_protection(conn, date_iso, ticker, pp)
                result["profit_protection"] += 1
            except Exception as e:
                log.debug("[CapEff] %s profit protection 실패: %s", ticker, e)
        conn.commit()

        # ── 3) portfolio.json 의 미국 종목 (yf_ticker 보유) 추가 처리 ────
        try:
            import json as _json
            pf_path = PROJECT_ROOT / "data" / "portfolio.json"
            if pf_path.exists():
                pf = _json.loads(pf_path.read_text(encoding="utf-8"))
                pf_tickers: list[tuple[str, str]] = []  # (yf_ticker, display_ticker)
                for h in (pf.get("holdings") or []):
                    yft = h.get("yf_ticker")
                    if yft and not str(yft).endswith(".KS"):
                        disp = (h.get("ticker") or yft).upper()
                        if disp.upper() not in seen:
                            pf_tickers.append((str(yft), disp.upper()))
                if pf_tickers:
                    from src.market_data import fetch_universe as _fetch_uni2
                    pf_md = _fetch_uni2([t for t, _ in pf_tickers],
                                        period="2y", enrich=True)
                    for yft, disp in pf_tickers:
                        md = (pf_md or {}).get(yft) or {}
                        if not md.get("available"):
                            continue
                        seen.add(disp)
                        try:
                            ce = calculate_capital_efficiency_score(
                                {"ticker": disp, "market_data": md}, qld_ctx=qld_ctx)
                            db.upsert_capital_efficiency_score(conn, date_iso, disp, ce)
                            result["capital_efficiency"] += 1
                        except Exception as e:
                            log.debug("[CapEff] %s cap eff 실패: %s", disp, e)
                        try:
                            pp = calculate_profit_protection_score(
                                {"ticker": disp, "market_data": md})
                            db.upsert_profit_protection(conn, date_iso, disp, pp)
                            result["profit_protection"] += 1
                        except Exception as e:
                            log.debug("[CapEff] %s profit protection 실패: %s", disp, e)
                    conn.commit()
        except Exception as e:
            log.warning("[CapEff] portfolio.json 종목 처리 실패: %s", e)

        log.info("[CapEff] done: %s", result)
        return {"ok": True, **result}
    except Exception as e:
        log.warning("[CapEff] Capital Efficiency 평가 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_backtest(conn, run_id: str, date_iso: str) -> dict:
    """Phase 4-A — 백테스트 갱신.

    1) data_cache 로 시장 일봉 증분 append (최초엔 가능한 긴 history)
    2) backtest_engine 으로 regime/overheat forward return, drawdown
       deployment, parking, profit protection 백테스트 재계산
    3) backtest_results / regime_forward_returns 테이블 저장

    매월 1일에는 full=True 로 전체 재다운로드 (split/배당 재조정 반영).
    외부 데이터(yfinance) 실패해도 파이프라인 전체가 죽지 않게 try/except.
    """
    log.info("[Backtest] Phase 4-A 백테스트 갱신 시작...")
    try:
        from src.backtest_engine import update_backtest_incrementally

        # 매월 1일 = 전체 재다운로드, 그 외 = 증분
        is_month_start = date_iso.endswith("-01")
        result = update_backtest_incrementally(conn, full=is_month_start)
        log.info("[Backtest] done: ok=%s saved=%s cache=%s",
                 result.get("ok"), result.get("saved"), result.get("cache"))
        return result
    except Exception as e:
        log.warning("[Backtest] 백테스트 갱신 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_kr_market_data(conn, run_id: str, date_iso: str) -> dict:
    """KR 시장 일봉 캐싱 (Stage 2).

    - `data_cache.collect_kr_tickers()` 가 kr_universe.csv + KR_ETF_TICKERS 를 합쳐
      대상 ticker 목록을 만든다 (이미 .KS 접미사 제거된 DB 컨벤션).
    - 월초(date_iso 가 -01 로 끝남) 에는 `refresh_full_kr_history` 로 전체 재다운로드
      해 split/배당/리밸런싱 재조정을 반영. 그 외엔 `append_new_kr_market_data`
      증분.
    - KR_HISTORY_ENABLED=False 면 즉시 skip. 어떤 실패도 graceful — 파이프라인
      절대 죽이지 않는다.

    Returns: {"ok": bool, "mode": "full"|"incremental"|"skipped", ...}
    """
    log.info("[KR Market] KR 시장 일봉 캐싱 시작...")
    cfg = load_config()
    if not cfg.kr_history_enabled:
        log.info("[KR Market] KR_HISTORY_ENABLED=False — skip")
        return {"ok": True, "mode": "skipped"}
    try:
        from src.data_cache import (
            append_new_kr_market_data,
            refresh_full_kr_history,
            collect_kr_tickers,
        )
        tickers = collect_kr_tickers()
        is_month_start = date_iso.endswith("-01")
        if is_month_start:
            res = refresh_full_kr_history(conn, tickers)
            log.info("[KR Market] FULL refresh — refreshed=%d rows=%d failed=%d",
                     res.get("refreshed", 0), res.get("rows", 0),
                     len(res.get("failed") or []))
            return {"ok": True, "mode": "full", **res,
                    "n_tickers": len(tickers)}
        res = append_new_kr_market_data(conn, tickers)
        log.info("[KR Market] 증분 append — updated=%d rows=%d failed=%d",
                 res.get("updated", 0), res.get("rows", 0),
                 len(res.get("failed") or []))
        return {"ok": True, "mode": "incremental", **res,
                "n_tickers": len(tickers)}
    except Exception as e:
        log.warning("[KR Market] KR 일봉 캐싱 실패 — graceful skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_kospi_regime(conn, run_id: str, date_iso: str) -> dict:
    """KOSPI Overheat Score + KR 시장 국면 평가 (Stage 2).

    `step_kr_market_data` 이후 호출되어야 한다 — KOSPI 200 (069500) 일봉이
    `market_price_history` 에 있어야 기술적 sub-score 산출 가능.
    KR_HISTORY_ENABLED=False 면 skip. 데이터 부족 시 차분히 "데이터 누적 중" dict
    저장.
    """
    log.info("[KR Regime] KOSPI Overheat Score 평가 시작...")
    cfg = load_config()
    if not cfg.kr_history_enabled:
        log.info("[KR Regime] KR_HISTORY_ENABLED=False — skip")
        return {"ok": True, "mode": "skipped"}
    try:
        from src.kr_market_regime import calculate_kospi_overheat_score
        regime = calculate_kospi_overheat_score(conn)

        # DB 저장 — kospi_market_regime
        try:
            sub = regime.get("sub_scores") or {}
            db.upsert_kospi_market_regime(conn, date_iso, {
                "kospi_overheat_score": regime.get("overheat_score"),
                "current_regime": regime.get("regime"),
                "kospi_valuation_score": sub.get("kospi_valuation_score"),
                "kospi_sentiment_score": sub.get("kospi_sentiment_score"),
                "kospi_concentration_score": sub.get("kospi_concentration_score"),
                "kospi_liquidity_score": sub.get("kospi_liquidity_score"),
                "kospi_earnings_revision_score":
                    sub.get("kospi_earnings_revision_score"),
                "kospi_technical_score": sub.get("kospi_technical_score"),
                "commentary_ko": regime.get("commentary_ko"),
                "sample_caveats_json": regime.get("sample_caveats"),
            })
        except Exception as e:
            log.warning("[KR Regime] kospi_market_regime upsert 실패: %s", e)

        log.info("[KR Regime] regime=%s overheat=%s missing=%d",
                 regime.get("regime"), regime.get("overheat_score"),
                 len(regime.get("missing_subscores") or []))
        return {
            "ok": True,
            "regime": regime.get("regime"),
            "overheat": regime.get("overheat_score"),
            "band": regime.get("band"),
        }
    except Exception as e:
        log.warning("[KR Regime] KOSPI regime 평가 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_market_cycle(conn, run_id: str, date_iso: str) -> dict:
    """Stage A — Market Cycle Research Engine.

    장기 시장 history 로부터 base rate(조정 빈도·낙폭/회복 기간·상승장 길이·
    신고가 근접 forward return·추세 상태별 forward return)를 실증적으로 추출.

    1) 항상 ensure_long_term_history (장기 일봉 확보 — 부족하면 yfinance max).
    2) FULL generate_market_cycle_summary 는 무겁다 — base rate 는 일간으로
       거의 안 움직이므로 월초이거나 market_cycles 테이블이 비었을 때만 실행.
       그 외에는 locate_current_market (가벼움) 만 갱신.

    외부 데이터(yfinance) 실패해도 파이프라인 전체가 죽지 않게 try/except.
    """
    log.info("[Market Cycle] Stage A — 시장 사이클 분석 시작...")
    try:
        from src.market_cycle_analyzer import (
            ensure_long_term_history,
            generate_market_cycle_summary,
            locate_current_market,
        )

        hist = ensure_long_term_history(conn)
        log.info("[Market Cycle] 장기 history: %s",
                 {t: v.get("rows") for t, v in hist.items()})

        # market_cycles 테이블 비었는지 확인
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM market_cycles"
            ).fetchone()
            cycles_empty = (row["c"] if hasattr(row, "keys") else row[0]) == 0
        except Exception:
            cycles_empty = True

        is_month_start = date_iso.endswith("-01")
        if is_month_start or cycles_empty:
            summary = generate_market_cycle_summary(conn)
            log.info("[Market Cycle] FULL 분석 완료 — saved=%s",
                     summary.get("saved"))
            return {"ok": True, "mode": "full", "saved": summary.get("saved"),
                    "current": (summary.get("current_market") or {}).get("verdict_ko")}
        else:
            cur = locate_current_market(conn, "QQQ")
            log.info("[Market Cycle] 현재 위치만 갱신 — %s",
                     cur.get("verdict_ko"))
            return {"ok": True, "mode": "locate_only",
                    "current": cur.get("verdict_ko")}
    except Exception as e:
        log.warning("[Market Cycle] Stage A 분석 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_entry_timing(conn, run_id: str, date_iso: str) -> dict:
    """데이터 사다리 — 낙폭 버킷별 (QQQ/QLD/TQQQ) forward return 집계.

    Stage A 의 step_market_cycle 이 장기 history 를 보장한 뒤 호출되어야 한다.

    - FULL 재계산 (calculate_entry_timing_buckets + persist) 은 월초 또는
      entry_timing_buckets 테이블이 비어있을 때만 — 일간 변화가 거의 없으므로.
    - 오늘의 진입 추천(recommend_current_entry) 은 매일 호출되지만, 이 함수가
      직접 저장하지는 않는다 (DB 저장은 step_backtest_solution 의 build_*
      가 만든 headline/items 에 녹아든다).

    Returns: {"ok": bool, "mode": "full"|"locate_only", "saved": int|None,
              "best_asset": str|None, "verdict": str|None}
    """
    log.info("[Entry Timing] 데이터 사다리 — 낙폭 버킷별 forward return...")
    try:
        from src.market_cycle_analyzer import (
            calculate_entry_timing_buckets,
            persist_entry_timing_buckets,
            recommend_current_entry,
        )

        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM entry_timing_buckets"
            ).fetchone()
            empty = (row["c"] if hasattr(row, "keys") else row[0]) == 0
        except Exception:
            empty = True
        is_month_start = date_iso.endswith("-01")

        saved = None
        mode = "locate_only"
        if is_month_start or empty:
            result = calculate_entry_timing_buckets(conn, base_asset="QQQ")
            saved = persist_entry_timing_buckets(conn, result)
            mode = "full"
            log.info("[Entry Timing] FULL 재계산 완료 — saved=%s", saved)

        # 가벼운 일간 추천 — 항상 호출 (lookup 만 함)
        rec = recommend_current_entry(conn, "QQQ")
        log.info("[Entry Timing] 오늘 추천: bucket=%s best=%s verdict=%s",
                 rec.get("current_bucket"), rec.get("best_asset"),
                 rec.get("verdict"))

        # ── KR 사다리 (Stage 2) — base_asset=069500, targets=(069500, 122630) ──
        # 인버스 2X(252670) 는 forward-return 의 방향성이 반대라 ladder 의
        # 의미가 다르다 — Stage 2 에선 제외.
        cfg = load_config()
        kr_saved = None
        kr_rec: dict | None = None
        if cfg.kr_history_enabled:
            try:
                # KR ladder 도 동일하게 비어있거나 월초면 FULL 재계산
                try:
                    row_kr = conn.execute(
                        "SELECT COUNT(*) AS c FROM entry_timing_buckets "
                        "WHERE base_asset='069500'"
                    ).fetchone()
                    kr_empty = (row_kr["c"] if hasattr(row_kr, "keys")
                                else row_kr[0]) == 0
                except Exception:
                    kr_empty = True

                if is_month_start or kr_empty:
                    kr_result = calculate_entry_timing_buckets(
                        conn, base_asset="069500",
                        target_assets=("069500", "122630"),
                    )
                    kr_saved = persist_entry_timing_buckets(conn, kr_result)
                    log.info("[Entry Timing KR] FULL 재계산 완료 — saved=%s",
                             kr_saved)
                kr_rec = recommend_current_entry(conn, "069500")
                log.info(
                    "[Entry Timing KR] 오늘 추천: bucket=%s best=%s verdict=%s",
                    kr_rec.get("current_bucket"), kr_rec.get("best_asset"),
                    kr_rec.get("verdict"),
                )
            except Exception as e:
                log.warning("[Entry Timing KR] 사다리 계산 실패 — skip: %s", e)

        return {
            "ok": True, "mode": mode, "saved": saved,
            "best_asset": rec.get("best_asset"),
            "verdict": rec.get("verdict"),
            "bucket": rec.get("current_bucket"),
            "kr_saved": kr_saved,
            "kr_best_asset": (kr_rec or {}).get("best_asset"),
            "kr_verdict": (kr_rec or {}).get("verdict"),
            "kr_bucket": (kr_rec or {}).get("current_bucket"),
        }
    except Exception as e:
        log.warning("[Entry Timing] 데이터 사다리 계산 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


def step_backtest_solution(conn, run_id: str, date_iso: str) -> dict:
    """백테스트 기반 오늘의 대응 — 백테스트 결과를 퀀트처럼 소화해
    '오늘 무엇을 할지' 의 구체적 처방을 만들어 backtest_solution 에 저장.

    step_backtest(백테스트 갱신)와 step_market_regime(시장 국면) 이후에
    실행되어야 한다 — 최신 백테스트·regime·crash 데이터를 모두 활용한다.

    LLM 없이 완전 동작. 어떤 실패도 잡아 로깅하고 파이프라인을 죽이지 않는다.

    Returns: {"data_mode": "...", "items": N} 또는 {"error": "..."}.
    """
    log.info("[Backtest Solution] 백테스트 기반 오늘의 대응 생성 시작...")
    try:
        from src.backtest_engine import generate_backtest_summary
        from src.backtest_solution import build_backtest_solution
        from src.market_cycle_analyzer import (
            locate_current_market, recommend_current_entry,
        )

        regime = db.fetch_latest_market_regime(conn)
        crash = db.fetch_latest_crash_deployment_plan(conn)

        try:
            bt_summary = generate_backtest_summary(conn)
        except Exception as e:
            log.warning("[Backtest Solution] 백테스트 요약 실패 — 빈 dict: %s", e)
            bt_summary = {}

        # Stage B — 시장 사이클 위치 (step_market_cycle 이후라 데이터 존재)
        try:
            cycle = locate_current_market(conn)
        except Exception as e:
            log.warning("[Backtest Solution] 시장 사이클 위치 조회 실패: %s", e)
            cycle = None

        # 데이터 사다리 — 오늘의 진입 추천 (entry_timing_buckets 가 있어야 의미)
        try:
            cycle_rec = recommend_current_entry(conn, "QQQ")
        except Exception as e:
            log.warning("[Backtest Solution] 데이터 사다리 추천 실패: %s", e)
            cycle_rec = None

        # portfolio.json 의 holdings — Item C(익절 대응) materiality 게이트.
        # 파일 부재/파싱 실패 시 None — Item C 는 자동으로 비활성된다.
        holdings: list[dict] | None = None
        try:
            import json as _json
            from pathlib import Path as _Path
            p = _Path("data/portfolio.json")
            if p.exists():
                pdata = _json.loads(p.read_text(encoding="utf-8"))
                h = pdata.get("holdings") if isinstance(pdata, dict) else None
                if isinstance(h, list):
                    holdings = h
        except Exception as e:
            log.debug("[Backtest Solution] portfolio.json 로드 실패: %s", e)
            holdings = None

        sol = build_backtest_solution(
            regime, crash, bt_summary, cycle=cycle,
            cycle_recommendation=cycle_rec,
            portfolio_holdings=holdings,
        )
        db.upsert_backtest_solution(conn, date_iso, {
            "headline": sol.get("headline"),
            "data_mode": sol.get("data_mode"),
            "items": sol.get("items") or [],
            "caveat": sol.get("caveat"),
            "cycle_position": sol.get("cycle_position") or "",
        })
        n_items = len(sol.get("items") or [])
        log.info("[Backtest Solution] done: data_mode=%s items=%d",
                 sol.get("data_mode"), n_items)
        return {"data_mode": sol.get("data_mode"), "items": n_items}
    except Exception as e:
        log.warning("[Backtest Solution] 생성 실패 — skip: %s", e)
        return {"error": str(e)}


def step_decision_grading(conn, run_id: str, date_iso: str) -> dict:
    """Phase 4-B — Decision Journal 사후 채점.

    decision_journal.json 의 각 결정에 대해 1M/3M/6M milestone 이 도래했고
    아직 채점되지 않았으면 (또는 기존 등급이 '채점 보류' 이면 재시도) 채점한다.

    채점: 종목과 QQQ 의 yfinance 일봉을 받아 결정일·milestone일 종가로
    window return 과 QQQ 대비 상대수익을 계산 → generate_decision_grade.
    yfinance 실패 시 '채점 보류' 행을 upsert — 파이프라인은 절대 죽지 않는다.

    Returns: {"checked": N, "graded": M, "pending": K}
    """
    log.info("[Decision Journal] Phase 4-B 결정 사후 채점 시작...")
    result = {"checked": 0, "graded": 0, "pending": 0}
    try:
        from src.decision_journal import (
            GRADE_PENDING,
            generate_decision_grade,
            load_decisions,
        )
        from src.market_data import fetch_max_history
        from src.performance_tracker import _price_on_or_after, _price_on_or_before
    except Exception as e:
        log.warning("[Decision Journal] 모듈 import 실패 — skip: %s", e)
        return result

    decisions = load_decisions()
    if not decisions:
        log.info("[Decision Journal] 기록된 결정 없음 — skip")
        return result

    today = _dt.date.fromisoformat(date_iso)
    # milestone 은 실제 달력 개월 수로 측정한다. dateutil 이 있으면
    # relativedelta(months=...) 로 정확한 1/3/6개월 후 날짜를 쓰고,
    # 없으면 raw day-offset 으로 fallback.
    try:
        from dateutil.relativedelta import relativedelta as _relativedelta
        _HAS_RELATIVEDELTA = True
    except Exception:
        _relativedelta = None  # type: ignore
        _HAS_RELATIVEDELTA = False
    # (label, months, fallback_days)
    milestones = [("1M", 1, 30), ("3M", 3, 91), ("6M", 6, 182)]

    # 기존 채점 행 — (decision_id, milestone) -> grade
    try:
        existing: dict[tuple[str, str], str] = {}
        for r in db.fetch_decision_grades(conn):
            existing[(r["decision_id"], r["milestone"])] = r["grade"]
    except Exception as e:
        log.warning("[Decision Journal] 기존 채점 조회 실패: %s", e)
        existing = {}

    # QQQ 일봉은 한 번만 받아 재사용
    qqq_hist = None
    qqq_fetch_failed = False

    def _qqq():
        nonlocal qqq_hist, qqq_fetch_failed
        if qqq_hist is None and not qqq_fetch_failed:
            try:
                qqq_hist = fetch_max_history("QQQ")
            except Exception as e:
                log.warning("[Decision Journal] QQQ history fetch 실패: %s", e)
            if qqq_hist is None:
                qqq_fetch_failed = True
        return qqq_hist

    ticker_hist_cache: dict[str, Any] = {}

    def _ticker_hist(tk: str):
        if tk not in ticker_hist_cache:
            try:
                ticker_hist_cache[tk] = fetch_max_history(tk)
            except Exception as e:
                log.warning("[Decision Journal] %s history fetch 실패: %s", tk, e)
                ticker_hist_cache[tk] = None
        return ticker_hist_cache[tk]

    for d in decisions:
        decision_id = str(d.get("id") or "")
        ticker = str(d.get("ticker") or "").upper().strip()
        action = str(d.get("action") or "").upper().strip()
        if not decision_id or not ticker:
            continue
        try:
            decision_date = _dt.date.fromisoformat(str(d.get("decision_date")))
        except Exception:
            log.debug("[Decision Journal] %s decision_date 파싱 실패 — skip", decision_id)
            continue

        for milestone, ms_months, ms_days in milestones:
            if _HAS_RELATIVEDELTA:
                milestone_date = decision_date + _relativedelta(months=ms_months)
            else:
                milestone_date = decision_date + _dt.timedelta(days=ms_days)
            if today < milestone_date:
                continue  # 아직 milestone 미도래
            prev_grade = existing.get((decision_id, milestone))
            if prev_grade is not None and prev_grade != GRADE_PENDING:
                continue  # 이미 정상 채점됨

            result["checked"] += 1

            # 채점 — graceful: 어떤 실패든 '채점 보류' upsert
            grade_row: dict[str, Any]
            try:
                tk_hist = _ticker_hist(ticker)
                qqq = _qqq()
                if tk_hist is None or qqq is None:
                    grade_row = generate_decision_grade(action, None, None, None)
                    note = (f"{ticker} 또는 QQQ 일봉 데이터를 받지 못해 "
                            f"채점을 보류합니다.")
                    grade_row["grade_note"] = note
                    p_dec = p_ms = bench_ret = None
                else:
                    p_dec = (_price_on_or_after(tk_hist, decision_date)
                             or _price_on_or_before(tk_hist, decision_date))
                    p_ms = (_price_on_or_after(tk_hist, milestone_date)
                            or _price_on_or_before(tk_hist, milestone_date))
                    q_dec = (_price_on_or_after(qqq, decision_date)
                             or _price_on_or_before(qqq, decision_date))
                    q_ms = (_price_on_or_after(qqq, milestone_date)
                            or _price_on_or_before(qqq, milestone_date))
                    bench_ret = None
                    if q_dec and q_ms and q_dec > 0:
                        bench_ret = (q_ms / q_dec - 1.0) * 100.0
                    grade_row = generate_decision_grade(
                        action, p_dec, p_ms, bench_ret,
                    )
            except Exception as e:
                log.warning("[Decision Journal] %s %s 채점 예외: %s",
                            decision_id, milestone, e)
                grade_row = generate_decision_grade(action, None, None, None)
                grade_row["grade_note"] = (
                    f"채점 중 오류가 발생해 보류합니다 ({type(e).__name__})."
                )
                p_dec = p_ms = bench_ret = None

            try:
                db.upsert_decision_grade(conn, {
                    "decision_id": decision_id,
                    "milestone": milestone,
                    "graded_date": date_iso,
                    "price_at_decision": p_dec,
                    "price_at_milestone": p_ms,
                    "return_pct": grade_row.get("return_pct"),
                    "benchmark_return_pct": bench_ret,
                    "relative_pct": grade_row.get("relative_pct"),
                    "grade": grade_row.get("grade"),
                    "grade_note": grade_row.get("grade_note"),
                })
                existing[(decision_id, milestone)] = grade_row.get("grade")
                if grade_row.get("grade") == GRADE_PENDING:
                    result["pending"] += 1
                else:
                    result["graded"] += 1
            except Exception as e:
                log.warning("[Decision Journal] %s %s upsert 실패: %s",
                            decision_id, milestone, e)

    log.info("[Decision Journal] done: %s", result)
    return result


def step_holdings_briefing(conn, run_id: str, date_iso: str) -> dict:
    """보유 종목 브리핑 — 사용자 보유 종목(portfolio.json) 중 의미 비중(>=1%)
    종목에 대해 일일 한국어 리서치 브리핑을 생성해 holdings_briefing 에 저장.

    - 이미 (date, ticker) 브리핑이 있으면 SKIP (idempotent — 하루 2회 실행/재실행 시
      LLM 비용 절감).
    - LLM 비활성/budget 소진/호출 실패 시 rule-based 폴백으로 항상 채워진다.
    - 종목별 실패는 catch — 스텝/파이프라인을 절대 중단시키지 않는다.

    Returns: {"target": N, "generated": M, "skipped": K, "rule_based": R}
    """
    log.info("[Holdings Briefing] 보유 종목 브리핑 생성 시작...")
    result = {"target": 0, "generated": 0, "skipped": 0, "rule_based": 0}
    try:
        from src.portfolio_review import load_portfolio
        from src.holdings_briefing import (
            generate_holding_briefing, select_meaningful_holdings,
        )
        from src.config import load_config, make_budget
        from src.market_cycle_analyzer import locate_current_market
    except Exception as e:
        log.warning("[Holdings Briefing] 모듈 import 실패 — skip: %s", e)
        return result

    try:
        pf = load_portfolio()
    except Exception as e:
        log.warning("[Holdings Briefing] portfolio.json 로드 실패 — skip: %s", e)
        return result
    if not pf.get("available"):
        log.info("[Holdings Briefing] 보유 종목 데이터 없음 — skip")
        return result

    meaningful = select_meaningful_holdings(pf.get("holdings") or [], 1.0)
    result["target"] = len(meaningful)
    if not meaningful:
        log.info("[Holdings Briefing] 의미 비중 종목 없음 — skip")
        return result

    # 최신 market_regime 행
    try:
        regime = db.fetch_latest_market_regime(conn)
    except Exception as e:
        log.warning("[Holdings Briefing] market_regime 조회 실패: %s", e)
        regime = None

    # 현재 시장 사이클 위치 (QQQ 기준) — today_focus/today_action 입력
    # step_market_cycle 이후라 데이터는 최신. 실패해도 None 으로 폴백.
    try:
        cycle = locate_current_market(conn, "QQQ")
    except Exception as e:
        log.warning("[Holdings Briefing] market_cycle 조회 실패 — None 폴백: %s", e)
        cycle = None

    # 기존 브리핑 (idempotent skip 용)
    try:
        existing = {
            r["ticker"] for r in db.fetch_holdings_briefings(conn, date_iso)
        }
    except Exception:
        existing = set()

    # cfg/budget 은 한 번만 생성해 공유 (LLM 예산 공유)
    cfg = load_config()
    budget = make_budget(cfg)

    for h in meaningful:
        ticker = (h.get("ticker") or "").strip()
        if not ticker:
            continue
        if ticker in existing:
            result["skipped"] += 1
            continue
        try:
            brief = generate_holding_briefing(
                h, regime, budget=budget, cfg=cfg, conn=conn, cycle=cycle,
            )
            db.upsert_holding_briefing(conn, date_iso, ticker, {
                "name": h.get("name") or ticker,
                "exposure_theme": brief.get("exposure_theme"),
                "summary_ko": brief.get("summary_ko"),
                "key_drivers_ko": brief.get("key_drivers_ko"),
                "risks_ko": brief.get("risks_ko"),
                "portfolio_note_ko": brief.get("portfolio_note_ko"),
                "today_focus_ko": brief.get("today_focus_ko"),
                "today_action_ko": brief.get("today_action_ko"),
                "upcoming_catalysts_ko": brief.get("upcoming_catalysts_ko"),
                "underlying_snapshot_ko": brief.get("underlying_snapshot_ko"),
                "model_used": brief.get("model_used"),
            })
            result["generated"] += 1
            if brief.get("model_used") == "rule-based":
                result["rule_based"] += 1
        except Exception as e:
            log.warning("[Holdings Briefing] %s 브리핑 실패 — skip: %s", ticker, e)

    log.info("[Holdings Briefing] done: %s", result)
    return result


def step_update_performance_tracking(conn, run_id: str, today: _dt.date) -> int:
    """Logic Auditor — Auto-recorded 결정 전체에 대해 holding period 별 성과 갱신.

    SPY / QQQ / QLD 대비 초과수익 + max drawdown / max gain / volatility / hit_status 까지.
    365일 이내 결정만 — fetch 부하 절감.
    """
    log.info("[12/12] Auditor — performance tracking (1D/1W/2W/1M/3M/6M/12M)...")
    try:
        from src.performance_tracker import update_performance_tracking_all
        result = update_performance_tracking_all(conn, today=today)
        log.info("[12/12] perf tracking: %s", result)
        return int(result.get("updated", 0))
    except Exception as e:
        log.warning("[12/12] performance tracking failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def run_research(
    only_tickers: list[str] | None = None,
    fetch_news: bool = True,
    dry_run: bool = False,
    today: _dt.date | None = None,
    skip_discovery: bool = False,
    skip_wide_fetch: bool = False,
) -> dict[str, Any]:
    today = today or _dt.date.today()
    date_iso = today.isoformat()
    cfg = load_config()
    # run 단위 LLM 호출 한도 — step_auto_curate 등에서 공유
    budget = make_budget(cfg)
    log.info("AlphaConfig: llm_mode=%s budget=%d cache=%s discovery=%s",
             cfg.llm_mode, cfg.max_llm_calls_per_run, cfg.enable_summary_cache, cfg.enable_discovery)

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
        "universe": 0, "wide_universe": 0,
        "price_ok": 0, "news_ok": 0, "scores": 0, "research": 0,
        "discovery_candidates": 0, "promoted": 0,
    }
    try:
        # ── Deep Dive 의 core watchlist 로드 ─────────────────────────
        unis = step_load_universe(conn, run_id, only_tickers=only_tickers)
        summary["universe"] = len(unis)
        core_tickers = {u["ticker"] for u in unis}

        # ── Wide Scan → Discovery Candidate ─────────────────────────
        promoted: list[dict] = []
        if cfg.enable_discovery and not skip_discovery and not only_tickers:
            wide = step_load_wide_universe(conn, run_id, limit=cfg.wide_universe_limit)
            summary["wide_universe"] = len(wide)
            wide_md = step_fetch_wide_market_data(
                conn, run_id, wide, date_iso, skip=skip_wide_fetch,
            )
            _, discovery_cands = step_run_discovery(
                conn, run_id, date_iso, wide, wide_md, top_k=cfg.discovery_top_k,
            )
            summary["discovery_candidates"] = len(discovery_cands)

            # Promotion (뉴스 fetch + summarize + 점수) → Promoted Candidate
            if cfg.enable_promotion and discovery_cands:
                promoted = step_run_promotion(
                    conn, run_id, date_iso, discovery_cands, wide_md, core_tickers,
                    cfg=cfg, promote_k=cfg.deep_dive_k,
                )
            summary["promoted"] = len(promoted)
        else:
            log.info("[2-7/15] discovery / promotion skipped")

        # ── Deep Dive — core watchlist + Promoted Candidate ─────────
        # 승격된 후보를 unis 에 임시로 합쳐 deep dive 처리
        promoted_metas = []
        for p in promoted:
            promoted_metas.append({
                "ticker": p["ticker"],
                "name_ko": "",  # core 가 아니면 한국어명 없음 — UI 에서 fallback
                "name_en": p.get("name") or "",
                "theme": "",
                "category": "",
                "_promoted": True,
            })
        all_deep_dive = unis + promoted_metas

        md_map = step_fetch_market_data(conn, run_id, all_deep_dive, date_iso)
        summary["price_ok"] = sum(1 for m in md_map.values() if m.get("available"))

        proxies, market_summary = step_fetch_market_proxies(conn, run_id, date_iso)

        if fetch_news:
            news_map = step_fetch_news(conn, run_id, all_deep_dive)
            summary["news_ok"] = sum(1 for n in news_map.values() if n)
        else:
            news_map = {u["ticker"]: [] for u in all_deep_dive}

        events_map = step_build_events(conn, run_id, all_deep_dive, news_map)
        score_map = step_calculate_scores(
            conn, run_id, date_iso, all_deep_dive, md_map, news_map, events_map
        )
        summary["scores"] = len(score_map)

        n_research, research_map = step_generate_stock_research(conn, run_id, date_iso, score_map)
        summary["research"] = n_research

        # Auto-Curation — Promoted Candidate 5종목/일 LLM 자동 큐레이션 (60일 캐시)
        ac_summary = step_auto_curate(
            conn, run_id, date_iso,
            promoted=promoted,
            score_map=score_map,
            research_map=research_map,
            cfg=cfg,
            budget=budget,
            max_age_days=60,
            max_calls_per_run=5,
        )
        summary["auto_curation"] = ac_summary

        # 전날 글로벌 브리핑 자동 생성 — Google News RSS (4 카테고리, when:1d)
        # → GPT-4o-mini 이벤트 리캡 (월 ~$0.06)
        try:
            from src.overnight_briefing import generate_overnight_briefing
            briefing_today = generate_overnight_briefing(conn, date_iso)
            summary["overnight_briefing_generated"] = (
                sum(len(c.get("events") or []) for c in briefing_today)
                if briefing_today else 0
            )
        except Exception as e:
            log.warning("전날 글로벌 브리핑 자동 생성 실패: %s", e)
            summary["overnight_briefing_generated"] = 0

        # 시장 환경 3 블록 자동 생성 — 자산 수익률(proxies) + RSS → LLM
        try:
            from src.market_env_summarizer import generate_market_env_blocks
            env_blocks = generate_market_env_blocks(
                conn, date_iso, proxies=proxies, market_summary=market_summary,
            )
            summary["market_env_generated"] = len(env_blocks) if env_blocks else 0
        except Exception as e:
            log.warning("시장 환경 자동 생성 실패: %s", e)
            summary["market_env_generated"] = 0

        # Portfolio Regime — Overheat Score / regime / portfolio mode /
        # crash deployment plan 평가 후 DB 저장 (Daily Brief 생성 전).
        try:
            regime_res = step_market_regime(conn, run_id, date_iso)
            summary["market_regime"] = regime_res.get("regime")
            summary["market_overheat"] = regime_res.get("overheat")
        except Exception as e:
            log.warning("portfolio regime 평가 실패: %s", e)
            summary["market_regime"] = None

        # Capital Efficiency (Phase 2) — parking 후보 / profit protection /
        # capital efficiency 점수 평가 후 DB 저장.
        try:
            ce_universe = []
            for u in all_deep_dive:
                s = score_map.get(u["ticker"]) or {}
                rc = s.get("row_context") or {}
                ce_universe.append({
                    **u,
                    "scores": {k: v for k, v in s.items()
                               if k not in ("row_context", "rationale")},
                    "curated_events": rc.get("curated_events"),
                })
            cap_res = step_capital_efficiency(
                conn, run_id, date_iso, ce_universe, md_map,
            )
            summary["capital_efficiency"] = cap_res
        except Exception as e:
            log.warning("Capital Efficiency 평가 실패: %s", e)
            summary["capital_efficiency"] = None

        # Phase 4-A — 백테스트 갱신 (시장 일봉 증분 + regime/overheat/
        # deployment/parking/profit-protection 백테스트 재계산 후 DB 저장).
        try:
            bt_res = step_backtest(conn, run_id, date_iso)
            summary["backtest"] = {
                "ok": bt_res.get("ok"),
                "saved": bt_res.get("saved"),
            }
        except Exception as e:
            log.warning("백테스트 갱신 실패: %s", e)
            summary["backtest"] = None

        # Stage A — Market Cycle Research Engine. 장기 history 로부터
        # 시장 사이클 base rate 를 실증 추출 (월초/테이블 빈 경우 FULL,
        # 그 외엔 현재 위치만 갱신). step_backtest 직후.
        try:
            mc_res = step_market_cycle(conn, run_id, date_iso)
            summary["market_cycle"] = mc_res
        except Exception as e:
            log.warning("시장 사이클 분석 실패: %s", e)
            summary["market_cycle"] = None

        # KR 시장 확장 (Stage 2)
        # 1) step_market_cycle 후, step_entry_timing 전에 KR 일봉을 채워야
        #    KR ladder (base_asset=069500) 가 작동한다.
        # 2) step_kospi_regime 도 KOSPI 200 일봉이 있을 때 가장 의미 있게
        #    동작한다.
        try:
            kr_md_res = step_kr_market_data(conn, run_id, date_iso)
            summary["kr_market_data"] = kr_md_res
        except Exception as e:
            log.warning("KR 시장 데이터 캐싱 실패: %s", e)
            summary["kr_market_data"] = None

        try:
            kr_reg_res = step_kospi_regime(conn, run_id, date_iso)
            summary["kospi_regime"] = kr_reg_res
        except Exception as e:
            log.warning("KOSPI regime 평가 실패: %s", e)
            summary["kospi_regime"] = None

        # 데이터 사다리 — 낙폭 버킷별 forward return 실증 통계. 하드코딩
        # _DEPLOY_LADDER 대체용. step_market_cycle 이후라 장기 history 보장.
        # step_kr_market_data 도 직전에 호출되어 KR ladder 도 동시 갱신.
        try:
            et_res = step_entry_timing(conn, run_id, date_iso)
            summary["entry_timing"] = et_res
        except Exception as e:
            log.warning("데이터 사다리 계산 실패: %s", e)
            summary["entry_timing"] = None

        # 백테스트 기반 오늘의 대응 — 백테스트 결과를 퀀트처럼 소화해
        # '오늘 무엇을 할지' 의 처방을 backtest_solution 테이블에 저장.
        # step_backtest(백테스트 갱신)·step_market_regime 이후라 최신 데이터 활용.
        try:
            bsol_res = step_backtest_solution(conn, run_id, date_iso)
            summary["backtest_solution"] = bsol_res
        except Exception as e:
            log.warning("백테스트 기반 대응 생성 실패: %s", e)
            summary["backtest_solution"] = None

        # Phase 4-B — Decision Journal 사후 채점 (1M/3M/6M milestone 도래한
        # 사용자 결정을 QQQ 대비로 채점해 decision_grades 테이블에 저장).
        try:
            dj_res = step_decision_grading(conn, run_id, date_iso)
            summary["decision_grading"] = {
                "checked": dj_res.get("checked"),
                "graded": dj_res.get("graded"),
                "pending": dj_res.get("pending"),
            }
        except Exception as e:
            log.warning("Decision Journal 채점 실패: %s", e)
            summary["decision_grading"] = None

        # 보유 종목 브리핑 — portfolio.json 의 의미 비중 종목에 대해
        # 일일 한국어 리서치 브리핑 생성 후 holdings_briefing 테이블에 저장.
        # market_regime 스텝 이후라 regime 행을 연계 분석에 쓸 수 있다.
        try:
            hb_res = step_holdings_briefing(conn, run_id, date_iso)
            summary["holdings_briefing"] = {
                "target": hb_res.get("target"),
                "generated": hb_res.get("generated"),
                "skipped": hb_res.get("skipped"),
                "rule_based": hb_res.get("rule_based"),
            }
        except Exception as e:
            log.warning("보유 종목 브리핑 생성 실패: %s", e)
            summary["holdings_briefing"] = None

        step_generate_daily_brief(
            conn, run_id, date_iso, all_deep_dive, score_map, market_summary,
            proxies=proxies,
        )

        # Logic Auditor — Alpha 의 매일 자동 판단 기록 (decision_log)
        promoted_queue_map = {p["ticker"]: p.get("queue_type") for p in promoted}
        n_decisions = step_record_alpha_decisions(
            conn, run_id, date_iso, all_deep_dive, score_map, research_map,
            promoted_queue_map=promoted_queue_map,
        )
        summary["decisions_recorded"] = n_decisions

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
    p.add_argument("--ticker", action="append", help="특정 종목만 처리 (Discovery 단계 자동 skip)")
    p.add_argument("--skip-news", action="store_true", help="뉴스 fetch 생략 (가격만 빠르게)")
    p.add_argument("--skip-discovery", action="store_true",
                   help="Wide Scan / Discovery / Promotion 단계 생략 (core watchlist 만 처리)")
    p.add_argument("--skip-wide-fetch", action="store_true",
                   help="Wide universe 가격 fetch 생략 (개발/테스트용)")
    p.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 출력만")
    args = p.parse_args(argv)

    res = run_research(
        only_tickers=args.ticker,
        fetch_news=not args.skip_news,
        dry_run=args.dry_run,
        skip_discovery=args.skip_discovery,
        skip_wide_fetch=args.skip_wide_fetch,
    )
    print("\n=== run_research 완료 ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
