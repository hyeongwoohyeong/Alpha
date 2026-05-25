"""Parking Stock Score — Capital Efficiency 시스템 (Phase 2).

핵심 질문:
- 시장이 비싸 신규 베타를 늘리기 부담스럽지만 현금을 100% 놀리기도 싫을 때,
  잠시 자금을 '주차(parking)'해 둘 만한 방어적 quality stock 후보는 무엇인가.

중요 원칙:
- Parking stock 은 현금성 자산이 아니며 원금을 보장하지 않는다.
- 시장이 급락하면 parking stock 도 함께 하락할 수 있다.
- commentary / UI 에 항상 "Cash Alternative: No / Defensive Equity Parking: Yes"
  를 명확히 한다.

원칙 (Phase 1 market_regime.py 와 동일):
- Rule-based. LLM 없이 완전 동작.
- 입력 데이터 없는 sub-score 는 '확인 필요' 로 표시·가중치 제외·재정규화.
- 모든 함수는 예외를 던지지 않는다.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("parking_strategy")

NEEDS_CHECK = "확인 필요"

# Parking 후보 고정 유니버스 (방어적 quality)
PARKING_UNIVERSE: dict[str, str] = {
    "MCD": "맥도날드",
    "KO": "코카콜라",
    "PEP": "펩시코",
    "COST": "코스트코",
    "WMT": "월마트",
    "BRK-B": "버크셔 해서웨이",
    "V": "비자",
    "MA": "마스터카드",
    "UNH": "유나이티드헬스",
    "JNJ": "존슨앤드존슨",
    "PG": "프록터앤드갬블",
    "WM": "웨이스트 매니지먼트",
}

# Parking Stock Score sub-score 가중치 (합 = 1.0)
SUBSCORE_WEIGHTS: dict[str, float] = {
    "earnings_stability_score": 0.20,
    "brand_moat_score": 0.15,
    "low_beta_score": 0.15,
    "drawdown_resilience_score": 0.15,
    "dividend_buyback_score": 0.10,
    "valuation_reasonableness_score": 0.15,
    "technical_support_score": 0.10,
}

SUBSCORE_LABELS_KO: dict[str, str] = {
    "earnings_stability_score": "이익 안정성",
    "brand_moat_score": "브랜드·해자",
    "low_beta_score": "저베타",
    "drawdown_resilience_score": "낙폭 방어력",
    "dividend_buyback_score": "배당·자사주",
    "valuation_reasonableness_score": "밸류에이션 합리성",
    "technical_support_score": "기술적 지지",
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


def _estimate_beta(md: dict | None) -> float | None:
    """가격이력에서 beta 를 SPY 없이 변동성 기반으로 근사.

    정확한 beta(vs SPY 회귀)는 무료 데이터 한계로 어렵다 — 연환산 변동성을
    시장 기준 변동성(~16%)과 비교해 근사 beta 를 만든다.
    """
    if not md:
        return None
    hist = md.get("history")
    if hist is None:
        return None
    try:
        if "Close" not in hist.columns:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 60:
            return None
        rets = closes.pct_change().dropna().tail(252)
        if len(rets) < 40:
            return None
        vol = float(rets.std()) * (252 ** 0.5)
        # S&P500 장기 연환산 변동성 ≈ 0.16 기준 근사 beta
        return vol / 0.16
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sub-score 계산기
# ---------------------------------------------------------------------------

def _score_earnings_stability(md: dict) -> tuple[float | None, str]:
    """이익 안정성 — 영업이익률·ROE 수준으로 근사 (수익성 견고하면 안정적)."""
    parts: list[float] = []
    notes: list[str] = []
    om = _f(md.get("operating_margin"))
    if om is not None:
        parts.append(_lerp_score(om, 0.05, 0.35))
        notes.append(f"영업이익률 {om * 100:.0f}%")
    roe = _f(md.get("roe"))
    if roe is not None:
        parts.append(_lerp_score(roe, 0.05, 0.40))
        notes.append(f"ROE {roe * 100:.0f}%")
    if not parts:
        return None, f"수익성 데이터 미수집 — 이익 안정성 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("이익 안정성 낮음" if score < 35 else "보통" if score < 60
            else "이익 안정성 양호" if score < 80 else "이익 안정성 매우 견고")
    return score, "이익 안정성: " + desc + " — " + ", ".join(notes) + "."


def _score_brand_moat(md: dict, gross_margin: float | None) -> tuple[float | None, str]:
    """브랜드·해자 — gross margin 으로 가격결정력 근사."""
    gm = _f(gross_margin) if gross_margin is not None else _f(md.get("gross_margin"))
    if gm is None:
        return None, f"gross margin 미수집 — 브랜드·해자 {NEEDS_CHECK}."
    # gross margin 25%(약함)~70%(강한 가격결정력)
    score = _lerp_score(gm, 0.25, 0.70)
    desc = ("가격결정력 약함" if score < 35 else "보통" if score < 60
            else "브랜드·해자 양호" if score < 80 else "강한 해자")
    return score, f"브랜드·해자: {desc} — gross margin {gm * 100:.0f}%."


def _score_low_beta(md: dict) -> tuple[float | None, str, float | None]:
    """저베타 — 근사 beta 가 낮을수록 parking 에 유리. Returns (score, comment, beta)."""
    beta = _estimate_beta(md)
    if beta is None:
        return None, f"가격이력 부족 — beta 추정 불가, 저베타 {NEEDS_CHECK}.", None
    # 근사 beta 0.5(저변동, 좋음)~1.6(고변동) — 낮을수록 높은 점수
    score = _lerp_score(beta, 1.6, 0.5)
    desc = ("변동성 높음(parking 부적합)" if score < 35 else "보통" if score < 60
            else "저베타 양호" if score < 80 else "매우 안정적")
    return score, f"저베타: {desc} — 근사 beta {beta:.2f} (변동성 기반 추정).", beta


def _score_drawdown_resilience(md: dict) -> tuple[float | None, str]:
    """낙폭 방어력 — 52주 고점 대비 낙폭이 얕을수록 방어력 양호."""
    dd = _f(md.get("drawdown_from_52w_high"))
    if dd is None:
        return None, f"52주 낙폭 데이터 미수집 — 낙폭 방어력 {NEEDS_CHECK}."
    # 낙폭 -35%(취약)~0%(견고)
    score = _lerp_score(dd, -0.35, 0.0)
    desc = ("낙폭 취약" if score < 35 else "보통" if score < 60
            else "낙폭 방어 양호" if score < 80 else "낙폭 방어 견고")
    return score, f"낙폭 방어력: {desc} — 52주 고점 대비 {dd * 100:+.0f}%."


def _score_dividend_buyback(md: dict) -> tuple[float | None, str]:
    """배당·자사주 — FCF yield 로 주주환원 여력 근사 (직접 배당률은 확인 필요)."""
    fcfy = _f(md.get("fcf_yield"))
    if fcfy is None:
        return None, (
            f"FCF yield·배당 데이터 미수집 — 배당·자사주 {NEEDS_CHECK}. "
            "직접 배당수익률은 별도 확인 필요."
        )
    # FCF yield 1%(여력 작음)~7%(여력 큼)
    score = _lerp_score(fcfy, 0.01, 0.07)
    desc = ("주주환원 여력 작음" if score < 35 else "보통" if score < 60
            else "주주환원 여력 양호" if score < 80 else "주주환원 여력 큼")
    return score, (
        f"배당·자사주: {desc} — FCF yield {fcfy * 100:.1f}% (근사). "
        f"실제 배당수익률은 {NEEDS_CHECK}."
    )


def _score_valuation_reasonableness(md: dict) -> tuple[float | None, str]:
    """밸류에이션 합리성 — forward PE 가 과하지 않으면 parking 에 유리."""
    fpe = _f(md.get("forward_pe"))
    if fpe is None or fpe <= 0 or fpe > 300:
        return None, f"forward PE 미수집 — 밸류에이션 합리성 {NEEDS_CHECK}."
    # forward PE 35(비쌈)~12(합리적) — 낮을수록 높은 점수
    score = _lerp_score(fpe, 35.0, 12.0)
    desc = ("밸류에이션 부담 큼" if score < 35 else "보통" if score < 60
            else "밸류에이션 합리적" if score < 80 else "밸류에이션 매력적")
    return score, f"밸류에이션 합리성: {desc} — forward PE {fpe:.0f}x."


def _score_technical_support(md: dict) -> tuple[float | None, str]:
    """기술적 지지 — 단기 수익률이 과도하게 빠지지 않고 안정적이면 양호."""
    parts: list[float] = []
    notes: list[str] = []
    r3m = _f(md.get("3m_return"))
    if r3m is not None:
        # -20%(붕괴)~+15%(완만 상승) — 너무 급등도 parking 의미상 중립
        parts.append(_lerp_score(r3m, -0.20, 0.15))
        notes.append(f"3개월 {r3m * 100:+.0f}%")
    r1m = _f(md.get("1m_return"))
    if r1m is not None:
        parts.append(_lerp_score(r1m, -0.12, 0.08))
        notes.append(f"1개월 {r1m * 100:+.0f}%")
    if not parts:
        return None, f"가격 추세 데이터 미수집 — 기술적 지지 {NEEDS_CHECK}."
    score = sum(parts) / len(parts)
    desc = ("기술적 약세(지지 불안)" if score < 35 else "보통" if score < 60
            else "기술적 지지 안정" if score < 80 else "기술적 견고")
    return score, "기술적 지지: " + desc + " — " + ", ".join(notes) + "."


# ---------------------------------------------------------------------------
# 점수대
# ---------------------------------------------------------------------------

def _parking_band_ko(score: float) -> str:
    if score < 30:
        return "부적합"
    if score < 50:
        return "보통"
    if score < 70:
        return "검토 가능"
    if score < 85:
        return "양호한 파킹 후보"
    return "우수한 파킹 후보"


# ---------------------------------------------------------------------------
# Parking Stock Score — 메인
# ---------------------------------------------------------------------------

def calculate_parking_stock_score(stock: dict[str, Any]) -> dict[str, Any]:
    """Parking Stock Score 0~100 산정.

    stock: ticker / market_data dict (또는 market_data 자체).

    Returns dict: parking_score, parking_band_ko, beta, 각 *_score,
    각 *_commentary_ko, used_weights, missing_subscores, why_parking_ko, risk_ko.
    """
    stock = stock or {}
    md = _md_of(stock)

    low_beta_score, low_beta_comment, beta = _score_low_beta(md)

    calculators = {
        "earnings_stability_score": lambda: _score_earnings_stability(md),
        "brand_moat_score": lambda: _score_brand_moat(md, md.get("gross_margin")),
        "low_beta_score": lambda: (low_beta_score, low_beta_comment),
        "drawdown_resilience_score": lambda: _score_drawdown_resilience(md),
        "dividend_buyback_score": lambda: _score_dividend_buyback(md),
        "valuation_reasonableness_score": lambda: _score_valuation_reasonableness(md),
        "technical_support_score": lambda: _score_technical_support(md),
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
        parking = sum(available[k] * used_weights[k] for k in available)
        parking = round(_clamp(parking), 1)
    else:
        used_weights = {}
        parking = None

    band = _parking_band_ko(parking) if parking is not None else NEEDS_CHECK

    result: dict[str, Any] = {
        "parking_score": parking,
        "parking_band_ko": band,
        "beta": round(beta, 2) if beta is not None else None,
        "used_weights": used_weights,
        "missing_subscores": missing,
    }
    for key in calculators:
        result[f"{key}"] = sub_scores[key]
        result[f"{key}_commentary_ko"] = commentaries[key]

    why, risk = generate_parking_commentary(result, stock)
    result["why_parking_ko"] = why
    result["risk_ko"] = risk
    return result


def generate_parking_commentary(
    result: dict[str, Any], stock: dict
) -> tuple[str, str]:
    """LLM 없이 규칙 기반 (왜 파킹 후보인가, 리스크) 코멘트 생성.

    Returns: (why_parking_ko, risk_ko)
    """
    score = result.get("parking_score")
    band = result.get("parking_band_ko", NEEDS_CHECK)
    beta = result.get("beta")

    if score is None:
        why = (
            "Parking Stock Score 산정에 필요한 데이터가 부족합니다 — 확인 필요."
        )
    else:
        band_lead = {
            "부적합": "방어적 파킹 용도로는 적합하지 않습니다.",
            "보통": "방어적 파킹 후보로 보통 수준입니다.",
            "검토 가능": "방어적 파킹 후보로 검토 가능한 수준입니다.",
            "양호한 파킹 후보": "이익 안정성·낙폭 방어력 측면에서 양호한 방어적 파킹 후보입니다.",
            "우수한 파킹 후보": "이익 안정성·해자·저베타 측면에서 우수한 방어적 파킹 후보입니다.",
        }
        why = (
            f"Parking Stock Score {score:.0f}/100 ({band}). "
            + band_lead.get(band, "")
            + (f" 근사 beta {beta:.2f} — 시장 대비 변동성이 낮은 편입니다." if beta is not None else "")
        )

    # 리스크 — 항상 명시
    risk = (
        "주의: Parking Stock 은 현금성 자산이 아니며 원금을 보장하지 않습니다 "
        "(Cash Alternative: No / Defensive Equity Parking: Yes). "
        "시장이 급락하면 이 종목들도 함께 하락할 수 있으며, 변동성이 낮을 뿐 "
        "손실 가능성이 없는 것은 아닙니다. 현금 그 자체의 대체재가 아니라, "
        "'현금을 100% 놀리기 싫을 때 변동성을 낮춰 임시로 자금을 두는 방어적 주식' "
        "이라는 점을 분명히 이해하고 사용하십시오."
    )
    missing = result.get("missing_subscores") or []
    if missing:
        labels = ", ".join(SUBSCORE_LABELS_KO.get(m, m) for m in missing)
        risk += f" ※ 데이터 부족으로 가중치 제외 항목: {labels} (나머지로 재정규화)."
    return why, risk


def screen_parking_candidates(
    market_data_map: dict[str, dict] | None = None
) -> list[dict[str, Any]]:
    """Parking 후보 고정 유니버스를 fetch + 점수화.

    market_data_map: {ticker: market_data dict} 가 주어지면 그것을 사용 (파이프라인용).
                     None 이면 직접 yfinance 로 fetch (graceful — 실패 시 빈 list).

    Returns: parking_score 내림차순 정렬된 후보 dict list.
    """
    tickers = list(PARKING_UNIVERSE.keys())

    if market_data_map is None:
        try:
            from .market_data import fetch_universe
            market_data_map = fetch_universe(tickers, period="2y", enrich=True)
        except Exception as e:
            log.warning("parking 유니버스 fetch 실패: %s", e)
            market_data_map = {}

    candidates: list[dict[str, Any]] = []
    for ticker in tickers:
        md = (market_data_map or {}).get(ticker) or {}
        try:
            scored = calculate_parking_stock_score({"ticker": ticker, "market_data": md})
        except Exception as e:
            log.warning("[%s] parking score 계산 실패: %s", ticker, e)
            continue
        scored["ticker"] = ticker
        scored["name"] = PARKING_UNIVERSE.get(ticker, ticker)
        candidates.append(scored)

    # parking_score 내림차순 (None 은 뒤로)
    candidates.sort(
        key=lambda c: (c.get("parking_score") is not None, c.get("parking_score") or 0.0),
        reverse=True,
    )
    return candidates
