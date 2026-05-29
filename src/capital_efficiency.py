"""Capital Efficiency Score — Capital Efficiency 시스템 (Phase 2).

핵심 질문:
- Alpha Score 는 "좋은 회사인가" 를 본다.
- Capital Efficiency Score 는 "지금 가격에서, 일정 기간 안에, QLD(나스닥100 2x)
  대비 더 나은 위험조정 수익률을 낼 수 있는가" 를 본다.

원칙 (Phase 1 market_regime.py 와 동일):
- Rule-based. 점수·분류·코멘트 전부 규칙 기반. LLM 없이 완전 동작.
- 입력 데이터가 없는 sub-score 는 '확인 필요' 로 표시하고 가중치에서 제외,
  나머지로 재정규화. 절대 0 으로 처리하지 않는다.
- 모든 함수는 예외를 던지지 않는다.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("capital_efficiency")

NEEDS_CHECK = "확인 필요"

# Capital Efficiency Score sub-score 가중치 (합 = 1.0)
# ※ 손으로 정한 휴리스틱 — data-fit 아님. 점수는 ordinal 순위로만 신뢰.
SUBSCORE_WEIGHTS: dict[str, float] = {
    "expected_return_potential": 0.25,
    "time_to_target_probability": 0.20,
    "downside_risk_score": 0.20,
    "catalyst_visibility_score": 0.15,
    "qld_relative_score": 0.10,
    "liquidity_exit_score": 0.10,
}

SUBSCORE_LABELS_KO: dict[str, str] = {
    "expected_return_potential": "기대수익 잠재력",
    "time_to_target_probability": "목표 도달 확률",
    "downside_risk_score": "하방·최대낙폭 리스크",
    "catalyst_visibility_score": "촉매 가시성",
    "qld_relative_score": "QLD 상대 매력도",
    "liquidity_exit_score": "유동성·청산 용이성",
}


# ---------------------------------------------------------------------------
# 보조 — clamp / 선형 매핑
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, low: float, high: float) -> float:
    """value 가 low→high 로 갈수록 0→100. 범위 밖은 clamp."""
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def _f(x: Any) -> float | None:
    """float 변환 — 실패 시 None."""
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _md_of(stock: dict) -> dict:
    """stock dict 에서 market_data 추출 (없으면 stock 자체를 사용)."""
    md = stock.get("market_data")
    if isinstance(md, dict):
        return md
    return stock


# ---------------------------------------------------------------------------
# Sub-score 계산기 — 각각 (score|None, commentary_ko) 반환
# ---------------------------------------------------------------------------

def _score_expected_return(md: dict) -> tuple[float | None, str]:
    """기대수익 잠재력 — 52주 고점 대비 낙폭 + 매출성장 + valuation 결합.

    낙폭이 깊고(회복 여지) 성장은 살아있으며 valuation 이 과하지 않으면 높은 점수.
    """
    parts: list[float] = []
    notes: list[str] = []

    dd = _f(md.get("drawdown_from_52w_high"))
    if dd is not None:
        # 고점 대비 -40%(회복 여지 큼)~0%(고점, 여지 작음)
        parts.append(_lerp_score(-dd, 0.0, 0.40))
        notes.append(f"52주 고점 대비 {dd * 100:+.0f}%")

    rg = _f(md.get("revenue_growth"))
    if rg is not None:
        # 매출성장 0%~40%
        parts.append(_lerp_score(rg, 0.0, 0.40))
        notes.append(f"매출성장 {rg * 100:+.0f}%")

    fpe = _f(md.get("forward_pe"))
    if fpe is not None and 0 < fpe < 200:
        # forward PE 낮을수록 기대수익 여지 큼 — 50(과열)~12(저평가)
        parts.append(_lerp_score(fpe, 50.0, 12.0))
        notes.append(f"forward PE {fpe:.0f}x")

    if not parts:
        return None, f"낙폭·성장·valuation 데이터 미수집 — 기대수익 잠재력 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("기대수익 여지 제한적" if score < 35 else "보통" if score < 60
            else "기대수익 여지 양호" if score < 80 else "기대수익 여지 큼")
    return score, "기대수익 잠재력: " + desc + " — " + ", ".join(notes) + "."


def estimate_time_to_target_probability(md: dict) -> tuple[float | None, str]:
    """목표 도달 확률 — 추세(200일선 위치)·모멘텀·변동성 으로 근사.

    추세가 살아있고 단기 모멘텀이 과열되지 않았으면 합리적 기간 내 목표 도달 가능성 ↑.
    """
    parts: list[float] = []
    notes: list[str] = []

    r3m = _f(md.get("3m_return"))
    r6m = _f(md.get("6m_return"))
    if r6m is not None:
        # 6개월 추세가 완만하게 우상향이면 좋음. -20%~+40%
        parts.append(_lerp_score(r6m, -0.20, 0.40))
        notes.append(f"6개월 {r6m * 100:+.0f}%")
    if r3m is not None:
        # 3개월이 너무 급등(과열, 되돌림 위험)이면 감점 — +50% 이상은 페널티
        if r3m > 0.50:
            parts.append(_lerp_score(r3m, 1.10, 0.50))  # 급등할수록 낮은 점수
            notes.append(f"3개월 {r3m * 100:+.0f}% (단기 급등)")
        else:
            parts.append(_lerp_score(r3m, -0.25, 0.30))
            notes.append(f"3개월 {r3m * 100:+.0f}%")

    if not parts:
        return None, f"가격 추세 데이터 미수집 — 목표 도달 확률 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("기간 내 목표 도달 가능성 낮음" if score < 35 else "보통" if score < 60
            else "합리적 기간 내 도달 가능성 양호" if score < 80 else "도달 가능성 높음")
    return score, "목표 도달 확률(추세 근사): " + desc + " — " + ", ".join(notes) + "."


def calculate_downside_risk_score(md: dict) -> tuple[float | None, str]:
    """하방·최대낙폭 리스크 — 변동성·기존 낙폭·valuation 부담.

    점수가 높을수록 '하방 리스크가 낮다'(자본효율에 유리). 변동성/과열일수록 낮은 점수.
    """
    parts: list[float] = []
    notes: list[str] = []

    vol = _annualized_vol(md)
    if vol is not None:
        # 연환산 변동성 20%(안정)~75%(고변동) — 변동성 낮을수록 높은 점수
        parts.append(_lerp_score(vol, 0.75, 0.20))
        notes.append(f"연환산 변동성 {vol * 100:.0f}%")

    fpe = _f(md.get("forward_pe"))
    if fpe is not None and 0 < fpe < 200:
        # valuation 부담 — PE 12(부담 작음)~55(부담 큼)
        parts.append(_lerp_score(fpe, 55.0, 12.0))
        notes.append(f"forward PE {fpe:.0f}x")

    r1y = _f(md.get("1y_return"))
    if r1y is not None:
        # 1년 +120% 이상 급등주는 되돌림 하방 위험 — 급등할수록 낮은 점수
        parts.append(_lerp_score(r1y, 1.50, -0.10))
        notes.append(f"1년 {r1y * 100:+.0f}%")

    if not parts:
        return None, f"변동성·valuation 데이터 미수집 — 하방 리스크 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("하방 리스크 큼" if score < 35 else "보통" if score < 60
            else "하방 리스크 제한적" if score < 80 else "하방 리스크 낮음")
    return score, "하방·최대낙폭 리스크: " + desc + " — " + ", ".join(notes) + "."


def _score_catalyst_visibility(stock: dict, md: dict) -> tuple[float | None, str]:
    """촉매 가시성 — 큐레이션 이벤트 / Alpha Score event 컴포넌트 로 근사.

    무료 데이터로 직접 측정 어려움 — 가능한 신호가 없으면 '확인 필요'.
    """
    # 큐레이션 이벤트
    events = stock.get("curated_events") or []
    n_events = len(events) if isinstance(events, list) else 0

    # scoring.py event sub-score (있으면 사용)
    scores = stock.get("scores") or {}
    ev_score = _f(scores.get("event"))

    if ev_score is not None:
        # event freshness 점수를 그대로 촉매 가시성 근사로 사용
        return ev_score, (
            f"촉매 가시성(이벤트 신선도 근사): {ev_score:.0f}/100"
            + (f", 큐레이션 이벤트 {n_events}건" if n_events else "")
            + "."
        )
    if n_events > 0:
        score = _lerp_score(n_events, 0.0, 3.0)
        return score, f"촉매 가시성: 최근 큐레이션 이벤트 {n_events}건 기준 근사."
    return None, (
        f"명시적 촉매(이벤트·실적 일정) 데이터 미수집 — 촉매 가시성 {NEEDS_CHECK}. "
        "큐레이션 이벤트 또는 Alpha Score event 컴포넌트가 있으면 반영."
    )


def calculate_qld_relative_attractiveness(
    md: dict, qld_ctx: dict | None
) -> tuple[float | None, str, str]:
    """QLD 상대 매력도 — 종목을 QLD(나스닥100 2x) 와 비교.

    Returns: (score|None, commentary_ko, view)
    view ∈ {Better than QLD, Similar to QLD, Worse than QLD, Not Comparable}
    """
    qld_ctx = qld_ctx or {}
    qld_md = qld_ctx.get("market_data") if isinstance(qld_ctx.get("market_data"), dict) else qld_ctx

    stock_6m = _f(md.get("6m_return"))
    stock_1y = _f(md.get("1y_return"))
    stock_vol = _annualized_vol(md)

    qld_6m = _f((qld_md or {}).get("6m_return"))
    qld_1y = _f((qld_md or {}).get("1y_return"))
    qld_vol = _annualized_vol(qld_md or {})

    # QLD 데이터 자체가 없으면 비교 불가
    if qld_6m is None and qld_1y is None:
        return None, (
            f"QLD 기준 데이터 미수집 — QLD 상대 매력도 {NEEDS_CHECK}. "
            "QLD 가격 데이터 확보 후 재평가."
        ), "Not Comparable"
    if stock_6m is None and stock_1y is None:
        return None, (
            f"종목 수익률 데이터 미수집 — QLD 상대 매력도 {NEEDS_CHECK}."
        ), "Not Comparable"

    # 위험조정 수익(간이 Sharpe 근사) 비교
    def _risk_adj(ret: float | None, vol: float | None) -> float | None:
        if ret is None:
            return None
        if vol is None or vol <= 0:
            return ret
        return ret / vol

    s_ra = _risk_adj(stock_1y if stock_1y is not None else stock_6m, stock_vol)
    q_ra = _risk_adj(qld_1y if qld_1y is not None else qld_6m, qld_vol)

    notes: list[str] = []
    if stock_1y is not None and qld_1y is not None:
        notes.append(f"1년 수익 종목 {stock_1y * 100:+.0f}% vs QLD {qld_1y * 100:+.0f}%")
    if stock_vol is not None and qld_vol is not None:
        notes.append(f"변동성 종목 {stock_vol * 100:.0f}% vs QLD {qld_vol * 100:.0f}%")

    if s_ra is None or q_ra is None:
        return 50.0, (
            "QLD 와 부분적으로만 비교 가능 — 위험조정 수익 비교 불충분. "
            + (", ".join(notes) if notes else "")
        ), "Similar to QLD"

    # 위험조정 수익 격차 → 점수·view
    gap = s_ra - q_ra
    score = _lerp_score(gap, -1.5, 1.5)
    if gap >= 0.4:
        view = "Better than QLD"
        view_ko = "QLD 보다 위험조정 매력 우위"
    elif gap <= -0.4:
        view = "Worse than QLD"
        view_ko = "QLD 보다 위험조정 매력 열위 — QLD 가 더 합리적 선택일 수 있음"
    else:
        view = "Similar to QLD"
        view_ko = "QLD 와 위험조정 매력 비슷한 수준"
    commentary = (
        f"QLD 상대 매력도: {view_ko}. " + (", ".join(notes) + "." if notes else "")
        + " (위험조정 수익 = 수익률/변동성 간이 비교)"
    )
    return score, commentary, view


def _score_liquidity_exit(md: dict) -> tuple[float | None, str]:
    """유동성·청산 용이성 — 거래대금(시총·거래량) 으로 근사."""
    parts: list[float] = []
    notes: list[str] = []

    mc = _f(md.get("market_cap"))
    if mc is not None and mc > 0:
        # 시총 5억$(소형, 청산 어려움)~2000억$(대형, 청산 용이) — 로그 스케일 근사
        import math
        lm = math.log10(mc)
        parts.append(_lerp_score(lm, math.log10(5e8), math.log10(2e11)))
        notes.append(f"시총 ${mc / 1e9:.0f}B")

    price = _f(md.get("current_price"))
    vol = _f(md.get("avg_volume_30d"))
    if price is not None and vol is not None and price > 0 and vol > 0:
        dollar_vol = price * vol
        # 일평균 거래대금 1000만$~50억$
        import math
        ld = math.log10(dollar_vol)
        parts.append(_lerp_score(ld, math.log10(1e7), math.log10(5e9)))
        notes.append(f"일평균 거래대금 ${dollar_vol / 1e6:.0f}M")

    if not parts:
        return None, f"시총·거래량 데이터 미수집 — 유동성·청산 용이성 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("유동성 낮음(청산 시 슬리피지 주의)" if score < 35 else "보통" if score < 60
            else "유동성 양호" if score < 80 else "유동성 매우 풍부")
    return score, "유동성·청산 용이성: " + desc + " — " + ", ".join(notes) + "."


# ---------------------------------------------------------------------------
# 변동성 헬퍼
# ---------------------------------------------------------------------------

def _annualized_vol(md: dict | None) -> float | None:
    """가격이력(history)에서 연환산 변동성. 데이터 부족 시 None."""
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
        std = float(rets.std())
        return std * (252 ** 0.5)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 점수대 분류
# ---------------------------------------------------------------------------

def _efficiency_band_ko(score: float) -> str:
    if score < 30:
        return "낮음"
    if score < 50:
        return "중립"
    if score < 70:
        return "검토 가능"
    if score < 85:
        return "양호"
    return "매우 높음"


# ---------------------------------------------------------------------------
# Capital Efficiency Score — 메인
# ---------------------------------------------------------------------------

def calculate_capital_efficiency_score(
    stock: dict[str, Any], qld_ctx: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Capital Efficiency Score 0~100 산정.

    stock: row dict (ticker/market_data/scores/curated_events 등) 또는 market_data dict.
    qld_ctx: QLD 의 row/market_data dict (QLD 상대 비교용). None 이면 해당 sub-score 제외.

    Returns dict: capital_efficiency_score, efficiency_band_ko, 각 *_score,
    각 *_commentary_ko, qld_relative_view, used_weights, missing_subscores, commentary_ko.
    """
    stock = stock or {}
    md = _md_of(stock)

    # QLD 상대 매력도 — view 도 함께 받음
    qld_score, qld_comment, qld_view = calculate_qld_relative_attractiveness(md, qld_ctx)

    calculators = {
        "expected_return_potential": lambda: _score_expected_return(md),
        "time_to_target_probability": lambda: estimate_time_to_target_probability(md),
        "downside_risk_score": lambda: calculate_downside_risk_score(md),
        "catalyst_visibility_score": lambda: _score_catalyst_visibility(stock, md),
        "qld_relative_score": lambda: (qld_score, qld_comment),
        "liquidity_exit_score": lambda: _score_liquidity_exit(md),
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

    # 가용 sub-score 만으로 가중치 재정규화
    available = {k: v for k, v in sub_scores.items() if v is not None}
    missing = [k for k, v in sub_scores.items() if v is None]

    if available:
        total_w = sum(SUBSCORE_WEIGHTS[k] for k in available)
        used_weights = {k: SUBSCORE_WEIGHTS[k] / total_w for k in available}
        efficiency = sum(available[k] * used_weights[k] for k in available)
        efficiency = round(_clamp(efficiency), 1)
    else:
        used_weights = {}
        efficiency = None

    result: dict[str, Any] = {
        "capital_efficiency_score": efficiency,
        "efficiency_band_ko": _efficiency_band_ko(efficiency) if efficiency is not None else NEEDS_CHECK,
        "qld_relative_view": qld_view,
        "used_weights": used_weights,
        "missing_subscores": missing,
    }
    for key in calculators:
        result[f"{key}"] = sub_scores[key]
        result[f"{key}_commentary_ko"] = commentaries[key]

    result["commentary_ko"] = generate_capital_efficiency_commentary(result)
    return result


def generate_capital_efficiency_commentary(result: dict[str, Any]) -> str:
    """LLM 없이 규칙 기반 한국어 종합 코멘트 생성."""
    score = result.get("capital_efficiency_score")
    band = result.get("efficiency_band_ko", NEEDS_CHECK)
    view = result.get("qld_relative_view", "Not Comparable")
    parts: list[str] = []

    if score is None:
        return (
            "Capital Efficiency Score 산정에 필요한 데이터가 부족합니다 — 확인 필요. "
            "가격이력·valuation 데이터 확보 후 재평가하십시오."
        )

    band_lead = {
        "낮음": "현재 가격에서 자본효율 매력이 낮습니다. 같은 자금이라면 QLD 등 대안이 더 합리적일 수 있습니다.",
        "중립": "현재 가격에서 자본효율은 중립 수준입니다. 적극적 신규 투입보다 관찰이 적절합니다.",
        "검토 가능": "현재 가격에서 자본효율 측면의 검토가 가능한 구간입니다. 다만 촉매·기간 가정을 함께 점검하십시오.",
        "양호": "현재 가격에서 자본효율 매력이 양호합니다. 위험조정 기준 합리적 진입 후보입니다.",
        "매우 높음": "현재 가격에서 자본효율 매력이 매우 높습니다. 다만 점수가 높다고 리스크가 없는 것은 아닙니다.",
    }
    parts.append(band_lead.get(band, ""))
    parts.append(f"Capital Efficiency Score 는 {score:.0f}/100 ({band}) 입니다.")

    view_ko = {
        "Better than QLD": "QLD(나스닥100 2x) 대비 위험조정 매력이 우위입니다.",
        "Similar to QLD": "QLD 와 위험조정 매력이 비슷한 수준입니다 — 차별화 요인을 확인하십시오.",
        "Worse than QLD": "QLD 대비 위험조정 매력이 열위입니다 — 같은 자금이라면 QLD 가 더 나을 수 있습니다.",
        "Not Comparable": f"QLD 와의 직접 비교는 데이터 부족으로 {NEEDS_CHECK} 상태입니다.",
    }
    parts.append(view_ko.get(view, ""))

    missing = result.get("missing_subscores") or []
    if missing:
        labels = ", ".join(SUBSCORE_LABELS_KO.get(m, m) for m in missing)
        parts.append(f"※ 데이터 부족으로 가중치에서 제외된 항목: {labels} (나머지로 재정규화).")

    parts.append(
        "이 점수는 '좋은 회사인가'(Alpha Score)가 아니라 '지금 가격에서 일정 기간 내 "
        "QLD 대비 더 나은가'를 보는 보조 지표입니다."
    )
    return " ".join(p for p in parts if p)
