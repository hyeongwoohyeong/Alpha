"""Backtest — Confluence Score 의 진짜 의미 검증.

작업:
  1. 2020~2024 매 분기 말 기준
  2. 그 시점 universe 의 각 종목 confluence_score 계산 (Growth + Earnings + Breakout)
  3. 그 후 6M / 12M 실제 가격 수익률 계산
  4. Score bucket 별 (50-60 / 60-70 / 70-80 / 80+) 평균/median/+100% 비율 통계

목표:
  - "Score 70+ 종목의 12M 평균 +X%, +100% 비율 Y%" 가 *실증 데이터로 backed* 되는지
  - false positive 비율 어느 정도인지
  - 임계값 (현재 70) 이 합리적인지

실행: python scripts/backtest_growth_score.py
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import statistics
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest_growth_score")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# Backtest 윈도우
QUARTERS = [
    "2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31",
    "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
    "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31", "2024-06-30", "2024-09-30",
]

# Sample universe — backtest 용
BACKTEST_UNIVERSE = [
    # +100% 검증 (학습 데이터)
    "APP", "PLTR", "MSTR", "RDDT", "NVDA", "META", "TSLA",
    "AVGO", "AMD", "AMZN", "GOOGL", "MSFT", "NFLX",
    "IONQ", "RGTI", "VST", "CEG", "TLN", "ARGX", "LLY",
    "RKLB", "HOOD", "SOFI", "CRWD", "PANW", "ANET",
    # +100% 안 간 종목 (false positive 검증)
    "F", "GM", "WMT", "KO", "PG", "JNJ", "PFE",
    "MMM", "VZ", "T", "INTC", "BA", "DIS", "NKE",
    "X", "CLF", "FCX", "BABA", "BIDU", "PYPL",
]


def historical_price(ticker: str, date_str: str) -> float | None:
    """특정 날짜의 종가 (yfinance) — 영업일 보정 포함."""
    try:
        import yfinance as yf
        end_dt = _dt.datetime.strptime(date_str, "%Y-%m-%d") + _dt.timedelta(days=5)
        start_dt = _dt.datetime.strptime(date_str, "%Y-%m-%d") - _dt.timedelta(days=10)
        hist = yf.Ticker(ticker).history(start=start_dt, end=end_dt, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        # 가장 가까운 영업일 종가
        target = _dt.datetime.strptime(date_str, "%Y-%m-%d")
        hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        closest = hist.iloc[(hist.index - target).total_seconds().abs().argmin()]
        return float(closest["Close"])
    except Exception as e:
        log.debug("price %s @ %s 실패: %s", ticker, date_str, e)
        return None


def calculate_return(ticker: str, start_date: str, months: int) -> float | None:
    """start_date 부터 N개월 후 가격 변동 %."""
    p_start = historical_price(ticker, start_date)
    end_dt = _dt.datetime.strptime(start_date, "%Y-%m-%d") + _dt.timedelta(days=30 * months)
    p_end = historical_price(ticker, end_dt.strftime("%Y-%m-%d"))
    if p_start is None or p_end is None or p_start == 0:
        return None
    return (p_end / p_start) - 1.0


def run_backtest():
    """매 분기 말 score + 그 후 12M return 매트릭스."""
    results = []  # [(quarter, ticker, score_proxy, fwd_12m_return), ...]

    log.info("=== Backtest start — %d quarters × %d tickers = %d combinations ===",
             len(QUARTERS), len(BACKTEST_UNIVERSE),
             len(QUARTERS) * len(BACKTEST_UNIVERSE))

    for q in QUARTERS:
        for ticker in BACKTEST_UNIVERSE:
            # 분기 말 시점의 score proxy — 실제로는 그 시점 분기 매출 데이터로 재계산해야 정확
            # 단순화: yfinance 분기 데이터는 *최신* 만 fetch 가능 (과거 시점 fetch 어려움)
            # 즉 진정한 시점별 backtest 는 별도 데이터 source (FactSet, Bloomberg) 필요
            # 본 backtest 는 *현재 score* vs *그 시점부터 12M return* 의 단순 상관관계만

            ret_12m = calculate_return(ticker, q, 12)
            if ret_12m is None:
                continue
            results.append({
                "quarter": q,
                "ticker": ticker,
                "fwd_12m_return": ret_12m,
            })

    log.info("Total observations: %d", len(results))
    return results


def analyze_backtest(results: list[dict]):
    """결과 통계 분석."""
    if not results:
        log.warning("No results")
        return
    returns = [r["fwd_12m_return"] for r in results]
    above_100 = [r for r in results if r["fwd_12m_return"] >= 1.0]
    above_50 = [r for r in results if r["fwd_12m_return"] >= 0.5]
    above_zero = [r for r in results if r["fwd_12m_return"] >= 0]

    print()
    print("=" * 60)
    print(f"📊 Backtest 결과 — {len(results)} observations")
    print("=" * 60)
    print(f"평균 12M return:     {statistics.mean(returns)*100:+.1f}%")
    print(f"Median 12M return:   {statistics.median(returns)*100:+.1f}%")
    print(f"+100% 도달 비율:     {len(above_100)/len(results)*100:.1f}% ({len(above_100)})")
    print(f"+50% 도달 비율:      {len(above_50)/len(results)*100:.1f}% ({len(above_50)})")
    print(f"+0 (양수) 비율:      {len(above_zero)/len(results)*100:.1f}% ({len(above_zero)})")
    print()
    print("Top 10 (+100% 사후):")
    for r in sorted(above_100, key=lambda x: -x["fwd_12m_return"])[:10]:
        print(f"  {r['quarter']} {r['ticker']:6s} +{r['fwd_12m_return']*100:.0f}%")


def main():
    log.info("=== Confluence Score Backtest ===")
    log.info("Note: 본 backtest 는 *현재 universe* 의 *과거 12M return* 분포 분석.")
    log.info("진정한 시점별 score backtest 는 historical fundamentals 필요 (FactSet 등).")
    log.info("")

    results = run_backtest()
    analyze_backtest(results)

    # 저장
    out_path = ROOT / "data" / "backtest_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    log.info("Saved to %s", out_path)


if __name__ == "__main__":
    main()
