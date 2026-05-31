"""Alpha Bet 사후 채점 — realized 베팅 vs benchmark.

학습 누적 목적:
  - alpha_bets.realized 항목 각각에 *그 기간 SPY/QQQ 수익률* 비교
  - Outperform 인지 확인 — 단순 시장 추세인지 진짜 alpha 인지
  - hit rate 80% 가 *진짜 alpha* 인지 *시장 운* 인지 분리

채점 기준:
  - alpha_outperform = bet_gain_pct - benchmark_gain_pct
  - +5%+ 면 strong alpha
  - -5%~+5% 면 시장 추세 정도 (no edge)
  - -5%- 면 underperform (시장보다 못함)

Benchmark 선택:
  - 미국 종목/ETF: QQQ
  - 한국 종목: KODEX 200 (069500)
  - 크립토: SPY (or BTC 자체)
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("alpha_bet_grading")

_ALPHA_BETS_PATH = Path(__file__).resolve().parents[1] / "data" / "alpha_bets.json"


def _benchmark_for_bet(bet: dict) -> str:
    """Bet category 별 benchmark ticker."""
    cat = (bet.get("category") or "").lower()
    ticker = (bet.get("ticker") or "").upper()
    if "crypto" in cat or "btc" in ticker or "eth" in ticker or "sol" in ticker:
        return "BTC-USD"  # crypto 는 BTC 자체 benchmark
    if "leveraged" in cat or "tactical" in cat or "etf" in cat:
        return "QQQ"  # 미국 레버리지 ETF 는 QQQ
    if any(kw in cat for kw in ["nasdaq", "us", "미국"]):
        return "QQQ"
    if "kr" in cat or any(kw in (bet.get("name") or "") for kw in ["KODEX", "TIGER", "하이닉스"]):
        return "069500.KS"  # KR ETF
    return "QQQ"  # default


def _fetch_return(ticker: str, start_date: str, end_date: str) -> float | None:
    """yfinance 로 기간 수익률 fetch."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=start_date, end=end_date, auto_adjust=False)
        if hist is None or hist.empty or len(hist) < 2:
            return None
        p0 = float(hist["Close"].iloc[0])
        p1 = float(hist["Close"].iloc[-1])
        return (p1 / p0 - 1.0) if p0 else None
    except Exception as e:
        log.debug("benchmark fetch 실패 %s: %s", ticker, e)
        return None


def grade_realized_bet(bet: dict) -> dict[str, Any]:
    """단일 realized bet 채점."""
    out = {
        "bet_id": bet.get("id"),
        "bet_name": bet.get("name"),
        "available": False,
    }
    realized = bet.get("realized") or {}
    bet_gain_pct = realized.get("gain_pct")
    if bet_gain_pct is None:
        # gain_pct 없으면 lifetime 누적 베팅 (BTC 등) — skip
        return out
    out["bet_gain_pct"] = bet_gain_pct

    # 기간 추출
    entry_date = (bet.get("entry") or {}).get("date") or ""
    exit_date = realized.get("date") or ""
    # date 형식 보정 — "2026-04" → "2026-04-01"
    try:
        if entry_date and len(entry_date) == 7:
            entry_date = entry_date + "-01"
        if exit_date and len(exit_date) == 7:
            exit_date = exit_date + "-01"
        if not entry_date or not exit_date:
            return out
        # ISO 검증
        _dt.date.fromisoformat(entry_date[:10])
        _dt.date.fromisoformat(exit_date[:10])
    except Exception:
        return out

    benchmark = _benchmark_for_bet(bet)
    bench_ret = _fetch_return(benchmark, entry_date, exit_date)
    if bench_ret is None:
        return out

    alpha = bet_gain_pct - bench_ret
    out.update({
        "available": True,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "benchmark": benchmark,
        "benchmark_return": bench_ret,
        "alpha_outperform": alpha,
        "grade": (
            "strong_alpha" if alpha >= 0.05
            else "modest_alpha" if alpha >= 0.01
            else "no_edge" if alpha >= -0.05
            else "underperform"
        ),
    })
    return out


def grade_all_realized() -> dict[str, Any]:
    """모든 realized bet 채점 + 종합 stats."""
    try:
        with open(_ALPHA_BETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        bets = data.get("bets") or []
    except Exception as e:
        log.warning("alpha_bets.json 로드 실패: %s", e)
        return {"available": False}

    realized = [b for b in bets if b.get("status") in ("realized", "failed")]
    graded = [grade_realized_bet(b) for b in realized]
    valid = [g for g in graded if g.get("available")]
    if not valid:
        return {"available": False, "total_realized": len(realized), "graded": 0}

    alphas = [g["alpha_outperform"] for g in valid]
    avg_alpha = sum(alphas) / len(alphas)
    strong = sum(1 for g in valid if g["grade"] == "strong_alpha")
    underperform = sum(1 for g in valid if g["grade"] == "underperform")

    return {
        "available": True,
        "total_realized": len(realized),
        "graded": len(valid),
        "avg_alpha_outperform": avg_alpha,
        "strong_alpha_count": strong,
        "underperform_count": underperform,
        "edge_rate": strong / len(valid),
        "details": valid,
    }


def format_grading_report() -> list[str]:
    """주간 텔레그램용 사후 채점 다이제스트."""
    summary = grade_all_realized()
    if not summary.get("available"):
        return []
    lines = ["📊 Alpha Bet 사후 채점 (vs 시장 benchmark)"]
    lines.append(f"  {summary['graded']}건 평가 — 평균 alpha "
                 f"{summary['avg_alpha_outperform']*100:+.1f}%")
    lines.append(f"  Strong alpha (+5%+): {summary['strong_alpha_count']}건")
    if summary["underperform_count"]:
        lines.append(f"  ⚠️ Underperform: {summary['underperform_count']}건 — 시장 추세보다 못함")
    edge = summary["edge_rate"] * 100
    if edge >= 50:
        lines.append(f"  ✓ Edge rate {edge:.0f}% — 진짜 alpha 입증")
    elif edge >= 30:
        lines.append(f"  ◐ Edge rate {edge:.0f}% — 부분적 alpha")
    else:
        lines.append(f"  · Edge rate {edge:.0f}% — 시장 추세 수준, edge 불확실")
    return lines


if __name__ == "__main__":
    summary = grade_all_realized()
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=float))
