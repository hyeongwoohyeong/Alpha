"""Cohort Relative Performance — Mag 7 평균 대비 상대 수익률.

사용 컨텍스트 (사용자 요구 2026-05-03):
    형우의 satellite 운용 strategy 가 Mag 7 cohort rotation — 시장이 가는데
    Mag 7 중 한 종목이 후행하면 매수, 결국 시장이 끌어올리리라는 가정.
    엔진이 이 strategy 를 직접 지원하기 위해 cohort 평균 대비 상대 수익률 +
    "Mag 7 Laggard" 큐를 제공.

기능:
    1. compute_mag7_cohort_returns(rows_or_md_map) — Mag 7 평균 returns 계산
    2. get_relative_performance(ticker, md, cohort) — 단일 종목의 상대 수익률
    3. detect_mag7_laggard(ticker, md, cohort, news_agg) — Laggard 큐 후보 판정
"""
from __future__ import annotations

from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Mag 7 정의 — Apple / Microsoft / Alphabet (A+C) / Amazon / Meta / Nvidia / Tesla
# ---------------------------------------------------------------------------

MAG7_TICKERS: set[str] = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
}

# Laggard 임계값
LAGGARD_3M_THRESHOLD = -0.10   # cohort 대비 -10%p 이상 후행
LAGGARD_1M_THRESHOLD = -0.05   # cohort 대비 -5%p 이상 후행 (단기)


# ---------------------------------------------------------------------------
# Cohort 평균 계산
# ---------------------------------------------------------------------------

def compute_mag7_cohort_returns(
    rows_or_md_map: Any,
) -> dict[str, Any]:
    """Mag 7 의 평균 수익률 (1Y, 3M, 1M) 계산.

    Args:
        rows_or_md_map: 다음 둘 중 하나
            - list of row dicts (각 row 에 'ticker' + 'market_data' 가 있어야 함)
            - dict {ticker: market_data}

    Returns: {
        "avg_1y": float | None,
        "avg_3m": float | None,
        "avg_1m": float | None,
        "n_available": int,
        "members": {ticker: {"r_1y", "r_3m", "r_1m"}}
    }
    """
    members: dict[str, dict[str, float | None]] = {}

    if isinstance(rows_or_md_map, dict):
        # {ticker: md} 형태
        for ticker, md in rows_or_md_map.items():
            tk = (ticker or "").upper()
            if tk not in MAG7_TICKERS:
                continue
            if not (md and md.get("available")):
                continue
            members[tk] = {
                "r_1y": md.get("1y_return"),
                "r_3m": md.get("3m_return"),
                "r_1m": md.get("1m_return"),
            }
    else:
        # list of rows
        for row in rows_or_md_map or []:
            tk = (row.get("ticker") or "").upper()
            if tk not in MAG7_TICKERS:
                continue
            md = row.get("market_data") or {}
            if not md.get("available"):
                continue
            members[tk] = {
                "r_1y": md.get("1y_return"),
                "r_3m": md.get("3m_return"),
                "r_1m": md.get("1m_return"),
            }

    def _avg(key: str) -> float | None:
        vals = [
            v[key] for v in members.values() if v.get(key) is not None
        ]
        return sum(vals) / len(vals) if vals else None

    return {
        "avg_1y": _avg("r_1y"),
        "avg_3m": _avg("r_3m"),
        "avg_1m": _avg("r_1m"),
        "n_available": len(members),
        "members": members,
    }


# ---------------------------------------------------------------------------
# 단일 종목의 cohort 대비 상대 수익률
# ---------------------------------------------------------------------------

def get_relative_performance(
    ticker: str, md: dict[str, Any] | None, cohort: dict[str, Any] | None,
) -> dict[str, Any]:
    """Mag 7 cohort 평균 대비 상대 수익률 계산.

    Returns: {
        "is_mag7": bool,
        "rel_1y": float | None,    # ticker 1Y - cohort avg 1Y (단위: 비율, 0.05 = +5%p)
        "rel_3m": float | None,
        "rel_1m": float | None,
        "lag_score": float | None,  # 음수일수록 후행 강함 (간단 ranking 용)
    }
    """
    tk = (ticker or "").upper()
    is_mag7 = tk in MAG7_TICKERS
    if not is_mag7 or not md or not md.get("available") or not cohort:
        return {"is_mag7": is_mag7, "rel_1y": None, "rel_3m": None, "rel_1m": None,
                "lag_score": None}

    def _diff(ticker_key: str, avg_key: str) -> float | None:
        v = md.get(ticker_key)
        a = cohort.get(avg_key)
        if v is None or a is None:
            return None
        return float(v) - float(a)

    rel_1y = _diff("1y_return", "avg_1y")
    rel_3m = _diff("3m_return", "avg_3m")
    rel_1m = _diff("1m_return", "avg_1m")

    # lag_score — 3M 가중치 0.6, 1M 0.4 (단기 강조)
    lag_parts: list[tuple[float, float]] = []
    if rel_3m is not None:
        lag_parts.append((rel_3m, 0.6))
    if rel_1m is not None:
        lag_parts.append((rel_1m, 0.4))
    lag_score = (
        sum(v * w for v, w in lag_parts) / sum(w for _, w in lag_parts)
        if lag_parts else None
    )

    return {
        "is_mag7": True,
        "rel_1y": rel_1y, "rel_3m": rel_3m, "rel_1m": rel_1m,
        "lag_score": lag_score,
    }


# ---------------------------------------------------------------------------
# Mag 7 Laggard 큐 판정
# ---------------------------------------------------------------------------

def detect_mag7_laggard(
    ticker: str, md: dict[str, Any] | None, cohort: dict[str, Any] | None,
    news_agg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mag 7 Laggard 큐 후보 판정.

    조건:
        - ticker in Mag 7
        - 3M return < cohort avg 3M - 10%p (즉 cohort 대비 -10%p 이상 후행)
        - news_agg.urgent != True (thesis broken 신호 없음)

    Returns: {
        "is_laggard": bool,
        "lag_3m": float | None,
        "lag_1m": float | None,
        "reason": str,
        "score": float (0~100, 후행이 클수록 점수 높음 — Discovery 큐 ranking 용),
    }
    """
    rel = get_relative_performance(ticker, md, cohort)
    if not rel.get("is_mag7"):
        return {"is_laggard": False, "reason": "not in Mag 7"}

    rel_3m = rel.get("rel_3m")
    rel_1m = rel.get("rel_1m")

    if rel_3m is None:
        return {"is_laggard": False, "reason": "3M return 데이터 없음"}

    if rel_3m > LAGGARD_3M_THRESHOLD:
        return {
            "is_laggard": False,
            "lag_3m": rel_3m,
            "reason": f"cohort 대비 {rel_3m*100:.1f}%p — 임계 {LAGGARD_3M_THRESHOLD*100:.0f}%p 미달",
        }

    # urgent risk check — thesis broken 가능성 시 제외
    if news_agg and news_agg.get("urgent"):
        return {
            "is_laggard": False,
            "lag_3m": rel_3m, "lag_1m": rel_1m,
            "reason": "Cohort 후행 + urgent risk 존재 — Quality Dislocation 으로 분류 필요",
        }

    # Laggard score — 후행 magnitude × 1M 추가 페널티 (가속 후행이면 점수 ↑)
    # rel_3m = -0.10 → 50점, -0.20 → 75점, -0.30 → 90점
    base_score = min(100.0, max(0.0, abs(rel_3m) * 250 + 25))
    if rel_1m is not None and rel_1m < -0.05:
        base_score += min(15.0, abs(rel_1m) * 100)
    score = round(min(100.0, base_score), 1)

    reason_parts = [
        f"3M cohort 대비 {rel_3m*100:.1f}%p 후행"
    ]
    if rel_1m is not None:
        reason_parts.append(f"1M {rel_1m*100:.1f}%p")
    reason_parts.append("urgent risk 부재 — thesis 유지로 추정")

    return {
        "is_laggard": True,
        "lag_3m": rel_3m, "lag_1m": rel_1m,
        "reason": " · ".join(reason_parts),
        "score": score,
    }


# ---------------------------------------------------------------------------
# 편의 — rows 에서 모든 Laggard 발굴
# ---------------------------------------------------------------------------

def find_all_laggards(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """rows 전체에서 Mag 7 Laggard 발굴.

    Returns: laggard score 순 정렬된 리스트, 각 entry:
        {"ticker", "name_ko", "lag_3m", "lag_1m", "score", "reason"}
    """
    rows_list = list(rows or [])
    cohort = compute_mag7_cohort_returns(rows_list)

    laggards: list[dict[str, Any]] = []
    for row in rows_list:
        ticker = (row.get("ticker") or "").upper()
        if ticker not in MAG7_TICKERS:
            continue
        md = row.get("market_data") or {}
        news_agg = row.get("news_agg")
        result = detect_mag7_laggard(ticker, md, cohort, news_agg)
        if result.get("is_laggard"):
            laggards.append({
                "ticker": ticker,
                "name_ko": row.get("name_ko", ""),
                "lag_3m": result["lag_3m"],
                "lag_1m": result.get("lag_1m"),
                "score": result["score"],
                "reason": result["reason"],
                "row": row,   # full row 보존 (UI 에서 카드 렌더 시 사용)
            })

    laggards.sort(key=lambda x: x["score"], reverse=True)
    return laggards
