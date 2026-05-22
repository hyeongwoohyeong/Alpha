"""전날 글로벌 이벤트 수집 — Google News RSS 기반.

기존 Yahoo Finance / MarketWatch RSS 피드가 폐기·차단되어 매일 동일 fallback 이
노출되던 문제를 해결 (2026-05-22). news_fetcher.py 가 이미 사용 중인 Google News
RSS (검증된 소스) 로 전면 교체.

4 카테고리별 전용 검색 쿼리 + `when:1d` 시간 필터로 "어제 발생한 사건" 만 수집:
    geopolitics — 지정학·전쟁
    earnings    — 주요 기업 실적·이벤트
    policy      — 정책·트럼프 발언
    market      — 시장·매크로 지표

이 모듈은 RSS fetch 만 담당 — LLM 합성은 overnight_briefing.py 에서.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime
from typing import Any

from .utils import get_logger

log = get_logger("macro_fetcher")


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


# ---------------------------------------------------------------------------
# 카테고리별 검색 쿼리
# ---------------------------------------------------------------------------
# 각 카테고리는 여러 쿼리로 분산 수집 — 한 쿼리에 OR 를 너무 많이 넣으면
# Google News 가 결과를 좁히는 경향이 있어 2~3개 쿼리로 나눔.
# `when:1d` = 최근 24시간 (전날 사건 위주).

BRIEFING_CATEGORIES: list[dict[str, Any]] = [
    {
        "key": "geopolitics",
        "label": "지정학·전쟁",
        "queries": [
            "Israel OR Iran OR Gaza OR Lebanon when:1d",
            "Ukraine OR Russia OR ceasefire OR airstrike when:1d",
            "China Taiwan OR North Korea OR sanctions OR military when:1d",
        ],
    },
    {
        "key": "earnings",
        "label": "주요 기업 실적·이벤트",
        "queries": [
            "earnings results when:1d stock",
            "guidance OR forecast OR merger OR acquisition when:1d company",
            "Nvidia OR Apple OR Microsoft OR Tesla OR Amazon when:1d",
        ],
    },
    {
        "key": "policy",
        "label": "정책·트럼프 발언",
        "queries": [
            "Trump when:1d",
            "Federal Reserve OR Fed OR FOMC OR rate cut when:1d",
            "tariff OR trade OR executive order OR regulation when:1d",
        ],
    },
    {
        "key": "market",
        "label": "시장·매크로 지표",
        "queries": [
            "stock market OR Nasdaq OR S&P 500 OR Dow Jones when:1d",
            "oil price OR Treasury yield OR dollar OR gold when:1d",
            "inflation OR CPI OR jobs report OR GDP when:1d",
        ],
    },
]

# 카테고리당 LLM 으로 넘길 최대 헤드라인 수
MAX_ITEMS_PER_CATEGORY = 18
# 쿼리당 fetch 상한
MAX_ITEMS_PER_QUERY = 12


# ---------------------------------------------------------------------------
# RSS Fetch helpers
# ---------------------------------------------------------------------------

def _safe_feedparser():
    try:
        import feedparser  # type: ignore
        return feedparser
    except Exception as e:
        log.error("feedparser import 실패: %s", e)
        return None


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_published(entry: Any) -> str:
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        try:
            return datetime(*pp[:6]).isoformat()
        except Exception:
            pass
    for k in ("published", "updated"):
        v = entry.get(k)
        if v:
            return str(v)
    return ""


def _extract_source(entry: Any, title: str) -> str:
    src = entry.get("source")
    if src:
        if hasattr(src, "get"):
            return src.get("title", "") or ""
        if hasattr(src, "title"):
            return src.title or ""
    # Google News 제목은 보통 "... - SOURCE" 형태
    m = re.search(r" - ([^-]+)$", title)
    return m.group(1).strip() if m else ""


def _entry_to_dict(entry: Any, category: dict[str, str]) -> dict[str, Any]:
    title = (entry.get("title") or "").strip()
    summary = _strip_html(entry.get("summary") or entry.get("description") or "")
    return {
        "title": title,
        "summary": summary[:400],
        "link": entry.get("link") or "",
        "published_at": _parse_published(entry),
        "source_name": _extract_source(entry, title),
        "category_key": category["key"],
        "category_label": category["label"],
    }


def _fetch_query(query: str, category: dict[str, str], limit: int) -> list[dict[str, Any]]:
    fp = _safe_feedparser()
    if fp is None:
        return []
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query))
    try:
        feed = fp.parse(url)
    except Exception as e:
        log.warning("RSS fetch 실패 (%s): %s", query, e)
        return []
    out: list[dict[str, Any]] = []
    for entry in (feed.entries or [])[:limit]:
        try:
            d = _entry_to_dict(entry, category)
            if d["title"]:
                out.append(d)
        except Exception:
            continue
    return out


def fetch_overnight_news() -> dict[str, list[dict[str, Any]]]:
    """4 카테고리별 전날 글로벌 뉴스 수집.

    Returns: {category_key: [news_item, ...]} — 카테고리당 최대 MAX_ITEMS_PER_CATEGORY.
    각 item 은 title / summary / link / published_at / source_name /
    category_key / category_label 키를 가짐.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for cat in BRIEFING_CATEGORIES:
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for q in cat["queries"]:
            items = _fetch_query(q, cat, MAX_ITEMS_PER_QUERY)
            for it in items:
                key = it["title"].strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    collected.append(it)
            time.sleep(0.3)  # RSS 서버 부하 완화
        # 발행 시간 desc 정렬 후 상한
        collected.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        result[cat["key"]] = collected[:MAX_ITEMS_PER_CATEGORY]
        log.info("fetch_overnight_news[%s]: %d items", cat["key"], len(result[cat["key"]]))
    return result


def total_news_count(news_by_cat: dict[str, list[dict[str, Any]]]) -> int:
    return sum(len(v) for v in (news_by_cat or {}).values())
