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

유니버스 도출 원칙 (2026-05 개편):
- 더 이상 고정 12개 종목만 보지 않는다 — 시장을 '리서치' 해 후보 선정.
- 1) 항상 포함: 방어적 ETF whitelist (SCHD/VYM/USMV/SPLV/VTV/VIG/NOBL/BIL/SHV/SGOV)
  — 카테고리 자체가 방어/인컴/단기채여서 유니버스 스캔이 놓쳐도 무조건 후보.
- 2) data/wide_universe.csv 에서 방어적 quality 게이트 통과 종목 필터링
  (large-cap + 방어적 섹터: Consumer Defensive / Healthcare / Utilities /
  성숙 large-cap Tech). 메타데이터 부족 시 게이트 완화 — graceful.
- 3) 기존 12개 PARKING_UNIVERSE 는 (1)+(2) 가 너무 적을 때만 fallback.
- 결과는 dedupe 후 50~150개 정도 — 너무 좁지도 너무 넓지도 않게.
"""
from __future__ import annotations

import csv
from typing import Any

from .utils import WIDE_UNIVERSE_CSV, get_logger

log = get_logger("parking_strategy")

NEEDS_CHECK = "확인 필요"

# Parking 후보 fallback 유니버스 (방어적 quality)
# NOTE: 더 이상 메인 유니버스가 아니다 — derive_parking_universe() 가 자체
# 도출에 실패해(예: wide_universe.csv 부재) 후보가 매우 적을 때만 fallback.
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

# ── (1) 항상 포함하는 방어적 ETF whitelist ─────────────────────────────
# 카테고리 자체가 방어적이라 정량 스캔에서 누락돼도 parking 후보 풀에 넣어야 함.
# - SCHD/VYM/VIG/NOBL: 배당성장·고배당 (Equity Income)
# - VTV: large-cap Value
# - USMV/SPLV: low-volatility / minimum-volatility factor
# - BIL/SHV/SGOV: 1~3M 미국 국채 — 현금 등가물(parking 의 가장 안전한 형태)
DEFENSIVE_ETF_WHITELIST: dict[str, str] = {
    "SCHD": "Schwab US Dividend Equity ETF",
    "VYM": "Vanguard High Dividend Yield ETF",
    "USMV": "iShares MSCI USA Min Vol ETF",
    "SPLV": "Invesco S&P 500 Low Volatility ETF",
    "VTV": "Vanguard Value ETF",
    "VIG": "Vanguard Dividend Appreciation ETF",
    "NOBL": "ProShares S&P 500 Dividend Aristocrats ETF",
    "BIL": "SPDR Bloomberg 1-3 Month T-Bill ETF",
    "SHV": "iShares Short Treasury Bond ETF",
    "SGOV": "iShares 0-3 Month Treasury Bond ETF",
}

# Cash-equivalent 단기채 ETF 그룹 — Crisis/Dislocation 국면에서 bonus 적용
SHORT_DURATION_BOND_ETFS: frozenset[str] = frozenset({"BIL", "SHV", "SGOV"})

# wide_universe.csv 에서 방어적으로 인정할 섹터
DEFENSIVE_SECTORS: frozenset[str] = frozenset({
    "Consumer Defensive",
    "Consumer Staples",
    "Healthcare",
    "Health Care",
    "Utilities",
})

# 성숙 large-cap Tech 의 일부 산업(가격결정력·현금흐름 견고) — 보조 게이트
# NOTE: Semiconductors 는 일부 cash-cow 가 있지만 sector-wide beta 가 높아
# 명시 화이트리스트로만 통과시킨다. 일반 Tech 산업은 Software 만 게이트 통과,
# 그 안에서도 mature 대장주(아래 화이트리스트)만 허용.
DEFENSIVE_TECH_INDUSTRIES: frozenset[str] = frozenset({
    "Software",
})
# 성숙 large-cap Tech 화이트리스트 — 안정적 현금흐름·낮은 변동성
# (이름은 정량 메타에서 확인 어렵기에 보수적으로 명시 — 추가 시 신중히)
MATURE_LARGE_CAP_TECH_WHITELIST: frozenset[str] = frozenset({
    "MSFT",   # 운영체제·클라우드, 배당 지속
    "AAPL",   # 거대 cash flow, 자사주
    "GOOGL",  # 광고 cash cow, 다만 베타 다소 있음
    "ORCL",   # 소프트웨어 cash flow
    "CSCO",   # 통신 장비, 배당
    "IBM",    # 배당주
    "TXN",    # 반도체 cash cow + 고배당
    "AVGO",   # 반도체 + 강한 배당 — 베타 다소 있음
})

# Financial 의 일부 — 결제 네트워크는 방어적 quality (V/MA)
DEFENSIVE_FINANCIAL_INDUSTRIES: frozenset[str] = frozenset({
    "Credit Services",
    "Insurance",
})

# Consumer Discretionary 에서 방어 성격이 강한 일부 (Home Improvement/Discount 등)
DEFENSIVE_DISCRETIONARY_INDUSTRIES: frozenset[str] = frozenset({
    "Home Improvement",
    "Discount Stores",
    "Restaurants",
})

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

def calculate_parking_stock_score(
    stock: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Parking Stock Score 0~100 산정.

    stock: ticker / market_data dict (또는 market_data 자체).
    weights: regime-aware 로 조정한 가중치 dict. None 이면 기본 SUBSCORE_WEIGHTS.

    Returns dict: parking_score, parking_band_ko, beta, 각 *_score,
    각 *_commentary_ko, used_weights, missing_subscores, why_parking_ko, risk_ko.
    """
    stock = stock or {}
    md = _md_of(stock)
    weight_table = weights if weights is not None else SUBSCORE_WEIGHTS

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
        total_w = sum(weight_table.get(k, SUBSCORE_WEIGHTS[k]) for k in available)
        if total_w <= 0:
            total_w = 1.0
        used_weights = {
            k: weight_table.get(k, SUBSCORE_WEIGHTS[k]) / total_w
            for k in available
        }
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


def derive_parking_universe(
    *, max_candidates: int = 150, min_fallback_threshold: int = 20,
) -> dict[str, str]:
    """Parking 후보 유니버스를 시장 메타데이터로부터 도출.

    파이프라인:
      1) DEFENSIVE_ETF_WHITELIST — 무조건 포함 (10종 안팎).
      2) data/wide_universe.csv 에서 방어적 quality 게이트 통과 종목 필터링:
         - is_active=1, is_etf=0, is_spac=0 (universe.py 와 동일)
         - market_cap_tier == 'large' (소형주 제외)
         - sector 가 DEFENSIVE_SECTORS 에 속함, 또는
         - Financial Services + Credit Services/Insurance (V·MA·BRK 류), 또는
         - Consumer Discretionary + 방어 성격 산업 (MCD·HD·COST 류), 또는
         - Technology + 성숙 large-cap (대형 cash flow 머신 일부).
      3) (1)+(2) 결과가 min_fallback_threshold 보다 적으면 PARKING_UNIVERSE 추가.
      4) dedupe 후 max_candidates 까지만 유지.

    데이터/필드 부족은 절대 예외로 던지지 않고 graceful 하게 fallback.

    Returns: {ticker: display_name} dict.
    """
    derived: dict[str, str] = {}

    # 1) ETF whitelist — 항상 포함
    derived.update(DEFENSIVE_ETF_WHITELIST)

    # 2) wide_universe.csv 필터링
    if WIDE_UNIVERSE_CSV.exists():
        try:
            with WIDE_UNIVERSE_CSV.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    ticker = (r.get("ticker") or "").strip().upper()
                    if not ticker:
                        continue
                    if (r.get("is_active") or "1").strip() == "0":
                        continue
                    if (r.get("is_etf") or "0").strip() == "1":
                        continue
                    if (r.get("is_spac") or "0").strip() == "1":
                        continue
                    tier = (r.get("market_cap_tier") or "").strip().lower()
                    sector = (r.get("sector") or "").strip()
                    industry = (r.get("industry") or "").strip()
                    name = (r.get("name") or "").strip() or ticker

                    # large-cap 게이트 — tier 가 비어있으면 게이트를 우회 (graceful)
                    if tier and tier != "large":
                        continue

                    # 섹터/산업 게이트
                    passes = False
                    if sector in DEFENSIVE_SECTORS:
                        passes = True
                    elif sector == "Financial Services" and (
                        industry in DEFENSIVE_FINANCIAL_INDUSTRIES
                    ):
                        passes = True
                    elif sector == "Consumer Discretionary" and (
                        industry in DEFENSIVE_DISCRETIONARY_INDUSTRIES
                    ):
                        passes = True
                    elif sector == "Technology":
                        # Tech 는 large-cap 이면서 (Software 산업 OR 명시
                        # mature-large-cap 화이트리스트) 인 경우만 통과 —
                        # 베타 데이터 없이는 보수적으로 거른다.
                        if (
                            industry in DEFENSIVE_TECH_INDUSTRIES
                            and ticker in MATURE_LARGE_CAP_TECH_WHITELIST
                        ) or ticker in MATURE_LARGE_CAP_TECH_WHITELIST:
                            passes = True

                    if not passes:
                        continue

                    # wide_universe 는 종종 "BRK.B" 처럼 . 표기 — 그대로 둠
                    derived.setdefault(ticker, name)
        except Exception as e:
            log.warning("wide_universe.csv 필터링 실패 (graceful): %s", e)
    else:
        log.info("wide_universe.csv 없음 — ETF whitelist + fallback 만 사용")

    # 3) fallback — 결과가 너무 적으면 기존 12종 추가
    if len(derived) < min_fallback_threshold:
        log.info(
            "parking 후보가 %d개로 적음 — PARKING_UNIVERSE fallback 추가 (12종)",
            len(derived),
        )
        for t, n in PARKING_UNIVERSE.items():
            derived.setdefault(t, n)

    # 4) 너무 많아도 절단 — ETF whitelist 우선 유지를 위해 우선순위 정렬
    if len(derived) > max_candidates:
        whitelist_keys = list(DEFENSIVE_ETF_WHITELIST.keys())
        fallback_keys = list(PARKING_UNIVERSE.keys())
        priority = {k: 0 for k in whitelist_keys}
        for k in fallback_keys:
            priority.setdefault(k, 1)
        ordered = sorted(
            derived.items(), key=lambda kv: priority.get(kv[0], 2),
        )
        derived = dict(ordered[:max_candidates])

    return derived


def _regime_adjusted_weights(regime: Any | None) -> tuple[dict[str, float], str]:
    """현재 시장 국면에 따라 SUBSCORE_WEIGHTS 를 재조정.

    - High overheat (>=65) OR Overheated/Expensive but Stable
      → low_beta / dividend_buyback 상향, valuation_reasonableness 하향
        (전부 비싸서 valuation 게이트 효용 감소).
    - Defensive (Correction Watch / Dislocation / Crisis)
      → drawdown_resilience / earnings_stability 상향.
    - 그 외 → 기존 가중치 유지.

    Returns: (weights dict (합계 1.0), regime_mode 라벨 문자열).
    """
    base = dict(SUBSCORE_WEIGHTS)
    mode = "normal"

    if regime is None:
        return base, mode

    cur_regime = None
    overheat = None
    try:
        keys = regime.keys() if hasattr(regime, "keys") else []
        if "current_regime" in keys:
            cur_regime = regime["current_regime"]
        if "market_overheat_score" in keys:
            overheat = _f(regime["market_overheat_score"])
    except Exception:
        return base, mode

    expensive_regimes = {"Overheated", "Expensive but Stable"}
    defensive_regimes = {"Correction Watch", "Dislocation", "Crisis"}

    is_expensive = (
        (cur_regime in expensive_regimes)
        or (overheat is not None and overheat >= 65)
    )
    is_defensive = cur_regime in defensive_regimes

    if is_expensive:
        mode = "expensive"
        base["low_beta_score"] = 0.22
        base["dividend_buyback_score"] = 0.17
        base["valuation_reasonableness_score"] = 0.08
        # 합 = 0.20 + 0.15 + 0.22 + 0.15 + 0.17 + 0.08 + 0.10 = 1.07
        # → 재정규화
    elif is_defensive:
        mode = "defensive"
        base["drawdown_resilience_score"] = 0.22
        base["earnings_stability_score"] = 0.25
        # 합 = 0.25 + 0.15 + 0.15 + 0.22 + 0.10 + 0.15 + 0.10 = 1.12
        # → 재정규화

    total = sum(base.values()) or 1.0
    base = {k: v / total for k, v in base.items()}
    return base, mode


def _bond_etf_bonus(ticker: str, regime_mode: str) -> float:
    """Crisis/Dislocation 국면에서 단기채 ETF 에 적용할 점수 bonus.

    BIL/SHV/SGOV 는 사실상 현금 등가물 — 위기 국면 parking 의 정답에 가깝다.
    score 가 None 이거나 defensive 모드가 아니면 0.
    """
    if regime_mode != "defensive":
        return 0.0
    if ticker.upper() in SHORT_DURATION_BOND_ETFS:
        return 12.0  # 0~100 스케일에서 의미 있는 가산
    return 0.0


def screen_parking_candidates(
    market_data_map: dict[str, dict] | None = None,
    *,
    regime: Any | None = None,
    universe: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parking 후보 유니버스를 fetch + (regime-aware) 점수화.

    - 유니버스: derive_parking_universe() 가 도출 (ETF whitelist + wide_universe
      방어적 필터 + 부족 시 PARKING_UNIVERSE fallback). universe 인자를 직접
      넘기면 그것을 그대로 사용 (테스트/디버깅용).
    - market_data_map: {ticker: market_data dict} 가 주어지면 그것을 사용
      (파이프라인용). None 이면 직접 yfinance 로 fetch (graceful — 실패 시 빈
      market_data 로 점수만 NULL 화).
    - regime: market_regime 테이블의 sqlite3.Row / dict / None — 현재 국면에
      맞춰 SUBSCORE_WEIGHTS 를 조정. defensive 국면이면 단기채 ETF 에 bonus.

    Returns: parking_score 내림차순 정렬된 후보 dict list.
    """
    parking_universe = universe if universe is not None else derive_parking_universe()
    tickers = list(parking_universe.keys())

    if market_data_map is None:
        try:
            from .market_data import fetch_universe
            market_data_map = fetch_universe(tickers, period="2y", enrich=True)
        except Exception as e:
            log.warning("parking 유니버스 fetch 실패: %s", e)
            market_data_map = {}

    weights, regime_mode = _regime_adjusted_weights(regime)

    candidates: list[dict[str, Any]] = []
    for ticker in tickers:
        # yfinance ticker 정규화 (BRK.B ↔ BRK-B 등 미세 차이 흡수)
        md = (market_data_map or {}).get(ticker)
        if md is None:
            md = (market_data_map or {}).get(ticker.replace(".", "-"))
        if md is None:
            md = (market_data_map or {}).get(ticker.replace("-", "."))
        md = md or {}
        try:
            scored = calculate_parking_stock_score(
                {"ticker": ticker, "market_data": md},
                weights=weights,
            )
        except Exception as e:
            log.warning("[%s] parking score 계산 실패: %s", ticker, e)
            continue
        scored["ticker"] = ticker
        scored["name"] = parking_universe.get(ticker, ticker)
        scored["regime_mode"] = regime_mode

        # 단기채 ETF bonus — defensive 국면
        bonus = _bond_etf_bonus(ticker, regime_mode)
        if bonus > 0 and scored.get("parking_score") is not None:
            adjusted = min(100.0, scored["parking_score"] + bonus)
            scored["bond_etf_bonus"] = bonus
            scored["parking_score_raw"] = scored["parking_score"]
            scored["parking_score"] = round(adjusted, 1)
            scored["parking_band_ko"] = _parking_band_ko(adjusted)

        candidates.append(scored)

    # parking_score 내림차순 (None 은 뒤로)
    candidates.sort(
        key=lambda c: (c.get("parking_score") is not None, c.get("parking_score") or 0.0),
        reverse=True,
    )
    return candidates
