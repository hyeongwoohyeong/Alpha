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

# regime → mode 매핑 (기본값 — verdict 가 비어 있을 때의 행동)
#
# 알파 추구가 1순위, 보존은 알파가 소진된 뒤의 default 다.
# 따라서 데이터 사다리 verdict 가 적극 진입을 가리킬 때는
# classify_portfolio_mode 내부에서 이 매핑을 한 단계 격상한다 (Aggressive).
# 반대로 overheat ≥ 85 면 한 단계 보수화한다 (Profit Protection).
_REGIME_TO_MODE: dict[str, str] = {
    REGIME_RISK_ON: MODE_SELECTIVE,
    REGIME_EXPENSIVE_STABLE: MODE_QUALITY_PARKING,
    REGIME_OVERHEATED: MODE_PROFIT_PROTECTION,
    REGIME_CORRECTION_WATCH: MODE_CASH_OPTIONALITY,
    REGIME_DISLOCATION: MODE_AGGRESSIVE,
    REGIME_CRISIS: MODE_CRISIS_DEFENSE,
}

# 데이터 사다리 verdict 중 '적극 진입' 신호 — Aggressive 격상 트리거
# (src/market_cycle_analyzer.py recommend_current_entry 의 verdict 문자열과 정확히 매칭)
_BOLD_ENTRY_VERDICTS: frozenset[str] = frozenset({
    "황금 진입 구간 — QQQ+QLD 동시 진입 데이터 우월",
    "TQQQ 진입 적기 (데이터상 정점)",
})

# '고점권' verdict — Quality Parking 유지(격상 금지)
_TOPPISH_VERDICT = "고점권 — 추격 매수 데이터적 가치 낮음"

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


def classify_portfolio_mode(
    regime: str,
    overheat: float | None,
    cycle_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """regime + overheat (+ 데이터 사다리 verdict) → 포트폴리오 모드 + 권장 파라미터.

    cycle_recommendation 은 src/market_cycle_analyzer.recommend_current_entry 결과 dict.
    verdict 가 적극 진입을 가리키면 Aggressive 로 격상 (알파 추구 우선 정책).
    verdict 가 비어 있거나 unavailable → 기존 동작 그대로 (변화 없음).

    Returns dict: portfolio_mode, portfolio_mode_ko, recommended_beta_level,
    recommended_cash_level, recommended_equity_type, commentary_ko,
    index_buy_ok (지수 신규매수 가부), leverage_ok (레버리지 가부),
    mode_upgrade_reason (verdict 격상 사유 — 격상 시에만 채움).
    """
    base_mode = _REGIME_TO_MODE.get(regime, MODE_SELECTIVE)
    mode = base_mode
    upgrade_reason: str | None = None
    extra_commentary: str | None = None

    # 1) 알파 시그널 격상 — verdict 가 적극 진입을 가리키면 Aggressive 로
    verdict: str | None = None
    if cycle_recommendation and isinstance(cycle_recommendation, dict):
        verdict = (cycle_recommendation.get("verdict") or "").strip() or None

    if verdict and verdict in _BOLD_ENTRY_VERDICTS \
            and regime in (REGIME_RISK_ON, REGIME_EXPENSIVE_STABLE):
        mode = MODE_AGGRESSIVE
        upgrade_reason = (
            f"데이터 사다리 verdict('{verdict}')가 적극 진입을 가리켜 "
            f"기본 모드({MODE_KO.get(base_mode, base_mode)})에서 "
            f"{MODE_KO[MODE_AGGRESSIVE]} 로 격상."
        )
        extra_commentary = (
            "※ 데이터 사다리 verdict 가 적극 진입을 가리켜 Aggressive 로 격상. "
            "regime 자체는 격상 전 상태였으나, 실증 분포가 알파 추구를 지지하므로 "
            "한 단계 공격적으로 운용 가능합니다 (분할 매수 원칙은 유지)."
        )
    elif verdict == _TOPPISH_VERDICT and regime == REGIME_EXPENSIVE_STABLE:
        # 고평가·안정 국면에서 verdict 가 고점권을 가리키면 Quality Parking 유지
        mode = base_mode

    # 2) overheat 보호 — 매우 높으면 한 단계 보수화 (격상 후에도 적용)
    #    Aggressive 격상 후라도 과열이 극단이면 Profit Protection 으로 끌어내림
    if overheat is not None and overheat >= 85:
        if mode in (MODE_SELECTIVE, MODE_QUALITY_PARKING, MODE_AGGRESSIVE):
            mode = MODE_PROFIT_PROTECTION
            # 격상 commentary 무효화 — 과열 보호가 우선
            extra_commentary = (
                f"※ Overheat Score {overheat:.0f} ≥ 85 — 데이터 사다리 verdict 보다 "
                "과열 보호를 우선해 Profit Protection 으로 보수화."
            )
            upgrade_reason = None

    params = _MODE_PARAMS.get(mode, _MODE_PARAMS[MODE_SELECTIVE])

    index_buy_ok = mode in (MODE_AGGRESSIVE, MODE_SELECTIVE, MODE_QUALITY_PARKING)
    leverage_ok = mode in (MODE_AGGRESSIVE, MODE_SELECTIVE)

    commentary = _MODE_COMMENTARY_KO.get(mode, "")
    if extra_commentary:
        commentary = (commentary + " " + extra_commentary).strip()

    out: dict[str, Any] = {
        "portfolio_mode": mode,
        "portfolio_mode_ko": MODE_KO.get(mode, mode),
        "recommended_beta_level": params["recommended_beta_level"],
        "recommended_cash_level": params["recommended_cash_level"],
        "recommended_equity_type": params["recommended_equity_type"],
        "commentary_ko": commentary,
        "index_buy_ok": index_buy_ok,
        "leverage_ok": leverage_ok,
    }
    if upgrade_reason:
        out["mode_upgrade_reason"] = upgrade_reason
    return out
