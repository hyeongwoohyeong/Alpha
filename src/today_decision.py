"""오늘의 판단 — 3-Layer 내러티브 빌더.

구조:
  Layer A) 내 포트폴리오 점검   — 집중/익절임박/손실주의/작동중
  Layer B) 시장 저평가·upside    — engine universe 의 rows 에서 발굴
  Layer C) 리밸런싱 액션         — 신호 없으면 통째로 생략 (소음 제거)

설계 원칙:
- materiality first: 절대 평가액 ≥ ₩5M 만 surfacing (작은 % 변동은 노이즈)
- 없으면 침묵: Layer C 가 비면 섹션 자체 누락
- rule-based: LLM 없어도 작동, 데이터 결함 시 graceful

의존성: portfolio.json (holdings) + diagnose_portfolio + rows + regime
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("today_decision")


# ---------------------------------------------------------------------------
# 임계값 (단일 정의)
# ---------------------------------------------------------------------------

# Materiality (사용자 룰 — feedback_materiality_principle 메모리)
_MIN_VALUE_KRW = 5_000_000          # 종목별 알림은 절대 평가액 ≥ ₩5M
_MIN_PNL_KRW = 2_000_000            # 익절/손실 알림은 PnL 절대값 ≥ ₩2M

# Concentration · Leverage (사용자 룰 — 실증 backed 아님)
# 일반 가이드라인. 종목별 alpha_bet 룰 우선 적용 후 fallback.
_TOP1_WARNING_PCT = 30.0
_TOP1_DANGER_PCT = 45.0
_LEVERAGE_WARNING_PCT = 30.0
_LEVERAGE_DANGER_PCT = 50.0

# Profit-take / Loss-cut — 일반 fallback ONLY.
# 종목별 alpha_bets.exit_rules 가 있으면 그것을 우선 사용한다 (단위: % — holdings.return_pct 와 동일).
# 이 일반 임계값은 alpha_bet 룰 없는 종목에만 적용되며, 라벨에 "일반 룰" 명시.
_PROFIT_TAKE_PCT = 30.0
_LOSS_CUT_PCT = -15.0

# Alpha Bet ledger 경로
_ALPHA_BETS_PATH = Path(__file__).resolve().parents[1] / "data" / "alpha_bets.json"

# Upside discovery — DEPRECATED for direct use
# Layer B 는 이제 daily_tracking.build_alpha_candidates_strict (score≥80+DD≤-10%) 사용
# 아래는 backward compat 용 (engine 다른 곳에서 호출하면 안전하게 작동)
_UPSIDE_TAGS = {"Research Now", "Quality Dislocation"}
_UPSIDE_MAX_ROWS = 5


# ---------------------------------------------------------------------------
# 데이터 정규화
# ---------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _hold_value(h: dict) -> float:
    return _f(h.get("value_krw")) or 0.0


def _hold_pnl(h: dict) -> float:
    return _f(h.get("pnl_krw")) or 0.0


def _hold_return(h: dict) -> float:
    return _f(h.get("return_pct")) or 0.0


def _hold_name(h: dict) -> str:
    return h.get("name") or h.get("ticker") or "(이름 없음)"


# ---------------------------------------------------------------------------
# Alpha Bet ledger lookup — 종목별 exit_rules 적용
# ---------------------------------------------------------------------------

def _load_active_alpha_bets() -> list[dict]:
    """alpha_bets.json 에서 status='active' bets 만 반환. 실패 시 [] (graceful)."""
    try:
        with open(_ALPHA_BETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [b for b in (data.get("bets") or []) if b.get("status") == "active"]
    except Exception as e:
        log.debug("alpha_bets 로드 실패: %s", e)
        return []


def _match_alpha_bet(holding: dict, bets: list[dict]) -> dict | None:
    """holding 과 alpha_bet 매칭 — ticker 정확 매칭 우선, name 부분 매칭 fallback."""
    h_ticker = (holding.get("ticker") or "").strip().lower()
    h_name = (holding.get("name") or "").strip().lower()
    if not bets:
        return None
    # 1) ticker 정확 매칭
    if h_ticker:
        for b in bets:
            b_ticker = (b.get("ticker") or "").strip().lower()
            if b_ticker and h_ticker == b_ticker:
                return b
    # 2) name 부분 매칭 (한글 키워드)
    if h_name:
        for b in bets:
            b_name = (b.get("name") or "").strip().lower()
            if not b_name:
                continue
            for kw in ["하이닉스", "soxl", "tqqq", "qld", "tiger 반도체", "kodex"]:
                if kw in h_name and kw in b_name:
                    return b
    return None


def _bet_signal_for_holding(holding: dict, bet: dict) -> list[dict[str, Any]]:
    """bet.exit_rules vs holding 현재 수익률 → 신호 list.

    holdings.return_pct 는 % (예: 2.7), bet.exit_rules.*_pct 는 분수 (예: 0.025).
    내부에서 분수로 통일 (ret_frac = holding.return_pct / 100).

    Returns list of dicts {severity, label, detail, verdict}.
    verdict ∈ {STAY, ADD, TRIM, SELL, STOP}.
    """
    out: list[dict[str, Any]] = []
    er = bet.get("exit_rules") or {}
    if not er:
        return out
    ret_pct = _hold_return(holding)          # % 단위
    ret_frac = ret_pct / 100.0               # 분수 단위
    name = _hold_name(holding)
    pnl = _hold_pnl(holding)

    # 1) Stop loss 도달
    stop = er.get("stop_loss_pct")
    if stop is not None and ret_frac <= stop:
        out.append({
            "severity": "warn",
            "label": "Alpha Bet STOP — 손절 도달",
            "detail": f"{name} {ret_pct:+.1f}% (₩{pnl/1e6:+.1f}M), 설정 손절 {stop*100:+.0f}% 도달",
            "verdict": "STOP",
        })
        return out  # stop 이 발화되면 다른 시그널 무시

    # 2) Stop 임박 (stop 의 80% 도달)
    if stop is not None and ret_frac <= stop * 0.8 and ret_frac > stop:
        margin = (ret_frac - stop) * 100  # 양수 (남은 여유)
        out.append({
            "severity": "warn",
            "label": "Alpha Bet 손절 임박",
            "detail": f"{name} {ret_pct:+.1f}%, 손절 {stop*100:+.0f}% 까지 {margin:+.1f}%pt 남음",
            "verdict": "STAY",  # 손절 전까지는 보유 — 다만 모니터링
        })

    # 3) Scale-out trigger 단계별
    scale_outs = er.get("scale_out") or []
    for s in scale_outs:
        trig = s.get("trigger_pct")
        if trig is None:
            continue
        frac = s.get("fraction") or 0
        note = s.get("note") or ""
        # 95% 도달부터 surfacing
        if ret_frac >= trig * 0.95:
            margin = (ret_frac - trig) * 100  # 양수 = 도달
            if margin >= 0:
                # 도달
                verdict = "TRIM" if frac < 0.5 else "SELL"
                out.append({
                    "severity": "warn",
                    "label": f"Alpha Bet 익절 트리거 도달",
                    "detail": f"{name} {ret_pct:+.1f}% — {note} 도달 (계획: {int(frac*100)}% 익절)",
                    "verdict": verdict,
                })
            else:
                # 임박
                out.append({
                    "severity": "info",
                    "label": f"Alpha Bet 익절 임박",
                    "detail": f"{name} {ret_pct:+.1f}% — {note} {-margin:.1f}%pt 남음",
                    "verdict": "STAY",
                })

    # 4) scale_outs 없으면 target_return_pct 단일 체크
    if not scale_outs:
        tgt = er.get("target_return_pct")
        if tgt is not None and ret_frac >= tgt * 0.95:
            margin = (ret_frac - tgt) * 100
            if margin >= 0:
                out.append({
                    "severity": "warn",
                    "label": "Alpha Bet target 도달",
                    "detail": f"{name} {ret_pct:+.1f}% — target {tgt*100:+.0f}% 도달",
                    "verdict": "SELL",
                })
            else:
                out.append({
                    "severity": "info",
                    "label": "Alpha Bet target 임박",
                    "detail": f"{name} {ret_pct:+.1f}% — target {tgt*100:+.0f}% 까지 {-margin:.1f}%pt",
                    "verdict": "STAY",
                })

    # 5) 본주 target (예: SK하이닉스 ₩2.5M) — 본주 가격 fetch 필요
    # 이번 단계에선 trigger 만 기록, 본주 가격 lookup 은 향후 alpha_bet_signals.py 에서
    underlying = er.get("target_underlying_price")
    if underlying:
        out.append({
            "severity": "info",
            "label": "Alpha Bet 본주 target",
            "detail": f"{name} — 본주 target: {underlying} (별도 가격 모니터링 필요)",
            "verdict": "STAY",
        })

    # 신호 없음 + 본주 target 도 없음 → 'STAY THE COURSE' 명시 (작전 정상 진행)
    if not out:
        out.append({
            "severity": "info",
            "label": "Alpha Bet 작동 중",
            "detail": f"{name} {ret_pct:+.1f}% — 신호 없음, stay the course (보유 유지)",
            "verdict": "STAY",
        })
    return out


def build_alpha_bet_signals(holdings: list[dict]) -> list[dict[str, Any]]:
    """Layer 0 — Alpha Bet 신호.

    Active alpha bets 별로 종목 매칭 → exit_rules trigger 체크.
    반환: items list (severity·label·detail·verdict 키), 우선순위:
      STOP > SELL > TRIM > ADD > STAY
    """
    bets = _load_active_alpha_bets()
    if not bets:
        return []
    items: list[dict[str, Any]] = []
    matched_holdings: set[str] = set()
    for h in holdings:
        v = _hold_value(h)
        if v < _MIN_VALUE_KRW:
            continue
        bet = _match_alpha_bet(h, bets)
        if not bet:
            continue
        signals = _bet_signal_for_holding(h, bet)
        for s in signals:
            s["bet_id"] = bet.get("id")
            s["bet_name"] = bet.get("name")
            items.append(s)
        matched_holdings.add(_hold_name(h))

    # 미매칭 active bets 도 표시 (포지션이 비어있거나 ₩5M 미만 — 진입 미완)
    for b in bets:
        # 해당 bet 에 매칭된 holding 이 하나도 없으면
        b_name = (b.get("name") or "").lower()
        if not any(b_name in mn.lower() or any(kw in mn.lower() and kw in b_name for kw in ["하이닉스","soxl","tqqq","qld"]) for mn in matched_holdings):
            items.append({
                "severity": "info",
                "label": "Alpha Bet 미매칭",
                "detail": f"{b.get('name','')} — 포트폴리오에 매칭 holding 없음 (또는 ₩5M 미만)",
                "verdict": "STAY",
                "bet_id": b.get("id"),
                "bet_name": b.get("name"),
            })

    # 우선순위 정렬: STOP > SELL > TRIM > ADD > STAY
    order = {"STOP": 0, "SELL": 1, "TRIM": 2, "ADD": 3, "STAY": 4}
    items.sort(key=lambda x: order.get(x.get("verdict", "STAY"), 5))
    return items


def _holding_in_active_bets(holding: dict, bets: list[dict]) -> bool:
    """이 holding 이 active alpha bet 에 매칭되는지 — 일반 익절/손실 룰 skip 판정용."""
    return _match_alpha_bet(holding, bets) is not None


# ---------------------------------------------------------------------------
# Layer A — 내 포트폴리오 점검
# ---------------------------------------------------------------------------

def build_portfolio_check(holdings: list[dict], diag: dict[str, Any]) -> list[dict[str, Any]]:
    """포트폴리오 현황의 주목 포인트만 정리. 평범한 항목은 안 surfacing.

    Returns list of {severity, label, detail} — severity: "warn"|"info"|"ok"
    빈 리스트면 호출부에서 "별 일 없음" 한 줄로 안내.
    """
    items: list[dict[str, Any]] = []

    # 1) 집중도
    top_pct = _f(diag.get("top_holding_pct"))
    top = diag.get("top_holding") or {}
    if top_pct is not None and top_pct >= _TOP1_WARNING_PCT:
        sev = "warn" if top_pct >= _TOP1_DANGER_PCT else "info"
        items.append({
            "severity": sev,
            "label": "단일 종목 집중",
            "detail": f"{_hold_name(top)} 순자산의 {top_pct:.1f}%",
        })

    # 2) 섹터 집중도 — 반도체 (가장 큰 실질 risk: 레버리지보다 본질적)
    semi_keywords = ["하이닉스", "반도체", "Semi", "SOXL", "SOXX", "SMH", "AI반도체", "삼성전자"]
    semi_value = 0.0
    semi_holdings = []
    for h in holdings:
        name = (h.get("name") or "") + " " + (h.get("ticker") or "")
        if any(kw in name for kw in semi_keywords):
            v = _hold_value(h)
            semi_value += v
            if v >= _MIN_VALUE_KRW:
                semi_holdings.append(h)
    if semi_value > 0:
        total_inv = sum(_hold_value(h) for h in holdings)
        semi_pct_inv = (semi_value / total_inv * 100.0) if total_inv > 0 else 0.0
        nw_est = _f(diag.get("net_worth_krw")) or 0
        if not nw_est:
            # 추정: 가장 큰 보유 + net_worth_pct 로 역산
            for h in holdings:
                pct = _f(h.get("net_worth_pct"))
                v = _hold_value(h)
                if pct and v and pct > 0:
                    nw_est = v / (pct / 100.0)
                    break
        semi_pct_nw = (semi_value / nw_est * 100.0) if nw_est > 0 else 0.0
        if semi_pct_inv >= 40.0 or semi_pct_nw >= 30.0:
            sev = "warn" if semi_pct_inv >= 60.0 else "info"
            items.append({
                "severity": sev,
                "label": "반도체 섹터 집중",
                "detail": (f"투자자산의 {semi_pct_inv:.0f}% / 순자산의 {semi_pct_nw:.0f}% — "
                           f"단일 섹터 베팅 (₩{semi_value / 1e6:.0f}M)"),
            })

    # 3) 레버리지 노출 — 반도체와 100% 겹치면 노이즈, 다른 섹터 레버리지면 의미 있음
    lev_pct = _f(diag.get("leverage_exposure_pct"))
    if lev_pct is not None and lev_pct >= _LEVERAGE_WARNING_PCT:
        # 반도체 외 레버리지 비중 계산 (TSLA·NFLX·QQQ 레버리지 등)
        non_semi_lev = 0.0
        for h in holdings:
            if not h.get("leverage"):
                continue
            name = (h.get("name") or "") + " " + (h.get("ticker") or "")
            if any(kw in name for kw in semi_keywords):
                continue
            non_semi_lev += _hold_value(h)
        # 반도체 레버리지가 압도적이면 이미 위에서 다뤘으므로 보조 정보로만
        if non_semi_lev / max(semi_value, 1) > 0.2:  # 비반도체 레버리지가 20%+ 면 별도 surfacing
            sev = "warn" if lev_pct >= _LEVERAGE_DANGER_PCT else "info"
            items.append({
                "severity": sev,
                "label": "총 레버리지 비중",
                "detail": f"{lev_pct:.0f}% (순자산 대비) — 비반도체 레버리지 ₩{non_semi_lev / 1e6:.1f}M 별도 포함",
            })
        # 반도체와 100% 겹치면 — 위의 '반도체 집중' 신호로 충분 (중복 surfacing 제거)

    # 3) 익절 임박 — materiality 통과 + 수익률 ≥ +30%
    # 단, alpha_bet 룰이 있는 종목은 Layer 0 에서 종목별 룰로 처리 — 중복 surfacing 방지
    active_bets = _load_active_alpha_bets()
    profit_candidates = []
    for h in holdings:
        v = _hold_value(h)
        pnl = _hold_pnl(h)
        ret = _hold_return(h)
        if v < _MIN_VALUE_KRW:
            continue
        if pnl < _MIN_PNL_KRW:
            continue
        if _holding_in_active_bets(h, active_bets):
            continue  # Layer 0 에서 처리
        if ret >= _PROFIT_TAKE_PCT:
            profit_candidates.append((h, ret, pnl))
    profit_candidates.sort(key=lambda t: -t[1])
    for h, ret, pnl in profit_candidates[:3]:
        items.append({
            "severity": "info",
            "label": "익절 후보 (일반 룰)",
            "detail": f"{_hold_name(h)} {ret:+.1f}% (₩{pnl / 1e6:.1f}M) — 사용자 alpha_bet 룰 없음, 일반 +{_PROFIT_TAKE_PCT:.0f}% 임계 적용",
        })

    # 4) 손실 주의 — alpha_bet 매칭 종목은 Layer 0 에서 처리
    loss_candidates = []
    for h in holdings:
        v = _hold_value(h)
        pnl = _hold_pnl(h)
        ret = _hold_return(h)
        if v < _MIN_VALUE_KRW:
            continue
        if abs(pnl) < _MIN_PNL_KRW:
            continue
        if _holding_in_active_bets(h, active_bets):
            continue  # Layer 0 에서 처리
        if ret <= _LOSS_CUT_PCT:
            loss_candidates.append((h, ret, pnl))
    loss_candidates.sort(key=lambda t: t[1])
    for h, ret, pnl in loss_candidates[:2]:
        items.append({
            "severity": "warn",
            "label": "손실 주의 (일반 룰)",
            "detail": f"{_hold_name(h)} {ret:+.1f}% (₩{pnl / 1e6:.1f}M) — 사용자 alpha_bet 룰 없음, 일반 {_LOSS_CUT_PCT:.0f}% 임계 적용",
        })

    # 5) 사용자 memo 에 명시된 target 트래킹 — 예: "₩2.5M 도달 시 수익실현"
    # 단순 패턴: holdings memo 에 '도달 시' 또는 '목표' 키워드 있으면 surfacing
    for h in holdings:
        memo = (h.get("memo") or "")
        if not memo:
            continue
        v = _hold_value(h)
        if v < _MIN_VALUE_KRW:
            continue
        if "도달 시" in memo or "수익실현" in memo or "익절" in memo:
            # 중복 방지: 이미 익절 후보로 잡혔으면 detail 만 보강
            name = _hold_name(h)
            already = any(i["label"] == "익절 후보" and name in i["detail"] for i in items)
            if not already:
                items.append({
                    "severity": "info",
                    "label": "사용자 익절 룰",
                    "detail": f"{name} — {memo}",
                })

    return items


# ---------------------------------------------------------------------------
# Layer B — 시장 저평가·upside 발굴
# ---------------------------------------------------------------------------

def build_upside_candidates(rows: list[dict[str, Any]], regime: Any | None) -> list[dict[str, Any]]:
    """engine universe rows 에서 저평가·upside 후보 발굴.

    선정 기준:
      - action_tag in {"Research Now", "Quality Dislocation"}
      - 또는 (final_score 상위) + (drawdown 의미있음)
    정렬: drawdown 깊은 순 (가장 저평가) + score 보조
    """
    if not rows:
        return []
    candidates: list[dict[str, Any]] = []
    for r in rows:
        md = r.get("market_data") or {}
        if not md.get("available"):
            continue
        scores = r.get("scores") or {}
        tag = r.get("action_tag") or ""
        if tag not in _UPSIDE_TAGS:
            continue
        dd = _f(md.get("drawdown_from_52w_high"))
        score = _f(scores.get("final_score"))
        candidates.append({
            "ticker": r.get("ticker"),
            "name": r.get("name_ko") or r.get("name_en") or r.get("ticker"),
            "tag": tag,
            "score": score,
            "drawdown": dd,
            "price": md.get("current_price"),
            "theme": r.get("theme"),
        })

    # 정렬: drawdown 깊은 순 (None 은 뒤로) + score 보조 (None 은 0)
    def _sort_key(c: dict) -> tuple:
        dd = c.get("drawdown")
        sc = c.get("score") or 0
        # drawdown 음수 — 더 작을수록(더 빠질수록) 앞
        dd_sort = dd if dd is not None else 1.0  # None 은 뒤로
        return (dd_sort, -sc)

    candidates.sort(key=_sort_key)
    return candidates[:_UPSIDE_MAX_ROWS]


# ---------------------------------------------------------------------------
# Layer C — 리밸런싱 액션 (없으면 빈 리스트 반환 — 호출부에서 섹션 생략)
# ---------------------------------------------------------------------------

def build_rebalance_actions(
    holdings: list[dict],
    diag: dict[str, Any],
    upside: list[dict[str, Any]],
    regime: Any | None,
) -> list[dict[str, Any]]:
    """리밸런싱 액션 — 실제 행동이 필요한 경우에만 항목 반환.

    Returns list of {priority, action, basis}. 비어있으면 섹션 생략.
    """
    actions: list[dict[str, Any]] = []

    # 1) Top1 집중도 위험 — 비중 축소 액션
    top_pct = _f(diag.get("top_holding_pct"))
    top = diag.get("top_holding") or {}
    if top_pct is not None and top_pct >= _TOP1_DANGER_PCT:
        memo = top.get("memo", "")
        target_hint = ""
        if "도달 시" in memo or "수익실현" in memo:
            target_hint = f" — {memo}"
        actions.append({
            "priority": "high",
            "action": f"{_hold_name(top)} 비중 축소 검토 (현재 {top_pct:.0f}%)",
            "basis": f"단일 종목 위험 임계 {_TOP1_DANGER_PCT:.0f}% 초과{target_hint}",
        })

    # 2) 익절 후보 — +30% 이상 + materiality
    for h in holdings:
        v = _hold_value(h)
        pnl = _hold_pnl(h)
        ret = _hold_return(h)
        if v < _MIN_VALUE_KRW or pnl < _MIN_PNL_KRW:
            continue
        if ret < _PROFIT_TAKE_PCT:
            continue
        memo = h.get("memo", "")
        basis = f"+{ret:.1f}% / ₩{pnl / 1e6:.1f}M 익절선"
        if "도달 시" in memo:
            basis = memo
        actions.append({
            "priority": "medium",
            "action": f"{_hold_name(h)} 단계적 익절 검토",
            "basis": basis,
        })

    # 3) 손실 컷 — -15% 이하 + materiality
    for h in holdings:
        v = _hold_value(h)
        pnl = _hold_pnl(h)
        ret = _hold_return(h)
        if v < _MIN_VALUE_KRW or abs(pnl) < _MIN_PNL_KRW:
            continue
        if ret > _LOSS_CUT_PCT:
            continue
        actions.append({
            "priority": "medium",
            "action": f"{_hold_name(h)} 손절 또는 비중 정리 검토",
            "basis": f"{ret:+.1f}% 손실 누적 — 추세 reversal 없으면 정리",
        })

    # 4) Funding 페어 — 익절·손절 후보가 있고 STRICT alpha (score≥80+DD≤-10%) 후보 있을 때만
    has_disposal = any(a["priority"] in {"high", "medium"} for a in actions)
    if has_disposal and upside:
        # upside 인자가 list[dict] with 'score' key (build_alpha_candidates_strict 결과) 면
        # score≥80 필터 통과한 것만 들어와있음 — 그대로 사용
        # 백워드 호환: upside 에 score 없으면 (구버전 호출) 첫 항목 skip 안 함
        top_upside = upside[0]
        score = top_upside.get("score")
        # score 가 있고 80 미달이면 funding pair 추천 안 함 (low conviction 추적 X)
        if score is None or score >= 80.0:
            dd = top_upside.get("drawdown")
            dd_str = f"DD {dd * 100:+.1f}%" if dd is not None else ""
            sc_str = f"score {score:.0f}" if score is not None else ""
            bits = [b for b in [top_upside.get("tag", ""), sc_str, dd_str] if b]
            actions.append({
                "priority": "low",
                "action": f"매도 자금 → {top_upside['name']} ({top_upside['ticker']}) 분할 진입 검토",
                "basis": " · ".join(bits),
            })

    # 5) 시장 국면 기반 베타 조절 — 사용자 투자 스타일 (전술적 성장)
    if regime is not None:
        overheat = None
        try:
            # regime 은 sqlite Row 형태일 수 있어 _regime_row_get 흉내
            if hasattr(regime, "__getitem__"):
                try:
                    overheat = _f(regime["market_overheat_score"])
                except Exception:
                    overheat = None
            elif isinstance(regime, dict):
                overheat = _f(regime.get("market_overheat_score"))
        except Exception:
            overheat = None
        lev_pct = _f(diag.get("leverage_exposure_pct"))
        if overheat is not None and lev_pct is not None:
            # 과열 + 레버리지 高 → 베타 축소
            if overheat >= 70 and lev_pct >= 30:
                actions.append({
                    "priority": "high",
                    "action": "레버리지 비중 단계적 축소",
                    "basis": f"시장 과열 점수 {overheat:.0f} / 임계 70 초과 + 레버리지 {lev_pct:.0f}%",
                })
            # 저평가 + 레버리지 低 → 베타 확대
            elif overheat <= 30 and lev_pct < 30:
                actions.append({
                    "priority": "medium",
                    "action": "QLD/TQQQ 분할 진입 검토",
                    "basis": f"시장 과열 점수 {overheat:.0f} / 저평가 구간",
                })

    return actions


# ---------------------------------------------------------------------------
# HTML 렌더링 헬퍼
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Headline synthesizer — Layer A + B + C 를 종합한 한 줄 결론
# ---------------------------------------------------------------------------

def synthesize_headline(
    layer_a_items: list[dict],
    alpha_candidates: list[dict],
    layer_c_actions: list[dict],
    core_cards: list[dict] | None = None,
    alpha_bet_signals: list[dict] | None = None,
) -> str:
    """4-Layer (0/A/B/C) 결과 종합 헤드라인.

    우선순위:
    - Layer 0 (Alpha Bet): STOP/SELL 신호 — 최우선 (사용자 본인 룰)
    - Layer A (포트폴리오): warn severity — 두 번째
    - Layer B (Alpha): Core 트래커 강한 매수 / 알파 후보
    - Layer C (리밸런싱): 액션 유무

    Returns: 한 줄 헤드라인 (max ~60자)
    """
    bits: list[str] = []

    # 0) Layer 0 — Alpha Bet STOP/SELL/TRIM (최우선)
    if alpha_bet_signals:
        critical = [s for s in alpha_bet_signals if s.get("verdict") in ("STOP", "SELL")]
        if critical:
            s = critical[0]
            name = s.get("bet_name", "").split(" ")[0] if s.get("bet_name") else ""
            verdict = s["verdict"]
            label_short = "손절" if verdict == "STOP" else "익절 도달"
            bits.append(f"{name} {label_short}!" if name else f"{verdict} 신호")
            # STOP/SELL 발화되면 다른 신호는 거의 의미 없음 — 단, 알파 후보 1줄만 추가
            if alpha_candidates:
                bits.append(f"알파 후보 {len(alpha_candidates)}건")
            return " · ".join(bits)
        trims = [s for s in alpha_bet_signals if s.get("verdict") == "TRIM"]
        if trims:
            s = trims[0]
            name = s.get("bet_name", "").split(" ")[0] if s.get("bet_name") else ""
            bits.append(f"{name} 부분 익절 검토" if name else "부분 익절 검토")

    # 1) Layer A — warn severity 가 있으면 최우선 (포트폴리오 risk)
    warns = [it for it in (layer_a_items or []) if it.get("severity") == "warn"]
    if warns:
        # 반도체 집중이 있으면 그게 가장 본질적
        semi_item = next((it for it in warns if "반도체" in it.get("label", "")), None)
        top_item = next((it for it in warns if "단일 종목" in it.get("label", "")), None)
        if semi_item:
            # "반도체 69%" 식 압축
            detail = semi_item["detail"]
            # 첫 % 추출
            import re
            m = re.search(r"(\d+%)", detail)
            pct = m.group(1) if m else ""
            bits.append(f"반도체 {pct} 베팅 유지" if pct else "반도체 집중 베팅")
        elif top_item:
            bits.append("단일 종목 집중")

    # 2) Layer B — Core 트래커 강한 매수 신호 + alpha 후보
    strong_buy_core = None
    if core_cards:
        for c in core_cards:
            v = c.get("verdict", "")
            if "진입 적기" in v or "공격 진입" in v:
                strong_buy_core = c
                break
    if strong_buy_core:
        v = strong_buy_core['verdict']
        n = strong_buy_core['name']
        # verdict 에 이미 name 들어있으면 verdict 만 (중복 제거)
        bits.append(v if n in v else f"{n} {v}")
    elif alpha_candidates:
        bits.append(f"고확신 알파 {len(alpha_candidates)}건")

    # 3) Layer C — 액션 합성 (가장 중요한 1건)
    high_actions = [a for a in (layer_c_actions or []) if a.get("priority") == "high"]
    if high_actions:
        # 액션 자체를 짧게 — '<주체> <동사>' 형태로 압축
        act = high_actions[0].get("action", "")
        # 'KODEX SK하이닉스레버리지 비중 축소 검토' → '하이닉스 비중 축소'
        short = act
        if "비중 축소" in act:
            short = "비중 축소 검토"
        elif "익절" in act or "수익실현" in act:
            short = "단계 익절 검토"
        elif "손절" in act or "비중 정리" in act:
            short = "손실 정리 검토"
        elif "분할 진입" in act:
            short = "분할 진입 검토"
        bits.append(short)
    elif not layer_c_actions:
        # 액션 0건 = 구조 유지
        if alpha_candidates:
            bits.append("진입 타이밍 점검")
        else:
            bits.append("인내 모드 — 구조 유지")

    if not bits:
        return "데이터 수집 중"
    return " · ".join(bits)


_SEVERITY_COLOR = {
    "warn": "#EF4444",
    "info": "#3B82F6",
    "ok": "#22C55E",
}

_PRIORITY_COLOR = {
    "high": ("#EF4444", "긴급"),
    "medium": ("#F59E0B", "주의"),
    "low": ("#94A3B8", "참고"),
}


_VERDICT_COLOR = {
    "STOP": "#EF4444",
    "SELL": "#F59E0B",
    "TRIM": "#F59E0B",
    "ADD":  "#06B6D4",
    "STAY": "#22C55E",
}
_VERDICT_LABEL_KO = {
    "STOP": "🛑 손절",
    "SELL": "📤 전량 익절",
    "TRIM": "✂️ 부분 익절",
    "ADD":  "➕ 추가 매수",
    "STAY": "✅ 유지 (Stay the Course)",
}


def render_layer_0_html(signals: list[dict]) -> str:
    """Layer 0 — Alpha Bet 신호. 사용자 본인 ledger 의 종목별 룰 기반.

    신호 없거나 STAY 만 있으면 한 줄 간단 요약. STOP/SELL/TRIM 있으면 강조 박스.
    """
    if not signals:
        body = (
            '<div style="font-size:13px; color:var(--muted); line-height:1.6;">'
            'Active alpha bet 없음 — data/alpha_bets.json 에 status="active" bet 추가 시 노출'
            '</div>'
        )
        return (
            '<section style="margin-bottom:18px; padding:14px 18px; '
            'background:var(--panel); border:1px solid var(--border); '
            'border-left:3px solid var(--muted); border-radius:8px;">'
            '<div style="font-size:10.5px; color:var(--muted); font-weight:700; '
            'text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px;">'
            'Layer 0 — Alpha Bet 신호 (본인 룰)</div>'
            f'{body}</section>'
        )

    # 신호별 카드
    cards = []
    for s in signals:
        verdict = s.get("verdict", "STAY")
        color = _VERDICT_COLOR.get(verdict, "#94A3B8")
        v_label = _VERDICT_LABEL_KO.get(verdict, verdict)
        label = s.get("label", "")
        detail = s.get("detail", "")
        cards.append(
            f'<div style="padding:10px 14px; margin-bottom:6px; '
            f'background:var(--panel-2); border-left:3px solid {color}; border-radius:6px;">'
            f'<div style="display:flex; justify-content:space-between; gap:10px; '
            f'flex-wrap:wrap; align-items:baseline; margin-bottom:4px;">'
            f'<div style="font-size:12.5px; color:var(--text); font-weight:600;">{label}</div>'
            f'<div style="font-size:13px; font-weight:800; color:{color};">{v_label}</div>'
            f'</div>'
            f'<div style="font-size:12px; color:var(--text-soft); line-height:1.55;">{detail}</div>'
            f'</div>'
        )

    return (
        '<section style="margin-bottom:18px; padding:14px 18px; '
        'background:var(--panel); border:1px solid var(--border); '
        'border-left:3px solid #06B6D4; border-radius:8px;">'
        '<div style="font-size:10.5px; color:#06B6D4; font-weight:700; '
        'text-transform:uppercase; letter-spacing:.06em; margin-bottom:8px;">'
        '🎯 Layer 0 — Alpha Bet 신호 (본인 ledger 룰)</div>'
        + "".join(cards) +
        '</section>'
    )


def render_layer_a_html(items: list[dict]) -> str:
    """Layer A — 포트폴리오 점검 HTML 블록."""
    if not items:
        body = (
            '<div style="font-size:13px; color:var(--muted); line-height:1.6;">'
            '특이 사항 없음 — 집중도·레버리지·익절·손실 모두 임계 내'
            '</div>'
        )
    else:
        rows = ""
        for it in items:
            color = _SEVERITY_COLOR.get(it["severity"], "#94A3B8")
            rows += (
                '<div style="display:flex; gap:10px; padding:7px 0; '
                'border-top:1px solid var(--line);">'
                f'<div style="flex:0 0 4px; border-radius:2px; background:{color};"></div>'
                '<div style="flex:1 1 auto;">'
                '<div style="font-size:12px; color:var(--muted); '
                f'font-weight:700;">{it["label"]}</div>'
                '<div style="font-size:13.5px; color:var(--text); '
                f'margin-top:1px; line-height:1.5;">{it["detail"]}</div>'
                '</div></div>'
            )
        body = f'<div style="margin-top:2px;">{rows}</div>'
    return (
        '<div style="font-size:12px; color:var(--muted); '
        'margin:14px 0 4px; letter-spacing:.03em;">'
        '<span style="color:var(--text-mid); font-weight:700;">1.</span> '
        '내 포트폴리오 점검</div>'
        + body
    )


def render_core_tracker_row(card: dict) -> str:
    """Core tracker 한 줄 — 항상 노출."""
    price = card.get("price")
    dd = card.get("dd_pct")
    dr = card.get("daily_pct")
    color = card.get("color", "#94A3B8")
    verdict = card.get("verdict", "—")
    detail = card.get("detail", "")

    def pct_html(v: float | None, kind: str = "default") -> str:
        if v is None:
            return '<span style="color:#64748B;">—</span>'
        if kind == "daily":
            cls = "color:#22C55E;" if v >= 0 else "color:#EF4444;"
        else:
            cls = "color:#EF4444;" if v < -5 else "color:#94A3B8;"
        sign = "+" if v > 0 else ""
        return f'<span style="{cls}">{sign}{v:.2f}%</span>'

    price_str = ""
    if price is not None:
        if card.get("symbol") in ("BTC-USD",):
            price_str = f"${price:,.0f}"
        elif card.get("kind") == "index_kr":
            price_str = f"₩{price:,.0f}"
        else:
            price_str = f"${price:,.2f}"

    return (
        '<div style="display:flex; justify-content:space-between; gap:10px; '
        'padding:8px 0; border-top:1px solid var(--line);">'
        '<div style="flex:1 1 auto; min-width:0;">'
        '<div style="font-size:13.5px; color:var(--text); font-weight:600;">'
        f'{card["name"]} '
        f'<span style="color:var(--muted); font-weight:400; font-size:11px;">{card["subtitle"]}</span>'
        '</div>'
        '<div style="font-size:11px; color:var(--muted); margin-top:1px;">'
        f'{price_str}  ·  일일 {pct_html(dr, "daily")}  ·  DD {pct_html(dd)}'
        '</div>'
        '</div>'
        '<div style="flex:0 0 auto; text-align:right;">'
        f'<div style="font-size:12px; color:{color}; font-weight:700;">{verdict}</div>'
        + (f'<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">{detail}</div>' if detail else '')
        + '</div>'
        '</div>'
    )


def render_alpha_candidate_row(c: dict) -> str:
    """Alpha 후보 한 줄 — score≥80+DD≤-10% 통과한 것만."""
    dd = c.get("drawdown")
    dd_str = f"{dd * 100:+.1f}%" if dd is not None else "—"
    sc = c.get("score")
    sc_str = f"{sc:.0f}" if sc is not None else "—"
    return (
        '<div style="display:flex; justify-content:space-between; gap:10px; '
        'padding:8px 0; border-top:1px solid var(--line);">'
        '<div style="flex:1 1 auto; min-width:0;">'
        '<div style="font-size:13.5px; color:var(--text); font-weight:600;">'
        f'{c["name"]} '
        f'<span style="color:var(--muted); font-weight:400; font-size:12px;">{c["ticker"]}</span>'
        '</div>'
        '<div style="font-size:12px; color:var(--muted); margin-top:1px;">'
        f'{c.get("theme", "")} · {c.get("tag", "")}</div>'
        '</div>'
        '<div style="flex:0 0 auto; text-align:right;">'
        '<div style="font-size:11px; color:#15803D; font-weight:700;">'
        f'score {sc_str} 🔥</div>'
        '<div style="font-size:11px; color:#EF4444; margin-top:2px;">'
        f'DD {dd_str}</div>'
        '</div>'
        '</div>'
    )


def render_parking_row(p: dict) -> str:
    """Parking 후보 한 줄."""
    dd = p.get("dd_pct")
    dd_str = f"{dd:+.1f}%" if dd is not None else "—"
    color = p.get("color", "#94A3B8")
    price = p.get("price")
    price_str = f"${price:,.2f}" if price is not None else ""
    return (
        '<div style="display:flex; justify-content:space-between; gap:10px; '
        'padding:8px 0; border-top:1px solid var(--line);">'
        '<div style="flex:1 1 auto; min-width:0;">'
        '<div style="font-size:13.5px; color:var(--text); font-weight:600;">'
        f'{p["name"]} '
        f'<span style="color:var(--muted); font-weight:400; font-size:12px;">{p["symbol"]}</span>'
        '</div>'
        '<div style="font-size:11px; color:var(--muted); margin-top:1px;">'
        f'{p.get("sector", "")} · {price_str} · DD {dd_str}</div>'
        '</div>'
        '<div style="flex:0 0 auto; text-align:right;">'
        f'<div style="font-size:11px; color:{color}; font-weight:700;">{p["verdict"]}</div>'
        + (f'<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">{p.get("detail", "")}</div>'
           if p.get("detail") else '')
        + '</div>'
        '</div>'
    )


def render_layer_b_html(
    core_cards: list[dict],
    alpha_candidates: list[dict],
    parking_cards: list[dict],
) -> str:
    """Layer B — 3-sub 구조: Core Trackers / Alpha Conviction / Parking.

    - Core: 항상 표시 (data 없어도 자리)
    - Alpha: 임계 통과 없으면 "인내 모드" 명시 (소음 X, 정직)
    - Parking: 후보 있을 때만 (없으면 sub 자체 생략)
    """
    out_parts = []

    # ── 헤더 ───────────────────────────────────────
    out_parts.append(
        '<div style="font-size:12px; color:var(--muted); '
        'margin:18px 0 4px; letter-spacing:.03em;">'
        '<span style="color:var(--text-mid); font-weight:700;">2.</span> '
        '시장에서 추적·발굴</div>'
    )

    # ── 2-A) Core Daily Trackers ──────────────────
    out_parts.append(
        '<div style="font-size:11px; color:var(--muted); '
        'margin:8px 0 0px; letter-spacing:.02em;">▸ Core Trackers (매일 추적)</div>'
    )
    if core_cards:
        out_parts.append('<div style="margin-top:0;">')
        for c in core_cards:
            out_parts.append(render_core_tracker_row(c))
        out_parts.append('</div>')
    else:
        out_parts.append(
            '<div style="font-size:12px; color:var(--muted); padding:8px 0;">'
            '데이터 수집 중'
            '</div>'
        )

    # ── 2-B) High-Conviction Alpha ────────────────
    out_parts.append(
        '<div style="font-size:11px; color:var(--muted); '
        'margin:14px 0 0px; letter-spacing:.02em;">'
        '▸ 고확신 알파 <span style="font-size:10px;">(score ≥ 80 & DD ≤ -10%)</span></div>'
    )
    if alpha_candidates:
        out_parts.append('<div style="margin-top:0;">')
        for c in alpha_candidates:
            out_parts.append(render_alpha_candidate_row(c))
        out_parts.append('</div>')
    else:
        out_parts.append(
            '<div style="font-size:12.5px; color:var(--muted); padding:8px 0; '
            'border-top:1px solid var(--line); line-height:1.55;">'
            '임계 80점 통과 종목 없음 — '
            '<span style="color:#94A3B8;">인내 모드: 신호 강도 부족 시 진입 보류가 정답.</span>'
            '</div>'
        )

    # ── 2-C) Parking Candidates ───────────────────
    # 후보 있을 때만 sub 노출
    if parking_cards:
        out_parts.append(
            '<div style="font-size:11px; color:var(--muted); '
            'margin:14px 0 0px; letter-spacing:.02em;">'
            '▸ 안전 파킹 후보 <span style="font-size:10px;">(MCD·COST·WMT 류 — 시장 과열·현금 비축 국면)</span></div>'
        )
        out_parts.append('<div style="margin-top:0;">')
        for p in parking_cards:
            out_parts.append(render_parking_row(p))
        out_parts.append('</div>')

    return ''.join(out_parts)


def render_layer_c_html(actions: list[dict]) -> str:
    """Layer C — 리밸런싱 액션 HTML. actions 비어있으면 빈 문자열 반환 (섹션 생략)."""
    if not actions:
        return ""  # 소음 제거 — 신호 없으면 섹션 자체 누락
    # 우선순위 정렬
    _order = {"high": 0, "medium": 1, "low": 2}
    actions = sorted(actions, key=lambda a: _order.get(a.get("priority"), 9))
    rows = ""
    for a in actions:
        color, label = _PRIORITY_COLOR.get(a.get("priority"), ("#94A3B8", "참고"))
        basis = a.get("basis", "")
        rows += (
            '<div style="display:flex; gap:11px; padding:9px 0; '
            'border-top:1px solid var(--line);">'
            f'<div style="flex:0 0 5px; border-radius:3px; background:{color};"></div>'
            '<div style="flex:1 1 auto;">'
            '<div style="font-size:13.5px; color:var(--text); '
            'font-weight:600; line-height:1.5;">'
            f'<span style="color:{color}; font-size:10px; font-weight:700; '
            f'letter-spacing:.04em; margin-right:8px;">{label}</span>'
            f'{a["action"]}</div>'
            + (f'<div style="font-size:12px; color:var(--muted); '
               f'margin-top:2px; line-height:1.5;">{basis}</div>' if basis else "")
            + '</div></div>'
        )
    return (
        '<div style="font-size:12px; color:var(--muted); '
        'margin:18px 0 4px; letter-spacing:.03em;">'
        '<span style="color:var(--text-mid); font-weight:700;">3.</span> '
        '리밸런싱 액션</div>'
        f'<div style="margin-top:2px;">{rows}</div>'
    )
