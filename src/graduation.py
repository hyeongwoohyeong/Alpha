"""Graduation Tracker — 능동 전술 운용 → 패시브 ETF 시스템 '졸업' 준비도.

핵심 질문:
- 사용자가 언제쯤 능동 전술 매매(국면 기반 베타 조절, 레버리지 ETF, parking
  종목 등)에서 손을 떼고 정해진 ETF 비중을 굴리는 단순 시스템으로 전환할
  준비가 되었는가?

원칙:
- 점수는 0~10 스케일. 모든 컴포넌트는 가정·확신도를 같이 보여준다.
- 시드(net_worth) 가 목표(10억) 의 15.6% 인 현 시점에서는 "조기 단계" 가
  정상이다 — 솔직하게 말한다. 과장 금지.
- 데이터가 부족하면 "데이터 부족" 으로 표시. 절대 위로 예외를 던지지 않음.
- target_allocation 은 사용자가 나중에 결정 — 여기서는 강제하지 않는다.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("graduation")

NEEDS_CHECK = "확인 필요"

# ── Config — 사용자가 손쉽게 수정할 수 있도록 모듈 상단 상수 ─────────────
TARGET_NET_WORTH_KRW: int = 1_000_000_000  # 10억원 — graduation seed 목표
# 목표 ETF 비중 — 사용자가 나중에 결정. None 인 동안 readiness 만 트래킹.
TARGET_ALLOCATION: dict[str, float] | None = None


# ── 안전 헬퍼 ──────────────────────────────────────────────────────────
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


def _clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


def _lerp(value: float, lo_in: float, hi_in: float,
          lo_out: float = 0.0, hi_out: float = 10.0) -> float:
    """linear interpolation, clamped 0~10."""
    if hi_in == lo_in:
        return (lo_out + hi_out) / 2.0
    t = (value - lo_in) / (hi_in - lo_in)
    t = max(0.0, min(1.0, t))
    return lo_out + t * (hi_out - lo_out)


def _row_get(row: Any, key: str) -> Any:
    """sqlite3.Row / dict / None 안전 접근."""
    if row is None:
        return None
    try:
        keys = row.keys() if hasattr(row, "keys") else []
    except Exception:
        return None
    if key in keys:
        try:
            return row[key]
        except Exception:
            return None
    return None


def _derive_net_worth_krw(
    portfolio_diag: dict[str, Any], holdings: list[dict] | None = None
) -> float | None:
    """portfolio_diag / holdings 에서 net_worth_krw 유도.

    1) portfolio_diag 가 net_worth_krw 를 직접 갖고 있으면 그것을 사용.
    2) holdings (list) 가 주어지면 portfolio_review.diagnose_portfolio 와
       동일한 방식으로 value_krw / (net_worth_pct/100) 중앙값을 계산.
    3) 둘 다 실패하면 portfolio_diag.total_value_krw / median(value/net_worth_pct)
       에서 net_worth_pct 가 있는 종목들로 중앙값 추정.
    4) 그래도 안 되면 None.
    """
    pd = portfolio_diag or {}
    direct = _f(pd.get("net_worth_krw"))
    if direct and direct > 0:
        return direct

    estimates: list[float] = []
    if holdings:
        for h in holdings:
            pct = _f(h.get("net_worth_pct"))
            v = _f(h.get("value_krw"))
            if pct is not None and pct != 0 and v is not None and v > 0:
                estimates.append(v / (pct / 100.0))

    if estimates:
        estimates.sort()
        m = len(estimates) // 2
        if len(estimates) % 2 == 1:
            return estimates[m]
        return (estimates[m - 1] + estimates[m]) / 2.0

    return None


# ── Component scoring ─────────────────────────────────────────────────

def _score_seed(net_worth_krw: float | None) -> tuple[float | None, str]:
    """시드 점수 — 목표 대비 도달도. min(10, NW/target*10)."""
    if net_worth_krw is None or net_worth_krw <= 0:
        return None, f"시드 데이터 부족 — {NEEDS_CHECK}."
    target = float(TARGET_NET_WORTH_KRW)
    if target <= 0:
        return None, "목표 시드가 설정되지 않았습니다."
    raw = (net_worth_krw / target) * 10.0
    score = min(10.0, max(0.0, raw))
    pct = (net_worth_krw / target) * 100.0
    comment = (
        f"시드 {net_worth_krw / 1e8:.2f}억 / 목표 "
        f"{target / 1e8:.0f}억 = {pct:.1f}% (점수 {score:.2f}/10)."
    )
    return score, comment


def _score_market(regime: Any | None, cycle: Any | None) -> tuple[float | None, str]:
    """시장 점수 — 시스템 전환 타이밍이 양호한가.

    - 극단 과열(overheat >= 80) 또는 극단 낙폭(cycle.drawdown_pct <= -0.20)
      → ~3/10 (시스템 진입 타이밍 나쁨).
    - 중간 대역 (overheat 35~65, drawdown -0.05 ~ 0) → ~9/10 (양호).
    - 그 외 → 선형 보간.
    """
    overheat = _f(_row_get(regime, "market_overheat_score"))
    cur_regime = _row_get(regime, "current_regime")
    drawdown = _f(_row_get(cycle, "drawdown_pct"))  # 분수
    trend = _row_get(cycle, "trend_state")

    if overheat is None and drawdown is None:
        return None, f"시장 국면 데이터 부족 — {NEEDS_CHECK}."

    notes: list[str] = []

    # 극단 신호 우선
    if overheat is not None and overheat >= 80:
        score = 3.0
        notes.append(f"Overheat {overheat:.0f}/100 — 극단 과열")
    elif drawdown is not None and drawdown <= -0.20:
        score = 3.0
        notes.append(f"고점 대비 낙폭 {drawdown * 100:.1f}% — 극단 조정")
    else:
        # 양호한 중간 대역에서 ~9, 양 끝으로 갈수록 ~4
        parts: list[float] = []
        if overheat is not None:
            if 35 <= overheat <= 65:
                parts.append(9.0)
            elif overheat < 35:
                # 낮은 과열도(공포 가까움) — 6
                parts.append(_lerp(overheat, 0, 35, 4.0, 6.0))
            else:  # 65~80
                parts.append(_lerp(overheat, 65, 80, 6.0, 4.0))
            notes.append(f"Overheat {overheat:.0f}/100")
        if drawdown is not None:
            if -0.05 <= drawdown <= 0.0:
                parts.append(9.0)
            elif drawdown > 0:
                parts.append(6.0)  # 가격이 ATH 위로 폭주
            else:  # -0.20 ~ -0.05
                parts.append(_lerp(drawdown, -0.20, -0.05, 4.0, 8.0))
            notes.append(f"낙폭 {drawdown * 100:+.1f}%")
        if not parts:
            return None, f"시장 점수 산정 불가 — {NEEDS_CHECK}."
        score = sum(parts) / len(parts)

    if cur_regime:
        notes.append(f"국면 '{cur_regime}'")
    if trend:
        notes.append(f"추세 '{trend}'")

    score = _clamp(score)
    comment = f"시장 점수 {score:.2f}/10 — " + ", ".join(notes) + "."
    return score, comment


def _score_tactical_cleanup(
    portfolio_diag: dict[str, Any]
) -> tuple[float | None, str]:
    """전술 정리도 점수 — 레버리지·집중도가 정상화됐는가.

    - leverage_exposure_pct >= 40 → ~3/10; <= 10 → ~9/10; 선형.
    - top_holding_pct >= 40 → -2; <= 20 → +0; 선형 (감점).
    - 결합 후 0~10 clamp.
    """
    pd = portfolio_diag or {}
    lev = _f(pd.get("leverage_exposure_pct"))
    top = _f(pd.get("top_holding_pct"))

    if lev is None and top is None:
        return None, f"포트폴리오 진단 데이터 부족 — {NEEDS_CHECK}."

    notes: list[str] = []

    if lev is not None:
        # 40% 이상이면 3, 10% 이하면 9, 그 사이 선형 (반대 방향)
        lev_score = _lerp(lev, 10.0, 40.0, 9.0, 3.0)
        notes.append(f"레버리지 노출 {lev:.1f}%")
    else:
        lev_score = 6.0  # 데이터 없을 때 중립
        notes.append("레버리지 노출 데이터 없음(중립 6.0)")

    if top is not None:
        if top >= 40:
            penalty = -2.0
        elif top <= 20:
            penalty = 0.0
        else:
            # 20~40 선형: -0 → -2
            penalty = -2.0 * (top - 20.0) / 20.0
        notes.append(f"최대 비중 {top:.1f}% (감점 {penalty:.1f})")
    else:
        penalty = 0.0
        notes.append("집중도 데이터 없음")

    score = _clamp(lev_score + penalty)
    comment = f"전술 정리 {score:.2f}/10 — " + ", ".join(notes) + "."
    return score, comment


# ── Status / commentary ───────────────────────────────────────────────

def _status_ko(readiness: float | None) -> str:
    if readiness is None:
        return "데이터 부족"
    if readiness >= 8.5:
        return "준비됨 — ETF 시스템 전환 검토 시점"
    if readiness >= 5.0:
        return "근접 — 일부 조건 충족"
    if readiness > 0:
        return "조기 단계"
    return "데이터 부족"


def _build_commentary(
    seed_score: float | None,
    market_score: float | None,
    tactical_score: float | None,
    readiness: float | None,
    net_worth_krw: float | None,
    target_krw: int,
    portfolio_diag: dict[str, Any],
) -> str:
    """1~2 문장의 솔직한 한국어 코멘트."""
    if readiness is None:
        return (
            "Graduation 점수 산정에 필요한 데이터가 부족합니다 — 포트폴리오/"
            "시장 데이터를 확인해주세요."
        )

    parts: list[str] = []
    if net_worth_krw is not None:
        pct = (net_worth_krw / target_krw) * 100.0 if target_krw > 0 else 0.0
        parts.append(
            f"시드 {net_worth_krw / 1e8:.2f}억 / {target_krw / 1e8:.0f}억 "
            f"목표의 {pct:.1f}%."
        )

    # 시장 / 전술 코멘트
    lev = _f(portfolio_diag.get("leverage_exposure_pct"))
    top = _f(portfolio_diag.get("top_holding_pct"))

    market_note = None
    if market_score is not None:
        if market_score >= 8:
            market_note = "시장 신호 양호"
        elif market_score >= 5:
            market_note = "시장 신호 보통"
        else:
            market_note = "시장 신호 극단(전환 부적합)"

    tactical_note = None
    if tactical_score is not None:
        if lev is not None and top is not None:
            tactical_note = (
                f"레버리지 {lev:.0f}%·집중도 {top:.0f}%로 전술 정리가 우선"
                if tactical_score < 6 else
                f"레버리지 {lev:.0f}%·집중도 {top:.0f}%로 전술 정리 양호"
            )
        else:
            tactical_note = (
                "전술 정리 양호" if tactical_score >= 6 else "전술 정리 필요"
            )

    if readiness >= 8.5:
        parts.append(
            "시드·시장·전술 모두 충족 — ETF 시스템 전환을 본격 검토할 시점."
        )
    elif readiness >= 5.0:
        seg = []
        if market_note:
            seg.append(market_note)
        if tactical_note:
            seg.append(tactical_note)
        parts.append(
            "근접 단계입니다 — " + (", ".join(seg) if seg else "일부 조건 충족")
            + ". 시드 도달 시 점진적 전환 가능."
        )
    else:
        seg = []
        if market_note:
            seg.append(market_note)
        if tactical_note:
            seg.append(tactical_note)
        parts.append(
            "아직 조기 단계입니다 — "
            + (", ".join(seg) + "." if seg else "시드 적립과 전술 정리가 우선.")
        )

    return " ".join(parts)


# ── Main API ──────────────────────────────────────────────────────────

def evaluate_graduation_readiness(
    portfolio_diag: dict[str, Any] | None,
    regime: Any | None,
    cycle: Any | None,
    *,
    holdings: list[dict] | None = None,
) -> dict[str, Any]:
    """능동 전술 운용 → ETF 시스템 졸업 준비도 평가.

    Args:
        portfolio_diag: portfolio_review.diagnose_portfolio() 결과 dict
            — total_value_krw, top_holding_pct, leverage_exposure_pct 등을 포함.
        regime: market_regime 테이블의 sqlite3.Row / dict — current_regime,
            market_overheat_score.
        cycle: locate_current_market() 결과 dict — drawdown_pct(분수),
            trend_state.
        holdings: 선택 — net_worth_krw 직접 유도용 (value_krw, net_worth_pct).
            diagnose_portfolio 가 net_worth_krw 를 갖고 있지 않을 때 fallback.

    Returns:
        dict with net_worth_krw, target_krw, seed_proximity_pct, seed_score,
        market_score, tactical_cleanup_score, readiness_score, status_ko,
        commentary_ko, components_ko.

    모든 데이터가 부족하면 readiness_score=None, status_ko='데이터 부족'.
    절대 예외를 던지지 않는다.
    """
    pd = portfolio_diag or {}
    target = int(TARGET_NET_WORTH_KRW)

    # 1) 순자산
    net_worth = _derive_net_worth_krw(pd, holdings)
    seed_score, seed_comment = _score_seed(net_worth)

    # 2) 시장
    market_score, market_comment = _score_market(regime, cycle)

    # 3) 전술 정리
    tactical_score, tactical_comment = _score_tactical_cleanup(pd)

    # 4) 가중 평균
    weights = {"seed": 0.50, "market": 0.25, "tactical": 0.25}
    parts: list[tuple[float, float]] = []
    if seed_score is not None:
        parts.append((seed_score, weights["seed"]))
    if market_score is not None:
        parts.append((market_score, weights["market"]))
    if tactical_score is not None:
        parts.append((tactical_score, weights["tactical"]))

    if parts:
        total_w = sum(w for _, w in parts) or 1.0
        readiness = sum(s * w for s, w in parts) / total_w
        readiness = round(_clamp(readiness), 2)
    else:
        readiness = None

    status = _status_ko(readiness)
    if net_worth is None and seed_score is None and market_score is None \
            and tactical_score is None:
        status = "데이터 부족"

    commentary = _build_commentary(
        seed_score, market_score, tactical_score, readiness,
        net_worth, target, pd,
    )

    components = [seed_comment, market_comment, tactical_comment]

    seed_prox = None
    if net_worth is not None and target > 0:
        seed_prox = round((net_worth / target) * 100.0, 2)

    return {
        "net_worth_krw": net_worth,
        "target_krw": target,
        "seed_proximity_pct": seed_prox,
        "seed_score": (round(seed_score, 2) if seed_score is not None else None),
        "market_score": (
            round(market_score, 2) if market_score is not None else None
        ),
        "tactical_cleanup_score": (
            round(tactical_score, 2) if tactical_score is not None else None
        ),
        "readiness_score": readiness,
        "status_ko": status,
        "commentary_ko": commentary,
        "components_ko": components,
    }
