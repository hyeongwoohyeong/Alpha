"""전날 글로벌 브리핑 자동 합성 — Google News RSS → LLM (GPT-4o-mini).

"자고 일어나면 어젯밤 글로벌하게 무슨 일이 있었는지" 를 한국 투자자에게 전달하는
이벤트 리캡. 기존 macro_summarizer 의 '주제별 매크로 이슈 3개' (상시 테마라 매일
비슷하게 읽힘) 를 대체.

산출 — 4 카테고리, 각 카테고리당 0~3개의 구체적·날짜 있는 사건:
    geopolitics — 지정학·전쟁
    earnings    — 주요 기업 실적·이벤트
    policy      — 정책·트럼프 발언
    market      — 시장·매크로 지표

각 이벤트:
    headline    — 무슨 일이 있었는가 (1줄, 한국어)
    detail      — 사실 관계 1~2 문장
    implication — 미국 주식 시장에 대한 함의 1문장 (매수/매도 추천 아님)

비용: input ~6K tokens × $0.15/M + output ~1.5K × $0.60/M ≈ $0.002/일, 월 ~$0.06.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("overnight_briefing")

PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60

# 카테고리 순서 / 라벨 (macro_fetcher.BRIEFING_CATEGORIES 와 일치)
CATEGORY_ORDER: list[tuple[str, str]] = [
    ("geopolitics", "지정학·전쟁"),
    ("earnings", "주요 기업 실적·이벤트"),
    ("policy", "정책·트럼프 발언"),
    ("market", "시장·매크로 지표"),
]


SYSTEM_PROMPT = """You are an experienced markets desk editor writing a Korean-language
"overnight global briefing" for a Korean retail investor in US equities.

The reader just woke up and wants to know: **what actually happened around the
world in the last ~24 hours** — wars and diplomacy, major company earnings and
corporate events, policy moves and Trump / Fed statements, and market / macro
data prints.

CRITICAL RULES:
1. Report ONLY concrete, dated events that appear in the supplied headlines.
   This is a news recap, NOT an evergreen theme summary. If the headlines do not
   support a real event for a category, return an EMPTY events array for it —
   never invent or pad.
2. Each event must be a specific occurrence ("X사가 어제 분기 실적을 발표했다",
   "트럼프가 어제 Y를 발표했다"), not a standing theme ("금리가 높다").
3. Do NOT give buy/sell recommendations, price targets, or forecasts. The
   "implication" field describes how the event bears on the US equity market —
   factual mechanism only.
4. All Korean must be natural, fluent Korean — not translated-from-English style.
5. Tone: 신중한 마켓 데스크 에디터 — 사실 중심, 과장 없음.

Output strictly valid JSON matching the schema. Per category, include the 0~3
MOST market-relevant events (deduplicate near-identical stories).
"""


_EVENT_SCHEMA = {
    "type": "array",
    "maxItems": 3,
    "items": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "무슨 일이 있었는가 1줄 한국어 (~40자)"},
            "detail": {"type": "string", "description": "사실 관계 1~2 문장 (~140자)"},
            "implication": {"type": "string", "description": "미국 주식 시장 함의 1문장 (~100자)"},
        },
        "required": ["headline", "detail", "implication"],
        "additionalProperties": False,
    },
}

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "geopolitics": _EVENT_SCHEMA,
        "earnings": _EVENT_SCHEMA,
        "policy": _EVENT_SCHEMA,
        "market": _EVENT_SCHEMA,
    },
    "required": ["geopolitics", "earnings", "policy", "market"],
    "additionalProperties": False,
}


def _build_user_prompt(
    news_by_cat: dict[str, list[dict[str, Any]]], today_iso: str,
) -> str:
    parts: list[str] = [
        f"[Briefing date] {today_iso} (recap of the prior ~24 hours)",
        "",
        "[Collected headlines by category — from Google News, last 24h]",
    ]
    for key, label in CATEGORY_ORDER:
        items = news_by_cat.get(key) or []
        parts.append("")
        parts.append(f"## {key} ({label}) — {len(items)} headlines")
        if not items:
            parts.append("(no headlines collected)")
            continue
        for i, n in enumerate(items, 1):
            title = (n.get("title") or "").strip()[:200]
            src = n.get("source_name", "")
            date = (n.get("published_at") or "")[:10]
            parts.append(f"{i}. [{src} · {date}] {title}")
            summary = (n.get("summary") or "").strip()
            if summary and summary[:60].lower() not in title.lower():
                parts.append(f"   {summary[:200]}")
    parts.append("")
    parts.append(
        "[Required Output] — 위 헤드라인만 근거로, 각 카테고리별 어제 발생한 "
        "구체적 사건 0~3개를 한국어 JSON 으로 합성. 사건이 없는 카테고리는 빈 배열. "
        "추측·외부지식·매수매도 추천 금지."
    )
    return "\n".join(parts)


def _call_openai(prompt: str, model: str = "gpt-4o-mini") -> tuple[dict[str, Any] | None, dict]:
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY 미설정 — overnight_briefing 호출 skip")
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
                    "name": "overnight_briefing",
                    "schema": BRIEFING_SCHEMA,
                    "strict": True,
                },
            },
            temperature=0.3,
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
        log.warning("overnight_briefing LLM 호출 실패: %s", e)
        return None, {"error": str(e)}


def _to_categories(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """LLM raw dict → 순서 있는 카테고리 리스트 (라벨 부착)."""
    categories: list[dict[str, Any]] = []
    for key, label in CATEGORY_ORDER:
        events = parsed.get(key) or []
        clean: list[dict[str, str]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            headline = (ev.get("headline") or "").strip()
            if not headline:
                continue
            clean.append({
                "headline": headline,
                "detail": (ev.get("detail") or "").strip(),
                "implication": (ev.get("implication") or "").strip(),
            })
        categories.append({"key": key, "label": label, "events": clean[:3]})
    return categories


def generate_overnight_briefing(
    conn, date_iso: str, *, force: bool = False, max_age_hours: int = 18,
) -> list[dict[str, Any]] | None:
    """전날 글로벌 브리핑 자동 생성 + DB 저장.

    Returns: 카테고리 리스트 (각 {key, label, events[]}) 또는 None (실패).
    """
    from .macro_fetcher import fetch_overnight_news, total_news_count

    # 1) 캐시 hit 확인
    if not force:
        try:
            row = conn.execute(
                "SELECT briefing_json, generated_at FROM overnight_briefing_auto WHERE date=?",
                (date_iso,),
            ).fetchone()
            if row:
                import datetime as _dt
                try:
                    gen = _dt.datetime.fromisoformat(row["generated_at"])
                    age_h = (_dt.datetime.now() - gen).total_seconds() / 3600.0
                    if age_h < max_age_hours:
                        log.info("overnight_briefing cache hit (age %.1fh)", age_h)
                        return json.loads(row["briefing_json"])
                except Exception:
                    pass
        except Exception:
            pass

    log.info("전날 글로벌 브리핑 자동 생성 시작 (%s)", date_iso)

    # 2) RSS 뉴스 수집
    try:
        news_by_cat = fetch_overnight_news()
    except Exception as e:
        log.warning("전날 뉴스 fetch 실패: %s", e)
        return None
    n_total = total_news_count(news_by_cat)
    if n_total == 0:
        log.warning("전날 뉴스 0건 — LLM 호출 skip")
        return None
    log.info("전날 뉴스 %d건 → LLM", n_total)

    # 3) LLM 합성
    prompt = _build_user_prompt(news_by_cat, date_iso)
    parsed, meta = _call_openai(prompt)
    if not parsed:
        log.warning("overnight_briefing LLM 합성 실패")
        return None
    categories = _to_categories(parsed)
    if not any(c["events"] for c in categories):
        log.warning("overnight_briefing — 모든 카테고리 이벤트 0건")
        return None

    # 4) DB 저장
    try:
        conn.execute(
            """
            INSERT INTO overnight_briefing_auto (date, briefing_json, sources_count,
                model_used, token_input, token_output, cost_estimate_usd, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                briefing_json = excluded.briefing_json,
                sources_count = excluded.sources_count,
                model_used = excluded.model_used,
                token_input = excluded.token_input,
                token_output = excluded.token_output,
                cost_estimate_usd = excluded.cost_estimate_usd,
                generated_at = excluded.generated_at
            """,
            (
                date_iso, json.dumps(categories, ensure_ascii=False), n_total,
                meta.get("model", "gpt-4o-mini"),
                meta.get("token_input", 0), meta.get("token_output", 0),
                meta.get("cost_estimate_usd", 0.0),
                __import__("datetime").datetime.now().isoformat(),
            ),
        )
        conn.commit()
        log.info(
            "overnight_briefing saved — cost ~$%.4f, %d events across 4 categories",
            meta.get("cost_estimate_usd", 0),
            sum(len(c["events"]) for c in categories),
        )
    except Exception as e:
        log.warning("overnight_briefing DB save 실패: %s", e)

    return categories


def fetch_today_overnight_briefing(conn, date_iso: str) -> list[dict[str, Any]] | None:
    """DB 의 오늘 전날 브리핑 조회 (없으면 None)."""
    try:
        row = conn.execute(
            "SELECT briefing_json FROM overnight_briefing_auto WHERE date=?",
            (date_iso,),
        ).fetchone()
        if row:
            return json.loads(row["briefing_json"])
    except Exception:
        pass
    return None
