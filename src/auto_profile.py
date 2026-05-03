"""Auto-Profile Engine — 큐레이션 미등록 종목의 8 EQ 차원 + 7 Moat 차원 자동 추정.

설계 원칙:
1. **하드코딩 종목 금지** — industry / sector 키워드 + market_data 정량 지표만 사용.
2. **LLM 사용 안 함** — keyword + heuristic 만으로 reasonable 한 추정.
3. **큐레이션 미등록 종목도 50~80 점 분포** 에 들어가도록 — Discovery 발견된 종목이
   점수 시스템에서 "확인 필요" 50 점 페널티를 받지 않게.
4. **결과는 항상 "Heuristic" Data Confidence** 로 표시 — 큐레이션과 시각적 구분.

핵심 임팩트:
    - Russell 1000 niche supplier 가 실제 데이터 기반 점수를 받음
    - 큐레이션 42 종목과 발견된 종목이 동등 조건에서 비교 가능
    - "형우가 모르는 알파" 가 시스템에 부각될 수 있음
"""
from __future__ import annotations

from typing import Any

from .utils import safe_float


# ---------------------------------------------------------------------------
# Industry / Sector → base profile (heuristic)
#
# 산업별로 "고객 분산 / 반복매출 / Lock-in / Brand / Network Effect …" 의 base 등급을
# 정의. yfinance 의 industry 텍스트를 키워드 매칭.
# ---------------------------------------------------------------------------

# (matchers, profile dict)
# matchers — industry / sector / name 텍스트에서 매칭 키워드 (lowercase)
# profile — 각 차원의 base 등급 ("Strong" / "Medium~Strong" / "Medium" / "Weak~Medium" / "Weak")

INDUSTRY_PROFILES: list[tuple[list[str], dict[str, str]]] = [
    # ── Software / SaaS ────────────────────────────────────────────────
    (
        ["software", "internet content", "internet retail"],
        {
            "customer_diversification": "Medium~Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Strong",
            "pricing_power": "Medium~Strong",
            "margin_quality": "Strong",
            "cash_conversion": "Strong",
            "capital_intensity": "Strong",   # asset-light = good
            "incremental_roic": "Strong",
            # Moat
            "network_effect": "Medium~Strong",
            "switching_cost": "Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium",
            "data_advantage": "Strong",
            "regulatory_barrier": "Weak",
            "cost_advantage": "Medium",
        },
    ),
    # ── Semiconductors ─────────────────────────────────────────────────
    (
        ["semiconductors", "semiconductor equipment"],
        {
            "customer_diversification": "Weak~Medium",
            "recurring_revenue": "Medium",
            "lock_in": "Medium~Strong",
            "pricing_power": "Medium~Strong",
            "margin_quality": "Strong",
            "cash_conversion": "Medium~Strong",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium~Strong",
            "network_effect": "Medium",
            "switching_cost": "Medium~Strong",
            "scale_advantage": "Strong",
            "brand": "Medium",
            "data_advantage": "Weak~Medium",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Strong",
        },
    ),
    # ── Aerospace & Defense ────────────────────────────────────────────
    (
        ["aerospace & defense", "aerospace and defense"],
        {
            "customer_diversification": "Weak~Medium",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak~Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium",
            "data_advantage": "Medium",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium",
        },
    ),
    # ── Healthcare — Drug Manufacturers / Biotech ─────────────────────
    (
        ["drug manufacturers", "biotechnology"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Medium~Strong",
            "pricing_power": "Medium~Strong",
            "margin_quality": "Medium~Strong",
            "cash_conversion": "Medium",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Medium~Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium",
        },
    ),
    # ── Healthcare — Medical Devices / Diagnostics ────────────────────
    (
        ["medical devices", "diagnostics & research", "diagnostics and research",
         "health information services"],
        {
            "customer_diversification": "Medium~Strong",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Strong",
            "pricing_power": "Medium~Strong",
            "margin_quality": "Medium~Strong",
            "cash_conversion": "Medium~Strong",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium~Strong",
            "network_effect": "Medium",
            "switching_cost": "Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium~Strong",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium",
        },
    ),
    # ── Healthcare Plans / Drug Retailers ──────────────────────────────
    (
        ["healthcare plans", "drug retailers"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Medium",
            "pricing_power": "Medium",
            "margin_quality": "Weak~Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Strong",
            "incremental_roic": "Medium",
            "network_effect": "Medium",
            "switching_cost": "Medium",
            "scale_advantage": "Strong",
            "brand": "Medium",
            "data_advantage": "Medium~Strong",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Banks / Capital Markets / Asset Management ─────────────────────
    (
        ["banks", "capital markets", "asset management"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Medium~Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak~Medium",
            "incremental_roic": "Medium",
            "network_effect": "Medium",
            "switching_cost": "Medium~Strong",
            "scale_advantage": "Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Insurance / Credit Services ────────────────────────────────────
    (
        ["insurance", "credit services"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium~Strong",
            "cash_conversion": "Strong",
            "capital_intensity": "Strong",
            "incremental_roic": "Medium~Strong",
            "network_effect": "Strong",
            "switching_cost": "Strong",
            "scale_advantage": "Strong",
            "brand": "Strong",
            "data_advantage": "Medium~Strong",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Strong",
        },
    ),
    # ── Utilities ──────────────────────────────────────────────────────
    (
        ["utilities", "utilities regulated", "utilities independent"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Strong",
            "pricing_power": "Weak~Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak",
            "incremental_roic": "Weak~Medium",
            "network_effect": "Weak",
            "switching_cost": "Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Weak",
            "data_advantage": "Weak",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Oil & Gas / Mining / Materials (commodity) ────────────────────
    (
        ["oil & gas", "uranium", "specialty chemicals", "specialty industrial",
         "gold", "copper", "steel", "specialty materials"],
        {
            "customer_diversification": "Medium~Strong",
            "recurring_revenue": "Medium",
            "lock_in": "Weak~Medium",
            "pricing_power": "Weak~Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak",
            "incremental_roic": "Weak~Medium",
            "network_effect": "Weak",
            "switching_cost": "Medium",
            "scale_advantage": "Medium~Strong",
            "brand": "Weak",
            "data_advantage": "Weak",
            "regulatory_barrier": "Medium~Strong",
            "cost_advantage": "Strong",
        },
    ),
    # ── Communication Equipment / Electronic Components ────────────────
    (
        ["communication equipment", "electronic components", "electrical equipment",
         "computer hardware"],
        {
            "customer_diversification": "Weak~Medium",
            "recurring_revenue": "Medium",
            "lock_in": "Medium~Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak~Medium",
            "switching_cost": "Medium~Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium",
            "data_advantage": "Weak~Medium",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Medium",
        },
    ),
    # ── Restaurants / Footwear / Apparel / Specialty Retail ────────────
    (
        ["restaurants", "footwear", "apparel", "specialty retail",
         "discount stores", "home improvement", "household products",
         "beverages"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Medium",
            "lock_in": "Weak~Medium",
            "pricing_power": "Medium~Strong",
            "margin_quality": "Medium",
            "cash_conversion": "Medium~Strong",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Weak",
            "scale_advantage": "Medium~Strong",
            "brand": "Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Weak",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Auto Manufacturers ────────────────────────────────────────────
    (
        ["auto manufacturers"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Weak~Medium",
            "lock_in": "Weak",
            "pricing_power": "Weak~Medium",
            "margin_quality": "Weak~Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak",
            "incremental_roic": "Weak~Medium",
            "network_effect": "Weak",
            "switching_cost": "Weak",
            "scale_advantage": "Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Medium",
        },
    ),
    # ── Travel / Lodging / Airlines / Gambling ─────────────────────────
    (
        ["travel services", "lodging", "airlines", "gambling", "leisure",
         "resorts & casinos"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Weak~Medium",
            "lock_in": "Weak",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak",
            "incremental_roic": "Weak~Medium",
            "network_effect": "Medium",
            "switching_cost": "Weak",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Medium",
        },
    ),
    # ── REIT — Specialty / Industrial / Healthcare ─────────────────────
    (
        ["reit"],
        {
            "customer_diversification": "Medium~Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium~Strong",
            "cash_conversion": "Strong",
            "capital_intensity": "Weak~Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Weak",
            "data_advantage": "Weak",
            "regulatory_barrier": "Medium~Strong",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Telecom Services ───────────────────────────────────────────────
    (
        ["telecom services"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Strong",
            "lock_in": "Strong",
            "pricing_power": "Weak~Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium~Strong",
            "capital_intensity": "Weak",
            "incremental_roic": "Weak~Medium",
            "network_effect": "Medium~Strong",
            "switching_cost": "Strong",
            "scale_advantage": "Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Strong",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Engineering & Construction / Specialty Industrial ──────────────
    (
        ["engineering & construction", "engineering and construction",
         "rental & leasing", "rental and leasing", "farm & heavy construction"],
        {
            "customer_diversification": "Medium~Strong",
            "recurring_revenue": "Medium",
            "lock_in": "Medium",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Weak~Medium",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Medium",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium",
            "data_advantage": "Weak",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Medium~Strong",
        },
    ),
    # ── Internet Content / Entertainment ───────────────────────────────
    (
        ["entertainment"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Medium",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium",
            "capital_intensity": "Medium",
            "incremental_roic": "Medium",
            "network_effect": "Medium",
            "switching_cost": "Medium",
            "scale_advantage": "Medium~Strong",
            "brand": "Strong",
            "data_advantage": "Medium~Strong",
            "regulatory_barrier": "Weak",
            "cost_advantage": "Medium",
        },
    ),
    # ── IT Services ───────────────────────────────────────────────────
    (
        ["information technology services"],
        {
            "customer_diversification": "Strong",
            "recurring_revenue": "Medium~Strong",
            "lock_in": "Medium~Strong",
            "pricing_power": "Medium",
            "margin_quality": "Medium",
            "cash_conversion": "Medium~Strong",
            "capital_intensity": "Strong",
            "incremental_roic": "Medium",
            "network_effect": "Weak",
            "switching_cost": "Medium~Strong",
            "scale_advantage": "Medium~Strong",
            "brand": "Medium~Strong",
            "data_advantage": "Medium",
            "regulatory_barrier": "Medium",
            "cost_advantage": "Medium~Strong",
        },
    ),
]

# Default profile — 산업 매칭 안 되는 종목용 (전부 Medium)
DEFAULT_PROFILE: dict[str, str] = {
    "customer_diversification": "Medium",
    "recurring_revenue": "Medium",
    "lock_in": "Medium",
    "pricing_power": "Medium",
    "margin_quality": "Medium",
    "cash_conversion": "Medium",
    "capital_intensity": "Medium",
    "incremental_roic": "Medium",
    "network_effect": "Medium",
    "switching_cost": "Medium",
    "scale_advantage": "Medium",
    "brand": "Medium",
    "data_advantage": "Medium",
    "regulatory_barrier": "Medium",
    "cost_advantage": "Medium",
}


def _match_industry_profile(meta: dict[str, Any]) -> dict[str, str]:
    """Industry / sector 키워드 매칭으로 base 프로필 반환."""
    text = " ".join([
        (meta.get("industry") or "").lower(),
        (meta.get("sector") or "").lower(),
        (meta.get("name") or "").lower(),
    ])
    for matchers, profile in INDUSTRY_PROFILES:
        for kw in matchers:
            if kw in text:
                return dict(profile)
    return dict(DEFAULT_PROFILE)


# ---------------------------------------------------------------------------
# 정량 데이터 보정 — 실제 financial 지표가 base 프로필을 덮어씀
# ---------------------------------------------------------------------------

def _adjust_with_financials(
    profile: dict[str, str],
    md: dict[str, Any],
) -> dict[str, str]:
    """gross_margin / operating_margin / fcf_yield / roe / market_cap 으로 보정."""
    md = md or {}
    gm = safe_float(md.get("gross_margin"))
    om = safe_float(md.get("operating_margin"))
    fcf = safe_float(md.get("fcf_yield"))
    roe = safe_float(md.get("roe"))
    rg = safe_float(md.get("revenue_growth") or md.get("revenue_growth_yoy"))
    mcap = safe_float(md.get("market_cap"))

    # Pricing Power — gross margin 직접 매핑 (가장 신뢰할 수 있는 정량 신호)
    if gm is not None:
        if gm >= 0.65:
            profile["pricing_power"] = "Strong"
        elif gm >= 0.45:
            profile["pricing_power"] = "Medium~Strong"
        elif gm >= 0.30:
            profile["pricing_power"] = "Medium"
        elif gm >= 0.20:
            profile["pricing_power"] = "Weak~Medium"
        else:
            profile["pricing_power"] = "Weak"

    # Margin Quality — operating margin 매핑
    if om is not None:
        if om >= 0.30:
            profile["margin_quality"] = "Strong"
        elif om >= 0.20:
            profile["margin_quality"] = "Medium~Strong"
        elif om >= 0.10:
            profile["margin_quality"] = "Medium"
        elif om >= 0.03:
            profile["margin_quality"] = "Weak~Medium"
        else:
            profile["margin_quality"] = "Weak"

    # Cash Conversion — FCF Yield
    if fcf is not None:
        if fcf >= 0.06:
            profile["cash_conversion"] = "Strong"
        elif fcf >= 0.03:
            profile["cash_conversion"] = "Medium~Strong"
        elif fcf >= 0.01:
            profile["cash_conversion"] = "Medium"
        elif fcf >= 0:
            profile["cash_conversion"] = "Weak~Medium"
        else:
            profile["cash_conversion"] = "Weak"

    # Incremental ROIC — ROE × Revenue Growth (간이 proxy)
    if roe is not None and rg is not None:
        score = roe * 100 + rg * 50  # 가중 합
        if score >= 30:
            profile["incremental_roic"] = "Strong"
        elif score >= 20:
            profile["incremental_roic"] = "Medium~Strong"
        elif score >= 10:
            profile["incremental_roic"] = "Medium"
        elif score >= 5:
            profile["incremental_roic"] = "Weak~Medium"
        else:
            profile["incremental_roic"] = "Weak"
    elif roe is not None:
        if roe >= 0.20:
            profile["incremental_roic"] = "Strong"
        elif roe >= 0.10:
            profile["incremental_roic"] = "Medium"
        elif roe < 0:
            profile["incremental_roic"] = "Weak"

    # Scale Advantage — market_cap (정량 명확)
    if mcap is not None:
        if mcap >= 200e9:
            profile["scale_advantage"] = "Strong"
        elif mcap >= 50e9:
            profile["scale_advantage"] = "Medium~Strong"
        elif mcap >= 10e9:
            profile["scale_advantage"] = "Medium"
        else:
            # 산업 base 유지
            pass

    # Customer Diversification — 정량 신호 부족 — base 유지

    # Capital Intensity — operating_margin > 25% & fcf_yield > 3% 동반이면 light
    if om is not None and fcf is not None:
        if om >= 0.25 and fcf >= 0.04:
            profile["capital_intensity"] = "Strong"   # asset-light
        elif om < 0.05 and (fcf or 0) < 0.01:
            profile["capital_intensity"] = "Weak"

    return profile


# ---------------------------------------------------------------------------
# 등급 → 한국어 코멘트 (기본 템플릿)
# ---------------------------------------------------------------------------

_COMMENT_TEMPLATES: dict[str, dict[str, str]] = {
    "customer_diversification": {
        "Strong": "산업 특성상 다고객 분산 구조 — 특정 고객 의존도가 낮은 경향.",
        "Medium~Strong": "주요 고객은 일정 수 있으나 매출 분산이 비교적 양호.",
        "Medium": "고객 분산도가 평균 수준 — 산업 / 사업부 데이터 추가 확인 필요.",
        "Weak~Medium": "특정 고객 / 채널 의존이 일부 존재할 가능성.",
        "Weak": "고객 집중도가 높을 가능성 — 상위 고객 매출 비중 확인 필요.",
    },
    "recurring_revenue": {
        "Strong": "산업 특성상 구독 / 장기 계약 / 사용량 기반 반복 매출 비중 높음.",
        "Medium~Strong": "반복 거래 패턴은 형성돼 있으나 본질적 구독 모델은 아닐 수 있음.",
        "Medium": "단발 매출과 반복 매출이 혼재 — segment 데이터 확인 필요.",
        "Weak~Medium": "단발성 / 거래성 매출 비중이 큰 산업 특성.",
        "Weak": "거래성 / 일회성 매출 의존도가 높은 사업 모델.",
    },
    "lock_in": {
        "Strong": "워크플로우 통합 / 인증 장벽 / 데이터 누적 등으로 전환비용 매우 높은 산업.",
        "Medium~Strong": "고객 시스템과의 통합 또는 인증 절차로 일정 수준 lock-in 가능.",
        "Medium": "전환비용이 평균 수준 — 산업 내 경쟁 정도에 따라 변동.",
        "Weak~Medium": "고객 전환이 비교적 용이한 사업 특성.",
        "Weak": "범용 / 가격 민감 사업으로 lock-in 약함.",
    },
    "pricing_power": {
        "Strong": "Gross margin 65%+ 수준 — 강한 가격 결정력의 정량 증거.",
        "Medium~Strong": "Gross margin 45~65% — 일정 수준 가격 결정력 보유.",
        "Medium": "Gross margin 30~45% — 평균적 가격 협상력.",
        "Weak~Medium": "Gross margin 20~30% — 가격 결정력 제한적.",
        "Weak": "Gross margin 20% 미만 — 가격 협상력 약함.",
    },
    "margin_quality": {
        "Strong": "Operating margin 30%+ — 구조적으로 매우 강한 수익성.",
        "Medium~Strong": "Operating margin 20~30% — 견조한 본업 수익성.",
        "Medium": "Operating margin 10~20% — 평균적 수익성.",
        "Weak~Medium": "Operating margin 3~10% — 수익성 제한적.",
        "Weak": "Operating margin 3% 미만 또는 적자 — 수익성 취약.",
    },
    "cash_conversion": {
        "Strong": "FCF Yield 6%+ — 회계 이익이 현금흐름으로 잘 전환되는 구조.",
        "Medium~Strong": "FCF Yield 3~6% — 양호한 현금 창출.",
        "Medium": "FCF Yield 1~3% — 평균 수준.",
        "Weak~Medium": "FCF Yield 0~1% — 운전자본 / capex 부담 가능성.",
        "Weak": "FCF margin 음수 — 회계 이익과 현금흐름 괴리 점검 필요.",
    },
    "capital_intensity": {
        "Strong": "Asset-light 구조로 추정 — 성장에 필요한 capex 부담 낮음.",
        "Medium~Strong": "자본 부담 평균 이하 — 성장 투자 효율 양호.",
        "Medium": "산업 평균 수준의 capex 부담.",
        "Weak~Medium": "성장에 일정 capex 가 지속 필요한 산업 특성.",
        "Weak": "고자본집약 산업 — 성장 투자 부담 큼.",
    },
    "incremental_roic": {
        "Strong": "ROE × 매출 성장이 동반 — 신규 투자 회수 효율 양호.",
        "Medium~Strong": "ROE 또는 매출 성장 한쪽이 양호 — 추가 수익성 확장 가능.",
        "Medium": "ROIC 수준 평균 — 신규 투자 효율 추가 확인 필요.",
        "Weak~Medium": "ROIC 추정치 낮음 — 자본 효율 점검 필요.",
        "Weak": "수익성 / 성장 모두 낮음 — 자본 효율 취약.",
    },
}


def _comment_for(dim_key: str, rating: str) -> str:
    return (_COMMENT_TEMPLATES.get(dim_key, {}).get(rating)
            or f"{rating} 수준으로 추정 (자동 추정 — 추가 리서치 권장).")


# ---------------------------------------------------------------------------
# Public — 8 EQ 차원 + 7 Moat 차원 자동 추정
# ---------------------------------------------------------------------------

EQ_DIMENSION_KEYS = (
    "customer_diversification", "recurring_revenue", "lock_in", "pricing_power",
    "margin_quality", "cash_conversion", "capital_intensity", "incremental_roic",
)

MOAT_DIMENSION_KEYS = (
    "network_effect", "switching_cost", "scale_advantage", "brand",
    "data_advantage", "regulatory_barrier", "cost_advantage",
)


def estimate_earnings_quality_dims(
    meta: dict[str, Any], md: dict[str, Any]
) -> dict[str, dict[str, str]]:
    """8 EQ 차원 자동 추정 — {key: {rating, comment}}."""
    profile = _match_industry_profile(meta)
    profile = _adjust_with_financials(profile, md)
    out: dict[str, dict[str, str]] = {}
    for k in EQ_DIMENSION_KEYS:
        rating = profile.get(k, "Medium")
        out[k] = {"rating": rating, "comment": _comment_for(k, rating)}
    return out


def estimate_moat_map(
    meta: dict[str, Any], md: dict[str, Any]
) -> dict[str, str]:
    """7 Moat 차원 자동 추정 — {key: rating}."""
    profile = _match_industry_profile(meta)
    profile = _adjust_with_financials(profile, md)
    return {k: profile.get(k, "Medium") for k in MOAT_DIMENSION_KEYS}


def estimate_alpha_judgment(
    ticker: str, meta: dict[str, Any], md: dict[str, Any],
) -> str:
    """자동 추정 종목의 종합 판단 — keyword + 정량 지표 기반."""
    name = (meta.get("name") or ticker).strip()
    industry = (meta.get("industry") or "").strip()
    gm = safe_float(md.get("gross_margin"))
    om = safe_float(md.get("operating_margin"))
    rg = safe_float(md.get("revenue_growth") or md.get("revenue_growth_yoy"))

    parts: list[str] = []
    parts.append(
        f"{name} 의 이익의 질은 {industry or '해당 산업'} 의 일반 특성과 정량 지표 (margin, "
        "growth, ROE) 를 기반으로 자동 추정됩니다."
    )

    margin_phrase = ""
    if gm is not None and om is not None:
        if gm >= 0.50 and om >= 0.20:
            margin_phrase = (
                f"Gross margin {gm * 100:.0f}% / Operating margin {om * 100:.0f}% 수준의 강한 "
                "수익성이 가격 결정력 / 마진 품질의 정량 증거로 작용합니다."
            )
        elif gm < 0.30 or (om or 0) < 0.05:
            margin_phrase = (
                f"Gross margin {gm * 100:.0f}% / Operating margin {(om or 0) * 100:.0f}% 수준으로 "
                "수익성이 평균 이하 — 가격 협상력 / 비용 구조 점검이 우선 과제입니다."
            )
    if margin_phrase:
        parts.append(margin_phrase)

    if rg is not None and rg >= 0.10:
        parts.append(
            f"매출 성장률 {rg * 100:.0f}% 수준으로 성장 사이클은 살아 있으며, 신규 투자의 "
            "증분 ROIC 가 유지되는지가 핵심 점검 포인트입니다."
        )
    elif rg is not None and rg < 0:
        parts.append(
            f"매출 성장률 {rg * 100:.0f}% — 사이클 / 본업 둔화 신호 — turnaround / restructuring "
            "여부 점검 필요."
        )

    parts.append(
        "본 평가는 큐레이션 미등록 상태에서의 자동 추정이며, 실제 customer concentration / "
        "recurring revenue 비중 / FCF margin / capex / qualification barrier 데이터의 추가 "
        "리서치가 정밀 판단의 전제 조건입니다."
    )
    return " ".join(parts)
