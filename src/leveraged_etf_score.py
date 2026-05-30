"""Leveraged ETF Suitability Score (LESS).

목적: 어떤 종목의 single-stock 2X ETF 를 전술적으로 사용할 만한지 정량 판정.
"2X ETF 가 있다 ≠ 좋은 회사" 라는 사용자 원칙을 enforced. 본주 quality + setup + regime
모두 통과해야만 high score.

설계:
- 7가지 sub-score (사용자 spec 가중치 그대로)
- capital_efficiency 모듈 재사용 (Catalyst, Liquidity, CapEff 일부) — 통일성 원칙
- Entry 10조건 / Block 8조건 룰 엔진 — 프로그램으로 확인 가능한 5+3 자동, 나머지는 external_check 로 surface

API:
- score_leveraged_etf(row, qld_ctx, regime, market_overheat) → dict
- Returns: {
    score, verdict, sub_scores, weights,
    entry_checks: [{rule, passed, value, note}],
    block_flags:  [{rule, triggered, value, note}],
    external_checks: [...],
    summary_ko,
    underlying_ticker, leveraged_etf_tickers,
  }
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger
from . import capital_efficiency as ceff

log = get_logger("leveraged_etf_score")


# ---------------------------------------------------------------------------
# Sub-score weights (사용자 spec 그대로)
# ---------------------------------------------------------------------------

LESS_WEIGHTS: dict[str, float] = {
    "underlying_quality":  0.20,
    "capital_efficiency":  0.20,
    "catalyst_visibility": 0.15,
    "price_dislocation":   0.15,
    "liquidity":           0.15,
    "regime_fit":          0.10,
    "risk_control":        0.05,
}

# Verdict 임계
VERDICT_BANDS = [
    (80, "2X ETF 전술 진입 검토 가능"),
    (60, "본주 우선, 2X 는 소액만 검토"),
    (40, "Watchlist"),
    (0,  "2X ETF 부적합"),
]


# ---------------------------------------------------------------------------
# 보조
# ---------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp(val: float, low: float, high: float, lo_score: float = 0.0, hi_score: float = 100.0) -> float:
    """val ∈ [low, high] → [lo_score, hi_score]."""
    if high == low:
        return (lo_score + hi_score) / 2
    t = (val - low) / (high - low)
    return lo_score + t * (hi_score - lo_score)


def verdict_for(score: float | None) -> str:
    if score is None:
        return "데이터 부족"
    for threshold, label in VERDICT_BANDS:
        if score >= threshold:
            return label
    return "2X ETF 부적합"


# ---------------------------------------------------------------------------
# Sub-score 계산
# ---------------------------------------------------------------------------

def _score_underlying_quality(row: dict) -> tuple[float | None, str]:
    """본주 quality — alpha score 직접 사용."""
    scores = row.get("scores") or {}
    final = _f(scores.get("final_score"))
    if final is None:
        return None, "Alpha Score 미수집 — Underlying Quality 확인 필요."
    # alpha 80+ = excellent, 60- = poor
    score = _clamp(_lerp(final, 50, 90))
    return score, f"본주 Alpha Score {final:.0f}/100 → Quality 점수 {score:.0f}/100."


def _score_capital_efficiency(md: dict) -> tuple[float | None, str]:
    """LESS 의 Capital Efficiency = 기대수익+목표확률+하방리스크 sub-composite.

    catalyst / liquidity / QLD 는 LESS 의 다른 컴포넌트로 분리 → 중복 카운트 방지.
    """
    er_score, er_note = ceff._score_expected_return(md)
    tt_score, tt_note = ceff.estimate_time_to_target_probability(md)
    dr_score, dr_note = ceff.calculate_downside_risk_score(md)
    available = [(s, w) for s, w in [(er_score, 0.4), (tt_score, 0.3), (dr_score, 0.3)] if s is not None]
    if not available:
        return None, "Capital Efficiency sub-components 데이터 부족."
    total_w = sum(w for _, w in available)
    composite = sum(s * (w / total_w) for s, w in available)
    return _clamp(composite), (
        f"기대수익·목표확률·하방리스크 가중 평균 {composite:.0f}/100."
    )


def _score_catalyst(row: dict, md: dict) -> tuple[float | None, str]:
    """cap_eff 의 catalyst_visibility 재사용."""
    return ceff._score_catalyst_visibility(row, md)


def _score_price_dislocation(md: dict) -> tuple[float | None, str]:
    """가격 dislocation — DD sweet spot -15~-30% 가 최고점.

    -5% 이내 (=고점권): 추격 매수 의미 없음 → 낮은 점수
    -15~-30%: 매력 sweet spot → 90+
    -30~-50%: 더 깊지만 thesis 훼손 risk 증가 → 60~80
    -50% 이하: 깊은 panic, 또는 진짜 망 → 분기 필요
    """
    dd = _f(md.get("drawdown_from_52w_high"))
    if dd is None:
        return None, "52주 drawdown 데이터 없음."
    # dd 는 음수
    a = abs(dd) * 100  # 양수 %
    if a < 5:
        score = 25
        tone = "고점권 (추격 매수 부적합)"
    elif a < 15:
        score = _lerp(a, 5, 15, 25, 70)
        tone = "조정 시작"
    elif a <= 30:
        score = _lerp(a, 15, 30, 70, 95)
        tone = "Sweet spot (15~30%)"
    elif a <= 50:
        score = _lerp(a, 30, 50, 95, 60)
        tone = "깊은 조정 — thesis 훼손 risk"
    else:
        score = _lerp(a, 50, 80, 60, 30)
        tone = "Panic / 구조 의심"
    return _clamp(score), f"52주 고점 대비 -{a:.0f}% — {tone}."


def _score_liquidity(md: dict) -> tuple[float | None, str]:
    """cap_eff 의 liquidity_exit 재사용."""
    return ceff._score_liquidity_exit(md)


def _score_regime_fit(market_overheat: float | None) -> tuple[float | None, str]:
    """Market regime fit — overheat 가 낮을수록 2X ETF 진입 적합.

    overheat ≤ 30 (저평가): 90+ (공격 적기)
    overheat 30-50: 70-90 (정상)
    overheat 50-70: 40-70 (조심)
    overheat > 70 (과열): 0-40 (2X 금지 zone)
    """
    if market_overheat is None:
        return 50.0, "시장 과열 점수 없음 — 중립 fallback 50."
    oh = market_overheat
    if oh <= 30:
        score = _lerp(oh, 0, 30, 95, 80)
        tone = "저평가 — 공격 적기"
    elif oh <= 50:
        score = _lerp(oh, 30, 50, 80, 65)
        tone = "정상 구간"
    elif oh <= 70:
        score = _lerp(oh, 50, 70, 65, 35)
        tone = "조심 — 2X 제한적"
    else:
        score = _lerp(oh, 70, 100, 35, 5)
        tone = "과열 — 2X 진입 금지 zone"
    return _clamp(score), f"시장 과열 {oh:.0f} — {tone}."


def _score_risk_control(md: dict, row: dict) -> tuple[float | None, str]:
    """Risk Control — valuation + vol + news risk 종합 (5% 비중 가벼움)."""
    bits: list[str] = []
    parts: list[float] = []

    pe = _f(md.get("forward_pe")) or _f(md.get("trailing_pe"))
    if pe is not None and pe > 0:
        # PE 15 이하 = quality, 60 이상 = stretch
        score = _clamp(_lerp(pe, 15, 60, 95, 25))
        parts.append(score)
        bits.append(f"PE {pe:.0f}x")

    vol = _f(md.get("annual_vol")) or _f(md.get("vol_30d"))
    if vol is not None:
        # vol 30% 이하 = controllable, 80% 이상 = wild
        score = _clamp(_lerp(vol * 100 if vol < 1 else vol, 30, 80, 90, 30))
        parts.append(score)
        bits.append(f"vol {vol * 100 if vol < 1 else vol:.0f}%")

    # 뉴스 risk 차감
    na = row.get("news_agg") or {}
    if na.get("urgent"):
        parts.append(20.0)  # 회계/조사 등 urgent news 강한 risk
        bits.append("urgent news")
    elif na.get("negative"):
        parts.append(50.0)
        bits.append("negative news")

    if not parts:
        return None, "Risk control 데이터 부족."
    score = sum(parts) / len(parts)
    return _clamp(score), f"Risk Control: {', '.join(bits)} 평균 {score:.0f}/100."


# ---------------------------------------------------------------------------
# Entry / Block 룰 엔진
# ---------------------------------------------------------------------------

def _check_entry_rules(
    row: dict, md: dict, regime: Any, qld_view: str, sub_scores: dict, market_overheat: float | None,
) -> list[dict]:
    """Entry 10조건 (프로그램 체크 가능한 것 + external 필요한 것)."""
    final_score = _f((row.get("scores") or {}).get("final_score"))
    dd = _f(md.get("drawdown_from_52w_high"))
    dd_pct = abs(dd) * 100 if dd is not None else None
    na = row.get("news_agg") or {}
    catalyst = sub_scores.get("catalyst_visibility")
    liquidity = sub_scores.get("liquidity")
    cap_eff = sub_scores.get("capital_efficiency")
    regime_label = _regime_label(regime)

    checks = [
        {
            "rule": "1. 본주 Alpha Score ≥ 75",
            "passed": (final_score is not None and final_score >= 75),
            "value": f"{final_score:.0f}" if final_score is not None else "데이터 없음",
        },
        {
            "rule": "2. Capital Efficiency ≥ 70",
            "passed": (cap_eff is not None and cap_eff >= 70),
            "value": f"{cap_eff:.0f}" if cap_eff is not None else "데이터 없음",
        },
        {
            "rule": "3. QLD Relative View ∈ {Better, Similar}",
            "passed": qld_view in ("Better than QLD", "Similar to QLD"),
            "value": qld_view or "데이터 없음",
        },
        {
            "rule": "4. 본주 DD -15% ~ -30%",
            "passed": (dd_pct is not None and 15 <= dd_pct <= 30),
            "value": f"-{dd_pct:.0f}%" if dd_pct is not None else "데이터 없음",
        },
        {
            "rule": "5. Thesis 훼손 신호 없음 (urgent news 없음)",
            "passed": not bool(na.get("urgent")),
            "value": "urgent news" if na.get("urgent") else "정상",
        },
        {
            "rule": "6. 1~6M 내 catalyst 존재 (Catalyst Visibility ≥ 50)",
            "passed": (catalyst is not None and catalyst >= 50),
            "value": f"{catalyst:.0f}" if catalyst is not None else "데이터 없음",
        },
        {
            "rule": "7. 시장 국면 = Risk-On / Pullback / Dislocation",
            "passed": regime_label in ("Risk-On", "Pullback in Uptrend", "Dislocation"),
            "value": regime_label or "데이터 없음",
        },
        {
            "rule": "8. 유동성·청산 용이성 ≥ 50",
            "passed": (liquidity is not None and liquidity >= 50),
            "value": f"{liquidity:.0f}" if liquidity is not None else "데이터 없음",
        },
        {
            "rule": "9. 손절·리뷰 기준 명확 (외부 입력 필요)",
            "passed": None,  # 사용자 수동 확인
            "value": "사용자 확인 필요",
        },
        {
            "rule": "10. 포트폴리오 레버리지 총량 이내 (외부 입력 필요)",
            "passed": None,
            "value": "사용자 확인 필요",
        },
    ]
    return checks


def _check_block_rules(
    row: dict, md: dict, regime: Any, market_overheat: float | None,
) -> list[dict]:
    """Block 8조건 — 하나라도 trigger 되면 신규 진입 금지."""
    dd = _f(md.get("drawdown_from_52w_high"))
    r6m = _f(md.get("6m_return"))
    r3m = _f(md.get("3m_return"))
    pe = _f(md.get("forward_pe")) or _f(md.get("trailing_pe"))
    na = row.get("news_agg") or {}
    regime_label = _regime_label(regime)

    # parabolic: 3M > +60% 또는 6M > +100% (고점에서 급등)
    parabolic = (r3m is not None and r3m > 0.6) or (r6m is not None and r6m > 1.0)

    flags = [
        {
            "rule": "1. 시장 국면 = Overheated / Casino",
            "triggered": (regime_label in ("Overheated", "Casino Market")
                          or (market_overheat is not None and market_overheat >= 75)),
            "value": regime_label or (f"overheat {market_overheat:.0f}" if market_overheat else "—"),
        },
        {
            "rule": "2. 본주 parabolic 급등 직후 (3M > +60% or 6M > +100%)",
            "triggered": parabolic,
            "value": (f"3M {r3m * 100:+.0f}%" if r3m is not None else "—")
                     + (f", 6M {r6m * 100:+.0f}%" if r6m is not None else ""),
        },
        {
            "rule": "3. Valuation stretch (PE > 60)",
            "triggered": (pe is not None and pe > 60),
            "value": f"PE {pe:.0f}x" if pe is not None else "데이터 없음",
        },
        {
            "rule": "4. 거래량/유동성 부족",
            "triggered": False,  # liquidity score 가 직접 evaluation — 별도 sub-score 에 반영
            "value": "liquidity sub-score 확인",
        },
        {
            "rule": "5. 스프레드 과도 (외부 확인 필요)",
            "triggered": None,
            "value": "거래소 호가 확인 필요",
        },
        {
            "rule": "6. 회계/희석/규제 리스크 (urgent news)",
            "triggered": bool(na.get("urgent")),
            "value": "urgent news" if na.get("urgent") else "정상",
        },
        {
            "rule": "7. 손절·리뷰 기준 없음 (외부 확인 필요)",
            "triggered": None,
            "value": "사용자 확인 필요",
        },
        {
            "rule": "8. 포트폴리오 레버리지 노출 과도 (외부 확인 필요)",
            "triggered": None,
            "value": "사용자 확인 필요",
        },
    ]
    return flags


def _regime_label(regime: Any) -> str:
    """regime 객체에서 'current_regime' 라벨 추출 — sqlite Row / dict 모두 대응."""
    if regime is None:
        return ""
    try:
        if hasattr(regime, "__getitem__"):
            try:
                return str(regime["current_regime"]) or ""
            except Exception:
                pass
        if isinstance(regime, dict):
            return str(regime.get("current_regime") or "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 메인 API
# ---------------------------------------------------------------------------

def score_leveraged_etf(
    row: dict[str, Any],
    qld_ctx: dict[str, Any] | None = None,
    regime: Any = None,
    market_overheat: float | None = None,
) -> dict[str, Any]:
    """LESS 메인 — sub-score 7개 + entry/block 룰 + verdict.

    Args:
        row: engine universe row (ticker, name_ko, scores, market_data, news_agg, curated_events)
        qld_ctx: QLD row/market_data (QLD relative view 산정용)
        regime: 시장 국면 row (current_regime, market_overheat_score)
        market_overheat: 명시 전달 시 regime 의 값 override
    """
    md = row.get("market_data") or {}
    ticker = (row.get("ticker") or "").upper()

    # market_overheat 보조: regime 에서 자동 추출
    if market_overheat is None and regime is not None:
        try:
            if hasattr(regime, "__getitem__"):
                market_overheat = _f(regime["market_overheat_score"])
            elif isinstance(regime, dict):
                market_overheat = _f(regime.get("market_overheat_score"))
        except Exception:
            pass

    # QLD relative view
    qld_score_raw, qld_comment, qld_view = ceff.calculate_qld_relative_attractiveness(md, qld_ctx)

    # Sub-scores 계산
    sub_funcs = {
        "underlying_quality":   lambda: _score_underlying_quality(row),
        "capital_efficiency":   lambda: _score_capital_efficiency(md),
        "catalyst_visibility":  lambda: _score_catalyst(row, md),
        "price_dislocation":    lambda: _score_price_dislocation(md),
        "liquidity":            lambda: _score_liquidity(md),
        "regime_fit":           lambda: _score_regime_fit(market_overheat),
        "risk_control":         lambda: _score_risk_control(md, row),
    }
    sub_scores: dict[str, float | None] = {}
    sub_comments: dict[str, str] = {}
    for k, fn in sub_funcs.items():
        try:
            s, c = fn()
        except Exception as e:
            log.warning("LESS sub-score %s 실패: %s", k, e)
            s, c = None, f"{k} 계산 실패 — {e}"
        sub_scores[k] = round(_clamp(s), 1) if s is not None else None
        sub_comments[k] = c

    # 가용 sub-score 만으로 가중 평균 (재정규화)
    available = {k: v for k, v in sub_scores.items() if v is not None}
    missing = [k for k, v in sub_scores.items() if v is None]
    if available:
        total_w = sum(LESS_WEIGHTS[k] for k in available)
        composite = sum(available[k] * LESS_WEIGHTS[k] / total_w for k in available)
        composite = round(_clamp(composite), 1)
    else:
        composite = None

    verdict = verdict_for(composite)

    # Rule engine
    entry_checks = _check_entry_rules(row, md, regime, qld_view, sub_scores, market_overheat)
    block_flags = _check_block_rules(row, md, regime, market_overheat)

    # External 검토 항목
    external = [
        e["rule"] for e in entry_checks if e["passed"] is None
    ] + [b["rule"] for b in block_flags if b["triggered"] is None]

    # 본주의 2X ETF tickers (leveraged_etf_map.json 조회)
    try:
        from .universe_taxonomy import get_leveraged_etf_tickers
        lev_tickers = get_leveraged_etf_tickers(ticker)
    except Exception:
        lev_tickers = []

    # 합성 요약 — 사용자 언어로 간결하게
    passed = sum(1 for e in entry_checks if e["passed"] is True)
    auto_entry = sum(1 for e in entry_checks if e["passed"] is not None)  # 자동 체크 가능 건수
    blocked = [b for b in block_flags if b["triggered"] is True]
    if blocked:
        summary = f"진입 차단 — {blocked[0]['rule'].split('. ', 1)[-1]}"
    elif composite is None:
        summary = "데이터 부족 — 산정 불가"
    elif composite >= 80:
        summary = f"진입 검토 가능 (자동 {passed}/{auto_entry} 통과). 손절·lev budget 확인 후 단계 진입."
    elif composite >= 60:
        summary = f"본주 우선 — 2X 는 소액만 검토 ({composite:.0f}/100). setup 더 깊어지면 재평가."
    elif composite >= 40:
        summary = f"Watchlist — 현 setup 부족 ({composite:.0f}/100)."
    else:
        summary = f"2X ETF 부적합 ({composite:.0f}/100)."

    return {
        "underlying_ticker": ticker,
        "leveraged_etf_tickers": lev_tickers,
        "score": composite,
        "verdict": verdict,
        "sub_scores": sub_scores,
        "sub_comments": sub_comments,
        "weights": LESS_WEIGHTS,
        "missing_subscores": missing,
        "qld_view": qld_view,
        "qld_comment": qld_comment,
        "entry_checks": entry_checks,
        "block_flags": block_flags,
        "external_checks": external,
        "summary_ko": summary,
    }
