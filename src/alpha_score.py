"""Alpha Score — 종목별 통합 투자 매력도 점수 (0~100).

8 요소 가중합:
    Thesis Strength             15%
    Earnings Quality            15%
    Moat / Lock-in              15%
    Price Opportunity           15%
    Event / Catalyst Quality    10%
    Industry Tailwind / Bottleneck 10%
    Financial Quality           10%
    Risk Control                10%

핵심 원칙 (사용자 spec):
    - "자동 매수 추천" 이 아니라 리서치 우선순위 / 투자 매력도 정량화 보조 지표
    - 95점 이상도 "최우선 정밀 검토 후보" 표현으로 제한 — "무조건 매수" 표현 금지
    - 데이터 부족 시 Provisional Score + Data Confidence 표시
    - 기존 점수 (Final Score / Discovery / Bottleneck / Earnings Durability) 와 매핑

기존 점수 → Alpha 컴포넌트 매핑:
    scores.thesis_strength       → Thesis Strength
    earnings_durability_score    → Earnings Quality
    moat_map (avg)               → Moat / Lock-in
    scores.price_opportunity     → Price Opportunity
    scores.event_freshness       → Event / Catalyst Quality
    bottleneck score / civ score → Industry Tailwind / Bottleneck Exposure
    scores.financial_quality     → Financial Quality
    scores.risk_control          → Risk Control (이미 "통제 우수 = 높은 점수" 구조)
"""
from __future__ import annotations

from typing import Any

from .utils import safe_float


# ---------------------------------------------------------------------------
# 가중치 (사용자 spec)
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
# Rating 매핑 — Alpha Score → 한국어 라벨 + 영어 라벨
# ---------------------------------------------------------------------------

# Rating 임계값 — 실제 정량 데이터 기반 분포에 맞춰 재조정.
# 8 컴포넌트 중 일부가 항상 50 (중립 fallback) 으로 채워지는 구조 특성상,
# 80+ 점수는 Exceptional 후보가 아니라 "정렬된 매우 강한 후보" 로 보는 게 적절.
RATING_TIERS = [
    (88, "Exceptional Candidate", "최우선 정밀 검토 후보"),
    (80, "High Conviction Candidate", "강한 비중 후보로 검토 가능"),
    (70, "Research Now", "적극 리서치 후보"),
    (62, "Watchlist / Wait for Better Entry", "관찰 / 진입 시점 대기"),
    (54, "Need Thesis Check", "Thesis 검증 필요"),
    (45, "Low Priority", "현재 우선순위 낮음"),
    (0, "Avoid / Not Enough Evidence", "회피 또는 근거 부족"),
]


def classify_alpha_rating(score: float, data_confidence: str = "Medium") -> tuple[str, str]:
    """Alpha Score → (rating_en, rating_ko)."""
    rating_en, rating_ko = "Low Priority", "현재 우선순위 낮음"
    for threshold, en, ko in RATING_TIERS:
        if score >= threshold:
            rating_en, rating_ko = en, ko
            break

    # Data Confidence 가 Low 면 한 단계 낮춤 (사용자 spec)
    if data_confidence == "Low":
        idx = next((i for i, t in enumerate(RATING_TIERS) if t[1] == rating_en), 0)
        if idx < len(RATING_TIERS) - 1:
            _, rating_en, rating_ko = RATING_TIERS[idx + 1]
    return rating_en, rating_ko


# ---------------------------------------------------------------------------
# 컴포넌트 점수 빌더 — 기존 Alpha 데이터 활용
# ---------------------------------------------------------------------------

def _moat_map_avg_score(moat_map: dict | None) -> float:
    """moat_map 의 7 차원 평균 점수 (0~100)."""
    if not moat_map:
        return 50.0
    rating_to_score = {
        "Strong": 90, "Medium~Strong": 75, "Medium": 60,
        "Weak~Medium": 45, "Medium~Weak": 45,
        "Weak": 30, "확인 필요": 50, "N/A": 50,
    }
    vals: list[float] = []
    for _key, m in moat_map.items():
        if isinstance(m, dict):
            r = m.get("rating", "확인 필요")
        else:
            r = m
        vals.append(rating_to_score.get(r, 50))
    return sum(vals) / len(vals) if vals else 50.0


def _to_0_100(x: float | None, default: float = 50.0) -> float:
    if x is None:
        return default
    return max(0.0, min(100.0, float(x)))


def _bottleneck_industry_score(
    bottleneck_thesis: dict | None,
    discovery_civ_score: float | None,
) -> float:
    """Industry Tailwind / Bottleneck Exposure — Bottleneck thesis 우선, 없으면 civ score."""
    if bottleneck_thesis:
        return _to_0_100(bottleneck_thesis.get("score"))
    if discovery_civ_score is not None:
        return _to_0_100(discovery_civ_score)
    return 50.0


# ---------------------------------------------------------------------------
# Data Confidence 판정
# ---------------------------------------------------------------------------

def _data_confidence(
    is_curated_eq: bool,
    has_market_data: bool,
    has_bottleneck: bool,
    has_event: bool,
) -> str:
    signals = sum([is_curated_eq, has_market_data, has_bottleneck, has_event])
    if is_curated_eq and has_market_data and (has_bottleneck or has_event):
        return "High"
    if has_market_data and is_curated_eq:
        return "Medium"
    if has_market_data:
        return "Medium" if signals >= 2 else "Low"
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
) -> dict[str, Any]:
    """8 컴포넌트 점수 + Alpha Score + Rating + Interpretation 반환.

    모든 입력은 nullable — 데이터 부족 시 50 (중립) + Data Confidence Low.
    """
    md = market_data or {}
    sc = scores or {}
    eq = earnings_quality or {}
    bn = bottleneck_thesis  # None 가능 — 매칭 없는 종목
    na = news_agg or {}

    # 컴포넌트별 점수 계산
    components: dict[str, float] = {}

    # 1) Thesis Strength — scores.thesis_strength 사용
    components["thesis_strength"] = _to_0_100(sc.get("thesis_strength"))

    # 2) Earnings Quality — earnings_durability_score 사용
    components["earnings_quality"] = _to_0_100(
        eq.get("earnings_durability_score"), default=50.0
    )

    # 3) Moat / Lock-in — moat_map 평균
    components["moat_lockin"] = _moat_map_avg_score(eq.get("moat_map"))

    # 4) Price Opportunity — scores.price_opportunity
    components["price_opportunity"] = _to_0_100(sc.get("price_opportunity"))

    # 5) Event / Catalyst Quality — scores.event_freshness
    components["event_catalyst"] = _to_0_100(sc.get("event_freshness"))

    # 6) Industry Tailwind / Bottleneck — Bottleneck thesis score 우선, 없으면 evidence_strength
    components["industry_bottleneck"] = _bottleneck_industry_score(
        bn, sc.get("evidence_strength"),
    )

    # 7) Financial Quality
    components["financial_quality"] = _to_0_100(sc.get("financial_quality"))

    # 8) Risk Control — scores.risk_control 그대로 (높을수록 통제 우수)
    components["risk_control"] = _to_0_100(sc.get("risk_control"))

    # 가중합
    alpha_raw = sum(components[k] * w for k, w in WEIGHTS.items())

    # 큐레이션 보정 — 종목 고유 thesis + Earnings Quality + Moat 가 모두 강하면 +3
    # (8 컴포넌트 중 가격 / 이벤트 / 리스크가 항상 중립 50 으로 끌어내리는 구조 보정)
    curated_bonus = 0.0
    if (
        eq
        and eq.get("is_curated")
        and (eq.get("earnings_durability_score") or 0) >= 70
        and components.get("moat_lockin", 50) >= 65
    ):
        curated_bonus = 3.0

    alpha_raw += curated_bonus
    alpha = round(min(100.0, alpha_raw), 1)

    # Data Confidence
    is_curated_eq = bool(eq.get("is_curated"))
    has_md = bool(md.get("available"))
    has_bn = bn is not None
    has_event = (sc.get("event_freshness") or 0) > 30 or bool(na.get("count"))
    confidence = _data_confidence(is_curated_eq, has_md, has_bn, has_event)

    # Provisional 표시 — 데이터 부족 시 점수에 약간 discount
    is_provisional = confidence == "Low"
    if is_provisional:
        alpha = round(alpha * 0.95, 1)

    rating_en, rating_ko = classify_alpha_rating(alpha, confidence)

    # 해석 1~2 문장
    interpretation = generate_alpha_score_interpretation(
        alpha=alpha,
        components=components,
        rating_en=rating_en,
        eq=eq,
        bn=bn,
        confidence=confidence,
        is_provisional=is_provisional,
    )

    return {
        "alpha_score": alpha,
        "alpha_rating_en": rating_en,
        "alpha_rating_ko": rating_ko,
        "components": components,
        "data_confidence": confidence,
        "is_provisional": is_provisional,
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# 해석 문장 생성
# ---------------------------------------------------------------------------

def generate_alpha_score_interpretation(
    *,
    alpha: float,
    components: dict[str, float],
    rating_en: str,
    eq: dict | None,
    bn: dict | None,
    confidence: str,
    is_provisional: bool,
) -> str:
    """1~2 문장 해석. 종목별 컴포넌트 강약점 요약."""
    # 가장 강한 / 약한 컴포넌트 식별
    sorted_comps = sorted(components.items(), key=lambda x: x[1], reverse=True)
    top1 = sorted_comps[0]
    bottom1 = sorted_comps[-1]

    top_label = LABELS_KO[top1[0]]
    bottom_label = LABELS_KO[bottom1[0]]

    parts: list[str] = []

    # 첫 문장 — 핵심 평가
    if alpha >= 95:
        parts.append(
            f"{top_label} ({top1[1]:.0f}) 이 가장 강하고, 8 컴포넌트 전반이 정렬된 "
            f"최우선 정밀 검토 후보 (Alpha Score {alpha:.0f}) 입니다."
        )
    elif alpha >= 90:
        parts.append(
            f"{top_label} ({top1[1]:.0f}) 가 매우 강한 강한 비중 후보 (Alpha Score "
            f"{alpha:.0f}) 입니다. 핵심 점검 포인트는 {bottom_label} ({bottom1[1]:.0f}) 입니다."
        )
    elif alpha >= 80:
        parts.append(
            f"적극 리서치 후보 (Alpha Score {alpha:.0f}). {top_label} ({top1[1]:.0f}) 가 "
            f"강점이며, {bottom_label} ({bottom1[1]:.0f}) 검증이 후속 과제입니다."
        )
    elif alpha >= 70:
        parts.append(
            f"관찰 / 진입 시점 대기 단계 (Alpha Score {alpha:.0f}). {top_label} 강점은 "
            f"확인되나, {bottom_label} ({bottom1[1]:.0f}) 가 부족해 즉시 강한 비중은 부담."
        )
    elif alpha >= 60:
        parts.append(
            f"Thesis 검증 필요 단계 (Alpha Score {alpha:.0f}). {bottom_label} "
            f"({bottom1[1]:.0f}) 의 추가 점검이 우선입니다."
        )
    elif alpha >= 50:
        parts.append(
            f"현재 Alpha 로직상 우선순위 낮음 (Alpha Score {alpha:.0f}). {bottom_label} "
            f"({bottom1[1]:.0f}) 등 핵심 컴포넌트가 부족합니다."
        )
    else:
        parts.append(
            f"Alpha Score {alpha:.0f} — 회피 또는 근거 부족 단계. 정밀 리서치보다 "
            "다른 후보를 우선 검토하는 편이 적절합니다."
        )

    # 큐레이션 / Bottleneck 추가 컨텍스트 — 한 단락 전체 노출 (잘라내지 않음)
    if eq and eq.get("is_curated") and eq.get("alpha_judgment"):
        aj = (eq.get("alpha_judgment") or "").strip()
        if aj:
            parts.append(aj)
    elif bn and bn.get("alpha_judgment"):
        bj = (bn.get("alpha_judgment") or "").strip()
        if bj:
            parts.append(bj)

    # Provisional / Confidence 안내
    if is_provisional:
        parts.append(
            "※ Data Confidence Low — 큐레이션 / 시장 데이터 부족 — Provisional Score 로 표시됩니다."
        )
    elif confidence == "Medium":
        parts.append(
            "※ Data Confidence Medium — 큐레이션 / 시장 데이터 일부 부족 — 추가 점검 권장."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# 컴포넌트 + Action Tag 일관성 보정
# ---------------------------------------------------------------------------

def reconcile_with_action_tag(
    alpha_result: dict[str, Any],
    action_tag: str | None,
    too_crowded: bool = False,
) -> dict[str, Any]:
    """기존 action_tag 와 Alpha rating 일관성 점검 + 특수 조건 적용 (사용자 spec).

    - Too Crowded 면 90+ 라도 High Conviction 으로 바로 표시 X
    - Risk Control < 50 이면 rating 을 Need Thesis Check / Avoid 로 제한
    - Price Opportunity < 50 이면 Watchlist / Wait for Entry 로 제한
    """
    alpha = alpha_result["alpha_score"]
    rating_en = alpha_result["alpha_rating_en"]
    components = alpha_result["components"]

    if too_crowded and alpha >= 90:
        rating_en = "Research Now (Crowded — 비중 신중)"
        alpha_result["alpha_rating_en"] = rating_en
        alpha_result["alpha_rating_ko"] = "관찰 (이미 컨센서스 형성 — 비중 신중)"

    if components.get("risk_control", 50) < 50 and alpha >= 70:
        alpha_result["alpha_rating_en"] = "Need Thesis Check"
        alpha_result["alpha_rating_ko"] = "Risk Control 낮음 — Thesis 점검 필요"

    if components.get("price_opportunity", 50) < 50 and rating_en in (
        "Exceptional Candidate", "High Conviction Candidate", "Research Now"
    ):
        alpha_result["alpha_rating_en"] = "Wait for Better Entry"
        alpha_result["alpha_rating_ko"] = "진입 시점 대기 (가격 부담)"

    return alpha_result
