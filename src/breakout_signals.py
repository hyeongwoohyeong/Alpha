"""신고가 Break-out 신호 — Momentum 강함.

학술/시장 검증 (Mark Minervini SEPA, Jesse Livermore):
  - 52W 신고가 도달 + 거래량 +50% vs 평균 → 다음 3M outperform
  - 신고가 + base build (수개월 횡보 후 break-out) → 가장 강한 단일 신호

신호 정의:
  - 현재가 ≥ 52W high × 0.95 (신고가권 진입)
  - 5일 평균 거래량 ≥ 20일 평균 거래량 × 1.5
  - 1M return ≥ +5% (단순 회복 X, 추세 진행중)
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger

log = get_logger("breakout_signals")

NEW_HIGH_THRESHOLD = 0.95           # 52W high 의 95% 이상 = 신고가권
VOL_SURGE_MULTIPLIER = 1.5          # 5일 평균 > 20일 평균 × 1.5
MIN_MONTH_RETURN = 0.05             # 1M +5% 이상 = 추세 진행


def evaluate_breakout_signal(
    closes: list[float],
    volumes: list[int] | list[float],
) -> dict[str, Any]:
    """가격·거래량 series → break-out 신호 평가.

    closes / volumes: 과거 → 최신 순, 최소 252일 (52주)
    """
    out: dict[str, Any] = {
        "available": False,
        "is_at_52w_high": False,
        "is_volume_surge": False,
        "is_uptrend": False,
        "signal_strength": 0.0,
        "current_price": None,
        "high_52w": None,
        "dd_from_high": None,
    }

    if not closes or len(closes) < 100:
        return out

    out["available"] = True
    current = closes[-1]
    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    out["current_price"] = current
    out["high_52w"] = high_52w
    out["dd_from_high"] = (current / high_52w) - 1.0 if high_52w else 0

    # 1) 신고가 도달
    out["is_at_52w_high"] = current >= high_52w * NEW_HIGH_THRESHOLD

    # 2) 거래량 surge
    if volumes and len(volumes) >= 20:
        recent_5 = sum(volumes[-5:]) / 5
        recent_20 = sum(volumes[-20:]) / 20
        if recent_20 > 0:
            out["is_volume_surge"] = recent_5 >= recent_20 * VOL_SURGE_MULTIPLIER
            out["vol_5d_vs_20d"] = recent_5 / recent_20

    # 3) Uptrend — 1M return
    if len(closes) >= 20:
        month_return = (current / closes[-20]) - 1.0 if closes[-20] else 0
        out["is_uptrend"] = month_return >= MIN_MONTH_RETURN
        out["month_return"] = month_return

    # Signal strength 0~100
    s = 0.0
    if out["is_at_52w_high"]:
        # 더 가까울수록 더 강함
        ratio = current / high_52w
        s += 30 + 20 * ratio  # 0.95 = 49pt, 1.00 = 50pt
    if out["is_volume_surge"]:
        s += 25
    if out["is_uptrend"]:
        s += 25
    out["signal_strength"] = round(min(100.0, s), 1)
    return out


def evaluate_breakout_from_ticker(ticker: str) -> dict[str, Any]:
    """ticker → yfinance 1년 데이터 fetch → break-out 평가."""
    try:
        import yfinance as yf
        t = ticker.strip().upper()
        if t.isdigit() and len(t) == 6:
            t = t + ".KS"
        tk = yf.Ticker(t)
        hist = tk.history(period="1y", auto_adjust=False)
        if hist is None or hist.empty:
            # KOSDAQ 재시도
            if t.endswith(".KS"):
                tk = yf.Ticker(t.replace(".KS", ".KQ"))
                hist = tk.history(period="1y", auto_adjust=False)
            if hist is None or hist.empty:
                return {"ticker": ticker, "available": False}
    except Exception as e:
        log.debug("history fetch %s 실패: %s", ticker, e)
        return {"ticker": ticker, "available": False}

    closes = hist["Close"].tolist()
    volumes = hist["Volume"].tolist()
    result = evaluate_breakout_signal(closes, volumes)
    result["ticker"] = ticker
    return result


def is_strong_breakout(result: dict) -> bool:
    """모든 3개 조건 hit = strong break-out."""
    return (
        result.get("available")
        and result.get("is_at_52w_high")
        and result.get("is_volume_surge")
        and result.get("is_uptrend")
    )


if __name__ == "__main__":
    import sys, json
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    out = evaluate_breakout_from_ticker(ticker)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=float))
