"""종목 리서치 본문 생성 (DB 저장용).

run_research.py 의 step_generate_stock_research 가 호출.
큐레이션 (curated.py) + scoring 결과 + market_data 를 결합해
다음 9개 필드를 생성한다.

1. easy_explanation       — 이 회사는 쉽게 말해
2. core_thesis            — 핵심 투자 논리
3. key_points (list)      — 핵심 투자 포인트
4. key_risks  (list)      — 주요 리스크
5. check_items (list)     — 확인 필요 사항
6. anti_thesis (list)     — Anti-Thesis
7. final_view             — 종합 판단
8. research_quality (dict) — 리서치 품질 메타
9. (events 메타는 events 테이블에 별도 저장)
"""
from __future__ import annotations

from typing import Any

from .curated import (
    company_type as _curated_company_type,
    key_metrics as _curated_key_metrics,
    simple_explanation as _curated_simple_explanation,
    thesis_pillars as _curated_thesis_pillars,
)
from .stock_detail import (
    anti_thesis,
    core_thesis_full,
    final_judgment,
    key_risks_bullets,
    research_quality,
)
from .universe import theme_label_ko
from .utils import display_name


def build_stock_research(row: dict[str, Any]) -> dict[str, Any]:
    """row 는 build_rows() 결과 (market_data + scores + curated_events + action_tag)."""
    ticker = row["ticker"]

    # 1. easy_explanation (큐레이션 우선)
    easy = _curated_simple_explanation(
        ticker, fallback_theme_label=theme_label_ko(row.get("theme", ""))
    ) or (
        f"{display_name(row.get('name_ko', ''), ticker)}는 "
        f"{theme_label_ko(row.get('theme', ''))} 카테고리에 속한 회사입니다. "
        "사업 구조와 최근 이벤트는 1차 자료 확인이 필요합니다."
    )

    # 2. core_thesis
    core = core_thesis_full(row)

    # 3. key_points (thesis_pillars 우선)
    pillars = _curated_thesis_pillars(ticker)
    if pillars:
        kpts = list(pillars[:3])
        # 큐레이션 이벤트가 strengthen 이면 보충 한 줄
        for ev in row.get("curated_events") or []:
            if ev.get("classification") == "strengthen":
                kpts.append(
                    f"최근 이벤트({ev.get('type','')}) 반영 시 thesis 확장 가능성"
                )
                break
    else:
        kpts = ["카테고리 catalyst 점검", "재무 추세 점검", "Valuation 정합성 점검"]

    # 4. key_risks
    krsks = key_risks_bullets(row)

    # 5. check_items (key_metrics 큐레이션 우선)
    metrics = _curated_key_metrics(ticker)
    if metrics:
        chks = list(metrics[:5])
    else:
        chks = ["실적 가이던스", "주요 catalyst", "Valuation 변화"]
    # 이벤트 needs_check / new_risk 보강
    for ev in row.get("curated_events") or []:
        if ev.get("classification") in ("needs_check", "new_risk", "weaken"):
            chks.append(f"최근 이벤트 점검: {ev.get('check') or ev.get('summary', '')}")
            break

    # 6. anti_thesis
    anti = anti_thesis(row)

    # 7. final_view
    final_view = final_judgment(row)

    # 8. research_quality
    rq = research_quality(row)

    return {
        "easy_explanation": easy,
        "core_thesis": core,
        "key_points": kpts,
        "key_risks": krsks,
        "check_items": chks,
        "anti_thesis": anti,
        "final_view": final_view,
        "research_quality": rq,
    }


def short_rationale(row: dict[str, Any]) -> str:
    """scores.rationale 에 한 줄 저장하기 위한 요약."""
    sc = row.get("scores") or {}
    md = row.get("market_data") or {}
    tag = row.get("action_tag", "Watchlist")
    company_type = row.get("company_type") or _curated_company_type(row["ticker"])

    parts = [f"{company_type}"]
    final = sc.get("final_score")
    if final is not None:
        parts.append(f"Final {final:.1f}")
    dd = md.get("drawdown_from_52w_high")
    if dd is not None:
        parts.append(f"52w DD {-dd*100:.1f}%")
    parts.append(f"→ {tag}")

    # 큐레이션 이벤트 영향
    impacts = []
    for ev in row.get("curated_events") or []:
        ti = ev.get("thesis_impact")
        if ti and ti not in ("확인 필요", "단기 노이즈"):
            impacts.append(ti)
    if impacts:
        parts.append("/ " + ", ".join(impacts))

    return " · ".join(parts)
