"""회고 / 성과 추적 분석.

run_research.py 의 step_update_performance_tracking 가 매일 수익률을 계산해 DB에
저장하면, 이 모듈이 그 데이터를 집계해 회고 리포트용 dict 를 만든다.

UI(app.py 의 render_retrospective)는 이 모듈 함수만 호출.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from . import database as db


def fetch_decision_outcomes(conn) -> list[dict[str, Any]]:
    """모든 결정 + 가장 최근 performance 를 조인.

    return list of {decision_id, date, ticker, action_tag, final_score,
                    entry_price, current_price, return_pct, days_held, outcome_tag}
    """
    rows = conn.execute(
        """
        SELECT d.*, p.return_1w, p.return_1m, p.return_3m, p.return_6m,
               p.outcome_tag, p.check_date
        FROM decision_log d
        LEFT JOIN performance_tracking p
          ON p.decision_id = d.decision_id
          AND p.check_date = (
            SELECT MAX(check_date) FROM performance_tracking
            WHERE decision_id = d.decision_id
          )
        ORDER BY d.date DESC, d.decision_id DESC
        """
    ).fetchall()

    out: list[dict[str, Any]] = []
    today = _dt.date.today()
    for r in rows:
        d = dict(r)
        try:
            decision_date = _dt.date.fromisoformat(d["date"])
            d["days_held"] = (today - decision_date).days
        except Exception:
            d["days_held"] = None
        # 가장 의미있는 수익률 (1w → 1m → 3m → 6m 순으로 폴백)
        d["return_pct"] = (
            d.get("return_6m") or d.get("return_3m")
            or d.get("return_1m") or d.get("return_1w")
        )
        # 최신 가격 (price_snapshot 에서 join 안 했으니 별도 조회)
        latest = db.fetch_latest_price_snapshot(conn, d["ticker"])
        d["current_price"] = latest[0]["current_price"] if latest else None
        out.append(d)
    return out


def aggregate_outcomes(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """outcome_tag 분포 + 평균 수익률 + 액션 태그별 hit ratio."""
    if not decisions:
        return {
            "total": 0, "outcomes": {}, "avg_return": None,
            "by_action_tag": {},
        }

    outcomes: dict[str, int] = {}
    by_tag: dict[str, dict] = {}
    rets: list[float] = []
    for d in decisions:
        ot = d.get("outcome_tag") or "미확정"
        outcomes[ot] = outcomes.get(ot, 0) + 1
        tag = d.get("action_tag") or "—"
        bt = by_tag.setdefault(tag, {"count": 0, "wins": 0, "losses": 0, "rets": []})
        bt["count"] += 1
        if ot == "맞음":
            bt["wins"] += 1
        elif ot == "틀림":
            bt["losses"] += 1
        rp = d.get("return_pct")
        if rp is not None:
            rets.append(rp)
            bt["rets"].append(rp)

    avg_ret = sum(rets) / len(rets) if rets else None

    # action tag 별 hit ratio
    tag_summary = {}
    for tag, info in by_tag.items():
        avg = sum(info["rets"]) / len(info["rets"]) if info["rets"] else None
        confirmed = info["wins"] + info["losses"]
        hit_ratio = (info["wins"] / confirmed) if confirmed > 0 else None
        tag_summary[tag] = {
            "count": info["count"],
            "wins": info["wins"],
            "losses": info["losses"],
            "hit_ratio": hit_ratio,
            "avg_return": avg,
        }

    return {
        "total": len(decisions),
        "outcomes": outcomes,
        "avg_return": avg_ret,
        "by_action_tag": tag_summary,
    }


def find_missed_opportunities(conn, min_return: float = 0.20) -> list[dict[str, Any]]:
    """관찰만 했는데 +20%↑ 오른 종목 (False Negative).

    decision_log에 없거나 Watchlist/Wait for Entry 였는데 큰 상승.
    """
    # 단순화: 최근 90일 내 score가 Watchlist/Wait for Entry였던 종목 중,
    # 현재 1y_return이 min_return 이상인 종목.
    cutoff = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
    cur = conn.execute(
        """
        SELECT DISTINCT s.ticker, s.action_tag, s.final_score, p.return_1y, u.name_ko
        FROM scores s
        JOIN price_snapshot p ON p.date=s.date AND p.ticker=s.ticker
        LEFT JOIN universe u ON u.ticker=s.ticker
        WHERE s.date >= ?
          AND s.action_tag IN ('Watchlist','Wait for Entry')
          AND p.return_1y IS NOT NULL
          AND p.return_1y >= ?
        ORDER BY p.return_1y DESC
        LIMIT 10
        """,
        (cutoff, min_return),
    )
    return [dict(r) for r in cur.fetchall()]


def find_false_positives(conn, max_return: float = -0.10) -> list[dict[str, Any]]:
    """Research Now / Quality Dislocation 추천했는데 -10% 이상 빠진 종목."""
    rows = conn.execute(
        """
        SELECT d.decision_id, d.date, d.ticker, d.action_tag, d.final_score,
               p.return_1m, p.return_3m, p.outcome_tag, u.name_ko
        FROM decision_log d
        JOIN performance_tracking p ON p.decision_id = d.decision_id
        LEFT JOIN universe u ON u.ticker = d.ticker
        WHERE d.action_tag IN ('Research Now', 'Quality Dislocation')
          AND p.outcome_tag = '틀림'
        ORDER BY d.date DESC
        LIMIT 10
        """
    ).fetchall()
    return [dict(r) for r in rows]


def build_retrospective_report(conn) -> dict[str, Any]:
    """회고 리포트 통합 dict — UI 가 한 번에 받아 그대로 렌더."""
    decisions = fetch_decision_outcomes(conn)
    summary = aggregate_outcomes(decisions)
    missed = find_missed_opportunities(conn)
    false_pos = find_false_positives(conn)

    return {
        "decisions": decisions[:50],
        "summary": summary,
        "missed_opportunities": missed,
        "false_positives": false_pos,
    }
