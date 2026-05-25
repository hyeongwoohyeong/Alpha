"""Buffett 식 기회 필터 — Portfolio Regime 시스템 (Phase 3).

원칙 (market_regime.py / marks_cycle.py 와 동일):
- Rule-based. LLM 없이 완전 동작. 외부 데이터 없어도 graceful.
- 입력(Overheat / regime)이 없으면 해당 질문은 '확인 필요'.
- 모든 함수는 예외를 던지지 않는다.

Buffett 식 사고: '지금 juicy 한 기회인가, 아니면 현금을 들고 기다리는 게
나은가' 를 규칙으로 판단한다. 6개 질문을 Overheat Score·regime 에서 도출.
점수가 높을수록 '지금이 매수하기 좋은 기회' (싸고 공포가 지배).
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("buffett_filter")

NEEDS_CHECK = "확인 필요"

# 6개 Buffett 식 질문 — answer score 0~100: 높을수록 '지금 행동하기 좋음(기회)'.
BUFFETT_QUESTIONS: list[dict[str, str]] = [
    {"key": "juicy_opportunity", "label": "지금 juicy 한 기회가 있는가",
     "ko": "현재 시장에 매력적인 가격의 기회가 실제로 존재하는가"},
    {"key": "cash_can_wait", "label": "현금을 들고 기다려도 되는가",
     "ko": "서두르지 않고 현금을 들고 기다리는 선택지가 유효한가"},
    {"key": "understandable", "label": "이해하는 사업인가",
     "ko": "투자 대상이 이해 가능한 사업 범위 안에 있는가"},
    {"key": "price_attractive", "label": "가격이 매력적인가",
     "ko": "지불하는 가격이 가치 대비 매력적인가"},
    {"key": "moat_durable", "label": "경쟁우위가 지속되는가",
     "ko": "보유/검토 대상의 경쟁우위(moat)가 견고하게 유지되는가"},
    {"key": "no_action_ok", "label": "지금 행동 안 해도 되는가",
     "ko": "아무것도 하지 않는 것이 합리적인 선택인가"},
]


def _avg(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _answer_scores(regime: dict[str, Any]) -> dict[str, float | None]:
    """Overheat sub-score / regime 으로부터 6개 질문 answer score 도출.

    answer score 가 높음 = '기회가 있고 지금 행동하기 좋음'.
    과열(overheat 높음)일수록 기회 점수는 낮아진다.
    """
    g = lambda k: regime.get(k)  # noqa: E731
    overheat = g("market_overheat_score")
    val = g("valuation_stretch_score")
    cre = g("liquidity_credit_score")
    tec = g("technical_extension_score")
    cur_regime = g("current_regime")
    dd = g("qqq_drawdown_from_high")  # 음수(낙폭) 또는 None

    # overheat 가 높을수록 기회 적음 → 반전 (inv)
    inv = (lambda x: None if x is None else (100.0 - x))

    out: dict[str, float | None] = {}
    # 1. juicy 한 기회 — 밸류에이션이 싸고 낙폭이 있으면 기회. overheat 반전 기반.
    parts1 = [inv(val), inv(overheat)]
    if dd is not None:
        # 낙폭 0%~-25% → 0~100 (깊을수록 기회)
        try:
            ddp = max(0.0, min(100.0, (-float(dd)) / 0.25 * 100.0))
            parts1.append(ddp)
        except (TypeError, ValueError):
            pass
    out["juicy_opportunity"] = _avg(parts1)
    # 2. 현금 들고 기다려도 되는가 — 과열일수록 '예'(기다리는 게 맞음 = 점수↑).
    #    여기서는 '기다리는 게 현명한가' 가 아니라 '지금 매수 기회인가' 척도로
    #    통일하기 위해, 과열이면 기회 낮음 → inv 사용.
    out["cash_can_wait"] = inv(overheat)
    # 3. 이해하는 사업인가 — 시장 데이터로 판단 불가 (투자자 본인 영역).
    out["understandable"] = None
    # 4. 가격 매력 — 밸류에이션 반전 (싸면 매력적).
    out["price_attractive"] = inv(val)
    # 5. 경쟁우위 지속 — 시장 데이터로 판단 불가 (종목별 deep-dive 영역).
    out["moat_durable"] = None
    # 6. 지금 행동 안 해도 되는가 — 과열·기술적 과열일수록 '안 해도 됨' = 점수↑.
    #    (기회 척도와 통일: 과열이면 신규행동 불필요 → inv 로 낮은 기회점수)
    out["no_action_ok"] = _avg([inv(overheat), inv(tec)])

    # regime 보정 — 디스로케이션/위기 후반엔 기회 가산
    if cur_regime in ("Dislocation",):
        for k in ("juicy_opportunity", "price_attractive"):
            if out.get(k) is not None:
                out[k] = min(100.0, out[k] + 12.0)
    return out


def _question_verdict(key: str, score: float) -> str:
    if score >= 65:
        return "예 — 기회 우위"
    if score >= 45:
        return "부분적 — 선별 검토"
    if score >= 25:
        return "대체로 아니오"
    return "아니오 — 인내 우위"


def evaluate_buffett_opportunity(regime: dict[str, Any]) -> dict[str, Any]:
    """Buffett 식 기회 판단.

    regime: build_market_regime() 출력.
    Returns dict:
      buffett_opportunity_score (0~100|None), opportunity_band (str),
      cash_optionality_comment (str), do_nothing_recommended (bool),
      checklist (list), missing (list[str]), commentary_ko (str).
    """
    regime = regime or {}
    answers = _answer_scores(regime)

    checklist: list[dict[str, Any]] = []
    available: list[float] = []
    missing: list[str] = []
    for q in BUFFETT_QUESTIONS:
        sc = answers.get(q["key"])
        if sc is None:
            missing.append(q["label"])
            note = ("투자자 본인의 판단 영역 — 시장 데이터로 산정 불가"
                    if q["key"] in ("understandable", "moat_durable")
                    else NEEDS_CHECK)
            checklist.append({**q, "score": None, "verdict": note})
            continue
        sc = max(0.0, min(100.0, float(sc)))
        available.append(sc)
        checklist.append({**q, "score": round(sc, 1),
                          "verdict": _question_verdict(q["key"], sc)})

    if available:
        opp = round(sum(available) / len(available), 1)
    else:
        opp = None

    band = _opportunity_band(opp)
    do_nothing = bool(opp is not None and opp < 40)
    cash_comment = _cash_optionality_comment(opp, regime)
    commentary = _build_commentary(opp, band, do_nothing, checklist, missing)

    return {
        "buffett_opportunity_score": opp,
        "opportunity_band": band,
        "cash_optionality_comment": cash_comment,
        "do_nothing_recommended": do_nothing,
        "checklist": checklist,
        "missing": missing,
        "commentary_ko": commentary,
    }


def _opportunity_band(opp: float | None) -> str:
    if opp is None:
        return NEEDS_CHECK
    if opp >= 70:
        return "기회 풍부 (Fat Pitch)"
    if opp >= 55:
        return "선별적 기회"
    if opp >= 40:
        return "기회 제한적"
    if opp >= 25:
        return "인내 구간"
    return "현금 우위 (Do Nothing)"


def _cash_optionality_comment(opp: float | None, regime: dict[str, Any]) -> str:
    if opp is None:
        return f"기회 점수 산정 불가 — {NEEDS_CHECK}. 현금 옵션은 중립으로 유지."
    if opp >= 70:
        return ("매력적인 가격의 기회가 보입니다. 보유 현금을 분할로 투입할 "
                "여지가 큽니다 — 현금을 '실탄' 으로 적극 활용할 국면.")
    if opp >= 55:
        return ("선별적 기회가 존재합니다. 현금 전부가 아니라 일부를 검증된 "
                "대상에 단계적으로 투입하는 접근이 유효합니다.")
    if opp >= 40:
        return ("기회가 제한적입니다. 현금 옵션 가치가 높아지는 구간으로, "
                "급하게 투입하기보다 여력을 남겨두는 편이 안전합니다.")
    return ("지금은 현금을 들고 기다리는 것 자체가 강력한 포지션입니다. "
            "무리한 매수보다 현금 옵션을 보존해 다음 기회를 대비하십시오.")


def _build_commentary(opp: float | None, band: str, do_nothing: bool,
                      checklist: list[dict], missing: list[str]) -> str:
    parts: list[str] = []
    if opp is None:
        parts.append(
            "Buffett 기회 점수를 산정할 데이터가 부족합니다 — "
            f"{NEEDS_CHECK}. Market Overheat 데이터 수집 후 재평가됩니다."
        )
        return " ".join(parts)

    lead = {
        "기회 풍부 (Fat Pitch)": "현재 시장은 Buffett 식 관점에서 기회가 풍부한 구간입니다. "
                              "가격이 가치 대비 매력적이며 적극적으로 행동할 만합니다.",
        "선별적 기회": "선별된 기회가 존재하는 구간입니다. 모든 것을 사기보다 "
                   "이해하는 사업·매력적 가격을 골라 행동하십시오.",
        "기회 제한적": "기회가 제한적인 구간입니다. 현금 옵션의 가치가 커지고 있어 "
                   "신규 행동은 신중해야 합니다.",
        "인내 구간": "인내가 우위인 구간입니다. 좋은 기회가 부족하므로 "
                  "행동하지 않는 것이 합리적입니다.",
        "현금 우위 (Do Nothing)": "현재는 'Do Nothing' 이 최선의 결정인 구간입니다. "
                              "시장이 비싸고 기회가 얇아 현금을 지키는 것이 우위입니다.",
    }
    parts.append(lead.get(band, "기회 국면을 평가 중입니다."))
    parts.append(f"Buffett 기회 점수 {opp:.0f}/100 ({band}).")
    if do_nothing:
        parts.append("→ 권고: 무리한 신규 매수보다 'Do Nothing' — 현금 옵션 보존이 우선입니다.")

    strong = sorted(
        [c for c in checklist if c.get("score") is not None and c["score"] >= 60],
        key=lambda c: c["score"], reverse=True,
    )[:2]
    if strong:
        labels = ", ".join(f"'{c['label']}'" for c in strong)
        parts.append(f"기회 쪽 신호가 강한 항목: {labels}.")

    judgement_only = [c["label"] for c in checklist
                      if c["key"] in ("understandable", "moat_durable")]
    if judgement_only:
        parts.append(
            f"※ {', '.join(judgement_only)} 은(는) 시장 데이터가 아닌 "
            "투자자 본인의 판단 영역입니다 — 종목 deep-dive 에서 직접 확인하십시오."
        )
    other_missing = [m for m in missing if m not in judgement_only]
    if other_missing:
        parts.append(f"※ 데이터 부족으로 제외된 질문: {', '.join(other_missing)}.")
    return " ".join(parts)
