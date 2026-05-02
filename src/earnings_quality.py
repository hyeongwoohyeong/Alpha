"""Earnings Quality & Moat Assessment + Strategic Lens 빌더.

종목별 큐레이션 (curated.EARNINGS_QUALITY_KO / MOAT_MAP_KO / STRATEGIC_LENS_KO)
이 있으면 그것을 사용하고, 없으면 기본 fallback ("확인 필요") 으로 채워서
모든 종목에 일관된 구조를 반환한다.

생성 결과는 stock_research 테이블의 JSON 컬럼에 저장 + UI 에서 카드로 표시.
"""
from __future__ import annotations

from typing import Any

from .curated import (
    alpha_judgment as _curated_alpha_judgment,
    earnings_quality as _curated_eq,
    moat_map as _curated_moat,
    strategic_lens as _curated_lens,
)


# ---------------------------------------------------------------------------
# 등급 → 점수 (0~1) 매핑 — Earnings Durability Score 계산용
# ---------------------------------------------------------------------------

_RATING_SCORE: dict[str, float] = {
    "Strong": 0.90,
    "Medium~Strong": 0.75,
    "Medium": 0.60,
    "Weak~Medium": 0.45,
    "Weak": 0.30,
    "Risk": 0.20,
    "Rising Risk": 0.30,   # 자본집약도 상승 등 — 중립~약간 부정
    "확인 필요": 0.50,    # 중립
    "N/A": 0.50,
}


def _rating_to_score(rating: str | None) -> float:
    if not rating:
        return 0.50
    return _RATING_SCORE.get(rating.strip(), 0.50)


# ---------------------------------------------------------------------------
# 8 차원 — 가중치 (사용자 스펙)
# ---------------------------------------------------------------------------

EQ_DIMENSIONS = [
    ("customer_diversification", "Customer Diversification", "고객 분산도", 0.15),
    ("recurring_revenue", "Recurring Revenue", "반복매출 비중", 0.15),
    ("lock_in", "Lock-in / Switching Cost", "전환비용 / lock-in", 0.15),
    ("pricing_power", "Pricing Power", "가격 결정력", 0.15),
    ("margin_quality", "Margin Quality", "마진 품질", 0.15),
    ("cash_conversion", "Cash Conversion", "현금 전환력", 0.15),
    ("capital_intensity", "Capital Intensity", "자본 집약도", 0.05),
    ("incremental_roic", "Incremental ROIC", "증분 ROIC", 0.05),
]


# 7 Moat 차원
MOAT_DIMENSIONS = [
    ("network_effect", "Network Effect", "네트워크 효과"),
    ("switching_cost", "Switching Cost", "전환비용"),
    ("scale_advantage", "Scale Advantage", "규모의 경제"),
    ("brand", "Brand", "브랜드"),
    ("data_advantage", "Data Advantage", "데이터 우위"),
    ("regulatory_barrier", "Regulatory / Certification Barrier", "규제 / 인증 장벽"),
    ("cost_advantage", "Cost Advantage", "비용 우위"),
]


# ---------------------------------------------------------------------------
# 빈 dim entry (fallback)
# ---------------------------------------------------------------------------

def _empty_dim() -> dict[str, str]:
    return {"rating": "확인 필요", "comment": "이 종목의 큐레이션 데이터가 아직 등록되지 않았습니다. 추가 리서치 필요."}


# ---------------------------------------------------------------------------
# Earnings Durability Score 계산
# ---------------------------------------------------------------------------

def compute_durability_score(eq: dict[str, dict[str, str]]) -> tuple[int, str]:
    """8 차원 등급에서 가중평균 점수 0~100 + 라벨 반환.

    Capital Intensity 와 Incremental ROIC 는 가중치 5% (사용자 스펙).
    """
    total = 0.0
    for key, _label_en, _label_ko, weight in EQ_DIMENSIONS:
        dim = eq.get(key) or {}
        s = _rating_to_score(dim.get("rating"))
        total += s * weight * 100  # 0~100 스케일

    score = int(round(total))
    if score >= 85:
        tier = "Very Strong"
    elif score >= 70:
        tier = "Strong"
    elif score >= 55:
        tier = "Moderate"
    elif score >= 40:
        tier = "Weak"
    else:
        tier = "High Risk"
    return score, tier


# ---------------------------------------------------------------------------
# Public — 종목 빌더
# ---------------------------------------------------------------------------

def build_earnings_quality(ticker: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """종목별 Earnings Quality 결과 dict 생성.

    Returns: {
        "earnings_durability_score": int 0~100,
        "earnings_durability_tier": "Very Strong | Strong | Moderate | Weak | High Risk",
        "dimensions": { dim_key: {"rating": ..., "comment": ..., "label_en": ..., "label_ko": ...}, ... },
        "moat_map": { moat_key: rating, ... },
        "alpha_judgment": "한 단락" or None,
        "is_curated": True / False,
    }
    """
    cur_eq = _curated_eq(ticker) or {}
    is_curated = bool(cur_eq)

    dimensions: dict[str, dict[str, str]] = {}
    for key, label_en, label_ko, weight in EQ_DIMENSIONS:
        dim = cur_eq.get(key) or _empty_dim()
        dimensions[key] = {
            "rating": dim.get("rating", "확인 필요"),
            "comment": dim.get("comment", ""),
            "label_en": label_en,
            "label_ko": label_ko,
            "weight_pct": int(weight * 100),
        }

    score, tier = compute_durability_score(cur_eq) if is_curated else (50, "Moderate")

    # Moat Map
    cur_moat = _curated_moat(ticker) or {}
    moat: dict[str, dict[str, str]] = {}
    for key, label_en, label_ko in MOAT_DIMENSIONS:
        moat[key] = {
            "rating": cur_moat.get(key, "확인 필요"),
            "label_en": label_en,
            "label_ko": label_ko,
        }

    # Alpha Judgment
    judgment = _curated_alpha_judgment(ticker)
    if not judgment:
        if is_curated:
            judgment = (
                f"{ticker} 의 이익의 질은 위 8 차원 평가 합산 기준 {tier} 수준입니다. "
                "구체적 종합 판단은 추가 큐레이션 등록 후 보강될 예정입니다."
            )
        else:
            judgment = (
                f"{ticker} 의 Earnings Quality 큐레이션은 아직 등록되지 않았습니다. "
                "상위 고객 매출 비중, 반복매출 비중, retention, FCF 전환율 등 핵심 데이터의 추가 리서치가 필요합니다."
            )

    return {
        "earnings_durability_score": score,
        "earnings_durability_tier": tier,
        "dimensions": dimensions,
        "moat_map": moat,
        "alpha_judgment": judgment,
        "is_curated": is_curated,
    }


def build_strategic_lens(ticker: str) -> dict[str, Any]:
    """종목별 Strategic Lens (SWOT / PESTEL / 3C / 3P).

    Returns: {
        "swot": {strength: [...], weakness: [...], opportunity: [...], threat: [...]},
        "pestel": {political, economic, social, technological, environmental, legal},
        "three_c": {company, customer, competitor},
        "three_p": {product, pricing, positioning},
        "is_curated": True / False,
    }
    """
    cur = _curated_lens(ticker)
    if cur:
        return {**cur, "is_curated": True}

    placeholder = {"확인 필요"}
    return {
        "swot": {
            "strength": ["확인 필요 — 큐레이션 미등록"],
            "weakness": ["확인 필요"],
            "opportunity": ["확인 필요"],
            "threat": ["확인 필요"],
        },
        "pestel": {
            "political": "확인 필요",
            "economic": "확인 필요",
            "social": "확인 필요",
            "technological": "확인 필요",
            "environmental": "확인 필요",
            "legal": "확인 필요",
        },
        "three_c": {
            "company": "확인 필요",
            "customer": "확인 필요",
            "competitor": "확인 필요",
        },
        "three_p": {
            "product": "확인 필요",
            "pricing": "확인 필요",
            "positioning": "확인 필요",
        },
        "is_curated": False,
    }
