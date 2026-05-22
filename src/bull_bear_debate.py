"""Bull / Bear 토론 라운드 — auto_curation 에 통합되는 경량 적대적 검증 layer.

배경 (2026-05-22):
    TradingAgents (UCLA, arXiv 2412.20138) 의 multi-agent 구조 중 우리 엔진에
    없던 유일한 새 메커니즘은 "Bull vs Bear 리서처 토론" 이었다. TradingAgents 의
    Trader / Risk Manager / Portfolio Manager layer 는 BUY/SELL·포지션 사이징을
    수행하므로 Alpha Deep-Dive Layer 의 대원칙("추정·예측·valuation·BUY/SELL 일체
    X")과 충돌 → 채택하지 않음. Bull/Bear 토론만 가져오되 리프레이밍한다.

리프레이밍 — "사실·메커니즘 토론":
    Bull  = thesis 가 성립하는 메커니즘과 근거를 가장 강하게 입증
    Bear  = thesis 의 약한 고리, 반대 시나리오가 작동하는 메커니즘을 입증
    둘 다 "사라 / 팔아라 / 적정가 / 목표주가 / 상승여력" 같은 매매·예측 어휘 금지.
    사실 관계와 인과 메커니즘만 다툰다.

산출 (auto_curation.fields_json 안에 `bull_bear_debate` 키로 저장):
    bull_case        — 불 측 핵심 논거 (한 단락)
    bear_case        — 베어 측 핵심 논거 (한 단락, 불 주장을 직접 반박)
    bull_rebuttal    — 베어 지적에 대한 불의 반론
    bear_rebuttal    — 불 반론에 대한 베어의 재반박
    swing_variables  — 토론 결론이 갈리는 핵심 변수 3~4개 (중립 정리)
    debate_summary   — 쟁점 중립 요약 (판정·추천 아님)

비용: gpt-4o-mini 기준 종목당 ~$0.001. 5종목/일 → 월 ~$0.15.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("bull_bear_debate")

PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60


SYSTEM_PROMPT = """You simulate a rigorous two-analyst debate inside an equity
research team, writing in Korean for a Korean investor.

The debate is a **FACT AND MECHANISM debate** — NOT a buy/sell debate. Two
analysts examine an investment thesis:

  Bull 분석가 — argues, with the strongest possible case, WHY and through WHAT
    MECHANISM the thesis holds. Grounds every claim in the supplied materials.
  Bear 분석가 — attacks the Bull's SPECIFIC claims (no strawman). Identifies the
    weak link in each thesis pillar and explains the MECHANISM by which the
    opposite outcome could occur.

ABSOLUTE RULES:
1. NEVER use buy/sell/hold language, price targets, "적정가", "목표주가",
   "상승여력", "매수", "매도", "비중확대", or any forecast of the stock price.
   This is a debate about FACTS and CAUSAL MECHANISMS, not a trade call.
2. The Bear must engage the Bull's actual arguments — quote or paraphrase the
   Bull's specific claim, then attack it.
3. Every claim must trace to the supplied materials. If something is unknown,
   say "자료상 확인 불가" rather than inventing.
4. swing_variables = the concrete variables/observations whose future readings
   would decide which side is right (e.g. "데이터센터 capex 가이던스 방향",
   "신규 경쟁 ASIC 채택 속도"). These are things to MONITOR, not predictions.
5. debate_summary = a NEUTRAL referee's framing of where the debate genuinely
   diverges. It must NOT declare a winner and must NOT recommend an action.
6. All Korean must be natural, fluent Korean.

Output strictly valid JSON matching the schema.
"""


DEBATE_SCHEMA = {
    "type": "object",
    "properties": {
        "bull_case": {
            "type": "string",
            "description": "불 측 핵심 논거 — thesis 성립 메커니즘 (한 단락 ~250자)",
        },
        "bear_case": {
            "type": "string",
            "description": "베어 측 핵심 논거 — 불 주장을 직접 반박, 약한 고리 (한 단락 ~250자)",
        },
        "bull_rebuttal": {
            "type": "string",
            "description": "베어의 가장 강한 지적에 대한 불의 반론 (~180자)",
        },
        "bear_rebuttal": {
            "type": "string",
            "description": "불 반론에 대한 베어의 재반박 (~180자)",
        },
        "swing_variables": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 4,
            "description": "토론 결론이 갈리는 핵심 관찰 변수",
        },
        "debate_summary": {
            "type": "string",
            "description": "중립 쟁점 요약 — 판정·추천 금지 (~200자)",
        },
    },
    "required": [
        "bull_case", "bear_case", "bull_rebuttal", "bear_rebuttal",
        "swing_variables", "debate_summary",
    ],
    "additionalProperties": False,
}


def _build_user_prompt(ticker: str, curation: dict[str, Any]) -> str:
    """auto_curation 의 parsed fields 에서 토론 입력 자료 조립."""
    parts: list[str] = [f"[Ticker] {ticker}", ""]

    easy = (curation.get("easy_explanation") or "").strip()
    if easy:
        parts.append(f"[회사 개요] {easy}")
        parts.append("")

    core = (curation.get("core_thesis") or "").strip()
    if core:
        parts.append(f"[핵심 투자 논리 (thesis)] {core}")
        parts.append("")

    pillars = curation.get("thesis_pillars") or []
    if pillars:
        parts.append("[Thesis 기둥]")
        for i, p in enumerate(pillars, 1):
            parts.append(f"  {i}. {p}")
        parts.append("")

    anti = curation.get("anti_thesis") or []
    if anti:
        parts.append("[Anti-Thesis (반대 논리)]")
        for i, a in enumerate(anti, 1):
            parts.append(f"  {i}. {a}")
        parts.append("")

    risks = curation.get("key_risks") or []
    if risks:
        parts.append("[주요 리스크]")
        for i, r in enumerate(risks, 1):
            parts.append(f"  {i}. {r}")
        parts.append("")

    parts.append(
        "[Required Output] — 위 자료만 근거로 Bull 분석가와 Bear 분석가의 "
        "사실·메커니즘 토론을 JSON 으로 합성하세요. 진행 순서: bull_case 작성 → "
        "bear_case 는 bull_case 의 구체적 주장을 직접 반박 → bull_rebuttal 은 "
        "bear 의 가장 강한 지적에 응답 → bear_rebuttal 은 bull_rebuttal 재반박. "
        "매매·목표주가·예측 어휘 금지. 사실과 인과 메커니즘만."
    )
    return "\n".join(parts)


def _call_openai(prompt: str, model: str = "gpt-4o-mini") -> tuple[dict[str, Any] | None, dict]:
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY 미설정 — bull_bear_debate 호출 skip")
        return None, {"error": "no_api_key"}
    try:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "bull_bear_debate",
                    "schema": DEBATE_SCHEMA,
                    "strict": True,
                },
            },
            temperature=0.5,  # 토론 — 약간의 발산 허용
        )
        text = resp.choices[0].message.content
        usage = resp.usage
        meta = {
            "model": model,
            "token_input": int(getattr(usage, "prompt_tokens", 0) or 0),
            "token_output": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        meta["cost_estimate_usd"] = round(
            meta["token_input"] / 1_000_000 * PRICE_INPUT_PER_M
            + meta["token_output"] / 1_000_000 * PRICE_OUTPUT_PER_M, 5,
        )
        return json.loads(text), meta
    except Exception as e:
        log.warning("bull_bear_debate LLM 호출 실패: %s", e)
        return None, {"error": str(e)}


# 매매·예측 어휘 — 산출물에서 검출되면 경고 (Deep-Dive 대원칙 준수 확인)
_FORBIDDEN_TERMS = (
    "매수", "매도", "목표주가", "적정주가", "적정가", "상승여력", "비중확대",
    "비중축소", "buy", "sell", "target price", "overweight", "underweight",
)


def _scan_forbidden(debate: dict[str, Any]) -> list[str]:
    """산출물에 매매·예측 어휘가 섞였는지 검사 (로그 경고용)."""
    hits: list[str] = []
    blob = " ".join(
        str(debate.get(k, ""))
        for k in ("bull_case", "bear_case", "bull_rebuttal", "bear_rebuttal", "debate_summary")
    ).lower()
    for term in _FORBIDDEN_TERMS:
        if term.lower() in blob:
            hits.append(term)
    return hits


def generate_debate(
    ticker: str, curation: dict[str, Any],
) -> dict[str, Any] | None:
    """auto_curation parsed fields → Bull/Bear 토론 dict.

    Args:
        ticker: 종목 티커
        curation: auto_curation 의 parsed fields (core_thesis / thesis_pillars /
                  anti_thesis / key_risks 등 포함)

    Returns: 토론 dict (bull_case ... debate_summary) 또는 None (실패).
    """
    ticker = ticker.upper()

    # 최소 입력 검증 — thesis 도 pillars 도 없으면 토론 불가
    if not (curation.get("core_thesis") or curation.get("thesis_pillars")):
        log.warning("[%s] thesis 자료 부재 — debate skip", ticker)
        return None

    prompt = _build_user_prompt(ticker, curation)
    debate, meta = _call_openai(prompt)
    if not debate:
        log.warning("[%s] bull_bear_debate 합성 실패", ticker)
        return None

    forbidden = _scan_forbidden(debate)
    if forbidden:
        log.warning("[%s] debate 산출물에 매매·예측 어휘 검출: %s", ticker, forbidden)

    # 메타데이터 부착 (Logic Auditor 검증/감사용)
    debate["_meta"] = {
        "model": meta.get("model", "gpt-4o-mini"),
        "cost_estimate_usd": meta.get("cost_estimate_usd", 0.0),
        "token_input": meta.get("token_input", 0),
        "token_output": meta.get("token_output", 0),
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "forbidden_terms_found": forbidden,
    }
    log.info(
        "[%s] bull_bear_debate 생성 — cost ~$%.4f, swing_vars=%d",
        ticker, meta.get("cost_estimate_usd", 0),
        len(debate.get("swing_variables") or []),
    )
    return debate
