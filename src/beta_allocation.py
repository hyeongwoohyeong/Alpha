"""포트폴리오 모드 / 베타 배분 — Portfolio Regime 시스템 (Phase 1).

regime → portfolio mode 매핑은 전부 rule-based. LLM 불필요.
"""
from __future__ import annotations

from typing import Any

from .market_regime import (
    REGIME_CORRECTION_WATCH,
    REGIME_CRISIS,
    REGIME_DISLOCATION,
    REGIME_EXPENSIVE_STABLE,
    REGIME_OVERHEATED,
    REGIME_RISK_ON,
)
from .utils import get_logger

log = get_logger("beta_allocation")

# 6 포트폴리오 모드
MODE_AGGRESSIVE = "Aggressive Deployment"
MODE_SELECTIVE = "Selective Risk-On"
MODE_QUALITY_PARKING = "Quality Parking"
MODE_CASH_OPTIONALITY = "Cash Optionality"
MODE_PROFIT_PROTECTION = "Profit Protection"
MODE_CRISIS_DEFENSE = "Crisis Defense"

MODE_KO: dict[str, str] = {
    MODE_AGGRESSIVE: "공격적 투입 (Aggressive Deployment)",
    MODE_SELECTIVE: "선별적 위험선호 (Selective Risk-On)",
    MODE_QUALITY_PARKING: "우량주 파킹 (Quality Parking)",
    MODE_CASH_OPTIONALITY: "현금 옵션 확보 (Cash Optionality)",
    MODE_PROFIT_PROTECTION: "차익 보호 (Profit Protection)",
    MODE_CRISIS_DEFENSE: "위기 방어 (Crisis Defense)",
}

# regime → mode 매핑
_REGIME_TO_MODE: dict[str, str] = {
    REGIME_RISK_ON: MODE_SELECTIVE,
    REGIME_EXPENSIVE_STABLE: MODE_QUALITY_PARKING,
    REGIME_OVERHEATED: MODE_PROFIT_PROTECTION,
    REGIME_CORRECTION_WATCH: MODE_CASH_OPTIONALITY,
    REGIME_DISLOCATION: MODE_AGGRESSIVE,
    REGIME_CRISIS: MODE_CRISIS_DEFENSE,
}

# mode 별 권장 파라미터
_MODE_PARAMS: dict[str, dict[str, str]] = {
    MODE_AGGRESSIVE: {
        "recommended_beta_level": "High",
        "recommended_cash_level": "낮음 (10~20%)",
        "recommended_equity_type": "QQQ 중심 + QLD 분할, 낙폭 깊으면 TQQQ 소액",
    },
    MODE_SELECTIVE: {
        "recommended_beta_level": "Moderate",
        "recommended_cash_level": "보통 (15~25%)",
        "recommended_equity_type": "QQQ + 선별 개별주, 레버리지는 제한적",
    },
    MODE_QUALITY_PARKING: {
        "recommended_beta_level": "Moderate",
        "recommended_cash_level": "보통 (20~30%)",
        "recommended_equity_type": "우량주(quality) 중심 파킹, 신규 베타 확대 자제",
    },
    MODE_CASH_OPTIONALITY: {
        "recommended_beta_level": "Moderate-Low",
        "recommended_cash_level": "높음 (30~45%)",
        "recommended_equity_type": "현금 비중 확대, QQQ 신규매수 보류·관망",
    },
    MODE_PROFIT_PROTECTION: {
        "recommended_beta_level": "Moderate-Low",
        "recommended_cash_level": "높음 (30~45%)",
        "recommended_equity_type": "차익 실현·현금화, 레버리지 ETF 축소",
    },
    MODE_CRISIS_DEFENSE: {
        "recommended_beta_level": "Low",
        "recommended_cash_level": "매우 높음 (45~70%)",
        "recommended_equity_type": "현금·방어. 신용 안정 확인 전 신규 투입 보류",
    },
}

_MODE_COMMENTARY_KO: dict[str, str] = {
    MODE_AGGRESSIVE: (
        "디스로케이션 구간 — 신용시장이 정상인 가운데 지수가 하락한 상태입니다. "
        "역사적으로 분할 매수에 유리한 국면으로, 베타를 적극적으로 늘릴 수 있습니다. "
        "단, 한 번에 전부 투입하지 말고 낙폭 단계별로 나눠 들어가십시오."
    ),
    MODE_SELECTIVE: (
        "위험선호 국면이지만 무차별 매수보다 선별적 접근이 유리합니다. "
        "QQQ 지수와 thesis 가 견고한 개별주 중심으로, 레버리지는 제한적으로 사용하십시오."
    ),
    MODE_QUALITY_PARKING: (
        "밸류에이션이 다소 높아 신규 베타 확대는 신중해야 합니다. "
        "보유 자금은 우량주에 파킹하되, 추격 매수보다 조정 시 추가 매수 여력을 남겨두십시오."
    ),
    MODE_CASH_OPTIONALITY: (
        "조정 경계 국면 — 시장 폭이 약화되고 있습니다. 현금 비중을 늘려 "
        "다음 기회에 대한 옵션을 확보하십시오. 지수 신규매수는 보류가 안전합니다."
    ),
    MODE_PROFIT_PROTECTION: (
        "과열 국면 — 신규 베타 확대보다 그동안의 차익을 보호하는 것이 우선입니다. "
        "레버리지 ETF 비중을 줄이고 현금화를 통해 다음 국면을 대비하십시오."
    ),
    MODE_CRISIS_DEFENSE: (
        "위기 국면 — 신용 스트레스 또는 급락이 확인됩니다. 방어가 최우선입니다. "
        "신규 투입은 신용시장 안정이 확인된 뒤에 단계적으로 시작하십시오."
    ),
}


def classify_portfolio_mode(regime: str, overheat: float | None) -> dict[str, Any]:
    """regime + overheat → 포트폴리오 모드 + 권장 파라미터.

    Returns dict: portfolio_mode, portfolio_mode_ko, recommended_beta_level,
    recommended_cash_level, recommended_equity_type, commentary_ko,
    index_buy_ok (지수 신규매수 가부), leverage_ok (레버리지 가부).
    """
    mode = _REGIME_TO_MODE.get(regime, MODE_SELECTIVE)

    # overheat 가 매우 높으면 Selective/Quality 라도 한 단계 보수화
    if overheat is not None and overheat >= 85 and mode in (MODE_SELECTIVE, MODE_QUALITY_PARKING):
        mode = MODE_PROFIT_PROTECTION

    params = _MODE_PARAMS.get(mode, _MODE_PARAMS[MODE_SELECTIVE])

    index_buy_ok = mode in (MODE_AGGRESSIVE, MODE_SELECTIVE, MODE_QUALITY_PARKING)
    leverage_ok = mode in (MODE_AGGRESSIVE, MODE_SELECTIVE)

    return {
        "portfolio_mode": mode,
        "portfolio_mode_ko": MODE_KO.get(mode, mode),
        "recommended_beta_level": params["recommended_beta_level"],
        "recommended_cash_level": params["recommended_cash_level"],
        "recommended_equity_type": params["recommended_equity_type"],
        "commentary_ko": _MODE_COMMENTARY_KO.get(mode, ""),
        "index_buy_ok": index_buy_ok,
        "leverage_ok": leverage_ok,
    }
