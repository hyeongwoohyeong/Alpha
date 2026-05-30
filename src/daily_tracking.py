"""Core Daily Trackers + Parking Universe — 매일 추적해야 하는 자산군.

분리 이유:
- Core trackers (TQQQ/QQQ/SPY/BTC) 는 universe.csv 의 alpha 후보가 아니라
  "매일 봐야 하는 시장 기본기 + 베타 도구". 사용자가 명시한 매일 추적 대상.
- Parking universe (MCD/COST/WMT/KO/PEP/V/MA/JNJ) 는 alpha 가 아니라
  defensive parking 후보 — 시장 과열·현금 비축 국면일 때만 의미.

둘 다 fetch 가격은 동일 (market_data.fetch_universe), verdict 로직만 분리.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("daily_tracking")


# ---------------------------------------------------------------------------
# 자산 정의
# ---------------------------------------------------------------------------

CORE_TRACKERS: list[dict[str, str]] = [
    {"symbol": "TQQQ",     "name": "TQQQ",       "subtitle": "나스닥100 3X — 베타 공격형",  "kind": "leverage_us"},
    {"symbol": "QQQ",      "name": "QQQ",        "subtitle": "나스닥100",                    "kind": "index_us"},
    {"symbol": "SPY",      "name": "SPY",        "subtitle": "S&P500",                       "kind": "index_us"},
    {"symbol": "SOXX",     "name": "SOXX",       "subtitle": "PHLX 반도체 — 사용자 핵심 섹터", "kind": "sector_us"},
    {"symbol": "SMH",      "name": "SMH",        "subtitle": "반도체 Top-25",                "kind": "sector_us"},
    {"symbol": "BTC-USD",  "name": "비트코인",    "subtitle": "BTC-USD",                      "kind": "crypto"},
    {"symbol": "069500.KS","name": "KODEX 200",  "subtitle": "코스피 200",                   "kind": "index_kr"},
    {"symbol": "000660.KS","name": "SK하이닉스",  "subtitle": "사용자 핵심 보유의 underlying",  "kind": "underlying_kr"},
]

PARKING_UNIVERSE: list[dict[str, str]] = [
    {"symbol": "MCD",  "name": "맥도날드",    "sector": "필수소비"},
    {"symbol": "COST", "name": "코스트코",    "sector": "유통"},
    {"symbol": "WMT",  "name": "월마트",      "sector": "유통"},
    {"symbol": "KO",   "name": "코카콜라",    "sector": "필수소비"},
    {"symbol": "PEP",  "name": "펩시코",      "sector": "필수소비"},
    {"symbol": "V",    "name": "비자",        "sector": "결제 네트워크"},
    {"symbol": "MA",   "name": "마스터카드",  "sector": "결제 네트워크"},
    {"symbol": "JNJ",  "name": "존슨앤존슨",  "sector": "헬스케어"},
]


# ---------------------------------------------------------------------------
# 임계값
# ---------------------------------------------------------------------------

# Alpha gate — 사용자 합의: score ≥ 80 + DD ≤ -10%
ALPHA_SCORE_MIN = 80.0
ALPHA_DRAWDOWN_MAX = -0.10   # ≤ -10% (음수, 부등호 ≤ 임)

# Parking 권장 트리거
PARKING_REGIME_OVERHEAT_MIN = 60.0    # 시장 과열 점수 ≥ 60 일 때 파킹 부각
PARKING_DD_RANGE = (-0.20, -0.03)     # 본주 DD 가 이 범위면 valuation 매력
PARKING_MAX_SHOW = 4                  # 한 번에 보여줄 개수 cap


# ---------------------------------------------------------------------------
# 가격 fetch
# ---------------------------------------------------------------------------

def fetch_all_trackers() -> dict[str, Any]:
    """Core + Parking 전체 fetch — fetch_universe 1회 호출.

    Returns: {core: [...], parking: [...]} — 각 항목은 {meta, data}.
    data 는 market_data.fetch_universe 결과 (available/current_price/dd/etc).
    """
    try:
        from .market_data import fetch_universe
        symbols = [t["symbol"] for t in CORE_TRACKERS] + [p["symbol"] for p in PARKING_UNIVERSE]
        md_map = fetch_universe(symbols, enrich=False)
    except Exception as e:
        log.warning("daily trackers fetch 실패: %s", e)
        md_map = {}
    return {
        "core": [{"meta": t, "data": md_map.get(t["symbol"], {})} for t in CORE_TRACKERS],
        "parking": [{"meta": p, "data": md_map.get(p["symbol"], {})} for p in PARKING_UNIVERSE],
    }


# ---------------------------------------------------------------------------
# Verdict 로직
# ---------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def core_tracker_verdict(meta: dict, data: dict, conn=None) -> dict[str, Any]:
    """Core tracker 의 verdict.

    - BTC: btc_tracker._btc_cycle_stage 재사용
    - QQQ/SPY/TQQQ: market_cycle_analyzer.recommend_current_entry (QQQ 데이터 사다리)
    - KODEX 200: recommend_current_entry base_asset=069500
    - 그 외: DD 기반 fallback
    """
    if not data or not data.get("available"):
        return {"verdict": "데이터 없음", "color": "#64748B", "detail": "—"}

    dd = _f(data.get("drawdown_from_52w_high"))
    sym = meta.get("symbol", "")

    # BTC — cycle stage
    if sym == "BTC-USD":
        try:
            from .btc_tracker import _btc_cycle_stage
            stage = _btc_cycle_stage(dd)
            return {"verdict": stage["stage"], "color": stage["color"], "detail": stage["tone"]}
        except Exception:
            pass

    # QQQ-family + SPY — use data ladder
    if sym in ("QQQ", "TQQQ", "SPY") and conn is not None:
        try:
            from .market_cycle_analyzer import recommend_current_entry
            rec = recommend_current_entry(conn, base_asset="QQQ")
            verdict = (rec.get("verdict") or "").strip() or "확인 필요"
            best = rec.get("best_asset")
            detail = rec.get("current_bucket") or ""
            # TQQQ 의 경우 best_asset 이 TQQQ 면 강하게 highlight
            if sym == "TQQQ" and best == "TQQQ" and "진입 적기" in verdict:
                return {"verdict": "TQQQ 진입 적기", "color": "#15803D", "detail": detail}
            return {"verdict": verdict, "color": _verdict_color(verdict), "detail": detail}
        except Exception as e:
            log.debug("recommend_current_entry 실패 (%s): %s", sym, e)

    # KODEX 200 — KR ladder
    if sym == "069500.KS" and conn is not None:
        try:
            from .market_cycle_analyzer import recommend_current_entry
            rec = recommend_current_entry(conn, base_asset="069500")
            verdict = (rec.get("verdict") or "").strip() or "확인 필요"
            return {"verdict": verdict, "color": _verdict_color(verdict), "detail": rec.get("current_bucket") or ""}
        except Exception:
            pass

    # Generic DD fallback
    if dd is None:
        return {"verdict": "확인 필요", "color": "#94A3B8", "detail": "—"}
    if dd >= -0.05:
        return {"verdict": "신고가권", "color": "#F59E0B", "detail": "과열 주의"}
    if dd >= -0.15:
        return {"verdict": "정상 구간", "color": "#3B82F6", "detail": "Mid-cycle"}
    if dd >= -0.25:
        return {"verdict": "진입 검토", "color": "#22C55E", "detail": "분할매수 구간"}
    return {"verdict": "공격 진입", "color": "#15803D", "detail": "깊은 낙폭"}


def _verdict_color(verdict: str) -> str:
    """verdict 라벨 → 색상."""
    v = verdict.lower()
    if "진입 적기" in verdict or "황금" in verdict or "공격" in verdict:
        return "#15803D"  # dark green — 강한 매수
    if "진입" in verdict or "검토" in verdict:
        return "#22C55E"  # green — 매수 검토
    if "정상" in verdict or "mid" in v:
        return "#3B82F6"  # blue — 정상
    if "조정" in verdict or "주의" in verdict:
        return "#F59E0B"  # amber — 경계
    if "과열" in verdict or "신고가" in verdict or "추격" in verdict:
        return "#EF4444"  # red — 과열
    return "#94A3B8"      # gray — 모호


def parking_verdict(meta: dict, data: dict, market_overheat: float | None) -> dict[str, Any]:
    """Parking 후보 verdict — show 가 True 일 때만 카드에 노출."""
    if not data or not data.get("available"):
        return {"verdict": "데이터 없음", "show": False, "color": "#64748B", "detail": "—"}
    dd = _f(data.get("drawdown_from_52w_high"))
    if dd is None:
        return {"verdict": "확인 필요", "show": False, "color": "#94A3B8", "detail": "—"}

    market_overheated = (market_overheat is not None and market_overheat >= PARKING_REGIME_OVERHEAT_MIN)
    in_sweet_spot = PARKING_DD_RANGE[0] <= dd <= PARKING_DD_RANGE[1]

    if in_sweet_spot:
        if market_overheated:
            return {
                "verdict": "파킹 적합",
                "show": True,
                "color": "#15803D",
                "detail": f"DD {dd * 100:+.1f}% · 시장 과열 — 자본 보존",
            }
        return {
            "verdict": "정상 valuation",
            "show": True,
            "color": "#22C55E",
            "detail": f"DD {dd * 100:+.1f}% · quality compounder",
        }
    # Sweet spot 밖이면 show=False (소음 제거)
    return {"verdict": "관망", "show": False, "color": "#94A3B8", "detail": ""}


# ---------------------------------------------------------------------------
# 카드 빌드 (today_decision Layer B 에서 호출)
# ---------------------------------------------------------------------------

def build_core_tracker_cards(tracker_data: dict, conn=None) -> list[dict[str, Any]]:
    """Core tracker 카드 리스트 — 항상 5개 노출 (data 없으면 verdict='데이터 없음').

    Returns list of {name, subtitle, price, dd_pct, daily_pct, verdict, color, detail}
    """
    cards = []
    for entry in (tracker_data or {}).get("core", []):
        meta = entry["meta"]
        data = entry["data"] or {}
        v = core_tracker_verdict(meta, data, conn=conn)
        price = _f(data.get("current_price"))
        dd = _f(data.get("drawdown_from_52w_high"))
        dr = _f(data.get("daily_return"))
        cards.append({
            "symbol": meta["symbol"],
            "name": meta["name"],
            "subtitle": meta.get("subtitle", ""),
            "kind": meta.get("kind", ""),
            "price": price,
            "dd_pct": dd * 100 if dd is not None else None,
            "daily_pct": dr * 100 if dr is not None else None,
            "verdict": v["verdict"],
            "color": v["color"],
            "detail": v["detail"],
        })
    return cards


def build_alpha_candidates_strict(rows: list[dict]) -> list[dict[str, Any]]:
    """고확신 알파 — score ≥ 80 AND DD ≤ -10%.

    기존 build_upside_candidates 는 action_tag 만 봤는데 conviction 부족.
    이건 hedge fund 기준 — 정량 임계 통과 종목만.
    """
    if not rows:
        return []
    qualified: list[dict] = []
    for r in rows:
        md = r.get("market_data") or {}
        if not md.get("available"):
            continue
        scores = r.get("scores") or {}
        score = _f(scores.get("final_score"))
        dd = _f(md.get("drawdown_from_52w_high"))
        if score is None or dd is None:
            continue
        # 양쪽 임계 모두 통과
        if score < ALPHA_SCORE_MIN:
            continue
        if dd > ALPHA_DRAWDOWN_MAX:  # dd 가 -10% 보다 위(덜 빠짐) 이면 제외
            continue
        qualified.append({
            "ticker": r.get("ticker"),
            "name": r.get("name_ko") or r.get("name_en") or r.get("ticker"),
            "theme": r.get("theme"),
            "tag": r.get("action_tag") or "—",
            "score": score,
            "drawdown": dd,
            "price": md.get("current_price"),
        })
    # 정렬: score desc, DD asc (더 빠진 게 앞)
    qualified.sort(key=lambda c: (-c["score"], c["drawdown"] or 0))
    return qualified


def build_parking_cards(tracker_data: dict, market_overheat: float | None) -> list[dict[str, Any]]:
    """Parking 후보 카드 — verdict.show=True 만 + max 4개.

    overheat 정보가 없거나 시장이 평온하면 sweet spot 종목만 normal valuation 으로 표시.
    """
    cards = []
    for entry in (tracker_data or {}).get("parking", []):
        meta = entry["meta"]
        data = entry["data"] or {}
        v = parking_verdict(meta, data, market_overheat)
        if not v.get("show"):
            continue
        dd = _f(data.get("drawdown_from_52w_high"))
        cards.append({
            "symbol": meta["symbol"],
            "name": meta["name"],
            "sector": meta.get("sector", ""),
            "price": data.get("current_price"),
            "dd_pct": dd * 100 if dd is not None else None,
            "verdict": v["verdict"],
            "color": v["color"],
            "detail": v["detail"],
        })
    # 정렬: DD 깊은 순 (valuation 좋은 순)
    cards.sort(key=lambda c: c.get("dd_pct") or 0)
    return cards[:PARKING_MAX_SHOW]
