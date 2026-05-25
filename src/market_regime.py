"""Market Overheat Score + Regime 분류 — Portfolio Regime 시스템 (Phase 1).

핵심 원칙:
- Rule-based. 점수·분류·코멘트 전부 규칙 기반. LLM 없이 완전 동작.
- 입력 데이터가 없는 sub-score 는 '확인 필요' 로 표시하고 가중치에서 제외,
  나머지로 재정규화. 절대 0 으로 처리하지 않는다 (점수 왜곡 방지).
- 모든 함수는 예외를 던지지 않는다.

Overheat Score 는 sub-score 6종의 가중합으로 설계됐으나, 그 중
earnings_revision_risk(실적 추정 리스크)는 무료 데이터 소스가 없어
현재 항상 '확인 필요'(None)로 처리된다. 즉 점수는 사실상 5개 factor 로
계산되며, 빠진 0.15 가중치는 나머지 sub-score 에 재정규화된다.
"""
from __future__ import annotations

from typing import Any

from . import macro_data as _md
from .utils import get_logger

log = get_logger("market_regime")

# Overheat Score sub-score 가중치 (합 = 1.0).
# 주의: earnings_revision_risk 는 데이터 소스 미연결 상태로 항상 None 이므로
# 실제 점수는 나머지 5개 sub-score 에 가중치를 재정규화해 계산된다.
SUBSCORE_WEIGHTS: dict[str, float] = {
    "valuation_stretch": 0.25,
    "sentiment_speculation": 0.20,
    "market_concentration": 0.15,
    "liquidity_credit": 0.15,
    "earnings_revision_risk": 0.15,
    "technical_extension": 0.10,
}

SUBSCORE_LABELS_KO: dict[str, str] = {
    "valuation_stretch": "밸류에이션 과열",
    "sentiment_speculation": "투자심리/투기",
    "market_concentration": "시장 집중도",
    "liquidity_credit": "유동성/신용",
    "earnings_revision_risk": "실적 추정 리스크",
    "technical_extension": "기술적 과열",
}

NEEDS_CHECK = "확인 필요"


# ---------------------------------------------------------------------------
# 보조 — 0~100 clamp / 선형 매핑
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, low: float, high: float) -> float:
    """value 가 low→high 로 갈수록 0→100. 범위 밖은 clamp."""
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


# ---------------------------------------------------------------------------
# Sub-score 계산기 — 각각 (score|None, commentary_ko) 반환
# None 이면 '확인 필요' (가중치에서 제외)
# ---------------------------------------------------------------------------

def _score_valuation(etf: dict, megacap: dict) -> tuple[float | None, str]:
    """밸류에이션 — SPY/QQQ forward PE. CAPE/Buffett Indicator 는 확인 필요."""
    pes: list[float] = []
    for sym in ("SPY", "QQQ"):
        md = etf.get(sym) or {}
        fpe = md.get("forward_pe")
        if fpe and 5 < fpe < 80:
            pes.append(fpe)
    if not pes:
        return None, f"SPY/QQQ forward PE 미수집 — 밸류에이션 {NEEDS_CHECK}. CAPE·Buffett Indicator 별도 확인 필요."
    avg_pe = sum(pes) / len(pes)
    # forward PE 15(저평가)~32(과열) 선형 매핑
    score = _lerp_score(avg_pe, 15.0, 32.0)
    return score, (
        f"지수 forward PE 평균 {avg_pe:.1f}x — "
        + ("역사적 저평가권" if score < 35 else "정상 범위" if score < 60
           else "고평가권" if score < 80 else "극단적 과열")
        + f". (CAPE·Buffett Indicator 는 {NEEDS_CHECK})"
    )


def _score_sentiment(etf: dict) -> tuple[float | None, str]:
    """투자심리 — 레버리지 ETF 거래량 vs 평균. AAII·Put/Call 은 확인 필요."""
    ratios: list[float] = []
    for sym in ("TQQQ", "QLD", "SQQQ"):
        r = _md.volume_ratio(etf.get(sym) or {})
        if r is not None:
            ratios.append(r)
    if not ratios:
        return None, f"레버리지 ETF 거래량 미수집 — 투자심리 {NEEDS_CHECK}. AAII·Put/Call 별도 확인."
    avg_ratio = sum(ratios) / len(ratios)
    # 거래량비 0.7(한산)~2.2(투기 과열) 매핑
    score = _lerp_score(avg_ratio, 0.7, 2.2)
    return score, (
        f"레버리지 ETF(TQQQ/QLD/SQQQ) 최근 거래량은 60일 평균의 {avg_ratio:.2f}배 — "
        + ("거래 한산, 투기 약함" if score < 35 else "정상 수준" if score < 60
           else "투기적 거래 증가" if score < 80 else "투기 과열(FOMO)")
        + f". (AAII·Put/Call 지표는 {NEEDS_CHECK})"
    )


def _score_concentration(etf: dict, megacap: dict, breadth_pct: float | None
                          ) -> tuple[float | None, str]:
    """시장 집중도 — RSP vs SPY 상대수익 + breadth(200일선 상회 비율)."""
    parts: list[float] = []
    notes: list[str] = []

    spy = etf.get("SPY") or {}
    rsp = etf.get("RSP") or {}
    spy_3m = spy.get("3m_return")
    rsp_3m = rsp.get("3m_return")
    if spy_3m is not None and rsp_3m is not None:
        # equal-weight(RSP) 가 cap-weight(SPY) 보다 약하면 집중 심화
        gap = spy_3m - rsp_3m  # 양수 = SPY 우위 = 소수 종목 주도
        # gap -0.03(breadth 양호)~+0.08(극단 집중)
        parts.append(_lerp_score(gap, -0.03, 0.08))
        notes.append(f"3개월 SPY-RSP 상대수익차 {gap * 100:+.1f}%p")

    if breadth_pct is not None:
        # 200일선 상회 비율 높을수록 breadth 양호 = 집중도 낮음 → 점수 반전
        parts.append(_lerp_score(1.0 - breadth_pct, 0.2, 0.8))
        notes.append(f"유니버스 200일선 상회 {breadth_pct * 100:.0f}%")

    if not parts:
        return None, f"breadth/상대수익 데이터 미수집 — 시장 집중도 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("폭넓은 강세(breadth 양호)" if score < 35 else "정상" if score < 60
            else "소수 대형주 주도(breadth 약화)" if score < 80 else "극단적 쏠림")
    return score, "시장 집중도: " + desc + " — " + ", ".join(notes) + "."


def _score_liquidity_credit(fred: dict, etf: dict) -> tuple[float | None, str]:
    """유동성/신용 — FRED HY/IG 스프레드·실질금리. FRED 없으면 HYG/LQD 추세 근사."""
    if fred and fred.get("available"):
        parts: list[float] = []
        notes: list[str] = []
        hy = (fred.get("hy_spread") or {}).get("latest")
        if hy is not None:
            # HY OAS 2.8%(과도한 안도)~8%(신용 스트레스)
            parts.append(_lerp_score(hy, 8.0, 2.8))  # 낮은 스프레드 = 과열 = 높은 점수
            notes.append(f"HY 스프레드 {hy:.2f}%")
        rry = (fred.get("real_yield_10y") or {}).get("latest")
        if rry is not None:
            # 실질금리 낮을수록 유동성 풍부 → 과열 기여. 2.5%(긴축)~-0.5%(완화)
            parts.append(_lerp_score(rry, 2.5, -0.5))
            notes.append(f"10년 실질금리 {rry:.2f}%")
        if parts:
            score = sum(parts) / len(parts)
            desc = ("신용 경계/긴축적" if score < 35 else "정상" if score < 60
                    else "유동성 풍부, 신용 안도" if score < 80 else "과도한 신용 안도")
            return score, "유동성/신용(FRED): " + desc + " — " + ", ".join(notes) + "."

    # FRED 없음 → HYG/LQD ETF 가격 추세 근사
    hyg = etf.get("HYG") or {}
    lqd = etf.get("LQD") or {}
    hyg_3m = hyg.get("3m_return")
    lqd_3m = lqd.get("3m_return")
    if hyg_3m is not None and lqd_3m is not None:
        # HYG 강세 = 신용 안도(과열 기여). 3M 수익 -0.05~+0.05
        score = _lerp_score(hyg_3m, -0.05, 0.05)
        return score, (
            f"유동성/신용(근사치 — FRED 미사용): HYG 3개월 {hyg_3m * 100:+.1f}%, "
            f"LQD {lqd_3m * 100:+.1f}% 추세 기반. "
            f"정확한 스프레드는 FRED API 키 등록 시 반영."
        )
    return None, f"FRED·HYG/LQD 모두 미수집 — 유동성/신용 {NEEDS_CHECK}."


def _score_earnings_revision(etf: dict) -> tuple[float | None, str]:
    """실적 추정 리스크 — 무료 데이터로 직접 계산 불가. 확인 필요(중립 처리)."""
    return None, (
        f"실적 추정치 상·하향 데이터는 무료 소스로 수집 불가 — {NEEDS_CHECK}. "
        "가중치에서 제외하고 나머지 sub-score 로 재정규화."
    )


def _score_technical(etf: dict) -> tuple[float | None, str]:
    """기술적 과열 — SPY/QQQ 200일선 이격·RSI·52주 위치·단기수익률."""
    parts: list[float] = []
    notes: list[str] = []
    for sym in ("SPY", "QQQ"):
        md = etf.get(sym) or {}
        gap = _md.compute_ma_gap(md, window=200)
        if gap is not None:
            # 200일선 이격 -10%~+20%
            parts.append(_lerp_score(gap, -0.10, 0.20))
            notes.append(f"{sym} 200일선 {gap * 100:+.1f}%")
        rsi = _md.compute_rsi(md)
        if rsi is not None:
            # RSI 35~80
            parts.append(_lerp_score(rsi, 35.0, 80.0))
            notes.append(f"{sym} RSI {rsi:.0f}")
        dd = md.get("drawdown_from_52w_high")
        if dd is not None:
            # 52주 고점 대비 -20%(약세)~0%(고점) → 고점 근접 = 과열
            parts.append(_lerp_score(dd, -0.20, 0.0))
    if not parts:
        return None, f"SPY/QQQ 가격이력 미수집 — 기술적 과열 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("기술적 침체권" if score < 35 else "정상" if score < 60
            else "기술적 과열 진입" if score < 80 else "극단적 과열(과매수)")
    return score, "기술적 지표: " + desc + " — " + ", ".join(notes[:4]) + "."


# ---------------------------------------------------------------------------
# Market Overheat Score — 메인
# ---------------------------------------------------------------------------

def _overheat_band_ko(score: float) -> str:
    if score < 30:
        return "정상"
    if score < 50:
        return "주의"
    if score < 70:
        return "과열 경계"
    if score < 85:
        return "과열"
    return "FOMO/Casino"


def calculate_market_overheat_score(data: dict[str, Any]) -> dict[str, Any]:
    """Market Overheat Score 0~100 산정.

    sub-score 는 6종으로 정의돼 있으나 earnings_revision_risk 는
    데이터 소스가 없어 항상 None — 실제로는 5개 factor 로 계산되고
    빠진 가중치는 재정규화된다.

    data: collect_regime_inputs() 의 출력 {fred, etf, megacap, breadth}.
    Returns dict: market_overheat_score, overheat_band_ko, 각 sub-score (*_score),
    각 sub-score 코멘트 (*_commentary_ko), used_weights, missing_subscores.
    """
    data = data or {}
    fred = data.get("fred") or {}
    etf = data.get("etf") or {}
    megacap = data.get("megacap") or {}
    breadth = data.get("breadth") or {}

    # breadth — universe 200일선 상회 비율.
    # FIX: 과거엔 megacap 7종으로 계산했으나, mega-cap 은 좁은 쏠림장에서도
    # 200일선 위에 머물러 breadth 신호가 반전됐다. 이제 섹터 전반의
    # 광범위 유니버스(collect_regime_inputs 의 'breadth')로 계산한다.
    # breadth 유니버스가 비면 megacap 으로 fallback (그땐 mega-cap 근사치).
    breadth_pct = _md.pct_above_ma(breadth, window=200)
    if breadth_pct is None:
        breadth_pct = _md.pct_above_ma(megacap, window=200)

    calculators = {
        "valuation_stretch": lambda: _score_valuation(etf, megacap),
        "sentiment_speculation": lambda: _score_sentiment(etf),
        "market_concentration": lambda: _score_concentration(etf, megacap, breadth_pct),
        "liquidity_credit": lambda: _score_liquidity_credit(fred, etf),
        "earnings_revision_risk": lambda: _score_earnings_revision(etf),
        "technical_extension": lambda: _score_technical(etf),
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
        overheat = sum(available[k] * used_weights[k] for k in available)
        overheat = round(_clamp(overheat), 1)
    else:
        used_weights = {}
        overheat = None

    result: dict[str, Any] = {
        "market_overheat_score": overheat,
        "overheat_band_ko": _overheat_band_ko(overheat) if overheat is not None else NEEDS_CHECK,
        "used_weights": used_weights,
        "missing_subscores": missing,
        "breadth_pct": breadth_pct,
    }
    for key in calculators:
        result[f"{key}_score"] = sub_scores[key]
        result[f"{key}_commentary_ko"] = commentaries[key]
    return result


# ---------------------------------------------------------------------------
# Regime 분류
# ---------------------------------------------------------------------------

REGIME_RISK_ON = "Risk-On"
REGIME_EXPENSIVE_STABLE = "Expensive but Stable"
REGIME_OVERHEATED = "Overheated"
REGIME_CORRECTION_WATCH = "Correction Watch"
REGIME_DISLOCATION = "Dislocation"
REGIME_CRISIS = "Crisis"

REGIME_KO: dict[str, str] = {
    REGIME_RISK_ON: "위험선호 (Risk-On)",
    REGIME_EXPENSIVE_STABLE: "고평가·안정 (Expensive but Stable)",
    REGIME_OVERHEATED: "과열 (Overheated)",
    REGIME_CORRECTION_WATCH: "조정 경계 (Correction Watch)",
    REGIME_DISLOCATION: "디스로케이션 (Dislocation)",
    REGIME_CRISIS: "위기 (Crisis)",
}


def assess_credit_stress(fred: dict, etf: dict) -> dict[str, Any]:
    """신용 스트레스 평가. status: normal / elevated / severe / unknown."""
    fred = fred or {}
    etf = etf or {}
    if fred.get("available"):
        hy = (fred.get("hy_spread") or {}).get("latest")
        hy_chg = (fred.get("hy_spread") or {}).get("change_30d")
        if hy is not None:
            if hy >= 7.0 or (hy >= 5.5 and (hy_chg or 0) >= 1.5):
                return {"status": "severe", "source": "FRED",
                        "detail": f"HY 스프레드 {hy:.2f}% — 신용 위기 수준"}
            if hy >= 4.5 or (hy_chg or 0) >= 1.0:
                return {"status": "elevated", "source": "FRED",
                        "detail": f"HY 스프레드 {hy:.2f}% (30일 {hy_chg:+.2f}%p) — 신용 경계"}
            return {"status": "normal", "source": "FRED",
                    "detail": f"HY 스프레드 {hy:.2f}% — 신용시장 안정"}
    # FRED 없음 → HYG 추세 근사
    hyg = etf.get("HYG") or {}
    hyg_1m = hyg.get("1m_return")
    if hyg_1m is not None:
        if hyg_1m <= -0.06:
            return {"status": "severe", "source": "HYG 근사",
                    "detail": f"HYG 1개월 {hyg_1m * 100:+.1f}% — 신용 급락(근사치)"}
        if hyg_1m <= -0.03:
            return {"status": "elevated", "source": "HYG 근사",
                    "detail": f"HYG 1개월 {hyg_1m * 100:+.1f}% — 신용 약세(근사치)"}
        return {"status": "normal", "source": "HYG 근사",
                "detail": f"HYG 1개월 {hyg_1m * 100:+.1f}% — 신용 안정(근사치)"}
    return {"status": "unknown", "source": None,
            "detail": f"신용 데이터 미수집 — {NEEDS_CHECK}"}


def classify_market_regime(overheat: float | None,
                           qqq_drawdown: float | None,
                           credit_stress: str | None,
                           breadth_pct: float | None = None) -> str:
    """6국면 분류.

    overheat: 0~100 (None 가능)
    qqq_drawdown: QQQ 52주 고점 대비 낙폭 (음수, 예 -0.12). None 가능.
    credit_stress: 'normal'|'elevated'|'severe'|'unknown'
    """
    dd = qqq_drawdown if qqq_drawdown is not None else 0.0

    # 1) 신용 위기 최우선
    if credit_stress == "severe":
        return REGIME_CRISIS

    # 2) 큰 낙폭 + 신용 정상 → Dislocation (-10%~-25% 구간)
    if dd <= -0.10 and dd > -0.30 and credit_stress in ("normal", "elevated", "unknown"):
        # 신용이 elevated 이고 낙폭이 크면 Crisis 직전 경계지만 Dislocation 으로 분류
        return REGIME_DISLOCATION

    # 3) 매우 큰 낙폭 (-30% 이상) — 신용 정상이어도 위기 국면
    if dd <= -0.30:
        return REGIME_CRISIS

    # 4) 조정 시작 + breadth 악화 → Correction Watch
    breadth_weak = (breadth_pct is not None and breadth_pct < 0.40)
    if dd <= -0.05 and (breadth_weak or credit_stress == "elevated"):
        return REGIME_CORRECTION_WATCH

    # 5) overheat 기반
    if overheat is not None:
        if overheat >= 70:
            return REGIME_OVERHEATED
        if overheat >= 50:
            return REGIME_EXPENSIVE_STABLE

    # 6) 기본
    return REGIME_RISK_ON


# ---------------------------------------------------------------------------
# Rule-based 한국어 종합 코멘트
# ---------------------------------------------------------------------------

def generate_regime_commentary(overheat_result: dict,
                               regime: str,
                               credit: dict,
                               qqq_drawdown: float | None) -> str:
    """LLM 없이 규칙 기반 한국어 종합 코멘트 생성."""
    overheat = overheat_result.get("market_overheat_score")
    band = overheat_result.get("overheat_band_ko", NEEDS_CHECK)
    parts: list[str] = []

    regime_lead = {
        REGIME_RISK_ON: "현재 미국 시장은 위험선호 국면입니다. 추세를 활용할 수 있는 환경입니다.",
        REGIME_EXPENSIVE_STABLE: "현재 시장은 밸류에이션이 다소 높지만 구조적으로 안정적입니다. 신규 진입은 선별적으로.",
        REGIME_OVERHEATED: "현재 시장은 과열 국면입니다. 신규 베타 확대보다 차익 보호와 현금 옵션 확보가 우선입니다.",
        REGIME_CORRECTION_WATCH: "조정 경계 국면입니다. 시장 폭(breadth)이 약화되고 있어 방어적 태세가 필요합니다.",
        REGIME_DISLOCATION: "디스로케이션 국면입니다. 신용시장이 정상인 가운데 지수가 의미 있게 하락 — 단계적 분할 매수 기회 구간입니다.",
        REGIME_CRISIS: "위기 국면입니다. 신용 스트레스 또는 급락이 확인됩니다. 방어가 최우선이며 투입은 신용 안정 확인 후.",
    }
    parts.append(regime_lead.get(regime, "시장 국면을 판단 중입니다."))

    if overheat is not None:
        parts.append(f"Market Overheat Score 는 {overheat:.0f}/100 ({band}) 입니다.")
    else:
        parts.append(f"Overheat Score 산정에 필요한 데이터가 부족합니다 — {NEEDS_CHECK}.")

    if qqq_drawdown is not None:
        parts.append(f"나스닥(QQQ)은 52주 고점 대비 {qqq_drawdown * 100:+.1f}% 입니다.")

    cs = credit.get("status")
    if cs and cs != "unknown":
        parts.append(f"신용 상태: {credit.get('detail', '')}")

    missing = overheat_result.get("missing_subscores") or []
    if missing:
        labels = ", ".join(SUBSCORE_LABELS_KO.get(m, m) for m in missing)
        parts.append(f"※ 데이터 부족으로 가중치에서 제외된 항목: {labels} (나머지로 재정규화).")

    return " ".join(parts)


def build_market_regime(data: dict[str, Any]) -> dict[str, Any]:
    """Overheat Score → credit → regime → commentary 통합 빌드.

    파이프라인/UI 가 호출하는 단일 진입점. data 는 collect_regime_inputs() 출력.
    """
    data = data or {}
    fred = data.get("fred") or {}
    etf = data.get("etf") or {}

    overheat_result = calculate_market_overheat_score(data)

    qqq = etf.get("QQQ") or {}
    qqq_drawdown = qqq.get("drawdown_from_52w_high")

    credit = assess_credit_stress(fred, etf)
    regime = classify_market_regime(
        overheat_result.get("market_overheat_score"),
        qqq_drawdown,
        credit.get("status"),
        breadth_pct=overheat_result.get("breadth_pct"),
    )
    commentary = generate_regime_commentary(overheat_result, regime, credit, qqq_drawdown)

    return {
        **overheat_result,
        "current_regime": regime,
        "current_regime_ko": REGIME_KO.get(regime, regime),
        "qqq_drawdown_from_high": qqq_drawdown,
        "credit_stress_status": credit.get("status"),
        "credit_stress_detail": credit.get("detail"),
        "commentary_ko": commentary,
    }
