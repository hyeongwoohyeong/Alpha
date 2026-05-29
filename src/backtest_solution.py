"""백테스트 기반 오늘의 대응 — 백테스트 결과를 퀀트처럼 직접 소화해
'오늘 무엇을 할지' 의 구체적 처방을 만든다.

원칙 (Phase 1~4 모듈과 동일):
- Rule-based. LLM 없이 완전 동작 — 순수 룰 + 백테스트 수치 합성.
- regime/crash/bt_summary 가 없거나 깨져도 예외를 위로 던지지 않는다 — graceful.
- 백테스트 수치를 인용한 항목은 반드시 그렇게 명시하고, 룰 폴백 항목은
  '룰 기준 잠정 권고' 로 솔직하게 라벨링한다 (거짓 정밀도 금지).
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("backtest_solution")

# Item C 의 caveat — 고정 문구
CAVEAT = (
    "※ 과거 백테스트 결과이며 미래 수익을 보장하지 않습니다. "
    "레버리지 ETF(QLD·TQQQ)는 변동성 끌림과 깊은 낙폭 위험이 있습니다. "
    "분할·단계 진입을 전제로 한 참고 가이드입니다."
)

# 낙폭 단계매수 사다리 — (낙폭 임계치 %, 단계, 권장 상품, 누적 목표비중 %)
# *** 폴백 전용 (FALLBACK ONLY) ***
# 이 표는 하드코딩 가설이다. 실증 데이터(QQQ 1999~)는 TQQQ 의 sweet spot 이
# -20% 가 아니라 -10~-15% (avg +23.8%, win 81%) 임을 보여준다. 따라서 본 사다리는
# entry_timing_buckets 통계가 비었거나 recommend_current_entry 결과가 없을 때만
# 사용되는 *graceful fallback* 으로 격하되었다. 정상 동작에서는
# build_backtest_solution(cycle_recommendation=...) 가 데이터 기반 추천을 그대로 쓴다.
_DEPLOY_LADDER: list[tuple[float, int, str, int]] = [
    (-5.0, 1, "QQQ", 20),
    (-10.0, 2, "QLD", 45),
    (-15.0, 3, "QLD", 65),
    (-20.0, 4, "TQQQ", 80),
    (-25.0, 5, "TQQQ", 100),
]

# 익절(Item C) 트리거 — 소유자의 materiality 원칙
_PROFIT_ITEM_MIN_VALUE_KRW = 5_000_000   # ₩5M 미만 포지션은 의사결정 비대상
_PROFIT_ITEM_MIN_GAIN_KRW = 2_000_000    # 미실현 수익 ₩2M 미만은 보호할 가치 없음
_PROFIT_ITEM_MIN_RETURN_PCT = 15.0       # +15% 미만은 "익절할 winner" 라고 부르기 무리
                                          # — 트림은 의미있는 % 수익이 났을 때 의미.

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

_DEPLOY_STRATEGY_KEY = "Drawdown Deployment (QQQ→QLD→TQQQ)"
_BUYHOLD_KEY = "Buy&Hold QQQ"


# ---------------------------------------------------------------------------
# 안전 접근 헬퍼
# ---------------------------------------------------------------------------

def _rget(row: Any, key: str) -> Any:
    """sqlite3.Row / dict 안전 접근 — 키 없거나 row None 이면 None 반환."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        keys = row.keys()
    except Exception:
        return None
    return row[key] if key in keys else None


def _to_float(x: Any) -> float | None:
    """숫자 변환 — 실패하면 None."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _overheat_band(score: float) -> str | None:
    """Overheat Score → backtest_engine 의 OVERHEAT_BANDS 밴드 이름."""
    try:
        from .backtest_engine import OVERHEAT_BANDS
    except Exception as e:
        log.debug("OVERHEAT_BANDS import 실패: %s", e)
        return None
    for lo, hi, name in OVERHEAT_BANDS:
        if lo <= score < hi:
            return name
    return None


# ---------------------------------------------------------------------------
# Item 빌더
# ---------------------------------------------------------------------------

def _cycle_get(cycle: Any, key: str) -> Any:
    """cycle dict 안전 접근 — None 이거나 dict 아니면 None."""
    if not isinstance(cycle, dict):
        return None
    return cycle.get(key)


def _credit_stress_level(crash_row: Any) -> str | None:
    """crash_row 의 credit_stress_status → 'high'|'elevated'|'low'|None.

    원문 문자열은 한국어/영문이 섞일 수 있어 키워드로 판별한다.
    """
    raw = _rget(crash_row, "credit_stress_status")
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if any(k in s for k in ("high", "심각", "높", "위험", "stress", "경계")):
        # '경계' 는 elevated 쪽으로 — high 키워드 우선 매칭 후 분기
        if any(k in s for k in ("high", "심각", "위험", "매우")):
            return "high"
        return "elevated"
    if any(k in s for k in ("elevat", "상승", "주의", "warn", "moderate", "중간")):
        return "elevated"
    if any(k in s for k in ("low", "낮", "안정", "정상", "calm", "normal")):
        return "low"
    return None


def _build_drawdown_item(
    dd: float | None, bt_summary: dict,
    cycle: Any = None, crash_row: Any = None,
    cycle_recommendation: dict | None = None,
) -> tuple[dict | None, bool]:
    """Item A — 낙폭 대응. (item, cited_backtest) 반환. item None 이면 skip.

    cycle_recommendation (recommend_current_entry 결과) 가 있고 best_asset 이
    채워져 있으면 그 데이터 기반 추천을 그대로 사용한다 — 하드코딩 _DEPLOY_LADDER
    는 폴백으로 격하. 추세/크레딧 가중치는 그대로 적용.

    낙폭 깊이뿐 아니라 cycle 의 trend_state 와 crash_row 의 credit_stress 를
    함께 가중한다 — '낙폭 깊이 + 추세 상태 + 크레딧 스트레스' 의 합성 판단.
    """
    if dd is None:
        return None, False

    # 분수(-0.12) 로 저장됐으면 % 로 환산
    if abs(dd) <= 1.0:
        dd = dd * 100.0

    trend_state = _cycle_get(cycle, "trend_state")
    credit = _credit_stress_level(crash_row)

    # 백테스트 근거 — 단계매수 vs Buy&Hold MDD 비교
    cited = False
    strategies = (((bt_summary or {}).get("drawdown_deployment") or {})
                  .get("strategies") or {})
    dep = strategies.get(_DEPLOY_STRATEGY_KEY) or {}
    bh = strategies.get(_BUYHOLD_KEY) or {}
    d_mdd = _to_float(dep.get("max_drawdown"))
    b_mdd = _to_float(bh.get("max_drawdown"))

    if d_mdd is not None and b_mdd is not None:
        cited = True
        # max_drawdown 은 음수 — 더 큰 값(덜 음수)이 좋다
        diff_pp = abs(d_mdd - b_mdd) * 100.0
        verb = "줄였습니다" if d_mdd > b_mdd else "줄이지 못했습니다"
        basis = (
            f"과거 검증: 단계매수 전략 MDD {d_mdd*100:.0f}% vs "
            f"QQQ Buy&Hold {b_mdd*100:.0f}% — 단계매수가 낙폭을 "
            f"{diff_pp:.0f}%p {verb}"
        )
        d_ret = _to_float(dep.get("total_return"))
        b_ret = _to_float(bh.get("total_return"))
        if d_ret is not None and b_ret is not None:
            basis += (
                f" (누적수익 단계매수 {d_ret*100:+.0f}% vs "
                f"Buy&Hold {b_ret*100:+.0f}%)"
            )
    else:
        basis = "백테스트 데이터 누적 중 — crash_deployment 룰 기준 권고."

    # 얕은 낙폭 — 단계매수 단계 아님. 본격 배치 구간이 아니므로 여기서는
    # '낙폭 대응' 항목을 만들지 않고, 상승장 가이드(Item A')에서 다룬다.
    if dd > -5.0:
        return None, False

    # ── 데이터 기반 자산/단계 결정 ─────────────────────────────────────
    # cycle_recommendation 이 있고 best_asset 이 정해졌으면 실증 통계 기반.
    # 그렇지 않으면 (DB 비었거나 표본 부족) 하드코딩 _DEPLOY_LADDER 폴백.
    rec = cycle_recommendation if isinstance(cycle_recommendation, dict) else None
    used_recommendation = False
    if rec and rec.get("available") and rec.get("best_asset"):
        instrument = rec.get("best_asset")
        # 누적 목표비중은 실증 권고에는 없는 정보 — 낙폭 깊이에서 보수적으로 산출.
        # 데이터 사다리는 자산 선택을 데이터에 위임하지만, 목표 비중은 변동성
        # 위험 한도(소유자의 5M materiality 원칙 + 자본 보존)로 단순 룰을 둔다.
        if dd <= -25.0:
            target_pct = 100
            stage = 5
        elif dd <= -20.0:
            target_pct = 80
            stage = 4
        elif dd <= -15.0:
            target_pct = 65
            stage = 3
        elif dd <= -10.0:
            target_pct = 45
            stage = 2
        else:
            target_pct = 20
            stage = 1
        priority = "high" if dd <= -10.0 else "medium"
        used_recommendation = True
    else:
        # 폴백 — 하드코딩 사다리에서 도달한 가장 깊은 단계 선택.
        step = _DEPLOY_LADDER[0]
        for entry in _DEPLOY_LADDER:
            if dd <= entry[0]:
                step = entry
            else:
                break
        _thr, stage, instrument, target_pct = step
        priority = "high" if dd <= -10.0 else "medium"

    # 추세 상태·크레딧 스트레스로 배치 속도/우선순위 조정
    cautions: list[str] = []
    basis_extra: list[str] = []
    danger_trend = trend_state in ("Trend Breakdown", "Recovery from Bear")
    if danger_trend:
        # 같은 낙폭이라도 추세 붕괴/약세 회복 국면이면 더 위험 — 속도 늦춤
        cautions.append(
            "추세 상태가 약세권(추세 붕괴/약세 후 회복)이라 같은 낙폭도 "
            "더 위험 — 단계 투입 속도를 늦추고 한 단계 보수적으로 진입"
        )
        basis_extra.append(f"추세 상태 '{trend_state}' — 낙폭 위험 가중")
        # 우선순위 하향 (high→medium, medium→low) — 추격 자제
        priority = "medium" if priority == "high" else "low"
    elif trend_state == "Pullback in Uptrend" and dd > -10.0:
        # 상승추세 내 얕은 눌림 — 건강한 조정으로 프레이밍
        cautions.append(
            "상승추세 내 눌림(healthy dip) 성격 — 분할 진입에 우호적이나 "
            "한 번에 베타를 키우지 말 것"
        )
        basis_extra.append("추세 상태 '상승추세 내 눌림' — 건강한 조정 성격")

    if credit == "high":
        cautions.append("크레딧 스트레스 높음 — 단계 투입 속도 늦춤")
        basis_extra.append("크레딧 스트레스 높음 — 배치 속도 감속")
        priority = "medium" if priority == "high" else priority
    elif credit == "elevated":
        cautions.append("크레딧 스트레스 상승 — 단계 간 간격을 넓혀 진입")
        basis_extra.append("크레딧 스트레스 상승")
    elif credit == "low":
        basis_extra.append("크레딧 스트레스 낮음 — 낙폭 매수 우호적")

    action = (
        f"QQQ 고점 대비 {dd:.1f}% — 단계매수 {stage}단계 구간. "
        f"{instrument}로 누적 목표비중 {target_pct}%까지 분할 진입 검토."
    )
    if cautions:
        action += " " + " / ".join(cautions) + "."

    if basis_extra:
        basis = basis + " | " + " · ".join(basis_extra)

    # 데이터 사다리(empirical) 인용 — 사용된 경우 가장 앞에 명시
    if used_recommendation and rec:
        rec_rationale = (rec.get("rationale_ko") or "").strip()
        verdict = (rec.get("verdict") or "").strip()
        if rec_rationale:
            basis = "데이터 사다리 — " + rec_rationale + " | " + basis
        if verdict:
            action = f"[데이터 판단: {verdict}] " + action
        cited = True  # 실증 통계 인용 — 룰 폴백 아님

    item = {
        "title": "낙폭 대응",
        "action": action,
        "basis": basis,
        "priority": priority,
    }
    return item, cited


def _build_uptrend_item(
    dd: float | None, score: float | None, cycle: Any,
) -> tuple[dict | None, bool]:
    """Item A' — 상승장 가이드. 본격 배치 구간이 아닐 때(얕은 낙폭)
    추세 상태 + 과열 점수로 'Hold / Don't Chase / Stay Invested' 지시.

    이 항목이 엔진의 누락이던 '시장이 오르고 있을 때' 가이드다.
    (item, cited) 반환 — cited 는 항상 False (백테스트 인용 아님).
    """
    # dd 정규화
    norm_dd = dd
    if norm_dd is not None and abs(norm_dd) <= 1.0:
        norm_dd = norm_dd * 100.0

    # 본격 배치 구간(낙폭 >= -5%)이 아닐 때만 — 깊은 낙폭이면 Item A 가 담당
    if norm_dd is not None and norm_dd <= -5.0:
        return None, False

    trend_state = _cycle_get(cycle, "trend_state")
    f3 = _cycle_get(cycle, "similar_forward_3m")
    n_sample = _cycle_get(cycle, "similar_sample_count") or 0

    # 과거 유사 구간 base rate 를 basis 에 인용 (있을 때만, 정직하게)
    cycle_basis = ""
    try:
        if f3 is not None and n_sample and int(n_sample) > 0:
            n_int = int(n_sample)
            conf = ("표본 충분" if n_int >= 60
                    else "표본 보통" if n_int >= 20
                    else "표본 부족 — 신뢰도 낮음")
            cycle_basis = (
                f"과거 유사 구간 {n_int}개에서 3개월 평균 {f3*100:+.1f}% "
                f"({conf})"
            )
    except Exception:
        cycle_basis = ""

    high_overheat = score is not None and score >= 65.0
    mod_overheat = score is not None and 50.0 <= score < 65.0

    if trend_state == "Strong Uptrend" and not high_overheat:
        action = (
            "추세 양호 — 기존 수익 포지션 보유 유지(Hold Winners), "
            "신규 레버리지 추격은 자제."
        )
        priority = "medium" if mod_overheat else "low"
        basis = "추세 상태 '강한 상승추세'"
    elif trend_state == "Uptrend but Extended" or high_overheat:
        action = (
            "과열 신호 — 신규 레버리지 추격 금지(Do Not Chase), "
            "과열된 수익 포지션 일부 보호(Trim Overextended)."
        )
        priority = "medium"
        basis = (
            "추세 상태 '상승추세이나 과열'"
            if trend_state == "Uptrend but Extended"
            else f"Overheat Score {score:.0f} — 과열권"
        )
    else:
        action = "추세 유지 — 현 비중을 유지(Stay Invested)하며 추격은 자제."
        priority = "low"
        ts = trend_state if trend_state else "추세 데이터 부족"
        basis = f"추세 상태 '{ts}' — 중립"

    if cycle_basis:
        basis = basis + " · " + cycle_basis
    else:
        basis = basis + " · 시장 사이클 기준 잠정 권고(과거 표본 부족)"

    item = {
        "title": "상승장 대응",
        "action": action,
        "basis": basis,
        "priority": priority,
    }
    return item, False


def _build_overheat_item(
    score: float | None, bt_summary: dict
) -> tuple[dict | None, bool]:
    """Item B — 과열 대응. (item, cited_backtest) 반환. item None 이면 skip."""
    if score is None:
        return None, False

    band = _overheat_band(score)
    by_band = (((bt_summary or {}).get("overheat_forward") or {})
               .get("by_band") or {})

    avg3 = None
    confidence = None
    if band:
        band_data = by_band.get(band) or {}
        qqq = band_data.get("QQQ") or {}
        m3 = qqq.get("3m") or {}
        avg3 = _to_float(m3.get("avg"))
        confidence = qqq.get("confidence")

    # 백테스트 근거가 있을 때
    if avg3 is not None:
        conf_txt = confidence if confidence else "표본 제한"
        basis = (
            f"과거 검증: Overheat {band} 진입 후 QQQ 3개월 평균 "
            f"{avg3*100:+.1f}% (표본 {conf_txt})"
        )
        if avg3 < 0:
            action = "신규 레버리지·베타 확대를 보류하고 현 비중을 유지하십시오."
            priority = "high" if score >= 65 else "medium"
        else:
            action = (
                "과거 검증상 이 과열 구간의 3개월 기대수익은 "
                "플러스였습니다 — 단계적 베타 확대 여지."
            )
            priority = "medium" if score >= 65 else "low"
        item = {
            "title": "과열 대응",
            "action": action,
            "basis": basis,
            "priority": priority,
        }
        return item, True

    # 룰 폴백 — 점수 임계치 기반
    basis = (
        f"백테스트 데이터 누적 중 — Overheat Score {score:.0f} 기준 룰 권고"
    )
    if score >= 65:
        action = "과열 구간 — 신규 레버리지·베타 확대를 보류하십시오."
        priority = "high" if score >= 80 else "medium"
    elif score < 50:
        action = "과열 부담이 낮습니다 — 단계적 베타 확대 여지가 있습니다."
        priority = "low"
    else:
        action = "중립 구간 — 현 비중을 유지하며 과열 추이를 관찰하십시오."
        priority = "low"
    item = {
        "title": "과열 대응",
        "action": action,
        "basis": basis,
        "priority": priority,
    }
    return item, False


def _material_leveraged_winners(
    portfolio_holdings: list[dict] | None,
) -> list[dict]:
    """포트폴리오에서 익절 대응 트리거 조건을 만족하는 포지션 목록.

    소유자의 De minimis materiality 원칙 — 모든 조건 AND:
    - leverage == True
    - value_krw ≥ ₩5,000,000 (포지션 규모)
    - 미실현 수익 ≥ ₩2,000,000 (절대 금액 — value_krw - cost_krw 또는 pnl_krw)
    - return_pct ≥ +15% (의미있는 % 수익 — "winner" 라 부를 만한 수준)

    +4% 정도의 미미한 % 수익은 절대 금액이 ₩2M 넘어도 "익절할 winner" 라 부르기
    무리. % 임계까지 합쳐 "진짜로 보호할 만한 큰 수익이 난 레버리지 포지션" 만
    트리거하도록 한다.

    holdings 가 None/비어있으면 빈 리스트. dict 가 아니면 무시.
    """
    if not portfolio_holdings:
        return []
    out: list[dict] = []
    for h in portfolio_holdings:
        if not isinstance(h, dict):
            continue
        if not h.get("leverage"):
            continue
        value = _to_float(h.get("value_krw"))
        if value is None or value < _PROFIT_ITEM_MIN_VALUE_KRW:
            continue
        # 미실현 수익 — pnl_krw 우선, 없으면 value - cost
        gain = _to_float(h.get("pnl_krw"))
        if gain is None:
            cost = _to_float(h.get("cost_krw"))
            if value is not None and cost is not None:
                gain = value - cost
        if gain is None or gain < _PROFIT_ITEM_MIN_GAIN_KRW:
            continue
        # % 수익률도 의미있는 수준이어야 — +4% 짜리는 "winner" 라 부르기 무리
        ret_pct = _to_float(h.get("return_pct"))
        if ret_pct is None or ret_pct < _PROFIT_ITEM_MIN_RETURN_PCT:
            continue
        out.append(h)
    return out


def _format_position_name(h: dict) -> str:
    """포지션 한 줄 인용용 — '이름(₩63M, +4% / +₩2.4M)' 같은 형식."""
    name = h.get("name") or h.get("ticker") or "포지션"
    value = _to_float(h.get("value_krw"))
    pnl = _to_float(h.get("pnl_krw"))
    if pnl is None:
        cost = _to_float(h.get("cost_krw"))
        if value is not None and cost is not None:
            pnl = value - cost
    ret_pct = _to_float(h.get("return_pct"))
    bits: list[str] = []
    if value is not None:
        bits.append(f"₩{value/1_000_000:.0f}M")
    if ret_pct is not None:
        bits.append(f"{ret_pct:+.0f}%")
    if pnl is not None:
        bits.append(f"+₩{pnl/1_000_000:.1f}M")
    suffix = f"({', '.join(bits)})" if bits else ""
    return f"{name}{suffix}"


def _build_profit_item(
    score: float | None, bt_summary: dict,
    portfolio_holdings: list[dict] | None = None,
) -> tuple[dict | None, bool]:
    """Item C — 익절 대응.

    score>=60 + profit_protection 백테스트가 MDD 를 개선하고,
    포트폴리오에 materiality 임계를 충족하는 레버리지 수익 포지션이
    *최소 1개* 존재할 때만 발동한다 (소유자의 De minimis 원칙).
    조건 어느 하나라도 미달이면 None — 일반론적 노이즈 알림 금지.
    """
    if score is None or score < 60.0:
        return None, False

    pp = (bt_summary or {}).get("profit_protection") or {}
    with_rule = pp.get("with_rule") or {}
    without_rule = pp.get("without_rule") or {}
    w_mdd = _to_float(with_rule.get("max_drawdown"))
    n_mdd = _to_float(without_rule.get("max_drawdown"))

    if w_mdd is None or n_mdd is None:
        # 데이터 없음 — 항목 자체를 만들지 않는다 (날조 금지)
        return None, False

    # max_drawdown 음수 — 더 큰 값(덜 음수)이 좋다
    if w_mdd <= n_mdd:
        # 익절 룰이 MDD 를 개선하지 못함 — 항목 생략
        return None, False

    # 소유자 materiality — 트리거 포지션이 없으면 무음
    triggers = _material_leveraged_winners(portfolio_holdings)
    if not triggers:
        return None, False

    diff_pp = abs(w_mdd - n_mdd) * 100.0
    # 가장 큰 포지션 기준 명명 — 최대 2개까지 인용
    triggers.sort(key=lambda h: _to_float(h.get("value_krw")) or 0.0,
                  reverse=True)
    names = " · ".join(_format_position_name(h) for h in triggers[:2])
    more = "" if len(triggers) <= 2 else f" 외 {len(triggers)-2}건"
    item = {
        "title": "익절 대응",
        "action": (
            f"{names}{more}이(가) 과열 구간 — 일부 익절(QLD→QQQ 식 비중 축소) "
            "검토."
        ),
        "basis": (
            f"과거 검증: 과열(85+) 시 QLD→QQQ 익절 룰이 MDD를 "
            f"{diff_pp:.0f}%p 축소 | 트리거: 레버리지 보유 ≥ ₩5M & 수익 ≥ ₩2M"
        ),
        "priority": "medium",
    }
    return item, True


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def build_backtest_solution(
    regime_row: Any, crash_row: Any, bt_summary: dict | None,
    cycle: dict | None = None,
    cycle_recommendation: dict | None = None,
    portfolio_holdings: list[dict] | None = None,
) -> dict[str, Any]:
    """백테스트 결과를 소화해 '오늘의 대응' 처방을 만든다.

    Args:
        regime_row: market_regime 최신 행 (sqlite3.Row/dict/None).
        crash_row: crash_deployment_plan 최신 행 (sqlite3.Row/dict/None).
        bt_summary: generate_backtest_summary(conn) 결과 dict.
        cycle: market_cycle_analyzer.locate_current_market(conn) 결과 dict
            또는 None. None 이면 종전 동작과 완전히 동일하게 작동한다
            (하위 호환). drawdown_pct·similar_forward_* 는 분수 단위다.
        cycle_recommendation: market_cycle_analyzer.recommend_current_entry
            결과 dict. 데이터 사다리 기반 추천 — 있으면 하드코딩 _DEPLOY_LADDER
            대신 사용한다 (헤드라인은 recommendation 의 verdict).
        portfolio_holdings: portfolio.json 의 holdings 리스트 (또는 None).
            Item C(익절 대응) 의 materiality 게이트 — 레버리지 ≥₩5M & 수익
            ≥₩2M 포지션이 있을 때만 Item C 가 발동.

    Returns:
        {available, data_mode, headline, items, caveat, cycle_position}
        - available: regime_row·crash_row 가 모두 None 이면 False.
        - data_mode: 실제 백테스트/데이터 사다리 수치를 인용한 항목이 1개
          이상이면 "backtest", 아니면 "rule_fallback".
        - items: priority 순(high→medium→low) 정렬.
        - cycle_position: cycle["verdict_ko"] 또는 "" — 시장이 과거 어느
          구간에 있는지를 말하는 한 줄.
    절대 예외를 던지지 않는다.
    """
    try:
        bt_summary = bt_summary or {}
        available = not (regime_row is None and crash_row is None)

        dd = _to_float(_rget(crash_row, "qqq_drawdown_from_high"))
        score = _to_float(_rget(regime_row, "market_overheat_score"))

        # cycle 의 drawdown_pct(분수)를 crash_row 낙폭의 폴백으로 사용
        if dd is None and isinstance(cycle, dict):
            dd = _to_float(cycle.get("drawdown_pct"))

        items: list[dict] = []
        cited_any = False

        # Item A — 낙폭 대응 (낙폭 깊이 + 추세 상태 + 크레딧 스트레스 합성)
        try:
            item_a, cited_a = _build_drawdown_item(
                dd, bt_summary, cycle=cycle, crash_row=crash_row,
                cycle_recommendation=cycle_recommendation)
            if item_a:
                items.append(item_a)
                cited_any = cited_any or cited_a
        except Exception as e:
            log.debug("Item A 생성 실패: %s", e)

        # Item A' — 상승장 대응 (본격 배치 구간이 아닐 때 Hold/Don't Chase)
        try:
            item_up, cited_up = _build_uptrend_item(dd, score, cycle)
            if item_up:
                items.append(item_up)
                cited_any = cited_any or cited_up
        except Exception as e:
            log.debug("Item A' 생성 실패: %s", e)

        # Item B — 과열 대응
        try:
            item_b, cited_b = _build_overheat_item(score, bt_summary)
            if item_b:
                items.append(item_b)
                cited_any = cited_any or cited_b
        except Exception as e:
            log.debug("Item B 생성 실패: %s", e)

        # Item C — 익절 대응 (materiality 게이트: 레버리지 ≥₩5M & 수익 ≥₩2M)
        try:
            item_c, cited_c = _build_profit_item(
                score, bt_summary, portfolio_holdings=portfolio_holdings)
            if item_c:
                items.append(item_c)
                cited_any = cited_any or cited_c
        except Exception as e:
            log.debug("Item C 생성 실패: %s", e)

        # priority 정렬
        items.sort(key=lambda it: _PRIORITY_RANK.get(it.get("priority"), 9))

        # headline — 데이터 사다리 verdict 가 있으면 그것이 헤드라인의 핵심
        rec_verdict = ""
        if isinstance(cycle_recommendation, dict):
            rec_verdict = (cycle_recommendation.get("verdict") or "").strip()
        headline = _build_headline(dd, items, verdict=rec_verdict)

        data_mode = "backtest" if cited_any else "rule_fallback"

        # 시장 사이클 위치 — Stage A 의 verdict_ko 그대로
        cycle_position = ""
        if isinstance(cycle, dict):
            cycle_position = (cycle.get("verdict_ko") or "").strip()

        return {
            "available": available,
            "data_mode": data_mode,
            "headline": headline,
            "items": items,
            "caveat": CAVEAT,
            "cycle_position": cycle_position,
        }
    except Exception as e:
        log.warning("build_backtest_solution 실패 — 빈 결과 반환: %s", e)
        return {
            "available": False,
            "data_mode": "rule_fallback",
            "headline": "오늘 백테스트 기반 특이 대응 없음 — 현 구조 유지",
            "items": [],
            "caveat": CAVEAT,
            "cycle_position": "",
        }


def _build_headline(
    dd: float | None, items: list[dict], verdict: str = "",
) -> str:
    """최우선 항목과 낙폭 컨텍스트로 한 줄 헤드라인을 만든다.

    verdict 가 비어있지 않으면 (recommend_current_entry 의 verdict — 예:
    "TQQQ 진입 적기 (데이터상 정점)", "고점권 — 추격 매수 데이터적 가치 낮음")
    그 verdict 를 헤드라인의 핵심으로 사용한다.
    """
    if not items and not verdict:
        return "오늘 백테스트 기반 특이 대응 없음 — 현 구조 유지"

    # dd 정규화 (분수면 ×100)
    norm_dd_pre = dd
    if norm_dd_pre is not None and abs(norm_dd_pre) <= 1.0:
        norm_dd_pre = norm_dd_pre * 100.0

    # verdict 가 있고 의미있는 라벨이면 그것이 헤드라인의 핵심
    if verdict and verdict not in ("데이터 누적 중", "중립 — 데이터 평이"):
        # verdict 가 이미 "고점권"/"진입" 같은 시장 위치 표현을 포함하면 prefix
        # 중복 방지 — 깔끔하게 verdict 만 사용 (낙폭 수치만 곁들임).
        verdict_has_context = any(
            k in verdict for k in ("고점", "진입", "구간", "추격", "분할")
        )
        if verdict_has_context and norm_dd_pre is not None:
            return f"나스닥 {norm_dd_pre:+.1f}% · {verdict}"
        if norm_dd_pre is not None and norm_dd_pre <= -5.0:
            return f"나스닥 고점 대비 {norm_dd_pre:.0f}% — {verdict}"
        if norm_dd_pre is not None:
            return f"나스닥 고점권({norm_dd_pre:+.1f}%) — {verdict}"
        return verdict

    if not items:
        return "오늘 백테스트 기반 특이 대응 없음 — 현 구조 유지"

    top = items[0]
    summary = (top.get("action") or top.get("title") or "").strip()
    # 너무 길면 첫 문장만 — 소수점(-12.0%)이 아닌 '문장 끝 마침표' 에서만 절단
    for sep in (". ", ".\n"):
        if sep in summary:
            summary = summary.split(sep)[0].strip()
            break
    # 헤드라인 접두사가 이미 낙폭/고점 컨텍스트를 표시하므로, summary 앞쪽의
    # 중복 고점 문구 제거. 예: "QQQ 고점 대비 -12.0% — 단계매수 2단계 구간"
    # → "단계매수 2단계 구간", "나스닥이 고점 부근입니다 — 분할매수 단계 아님"
    # → "분할매수 단계 아님"
    if "고점" in summary and "— " in summary:
        head, _, rest = summary.partition("— ")
        if "고점" in head and rest.strip():
            summary = rest.strip()
    if len(summary) > 60:
        summary = summary[:60].rstrip() + "…"

    # dd 정규화 (분수면 ×100)
    norm_dd = dd
    if norm_dd is not None and abs(norm_dd) <= 1.0:
        norm_dd = norm_dd * 100.0

    if norm_dd is not None and norm_dd <= -5.0:
        return f"나스닥 고점 대비 {norm_dd:.0f}% — {summary}"
    if norm_dd is not None:
        return f"나스닥 고점권 — {summary}"
    return summary
