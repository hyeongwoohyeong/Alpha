"""우량주 과매도(Quality Dislocation) 후보 추출."""
from __future__ import annotations

from typing import Any


QUALITY_CATEGORIES = {
    "Quality Platform",
    "AI Infrastructure",
    "Healthcare Infrastructure",
    "Defense",
    "Energy Security",
}


def is_quality_dislocation(row: dict[str, Any]) -> bool:
    md = row.get("market_data") or {}
    scores = row.get("scores") or {}
    news_agg = row.get("news_agg") or {}
    if not md.get("available"):
        return False

    dd = md.get("drawdown_from_52w_high")
    if dd is None:
        return False
    abs_dd = -dd

    if news_agg.get("urgent"):
        return False

    if abs_dd < 0.20:
        return False
    if abs_dd > 0.55:
        return False

    quality_ok = (scores.get("quality") or 0) >= 55 or row.get("category") in QUALITY_CATEGORIES
    if not quality_ok:
        return False

    # 뉴스가 매우 부정적인 경우는 별도 (Need Thesis Check 영역)
    if (news_agg.get("score_sum") or 0) < -2.0:
        return False
    return True


def list_dislocation(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    cands = [r for r in rows if is_quality_dislocation(r)]
    # drawdown 큰 순 → final_score 높은 순으로 정렬
    cands.sort(
        key=lambda r: (
            -(-(r["market_data"].get("drawdown_from_52w_high") or 0)),
            -(r["scores"].get("final_score") or 0),
        )
    )
    return cands[:limit]
