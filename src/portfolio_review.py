"""내 포트폴리오 리뷰 — data/portfolio.json 의 실제 보유 종목을 읽어
포트폴리오 레벨 진단 + 포지션별 리뷰 + Market Regime 연계 코멘트를 생성.

원칙 (Phase 1·2 모듈과 동일):
- Rule-based. LLM 없이 완전 동작.
- portfolio.json 이 없거나 깨져도 예외를 위로 던지지 않는다 — graceful.
- 외부 데이터(yfinance/DB) 없어도 '확인 필요' 로 표시.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("portfolio_review")

NEEDS_CHECK = "확인 필요"


# ---------------------------------------------------------------------------
# portfolio.json 로드
# ---------------------------------------------------------------------------

def load_portfolio(project_root: Path | str | None = None) -> dict[str, Any]:
    """data/portfolio.json 로드. 없거나 깨지면 {'available': False, ...}.

    Returns: {available, holdings: list, as_of, error}
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    path = Path(project_root) / "data" / "portfolio.json"
    if not path.exists():
        return {"available": False, "holdings": [], "as_of": None,
                "error": "data/portfolio.json 파일이 없습니다."}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("portfolio.json 파싱 실패: %s", e)
        return {"available": False, "holdings": [], "as_of": None,
                "error": f"portfolio.json 파싱 오류: {e}"}
    holdings = raw.get("holdings")
    if not isinstance(holdings, list):
        return {"available": False, "holdings": [], "as_of": raw.get("as_of"),
                "error": "portfolio.json 에 holdings 배열이 없습니다."}
    return {
        "available": True,
        "holdings": holdings,
        "as_of": raw.get("as_of"),
        "base_currency": raw.get("base_currency", "KRW"),
        "error": None,
    }


# ---------------------------------------------------------------------------
# 보조
# ---------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 포트폴리오 레벨 진단
# ---------------------------------------------------------------------------

def diagnose_portfolio(holdings: list[dict]) -> dict[str, Any]:
    """포트폴리오 레벨 진단 — 총 평가액·종목수·집중도·레버리지 노출·수익분포.

    Returns dict (모든 값은 None-safe).
    """
    holdings = holdings or []
    n = len(holdings)
    total_value = sum(_f(h.get("value_krw")) or 0.0 for h in holdings)
    total_cost = sum(_f(h.get("cost_krw")) or 0.0 for h in holdings)
    total_pnl = sum(_f(h.get("pnl_krw")) or 0.0 for h in holdings)

    # 집중도 — 최대 비중 종목
    top = None
    top_pct = None
    for h in holdings:
        pct = _f(h.get("net_worth_pct"))
        # net_worth_pct 없으면 value 기준 비중으로 근사
        if pct is None and total_value > 0:
            v = _f(h.get("value_krw"))
            pct = (v / total_value * 100.0) if v is not None else None
        if pct is not None and (top_pct is None or pct > top_pct):
            top_pct = pct
            top = h

    # 레버리지 노출 — leverage=true 종목 비중 합
    lev_pct = 0.0
    lev_names: list[str] = []
    has_lev_pct = False
    for h in holdings:
        if h.get("leverage"):
            pct = _f(h.get("net_worth_pct"))
            if pct is None and total_value > 0:
                v = _f(h.get("value_krw"))
                pct = (v / total_value * 100.0) if v is not None else None
            if pct is not None:
                lev_pct += pct
                has_lev_pct = True
            lev_names.append(h.get("name") or h.get("ticker") or "")

    # 수익/손실 분포
    winners = [h for h in holdings if (_f(h.get("return_pct")) or 0.0) > 0]
    losers = [h for h in holdings if (_f(h.get("return_pct")) or 0.0) < 0]

    return {
        "n_holdings": n,
        "total_value_krw": total_value,
        "total_cost_krw": total_cost,
        "total_pnl_krw": total_pnl,
        "total_return_pct": (total_pnl / total_cost * 100.0) if total_cost > 0 else None,
        "top_holding": top,
        "top_holding_pct": top_pct,
        "leverage_exposure_pct": lev_pct if has_lev_pct else None,
        "leverage_names": lev_names,
        "n_winners": len(winners),
        "n_losers": len(losers),
    }


# ---------------------------------------------------------------------------
# Market Regime 연계 — rule-based 한국어 코멘트
# ---------------------------------------------------------------------------

# overheat band → 과열 국면 키워드
_EXPENSIVE_REGIMES = {"Overheated", "Expensive but Stable"}
_DEFENSIVE_REGIMES = {"Correction Watch", "Dislocation", "Crisis"}


def generate_portfolio_commentary(
    diag: dict[str, Any],
    regime: Any | None,
    position_reviews: list[dict] | None = None,
) -> str:
    """포트폴리오 진단 + Market Regime 을 엮은 rule-based 한국어 코멘트.

    regime: market_regime 테이블의 sqlite3.Row / dict (없으면 None).
    """
    parts: list[str] = []

    # 시장 국면 읽기
    cur_regime = None
    overheat = None
    if regime is not None:
        try:
            keys = regime.keys()
        except Exception:
            keys = []
        if "current_regime" in keys:
            cur_regime = regime["current_regime"]
        if "market_overheat_score" in keys:
            overheat = _f(regime["market_overheat_score"])

    top_pct = diag.get("top_holding_pct")
    top = diag.get("top_holding") or {}
    lev_pct = diag.get("leverage_exposure_pct")

    # 1) 국면 리드
    if cur_regime:
        oh_str = f"{overheat:.0f}/100" if overheat is not None else NEEDS_CHECK
        parts.append(
            f"현재 시장 국면은 '{cur_regime}' (Overheat Score {oh_str}) 입니다."
        )
    else:
        parts.append(
            f"현재 시장 국면 데이터가 없습니다 — {NEEDS_CHECK} "
            "(파이프라인 실행 후 Market Regime 과 연계 분석됩니다)."
        )

    expensive = bool(cur_regime in _EXPENSIVE_REGIMES) or (
        overheat is not None and overheat >= 50
    )
    defensive = bool(cur_regime in _DEFENSIVE_REGIMES)

    # 2) 집중도 진단
    if top_pct is not None:
        top_name = top.get("name") or top.get("ticker") or "최대 비중 종목"
        if top_pct >= 35:
            conc_msg = (
                f"최대 비중 종목 '{top_name}' 이 순자산의 {top_pct:.1f}% 를 차지해 "
                "단일 종목 과집중 상태입니다."
            )
            if expensive:
                conc_msg += (
                    " 시장이 비싼 국면에서 이 정도 집중은 변동성 위험이 큽니다 — "
                    "집중도 완화를 우선 검토하십시오."
                )
            elif defensive:
                conc_msg += (
                    " 방어적 국면에서 단일 종목 집중은 하락 시 손실을 키웁니다 — "
                    "분산 강화를 검토하십시오."
                )
            parts.append(conc_msg)
        elif top_pct >= 20:
            parts.append(
                f"최대 비중 종목 '{top_name}' 이 순자산의 {top_pct:.1f}% — "
                "다소 높은 편이나 관리 가능한 수준입니다."
            )

    # 3) 레버리지 노출
    if lev_pct is not None:
        if lev_pct >= 40:
            lev_msg = (
                f"레버리지 종목 비중 합이 순자산의 {lev_pct:.1f}% 로 매우 높습니다."
            )
        elif lev_pct >= 20:
            lev_msg = (
                f"레버리지 종목 비중 합이 순자산의 {lev_pct:.1f}% 입니다."
            )
        else:
            lev_msg = (
                f"레버리지 종목 비중 합은 순자산의 {lev_pct:.1f}% 로 제한적입니다."
            )
        if lev_pct >= 20 and (expensive or defensive):
            lev_msg += (
                " 시장이 과열·조정 국면일수록 레버리지는 하락을 증폭시킵니다 — "
                "수익 보호(익절)와 레버리지 노출 축소를 함께 검토하십시오."
            )
        parts.append(lev_msg)

    # 4) 큰 수익 레버리지 포지션 — Profit Protection 관점
    big_lev_winners: list[str] = []
    for r in (position_reviews or []):
        if r.get("leverage") and (_f(r.get("return_pct")) or 0.0) >= 25:
            big_lev_winners.append(
                f"{r.get('name') or r.get('ticker')} ({_f(r.get('return_pct')):+.0f}%)"
            )
    if big_lev_winners:
        parts.append(
            "다음 레버리지 포지션은 이미 큰 수익이 났습니다 — "
            + ", ".join(big_lev_winners)
            + ". 좋은 종목이라도 레버리지+큰 수익이 겹치면 수익 보호(부분 익절)를 "
            "권고합니다 (Profit Protection 관점)."
        )

    # 5) 종합
    if expensive and (lev_pct is not None and lev_pct >= 20 or (top_pct or 0) >= 35):
        parts.append(
            "종합: 시장이 비싼 국면에서 집중도·레버리지가 높습니다 — "
            "신규 베타 확대보다 '수익 보호 · 집중도 완화'가 우선입니다."
        )
    elif defensive:
        parts.append(
            "종합: 방어적 국면입니다 — 레버리지·집중도를 낮추고 "
            "방어적 포지션 비중을 점검하십시오."
        )
    else:
        parts.append(
            "종합: 현재 포트폴리오 구조를 유지하되, 집중도·레버리지 지표를 "
            "주기적으로 점검하십시오."
        )

    return " ".join(parts)
