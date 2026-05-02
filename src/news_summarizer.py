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

from .curated import (
    simple_explanation as _curated_simple_explanation,
    thesis_pillars as _curated_thesis_pillars,
    company_type as _curated_company_type,
)
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

def _detect_news_topic(text: str) -> tuple[str, str]:
    """제목/본문에서 토픽을 한국어로 추론.

    return (topic_label_ko, headline_phrase_ko)
    """
    s = text.lower()
    # 가장 흔한 패턴들 — 한국어 의역
    if any(k in s for k in ["acquires", "acquire", "acquisition", "merger", "merges", "buyout", "deal"]):
        if any(k in s for k in ["walks away", "drops bid", "withdraws", "abandons", "ends pursuit"]):
            return ("M&A 철수/무산", "인수 추진을 중단했다는 보도")
        if any(k in s for k in ["completes", "completed", "closes the deal", "deal closed", "finalized"]):
            return ("M&A 종결", "거래를 마무리했다는 보도")
        return ("M&A", "인수합병 관련 보도")
    if any(k in s for k in ["earnings beat", "beats estimates", "beat consensus", "tops estimates"]):
        return ("실적", "실적이 컨센서스를 상회했다는 보도")
    if any(k in s for k in ["earnings miss", "misses estimates", "miss consensus", "falls short"]):
        return ("실적", "실적이 컨센서스를 하회했다는 보도")
    if any(k in s for k in ["raises guidance", "raised guidance", "guidance up", "raises outlook"]):
        return ("가이던스", "가이던스 상향 보도")
    if any(k in s for k in ["cuts guidance", "lowers guidance", "guidance down", "cuts outlook"]):
        return ("가이던스", "가이던스 하향 보도")
    if any(k in s for k in ["upgrade", "upgraded", "raised price target", "buy rating"]):
        return ("증권사 의견", "투자의견 상향 또는 목표주가 상향 보도")
    if any(k in s for k in ["downgrade", "downgraded", "cut price target", "sell rating"]):
        return ("증권사 의견", "투자의견 하향 또는 목표주가 하향 보도")
    if any(k in s for k in ["lawsuit", "sued", "legal action", "court", "settlement"]):
        return ("법적 이슈", "소송 또는 법적 분쟁 관련 보도")
    if any(k in s for k in ["investigation", "subpoena", "probe", "fraud", "accounting", "restatement"]):
        return ("규제/조사", "규제 또는 조사 관련 보도")
    if any(k in s for k in ["regulatory approval", "antitrust", "approved by", "fda approval", "doj"]):
        return ("규제 승인", "규제 / 당국 승인 관련 보도")
    if any(k in s for k in ["wins contract", "awarded", "secures order", "lands deal", "signed"]):
        return ("수주/계약", "수주 또는 신규 계약 보도")
    if any(k in s for k in ["launches", "launch", "unveils", "announces new", "new product", "release"]):
        return ("신제품", "신제품 또는 신규 서비스 발표")
    if any(k in s for k in ["buyback", "repurchase", "dividend"]):
        return ("주주환원", "자사주 매입 또는 배당 관련 보도")
    if any(k in s for k in ["ceo", "cfo", "resigns", "appointed", "step down", "departure"]):
        return ("경영진", "경영진 변화 보도")
    if any(k in s for k in ["all-time high", "record high", "soars", "surges", "rally", "rallies"]):
        return ("주가", "주가가 강세를 보였다는 보도")
    if any(k in s for k in ["plunges", "tumbles", "slumps", "drops", "falls"]):
        return ("주가", "주가가 약세를 보였다는 보도")
    if any(k in s for k in ["analyst", "rating", "price target", "outlook"]):
        return ("애널리스트 코멘트", "애널리스트 의견 / 전망 보도")
    if any(k in s for k in ["want to buy", "watching", "consider", "should you buy", "is it time"]):
        return ("종목 분석", "투자 매력도 점검 또는 종목 분석 칼럼")
    return ("일반 보도", "관련 보도")


def _extract_numbers(text: str) -> list[str]:
    """본문에서 핵심 숫자 추출 ($200 million, 35%, 12 contracts 등)."""
    if not text:
        return []
    pat = re.compile(
        r"\$?\d[\d,\.]*(?:\s*(?:million|billion|trillion|m|b|k|%|cents?))?",
        re.IGNORECASE,
    )
    raw = pat.findall(text)
    # 너무 짧은 것 제거 (예: "1", "5")
    return [n.strip() for n in raw if len(n.strip()) >= 2 and not n.strip().isdigit() or len(n.strip()) >= 3][:5]


def _is_title_redundant(title: str, summary: str) -> bool:
    """summary 가 title 거의 반복인지 (정보 가치 거의 없는 케이스)."""
    if not summary or not title:
        return True
    t = re.sub(r"[^\w\s]", "", title.lower()).split()
    s = re.sub(r"[^\w\s]", "", summary.lower()).split()
    if not t:
        return False
    overlap = len(set(t) & set(s))
    coverage = overlap / max(len(set(t)), 1)
    # title 단어 70% 이상이 summary에 그대로 → 정보 가치 낮음
    return coverage >= 0.7 and len(s) <= len(t) * 1.5


def _build_summary_one_liner(
    title: str,
    summary: str,
    topic_label: str,
    headline_phrase: str,
    numbers: list[str],
    has_real_body: bool,
) -> str:
    """기사의 핵심 rationale 1~2 문장. 영어 그대로 노출 X."""
    n_str = ", ".join(numbers[:2]) if numbers else ""
    if not has_real_body:
        return f"{topic_label} — {headline_phrase}."

    # 토픽별 한국어 템플릿
    if topic_label == "M&A":
        if n_str:
            return f"M&A 보도 — 약 {n_str} 규모로 거론되는 인수합병 움직임."
        return f"M&A 보도 — {headline_phrase}."
    if topic_label == "M&A 종결":
        if n_str:
            return f"M&A 종결 — 약 {n_str} 규모의 인수가 완료됐다는 보도."
        return f"M&A 종결 — 거래가 마무리됐다는 보도."
    if topic_label == "M&A 철수/무산":
        return f"M&A 철수/무산 — 인수 추진을 중단했다는 보도. 기존 인수 리스크는 해소되는 방향."
    if topic_label == "실적":
        if n_str:
            return f"실적 보도 — 핵심 숫자 {n_str}. {headline_phrase}."
        return f"실적 보도 — {headline_phrase}."
    if topic_label == "가이던스":
        if n_str:
            return f"가이던스 변경 — 관련 수치 {n_str}. {headline_phrase}."
        return f"가이던스 변경 — {headline_phrase}."
    if topic_label == "증권사 의견":
        return f"애널리스트 의견 변경 — {headline_phrase}."
    if topic_label == "법적 이슈":
        return f"법적 이슈 — {headline_phrase}. anti-thesis 점검 필요."
    if topic_label == "규제/조사":
        return f"규제/조사 보도 — {headline_phrase}. 신규 리스크 점검 필요."
    if topic_label == "규제 승인":
        return f"규제 승인 — {headline_phrase}. 진행 중이던 리스크 해소 가능성."
    if topic_label == "수주/계약":
        if n_str:
            return f"수주/계약 — 관련 규모 {n_str}. {headline_phrase}."
        return f"수주/계약 — {headline_phrase}."
    if topic_label == "신제품":
        return f"신제품/서비스 발표 — {headline_phrase}."
    if topic_label == "주주환원":
        if n_str:
            return f"주주환원 — 관련 규모 {n_str}. {headline_phrase}."
        return f"주주환원 — {headline_phrase}."
    if topic_label == "경영진":
        return f"경영진 변화 — {headline_phrase}."
    if topic_label == "주가":
        if n_str:
            return f"주가 움직임 — {headline_phrase} (관련 수치 {n_str})."
        return f"주가 움직임 — {headline_phrase}."
    if topic_label == "애널리스트 코멘트":
        return f"애널리스트 코멘트 — {headline_phrase}."
    if topic_label == "종목 분석":
        return f"종목 분석 칼럼 — {headline_phrase}. 매체의 의견성 콘텐츠."
    return f"{topic_label} — {headline_phrase}."


def _summarize_rule_based(
    news: dict[str, Any], stock: dict[str, Any] | None
) -> dict[str, Any]:
    """핵심 rationale 만 — 메타 문구·회사 소개·IR 안내·영어 본문 그대로 노출 모두 제거."""
    title = (news.get("title") or "").strip()
    summary = (news.get("summary") or "").strip()
    published = (news.get("published_at") or "").strip()
    source = (news.get("source") or "").strip()
    ticker = news.get("ticker") or (stock or {}).get("ticker") or ""

    # 본문 가치 판정 — 단순 길이가 아니라 title 과 비교
    title_redundant = _is_title_redundant(title, summary)
    has_real_body = len(summary) >= 50 and not title_redundant

    # 토픽 / 헤드라인
    topic_label, headline_phrase = _detect_news_topic(f"{title} {summary}")

    # 상태 / 영향
    status = classify_event_status(f"{title} {summary}")
    urgent = is_urgent_risk(f"{title} {summary}")
    staleness = compute_staleness(published)
    src_q = source_quality_from_name(source)

    score = 0.0
    pos_kw = ["beat", "beats", "raises", "raised", "record", "approval", "approved",
              "wins", "expands", "strong", "exceeds", "tops", "soars", "surges",
              "rally", "rallies", "buyback", "raised guidance",
              "상향", "사상 최고", "수주", "확장", "강세", "급등"]
    neg_kw = ["miss", "misses", "cuts", "cut", "lowers", "lowered", "downgrade",
              "downgraded", "lawsuit", "fraud", "investigation", "bankruptcy",
              "halt", "recall", "plunges", "tumbles", "slumps", "drops bid",
              "walks away", "withdraws", "cuts guidance",
              "하향", "조사", "소송", "회계", "리콜", "급락"]
    s_lower = (title + " " + summary).lower()
    score += sum(0.8 for k in pos_kw if k in s_lower)
    score -= sum(0.8 for k in neg_kw if k in s_lower)
    # 강한 시그널 — 같은 기사에 양수 키워드 2개 이상이면 강화
    if sum(1 for k in pos_kw if k in s_lower) >= 2:
        score += 0.5
    if sum(1 for k in neg_kw if k in s_lower) >= 2:
        score -= 0.5

    impact = thesis_impact_from(status, score, urgent, staleness)

    if not has_real_body:
        confidence = "Low"
    elif src_q == "High" and staleness in ("fresh", "aging"):
        confidence = "High"
    elif src_q == "High" or staleness in ("fresh", "aging"):
        confidence = "Medium"
    else:
        confidence = "Low"

    pillars = _curated_thesis_pillars(ticker) if ticker else []
    numbers = _extract_numbers(summary) if has_real_body else []

    # ── summary: 한 줄 한국어 ──────────────────────────────────────────
    summary_short = _build_summary_one_liner(
        title, summary, topic_label, headline_phrase, numbers, has_real_body
    )

    # ── key_points: 사용자가 모를만한 인사이트 (불릿 3~5개) ───────────────
    key_points: list[str] = []

    # 1. thesis pillar 연결 (가장 중요한 인사이트)
    if pillars and impact in ("Thesis 강화", "Thesis 약화", "리스크 해소", "신규 리스크"):
        primary = pillars[0]
        direction = {
            "Thesis 강화": "강화 가능성",
            "Thesis 약화": "약화 가능성",
            "리스크 해소": "기존 리스크 해소",
            "신규 리스크": "신규 리스크 부각",
        }[impact]
        key_points.append(
            f"투자 논리 \"{primary}\" 관점에서 {direction}"
        )
    elif pillars:
        key_points.append(
            f"종목의 핵심 thesis: \"{pillars[0]}\" — 이 뉴스가 직접 영향을 주는지 확인 필요"
        )

    # 2. 핵심 숫자
    if numbers:
        key_points.append(f"기사에 언급된 핵심 숫자: {', '.join(numbers[:3])}")

    # 3. 이벤트 상태가 종료/완료/무산이면 강조 (사용자가 헷갈리기 쉬움)
    if status in ("종료", "완료", "무산"):
        key_points.append(f"이벤트 상태: {status} — 진행 중 이벤트 아님 (재해석 필요)")
    elif status == "진행 중" and impact != "확인 필요":
        key_points.append(f"이벤트 진행 중 — 후속 보도와 회사 가이던스 변화가 핵심 catalyst")

    # 4. 추가 thesis pillar 연결 (있으면)
    if len(pillars) >= 2 and impact in ("Thesis 강화", "Thesis 약화"):
        key_points.append(f"부차적 영향 가능 thesis: \"{pillars[1]}\"")

    # 5. urgent 신호
    if urgent:
        key_points.append(
            "anti-thesis 키워드 포함 — 회계/조사/소송/dilution 등 risk 키워드 감지"
        )

    # 본문 부족 시
    if not has_real_body:
        key_points.append("본문 발췌 부족 — 자동 분류 신뢰도 제한적, 원문 직접 확인 권장")

    if not key_points:
        key_points.append(f"이 기사의 thesis 영향은 자동 분류상 \"{impact}\"")

    # ── investment_implication: 1 문장 ─────────────────────────────────
    implication = _short_implication(impact, pillars[0] if pillars else "")

    return {
        "detailed_summary_ko": summary_short,
        "key_points_ko": key_points[:5],
        "investment_implication_ko": implication,
        "thesis_impact_ko": impact,
        "confidence_level_ko": confidence,
        "body_excerpt": summary if has_real_body else None,
    }


def _short_implication(impact: str, pillar: str) -> str:
    """1 문장 투자적 의미. 메타 문구 / 단정 회피 안내 제거."""
    if impact == "Thesis 강화":
        return (
            f"기존 thesis (\"{pillar}\") 강화 신호로 해석될 여지가 있으며, "
            "후속 매출/마진 영향 시점이 핵심 변수입니다."
            if pillar
            else "기존 thesis 강화 신호로 해석될 여지가 있습니다."
        )
    if impact == "Thesis 약화":
        return (
            "기존 thesis 의 일부가 흔들릴 수 있는 신호로, "
            "구조적 훼손 vs 단기 이벤트 구분이 우선 과제입니다."
        )
    if impact == "리스크 해소":
        return (
            "기존 단기 리스크가 해소되는 흐름으로, 투자 초점이 본업 지표로 다시 이동할 가능성."
        )
    if impact == "신규 리스크":
        return (
            "신규 리스크 신호로 anti-thesis 점검이 우선되며, 후속 보도 / 출처 신뢰도 확인이 필요합니다."
        )
    if impact == "단기 노이즈":
        return "thesis 에 미치는 영향은 제한적이며, 단기 sentiment 영향만 가능."
    return "정밀 검토 필요 — 단정 가능한 단계가 아닙니다."


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

_LLM_PROMPT = """다음은 미국 상장주식 관련 영어 뉴스입니다. 한국어로 핵심만 추출하세요.

[뉴스 정보]
- 종목: {ticker_disp}
- 제목: {title}
- 출처: {source}
- 발행일: {published_at}
- 본문 발췌: {summary}

[중요 — 절대 하지 말 것]
- "이 보도는 ~매체이 ~에 전한 ~ 관련 기사" 같은 메타 문구 금지
- 회사 소개, 사업 설명 금지 (사용자는 이미 알고 있음)
- "원문 / IR / 공시를 직접 확인하라" 같은 안내 문구 금지
- "~ 잠정 분류됩니다" 같은 메타 분류 문장 금지
- 단순 제목 번역 금지

[해야 할 것]
1. detailed_summary_ko: 1~2문장. **무슨 일이 있었고 왜 중요한지만**.
2. key_points_ko: 3~5개 불릿. **사용자가 모를만한 인사이트** (예: 이 뉴스가 종목의 어떤 thesis pillar 와 연결되는지, 핵심 숫자, 이해관계자, 시점 등).
3. investment_implication_ko: 1문장. Thesis 영향 + 어떤 변수를 봐야 하는지.
4. thesis_impact_ko: Thesis 강화 / Thesis 약화 / 리스크 해소 / 신규 리스크 / 단기 노이즈 / 확인 필요 중 하나.
5. confidence_level_ko: High / Medium / Low.

[출력 형식 — JSON 만]
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
