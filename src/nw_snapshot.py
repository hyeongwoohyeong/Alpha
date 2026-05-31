"""NW (Net Worth) 일일 snapshot — 자산 변동 정확 추적.

매일 1회 (cron) 호출:
  - portfolio.json holdings 합 (투자자산)
  - wealth_inputs.json balance_sheet (부동산/예금/부채 등)
  - 합계 = NW
  - DB nw_snapshots 에 저장 (date PK — 같은 날 update)

활용:
  - briefing 의 "자산 변동" 섹션 정확화 (어제 대비 +₩X)
  - 분양 페이스 자동 감지 (월 ₩6.5M 평균 vs 실제)
  - dashboard 의 wealth chart 정확화
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("nw_snapshot")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_PORTFOLIO_PATH = _DATA_DIR / "portfolio.json"
_WEALTH_PATH = _DATA_DIR / "wealth_inputs.json"


def calculate_current_nw() -> dict[str, Any]:
    """현재 NW 계산 — portfolio.json + wealth_inputs.json 통합."""
    out: dict[str, Any] = {
        "nw_krw": 0.0,
        "investment_krw": 0.0,
        "cash_krw": 0.0,
        "real_estate_krw": 0.0,
        "deposit_krw": 0.0,
        "debt_krw": 0.0,
        "apartment_paid_krw": 0.0,
    }

    # 1) 투자자산 — portfolio.json
    try:
        with open(_PORTFOLIO_PATH, encoding="utf-8") as f:
            pf = json.load(f) or {}
        inv = sum(float(h.get("value_krw") or 0) for h in pf.get("holdings", []))
        out["investment_krw"] = inv
    except Exception as e:
        log.debug("portfolio.json 로드 실패: %s", e)

    # 2) 부동산/예금/부채 — wealth_inputs.json
    try:
        with open(_WEALTH_PATH, encoding="utf-8") as f:
            wi = json.load(f) or {}
        bs = wi.get("balance_sheet", {})
        out["real_estate_krw"] = float(bs.get("real_estate_krw") or 0)
        out["apartment_paid_krw"] = float(bs.get("apartment_paid_krw") or 0)
        out["deposit_krw"] = float(bs.get("deposit_krw") or 0)
        out["cash_krw"] = float(bs.get("cash_outside_krw") or 0)
        out["debt_krw"] = float(bs.get("debt_krw") or 0)
    except Exception as e:
        log.debug("wealth_inputs.json 로드 실패: %s", e)

    out["nw_krw"] = (
        out["investment_krw"]
        + out["real_estate_krw"]
        + out["apartment_paid_krw"]
        + out["deposit_krw"]
        + out["cash_krw"]
        - out["debt_krw"]
    )
    return out


def save_snapshot(date_iso: str | None = None) -> dict[str, Any]:
    """오늘 NW snapshot 저장 (DB upsert)."""
    from . import database as db
    date_str = date_iso or _dt.date.today().isoformat()
    snap = calculate_current_nw()

    with db.db_session() as conn:
        db.init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO nw_snapshots "
            "(snapshot_date, nw_krw, investment_krw, cash_krw, "
            "real_estate_krw, deposit_krw, debt_krw, apartment_paid_krw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date_str, snap["nw_krw"], snap["investment_krw"],
             snap["cash_krw"], snap["real_estate_krw"],
             snap["deposit_krw"], snap["debt_krw"],
             snap["apartment_paid_krw"]),
        )
        conn.commit()
    log.info("NW snapshot saved: %s ₩%.0fM", date_str, snap["nw_krw"]/1e6)
    return {**snap, "date": date_str}


def get_recent_snapshots(days: int = 30) -> list[dict]:
    """최근 N일 snapshot 조회 — 변동 분석용."""
    from . import database as db
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    with db.db_session() as conn:
        db.init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_date, nw_krw, investment_krw, debt_krw "
            "FROM nw_snapshots "
            "WHERE snapshot_date >= ? "
            "ORDER BY snapshot_date DESC",
            (cutoff,)
        )
        rows = cur.fetchall()
    return [{"date": r[0], "nw_krw": r[1], "investment_krw": r[2],
             "debt_krw": r[3]} for r in rows]


def get_delta(days_back: int = 1) -> dict[str, Any]:
    """N일 전 대비 NW 변동."""
    snapshots = get_recent_snapshots(days=days_back + 5)
    if len(snapshots) < 2:
        return {"available": False, "reason": "snapshot 데이터 부족 (≥ 2일 필요)"}

    today = snapshots[0]
    target_date = _dt.date.fromisoformat(today["date"]) - _dt.timedelta(days=days_back)

    # 가장 가까운 날짜 (target_date 또는 그 이전)
    past = None
    for s in snapshots[1:]:
        s_date = _dt.date.fromisoformat(s["date"])
        if s_date <= target_date:
            past = s
            break
    if not past:
        past = snapshots[-1]  # fallback: 가장 오래된

    delta = today["nw_krw"] - past["nw_krw"]
    pct = (delta / past["nw_krw"] * 100) if past["nw_krw"] else 0
    return {
        "available": True,
        "today_nw": today["nw_krw"],
        "past_nw": past["nw_krw"],
        "past_date": past["date"],
        "delta_krw": delta,
        "delta_pct": pct,
        "days": (_dt.date.fromisoformat(today["date"])
                 - _dt.date.fromisoformat(past["date"])).days,
    }


def check_pace_vs_target() -> dict[str, Any]:
    """분양 deadline 페이스 검증 — 매월 ₩6.5M 저축 가정.

    Returns:
        {
          "on_pace": bool,
          "monthly_nw_growth": float,  # 최근 30일 변동을 월 환산
          "target_monthly_growth": float,  # ₩6.5M
          "delta_from_target": float,
        }
    """
    delta_30d = get_delta(days_back=30)
    if not delta_30d.get("available"):
        return {"available": False}

    monthly_growth = delta_30d["delta_krw"] * (30 / max(delta_30d["days"], 1))
    target_monthly = 6_500_000  # 사용자 저축 가능 추정 (financial_decisions.md)
    return {
        "available": True,
        "monthly_nw_growth": monthly_growth,
        "target_monthly_growth": target_monthly,
        "on_pace": monthly_growth >= target_monthly,
        "delta_from_target": monthly_growth - target_monthly,
        "days_observed": delta_30d["days"],
    }


if __name__ == "__main__":
    snap = save_snapshot()
    print(json.dumps(snap, indent=2, ensure_ascii=False, default=float))
    print()
    print("최근 30일 변동:")
    print(json.dumps(get_delta(days_back=30), indent=2, ensure_ascii=False, default=float))
