"""매크로 이슈 자동 합성 — RSS 뉴스 50건 → LLM (GPT-4o-mini) → 3 이슈.

Output schema (curated.MACRO_ISSUES 와 동일):
    {
        "title": "한국어 1줄 헤드라인",
        "category": "금리 / 채권 / 매크로 등",
        "impact": "시장 영향 1줄",
        "sectors": "관련 섹터 콤마 구분",
        "interpretation": "투자적 해석 2~3 문장 한국어"
    }

비용:
    Input ~5K tokens × $0.15/M + Output ~1K tokens × $0.60/M = 약 $0.0015/일
    월 $0.05 미만.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("macro_summarizer")


# OpenAI 가격
PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60


SYSTEM_PROMPT = """You are an experienced macro / geopolitics analyst writing in Korean
for a Korean retail investor. You synthesize the day's macro / policy / geopolitical
news (US + Korea sources) into the **3 most market-moving issues** affecting
US equity investors.

Output strictly valid JSON matching the schema. All Korean text must be natural,
fluent Korean — not translated-from-English style.

Tone: 신중한 매크로 애널리스트 — 사실 기반, 과장 없음, 양면 균형 (positive / negative
모두 다룸). 단정적 매수/매도 추천 금지 — "투자적 해석" 은 시장 영향과 점검 사항 중심.

Categories must be one of:
    "금리 / 채권 / 매크로"
    "정책 / 무역"
    "지정학 / 에너지"
    "재정 / 통화"
    "기술 / 반도체"
    "외환 / 환율"
    "원자재"
    "AI 인프라"

Sectors: 콤마로 구분된 한국어 섹터 명. 예: "AI 인프라, 반도체, 소프트웨어"
"""


# JSON Schema
MACRO_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "한국어 1줄 헤드라인 (~30자)"},
                    "category": {"type": "string"},
                    "impact": {"type": "string", "description": "시장 영향 1줄 (~80자)"},
                    "sectors": {"type": "string", "description": "콤마 구분 섹터"},
                    "interpretation": {"type": "string", "description": "투자적 해석 2~3 문장 (~200자)"},
                },
                "required": ["title", "category", "impact", "sectors", "interpretation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def _build_user_prompt(news_items: list[dict[str, Any]], today_iso: str) -> str:
    """뉴스 50건 → LLM prompt 조립."""
    parts: list[str] = []
    parts.append(f"[Date] {today_iso}")
    parts.append(f"[News count] {len(news_items)} items from US + Korea sources")
    parts.append("")
    parts.append("[Recent macro / market news headlines + summaries]")
    for i, n in enumerate(news_items, 1):
        title = (n.get("title") or "").strip()[:200]
        summary = (n.get("summary") or "").strip()[:300]
        source = n.get("source_name", "")
        date = (n.get("published_at") or "")[:10]
        if not title:
            continue
        parts.append(f"{i}. [{source} · {date}] {title}")
        if summary and summary != title:
            parts.append(f"   {summary}")
    parts.append("")
    parts.append(
        "[Required Output] — 위 뉴스를 종합해 미국 주식 투자자에게 가장 영향 큰 "
        "매크로·정책·지정학 이슈 정확히 3개를 한국어로 합성. JSON schema 따름."
    )
    return "\n".join(parts)


def call_openai_for_macro(
    user_prompt: str, *, model: str = "gpt-4o-mini",
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """OpenAI structured output 호출."""
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY 미설정 — macro_summarizer 호출 skip")
        return None, {"error": "no_api_key"}

    try:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "macro_issues",
                    "schema": MACRO_SCHEMA,
                    "strict": True,
                },
            },
            temperature=0.4,  # 약간의 자연스러움
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
            + meta["token_output"] / 1_000_000 * PRICE_OUTPUT_PER_M,
            5,
        )
        try:
            parsed = json.loads(text)
            return parsed.get("issues") or [], meta
        except json.JSONDecodeError as e:
            log.warning("macro_summarizer JSON parse 실패: %s\n%s", e, text[:300])
            return None, meta
    except Exception as e:
        log.warning("OpenAI 호출 실패 (macro_summarizer): %s", e)
        return None, {"error": str(e)}


def generate_macro_issues(
    conn, date_iso: str, *, force: bool = False, max_age_hours: int = 18,
) -> list[dict[str, Any]] | None:
    """매크로 이슈 3개 자동 생성 + DB 저장.

    Args:
        date_iso: 오늘 일자 (YYYY-MM-DD)
        force: True 면 캐시 무시하고 재생성
        max_age_hours: 캐시 fresh 판정 (default 18시간 — 하루 한 번)

    Returns: 3 개 issue dict 또는 None (실패).
    """
    from .macro_fetcher import fetch_macro_news
    from . import database as db

    # 1) 캐시 hit 확인
    if not force:
        try:
            row = conn.execute(
                "SELECT issues_json, generated_at FROM macro_issues_auto WHERE date=?",
                (date_iso,),
            ).fetchone()
            if row:
                # 같은 날 이미 생성 — 18시간 이내면 그대로 사용
                import datetime as _dt
                try:
                    gen = _dt.datetime.fromisoformat(row["generated_at"])
                    age_hours = (_dt.datetime.now() - gen).total_seconds() / 3600.0
                    if age_hours < max_age_hours:
                        log.info("macro_issues cache hit (age %.1fh)", age_hours)
                        return json.loads(row["issues_json"])
                except Exception:
                    pass
        except Exception:
            pass

    log.info("매크로 이슈 자동 생성 시작 (%s)", date_iso)

    # 2) RSS 뉴스 fetch
    try:
        news = fetch_macro_news(max_per_source=20, max_total=60, only_macro=True)
    except Exception as e:
        log.warning("매크로 뉴스 fetch 실패: %s", e)
        return None
    if not news:
        log.warning("매크로 뉴스 0건 — LLM 호출 skip")
        return None
    log.info("매크로 뉴스 %d건 → LLM", len(news))

    # 3) prompt 조립 + LLM 호출
    user_prompt = _build_user_prompt(news, date_iso)
    issues, meta = call_openai_for_macro(user_prompt)
    if not issues or len(issues) < 3:
        log.warning("LLM 매크로 이슈 합성 실패 또는 3개 미만")
        return None

    # 4) DB 저장
    try:
        sources_count = len({n.get("source_name", "") for n in news})
        conn.execute(
            """
            INSERT INTO macro_issues_auto (date, issues_json, sources_count,
                model_used, token_input, token_output, cost_estimate_usd, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                issues_json = excluded.issues_json,
                sources_count = excluded.sources_count,
                model_used = excluded.model_used,
                token_input = excluded.token_input,
                token_output = excluded.token_output,
                cost_estimate_usd = excluded.cost_estimate_usd,
                generated_at = excluded.generated_at
            """,
            (
                date_iso, json.dumps(issues, ensure_ascii=False),
                sources_count,
                meta.get("model", "gpt-4o-mini"),
                meta.get("token_input", 0), meta.get("token_output", 0),
                meta.get("cost_estimate_usd", 0.0),
                __import__("datetime").datetime.now().isoformat(),
            ),
        )
        conn.commit()
        log.info(
            "macro_issues saved — cost ~$%.4f, %d input / %d output tokens, %d sources",
            meta.get("cost_estimate_usd", 0),
            meta.get("token_input", 0), meta.get("token_output", 0),
            sources_count,
        )
    except Exception as e:
        log.warning("macro_issues DB save 실패: %s", e)

    return issues


def fetch_today_macro_issues(conn, date_iso: str) -> list[dict[str, Any]] | None:
    """DB 의 오늘 macro_issues 조회 (없으면 None)."""
    try:
        row = conn.execute(
            "SELECT issues_json FROM macro_issues_auto WHERE date=?",
            (date_iso,),
        ).fetchone()
        if row:
            return json.loads(row["issues_json"])
    except Exception:
        pass
    return None
