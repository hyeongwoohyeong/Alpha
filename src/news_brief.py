"""뉴스 fetcher + LLM 요약 — 브리핑용 top 3 "내 종목 영향" 추론.

Sources:
  - Google News RSS (한국어/영어 검색 query)
  - 무료, no auth, 빠름

LLM:
  - OpenAI gpt-4o-mini (입력 ~3000 / 출력 ~800 token, ~$0.001/회)
  - graceful: API key 없으면 fetch 한 뉴스 제목만 반환

Output:
  list of {
    "rank": int, "title": str, "impact": str,
    "tickers_affected": [str], "source_url": str
  }
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.parse
from typing import Any

from .utils import get_logger

log = get_logger("news_brief")

# 검색 queries — 사용자 포트폴리오 + 매크로 중심
_QUERIES = [
    "SK하이닉스",
    "엔비디아 NVIDIA",
    "Fed FOMC 금리",
    "반도체 HBM",
    "테슬라",
    "비트코인 BTC halving",
]

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

# OpenAI 가격
_PRICE_IN = 0.15  # gpt-4o-mini /1M
_PRICE_OUT = 0.6


def _fetch_rss(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Google News RSS — 단일 query 최근 N개."""
    import requests
    url = _GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(query))
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        body = r.text
    except Exception as e:
        log.debug("RSS fetch %s 실패: %s", query, e)
        return []
    # 매우 단순한 RSS 파싱 — feedparser 의존성 피함
    items = []
    item_pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
    title_pattern = re.compile(r"<title>(.*?)</title>", re.DOTALL)
    link_pattern = re.compile(r"<link>(.*?)</link>", re.DOTALL)
    date_pattern = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL)
    for m in item_pattern.findall(body)[:limit]:
        t = title_pattern.search(m)
        l = link_pattern.search(m)
        d = date_pattern.search(m)
        if not t:
            continue
        items.append({
            "title": (t.group(1) if t else "").strip(),
            "link": (l.group(1) if l else "").strip(),
            "pub_date": (d.group(1) if d else "").strip(),
            "query": query,
        })
    return items


def _fetch_all_news(per_query: int = 4) -> list[dict[str, str]]:
    """모든 query 의 뉴스 합쳐서 dedup."""
    seen = set()
    all_items = []
    for q in _QUERIES:
        for it in _fetch_rss(q, limit=per_query):
            key = it["title"][:80]
            if key in seen:
                continue
            seen.add(key)
            all_items.append(it)
    return all_items


def _build_llm_prompt(news_items: list[dict], holdings_tickers: list[str]) -> str:
    """gpt-4o-mini 에게 줄 prompt — top 3 영향 큰 뉴스 추론."""
    tickers_str = ", ".join(holdings_tickers[:15])
    items_text = "\n".join([
        f"{i+1}. [{it.get('query','')}] {it['title']}"
        for i, it in enumerate(news_items[:25])
    ])
    return f"""너는 한국 개인 투자자의 일일 뉴스 큐레이터다.

[사용자 보유·관심 종목]
{tickers_str}

[최근 24h 뉴스 헤드라인 25개]
{items_text}

[작업]
위 뉴스 중 *사용자 보유·관심 종목에 직접 영향* 줄 가능성 높은 3개를 골라 JSON 으로 반환.
각 뉴스에 대해:
  - "rank": 1~3
  - "headline": 원문 헤드라인을 30자 내 한국어로 요약 (구체 사실 1가지)
  - "impact": "→ {{종목}} 단기 ↑/↓ ({{한 줄 이유}})" 형식 — 25자 내
  - "tickers": 영향 받는 ticker 리스트 (위 보유 종목 중)

JSON schema:
{{
  "items": [
    {{"rank": 1, "headline": "...", "impact": "...", "tickers": ["SOXL", ...]}},
    ...
  ]
}}

3개 미만이면 있는 만큼만. 광고/낚시 헤드라인은 제외.
"""


def _call_llm(prompt: str, model: str = "gpt-4o-mini") -> dict | None:
    """OpenAI 호출 — auto_curation.py 와 동일 패턴."""
    if not os.environ.get("OPENAI_API_KEY"):
        log.info("OPENAI_API_KEY 없음 — LLM 요약 skip")
        return None
    try:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content":
                 "너는 한국 개인 투자자의 뉴스 큐레이터. JSON 만 출력."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        text = resp.choices[0].message.content
        usage = resp.usage
        cost = (
            int(getattr(usage, "prompt_tokens", 0) or 0) / 1e6 * _PRICE_IN
            + int(getattr(usage, "completion_tokens", 0) or 0) / 1e6 * _PRICE_OUT
        )
        log.info("LLM 호출: input=%s out=%s cost=$%.4f",
                 usage.prompt_tokens, usage.completion_tokens, cost)
        return json.loads(text)
    except Exception as e:
        log.warning("LLM 호출 실패: %s", e)
        return None


def get_news_for_briefing() -> list[dict]:
    """브리핑용 top 3 뉴스 + 영향 요약. 실패 시 빈 list."""
    news = _fetch_all_news()
    if not news:
        log.info("뉴스 fetch 실패 또는 비어있음")
        return []
    # 보유 종목 ticker 추출
    try:
        from pathlib import Path
        portfolio = json.load(
            open(Path(__file__).resolve().parents[1] / "data" / "portfolio.json", encoding="utf-8")
        )
        tickers = [h.get("ticker", "") for h in portfolio.get("holdings", []) if h.get("ticker")]
    except Exception:
        tickers = []
    tickers = list(set(tickers))[:15] or ["KODEX_HYNIX_2X", "SOXL", "TQQQ", "QQQ"]
    prompt = _build_llm_prompt(news, tickers)
    out = _call_llm(prompt)
    if not out or "items" not in out:
        # LLM 실패 시 헤드라인만 fallback
        return [
            {"rank": i + 1, "headline": it["title"][:60], "impact": "",
             "tickers": [], "source_url": it.get("link", "")}
            for i, it in enumerate(news[:3])
        ]
    items = out.get("items", [])[:3]
    # url 보강
    for it in items:
        it["source_url"] = ""  # LLM 이 url 못 보존 — 향후 매칭 가능
    return items


def format_news_section(items: list[dict]) -> list[str]:
    """브리핑 메시지 안에 들어갈 줄 list."""
    if not items:
        return []
    lines = ["📰 주요 뉴스 (내 종목 영향)"]
    for it in items:
        rank = it.get("rank", "?")
        h = it.get("headline", "")
        impact = it.get("impact", "")
        lines.append(f"{rank}. {h}")
        if impact:
            lines.append(f"   {impact}")
    return lines


if __name__ == "__main__":
    # python -m src.news_brief — 수동 테스트
    items = get_news_for_briefing()
    print(json.dumps(items, indent=2, ensure_ascii=False))
    print()
    print("\n".join(format_news_section(items)))
