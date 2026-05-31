"""Master Universe Loader — 모든 universe csv 의 single source of truth.

기존 6 파일을 dedup + tag 부여하여 단일 view 제공:
  - universe.csv               (core 50, manual curated, 자세한 분석 대상)
  - wide_universe.csv          (manual curated US, ~351)
  - kr_universe.csv            (KR quality 필터 통과, 23)
  - kr_momentum_universe.csv   (KR 모멘텀 종목 manual, 40)
  - kr_dynamic_universe.csv    (KR fdr daily rebuild, ~2000)
  - us_dynamic_universe.csv    (US fdr daily rebuild, ~5700)

사용:
    from src.master_universe import load_master_universe
    rows = load_master_universe()
    # 각 row: {ticker, name, market, market_cap_tier, sources[], tags[], industry, ...}

Tags 의미:
    core           — universe.csv 의 fully scored alpha 대상
    wide_curated   — wide_universe.csv 의 manual sector tag
    kr_quality     — pykrx quality 필터 통과
    kr_momentum    — manual momentum (한미반도체 등)
    dynamic        — fdr 매일 rebuild 된 살아있는 종목
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Universe csv 경로 + tag
_SOURCES: list[tuple[str, str, str]] = [
    # (file_name, source_tag, market_default)
    ("universe.csv",              "core",         "US"),
    ("wide_universe.csv",         "wide_curated", "US"),
    ("kr_universe.csv",           "kr_quality",   "KR"),
    ("kr_momentum_universe.csv",  "kr_momentum",  "KR"),
    ("kr_dynamic_universe.csv",   "kr_dynamic",   "KR"),
    ("us_dynamic_universe.csv",   "us_dynamic",   "US"),
]


def _read_csv_skip_comments(path: Path) -> list[dict[str, str]]:
    """주석 (#) 줄 skip 후 dict reader."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    return list(csv.DictReader(lines))


def _normalize_row(row: dict, source_tag: str, market_default: str) -> dict[str, Any]:
    """각 csv 의 다양한 컬럼명 표준화."""
    # ticker
    ticker = (row.get("ticker") or row.get("Code") or "").strip()
    if not ticker:
        return None
    # name
    name = (row.get("name") or row.get("name_ko") or row.get("Name")
            or row.get("name_en") or "").strip()
    # market
    market = (row.get("market") or row.get("exchange") or "").strip() or market_default
    # tier
    tier = (row.get("market_cap_tier") or row.get("tier") or "").strip() or "unknown"
    # industry / sector / theme / catalyst
    industry = (row.get("industry") or "").strip()
    sector = (row.get("sector") or "").strip()
    theme = (row.get("theme") or "").strip()
    catalyst = (row.get("catalyst") or row.get("category") or "").strip()

    # market_cap (KR 만, fdr)
    mcap = None
    for k in ["market_cap_krw", "Marcap"]:
        if k in row and row[k]:
            try:
                mcap = float(row[k])
                break
            except Exception:
                pass

    return {
        "ticker": ticker,
        "name": name,
        "market": market,
        "market_cap_tier": tier,
        "market_cap_krw": mcap,
        "industry": industry,
        "sector": sector,
        "theme": theme,
        "catalyst": catalyst,
        "sources": [source_tag],
        "tags": [],
    }


def load_master_universe(
    sources: list[str] | None = None,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    """모든 universe 통합 load. dedup 시 같은 ticker 의 source 들 합산.

    Args:
        sources: 특정 source 만 필터링 (None 이면 전부)
        dedupe: True 면 ticker 기준 dedup (sources field 합쳐짐)

    Returns:
        list of normalized rows (dict)
    """
    all_rows: list[dict[str, Any]] = []

    for fname, src_tag, mkt_default in _SOURCES:
        if sources and src_tag not in sources:
            continue
        path = DATA_DIR / fname
        rows = _read_csv_skip_comments(path)
        for r in rows:
            normalized = _normalize_row(r, src_tag, mkt_default)
            if normalized:
                all_rows.append(normalized)

    if not dedupe:
        return all_rows

    # Dedupe by ticker — merge sources
    by_ticker: dict[str, dict] = {}
    for r in all_rows:
        t = r["ticker"]
        if t in by_ticker:
            # 합치기
            existing = by_ticker[t]
            for src in r["sources"]:
                if src not in existing["sources"]:
                    existing["sources"].append(src)
            # 누락 필드 보강
            for k in ["name", "industry", "sector", "theme", "catalyst", "market_cap_krw"]:
                if not existing.get(k) and r.get(k):
                    existing[k] = r[k]
            # market_cap_tier 가 unknown 이면 update
            if existing.get("market_cap_tier") == "unknown" and r.get("market_cap_tier") != "unknown":
                existing["market_cap_tier"] = r["market_cap_tier"]
        else:
            by_ticker[t] = r

    # Tags 자동 부여
    for t, r in by_ticker.items():
        srcs = r["sources"]
        if "core" in srcs:
            r["tags"].append("active_alpha")
        if "kr_quality" in srcs:
            r["tags"].append("quality_filter")
        if "kr_momentum" in srcs or "wide_curated" in srcs:
            r["tags"].append("momentum_curated")
        if any(s in srcs for s in ["kr_dynamic", "us_dynamic"]):
            r["tags"].append("dynamic")
        # 두 개 이상 source 에 등장 = 강한 후보
        if len(srcs) >= 2:
            r["tags"].append("multi_source")

    return list(by_ticker.values())


def get_universe_stats() -> dict[str, Any]:
    """Universe 통계 — 디버그용."""
    rows = load_master_universe(dedupe=False)
    deduped = load_master_universe(dedupe=True)

    by_source: dict[str, int] = {}
    by_market: dict[str, int] = {}
    by_tier: dict[str, int] = {}

    for r in deduped:
        for s in r["sources"]:
            by_source[s] = by_source.get(s, 0) + 1
        by_market[r["market"]] = by_market.get(r["market"], 0) + 1
        by_tier[r["market_cap_tier"]] = by_tier.get(r["market_cap_tier"], 0) + 1

    return {
        "total_raw": len(rows),
        "total_deduped": len(deduped),
        "by_source": by_source,
        "by_market": by_market,
        "by_tier": by_tier,
        "multi_source_count": sum(1 for r in deduped if len(r["sources"]) >= 2),
    }


def filter_universe(
    *,
    markets: list[str] | None = None,
    tiers: list[str] | None = None,
    has_tag: str | None = None,
    has_catalyst: bool | None = None,
) -> list[dict[str, Any]]:
    """master 에서 filter. 모든 조건 AND."""
    rows = load_master_universe()
    if markets:
        rows = [r for r in rows if r["market"] in markets]
    if tiers:
        rows = [r for r in rows if r["market_cap_tier"] in tiers]
    if has_tag:
        rows = [r for r in rows if has_tag in r["tags"]]
    if has_catalyst is True:
        rows = [r for r in rows if r.get("catalyst")]
    elif has_catalyst is False:
        rows = [r for r in rows if not r.get("catalyst")]
    return rows


if __name__ == "__main__":
    import json
    stats = get_universe_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
