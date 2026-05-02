"""실제 주가 데이터 수집 (yfinance).

배치 호출 우선:
- 42종목을 한 번의 yf.download() 호출로 받아 Yahoo 측 봇 차단 위험을 크게 줄인다.
- 배치 실패 시에만 종목별 fallback.
- 종목별 실패는 격리 (앱이 죽지 않게).
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any

import pandas as pd

from .utils import get_logger, safe_float

log = get_logger("market_data")

# 시장 분위기 추적용 ETF/지수 프록시
MARKET_PROXIES: dict[str, str] = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "IWM": "러셀2000",
    "TLT": "장기채(20Y+)",
    "GLD": "금",
    "USO": "원유",
    "BTC-USD": "비트코인",
}


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

# yfinance import 상태를 모듈 변수에 저장 (UI에서 진단용)
YF_IMPORT_ERROR: str | None = None


def _safe_yf():
    global YF_IMPORT_ERROR
    try:
        import yfinance as yf  # type: ignore
        YF_IMPORT_ERROR = None
        return yf
    except Exception as e:  # pragma: no cover
        YF_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        log.error("yfinance import 실패: %s", YF_IMPORT_ERROR)
        return None


def get_yfinance_status() -> dict[str, Any]:
    """현재 Python 환경의 yfinance 상태를 진단."""
    import sys
    info: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "yfinance_installed": False,
        "yfinance_version": None,
        "yfinance_path": None,
        "import_error": None,
    }
    try:
        import yfinance as yf  # type: ignore
        info["yfinance_installed"] = True
        info["yfinance_version"] = getattr(yf, "__version__", "unknown")
        info["yfinance_path"] = getattr(yf, "__file__", None)
    except Exception as e:
        info["import_error"] = f"{type(e).__name__}: {e}"
    return info


def _empty(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "available": False,
        "error": None,
        "current_price": None,
        "previous_close": None,
        "daily_return": None,
        "5d_return": None,
        "1m_return": None,
        "3m_return": None,
        "6m_return": None,
        "1y_return": None,
        "52w_high": None,
        "52w_low": None,
        "drawdown_from_52w_high": None,
        "volume": None,
        "avg_volume_30d": None,
        "market_cap": None,
        "trailing_pe": None,
        "forward_pe": None,
        "gross_margin": None,
        "operating_margin": None,
        # 추가 valuation 필드
        "pbr": None,
        "psr": None,
        "ev_ebitda": None,
        "roe": None,
        "revenue_growth": None,
        "fcf_yield": None,
        "history": None,
        "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }


def _period_return(closes: pd.Series, days: int) -> float | None:
    if closes is None or len(closes) < 2:
        return None
    last_idx = len(closes) - 1
    target_idx = max(0, last_idx - days)
    if target_idx >= last_idx:
        return None
    try:
        start = float(closes.iloc[target_idx])
        end = float(closes.iloc[last_idx])
        if start <= 0:
            return None
        return (end / start) - 1.0
    except Exception:
        return None


def _hist_to_record(ticker: str, hist: pd.DataFrame | None) -> dict[str, Any]:
    rec = _empty(ticker)
    if hist is None or hist.empty:
        rec["error"] = "no price history"
        return rec
    if "Close" not in hist.columns:
        # 컬럼이 multi-index 가 아닌데 Close가 없으면 비정상
        rec["error"] = "missing Close column"
        return rec

    closes = hist["Close"].dropna()
    if closes.empty:
        rec["error"] = "empty closes"
        return rec

    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else last

    high_52w = float(closes.max())
    low_52w = float(closes.min())
    dd_high = (last / high_52w) - 1.0 if high_52w > 0 else None

    vol = None
    avg_vol30 = None
    if "Volume" in hist.columns:
        try:
            vol = int(hist["Volume"].iloc[-1])
        except Exception:
            pass
        try:
            avg_vol30 = float(hist["Volume"].tail(30).mean())
        except Exception:
            pass

    rec.update(
        {
            "available": True,
            "current_price": last,
            "previous_close": prev,
            "daily_return": (last / prev - 1.0) if prev > 0 else None,
            "5d_return": _period_return(closes, 5),
            "1m_return": _period_return(closes, 21),
            "3m_return": _period_return(closes, 63),
            "6m_return": _period_return(closes, 126),
            "1y_return": _period_return(closes, 252),
            "52w_high": high_52w,
            "52w_low": low_52w,
            "drawdown_from_52w_high": dd_high,
            "volume": vol,
            "avg_volume_30d": avg_vol30,
            "history": hist,
        }
    )
    return rec


def _enrich_one(yf, ticker: str, rec: dict[str, Any]) -> None:
    """fast_info / info에서 valuation 보강. 실패해도 무시."""
    # fast_info (가벼움, 거의 안 막힘)
    try:
        tk = yf.Ticker(ticker)
        fi = getattr(tk, "fast_info", None)
        if fi is not None:
            mc = safe_float(getattr(fi, "market_cap", None))
            if mc:
                rec["market_cap"] = mc
    except Exception:
        pass
    # info (느리고 가끔 막힘 → best-effort)
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        if isinstance(info, dict):
            rec["market_cap"] = rec.get("market_cap") or safe_float(info.get("marketCap"))
            rec["trailing_pe"] = safe_float(info.get("trailingPE"))
            rec["forward_pe"] = safe_float(info.get("forwardPE"))
            rec["gross_margin"] = safe_float(info.get("grossMargins"))
            rec["operating_margin"] = safe_float(info.get("operatingMargins"))
            # 추가 valuation
            rec["pbr"] = safe_float(info.get("priceToBook"))
            rec["psr"] = safe_float(info.get("priceToSalesTrailing12Months"))
            rec["ev_ebitda"] = safe_float(info.get("enterpriseToEbitda"))
            rec["roe"] = safe_float(info.get("returnOnEquity"))
            rec["revenue_growth"] = safe_float(info.get("revenueGrowth"))
            fcf = safe_float(info.get("freeCashflow"))
            mc = rec.get("market_cap")
            if fcf is not None and mc and mc > 0:
                rec["fcf_yield"] = fcf / mc
    except Exception as e:
        log.debug("[%s] info fetch 실패: %s", ticker, e)


# ---------------------------------------------------------------------------
# Public — 단일 / 배치
# ---------------------------------------------------------------------------

def fetch_one(ticker: str, period: str = "1y") -> dict[str, Any]:
    """단일 종목 fetch (배치 실패 시 fallback 용)."""
    yf = _safe_yf()
    rec = _empty(ticker)
    if yf is None:
        rec["error"] = f"yfinance import failed: {YF_IMPORT_ERROR or 'not installed in this Python env'}"
        return rec
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period, auto_adjust=False)
        if hist is None or hist.empty:
            hist = tk.history(period="13mo", auto_adjust=False)
        rec = _hist_to_record(ticker, hist)
        if rec.get("available"):
            _enrich_one(yf, ticker, rec)
    except Exception as e:
        log.warning("[%s] fetch_one 실패: %s", ticker, e)
        rec["error"] = str(e)
    return rec


def fetch_universe(
    tickers: list[str],
    period: str = "1y",
    enrich: bool = True,
) -> dict[str, dict[str, Any]]:
    """배치 fetch. 권장 진입점.

    1) yf.download(tickers, period=...) 한 번에 → 가격 시계열 수집
    2) 실패한 종목만 종목별 fetch_one 으로 fallback
    3) enrich=True면 fast_info/info에서 valuation 보강
    """
    yf = _safe_yf()
    out: dict[str, dict[str, Any]] = {t: _empty(t) for t in tickers}
    if yf is None:
        msg = f"yfinance import failed: {YF_IMPORT_ERROR or 'not installed in this Python env'}"
        for t in tickers:
            out[t]["error"] = msg
        return out
    if not tickers:
        return out

    last_err: str | None = None
    df: pd.DataFrame | None = None
    for attempt in range(3):
        try:
            df = yf.download(
                tickers,
                period=period,
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
            if df is not None and not df.empty:
                break
        except Exception as e:
            last_err = str(e)
            log.warning("yf.download 실패 (시도 %d): %s", attempt + 1, e)
        time.sleep(1.2 * (attempt + 1))

    if df is None or df.empty:
        log.warning("배치 fetch 실패 (last_err=%s) — 개별 fetch fallback", last_err)
        for t in tickers:
            out[t] = fetch_one(t, period=period)
            if not out[t].get("available") and last_err and not out[t].get("error"):
                out[t]["error"] = f"batch fail: {last_err}"
        return out

    # 컬럼 구조 분기
    is_multi = isinstance(df.columns, pd.MultiIndex)
    available_top: set[str] = set()
    if is_multi:
        try:
            available_top = set(df.columns.get_level_values(0))
        except Exception:
            available_top = set()

    for t in tickers:
        try:
            if is_multi:
                if t in available_top:
                    sub = df[t].copy()
                else:
                    sub = pd.DataFrame()
            else:
                # 단일 ticker거나 그룹화 안 된 경우
                sub = df.copy()
            sub = sub.dropna(how="all")
            rec = _hist_to_record(t, sub)
            if not rec.get("available"):
                # 한 종목만 실패한 경우 개별 fetch fallback
                rec = fetch_one(t, period=period)
            out[t] = rec
        except Exception as e:
            log.warning("[%s] 배치 결과 파싱 실패: %s", t, e)
            try:
                out[t] = fetch_one(t, period=period)
            except Exception as e2:
                out[t] = _empty(t)
                out[t]["error"] = f"{e} / {e2}"

    # Enrich (best-effort)
    if enrich:
        for t in tickers:
            if out[t].get("available"):
                try:
                    _enrich_one(yf, t, out[t])
                except Exception:
                    pass
    return out


def fetch_market_proxies() -> dict[str, dict[str, Any]]:
    return fetch_universe(list(MARKET_PROXIES.keys()), enrich=False)


# ---------------------------------------------------------------------------
# 장기 (전체 기간) 가격 시계열
# ---------------------------------------------------------------------------

def fetch_max_history(ticker: str):
    """yfinance period='max' 일봉. 종목 상세 화면 주가 차트 전용."""
    yf = _safe_yf()
    if yf is None:
        return None
    last_err: str | None = None
    for attempt in range(3):
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="max", interval="1d", auto_adjust=False)
            if hist is None or hist.empty:
                hist = tk.history(period="10y", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty:
                # timezone-aware → naive 로 통일 (이벤트 마커 매칭 용이)
                try:
                    if getattr(hist.index, "tz", None) is not None:
                        hist.index = hist.index.tz_convert(None)
                except Exception:
                    try:
                        hist.index = hist.index.tz_localize(None)
                    except Exception:
                        pass
                return hist
        except Exception as e:
            last_err = str(e)
            log.warning("[%s] max history 시도 %d 실패: %s", ticker, attempt + 1, e)
        time.sleep(1.0 * (attempt + 1))
    log.warning("[%s] max history 최종 실패: %s", ticker, last_err)
    return None


# ---------------------------------------------------------------------------
# 시장 톤 요약 (한국어)
# ---------------------------------------------------------------------------

def market_summary_ko(proxies: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []

    def line(sym: str) -> str | None:
        d = proxies.get(sym)
        if not d or not d.get("available"):
            return None
        dr = d.get("daily_return")
        r1m = d.get("1m_return")
        if dr is None:
            return None
        return (
            f"{MARKET_PROXIES[sym]}({sym}) 일일 {dr * 100:+.2f}%"
            + (f", 1개월 {r1m * 100:+.1f}%" if r1m is not None else "")
        )

    for sym in MARKET_PROXIES:
        s = line(sym)
        if s:
            parts.append(s)

    if not parts:
        return "시장 데이터 수집에 실패했습니다."

    spy = proxies.get("SPY", {})
    qqq = proxies.get("QQQ", {})
    tlt = proxies.get("TLT", {})
    gld = proxies.get("GLD", {})

    spy_dr = spy.get("daily_return") if spy.get("available") else None
    qqq_dr = qqq.get("daily_return") if qqq.get("available") else None
    tlt_dr = tlt.get("daily_return") if tlt.get("available") else None
    gld_dr = gld.get("daily_return") if gld.get("available") else None

    tone = "혼조"
    risk_on = sum(1 for x in (spy_dr, qqq_dr) if x is not None and x > 0)
    risk_off = sum(1 for x in (tlt_dr, gld_dr) if x is not None and x > 0)
    if (spy_dr or 0) > 0 and (qqq_dr or 0) > 0 and (tlt_dr or 0) <= 0:
        tone = "Risk-On"
    elif (spy_dr or 0) < 0 and (tlt_dr or 0) > 0:
        tone = "Risk-Off"
    elif risk_on >= 1 and risk_off >= 1:
        tone = "혼조"

    return f"오늘 시장 톤은 {tone} 입니다. " + " · ".join(parts)
