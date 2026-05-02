"""공용 헬퍼.

가능한 한 외부 의존성 없는 작은 함수들로 구성한다.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UNIVERSE_CSV = DATA_DIR / "universe.csv"
DAILY_SNAPSHOTS_CSV = DATA_DIR / "daily_snapshots.csv"
DECISION_LOG_CSV = DATA_DIR / "decision_log.csv"
WATCHLIST_JSON = DATA_DIR / "watchlist.json"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 로거
# ---------------------------------------------------------------------------

def get_logger(name: str = "alpha") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(h)
        logger.setLevel(os.environ.get("ALPHA_LOG", "INFO"))
    return logger


# ---------------------------------------------------------------------------
# 시간/숫자 포맷
# ---------------------------------------------------------------------------

def today_kst() -> str:
    """오늘 날짜 (YYYY-MM-DD). 단순 로컬 날짜를 사용한다."""
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{value * 100:.{digits}f}%"
    except Exception:
        return "-"


def fmt_money(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"${value:,.{digits}f}"
    except Exception:
        return "-"


def fmt_marketcap(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    units = [(1e12, "T"), (1e9, "B"), (1e6, "M")]
    for u, label in units:
        if value >= u:
            return f"${value / u:,.2f}{label}"
    return f"${value:,.0f}"


def safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# 한국어 종목 표기
# ---------------------------------------------------------------------------

def display_name(name_ko: str | None, ticker: str) -> str:
    """종목명 표기는 무조건 '한국어명 (티커)' 형식.

    name_ko가 비어 있으면 ticker만 fallback.
    """
    if name_ko and name_ko.strip():
        return f"{name_ko.strip()} ({ticker})"
    return ticker


# ---------------------------------------------------------------------------
# 점수 → 라벨 변환
# ---------------------------------------------------------------------------

def score_label(score: float) -> str:
    if score >= 75:
        return "매우 강함"
    if score >= 60:
        return "강함"
    if score >= 45:
        return "중립"
    if score >= 30:
        return "약함"
    return "매우 약함"


# ---------------------------------------------------------------------------
# iterable utilities
# ---------------------------------------------------------------------------

def first_non_none(items: Iterable[Any]) -> Any:
    for x in items:
        if x is not None:
            return x
    return None
