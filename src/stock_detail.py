"""종목 상세 — 애널리스트 리포트 Executive Summary 구조.

투자 논리 / 핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항 /
시나리오 / 투자 현인 Lens / Anti-Thesis / 종합 판단 까지 한국어 리포트 톤으로 생성한다.
"""
from __future__ import annotations

from typing import Any

from .brief_generator import (
    check_items,
    core_thesis,
    investment_type,
    key_risk,
)
from .curated import (
    company_type as _curated_company_type,
    event_classification_label,
    key_metrics as _curated_key_metrics,
    parse_event_date,
    price_interpretation as _curated_price_interpretation,
    recent_events as _curated_recent_events,
    simple_explanation as _curated_simple_explanation,
    thesis_pillars as _curated_thesis_pillars,
)
from .event_processor import (
    aggregate_source_quality,
    enrich_curated_event,
    staleness_label,
)
from .universe import category_label_ko, theme_label_ko
from .utils import display_name, fmt_marketcap, fmt_money, fmt_pct, score_label


# ---------------------------------------------------------------------------
# 투자 지표 카드 (6 + 더 보기 4)
# ---------------------------------------------------------------------------

def _fmt_multiple(x):
    if x is None:
        return "데이터 확인 필요"
    try:
        x = float(x)
    except Exception:
        return "데이터 확인 필요"
    if x <= 0:
        return "데이터 확인 필요"
    return f"{x:.1f}배"


def valuation_metrics_cards(row: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """기본 6 + 더 보기 4."""
    md = row.get("market_data") or {}
    main = [
        {"label": "시가총액", "value": fmt_marketcap(md.get("market_cap"))},
        {"label": "PER", "value": _fmt_multiple(md.get("trailing_pe"))},
        {"label": "Forward PER", "value": _fmt_multiple(md.get("forward_pe"))},
        {"label": "PBR", "value": _fmt_multiple(md.get("pbr"))},
        {"label": "PSR", "value": _fmt_multiple(md.get("psr"))},
        {"label": "EV/EBITDA", "value": _fmt_multiple(md.get("ev_ebitda"))},
    ]
    extras = [
        {"label": "FCF Yield", "value": fmt_pct(md.get("fcf_yield")) if md.get("fcf_yield") is not None else "데이터 확인 필요"},
        {"label": "ROE", "value": fmt_pct(md.get("roe")) if md.get("roe") is not None else "데이터 확인 필요"},
        {"label": "매출 성장률", "value": fmt_pct(md.get("revenue_growth")) if md.get("revenue_growth") is not None else "데이터 확인 필요"},
        {"label": "영업이익률", "value": fmt_pct(md.get("operating_margin")) if md.get("operating_margin") is not None else "데이터 확인 필요"},
    ]
    return main, extras


# ---------------------------------------------------------------------------
# 가치평가 비교 (산업/Peer 평균 대비)
# ---------------------------------------------------------------------------

VALUATION_FIELD = {
    "PER": "trailing_pe",
    "PBR": "pbr",
    "PSR": "psr",
    "EV/EBITDA": "ev_ebitda",
}


def _avg_filtered(values: list[float]) -> float | None:
    cleaned = [v for v in values if v is not None and 0 < v < 200]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def valuation_comparison(
    row: dict[str, Any],
    metric: str,
    all_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    field = VALUATION_FIELD.get(metric, "trailing_pe")
    company_v = (row.get("market_data") or {}).get(field)

    # industry: 같은 category, 본인 제외
    industry_vals = [
        (r.get("market_data") or {}).get(field)
        for r in all_rows
        if r.get("category") == row.get("category") and r["ticker"] != row["ticker"]
    ]
    industry_avg = _avg_filtered([v for v in industry_vals if v is not None])

    # peer: 같은 theme, 본인 제외
    peer_vals = [
        (r.get("market_data") or {}).get(field)
        for r in all_rows
        if r.get("theme") == row.get("theme") and r["ticker"] != row["ticker"]
    ]
    peer_avg = _avg_filtered([v for v in peer_vals if v is not None])

    interp = _valuation_interpretation(company_v, industry_avg, peer_avg, metric)
    return {
        "metric": metric,
        "company": company_v,
        "industry_avg": industry_avg,
        "peer_avg": peer_avg,
        "interpretation": interp,
    }


def _valuation_interpretation(
    company: float | None,
    industry: float | None,
    peer: float | None,
    metric: str,
) -> str:
    if company is None or company <= 0:
        return f"동사의 {metric} 데이터가 부족하여 정밀 검토가 필요합니다."
    if industry is None and peer is None:
        return (
            f"비교 가능한 산업/Peer 데이터가 부족합니다. 단독 multiple({company:.1f}배) 점검이 필요합니다."
        )
    ref = peer if peer is not None else industry
    ratio = company / ref if ref else None
    if ratio is None:
        return f"동사의 {metric}는 {company:.1f}배 수준입니다."
    if ratio > 1.2:
        return (
            f"동사는 {metric} 기준 산업/Peer 평균 대비 프리미엄에 거래되고 있습니다. "
            "영업이익률, 매출 성장률, FCF 마진의 정합성을 함께 점검할 필요가 있습니다."
        )
    if ratio < 0.8:
        return (
            f"동사는 {metric} 기준 산업/Peer 평균 대비 디스카운트 영역에 위치하고 있습니다. "
            "디스카운트 사유와 thesis 훼손 여부 확인이 우선 과제입니다."
        )
    return (
        f"동사는 {metric} 기준 산업/Peer 평균과 유사한 수준에서 거래되고 있으며, "
        "Multiple과 추정치 방향성의 정합성을 함께 점검할 단계입니다."
    )


# ---------------------------------------------------------------------------
# 뉴스 thesis 분류 + 투자적 의미
# ---------------------------------------------------------------------------

_NEWS_URGENT_KEYWORDS = {
    "fraud", "investigation", "subpoena", "bankruptcy", "going concern",
    "lawsuit", "restatement", "secondary offering", "dilution", "halt",
    "recall", "probe",
}


def _classify_news(news: dict[str, Any]) -> str:
    title = (news.get("title") or "").lower()
    score = news.get("importance_score", 0) or 0
    if any(k in title for k in _NEWS_URGENT_KEYWORDS):
        return "new_risk"
    if score >= 1.0:
        return "strengthen"
    if score <= -1.0:
        return "weaken"
    if -0.3 <= score <= 0.3:
        return "noise"
    return "needs_check"


def _news_implication(news: dict[str, Any], cls: str) -> str:
    if cls == "new_risk":
        return "anti-thesis 우선 점검이 필요한 risk 키워드가 포함된 뉴스로 해석됩니다."
    if cls == "strengthen":
        return "기존 투자 thesis를 강화할 수 있는 catalyst로 해석될 여지가 있습니다."
    if cls == "weaken":
        return "기존 투자 thesis의 일부를 약화시킬 수 있는 흐름으로 해석됩니다."
    if cls == "noise":
        return "단기 sentiment에 미치는 영향이 제한적인 뉴스로 해석됩니다."
    return "단정 판단보다 추가 정밀 검토가 필요한 사안으로 해석됩니다."


def news_with_impact(row: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    items = list(row.get("news") or [])[:limit]
    out: list[dict[str, Any]] = []
    for n in items:
        cls = _classify_news(n)
        out.append(
            {
                **n,
                "thesis_impact": cls,
                "thesis_impact_label": event_classification_label(cls),
                "investment_implication": _news_implication(n, cls),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 이 회사는 쉽게 말해 / 최근 주요 이벤트
# ---------------------------------------------------------------------------

def simple_explanation(row: dict[str, Any]) -> str:
    theme = theme_label_ko(row.get("theme", ""))
    text = _curated_simple_explanation(row["ticker"], fallback_theme_label=theme)
    return text or (
        f"이 종목은 {theme} 카테고리에 속한 회사입니다. 사업 구조와 최근 이벤트는 1차 자료 확인이 필요합니다."
    )


def recent_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = _curated_recent_events(row["ticker"])
    out: list[dict[str, Any]] = []
    for it in items:
        enriched = enrich_curated_event(it)
        enriched["classification_label"] = event_classification_label(
            it.get("classification", "needs_check")
        )
        out.append(enriched)
    return out


def research_quality(row: dict[str, Any]) -> dict[str, str]:
    """종목 상세 하단의 리서치 품질 카드 데이터.

    - 가장 최신 큐레이션 이벤트 + 최근 뉴스 메타 종합
    - 큐레이션이 없으면 뉴스 기준
    """
    events = recent_events(row)
    news_agg = row.get("news_agg") or {}
    if events:
        ev = events[0]
        return {
            "staleness": staleness_label(ev.get("staleness", "outdated")),
            "source_quality": ev.get("source_quality") or aggregate_source_quality(ev.get("sources") or []),
            "status": ev.get("status", "확인 필요"),
            "confidence": ev.get("confidence", "Low"),
            "last_updated": ev.get("last_updated") or ev.get("date") or "확인 필요",
            "sources": ", ".join(ev.get("sources") or []) or "확인 필요",
        }
    # 뉴스 기준
    fresh = news_agg.get("fresh_count", 0)
    outdated = news_agg.get("outdated_count", 0)
    if fresh >= 1:
        st_lbl = "Fresh"
    elif outdated >= news_agg.get("count", 0) and news_agg.get("count", 0) > 0:
        st_lbl = "Outdated"
    else:
        st_lbl = "Aging"
    return {
        "staleness": st_lbl,
        "source_quality": "Medium",
        "status": "확인 필요",
        "confidence": "Medium" if fresh >= 1 else "Low",
        "last_updated": "최근 뉴스 기준",
        "sources": "Google News RSS",
    }


def price_interpretation(row: dict[str, Any]) -> str:
    return _curated_price_interpretation(
        row["ticker"], fallback_theme_label=theme_label_ko(row.get("theme", ""))
    )


def chart_event_markers(row: dict[str, Any]) -> list[dict[str, Any]]:
    """recent_events의 date를 (year, month, day) 튜플로 파싱한 리스트.

    실제 chart 위치 매칭은 app.py 의 render_price_chart 가 담당.
    """
    out = []
    for ev in _curated_recent_events(row["ticker"]):
        d = parse_event_date(ev.get("date"))
        if d is None:
            continue
        out.append(
            {
                "date_tuple": d,  # (y, m, d)
                "type": ev.get("type", ""),
                "summary": ev.get("summary", ""),
                "classification": ev.get("classification", "needs_check"),
                "classification_label": event_classification_label(
                    ev.get("classification", "needs_check")
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 핵심 투자 논리 (1~2문장)
# ---------------------------------------------------------------------------

def core_thesis_full(row: dict[str, Any]) -> str:
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    cat = category_label_ko(row.get("category", ""))
    theme = theme_label_ko(row.get("theme", ""))
    name = display_name(row.get("name_ko", ""), row["ticker"])
    tag = row.get("action_tag", "Watchlist")

    base = f"{name}는 {cat} / {theme} 카테고리에 속한 종목입니다."

    if tag == "Quality Dislocation":
        return (
            base
            + " 카테고리 리더로서의 thesis는 유효하나 최근 단기 이벤트로 주가 조정이 발생한 구간으로, "
            "thesis 훼손 여부 확인 시 중장기 Re-rating 여지가 존재합니다."
        )
    if tag == "Research Now":
        return (
            base
            + " 카테고리 catalyst와 종목별 Momentum이 동시에 살아있는 구간으로, "
            "투자 논리와 anti-thesis를 함께 정리한 뒤 분할 진입 영역을 정의할 단계입니다."
        )
    if tag == "Wait for Entry":
        return (
            base
            + " 테마 적합도는 유효하나 단기 valuation 부담이 확대된 구간으로, "
            "조정 시 entry 영역의 사전 정의가 필요합니다."
        )
    if tag == "Too Crowded":
        return (
            base
            + " 시장 컨센서스가 강하게 형성된 구간으로, multiple과 expectation 정합성 확인 후 "
            "trim/유지 여부를 판단할 단계입니다."
        )
    if tag == "Need Thesis Check":
        return (
            base
            + " 주가 하락 폭이 확대된 구간으로, 단기 이벤트 vs 구조적 훼손을 분리 판단해야 하는 단계입니다."
        )
    if tag == "Avoid":
        return (
            base
            + " 회계·조사·dilution 등 risk 키워드가 포함된 구간으로, anti-thesis 점검과 1차 자료 확인이 우선 과제입니다."
        )
    return base + " 카테고리 catalyst와 종목별 catalyst를 함께 모니터링할 단계입니다."


# ---------------------------------------------------------------------------
# 핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항 (각 3~5개 불렛)
# ---------------------------------------------------------------------------

def _event_point_supplement(row: dict[str, Any]) -> str | None:
    """최근 이벤트(Thesis 강화 분류)를 핵심 투자 포인트에 반영하는 보조 문장."""
    events = _curated_recent_events(row["ticker"])
    for ev in events:
        if ev.get("classification") == "strengthen":
            return f"최근 이벤트(이벤트 유형: {ev['type']}) 반영 시 기존 thesis가 확장될 여지가 있습니다."
    return None


def _event_check_supplement(row: dict[str, Any]) -> list[str]:
    """최근 이벤트 (확인 필요/신규 리스크)를 확인 필요 사항에 반영."""
    out: list[str] = []
    events = _curated_recent_events(row["ticker"])
    for ev in events:
        if ev.get("classification") in ("needs_check", "new_risk", "weaken"):
            out.append(f"최근 이벤트 점검: {ev.get('check') or ev.get('summary', '세부 조건 확인 필요')}")
    return out


def key_points_bullets_v2(row: dict[str, Any]) -> list[str]:
    """thesis_pillars를 우선 사용하고, 큐레이션 이벤트로 보강."""
    pillars = _curated_thesis_pillars(row["ticker"])
    if pillars:
        bits = list(pillars[:3])
        sup = _event_point_supplement(row)
        if sup:
            bits.append(sup)
        return bits
    # fallback: 이전 자동 생성 로직
    return key_points_bullets(row)


def key_points_bullets(row: dict[str, Any]) -> list[str]:
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    na = row.get("news_agg") or {}
    theme = theme_label_ko(row.get("theme", ""))
    bits: list[str] = []

    r3m = md.get("3m_return")
    r1y = md.get("1y_return")
    if r3m is not None and r3m > 0.10:
        bits.append(f"최근 3개월 {fmt_pct(r3m)}의 주가 Momentum이 유지되고 있습니다.")
    if r1y is not None and 0.10 <= r1y <= 0.6:
        bits.append(f"최근 1년 {fmt_pct(r1y)}의 안정적 주가 trend가 형성되어 있습니다.")
    if (sc.get("theme") or 0) >= 80:
        bits.append(f"{theme} 테마 적합도가 높아 카테고리 tailwind를 직접 수혜할 수 있는 포지셔닝입니다.")
    if (sc.get("news") or 0) >= 60 and (na.get("score_sum") or 0) > 0:
        bits.append("최근 뉴스 흐름의 톤이 우호적으로 형성되어 catalyst 확장 가능성이 있습니다.")
    om = md.get("operating_margin")
    if om is not None and om >= 0.20:
        bits.append(f"영업이익률 {om * 100:.0f}% 수준의 수익성으로 quality compounder 속성을 보유하고 있습니다.")
    cap = md.get("market_cap")
    if cap and cap >= 100e9:
        bits.append("시가총액 100B+ 수준의 카테고리 리더로서 유동성과 시장 신뢰도가 확보되어 있습니다.")
    dd = md.get("drawdown_from_52w_high")
    if dd is not None and 0.20 <= -dd <= 0.40 and row.get("action_tag") == "Quality Dislocation":
        bits.append(
            f"52주 고점 대비 {fmt_pct(-(-dd))} 조정으로 risk/reward 균형이 개선된 구간에 진입했습니다."
        )
    sup = _event_point_supplement(row)
    if sup:
        bits.insert(0, sup)
    if not bits:
        bits.append(
            f"현 시점의 정량 지표는 중립적이며, {theme} 카테고리 가치와 종목별 catalyst 중심으로 점검이 필요합니다."
        )
    return bits[:4]


def key_risks_bullets(row: dict[str, Any]) -> list[str]:
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    na = row.get("news_agg") or {}
    bits: list[str] = []

    if na.get("urgent"):
        bits.append("최근 뉴스에 회계·조사·dilution 등 risk 키워드가 포함되어 있어 anti-thesis 점검이 필요합니다.")
    if na.get("negative"):
        bits.append("최근 뉴스 톤이 부정적으로 형성되어 단기 sentiment 부담이 확대된 구간입니다.")
    pe = md.get("forward_pe") or md.get("trailing_pe")
    if pe and pe > 50:
        bits.append(f"forward/trailing PE {pe:.1f}x 수준의 valuation 부담이 multiple 변동성을 확대시킬 수 있습니다.")
    r6m = md.get("6m_return")
    if r6m and r6m > 0.6:
        bits.append(f"최근 6개월 {fmt_pct(r6m)} 상승으로 expectation이 높은 구간이며 Too Crowded 전환 가능성이 있습니다.")
    dd = md.get("drawdown_from_52w_high")
    if dd is not None and -dd > 0.40:
        bits.append(f"52주 고점 대비 {fmt_pct(-(-dd))} 조정으로 구조적 thesis 훼손 가능성을 점검해야 합니다.")
    if (sc.get("risk") or 0) >= 50:
        bits.append("리스크 점수가 50 이상으로 anti-thesis 우선 점검이 필요한 단계입니다.")
    om = md.get("operating_margin")
    if om is not None and om < 0:
        bits.append("영업이익이 적자 구간으로 cash burn 및 dilution 리스크가 존재합니다.")
    if not bits:
        bits.append("현 시점의 특이 risk 신호는 약하나 valuation과 catalyst의 정합성 확인이 필요합니다.")
    return bits[:4]


def check_items_bullets(row: dict[str, Any]) -> list[str]:
    theme = row.get("theme", "")
    by_theme: dict[str, list[str]] = {
        "ai_semiconductor": [
            "데이터센터 매출 증가율과 hyperscaler capex 가이던스 변화",
            "HBM/팹 capacity 추가 발표 및 ASP 추세",
            "FY 가이던스 revision 방향 (상향/유지/하향)",
        ],
        "ai_networking": [
            "AI 클러스터 ethernet 채택률과 hyperscaler 주문 강도",
            "ASIC/Optic 부문 backlog 및 ASP 추세",
            "Gross margin 추세 및 capacity 확장 진행도",
        ],
        "data_center_power": [
            "PJM/ERCOT 등 전력 수요 데이터 및 가격 추세",
            "PPA 체결과 capacity factor 변화",
            "Regulatory tailwind/headwind",
        ],
        "public_safety": [
            "소프트웨어 매출 비중과 ARR 성장률",
            "AI 모듈 attach rate 및 유료화 지표",
            "Evidence Cloud Lock-in 및 신규 contract 갱신",
        ],
        "defense": [
            "DoD 예산안과 backlog 변화",
            "우선순위 프로그램 (Sentinel, B-21 등) 진척도",
            "Free cash flow 가이던스",
        ],
        "space": [
            "발사 cadence와 NASA/DoD task order 수주",
            "정부 계약 비중 및 수익 인식 시점",
            "Gross margin 추세",
        ],
        "healthcare_infra": [
            "프로시저 볼륨 및 reimbursement 환경",
            "신제품 trial / 임상 결과",
            "가이던스 revision 방향",
        ],
        "platform": [
            "광고/구독 ARPU와 사용자 지표",
            "FCF 마진과 CAPEX 사이클",
            "AI 비용/수익화 균형",
        ],
        "ecommerce_platform": [
            "GMV 성장률과 take-rate",
            "결제/광고 등 부가 매출 비중",
            "FX 영향 및 지역 mix",
        ],
        "travel_mobility": [
            "ADR/RevPAR/booking 추세",
            "Travel demand 지표와 가이던스",
            "Regulation 리스크",
        ],
        "mobility_consumer": [
            "Robotaxi 진척도와 수요 데이터",
            "자동차 마진과 인센티브 추세",
            "Regulatory milestone",
        ],
        "consumer_brand": [
            "동일점포 매출과 트래픽",
            "브랜드 헬스 / 프로모션 강도",
            "글로벌 mix 변화",
        ],
    }
    # 큐레이션 key_metrics 우선
    metrics = _curated_key_metrics(row["ticker"])
    if metrics:
        base = list(metrics)
    else:
        base = list(by_theme.get(theme, ["실적 가이던스", "주요 catalyst", "Valuation 변화"]))
    base.extend(_event_check_supplement(row))
    return base[:5]


# ---------------------------------------------------------------------------
# 시나리오 분석
# ---------------------------------------------------------------------------

def scenarios(row: dict[str, Any]) -> dict[str, str]:
    cat = category_label_ko(row.get("category", ""))
    theme = theme_label_ko(row.get("theme", ""))
    name = display_name(row.get("name_ko", ""), row["ticker"])

    bull = (
        f"{theme} 카테고리 수요가 컨센서스를 상회하며 {name}의 매출/마진이 동시에 확장되는 시나리오. "
        "Multiple 유지와 EPS revision의 동반 상향으로 추가 upside 잠재력이 확대됩니다."
    )
    base = (
        f"{theme} 카테고리 수요가 컨센서스 수준에서 유지되며, {name}는 thesis 유지 속에서 "
        "share gain 정도와 마진 변동성이 핵심 변수가 됩니다."
    )
    bear = (
        f"수요 둔화 또는 가격/공급 경쟁 심화로 {name}의 마진이 압박을 받는 시나리오. "
        "Multiple compression이 EPS revision보다 빠르게 진행되어 단기 underperform 가능성이 존재합니다."
    )
    if row.get("action_tag") == "Quality Dislocation":
        bear = (
            f"구조적 thesis 훼손 신호 (시장 점유율 하락, 마진 base 이동, 카테고리 둔화)가 "
            f"추가로 확인되며 {name}의 Re-rating 시점이 지연되는 시나리오."
        )
        bull = (
            f"단기 이벤트성 부담이 해소되며 {name}의 카테고리 리더 프리미엄이 회복되고, "
            "Re-rating이 가시화되는 시나리오."
        )
    return {"bear": bear, "base": base, "bull": bull}


# ---------------------------------------------------------------------------
# 투자 현인 Lens
# ---------------------------------------------------------------------------

def lens_views(row: dict[str, Any]) -> list[dict[str, str]]:
    """투자 현인 Lens — 4개 카드. 각 카드는 name / headline / body 구성.

    카드 크기를 통일하기 위해 body 는 2~3 문장 이내로 짧게 유지한다.
    """
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    pe = md.get("forward_pe") or md.get("trailing_pe")
    r1y = md.get("1y_return")
    theme = row.get("theme", "")

    # Howard Marks — Valuation / 기대 괴리
    if pe and pe > 45:
        howard = (
            "현재 가격이 미래 기대를 과도하게 반영하고 있어 기대수익률은 제한될 수 있습니다."
        )
    elif pe and pe < 18:
        howard = (
            "Expectation이 낮아진 구간으로, 비대칭적 reward 구조가 형성될 수 있는 단계입니다."
        )
    else:
        howard = (
            "Valuation은 중립 구간이며, 기대수익률은 EPS revision의 방향성에 의해 결정될 가능성이 큽니다."
        )

    # Peter Lynch — 성장률 대비 가격
    if r1y and r1y > 0.30 and pe and pe > 25:
        lynch = (
            "성장률·침투율이 유지되면 카테고리 확장 여지가 있는 고성장 후보로 해석할 수 있습니다."
        )
    elif theme in ("public_safety", "space", "healthcare_infra", "ai_networking"):
        lynch = (
            "카테고리 침투율이 초기 단계로, 실적의 점진적 가속이 multiple을 정당화할 수 있는 구조입니다."
        )
    else:
        lynch = (
            "성장률이 안정 구간에 진입한 카테고리로, share gain과 단가 인상 여력이 핵심 변수입니다."
        )

    # SpaceX — 비용곡선 붕괴와 인프라 장악
    if theme in ("ai_semiconductor", "ai_networking", "data_center_power"):
        spacex = (
            "핵심 비용을 낮추고 사용 빈도를 확대하며, 그 위에 데이터·운영 인프라를 구축할 수 있는지가 관건입니다."
        )
    elif theme in ("public_safety", "defense", "space"):
        spacex = (
            "하드웨어를 발판으로 데이터·운영 플랫폼을 구축하고 사용 빈도를 확대할 수 있는 구조인지가 관건입니다."
        )
    elif theme == "healthcare_infra":
        spacex = (
            "단위경제성을 개선하면서 카테고리 침투를 확대할 수 있는 구조인지 확인이 필요합니다."
        )
    else:
        spacex = (
            "사용 빈도와 단위경제의 동반 개선이 multiple 정당화의 전제 조건입니다."
        )

    # Quant — 숫자로 확인되는 변화
    m = sc.get("momentum") or 50
    n = sc.get("news") or 50
    if m >= 65 and n >= 60:
        quant = (
            "Momentum·Earnings Revision·Revenue Acceleration이 동시에 개선되는 구간으로 우호적 신호가 누적되는 단계입니다."
        )
    elif m < 45:
        quant = (
            "Momentum 지표가 우호적이지 않으며, Revision 지표 개선이 선행되어야 점수가 회복될 수 있습니다."
        )
    else:
        quant = (
            "Momentum은 중립이며, Earnings Revision 방향성이 다음 분기 신호를 결정할 변수입니다."
        )

    return [
        {"name": "Howard Marks Lens", "headline": "가격과 기대의 괴리", "body": howard},
        {"name": "Peter Lynch Lens", "headline": "성장률 대비 가격", "body": lynch},
        {"name": "SpaceX Lens", "headline": "비용곡선 붕괴와 인프라 장악", "body": spacex},
        {"name": "Quant Lens", "headline": "숫자로 확인되는 변화", "body": quant},
    ]


# ---------------------------------------------------------------------------
# Anti-Thesis
# ---------------------------------------------------------------------------

def anti_thesis(row: dict[str, Any]) -> list[str]:
    bits: list[str] = [
        "operating margin이 두 분기 연속 하락 전환되는 경우",
        "주요 가이던스가 컨센서스 대비 하향되는 경우",
        "뉴스 흐름에 회계·조사·dilution 등 risk 키워드가 등장하는 경우",
    ]
    theme = row.get("theme", "")
    if theme == "ai_semiconductor":
        bits.append("hyperscaler capex revision이 둔화로 전환되는 경우")
    if theme == "public_safety":
        bits.append("소프트웨어 매출 비중 정체 또는 attach rate 하락이 확인되는 경우")
    if theme == "data_center_power":
        bits.append("AI 데이터센터 전력 수요 둔화 신호가 확인되는 경우")
    if theme == "healthcare_infra":
        bits.append("프로시저 볼륨 둔화 또는 reimbursement 환경 악화가 확인되는 경우")
    return bits[:5]


# ---------------------------------------------------------------------------
# 종합 판단
# ---------------------------------------------------------------------------

def final_judgment(row: dict[str, Any]) -> str:
    name = display_name(row.get("name_ko", ""), row["ticker"])
    tag = row.get("action_tag", "Watchlist")
    cat = category_label_ko(row.get("category", ""))
    md = row.get("market_data") or {}
    pe = md.get("forward_pe") or md.get("trailing_pe")

    if tag == "Research Now":
        return (
            f"현 시점에서 {name}는 즉시 매수 후보라기보다 Research Now 후보로 분류됩니다. "
            f"{cat} 카테고리 catalyst와 종목별 Momentum이 우호적으로 작동하나, "
            "Valuation 부담과 anti-thesis를 함께 점검한 뒤 분할 진입 영역의 사전 정의가 필요합니다."
        )
    if tag == "Quality Dislocation":
        return (
            f"현 시점에서 {name}는 Quality Dislocation 후보로 분류됩니다. "
            "단기 이벤트성 주가 조정인지 구조적 thesis 훼손인지 분리 판단이 선행되어야 하며, "
            "thesis 유효성 확인 시 중장기 Re-rating 여지가 존재합니다."
        )
    if tag == "Wait for Entry":
        return (
            f"{name}는 카테고리 적합도는 유효하나 단기 valuation 부담이 확대된 구간으로, "
            "Wait for Entry 후보로 분류됩니다. 조정 시 분할 진입 영역의 사전 정의가 필요합니다."
        )
    if tag == "Too Crowded":
        return (
            f"{name}는 시장 컨센서스가 강하게 형성된 Too Crowded 구간으로, "
            "Multiple과 expectation 정합성 점검 후 trim/유지 여부의 판단이 필요합니다."
        )
    if tag == "Need Thesis Check":
        return (
            f"{name}는 주가 하락 폭이 확대된 구간으로, Need Thesis Check 후보로 분류됩니다. "
            "단기 이벤트 vs 구조적 훼손 분리 판단이 우선 과제입니다."
        )
    if tag == "Avoid":
        return (
            f"{name}는 risk 키워드가 포함된 구간으로 신규 진입은 보류 권고됩니다. "
            "anti-thesis 점검과 1차 자료 확인이 선행되어야 합니다."
        )
    return (
        f"{name}는 {cat} 카테고리 관찰 후보로, 카테고리 catalyst와 종목별 catalyst를 함께 모니터링할 단계입니다."
    )


# ---------------------------------------------------------------------------
# 지표 카드용 정성 라벨
# ---------------------------------------------------------------------------

def upside_potential(row: dict[str, Any]) -> str:
    sc = row.get("scores") or {}
    md = row.get("market_data") or {}
    final = sc.get("final_score") or 50
    dd = md.get("drawdown_from_52w_high")
    abs_dd = -dd if dd is not None else 0
    if row.get("action_tag") == "Avoid":
        return "제한적"
    if final >= 75 and abs_dd >= 0.20:
        return "높음"
    if final >= 65:
        return "중상"
    if final >= 50:
        return "중립"
    return "제한적"


def risk_grade(row: dict[str, Any]) -> str:
    sc = row.get("scores") or {}
    risk = sc.get("risk") or 0
    if risk >= 50:
        return "High"
    if risk >= 25:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Detail 빌더
# ---------------------------------------------------------------------------

def build_stock_detail(row: dict[str, Any]) -> dict[str, Any]:
    md = row["market_data"]
    sc = row["scores"]
    return {
        "name_kr": display_name(row.get("name_ko", ""), row["ticker"]),
        "ticker": row["ticker"],
        "category": category_label_ko(row.get("category", "")),
        "theme": theme_label_ko(row.get("theme", "")),
        "investment_type": investment_type(row),
        "company_type": row.get("company_type") or _curated_company_type(row["ticker"]),
        "judgment_tag": row.get("action_tag", "Watchlist"),
        "simple_explanation": simple_explanation(row),
        "recent_events": recent_events(row),
        "thesis_full": core_thesis_full(row),
        "current_price": fmt_money(md.get("current_price")),
        "daily_return": fmt_pct(md.get("daily_return")),
        "drawdown": fmt_pct(md.get("drawdown_from_52w_high")),
        "final_score": sc.get("final_score"),
        "score_label": score_label(sc.get("final_score") or 0),
        "upside": upside_potential(row),
        "risk_grade": risk_grade(row),
        "key_points": key_points_bullets_v2(row),     # ← thesis_pillars 우선
        "key_risks": key_risks_bullets(row),
        "check_items": check_items_bullets(row),       # ← curated key_metrics 우선
        "anti_thesis": anti_thesis(row),
        "final_judgment": final_judgment(row),
        "research_quality": research_quality(row),
        # 하위 호환 (UI에서 더 이상 사용하지 않지만 다른 코드가 참조 가능)
        "scenarios": scenarios(row),
        "lens_views": lens_views(row),
    }
