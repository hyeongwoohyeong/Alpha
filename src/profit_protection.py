"""Profit Protection Score — Capital Efficiency 시스템 (Phase 2).

핵심 질문:
- 이미 보유 중이고 큰 수익이 난 고베타/레버리지 포지션에서, '익절(수익 보호)'이
  얼마나 필요한가.

중요 원칙:
- 회사가 나빠서 익절을 권하는 것이 아니다. 좋은 회사라도, 가격 과열 + 충분한
  수익 + 레버리지/포지션 리스크가 겹치면 '수익 보호'를 권고한다.
- commentary 에 이 점을 항상 명확히 한다.

원칙 (Phase 1 market_regime.py 와 동일):
- Rule-based. LLM 없이 완전 동작.
- 입력 데이터 없는 sub-score 는 '확인 필요' 로 표시·가중치 제외·재정규화.
- 모든 함수는 예외를 던지지 않는다.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("profit_protection")

NEEDS_CHECK = "확인 필요"

# Profit Protection Score sub-score 가중치 (합 = 1.0)
SUBSCORE_WEIGHTS: dict[str, float] = {
    "unrealized_gain_score": 0.20,
    "valuation_stretch_score": 0.20,
    "technical_extension_score": 0.15,
    "narrative_crowding_score": 0.15,
    "earnings_revision_gap_score": 0.15,
    "position_risk_score": 0.15,
}

SUBSCORE_LABELS_KO: dict[str, str] = {
    "unrealized_gain_score": "미실현 수익",
    "valuation_stretch_score": "밸류에이션 과열",
    "technical_extension_score": "기술적 과열",
    "narrative_crowding_score": "내러티브 쏠림",
    "earnings_revision_gap_score": "이익 추정 vs 가격 괴리",
    "position_risk_score": "포지션 리스크·레버리지",
}

# 고베타 / 레버리지로 간주하는 ticker (보조 — beta 직접 추정 실패 시)
LEVERAGED_TICKERS: set[str] = {
    "TQQQ", "QLD", "SOXL", "UPRO", "SPXL", "TECL", "FNGU", "BULZ", "USD",
}


# ---------------------------------------------------------------------------
# 보조
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, low: float, high: float) -> float:
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except (TypeError, ValueError):
        return None


def _md_of(stock: dict) -> dict:
    md = stock.get("market_data")
    if isinstance(md, dict):
        return md
    return stock


def _annualized_vol(md: dict | None) -> float | None:
    if not md:
        return None
    hist = md.get("history")
    if hist is None:
        return None
    try:
        if "Close" not in hist.columns:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 30:
            return None
        rets = closes.pct_change().dropna().tail(252)
        if len(rets) < 20:
            return None
        return float(rets.std()) * (252 ** 0.5)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 미실현 수익 — 진입가가 있으면 계산, 없으면 '확인 필요'
# ---------------------------------------------------------------------------

def _current_unrealized_gain(position: dict, md: dict) -> float | None:
    """진입가(entry_price/cost_basis)와 현재가로 미실현 수익률 계산.

    진입가가 없으면 None ('확인 필요'). 1년 수익률을 대용하지 않는다
    — 보유 시점이 1년 전이라는 보장이 없기 때문.
    """
    entry = _f(position.get("entry_price")) or _f(position.get("cost_basis"))
    cur = _f(md.get("current_price"))
    if entry is None or cur is None or entry <= 0:
        return None
    return (cur / entry) - 1.0


def _score_unrealized_gain(position: dict, md: dict) -> tuple[float | None, str, float | None]:
    """미실현 수익 sub-score. Returns (score|None, comment, gain|None)."""
    gain = _current_unrealized_gain(position, md)
    if gain is None:
        return None, (
            f"진입가(매입단가) 정보가 없어 미실현 수익을 계산할 수 없습니다 — {NEEDS_CHECK}. "
            "watchlist 에 entry_price 가 입력되면 반영됩니다."
        ), None
    # 미실현 수익 0%(보호 불필요)~150%(큰 수익, 보호 강하게)
    score = _lerp_score(gain, 0.0, 1.50)
    desc = ("수익 미미 — 보호 불필요" if score < 25 else "보통 수익" if score < 55
            else "상당한 수익 — 보호 검토" if score < 80 else "매우 큰 수익 — 보호 필요성 높음")
    return score, f"미실현 수익 {gain * 100:+.0f}% — {desc}.", gain


def _score_valuation_stretch(md: dict) -> tuple[float | None, str]:
    """밸류에이션 과열 — forward PE / PSR. 높을수록 익절 필요성 ↑."""
    parts: list[float] = []
    notes: list[str] = []

    fpe = _f(md.get("forward_pe"))
    if fpe is not None and 0 < fpe < 300:
        parts.append(_lerp_score(fpe, 15.0, 60.0))
        notes.append(f"forward PE {fpe:.0f}x")
    psr = _f(md.get("psr"))
    if psr is not None and 0 < psr < 100:
        parts.append(_lerp_score(psr, 3.0, 25.0))
        notes.append(f"PSR {psr:.1f}x")

    if not parts:
        return None, f"forward PE·PSR 미수집 — 밸류에이션 과열 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("밸류에이션 부담 낮음" if score < 35 else "보통" if score < 60
            else "밸류에이션 부담 큼" if score < 80 else "밸류에이션 극단 과열")
    return score, "밸류에이션 과열: " + desc + " — " + ", ".join(notes) + "."


def _score_technical_extension(md: dict) -> tuple[float | None, str]:
    """기술적 과열 — 단기 급등·52주 고점 근접. 높을수록 익절 필요성 ↑."""
    parts: list[float] = []
    notes: list[str] = []

    dd = _f(md.get("drawdown_from_52w_high"))
    if dd is not None:
        # 고점 근접(dd≈0)일수록 과열. -25%~0%
        parts.append(_lerp_score(dd, -0.25, 0.0))
        notes.append(f"52주 고점 대비 {dd * 100:+.0f}%")
    r3m = _f(md.get("3m_return"))
    if r3m is not None:
        # 3개월 급등 0%~+70%
        parts.append(_lerp_score(r3m, 0.0, 0.70))
        notes.append(f"3개월 {r3m * 100:+.0f}%")
    r1m = _f(md.get("1m_return"))
    if r1m is not None:
        parts.append(_lerp_score(r1m, 0.0, 0.30))
        notes.append(f"1개월 {r1m * 100:+.0f}%")

    if not parts:
        return None, f"가격 추세 데이터 미수집 — 기술적 과열 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("기술적 과열 아님" if score < 35 else "보통" if score < 60
            else "기술적 과열 진입" if score < 80 else "극단적 과열(과매수)")
    return score, "기술적 과열: " + desc + " — " + ", ".join(notes) + "."


def _score_narrative_crowding(md: dict) -> tuple[float | None, str]:
    """내러티브 쏠림 — 거래량 급증 + 1년 급등 으로 근사 (FOMO 쏠림)."""
    parts: list[float] = []
    notes: list[str] = []

    vol = _f(md.get("volume"))
    avg_vol = _f(md.get("avg_volume_30d"))
    if vol is not None and avg_vol is not None and avg_vol > 0:
        ratio = vol / avg_vol
        parts.append(_lerp_score(ratio, 0.8, 2.5))
        notes.append(f"거래량 평균比 {ratio:.1f}x")
    r1y = _f(md.get("1y_return"))
    if r1y is not None:
        parts.append(_lerp_score(r1y, 0.20, 1.50))
        notes.append(f"1년 {r1y * 100:+.0f}%")

    if not parts:
        return None, f"거래량·수익률 데이터 미수집 — 내러티브 쏠림 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("쏠림 약함" if score < 35 else "보통" if score < 60
            else "쏠림 확대(인기 과열)" if score < 80 else "극단적 쏠림(FOMO)")
    return score, "내러티브 쏠림: " + desc + " — " + ", ".join(notes) + "."


def _score_earnings_revision_gap(md: dict) -> tuple[float | None, str]:
    """이익 추정 vs 가격 괴리 — 가격은 급등했는데 valuation 이 따라 비싸졌는지로 근사.

    실적 추정치 상·하향 데이터는 무료 소스로 직접 수집 불가 → 가격 급등 대비
    forward PE 부담으로 괴리를 근사. 둘 다 없으면 '확인 필요'.
    """
    r6m = _f(md.get("6m_return"))
    fpe = _f(md.get("forward_pe"))
    if r6m is None and fpe is None:
        return None, (
            f"실적 추정 vs 가격 괴리는 무료 데이터로 직접 측정 불가 — {NEEDS_CHECK}."
        )
    parts: list[float] = []
    notes: list[str] = []
    if r6m is not None:
        parts.append(_lerp_score(r6m, 0.0, 0.80))
        notes.append(f"6개월 가격 {r6m * 100:+.0f}%")
    if fpe is not None and 0 < fpe < 300:
        parts.append(_lerp_score(fpe, 15.0, 55.0))
        notes.append(f"forward PE {fpe:.0f}x")
    score = sum(parts) / len(parts)
    return score, (
        "이익 추정 vs 가격 괴리(근사): "
        + ("괴리 작음" if score < 40 else "괴리 확대 — 가격이 펀더멘털을 앞서감" if score < 70
           else "괴리 큼 — 가격이 펀더멘털을 크게 앞섬")
        + " — " + ", ".join(notes) + f". (실적 추정치 직접 데이터는 {NEEDS_CHECK})"
    )


def analyze_high_beta_position_risk(
    position: dict, md: dict
) -> tuple[float | None, str, bool]:
    """포지션 리스크·레버리지 분석. Returns (score|None, comment, leverage_flag).

    레버리지/고변동 포지션일수록 점수 높음(익절 필요성 ↑).
    """
    ticker = str(position.get("ticker") or "").upper()
    leverage_flag = ticker in LEVERAGED_TICKERS

    parts: list[float] = []
    notes: list[str] = []

    if leverage_flag:
        parts.append(85.0)
        notes.append("레버리지 ETF (구조적 변동성 증폭)")

    vol = _annualized_vol(md)
    if vol is not None:
        # 연환산 변동성 25%(저변동)~80%(고변동)
        parts.append(_lerp_score(vol, 0.25, 0.80))
        notes.append(f"연환산 변동성 {vol * 100:.0f}%")
    elif not leverage_flag:
        # 변동성도 모르고 레버리지도 아니면 판단 불가
        return None, f"변동성 데이터 미수집 — 포지션 리스크 {NEEDS_CHECK}.", False

    r1y = _f(md.get("1y_return"))
    if r1y is not None and r1y > 0.80:
        # 1년 +80% 이상이면 되돌림 폭 리스크 가산
        parts.append(_lerp_score(r1y, 0.80, 2.50))
        notes.append(f"1년 {r1y * 100:+.0f}% (되돌림 폭 리스크)")

    if not parts:
        return None, f"포지션 리스크 데이터 미수집 — {NEEDS_CHECK}.", leverage_flag
    score = sum(parts) / len(parts)
    desc = ("포지션 리스크 낮음" if score < 35 else "보통" if score < 60
            else "포지션 리스크 큼" if score < 80 else "포지션 리스크 매우 큼")
    return score, "포지션 리스크·레버리지: " + desc + " — " + ", ".join(notes) + ".", leverage_flag


# ---------------------------------------------------------------------------
# 점수대 / 권고
# ---------------------------------------------------------------------------

def _protection_band_ko(score: float) -> str:
    if score < 30:
        return "보유 유지"
    if score < 50:
        return "일부 보호"
    if score < 70:
        return "단계적 익절"
    if score < 85:
        return "강한 익절"
    return "대부분 정리"


def generate_profit_taking_recommendation(score: float | None, band: str) -> str:
    """점수대별 익절 권고 액션 (rule-based)."""
    if score is None:
        return "데이터 부족 — 익절 권고를 판단할 수 없습니다 (확인 필요)."
    return {
        "보유 유지": "현재로서는 수익 보호가 시급하지 않습니다. 보유를 유지하되 과열 지표를 주기적으로 점검하십시오.",
        "일부 보호": "일부(예: 10~25%) 비중 축소로 수익 일부를 보호하는 것을 검토하십시오.",
        "단계적 익절": "과열 신호가 누적되었습니다. 25~50% 비중을 단계적으로 익절해 수익을 보호하십시오.",
        "강한 익절": "과열·수익·리스크가 강하게 겹칩니다. 절반 이상 익절로 수익을 적극 보호하십시오.",
        "대부분 정리": "수익 보호 필요성이 매우 높습니다. 핵심 비중만 남기고 대부분 정리를 검토하십시오.",
    }.get(band, "익절 권고 판단 불가.")


# ---------------------------------------------------------------------------
# Profit Protection Score — 메인
# ---------------------------------------------------------------------------

def calculate_profit_protection_score(position: dict[str, Any]) -> dict[str, Any]:
    """Profit Protection Score 0~100 산정.

    position: 보유 포지션 dict — ticker / market_data / (선택) entry_price.
              market_data 가 없으면 position 자체를 market_data 로 사용.

    Returns dict: profit_protection_score, protection_band_ko, suggested_action,
    current_gain, leverage_flag, 각 *_score, 각 *_commentary_ko,
    used_weights, missing_subscores, commentary_ko.
    """
    position = position or {}
    md = _md_of(position)

    ug_score, ug_comment, gain = _score_unrealized_gain(position, md)
    pos_score, pos_comment, leverage_flag = analyze_high_beta_position_risk(position, md)

    calculators = {
        "unrealized_gain_score": lambda: (ug_score, ug_comment),
        "valuation_stretch_score": lambda: _score_valuation_stretch(md),
        "technical_extension_score": lambda: _score_technical_extension(md),
        "narrative_crowding_score": lambda: _score_narrative_crowding(md),
        "earnings_revision_gap_score": lambda: _score_earnings_revision_gap(md),
        "position_risk_score": lambda: (pos_score, pos_comment),
    }

    sub_scores: dict[str, float | None] = {}
    commentaries: dict[str, str] = {}
    for key, fn in calculators.items():
        try:
            score, comment = fn()
        except Exception as e:
            log.warning("sub-score %s 계산 실패: %s", key, e)
            score, comment = None, f"{SUBSCORE_LABELS_KO[key]} 계산 오류 — {NEEDS_CHECK}."
        sub_scores[key] = (round(_clamp(score), 1) if score is not None else None)
        commentaries[key] = comment

    available = {k: v for k, v in sub_scores.items() if v is not None}
    missing = [k for k, v in sub_scores.items() if v is None]

    if available:
        total_w = sum(SUBSCORE_WEIGHTS[k] for k in available)
        used_weights = {k: SUBSCORE_WEIGHTS[k] / total_w for k in available}
        protection = sum(available[k] * used_weights[k] for k in available)
        protection = round(_clamp(protection), 1)
    else:
        used_weights = {}
        protection = None

    band = _protection_band_ko(protection) if protection is not None else NEEDS_CHECK
    suggested_action = generate_profit_taking_recommendation(protection, band)

    result: dict[str, Any] = {
        "profit_protection_score": protection,
        "protection_band_ko": band,
        "suggested_action": suggested_action,
        "current_gain": gain,
        "leverage_flag": leverage_flag,
        "used_weights": used_weights,
        "missing_subscores": missing,
    }
    for key in calculators:
        result[f"{key}"] = sub_scores[key]
        result[f"{key}_commentary_ko"] = commentaries[key]

    result["commentary_ko"] = _generate_protection_commentary(result, position)
    return result


def _generate_protection_commentary(result: dict[str, Any], position: dict) -> str:
    """LLM 없이 규칙 기반 한국어 종합 코멘트."""
    score = result.get("profit_protection_score")
    band = result.get("protection_band_ko", NEEDS_CHECK)
    gain = result.get("current_gain")
    leverage = result.get("leverage_flag")
    parts: list[str] = []

    if score is None:
        return (
            "Profit Protection Score 산정에 필요한 데이터가 부족합니다 — 확인 필요."
        )

    # 핵심 원칙 — 회사가 나빠서가 아님
    parts.append(
        "이 점수는 회사의 펀더멘털이 나빠졌다는 신호가 아닙니다. "
        "좋은 회사라도 가격 과열·충분한 수익·레버리지/포지션 리스크가 겹치면 "
        "'수익 보호(익절)'를 권고하는 지표입니다."
    )
    parts.append(f"Profit Protection Score 는 {score:.0f}/100 ({band}) 입니다.")
    if gain is not None:
        parts.append(f"현재 미실현 수익은 약 {gain * 100:+.0f}% 입니다.")
    else:
        parts.append("진입가 정보가 없어 미실현 수익은 확인 필요 상태입니다.")
    if leverage:
        parts.append(
            "해당 포지션은 레버리지 성격이 있어, 하락 시 손실이 증폭됩니다 — "
            "수익 보호의 중요성이 더 큽니다."
        )
    parts.append(result.get("suggested_action") or "")

    missing = result.get("missing_subscores") or []
    if missing:
        labels = ", ".join(SUBSCORE_LABELS_KO.get(m, m) for m in missing)
        parts.append(f"※ 데이터 부족으로 가중치에서 제외된 항목: {labels} (나머지로 재정규화).")
    return " ".join(p for p in parts if p)
