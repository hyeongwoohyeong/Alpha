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
    key_metrics as _curated_key_metrics,
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
    *,
    budget=None,
    cfg=None,
    conn=None,
) -> dict[str, Any]:
    """뉴스 → 한국어 리서치 메모.

    캐시/LLM 모드:
        - conn 이 주어지고 ENABLE_SUMMARY_CACHE=on 이면 article_summaries 조회 → hit 시 재사용
        - LLM_MODE=none 이면 항상 룰 기반
        - LLM_MODE=low_cost / high_quality 이면 LLM 호출 (budget 한도 안에서)
        - 결과는 conn 이 있으면 article_summaries 캐시에 저장
    """
    # cfg / budget lazy import (순환 import 회피)
    from .config import load_config, make_budget
    cfg = cfg or load_config()
    if budget is None:
        budget = make_budget(cfg)

    url = (news_item.get("link") or "").strip() or None

    # 1) 캐시 조회
    if conn is not None and cfg.enable_summary_cache and url:
        try:
            from . import database as _db
            cached = _db.fetch_article_summary(conn, url)
        except Exception as e:
            log.debug("article cache read failed: %s", e)
            cached = None
        if cached:
            try:
                fu = cached["follow_up_items_ko"]
                follow_ups = _safe_json_list(fu)
            except Exception:
                follow_ups = []
            return {
                "detailed_summary_ko": cached["detailed_summary_ko"],
                "investment_implication_ko": cached["investment_implication_ko"],
                "follow_up_items_ko": follow_ups,
                "thesis_impact_ko": cached["thesis_impact"] or "확인 필요",
                "confidence_level_ko": cached["confidence_level"] or "Low",
                "content_availability": cached["content_availability"],
                "body_excerpt": news_item.get("summary"),
                "key_points_ko": [],
                "from_cache": True,
                "model_used": cached["model_used"],
            }

    # 2) LLM 모드 판단
    payload: dict[str, Any] | None = None
    model_used = "rule-based"
    if cfg.llm_enabled and budget.can_call():
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                payload = _summarize_with_anthropic(news_item, stock_context)
                budget.record()
                model_used = "claude-haiku-4-5" if not cfg.use_high_quality_llm else "claude-opus-4"
            except Exception as e:
                log.warning("Anthropic 요약 실패 → 폴백: %s", e)
        if payload is None and os.environ.get("OPENAI_API_KEY"):
            try:
                payload = _summarize_with_openai(news_item, stock_context)
                budget.record()
                model_used = "gpt-4o-mini" if not cfg.use_high_quality_llm else "gpt-4o"
            except Exception as e:
                log.warning("OpenAI 요약 실패 → 폴백: %s", e)

    if payload is None:
        payload = _summarize_rule_based(news_item, stock_context)
        model_used = "rule-based"

    # 3) 캐시 저장
    if conn is not None and cfg.enable_summary_cache and url:
        try:
            from . import database as _db
            _db.upsert_article_summary(
                conn,
                url=url,
                title=news_item.get("title"),
                source=news_item.get("source"),
                published_at=news_item.get("published_at"),
                ticker=news_item.get("ticker"),
                content_availability=payload.get("content_availability"),
                detailed_summary_ko=payload.get("detailed_summary_ko"),
                investment_implication_ko=payload.get("investment_implication_ko"),
                follow_up_items_ko=payload.get("follow_up_items_ko"),
                thesis_impact=payload.get("thesis_impact_ko"),
                confidence_level=payload.get("confidence_level_ko"),
                model_used=model_used,
            )
            conn.commit()
        except Exception as e:
            log.debug("article cache write failed: %s", e)

    payload["model_used"] = model_used
    payload["from_cache"] = False
    return payload


def _safe_json_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        import json as _json
        v = _json.loads(raw)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    return []


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
    if any(k in s for k in ["raises guidance", "raised guidance", "guidance up", "raises outlook",
                              "raises forecast", "raised forecast", "raises spending forecast",
                              "raises capex", "raised capex", "boosts forecast"]):
        return ("가이던스", "가이던스 / 지출 전망 상향 보도")
    if any(k in s for k in ["cuts guidance", "lowers guidance", "guidance down", "cuts outlook",
                              "cuts forecast", "lowers forecast", "cuts capex", "lowered forecast"]):
        return ("가이던스", "가이던스 / 지출 전망 하향 보도")
    if any(k in s for k in ["capex", "spending forecast", "spending plan", "investment plan",
                              "ai spending", "ai investment", "capital expenditure"]):
        return ("가이던스", "자본 지출(CAPEX) 계획 관련 보도")
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
    # 폴백 — 사용자 금지어 ("일반 보도" / "관련 보도") 사용 금지
    return ("종합 동향", "회사 동향 관련 보도")


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


def _shorten_pillar(pillar: str) -> str:
    """긴 thesis pillar 문장을 자연스러운 단축 명사구로 압축.

    예시
    ----
    'AI GPU/CUDA 생태계의 사실상 독점적 카테고리 리더십'
        → 'AI GPU 카테고리 리더십'
    'Hyperscaler capex 사이클의 가장 직접적 수혜'
        → 'Hyperscaler capex 수혜'
    'AI 학습용 HBM 수요의 직접 수혜 카테고리 리더'
        → 'HBM 수요 수혜'
    """
    if not pillar:
        return ""
    text = pillar.strip()

    # 1) 흔한 수식어/부사 제거 (의미 손실 거의 없음)
    fillers = [
        "사실상의 ", "사실상 ",
        "가장 직접적인 ", "가장 직접적 ", "직접적인 ", "직접적 ",
        "가장 깊은 곳의 ", "가장 깊은 ", "가장 ",
        "전반적인 ", "전반적 ",
        "구조적인 ", "구조적 ",
        "잠재적인 ", "잠재적 ",
        "근본적인 ", "근본적 ",
        "본질적인 ", "본질적 ",
        "장기적인 ", "장기적 ",
        "단기적인 ", "단기적 ",
        "독점적인 ", "독점적 ",
        "지배적인 ", "지배적 ",
        "압도적인 ", "압도적 ",
        "안정적인 ", "안정적 ",
        "지속적인 ", "지속적 ",
        "최대 ",
    ]
    for f in fillers:
        text = text.replace(f, "")

    # 2) "~의" 로 이어지는 길게 늘어진 형태는 마지막 절만 남기는 편이 자연스럽다
    #    단, 의미 핵심이 앞에 있을 수 있어 너무 길 때만 적용.
    if len(text) > 22 and "의 " in text:
        # 마지막 "의 " 기준으로 잘라 뒤쪽 명사구를 채택
        head, tail = text.rsplit("의 ", 1)
        # 앞부분에서 가장 핵심적인 명사 1개 정도는 살린다
        head_tokens = head.split()
        if head_tokens:
            keep = " ".join(head_tokens[:2])  # 앞 1~2 토큰만 유지
            candidate = f"{keep} {tail}"
        else:
            candidate = tail
        text = candidate.strip()

    # 3) 끝의 군더더기 제거
    text = text.rstrip(",.·- ")

    # 4) 그래도 너무 길면 ~30자에서 단어 경계로 자름 (말줄임 없이)
    if len(text) > 32:
        cut = text[:32]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(",.·- ")

    return text.strip()


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


def _classify_content_availability(title: str, summary: str) -> str:
    """기사 본문 접근 수준 분류.

    - Full Text: 본문 길이가 충분히 길고 (>=200자) 제목과 다름
    - Snippet Only: 50~199자
    - Title Only: 짧거나 제목 반복
    - Unavailable: 제목조차 없음
    """
    if not (title or summary):
        return "Unavailable"
    s = (summary or "").strip()
    title_redundant = _is_title_redundant(title, s)
    if len(s) >= 200 and not title_redundant:
        return "Full Text"
    if len(s) >= 50 and not title_redundant:
        return "Snippet Only"
    return "Title Only"


_TOPIC_CONTEXT_KO: dict[str, str] = {
    "M&A": (
        "이번 보도는 인수합병 추진과 관련된 사안으로, 거래 구조와 자금조달 방식, 규제 승인 일정에 따라 실제 thesis 영향이 "
        "달라질 수 있습니다."
    ),
    "M&A 종결": (
        "기존에 추진되던 거래가 마무리됐다는 점에서 그 동안 가격에 반영돼 있던 불확실성이 해소되는 구간입니다."
    ),
    "M&A 철수/무산": (
        "추진되던 거래가 무산됐다는 점에서 인수자 측에는 자본 부담이 줄어드는 신호로 해석될 수 있습니다."
    ),
    "실적": (
        "실적 발표는 회사의 단기 매출·마진 흐름과 가이던스 신뢰성을 동시에 시험하는 이벤트라는 점에서 시장 반응의 폭이 큰 사안입니다."
    ),
    "가이던스": (
        "가이던스 변경은 회사가 직접 시장 기대를 재조정하는 신호로, 향후 실적 추정과 multiple 에 직접적 영향을 줍니다."
    ),
    "증권사 의견": (
        "증권사 의견 변경은 단독으로 thesis 를 흔들기보다 시장 sentiment 와 단기 수급에 영향을 주는 이벤트로 해석됩니다."
    ),
    "법적 이슈": (
        "법적 분쟁은 단기적 비용 부담과 평판 리스크가 동시에 부각되는 사안으로, 합의금 규모와 결과 시점이 핵심 변수입니다."
    ),
    "규제/조사": (
        "규제 또는 조사 관련 보도는 사실관계가 확정되기 전까지 시장이 보수적으로 가격에 반영하는 경향이 있습니다."
    ),
    "규제 승인": (
        "규제 승인 보도는 진행 중이던 사업 / 제품의 가시성을 높여 단기 모멘텀과 thesis 강화 효과를 동시에 가져올 수 있습니다."
    ),
    "수주/계약": (
        "수주 또는 신규 계약 보도는 매출 가시성 확장 신호로 해석되며, 수익 인식 시점과 마진 구조가 후속 점검 포인트입니다."
    ),
    "신제품": (
        "신제품 / 신규 서비스 발표는 카테고리 경쟁력과 ASP 변화 관점에서 의미가 있으며, 실제 매출 기여 시점은 후속 분기에 확인됩니다."
    ),
    "주주환원": (
        "자사주 매입이나 배당 정책 변경은 회사가 잉여 현금흐름과 자본 배분 우선순위에 대해 시장에 보내는 신호입니다."
    ),
    "경영진": (
        "경영진 변화는 자본 배분과 사업 방향성에 영향을 줄 수 있어 후임자 배경과 전략 변화 가능성을 함께 점검할 필요가 있습니다."
    ),
    "주가": (
        "주가 흐름 자체에 대한 보도이며, 펀더멘털 변화보다 sentiment / 수급 측면 신호로 우선 해석됩니다."
    ),
    "애널리스트 코멘트": (
        "애널리스트 코멘트는 시장 컨센서스 형성에 영향을 줄 수 있으나, 수치 변경 폭과 근거 데이터를 함께 봐야 합니다."
    ),
    "종목 분석": (
        "종목 분석 칼럼은 기자 / 매체의 의견성 콘텐츠로, 새로운 사실보다 기존 정보의 재해석에 가깝다는 점을 감안할 필요가 있습니다."
    ),
    "종합 동향": (
        "단일 catalyst 보다 회사 / 산업 흐름 전반에 대한 보도로, 개별 thesis 단정보다는 본업 지표 변화 추세를 함께 점검할 필요가 있습니다."
    ),
}


_THESIS_IMPACT_NARRATIVE: dict[str, str] = {
    "Thesis 강화": (
        "이번 보도는 기존 투자 thesis 의 핵심 축을 강화할 수 있는 신호로 해석되며, 단정 이전에 실제 매출·마진으로 "
        "연결되는 시점과 가이던스 변화 폭을 함께 봐야 합니다."
    ),
    "Thesis 약화": (
        "이번 보도는 기존 thesis 의 일부 가정에 부담을 주는 신호로, 구조적 훼손인지 단기 이벤트성 부담인지를 "
        "분리해서 점검할 필요가 있습니다."
    ),
    "리스크 해소": (
        "그 동안 가격에 반영되던 단기 리스크가 해소되는 흐름이며, 투자 판단의 초점이 본업 실적과 가이던스로 다시 "
        "이동할 가능성이 있습니다."
    ),
    "신규 리스크": (
        "기존 thesis 에 새로운 부담이 더해질 수 있는 사안으로, anti-thesis 점검이 우선되며 출처 신뢰도와 후속 "
        "보도를 함께 확인해야 합니다."
    ),
    "단기 노이즈": (
        "장기 thesis 에 주는 영향은 제한적이며, 단기 sentiment 측면 영향이 우선되는 보도로 해석됩니다."
    ),
    "확인 필요": (
        "현 시점에서는 단정이 가능한 단계가 아니며, 본문 사실관계와 후속 보도, 회사 가이던스 변화를 함께 확인할 "
        "필요가 있습니다."
    ),
}


def _build_detailed_memo(
    *,
    name_disp: str,
    title: str,
    topic_label: str,
    headline_phrase: str,
    numbers: list[str],
    pillar_short: str,
    impact: str,
    status: str,
    urgent: bool,
    content_availability: str,
) -> str:
    """국내 증권사 리서치 메모 톤의 한국어 상세 요약. 5문장 이상.

    "일반 보도", "관련 보도", "핵심 포인트입니다" 같은 의미 없는 표현은 사용하지 않는다.
    본문 접근 수준에 따라 분량과 단정 강도를 조절한다.
    """
    name = name_disp or "해당 종목"
    sentences: list[str] = []

    # ── 1문장: 무슨 일이 있었는지 (금지어 "관련 보도" 회피)
    if topic_label == "실적" and numbers:
        sentences.append(
            f"{name} 의 분기 실적 발표가 보도됐으며, 기사에서 언급된 주요 수치는 {', '.join(numbers[:3])} 입니다."
        )
    elif topic_label == "가이던스" and numbers:
        sentences.append(
            f"{name} 와 관련해 가이던스 또는 자본 지출 계획 조정이 보도됐으며, "
            f"기사에서 언급된 주요 수치는 {', '.join(numbers[:3])} 입니다."
        )
    elif topic_label == "M&A" and numbers:
        sentences.append(
            f"{name} 와 관련해 약 {numbers[0]} 규모로 거론되는 인수합병 움직임이 보도됐습니다."
        )
    elif topic_label == "법적 이슈":
        sentences.append(
            f"{name} 에 대한 법적 분쟁 사안이 보도됐으며, 단기 sentiment 와 anti-thesis 점검이 동시에 필요한 단계입니다."
        )
    elif topic_label == "규제/조사":
        sentences.append(
            f"{name} 에 대한 규제 / 조사 사안이 보도됐으며, 사실관계가 확정되기 전까지는 시장이 보수적으로 가격에 반영하는 경향이 있는 유형의 이슈입니다."
        )
    elif topic_label == "규제 승인":
        sentences.append(
            f"{name} 와 관련된 규제 / 당국 승인 절차에 진전이 있었다는 소식이 전해졌습니다."
        )
    elif topic_label == "수주/계약" and numbers:
        sentences.append(
            f"{name} 가 약 {numbers[0]} 규모의 수주 또는 신규 계약을 확보했다는 소식이 전해졌습니다."
        )
    elif topic_label == "주주환원":
        sentences.append(
            f"{name} 의 자사주 매입 또는 배당 정책 변경 소식이 전해졌습니다."
        )
    elif topic_label == "경영진":
        sentences.append(
            f"{name} 의 주요 경영진 변화 소식이 전해졌습니다."
        )
    elif topic_label == "신제품":
        sentences.append(
            f"{name} 의 신제품 또는 신규 서비스 발표가 확인됐습니다."
        )
    elif topic_label == "주가":
        sentences.append(
            f"이번 기사는 {name} 의 주가 흐름 자체에 대한 내용이며, 펀더멘털보다 sentiment / 수급 측면 신호에 가깝습니다."
        )
    elif topic_label in ("증권사 의견", "애널리스트 코멘트"):
        sentences.append(
            f"{name} 에 대한 증권사 / 애널리스트 의견 변경 소식이 확인됐습니다."
        )
    elif topic_label == "종목 분석":
        sentences.append(
            f"이번 콘텐츠는 매체의 {name} 분석성 칼럼으로, 새로운 사실관계라기보다 기존 정보의 재해석에 가깝습니다."
        )
    else:
        # 종합 동향 — 토픽 분류가 모호한 경우. "일반 보도" / "관련 보도" 같은 표현은 사용 금지.
        if numbers:
            sentences.append(
                f"{name} 와 관련해 {', '.join(numbers[:2])} 수치가 거론된 동향성 보도가 확인됐습니다."
            )
        else:
            sentences.append(
                f"{name} 와 관련된 동향성 보도가 확인됐으며, 단일 catalyst 라기보다 회사 / 산업 사이클 흐름에 대한 "
                "내용으로 해석됩니다."
            )

    # ── 2문장: 토픽 컨텍스트 (왜 중요한지)
    ctx = _TOPIC_CONTEXT_KO.get(topic_label)
    if ctx:
        sentences.append(ctx)

    # ── 3문장: 숫자가 있다면 의미 부여
    if numbers and topic_label not in ("주가",):
        first = numbers[0]
        if topic_label == "실적":
            sentences.append(
                f"특히 보도에서 거론된 {first} 수치가 컨센서스 대비 어떤 수준이었는지, 그리고 동일 분기 가이던스가 "
                "함께 갱신됐는지가 다음 점검 포인트입니다."
            )
        elif topic_label == "가이던스":
            sentences.append(
                f"가이던스 폭({first}) 자체보다, 그 변경 근거가 비용 / 매출 / 환율 / 수요 중 어느 쪽인지에 따라 시장 "
                "반응의 지속성이 달라질 수 있습니다."
            )
        elif topic_label == "M&A":
            sentences.append(
                f"인수가({first}) 규모와 자금조달 구조, 규제 승인 일정이 거래 종결 가능성과 단기 valuation 변화의 핵심 "
                "변수입니다."
            )
        elif topic_label == "수주/계약":
            sentences.append(
                f"보도된 {first} 규모가 회사 매출에서 차지하는 비중과 인식 시점이 매출 가시성 개선의 의미를 결정합니다."
            )
        else:
            sentences.append(
                f"기사에서 거론된 {first} 수치의 의미는 회사의 후속 가이던스와 IR 발표를 통해 구체화돼야 합니다."
            )

    # ── 4문장: pillar 와 thesis 영향 narrative
    impact_narr = _THESIS_IMPACT_NARRATIVE.get(impact, _THESIS_IMPACT_NARRATIVE["확인 필요"])
    if pillar_short and impact in ("Thesis 강화", "Thesis 약화", "신규 리스크", "리스크 해소"):
        sentences.append(
            f"기존 투자 thesis 인 \"{pillar_short}\" 관점에서 보면, {impact_narr.lower()[:1]}{impact_narr[1:]}"
        )
    else:
        sentences.append(impact_narr)

    # ── 5문장: 후속 관전 포인트 한 줄로
    if topic_label in ("실적", "가이던스"):
        sentences.append(
            "다음 분기 실적과 가이던스 변화, 그리고 이번 보도가 회사 자본 배분 정책에 어떤 영향을 주는지가 단기 관전 "
            "포인트입니다."
        )
    elif topic_label in ("법적 이슈", "규제/조사"):
        sentences.append(
            "회사 측 공식 입장과 합의 / 결과 시점, 그리고 본업 실적에 미치는 영향 정도가 후속 관전 포인트입니다."
        )
    elif topic_label == "M&A":
        sentences.append(
            "거래 종결 시점과 자금조달 구조, 인수 후 통합 비용이 향후 multiple 에 영향을 줄 수 있는 변수들입니다."
        )
    else:
        sentences.append(
            "이번 보도 이후 회사 측 공식 발표나 동종 업체 동향이 추가로 확인되면, thesis 단정 강도가 달라질 수 있습니다."
        )

    # ── 본문 접근 수준이 낮으면 명시
    if content_availability == "Title Only":
        sentences.append(
            "현재 본문 전문 접근이 어려운 상태로, 위 해석은 기사 제목 기준의 잠정적 정리이며 원문 본문 확인이 필요합니다."
        )
    elif content_availability == "Snippet Only":
        sentences.append(
            "본문 전체가 아닌 발췌 기준 정리이므로, 세부 수치와 가이던스 문구는 원문에서 추가로 확인할 필요가 있습니다."
        )

    # urgent 사안이면 한 줄 추가 (중복 방지)
    if urgent and not any("anti-thesis" in s for s in sentences):
        sentences.append(
            "회계 / 조사 / 소송 등 risk 키워드가 포함된 사안인 만큼, 보도 사실관계와 추가 출처 확인이 우선되어야 합니다."
        )

    # 종결/완료/무산 사안 — 진행중으로 오해하지 않도록
    if status in ("종료", "완료", "무산"):
        sentences.append(
            f"또한 이 사안은 이미 {status}된 이벤트로, 진행 중 사안으로 오해하지 않도록 주의가 필요합니다."
        )

    # 4문장 미만으로 떨어지면 fallback 문장으로 보강 (UI 최소 보증)
    if len(sentences) < 4:
        sentences.append(
            "현 시점에서는 보도 자체로 단정 가능한 정보 폭이 제한적이므로, 회사 IR 자료와 후속 보도를 통해 사실관계를 "
            "확인할 필요가 있습니다."
        )

    return " ".join(sentences)


def _build_key_thesis_paragraph(
    *,
    name_disp: str,
    impact: str,
    pillar_short: str,
    topic_label: str,
    content_availability: str,
) -> str:
    """투자적 의미(Key Thesis) — 2~3문장. thesis 영향과 그 근거를 정리."""
    name = name_disp or "해당 종목"
    impact_narr = _THESIS_IMPACT_NARRATIVE.get(impact, _THESIS_IMPACT_NARRATIVE["확인 필요"])
    parts: list[str] = []

    # 1문장 — thesis 와 이번 보도의 연결
    if pillar_short and impact in ("Thesis 강화", "Thesis 약화", "신규 리스크", "리스크 해소"):
        parts.append(
            f"{name} 의 기존 투자 thesis 가 \"{pillar_short}\" 에 있었다면, 이번 보도는 그 핵심 축에 직접 연동되는 신호입니다."
        )
    else:
        parts.append(
            f"{name} 의 기존 투자 thesis 와 이번 보도의 연결 관계를 보수적으로 점검해야 하는 단계입니다."
        )

    # 2문장 — impact narrative
    parts.append(impact_narr)

    # 3문장 — 단기 vs 중장기 분리
    if impact == "Thesis 강화":
        parts.append(
            "단기적으로는 sentiment 개선이 가능하나, 중장기적으로는 매출·마진 지표가 실제 개선 방향으로 확인되어야 "
            "re-rating 로 이어질 수 있습니다."
        )
    elif impact == "Thesis 약화":
        parts.append(
            "단기 multiple 부담이 커질 수 있으며, 가이던스 / 분기 실적에서 회복 신호가 확인되기 전까지는 신중한 접근이 "
            "필요합니다."
        )
    elif impact == "신규 리스크":
        parts.append(
            "단기 valuation 부담과 함께 anti-thesis 점검이 우선이며, 사실관계가 정리되기 전까지는 포지션 사이즈를 "
            "보수적으로 유지하는 편이 적절합니다."
        )
    elif impact == "리스크 해소":
        parts.append(
            "단기 디스카운트 요인이 줄어드는 흐름으로, 본업 실적이 정상 궤도로 돌아오는지 여부가 다음 re-rating 트리거가 "
            "됩니다."
        )
    elif impact == "단기 노이즈":
        parts.append(
            "포지션 사이징이나 비중 조정의 트리거로 삼기에는 정보 폭이 부족하며, 본업 지표 변화 여부를 우선 확인해야 합니다."
        )
    else:
        parts.append(
            "현 단계에서는 보도 자체보다 회사 측 공식 발표 / 추가 출처를 통한 사실관계 확정이 우선됩니다."
        )

    if content_availability == "Title Only":
        parts.append(
            "단, 현재는 본문 접근이 제한된 상태이므로 위 해석은 잠정적이며, 원문 본문 확인 이후 다시 점검할 필요가 있습니다."
        )

    return " ".join(parts)


def _build_follow_up_items(
    *,
    ticker: str,
    topic_label: str,
    impact: str,
    urgent: bool,
    content_availability: str,
) -> list[str]:
    """확인 필요 사항(Follow-up Items) — 실제 후속 리서치 항목 4~6개."""
    items: list[str] = []

    # 1) 큐레이션된 종목별 핵심 지표
    metrics = _curated_key_metrics(ticker) if ticker else []
    for m in metrics[:3]:
        items.append(f"{m} 추세")

    # 2) 토픽별 구체 follow-up
    topic_items = {
        "실적": [
            "다음 분기 가이던스 변경 폭",
            "OPM / FCF margin 변화",
            "보도된 수치의 컨센서스 대비 위치",
        ],
        "가이던스": [
            "가이던스 변경 근거 (비용 / 매출 / 환율 / 수요)",
            "Capex 가이던스 세부 구성",
            "다음 분기 실적 발표에서의 후속 코멘트",
        ],
        "M&A": [
            "거래 자금조달 구조와 EPS dilution",
            "규제 승인 일정",
            "인수 후 통합 비용 예상",
        ],
        "M&A 종결": [
            "통합 비용 인식 분기",
            "회계상 영업외 비용 처리 여부",
        ],
        "M&A 철수/무산": [
            "위약금 / 비용 인식 규모",
            "차후 자본 배분 우선순위",
        ],
        "법적 이슈": [
            "회사 측 공식 입장 및 합의 가능성",
            "예상 합의금 / 비용 규모",
            "본업 실적에 미치는 영향 정도",
        ],
        "규제/조사": [
            "조사 범위와 결과 시점",
            "유사 동종 업체 사례",
            "단기 비용 부담 추정",
        ],
        "규제 승인": [
            "승인 이후 매출 인식 시점",
            "ASP / 마진 구조 변화",
        ],
        "수주/계약": [
            "수주 규모의 매출 비중 및 인식 시점",
            "추가 수주 파이프라인",
            "Backlog book-to-bill 변화",
        ],
        "신제품": [
            "초기 채택률 / 사전 주문 강도",
            "ASP / 마진 영향",
            "기존 제품 대비 cannibalization 여부",
        ],
        "주주환원": [
            "FCF 대비 환원 비율",
            "잔여 매입 한도 및 기간",
        ],
        "경영진": [
            "후임자 배경 및 전략 변화 가능성",
            "자본 배분 / R&D 우선순위 변화",
        ],
        "주가": [
            "본업 펀더멘털 변화 여부",
            "수급 측면 (옵션 / short interest) 변화",
        ],
        "증권사 의견": [
            "목표주가 변경 폭과 근거",
            "컨센서스 변화 추세",
        ],
        "애널리스트 코멘트": [
            "목표주가 / EPS 추정 변경 폭",
            "코멘트 근거 데이터",
        ],
        "종목 분석": [
            "회사 측 공식 발표 또는 IR 자료",
            "동종 업체 비교 데이터",
        ],
    }
    for t in topic_items.get(topic_label, [])[:3]:
        items.append(t)

    # 3) urgent 사안이면 출처 확인 항목 추가
    if urgent:
        items.append("출처 신뢰도 및 동일 사안 다수 매체 보도 여부")

    # 4) 본문 미확보 시 명시적 항목
    if content_availability in ("Title Only", "Unavailable"):
        items.append("기사 본문 확인 (현재 제목/요약 기준 잠정 정리)")

    # 5) 최소 4개 보장 (희박한 토픽 대비)
    while len(items) < 4:
        items.append("다음 실적 발표에서의 가이던스 / 자본 배분 변화")

    # 중복 제거 (순서 유지)
    seen: set[str] = set()
    deduped: list[str] = []
    for it in items:
        if it not in seen:
            deduped.append(it)
            seen.add(it)
    return deduped[:6]


def _summarize_rule_based(
    news: dict[str, Any], stock: dict[str, Any] | None
) -> dict[str, Any]:
    """뉴스 → 한국어 리서치 메모. 출력 스키마:

    detailed_summary_ko / investment_implication_ko / follow_up_items_ko /
    thesis_impact_ko / confidence_level_ko / content_availability / body_excerpt
    """
    title = (news.get("title") or "").strip()
    summary = (news.get("summary") or "").strip()
    published = (news.get("published_at") or "").strip()
    source = (news.get("source") or "").strip()
    ticker = news.get("ticker") or (stock or {}).get("ticker") or ""
    name_ko = (stock or {}).get("name_ko") if stock else None
    name_disp = display_name(name_ko or "", ticker) if ticker else ""

    # 본문 가용성
    content_availability = _classify_content_availability(title, summary)
    has_real_body = content_availability in ("Full Text", "Snippet Only")

    # 토픽 / 헤드라인
    topic_label, headline_phrase = _detect_news_topic(f"{title} {summary}")

    # 상태 / 영향 / 키워드 점수
    status = classify_event_status(f"{title} {summary}")
    urgent = is_urgent_risk(f"{title} {summary}")
    staleness = compute_staleness(published)
    src_q = source_quality_from_name(source)

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
    score = 0.0
    score += sum(0.8 for k in pos_kw if k in s_lower)
    score -= sum(0.8 for k in neg_kw if k in s_lower)
    if sum(1 for k in pos_kw if k in s_lower) >= 2:
        score += 0.5
    if sum(1 for k in neg_kw if k in s_lower) >= 2:
        score -= 0.5

    impact = thesis_impact_from(status, score, urgent, staleness)

    # 양면 (Two-sided) 이벤트 감지 — CAPEX 가이던스 상향 + 주가 하락 같은 케이스
    # 시장 반응이 긍정 키워드와 어긋나는 경우 "확인 필요" 로 보수적 분류
    txt_low = (title + " " + summary).lower()
    capex_keywords = ("capex", "spending forecast", "ai spending",
                       "raises forecast", "investment plan", "capital expenditure")
    negative_reaction = ("sinks", "falls", "drops", "tumbles", "plunges", "slumps",
                          "selloff", "concerns", "worries")
    has_capex = any(k in txt_low for k in capex_keywords)
    has_negative_reaction = any(k in txt_low for k in negative_reaction)
    if has_capex and has_negative_reaction and impact == "Thesis 강화":
        # 양면 신호 — 단순 강화 분류는 부적절
        impact = "확인 필요"

    # Confidence — 본문 가용성을 1차 기준으로
    if content_availability == "Full Text" and src_q == "High" and staleness in ("fresh", "aging"):
        confidence = "High"
    elif content_availability == "Full Text":
        confidence = "Medium"
    elif content_availability == "Snippet Only" and src_q == "High":
        confidence = "Medium"
    elif content_availability == "Snippet Only":
        confidence = "Low"
    else:
        # Title Only / Unavailable
        confidence = "Low"

    pillars = _curated_thesis_pillars(ticker) if ticker else []
    pillar_short = _shorten_pillar(pillars[0]) if pillars else ""

    numbers = _extract_numbers(summary) if has_real_body else []
    if not numbers:
        numbers = _extract_numbers(title)

    detailed_memo = _build_detailed_memo(
        name_disp=name_disp,
        title=title,
        topic_label=topic_label,
        headline_phrase=headline_phrase,
        numbers=numbers,
        pillar_short=pillar_short,
        impact=impact,
        status=status,
        urgent=urgent,
        content_availability=content_availability,
    )

    key_thesis = _build_key_thesis_paragraph(
        name_disp=name_disp,
        impact=impact,
        pillar_short=pillar_short,
        topic_label=topic_label,
        content_availability=content_availability,
    )

    follow_ups = _build_follow_up_items(
        ticker=ticker,
        topic_label=topic_label,
        impact=impact,
        urgent=urgent,
        content_availability=content_availability,
    )

    return {
        "detailed_summary_ko": detailed_memo,
        "investment_implication_ko": key_thesis,
        "follow_up_items_ko": follow_ups,
        "thesis_impact_ko": impact,
        "confidence_level_ko": confidence,
        "content_availability": content_availability,
        "body_excerpt": summary if has_real_body else None,
        # legacy compatibility — 일부 호출자가 아직 참조 가능
        "key_points_ko": [],
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

_LLM_PROMPT = """너는 국내 탑티어 증권사 리서치센터의 애널리스트처럼 영어 기사를 한국어 투자 메모로 요약한다.
사용자가 영어 원문을 클릭하지 않아도 기사 전반을 이해할 수 있게 작성한다.

[뉴스 정보]
- 종목: {ticker_disp}
- 제목: {title}
- 출처: {source}
- 발행일: {published_at}
- 기사 본문 또는 snippet: {summary}
- 기존 투자 thesis 핵심 축: {pillars}

[작성 규칙]
- 기사 제목을 그대로 반복하지 말 것
- "일반 보도", "관련 보도", "핵심 포인트입니다" 같은 무의미한 표현 금지
- "~매체이 ~에 전한 ~ 관련 기사" 같은 메타 문구 금지
- 회사 소개, 사업 설명 금지 (사용자는 이미 알고 있음)
- "원문 / IR / 공시를 직접 확인하라" 같은 안내 문구 금지
- detailed_summary_ko 는 최소 5문장, 가능하면 6~10문장
- 기사 내 핵심 수치 / 배경 / 시장 반응 / 향후 체크포인트 포함
- 단정이 어려운 내용은 "보도에 따르면", "확인 필요" 라고 표현
- 영어 원문을 직역하지 말고 한국어 리서치 메모 문체로 작성
- 투자 thesis 에 어떤 영향을 주는지 반드시 설명
- follow_up_items_ko 는 실제 후속 리서치 항목 (지표 / 가이던스 / 비교 데이터 등)

[본문이 부족할 때]
- 제목과 짧은 snippet 만 있을 경우, detailed_summary_ko 는 3~5문장으로 제한하고 "본문 확인 필요" 를 명시
- 제목만 있을 경우, "기사 제목 기준 잠정 정리" 임을 명시하고 단정적 표현 금지

[Thesis 영향 분류 (thesis_impact 필드)]
- Thesis 강화 / Thesis 약화 / 신규 리스크 / 리스크 해소 / 단기 노이즈 / 확인 필요

[Confidence 분류 (confidence_level 필드)]
- High: 본문 풀텍스트 + 신뢰도 높은 출처
- Medium: 충분한 snippet + 다수 출처
- Low: 제목 또는 제한적 snippet

[Content Availability (content_availability 필드)]
- Full Text / Snippet Only / Title Only / Unavailable

[출력 형식 — JSON 만, 다른 텍스트 절대 금지]
{{
  "detailed_summary_ko": "...",
  "investment_implication_ko": "...",
  "follow_up_items_ko": ["...", "...", "..."],
  "thesis_impact": "...",
  "confidence_level": "...",
  "content_availability": "..."
}}
"""


def _normalize_llm_payload(data: dict, fallback_news: dict, fallback_stock: dict | None) -> dict:
    """LLM 응답 키 정규화 — `_ko` 접미사 / 한국어 키 차이 흡수."""
    out: dict[str, Any] = {}
    out["detailed_summary_ko"] = data.get("detailed_summary_ko") or data.get("detailed_summary") or ""
    out["investment_implication_ko"] = (
        data.get("investment_implication_ko") or data.get("investment_implication") or ""
    )
    fu = data.get("follow_up_items_ko") or data.get("follow_up_items") or data.get("follow_ups") or []
    if isinstance(fu, str):
        fu = [s.strip("- •·\t ").strip() for s in fu.splitlines() if s.strip()]
    out["follow_up_items_ko"] = [str(x).strip() for x in fu if str(x).strip()][:6]
    out["thesis_impact_ko"] = data.get("thesis_impact_ko") or data.get("thesis_impact") or "확인 필요"
    out["confidence_level_ko"] = data.get("confidence_level_ko") or data.get("confidence_level") or "Low"
    out["content_availability"] = data.get("content_availability") or _classify_content_availability(
        fallback_news.get("title", ""), fallback_news.get("summary", "")
    )
    out["body_excerpt"] = data.get("body_excerpt") or fallback_news.get("summary")
    out["key_points_ko"] = []  # legacy field
    # detailed_summary_ko 가 비어 있으면 rule-based 로 보강 (UI 빈 카드 방지)
    if not out["detailed_summary_ko"]:
        rb = _summarize_rule_based(fallback_news, fallback_stock)
        out["detailed_summary_ko"] = rb["detailed_summary_ko"]
        if not out["investment_implication_ko"]:
            out["investment_implication_ko"] = rb["investment_implication_ko"]
        if not out["follow_up_items_ko"]:
            out["follow_up_items_ko"] = rb["follow_up_items_ko"]
    return out


def _format_llm_prompt(news: dict[str, Any], stock: dict[str, Any] | None) -> str:
    ticker = news.get("ticker", "")
    name_ko = (stock or {}).get("name_ko", "") if stock else ""
    pillars = _curated_thesis_pillars(ticker) if ticker else []
    pillars_str = " / ".join(pillars[:3]) if pillars else "(미등록)"
    return _LLM_PROMPT.format(
        ticker_disp=display_name(name_ko, ticker) if ticker else "해당 종목",
        title=news.get("title", ""),
        source=news.get("source", ""),
        published_at=news.get("published_at", ""),
        summary=news.get("summary", "") or "(본문 없음)",
        pillars=pillars_str,
    )


def _summarize_with_anthropic(news, stock):
    """Claude API 호출 (anthropic SDK 필요)."""
    import anthropic
    client = anthropic.Anthropic()
    prompt = _format_llm_prompt(news, stock)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    import json as _json
    try:
        data = _json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception as e:
        log.warning("Anthropic 응답 JSON 파싱 실패: %s", e)
        return _summarize_rule_based(news, stock)
    return _normalize_llm_payload(data, news, stock)


def _summarize_with_openai(news, stock):
    """OpenAI API 호출 (openai SDK 필요)."""
    import openai
    client = openai.OpenAI()
    prompt = _format_llm_prompt(news, stock)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    import json as _json
    try:
        data = _json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning("OpenAI 응답 JSON 파싱 실패: %s", e)
        return _summarize_rule_based(news, stock)
    return _normalize_llm_payload(data, news, stock)
