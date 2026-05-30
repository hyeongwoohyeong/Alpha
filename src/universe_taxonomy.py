"""Universe 카테고리 분류 schema.

사용자 투자 아이디어를 8 카테고리로 정리. 각 종목은 1개 이상의 카테고리에 속할 수 있음
(예: TSLA = M7 + Robotics_EV_Space_Defense, NVDA = M7 + AI_Infra_Semi).

설계 원칙:
- Pure data — scoring 로직 X (Phase 2 에서 추가)
- 카테고리 마다 "어떤 시장 국면에서 어떤 역할" 명시 (단순 종목 리스트 X)
- 사용자가 Robotics Bottleneck / AI Infra Bottleneck 등을 강조 → category 메타에 role 적시
- KR 종목은 별도 카테고리 (Korea Discount Risk 함께 적용)

다른 모듈에서:
- `get_categories_for(ticker)` → 해당 종목 카테고리 리스트
- `iter_category(name)` → 해당 카테고리의 모든 종목
- `regime_role(category, regime)` → 해당 국면에서 이 카테고리가 갖는 역할
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("universe_taxonomy")


# ---------------------------------------------------------------------------
# 카테고리 정의 (8개)
# ---------------------------------------------------------------------------

UNIVERSE_TAXONOMY: dict[str, dict[str, Any]] = {
    "M7_Core_Megacap": {
        "label": "M7 / 대형 플랫폼",
        "role": "시장 risk appetite + AI/platform leadership 의 core proxy. 2X ETF 가능 종목 다수",
        "tickers_us": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX"],
        "tickers_kr": [],
        "scoring_modules": [
            "alpha_score", "capital_efficiency", "qld_relative_view",
            "leveraged_etf_suitability", "profit_protection", "market_regime_fit",
        ],
        "regime_roles": {
            "Risk-On": "공격 보유 + selective 2X",
            "Expensive but Stable": "core 보유 유지, 신규 2X 제한",
            "Overheated": "2X 금지, profit protection 우선",
            "Correction Watch": "watchlist, 아직 공격 X",
            "Dislocation": "고확신 종목 2X 진입 검토",
            "Crisis": "현금/QQQ/SPY 중심, 2X 금지",
        },
    },

    "Index_Leverage_Regime_Tools": {
        "label": "지수 / 레버리지 / Portfolio Regime 도구",
        "role": "Portfolio Regime 핵심 도구 — drawdown deployment + parking + hedge",
        "tickers_us": [
            "SPY", "QQQ", "SSO", "UPRO", "QLD", "TQQQ", "SOXL", "SCHG", "MAGS",
            "DDM", "UDOW", "TLT", "TMF", "GLD", "SLV", "SCHD", "DGRO",
            "JEPI", "JEPQ", "XLV", "VHT",
        ],
        "tickers_kr": [],
        "scoring_modules": [
            "portfolio_regime_engine", "crash_deployment", "market_cycle_lab",
            "parking_strategy", "drawdown_deployment_backtest",
        ],
        "sub_roles": {
            "benchmark": ["SPY", "QQQ"],
            "leverage_attack": ["QLD", "TQQQ", "SSO", "UPRO", "SOXL", "DDM", "UDOW"],
            "defensive_parking": ["SCHD", "DGRO", "JEPI", "JEPQ", "XLV", "VHT"],
            "hedge_alt": ["GLD", "SLV", "TLT", "TMF"],
            "growth_thematic": ["SCHG", "MAGS"],
        },
    },

    "AI_Infra_Semi_Bottleneck": {
        "label": "AI 데이터센터 / 전력 / Connectivity / Server Infra",
        "role": "AI capex 사이클의 bottleneck supplier — GPU 외 connectivity·power·cooling·optical·server·materials",
        "tickers_us": [
            "CRDO", "ALAB", "COHR", "CLS", "DELL", "ETN", "CEG", "NVT",
            "TEL", "ON", "STM", "ACMR", "ROG", "MTRN", "APH", "VRT",
            "LITE", "MRVL", "MU", "AVGO", "TSM", "MPWR", "NXPI", "LRCX", "MCHP",
            "ASML", "AMD", "NVDA", "ANET", "VST", "CCJ", "SMCI",
        ],
        "tickers_kr": ["005930.KS", "000660.KS"],
        "scoring_modules": [
            "alpha_score", "ai_infra_bottleneck_score", "capital_efficiency",
            "qld_relative_view", "leveraged_etf_suitability", "profit_protection",
        ],
        "bottleneck_layers": {
            "connectivity": ["CRDO", "ALAB", "ANET"],
            "optical_photonics": ["COHR", "LITE"],
            "server_ems": ["CLS", "DELL", "SMCI"],
            "power_grid": ["ETN", "CEG", "NVT", "VRT", "VST", "CCJ"],
            "connector_sensor": ["TEL", "APH"],
            "power_semi": ["ON", "STM", "MPWR", "NXPI"],
            "materials": ["MTRN", "ROG"],
            "memory_hbm": ["MU", "000660.KS"],
            "foundry": ["TSM"],
            "broadcom": ["AVGO", "MRVL"],
            "semicap_equipment": ["ASML", "LRCX", "ACMR", "MCHP"],
            "gpu_compute": ["NVDA", "AMD"],
        },
    },

    "Robotics_Bottleneck": {
        "label": "로봇 / 자동화 / 로봇 병목 부품",
        "role": "로봇 완성 1등 보다 'Tesla/Hyundai/Samsung/Figure/Agility 모두 공급 가능한가' 의 cross-OEM 병목",
        "tickers_us": [
            "TSLA", "TEL", "APH", "HON", "ST", "ADI", "ALGM", "TXN", "ON",
            "STM", "NXPI", "MPWR", "CGNX", "ZBRA", "TER", "KEYS", "FTV",
            "AME", "ROK",
        ],
        "tickers_kr": [
            "005930.KS", "005380.KS", "277810.KQ", "454910.KS", "090360.KQ",
            "108490.KQ", "012330.KS", "058610.KQ", "389500.KQ", "059270.KQ",
            "160190.KQ", "098460.KQ", "100120.KQ", "064290.KQ",
        ],
        "scoring_modules": [
            "alpha_score", "robotics_bottleneck_score", "every_robot_content_score",
            "mission_criticality", "supplier_scarcity", "cross_oem_exposure",
        ],
        "bottleneck_layers": {
            "platform": ["TSLA", "005930.KS", "005380.KS"],
            "actuator_reducer": [
                "012330.KS", "058610.KQ", "389500.KQ", "059270.KQ",
                "160190.KQ", "108490.KQ", "277810.KQ",
            ],
            "sensor_connector_control": [
                "TEL", "APH", "HON", "ST", "ADI", "ALGM", "TXN", "ON",
                "STM", "NXPI", "MPWR",
            ],
            "machine_vision_inspection": [
                "CGNX", "ZBRA", "TER", "KEYS", "FTV", "AME", "ROK",
                "098460.KQ", "100120.KQ", "064290.KQ",
            ],
        },
    },

    "Civilization_Alpha_DefenseSpace": {
        "label": "우주 / 방산 / 항공 / 원전 / Civilization Alpha",
        "role": "완성 우주/로켓 보다 공급망 병목 — 항공/방산 정밀 베어링·복합소재·특수소재·원전·공공안전 OS",
        "tickers_us": ["KBR", "RTX", "BWXT", "FTAI", "RBC", "HXL", "MTRN", "CEG", "NASA", "AXON"],
        "tickers_kr": [],
        "scoring_modules": [
            "alpha_score", "civilization_alpha_score", "defense_aerospace_bottleneck_score",
            "mission_critical_supplier_score", "quality_compounder_score",
            "capital_efficiency", "valuation_reset_watch",
        ],
        "sub_roles": {
            "aerospace_precision": ["RBC", "HXL", "MTRN", "FTAI"],
            "defense_prime": ["RTX", "KBR"],
            "nuclear_energy": ["BWXT", "CEG"],
            "public_safety_os": ["AXON"],
            "space_thematic_etf": ["NASA"],
        },
    },

    "Healthcare_Defensive_Bio": {
        "label": "헬스케어 / 바이오 / 생명과학",
        "role": "Defensive quality + life science tools 중심. Event-driven bio 는 별도 risk 분류",
        "tickers_us": ["DHR", "BNTX", "NVAX", "XLV", "VHT"],
        "tickers_kr": ["214450.KQ"],
        "scoring_modules": [
            "alpha_score", "defensive_quality_score", "event_risk_score",
            "parking_suitability", "capital_efficiency", "catalyst_visibility",
        ],
        "sub_roles": {
            "life_science_tools": ["DHR"],
            "broad_healthcare_etf": ["XLV", "VHT"],
            "event_bio": ["BNTX", "NVAX"],
            "medical_aesthetics": ["214450.KQ"],
        },
    },

    "Quality_Compounder_VerticalSoftware": {
        "label": "금융 데이터 / 버티컬 소프트웨어 / Quality Compounder",
        "role": "Recurring revenue + pricing power + switching cost. 장기 compounder / Quality Parking",
        "tickers_us": ["SPGI", "ROP", "AXON", "ZBRA", "DHR"],
        "tickers_kr": [],
        "scoring_modules": [
            "alpha_score", "earnings_quality", "moat_lockin_score",
            "recurring_revenue_score", "pricing_power_score",
            "capital_efficiency", "parking_suitability", "quality_dislocation",
        ],
    },

    "Valuation_Watchlist": {
        "label": "Valuation 분석 종목 (주기 트래킹)",
        "role": "사용자가 Valuation 탭에 등재한 deep-dive 종목 — 알파 엔진이 주기적으로 가격·뉴스·재무 추적. 각 종목은 별도 IC Memo + Pro-forma 모델 보유",
        "tickers_us": [],
        "tickers_kr": ["000720.KS", "214450.KQ", "207940.KS"],
        "scoring_modules": [
            "alpha_score", "capital_efficiency", "qld_relative_view",
            "profit_protection", "market_regime_fit",
        ],
        "note": "이 카테고리에 등재된 종목은 data/valuations/{회사명}.json 의 deep-dive 분석과 연동. 분기별 재무 갱신 시 valuation_data 도 함께 업데이트.",
    },

    "Korea_Thematic": {
        "label": "한국 테마 / 국내 관심종목",
        "role": "구조적 디스카운트 + 지배구조 risk + 국가 risk 반영. HBM/반도체·로봇·전력·조선·K-food·의료미용 별도 alpha 후보",
        "tickers_us": [],
        "tickers_kr": [
            "005930.KS", "000660.KS", "005380.KS", "012330.KS",
            "277810.KQ", "454910.KS", "058610.KQ", "389500.KQ",
            "059270.KQ", "160190.KQ", "098460.KQ", "100120.KQ", "064290.KQ",
            "006260.KS", "010120.KS", "373220.KS", "006400.KS",
            "051910.KS", "003230.KS", "214450.KQ",
        ],
        "thematic_etfs": [
            "TIGER 반도체TOP10", "KODEX AI전력핵심장비",
            "SOL 조선TOP3플러스", "KoAct 바이오헬스케어액티브",
        ],
        "scoring_modules": [
            "alpha_score", "korea_discount_risk", "hbm_structural_shift_score",
            "korea_thematic_etf_watch", "capital_efficiency",
            "qld_relative_view", "event_policy_risk",
        ],
    },
}


# ---------------------------------------------------------------------------
# 조회 함수
# ---------------------------------------------------------------------------

def get_categories_for(ticker: str) -> list[str]:
    """주어진 ticker 가 속한 카테고리 리스트.

    종목은 여러 카테고리에 속할 수 있음 (예: TSLA = M7 + Robotics).
    """
    if not ticker:
        return []
    t = ticker.strip().upper()
    cats = []
    for cat_name, cat_data in UNIVERSE_TAXONOMY.items():
        all_tickers = (cat_data.get("tickers_us") or []) + (cat_data.get("tickers_kr") or [])
        if t in [a.upper() for a in all_tickers]:
            cats.append(cat_name)
    return cats


def iter_category(category_name: str) -> list[str]:
    """해당 카테고리의 모든 종목 (US + KR)."""
    cat = UNIVERSE_TAXONOMY.get(category_name)
    if not cat:
        return []
    return list((cat.get("tickers_us") or []) + (cat.get("tickers_kr") or []))


def regime_role(category_name: str, regime: str) -> str:
    """해당 국면에서 이 카테고리의 역할 (M7 만 정의됨, 다른 건 추후 확장)."""
    cat = UNIVERSE_TAXONOMY.get(category_name, {})
    return (cat.get("regime_roles") or {}).get(regime, "")


def get_bottleneck_layer(ticker: str, category_name: str) -> str | None:
    """해당 카테고리 내에서 ticker 의 bottleneck layer (예: AI_Infra 의 'connectivity')."""
    cat = UNIVERSE_TAXONOMY.get(category_name, {})
    layers = cat.get("bottleneck_layers", {})
    t = (ticker or "").strip().upper()
    for layer_name, tickers in layers.items():
        if t in [a.upper() for a in tickers]:
            return layer_name
    return None


def all_tickers() -> set[str]:
    """taxonomy 에 등장하는 모든 ticker (US + KR, 중복 제거)."""
    s: set[str] = set()
    for cat_data in UNIVERSE_TAXONOMY.values():
        for t in (cat_data.get("tickers_us") or []) + (cat_data.get("tickers_kr") or []):
            s.add(t.upper())
    return s


def has_leveraged_etf(ticker: str) -> bool:
    """이 ticker 에 대한 single-stock 2X ETF 가 있는가? (data/leveraged_etf_map.json 조회)."""
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "leveraged_etf_map.json"
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        t = (ticker or "").strip().upper()
        for cat, mapping in (raw.get("categories") or {}).items():
            if t in mapping:
                return True
    except Exception as e:
        log.debug("leveraged_etf_map 조회 실패: %s", e)
    return False


def get_leveraged_etf_tickers(ticker: str) -> list[str]:
    """이 ticker 의 모든 2X ETF ticker 리스트."""
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "leveraged_etf_map.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        t = (ticker or "").strip().upper()
        out: list[str] = []
        for cat, mapping in (raw.get("categories") or {}).items():
            if t in mapping:
                out.extend(mapping[t])
        return list(dict.fromkeys(out))  # 중복 제거 (TSLA 가 M7 + Robotics 양쪽)
    except Exception:
        return []
