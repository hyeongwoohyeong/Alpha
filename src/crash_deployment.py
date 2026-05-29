"""Nasdaq 하락 단계별 투입 계획 — Portfolio Regime 시스템 (Phase 1).

QQQ 52주 고점 대비 낙폭에 따라 단계적 투입 계획을 rule-based 로 생성.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("crash_deployment")


def calculate_nasdaq_drawdown_from_high(qqq_hist: Any) -> float | None:
    """QQQ 가격이력(DataFrame)에서 52주 고점 대비 현재 낙폭(%, 음수) 계산.

    qqq_hist 가 없거나 비정상이면 None.
    """
    if qqq_hist is None:
        return None
    try:
        if "Close" not in qqq_hist.columns:
            return None
        closes = qqq_hist["Close"].dropna()
        if len(closes) < 2:
            return None
        # 최근 252 영업일 (52주) 범위 고점
        window = closes.tail(252)
        high = float(window.max())
        last = float(closes.iloc[-1])
        if high <= 0:
            return None
        return (last / high) - 1.0
    except Exception as e:
        log.warning("nasdaq drawdown 계산 실패: %s", e)
        return None


# 낙폭 구간별 투입 계획 — (임계 낙폭, zone, instrument, action)
_DEPLOYMENT_ZONES: list[dict[str, Any]] = [
    {
        "min_dd": 0.0,
        "zone": "정상 구간 (낙폭 -5% 미만)",
        "instrument": "관찰",
        "action": "신규 단계 투입 없음. 고점 대비 -5% 도달 시 1단계 준비.",
    },
    {
        "min_dd": -0.05,
        "zone": "1단계 (-5% ~ -10%)",
        "instrument": "QQQ",
        "action": "QQQ 1차 분할 매수 시작 (계획 자금의 약 20%).",
    },
    {
        "min_dd": -0.10,
        "zone": "2단계 (-10% ~ -15%)",
        "instrument": "QQQ + QLD",
        "action": "QQQ 추가 + QLD(2x) 분할 시작 (누적 약 40~50%).",
    },
    {
        "min_dd": -0.15,
        "zone": "3단계 (-15% ~ -20%)",
        "instrument": "QLD + TQQQ 소액",
        "action": "QLD 확대 + TQQQ(3x) 소액 진입 (누적 약 60~70%).",
    },
    {
        "min_dd": -0.20,
        "zone": "4단계 (-20% ~ -25%)",
        "instrument": "QLD + TQQQ",
        "action": "QLD/TQQQ 비중 확대 (누적 약 80%). 신용 안정 시 공격적 투입.",
    },
    {
        "min_dd": -0.25,
        "zone": "5단계 (-25% 이하)",
        "instrument": "TQQQ 공격",
        "action": "최대 공격 구간. 잔여 자금 투입 — 단, 신용 스트레스 확인 필수.",
    },
]


def _select_zone(drawdown: float | None) -> dict[str, Any]:
    if drawdown is None:
        return {
            "min_dd": None,
            "zone": "확인 필요 — QQQ 낙폭 데이터 없음",
            "instrument": "확인 필요",
            "action": "QQQ 가격 데이터 수집 후 재평가.",
        }
    chosen = _DEPLOYMENT_ZONES[0]
    for z in _DEPLOYMENT_ZONES:
        if drawdown <= z["min_dd"] + 1e-9:
            chosen = z
    return chosen


def generate_deployment_plan(drawdown: float | None,
                             credit_stress: str | None,
                             cycle_recommendation: dict | None = None) -> dict[str, Any]:
    """낙폭 + 신용 스트레스 + 실증 데이터 사다리 → 단계별 투입 계획.

    drawdown: QQQ 52주 고점 대비 낙폭 (음수, 예 -0.12). None 가능.
    credit_stress: 'normal'|'elevated'|'severe'|'unknown'
    cycle_recommendation: market_cycle_analyzer.recommend_current_entry 결과.
        있으면 권장 수단(recommended_instrument)을 실증 데이터의 best_asset 로
        대체하고 근거를 commentary 에 인용. None 이면 zone 의 하드코드 instrument.
    Returns dict: qqq_drawdown_from_high, deployment_zone, recommended_instrument,
    suggested_action, credit_stress_status, liquidity_status, required_checks,
    commentary_ko.
    """
    zone = _select_zone(drawdown)
    cs = credit_stress or "unknown"

    # ── 실증 데이터 사다리 우선 (없을 때만 zone 의 하드코드 instrument) ──
    rec_instrument: str | None = None
    rec_note: str = ""
    if isinstance(cycle_recommendation, dict) and cycle_recommendation.get("available"):
        ba = cycle_recommendation.get("best_asset")
        if ba and isinstance(ba, str):
            rec_instrument = ba
            ev = cycle_recommendation.get("evidence") or []
            best_ev = next((e for e in ev if e.get("asset") == ba), None)
            if best_ev:
                rec_note = (
                    f" (데이터 사다리: 현재 버킷 평균 {best_ev.get('avg', 0)*100:+.1f}%, "
                    f"적중률 {best_ev.get('win', 0)*100:.0f}%, 표본 {best_ev.get('n', 0)}건)"
                )

    required_checks: list[str] = [
        "QQQ 200일선 대비 위치 및 추세 재확인",
        "VIX 등 변동성 지표가 진정 국면인지 확인",
    ]

    # 신용 상태 → 투입 속도 조절
    if cs == "severe":
        liquidity_status = "신용 위기 — 투입 보류"
        speed_note = (
            "신용 스트레스가 심각합니다. 표의 단계 투입을 보류하고, "
            "신용시장(HY 스프레드)이 진정될 때까지 관망하십시오. "
            "낙폭이 깊더라도 '떨어지는 칼날'일 수 있습니다."
        )
        required_checks.insert(0, "HY 스프레드 추가 확대 여부 — 진정 전까지 투입 금지")
    elif cs == "elevated":
        liquidity_status = "신용 경계 — 투입 속도 축소"
        speed_note = (
            "신용 스트레스가 높아지고 있습니다. 각 단계 투입 금액을 평소의 "
            "절반 수준으로 줄이고, 단계 간 간격을 더 길게 두십시오."
        )
        required_checks.insert(0, "HY 스프레드 방향 — 안정 확인 후 정상 속도로 복귀")
    elif cs == "normal":
        liquidity_status = "신용 안정 — 계획대로 투입 가능"
        speed_note = "신용시장이 안정적입니다. 표의 단계별 계획대로 분할 투입할 수 있습니다."
    else:
        liquidity_status = "신용 상태 확인 필요"
        speed_note = (
            "신용 데이터를 수집하지 못했습니다. 보수적으로 — 투입 속도를 낮추고 "
            "신용 상태를 먼저 확인하십시오."
        )
        required_checks.insert(0, "HY/IG 스프레드 데이터 확보 (FRED API 키 등록 권장)")

    final_instrument = rec_instrument or zone["instrument"]

    if drawdown is None:
        commentary = (
            "QQQ 낙폭 데이터를 수집하지 못해 투입 단계를 판단할 수 없습니다 — 확인 필요. "
            "데이터 수집 후 재평가하십시오."
        )
    else:
        commentary = (
            f"나스닥(QQQ)은 52주 고점 대비 {drawdown * 100:+.1f}% 입니다. "
            f"현재 '{zone['zone']}'에 해당하며, 권장 수단은 {final_instrument} 입니다"
            f"{rec_note}. "
            f"{zone['action']} {speed_note}"
        )

    return {
        "qqq_drawdown_from_high": drawdown,
        "deployment_zone": zone["zone"],
        "recommended_instrument": final_instrument,
        "suggested_action": zone["action"],
        "credit_stress_status": cs,
        "liquidity_status": liquidity_status,
        "required_checks": required_checks,
        "commentary_ko": commentary,
    }
