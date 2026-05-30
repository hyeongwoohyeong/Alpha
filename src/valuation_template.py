"""Valuation 탭 — IC Memo + Financial Model 템플릿.

PE/IC Memo 양식 (1페이지 압축 + 분석 깊이) 기반 generic 한 데이터 구조 + HTML 렌더.
JKL/특정 회사 로고·이름 제외 — 사용자 본인 워크플로 도구.

데이터 schema (build_*** 함수 입력):
{
  'company': {
    'name_ko': str, 'name_en': str, 'ticker': str,
    'industry': str, 'market_cap_krw': float | None, 'is_listed': bool,
  },
  'ic_memo': {
    'investment_verdict': '긍정 의견' | '중립 의견' | '부정 의견',
    'verdict_oneliner': str,  # 한 문단 요약
    'investment_thesis': [str, str, str],  # 3개 핵심 논지
    'investment_points': [str, ...],       # 3-5개 driver
    'risks_and_mitigants': [{'risk': str, 'mitigant': str}, ...],
    'financials_narrative': str,  # 예상 재무제표 표 위 한 줄 요약
    'returns_narrative': str,     # IRR/MOIC 표 위 한 줄 요약
    'additional_dd': {'commercial': [str], 'financial': [str], 'legal': [str], 'tax': [str], 'market': [str]},
  },
  'financials': {
    'years': [int, ...],  # 8개년 (2023A-2030F 등)
    'a_years_count': int, # actual 연도 수 (예: 3 → 첫 3개는 A, 나머지 F)
    'revenue': [float, ...],            'revenue_growth': [float | None, ...],  # 분수 (0.34 = 34%)
    'ebitda': [...],                    'ebitda_margin': [...],
    'ebitda_growth': [...],
    'net_income': [...],
    'assets': [...], 'liabilities': [...], 'debt_ratio': [...], 'equity': [...],
    'ocf': [...],
  },
  'scenarios': {
    'bull': {'revenue_cagr': float, 'ebitda_margin_terminal': float, 'exit_ebitda': float, 'exit_multiple': float, 'irr': float, 'moic': float},
    'base': {...},
    'bear': {...},  # 또는 workout/상환
  },
  'returns': {
    'irr_table': {'multiples': [10.0, 11.0, 12.0], 'bull': [...], 'base': [...], 'workout': [...]},
    'moic_table': {'multiples': [10.0, 11.0, 12.0], 'bull': [...], 'base': [...], 'workout': [...]},
  },
  'investment_structure': {
    'instrument': 'CB' | 'Common' | 'RCPS' | 'FI Minority' | 'Public Market',
    'investment_krw_mm': float, 'holding_period_years': int,
    'ytm': float | None, 'coupon': float | None,
    'narrative': str,
  },
  'judgment': {  # 사용자 10대 질문 답변
    'good_company': str, 'good_investment_now': str, 'qld_alternative': str,
    'irr_moic_sufficient': str, 'worst_case_loss': str, 'earnings_visible': str,
    'valuation_band': str,  # 싸다/적정/비싸다
    'catalyst_quality': str, 'instrument_recommendation': str, 'next_action': str,
  },
  'sources': [{'title': str, 'url': str | None}, ...],
  'generated_at': str,  # ISO
}

KRW million 단위 통일 — financials 의 모든 숫자는 백만원.
"""
from __future__ import annotations

from typing import Any
from datetime import datetime


# ---------------------------------------------------------------------------
# 보조 — 포맷팅
# ---------------------------------------------------------------------------

def _fmt_int(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v: Any, digits: int = 0) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_multiple(v: Any, digits: int = 1) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):.{digits}f}x"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# IC Memo HTML 렌더 (1페이지 압축 → 화면용)
# ---------------------------------------------------------------------------

def render_ic_memo_html(data: dict[str, Any]) -> str:
    """IC Memo HTML 렌더 — 사용자 워크플로용. JKL 로고 없음."""
    company = data.get("company", {}) or {}
    memo = data.get("ic_memo", {}) or {}
    fin = data.get("financials", {}) or {}
    ret = data.get("returns", {}) or {}
    structure = data.get("investment_structure", {}) or {}

    name_ko = company.get("name_ko") or company.get("ticker") or "—"
    ticker = company.get("ticker", "")
    industry = company.get("industry", "")
    mcap = company.get("market_cap_krw")
    mcap_str = f"  ·  시가총액 ₩{(mcap or 0) / 1e8:,.0f}억" if mcap else ""

    verdict = memo.get("investment_verdict", "—")
    verdict_color = (
        "#22C55E" if "긍정" in verdict
        else "#EF4444" if "부정" in verdict
        else "#94A3B8"
    )
    verdict_oneliner = memo.get("verdict_oneliner", "")

    # 헤더
    parts: list[str] = []
    parts.append(
        '<div style="background:var(--panel,#0F172A); border-radius:10px; padding:18px 22px; '
        'border-left:4px solid ' + verdict_color + ';">'
        '<div style="display:flex; justify-content:space-between; align-items:baseline; '
        'gap:12px; flex-wrap:wrap; margin-bottom:6px;">'
        '<div>'
        '<div style="font-size:11px; color:var(--muted,#94A3B8); letter-spacing:.05em;">INVESTMENT COMMITTEE MEMO</div>'
        f'<div style="font-size:20px; color:var(--text,#F8FAFC); font-weight:700; margin-top:3px;">'
        f'{name_ko} <span style="font-size:14px; color:var(--muted,#94A3B8); font-weight:400;">({ticker})</span></div>'
        f'<div style="font-size:12px; color:var(--muted,#94A3B8); margin-top:3px;">{industry}{mcap_str}</div>'
        '</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:11px; color:var(--muted,#94A3B8);">투자 결론</div>'
        f'<div style="font-size:22px; font-weight:700; color:{verdict_color}; line-height:1;">{verdict}</div>'
        '</div>'
        '</div>'
    )
    if verdict_oneliner:
        parts.append(
            f'<div style="margin-top:8px; padding-top:8px; border-top:1px solid rgba(148,163,184,0.15); '
            f'font-size:13px; color:var(--text,#F8FAFC); line-height:1.6;">{verdict_oneliner}</div>'
        )
    parts.append('</div>')

    # 투자 논지
    parts.append(_section_header("투자 논지"))
    parts.append(_render_numbered_list(memo.get("investment_thesis") or []))

    # 투자 포인트
    parts.append(_section_header("투자 포인트"))
    parts.append(_render_numbered_list(memo.get("investment_points") or []))

    # 리스크 및 대응
    parts.append(_section_header("리스크 및 대응"))
    risks = memo.get("risks_and_mitigants") or []
    if risks:
        rows = ""
        for i, r in enumerate(risks, 1):
            rows += (
                '<div style="display:flex; gap:12px; padding:8px 0; '
                'border-top:1px solid rgba(148,163,184,0.12);">'
                f'<div style="flex:0 0 24px; color:#EF4444; font-weight:700; font-size:13px;">{i}.</div>'
                '<div style="flex:1;">'
                f'<div style="font-size:13px; color:var(--text,#F8FAFC); line-height:1.55;">{r.get("risk", "")}</div>'
                f'<div style="font-size:12px; color:var(--muted,#94A3B8); margin-top:2px; line-height:1.55;">'
                f'<span style="color:#22C55E;">→ 대응:</span> {r.get("mitigant", "")}</div>'
                '</div></div>'
            )
        parts.append(f'<div style="margin-top:4px;">{rows}</div>')
    else:
        parts.append('<div style="color:var(--muted,#94A3B8); font-size:13px;">데이터 없음</div>')

    # 예상 재무제표
    parts.append(_section_header("예상 재무제표"))
    fn = memo.get("financials_narrative") or ""
    if fn:
        parts.append(
            f'<div style="font-size:12.5px; color:var(--muted,#94A3B8); '
            f'margin-bottom:8px; line-height:1.6;">{fn}</div>'
        )
    parts.append(_render_financials_table(fin))

    # 예상 투자성과
    parts.append(_section_header("예상 투자성과"))
    rn = memo.get("returns_narrative") or ""
    if rn:
        parts.append(
            f'<div style="font-size:12.5px; color:var(--muted,#94A3B8); '
            f'margin-bottom:8px; line-height:1.6;">{rn}</div>'
        )
    parts.append(_render_returns_tables(ret))
    # 투자 구조
    if structure.get("instrument"):
        parts.append(_render_structure_card(structure))

    # 추가 검토 필요 사항
    parts.append(_section_header("추가 검토 필요 사항"))
    dd = memo.get("additional_dd") or {}
    parts.append(_render_dd_section(dd))

    return "".join(parts)


def _section_header(title: str) -> str:
    return (
        f'<div style="font-size:13px; color:var(--text,#F8FAFC); font-weight:700; '
        f'letter-spacing:.02em; margin:22px 0 6px; padding-bottom:4px; '
        f'border-bottom:1px solid rgba(148,163,184,0.15);">[{title}]</div>'
    )


def _render_numbered_list(items: list[str]) -> str:
    if not items:
        return '<div style="color:var(--muted,#94A3B8); font-size:13px;">데이터 없음</div>'
    rows = ""
    for i, it in enumerate(items, 1):
        rows += (
            '<div style="display:flex; gap:10px; padding:6px 0; '
            'font-size:13px; color:var(--text,#F8FAFC); line-height:1.55;">'
            f'<div style="flex:0 0 18px; color:var(--accent,#3B82F6); font-weight:600;">{i}.</div>'
            f'<div style="flex:1;">{it}</div></div>'
        )
    return f'<div>{rows}</div>'


def _render_financials_table(fin: dict) -> str:
    years = fin.get("years") or []
    if not years:
        return '<div style="color:var(--muted,#94A3B8); font-size:13px;">재무 데이터 미입력</div>'
    actuals_count = int(fin.get("a_years_count") or 0)

    def _yr_label(i: int) -> str:
        suffix = "A" if i < actuals_count else "F"
        return f'<span style="color:var(--muted,#94A3B8);">{years[i]}</span><span style="color:#64748B; font-size:10px;">{suffix}</span>'

    th_cells = "".join(
        f'<th style="text-align:right; padding:5px 8px; font-size:11px; font-weight:600;">{_yr_label(i)}</th>'
        for i in range(len(years))
    )

    def _row(label: str, values: list, fmt_fn, italic: bool = False) -> str:
        cells = "".join(
            f'<td style="text-align:right; padding:4px 8px; font-size:12px; font-variant-numeric:tabular-nums; '
            + ('font-style:italic; color:var(--muted,#94A3B8);' if italic else 'color:var(--text,#F8FAFC);')
            + f'">{fmt_fn(v)}</td>'
            for v in values
        )
        return (
            '<tr>'
            f'<td style="padding:4px 8px; font-size:12px; '
            + ('font-style:italic; color:var(--muted,#94A3B8);' if italic else 'color:var(--text,#F8FAFC);')
            + f'">{label}</td>'
            f'{cells}</tr>'
        )

    body = (
        _row("매출액", fin.get("revenue") or [], _fmt_int)
        + _row("% growth", fin.get("revenue_growth") or [], _fmt_pct, italic=True)
        + _row("EBITDA", fin.get("ebitda") or [], _fmt_int)
        + _row("% Sales", fin.get("ebitda_margin") or [], _fmt_pct, italic=True)
        + _row("% growth", fin.get("ebitda_growth") or [], _fmt_pct, italic=True)
        + _row("당기순이익", fin.get("net_income") or [], _fmt_int)
        + _row("자산", fin.get("assets") or [], _fmt_int)
        + _row("부채", fin.get("liabilities") or [], _fmt_int)
        + _row("부채비율", fin.get("debt_ratio") or [], _fmt_pct, italic=True)
        + _row("자본", fin.get("equity") or [], _fmt_int)
        + _row("OCF", fin.get("ocf") or [], _fmt_int)
    )

    return (
        '<div style="overflow-x:auto; border:1px solid rgba(148,163,184,0.15); border-radius:6px;">'
        '<table style="width:100%; border-collapse:collapse;">'
        '<thead style="background:rgba(148,163,184,0.06);">'
        f'<tr><th style="text-align:left; padding:6px 8px; font-size:11px; color:var(--muted,#94A3B8); font-weight:600;">KRW million</th>{th_cells}</tr>'
        '</thead>'
        f'<tbody>{body}</tbody></table></div>'
    )


def _render_returns_tables(ret: dict) -> str:
    irr_tbl = ret.get("irr_table") or {}
    moic_tbl = ret.get("moic_table") or {}
    if not irr_tbl and not moic_tbl:
        return '<div style="color:var(--muted,#94A3B8); font-size:13px;">투자성과 데이터 미입력</div>'

    def _build(tbl: dict, title: str, fmt_fn, color: str) -> str:
        multiples = tbl.get("multiples") or []
        bull = tbl.get("bull") or []
        base = tbl.get("base") or []
        workout = tbl.get("workout") or []
        rows_html = ""
        for i, m in enumerate(multiples):
            rows_html += (
                f'<tr>'
                f'<td style="padding:4px 10px; font-style:italic; color:{color}; font-weight:600; '
                f'font-size:12px;">{m:.1f}x</td>'
                f'<td style="text-align:right; padding:4px 10px; font-size:12px; color:var(--text,#F8FAFC);">{fmt_fn(bull[i]) if i < len(bull) else "—"}</td>'
                f'<td style="text-align:right; padding:4px 10px; font-size:12px; color:var(--text,#F8FAFC);">{fmt_fn(base[i]) if i < len(base) else "—"}</td>'
                f'<td style="text-align:right; padding:4px 10px; font-size:12px; color:var(--muted,#94A3B8);">{fmt_fn(workout[i]) if i < len(workout) else "—"}</td>'
                '</tr>'
            )
        return (
            f'<div style="flex:1; border:1px solid rgba(148,163,184,0.15); border-radius:6px; overflow:hidden;">'
            '<table style="width:100%; border-collapse:collapse;">'
            '<thead style="background:rgba(148,163,184,0.06);">'
            f'<tr><th style="padding:6px 10px; text-align:left; font-size:11px; color:{color}; font-weight:700;">{title}</th>'
            '<th style="padding:6px 10px; text-align:right; font-size:11px; color:#22C55E; font-weight:600;">Bull</th>'
            '<th style="padding:6px 10px; text-align:right; font-size:11px; color:var(--text,#F8FAFC); font-weight:600;">Base</th>'
            '<th style="padding:6px 10px; text-align:right; font-size:11px; color:var(--muted,#94A3B8); font-weight:600;">상환</th>'
            '</tr></thead>'
            f'<tbody>{rows_html}</tbody></table></div>'
        )

    return (
        '<div style="display:flex; gap:10px; flex-wrap:wrap;">'
        + _build(irr_tbl, "IRR", lambda v: _fmt_pct(v, 0), "#F59E0B")
        + _build(moic_tbl, "MOIC", lambda v: _fmt_multiple(v, 1), "#3B82F6")
        + '</div>'
    )


def _render_structure_card(structure: dict) -> str:
    inst = structure.get("instrument", "—")
    inv = structure.get("investment_krw_mm")
    period = structure.get("holding_period_years")
    ytm = structure.get("ytm")
    coupon = structure.get("coupon")
    narr = structure.get("narrative", "")
    chips = [f'<b>{inst}</b>']
    if inv:
        chips.append(f"투자금액 ₩{int(inv):,}M")
    if period:
        chips.append(f"투자기간 {period}년")
    if ytm is not None:
        chips.append(f"YTM {ytm * 100:.1f}%")
    if coupon is not None:
        chips.append(f"Coupon {coupon * 100:.1f}%")
    chip_html = "  ·  ".join(chips)
    return (
        '<div style="margin-top:10px; padding:10px 14px; background:rgba(59,130,246,0.06); '
        'border-left:3px solid #3B82F6; border-radius:4px;">'
        '<div style="font-size:11px; color:var(--muted,#94A3B8); margin-bottom:3px;">투자 구조</div>'
        f'<div style="font-size:13px; color:var(--text,#F8FAFC); line-height:1.5;">{chip_html}</div>'
        + (f'<div style="font-size:12px; color:var(--muted,#94A3B8); margin-top:4px; line-height:1.55;">{narr}</div>' if narr else '')
        + '</div>'
    )


def _render_dd_section(dd: dict) -> str:
    cat_labels = [
        ("commercial", "Commercial"),
        ("financial", "Financial"),
        ("legal", "Legal / Regulatory"),
        ("tax", "Tax / Structuring"),
        ("market", "Market / Exit"),
    ]
    parts = []
    for k, label in cat_labels:
        items = dd.get(k) or []
        if not items:
            continue
        bullets = "".join(
            f'<li style="margin-bottom:3px; font-size:12.5px; color:var(--text,#F8FAFC); line-height:1.55;">{it}</li>'
            for it in items
        )
        parts.append(
            f'<div style="margin-top:8px;">'
            f'<div style="font-size:11px; color:var(--accent,#3B82F6); font-weight:700; '
            f'letter-spacing:.05em; margin-bottom:3px;">{label}</div>'
            f'<ul style="margin:0; padding-left:18px;">{bullets}</ul>'
            '</div>'
        )
    if not parts:
        return '<div style="color:var(--muted,#94A3B8); font-size:13px;">미입력</div>'
    return "".join(parts)


# ---------------------------------------------------------------------------
# Financial Model HTML 렌더 (Excel 대안)
# ---------------------------------------------------------------------------

def render_model_html(data: dict[str, Any]) -> str:
    """Financial Model HTML — Excel 대안. 사용자 spec 의 7개 시트 핵심을 한 화면에."""
    parts: list[str] = []

    parts.append(
        '<div style="font-size:13px; color:var(--text,#F8FAFC); font-weight:700; '
        'margin-bottom:12px;">📊 Financial Model</div>'
    )

    # 1) Summary
    parts.append(_section_header("Summary"))
    parts.append(_render_summary_block(data))

    # 2) Historical + Projection (이미 IC Memo 에 표가 있지만 model 에선 동일 사용)
    parts.append(_section_header("Historical + Projection"))
    parts.append(_render_financials_table(data.get("financials", {})))

    # 3) Scenarios
    parts.append(_section_header("Scenarios (Bull / Base / Bear)"))
    parts.append(_render_scenarios_block(data.get("scenarios", {})))

    # 4) Returns + Sensitivity
    parts.append(_section_header("Returns / Sensitivity"))
    parts.append(_render_returns_tables(data.get("returns", {})))

    # 5) Key Assumptions
    parts.append(_section_header("Key Assumptions"))
    parts.append(_render_assumptions_block(data))

    # 6) Judgment (사용자 10대 질문)
    parts.append(_section_header("판단 체크리스트"))
    parts.append(_render_judgment_block(data.get("judgment", {})))

    # 7) Sources
    parts.append(_section_header("출처 / Research Note"))
    parts.append(_render_sources_block(data.get("sources", []), data.get("generated_at")))

    return "".join(parts)


def _render_summary_block(data: dict) -> str:
    company = data.get("company", {}) or {}
    memo = data.get("ic_memo", {}) or {}
    scen = data.get("scenarios", {}) or {}
    base = scen.get("base") or {}
    bull = scen.get("bull") or {}
    bear = scen.get("bear") or {}
    structure = data.get("investment_structure", {}) or {}

    chips = []
    if company.get("market_cap_krw"):
        chips.append(("시가총액", f"₩{company['market_cap_krw'] / 1e8:,.0f}억"))
    if structure.get("investment_krw_mm"):
        chips.append(("투자금액", f"₩{structure['investment_krw_mm']:,.0f}M"))
    if structure.get("holding_period_years"):
        chips.append(("기간", f"{structure['holding_period_years']}년"))
    if base.get("irr") is not None:
        chips.append(("Base IRR", _fmt_pct(base["irr"], 0)))
    if base.get("moic") is not None:
        chips.append(("Base MOIC", _fmt_multiple(base["moic"], 1)))
    if bull.get("irr") is not None:
        chips.append(("Bull IRR", _fmt_pct(bull["irr"], 0)))
    if bear.get("irr") is not None:
        chips.append(("Bear IRR", _fmt_pct(bear["irr"], 0)))

    chip_html = "".join(
        f'<div style="background:rgba(148,163,184,0.06); border-radius:6px; padding:8px 12px;">'
        f'<div style="font-size:10px; color:var(--muted,#94A3B8); letter-spacing:.03em;">{label}</div>'
        f'<div style="font-size:15px; color:var(--text,#F8FAFC); font-weight:600; margin-top:2px;">{val}</div>'
        '</div>'
        for label, val in chips
    )
    return (
        '<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:4px;">'
        + chip_html +
        '</div>'
    )


def _render_scenarios_block(scen: dict) -> str:
    cases = [
        ("Bull", "bull", "#22C55E"),
        ("Base", "base", "#3B82F6"),
        ("Bear / 상환", "bear", "#EF4444"),
    ]
    th = '<th style="text-align:left; padding:6px 10px; font-size:11px; color:var(--muted,#94A3B8);">시나리오</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">Revenue CAGR</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">Terminal EBITDA Margin</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">Exit EBITDA (₩M)</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">Exit Multiple</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">IRR</th>'
    th += '<th style="text-align:right; padding:6px 10px; font-size:11px;">MOIC</th>'
    rows = ""
    for label, key, color in cases:
        s = scen.get(key) or {}
        rows += (
            f'<tr><td style="padding:5px 10px; font-size:12px; color:{color}; font-weight:700;">{label}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px;">{_fmt_pct(s.get("revenue_cagr"), 1)}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px;">{_fmt_pct(s.get("ebitda_margin_terminal"), 1)}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px;">{_fmt_int(s.get("exit_ebitda"))}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px;">{_fmt_multiple(s.get("exit_multiple"), 1)}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px; font-weight:600; color:{color};">{_fmt_pct(s.get("irr"), 0)}</td>'
            f'<td style="text-align:right; padding:5px 10px; font-size:12px; font-weight:600; color:{color};">{_fmt_multiple(s.get("moic"), 1)}</td>'
            '</tr>'
        )
    return (
        '<div style="overflow-x:auto; border:1px solid rgba(148,163,184,0.15); border-radius:6px;">'
        '<table style="width:100%; border-collapse:collapse;">'
        f'<thead style="background:rgba(148,163,184,0.06);"><tr>{th}</tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def _render_assumptions_block(data: dict) -> str:
    assumptions = data.get("assumptions") or []
    if not assumptions:
        return '<div style="color:var(--muted,#94A3B8); font-size:13px;">가정 미입력</div>'
    rows = "".join(
        f'<div style="display:flex; gap:10px; padding:5px 0; font-size:12.5px; '
        'border-top:1px solid rgba(148,163,184,0.10);">'
        f'<div style="flex:0 0 220px; color:var(--muted,#94A3B8);">{a.get("label", "—")}</div>'
        f'<div style="flex:0 0 90px; color:var(--text,#F8FAFC); font-weight:500; font-variant-numeric:tabular-nums;">{a.get("value", "—")}</div>'
        f'<div style="flex:1; color:var(--muted,#94A3B8); line-height:1.55;">{a.get("note", "")}</div>'
        '</div>'
        for a in assumptions
    )
    return f'<div>{rows}</div>'


def _render_judgment_block(judg: dict) -> str:
    questions = [
        ("good_company", "이 회사는 좋은 회사인가?"),
        ("good_investment_now", "지금 가격에서 좋은 투자인가?"),
        ("qld_alternative", "QLD 대비 더 나은 자본효율인가?"),
        ("irr_moic_sufficient", "3~5년 IRR/MOIC 충분한가?"),
        ("worst_case_loss", "Worst Case 원금 손실 가능성?"),
        ("earnings_visible", "실적 가시성 vs 이벤트 선취매?"),
        ("valuation_band", "현재 valuation 위치?"),
        ("catalyst_quality", "Catalyst → 숫자 전환 가능성?"),
        ("instrument_recommendation", "본주/CB/RCPS/레버리지 중 적합?"),
        ("next_action", "지금 바로 해야 할 행동?"),
    ]
    rows = ""
    for k, q in questions:
        ans = (judg.get(k) or "확인 필요").strip()
        rows += (
            '<div style="display:flex; gap:12px; padding:6px 0; '
            'border-top:1px solid rgba(148,163,184,0.10);">'
            f'<div style="flex:0 0 280px; font-size:12.5px; color:var(--muted,#94A3B8); line-height:1.55;">{q}</div>'
            f'<div style="flex:1; font-size:13px; color:var(--text,#F8FAFC); line-height:1.55; font-weight:500;">{ans}</div>'
            '</div>'
        )
    return f'<div>{rows}</div>'


def _render_sources_block(sources: list, generated_at: str | None) -> str:
    if not sources:
        body = '<div style="color:var(--muted,#94A3B8); font-size:13px;">출처 미입력</div>'
    else:
        rows = ""
        for s in sources:
            title = s.get("title", "—")
            url = s.get("url")
            if url:
                rows += f'<li style="margin-bottom:3px; font-size:12.5px;"><a href="{url}" target="_blank" style="color:#3B82F6; text-decoration:none;">{title}</a></li>'
            else:
                rows += f'<li style="margin-bottom:3px; font-size:12.5px; color:var(--text,#F8FAFC);">{title}</li>'
        body = f'<ul style="margin:0; padding-left:20px;">{rows}</ul>'
    footer = ""
    if generated_at:
        footer = (
            f'<div style="margin-top:10px; font-size:11px; color:var(--muted,#94A3B8); '
            f'font-style:italic;">Generated at {generated_at}</div>'
        )
    return body + footer


# ---------------------------------------------------------------------------
# 검증 — empty / partial 데이터 graceful
# ---------------------------------------------------------------------------

def make_empty_template() -> dict[str, Any]:
    """빈 템플릿 — UI 골격 테스트용 / placeholder."""
    return {
        "company": {"name_ko": "—", "name_en": "", "ticker": "—", "industry": "—",
                    "market_cap_krw": None, "is_listed": False},
        "ic_memo": {
            "investment_verdict": "—",
            "verdict_oneliner": "회사명을 입력하면 분석이 시작됩니다.",
            "investment_thesis": [],
            "investment_points": [],
            "risks_and_mitigants": [],
            "financials_narrative": "",
            "returns_narrative": "",
            "additional_dd": {},
        },
        "financials": {"years": [], "a_years_count": 0,
                       "revenue": [], "revenue_growth": [], "ebitda": [], "ebitda_margin": [],
                       "ebitda_growth": [], "net_income": [], "assets": [], "liabilities": [],
                       "debt_ratio": [], "equity": [], "ocf": []},
        "scenarios": {"bull": {}, "base": {}, "bear": {}},
        "returns": {"irr_table": {}, "moic_table": {}},
        "investment_structure": {},
        "judgment": {},
        "assumptions": [],
        "sources": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
