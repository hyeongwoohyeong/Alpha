"""KOSPI Overheat Score + KR Regime 분류 — Stage 2 (KR 시장 확장).

미국 `market_regime.py` 와 동일한 설계 원칙·구조를 따른다.
- Rule-based. 점수·분류·코멘트 전부 규칙 기반. LLM 없이 완전 동작.
- 입력 데이터가 없는 sub-score 는 '확인 필요' 로 표시하고 가중치에서 제외,
  나머지로 재정규화. 절대 0 으로 처리하지 않는다 (점수 왜곡 방지).
- 모든 함수는 예외를 던지지 않는다.
- 미국 regime 과 완전히 독립적으로 산출 — 한국 시장 자체의 과열도만 본다.

6 sub-score (각 0~100, 100 = 가장 과열):
1. KOSPI Valuation Stretch — KOSPI 200 P/B + forward P/E (pykrx).
2. KR Sentiment / VKOSPI — 변동성. yfinance `^VKOSPI` → 실패 시 KOSPI
   30일 실현변동성 fallback.
3. KOSPI Concentration — 상위 10 종목 시총 비중 (pykrx).
4. KR Liquidity / Credit — KRW/USD + KR 10년 국채 트렌드.
5. KR Earnings Revision Risk — 데이터 소스 미연결 → 항상 None.
6. KR Technical Extension — KOSPI 200(069500) 200DMA 이격 + RSI.

밴드는 미국과 동일하게 `backtest_engine.OVERHEAT_BANDS` (30/50/70/85) 사용
— 단일 진실 출처.
"""
from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any

from .utils import get_logger

log = get_logger("kr_market_regime")

# Sub-score 가중치 (합 = 1.0). 미국과 동일 비율 — earnings_revision_risk 가
# 영구적으로 None 인 점도 동일하므로 실질 점수는 5개로 재정규화된다.
SUBSCORE_WEIGHTS: dict[str, float] = {
    "kospi_valuation_score": 0.25,
    "kospi_sentiment_score": 0.20,
    "kospi_concentration_score": 0.15,
    "kospi_liquidity_score": 0.15,
    "kospi_earnings_revision_score": 0.15,
    "kospi_technical_score": 0.10,
}

SUBSCORE_LABELS_KO: dict[str, str] = {
    "kospi_valuation_score": "KOSPI 밸류에이션 과열",
    "kospi_sentiment_score": "KR 투자심리 / VKOSPI",
    "kospi_concentration_score": "KOSPI 집중도",
    "kospi_liquidity_score": "KR 유동성 / 신용",
    "kospi_earnings_revision_score": "KR 실적 추정 리스크",
    "kospi_technical_score": "KOSPI 기술적 과열",
}

NEEDS_CHECK = "확인 필요"

# KR 시장 regime — 6 국면. 미국과 라벨은 같되 한국어 톤 조정.
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


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, low: float, high: float) -> float:
    """value 가 low→high 로 갈수록 0→100. 범위 밖은 clamp."""
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def _safe_closes(conn, ticker: str) -> list[float]:
    """market_price_history 의 adj_close (or close) 리스트, 오름차순."""
    try:
        from . import database as _db
        rows = _db.fetch_market_price_history(conn, ticker)
    except Exception as e:
        log.debug("[%s] price 로드 실패: %s", ticker, e)
        return []
    out: list[float] = []
    for r in rows:
        try:
            v = r["adj_close"] if hasattr(r, "keys") else r[6]
        except Exception:
            v = None
        if v is None:
            try:
                v = r["close"] if hasattr(r, "keys") else r[5]
            except Exception:
                v = None
        if v is None:
            continue
        try:
            out.append(float(v))
        except Exception:
            continue
    return out


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for k in range(len(closes) - period, len(closes)):
        if k <= 0:
            continue
        delta = closes[k] - closes[k - 1]
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _realized_vol_30d(closes: list[float]) -> float | None:
    """최근 30 영업일 로그수익률의 annualized 표준편차 (백분율)."""
    import math
    if len(closes) < 31:
        return None
    rets: list[float] = []
    for i in range(len(closes) - 30, len(closes)):
        if i <= 0:
            continue
        prev = closes[i - 1]
        cur = closes[i]
        if prev <= 0 or cur <= 0:
            continue
        try:
            rets.append(math.log(cur / prev))
        except Exception:
            continue
    if len(rets) < 10:
        return None
    try:
        sd = statistics.stdev(rets)
    except Exception:
        return None
    return float(sd * math.sqrt(252) * 100.0)


# ---------------------------------------------------------------------------
# 입력 수집 — pykrx / yfinance graceful fetch
# ---------------------------------------------------------------------------

def collect_kospi_inputs(conn) -> dict[str, Any]:
    """KR regime 산출에 필요한 raw 입력을 모은다. 실패한 항목은 None.

    Returns 의 키:
      - kospi200_pbr: float | None
      - kospi200_forward_pe: float | None
      - kospi200_trailing_pe: float | None
      - vkospi: float | None
      - kospi_realized_vol_30d: float | None
      - top10_weight: float | None  (0~1 분수)
      - krw_usd_3m_change: float | None  (분수, 양수=원화 약세)
      - kr_10y_yield_change: float | None (분수, 양수=금리 상승)
      - kospi200_closes: list[float]  (069500 close)
      - sample_caveats: list[str]  (수집 누락 경고 — UI 노출용)
    """
    out: dict[str, Any] = {
        "kospi200_pbr": None,
        "kospi200_forward_pe": None,
        "kospi200_trailing_pe": None,
        "vkospi": None,
        "kospi_realized_vol_30d": None,
        "top10_weight": None,
        "krw_usd_3m_change": None,
        "kr_10y_yield_change": None,
        "kospi200_closes": [],
        "sample_caveats": [],
    }

    # (1) KOSPI 200 valuation — pykrx
    try:
        from pykrx import stock as _pykrx_stock  # type: ignore
        today = _dt.date.today().strftime("%Y%m%d")
        # 최근 영업일까지 ~10일 backfill
        df = None
        for back in range(0, 10):
            d = (_dt.date.today() - _dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = _pykrx_stock.get_index_fundamental(d, "1028")  # KOSPI 200 = 1028
                if df is not None and not df.empty:
                    break
            except Exception:
                df = None
                continue
        if df is not None and not df.empty:
            try:
                row = df.iloc[-1]
                pe = float(row.get("PER") or 0) or None
                pbr = float(row.get("PBR") or 0) or None
                out["kospi200_trailing_pe"] = pe
                out["kospi200_pbr"] = pbr
            except Exception as e:
                log.debug("KOSPI 200 fundamental 파싱 실패: %s", e)
    except Exception as e:
        log.debug("pykrx fundamental fetch 실패: %s", e)
        out["sample_caveats"].append(
            f"pykrx 미설치/실패로 KOSPI 200 P/B·P/E {NEEDS_CHECK}."
        )

    # forward PE — 무료 데이터로 KOSPI 200 forward PE 직접은 어려움. trailing 으로 대체.
    if out["kospi200_forward_pe"] is None and out["kospi200_trailing_pe"] is not None:
        out["kospi200_forward_pe"] = out["kospi200_trailing_pe"]

    # (2) KOSPI 200 ETF (069500) 가격 시계열 — technical / realized vol 입력
    closes = _safe_closes(conn, "069500")
    out["kospi200_closes"] = closes
    if closes:
        out["kospi_realized_vol_30d"] = _realized_vol_30d(closes)

    # (3) VKOSPI — yfinance `^VKOSPI`. 데이터 거의 없거나 차단되는 경우가 많음.
    try:
        from .market_data import _safe_yf  # type: ignore
        yf = _safe_yf()
        if yf is not None:
            tk = yf.Ticker("^VKOSPI")
            hist = tk.history(period="5d", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty:
                try:
                    out["vkospi"] = float(hist["Close"].iloc[-1])
                except Exception:
                    pass
    except Exception as e:
        log.debug("VKOSPI fetch 실패: %s", e)

    if out["vkospi"] is None and out["kospi_realized_vol_30d"] is None:
        out["sample_caveats"].append(
            "VKOSPI 및 KOSPI 30일 실현변동성 모두 미수집 — KR 투자심리 "
            f"{NEEDS_CHECK}."
        )
    elif out["vkospi"] is None:
        out["sample_caveats"].append(
            "VKOSPI 미수집 — 30일 실현변동성 fallback 사용."
        )

    # (4) Top-10 KOSPI 200 weight — pykrx 시총 기반 근사
    try:
        from pykrx import stock as _pykrx_stock  # type: ignore
        # 최근 영업일 backfill
        weight = None
        for back in range(0, 10):
            d = (_dt.date.today() - _dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                cap = _pykrx_stock.get_index_portfolio_deposit_file(d, "1028")
                # 일부 버전은 list of tickers 반환 — 그 경우 시총으로 별도 계산
                if cap is None:
                    continue
                # cap 이 list 면 ticker 리스트만 — 시총 join 필요
                if isinstance(cap, list):
                    mc_df = _pykrx_stock.get_market_cap_by_ticker(d, market="KOSPI")
                    if mc_df is None or mc_df.empty:
                        continue
                    in_idx = mc_df[mc_df.index.isin(cap)].copy()
                    if in_idx.empty:
                        continue
                    in_idx = in_idx.sort_values("시가총액", ascending=False)
                    total = float(in_idx["시가총액"].sum() or 0)
                    top10 = float(in_idx["시가총액"].head(10).sum() or 0)
                    if total > 0:
                        weight = top10 / total
                        break
                else:
                    # DataFrame 일 경우 — 비중 컬럼이 있을 수도 있음
                    try:
                        if "비중" in cap.columns:
                            sorted_w = cap.sort_values("비중", ascending=False)
                            weight = float(sorted_w["비중"].head(10).sum()) / 100.0
                            break
                    except Exception:
                        pass
            except Exception:
                continue
        if weight is not None:
            out["top10_weight"] = weight
    except Exception as e:
        log.debug("Top-10 weight 계산 실패: %s", e)

    if out["top10_weight"] is None:
        out["sample_caveats"].append(
            f"KOSPI 200 상위 10 종목 비중 미수집 — 집중도 {NEEDS_CHECK}."
        )

    # (5) KRW/USD 3개월 변화 — yfinance `KRW=X`
    try:
        from .market_data import _safe_yf  # type: ignore
        yf = _safe_yf()
        if yf is not None:
            tk = yf.Ticker("KRW=X")
            hist = tk.history(period="6mo", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty and len(hist) > 60:
                try:
                    last = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-63])
                    if prev > 0:
                        out["krw_usd_3m_change"] = last / prev - 1.0
                except Exception:
                    pass
    except Exception as e:
        log.debug("KRW=X fetch 실패: %s", e)

    if out["krw_usd_3m_change"] is None:
        out["sample_caveats"].append(
            f"KRW/USD 추세 미수집 — 유동성 입력 부분 누락."
        )

    # (6) KR 10년 국채 — 무료 yfinance 심볼이 일관되지 않음 (^TNX 는 미국).
    # 한국 10Y 무료 ticker 부재 — 누락 처리.
    out["sample_caveats"].append(
        "KR 10년 국채 무료 yfinance 심볼 부재 — 금리 입력 제외 (FX 만 사용)."
    )

    return out


# ---------------------------------------------------------------------------
# Sub-score 계산기 — 각각 (score|None, commentary_ko) 반환
# ---------------------------------------------------------------------------

def _score_kospi_valuation(inp: dict) -> tuple[float | None, str]:
    """KOSPI 200 P/B + forward P/E.

    Anchor (요청 명세 그대로 — 한국 시장 평균 P/B ~1.0, P/E ~10.5 부근 기준):
      - P/B < 0.8 → 0,  P/B > 1.5 → 100
      - P/E < 9  → 0,  P/E > 16  → 100
    두 점수의 평균을 반환. 둘 다 없으면 None.
    """
    parts: list[float] = []
    notes: list[str] = []
    pb = inp.get("kospi200_pbr")
    if pb is not None and pb > 0:
        parts.append(_lerp_score(pb, 0.8, 1.5))
        notes.append(f"KOSPI 200 P/B {pb:.2f}")
    pe = inp.get("kospi200_forward_pe") or inp.get("kospi200_trailing_pe")
    if pe is not None and pe > 0:
        parts.append(_lerp_score(pe, 9.0, 16.0))
        notes.append(f"KOSPI 200 P/E {pe:.1f}x")
    if not parts:
        return None, (
            f"KOSPI 200 P/B·P/E 미수집 — 밸류에이션 {NEEDS_CHECK}."
        )
    score = sum(parts) / len(parts)
    desc = ("역사적 저평가권" if score < 35 else "정상 범위" if score < 60
            else "고평가권" if score < 80 else "극단적 고평가")
    return score, "KOSPI 밸류에이션: " + desc + " — " + ", ".join(notes) + "."


def _score_kospi_sentiment(inp: dict) -> tuple[float | None, str]:
    """VKOSPI 기반. 데이터 없으면 KOSPI 30일 실현변동성 fallback.

    낮은 변동성 = 안도 = 과열 기여 (점수↑). 높은 변동성 = panic = 점수↓.
      - VKOSPI < 12 → 100, > 35 → 0
      - 실현변동성 (annualized %) — 동일 범위로 fallback.
    """
    v = inp.get("vkospi")
    if v is not None:
        # 12(complacent)~35(panic) — 12 이하 = 100, 35 이상 = 0 (inverse 선형)
        score = _lerp_score(v, 35.0, 12.0)
        desc = ("안도/낮은 변동성 (과열 신호)" if score >= 70
                else "정상" if score >= 35 else "panic / 변동성 급등")
        return score, f"VKOSPI {v:.1f} — {desc}."
    rv = inp.get("kospi_realized_vol_30d")
    if rv is not None:
        score = _lerp_score(rv, 35.0, 12.0)
        desc = ("안도/낮은 변동성 (과열 신호)" if score >= 70
                else "정상" if score >= 35 else "panic / 변동성 급등")
        return score, (
            f"VKOSPI 미수집 → KOSPI 30일 실현변동성 {rv:.1f}% (annualized) — {desc}."
        )
    return None, (
        f"VKOSPI 및 실현변동성 모두 미수집 — KR 투자심리 {NEEDS_CHECK}."
    )


def _score_kospi_concentration(inp: dict) -> tuple[float | None, str]:
    """KOSPI 200 상위 10 종목 비중. > 50% → 100, < 35% → 0."""
    w = inp.get("top10_weight")
    if w is None:
        return None, f"KOSPI 상위 10 종목 비중 미수집 — 집중도 {NEEDS_CHECK}."
    score = _lerp_score(w, 0.35, 0.50)
    desc = ("폭넓은 분산" if score < 35 else "정상" if score < 60
            else "소수 대형주 쏠림" if score < 80 else "극단적 쏠림")
    return score, (
        f"KOSPI 200 상위 10 종목 비중 {w * 100:.1f}% — {desc}."
    )


def _score_kr_liquidity(inp: dict) -> tuple[float | None, str]:
    """KRW/USD 트렌드 + (가능 시) 한국 10Y 금리.

    KRW/USD 가 가파르게 약세 (큰 양수) = 외국인 자금 이탈 = 스트레스 = 점수↓.
    즉 inverse 선형: 3M 변화 -5% (원화 강세) → 100, +8% (원화 급락) → 0.
    금리 데이터 없을 땐 FX 만으로 산출.
    """
    fx = inp.get("krw_usd_3m_change")
    if fx is None:
        return None, (
            f"KRW/USD 추세 미수집 — KR 유동성/신용 {NEEDS_CHECK}."
        )
    # -0.05 (원화 강세) → 100, +0.08 (원화 급락) → 0
    score = _lerp_score(fx, 0.08, -0.05)
    fx_desc = (
        "원화 강세 — 외국인 자금 우호" if score >= 70
        else "정상" if score >= 35
        else "원화 약세 — 자금 이탈 신호"
    )
    return score, (
        f"KR 유동성: KRW/USD 3개월 {fx * 100:+.1f}% — {fx_desc} "
        f"(KR 10Y 금리 데이터 부재로 FX 단독 산출)."
    )


def _score_kr_earnings_revision(inp: dict) -> tuple[float | None, str]:
    """KR 실적 추정 리스크 — 무료 데이터 소스 없음. 항상 None (honest skip).

    미국 모듈 (`_score_earnings_revision`) 과 동일한 정책 — 가중치 재정규화로
    처리된다.
    """
    return None, (
        f"KR 실적 추정치 상·하향 데이터는 무료 소스로 수집 불가 — {NEEDS_CHECK}. "
        "가중치에서 제외하고 나머지 sub-score 로 재정규화."
    )


def _score_kospi_technical(inp: dict) -> tuple[float | None, str]:
    """KOSPI 200 ETF (069500) 의 200DMA 이격 + RSI(14) 기반 기술적 과열.

    - 200DMA 이격 > +12% AND RSI > 75 → 100
    - 200DMA 이격 < -10% OR RSI < 30 → 0
    - 그 외 — 두 신호의 평균.
    """
    closes = inp.get("kospi200_closes") or []
    if len(closes) < 200:
        return None, (
            f"069500(KOSPI 200) 일봉 부족 (현재 {len(closes)}행) — 기술적 과열 "
            f"{NEEDS_CHECK}."
        )
    px = closes[-1]
    ma200 = sum(closes[-200:]) / 200.0
    gap = (px / ma200 - 1.0) if ma200 > 0 else 0.0
    rsi = _rsi(closes)

    parts: list[float] = []
    notes: list[str] = []
    # 200DMA 이격 -10% → 0, +12% → 100
    parts.append(_lerp_score(gap, -0.10, 0.12))
    notes.append(f"200DMA 이격 {gap * 100:+.1f}%")
    if rsi is not None:
        # RSI 30 → 0, 75 → 100
        parts.append(_lerp_score(rsi, 30.0, 75.0))
        notes.append(f"RSI {rsi:.0f}")

    score = sum(parts) / len(parts)

    # 극단 조건 — 둘 다 충족 시 100, 한쪽이라도 침체면 강제 하향
    if rsi is not None and gap > 0.12 and rsi > 75:
        score = 100.0
    elif gap < -0.10 or (rsi is not None and rsi < 30):
        score = 0.0

    desc = ("기술적 침체권" if score < 35 else "정상" if score < 60
            else "기술적 과열 진입" if score < 80 else "극단적 과매수")
    return score, "KOSPI 200(069500): " + desc + " — " + ", ".join(notes) + "."


# ---------------------------------------------------------------------------
# Band — 미국과 동일 (OVERHEAT_BANDS 단일 출처)
# ---------------------------------------------------------------------------

def _overheat_band_ko(score: float | None) -> str:
    if score is None:
        return NEEDS_CHECK
    try:
        from .backtest_engine import OVERHEAT_BANDS
    except Exception:
        # fallback — 그래도 일관된 문자열
        if score < 30:
            return "정상"
        if score < 50:
            return "주의"
        if score < 70:
            return "과열 경계"
        if score < 85:
            return "과열"
        return "FOMO/Casino"
    for lo, hi, name in OVERHEAT_BANDS:
        if lo <= score < hi:
            # OVERHEAT_BANDS 의 라벨은 "0-30 정상" 같은 형태 — 뒤 한국어만 추출
            try:
                return name.split(" ", 1)[1] if " " in name else name
            except Exception:
                return name
    return NEEDS_CHECK


# ---------------------------------------------------------------------------
# Regime 분류 — 미국과 동일 패턴 (단순화: KR 은 신용 데이터 부재)
# ---------------------------------------------------------------------------

def _classify_kr_regime(overheat: float | None,
                        kospi_drawdown: float | None) -> str:
    """KOSPI 200 의 낙폭 + Overheat 점수로 6 국면 분류.

    신용 데이터(HY 스프레드 등) KR 무료 데이터 부재 → drawdown + overheat 만.
    """
    dd = kospi_drawdown if kospi_drawdown is not None else 0.0

    # 큰 낙폭 (-30%+) → 위기
    if dd <= -0.30:
        return REGIME_CRISIS
    # -10%~-25% 디스로케이션
    if dd <= -0.10 and dd > -0.30:
        return REGIME_DISLOCATION
    # 작은 조정 — Correction Watch
    if dd <= -0.05:
        return REGIME_CORRECTION_WATCH

    if overheat is not None:
        if overheat >= 70:
            return REGIME_OVERHEATED
        if overheat >= 50:
            return REGIME_EXPENSIVE_STABLE
    return REGIME_RISK_ON


def _kospi_drawdown_from_high(closes: list[float]) -> float | None:
    """KOSPI 200 52주 고점 대비 낙폭(분수, 음수~0)."""
    if not closes:
        return None
    n = len(closes)
    lo = max(0, n - 252)
    hi = max(closes[lo:n])
    if hi <= 0:
        return None
    return closes[-1] / hi - 1.0


# ---------------------------------------------------------------------------
# Public — calculate_kospi_overheat_score
# ---------------------------------------------------------------------------

def calculate_kospi_overheat_score(conn) -> dict[str, Any]:
    """KOSPI Overheat Score 산정 (메인 진입점).

    Returns dict:
        - overheat_score: float | None
        - sub_scores: dict[str, float | None]  (키: SUBSCORE_WEIGHTS 의 키)
        - sub_commentaries: dict[str, str]
        - band: str
        - regime: str
        - regime_ko: str
        - commentary_ko: str
        - kospi_drawdown_from_52w_high: float | None
        - generated_at: ISO datetime
        - sample_caveats: list[str]
        - missing_subscores: list[str]
        - used_weights: dict[str, float]

    절대 예외를 위로 던지지 않는다 — 모든 입력 fetch 실패 시 차분히 "데이터
    누적 중" dict 반환.
    """
    generated_at = _dt.datetime.now().isoformat(timespec="seconds")

    # 입력 수집 — 어떤 실패든 calm
    try:
        inp = collect_kospi_inputs(conn)
    except Exception as e:
        log.warning("KOSPI 입력 수집 전체 실패 (graceful): %s", e)
        return {
            "overheat_score": None,
            "sub_scores": {k: None for k in SUBSCORE_WEIGHTS},
            "sub_commentaries": {
                k: f"{SUBSCORE_LABELS_KO[k]} {NEEDS_CHECK}." for k in SUBSCORE_WEIGHTS
            },
            "band": NEEDS_CHECK,
            "regime": REGIME_RISK_ON,
            "regime_ko": REGIME_KO[REGIME_RISK_ON],
            "commentary_ko": (
                "KR 시장 데이터 수집 실패 — KOSPI Overheat Score 산출 불가. "
                "데이터 누적 중."
            ),
            "kospi_drawdown_from_52w_high": None,
            "generated_at": generated_at,
            "sample_caveats": [
                f"KR 데이터 전체 수집 실패 ({type(e).__name__}) — 데이터 누적 중."
            ],
            "missing_subscores": list(SUBSCORE_WEIGHTS.keys()),
            "used_weights": {},
        }

    calculators = {
        "kospi_valuation_score": _score_kospi_valuation,
        "kospi_sentiment_score": _score_kospi_sentiment,
        "kospi_concentration_score": _score_kospi_concentration,
        "kospi_liquidity_score": _score_kr_liquidity,
        "kospi_earnings_revision_score": _score_kr_earnings_revision,
        "kospi_technical_score": _score_kospi_technical,
    }

    sub_scores: dict[str, float | None] = {}
    sub_commentaries: dict[str, str] = {}
    for key, fn in calculators.items():
        try:
            score, comment = fn(inp)
        except Exception as e:
            log.warning("KR sub-score %s 계산 실패 (graceful): %s", key, e)
            score, comment = None, (
                f"{SUBSCORE_LABELS_KO[key]} 계산 오류 — {NEEDS_CHECK}."
            )
        sub_scores[key] = (round(_clamp(score), 1) if score is not None else None)
        sub_commentaries[key] = comment

    # 재정규화
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

    band = _overheat_band_ko(overheat)

    # KOSPI 낙폭 + regime
    closes = inp.get("kospi200_closes") or []
    dd = _kospi_drawdown_from_high(closes)
    regime = _classify_kr_regime(overheat, dd)

    # 종합 코멘트
    parts: list[str] = []
    regime_lead = {
        REGIME_RISK_ON: "한국 시장은 위험선호 국면입니다. 추세를 활용할 환경.",
        REGIME_EXPENSIVE_STABLE: "한국 시장은 다소 고평가지만 안정적입니다. 신규 진입은 선별적.",
        REGIME_OVERHEATED: "한국 시장은 과열 국면입니다. 신규 베타 확대보다 보호와 현금 옵션 확보가 우선.",
        REGIME_CORRECTION_WATCH: "한국 시장은 조정 경계 국면입니다 — KOSPI 200 가 단기 약화.",
        REGIME_DISLOCATION: "한국 시장은 디스로케이션 국면 — 의미 있는 낙폭. 분할 매수 검토 구간.",
        REGIME_CRISIS: "한국 시장은 위기 국면입니다 — 큰 낙폭. 방어 우선, 분할 진입은 신중히.",
    }
    parts.append(regime_lead.get(regime, "한국 시장 국면 판단 중."))

    if overheat is not None:
        parts.append(f"KOSPI Overheat Score 는 {overheat:.0f}/100 ({band}) 입니다.")
    else:
        parts.append(
            f"KOSPI Overheat Score 산정에 필요한 데이터가 부족합니다 — {NEEDS_CHECK}."
        )
    if dd is not None:
        parts.append(f"KOSPI 200 은 52주 고점 대비 {dd * 100:+.1f}% 입니다.")
    if missing:
        labels = ", ".join(SUBSCORE_LABELS_KO.get(m, m) for m in missing)
        parts.append(f"※ 가중치에서 제외된 항목: {labels} (나머지로 재정규화).")
    commentary = " ".join(parts)

    return {
        "overheat_score": overheat,
        "sub_scores": sub_scores,
        "sub_commentaries": sub_commentaries,
        "band": band,
        "regime": regime,
        "regime_ko": REGIME_KO.get(regime, regime),
        "commentary_ko": commentary,
        "kospi_drawdown_from_52w_high": (round(dd, 4) if dd is not None else None),
        "generated_at": generated_at,
        "sample_caveats": list(inp.get("sample_caveats") or []),
        "missing_subscores": missing,
        "used_weights": used_weights,
    }
