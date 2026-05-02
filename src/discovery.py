"""Wide Scan → Discovery Candidate — 미국 상장주식 정량 스크리닝.

퍼널 단계:
    Wide Scan         — wide_universe (~300) 가격/거래량 batch fetch
    Discovery Cand.   — 4 큐 (정량 시그널) 통과 후보 80개 선정 (이 모듈)
    Promoted Cand.    — promotion.py 가 처리
    Deep Dive         — core watchlist + 상위 promoted

큐:
    A. Quality Dislocation     — 우량주가 단기 이슈로 20~45% 조정받은 후보
    B. Earnings Revision       — 실적/가이던스 직후 시장 기대가 바뀐 후보
    C. Unusual Volume          — 거래량/가격이 뉴스 해석 전 먼저 움직인 후보
    D. Civilization Alpha      — 인프라/병목/카테고리 장악 가능성 있는 테마 후보

원칙:
    - LLM 사용 금지 (정량만)
    - 뉴스 fetch 금지 (Promoted Candidate 단계부터)
    - market_data 의 batch fetch 결과만 사용
    - 데이터 부족 / NaN 은 큐에서 제외 (False positive 회피)
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from .utils import get_logger, safe_float

log = get_logger("discovery")


# ---------------------------------------------------------------------------
# 큐 ID
# ---------------------------------------------------------------------------

QUEUE_DISLOCATION = "Quality Dislocation"
QUEUE_EARNINGS = "Earnings Revision"
QUEUE_UNUSUAL_VOLUME = "Unusual Volume"
QUEUE_CIVILIZATION = "Civilization Alpha"

ALL_QUEUES = (
    QUEUE_DISLOCATION,
    QUEUE_EARNINGS,
    QUEUE_UNUSUAL_VOLUME,
    QUEUE_CIVILIZATION,
)


# ---------------------------------------------------------------------------
# 1차 필터 — wide universe → 데이터 quality / 유동성 통과
# ---------------------------------------------------------------------------

MIN_MARKET_CAP = 300_000_000     # $300M
MIN_AVG_DOLLAR_VOLUME = 5_000_000  # $5M
RISK_KEYWORDS_NAME = (
    "spac", "warrant", "right", "preferred",
)


def passes_universe_filter(meta: dict[str, Any], md: dict[str, Any]) -> bool:
    """기본 필터.

    - market_cap >= $300M (있을 때만 — 없으면 wide_universe.csv 의 tier 로 fallback)
    - 평균 거래대금 >= $5M (current_price * avg_volume_20d)
    - 가격 >= $1 (penny stock 제외)
    - SPAC / warrant / preferred 등 이름 키워드 제외
    """
    if not md or not md.get("available"):
        return False

    name = (meta.get("name") or "").lower()
    for kw in RISK_KEYWORDS_NAME:
        if kw in name:
            return False

    price = safe_float(md.get("current_price"))
    # market_data 는 avg_volume_30d 로 저장 — fallback 도 같이
    avg_vol = safe_float(
        md.get("avg_volume_30d")
        or md.get("avg_volume_20d")
        or md.get("avg_volume")
    )

    # 가격 / 유동성 체크 — 항상 강제
    if price is None or price <= 1.0:
        return False
    if avg_vol is None or (price * avg_vol) < MIN_AVG_DOLLAR_VOLUME:
        return False

    # 시총 체크 — yfinance 가 batch download 에서 안 줄 때가 많아 fallback 처리
    mcap = safe_float(md.get("market_cap"))
    if mcap is not None:
        if mcap < MIN_MARKET_CAP:
            return False
    else:
        # wide_universe.csv 의 market_cap_tier 로 fallback (large / mid / small)
        tier = (meta.get("market_cap_tier") or "").lower()
        if tier == "small":
            # small-cap 은 시총 정보 없으면 보수적으로 제외
            return False
        # large / mid 또는 미지정 → 가격*거래량 통과했으면 허용

    return True


# ---------------------------------------------------------------------------
# Civilization 테마 키워드 (sector + industry + name)
# ---------------------------------------------------------------------------

# (theme_label, keyword_list, base_weight)
_CIVILIZATION_THEMES: list[tuple[str, list[str], float]] = [
    ("AI Infrastructure", [
        "semiconductor", "semiconductors", "ai", "gpu", "data center",
        "cloud", "computer hardware", "communication equipment",
    ], 1.00),
    ("Data Center Power", [
        "utilities independent", "utilities regulated", "electrical equipment",
        "specialty industrial", "data center",
    ], 0.95),
    ("Nuclear / Uranium", [
        "uranium", "nuclear", "small modular",
    ], 0.90),
    ("Defense", [
        "aerospace & defense", "aerospace", "defense",
    ], 0.85),
    ("Public Safety", [
        "axon", "palantir", "public safety",
    ], 0.85),
    ("Healthcare Infrastructure", [
        "diagnostics & research", "medical devices", "biotechnology",
        "drug manufacturers", "health information",
    ], 0.80),
    ("Robotics / Automation", [
        "robotics", "automation", "specialty industrial",
    ], 0.80),
    ("Space Infrastructure", [
        "space", "satellite", "rocket",
    ], 0.85),
    ("Cybersecurity", [
        "cybersecurity", "security software", "okta", "fortinet", "palo alto",
        "crowdstrike",
    ], 0.85),
    ("Payment / Identity Infra", [
        "credit services", "payments", "capital markets", "identity",
    ], 0.70),
    ("Energy Security", [
        "oil & gas integrated", "oil & gas e&p", "oil & gas midstream",
        "oil & gas refining", "uranium",
    ], 0.65),
]


def _civilization_theme_match(meta: dict[str, Any]) -> tuple[str | None, float]:
    """sector / industry / name 키워드 매칭으로 테마 라벨과 가중치 반환."""
    haystack = " ".join([
        (meta.get("sector") or "").lower(),
        (meta.get("industry") or "").lower(),
        (meta.get("name") or "").lower(),
    ])
    if not haystack.strip():
        return None, 0.0

    best_label: str | None = None
    best_weight = 0.0
    for label, keywords, weight in _CIVILIZATION_THEMES:
        for kw in keywords:
            if kw in haystack:
                if weight > best_weight:
                    best_weight = weight
                    best_label = label
                break
    return best_label, best_weight


# ---------------------------------------------------------------------------
# 큐별 시그널 / 점수 계산
# ---------------------------------------------------------------------------

def _md_get(md: dict, *keys, default=None):
    """여러 후보 키에서 첫 non-None 값 반환 (field 이름 다양성 흡수)."""
    for k in keys:
        v = md.get(k)
        if v is not None:
            return v
    return default


def _vol_ratio(md: dict) -> float | None:
    """현재 거래량 vs 평균 거래량 비율."""
    cur = safe_float(md.get("volume"))
    avg = safe_float(_md_get(md, "avg_volume_30d", "avg_volume_20d", "avg_volume"))
    if cur is None or avg is None or avg <= 0:
        return None
    return cur / avg


def _score_dislocation(meta: dict[str, Any], md: dict[str, Any]) -> dict | None:
    """Quality Dislocation 큐.

    트리거:
        - 52주 고점 대비 -20% ~ -45% 사이
        - 1년 수익률 > -50% (값이 있는 경우만 — value trap 회피)
        - 매출 성장률 양호 (있는 경우 가산점)
    """
    drawdown = safe_float(md.get("drawdown_from_52w_high"))  # 음수 (-0.30 = 30% 하락)
    if drawdown is None:
        return None
    dd_pct = abs(drawdown)  # 양수
    if not (0.20 <= dd_pct <= 0.45):
        return None

    ret_1y = safe_float(_md_get(md, "1y_return", "return_1y"))
    if ret_1y is not None and ret_1y < -0.50:
        # value trap penalty — 너무 길게 빠진 것은 제외
        return None

    rev_growth = safe_float(_md_get(md, "revenue_growth", "revenue_growth_yoy"))
    roe = safe_float(md.get("roe"))

    # 점수: dislocation 자체 강도 + quality proxy
    # dislocation 30% 가 sweet spot — 너무 적거나 너무 많지 않게
    dd_score = 100 * (1.0 - abs(dd_pct - 0.30) / 0.20)
    dd_score = max(0.0, min(100.0, dd_score))

    quality = 50.0
    if rev_growth is not None:
        quality += min(25.0, max(-15.0, rev_growth * 100))
    if roe is not None:
        quality += min(15.0, max(-10.0, roe * 50))
    quality = max(0.0, min(100.0, quality))

    score = 0.6 * dd_score + 0.4 * quality

    summary = (
        f"52주 고점 대비 {dd_pct * 100:.1f}% 조정. "
        f"매출 성장률 {(rev_growth * 100):.1f}%." if rev_growth is not None
        else f"52주 고점 대비 {dd_pct * 100:.1f}% 조정."
    )
    return {
        "score": score,
        "summary": summary,
        "metrics": {
            "drawdown_pct": dd_pct,
            "return_1y": ret_1y,
            "revenue_growth_yoy": rev_growth,
            "roe": roe,
        },
    }


def _score_earnings(meta: dict[str, Any], md: dict[str, Any]) -> dict | None:
    """Earnings Revision 큐.

    트리거 (proxy — 실적 발표 일정 직접 fetch 안 함):
        - 1D 또는 5D 수익률이 ±5% 이상
        - 거래량이 평균 대비 1.5x 이상 (이벤트 직후 신호)
        - 매출 성장률 가속 (rev_growth_yoy > 5%) 시 가산
    """
    ret_1d = safe_float(md.get("daily_return"))
    ret_5d = safe_float(_md_get(md, "5d_return", "return_5d", "return_1w"))
    vol_ratio = _vol_ratio(md)

    if ret_1d is None and ret_5d is None:
        return None

    big_move = (ret_1d is not None and abs(ret_1d) >= 0.05) or (
        ret_5d is not None and abs(ret_5d) >= 0.10
    )
    if not big_move:
        return None
    if vol_ratio is None or vol_ratio < 1.5:
        return None

    rev_growth = safe_float(_md_get(md, "revenue_growth", "revenue_growth_yoy"))

    # 점수: 움직임 강도 + 거래량 confirmation + 매출 성장 가속
    move_strength = 0.0
    if ret_1d is not None:
        move_strength = min(60.0, abs(ret_1d) * 100 * 6)
    elif ret_5d is not None:
        move_strength = min(60.0, abs(ret_5d) * 100 * 4)

    vol_strength = min(40.0, (vol_ratio - 1.0) * 20)
    growth_bonus = 0.0
    if rev_growth is not None and rev_growth > 0.05:
        growth_bonus = min(15.0, rev_growth * 50)
    score = move_strength + vol_strength + growth_bonus
    score = max(0.0, min(100.0, score))

    direction = "급등" if (ret_1d or ret_5d or 0) > 0 else "급락"
    move_pct = (ret_1d if ret_1d is not None else ret_5d) or 0.0
    summary = (
        f"최근 {direction} ({move_pct * 100:+.1f}%) + 거래량 {vol_ratio:.1f}x. "
        "실적 / 가이던스 이벤트 가능성."
    )
    return {
        "score": score,
        "summary": summary,
        "metrics": {
            "daily_return": ret_1d,
            "return_5d": ret_5d,
            "volume_ratio_20d": vol_ratio,
            "revenue_growth_yoy": rev_growth,
        },
    }


def _score_unusual_volume(meta: dict[str, Any], md: dict[str, Any]) -> dict | None:
    """Unusual Volume 큐.

    트리거:
        - 거래량 20일 평균 대비 3x 이상
        - 시총 $500M~$20B 가중치 (mid-small cap 위주)
        - 5D 수익률 절대값 > 8%
    """
    vol_ratio = _vol_ratio(md)
    if vol_ratio is None or vol_ratio < 3.0:
        return None

    ret_5d = safe_float(_md_get(md, "5d_return", "return_5d", "return_1w"))
    if ret_5d is not None and abs(ret_5d) < 0.08:
        return None

    mcap = safe_float(md.get("market_cap"))
    if mcap is None:
        return None

    # 시총 sweet spot ($500M~$20B)
    if 500e6 <= mcap <= 20e9:
        size_factor = 1.0
    elif mcap < 500e6:
        size_factor = 0.5
    else:
        size_factor = 0.7

    vol_score = min(60.0, math.log(vol_ratio, 2) * 25)  # 3x → ~40, 6x → ~65
    move_score = 0.0
    if ret_5d is not None:
        move_score = min(40.0, abs(ret_5d) * 100 * 3)
    score = (vol_score + move_score) * size_factor
    score = max(0.0, min(100.0, score))

    summary = (
        f"거래량 20일 평균 대비 {vol_ratio:.1f}x 폭증" + (
            f" ({ret_5d * 100:+.1f}% / 5D)." if ret_5d is not None else "."
        )
    )
    return {
        "score": score,
        "summary": summary,
        "metrics": {
            "volume_ratio_20d": vol_ratio,
            "return_5d": ret_5d,
            "market_cap": mcap,
        },
    }


def _score_civilization(meta: dict[str, Any], md: dict[str, Any]) -> dict | None:
    """Civilization Alpha 큐.

    트리거:
        - sector / industry / name 키워드 테마 매칭
        - 가격 / 모멘텀 modifier (1M / 3M)
        - 매출 성장 / FCF Yield 보정
    """
    label, theme_weight = _civilization_theme_match(meta)
    if not label or theme_weight < 0.5:
        return None

    ret_1m = safe_float(_md_get(md, "1m_return", "return_1m"))
    ret_3m = safe_float(_md_get(md, "3m_return", "return_3m"))
    rev_growth = safe_float(_md_get(md, "revenue_growth", "revenue_growth_yoy"))
    fcf_yield = safe_float(md.get("fcf_yield"))

    base = theme_weight * 50  # 25~50

    momentum = 0.0
    if ret_3m is not None:
        # 3개월 +20% 까지는 가산
        momentum += min(20.0, max(-10.0, ret_3m * 50))
    if ret_1m is not None:
        momentum += min(10.0, max(-5.0, ret_1m * 30))

    fundamentals = 0.0
    if rev_growth is not None:
        fundamentals += min(20.0, max(-10.0, rev_growth * 80))
    if fcf_yield is not None and fcf_yield > 0:
        fundamentals += min(10.0, fcf_yield * 100)

    score = base + momentum + fundamentals
    score = max(0.0, min(100.0, score))

    summary = (
        f"{label} 테마 매칭. "
        + (f"3개월 {ret_3m * 100:+.1f}% / " if ret_3m is not None else "")
        + (f"매출 성장 {rev_growth * 100:.1f}%." if rev_growth is not None
           else "성장 / 수익성 데이터는 후속 점검.")
    )
    return {
        "score": score,
        "summary": summary,
        "metrics": {
            "theme": label,
            "theme_weight": theme_weight,
            "return_1m": ret_1m,
            "return_3m": ret_3m,
            "revenue_growth_yoy": rev_growth,
            "fcf_yield": fcf_yield,
        },
    }


_QUEUE_SCORERS = {
    QUEUE_DISLOCATION: _score_dislocation,
    QUEUE_EARNINGS: _score_earnings,
    QUEUE_UNUSUAL_VOLUME: _score_unusual_volume,
    QUEUE_CIVILIZATION: _score_civilization,
}


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------

def run_discovery(
    wide_universe: list[dict[str, Any]],
    md_map: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """wide_universe + market_data 로 큐별 후보 리스트 생성.

    Returns:
        {queue_type: [candidate_dict, ...], ...}
        candidate_dict = {
            "ticker", "name", "sector", "industry",
            "queue_type", "score", "rank",
            "signal_summary", "key_metrics",
            "discovery_score" (queue 별 score),
        }
    """
    by_queue: dict[str, list[dict[str, Any]]] = {q: [] for q in ALL_QUEUES}

    n_total = 0
    n_filtered = 0
    for meta in wide_universe:
        ticker = meta["ticker"]
        md = md_map.get(ticker) or {}
        n_total += 1
        if not passes_universe_filter(meta, md):
            continue
        n_filtered += 1
        for queue_type, scorer in _QUEUE_SCORERS.items():
            try:
                result = scorer(meta, md)
            except Exception as e:
                log.debug("[%s] %s scoring failed: %s", ticker, queue_type, e)
                continue
            if not result or result.get("score") is None:
                continue
            by_queue[queue_type].append({
                "ticker": ticker,
                "name": meta.get("name"),
                "sector": meta.get("sector"),
                "industry": meta.get("industry"),
                "queue_type": queue_type,
                "score": float(result["score"]),
                "discovery_score": float(result["score"]),
                "signal_summary": result["summary"],
                "key_metrics": result.get("metrics") or {},
            })

    # 큐별 정렬 + rank 부여
    for q, items in by_queue.items():
        items.sort(key=lambda x: x["score"], reverse=True)
        for i, it in enumerate(items, 1):
            it["rank"] = i

    log.info(
        "discovery: filtered=%d/%d / dislocation=%d earnings=%d volume=%d civ=%d",
        n_filtered, n_total,
        len(by_queue[QUEUE_DISLOCATION]),
        len(by_queue[QUEUE_EARNINGS]),
        len(by_queue[QUEUE_UNUSUAL_VOLUME]),
        len(by_queue[QUEUE_CIVILIZATION]),
    )
    return by_queue


def select_discovery_candidates(
    by_queue: dict[str, list[dict[str, Any]]],
    top_k: int = 80,
    per_queue_cap: int = 30,
) -> list[dict[str, Any]]:
    """큐 통합 — 큐별 상위 per_queue_cap 후 중복 제거. 최종 top_k Discovery Candidate.

    같은 종목이 여러 큐에 들어가면 모든 큐 정보를 보존 (queues 리스트).
    """
    pool: dict[str, dict[str, Any]] = {}
    for q, items in by_queue.items():
        for it in items[:per_queue_cap]:
            t = it["ticker"]
            cur = pool.get(t)
            if cur is None:
                pool[t] = {
                    **it,
                    "queues": [q],
                    "best_queue": q,
                    "best_score": it["score"],
                }
            else:
                cur["queues"].append(q)
                if it["score"] > cur["best_score"]:
                    cur["best_queue"] = q
                    cur["best_score"] = it["score"]
                    cur["queue_type"] = q
                    cur["signal_summary"] = it["signal_summary"]
                    cur["key_metrics"] = it["key_metrics"]

    # 통합 점수: 다중 큐 매칭은 가산점
    for t, it in pool.items():
        bonus = 5 * (len(set(it["queues"])) - 1)  # 큐 1개 추가당 +5
        it["final_discovery_score"] = it["best_score"] + bonus

    ranked = sorted(pool.values(), key=lambda x: x["final_discovery_score"], reverse=True)
    return ranked[:top_k]


# Backward-compat alias (구 import 호환)
select_tier1 = select_discovery_candidates
