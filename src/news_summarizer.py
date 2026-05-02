"""뉴스 한국어 상세 요약기.

뉴스 카드/뉴스 상세 페이지에 표시되는 한국어 리서치 메모를 생성한다.

우선순위:
1) 환경변수 ANTHROPIC_API_KEY 가 있으면 Claude 호출 (해커톤 / 향후 LLM 연동)
2) 환경변수 OPENAI_API_KEY 가 있으면 OpenAI 호출
3) 둘 다 없으면 rule-based 폴백 — 제목 + summary 를 한국어로 풀어쓴 메모

폴백 메모는 5문장 이상 보장 + thesis_impact / confidence 자동 분류.

본문 접근이 안 되는 경우(Google News RSS 는 보통 제목+짧은 snippet 만 제공)
"본문 확인 필요" 표시 + confidence Low.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .event_processor import (
    classify_event_status,
    compute_staleness,
    is_urgent_risk,
    source_quality_from_name,
    thesis_impact_from,
)
from .universe import theme_label_ko
from .utils import display_name, get_logger

log = get_logger("news_summarizer")


# ---------------------------------------------------------------------------
# 영어 키워드 → 한국어 풀어쓰기 사전 (rule-based 폴백 보강)
# ---------------------------------------------------------------------------

_EN_TO_KO_PHRASE: dict[str, str] = {
    "acquires": "인수합니다",
    "acquired": "인수했습니다",
    "acquisition": "인수",
    "merges with": "합병합니다",
    "merger": "합병",
    "buyout": "지분 인수",
    "bid for": "인수 제안",
    "bids for": "에 대한 인수 제안",
    "walks away": "추진을 중단합니다",
    "drops bid": "인수 제안을 철회합니다",
    "completes": "마무리합니다",
    "deal closed": "거래를 종료했습니다",
    "regulatory approval": "규제 승인",
    "earnings beat": "실적이 컨센서스를 상회",
    "earnings miss": "실적이 컨센서스를 하회",
    "raises guidance": "가이던스를 상향",
    "cuts guidance": "가이던스를 하향",
    "lowers guidance": "가이던스를 하향",
    "downgrade": "투자의견 하향",
    "upgrade": "투자의견 상향",
    "lawsuit": "소송",
    "investigation": "조사",
    "subpoena": "소환장",
    "fraud": "회계 부정 의혹",
    "bankruptcy": "파산",
    "going concern": "계속기업 가능성에 대한 의문",
    "stock plunges": "주가가 급락",
    "stock surges": "주가가 급등",
    "soars": "급등",
    "tumbles": "급락",
    "all-time high": "사상 최고치",
    "record high": "사상 최고치",
}


def _humanize_english(text: str) -> str:
    """영어 문장에서 자주 나오는 표현을 한국어로 치환 (의미 보존 수준)."""
    s = text
    for en, ko in _EN_TO_KO_PHRASE.items():
        s = re.sub(re.escape(en), ko, s, flags=re.IGNORECASE)
    return s


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------

def summarize_news_to_korean(
    news_item: dict[str, Any],
    stock_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """뉴스 항목을 한국어 리서치 메모로 변환.

    Returns:
    {
        "detailed_summary_ko": "...",
        "key_points_ko": [...],
        "investment_implication_ko": "...",
        "thesis_impact_ko": "Thesis 강화 / Thesis 약화 / 신규 리스크 / 단기 노이즈 / 리스크 해소 / 확인 필요",
        "confidence_level_ko": "High / Medium / Low",
        "body_excerpt": "...",  # 가져올 수 있으면 원문 발췌
    }

    LLM 환경변수가 있으면 LLM 호출, 없으면 rule-based 폴백.
    """
    # LLM 사용 가능 여부 (향후 연동) — 현재는 rule-based 만 활성화
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _summarize_with_anthropic(news_item, stock_context)
        except Exception as e:
            log.warning("Anthropic 요약 실패 → 폴백: %s", e)
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _summarize_with_openai(news_item, stock_context)
        except Exception as e:
            log.warning("OpenAI 요약 실패 → 폴백: %s", e)
    return _summarize_rule_based(news_item, stock_context)


# ---------------------------------------------------------------------------
# Rule-based 폴백
# ---------------------------------------------------------------------------

def _summarize_rule_based(
    news: dict[str, Any], stock: dict[str, Any] | None
) -> dict[str, Any]:
    title = (news.get("title") or "").strip()
    summary = (news.get("summary") or "").strip()
    source = (news.get("source") or "").strip()
    published = (news.get("published_at") or "").strip()
    link = news.get("link") or ""
    ticker = news.get("ticker") or (stock or {}).get("ticker") or ""
    name_ko = (stock or {}).get("name_ko") or ""
    theme = (stock or {}).get("theme") or ""

    has_body = len(summary) >= 50  # 본문이 충분한지
    name_disp = display_name(name_ko, ticker) if ticker else "해당 종목"
    theme_label = theme_label_ko(theme) if theme else "관련 카테고리"

    # status / urgent / staleness 추론
    status = classify_event_status(f"{title} {summary}")
    urgent = is_urgent_risk(f"{title} {summary}")
    staleness = compute_staleness(published)
    src_q = source_quality_from_name(source)

    # importance score 추정 (간이)
    score = 0.0
    pos = ["beat", "raises", "record", "approval", "wins", "expands", "strong",
           "상향", "사상 최고", "수주", "수주 확보", "확장"]
    neg = ["miss", "cuts", "lowers", "downgrade", "lawsuit", "fraud", "investigation",
           "bankruptcy", "halt", "recall", "하향", "조사", "소송", "회계", "리콜"]
    s_lower = (title + " " + summary).lower()
    score += sum(0.7 for k in pos if k.lower() in s_lower)
    score -= sum(0.7 for k in neg if k.lower() in s_lower)

    # thesis_impact
    impact = thesis_impact_from(status, score, urgent, staleness)

    # confidence
    if not has_body:
        confidence = "Low"
    elif src_q == "High" and staleness in ("fresh", "aging"):
        confidence = "High"
    elif src_q == "High" or staleness in ("fresh", "aging"):
        confidence = "Medium"
    else:
        confidence = "Low"

    # 상세 요약 (본문 있을 때 vs 없을 때)
    if has_body:
        humanized = _humanize_english(summary)
        sentences = []
        sentences.append(
            f"해당 기사는 {name_disp} 관련 보도로, {published or '최근'} {source or '주요 매체'}를 통해 전해졌습니다."
        )
        # 제목 기반 핵심
        sentences.append(
            f"기사 제목은 \"{title}\" 이며, 보도된 핵심 내용은 다음과 같습니다."
        )
        # snippet 한국어 풀어쓰기
        if humanized:
            sentences.append(humanized[:400] + ("…" if len(humanized) > 400 else ""))
        # 카테고리 / 투자 영향
        sentences.append(
            f"{theme_label} 카테고리 관점에서 이 보도는 {_impact_to_phrase(impact)} 신호로 해석될 여지가 있습니다."
        )
        sentences.append(
            "다만 본 메모는 기사 제목과 짧은 발췌 기반의 자동 요약이므로, "
            "정밀 검토를 위해서는 원문 기사와 회사 IR / 공시를 함께 확인할 필요가 있습니다."
        )
        detailed = " ".join(sentences)
    else:
        detailed = (
            f"해당 기사는 {name_disp} 관련 보도로, {source or '출처 확인 필요'} / "
            f"{published or '날짜 확인 필요'} 자료입니다. 본문 발췌가 충분하지 않아 "
            "기사 전반의 맥락을 자동 요약하기 어렵습니다. 본문 확인 필요 — "
            "원문을 통해 다음을 직접 점검하시기 바랍니다: 보도된 사건의 핵심 사실, "
            "관련 이해관계자, 회사 / 산업에 대한 영향 강도, 후속 catalyst 또는 리스크. "
            f"분류상 이 보도는 {_impact_to_phrase(impact)} 신호로 잠정 분류됩니다."
        )

    # 핵심 포인트 (제목 / 출처 / 분류)
    key_points = [
        f"보도 매체: {source or '출처 확인 필요'} · 발행일: {published or '날짜 확인 필요'}",
        f"이벤트 상태(자동 분류): {status}",
        f"Thesis 영향(자동 분류): {impact}",
    ]
    if not has_body:
        key_points.append("본문 확인 필요 — 자동 요약은 제한적")

    # 투자적 의미
    implication = _build_implication(impact, status, name_disp, has_body)

    return {
        "detailed_summary_ko": detailed,
        "key_points_ko": key_points,
        "investment_implication_ko": implication,
        "thesis_impact_ko": impact,
        "confidence_level_ko": confidence,
        "body_excerpt": summary if has_body else None,
    }


def _impact_to_phrase(impact: str) -> str:
    return {
        "Thesis 강화": "기존 투자 thesis 를 강화할 수 있는",
        "Thesis 약화": "기존 투자 thesis 를 약화시킬 수 있는",
        "리스크 해소": "기존 단기 리스크가 해소되는",
        "신규 리스크": "새로운 리스크가 부각되는",
        "단기 노이즈": "단기 sentiment 에 영향을 줄 수 있는",
        "확인 필요": "추가 정밀 검토가 필요한",
    }.get(impact, "추가 정밀 검토가 필요한")


def _build_implication(impact: str, status: str, name: str, has_body: bool) -> str:
    base = {
        "Thesis 강화": (
            f"이 보도는 {name} 의 기존 투자 thesis 를 강화할 수 있는 catalyst 로 해석될 여지가 있습니다. "
            "다만 단정에 앞서 매출/마진 영향 시점과 회사 가이던스 변화를 함께 확인할 필요가 있습니다."
        ),
        "Thesis 약화": (
            f"이 보도는 {name} 의 기존 thesis 의 일부가 약화될 수 있음을 시사합니다. "
            "구조적 훼손 여부와 단기 이벤트성 부담을 분리해 점검할 필요가 있습니다."
        ),
        "리스크 해소": (
            f"기존에 부각되던 단기 리스크가 해소되는 흐름으로, {name} 의 투자 판단 초점이 본업 지표로 "
            "이동할 가능성이 있습니다."
        ),
        "신규 리스크": (
            f"이 보도는 {name} 에 대한 신규 리스크 신호일 수 있어 anti-thesis 점검이 우선됩니다. "
            "출처 신뢰도와 후속 보도 확인이 필요합니다."
        ),
        "단기 노이즈": (
            f"투자 thesis 에 미치는 영향은 제한적이며, {name} 의 단기 sentiment 에만 영향을 줄 가능성이 있습니다."
        ),
        "확인 필요": (
            f"단정 가능한 단계가 아니며, 본문 기사 / IR / 추가 보도를 통한 사실 확인이 우선되어야 합니다."
        ),
    }.get(impact, "단정 판단보다 추가 정밀 검토가 우선되어야 합니다.")
    if not has_body:
        base += " (본문 발췌 부족 — 자동 분류의 신뢰도가 낮으므로 원문 확인 필수)"
    return base


# ---------------------------------------------------------------------------
# LLM 연동 자리 (스켈레톤)
# ---------------------------------------------------------------------------

_LLM_PROMPT = """다음은 미국 상장주식 관련 영어 뉴스입니다. 한국어 증권사 리서치 메모 톤으로 요약해주세요.

[뉴스 정보]
- 종목: {ticker_disp}
- 제목: {title}
- 출처: {source}
- 발행일: {published_at}
- 본문 발췌: {summary}

[요구사항]
1. 5~8문장의 detailed_summary_ko 작성 (배경, 핵심 사건, 이해관계자, 영향 등)
2. key_points_ko (3~5개 불릿)
3. investment_implication_ko 1~2문장
4. thesis_impact_ko: Thesis 강화 / Thesis 약화 / 리스크 해소 / 신규 리스크 / 단기 노이즈 / 확인 필요 중 하나
5. confidence_level_ko: High / Medium / Low

본문 발췌가 부족하면 "본문 확인 필요"를 명시하고 confidence_level_ko 를 Low 로.

[출력 형식 — JSON]
{{
  "detailed_summary_ko": "...",
  "key_points_ko": ["...", "..."],
  "investment_implication_ko": "...",
  "thesis_impact_ko": "...",
  "confidence_level_ko": "..."
}}
"""


def _summarize_with_anthropic(news, stock):
    """Claude API 호출 (anthropic SDK 필요)."""
    import anthropic
    client = anthropic.Anthropic()
    ticker = news.get("ticker", "")
    name_ko = (stock or {}).get("name_ko", "")
    prompt = _LLM_PROMPT.format(
        ticker_disp=display_name(name_ko, ticker) if ticker else "해당 종목",
        title=news.get("title", ""),
        source=news.get("source", ""),
        published_at=news.get("published_at", ""),
        summary=news.get("summary", "") or "(본문 없음)",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    import json as _json
    try:
        data = _json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception as e:
        log.warning("Anthropic 응답 JSON 파싱 실패: %s", e)
        return _summarize_rule_based(news, stock)
    data.setdefault("body_excerpt", news.get("summary"))
    return data


def _summarize_with_openai(news, stock):
    """OpenAI API 호출 (openai SDK 필요)."""
    import openai
    client = openai.OpenAI()
    ticker = news.get("ticker", "")
    name_ko = (stock or {}).get("name_ko", "")
    prompt = _LLM_PROMPT.format(
        ticker_disp=display_name(name_ko, ticker) if ticker else "해당 종목",
        title=news.get("title", ""),
        source=news.get("source", ""),
        published_at=news.get("published_at", ""),
        summary=news.get("summary", "") or "(본문 없음)",
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=900,
        response_format={"type": "json_object"},
    )
    import json as _json
    try:
        data = _json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning("OpenAI 응답 JSON 파싱 실패: %s", e)
        return _summarize_rule_based(news, stock)
    data.setdefault("body_excerpt", news.get("summary"))
    return data
