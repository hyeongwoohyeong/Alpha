"""매크로·정책·지정학 뉴스 수집 — RSS 기반.

소스 (사용자 요구 2026-05-04: A + B 혼합):
    A. 미국 매체
        - Yahoo Finance "Top Stories"
        - MarketWatch "Top Stories"
    B. 한국 매체
        - 한국경제 (경제 헤드라인)
        - 매일경제 (경제 헤드라인)

이 함수는 RSS 만 fetch — LLM 합성은 macro_summarizer.py 에서.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .utils import get_logger

log = get_logger("macro_fetcher")


# ---------------------------------------------------------------------------
# RSS Source 설정
# ---------------------------------------------------------------------------

RSS_SOURCES: list[dict[str, str]] = [
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
        "lang": "en",
        "region": "US",
    },
    {
        "name": "MarketWatch",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "lang": "en",
        "region": "US",
    },
    {
        "name": "한국경제",
        "url": "https://www.hankyung.com/feed/economy",
        "lang": "ko",
        "region": "KR",
    },
    {
        "name": "매일경제",
        "url": "https://www.mk.co.kr/rss/30000001/",
        "lang": "ko",
        "region": "KR",
    },
]

# 매크로 키워드 — 헤드라인 / 요약에 이 중 하나라도 매칭되면 "매크로 관련" 으로 분류
MACRO_KEYWORDS: dict[str, list[str]] = {
    "interest_rate": [
        "fed", "fomc", "interest rate", "rate cut", "rate hike",
        "treasury", "yield", "10-year", "bond market",
        "연준", "금리", "기준금리", "국채", "수익률", "장단기",
    ],
    "inflation": [
        "inflation", "cpi", "ppi", "core inflation",
        "물가", "인플레이션", "소비자물가",
    ],
    "geopolitical": [
        "trump", "biden", "iran", "israel", "ukraine", "russia", "china",
        "taiwan", "geopolitical", "war", "conflict", "sanction",
        "트럼프", "바이든", "이란", "이스라엘", "우크라", "중국", "대만",
        "지정학", "전쟁", "분쟁", "제재", "포격", "충돌",
    ],
    "trade_tariff": [
        "tariff", "trade war", "trade deal", "export control", "import",
        "관세", "무역", "수출 통제", "수출규제", "통상",
    ],
    "energy_oil": [
        "oil price", "opec", "crude oil", "wti", "brent", "natural gas",
        "유가", "opec", "원유", "lng", "에너지",
    ],
    "ai_chip_policy": [
        "semiconductor", "chip act", "nvidia export", "ai chip",
        "반도체", "칩스법", "nvda 수출", "ai 칩", "장비 규제",
    ],
    "fiscal_policy": [
        "fiscal policy", "stimulus", "debt ceiling", "shutdown", "tax",
        "재정", "부양", "부채한도", "셧다운", "세제",
    ],
    "central_bank": [
        "ecb", "boj", "pboc", "bok", "central bank",
        "유럽중앙은행", "일본은행", "한국은행", "중앙은행",
    ],
    "market_volatility": [
        "vix", "volatility", "selloff", "rally", "correction",
        "변동성", "급락", "급등", "조정",
    ],
}


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


def _entry_to_dict(entry: Any, source: dict[str, str]) -> dict[str, Any]:
    """feedparser entry → 표준 dict."""
    title = (entry.get("title") or "").strip()
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    link = entry.get("link") or ""
    published_at = ""

    # published_parsed 가 가장 일관됨
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        try:
            dt = datetime(*pp[:6], tzinfo=timezone.utc)
            published_at = dt.isoformat()
        except Exception:
            pass

    if not published_at:
        # ISO 형식 string 시도
        for k in ("published", "updated"):
            v = entry.get(k)
            if v:
                published_at = str(v)
                break

    # HTML 태그 단순 strip (description 안에 종종 들어있음)
    summary_clean = _strip_html_basic(summary)

    return {
        "title": title,
        "summary": summary_clean[:500],
        "link": link,
        "published_at": published_at,
        "source_name": source.get("name", ""),
        "source_lang": source.get("lang", "en"),
        "source_region": source.get("region", ""),
    }


def _strip_html_basic(text: str) -> str:
    """간단한 HTML tag 제거."""
    import re
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_macro_relevant(item: dict[str, Any]) -> tuple[bool, list[str]]:
    """헤드라인 + 요약을 매크로 키워드와 매칭. 한국 매체는 모두 매크로로 가정."""
    if item.get("source_region") == "KR":
        # 한국 경제지의 경제 헤드라인은 기본적으로 매크로 / 시장 관련
        return True, ["korean_economic_news"]

    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    matched_categories: list[str] = []
    for cat, keywords in MACRO_KEYWORDS.items():
        if any(kw.lower() in text for kw in keywords):
            matched_categories.append(cat)
    return bool(matched_categories), matched_categories


def fetch_rss_source(
    source: dict[str, str], max_items: int = 30,
) -> list[dict[str, Any]]:
    """단일 RSS source fetch."""
    fp = _safe_feedparser()
    if fp is None:
        return []
    try:
        feed = fp.parse(source["url"])
    except Exception as e:
        log.warning("RSS fetch 실패 (%s): %s", source["name"], e)
        return []

    items: list[dict[str, Any]] = []
    for entry in (feed.entries or [])[:max_items]:
        try:
            items.append(_entry_to_dict(entry, source))
        except Exception:
            continue
    return items


def fetch_macro_news(
    *,
    max_per_source: int = 20,
    max_total: int = 60,
    only_macro: bool = True,
) -> list[dict[str, Any]]:
    """모든 RSS source 에서 매크로 뉴스 수집.

    Args:
        max_per_source: 각 source 당 최대 fetch 개수
        max_total: 합산 최대 개수 (LLM token 절감)
        only_macro: True 면 매크로 키워드 매칭된 것만 (한국 매체는 모두 통과)

    Returns: 매크로 관련 뉴스 list of dicts
    """
    all_items: list[dict[str, Any]] = []
    for source in RSS_SOURCES:
        items = fetch_rss_source(source, max_items=max_per_source)
        for item in items:
            if only_macro:
                is_macro, cats = _is_macro_relevant(item)
                if not is_macro:
                    continue
                item["macro_categories"] = cats
            all_items.append(item)
        # rate limit — 0.5 sec 간격 (RSS 서버 친절히)
        time.sleep(0.5)

    # 발행 시간 desc 정렬 (가장 최근 우선)
    def _key(it: dict) -> str:
        return it.get("published_at", "")
    all_items.sort(key=_key, reverse=True)

    log.info(
        "fetch_macro_news: %d items (sources=%d, only_macro=%s)",
        len(all_items[:max_total]), len(RSS_SOURCES), only_macro,
    )
    return all_items[:max_total]
