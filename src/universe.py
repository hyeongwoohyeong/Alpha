"""Universe 로딩/관리.

universe.csv를 읽어 ticker, name_ko, name_en, theme, category dict 리스트로 반환한다.
"""
from __future__ import annotations

import csv
import json
from typing import Any

from .utils import UNIVERSE_CSV, WATCHLIST_JSON, ensure_data_dir, get_logger

log = get_logger("universe")


# 테마별 가중치(테마 적합도 점수에 사용)
THEME_WEIGHTS: dict[str, float] = {
    "ai_semiconductor": 1.00,
    "ai_networking": 1.00,
    "data_center_power": 0.95,
    "public_safety": 0.95,
    "defense": 0.90,
    "space": 0.85,
    "healthcare_infra": 0.85,
    "platform": 0.80,
    "ecommerce_platform": 0.80,
    "travel_mobility": 0.70,
    "mobility_consumer": 0.70,
    "consumer_brand": 0.55,
}

# 테마 한국어 라벨
THEME_LABEL_KO: dict[str, str] = {
    "ai_semiconductor": "AI 반도체",
    "ai_networking": "AI 네트워킹/인프라",
    "data_center_power": "데이터센터 전력",
    "public_safety": "공공안전 플랫폼",
    "defense": "방산",
    "space": "우주",
    "healthcare_infra": "헬스케어 인프라",
    "platform": "퀄리티 플랫폼",
    "ecommerce_platform": "이커머스 플랫폼",
    "travel_mobility": "여행/모빌리티",
    "mobility_consumer": "모빌리티/소비",
    "consumer_brand": "컨슈머 브랜드",
}

CATEGORY_LABEL_KO: dict[str, str] = {
    "AI Infrastructure": "AI 인프라",
    "Energy Security": "에너지 안보",
    "Public Safety": "공공안전",
    "Defense": "방산",
    "Space": "우주",
    "Healthcare Infrastructure": "헬스케어 인프라",
    "Quality Platform": "퀄리티 플랫폼",
    "Consumer Mobility": "소비/모빌리티",
    "Consumer Brand": "컨슈머 브랜드",
}


def load_universe() -> list[dict[str, Any]]:
    """universe.csv 로드. 존재하지 않거나 비어 있으면 빈 리스트."""
    if not UNIVERSE_CSV.exists():
        log.warning("universe.csv 가 존재하지 않습니다: %s", UNIVERSE_CSV)
        return []

    rows: list[dict[str, Any]] = []
    with UNIVERSE_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r.get("ticker"):
                continue
            ticker = r["ticker"].strip().upper()
            rows.append(
                {
                    "ticker": ticker,
                    "name_ko": (r.get("name_ko") or "").strip(),
                    "name_en": (r.get("name_en") or "").strip(),
                    "theme": (r.get("theme") or "").strip(),
                    "category": (r.get("category") or "").strip(),
                }
            )
    return rows


def get_universe_map() -> dict[str, dict[str, Any]]:
    return {row["ticker"]: row for row in load_universe()}


def theme_weight(theme: str) -> float:
    return THEME_WEIGHTS.get(theme, 0.6)


def theme_label_ko(theme: str) -> str:
    return THEME_LABEL_KO.get(theme, theme or "기타")


def category_label_ko(category: str) -> str:
    return CATEGORY_LABEL_KO.get(category, category or "기타")


# ---------------------------------------------------------------------------
# 사용자 관심종목 (watchlist) - 별도 json 파일에 저장
# ---------------------------------------------------------------------------

def load_watchlist() -> list[str]:
    ensure_data_dir()
    if not WATCHLIST_JSON.exists():
        return []
    try:
        return list(json.loads(WATCHLIST_JSON.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning("watchlist 로드 실패: %s", e)
        return []


def save_watchlist(tickers: list[str]) -> None:
    ensure_data_dir()
    WATCHLIST_JSON.write_text(
        json.dumps(sorted(set(t.upper() for t in tickers)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_to_watchlist(ticker: str) -> list[str]:
    wl = set(load_watchlist())
    wl.add(ticker.upper())
    save_watchlist(sorted(wl))
    return sorted(wl)


def remove_from_watchlist(ticker: str) -> list[str]:
    wl = set(load_watchlist())
    wl.discard(ticker.upper())
    save_watchlist(sorted(wl))
    return sorted(wl)
