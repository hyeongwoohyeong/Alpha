"""실제 뉴스 수집.

Google News RSS를 1차 소스로, 실패 시 yfinance .news를 fallback으로 사용한다.
크롤링이 실패해도 앱이 죽지 않도록 격리한다.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
import urllib.parse
from typing import Any, Iterable

from .utils import get_logger

log = get_logger("news")

# ---------------------------------------------------------------------------
# 키워드 → 중요도 점수
# ---------------------------------------------------------------------------

POSITIVE_KEYWORDS: dict[str, float] = {
    "beats": 1.0,
    "beat": 1.0,
    "raises guidance": 1.5,
    "raise guidance": 1.5,
    "raised guidance": 1.5,
    "record": 0.8,
    "all-time high": 0.8,
    "upgrade": 1.0,
    "buy rating": 0.8,
    "outperform": 0.8,
    "contract": 0.7,
    "wins contract": 1.0,
    "awarded": 0.7,
    "order": 0.6,
    "backlog": 0.7,
    "partnership": 0.7,
    "approval": 0.8,
    "fda approval": 1.2,
    "ai": 0.4,
    "data center": 0.5,
    "expansion": 0.5,
    "launch": 0.4,
    "acquisition": 0.6,
    "acquires": 0.6,
}

NEGATIVE_KEYWORDS: dict[str, float] = {
    "miss": -1.0,
    "misses": -1.0,
    "cuts guidance": -1.7,
    "lowers guidance": -1.5,
    "downgrade": -1.0,
    "sell rating": -0.8,
    "underperform": -0.8,
    "lawsuit": -1.0,
    "investigation": -1.5,
    "fraud": -2.5,
    "accounting": -1.5,
    "restatement": -1.7,
    "subpoena": -2.0,
    "dilution": -1.0,
    "secondary offering": -0.8,
    "bankruptcy": -3.0,
    "going concern": -2.5,
    "cash burn": -1.0,
    "recall": -1.0,
    "delay": -0.5,
    "halt": -0.7,
    "warning": -0.8,
    "probe": -1.2,
    "antitrust": -0.8,
    "tariff": -0.4,
}

URGENT_KEYWORDS: set[str] = {
    "fraud", "investigation", "subpoena", "bankruptcy", "going concern",
    "restatement", "halt", "recall", "probe", "lawsuit", "cuts guidance",
    "lowers guidance", "secondary offering", "dilution", "going-private",
}


def _kw_score(text: str) -> tuple[float, list[str]]:
    """텍스트에서 키워드 매칭으로 중요도 점수와 hit list 반환."""
    if not text:
        return 0.0, []
    s = text.lower()
    score = 0.0
    hits: list[str] = []
    for kw, w in POSITIVE_KEYWORDS.items():
        if kw in s:
            score += w
            hits.append(f"+{kw}")
    for kw, w in NEGATIVE_KEYWORDS.items():
        if kw in s:
            score += w  # w는 음수
            hits.append(kw)
    return score, hits


# ---------------------------------------------------------------------------
# Google News RSS
# ---------------------------------------------------------------------------

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _safe_feedparser():
    try:
        import feedparser  # type: ignore
        return feedparser
    except Exception as e:
        log.error("feedparser import 실패: %s", e)
        return None


def _parse_published(entry: Any) -> str | None:
    for key in ("published", "updated", "pubDate"):
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            return str(v)
    pp = getattr(entry, "published_parsed", None)
    if pp:
        try:
            return _dt.datetime(*pp[:6]).isoformat()
        except Exception:
            pass
    return None


def _entry_to_news(entry: Any, ticker: str | None, category: str | None) -> dict[str, Any]:
    title = (
        getattr(entry, "title", None)
        or (entry.get("title") if isinstance(entry, dict) else None)
        or ""
    )
    link = (
        getattr(entry, "link", None)
        or (entry.get("link") if isinstance(entry, dict) else None)
        or ""
    )
    source = ""
    src = getattr(entry, "source", None)
    if src:
        # feedparser source는 dict-like 또는 str
        if hasattr(src, "title"):
            source = src.title or ""
        elif isinstance(src, dict):
            source = src.get("title", "")
    if not source:
        # 제목 끝에 ' - SOURCE' 패턴이 자주 있음
        m = re.search(r" - ([^-]+)$", title)
        if m:
            source = m.group(1).strip()
    summary = (
        getattr(entry, "summary", None)
        or (entry.get("summary") if isinstance(entry, dict) else None)
        or ""
    )
    # HTML tags 제거
    summary = re.sub(r"<[^>]+>", " ", summary).strip()
    if len(summary) > 500:
        summary = summary[:500] + "…"

    score, hits = _kw_score(title + " " + summary)
    return {
        "ticker": ticker,
        "title": title.strip(),
        "source": source.strip(),
        "published_at": _parse_published(entry),
        "link": link.strip(),
        "summary": summary,
        "category": category,
        "importance_score": round(score, 3),
        "kw_hits": hits,
    }


def _google_news(query: str, limit: int = 5) -> list[dict[str, Any]]:
    fp = _safe_feedparser()
    if fp is None:
        return []
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query))
    try:
        feed = fp.parse(url)
    except Exception as e:
        log.warning("RSS 파싱 실패 (%s): %s", query, e)
        return []
    entries = getattr(feed, "entries", []) or []
    out: list[dict[str, Any]] = []
    for e in entries[:limit]:
        try:
            out.append(_entry_to_news(e, ticker=None, category=None))
        except Exception as ex:
            log.debug("entry 파싱 실패: %s", ex)
            continue
    return out


def _yf_news(ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return []
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        log.debug("[%s] yf.news 실패: %s", ticker, e)
        return []

    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        try:
            title = it.get("title") or ""
            link = it.get("link") or ""
            source = it.get("publisher") or ""
            ts = it.get("providerPublishTime")
            published = (
                _dt.datetime.fromtimestamp(int(ts)).isoformat() if ts else None
            )
            score, hits = _kw_score(title)
            out.append(
                {
                    "ticker": ticker,
                    "title": title,
                    "source": source,
                    "published_at": published,
                    "link": link,
                    "summary": "",
                    "category": "ticker",
                    "importance_score": round(score, 3),
                    "kw_hits": hits,
                }
            )
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_ticker_news(ticker: str, name_en: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """단일 종목의 최신 뉴스. 1차 Google News, 0건이면 yfinance fallback."""
    query_parts = [ticker]
    if name_en:
        query_parts.append(f'"{name_en}"')
    query = " OR ".join(query_parts) + " stock"
    items = _google_news(query, limit=limit)
    for it in items:
        it["ticker"] = ticker
        it["category"] = "ticker"
    if items:
        return items
    return _yf_news(ticker, limit=limit)


def fetch_theme_news(theme: str, limit: int = 5) -> list[dict[str, Any]]:
    """테마별 macro/산업 뉴스."""
    queries = THEME_QUERIES.get(theme) or [theme.replace("_", " ")]
    out: list[dict[str, Any]] = []
    for q in queries:
        items = _google_news(q, limit=limit)
        for it in items:
            it["category"] = f"theme:{theme}"
        out.extend(items)
        # 너무 많이 호출하지 않도록 약간 sleep
        time.sleep(0.05)
    # 중복 제거(title 기준)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for it in out:
        key = it.get("title", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    deduped.sort(key=lambda x: x.get("importance_score", 0), reverse=True)
    return deduped[:limit]


THEME_QUERIES: dict[str, list[str]] = {
    "ai_semiconductor": ["AI chip demand", "GPU data center", "TSMC capex"],
    "ai_networking": ["AI data center networking", "ethernet AI cluster"],
    "data_center_power": [
        "AI data center power demand",
        "nuclear power data center",
        "grid capacity AI",
    ],
    "public_safety": ["public safety AI", "police body camera AI"],
    "defense": ["US defense spending", "Pentagon contract"],
    "space": ["commercial space launch", "lunar lander NASA"],
    "healthcare_infra": ["organ transplant outcomes", "robotic surgery", "diagnostics platform"],
    "platform": ["big tech earnings", "streaming subscriber growth"],
    "ecommerce_platform": ["ecommerce GMV", "merchant solutions"],
    "travel_mobility": ["travel demand outlook", "robotaxi rollout"],
    "mobility_consumer": ["robotaxi Tesla", "EV demand outlook"],
    "consumer_brand": ["consumer spending US"],
}


def aggregate_importance(news_list: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """뉴스 리스트에서 종합 신호.

    - urgent 키워드는 staleness 가 outdated 면 무시 (오래된 위험 키워드가 지금까지
      Action Tag 에 영향 주지 않도록).
    - confidence 가 Low 인 뉴스는 importance score 가중치 절반.
    """
    from .event_processor import enrich_news, is_urgent_risk

    items = [enrich_news(i) for i in (news_list or [])]
    if not items:
        return {
            "count": 0,
            "score_sum": 0.0,
            "negative": False,
            "urgent": False,
            "top_titles": [],
            "fresh_count": 0,
            "outdated_count": 0,
        }

    score = 0.0
    fresh_count = 0
    outdated_count = 0
    for it in items:
        s = it.get("importance_score", 0) or 0
        if it.get("confidence") == "Low":
            s *= 0.5
        score += s
        if it.get("staleness") in ("fresh", "aging"):
            fresh_count += 1
        if it.get("staleness") == "outdated":
            outdated_count += 1

    # urgent: 다음 조건을 모두 만족할 때만 (엄격 강화)
    #   1) is_urgent (urgent risk 키워드 포함)
    #   2) staleness != outdated (90일 이내)
    #   3) event_status not in (종료, 완료)
    #   4) source_quality >= Medium (블로그/SEO 사이트 단독은 무시)
    urgent = any(
        it.get("is_urgent")
        and it.get("staleness") != "outdated"
        and it.get("event_status") not in ("종료", "완료")
        and it.get("source_quality") in ("High", "Medium")
        for it in items
    )
    neg = score < -0.5
    top_titles = [i.get("title", "") for i in items[:3]]
    return {
        "count": len(items),
        "score_sum": round(score, 3),
        "negative": neg,
        "urgent": urgent,
        "top_titles": top_titles,
        "fresh_count": fresh_count,
        "outdated_count": outdated_count,
    }
