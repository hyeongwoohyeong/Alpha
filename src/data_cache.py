"""시장 데이터 증분 캐시 — Phase 4-A 백테스트 기반.

`market_price_history` 테이블에 일봉을 누적 저장한다.
- 최초 수집: yfinance period="max" (최대한 긴 history).
- 이후: 마지막 저장일 이후 일봉만 append.
- 월 1회: 전체 재다운로드(split/배당 재조정 반영).

원칙:
- yfinance 실패해도 예외를 위로 던지지 않는다 — graceful, 실패 티커는 로그만.
- 앱/파이프라인이 이 모듈 때문에 죽지 않는다.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger, now_iso

log = get_logger("data_cache")

# 백테스트가 필요로 하는 핵심 티커
BACKTEST_TICKERS: list[str] = [
    "SPY", "QQQ", "QLD", "TQQQ", "RSP", "HYG", "LQD",
    # parking strategy 후보
    "MCD", "KO", "COST", "PEP", "JNJ",
]


# ---------------------------------------------------------------------------
# 마지막 저장일 / 무결성
# ---------------------------------------------------------------------------

def get_last_update_date(conn, ticker: str) -> str | None:
    """DB의 해당 티커 마지막 일봉 날짜(ISO). 없으면 None."""
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM market_price_history WHERE ticker=?",
            (ticker,),
        ).fetchone()
        if row is None:
            return None
        d = row["d"] if hasattr(row, "keys") else row[0]
        return d
    except Exception as e:
        log.debug("get_last_update_date(%s) 실패: %s", ticker, e)
        return None


def validate_data_integrity(conn, ticker: str) -> dict[str, Any]:
    """결측·중복 점검. 예외 비전파."""
    out: dict[str, Any] = {
        "ticker": ticker, "rows": 0, "duplicates": 0,
        "first_date": None, "last_date": None, "gap_days": None,
        "status": "unknown",
    }
    try:
        rows = conn.execute(
            "SELECT date, close FROM market_price_history "
            "WHERE ticker=? ORDER BY date",
            (ticker,),
        ).fetchall()
        if not rows:
            out["status"] = "empty"
            return out
        dates = [r["date"] if hasattr(r, "keys") else r[0] for r in rows]
        out["rows"] = len(dates)
        out["first_date"] = dates[0]
        out["last_date"] = dates[-1]
        out["duplicates"] = len(dates) - len(set(dates))
        # null close 점검
        null_close = sum(
            1 for r in rows
            if (r["close"] if hasattr(r, "keys") else r[1]) is None
        )
        out["null_close"] = null_close
        try:
            d0 = _dt.date.fromisoformat(dates[0])
            d1 = _dt.date.fromisoformat(dates[-1])
            out["gap_days"] = (d1 - d0).days
        except Exception:
            pass
        if out["duplicates"] > 0 or null_close > 0:
            out["status"] = "issues"
        elif out["rows"] < 60:
            out["status"] = "sparse"
        else:
            out["status"] = "ok"
    except Exception as e:
        log.debug("validate_data_integrity(%s) 실패: %s", ticker, e)
        out["status"] = "error"
    return out


# ---------------------------------------------------------------------------
# 다운로드 헬퍼
# ---------------------------------------------------------------------------

def _hist_to_rows(ticker: str, hist, source: str) -> list[dict[str, Any]]:
    """yfinance DataFrame → market_price_history row dict 리스트."""
    rows: list[dict[str, Any]] = []
    if hist is None:
        return rows
    try:
        if hist.empty:
            return rows
        cols = set(hist.columns)
        ts = now_iso()
        for idx, r in hist.iterrows():
            try:
                date_iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            except Exception:
                date_iso = str(idx)[:10]

            def _g(name):
                try:
                    if name in cols:
                        v = r[name]
                        return float(v) if v is not None else None
                except Exception:
                    return None
                return None

            close = _g("Close")
            if close is None:
                continue
            adj = _g("Adj Close")
            vol = _g("Volume")
            rows.append({
                "date": date_iso,
                "ticker": ticker,
                "open": _g("Open"),
                "high": _g("High"),
                "low": _g("Low"),
                "close": close,
                "adj_close": adj if adj is not None else close,
                "volume": int(vol) if vol is not None else None,
                "source": source,
                "created_at": ts,
            })
    except Exception as e:
        log.warning("[%s] hist 파싱 실패: %s", ticker, e)
    return rows


def _download_ticker(ticker: str, period: str = "max", start: str | None = None):
    """단일 티커 일봉 다운로드. 실패 시 None (예외 비전파)."""
    try:
        from .market_data import _safe_yf  # type: ignore
        yf = _safe_yf()
    except Exception as e:
        log.warning("yfinance import 실패: %s", e)
        return None
    if yf is None:
        return None
    for attempt in range(3):
        try:
            tk = yf.Ticker(ticker)
            if start:
                hist = tk.history(start=start, interval="1d", auto_adjust=False)
            else:
                hist = tk.history(period=period, interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty:
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
            log.warning("[%s] 다운로드 시도 %d 실패: %s", ticker, attempt + 1, e)
        import time as _time
        _time.sleep(1.0 * (attempt + 1))
    return None


# ---------------------------------------------------------------------------
# Public — 증분 / 전체
# ---------------------------------------------------------------------------

def append_new_market_data(conn, tickers: list[str] | None = None) -> dict[str, Any]:
    """마지막 저장일 이후 일봉만 append. 최초엔 가능한 긴 history.

    Returns: {"updated": int, "rows": int, "failed": [ticker, ...]}
    """
    from . import database as _db
    tickers = tickers or BACKTEST_TICKERS
    result: dict[str, Any] = {"updated": 0, "rows": 0, "failed": []}
    for ticker in tickers:
        try:
            last = get_last_update_date(conn, ticker)
            if last is None:
                hist = _download_ticker(ticker, period="max")
                source = "yfinance-max"
            else:
                # 마지막 날 다음날부터
                try:
                    start = (_dt.date.fromisoformat(last)
                             + _dt.timedelta(days=1)).isoformat()
                except Exception:
                    start = None
                if start and start > _dt.date.today().isoformat():
                    # 이미 최신
                    continue
                hist = _download_ticker(ticker, start=start) if start else \
                    _download_ticker(ticker, period="1mo")
                source = "yfinance-incr"
            rows = _hist_to_rows(ticker, hist, source)
            # 이미 있는 날짜 제외 (incremental 안전)
            if last is not None:
                rows = [r for r in rows if r["date"] > last]
            if not rows:
                continue
            _db.upsert_market_price_history(conn, rows)
            result["updated"] += 1
            result["rows"] += len(rows)
            log.info("[%s] %d개 일봉 append (last=%s)", ticker, len(rows), last)
        except Exception as e:
            log.warning("[%s] append 실패 (graceful): %s", ticker, e)
            result["failed"].append(ticker)
    return result


def refresh_full_history(conn, tickers: list[str] | None = None) -> dict[str, Any]:
    """전체 재다운로드 (월 1회용 — split/배당 재조정 반영)."""
    from . import database as _db
    tickers = tickers or BACKTEST_TICKERS
    result: dict[str, Any] = {"refreshed": 0, "rows": 0, "failed": []}
    for ticker in tickers:
        try:
            hist = _download_ticker(ticker, period="max")
            rows = _hist_to_rows(ticker, hist, "yfinance-full")
            if not rows:
                result["failed"].append(ticker)
                continue
            _db.upsert_market_price_history(conn, rows)
            result["refreshed"] += 1
            result["rows"] += len(rows)
            log.info("[%s] 전체 재다운로드 %d개 일봉", ticker, len(rows))
        except Exception as e:
            log.warning("[%s] refresh 실패 (graceful): %s", ticker, e)
            result["failed"].append(ticker)
    return result
