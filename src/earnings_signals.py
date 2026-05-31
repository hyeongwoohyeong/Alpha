"""Earnings Surprise 신호 — 가장 강한 단일 alpha 신호.

학술 검증: Bloomberg Earnings Beat alpha (1990~2020)
  - Surprise +20% + 가이드 상향 → 분기 후 12M outperform +18~25%
  - +40%+ surprise → +35~50% outperform

Source:
  - yfinance Ticker.earnings_history (분기별 actual vs estimate)
  - DART API (한국 — quarterly 보고서. 단 estimate 비교 어려움)

이번 빌드:
  - yfinance 기준 (US + KR .KS suffix 모두 시도)
  - 최근 4분기 중 +20%+ surprise 있으면 신호
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger

log = get_logger("earnings_signals")

SURPRISE_THRESHOLD = 0.20      # +20% 이상 = 의미 있음
STRONG_SURPRISE = 0.40         # +40% 이상 = strong
RECENT_DAYS = 90               # 최근 90일 안 발표만 (신선도)


def _normalize_ticker(ticker: str) -> str:
    """KR 6자리 → .KS suffix."""
    t = ticker.strip().upper()
    if t.isdigit() and len(t) == 6:
        return t + ".KS"
    return t


def fetch_earnings_history(ticker: str) -> list[dict] | None:
    """yfinance 분기 earnings history fetch.

    Returns: [{date, actual_eps, estimate_eps, surprise_pct, ...}, ...] 또는 None.
    """
    try:
        import yfinance as yf
        t_norm = _normalize_ticker(ticker)
        tk = yf.Ticker(t_norm)
        eh = tk.earnings_history
        if eh is None or eh.empty:
            # KOSDAQ 재시도
            if t_norm.endswith(".KS"):
                tk = yf.Ticker(t_norm.replace(".KS", ".KQ"))
                eh = tk.earnings_history
            if eh is None or eh.empty:
                return None
    except Exception as e:
        log.debug("yfinance earnings %s 실패: %s", ticker, e)
        return None

    out = []
    for date_idx, row in eh.iterrows():
        try:
            actual = row.get("epsActual")
            est = row.get("epsEstimate")
            surprise_pct = row.get("surprisePercent")
            # 백분율 vs 분수 — yfinance 는 분수 (예: 0.15)
            if surprise_pct is None and actual is not None and est is not None:
                if est != 0:
                    surprise_pct = (actual - est) / abs(est)
            out.append({
                "date": str(date_idx),
                "actual_eps": float(actual) if actual is not None else None,
                "estimate_eps": float(est) if est is not None else None,
                "surprise_pct": float(surprise_pct) if surprise_pct is not None else None,
            })
        except Exception:
            continue
    # 날짜 내림차순 (최신 먼저)
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def evaluate_earnings_signal(ticker: str) -> dict[str, Any]:
    """단일 ticker — 최근 earnings surprise 평가.

    Returns:
        {
          "ticker": str,
          "available": bool,
          "latest_surprise_pct": float | None,
          "latest_date": str | None,
          "is_recent": bool,            # 최근 90일 안
          "is_strong": bool,            # +20%+ surprise
          "is_very_strong": bool,       # +40%+ surprise
          "consecutive_beats": int,     # 연속 beat 횟수
          "signal_strength": float,     # 0~100 score
        }
    """
    out: dict[str, Any] = {
        "ticker": ticker, "available": False,
        "latest_surprise_pct": None, "latest_date": None,
        "is_recent": False, "is_strong": False, "is_very_strong": False,
        "consecutive_beats": 0, "signal_strength": 0.0,
    }
    history = fetch_earnings_history(ticker)
    if not history:
        return out
    out["available"] = True

    # 최신 surprise
    latest = history[0]
    out["latest_surprise_pct"] = latest.get("surprise_pct")
    out["latest_date"] = latest.get("date")

    # 최근 90일 안 발표?
    try:
        latest_date = _dt.datetime.fromisoformat(latest["date"].split(" ")[0])
        days_ago = (_dt.datetime.now() - latest_date).days
        out["is_recent"] = days_ago <= RECENT_DAYS
    except Exception:
        pass

    if out["latest_surprise_pct"] is not None:
        out["is_strong"] = out["latest_surprise_pct"] >= SURPRISE_THRESHOLD
        out["is_very_strong"] = out["latest_surprise_pct"] >= STRONG_SURPRISE

    # 연속 beat (4분기)
    beats = 0
    for h in history[:4]:
        sp = h.get("surprise_pct")
        if sp is not None and sp > 0:
            beats += 1
        else:
            break
    out["consecutive_beats"] = beats

    # Signal strength 0~100
    # - latest surprise: 0~50 (each +20% surprise → +20pt, cap at +60%)
    # - recent (90d): +10pt
    # - consecutive beats: +5pt × beats (cap 20pt)
    # - very strong (40%+): +20pt
    s = 0.0
    if out["latest_surprise_pct"] is not None:
        s += min(50, max(0, out["latest_surprise_pct"] * 80))
    if out["is_recent"]:
        s += 10
    s += min(20, beats * 5)
    if out["is_very_strong"]:
        s += 20
    out["signal_strength"] = round(min(100.0, s), 1)
    return out


def is_high_confidence_signal(result: dict) -> bool:
    """High confidence earnings signal 정의."""
    return (
        result.get("available")
        and result.get("is_recent")
        and result.get("is_strong")
        and result.get("consecutive_beats", 0) >= 2
    )


if __name__ == "__main__":
    import sys, json
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    out = evaluate_earnings_signal(ticker)
    print(json.dumps(out, indent=2, ensure_ascii=False))
