"""실시간 가격 fetcher — 알림 엔진용 (30분 cron).

대상:
    - US 주식/ETF: yfinance (TQQQ, QQQ, SPY, SOXL, ...)
    - KR 주식/ETF: pykrx + naver finance fallback
    - 크립토: upbit (KRW-BTC) — 무료 / no auth

모든 fetch 는 timeout + graceful fallback. 실패 시 dict 의 키만 누락.

Returns 통일된 dict:
{
  "ticker": str, "name": str,
  "price": float, "currency": str ("USD" | "KRW"),
  "change_pct_24h": float | None,  # 분수
  "high_52w": float | None,
  "low_52w": float | None,
  "drawdown_from_52w_high": float | None,  # 분수, 음수
  "as_of": "2026-05-31T01:23 KST",
  "source": "yfinance" | "upbit" | "pykrx" | "naver",
  "available": bool,
}
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger

log = get_logger("realtime_prices")

_NOW_KST = lambda: _dt.datetime.utcnow() + _dt.timedelta(hours=9)


def _stamp() -> str:
    return _NOW_KST().strftime("%Y-%m-%dT%H:%M KST")


# ---------------------------------------------------------------------------
# US — yfinance
# ---------------------------------------------------------------------------

def fetch_us_ticker(ticker: str) -> dict[str, Any]:
    """US 종목/ETF — yfinance fast_info."""
    out: dict[str, Any] = {
        "ticker": ticker, "name": ticker, "price": None, "currency": "USD",
        "change_pct_24h": None, "high_52w": None, "low_52w": None,
        "drawdown_from_52w_high": None,
        "as_of": _stamp(), "source": "yfinance", "available": False,
    }
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = getattr(t, "fast_info", None) or {}
        last = fi.get("last_price") or fi.get("lastPrice")
        prev = fi.get("previous_close") or fi.get("previousClose")
        hi = fi.get("year_high") or fi.get("fiftyTwoWeekHigh")
        lo = fi.get("year_low") or fi.get("fiftyTwoWeekLow")
        if last is None:
            # fallback: history 1d
            h = t.history(period="2d", auto_adjust=False)
            if not h.empty:
                last = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else last
        if last is None:
            return out
        out["price"] = float(last)
        if prev is not None and prev:
            out["change_pct_24h"] = (float(last) / float(prev)) - 1.0
        if hi is not None:
            out["high_52w"] = float(hi)
            out["drawdown_from_52w_high"] = (float(last) / float(hi)) - 1.0
        if lo is not None:
            out["low_52w"] = float(lo)
        out["available"] = True
    except Exception as e:
        log.debug("yfinance %s 실패: %s", ticker, e)
    return out


# ---------------------------------------------------------------------------
# KR — pykrx (장중 가격) + naver finance fallback
# ---------------------------------------------------------------------------

def fetch_kr_ticker(code: str) -> dict[str, Any]:
    """KR 종목/ETF — 6자리 코드 (예: 000660 SK하이닉스)."""
    out: dict[str, Any] = {
        "ticker": code, "name": code, "price": None, "currency": "KRW",
        "change_pct_24h": None, "high_52w": None, "low_52w": None,
        "drawdown_from_52w_high": None,
        "as_of": _stamp(), "source": "pykrx", "available": False,
    }
    # 1) pykrx — 일별 OHLCV (장중에는 어제까지)
    try:
        from pykrx import stock
        today = _NOW_KST().strftime("%Y%m%d")
        yest = (_NOW_KST() - _dt.timedelta(days=1)).strftime("%Y%m%d")
        # 52주 데이터
        year_ago = (_NOW_KST() - _dt.timedelta(days=370)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv(year_ago, today, code)
        if df is not None and not df.empty:
            last = float(df["종가"].iloc[-1])
            prev = float(df["종가"].iloc[-2]) if len(df) >= 2 else last
            out["price"] = last
            if prev:
                out["change_pct_24h"] = (last / prev) - 1.0
            hi = float(df["고가"].max())
            lo = float(df["저가"].min())
            out["high_52w"] = hi
            out["low_52w"] = lo
            out["drawdown_from_52w_high"] = (last / hi) - 1.0 if hi else None
            try:
                out["name"] = stock.get_market_ticker_name(code)
            except Exception:
                pass
            out["available"] = True
            return out
    except Exception as e:
        log.debug("pykrx %s 실패: %s", code, e)

    # 2) naver finance fallback — 장중 가격
    try:
        import requests, re
        r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}", timeout=5)
        # 매우 단순한 패턴 — production 신뢰성 위해선 BeautifulSoup 권장
        body = r.text
        m = re.search(r'<dd>현재가\s*([\d,]+)', body)
        if m:
            out["price"] = float(m.group(1).replace(",", ""))
            out["source"] = "naver"
            out["available"] = True
    except Exception as e:
        log.debug("naver %s 실패: %s", code, e)
    return out


# ---------------------------------------------------------------------------
# Crypto — Upbit (무료, no auth)
# ---------------------------------------------------------------------------

def fetch_upbit(market: str = "KRW-BTC") -> dict[str, Any]:
    """Upbit 단일 market — KRW-BTC, KRW-ETH 등.

    Returns dict with same shape as fetch_us_ticker.
    """
    out: dict[str, Any] = {
        "ticker": market, "name": market.split("-")[-1], "price": None, "currency": "KRW",
        "change_pct_24h": None, "high_52w": None, "low_52w": None,
        "drawdown_from_52w_high": None,
        "as_of": _stamp(), "source": "upbit", "available": False,
    }
    try:
        import requests
        r = requests.get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": market}, timeout=5,
        )
        if r.status_code != 200:
            return out
        data = r.json()
        if not data:
            return out
        d = data[0]
        out["price"] = float(d.get("trade_price") or 0)
        out["change_pct_24h"] = float(d.get("signed_change_rate") or 0)  # 분수
        out["high_52w"] = float(d.get("highest_52_week_price") or 0) or None
        out["low_52w"] = float(d.get("lowest_52_week_price") or 0) or None
        if out["high_52w"]:
            out["drawdown_from_52w_high"] = (out["price"] / out["high_52w"]) - 1.0
        out["available"] = True
    except Exception as e:
        log.debug("upbit %s 실패: %s", market, e)
    return out


# ---------------------------------------------------------------------------
# Holdings-aware fetcher — portfolio.json 의 holdings 자동 fetch
# ---------------------------------------------------------------------------

def fetch_holdings_prices(holdings: list[dict]) -> dict[str, dict]:
    """holdings 별로 실시간 가격 dict 반환 (ticker → quote)."""
    out: dict[str, dict] = {}
    for h in holdings:
        ticker = (h.get("ticker") or "").strip()
        if not ticker:
            continue
        # 6자리 숫자 = KR code, 그 외 영문 = US
        if ticker.isdigit() and len(ticker) == 6:
            quote = fetch_kr_ticker(ticker)
        elif ticker.upper() in ("BTC", "KRW-BTC"):
            quote = fetch_upbit("KRW-BTC")
        elif ticker.upper() in ("ETH", "KRW-ETH"):
            quote = fetch_upbit("KRW-ETH")
        elif ticker.upper().startswith("KODEX_") or ticker.upper().startswith("TIGER_"):
            # 내부 별칭 — skip (or 별도 매핑 필요)
            continue
        else:
            quote = fetch_us_ticker(ticker.upper())
        out[ticker] = quote
    return out
