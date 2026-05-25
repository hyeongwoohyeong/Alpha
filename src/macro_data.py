"""매크로 / 시장 데이터 fetcher — Portfolio Regime 시스템 (Phase 1).

원칙:
- FRED API 키가 없으면 graceful — None 반환, 앱/파이프라인은 절대 죽지 않음.
- yfinance 실패도 격리 — 없는 데이터는 None / 빈 dict.
- 모든 public 함수는 예외를 위로 던지지 않는다 (try/except 로 흡수).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .config import get_fred_api_key
from .market_data import fetch_universe
from .utils import get_logger

log = get_logger("macro_data")

# FRED 시리즈 ID — 무료 공개 데이터
FRED_SERIES: dict[str, str] = {
    "hy_spread": "BAMLH0A0HYM2",   # 하이일드 OAS 스프레드 (%)
    "ig_spread": "BAMLC0A0CM",     # 투자등급 OAS 스프레드 (%)
    "real_yield_10y": "DFII10",    # 10년물 실질금리 (%)
    "treasury_10y": "DGS10",       # 10년물 명목금리 (%)
    "yield_curve_10y2y": "T10Y2Y", # 10년-2년 장단기차 (%)
}

# Portfolio Regime 이 추적하는 ETF (가격이력 + 거래량)
REGIME_TICKERS: list[str] = [
    "SPY", "QQQ", "RSP", "HYG", "LQD", "TQQQ", "QLD", "SQQQ",
]

# 시장 집중도 근사용 대형주
MEGA_CAP_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO",
]

# 시장 폭(breadth) 측정용 광범위 유니버스 — 11개 GICS 섹터 전반의 large/mid-cap.
# mega-cap 7종만으로 breadth 를 재면 좁은 쏠림장에서 신호가 반전되므로
# (mega-cap 은 쏠림장에서도 200일선 위에 머무름) 섹터 전반의 종목으로 측정한다.
BREADTH_UNIVERSE_TICKERS: list[str] = [
    # Tech / Communication
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL",
    "CRM", "ADBE", "CSCO", "ACN", "TXN", "QCOM", "AMD", "INTC",
    "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "AXP", "C", "SPGI",
    # Health Care
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "MDT", "ISRG", "GILD",
    # Consumer Discretionary
    "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "TSLA",
    # Consumer Staples
    "WMT", "PG", "KO", "PEP", "COST", "MDLZ", "CL", "MO",
    # Industrials
    "CAT", "HON", "UNP", "GE", "BA", "RTX", "DE", "LMT", "UPS", "MMM",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG",
    # Utilities / Real Estate / Materials
    "NEE", "DUK", "SO", "AMT", "PLD", "EQIX", "LIN", "SHW", "FCX", "APD",
]

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------------------------------------------------------------------
# FRED REST API
# ---------------------------------------------------------------------------

def _fetch_fred_series(series_id: str, api_key: str, days: int = 400) -> list[dict] | None:
    """단일 FRED 시리즈의 최근 관측치 list 반환. 실패 시 None."""
    try:
        import requests  # type: ignore
    except Exception as e:
        log.warning("requests import 실패: %s", e)
        return None
    try:
        start = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
        }
        resp = requests.get(FRED_BASE_URL, params=params, timeout=15)
        if resp.status_code != 200:
            log.warning("FRED %s HTTP %s", series_id, resp.status_code)
            return None
        data = resp.json()
        obs = data.get("observations") or []
        out: list[dict] = []
        for o in obs:
            val = o.get("value")
            if val in (None, "", "."):
                continue
            try:
                out.append({"date": o.get("date"), "value": float(val)})
            except (ValueError, TypeError):
                continue
        return out or None
    except Exception as e:
        log.warning("FRED %s fetch 실패: %s", series_id, e)
        return None


def fetch_fred_macro() -> dict[str, Any]:
    """FRED 매크로 시리즈 일괄 fetch.

    Returns: {
        "available": bool,
        "reason": str | None,
        "<key>": {"latest": float|None, "prev_30d": float|None,
                   "change_30d": float|None, "history": list},
    }
    키가 없거나 모든 호출이 실패하면 available=False.
    """
    out: dict[str, Any] = {"available": False, "reason": None}
    api_key = get_fred_api_key()
    if not api_key:
        out["reason"] = "FRED_API_KEY 미설정"
        log.info("FRED 키 미설정 — 매크로 데이터 skip (graceful)")
        return out

    any_ok = False
    for key, series_id in FRED_SERIES.items():
        hist = _fetch_fred_series(series_id, api_key)
        if not hist:
            out[key] = {"latest": None, "prev_30d": None,
                        "change_30d": None, "history": []}
            continue
        any_ok = True
        latest = hist[-1]["value"]
        # 30 영업일 ≈ 30 캘린더 인덱스 전 (FRED 일별 시리즈는 영업일)
        prev_idx = max(0, len(hist) - 31)
        prev_30d = hist[prev_idx]["value"] if prev_idx < len(hist) - 1 else None
        change_30d = (latest - prev_30d) if (latest is not None and prev_30d is not None) else None
        out[key] = {
            "latest": latest,
            "prev_30d": prev_30d,
            "change_30d": change_30d,
            "history": hist,
        }

    out["available"] = any_ok
    if not any_ok:
        out["reason"] = "FRED 호출 전부 실패 (네트워크/키 확인 필요)"
    return out


# ---------------------------------------------------------------------------
# yfinance 기반 시장 데이터 + 기술적 헬퍼
# ---------------------------------------------------------------------------

# forward PE 를 별도 보강할 대상 (지수 ETF) — _score_valuation 이 사용
_VALUATION_TICKERS: tuple[str, ...] = ("SPY", "QQQ")


def _enrich_regime_etf_metadata(etf_map: dict[str, dict[str, Any]]) -> None:
    """fetch_universe 의 enrich 가 rate-limit 등으로 forward PE 를 못 채운 경우
    보강. SPY/QQQ 등에 대해 yfinance .info 를 직접 재시도해 forwardPE/trailingPE
    를 etf dict 에 넣는다. 또 _score_sentiment 가 바로 쓸 수 있도록 volume_ratio
    를 사전 계산해 'volume_ratio' 키로 캐싱한다.

    yfinance .info 는 자주 실패하므로 try/except 로 격리 — graceful.
    """
    if not etf_map:
        return
    try:
        from .market_data import _safe_yf  # type: ignore
        yf = _safe_yf()
    except Exception as e:
        log.debug("yfinance import 실패 — ETF 메타 보강 skip: %s", e)
        yf = None

    for sym, md in etf_map.items():
        if not isinstance(md, dict):
            continue
        # (1) volume_ratio 사전 계산 — history 만 있으면 계산 가능 (외부호출 X)
        try:
            vr = volume_ratio(md)
            if vr is not None:
                md["volume_ratio"] = vr
        except Exception:
            pass

        # (2) forward PE — 지수 ETF 만, 이미 있으면 skip
        if sym not in _VALUATION_TICKERS:
            continue
        fpe = md.get("forward_pe")
        if fpe and 5 < fpe < 80:
            continue
        if yf is None:
            continue
        for attempt in range(2):
            try:
                from .utils import safe_float
                info = yf.Ticker(sym).info or {}
                if isinstance(info, dict):
                    fp = safe_float(info.get("forwardPE"))
                    tp = safe_float(info.get("trailingPE"))
                    if fp and 5 < fp < 80:
                        md["forward_pe"] = fp
                    elif tp and 5 < tp < 80:
                        # forward 없으면 trailing 으로 근사 (보수적)
                        md["forward_pe"] = tp
                    if tp:
                        md["trailing_pe"] = md.get("trailing_pe") or tp
                if md.get("forward_pe"):
                    break
            except Exception as e:
                log.debug("[%s] forward PE 보강 시도 %d 실패: %s", sym, attempt + 1, e)


def fetch_regime_market_data() -> dict[str, dict[str, Any]]:
    """Regime 추적 ETF 의 가격이력/거래량 batch fetch. 실패해도 빈 dict 안전.

    fetch_universe(enrich=True) 가 forward PE 를 못 채우는 경우가 잦아
    _enrich_regime_etf_metadata 로 추가 보강 (forward PE + volume_ratio).
    """
    try:
        etf = fetch_universe(REGIME_TICKERS, period="2y", enrich=True)
    except Exception as e:
        log.warning("regime market data fetch 실패: %s", e)
        return {}
    try:
        _enrich_regime_etf_metadata(etf)
    except Exception as e:
        log.warning("regime ETF 메타데이터 보강 실패 (graceful): %s", e)
    return etf


def fetch_megacap_market_data() -> dict[str, dict[str, Any]]:
    """대형주 시총 — 시장 집중도 근사용. 실패해도 빈 dict."""
    try:
        return fetch_universe(MEGA_CAP_TICKERS, period="6mo", enrich=True)
    except Exception as e:
        log.warning("megacap market data fetch 실패: %s", e)
        return {}


def fetch_breadth_market_data() -> dict[str, dict[str, Any]]:
    """시장 폭(breadth) 측정용 광범위 유니버스 가격이력 batch fetch.

    200일선 상회 비율을 megacap 7종이 아닌 섹터 전반 ~80종목으로 계산하기
    위함. enrich 불필요(가격이력만 쓰면 됨). 실패해도 빈 dict 안전.
    """
    try:
        return fetch_universe(BREADTH_UNIVERSE_TICKERS, period="1y", enrich=False)
    except Exception as e:
        log.warning("breadth universe market data fetch 실패: %s", e)
        return {}


def _closes(md: dict[str, Any]):
    """market_data 레코드에서 Close 시리즈 추출. 없으면 None."""
    if not md or not md.get("available"):
        return None
    hist = md.get("history")
    if hist is None:
        return None
    try:
        if "Close" not in hist.columns:
            return None
        c = hist["Close"].dropna()
        return c if len(c) >= 2 else None
    except Exception:
        return None


def compute_rsi(md: dict[str, Any], period: int = 14) -> float | None:
    """RSI(14). 데이터 부족 시 None."""
    closes = _closes(md)
    if closes is None or len(closes) < period + 1:
        return None
    try:
        delta = closes.diff().dropna()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        last_gain = gain.iloc[-1]
        last_loss = loss.iloc[-1]
        if last_loss is None or last_loss == 0:
            return 100.0
        rs = last_gain / last_loss
        return float(100.0 - (100.0 / (1.0 + rs)))
    except Exception:
        return None


def compute_ma_gap(md: dict[str, Any], window: int = 200) -> float | None:
    """200일 이동평균 대비 현재가 이격률 (양수=위, 음수=아래). 데이터 부족 시 None."""
    closes = _closes(md)
    if closes is None or len(closes) < window:
        return None
    try:
        ma = closes.tail(window).mean()
        last = float(closes.iloc[-1])
        if ma is None or ma <= 0:
            return None
        return float((last / ma) - 1.0)
    except Exception:
        return None


def pct_above_ma(md_map: dict[str, dict[str, Any]], window: int = 200) -> float | None:
    """주어진 종목들 중 200일선 위에 있는 비율 (0~1). 계산 가능한 종목이 없으면 None."""
    if not md_map:
        return None
    above = 0
    total = 0
    for md in md_map.values():
        gap = compute_ma_gap(md, window=window)
        if gap is None:
            continue
        total += 1
        if gap > 0:
            above += 1
    if total == 0:
        return None
    return above / total


def volume_ratio(md: dict[str, Any], recent: int = 5, base: int = 60) -> float | None:
    """최근 평균 거래량 / 장기 평균 거래량. 1.0 보다 크면 거래 활발. 부족 시 None."""
    if not md or not md.get("available"):
        return None
    hist = md.get("history")
    if hist is None:
        return None
    try:
        if "Volume" not in hist.columns:
            return None
        vol = hist["Volume"].dropna()
        if len(vol) < base:
            return None
        recent_avg = float(vol.tail(recent).mean())
        base_avg = float(vol.tail(base).mean())
        if base_avg <= 0:
            return None
        return recent_avg / base_avg
    except Exception:
        return None


def collect_regime_inputs() -> dict[str, Any]:
    """Portfolio Regime 계산에 필요한 모든 원천 데이터를 한 번에 수집.

    파이프라인 / UI 진입점. 어떤 부분이 실패해도 나머지는 채워진다.
    Returns dict 키: fred, etf, megacap, breadth, collected_at.
    """
    out: dict[str, Any] = {
        "collected_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        out["fred"] = fetch_fred_macro()
    except Exception as e:
        log.warning("FRED 수집 실패: %s", e)
        out["fred"] = {"available": False, "reason": str(e)}
    try:
        out["etf"] = fetch_regime_market_data()
    except Exception as e:
        log.warning("ETF 수집 실패: %s", e)
        out["etf"] = {}
    try:
        out["megacap"] = fetch_megacap_market_data()
    except Exception as e:
        log.warning("megacap 수집 실패: %s", e)
        out["megacap"] = {}
    try:
        # breadth — 200일선 상회 비율을 섹터 전반 광범위 유니버스로 측정
        out["breadth"] = fetch_breadth_market_data()
    except Exception as e:
        log.warning("breadth universe 수집 실패: %s", e)
        out["breadth"] = {}
    return out
