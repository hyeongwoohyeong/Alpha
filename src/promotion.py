"""Promotion Engine — Discovery Candidate 50~100개 → Promoted Candidate 10~20개.

퍼널:
    Wide Scan → Discovery Candidate → Promoted Candidate → Deep Dive

수행:
    - 뉴스 fetch (후보당 N건만)
    - 뉴스 클러스터링 + 이벤트 상태 / 출처 / staleness / thesis_impact
    - 정량 점수 + 뉴스 시그널 종합 → promotion_score
    - 상위 K개 promoted_to_deep_dive=True

LLM:
    - LLM 모드가 켜져 있으면 article_summaries 캐시 활용 또는 신규 호출
    - LLM_MODE=none 이면 룰 기반 요약만
    - core watchlist 종목은 자동 promotion 제외 (이미 Deep Dive 항상 처리됨)
"""
from __future__ import annotations

from typing import Any

from .config import AlphaConfig, LlmBudget, load_config, make_budget
from .event_processor import (
    classify_event_status,
    cluster_news_by_event,
    is_urgent_risk,
)
from .news_fetcher import fetch_ticker_news
from .news_summarizer import summarize_news_to_korean
from .utils import get_logger, safe_float

log = get_logger("promotion")


def _bool(x: Any) -> bool:
    return bool(x)


def _news_relevance_score(news_items: list[dict[str, Any]]) -> tuple[float, str, str]:
    """뉴스 시그널 → 0~100 + 대표 thesis_impact + 1줄 요약.

    뉴스가 없으면 (0, "확인 필요", "")
    """
    if not news_items:
        return 0.0, "확인 필요", ""

    clusters = cluster_news_by_event(news_items, ticker=news_items[0].get("ticker", ""))
    if not clusters:
        return 0.0, "확인 필요", ""
    top = clusters[0]

    impact = top.get("thesis_impact") or "확인 필요"
    confidence = top.get("confidence") or "Low"
    staleness = top.get("staleness") or "fresh"
    src_q = top.get("source_quality") or "Low"

    base = {
        "Thesis 강화": 75.0, "리스크 해소": 70.0,
        "신규 리스크": 65.0, "Thesis 약화": 60.0,
        "단기 노이즈": 35.0, "확인 필요": 30.0,
    }.get(impact, 30.0)

    # confidence + source 보정
    if confidence == "High":
        base += 12
    elif confidence == "Low":
        base -= 10
    if src_q == "High":
        base += 8
    elif src_q == "Low":
        base -= 5
    # staleness penalty
    if staleness == "stale":
        base -= 8
    elif staleness == "outdated":
        base -= 15
    base = max(0.0, min(100.0, base))

    summary = (top.get("title") or "")[:200]
    return base, impact, summary


def _financial_quality_score(md: dict[str, Any]) -> float:
    """간단 정량 quality proxy 0~100. NaN 은 50 (중립)."""
    rev_growth = safe_float(md.get("revenue_growth") or md.get("revenue_growth_yoy"))
    roe = safe_float(md.get("roe"))
    fcf_yield = safe_float(md.get("fcf_yield"))
    op_margin = safe_float(md.get("operating_margin"))

    score = 50.0
    if rev_growth is not None:
        score += min(20.0, max(-15.0, rev_growth * 80))
    if roe is not None:
        score += min(15.0, max(-10.0, roe * 60))
    if fcf_yield is not None and fcf_yield > 0:
        score += min(10.0, fcf_yield * 100)
    if op_margin is not None:
        score += min(10.0, max(-10.0, op_margin * 30))
    return max(0.0, min(100.0, score))


def _risk_penalty(meta: dict[str, Any], md: dict[str, Any], news: list[dict[str, Any]]) -> float:
    """리스크 페널티 0~30 (큰 값일수록 위험)."""
    pen = 0.0
    # 1년 -50% 이하 — 파괴적 trend
    ret_1y = safe_float(md.get("1y_return") or md.get("return_1y"))
    if ret_1y is not None and ret_1y < -0.50:
        pen += 12
    # urgent 키워드
    text = " ".join((n.get("title") or "") + " " + (n.get("summary") or "") for n in news)
    if text and is_urgent_risk(text):
        pen += 15
    # going concern / dilution / fraud 키워드
    bad_kw = ("going concern", "fraud", "investigation", "delisting", "bankruptcy",
              "회계", "조사", "파산")
    low = text.lower()
    for kw in bad_kw:
        if kw in low:
            pen += 6
            break
    return min(30.0, pen)


def _action_recommendation(promotion_score: float, impact: str) -> str:
    if promotion_score >= 75 and impact in ("Thesis 강화", "리스크 해소"):
        return "Deep Dive 권장 — 단기 catalyst 가능성"
    if promotion_score >= 70 and impact == "신규 리스크":
        return "Deep Dive 권장 — anti-thesis 점검 우선"
    if promotion_score >= 65:
        return "Deep Dive 후보 — 추가 데이터 확인"
    if promotion_score >= 50:
        return "관찰 — 큐 유지, 다음 run 재검토"
    return "보류 — 시그널 부족"


def run_promotion(
    discovery_candidates: list[dict[str, Any]],
    md_map: dict[str, dict[str, Any]],
    *,
    core_tickers: set[str],
    cfg: AlphaConfig | None = None,
    fetch_news_fn=None,
    summarize_fn=None,
    news_per_ticker: int = 3,
) -> list[dict[str, Any]]:
    """Discovery Candidate → 뉴스/이벤트/재무 종합 → Promotion Score.

    Args:
        discovery_candidates: select_discovery_candidates() 결과
        md_map: 가격/재무 데이터 (Wide Scan fetch 재사용)
        core_tickers: 이미 deep dive 대상인 core watchlist (제외 처리)
        fetch_news_fn: dependency injection (테스트용)
        summarize_fn: dependency injection (테스트용)

    Returns:
        Promotion 결과 list (promoted_to_deep_dive 플래그 포함)
    """
    cfg = cfg or load_config()
    budget = make_budget(cfg)
    fetch_news_fn = fetch_news_fn or fetch_ticker_news
    summarize_fn = summarize_fn or summarize_news_to_korean

    out: list[dict[str, Any]] = []
    for cand in discovery_candidates:
        ticker = cand["ticker"]
        if ticker in core_tickers:
            # core watchlist 종목은 자동 deep dive — promotion 흐름에서 제외
            continue
        md = md_map.get(ticker) or {}

        # 1) 뉴스 fetch (제한)
        try:
            news = fetch_news_fn(ticker, name_en=cand.get("name"), limit=news_per_ticker) or []
        except Exception as e:
            log.debug("[%s] news fetch failed: %s", ticker, e)
            news = []
        for n in news:
            n["ticker"] = ticker

        # 2) 한국어 요약 (LLM 또는 룰 기반 — summarize_fn 가 캐시/모드 처리)
        for n in news:
            try:
                payload = summarize_fn(n, stock_context=cand, budget=budget, cfg=cfg)
                n.update({
                    "detailed_summary_ko": payload.get("detailed_summary_ko"),
                    "investment_implication_ko": payload.get("investment_implication_ko"),
                    "thesis_impact_ko": payload.get("thesis_impact_ko"),
                    "confidence_level_ko": payload.get("confidence_level_ko"),
                    "follow_up_items_ko": payload.get("follow_up_items_ko"),
                    "content_availability": payload.get("content_availability"),
                })
            except TypeError:
                # summarize_fn 가 budget/cfg 미지원 시그니처 (legacy) 인 경우 폴백
                try:
                    payload = summarize_fn(n, stock_context=cand)
                    n.update(payload)
                except Exception as e:
                    log.debug("[%s] summarize legacy failed: %s", ticker, e)
            except Exception as e:
                log.debug("[%s] summarize failed: %s", ticker, e)

        # 3) 점수 계산
        news_score, impact, latest_summary = _news_relevance_score(news)
        fin_quality = _financial_quality_score(md)
        risk_pen = _risk_penalty(cand, md, news)
        discovery_score = float(cand.get("final_discovery_score", cand.get("score", 0)))

        # Promotion Score = Discovery 30% + News 25% + Financial 15% + Thesis 20% (impact base) + Risk -10%
        thesis_potential = {
            "Thesis 강화": 80, "리스크 해소": 75,
            "신규 리스크": 55, "Thesis 약화": 50,
            "단기 노이즈": 35, "확인 필요": 40,
        }.get(impact, 40)

        promo = (
            0.30 * discovery_score
            + 0.25 * news_score
            + 0.15 * fin_quality
            + 0.20 * thesis_potential
            - 0.10 * (risk_pen * 3.33)  # risk_pen (0~30) → 0~100 스케일로 변환 후 -10%
        )
        promo = max(0.0, min(100.0, promo))

        recommendation = _action_recommendation(promo, impact)

        out.append({
            "ticker": ticker,
            "name": cand.get("name"),
            "queue_type": cand.get("best_queue") or cand.get("queue_type"),
            "discovery_score": discovery_score,
            "promotion_score": promo,
            "reason": cand.get("signal_summary", ""),
            "latest_event_summary": latest_summary,
            "thesis_impact": impact,
            "action_recommendation": recommendation,
            "news_count": len(news),
            "news": news,
        })

    # promotion_score 기준 정렬
    out.sort(key=lambda x: x["promotion_score"], reverse=True)
    return out


def select_promoted(
    promotion_results: list[dict[str, Any]],
    k: int = 15,
    score_threshold: float = 60.0,
) -> list[dict[str, Any]]:
    """상위 k개 또는 score_threshold 이상을 deep dive 로 승격."""
    selected: list[dict[str, Any]] = []
    for r in promotion_results:
        if len(selected) >= k:
            break
        if r["promotion_score"] < score_threshold:
            break  # 이미 정렬돼 있으므로 break
        r = {**r, "promoted_to_deep_dive": True}
        selected.append(r)
    return selected
