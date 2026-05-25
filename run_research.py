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

        # crash deployment — QQQ 가격이력 사용
        qqq_hist = ((data.get("etf") or {}).get("QQQ") or {}).get("history")
        from src.crash_deployment import calculate_nasdaq_drawdown_from_high
        dd = calculate_nasdaq_drawdown_from_high(qqq_hist)
        if dd is None:
            dd = regime.get("qqq_drawdown_from_high")
        plan = generate_deployment_plan(dd, regime.get("credit_stress_status"))

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
            # cycle_psychology / buffett_opportunity 는 Phase 3 — 지금은 NULL
            "cycle_psychology_score": None,
            "buffett_opportunity_score": None,
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
            "[Regime] regime=%s overheat=%s mode=%s zone=%s",
            regime.get("current_regime"), regime.get("market_overheat_score"),
            mode.get("portfolio_mode"), plan.get("deployment_zone"),
        )
        return {
            "ok": True,
            "regime": regime.get("current_regime"),
            "overheat": regime.get("market_overheat_score"),
            "portfolio_mode": mode.get("portfolio_mode"),
        }
    except Exception as e:
        log.warning("[Regime] portfolio regime 평가 실패 — skip: %s", e)
        return {"ok": False, "error": str(e)}


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
