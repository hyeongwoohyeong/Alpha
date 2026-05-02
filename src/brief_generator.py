"""오늘의 투자 브리프 — 리포트 톤 한국어 생성.

이 파일은 룰 기반 분석으로 다음을 만든다:
- 종목별 투자 유형 / 핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항
- 금일 핵심 판단 (오늘의 결론, 한 단락)
- 금일 주요 알림 (3개, 리포트 문체)
- 금일 점검 사항 (3개, 종목명+티커 prefix)

향후 LLM 연동 시 generate_daily_judgment / core_thesis 등을 LLM 호출로 교체하면 된다.
"""
from __future__ import annotations

from typing import Any

from .curated import macro_issues as _curated_macro_issues
from .curated import market_environment_blocks as _curated_market_blocks
from .universe import category_label_ko, theme_label_ko
from .utils import display_name, fmt_pct


# ---------------------------------------------------------------------------
# 투자 유형 (theme → 리포트용 분류명)
# ---------------------------------------------------------------------------

INVESTMENT_TYPE_BY_THEME: dict[str, str] = {
    "ai_semiconductor": "AI 반도체 / Compute Layer",
    "ai_networking": "AI 인프라 / Connectivity Layer",
    "data_center_power": "전력 인프라 / Energy Tailwind",
    "public_safety": "공공안전 OS 전환 후보 / Civilization Alpha",
    "defense": "방산 / Defense Cycle",
    "space": "우주 인프라 / Long-duration Bet",
    "healthcare_infra": "헬스케어 인프라 / Quality Compounder",
    "platform": "글로벌 플랫폼 / Quality Platform",
    "ecommerce_platform": "이커머스 플랫폼 / Quality Platform",
    "travel_mobility": "여행 / Reopening Beneficiary",
    "mobility_consumer": "모빌리티 / Robotaxi Optionality",
    "consumer_brand": "글로벌 컨슈머 브랜드",
}


def investment_type(row: dict[str, Any]) -> str:
    return INVESTMENT_TYPE_BY_THEME.get(row.get("theme", ""), "관심 후보")


# ---------------------------------------------------------------------------
# Top picks 선정 (테마 분산)
# ---------------------------------------------------------------------------

def select_top_picks(rows: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    """company_type 분포를 고려한 picks 선정.

    우선순위 (각 카테고리당 최소 1개):
    1. Quality Dislocation (1~2개)
    2. Civilization Alpha (1개)
    3. Re-rating Candidate (1개)
    4. Research Now 의 Structural Growth (나머지)

    AI 인프라 카테고리 최대 2개, 같은 theme 최대 2개 제약.
    """
    cands = [
        r
        for r in rows
        if (r.get("scores") or {}).get("final_score") is not None
        and r.get("action_tag") not in ("Avoid", "Data Unavailable")
    ]
    cands.sort(key=lambda r: r["scores"]["final_score"], reverse=True)

    picked: list[dict[str, Any]] = []
    used_categories: dict[str, int] = {}
    used_themes: dict[str, int] = {}

    def _try_add(r):
        if r in picked:
            return False
        if (
            r["category"] == "AI Infrastructure"
            and used_categories.get("AI Infrastructure", 0) >= 2
        ):
            return False
        if used_themes.get(r["theme"], 0) >= 2:
            return False
        picked.append(r)
        used_categories[r["category"]] = used_categories.get(r["category"], 0) + 1
        used_themes[r["theme"]] = used_themes.get(r["theme"], 0) + 1
        return True

    # 1. Quality Dislocation 1~2개 (Action Tag 기준)
    qd = [r for r in cands if r.get("action_tag") == "Quality Dislocation"]
    for r in qd[:2]:
        _try_add(r)
        if len(picked) >= n:
            return picked

    # 2. Civilization Alpha — Research Now 인 것 우선
    civ = [
        r for r in cands
        if r.get("company_type") == "Civilization Alpha"
        and r.get("action_tag") == "Research Now"
    ]
    for r in civ[:1]:
        _try_add(r)
        if len(picked) >= n:
            return picked

    # 3. Re-rating Candidate — Research Now 우선
    rrc = [
        r for r in cands
        if r.get("company_type") == "Re-rating Candidate"
        and r.get("action_tag") == "Research Now"
    ]
    for r in rrc[:1]:
        _try_add(r)
        if len(picked) >= n:
            return picked

    # 4. Research Now 나머지 (final score 순)
    rn = [r for r in cands if r.get("action_tag") == "Research Now" and r not in picked]
    for r in rn:
        _try_add(r)
        if len(picked) >= n:
            return picked

    # 5. fallback: 점수 순 + 제약 만족하는 것
    for r in cands:
        _try_add(r)
        if len(picked) >= n:
            break
    return picked


# ---------------------------------------------------------------------------
# 종목 카드 텍스트 (리포트 톤)
# ---------------------------------------------------------------------------

def core_thesis(row: dict[str, Any]) -> str:
    """핵심 투자 포인트 — 한 문단."""
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    na = row.get("news_agg") or {}
    tag = row.get("action_tag", "Watchlist")
    theme = theme_label_ko(row.get("theme", ""))
    name = display_name(row.get("name_ko", ""), row["ticker"])

    if tag == "Quality Dislocation":
        dd = md.get("drawdown_from_52w_high")
        dd_str = fmt_pct(-(-dd)) if dd is not None else "조정"
        return (
            f"{name}는 {theme} 카테고리 리더로서, 52주 고점 대비 {dd_str} 수준의 주가 조정 구간에 진입한 "
            "Quality Dislocation 후보입니다. 단기 이벤트성 조정 여부와 thesis 유효성 점검이 선행 과제입니다."
        )
    if tag == "Research Now":
        bits = []
        if (sc.get("momentum") or 0) >= 60:
            r3 = md.get("3m_return")
            if r3 is not None:
                bits.append(f"최근 3개월 {fmt_pct(r3)}의 주가 Momentum")
        if (sc.get("theme") or 0) >= 80:
            bits.append(f"{theme} 테마 적합도가 높은 카테고리 포지셔닝")
        if (sc.get("news") or 0) >= 60 and (na.get("score_sum") or 0) > 0:
            bits.append("최근 뉴스 흐름의 우호적 톤")
        if not bits:
            body = f"{theme} 카테고리 적합도"
        elif len(bits) == 1:
            body = bits[0]
        else:
            body = ", ".join(bits[:-1]) + " 및 " + bits[-1]
        return (
            f"{name}는 {body}이 동시에 확인되는 구간으로, "
            "투자 논리와 가장 큰 anti-thesis를 함께 정리할 만한 후보로 분류됩니다."
        )
    if tag == "Wait for Entry":
        return (
            f"{name}는 {theme} 테마 적합도는 유효하나 단기 주가 급등으로 valuation 부담이 확대된 구간으로, "
            "분할 진입 영역의 사전 정의가 필요합니다."
        )
    if tag == "Too Crowded":
        return (
            f"{name}는 시장 컨센서스가 강하게 형성된 구간으로, "
            "Multiple과 expectation 정합성 점검 후 trim/유지 여부를 판단할 단계입니다."
        )
    if tag == "Need Thesis Check":
        return (
            f"{name}는 주가 하락 폭은 확대되었으나 원인이 단기 이벤트인지 구조적 훼손인지 분리 판단이 "
            "필요한 단계로, 정밀 검토 후보로 분류됩니다."
        )
    if tag == "Avoid":
        return (
            f"{name}는 회계·조사·dilution 등 risk 키워드가 포함된 구간으로, "
            "신규 진입은 보류하고 anti-thesis와 1차 자료 확인이 우선 과제입니다."
        )
    return (
        f"{name}는 {theme} 카테고리 관찰 후보로, 카테고리 모멘텀과 종목별 catalyst를 함께 모니터링할 단계입니다."
    )


def key_risk(row: dict[str, Any]) -> str:
    md = row.get("market_data") or {}
    sc = row.get("scores") or {}
    na = row.get("news_agg") or {}

    if na.get("urgent"):
        return "최근 뉴스에 회계·조사·dilution 등 risk 키워드가 포함되어 있어 anti-thesis 우선 점검이 필요합니다."
    if (sc.get("risk") or 0) >= 50:
        return "주가 조정과 부정적 뉴스 흐름이 동반되는 구간으로, downside 리스크 점검이 필요합니다."
    pe = md.get("forward_pe") or md.get("trailing_pe")
    if pe and pe > 50:
        return f"forward PE {pe:.1f}x 수준의 valuation 부담이 단기 주가의 변동성을 확대시킬 수 있습니다."
    r6m = md.get("6m_return")
    if r6m and r6m > 0.6:
        return f"최근 6개월 {fmt_pct(r6m)} 상승으로 expectation이 높은 구간이며, Too Crowded 전환 가능성이 있습니다."
    dd = md.get("drawdown_from_52w_high")
    if dd is not None and -dd > 0.30:
        return f"52주 고점 대비 {fmt_pct(-(-dd))} 수준의 조정으로, 구조적 thesis 훼손 여부 확인이 필요합니다."
    return "단기 catalyst 부재 시 시장 평균 대비 underperform 가능성이 존재합니다."


def check_items(row: dict[str, Any]) -> str:
    """확인 필요 사항 — 카드용 한 줄 요약."""
    theme = row.get("theme", "")
    by_theme: dict[str, str] = {
        "ai_semiconductor": "데이터센터 capex revision 및 GPU/HBM 수급 지표",
        "ai_networking": "AI 클러스터 ethernet 채택률, hyperscaler 주문 강도",
        "data_center_power": "PJM/ERCOT 전력 수요, PPA 체결 흐름, 규제 환경",
        "public_safety": "소프트웨어 매출 비중, AI 리포팅 유료화 지표, Evidence Cloud Lock-in",
        "defense": "DoD 예산안, backlog 변화, 우선순위 프로그램 진척도",
        "space": "발사 cadence, NASA/DoD task order 수주, gross margin 추세",
        "healthcare_infra": "프로시저 볼륨, 가이던스 revision 방향, reimbursement 환경",
        "platform": "광고/구독 ARPU, FCF 마진, AI 비용/수익화 균형",
        "ecommerce_platform": "GMV 성장률, take-rate 변화, 부가 매출 비중",
        "travel_mobility": "ADR/RevPAR/booking 추세, 가이던스 변동성",
        "mobility_consumer": "robotaxi 진척도, 마진 추세, regulatory milestone",
        "consumer_brand": "동일점포 매출, 트래픽, 프로모션 강도",
    }
    return by_theme.get(theme, "최근 실적, 가이던스, 주요 catalyst 점검")


# ---------------------------------------------------------------------------
# 금일 핵심 판단 / 시장 환경 / 알림 / 점검 사항
# ---------------------------------------------------------------------------

def daily_judgment(rows: list[dict[str, Any]], picks: list[dict[str, Any]]) -> str:
    """금일 핵심 판단 — 한 단락 (3~4 문장)."""
    if not picks:
        return (
            "금일은 명확히 부각되는 신규 후보가 부족합니다. 신규 진입보다 보유 종목의 thesis 재확인과 "
            "관심종목의 catalyst 모니터링에 시간을 배분하는 것이 유효합니다."
        )

    qd_count = sum(1 for r in picks if r.get("action_tag") == "Quality Dislocation")
    rn_count = sum(1 for r in picks if r.get("action_tag") == "Research Now")
    avoid_count = sum(1 for r in rows if r.get("action_tag") == "Avoid")

    if qd_count >= 2:
        return (
            "금일은 AI 인프라 대장주보다 우량주 과매도 후보와 카테고리 리더의 thesis 재확인을 우선할 만한 환경입니다. "
            "단기 주가 조정의 원인이 이벤트성인지 구조적인지 분리 판단하고, 분할 진입 영역의 사전 정의가 필요합니다."
        )
    if rn_count >= 3:
        return (
            "금일은 Momentum과 뉴스 흐름이 동시에 우호적으로 작동하는 종목군이 확장된 환경입니다. "
            "Research Now 후보 중심으로 투자 논리와 anti-thesis를 정리하되, valuation 부담이 큰 종목은 분할 진입 관점으로 접근하는 것이 유효합니다."
        )
    if avoid_count >= 3:
        return (
            "금일은 risk 신호가 확대된 환경입니다. 신규 진입보다 보유 종목의 anti-thesis 점검과 "
            "downside 리스크 관리에 우선순위를 두는 것이 유효합니다."
        )
    if qd_count == 1:
        top = picks[0]
        return (
            f"금일은 {display_name(top['name_ko'], top['ticker'])} 등 우량주 과매도 후보를 우선 점검하면서, "
            "관심종목의 Action Tag 변화와 카테고리 catalyst를 함께 모니터링할 만한 환경입니다."
        )
    return (
        "금일은 신규 후보 발굴보다 기존 관심종목의 thesis 재확인과 카테고리 catalyst 모니터링에 "
        "시간을 배분하는 것이 유효합니다."
    )


def daily_alerts(rows: list[dict[str, Any]], n: int = 3) -> list[str]:
    """금일 주요 알림 — 종목명(티커) 포함, 리포트 문체."""
    alerts: list[str] = []
    seen: set[str] = set()

    def add(t: str, msg: str):
        if t in seen or len(alerts) >= n:
            return
        seen.add(t)
        alerts.append(msg)

    for r in rows:
        na = r.get("news_agg") or {}
        if na.get("urgent"):
            add(
                r["ticker"],
                f"{display_name(r['name_ko'], r['ticker'])}는 최근 뉴스에 회계·조사·dilution 등 risk 키워드가 "
                "포함되어 anti-thesis 점검이 필요합니다.",
            )

    for r in rows:
        if len(alerts) >= n:
            break
        md = r.get("market_data") or {}
        dd = md.get("drawdown_from_52w_high")
        if dd is None:
            continue
        if -dd >= 0.25 and r.get("action_tag") not in ("Avoid",):
            add(
                r["ticker"],
                f"{display_name(r['name_ko'], r['ticker'])}는 52주 고점 대비 약 {fmt_pct(-(-dd))} 하락하며 "
                "Quality Dislocation 검토 구간에 진입했습니다.",
            )

    for r in rows:
        if len(alerts) >= n:
            break
        if r.get("action_tag") == "Too Crowded":
            md = r.get("market_data") or {}
            r1y = md.get("1y_return") or 0
            add(
                r["ticker"],
                f"{display_name(r['name_ko'], r['ticker'])}는 1년 {fmt_pct(r1y)} 상승으로 "
                "Valuation 부담이 확대되어 Too Crowded 여부 점검이 필요합니다.",
            )

    for r in rows:
        if len(alerts) >= n:
            break
        sc = r.get("scores") or {}
        if (sc.get("news") or 0) >= 75 and r.get("action_tag") not in ("Avoid",):
            add(
                r["ticker"],
                f"{display_name(r['name_ko'], r['ticker'])}는 최근 뉴스 강도가 확대되어 "
                "catalyst 정합성 점검이 필요합니다.",
            )

    return alerts[:n]


def daily_check_items(picks: list[dict[str, Any]], n: int = 3) -> list[str]:
    """금일 점검 사항 — 1.종목명(티커): ... 형식."""
    out: list[str] = []
    for i, r in enumerate(picks[:n], start=1):
        out.append(f"{i}. {display_name(r['name_ko'], r['ticker'])}: {check_items(r)} 점검")
    return out


def market_environment(market_summary: str) -> str:
    """[하위 호환] 금일 시장 환경 — 단일 문자열."""
    if not market_summary or "실패" in market_summary:
        return "금일 시장 데이터 수집에 실패했습니다. 데이터 업데이트를 다시 시도하세요."
    return market_summary


def market_environment_blocks(market_summary: str | None = None) -> list[dict[str, str]]:
    """금일 시장 환경 — 3개 카드 블록 (지수/금리/주도 테마)."""
    return _curated_market_blocks(market_summary)


def macro_issues() -> list[dict[str, str]]:
    """금일 주요 매크로·정책·지정학 이슈 (최대 3개)."""
    return _curated_macro_issues()[:3]


# ---------------------------------------------------------------------------
# Brief 빌더
# ---------------------------------------------------------------------------

def build_daily_brief(
    rows: list[dict[str, Any]],
    market_proxies: dict[str, dict[str, Any]],
    market_summary: str,
) -> dict[str, Any]:
    picks = select_top_picks(rows, n=5)
    return {
        "judgment": daily_judgment(rows, picks),
        "market_environment": market_environment(market_summary),
        "market_blocks": market_environment_blocks(market_summary),
        "macro_issues": macro_issues(),
        "picks": picks,
        "alerts": daily_alerts(rows, n=3),
        "check_items": daily_check_items(picks, n=3),
    }


# ---------------------------------------------------------------------------
# 하위 호환 alias (구 함수명 사용처가 있을 경우 대비)
# ---------------------------------------------------------------------------

why_to_watch = core_thesis
biggest_risk = key_risk


def next_action(row: dict[str, Any]) -> str:
    return check_items(row) + " 점검"
