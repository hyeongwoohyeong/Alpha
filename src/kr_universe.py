"""KR 우량주 universe 런타임 로더.

`data/kr_universe.csv` 를 읽어 dict 리스트로 반환한다.
빌드 책임은 `scripts/build_kr_universe.py` — 본 모듈은 순수 I/O.

CSV 포맷 (header comment 가 앞에 붙음):
    # KR 우량주 universe — 자동 생성
    # 생성일: YYYY-MM-DD
    # pykrx version: ...
    # ...
    ticker,name_ko,sector,industry,market_cap_krw,roe_5y_avg,debt_ratio,...
    005930,삼성전자,...

사용 예:
    >>> from src.kr_universe import load_kr_universe
    >>> rows = load_kr_universe()
    >>> rows[0]
    {'ticker': '005930', 'name_ko': '삼성전자', ...}
"""
from __future__ import annotations

import csv
from typing import Any

from .utils import DATA_DIR, get_logger

log = get_logger("kr_universe")

KR_UNIVERSE_CSV = DATA_DIR / "kr_universe.csv"


def _to_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _to_bool(v: Any) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f"):
        return False
    return None


def load_kr_universe() -> list[dict[str, Any]]:
    """KR 우량주 universe 로드.

    `data/kr_universe.csv` 가 없으면 빈 리스트 반환.
    `#` 로 시작하는 헤더 코멘트 라인은 skip — 진짜 header 가 나올 때까지.

    Returns:
        list of dict with keys:
            ticker, name_ko, sector, industry, market_cap_krw,
            roe_5y_avg, debt_ratio, ocf_5y_positive,
            op_profit_4q_positive, avg_dollar_volume_30d, kcgs_grade,
            filter_passed, filters_failed
    """
    if not KR_UNIVERSE_CSV.exists():
        log.warning("kr_universe.csv 가 없습니다 — `python scripts/"
                    "build_kr_universe.py` 를 먼저 실행하세요: %s",
                    KR_UNIVERSE_CSV)
        return []

    rows: list[dict[str, Any]] = []
    try:
        with KR_UNIVERSE_CSV.open("r", encoding="utf-8") as f:
            # `#` comment line 스킵
            non_comment_lines = (line for line in f if not line.lstrip().startswith("#"))
            reader = csv.DictReader(non_comment_lines)
            for r in reader:
                ticker = (r.get("ticker") or "").strip()
                if not ticker:
                    continue
                rows.append({
                    "ticker": ticker,
                    "name_ko": (r.get("name_ko") or "").strip(),
                    "sector": (r.get("sector") or "").strip(),
                    "industry": (r.get("industry") or "").strip(),
                    "market_cap_krw": _to_int(r.get("market_cap_krw")),
                    "roe_5y_avg": _to_float(r.get("roe_5y_avg")),
                    "debt_ratio": _to_float(r.get("debt_ratio")),
                    "ocf_5y_positive": _to_bool(r.get("ocf_5y_positive")),
                    "op_profit_4q_positive": _to_bool(
                        r.get("op_profit_4q_positive")
                    ),
                    "avg_dollar_volume_30d": _to_int(
                        r.get("avg_dollar_volume_30d")
                    ),
                    "kcgs_grade": (r.get("kcgs_grade") or "").strip(),
                    "filter_passed": _to_bool(r.get("filter_passed")),
                    "filters_failed": (r.get("filters_failed") or "").strip(),
                })
    except Exception as e:
        log.warning("kr_universe.csv 로드 실패: %s", e)
        return []

    return rows


def get_kr_universe_map() -> dict[str, dict[str, Any]]:
    """ticker → row dict 매핑."""
    return {row["ticker"]: row for row in load_kr_universe()}
