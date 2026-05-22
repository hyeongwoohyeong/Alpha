"""금일 시장 환경 3 블록 자동 생성 — 시장 데이터 + 매크로 뉴스 → LLM.

3 블록:
    1. 지수 및 Risk Appetite — 자산 테이블이 자동 표시되므로 본문은 없음.
       다만 nominal title 만 LLM 출력으로 일관성 유지.
    2. 금리 / 유동성 — 채권 / Fed 정책 / 신용 환경 한 단락
    3. 주도 테마 및 수급 — AI 인프라 / 재생에너지 / 방산 등 카테고리

비용: macro_summarizer 와 함께 하루 한 번 호출. ~$0.001 추가.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("market_env_summarizer")


PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60


SYSTEM_PROMPT = """You are a Korean equity market strategist writing the daily
"market environment" briefing — 3 short blocks summarizing today's market context
for a Korean retail investor.

Block titles are fixed:
    1. "지수 및 RISK APPETITE" — body 는 자동 자산 테이블이라 본문 LLM 생성 불필요. 빈 string.
    2. "금리·유동성" — 채권 / Fed / 신용 / 유동성 한 단락 (~150자 한국어)
    3. "주도 테마 및 수급" — 그날 자금이 몰리는 카테고리 / 회피되는 카테고리 한 단락 (~150자)

Output strictly JSON matching the schema. All Korean text natural / fluent.

Tone: 신중한 시장 전략가 — 단정적 매수/매도 추천 금지. 시장 환경 묘사 + 점검 사항 중심.
"""


MARKET_ENV_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}


def _build_user_prompt(
    today_iso: str,
    market_summary: str | None,
    macro_news_top: list[dict[str, Any]] | None,
) -> str:
    parts: list[str] = []
    parts.append(f"[Date] {today_iso}")
    if market_summary:
        parts.append(f"[Market summary] {market_summary[:500]}")
    parts.append("")
    if macro_news_top:
        parts.append("[Top macro news (most recent)]")
        for i, n in enumerate(macro_news_top[:15], 1):
            t = (n.get("title") or "")[:200]
            s = (n.get("summary") or "")[:200]
            src = n.get("source_name", "")
            parts.append(f"{i}. [{src}] {t}")
            if s and s != t:
                parts.append(f"   {s}")
    parts.append("")
    parts.append(
        "[Required Output] — 3 블록 (지수 및 RISK APPETITE / 금리·유동성 / 주도 테마 및 수급) 한국어 합성. "
        "1번 블록의 body 는 빈 string. 2번과 3번은 ~150자 한 단락."
    )
    return "\n".join(parts)


def _call_openai(prompt: str, model: str = "gpt-4o-mini") -> tuple[list[dict[str, Any]] | None, dict]:
    if not os.environ.get("OPENAI_API_KEY"):
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
                    "name": "market_env",
                    "schema": MARKET_ENV_SCHEMA,
                    "strict": True,
                },
            },
            temperature=0.4,
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
        return json.loads(text).get("blocks"), meta
    except Exception as e:
        log.warning("market_env LLM 호출 실패: %s", e)
        return None, {"error": str(e)}


def generate_market_env_blocks(
    conn, date_iso: str,
    *, market_summary: str | None = None,
    force: bool = False, max_age_hours: int = 18,
) -> list[dict[str, str]] | None:
    """매일 시장 환경 3 블록 자동 합성 + DB 저장 (macro_issues_auto 와 같은 테이블 패턴)."""
    # 캐시 hit
    if not force:
        try:
            row = conn.execute(
                "SELECT blocks_json, generated_at FROM market_env_auto WHERE date=?",
                (date_iso,),
            ).fetchone()
            if row:
                import datetime as _dt
                try:
                    gen = _dt.datetime.fromisoformat(row["generated_at"])
                    age = (_dt.datetime.now() - gen).total_seconds() / 3600.0
                    if age < max_age_hours:
                        return json.loads(row["blocks_json"])
                except Exception:
                    pass
        except Exception:
            pass

    log.info("market_env 3 블록 자동 생성 시작 (%s)", date_iso)

    # macro 뉴스 — Google News RSS (4 카테고리) 를 flatten 해서 컨텍스트로 사용
    macro_news: list[dict[str, Any]] = []
    try:
        from .macro_fetcher import fetch_overnight_news
        by_cat = fetch_overnight_news()
        for items in by_cat.values():
            macro_news.extend(items)
        macro_news.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        macro_news = macro_news[:15]
    except Exception as e:
        log.warning("market_env: macro 뉴스 fetch 실패: %s", e)

    prompt = _build_user_prompt(date_iso, market_summary, macro_news)
    blocks, meta = _call_openai(prompt)
    if not blocks or len(blocks) < 3:
        log.warning("market_env LLM 합성 실패 또는 3 블록 미만")
        return None

    # 강제 — block 1 의 title 을 우리 표준 ("지수 및 RISK APPETITE") 으로 정규화
    expected_titles = ["지수 및 RISK APPETITE", "금리·유동성", "주도 테마 및 수급"]
    for i, exp in enumerate(expected_titles):
        if i < len(blocks):
            blocks[i]["title"] = exp

    # block 1 의 body 는 항상 빈 string (자산 테이블 자동 렌더링)
    if len(blocks) >= 1:
        blocks[0]["body"] = ""

    # DB 저장
    try:
        conn.execute(
            """
            INSERT INTO market_env_auto (date, blocks_json, model_used,
                token_input, token_output, cost_estimate_usd, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                blocks_json = excluded.blocks_json,
                model_used = excluded.model_used,
                token_input = excluded.token_input,
                token_output = excluded.token_output,
                cost_estimate_usd = excluded.cost_estimate_usd,
                generated_at = excluded.generated_at
            """,
            (
                date_iso, json.dumps(blocks, ensure_ascii=False),
                meta.get("model", "gpt-4o-mini"),
                meta.get("token_input", 0), meta.get("token_output", 0),
                meta.get("cost_estimate_usd", 0.0),
                __import__("datetime").datetime.now().isoformat(),
            ),
        )
        conn.commit()
        log.info("market_env saved — cost ~$%.4f", meta.get("cost_estimate_usd", 0))
    except Exception as e:
        log.warning("market_env DB save 실패: %s", e)

    return blocks


def fetch_today_market_env(conn, date_iso: str) -> list[dict[str, str]] | None:
    """DB 의 오늘 market_env 조회 (없으면 None)."""
    try:
        row = conn.execute(
            "SELECT blocks_json FROM market_env_auto WHERE date=?",
            (date_iso,),
        ).fetchone()
        if row:
            return json.loads(row["blocks_json"])
    except Exception:
        pass
    return None
