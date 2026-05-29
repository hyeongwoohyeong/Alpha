"""시장 데이터 증분 캐시 — Phase 4-A 백테스트 기반.

`market_price_history` 테이블에 일봉을 누적 저장한다.
- 최초 수집: yfinance period="max" (최대한 긴 history).
- 이후: 마지막 저장일 이후 일봉만 append.
- 월 1회: 전체 재다운로드(split/배당 재조정 반영).

원칙:
- yfinance 실패해도 예외를 위로 던지지 않는다 — graceful, 실패 티커는 로그만.
- 앱/파이프라인이 이 모듈 때문에 죽지 않는다.

KR 확장 (Stage 2):
- `KR_ETF_TICKERS` + `kr_universe.csv` 종목까지 같은 `market_price_history`
  테이블에 누적. yfinance 호출 시 `.KS` 접미사를 붙이고, DB 에는 접미사
  없는 6자리 ticker (예: `005930`) 로 저장 — 엔진의 KR 티커 컨벤션과 일치.
- `KS11` (KOSPI 인덱스) 는 yfinance 심볼 `^KS11`, DB ticker 는 `KS11`.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger, now_iso

log = get_logger("data_cache")

# 백테스트가 필요로 하는 핵심 티커 (US)
BACKTEST_TICKERS: list[str] = [
    "SPY", "QQQ", "QLD", "TQQQ", "RSP", "HYG", "LQD",
    # parking strategy 후보
    "MCD", "KO", "COST", "PEP", "JNJ",
]

# KR 시장 데이터 사다리 / regime 분석에 필요한 ETF / 인덱스.
# DB ticker (.KS 미포함) 를 키, yfinance 심볼 (.KS / ^ 포함) 을 값으로 둔다.
# - 069500 KODEX 200: KOSPI 200 추종 핵심 ETF.
# - 122630 KODEX 레버리지: KOSPI 200 일간 2배.
# - 252670 KODEX 인버스2X: 인버스 (data ladder 의 forward-return 반대,
#   현재는 fetch 만 — 사다리 target 으로는 사용하지 않음).
# - KS11: KOSPI 종합 인덱스 (yfinance `^KS11`).
KR_ETF_TICKERS: dict[str, str] = {
    "069500": "069500.KS",
    "122630": "122630.KS",
    "252670": "252670.KS",
    "KS11": "^KS11",
}


def _yf_symbol_for_kr(db_ticker: str) -> str:
    """DB ticker → yfinance 심볼. 알려진 매핑 우선, 그 외엔 `.KS` 부착.

    예: '005930' → '005930.KS', 'KS11' → '^KS11', '069500' → '069500.KS'.
    이미 '.KS' / '^' 가 포함돼 있으면 그대로 반환.
    """
    if not db_ticker:
        return db_ticker
    if db_ticker in KR_ETF_TICKERS:
        return KR_ETF_TICKERS[db_ticker]
    if db_ticker.endswith(".KS") or db_ticker.startswith("^"):
        return db_ticker
    # 6자리 한국 종목 코드 — 숫자 only 가정
    if db_ticker.isdigit():
        return f"{db_ticker}.KS"
    return db_ticker


def collect_kr_tickers() -> list[str]:
    """KR 캐싱 대상 DB ticker 목록.

    - `KR_ETF_TICKERS` 의 키 (069500/122630/252670/KS11)
    - `data/kr_universe.csv` 의 filter_passed=True 종목 ticker.

    CSV 가 비었거나 (mock 데이터만 있는 sandbox) 로드 실패해도 ETF 만 반환.
    """
    out: list[str] = list(KR_ETF_TICKERS.keys())
    try:
        from .kr_universe import load_kr_universe
        rows = load_kr_universe() or []
        seen = set(out)
        for r in rows:
            t = (r.get("ticker") or "").strip()
            if not t or t in seen:
                continue
            # filter_passed 가 False/None 이면 skip (mock 행 제외)
            if r.get("filter_passed") is False:
                continue
            seen.add(t)
            out.append(t)
    except Exception as e:
        log.debug("kr_universe 로드 실패 (graceful): %s", e)
    return out


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


def _download_ticker(
    ticker: str, period: str = "max", start: str | None = None,
    yf_symbol: str | None = None,
):
    """단일 티커 일봉 다운로드. 실패 시 None (예외 비전파).

    `yf_symbol` 이 주어지면 yfinance 호출에 그 심볼을 쓰고, DB 저장 시점의
    ticker 와 분리한다 — KR 티커(`005930` ↔ `005930.KS`) 호환용.
    """
    try:
        from .market_data import _safe_yf  # type: ignore
        yf = _safe_yf()
    except Exception as e:
        log.warning("yfinance import 실패: %s", e)
        return None
    if yf is None:
        return None
    sym = yf_symbol or ticker
    for attempt in range(3):
        try:
            tk = yf.Ticker(sym)
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

def append_new_market_data(
    conn, tickers: list[str] | None = None,
    symbol_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """마지막 저장일 이후 일봉만 append. 최초엔 가능한 긴 history.

    symbol_map: DB ticker → yfinance 심볼 매핑 (KR 호환).
    None 이면 ticker 자체를 yfinance 심볼로 사용.

    Returns: {"updated": int, "rows": int, "failed": [ticker, ...]}
    """
    from . import database as _db
    tickers = tickers or BACKTEST_TICKERS
    symbol_map = symbol_map or {}
    result: dict[str, Any] = {"updated": 0, "rows": 0, "failed": []}
    for ticker in tickers:
        try:
            yf_sym = symbol_map.get(ticker)
            last = get_last_update_date(conn, ticker)
            if last is None:
                hist = _download_ticker(ticker, period="max", yf_symbol=yf_sym)
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
                hist = (_download_ticker(ticker, start=start, yf_symbol=yf_sym)
                        if start else
                        _download_ticker(ticker, period="1mo", yf_symbol=yf_sym))
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


def refresh_full_history(
    conn, tickers: list[str] | None = None,
    symbol_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """전체 재다운로드 (월 1회용 — split/배당 재조정 반영).

    symbol_map: DB ticker → yfinance 심볼 매핑 (KR 호환).
    """
    from . import database as _db
    tickers = tickers or BACKTEST_TICKERS
    symbol_map = symbol_map or {}
    result: dict[str, Any] = {"refreshed": 0, "rows": 0, "failed": []}
    for ticker in tickers:
        try:
            yf_sym = symbol_map.get(ticker)
            hist = _download_ticker(ticker, period="max", yf_symbol=yf_sym)
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


# ---------------------------------------------------------------------------
# KR 전용 — `.KS` 접미사 자동 변환을 포함한 thin wrapper
# ---------------------------------------------------------------------------

def _build_kr_symbol_map(tickers: list[str]) -> dict[str, str]:
    """DB ticker 리스트에 대해 yfinance 심볼 매핑 dict 를 만든다."""
    return {t: _yf_symbol_for_kr(t) for t in tickers}


def append_new_kr_market_data(
    conn, tickers: list[str] | None = None,
) -> dict[str, Any]:
    """KR 티커(universe + ETF) 증분 append.

    tickers 가 None 이면 `collect_kr_tickers()` 사용. ticker 는 DB 컨벤션
    (접미사 없음, 예 `005930`) 이고 yfinance 호출 시 `.KS` 자동 부착.

    KR 시장이 휴장이거나 yfinance 가 KR 종목을 차단해도 graceful — 실패
    티커는 result["failed"] 에 누적되고 파이프라인은 죽지 않는다.
    """
    tickers = tickers or collect_kr_tickers()
    if not tickers:
        log.info("KR ticker 가 비어 있음 (kr_universe.csv 미생성?) — KR fetch skip")
        return {"updated": 0, "rows": 0, "failed": []}
    smap = _build_kr_symbol_map(tickers)
    log.info("KR 증분 fetch — %d 티커 (예: %s → %s)",
             len(tickers), tickers[0], smap.get(tickers[0]))
    return append_new_market_data(conn, tickers, symbol_map=smap)


def refresh_full_kr_history(
    conn, tickers: list[str] | None = None,
) -> dict[str, Any]:
    """KR 티커 전체 재다운로드 (월초용)."""
    tickers = tickers or collect_kr_tickers()
    if not tickers:
        log.info("KR ticker 가 비어 있음 — KR refresh skip")
        return {"refreshed": 0, "rows": 0, "failed": []}
    smap = _build_kr_symbol_map(tickers)
    log.info("KR 전체 재다운로드 — %d 티커", len(tickers))
    return refresh_full_history(conn, tickers, symbol_map=smap)
