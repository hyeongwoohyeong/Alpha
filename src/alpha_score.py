"""Alpha Score — 종목별 통합 투자 매력도 점수 (0~100).

설계 원칙 (사용자 spec — 2026-05 재설계):
    1. **모든 컴포넌트가 50 으로 시작하는 구조 제거.**
       데이터 부족 시 score=None / status="Insufficient Data" 로 처리하고,
       산정된 항목만 가중평균.
    2. **50 점은 "근거 있는 중립" 일 때만.** 데이터 없는 항목을 50 으로 채워
       100 점 만점의 의미를 약화시키지 않음.
    3. **Scored Coverage** (산정된 항목의 weight 비율) 가 낮으면
       Missing Data Penalty + Provisional Score 표시.
    4. **각 컴포넌트는 status / confidence / reason 메타 보유** — UI 에서
       N/A 배지 + 산정 근거 툴팁으로 표시.
    5. **Risk Control 은 리스크가 높을수록 낮은 점수** (이미 그렇게 구현됨).

8 컴포넌트 가중치:
    Thesis Strength             15%
    Earnings Quality            15%
    Moat / Lock-in              15%
    Price Opportunity           15%
    Event / Catalyst Quality    10%
    Industry Tailwind / Bottleneck 10%
    Financial Quality           10%
    Risk Control                10%

Component Status:
    Scored             — 충분한 데이터로 점수 산정됨
    Neutral            — 명확한 긍정/부정 근거 없음, 평균 수준 — 50 점 부여 가능
    Insufficient Data  — 판단에 필요한 데이터 부족, score=None, 가중평균에서 제외
    Not Applicable     — 해당 종목에 적용하기 어려운 항목
    Calculation Error  — 계산 실패 — score=None, data quality flag

Missing Data Penalty (scored_weight_ratio 기반):
    >= 85% → 페널티 없음
    70~84% → -3 점
    50~69% → -7 점
    < 50%  → -12 점 + Provisional 표시
"""
from __future__ import annotations

from typing import Any

from .utils import safe_float


# ---------------------------------------------------------------------------
# 가중치 + 라벨
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "thesis_strength": 0.15,
    "earnings_quality": 0.15,
    "moat_lockin": 0.15,
    "price_opportunity": 0.15,
    "event_catalyst": 0.10,
    "industry_bottleneck": 0.10,
    "financial_quality": 0.10,
    "risk_control": 0.10,
}

LABELS_KO: dict[str, str] = {
    "thesis_strength": "Thesis Strength",
    "earnings_quality": "Earnings Quality",
    "moat_lockin": "Moat / Lock-in",
    "price_opportunity": "Price Opportunity",
    "event_catalyst": "Event / Catalyst Quality",
    "industry_bottleneck": "Industry Tailwind / Bottleneck",
    "financial_quality": "Financial Quality",
    "risk_control": "Risk Control",
}


# ---------------------------------------------------------------------------
# Rating tier — Alpha Score → 라벨
# ---------------------------------------------------------------------------

RATING_TIERS = [
    (88, "Exceptional Candidate", "최우선 정밀 검토 후보"),
    (80, "High Conviction Candidate", "강한 비중 후보로 검토 가능"),
    (70, "Research Now", "적극 리서치 후보"),
    (62, "Watchlist / Wait for Better Entry", "관찰 / 진입 시점 대기"),
    (54, "Need Thesis Check", "Thesis 검증 필요"),
    (45, "Low Priority", "현재 우선순위 낮음"),
    (0, "Avoid / Not Enough Evidence", "회피 또는 근거 부족"),
]


def compute_alpha_percentile(score: float | None) -> dict[str, Any] | None:
    """ticker 의 Alpha Score 가 전체 stock_research 분포에서 어디에 위치하는지.

    Returns: {
        "percentile": int 0~100,           # 상위 % (작을수록 강함)
        "rank": int,                        # 전체 종목 중 순위 (1 = 최고)
        "total": int,                       # 전체 비교 종목 수
        "median": float,                    # 분포 중앙값
    } or None (분포 부족 시).

    표본 < 5 면 의미 없으므로 None 반환.
    """
    if score is None:
        return None
    try:
        import json as _json
        from . import database as _db
        with _db.db_session() as conn:
            rows = _db.fetch_latest_stock_research_all(conn)
        scores: list[float] = []
        for r in rows:
            try:
                a = _json.loads(r["alpha_score_json"] or "{}")
                s = a.get("alpha_score")
                if s is not None:
                    scores.append(float(s))
            except Exception:
                continue
        if len(scores) < 5:
            return None
        scores.sort(reverse=True)
        # 자기 자신보다 strictly 높은 점수 개수 + 1 = rank
        rank = sum(1 for s in scores if s > score) + 1
        # percentile = 자기보다 작거나 같은 비율 (top 10% 라면 percentile=90)
        below_or_equal = sum(1 for s in scores if s <= score)
        pct_rank = int(round(below_or_equal / len(scores) * 100))
        median = scores[len(scores) // 2]
        return {
            "percentile": pct_rank,         # 100 = 최고, 0 = 최저
            "top_pct": max(1, 100 - pct_rank),  # "상위 X%" — 보고용 (1~100)
            "rank": rank,
            "total": len(scores),
            "median": round(median, 1),
        }
    except Exception:
        return None


def classify_alpha_rating(score: float, data_confidence: str = "Medium") -> tuple[str, str]:
    """Alpha Score → (rating_en, rating_ko). Low confidence 시 한 단계 낮춤."""
    rating_en, rating_ko = "Low Priority", "현재 우선순위 낮음"
    for threshold, en, ko in RATING_TIERS:
        if score >= threshold:
            rating_en, rating_ko = en, ko
            break
    if data_confidence == "Low":
        idx = next((i for i, t in enumerate(RATING_TIERS) if t[1] == rating_en), 0)
        if idx < len(RATING_TIERS) - 1:
            _, rating_en, rating_ko = RATING_TIERS[idx + 1]
    return rating_en, rating_ko


# ---------------------------------------------------------------------------
# ComponentScore — 단일 컴포넌트의 점수 + 메타
# ---------------------------------------------------------------------------

# Status 값 — 4 종류 (사용자 spec)
STATUS_SCORED = "Scored"
STATUS_NEUTRAL = "Neutral"
STATUS_INSUFFICIENT = "Insufficient Data"
STATUS_NOT_APPLICABLE = "Not Applicable"
STATUS_CALC_ERROR = "Calculation Error"


def _component(
    score: float | None,
    *,
    status: str,
    confidence: str = "Medium",
    reason: str = "",
) -> dict[str, Any]:
    """ComponentScore dict 생성기."""
    if score is not None:
        score = max(0.0, min(100.0, float(score)))
    return {
        "score": score,
        "status": status,
        "confidence": confidence,
        "reason": reason or "",
    }


def _is_included(comp: dict[str, Any]) -> bool:
    """가중평균 산정에 포함되는지 — Scored / Neutral 만 포함."""
    return comp.get("score") is not None and comp.get("status") in (STATUS_SCORED, STATUS_NEUTRAL)


# ---------------------------------------------------------------------------
# Rating → 점수 매핑 (큐레이션 / Auto-Profile 의 Strong/Medium/Weak 등급용)
# ---------------------------------------------------------------------------

_RATING_TO_SCORE: dict[str, float] = {
    "Strong": 88, "Medium~Strong": 75, "Medium": 60,
    "Weak~Medium": 45, "Medium~Weak": 45,
    "Weak": 30, "Risk": 25, "Rising Risk": 35,
    "확인 필요": None,  # type: ignore — None 으로 처리
    "N/A": None,
}


def _rating_score(rating: str | None) -> float | None:
    if not rating:
        return None
    return _RATING_TO_SCORE.get(rating.strip())


# ---------------------------------------------------------------------------
# 컴포넌트별 빌더
# ---------------------------------------------------------------------------

def _component_thesis_strength(
    *, scores: dict, eq: dict, ticker: str,
) -> dict[str, Any]:
    """Thesis Strength — 큐레이션 thesis pillar / scoring.thesis_strength 활용.

    데이터 부족 = thesis_pillars 비어있고 scoring 도 default 50 인 경우 Insufficient.
    """
    # scoring.py 의 thesis_strength_score 는 큐레이션 pillar 기반 50~86 분포
    val = safe_float(scores.get("thesis_strength"))

    # 큐레이션이 있는지 확인 (curated.thesis_pillars)
    try:
        from .curated import thesis_pillars as _thesis_pillars
        pillars = _thesis_pillars(ticker) or []
    except Exception:
        pillars = []

    is_curated_eq = bool(eq.get("is_curated"))
    is_auto = bool(eq.get("is_auto_profiled"))

    if val is None:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="Thesis 점수 산정 불가 — 종목별 투자 논리 데이터 부재.",
        )

    # 큐레이션이 강하게 있는 경우 — Scored
    if pillars and is_curated_eq:
        return _component(
            val, status=STATUS_SCORED,
            confidence="High" if len(pillars) >= 3 else "Medium",
            reason=f"큐레이션 thesis pillar {len(pillars)} 개 + 카테고리 weight 반영.",
        )

    # 큐레이션 없고 자동 추정 — Heuristic 으로 Scored 처리하되 confidence Low
    if is_auto:
        return _component(
            val, status=STATUS_SCORED,
            confidence="Low",
            reason="큐레이션 thesis 미등록 — 카테고리 + Auto-Profile 기반 추정.",
        )

    # 큐레이션도 자동 추정도 없는데 점수만 있는 경우 — Neutral
    if 45 <= val <= 55:
        return _component(
            val, status=STATUS_NEUTRAL, confidence="Low",
            reason="명확한 thesis pillar 부재 — 카테고리 weight 만으로 평균 수준.",
        )
    return _component(
        val, status=STATUS_SCORED, confidence="Low",
        reason="제한된 데이터 기반 산정 — 추가 큐레이션 권장.",
    )


def _component_earnings_quality(
    *, eq: dict,
) -> dict[str, Any]:
    """Earnings Quality — earnings_durability_score 사용.

    is_curated → High confidence Scored
    is_auto_profiled → Heuristic Scored (confidence Low)
    둘 다 아님 → Insufficient
    """
    score = safe_float(eq.get("earnings_durability_score"))
    is_curated = bool(eq.get("is_curated"))
    is_auto = bool(eq.get("is_auto_profiled"))
    tier = eq.get("earnings_durability_tier") or ""

    if not is_curated and not is_auto:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="고객 분산 / 반복매출 / FCF 전환 등 이익의 질 데이터 부재.",
        )

    if score is None:
        return _component(
            None, status=STATUS_CALC_ERROR, confidence="Low",
            reason="Earnings Durability 계산 실패.",
        )

    if is_curated:
        return _component(
            score, status=STATUS_SCORED, confidence="High",
            reason=f"큐레이션 8 차원 합산 → {tier or 'Scored'} (durability {score:.0f}).",
        )

    # auto_profiled
    return _component(
        score, status=STATUS_SCORED, confidence="Low",
        reason=f"산업 keyword + 정량 지표 기반 자동 추정 ({tier or 'Heuristic'}).",
    )


def _component_moat(
    *, eq: dict,
) -> dict[str, Any]:
    """Moat / Lock-in — moat_map 7 차원 평균. "확인 필요" / "N/A" 는 평균에서 제외."""
    moat_map = eq.get("moat_map") or {}
    if not moat_map:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="해자 7 차원 (Network / Switching / Scale / Brand / Data / Reg / Cost) 데이터 없음.",
        )
    is_curated = bool(eq.get("is_curated"))
    is_auto = bool(eq.get("is_auto_profiled"))

    valid_scores: list[float] = []
    invalid: list[str] = []
    for key, m in moat_map.items():
        rating = m.get("rating") if isinstance(m, dict) else m
        s = _rating_score(rating)
        if s is None:
            invalid.append(key)
        else:
            valid_scores.append(s)

    if not valid_scores:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="해자 7 차원 모두 '확인 필요' — 점수화 불가.",
        )

    avg = sum(valid_scores) / len(valid_scores)
    coverage = len(valid_scores) / max(len(moat_map), 1)

    if is_curated and coverage >= 0.7:
        return _component(
            avg, status=STATUS_SCORED, confidence="High",
            reason=f"큐레이션 해자 {len(valid_scores)}/{len(moat_map)} 차원 평균.",
        )
    if is_auto and coverage >= 0.5:
        return _component(
            avg, status=STATUS_SCORED, confidence="Low",
            reason=f"산업 base + 정량 보정 — {len(valid_scores)}/{len(moat_map)} 차원.",
        )
    if coverage < 0.3:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason=f"해자 차원 {len(valid_scores)}/{len(moat_map)} 만 평가 — 결정적 근거 부족.",
        )
    return _component(
        avg, status=STATUS_NEUTRAL, confidence="Low",
        reason=f"부분 해자 데이터 — {len(valid_scores)}/{len(moat_map)} 차원 평균.",
    )


def _component_price_opportunity(
    *, scores: dict, md: dict,
) -> dict[str, Any]:
    """Price Opportunity — drawdown × multiple 결합.

    drawdown 데이터 자체가 없으면 Insufficient.
    """
    val = safe_float(scores.get("price_opportunity"))
    dd = safe_float(md.get("drawdown_from_52w_high"))
    pe = safe_float(md.get("forward_pe")) or safe_float(md.get("trailing_pe"))

    if dd is None and pe is None:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="52주 고점 drawdown / PE 모두 부재 — 가격 기회 평가 불가.",
        )

    if val is None:
        return _component(
            None, status=STATUS_CALC_ERROR, confidence="Low",
            reason="Price Opportunity 계산 실패.",
        )

    # drawdown 만 있고 PE 없는 경우 → confidence Medium
    parts: list[str] = []
    if dd is not None:
        parts.append(f"고점 대비 {dd*100:.0f}% drawdown")
    if pe is not None:
        parts.append(f"forward PE {pe:.1f}x")
    reason = " / ".join(parts) + " 결합."

    confidence = "High" if (dd is not None and pe is not None) else "Medium"

    # Neutral 영역 (45~55) 은 status Neutral, 그 외는 Scored
    if 45 <= val <= 55 and dd is None:
        return _component(val, status=STATUS_NEUTRAL, confidence="Low",
                          reason="가격 우위 신호 약함 — 평균 수준.")
    return _component(val, status=STATUS_SCORED, confidence=confidence, reason=reason)


def _component_event_catalyst(
    *, scores: dict, news_agg: dict, curated_events: list | None,
) -> dict[str, Any]:
    """Event / Catalyst Quality — staleness × confidence × thesis_impact.

    fresh / aging 이벤트 1개도 없고 큐레이션 이벤트도 없으면 Insufficient.
    Outdated only → 35~45 score 가능 (Scored 부정).
    """
    val = safe_float(scores.get("event_freshness"))
    fresh = (news_agg or {}).get("fresh_count", 0) or 0
    aging = (news_agg or {}).get("aging_count", 0) or 0
    outdated = (news_agg or {}).get("outdated_count", 0) or 0
    curated = curated_events or []
    has_fresh_curated = any(
        (ev or {}).get("staleness") in ("fresh", "aging") for ev in curated
    )

    # 모든 신호 부재
    if val is None and fresh == 0 and aging == 0 and outdated == 0 and not curated:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="최근 뉴스 / 큐레이션 이벤트 없음 — catalyst 평가 데이터 부재.",
        )

    if val is None:
        return _component(
            None, status=STATUS_CALC_ERROR, confidence="Low",
            reason="Event Freshness 계산 실패.",
        )

    # Outdated 만 있는 경우 — 부정 score 가 의도된 결과
    if fresh == 0 and aging == 0 and not has_fresh_curated:
        if outdated > 0:
            return _component(
                val, status=STATUS_SCORED, confidence="Low",
                reason=f"Outdated 뉴스만 존재 ({outdated}건) — catalyst 신선도 낮음.",
            )
        # 큐레이션 이벤트만 있는데 staleness 정보 없음
        return _component(
            val, status=STATUS_NEUTRAL, confidence="Low",
            reason="이벤트 staleness 미확인 — 평균 수준 추정.",
        )

    # Fresh 신호 있음
    parts = []
    if fresh > 0:
        parts.append(f"Fresh {fresh}건")
    if aging > 0:
        parts.append(f"Aging {aging}건")
    if has_fresh_curated:
        parts.append(f"큐레이션 이벤트 {len(curated)}건")
    confidence = "High" if (fresh >= 2 and has_fresh_curated) else (
        "Medium" if (fresh >= 1 or has_fresh_curated) else "Low"
    )
    return _component(
        val, status=STATUS_SCORED, confidence=confidence,
        reason=" + ".join(parts) + " — staleness × thesis_impact 가중합.",
    )


def _component_industry_bottleneck(
    *, scores: dict, bottleneck_thesis: dict | None,
) -> dict[str, Any]:
    """Industry Tailwind / Bottleneck Exposure.

    bottleneck_thesis 가 있으면 거기 score 사용 (가장 확실).
    없으면 evidence_strength fallback — 하지만 evidence_strength 자체가
    구조 신호가 아니라 재무+이벤트 누적이라 confidence Low.
    """
    if bottleneck_thesis and bottleneck_thesis.get("score") is not None:
        s = safe_float(bottleneck_thesis.get("score"))
        return _component(
            s, status=STATUS_SCORED, confidence="High",
            reason=(bottleneck_thesis.get("alpha_judgment") or
                    "Bottleneck Thesis 산출 점수 — 8 factor 합산.")[:300],
        )

    ev = safe_float(scores.get("evidence_strength"))
    if ev is None:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="Bottleneck Thesis 매칭 없음 + Evidence Strength 부재 — 산업 위치 평가 불가.",
        )
    # Bottleneck 매칭 없는 종목 — Neutral 처리 (evidence_strength 가 산업 신호 아님)
    return _component(
        ev, status=STATUS_NEUTRAL, confidence="Low",
        reason="명확한 Bottleneck 노출 미확인 — Evidence Strength fallback.",
    )


def _component_financial_quality(
    *, scores: dict, md: dict,
) -> dict[str, Any]:
    """Financial Quality — OPM / ROE / FCF / 시총 지표.

    핵심 지표 (OPM/ROE/FCF) 가 모두 None 이면 Insufficient.
    """
    val = safe_float(scores.get("financial_quality"))
    om = safe_float(md.get("operating_margin"))
    roe = safe_float(md.get("roe"))
    fcfy = safe_float(md.get("fcf_yield"))
    cap = safe_float(md.get("market_cap"))

    metric_count = sum(1 for x in (om, roe, fcfy) if x is not None)
    if metric_count == 0 and not cap:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="OPM / ROE / FCF Yield 모두 None — 재무 품질 평가 불가.",
        )

    if val is None:
        return _component(
            None, status=STATUS_CALC_ERROR, confidence="Low",
            reason="Financial Quality 계산 실패.",
        )

    # 메트릭 보유 개수에 따라 confidence
    confidence = "High" if metric_count >= 3 else ("Medium" if metric_count >= 2 else "Low")

    parts = []
    if om is not None:
        parts.append(f"OPM {om*100:.0f}%")
    if roe is not None:
        parts.append(f"ROE {roe*100:.0f}%")
    if fcfy is not None:
        parts.append(f"FCF Yield {fcfy*100:.1f}%")
    reason = (" / ".join(parts) + " 합산." ) if parts else "재무 메트릭 일부 부재."

    if metric_count == 0:
        # cap 만 있는 경우 — Neutral
        return _component(val, status=STATUS_NEUTRAL, confidence="Low", reason=reason)
    return _component(val, status=STATUS_SCORED, confidence=confidence, reason=reason)


def _component_risk_control(
    *, scores: dict, md: dict, news_agg: dict, curated_events: list | None,
) -> dict[str, Any]:
    """Risk Control — drawdown / PE / 부정 이벤트 / urgent 뉴스 기반.

    drawdown / PE / events / news 모두 부재면 Insufficient.
    """
    val = safe_float(scores.get("risk_control"))
    dd = safe_float(md.get("drawdown_from_52w_high"))
    pe = safe_float(md.get("forward_pe")) or safe_float(md.get("trailing_pe"))
    has_events = bool(curated_events)
    urgent = bool((news_agg or {}).get("urgent"))
    score_sum = (news_agg or {}).get("score_sum")

    no_signals = (
        dd is None and pe is None and not has_events
        and not urgent and (score_sum is None or score_sum == 0)
    )
    if val is None or no_signals:
        return _component(
            None, status=STATUS_INSUFFICIENT, confidence="Low",
            reason="Drawdown / Valuation / 이벤트 / 뉴스 신호 부재 — 리스크 평가 불가.",
        )

    parts = []
    if dd is not None:
        parts.append(f"고점 대비 {dd*100:.0f}% drawdown")
    if pe is not None:
        parts.append(f"PE {pe:.1f}x")
    if urgent:
        parts.append("urgent 뉴스 존재")
    if has_events:
        parts.append(f"큐레이션 이벤트 {len(curated_events or [])}건 반영")
    reason = " / ".join(parts) + " 합산 — 100 - risk."

    confidence = "High" if (dd is not None and pe is not None and has_events) else (
        "Medium" if (dd is not None or pe is not None or has_events) else "Low"
    )

    # val < 50 이면 리스크가 큼 — 명확한 Scored
    return _component(val, status=STATUS_SCORED, confidence=confidence, reason=reason)


# ---------------------------------------------------------------------------
# Component 통합 빌더 — calculate_alpha_score 가 호출
# ---------------------------------------------------------------------------

def build_components(
    *,
    ticker: str,
    market_data: dict | None,
    scores: dict | None,
    earnings_quality: dict | None = None,
    bottleneck_thesis: dict | None = None,
    news_agg: dict | None = None,
    curated_events: list | None = None,
) -> dict[str, dict[str, Any]]:
    """8 컴포넌트 ComponentScore dict 반환."""
    md = market_data or {}
    sc = scores or {}
    eq = earnings_quality or {}
    na = news_agg or {}

    return {
        "thesis_strength": _component_thesis_strength(scores=sc, eq=eq, ticker=ticker),
        "earnings_quality": _component_earnings_quality(eq=eq),
        "moat_lockin": _component_moat(eq=eq),
        "price_opportunity": _component_price_opportunity(scores=sc, md=md),
        "event_catalyst": _component_event_catalyst(
            scores=sc, news_agg=na, curated_events=curated_events,
        ),
        "industry_bottleneck": _component_industry_bottleneck(
            scores=sc, bottleneck_thesis=bottleneck_thesis,
        ),
        "financial_quality": _component_financial_quality(scores=sc, md=md),
        "risk_control": _component_risk_control(
            scores=sc, md=md, news_agg=na, curated_events=curated_events,
        ),
    }


# ---------------------------------------------------------------------------
# Missing Data Penalty
# ---------------------------------------------------------------------------

def _missing_data_penalty(scored_weight_ratio: float) -> tuple[float, bool]:
    """scored_weight_ratio (0~1) 에 따른 페널티 + Provisional 여부.

    >= 0.85 → 0
    0.70~0.84 → -3
    0.50~0.69 → -7
    < 0.50 → -12 + Provisional
    """
    if scored_weight_ratio >= 0.85:
        return 0.0, False
    if scored_weight_ratio >= 0.70:
        return -3.0, False
    if scored_weight_ratio >= 0.50:
        return -7.0, False
    return -12.0, True


# ---------------------------------------------------------------------------
# Data Confidence 판정 (전체 — 8 컴포넌트 status 종합)
# ---------------------------------------------------------------------------

def _data_confidence(
    components: dict[str, dict[str, Any]],
    *,
    is_curated_eq: bool,
    is_auto_profiled: bool,
    has_market_data: bool,
    scored_weight_ratio: float,
    is_manually_curated: bool = False,
    is_llm_researched: bool = False,
) -> str:
    """Data Confidence 라벨.

    우선순위 (2026-05 — auto_curation 도입 후):
        Manual Override : 사용자가 curated.py 에 직접 입력 — 항상 최우선
        LLM Researched  : auto_curation DB 에 LLM 생성 큐레이션 존재 (SEC 10-K + 뉴스)
        Heuristic       : auto_profile (산업 keyword + 정량) 만
        Low             : 데이터 부족
    """
    if not has_market_data:
        return "Low"
    if scored_weight_ratio < 0.50:
        return "Low"

    # 1. Manual Override — 사용자 수동 큐레이션 (최우선)
    if is_manually_curated and scored_weight_ratio >= 0.70:
        return "Manual Override"

    # 2. LLM Researched — auto_curation DB
    if is_llm_researched and scored_weight_ratio >= 0.60:
        return "LLM Researched"

    # 3. Heuristic — auto_profile (산업 + 정량 추정)
    if is_auto_profiled and scored_weight_ratio >= 0.60:
        return "Heuristic"

    # 4. 컴포넌트 confidence 기반 fallback (기존 룰)
    high_count = sum(1 for c in components.values() if c.get("confidence") == "High")
    med_count = sum(1 for c in components.values() if c.get("confidence") == "Medium")
    if scored_weight_ratio >= 0.70 and (high_count + med_count) >= 4:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# 메인 빌더
# ---------------------------------------------------------------------------

def calculate_alpha_score(
    *,
    ticker: str,
    market_data: dict | None,
    scores: dict | None,
    earnings_quality: dict | None = None,
    bottleneck_thesis: dict | None = None,
    news_agg: dict | None = None,
    curated_events: list | None = None,
) -> dict[str, Any]:
    """8 컴포넌트 점수 + Alpha Score + Rating + Coverage + 해석 반환.

    핵심 차이 (이전 버전 대비):
        1. 데이터 부족 컴포넌트는 score=None (Insufficient Data) — 가중평균에서 제외
        2. scored_weight_ratio 계산 → Missing Data Penalty 적용
        3. 각 컴포넌트의 status / confidence / reason 동시 반환
    """
    md = market_data or {}
    eq = earnings_quality or {}
    na = news_agg or {}
    bn = bottleneck_thesis

    # 8 컴포넌트 빌드 (status / confidence / reason 포함)
    components = build_components(
        ticker=ticker, market_data=md, scores=scores,
        earnings_quality=eq, bottleneck_thesis=bn,
        news_agg=na, curated_events=curated_events,
    )

    # Scored / Neutral 만 가중평균 — Insufficient / Calc Error 는 제외
    total_weight = 0.0
    weighted_sum = 0.0
    missing_components: list[str] = []
    for key, weight in WEIGHTS.items():
        comp = components[key]
        if _is_included(comp):
            weighted_sum += float(comp["score"]) * weight
            total_weight += weight
        else:
            missing_components.append(key)

    if total_weight == 0:
        # 모든 컴포넌트 부재 — N/A
        return {
            "alpha_score": None,
            "alpha_rating_en": "Avoid / Not Enough Evidence",
            "alpha_rating_ko": "회피 또는 근거 부족 — 산정 불가",
            "components": components,
            "data_confidence": "Low",
            "is_provisional": True,
            "scored_weight_ratio": 0.0,
            "scored_coverage_pct": 0,
            "missing_components": missing_components,
            "missing_data_penalty": 0.0,
            "raw_alpha_score": None,
            "interpretation": (
                f"{ticker} 의 Alpha Score 산정에 필요한 8 컴포넌트 데이터가 모두 부재합니다. "
                "기본 시장 데이터 / 큐레이션 / 이벤트 신호 확보 후 재계산 가능."
            ),
        }

    # Re-normalize — total_weight 가 1.0 이 아닐 때 (missing 항목 제외)
    raw_alpha = weighted_sum / total_weight  # 0~100, missing 제외 가중평균

    scored_weight_ratio = total_weight  # 1.0 만점 기준
    penalty, is_provisional_by_coverage = _missing_data_penalty(scored_weight_ratio)

    # 큐레이션 보너스 폐지 (사용자 요구 2026-05-03)
    # 이전 룰: is_curated + EQ ≥ 75 + Moat ≥ 70 → +3
    # 변경 이유: 큐레이션은 시드 예시일 뿐, 모든 종목이 8 컴포넌트만으로
    # 동등하게 평가받아야 함. EQ / Moat 가 강하면 그 컴포넌트 점수에 이미 반영됨 —
    # 큐레이션 여부로 추가 보너스 주는 건 이중 가산 + 편향.
    alpha = round(min(100.0, max(0.0, raw_alpha + penalty)), 1)

    # Data Confidence
    is_curated_eq = bool(eq.get("is_curated"))
    is_manual = bool(eq.get("is_manually_curated"))
    is_llm = bool(eq.get("is_llm_researched"))
    is_auto_eq = bool(eq.get("is_auto_profiled"))
    has_md = bool(md.get("available"))
    confidence = _data_confidence(
        components, is_curated_eq=is_curated_eq, is_auto_profiled=is_auto_eq,
        has_market_data=has_md, scored_weight_ratio=scored_weight_ratio,
        is_manually_curated=is_manual, is_llm_researched=is_llm,
    )

    is_provisional = is_provisional_by_coverage or (confidence == "Low")
    if confidence == "Low" and not is_provisional_by_coverage:
        # Low confidence 추가 discount
        alpha = round(alpha * 0.95, 1)

    rating_en, rating_ko = classify_alpha_rating(alpha, confidence)

    interpretation = generate_alpha_score_interpretation(
        alpha=alpha,
        components=components,
        rating_en=rating_en,
        eq=eq, bn=bn,
        confidence=confidence,
        is_provisional=is_provisional,
        scored_weight_ratio=scored_weight_ratio,
        missing_components=missing_components,
        penalty=penalty,
    )

    return {
        "alpha_score": alpha,
        "alpha_rating_en": rating_en,
        "alpha_rating_ko": rating_ko,
        "components": components,
        "data_confidence": confidence,
        "is_provisional": is_provisional,
        "scored_weight_ratio": round(scored_weight_ratio, 3),
        "scored_coverage_pct": int(round(scored_weight_ratio * 100)),
        "missing_components": missing_components,
        "missing_data_penalty": penalty,
        "raw_alpha_score": round(raw_alpha, 1),
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# 해석 문장 생성
# ---------------------------------------------------------------------------

def _component_score_or_na(comp: dict[str, Any]) -> str:
    s = comp.get("score")
    if s is None:
        return "N/A"
    return f"{s:.0f}"


def generate_alpha_score_interpretation(
    *,
    alpha: float,
    components: dict[str, dict[str, Any]],
    rating_en: str,
    eq: dict | None,
    bn: dict | None,
    confidence: str,
    is_provisional: bool,
    scored_weight_ratio: float,
    missing_components: list[str],
    penalty: float,
) -> str:
    """1~3 문장 해석. 컴포넌트 강약점 + Coverage + 페널티 안내."""
    # 가장 강한 / 약한 컴포넌트 (Scored 만 대상)
    scored_items = [
        (k, c) for k, c in components.items()
        if _is_included(c) and c.get("score") is not None
    ]
    if not scored_items:
        return f"Alpha Score {alpha:.0f} — 산정 가능한 컴포넌트 부재."

    sorted_comps = sorted(scored_items, key=lambda kv: kv[1]["score"], reverse=True)
    top_key, top_comp = sorted_comps[0]
    bot_key, bot_comp = sorted_comps[-1]

    parts: list[str] = []

    if alpha >= 88:
        parts.append(
            f"{LABELS_KO[top_key]} ({top_comp['score']:.0f}) 가 가장 강하고, "
            f"{int(round(scored_weight_ratio*100))}% Coverage 기준 매우 정렬된 후보 "
            f"(Alpha Score {alpha:.0f})입니다."
        )
    elif alpha >= 80:
        parts.append(
            f"적극 검토 후보 (Alpha Score {alpha:.0f}). {LABELS_KO[top_key]} "
            f"({top_comp['score']:.0f}) 가 강점이며, {LABELS_KO[bot_key]} "
            f"({bot_comp['score']:.0f}) 검증이 후속 과제입니다."
        )
    elif alpha >= 70:
        parts.append(
            f"리서치 후보 (Alpha Score {alpha:.0f}). {LABELS_KO[top_key]} 강점은 "
            f"확인되나 {LABELS_KO[bot_key]} ({bot_comp['score']:.0f}) 가 부족합니다."
        )
    elif alpha >= 62:
        parts.append(
            f"관찰 / 진입 시점 대기 (Alpha Score {alpha:.0f}). {LABELS_KO[bot_key]} "
            f"({bot_comp['score']:.0f}) 가 가장 큰 우려입니다."
        )
    elif alpha >= 54:
        parts.append(
            f"Thesis 검증 필요 (Alpha Score {alpha:.0f}). {LABELS_KO[bot_key]} "
            f"({bot_comp['score']:.0f}) 등 핵심 컴포넌트가 약합니다."
        )
    elif alpha >= 45:
        parts.append(f"우선순위 낮음 (Alpha Score {alpha:.0f}).")
    else:
        parts.append(f"회피 또는 근거 부족 단계 (Alpha Score {alpha:.0f}).")

    # 큐레이션 / Auto-Profile / Bottleneck 추가 컨텍스트
    if eq and eq.get("is_curated") and eq.get("alpha_judgment"):
        parts.append((eq.get("alpha_judgment") or "").strip())
    elif eq and eq.get("is_auto_profiled") and eq.get("alpha_judgment"):
        parts.append((eq.get("alpha_judgment") or "").strip())
    elif bn and bn.get("alpha_judgment"):
        parts.append((bn.get("alpha_judgment") or "").strip())

    # Coverage / Provisional / Confidence 안내
    coverage_pct = int(round(scored_weight_ratio * 100))
    n_missing = len(missing_components)
    n_total = len(WEIGHTS)
    n_scored = n_total - n_missing

    if scored_weight_ratio < 0.85:
        coverage_msg = (
            f"※ 총 {n_total}개 항목 중 {n_scored}개가 산정되었으며 "
            f"{n_missing}개 항목은 데이터 부족으로 제외되었습니다 "
            f"(Scored Coverage {coverage_pct}%, Missing Data Penalty {penalty:.0f}점)."
        )
        parts.append(coverage_msg)

    if is_provisional and scored_weight_ratio < 0.50:
        parts.append(
            "※ 일부 핵심 항목의 데이터가 부족해 현재 점수는 Provisional Score입니다. "
            "추가 데이터 확보 후 점수가 변경될 수 있습니다."
        )
    elif confidence == "Low":
        parts.append(
            "※ Data Confidence Low — 점수가 한 단계 낮춰 표시되며, 핵심 데이터 보강 권장."
        )
    elif confidence == "Heuristic":
        parts.append(
            "※ Data Confidence Heuristic — 큐레이션이 아닌 산업 keyword + 정량 지표 "
            "(margin / FCF / ROE) 기반 자동 추정. 큐레이션 등록 시 Confidence 상향."
        )
    elif confidence == "Medium":
        parts.append(
            "※ Data Confidence Medium — 큐레이션 / 시장 데이터 일부 부족 — 추가 점검 권장."
        )

    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Action Tag 일관성 보정
# ---------------------------------------------------------------------------

def reconcile_with_action_tag(
    alpha_result: dict[str, Any],
    action_tag: str | None,
    too_crowded: bool = False,
) -> dict[str, Any]:
    """Action Tag 와 rating 의 일관성 + 특수 조건 보정.

    components 가 ComponentScore dict 구조로 바뀌었으므로 score 추출 helper 사용.
    """
    alpha = alpha_result.get("alpha_score")
    if alpha is None:
        return alpha_result

    components = alpha_result.get("components", {}) or {}
    rating_en = alpha_result.get("alpha_rating_en", "")

    def _score(key: str) -> float | None:
        c = components.get(key) or {}
        return safe_float(c.get("score"))

    risk = _score("risk_control")
    price = _score("price_opportunity")

    if too_crowded and alpha >= 90:
        alpha_result["alpha_rating_en"] = "Research Now (Crowded — 비중 신중)"
        alpha_result["alpha_rating_ko"] = "관찰 (이미 컨센서스 형성 — 비중 신중)"

    if risk is not None and risk < 50 and alpha >= 70:
        alpha_result["alpha_rating_en"] = "Need Thesis Check"
        alpha_result["alpha_rating_ko"] = "Risk Control 낮음 — Thesis 점검 필요"

    if price is not None and price < 50 and rating_en in (
        "Exceptional Candidate", "High Conviction Candidate", "Research Now"
    ):
        alpha_result["alpha_rating_en"] = "Wait for Better Entry"
        alpha_result["alpha_rating_ko"] = "진입 시점 대기 (가격 부담)"

    return alpha_result
