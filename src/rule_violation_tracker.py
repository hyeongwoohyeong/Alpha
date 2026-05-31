"""Alpha Bet 룰 위반 트래커 — 80% hit rate 유지 도구.

원리:
  - 매매 history (wealth_inputs.json 의 recent_ledger 또는 별도 trades.json)
  - 각 매매에 대해 *그 시점 룰* 과 비교
  - 룰 위반 (즉흥 매매) detect → 사후 PnL 비교

분류:
  - rule_compliant: alpha_bet exit_rules trigger 도달 후 매매
  - rule_violation: trigger 도달 X 인데 매매 (즉흥)
  - no_active_bet: alpha_bet ledger 에 없는 종목 매매 (별도 분류)

목적:
  - 매주 일요일 사용자에게 "이번 주 룰 위반 N건, PnL 영향 ±₩X" 보고
  - 즉흥 매매 비용 가시화 → behavior change
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("rule_violation_tracker")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_WEALTH_PATH = _DATA_DIR / "wealth_inputs.json"
_ALPHA_BETS_PATH = _DATA_DIR / "alpha_bets.json"


def _load_ledger() -> list[dict]:
    """매매 history — wealth_inputs.json 의 realized_pnl.recent_ledger."""
    try:
        with open(_WEALTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("realized_pnl") or {}).get("recent_ledger") or []
    except Exception as e:
        log.debug("ledger 로드 실패: %s", e)
        return []


def _load_bets() -> list[dict]:
    """alpha_bets — active + realized 둘 다."""
    try:
        with open(_ALPHA_BETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("bets") or []
    except Exception as e:
        log.debug("bets 로드 실패: %s", e)
        return []


def _match_bet(trade: dict, bets: list[dict]) -> dict | None:
    """매매와 alpha bet 매칭 — name / ticker / 부분 매칭."""
    t_name = (trade.get("name") or "").strip().lower()
    t_ticker = (trade.get("ticker") or "").strip().lower()
    if not (t_name or t_ticker):
        return None
    for b in bets:
        b_name = (b.get("name") or "").strip().lower()
        b_ticker = (b.get("ticker") or "").strip().lower()
        if t_ticker and b_ticker and t_ticker == b_ticker:
            return b
        if t_name and b_name:
            # 부분 매칭 (한글 키워드 위주)
            for kw in ["하이닉스", "soxl", "tqqq", "qld", "tiger 반도체",
                       "kodex", "btc", "비트코인", "eth", "sol"]:
                if kw in t_name and kw in b_name:
                    return b
    return None


def classify_trade(trade: dict, bets: list[dict]) -> dict:
    """단일 매매 분류 → rule_compliant / rule_violation / no_active_bet.

    Compliant 정의 (간소화 — 정확한 trigger 시점 비교는 복잡):
      - trade 가 alpha_bet 의 realized 항목과 매칭 (date 근처) → compliant
      - alpha_bet 매칭되지만 exit_rules trigger 와 무관 → violation (즉흥)
      - alpha_bet 매칭 X → no_active_bet (별도 의사결정)
    """
    bet = _match_bet(trade, bets)
    out = {
        "trade_date": trade.get("date"),
        "name": trade.get("name"),
        "ticker": trade.get("ticker"),
        "pnl_krw": float(trade.get("netPnL") or trade.get("krwPnL") or 0),
        "bet_id": None,
        "bet_name": None,
        "classification": "no_active_bet",
        "reason": "alpha_bets ledger 에 매칭 X — 별도 의사결정 매매",
    }
    if not bet:
        return out

    out["bet_id"] = bet.get("id")
    out["bet_name"] = bet.get("name")

    # bet 의 realized 정보로 사후 판단
    realized = bet.get("realized") or {}
    if not realized:
        # 아직 진행중 bet 인데 매매 한 경우 — 의심 (룰 trigger 아직 X)
        out["classification"] = "rule_violation"
        out["reason"] = "active bet (룰 미발화) 인데 매매 — 즉흥 가능성"
        return out

    # realized 매매 — date 비교
    bet_date = realized.get("date") or ""
    trade_date = trade.get("date") or ""
    # 같은 날짜 (±5일) 면 compliant 으로 추정
    try:
        if bet_date and trade_date:
            bd = _dt.date.fromisoformat(bet_date.split("~")[0].strip()[:10])
            td = _dt.date.fromisoformat(trade_date[:10])
            diff = abs((td - bd).days)
            if diff <= 5:
                out["classification"] = "rule_compliant"
                out["reason"] = f"alpha_bet realized date 와 ±{diff}일 — 룰 매매로 추정"
                return out
    except Exception:
        pass

    # 그 외 — 위반 추정
    out["classification"] = "rule_violation"
    out["reason"] = "alpha_bet realized 와 date mismatch — 룰 외 매매 가능성"
    return out


def analyze_violations(days_back: int = 30) -> dict[str, Any]:
    """최근 N일 매매 룰 위반 분석.

    Returns:
        {
          "total_trades": int,
          "compliant": int, "violation": int, "no_active_bet": int,
          "compliant_pnl_krw": float, "violation_pnl_krw": float, "other_pnl_krw": float,
          "violation_details": [...],
        }
    """
    trades = _load_ledger()
    bets = _load_bets()

    cutoff = _dt.date.today() - _dt.timedelta(days=days_back)
    recent_trades = []
    for t in trades:
        try:
            td = _dt.date.fromisoformat((t.get("date") or "")[:10])
            if td >= cutoff:
                recent_trades.append(t)
        except Exception:
            continue

    classified = [classify_trade(t, bets) for t in recent_trades]

    summary = {
        "total_trades": len(classified),
        "compliant": 0, "violation": 0, "no_active_bet": 0,
        "compliant_pnl_krw": 0.0,
        "violation_pnl_krw": 0.0,
        "other_pnl_krw": 0.0,
        "violation_details": [],
        "compliance_rate": 0.0,
    }
    for c in classified:
        cls = c["classification"]
        pnl = c["pnl_krw"]
        if cls == "rule_compliant":
            summary["compliant"] += 1
            summary["compliant_pnl_krw"] += pnl
        elif cls == "rule_violation":
            summary["violation"] += 1
            summary["violation_pnl_krw"] += pnl
            summary["violation_details"].append(c)
        else:
            summary["no_active_bet"] += 1
            summary["other_pnl_krw"] += pnl

    bet_total = summary["compliant"] + summary["violation"]
    if bet_total > 0:
        summary["compliance_rate"] = summary["compliant"] / bet_total

    return summary


def format_weekly_violation_report() -> list[str]:
    """주간 룰 위반 다이제스트 — 텔레그램 일요일 브리핑용."""
    summary = analyze_violations(days_back=7)
    if summary["total_trades"] == 0:
        return ["📋 이번 주 매매 룰 점검", "  매매 없음 — 작전 정상 진행 ✓"]

    lines = ["📋 이번 주 매매 룰 점검"]
    lines.append(f"  총 매매: {summary['total_trades']}건 "
                 f"(룰 준수 {summary['compliant']}, 위반 {summary['violation']}, "
                 f"별도 {summary['no_active_bet']})")
    if summary["violation"] > 0:
        compliance = summary["compliance_rate"] * 100
        lines.append(f"  ⚠️ 룰 준수율: {compliance:.0f}% "
                     f"(즉흥 매매 PnL ₩{summary['violation_pnl_krw']/1e6:+.1f}M)")
        for v in summary["violation_details"][:3]:
            lines.append(f"     • {v['name']} {v['trade_date']} — {v['reason']}")
    else:
        lines.append(f"  ✓ 룰 준수율 100% — 80% hit rate 유지 path 위에 있음")
    return lines


if __name__ == "__main__":
    summary = analyze_violations(days_back=90)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=float))
    print()
    print("\n".join(format_weekly_violation_report()))
