"""Growth Momentum Score — hyper-growth 종목 자동 감지.

목적: +100% 가능 종목 (AppLovin/한미반도체/팔란티어 패턴) 사전 발굴.

Score 0-100 4 components:
  - 40pt: 최근 4Q YoY 매출 성장률 평균
  - 30pt: 가속 패턴 (성장률 monotonic 상승)
  - 15pt: 안정성 (분기 stdev < 10%)
  - 15pt: EPS 추세 (직전 4Q vs 그 전 4Q)

Triggers:
  - score ≥ 70: hyper-growth 후보
  - score ≥ 80: +100% Watch 자격

Data source:
  - yfinance Ticker.quarterly_income_stmt (글로벌, 한국 .KS/.KQ)
  - 실패 시 graceful — score=None
"""
from __future__ import annotations

import statistics
from typing import Any

from .utils import get_logger

log = get_logger("growth_momentum")


def _normalize_ticker(ticker: str) -> str:
    """KR 종목 6자리 숫자면 .KS suffix. 이미 .KS/.KQ 있으면 그대로."""
    t = ticker.strip().upper()
    if t.isdigit() and len(t) == 6:
        # KOSDAQ 우선 .KQ 시도 — 실패 시 .KS fallback (호출부에서 처리)
        return t + ".KS"
    return t


def _fetch_quarterly_revenues(ticker: str) -> tuple[list[float], list[float]] | None:
    """yfinance 로 분기 매출·순이익 fetch.

    Returns (revenues, eps_or_net_income) 또는 None.
    Series 는 *과거 → 최신* 순으로 정렬.
    """
    try:
        import yfinance as yf
        t_norm = _normalize_ticker(ticker)
        tk = yf.Ticker(t_norm)
        qs = tk.quarterly_income_stmt
        if qs is None or qs.empty:
            # KR .KS 실패 시 .KQ 재시도
            if t_norm.endswith(".KS"):
                tk = yf.Ticker(t_norm.replace(".KS", ".KQ"))
                qs = tk.quarterly_income_stmt
            if qs is None or qs.empty:
                return None
    except Exception as e:
        log.debug("yfinance fetch %s 실패: %s", ticker, e)
        return None

    # Revenue 추출 — 컬럼 순서 yfinance 는 최신 → 과거이므로 reverse
    try:
        revenue_row = None
        for key in ["Total Revenue", "Operating Revenue", "Revenue"]:
            if key in qs.index:
                revenue_row = qs.loc[key]
                break
        if revenue_row is None:
            return None
        revenues = revenue_row.values.tolist()[::-1]  # 과거 → 최신
        revenues = [float(r) for r in revenues if r is not None and not _is_nan(r)]
    except Exception as e:
        log.debug("revenue 파싱 %s 실패: %s", ticker, e)
        return None

    # Net income 추출 (EPS proxy — 분기 변동 큼)
    try:
        ni_row = qs.loc["Net Income"] if "Net Income" in qs.index else None
        net_income = []
        if ni_row is not None:
            net_income = [float(v) for v in ni_row.values.tolist()[::-1]
                          if v is not None and not _is_nan(v)]
    except Exception:
        net_income = []

    return revenues, net_income


def _is_nan(v: Any) -> bool:
    try:
        return v != v  # nan 특성
    except Exception:
        return False


def calculate_growth_momentum_score(
    revenues: list[float],
    net_income: list[float] | None = None,
) -> dict[str, Any]:
    """Growth Momentum Score 계산.

    revenues: 분기 매출 (과거 → 최신 순), 최소 5개 필요
    net_income: 분기 순이익 (optional)

    Returns: dict with score, components, breakdown.
    """
    out: dict[str, Any] = {
        "score": None,
        "available": False,
        "yoy_growth_recent": None,
        "yoy_growth_avg_4q": None,
        "is_accelerating": False,
        "components": {},
    }

    if not revenues or len(revenues) < 5:
        out["error"] = "insufficient_data"
        return out

    # 1) YoY 매출 성장률 — 직전 4분기
    yoy_growth = []
    for i in range(4, len(revenues)):
        prev = revenues[i - 4]
        curr = revenues[i]
        if prev and prev > 0:
            yoy_growth.append((curr - prev) / prev)

    if not yoy_growth:
        out["error"] = "no_yoy_data"
        return out

    out["yoy_growth_recent"] = yoy_growth[-1]
    out["yoy_growth_avg_4q"] = sum(yoy_growth[-4:]) / min(4, len(yoy_growth))

    # 2) Component A — 평균 YoY 성장률 (40pt)
    avg_g = out["yoy_growth_avg_4q"]
    # +30% YoY → 30pt, +50% YoY → 40pt (capped)
    s_avg = min(40.0, max(0.0, avg_g * 100))

    # 3) Component B — 가속 (30pt)
    # 최근 3분기 YoY 가 monotonic 증가 (또는 직전 분기가 가장 큼)
    s_accel = 0.0
    if len(yoy_growth) >= 3:
        recent3 = yoy_growth[-3:]
        if recent3[0] < recent3[1] < recent3[2]:
            s_accel = 30.0  # 완전 가속
        elif recent3[2] > recent3[1] and recent3[2] > recent3[0]:
            s_accel = 20.0  # 직전 분기 강함
        elif recent3[2] > avg_g:
            s_accel = 10.0

    out["is_accelerating"] = s_accel > 0

    # 4) Component C — 안정성 (15pt) — 변동 너무 크면 -
    s_stable = 0.0
    if len(yoy_growth) >= 4:
        try:
            stdev = statistics.stdev(yoy_growth[-4:])
            if stdev < 0.10:
                s_stable = 15.0
            elif stdev < 0.20:
                s_stable = 10.0
            elif stdev < 0.30:
                s_stable = 5.0
        except Exception:
            pass

    # 5) Component D — EPS / Net income 추세 (15pt)
    s_eps = 0.0
    if net_income and len(net_income) >= 8:
        recent_4 = net_income[-4:]
        prev_4 = net_income[-8:-4]
        if all(x is not None for x in recent_4 + prev_4):
            avg_recent = sum(recent_4) / 4
            avg_prev = sum(prev_4) / 4
            if avg_prev > 0:
                eps_growth = (avg_recent - avg_prev) / avg_prev
                s_eps = min(15.0, max(0.0, eps_growth * 30))
            elif avg_prev <= 0 and avg_recent > 0:
                s_eps = 15.0  # 적자 → 흑자 전환

    total = s_avg + s_accel + s_stable + s_eps
    out["score"] = round(min(100.0, total), 1)
    out["components"] = {
        "avg_growth_pt": round(s_avg, 1),
        "acceleration_pt": round(s_accel, 1),
        "stability_pt": round(s_stable, 1),
        "eps_trend_pt": round(s_eps, 1),
    }
    out["available"] = True
    return out


def score_ticker(ticker: str) -> dict[str, Any]:
    """단일 ticker → Growth Momentum Score. 외부 API 사용."""
    fetched = _fetch_quarterly_revenues(ticker)
    if not fetched:
        return {"ticker": ticker, "score": None, "available": False,
                "error": "fetch_failed"}
    revenues, net_income = fetched
    result = calculate_growth_momentum_score(revenues, net_income)
    result["ticker"] = ticker
    return result


def score_universe(tickers: list[str], max_concurrent: int = 5) -> list[dict]:
    """Universe 전체 score 일괄 계산. concurrent 호출.

    Returns: score 내림차순 정렬된 list.
    """
    results: list[dict] = []
    try:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=max_concurrent) as ex:
            for r in ex.map(score_ticker, tickers):
                results.append(r)
    except Exception as e:
        log.warning("concurrent score 실패, 순차 fallback: %s", e)
        results = [score_ticker(t) for t in tickers]

    # 점수 내림차순 (None 은 뒤로)
    results.sort(key=lambda r: r.get("score") if r.get("score") is not None else -1,
                 reverse=True)
    return results


def hyper_growth_candidates(scored: list[dict], min_score: float = 70.0) -> list[dict]:
    """Growth Momentum ≥ N 점만 필터. 기본 70."""
    return [r for r in scored if (r.get("score") or 0) >= min_score]


if __name__ == "__main__":
    # python -m src.growth_momentum APP — 단일 ticker 테스트
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.growth_momentum <ticker>")
        sys.exit(1)
    ticker = sys.argv[1]
    result = score_ticker(ticker)
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
