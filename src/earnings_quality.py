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

    데이터 우선순위 (2026-05 — auto_curation 도입 후):
        1. Manual Override (curated.py 정적 dict) — `is_curated=True, is_llm_researched=False`
        2. LLM Researched (auto_curation DB) — `is_curated=True, is_llm_researched=True`
           ※ is_curated 는 "유의미한 큐레이션 데이터가 있다" 의 의미 — manual + LLM 모두 True
        3. Heuristic (auto_profile) — `is_auto_profiled=True`
        4. 둘 다 불가 → 셋 다 False

    Returns: {
        ...
        "is_curated": True / False,             # manual 또는 LLM 큐레이션 존재
        "is_manually_curated": True / False,    # curated.py 정적 dict (Manual Override)
        "is_llm_researched": True / False,      # auto_curation DB 만 (manual 없음)
        "is_auto_profiled": True / False,       # auto_profile 만 (큐레이션 없음)
    }
    """
    from .curated import is_manually_curated as _is_manual

    is_manual = _is_manual(ticker)
    cur_eq = _curated_eq(ticker) or {}
    # cur_eq 는 manual 정적 dict 또는 auto_curation 둘 중 하나일 수 있음
    has_eq_data = bool(cur_eq)
    is_llm_researched = has_eq_data and not is_manual
    is_curated = has_eq_data  # manual + LLM 모두 True

    # ── 자동 추정 데이터 (큐레이션 없을 때만 산출) ─────────────────────────
    auto_eq: dict[str, dict[str, str]] = {}
    auto_moat: dict[str, str] = {}
    auto_judgment: str | None = None
    is_auto_profiled = False

    if not is_curated:
        try:
            from .auto_profile import (
                estimate_alpha_judgment,
                estimate_earnings_quality_dims,
                estimate_moat_map,
            )

            row = row or {}
            meta = {
                "ticker": ticker,
                "name": row.get("name_en") or row.get("name_ko") or ticker,
                "industry": row.get("industry"),
                "sector": row.get("sector"),
            }
            md = row.get("market_data") or {}
            auto_eq = estimate_earnings_quality_dims(meta, md)
            auto_moat = estimate_moat_map(meta, md)
            auto_judgment = estimate_alpha_judgment(ticker, meta, md)
            # auto_eq 가 의미있는 결과 (전부 Medium 이 아닐 때) 인 경우만 활성화
            if auto_eq:
                is_auto_profiled = True
        except Exception:
            # auto_profile 실패 시 fallback 으로 진행
            is_auto_profiled = False

    # ── 8 EQ 차원 dictionary ─────────────────────────────────────────────
    dimensions: dict[str, dict[str, str]] = {}
    for key, label_en, label_ko, weight in EQ_DIMENSIONS:
        if is_curated:
            dim = cur_eq.get(key) or _empty_dim()
            rating = dim.get("rating", "확인 필요")
            comment = dim.get("comment", "")
        elif is_auto_profiled:
            dim = auto_eq.get(key) or {"rating": "Medium", "comment": ""}
            rating = dim.get("rating", "Medium")
            comment = dim.get("comment", "")
        else:
            empty = _empty_dim()
            rating = empty["rating"]
            comment = empty["comment"]
        dimensions[key] = {
            "rating": rating,
            "comment": comment,
            "label_en": label_en,
            "label_ko": label_ko,
            "weight_pct": int(weight * 100),
        }

    # ── Earnings Durability Score / Tier ─────────────────────────────────
    if is_curated:
        score, tier = compute_durability_score(cur_eq)
    elif is_auto_profiled:
        # auto_eq 는 {key: {rating,comment}} 구조 — durability 가중평균 계산 가능
        score, tier = compute_durability_score(auto_eq)
    else:
        score, tier = 50, "Moderate"

    # ── Moat Map ─────────────────────────────────────────────────────────
    cur_moat = _curated_moat(ticker) or {}
    moat: dict[str, dict[str, str]] = {}
    for key, label_en, label_ko in MOAT_DIMENSIONS:
        if is_curated and cur_moat.get(key):
            rating = cur_moat[key]
        elif is_auto_profiled and auto_moat.get(key):
            rating = auto_moat[key]
        else:
            rating = "확인 필요"
        moat[key] = {
            "rating": rating,
            "label_en": label_en,
            "label_ko": label_ko,
        }

    # ── Alpha Judgment ───────────────────────────────────────────────────
    judgment = _curated_alpha_judgment(ticker)
    if not judgment:
        if is_curated:
            judgment = (
                f"{ticker} 의 이익의 질은 위 8 차원 평가 합산 기준 {tier} 수준입니다. "
                "구체적 종합 판단은 추가 큐레이션 등록 후 보강될 예정입니다."
            )
        elif is_auto_profiled and auto_judgment:
            judgment = auto_judgment
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
        "is_manually_curated": is_manual,
        "is_llm_researched": is_llm_researched,
        "is_auto_profiled": is_auto_profiled,
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
