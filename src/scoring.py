"""Scoring 엔진 v2 — 의미 명확화.

Final Score =
  Thesis Strength    20%   (큐레이션 thesis_pillars + 카테고리 weight)
+ Evidence Strength  20%   (재무 트렌드 + 큐레이션 이벤트의 thesis_impact)
+ Price Opportunity  15%   (drawdown × multiple 결합)
+ Financial Quality  15%   (OPM / FCF margin / ROE / 부채 / 시가총액)
+ Event Freshness    15%   (최근 이벤트 staleness × confidence × 영향 강도)
+ Risk Control       15%   (downside × valuation 부담 × 구조적 훼손 신호)

각 sub-score 는 0~100 스케일.
모두 의미 명확 — 중복 제거 (Theme/Quality 통합, Dislocation/Risk 의미 분리).

Action Tag 결정은 의사결정 트리 (assign_action_tag) 로 수행.
"""
from __future__ import annotations

from typing import Any

from .curated import (
    THESIS_PILLARS,
    company_type as _curated_company_type,
    thesis_pillars as _thesis_pillars_lookup,  # auto_curation fallback 포함
)
from .universe import theme_weight
from .utils import clip


# ---------------------------------------------------------------------------
# 스코어 가중치
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "thesis":    0.20,
    "evidence":  0.20,
    "price":     0.15,
    "financial": 0.15,
    "event":     0.15,
    "risk":      0.15,
}

# Action Tag 의사결정 트리 임계값 (매직 넘버 한 곳에 모음)
THRESHOLDS: dict[str, float] = {
    "drawdown_quality_dislocation_min": 0.20,
    "drawdown_quality_dislocation_max": 0.45,
    "research_now_final_min": 60.0,
    "research_now_risk_max": 35.0,
    "too_crowded_6m_return": 0.80,
    "too_crowded_pe_min": 45.0,
    "wait_entry_3m_return": 0.30,
    "avoid_risk_min": 60.0,
    "thesis_check_drawdown": 0.30,
}


# ---------------------------------------------------------------------------
# 1) Thesis Strength — 큐레이션 thesis_pillars + 카테고리 weight
# ---------------------------------------------------------------------------

def thesis_strength_score(row: dict[str, Any]) -> float:
    """Thesis Strength — 큐레이션 thesis_pillars + 카테고리 weight 결합.

    de-bias (2026-05-03): 정적 THESIS_PILLARS dict 직접 호출 대신 lookup 함수 사용.
    이렇게 해야 auto_curation (LLM 자동 큐레이션) 으로 pillar 받은 종목도
    pillar_count 인정 받음 — 큐레이션 미등록 종목이 thesis 점수에서 구조적으로
    36 점 페널티 받던 편향 제거.
    """
    # 1차: lookup 함수 — Manual Override (THESIS_PILLARS dict) → auto_curation DB → []
    pillars = _thesis_pillars_lookup(row["ticker"])
    pillar_count = min(len(pillars), 3)
    base = 50.0 + pillar_count * 12.0    # 0개 50 / 3개 86

    # 카테고리 weight 보정 (0.55~1.0)
    w = theme_weight(row.get("theme", ""))
    base = base * (0.7 + 0.3 * w)

    # company_type별 추가 보정 — 마찬가지로 auto_curation 자동 분류 시도
    ctype = _curated_company_type(row["ticker"])
    if not ctype:
        # auto_curation 의 thesis_type 이라도 있으면 활용
        try:
            from .curated import _ac_field
            ctype = _ac_field(row["ticker"], "thesis_type") or ""
        except Exception:
            ctype = ""

    if ctype == "Civilization Alpha":
        base += 5
    elif ctype == "Re-rating Candidate":
        base += 3
    elif ctype == "Turnaround":
        base -= 3
    return clip(base, 0.0, 100.0)


# ---------------------------------------------------------------------------
# 2) Evidence Strength — 재무 트렌드 + thesis_impact 누적
# ---------------------------------------------------------------------------

def evidence_strength_score(row: dict[str, Any]) -> float:
    md = row.get("market_data") or {}
    sc_news = (row.get("news_agg") or {})
    score = 50.0

    # 재무 가속 (매출 성장률 + OPM)
    rg = md.get("revenue_growth")
    if rg is not None:
        if rg >= 0.20:
            score += 12
        elif rg >= 0.10:
            score += 6
        elif rg >= 0:
            score += 0
        else:
            score -= 8
    om = md.get("operating_margin")
    if om is not None:
        if om >= 0.25:
            score += 8
        elif om >= 0.15:
            score += 4
        elif om < 0:
            score -= 8

    # 큐레이션 이벤트의 thesis_impact 누적
    events = row.get("curated_events") or []  # stock_detail.recent_events 결과 주입
    for ev in events:
        impact = ev.get("thesis_impact") or {
            "strengthen": "Thesis 강화",
            "weaken": "Thesis 약화",
            "needs_check": "확인 필요",
            "new_risk": "신규 리스크",
            "noise": "단기 노이즈",
        }.get(ev.get("classification", ""), "확인 필요")
        if impact in ("Thesis 강화", "리스크 해소"):
            score += 6
        elif impact in ("Thesis 약화", "신규 리스크"):
            score -= 6
        # 단기 노이즈 / 확인 필요는 0

    # 뉴스 강도 (최근 fresh/aging 위주)
    fresh = sc_news.get("fresh_count", 0) or 0
    if fresh >= 3:
        score += 6
    elif fresh >= 1:
        score += 3

    return clip(score, 0.0, 100.0)


# ---------------------------------------------------------------------------
# 3) Price Opportunity — drawdown × multiple 결합
# ---------------------------------------------------------------------------

def price_opportunity_score(row: dict[str, Any]) -> float:
    md = row.get("market_data") or {}
    dd = md.get("drawdown_from_52w_high")
    pe = md.get("forward_pe") or md.get("trailing_pe")

    if dd is None:
        return 50.0
    abs_dd = -dd  # 양수

    # drawdown 베이스 점수
    if abs_dd >= 0.40:
        base = 70.0  # 너무 큰 drawdown은 thesis 훼손 의심
    elif abs_dd >= 0.30:
        base = 80.0
    elif abs_dd >= 0.20:
        base = 70.0
    elif abs_dd >= 0.10:
        base = 55.0
    else:
        base = 40.0

    # multiple 결합 (pe 낮을수록 + 가산)
    if pe and pe > 0:
        if pe >= 60:
            base -= 10
        elif pe >= 45:
            base -= 5
        elif pe < 18:
            base += 8
        elif pe < 28:
            base += 4

    return clip(base, 0.0, 100.0)


# ---------------------------------------------------------------------------
# 4) Financial Quality — OPM / FCF margin / ROE / 부채 / 시총
# ---------------------------------------------------------------------------

def financial_quality_score(row: dict[str, Any]) -> float:
    md = row.get("market_data") or {}
    score = 50.0

    om = md.get("operating_margin")
    if om is not None:
        if om >= 0.30:
            score += 14
        elif om >= 0.20:
            score += 9
        elif om >= 0.10:
            score += 4
        elif om < 0:
            score -= 12

    roe = md.get("roe")
    if roe is not None:
        if roe >= 0.20:
            score += 8
        elif roe >= 0.10:
            score += 3
        elif roe < 0:
            score -= 5

    fcfy = md.get("fcf_yield")
    if fcfy is not None:
        if fcfy >= 0.05:
            score += 8
        elif fcfy >= 0.02:
            score += 3
        elif fcfy < 0:
            score -= 4

    cap = md.get("market_cap")
    if cap and cap > 0:
        if cap >= 500e9:
            score += 4
        elif cap >= 100e9:
            score += 2
        elif cap < 5e9:
            score -= 4

    return clip(score, 0.0, 100.0)


# ---------------------------------------------------------------------------
# 5) Event Freshness & Relevance — staleness × confidence × 영향
# ---------------------------------------------------------------------------

def event_freshness_score(row: dict[str, Any]) -> float:
    events = row.get("curated_events") or []
    if not events:
        # 큐레이션 이벤트 없으면 뉴스 메타 기반
        agg = row.get("news_agg") or {}
        fresh = agg.get("fresh_count", 0) or 0
        outdated = agg.get("outdated_count", 0) or 0
        if fresh >= 2:
            return 65.0
        if fresh >= 1:
            return 55.0
        if outdated > 0 and fresh == 0:
            return 35.0
        return 50.0

    score = 50.0
    for ev in events:
        st = ev.get("staleness", "outdated")
        conf = ev.get("confidence", "Low")
        impact = ev.get("thesis_impact") or "확인 필요"

        # staleness 가중
        st_w = {"fresh": 1.0, "aging": 0.7, "stale": 0.4, "outdated": 0.0}.get(st, 0.0)
        # confidence 가중
        conf_w = {"High": 1.0, "Medium": 0.7, "Low": 0.4}.get(conf, 0.4)
        # 영향 강도
        impact_w = {
            "Thesis 강화": 12,
            "리스크 해소": 10,
            "Thesis 약화": -8,
            "신규 리스크": -10,
            "확인 필요": 0,
            "단기 노이즈": 0,
        }.get(impact, 0)

        score += impact_w * st_w * conf_w

    return clip(score, 0.0, 100.0)


# ---------------------------------------------------------------------------
# 6) Risk Control — downside × valuation 부담 × 구조적 훼손
# ---------------------------------------------------------------------------

def risk_control_score(row: dict[str, Any]) -> float:
    md = row.get("market_data") or {}
    agg = row.get("news_agg") or {}
    events = row.get("curated_events") or []

    risk = 0.0  # 0~100 (높을수록 risk)

    # downside (drawdown 정도)
    dd = md.get("drawdown_from_52w_high")
    if dd is not None:
        abs_dd = -dd
        if abs_dd >= 0.50:
            risk += 25
        elif abs_dd >= 0.40:
            risk += 18
        elif abs_dd >= 0.30:
            risk += 10
        elif abs_dd >= 0.20:
            risk += 5

    # valuation 부담
    pe = md.get("forward_pe") or md.get("trailing_pe")
    if pe and pe > 0:
        if pe >= 60:
            risk += 20
        elif pe >= 45:
            risk += 12
        elif pe >= 30:
            risk += 5

    # 구조적 훼손 신호 (큐레이션 이벤트 thesis_impact)
    for ev in events:
        impact = ev.get("thesis_impact") or "확인 필요"
        if impact == "신규 리스크":
            risk += 15
        elif impact == "Thesis 약화":
            risk += 10

    # 뉴스 urgent (이미 outdated/저신뢰는 제외된 상태)
    if agg.get("urgent"):
        risk += 20
    score_sum = agg.get("score_sum", 0.0) or 0.0
    if score_sum < -2.0:
        risk += 8

    # 영업적자
    om = md.get("operating_margin")
    if om is not None and om < 0:
        risk += 8

    risk = clip(risk, 0.0, 100.0)
    # risk_control = 100 - risk (높을수록 안전)
    return 100.0 - risk


# ---------------------------------------------------------------------------
# Final Score
# ---------------------------------------------------------------------------

def compute_scores(
    md: dict[str, Any],
    theme: str,
    news_agg: dict[str, Any],
    row_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """row_context 는 ticker / curated_events 등 큐레이션 데이터를 포함.

    호출 측: engine.build_rows 가 row 를 만들 때 이 함수를 호출.
    """
    if not md.get("available"):
        return {
            "available": False,
            "thesis": None, "evidence": None, "price": None,
            "financial": None, "event": None, "risk": None,
            # alpha_score.py 가 사용하는 long-name alias (2026-05 호환)
            "thesis_strength": None, "evidence_strength": None,
            "price_opportunity": None, "financial_quality": None,
            "event_freshness": None, "risk_control": None,
            "final_score": None,
        }

    # row_context가 None이면 빈 row 만들기 (하위 호환)
    row = dict(row_context or {})
    row.setdefault("ticker", md.get("ticker", ""))
    row["theme"] = theme
    row["market_data"] = md
    row["news_agg"] = news_agg

    t = thesis_strength_score(row)
    e = evidence_strength_score(row)
    p = price_opportunity_score(row)
    f = financial_quality_score(row)
    ev = event_freshness_score(row)
    r = risk_control_score(row)

    final = (
        t * WEIGHTS["thesis"]
        + e * WEIGHTS["evidence"]
        + p * WEIGHTS["price"]
        + f * WEIGHTS["financial"]
        + ev * WEIGHTS["event"]
        + r * WEIGHTS["risk"]
    )
    return {
        "available": True,
        "thesis": round(t, 1),
        "evidence": round(e, 1),
        "price": round(p, 1),
        "financial": round(f, 1),
        "event": round(ev, 1),
        "risk": round(r, 1),
        # alpha_score.py 가 사용하는 long-name alias (2026-05 — Alpha Score 변별력 재설계 호환)
        # 두 이름 모두 동시 노출 — 기존 호출처 (scoring rules / brief_generator) 와
        # 신규 호출처 (alpha_score._component_*) 가 동일 dict 에서 작동하도록.
        "thesis_strength": round(t, 1),
        "evidence_strength": round(e, 1),
        "price_opportunity": round(p, 1),
        "financial_quality": round(f, 1),
        "event_freshness": round(ev, 1),
        "risk_control": round(r, 1),
        "final_score": round(final, 1),
    }


# ---------------------------------------------------------------------------
# Company Type 동적 분류 (정적 분류를 동적 신호로 덮어씀)
# ---------------------------------------------------------------------------

def classify_company_type(row: dict[str, Any]) -> str:
    """정적 company_type을 시작점으로, 시장 신호에 따라 동적 덮어씀.

    예:
    - Structural Growth 종목이 6m +80%, PE 50+ → Too Crowded
    - 어떤 종목이든 urgent risk → Avoid
    - 우량주가 30%+ 조정 + thesis 유지 → Quality Dislocation
    """
    static_type = _curated_company_type(row["ticker"])
    md = row.get("market_data") or {}
    agg = row.get("news_agg") or {}
    events = row.get("curated_events") or []

    if not md.get("available"):
        return static_type

    # 1) Avoid: urgent risk + High/Medium 출처
    if agg.get("urgent"):
        return "Avoid"

    # 2) 구조적 훼손 우세 → Avoid 또는 Turnaround
    impacts = [ev.get("thesis_impact") for ev in events]
    if "신규 리스크" in impacts and len([i for i in impacts if i == "신규 리스크"]) >= 2:
        return "Avoid"

    # 3) Quality Dislocation 동적 분류
    dd = md.get("drawdown_from_52w_high")
    if dd is not None:
        abs_dd = -dd
        if (
            THRESHOLDS["drawdown_quality_dislocation_min"]
            <= abs_dd
            <= THRESHOLDS["drawdown_quality_dislocation_max"]
        ):
            # thesis 약화 신호 없으면 Quality Dislocation 후보
            if "Thesis 약화" not in impacts and "신규 리스크" not in impacts:
                if static_type in ("Structural Growth", "Re-rating Candidate", "Civilization Alpha", "Quality Dislocation"):
                    return "Quality Dislocation"

    # 4) Too Crowded 동적 분류
    r6m = md.get("6m_return") or 0
    pe = md.get("forward_pe") or md.get("trailing_pe") or 0
    if r6m >= THRESHOLDS["too_crowded_6m_return"] and pe >= THRESHOLDS["too_crowded_pe_min"]:
        return "Too Crowded"

    return static_type


# ---------------------------------------------------------------------------
# Action Tag 의사결정 트리 (company_type + scores 기반)
# ---------------------------------------------------------------------------

ACTION_TAGS_KO: dict[str, str] = {
    "Research Now": "Research Now",
    "Watchlist": "Watchlist",
    "Wait for Entry": "Wait for Entry",
    "Quality Dislocation": "Quality Dislocation",
    "Too Crowded": "Too Crowded",
    "Need Thesis Check": "Need Thesis Check",
    "Avoid": "Avoid",
    "Data Unavailable": "Data Unavailable",
}


def assign_action_tag(
    md: dict[str, Any],
    scores: dict[str, Any],
    news_agg: dict[str, Any],
    category: str,
    row_context: dict[str, Any] | None = None,
) -> str:
    """7유형 company_type 분류 + 6요소 점수 기반 Action Tag 결정 트리.

    트리 순서:
    1. Data Unavailable
    2. Avoid (urgent risk OR risk_control 매우 낮음)
    3. company_type == Too Crowded → Too Crowded
    4. company_type == Quality Dislocation → Quality Dislocation
    5. company_type == Avoid → Avoid
    6. Need Thesis Check (thesis 약화/신규 리스크 + drawdown 큼)
    7. Research Now (final 높음 + risk 낮음)
    8. Wait for Entry (theme/thesis 강하나 가격 부담)
    9. Watchlist (default)
    """
    if not scores.get("available"):
        return "Data Unavailable"

    row = dict(row_context or {})
    row.setdefault("ticker", md.get("ticker", ""))
    row["market_data"] = md
    row["news_agg"] = news_agg

    # 동적 company_type 결정 (이미 결정된 경우 사용)
    ctype = row.get("company_type") or classify_company_type(row)

    # 1. urgent risk → Avoid
    if news_agg.get("urgent") or scores.get("risk", 100) <= (100 - THRESHOLDS["avoid_risk_min"]):
        return "Avoid"

    # 2. company_type 우선
    if ctype == "Too Crowded":
        return "Too Crowded"
    if ctype == "Avoid":
        return "Avoid"
    if ctype == "Quality Dislocation":
        # thesis 유지 여부 확인
        events = row.get("curated_events") or []
        impacts = [ev.get("thesis_impact") for ev in events]
        if "Thesis 약화" in impacts or "신규 리스크" in impacts:
            return "Need Thesis Check"
        return "Quality Dislocation"

    # 3. Need Thesis Check (drawdown + thesis 약화)
    dd = md.get("drawdown_from_52w_high") or 0
    abs_dd = -dd
    events = row.get("curated_events") or []
    impacts = [ev.get("thesis_impact") for ev in events]
    if abs_dd >= THRESHOLDS["thesis_check_drawdown"] and (
        "Thesis 약화" in impacts or "신규 리스크" in impacts
    ):
        return "Need Thesis Check"

    # 4. Research Now (final 높음 + risk 낮음)
    final = scores.get("final_score", 0)
    risk_control = scores.get("risk", 0)  # 높을수록 안전
    if final >= THRESHOLDS["research_now_final_min"] and risk_control >= 60:
        # 단, valuation 너무 높으면 Wait for Entry로
        pe = md.get("forward_pe") or md.get("trailing_pe") or 0
        r3m = md.get("3m_return") or 0
        if pe >= THRESHOLDS["too_crowded_pe_min"] and r3m >= THRESHOLDS["wait_entry_3m_return"]:
            return "Wait for Entry"
        return "Research Now"

    # 5. Wait for Entry (Thesis 강하나 단기 급등)
    r3m = md.get("3m_return") or 0
    if scores.get("thesis", 0) >= 75 and r3m >= THRESHOLDS["wait_entry_3m_return"]:
        return "Wait for Entry"

    # 6. Default
    return "Watchlist"
