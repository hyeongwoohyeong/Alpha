"""금일 시장 환경 3 블록 + 금일 핵심 판단 자동 생성 — 시장 데이터 + 매크로 뉴스 → LLM.

시장 환경 3 블록:
    1. 지수 및 Risk Appetite — 자산 테이블이 자동 표시되므로 본문은 없음.
    2. 금리·유동성 — 채권 / Fed 정책 / 유동성 한 단락
    3. 주도 테마 및 수급 — 그날 자금이 몰리는/회피되는 카테고리 한 단락

금일 핵심 판단:
    그날의 top picks + 시장 데이터를 근거로 한 3~4 문장 LLM 합성 (기존 룰 기반
    6문단 템플릿 대체).

설계 (2026-05-22): 기존 블록은 "시장 환경 묘사" 프롬프트라 매일 비슷한 상시
묘사체가 나왔다. 이제 실제 자산 수익률(SPY/QQQ/IWM/TLT/GLD/USO/BTC 의 1D·1M)을
프롬프트에 넣고, 블록이 반드시 그 수치를 인용하도록 강제 → 매일 눈에 띄게 달라짐.

비용: 하루 한 번, market_env + daily_judgment 합쳐 ~$0.003.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("market_env_summarizer")


PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60


# ---------------------------------------------------------------------------
# 공통 — proxies(자산 테이블) 텍스트 포맷
# ---------------------------------------------------------------------------

_PROXY_LABELS: list[tuple[str, str]] = [
    ("SPY", "S&P500"),
    ("QQQ", "나스닥100"),
    ("IWM", "러셀2000(소형주)"),
    ("TLT", "미국 장기국채(TLT) — 상승=장기금리 하락"),
    ("GLD", "금"),
    ("USO", "원유"),
    ("BTC-USD", "비트코인"),
]


def _pct(v: Any) -> str:
    try:
        f = float(v) * 100
        return f"{f:+.2f}%"
    except Exception:
        return "N/A"


def format_proxies(proxies: dict[str, Any] | None) -> str:
    """proxies dict → LLM 프롬프트용 자산 수익률 텍스트."""
    if not proxies:
        return "(자산 시세 데이터 없음)"
    lines: list[str] = []
    for sym, label in _PROXY_LABELS:
        d = (proxies or {}).get(sym) or {}
        if not d.get("available"):
            continue
        lines.append(
            f"- {label} ({sym}): 1일 {_pct(d.get('daily_return'))}, "
            f"1개월 {_pct(d.get('1m_return'))}"
        )
    return "\n".join(lines) if lines else "(자산 시세 데이터 없음)"


# ===========================================================================
# Part A — 금일 시장 환경 3 블록
# ===========================================================================

SYSTEM_PROMPT = """You are a Korean equity market strategist writing the daily
"market environment" briefing — 3 short blocks for a Korean retail investor in
US equities.

Block titles are fixed:
    1. "지수 및 RISK APPETITE" — body 는 자동 자산 테이블이라 빈 string.
    2. "금리·유동성" — 한 단락 (~150자 한국어)
    3. "주도 테마 및 수급" — 한 단락 (~150자 한국어)

CRITICAL — 매일 똑같이 읽히는 상시 묘사체 금지:
- 각 블록은 제공된 [오늘의 자산 수익률] 의 **구체적 숫자를 최소 1개 이상 직접 인용**해야 한다.
  예: "TLT가 1일 +0.4% 오르며 장기금리가 소폭 하락" / "러셀2000(+0.9%)이 나스닥(+0.2%)을
  앞서며 소형주로 수급이 확산".
- "금리·유동성": TLT 의 방향으로 장기금리 흐름을 읽고, 매크로 뉴스의 Fed/금리 관련
  사건이 있으면 함께 언급.
- "주도 테마 및 수급": 지수 간 상대 성과(나스닥 vs 러셀 vs S&P), GLD/USO/BTC 의 위험
  선호 신호, 매크로 뉴스의 섹터 단서를 근거로 그날 어디로 자금이 쏠리는지 서술.
- 숫자 없이 일반론만 쓰지 말 것.

Output strictly JSON matching the schema. All Korean natural / fluent.
Tone: 신중한 시장 전략가 — 단정적 매수/매도 추천·예측 금지. 사실·수치·점검 사항 중심.
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


def _build_env_prompt(
    today_iso: str,
    proxies: dict[str, Any] | None,
    market_summary: str | None,
    macro_news_top: list[dict[str, Any]] | None,
) -> str:
    parts: list[str] = [f"[Date] {today_iso}", ""]
    parts.append("[오늘의 자산 수익률] — 블록 본문에 반드시 인용")
    parts.append(format_proxies(proxies))
    parts.append("")
    if market_summary:
        parts.append(f"[Market summary] {market_summary[:400]}")
        parts.append("")
    if macro_news_top:
        parts.append("[Top macro news (most recent)]")
        for i, n in enumerate(macro_news_top[:15], 1):
            t = (n.get("title") or "")[:180]
            src = n.get("source_name", "")
            parts.append(f"{i}. [{src}] {t}")
        parts.append("")
    parts.append(
        "[Required Output] — 3 블록 (지수 및 RISK APPETITE / 금리·유동성 / 주도 테마 및 수급) "
        "한국어 합성. 1번 body 는 빈 string. 2·3번은 위 자산 수익률의 구체 숫자를 인용한 "
        "~150자 한 단락. 숫자 없는 일반론 금지."
    )
    return "\n".join(parts)


def _call_openai(
    prompt: str, system: str, schema: dict, schema_name: str,
    model: str = "gpt-4o-mini",
) -> tuple[Any, dict]:
    if not os.environ.get("OPENAI_API_KEY"):
        return None, {"error": "no_api_key"}
    try:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
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
        return json.loads(text), meta
    except Exception as e:
        log.warning("LLM 호출 실패 (%s): %s", schema_name, e)
        return None, {"error": str(e)}


def generate_market_env_blocks(
    conn, date_iso: str,
    *, proxies: dict[str, Any] | None = None,
    market_summary: str | None = None,
    force: bool = False, max_age_hours: int = 18,
) -> list[dict[str, str]] | None:
    """매일 시장 환경 3 블록 자동 합성 + DB 저장."""
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

    macro_news = []
    try:
        from .macro_fetcher import fetch_overnight_news
        by_cat = fetch_overnight_news()
        for items in by_cat.values():
            macro_news.extend(items)
        macro_news.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        macro_news = macro_news[:15]
    except Exception as e:
        log.warning("market_env: macro 뉴스 fetch 실패: %s", e)

    prompt = _build_env_prompt(date_iso, proxies, market_summary, macro_news)
    parsed, meta = _call_openai(
        prompt, SYSTEM_PROMPT, MARKET_ENV_SCHEMA, "market_env",
    )
    blocks = parsed.get("blocks") if isinstance(parsed, dict) else None
    if not blocks or len(blocks) < 3:
        log.warning("market_env LLM 합성 실패 또는 3 블록 미만")
        return None

    expected_titles = ["지수 및 RISK APPETITE", "금리·유동성", "주도 테마 및 수급"]
    for i, exp in enumerate(expected_titles):
        if i < len(blocks):
            blocks[i]["title"] = exp
    if len(blocks) >= 1:
        blocks[0]["body"] = ""

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


# ===========================================================================
# Part B — 금일 핵심 판단 (룰 기반 6문단 템플릿 대체)
# ===========================================================================

JUDGMENT_SYSTEM_PROMPT = """You write the "금일 핵심 판단" — a 3~4 sentence Korean
paragraph for a US-equity retail investor, summarizing what today's research run
surfaced and where the investor should focus attention.

INPUTS you receive: today's top picks (with Action Tag and category) and today's
asset returns.

RULES:
- 3~4 자연스러운 한국어 문장. 그날의 실제 picks 와 시장 데이터를 반영 — 매일 다르게.
- Action Tag 의미: "Quality Dislocation"=우량주 과매도 후보, "Research Now"=논리·뉴스가
  동시에 우호적인 후보, "Watchlist"=관찰, "Avoid"=회피 신호.
- 무엇에 시간·관심을 배분할지 관점을 제시 (예: thesis 재확인 / catalyst 모니터링 /
  anti-thesis 점검).
- 단정적 매수/매도 추천, 목표주가, 상승여력, 주가 예측 어휘 금지.
- 구체적으로: 오늘 picks 의 종목명이나 Action Tag 구성, 시장 수익률 흐름을 근거로 언급.

Output strictly JSON matching the schema.
"""

JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "judgment": {"type": "string", "description": "3~4 문장 한국어 핵심 판단"},
    },
    "required": ["judgment"],
    "additionalProperties": False,
}


def _format_picks(picks: list[dict[str, Any]] | None) -> str:
    """picks → LLM 프롬프트용 텍스트."""
    if not picks:
        return "(금일 부각된 top picks 없음)"
    lines: list[str] = []
    for i, p in enumerate(picks[:6], 1):
        ticker = p.get("ticker", "")
        name = p.get("name_ko") or p.get("name_en") or ""
        tag = p.get("action_tag", "")
        ctype = p.get("company_type") or ""
        extra = f", 유형: {ctype}" if ctype else ""
        lines.append(f"{i}. {ticker} {name} — Action Tag: {tag}{extra}")
    return "\n".join(lines)


def _build_judgment_prompt(
    today_iso: str,
    picks: list[dict[str, Any]] | None,
    proxies: dict[str, Any] | None,
    avoid_count: int = 0,
) -> str:
    parts: list[str] = [f"[Date] {today_iso}", ""]
    parts.append("[금일 Top Picks]")
    parts.append(_format_picks(picks))
    parts.append(f"\n[금일 회피(Avoid) 신호 종목 수] {avoid_count}")
    parts.append("")
    parts.append("[오늘의 자산 수익률]")
    parts.append(format_proxies(proxies))
    parts.append("")
    parts.append(
        "[Required Output] — 위 picks 구성과 시장 수익률을 근거로 '금일 핵심 판단' "
        "3~4 문장을 한국어로 합성. 매매·목표가·예측 어휘 금지."
    )
    return "\n".join(parts)


def generate_daily_judgment(
    conn, date_iso: str,
    picks: list[dict[str, Any]] | None,
    proxies: dict[str, Any] | None = None,
    *, avoid_count: int = 0, force: bool = False, max_age_hours: int = 18,
) -> str | None:
    """금일 핵심 판단 LLM 합성 + DB 저장. 실패 시 None (호출측이 룰 fallback)."""
    if not force:
        try:
            row = conn.execute(
                "SELECT judgment, generated_at FROM daily_judgment_auto WHERE date=?",
                (date_iso,),
            ).fetchone()
            if row:
                import datetime as _dt
                try:
                    gen = _dt.datetime.fromisoformat(row["generated_at"])
                    age = (_dt.datetime.now() - gen).total_seconds() / 3600.0
                    if age < max_age_hours:
                        return row["judgment"]
                except Exception:
                    pass
        except Exception:
            pass

    log.info("금일 핵심 판단 LLM 합성 시작 (%s)", date_iso)
    prompt = _build_judgment_prompt(date_iso, picks, proxies, avoid_count)
    parsed, meta = _call_openai(
        prompt, JUDGMENT_SYSTEM_PROMPT, JUDGMENT_SCHEMA, "daily_judgment",
    )
    judgment = parsed.get("judgment") if isinstance(parsed, dict) else None
    if not judgment or not str(judgment).strip():
        log.warning("daily_judgment LLM 합성 실패")
        return None
    judgment = str(judgment).strip()

    try:
        conn.execute(
            """
            INSERT INTO daily_judgment_auto (date, judgment, model_used,
                token_input, token_output, cost_estimate_usd, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                judgment = excluded.judgment,
                model_used = excluded.model_used,
                token_input = excluded.token_input,
                token_output = excluded.token_output,
                cost_estimate_usd = excluded.cost_estimate_usd,
                generated_at = excluded.generated_at
            """,
            (
                date_iso, judgment,
                meta.get("model", "gpt-4o-mini"),
                meta.get("token_input", 0), meta.get("token_output", 0),
                meta.get("cost_estimate_usd", 0.0),
                __import__("datetime").datetime.now().isoformat(),
            ),
        )
        conn.commit()
        log.info("daily_judgment saved — cost ~$%.4f", meta.get("cost_estimate_usd", 0))
    except Exception as e:
        log.warning("daily_judgment DB save 실패: %s", e)

    return judgment


def fetch_today_daily_judgment(conn, date_iso: str) -> str | None:
    """DB 의 오늘 금일 핵심 판단 조회 (없으면 None)."""
    try:
        row = conn.execute(
            "SELECT judgment FROM daily_judgment_auto WHERE date=?",
            (date_iso,),
        ).fetchone()
        if row:
            return row["judgment"]
    except Exception:
        pass
    return None
