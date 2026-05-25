"""Alpha 전역 설정 — LLM 모드 / 비용 제어 / 파이프라인 토글.

원칙:
- LLM 키가 없거나 LLM_MODE=none 이어도 엔진 전체가 동작해야 한다.
- 비용 발생 작업 (LLM 호출, 뉴스 fetch 확장) 은 후보를 줄인 뒤에만 수행.
- 같은 기사 URL 은 article_summaries 캐시로 재사용.
- 설정은 환경변수 우선, 미설정 시 기본값.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# LLM 모드
# ---------------------------------------------------------------------------

LLM_MODE_NONE = "none"
LLM_MODE_LOW_COST = "low_cost"
LLM_MODE_HIGH_QUALITY = "high_quality"
_VALID_LLM_MODES = {LLM_MODE_NONE, LLM_MODE_LOW_COST, LLM_MODE_HIGH_QUALITY}


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def _env_int(name: str, default: int) -> int:
    val = _env(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AlphaConfig:
    """런타임 설정 스냅샷."""

    # LLM 모드 — none / low_cost / high_quality
    llm_mode: str = LLM_MODE_NONE
    # 한 run 당 최대 LLM 호출 수 (캐시 hit 은 제외)
    max_llm_calls_per_run: int = 30
    # 기사 요약 캐시 사용 여부
    enable_summary_cache: bool = True

    # Discovery 단계 토글 (디버그/테스트 시 끌 수 있음)
    enable_discovery: bool = True
    enable_promotion: bool = True

    # Wide Scan 처리 한도 (필터 통과 후 상위 N개만 가격 fetch)
    wide_universe_limit: int = 1500
    # Wide Scan → Discovery Candidate (큐 통합 상위 K)
    discovery_top_k: int = 80
    # Promoted Candidate → Deep Dive 승격 K
    deep_dive_k: int = 15

    # 뉴스 fetch — Discovery Candidate 단계에서 후보당
    news_per_discovery_ticker: int = 3

    @property
    def llm_enabled(self) -> bool:
        return self.llm_mode in (LLM_MODE_LOW_COST, LLM_MODE_HIGH_QUALITY)

    @property
    def use_high_quality_llm(self) -> bool:
        return self.llm_mode == LLM_MODE_HIGH_QUALITY

    # ── Backward-compat aliases (구 이름 호환) ──
    @property
    def tier1_top_k(self) -> int:
        return self.discovery_top_k

    @property
    def promote_to_deep_dive_k(self) -> int:
        return self.deep_dive_k

    @property
    def news_per_tier1_ticker(self) -> int:
        return self.news_per_discovery_ticker


# ---------------------------------------------------------------------------
# FRED API 키 — Portfolio Regime 매크로 데이터용 (무료, 선택)
# ---------------------------------------------------------------------------
# 키가 없으면 None 을 반환하고, macro_data 모듈은 FRED 호출을 graceful 하게 skip.

def get_fred_api_key() -> str | None:
    """FRED API 키를 읽는다. 없으면 None.

    우선순위: 환경변수 → Streamlit secrets.
    Streamlit Cloud Secrets UI 로 등록한 키는 st.secrets 로만 노출되므로
    (os.environ 에는 안 들어감) st.secrets 도 확인해야 한다.
    파이프라인이 Streamlit 런타임 밖(GitHub Actions 등)에서 돌 때는
    st.secrets 접근이 예외를 던지므로 try/except 로 graceful 처리.
    """
    key = _env("FRED_API_KEY")
    if key:
        return key
    try:
        import streamlit as st  # type: ignore

        val = st.secrets.get("FRED_API_KEY")  # type: ignore[attr-defined]
        if val:
            return str(val).strip() or None
    except Exception:
        pass
    return None


def load_config() -> AlphaConfig:
    """환경변수에서 설정 로드. 매 호출 시 새로 읽는다 (테스트 친화적)."""
    mode = (_env("LLM_MODE", LLM_MODE_NONE) or LLM_MODE_NONE).strip().lower()
    if mode not in _VALID_LLM_MODES:
        mode = LLM_MODE_NONE

    # API 키가 하나도 없으면 강제로 none 모드
    if mode != LLM_MODE_NONE:
        if not (_env("ANTHROPIC_API_KEY") or _env("OPENAI_API_KEY")):
            mode = LLM_MODE_NONE

    return AlphaConfig(
        llm_mode=mode,
        max_llm_calls_per_run=_env_int("MAX_LLM_CALLS_PER_RUN", 30),
        enable_summary_cache=_env_bool("ENABLE_SUMMARY_CACHE", True),
        enable_discovery=_env_bool("ENABLE_DISCOVERY", True),
        enable_promotion=_env_bool("ENABLE_PROMOTION", True),
        wide_universe_limit=_env_int("WIDE_UNIVERSE_LIMIT", 1500),
        # 새 환경변수 우선 → 없으면 구 이름 fallback
        discovery_top_k=_env_int("DISCOVERY_TOP_K", _env_int("TIER1_TOP_K", 80)),
        deep_dive_k=_env_int("DEEP_DIVE_K", _env_int("PROMOTE_TO_DEEP_DIVE_K", 15)),
        news_per_discovery_ticker=_env_int(
            "NEWS_PER_DISCOVERY_TICKER",
            _env_int("NEWS_PER_TIER1_TICKER", 3),
        ),
    )


# ---------------------------------------------------------------------------
# LLM 호출 카운터 — 한 run 안에서 max_llm_calls_per_run 강제
# ---------------------------------------------------------------------------

class LlmBudget:
    """런타임 LLM 호출 카운터. 동시성 X, 단일 프로세스 가정."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self._used)

    def can_call(self) -> bool:
        return self._used < self.max_calls

    def record(self) -> None:
        self._used += 1


def make_budget(cfg: AlphaConfig | None = None) -> LlmBudget:
    cfg = cfg or load_config()
    return LlmBudget(cfg.max_llm_calls_per_run if cfg.llm_enabled else 0)
