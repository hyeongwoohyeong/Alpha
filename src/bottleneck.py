"""Bottleneck Supplier Queue — 밸류체인 병목 공급자 발굴 로직.

설계 원칙:
1. 특정 종목 / 특정 섹터에 하드코딩하지 않는다.
2. 7 산업 (AI / Space / EV / Healthcare / Defense / Robotics / Grid) 의 밸류체인을
   섹터별 키워드 + 밸류체인 위치로 분해해 데이터로 표현한다.
3. wide_universe 의 industry / name 텍스트 키워드 매칭으로 후보 식별 → 모든 섹터에 일반 적용.
4. NO_LLM_MODE 동작 — keyword + 정량 지표로 점수 계산.

핵심 철학 (사용자 spec):
"최종 승자를 맞히는 게 아니라, 산업 성장 시 모든 플레이어가 공통으로 필요로 하는 병목 공급자."
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger, safe_float

log = get_logger("bottleneck")


QUEUE_BOTTLENECK = "Bottleneck Supplier"


# ---------------------------------------------------------------------------
# 7 산업 밸류체인 분해 — (theme, tailwind_score, [(value_chain_position, keywords)])
#
# 모든 키워드는 lowercase 비교. industry / name / sector 텍스트를 합쳐 매칭.
# value_chain_position 별 base_criticality 를 매겨 Bottleneck Criticality 점수 산출.
#
# tailwind_score: 0.0 ~ 1.0 — 산업 구조적 성장 (CAPEX / 정책 / 안보 / 비용곡선 등)
# ---------------------------------------------------------------------------

# (theme_label_ko, tailwind_score, [(position_label, base_criticality, keywords)])
BOTTLENECK_THEMES: list[tuple[str, float, list[tuple[str, float, list[str]]]]] = [
    # ── AI / Data Center ────────────────────────────────────────────────
    (
        "AI / 데이터센터 인프라", 0.95,
        [
            ("HBM / Memory 공급", 0.95,
             ["memory", "dram", "hbm", "nand", "storage"]),
            ("Advanced Packaging / 반도체 장비", 0.95,
             ["semiconductor equipment", "packaging", "wafer", "lithography", "etch", "deposition"]),
            ("Optical Interconnect / 고속 연결", 0.90,
             ["optical", "interconnect", "fiber", "transceiver", "photonics"]),
            ("Networking / Ethernet", 0.85,
             ["communication equipment", "networking", "ethernet", "switch", "router"]),
            ("데이터센터 전력 / 냉각", 0.95,
             ["electrical equipment", "specialty industrial", "thermal", "cooling", "power management"]),
            ("Power Semiconductor", 0.85,
             ["power semiconductor", "wide bandgap", "silicon carbide", "gallium nitride"]),
        ],
    ),
    # ── Space / Aerospace ────────────────────────────────────────────────
    (
        "우주 / Aerospace 인프라", 0.85,
        [
            ("정밀 광학 / Optics", 0.90,
             ["optics", "optical instruments", "precision optics", "imaging"]),
            ("Specialty Materials / 합금", 0.95,
             ["specialty materials", "specialty chemicals", "advanced materials", "beryllium",
              "graphite", "ceramics", "composites"]),
            ("RF / GaN / mmWave 컴포넌트", 0.90,
             ["rf", "millimeter wave", "antenna", "gallium nitride", "amplifier"]),
            ("Radiation-hardened Electronics", 0.95,
             ["radiation hardened", "rad hard", "space-grade"]),
            ("Solar / Power System", 0.80,
             ["solar", "photovoltaic", "power conversion", "battery"]),
            ("Coatings / 접착 / 실란트", 0.85,
             ["coating", "adhesive", "sealant", "specialty chemicals"]),
        ],
    ),
    # ── EV / Battery ─────────────────────────────────────────────────────
    (
        "전기차 / 배터리 밸류체인", 0.75,
        [
            ("Power Semiconductor / SiC / GaN", 0.95,
             ["power semiconductor", "silicon carbide", "gallium nitride", "wide bandgap"]),
            ("Battery Materials / Cathode / Anode", 0.85,
             ["lithium", "cathode", "anode", "electrolyte", "separator", "specialty chemicals"]),
            ("Thermal Management / Connector", 0.80,
             ["thermal", "connector", "busbar", "cable", "wire harness"]),
            ("Charging Infrastructure", 0.75,
             ["charging", "charger", "ev infrastructure"]),
            ("BMS / Power Electronics", 0.80,
             ["battery management", "inverter", "power electronics"]),
        ],
    ),
    # ── Healthcare / Bio ─────────────────────────────────────────────────
    (
        "헬스케어 / 바이오 인프라", 0.80,
        [
            ("CDMO / Bioprocessing 소모품", 0.95,
             ["cdmo", "bioprocessing", "single-use", "fill-finish", "sterile"]),
            ("Diagnostic Reagents / Cold Chain", 0.85,
             ["diagnostic", "reagent", "cold chain", "specialty chemicals"]),
            ("Lab Automation / Imaging", 0.80,
             ["lab automation", "imaging", "diagnostics & research"]),
            ("Medical Devices 정밀 부품", 0.80,
             ["medical devices", "surgical instruments", "precision instruments"]),
        ],
    ),
    # ── Defense ─────────────────────────────────────────────────────────
    (
        "방산 / Defense 핵심 부품", 0.85,
        [
            ("Sensors / Seekers / EW", 0.95,
             ["sensor", "electronic warfare", "seeker", "guidance"]),
            ("Secure Communication / Optics", 0.90,
             ["secure communication", "tactical communication", "optics"]),
            ("Propulsion / Energetics", 0.85,
             ["propulsion", "rocket motor", "energetics", "specialty chemicals"]),
            ("Defense Electronics", 0.85,
             ["defense electronics", "aerospace & defense", "communication equipment"]),
        ],
    ),
    # ── Robotics / Automation ───────────────────────────────────────────
    (
        "로보틱스 / 자동화 핵심 부품", 0.80,
        [
            ("Servo Motors / Reducers / Encoders", 0.90,
             ["motor", "actuator", "reducer", "encoder", "harmonic"]),
            ("Vision / LiDAR Sensors", 0.85,
             ["lidar", "vision sensor", "machine vision"]),
            ("Industrial Automation Software", 0.75,
             ["industrial automation", "automation software"]),
            ("Specialty Industrial Components", 0.80,
             ["specialty industrial", "factory automation"]),
        ],
    ),
    # ── Grid / Power ────────────────────────────────────────────────────
    (
        "전력망 / Grid 인프라", 0.90,
        [
            ("Transformer / Switchgear", 0.95,
             ["transformer", "switchgear", "substation", "electrical equipment"]),
            ("HVDC / Grid Automation", 0.90,
             ["hvdc", "grid", "transmission", "power management"]),
            ("Power Protection / Monitoring", 0.85,
             ["power protection", "grid monitoring", "specialty industrial"]),
            ("Copper / Aluminum / Cable", 0.75,
             ["copper", "aluminum", "cable", "wire"]),
        ],
    ),
]


# ---------------------------------------------------------------------------
# 식별 — 회사 메타에서 매칭되는 (theme, value_chain_position, criticality, tailwind)
# ---------------------------------------------------------------------------

def identify_bottleneck_themes(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """wide_universe meta (sector / industry / name) 에서 bottleneck 테마 / 위치 매칭.

    Returns: [{theme, position, criticality, tailwind, matched_keywords}, ...]
    같은 종목이 여러 테마에 걸리면 모두 반환 — caller 가 가장 강한 매칭만 사용.
    """
    text = " ".join([
        (meta.get("sector") or "").lower(),
        (meta.get("industry") or "").lower(),
        (meta.get("name") or "").lower(),
    ])
    if not text.strip():
        return []

    matches: list[dict[str, Any]] = []
    for theme, tailwind, positions in BOTTLENECK_THEMES:
        for position, criticality, keywords in positions:
            hits = [k for k in keywords if k in text]
            if hits:
                matches.append({
                    "theme": theme,
                    "position": position,
                    "criticality": criticality,
                    "tailwind": tailwind,
                    "matched_keywords": hits,
                })
    return matches


def classify_value_chain_position(meta: dict[str, Any]) -> dict[str, Any] | None:
    """가장 강한 매칭 1개 반환. 없으면 None."""
    matches = identify_bottleneck_themes(meta)
    if not matches:
        return None
    # 가장 높은 criticality + 매칭 키워드 수 우선
    matches.sort(
        key=lambda m: (m["criticality"], len(m["matched_keywords"])),
        reverse=True,
    )
    return matches[0]


# ---------------------------------------------------------------------------
# 8 요소 Bottleneck Alpha Score
# ---------------------------------------------------------------------------

WEIGHTS = {
    "industry_tailwind": 0.15,
    "bottleneck_criticality": 0.20,
    "supplier_concentration": 0.15,
    "customer_diversification": 0.10,
    "switching_cost": 0.15,
    "pricing_power": 0.10,
    "earnings_quality": 0.10,
    "valuation_reset": 0.05,
}


def _score_supplier_concentration(market_cap: float | None) -> float:
    """공급자 과점 정도 추정 (heuristic).

    소수 공급자 시장은 대형주 비중 높음 — market_cap 기반 proxy.
    완벽하지 않으나 NO_LLM_MODE 에서 사용 가능한 신호.
    """
    if market_cap is None:
        return 50.0
    if market_cap >= 50e9:
        return 70.0  # 대형 = 카테고리 리더 가능성
    if market_cap >= 10e9:
        return 60.0
    if market_cap >= 2e9:
        return 50.0
    return 40.0


def _score_customer_diversification(market_cap: float | None) -> float:
    """고객 분산도 — large cap 일수록 다고객 분산 가정 (heuristic)."""
    if market_cap is None:
        return 50.0
    if market_cap >= 30e9:
        return 65.0
    if market_cap >= 5e9:
        return 55.0
    return 45.0


def _score_switching_cost(position: str) -> float:
    """Value chain position 별 switching cost / qualification barrier base 점수."""
    high = (
        "Specialty Materials", "Radiation-hardened", "Sensors / Seekers",
        "Secure Communication", "정밀 광학", "Battery Materials",
        "CDMO / Bioprocessing", "HBM / Memory",
    )
    medium = (
        "Optical Interconnect", "Power Semiconductor", "Servo Motors",
        "Transformer / Switchgear", "Defense Electronics",
    )
    for h in high:
        if h in position:
            return 80.0
    for m in medium:
        if m in position:
            return 65.0
    return 50.0


def _score_pricing_power(md: dict[str, Any]) -> float:
    """Gross / operating margin 기반 가격 결정력 추정."""
    gm = safe_float(md.get("gross_margin"))
    om = safe_float(md.get("operating_margin"))
    score = 50.0
    if gm is not None:
        if gm > 0.50:
            score += 25
        elif gm > 0.35:
            score += 15
        elif gm > 0.20:
            score += 5
        else:
            score -= 10
    if om is not None:
        if om > 0.25:
            score += 15
        elif om > 0.15:
            score += 5
        elif om < 0:
            score -= 15
    return max(0.0, min(100.0, score))


def _score_earnings_quality(md: dict[str, Any]) -> float:
    """반복매출 / 마진 안정성 / FCF 전환 등 정량 proxy."""
    score = 50.0
    om = safe_float(md.get("operating_margin"))
    rg = safe_float(md.get("revenue_growth") or md.get("revenue_growth_yoy"))
    fcf = safe_float(md.get("fcf_yield"))
    roe = safe_float(md.get("roe"))
    if om is not None and om > 0.15:
        score += 12
    if rg is not None and rg > 0.05:
        score += 10
    if fcf is not None and fcf > 0.03:
        score += 12
    if roe is not None and roe > 0.10:
        score += 10
    if om is not None and om < 0:
        score -= 20
    return max(0.0, min(100.0, score))


def _score_valuation_reset(md: dict[str, Any]) -> float:
    """주가 조정 정도 — 이미 급등했으면 감점."""
    dd = safe_float(md.get("drawdown_from_52w_high"))  # 음수
    if dd is None:
        return 50.0
    abs_dd = abs(dd)
    # 0~15% 조정 → 이미 급등 의심 (낮은 점수)
    # 15~40% 조정 → 좋은 매수 영역 (높은 점수)
    # 40%+ 조정 → 펀더 훼손 의심 (점수 하락)
    if abs_dd < 0.05:
        return 30.0
    if abs_dd < 0.15:
        return 50.0
    if abs_dd <= 0.40:
        return 80.0
    return 50.0


def score_bottleneck_candidate(
    meta: dict[str, Any],
    md: dict[str, Any],
    match: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """단일 종목 Bottleneck Alpha Score 계산.

    Returns: {
        "score": float 0~100,
        "theme": str, "position": str,
        "factor_scores": {factor: 0~100, ...},
        "summary": str,
    } 또는 매칭 없으면 None.
    """
    match = match or classify_value_chain_position(meta)
    if not match:
        return None

    market_cap = safe_float(md.get("market_cap"))

    factor_scores = {
        "industry_tailwind": match["tailwind"] * 100,
        "bottleneck_criticality": match["criticality"] * 100,
        "supplier_concentration": _score_supplier_concentration(market_cap),
        "customer_diversification": _score_customer_diversification(market_cap),
        "switching_cost": _score_switching_cost(match["position"]),
        "pricing_power": _score_pricing_power(md),
        "earnings_quality": _score_earnings_quality(md),
        "valuation_reset": _score_valuation_reset(md),
    }

    total = sum(factor_scores[k] * w for k, w in WEIGHTS.items())
    total = max(0.0, min(100.0, total))

    summary = (
        f"{match['theme']} 밸류체인의 \"{match['position']}\" 위치 — "
        f"산업 성장 시 최종 승자와 무관하게 수요가 발생할 수 있는 병목 공급자 후보입니다."
    )

    return {
        "score": total,
        "theme": match["theme"],
        "position": match["position"],
        "matched_keywords": match.get("matched_keywords", []),
        "factor_scores": factor_scores,
        "summary": summary,
        "tailwind": match["tailwind"],
        "criticality": match["criticality"],
    }


# ---------------------------------------------------------------------------
# 리스크 플래그 — sector / market_cap / dd 기반 (NO_LLM 가능)
# ---------------------------------------------------------------------------

def evaluate_bottleneck_risks(
    meta: dict[str, Any],
    md: dict[str, Any],
    score_result: dict[str, Any],
) -> list[str]:
    """7 가지 리스크 플래그 (Commodity / Customer Concentration / Theme Dilution /
    Already Priced-in / Technology Substitution / Capital Intensity / Margin Pass-through)."""
    flags: list[str] = []
    md = md or {}

    # Already Priced-in — 1년 +50% 이상
    r1y = safe_float(md.get("1y_return") or md.get("return_1y"))
    if r1y is not None and r1y > 0.50:
        flags.append("Already Priced-in: 최근 1년 +50% 이상 상승 — 병목성 multiple 이미 반영 가능성")

    # Theme Dilution — 대형주 ($100B+) 면 해당 테마가 전체 매출 비중에서 작을 가능성
    mcap = safe_float(md.get("market_cap"))
    if mcap is not None and mcap > 100e9:
        flags.append("Theme Dilution: 대형주 — 해당 병목 사업부 매출 비중 / 영향력 확인 필요")

    # Capital Intensity — capex 정보 부족 시 일반 경고
    om = safe_float(md.get("operating_margin"))
    if om is not None and om < 0.05:
        flags.append("Capital Intensity / Margin: 영업이익률 5% 미만 — 자본집약도 / 마진 전가력 점검 필요")

    # Customer Concentration — 미들캡 + low diversification heuristic
    if mcap is not None and mcap < 5e9:
        flags.append("Customer Concentration: 중소형주 — 상위 고객 매출 비중 데이터 확인 필요")

    # Commodity Risk — 매칭 키워드에 commodity 적인 키워드 (copper / aluminum / lithium) 만 있으면
    matched = score_result.get("matched_keywords") or []
    commodity_words = {"copper", "aluminum", "lithium", "cable", "wire"}
    if matched and set(matched).issubset(commodity_words):
        flags.append("Commodity Risk: 범용 소재 키워드 위주 — 진정한 병목 vs commodity 구분 필요")

    # Technology Substitution — optical 키워드 + 매칭이 구리 기반이면
    haystack = " ".join([
        (meta.get("industry") or "").lower(),
        (meta.get("name") or "").lower(),
    ])
    if any(k in haystack for k in ("copper", "wire")) and "optical" in str(matched).lower():
        flags.append("Technology Substitution: 광 / 구리 전환 사이클 — 병목 자체가 사라질 가능성")

    if not flags:
        flags.append("정량 기준 특이 risk 신호는 약한 단계 — 큐레이션 추가 점검 권장")

    return flags


# ---------------------------------------------------------------------------
# Discovery 큐 통합 진입점 — discovery._QUEUE_SCORERS 에 등록되는 시그너처
# ---------------------------------------------------------------------------

def score_for_discovery_queue(meta: dict[str, Any], md: dict[str, Any]) -> dict | None:
    """discovery.run_discovery 의 _QUEUE_SCORERS 등록용 — 다른 큐와 동일한 시그너처."""
    if not md or not md.get("available"):
        return None
    result = score_bottleneck_candidate(meta, md)
    if not result:
        return None
    score = result["score"]
    # 60 점 미만은 discovery 큐에서 제외 (signal-to-noise 확보)
    if score < 55:
        return None
    return {
        "score": score,
        "summary": result["summary"],
        "metrics": {
            "theme": result["theme"],
            "position": result["position"],
            "factor_scores": result["factor_scores"],
            "tailwind": result["tailwind"],
            "criticality": result["criticality"],
            "matched_keywords": result["matched_keywords"],
        },
    }


# ---------------------------------------------------------------------------
# 종목 상세 — Bottleneck Thesis 섹션 데이터
# ---------------------------------------------------------------------------

def build_bottleneck_thesis(ticker: str, meta: dict[str, Any], md: dict[str, Any]) -> dict[str, Any] | None:
    """종목 상세에 표시할 Bottleneck Thesis 데이터.

    매칭 없으면 None — 종목 상세에서 섹션 자체 미표시.
    """
    match = classify_value_chain_position(meta)
    if not match:
        return None
    score_result = score_bottleneck_candidate(meta, md, match)
    if not score_result:
        return None

    risks = evaluate_bottleneck_risks(meta, md, score_result)

    fs = score_result["factor_scores"]
    fs_label = lambda v: (
        "Strong" if v >= 70 else "Medium" if v >= 50 else "Weak"
    )

    return {
        "target_industry": match["theme"],
        "value_chain_position": match["position"],
        "bottleneck_description": (
            f"{match['theme']} 밸류체인 내 \"{match['position']}\" 위치에 해당하는 후보로, "
            "최종 승자(OEM / 플랫폼) 와 무관하게 산업 성장 시 수요가 증가할 수 있는 병목 공급자 영역입니다."
        ),
        "why_it_matters": (
            f"산업 성장 시 모든 플레이어가 공통으로 필요로 하는 인프라 / 부품 / 소재 / 공정 영역이며, "
            "최종 제품 승자 예측보다 상대적으로 안정적인 매출 가시성을 기대할 수 있습니다."
        ),
        "who_benefits": (
            f"{match['theme']} 의 capex 사이클 또는 채택 가속 단계에서 본 영역의 수요가 함께 확대되며, "
            "특정 OEM 매출 의존도보다 다고객 노출이 가능합니다."
        ),
        "alpha_judgment": (
            f"Bottleneck Alpha Score: {int(score_result['score'])} / 100. "
            f"산업 tailwind {fs_label(fs['industry_tailwind'])}, 병목 criticality "
            f"{fs_label(fs['bottleneck_criticality'])}, switching cost / 인증 장벽 "
            f"{fs_label(fs['switching_cost'])} 수준의 후보입니다. "
            "고객 집중도 / 사업부 매출 비중 / commodity risk 점검이 후속 리서치 우선순위입니다."
        ),
        "key_risk": " / ".join(risks[:3]),
        "all_risks": risks,
        "factor_scores": fs,
        "score": int(score_result["score"]),
        "matched_keywords": score_result["matched_keywords"],
    }
