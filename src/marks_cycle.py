"""Howard Marks 식 사이클 심리 체크리스트 — Portfolio Regime 시스템 (Phase 3).

원칙 (market_regime.py 와 동일):
- Rule-based. 점수·분류·코멘트 전부 규칙 기반. LLM 없이 완전 동작.
- 입력(Overheat sub-score)이 없으면 해당 질문은 '확인 필요' 로 처리하고
  점수 산정에서 제외, 나머지로 재정규화한다.
- 모든 함수는 예외를 던지지 않는다 (호출부에서 graceful).
- 하워드 막스 문장 직접 인용 금지 — 그의 '사이클 심리' 사고방식을 체크리스트화.

핵심 입력: market_regime.calculate_market_overheat_score() / build_market_regime()
출력 dict (`market_regime` 의 sub-score 들 — *_score, market_overheat_score 등).
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("marks_cycle")

NEEDS_CHECK = "확인 필요"

# 10개 사이클 심리 질문 — 각 질문은 '시장이 사이클의 어느 쪽에 있는가' 를 본다.
# answer score 0~100: 높을수록 '낙관·과열 쪽' (사이클 고점 심리).
CYCLE_QUESTIONS: list[dict[str, str]] = [
    {"key": "risk_ignored", "label": "리스크를 무시하고 있는가",
     "ko": "시장이 위험을 가볍게 보고 안도하고 있는가"},
    {"key": "story_over_price", "label": "스토리가 가격보다 우선되는가",
     "ko": "밸류에이션보다 성장 스토리가 매수 근거가 되고 있는가"},
    {"key": "this_time_different", "label": "'이번엔 다르다' 가 통하는가",
     "ko": "과거 잣대가 안 통한다는 인식이 퍼져 있는가"},
    {"key": "easy_credit", "label": "신용이 느슨한가",
     "ko": "자금 조달이 쉽고 신용 스프레드가 과도하게 좁은가"},
    {"key": "leverage_buying", "label": "레버리지 투자가 늘고 있는가",
     "ko": "레버리지 ETF·차입 매수가 활발한가"},
    {"key": "good_company_confusion", "label": "좋은 회사와 좋은 투자를 혼동하는가",
     "ko": "'좋은 기업이면 어떤 가격이든 좋다' 는 사고가 퍼져 있는가"},
    {"key": "aggression_praised", "label": "공격적 태도가 칭송받는가",
     "ko": "공격적으로 베팅한 쪽이 칭송받는 분위기인가"},
    {"key": "cash_mocked", "label": "현금 보유가 조롱받는가",
     "ko": "현금을 들고 있으면 뒤처진다는 압박이 있는가"},
    {"key": "fomo_over_fear", "label": "FOMO 가 손실 우려를 압도하는가",
     "ko": "기회를 놓칠 두려움이 손실 두려움보다 큰가"},
    {"key": "thin_reward", "label": "기대수익 대비 보상이 얇은가",
     "ko": "감수하는 위험 대비 기대수익(리스크 프리미엄)이 얇은가"},
]


def _band(score: float) -> str:
    if score < 30:
        return "비관/공포"
    if score < 45:
        return "신중"
    if score < 60:
        return "중립"
    if score < 75:
        return "낙관"
    if score < 88:
        return "과신"
    return "도취(Euphoria)"


def _avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _answer_scores(regime: dict[str, Any]) -> dict[str, float | None]:
    """Overheat sub-score 로부터 10개 질문 각각의 answer score(0~100|None) 도출.

    regime: build_market_regime() 또는 calculate_market_overheat_score() 출력.
    """
    g = lambda k: regime.get(k)  # noqa: E731
    val = g("valuation_stretch_score")
    sen = g("sentiment_speculation_score")
    con = g("market_concentration_score")
    cre = g("liquidity_credit_score")
    tec = g("technical_extension_score")
    overheat = g("market_overheat_score")

    out: dict[str, float | None] = {}
    # 1. 리스크 무시 — 신용 안도(cre) + 기술적 과열(tec)
    out["risk_ignored"] = _avg([cre, tec])
    # 2. 스토리 우선 — 밸류에이션 과열
    out["story_over_price"] = val
    # 3. '이번엔 다르다' — 밸류에이션 + 기술적 과열 함께 극단일 때
    out["this_time_different"] = _avg([val, tec])
    # 4. 신용 느슨 — 유동성/신용 sub-score 그대로
    out["easy_credit"] = cre
    # 5. 레버리지 투자 — 투기심리(레버리지 ETF 거래량)
    out["leverage_buying"] = sen
    # 6. 좋은회사 vs 좋은투자 혼동 — 밸류에이션 + 집중도(소수 대형주 쏠림)
    out["good_company_confusion"] = _avg([val, con])
    # 7. 공격 칭송 — 투기심리 + 기술적 과열
    out["aggression_praised"] = _avg([sen, tec])
    # 8. 현금 조롱 — 종합 overheat (시장 전체가 뜨거우면 현금이 조롱받음)
    out["cash_mocked"] = overheat
    # 9. FOMO > 손실우려 — 투기심리 + overheat
    out["fomo_over_fear"] = _avg([sen, overheat])
    # 10. 얇은 보상 — 밸류에이션 + 신용 (둘 다 높으면 리스크 프리미엄 얇음)
    out["thin_reward"] = _avg([val, cre])
    return out


def _question_verdict(score: float) -> str:
    """answer score → '예/경계/아니오' 류 한국어 판정."""
    if score >= 70:
        return "예 — 사이클 고점 심리"
    if score >= 50:
        return "부분적으로 — 경계 신호"
    if score >= 30:
        return "대체로 아니오"
    return "아니오 — 사이클 저점 쪽"


def evaluate_cycle_psychology(regime: dict[str, Any]) -> dict[str, Any]:
    """Howard Marks 식 사이클 심리 평가.

    regime: build_market_regime() 출력 (Overheat sub-score 포함).
    Returns dict:
      cycle_psychology_score (0~100|None), market_mood (str), risk_posture (str),
      checklist (list of {key,label,ko,score,verdict}), missing (list[str]),
      commentary_ko (str).
    """
    regime = regime or {}
    answers = _answer_scores(regime)

    checklist: list[dict[str, Any]] = []
    available: list[float] = []
    missing: list[str] = []
    for q in CYCLE_QUESTIONS:
        sc = answers.get(q["key"])
        if sc is None:
            missing.append(q["label"])
            checklist.append({**q, "score": None, "verdict": NEEDS_CHECK})
            continue
        sc = max(0.0, min(100.0, float(sc)))
        available.append(sc)
        checklist.append({**q, "score": round(sc, 1),
                          "verdict": _question_verdict(sc)})

    if available:
        psych = round(sum(available) / len(available), 1)
    else:
        psych = None

    mood = _band(psych) if psych is not None else NEEDS_CHECK

    # Risk Posture — 심리가 과열일수록 방어적 태세 권고
    if psych is None:
        posture = NEEDS_CHECK
    elif psych >= 75:
        posture = "방어 — 차익 보호·현금 옵션 우선"
    elif psych >= 60:
        posture = "신중 — 신규 베타 확대 자제"
    elif psych >= 45:
        posture = "중립 — 균형 유지"
    elif psych >= 30:
        posture = "선별적 위험선호 — 기회 탐색 가능"
    else:
        posture = "공격 — 비관 속 분할 매수 우위"

    commentary = _build_commentary(psych, mood, posture, checklist, missing)

    return {
        "cycle_psychology_score": psych,
        "market_mood": mood,
        "risk_posture": posture,
        "checklist": checklist,
        "missing": missing,
        "commentary_ko": commentary,
    }


def _build_commentary(psych: float | None, mood: str, posture: str,
                      checklist: list[dict], missing: list[str]) -> str:
    """rule-based 한국어 코멘트."""
    parts: list[str] = []
    if psych is None:
        parts.append(
            "사이클 심리 점수를 산정할 데이터가 부족합니다 — "
            f"{NEEDS_CHECK}. Market Overheat sub-score 수집 후 재평가됩니다."
        )
        return " ".join(parts)

    lead = {
        "비관/공포": "시장 심리는 사이클 저점 쪽 — 공포가 지배적입니다. "
                  "역사적으로 위험을 감수하기 유리한 구간입니다.",
        "신중": "시장 심리는 신중한 편입니다. 과열 신호는 약하며 선별적 접근이 유효합니다.",
        "중립": "시장 심리는 중립 구간입니다. 사이클의 어느 한쪽으로 치우치지 않았습니다.",
        "낙관": "시장 심리는 낙관 쪽으로 기울어 있습니다. 일부 과열 신호가 보이기 시작했습니다.",
        "과신": "시장 심리는 과신 구간입니다. 리스크 대비 보상이 얇아지고 있어 "
              "신규 베타 확대보다 차익 보호가 우선입니다.",
        "도취(Euphoria)": "시장 심리는 도취 구간입니다. 사이클 고점 심리가 광범위하게 "
                       "확인되며, 방어적 태세가 강하게 요구됩니다.",
    }
    parts.append(lead.get(mood, "시장 심리를 평가 중입니다."))
    parts.append(f"사이클 심리 점수 {psych:.0f}/100 ({mood}) · 권장 태세: {posture}.")

    # 가장 뜨거운 질문 1~3개를 근거로 제시
    hot = sorted(
        [c for c in checklist if c.get("score") is not None and c["score"] >= 65],
        key=lambda c: c["score"], reverse=True,
    )[:3]
    if hot:
        labels = ", ".join(f"'{c['label']}'" for c in hot)
        parts.append(f"특히 과열 신호가 강한 항목: {labels}.")
    cold = sorted(
        [c for c in checklist if c.get("score") is not None and c["score"] < 35],
        key=lambda c: c["score"],
    )[:2]
    if cold:
        labels = ", ".join(f"'{c['label']}'" for c in cold)
        parts.append(f"반대로 비관/저점 쪽 신호: {labels}.")

    if missing:
        parts.append(
            f"※ 데이터 부족으로 평가에서 제외된 질문: {', '.join(missing)} "
            "(나머지로 재정규화)."
        )
    return " ".join(parts)
