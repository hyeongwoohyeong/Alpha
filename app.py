"""Alpha — Investment Research Platform (Streamlit).

라이트 모드, 네이비/블루/그레이 팔레트, 카드 중심 리포트형 UI.
"""
from __future__ import annotations

import sys
from pathlib import Path

# src 모듈 경로 보장
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import datetime as _dt
import logging
from typing import Any

import pandas as pd
import streamlit as st

# 모듈 로거 — except 블록 등에서 log.warning(...) 호출 시 NameError 방지
log = logging.getLogger("alpha_app")

from src.brief_generator import (
    build_daily_brief,
    check_items,
    core_thesis,
    investment_type,
    key_risk,
)
from src.dislocation import list_dislocation
from src.engine import (
    append_decision,
    append_snapshot,
    build_rows,
    diagnose_data_health,
    fetch_market_context,
)
from src import database as db
from src.financials import (
    fetch_annual_financials,
    fetch_quarterly_financials,
    financials_interpretation,
    latest_annual_summary,
)
from src.market_data import fetch_max_history, get_yfinance_status
from src.reflection import build_retrospective_report
from src.stock_detail import (
    build_stock_detail,
    chart_event_markers,
    news_with_impact,
    price_interpretation,
    valuation_comparison,
    valuation_metrics_cards,
)
from src.universe import (
    add_to_watchlist,
    category_label_ko,
    load_watchlist,
    remove_from_watchlist,
    theme_label_ko,
)
from src.utils import (
    DAILY_SNAPSHOTS_CSV,
    display_name,
    fmt_marketcap,
    fmt_money,
    fmt_pct,
    score_label,
)

# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Alpha — Investment Research",
    layout="wide",
    initial_sidebar_state="auto",   # 모바일에서는 자동으로 collapsed
)

# ---------------------------------------------------------------------------
# 디자인 시스템 (라이트 / 네이비-블루-그레이 팔레트)
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
    :root {
        --bg: #05070D;
        --panel: #111827;
        --panel-soft: #1F2937;
        --line: #2D3748;
        --line-strong: #374151;
        --text: #F9FAFB;
        --text-mid: #CBD5E1;
        --muted: #9CA3AF;
        --muted-soft: #6B7280;
        --navy: #1E3A8A;
        --blue: #3B82F6;
        --blue-soft: #1E3A8A;
        --blue-2: #7C9FC4;
        --green: #22C55E;
        --red: #EF4444;
    }

    html, body, [data-testid="stAppViewContainer"] {
        background: var(--bg);
        color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
                     "Pretendard", "Apple SD Gothic Neo", "Noto Sans KR",
                     "Segoe UI", Helvetica, Arial, sans-serif;
    }
    [data-testid="stHeader"] { background: var(--bg); }
    .block-container { padding-top: 1.4rem; max-width: 1320px; }

    /* ---------- Sidebar ---------- */
    [data-testid="stSidebar"] {
        background: var(--panel);
        border-right: 1px solid var(--line);
    }
    [data-testid="stSidebar"] * { color: var(--text); }
    .brand {
        display: flex; align-items: center; gap: 12px;
        padding: 4px 0 2px 2px;
    }
    .brand-mark {
        display: inline-flex; align-items: center; justify-content: center;
        width: 46px; height: 46px;
        border-radius: 12px;
        background: var(--blue);
        color: #FFFFFF;
        font-family: "Times New Roman", Georgia, serif;
        font-style: italic;
        font-size: 30px;
        font-weight: 600;
        line-height: 1;
    }
    .brand-text {
        font-size: 30px;
        font-weight: 800;
        letter-spacing: -0.4px;
        color: var(--text);
    }
    .brand-sub {
        font-size: 12px;
        color: var(--muted);
        letter-spacing: 1.5px;
        text-transform: uppercase;
        margin: 6px 0 16px 2px;
    }
    .nav-label {
        font-size: 12px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.4px;
        text-transform: uppercase;
        margin: 8px 2px 6px 2px;
    }

    /* sidebar 버튼 (메뉴) — 라디오 원형 제거, 사각 버튼 */
    [data-testid="stSidebar"] .stButton > button {
        width: 100%;
        text-align: left;
        justify-content: flex-start;
        padding: 10px 14px;
        border-radius: 8px;
        background: var(--panel);
        color: var(--text);
        border: 1px solid transparent;
        font-weight: 500;
        margin-bottom: 2px;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: var(--panel-soft);
        color: var(--blue);
        border-color: transparent;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #1E3A8A;             /* 어두운 블루 배경 */
        color: #BFDBFE;                  /* 밝은 블루 글씨 */
        border: 1px solid #2D3748;
        font-weight: 700;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
        background: #172554;
        color: #BFDBFE;
        border-color: #374151;
    }

    /* ---------- 본문 헤더 ---------- */
    .page-header {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 16px;
        padding-bottom: 14px;
        border-bottom: 1px solid var(--line);
        margin-bottom: 22px;
    }
    .page-title {
        font-size: 30px;
        font-weight: 800;
        letter-spacing: -0.4px;
        color: var(--text);
        margin: 0;
        line-height: 1.2;
    }
    .page-meta {
        font-size: 14px;
        color: var(--muted);
        margin-top: 6px;
    }
    .page-meta b { color: var(--text-mid); font-weight: 600; }

    /* ---------- 카드 공통 ---------- */
    .card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 20px 22px;
        margin-bottom: 14px;
    }
    .card-soft {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 16px 18px;
    }

    /* ---------- 섹션 타이틀 ---------- */
    .section-title {
        font-size: 13px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.6px;
        text-transform: uppercase;
        margin: 36px 0 14px 0;
    }
    .section-title.first { margin-top: 6px; }
    .section-spacer { margin-bottom: 40px; }
    .section-summary {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 20px 24px;
        font-size: 15px;
        line-height: 1.75;
        color: var(--text-mid);
        margin-top: 18px;
        margin-bottom: 32px;
        word-break: keep-all;
    }
    /* 차트 컨테이너 — 다음 요소와의 여백 */
    .chart-after { margin-bottom: 18px; }

    /* ---------- 금일 핵심 판단 카드 ---------- */
    .judgment-card {
        background: linear-gradient(180deg, #111827 0%, #0B1120 100%);
        border: 1px solid var(--line);
        border-left: 4px solid var(--blue);
        border-radius: 12px;
        padding: 22px 26px;
        margin-bottom: 14px;
    }
    .judgment-eyebrow {
        font-size: 12px;
        font-weight: 700;
        color: var(--navy);
        letter-spacing: 1.6px;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    .judgment-body {
        font-size: 18px;
        line-height: 1.7;
        color: var(--text);
        font-weight: 500;
    }

    /* ---------- 시장 환경 카드 ---------- */
    .env-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 16px 20px;
        margin-bottom: 18px;
    }
    .env-card .env-eyebrow {
        font-size: 12px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.4px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .env-card .env-body {
        font-size: 15px;
        color: var(--text-mid);
        line-height: 1.65;
    }

    /* ---------- 종목 카드 (홈) ---------- */
    .pick {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 22px 24px;
        margin-bottom: 14px;
    }
    .pick-head {
        display: flex; align-items: center; justify-content: space-between;
        gap: 12px; flex-wrap: wrap;
    }
    .pick-name {
        font-size: 19px;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.2px;
    }
    .pick-type {
        font-size: 14px;
        color: var(--muted);
        margin-top: 6px;
    }
    .pick-divider { height: 1px; background: var(--line); margin: 16px 0 14px 0; }

    .pick-block { margin-bottom: 12px; }
    .pick-block:last-child { margin-bottom: 0; }
    .pick-block-label {
        font-size: 13px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .pick-block-body {
        font-size: 15px;
        color: var(--text);
        line-height: 1.7;
    }
    .pick-foot {
        display: flex; align-items: center; justify-content: space-between;
        margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line);
        gap: 10px;
    }
    .pick-quote {
        display: flex; align-items: baseline; gap: 10px;
        font-variant-numeric: tabular-nums;
    }
    .pick-price {
        font-size: 17px; font-weight: 700; color: var(--text);
    }
    .pick-change.pos { color: var(--green); font-weight: 600; }
    .pick-change.neg { color: var(--red); font-weight: 600; }
    .pick-change.neutral { color: var(--muted); }

    /* ---------- Tag (투자 판단) ---------- */
    .tag {
        display: inline-flex; align-items: center;
        padding: 5px 14px;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.4px;
        border: 1px solid transparent;
        white-space: nowrap;
    }
    /* 모두 "어두운 배경 + 밝은 글씨" 패턴. 색 구분은 톤다운된 컬러칩으로. */
    .tag-research-now { background: #1E3A8A; color: #BFDBFE; border-color: #2D4A8A; }
    .tag-quality-dislocation { background: #164E63; color: #A5F3FC; border-color: #1E6A82; }
    .tag-watchlist { background: #1F2937; color: var(--text-mid); border-color: var(--line-strong); }
    .tag-wait-for-entry { background: #4A3A12; color: #FCD34D; border-color: #6B541E; }
    .tag-too-crowded { background: #3A2A52; color: #D8B4FE; border-color: #543C73; }
    .tag-need-thesis-check { background: #1F2937; color: var(--text-mid); border-color: var(--line-strong); }
    .tag-avoid { background: #4A1F1F; color: #FCA5A5; border-color: #6B2E2E; }
    .tag-data-unavailable { background: var(--panel-soft); color: var(--muted-soft); border-color: var(--line); }

    /* ---------- 알림 / 점검 사항 row ---------- */
    .info-row {
        background: var(--panel);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue);
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 10px;
        font-size: 15px;
        color: var(--text);
        line-height: 1.65;
    }
    .info-row.alert { border-left-color: var(--blue-2); }
    .info-row.check { border-left-color: var(--navy); }

    /* ---------- 상세 화면 KV / 시나리오 / Lens ---------- */
    .kv {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 0; border-bottom: 1px solid var(--line);
        font-size: 15px;
    }
    .kv:last-child { border-bottom: none; }
    .kv-k { color: var(--muted); font-weight: 500; font-size: 14px; }
    .kv-v { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; font-size: 15px; }

    .metric-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 24px 26px;
        min-height: 150px;             /* 6개 카드 모두 동일 높이 */
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
    }
    /* expander로 펼치는 더 보기 영역 — 슬레이트 박스 */
    [data-testid="stExpander"] {
        margin-top: 22px;
        margin-bottom: 8px;
    }
    [data-testid="stExpander"] > details {
        background: var(--panel-soft);
        border: 1px solid var(--line) !important;
        border-radius: 16px !important;
        padding: 4px 8px;
    }
    [data-testid="stExpander"] > details > summary {
        padding: 12px 16px !important;
        font-weight: 600;
        color: var(--text);
    }
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
        padding: 16px 20px 20px 20px;
    }
    .metric-label {
        font-size: 13px;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        font-weight: 700;
        margin-bottom: 14px;
    }
    .metric-value {
        font-size: 20px;
        font-weight: 700;
        color: var(--text);
        line-height: 1.3;
        font-variant-numeric: tabular-nums;
        word-break: keep-all;          /* 한국어 단어 단위 줄바꿈 */
    }
    .metric-sub { font-size: 13px; color: var(--muted); margin-top: 8px; }

    .scenario {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 16px 18px;
        height: 100%;
    }
    .scenario-label {
        display: inline-block;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 1.4px;
        text-transform: uppercase;
        padding: 4px 12px;
        border-radius: 6px;
        margin-bottom: 12px;
    }
    .scenario-bull { color: #86EFAC; background: #14321F; border: 1px solid #1E5235; }
    .scenario-base { color: #BFDBFE; background: #1E3A8A; border: 1px solid #2D4A8A; }
    .scenario-bear { color: #FCA5A5; background: #3A1A1A; border: 1px solid #5C2A2A; }
    .scenario-body { font-size: 15px; color: var(--text); line-height: 1.7; }

    .lens {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 18px 20px;
        min-height: 170px;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
    }
    .lens-name {
        font-size: 13px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    .lens-headline {
        font-size: 16px;
        font-weight: 700;
        color: var(--navy);
        margin-bottom: 10px;
        line-height: 1.45;
    }
    .lens-body {
        font-size: 14.5px;
        color: var(--text-mid);
        line-height: 1.7;
        word-break: keep-all;
    }

    /* ---------- 시장 환경 3블록 ---------- */
    .env-block {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 22px 24px;
        min-height: 240px;
        display: flex;
        flex-direction: column;
    }
    .env-block-title {
        font-size: 13px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.3px;
        text-transform: uppercase;
        margin-bottom: 12px;
    }
    .env-block-body {
        font-size: 15px;
        color: var(--text);
        line-height: 1.7;
        word-break: keep-all;
    }

    /* ---------- 시장 자산 테이블 (지수 및 Risk Appetite 카드 내부) ---------- */
    .market-table {
        margin-top: 4px;
    }
    .market-header,
    .market-row {
        display: grid;
        grid-template-columns: 1.5fr 0.85fr 0.85fr;
        align-items: center;
        padding: 10px 0;
        border-bottom: 1px solid var(--line);
    }
    .market-header {
        font-size: 12px;
        font-weight: 700;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        padding: 8px 0;
    }
    .market-header .col-r,
    .market-row .col-r {
        text-align: right;
    }
    .market-row { font-size: 14px; padding: 11px 0; }
    .market-row:last-child { border-bottom: none; }
    .market-name { font-weight: 700; color: var(--text); }
    .market-ret {
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        text-align: right;
    }
    .ret-positive { color: var(--green); }
    .ret-negative { color: var(--red); }
    .ret-neutral { color: var(--muted); font-weight: 500; }

    /* ---------- 매크로 이슈 카드 ---------- */
    .macro {
        background: var(--panel);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue-2);
        border-radius: 16px;
        padding: 28px 32px;
        margin-bottom: 24px;
    }
    .macro-head {
        display: flex; justify-content: space-between; align-items: center; gap: 16px;
        flex-wrap: wrap; margin-bottom: 22px;
    }
    .macro-title {
        font-size: 18px;
        font-weight: 800;
        color: var(--text);
    }
    .macro-cat {
        font-size: 12px;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        font-weight: 700;
        white-space: nowrap;
    }
    /* ---------- 전날 글로벌 브리핑 ---------- */
    .ob-empty {
        background: var(--panel);
        border: 1px dashed var(--line);
        border-radius: 14px;
        padding: 22px 26px;
        font-size: 14px;
        line-height: 1.7;
        color: var(--muted);
        margin-bottom: 24px;
    }
    .ob-cat {
        background: var(--panel);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue-2);
        border-radius: 16px;
        padding: 24px 30px 12px 30px;
        margin-bottom: 20px;
    }
    .ob-cat-label {
        font-size: 13px;
        font-weight: 800;
        color: var(--blue-2);
        letter-spacing: 0.8px;
        margin-bottom: 18px;
    }
    .ob-event {
        padding-bottom: 16px;
        margin-bottom: 16px;
        border-bottom: 1px solid var(--line);
    }
    .ob-event:last-child { border-bottom: none; }
    .ob-event-headline {
        font-size: 16px;
        font-weight: 800;
        color: var(--text);
        line-height: 1.55;
        margin-bottom: 8px;
        word-break: keep-all;
    }
    .ob-event-detail {
        font-size: 14.5px;
        line-height: 1.8;
        color: var(--text-mid);
        margin-bottom: 10px;
        word-break: keep-all;
        overflow-wrap: break-word;
    }
    .ob-noevent {
        font-size: 14px;
        color: var(--muted);
        padding-bottom: 12px;
    }
    /* ---------- Bull / Bear 토론 ---------- */
    .bbd-intro {
        font-size: 14px;
        line-height: 1.75;
        color: var(--muted);
        margin-bottom: 18px;
        word-break: keep-all;
    }
    .bbd-side {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 22px 24px;
        height: 100%;
    }
    .bbd-side-bull { border-top: 3px solid #22C55E; }
    .bbd-side-bear { border-top: 3px solid #EF4444; }
    .bbd-side-head {
        font-size: 14px;
        font-weight: 800;
        letter-spacing: 0.6px;
        margin-bottom: 14px;
    }
    .bbd-side-bull .bbd-side-head { color: #4ADE80; }
    .bbd-side-bear .bbd-side-head { color: #F87171; }
    .bbd-round-label {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: var(--muted);
        margin: 14px 0 6px 0;
    }
    .bbd-text {
        font-size: 14.5px;
        line-height: 1.8;
        color: var(--text-mid);
        word-break: keep-all;
        overflow-wrap: break-word;
    }
    .bbd-swing {
        background: var(--panel);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue-2);
        border-radius: 16px;
        padding: 22px 26px;
        margin-top: 18px;
    }
    .bbd-swing-label {
        font-size: 13px;
        font-weight: 800;
        color: var(--blue-2);
        letter-spacing: 0.8px;
        margin-bottom: 12px;
    }
    .bbd-swing ul { margin: 0; padding-left: 20px; }
    .bbd-swing li {
        font-size: 14.5px;
        line-height: 1.75;
        color: var(--text-mid);
        margin-bottom: 7px;
        word-break: keep-all;
    }
    .bbd-summary {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 20px 26px;
        margin-top: 16px;
        font-size: 14.5px;
        line-height: 1.8;
        color: var(--text-mid);
        word-break: keep-all;
    }
    .bbd-summary-label {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 8px;
    }
    /* ──────────────────────────────────────────────
       hanging indent 통합 클래스
       (매크로 / 최근 이벤트 / 주요 뉴스 본문에 공통 적용)

       padding-left: 모든 줄을 라벨 폭만큼 들여씀
       text-indent: 첫 줄만 다시 왼쪽으로 끌어당김 → 첫 줄은 0px 에서 시작
       라벨(inline-block, width=라벨폭)이 첫 줄의 라벨 자리를 차지하고,
       본문은 자연스럽게 라벨 직후부터 시작.
       두 번째 줄부터는 text-indent 효과가 없으므로 본문 시작점에 정렬.
       ────────────────────────────────────────────── */
    .para-row {
        --label-width: 150px;
        padding-left: var(--label-width);
        text-indent: calc(-1 * var(--label-width));
        font-size: 15px;
        line-height: 1.8;
        color: var(--text-mid);
        margin-bottom: 14px;
        word-break: keep-all;
        overflow-wrap: break-word;
        line-break: strict;
    }
    .para-row:last-child { margin-bottom: 0; }
    .para-label {
        display: inline-block;
        width: var(--label-width);
        padding-right: 24px;
        box-sizing: border-box;
        font-weight: 800;
        color: var(--text);
        white-space: nowrap;
        text-indent: 0;            /* 라벨 자체는 들여쓰기 영향 받지 않음 */
        vertical-align: top;
    }
    .para-text {
        color: var(--text-mid);
        word-break: keep-all;
        overflow-wrap: break-word;
    }
    @media (max-width: 640px) {
        .para-row {
            padding-left: 0;
            text-indent: 0;
            font-size: 14.5px;
            line-height: 1.7;
        }
        .para-label {
            display: block;
            width: auto;
            padding-right: 0;
            margin-bottom: 4px;
        }
    }
    /* 라벨 + 불릿 리스트 — grid 2열로 hanging indent 와 같은 정렬 */
    .para-row.kpts-row {
        display: grid;
        grid-template-columns: var(--label-width) minmax(0, 1fr);
        column-gap: 22px;
        align-items: start;
        padding-left: 0;
        text-indent: 0;
        margin-bottom: 14px;
    }
    .para-row.kpts-row .para-label {
        display: block;
        width: auto;
        padding-right: 0;
        text-indent: 0;
        margin: 0;
    }
    .para-row.kpts-row .para-text {
        font-size: 15px;
        line-height: 1.7;
        color: var(--text-mid);
        word-break: keep-all;
        overflow-wrap: break-word;
        min-width: 0;
    }
    .para-row.kpts-row .kpts-line {
        padding: 5px 0;
        font-size: 15px;
        line-height: 1.75;
        color: var(--text);
    }
    .para-row.kpts-row .kpts-line + .kpts-line {
        border-top: 1px dashed var(--line);
    }
    /* 뉴스 카드 Follow-up Items — 정돈된 리스트 */
    ul.news-followups {
        list-style: disc outside;
        padding-left: 18px;
        margin: 0;
    }
    ul.news-followups li {
        font-size: 15px;
        line-height: 1.7;
        color: var(--text);
        padding: 3px 0;
    }
    /* 뉴스 카드 관련 기사 묶음 */
    .related-news-link {
        font-size: 13px;
        color: var(--navy);
        text-decoration: none;
        border-bottom: 1px dashed var(--line-strong);
        padding: 0 2px;
    }
    .related-news-link:hover {
        background: var(--accent-bg);
    }
    /* 데이터 품질 경고 배너 */
    .data-quality-warning {
        background: #3A2E0A;
        border-left: 4px solid #F59E0B;
        color: #FCD34D;
        padding: 10px 14px;
        border-radius: 6px;
        font-size: 14px;
        line-height: 1.6;
        margin-bottom: 14px;
    }
    /* ───────── Pick Card 의 Alpha Score 배지 (Action Tag 왼쪽) ───────── */
    .pick-head-right {
        display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
    }
    .pick-alpha-badge {
        display: inline-flex; align-items: baseline; gap: 4px;
        padding: 4px 10px;
        border: 1.5px solid var(--line-strong);
        border-radius: 6px;
        background: var(--panel-soft);
        line-height: 1;
    }
    .pick-alpha-label {
        font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
        text-transform: uppercase; color: inherit; opacity: 0.85;
    }
    .pick-alpha-score {
        font-size: 16px; font-weight: 800;
    }
    /* ───────── Alpha Score 카드 ───────── */
    .alpha-score-card {
        background: linear-gradient(180deg, #111827 0%, #0B1120 100%);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 22px 24px;
        margin-bottom: 14px;
    }
    .alpha-score-header {
        display: flex; justify-content: space-between; align-items: baseline;
        gap: 12px; flex-wrap: wrap; margin-bottom: 8px;
    }
    .alpha-score-eyebrow {
        font-size: 13px; font-weight: 700; color: var(--navy);
        letter-spacing: 0.06em; text-transform: uppercase;
    }
    .alpha-score-eyebrow-sub {
        font-size: 12px; color: var(--muted); font-weight: 400; text-transform: none;
    }
    .alpha-score-confidence {
        font-size: 12px; font-weight: 600;
    }
    .alpha-score-main {
        display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
        margin-top: 8px; margin-bottom: 12px;
    }
    .alpha-score-value {
        font-size: 48px; font-weight: 800; line-height: 1;
    }
    .alpha-score-denom {
        font-size: 18px; color: var(--muted); font-weight: 500; margin-left: 4px;
    }
    .alpha-score-rating {
        font-size: 18px; font-weight: 700;
    }
    .alpha-score-rating-ko {
        font-size: 13px; color: var(--muted); font-weight: 500;
    }
    .alpha-score-interpretation {
        font-size: 14px; color: var(--text-mid); line-height: 1.7;
        word-break: keep-all; overflow-wrap: break-word;
    }
    /* 8 컴포넌트 horizontal bars */
    .alpha-comp-grid {
        display: grid; grid-template-columns: 1fr; gap: 6px;
        background: var(--panel-soft); border: 1px solid var(--line);
        border-radius: 8px; padding: 14px 18px; margin-bottom: 10px;
    }
    @media (min-width: 900px) {
        .alpha-comp-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 22px; }
    }
    .alpha-comp-row {
        display: grid;
        grid-template-columns: minmax(140px, 1fr) minmax(80px, 2fr) 36px;
        gap: 10px;
        align-items: center;
        padding: 4px 0;
    }
    .alpha-comp-label {
        font-size: 13px; color: var(--text); font-weight: 600;
        display: flex; flex-direction: column; gap: 3px;
        min-width: 0;
    }
    .alpha-comp-label-main {
        display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px;
    }
    .alpha-comp-weight {
        font-size: 11px; color: var(--muted); font-weight: 400;
    }
    .alpha-comp-status {
        font-size: 10px; line-height: 1; flex-shrink: 0;
    }
    .alpha-comp-bar-wrap {
        background: var(--line); height: 8px; border-radius: 4px; overflow: hidden;
    }
    .alpha-comp-bar {
        height: 100%; border-radius: 4px;
        transition: width 0.3s ease;
    }
    .alpha-comp-value {
        font-size: 13px; font-weight: 700; text-align: right;
    }
    .alpha-comp-na {
        font-style: italic; opacity: 0.85;
    }
    .alpha-coverage-note {
        background: #3A2E0A;
        border: 1px solid #6B541E;
        color: #FCD34D;
        padding: 10px 14px;
        border-radius: 8px;
        font-size: 13px;
        line-height: 1.55;
        margin-top: 12px;
    }
    .alpha-coverage-note b {
        color: #FDE68A;
    }
    .alpha-score-coverage {
        font-weight: 600;
    }
    /* ───────── Earnings Quality 8 차원 grid ───────── */
    .eq-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 14px;
    }
    @media (min-width: 1100px) {
        .eq-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }
    .eq-dim-card {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 14px;
    }
    .eq-dim-head {
        display: flex; justify-content: space-between; align-items: center;
        gap: 8px; margin-bottom: 6px; flex-wrap: wrap;
    }
    .eq-dim-label {
        font-size: 14px; font-weight: 600; color: var(--text);
    }
    .eq-dim-sub { font-size: 12px; color: var(--muted); font-weight: 400; }
    .eq-dim-comment {
        font-size: 13px; line-height: 1.65; color: var(--text-mid);
        word-break: keep-all; overflow-wrap: break-word;
    }
    /* ───────── Moat Map ───────── */
    .moat-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 14px;
    }
    @media (min-width: 900px) {
        .moat-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }
    .moat-cell {
        display: flex; justify-content: space-between; align-items: center;
        gap: 8px;
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 10px 12px;
        font-size: 13px;
    }
    .moat-label {
        font-weight: 600; color: var(--text); flex: 1;
    }
    .moat-sub { font-size: 11px; color: var(--muted); font-weight: 400; }
    /* ───────── Strategic Lens — SWOT / PESTEL / 3C / 3P ───────── */
    .lens-grid {
        display: grid;
        grid-template-columns: repeat(1, minmax(0, 1fr));
        gap: 12px;
    }
    @media (min-width: 900px) {
        .lens-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .lens-card {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px 16px;
    }
    .lens-card-title {
        font-size: 14px; font-weight: 700; color: var(--navy);
        letter-spacing: 0.04em;
        margin-bottom: 10px;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--line);
    }
    .lens-swot-grid {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px;
    }
    .lens-swot-cell { padding: 4px; }
    .lens-swot-label {
        font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 4px;
        display: inline-block; margin-bottom: 6px;
    }
    .lens-swot-label.strength { background: #1E3A8A; color: #BFDBFE; }
    .lens-swot-label.weakness { background: #4A1F1F; color: #FCA5A5; }
    .lens-swot-label.opportunity { background: #14321F; color: #86EFAC; }
    .lens-swot-label.threat { background: #3A2E0A; color: #FCD34D; }
    .lens-row {
        display: grid;
        grid-template-columns: 110px minmax(0, 1fr);
        gap: 10px;
        padding: 4px 0;
        font-size: 13px;
        line-height: 1.6;
    }
    .lens-row-label {
        font-weight: 600; color: var(--navy);
    }
    .lens-row-text {
        color: var(--text-mid);
        word-break: keep-all;
        overflow-wrap: break-word;
    }
    @media (max-width: 640px) {
        .para-row.kpts-row {
            grid-template-columns: 1fr;
            row-gap: 4px;
        }
    }

    /* ---------- 이 회사는 쉽게 말해 ---------- */
    .simple-card {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue);
        border-radius: 10px;
        padding: 18px 22px;
        margin-bottom: 14px;
    }
    .simple-eyebrow {
        font-size: 12px;
        font-weight: 700;
        color: var(--navy);
        letter-spacing: 1.4px;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    .simple-body {
        font-size: 15.5px;
        color: var(--text);
        line-height: 1.8;
        word-break: keep-all;
    }

    /* ---------- 최근 주요 이벤트 ---------- */
    .event-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 10px;
    }
    .event-head {
        display: flex; justify-content: space-between; align-items: center;
        gap: 10px; flex-wrap: wrap; margin-bottom: 10px;
    }
    .event-meta {
        font-size: 13px;
        color: var(--muted);
        letter-spacing: 0.4px;
    }
    .event-meta b { color: var(--text); font-weight: 700; margin-right: 6px; }
    /* 구 .event-row 클래스는 사용하지 않음 — .para-row 로 통합 */

    /* ---------- Streamlit tabs (사각 segmented) ---------- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
        border-bottom: 1px solid var(--line);
        flex-wrap: nowrap;
    }
    .stTabs [data-baseweb="tab"] {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-bottom: none;
        border-radius: 8px 8px 0 0;
        padding: 10px 24px !important;
        font-weight: 600;
        font-size: 14px;
        color: var(--muted);
        white-space: nowrap;
        min-height: 42px;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background: var(--panel);
        color: var(--navy);
        border-color: var(--line);
        border-bottom: 1px solid var(--panel);
        font-weight: 700;
    }
    .stTabs [data-baseweb="tab-highlight"] { display: none; }
    .stTabs [data-baseweb="tab-panel"] {
        background: var(--panel);
        border: 1px solid var(--line);
        border-top: none;
        border-radius: 0 8px 8px 8px;
        padding: 22px 24px;
    }

    /* ---------- 뉴스 카드 ---------- */
    .news-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-left: 3px solid var(--blue-2);
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 10px;
    }
    .news-head {
        display: flex; justify-content: space-between; align-items: flex-start;
        gap: 10px; flex-wrap: wrap; margin-bottom: 6px;
    }
    .news-title {
        font-size: 16px;
        font-weight: 700;
        color: var(--text);
        line-height: 1.5;
    }
    .news-title a { color: var(--text); text-decoration: none; }
    .news-title a:hover { color: var(--blue); text-decoration: underline; }
    .news-meta {
        font-size: 13px;
        color: var(--muted);
        margin-bottom: 12px;
    }
    /* 구 .news-row 클래스는 사용하지 않음 — .para-row 로 통합 */
    .news-link {
        display: inline-block;
        margin-top: 10px;
        padding: 8px 14px;
        background: #1E3A8A;
        color: #BFDBFE;
        border: 1px solid #2D4A8A;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 700;
        text-decoration: none;
    }
    .news-link:hover { background: #172554; text-decoration: none; }

    /* ---------- 가치평가 비교 요약 (section-summary alias) ---------- */
    .val-summary {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 18px 22px;
        font-size: 14px;
        line-height: 1.7;
        color: var(--text-mid);
        margin-top: 18px;
        margin-bottom: 8px;
        word-break: keep-all;
    }

    /* ---------- 재무 요약 row (가로 KV 바) ---------- */
    .fin-summary {
        display: flex;
        gap: 28px;
        flex-wrap: wrap;
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 16px 20px;
        margin: 24px 0 8px 0;
    }
    .fin-summary .item { font-size: 13.5px; color: var(--text); }
    .fin-summary .item .k { color: var(--muted); margin-right: 6px; font-weight: 600; }
    .fin-summary .item .v { color: var(--text); font-weight: 700; font-variant-numeric: tabular-nums; }

    /* 이벤트 분류 칩 + 상태 배지 + 리서치 품질 메타 */
    .chip {
        display: inline-flex; align-items: center;
        padding: 4px 12px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.4px;
        border: 1px solid transparent;
    }
    /* 이벤트 카드 푸터 (출처 / 업데이트 / Confidence) */
    .event-foot {
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px dashed var(--line);
        font-size: 12.5px;
        color: var(--muted);
        line-height: 1.7;
    }
    .event-foot b { color: var(--text-mid); font-weight: 700; margin-right: 4px; }
    .event-foot .sep { margin: 0 10px; color: var(--line-strong); }
    /* 상태 배지 (status: 진행 중/종료/완료/무산/확인 필요) */
    .status-badge {
        display: inline-flex; align-items: center;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.3px;
        margin-left: 6px;
        border: 1px solid transparent;
    }
    .status-진행 { background: #1E3A8A; color: #BFDBFE; border-color: #2D4A8A; }
    .status-종료 { background: #1F2937; color: var(--text-mid); border-color: var(--line-strong); }
    .status-완료 { background: #14321F; color: #86EFAC; border-color: #1E5235; }
    .status-무산 { background: #1F2937; color: var(--text-mid); border-color: var(--line-strong); }
    .status-확인 { background: #4A3A12; color: #FCD34D; border-color: #6B541E; }

    /* 리서치 품질 카드 */
    .quality-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 18px 22px;
        margin-bottom: 24px;
    }
    .quality-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 16px 24px;
    }
    .quality-item .k {
        font-size: 11px;
        color: var(--muted);
        letter-spacing: 1.2px;
        text-transform: uppercase;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .quality-item .v {
        font-size: 15px;
        color: var(--text);
        font-weight: 700;
    }
    .quality-foot {
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px dashed var(--line);
        font-size: 13px;
        color: var(--muted);
    }
    .quality-foot b { color: var(--text-mid); font-weight: 700; margin-right: 4px; }
    @media (max-width: 768px) {
        .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .chip-strengthen { background: #14321F; color: #86EFAC; border-color: #1E5235; }
    .chip-weaken { background: #4A1F1F; color: #FCA5A5; border-color: #6B2E2E; }
    .chip-new-risk { background: #4A3A12; color: #FCD34D; border-color: #6B541E; }
    .chip-noise { background: #1F2937; color: var(--text-mid); border-color: var(--line-strong); }
    .chip-needs-check { background: #1E3A8A; color: #BFDBFE; border-color: #2D4A8A; }

    .bullet {
        display: flex; gap: 12px; padding: 8px 0;
        font-size: 15px; line-height: 1.7; color: var(--text);
    }
    .bullet-num {
        color: var(--navy);
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        min-width: 22px;
    }

    /* ---------- 진단 박스 ---------- */
    .diagnose {
        background: #2E2410;
        border: 1px solid #6B541E;
        border-radius: 10px;
        padding: 14px 16px;
        margin-bottom: 16px;
        font-size: 13.5px;
        color: var(--text);
    }
    .diagnose b { color: #FCD34D; }
    .diagnose code {
        background: rgba(249,250,251,0.10);
        padding: 1px 6px;
        border-radius: 4px;
        font-size: 12px;
    }

    /* ---------- 일반 버튼 (본문) ---------- */
    .stButton > button {
        background: var(--panel);
        color: var(--text-mid);
        border: 1px solid var(--line-strong);
        border-radius: 8px;
        font-weight: 600;
        padding: 8px 16px;
    }
    .stButton > button:hover {
        border-color: var(--navy);
        color: var(--navy);
    }
    .stButton > button[kind="primary"] {
        background: #1E3A8A;
        color: #BFDBFE;
        border-color: #2D4A8A;
        font-weight: 700;
    }
    .stButton > button[kind="primary"]:hover {
        background: #172554;
        color: #BFDBFE;
        border-color: #374151;
    }

    /* ---------- 입력 ---------- */
    [data-baseweb="select"] > div {
        background: var(--panel) !important;
        border-color: var(--line-strong) !important;
        border-radius: 8px !important;
    }

    /* ---------- 링크 ---------- */
    a { color: var(--blue); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* ---------- 기본 streamlit metric 숨김 (우리 카드 사용) ---------- */
    [data-testid="stMetricValue"] { color: var(--text); }

    /* ---------- 모바일 (<768px) 폴백 ---------- */
    @media (max-width: 768px) {
        .block-container { padding-top: 0.8rem; padding-left: 0.8rem; padding-right: 0.8rem; }
        .alpha-title, .page-title { font-size: 24px !important; }
        .judgment-card { padding: 16px 18px; }
        .judgment-body { font-size: 16px; }
        .card, .pick { padding: 16px 18px; }
        .pick-name { font-size: 17px; }
        .metric-card { padding: 16px 18px; min-height: 110px; }
        .metric-value { font-size: 18px; }
        .env-block { min-height: auto; padding: 16px 18px; }
        .macro { padding: 18px 20px; }
        .news-card, .event-card { padding: 18px 20px; }
        .quality-card { padding: 14px 16px; }
        .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .stTabs [data-baseweb="tab"] { padding: 8px 14px !important; font-size: 13px; }
        /* 시장 자산 테이블 — 모바일에서 1열 1줄로 */
        .market-row { font-size: 13px; padding: 10px 0; grid-template-columns: 1.4fr 0.8fr 0.8fr; }
        .market-name { font-size: 13px; }
    }

    /* iframe embed 시 보이는 흰 테두리/외곽선 제거 — 모든 구조 컨테이너 */
    html, body, .stApp, .main, section.main, .block-container,
    [data-testid="stApp"], [data-testid="stAppViewContainer"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"],
    [data-testid="stHeader"], [data-testid="stToolbar"],
    [data-testid="stBottomBlockContainer"], [data-testid="stSidebarContent"],
    iframe {
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
    }
    [data-testid="stDecoration"] { display: none !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------

# TTL 30분 → 6시간으로 — Streamlit Cloud 가 idle 후 깨어날 때마다 ~42 종목
# 뉴스 RSS·yfinance 재수집 (수십 초~수 분)이 매번 일어나 첫 로드가 너무 느렸음.
# 파이프라인이 1일 2회 돌고 서버측 데이터도 그 주기로 갱신되므로 6시간 캐시는
# 신선도 측면에서도 충분하다. 사용자가 trigger_refresh() 누르면 즉시 갱신됨.
@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def cached_build_rows(_token: int, fetch_news: bool = True) -> list[dict[str, Any]]:
    return build_rows(fetch_news=fetch_news)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def cached_market_context(_token: int) -> tuple[dict[str, Any], str]:
    return fetch_market_context()


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def cached_daily_trackers(_token: int) -> dict[str, Any]:
    """Core (TQQQ/QQQ/SPY/BTC/KODEX200) + Parking (MCD/COST/WMT/KO/PEP/V/MA/JNJ).

    매일 추적해야 하는 자산군 — engine universe 와 분리 (alpha 와 parking 은 다른 카테고리).
    """
    from src.daily_tracking import fetch_all_trackers
    return fetch_all_trackers()


@st.cache_data(show_spinner=False, ttl=60 * 60)
def cached_annual_financials(_token: int, ticker: str) -> list[dict[str, Any]]:
    return fetch_annual_financials(ticker)


@st.cache_data(show_spinner=False, ttl=60 * 60)
def cached_quarterly_financials(_token: int, ticker: str) -> list[dict[str, Any]]:
    return fetch_quarterly_financials(ticker)


@st.cache_data(show_spinner=False, ttl=60 * 60)
def cached_max_history(_token: int, ticker: str):
    return fetch_max_history(ticker)


# ---------------------------------------------------------------------------
# 페이지 / 종목 변경 시 화면 최상단으로 스크롤
# ---------------------------------------------------------------------------

def scroll_to_top():
    """rerun 직후 화면을 강제로 최상단으로 이동.

    - Streamlit 렌더링 타이밍 차이를 흡수하기 위해 즉시 + 50ms + 200ms 3회 호출.
    - window.parent.scrollTo (iframe 외부) / scrollIntoView(#page-top) 다중 fallback.
    - behavior 'instant' 로 중간 스크롤 화면이 보이지 않도록 함.
    """
    import streamlit.components.v1 as _components
    _components.html(
        """
        <script>
            const scrollToTop = () => {
                try { window.parent.scrollTo({ top: 0, left: 0, behavior: 'instant' }); } catch (e) {}
                try { window.parent.document.documentElement.scrollTop = 0; } catch (e) {}
                try { window.parent.document.body.scrollTop = 0; } catch (e) {}
                try { window.scrollTo(0, 0); } catch (e) {}
                try {
                    const anchor = window.parent.document.getElementById('page-top');
                    if (anchor) anchor.scrollIntoView({ behavior: 'instant', block: 'start' });
                } catch (e) {}
            };
            scrollToTop();
            setTimeout(scrollToTop, 50);
            setTimeout(scrollToTop, 200);
        </script>
        """,
        height=0,
    )


def _sync_query_params(page: str, ticker: str | None = None, news_id: str | None = None):
    """URL query params 동기화 — Streamlit 의 native 동작에 의존.

    Streamlit 1.30+ 는 query_params 변경 시 pushState 로 history 추가.
    브라우저 back 시 URL 변경 → 자동 rerun → 본 모듈 상단의 sync 블록이
    session_state 를 업데이트.
    """
    try:
        qp = st.query_params
        qp["page"] = page
        if ticker:
            qp["ticker"] = ticker
        elif "ticker" in qp:
            del qp["ticker"]
        if news_id:
            qp["news_id"] = news_id
        elif "news_id" in qp:
            del qp["news_id"]
    except Exception:
        pass


def navigate_to(
    nav_key: str,
    ticker: str | None = None,
    news_id: str | None = None,
    push_history: bool = True,
) -> None:
    """공통 페이지 이동.

    - 종목 카드 / 뉴스 카드 / 우량주 과매도 / 관심종목 의 모든 이동 경로가
      이 함수를 통해 일관되게 처리되도록 한다.
    - push_history=True 면 현재 상태를 navigation_history 에 push.
    - 사이드바 메뉴 클릭은 navigate_from_sidebar() 사용 (history clear).
    """
    if push_history:
        history = st.session_state.setdefault("navigation_history", [])
        current_state = {
            "page_key": st.session_state.get("nav_key", "brief"),
            "ticker": st.session_state.get("selected_ticker"),
            "news_id": st.session_state.get("selected_news_id"),
        }
        # 같은 상태가 연속으로 쌓이지 않도록
        if not history or history[-1] != current_state:
            history.append(current_state)
        st.session_state["navigation_history"] = history[-20:]

    if ticker is not None:
        st.session_state["selected_ticker"] = ticker
    if news_id is not None:
        st.session_state["selected_news_id"] = news_id
    st.session_state["nav_key"] = nav_key
    st.session_state["scroll_to_top"] = True

    _sync_query_params(
        nav_key,
        st.session_state.get("selected_ticker"),
        st.session_state.get("selected_news_id"),
    )
    st.rerun()


def navigate_from_sidebar(nav_key: str) -> None:
    """사이드바 메뉴 클릭 — history 초기화 + push_history=False."""
    st.session_state["navigation_history"] = []
    st.session_state["selected_news_id"] = None
    navigate_to(nav_key, push_history=False)


def go_back() -> None:
    """앱 내부 뒤로가기."""
    history = st.session_state.get("navigation_history", []) or []
    if history:
        prev = history.pop()
        st.session_state["navigation_history"] = history
        st.session_state["nav_key"] = prev.get("page_key") or "brief"
        st.session_state["selected_ticker"] = prev.get("ticker")
        st.session_state["selected_news_id"] = prev.get("news_id")
    else:
        st.session_state["nav_key"] = "brief"
        st.session_state["selected_ticker"] = None
        st.session_state["selected_news_id"] = None
    st.session_state["scroll_to_top"] = True
    _sync_query_params(
        st.session_state["nav_key"],
        st.session_state.get("selected_ticker"),
        st.session_state.get("selected_news_id"),
    )
    st.rerun()


def render_back_button(key_suffix: str = ""):
    """페이지 헤더 위에 표시할 ← 이전으로 버튼."""
    history = st.session_state.get("navigation_history") or []
    if not history:
        return
    cols = st.columns([2, 8])
    with cols[0]:
        if st.button("← 이전으로", key=f"back_{key_suffix}", use_container_width=True):
            go_back()


def _portfolio_mtime_token() -> int:
    """data/portfolio.json mtime 을 정수로 — 파일 변경 시 cached_* 자동 invalidate.

    portfolio.json 이 git push 로 갱신되면 mtime 이 바뀌어 token 이 달라지고
    @st.cache_data 가 새 key 로 인식해서 재계산을 트리거.
    """
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parent / "data" / "portfolio.json"
        if p.exists():
            return int(p.stat().st_mtime)
    except Exception:
        pass
    return 0


def get_refresh_token() -> int:
    base = st.session_state.get("refresh_token", 0)
    # portfolio.json mtime 을 더해서 파일 갱신 시 자동 invalidate
    return base + _portfolio_mtime_token()


def trigger_refresh():
    st.session_state["refresh_token"] = st.session_state.get("refresh_token", 0) + 1
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# Tag 렌더
# ---------------------------------------------------------------------------

ACTION_TAG_CLASS = {
    "Research Now": "tag-research-now",
    "Quality Dislocation": "tag-quality-dislocation",
    "Watchlist": "tag-watchlist",
    "Wait for Entry": "tag-wait-for-entry",
    "Too Crowded": "tag-too-crowded",
    "Need Thesis Check": "tag-need-thesis-check",
    "Avoid": "tag-avoid",
    "Data Unavailable": "tag-data-unavailable",
}


def render_tag(tag: str) -> str:
    cls = ACTION_TAG_CLASS.get(tag, "tag-watchlist")
    return f'<span class="tag {cls}">{tag}</span>'


# ---------------------------------------------------------------------------
# Sidebar — 브랜드, 사각 버튼 nav, 환경 진단
# ---------------------------------------------------------------------------

NAV_ITEMS: list[tuple[str, str]] = [
    ("brief", "오늘의 투자 브리프"),
    ("regime", "Portfolio review"),
    ("validation", "Validation Lab"),
    ("journal", "Decision Journal"),
    ("discovery", "Discovery"),
    ("detail", "종목 상세"),
    ("dislocation", "우량주 과매도"),
    ("watchlist", "관심종목"),
    ("retro", "회고 리포트"),
]
NAV_LABEL_BY_KEY = {k: l for k, l in NAV_ITEMS}

# Query params 와 session_state 동기화 — 매 rerun 마다 실행
# (브라우저 뒤로가기 / 앞으로가기로 URL 만 바뀐 경우도 화면 따라가도록)
try:
    qp = st.query_params
    qp_page = (qp.get("page") or "").strip()
    qp_ticker = (qp.get("ticker") or "").strip() or None
    qp_news_id = (qp.get("news_id") or "").strip() or None
except Exception:
    qp_page, qp_ticker, qp_news_id = "", None, None

if "nav_key" not in st.session_state:
    # 최초 로드 — URL 우선
    st.session_state["nav_key"] = qp_page or "brief"
    st.session_state["selected_ticker"] = qp_ticker
    st.session_state["selected_news_id"] = qp_news_id
else:
    # 후속 rerun — 브라우저 뒤로/앞으로 가기로 URL 이 session_state 보다
    # 앞서 바뀐 경우 동기화 (in-app navigate_to 후에는 이미 동기화돼 있음)
    cur_page = st.session_state.get("nav_key") or "brief"
    cur_ticker = st.session_state.get("selected_ticker")
    cur_news_id = st.session_state.get("selected_news_id")
    target_page = qp_page or "brief"
    url_changed = (
        target_page != cur_page
        or (qp_ticker or "") != (cur_ticker or "")
        or (qp_news_id or "") != (cur_news_id or "")
    )
    if url_changed:
        st.session_state["nav_key"] = target_page
        st.session_state["selected_ticker"] = qp_ticker
        st.session_state["selected_news_id"] = qp_news_id
        st.session_state["scroll_to_top"] = True

if "navigation_history" not in st.session_state:
    st.session_state["navigation_history"] = []
if "selected_news_id" not in st.session_state:
    st.session_state["selected_news_id"] = None

with st.sidebar:
    st.markdown(
        '<div class="brand">'
        '<div class="brand-mark">α</div>'
        '<div class="brand-text">Alpha</div>'
        "</div>"
        '<div class="brand-sub">Investment Research Platform</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="nav-label">Navigation</div>', unsafe_allow_html=True)
    for key, label in NAV_ITEMS:
        is_active = st.session_state["nav_key"] == key
        if st.button(
            label,
            key=f"nav_{key}",
            type="primary" if is_active else "secondary",
            use_container_width=True,
        ):
            navigate_from_sidebar(key)

    st.markdown('<div class="nav-label" style="margin-top:24px;">System</div>', unsafe_allow_html=True)
    with st.expander("환경 진단", expanded=False):
        _yfs = get_yfinance_status()
        st.code(
            "Python    " + _yfs["python_executable"] + "\n"
            "Version   " + _yfs["python_version"] + "\n"
            "yfinance  " + (
                f"{_yfs['yfinance_version']}"
                if _yfs["yfinance_installed"]
                else f"NOT INSTALLED — {_yfs['import_error']}"
            ),
            language="text",
        )

nav = st.session_state["nav_key"]


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

token = get_refresh_token()
load_error: str | None = None


def _bounded_call(fn, *args, timeout: float = 90.0, **kwargs):
    """fn 을 별 thread 에서 호출하고 timeout 안에 끝나지 않으면 TimeoutError.

    Streamlit Cloud 에서 yfinance/Yahoo 가 hang 하면 앱이 영원히 spinner 만
    돌아 깨어나지 않던 버그를 차단한다. 실패해도 앱은 빈 데이터로 렌더된다.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TO
    with ThreadPoolExecutor(max_workers=1) as _pool:
        _fut = _pool.submit(fn, *args, **kwargs)
        return _fut.result(timeout=timeout)


with st.spinner("데이터 준비 중..."):
    try:
        rows = _bounded_call(cached_build_rows, token, fetch_news=True, timeout=90)
    except Exception as e:
        rows = []
        load_error = f"유니버스 수집 timeout/실패 — 빈 데이터로 렌더: {e}"
    try:
        proxies, market_summary = _bounded_call(cached_market_context, token, timeout=45)
    except Exception as e:
        proxies, market_summary = {}, "금일 시장 데이터 수집 실패 (timeout)."
        load_error = (load_error + " | " if load_error else "") + str(e)

if load_error:
    st.warning(f"⚠ 데이터 수집 일부 실패 — 사용 가능한 부분만 표시. ({load_error})")


def _maybe_snapshot():
    if not rows:
        return
    diag = diagnose_data_health(rows)
    if diag.get("all_failed"):
        return
    today = _dt.date.today().isoformat()
    if st.session_state.get("snapshot_date") == today:
        return
    try:
        append_snapshot(rows)
        st.session_state["snapshot_date"] = today
    except Exception as e:
        st.warning(f"스냅샷 저장 실패: {e}")


_maybe_snapshot()


def render_data_health_warning():
    if not rows:
        return
    diag = diagnose_data_health(rows)
    if not diag.get("all_failed"):
        if diag["available"] < diag["total"]:
            miss = diag["total"] - diag["available"]
            st.markdown(
                f'<div class="diagnose">투자 유니버스 {diag["total"]}종목 중 {miss}종목의 가격 데이터 수집에 실패했습니다. 해당 종목은 <b>Data Unavailable</b>로 표시됩니다.</div>',
                unsafe_allow_html=True,
            )
        return
    yf_status = get_yfinance_status()
    py_exec = yf_status["python_executable"]
    if not yf_status["yfinance_installed"]:
        st.markdown(
            '<div class="diagnose">'
            "<b>yfinance 가 Streamlit 사용 Python 환경에 설치되어 있지 않습니다.</b><br/>"
            f"Python: <code>{py_exec}</code><br/>"
            f"Import 에러: <code>{yf_status['import_error']}</code><br/><br/>"
            "해결 방법: 터미널에서 아래 명령으로 동일 환경에 설치한 뒤, Streamlit을 재시작하세요.<br/>"
            f"<code>{py_exec} -m pip install -U yfinance feedparser pandas plotly requests</code>"
            "</div>",
            unsafe_allow_html=True,
        )
        return
    sample = diag.get("sample_error") or "원인 불명"
    st.markdown(
        '<div class="diagnose">'
        "<b>금일 가격 데이터 수집이 모두 실패했습니다.</b><br/>"
        f"yfinance {yf_status['yfinance_version']} 는 정상 import되었으나 데이터 호출이 실패합니다. "
        "Yahoo 측에서 일시적으로 차단되었을 가능성이 높습니다 (보통 5~15분 후 회복).<br/>"
        f"가장 흔한 에러: <code>{sample}</code>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 헤더 (페이지별)
# ---------------------------------------------------------------------------

def page_header(title: str, meta: str = "", show_refresh: bool = True):
    today_str = _dt.date.today().strftime("%Y.%m.%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][_dt.date.today().weekday()]
    now_str = _dt.datetime.now().strftime("%H:%M KST")

    col_l, col_r = st.columns([6, 1])
    with col_l:
        st.markdown(
            f'<h1 class="page-title">{title}</h1>'
            f'<div class="page-meta"><b>{today_str} ({weekday})</b>'
            + (f" · {meta}" if meta else "")
            + f" · {now_str}</div>",
            unsafe_allow_html=True,
        )
    with col_r:
        if show_refresh:
            st.markdown('<div style="height:18px;"></div>', unsafe_allow_html=True)
            cols = [None, st.container()]
            with cols[1]:
                if st.button("Update", use_container_width=True, type="primary"):
                    # 파이프라인을 Streamlit Cloud 에서 직접 실행하지 않는다.
                    # Cloud 의 리소스/시간 제한에 끊기면 SQLite DB 가 malformed 로
                    # 손상되기 때문. 대신 GitHub Actions 의 Daily Research 워크플로를
                    # 트리거한다 — 깨끗한 러너에서 끝까지 돌고 DB 를 안전하게 커밋한다.
                    import os as _os
                    _GH_REPO = "hyeongwoohyeong/Alpha"
                    _GH_WF = "daily_research.yml"
                    _actions_url = (
                        f"https://github.com/{_GH_REPO}/actions/workflows/{_GH_WF}"
                    )
                    _token = None
                    try:
                        _token = st.secrets.get("GITHUB_TOKEN")  # type: ignore[attr-defined]
                    except Exception:
                        _token = None
                    if not _token:
                        _token = _os.environ.get("GITHUB_TOKEN")
                    if _token:
                        try:
                            import requests as _rq
                            _resp = _rq.post(
                                f"https://api.github.com/repos/{_GH_REPO}"
                                f"/actions/workflows/{_GH_WF}/dispatches",
                                headers={
                                    "Authorization": f"Bearer {_token}",
                                    "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28",
                                },
                                json={"ref": "main"},
                                timeout=15,
                            )
                            if _resp.status_code in (201, 204):
                                st.success(
                                    "GitHub Actions 파이프라인을 트리거했습니다. "
                                    "러너에서 안전하게 실행된 뒤(보통 3~10분) DB 가 "
                                    "갱신되고 앱이 자동 재배포됩니다."
                                )
                            else:
                                st.error(
                                    f"워크플로 트리거 실패 (HTTP {_resp.status_code}). "
                                    "아래 링크에서 직접 실행해 주세요."
                                )
                                st.markdown(f"[GitHub Actions 열기]({_actions_url})")
                        except Exception as e:
                            st.error(f"워크플로 트리거 오류: {e}")
                            st.markdown(f"[GitHub Actions 열기]({_actions_url})")
                    else:
                        st.info(
                            "데이터는 매일 07:30(KST) GitHub Actions 가 자동 갱신합니다. "
                            "즉시 갱신하려면 아래에서 'Daily Research' 워크플로를 직접 "
                            "실행하세요. (원클릭 트리거를 쓰려면 Streamlit Secrets 에 "
                            "GITHUB_TOKEN 을 등록하세요.)"
                        )
                        st.markdown(
                            f"[GitHub Actions 열기 — Daily Research]({_actions_url})"
                        )
    st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="border-bottom:1px solid var(--line); margin-bottom:22px;"></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 시장 자산 테이블 (금일 시장 환경 첫 블록 본문)
# ---------------------------------------------------------------------------

_MARKET_TABLE_LABELS: list[tuple[str, str]] = [
    ("SPY", "S&P500 (SPY)"),
    ("QQQ", "나스닥100 (QQQ)"),
    ("IWM", "러셀2000 (IWM)"),
    ("TLT", "장기채 (TLT)"),
    ("GLD", "금 (GLD)"),
    ("USO", "원유 (USO)"),
    ("BTC-USD", "비트코인 (BTC-USD)"),
]


def _ret_html(v) -> str:
    if v is None:
        return '<span class="ret-neutral">—</span>'
    pct = v * 100
    if pct > 0:
        return f'<span class="ret-positive">+{pct:.2f}%</span>'
    if pct < 0:
        return f'<span class="ret-negative">{pct:.2f}%</span>'
    return '<span class="ret-neutral">0.00%</span>'


def _market_table_html(proxies: dict[str, Any]) -> str:
    rows: list[str] = [
        '<div class="market-header">'
        "<div>자산</div>"
        '<div class="col-r">1일</div>'
        '<div class="col-r">1개월</div>'
        "</div>"
    ]
    for sym, label in _MARKET_TABLE_LABELS:
        d = (proxies or {}).get(sym) or {}
        if not d.get("available"):
            rows.append(
                '<div class="market-row">'
                f'<div class="market-name">{label}</div>'
                '<div class="market-ret"><span class="ret-neutral">—</span></div>'
                '<div class="market-ret"><span class="ret-neutral">—</span></div>'
                "</div>"
            )
            continue
        dr = d.get("daily_return")
        r1m = d.get("1m_return")
        rows.append(
            '<div class="market-row">'
            f'<div class="market-name">{label}</div>'
            f'<div class="market-ret">{_ret_html(dr)}</div>'
            f'<div class="market-ret">{_ret_html(r1m)}</div>'
            "</div>"
        )
    return '<div class="market-table">' + "".join(rows) + "</div>"


# ---------------------------------------------------------------------------
# 카드: 금일 주요 관찰 종목
# ---------------------------------------------------------------------------

_RANGE_OPTIONS = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "MAX"]


def _filter_history_by_range(hist, range_key: str):
    if hist is None or hist.empty:
        return hist
    if range_key == "MAX":
        return hist
    last = hist.index[-1]
    if range_key == "YTD":
        try:
            start = pd.Timestamp(year=last.year, month=1, day=1)
        except Exception:
            return hist
    else:
        deltas = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5}
        d = deltas.get(range_key, 365)
        start = last - pd.Timedelta(days=d)
    try:
        return hist[hist.index >= start]
    except Exception:
        return hist


def _stats_top(hist) -> dict:
    closes = hist["Close"].dropna()
    if closes.empty:
        return {}
    last = float(closes.iloc[-1])
    last_date = closes.index[-1]
    # 52주 윈도우
    if len(closes) > 252:
        w52 = closes.iloc[-252:]
    else:
        w52 = closes
    high52 = float(w52.max())
    dd52 = (last / high52 - 1.0) if high52 > 0 else None
    # 1Y / MAX 수익률
    r1y = None
    if len(closes) > 252:
        try:
            r1y = (last / float(closes.iloc[-252])) - 1.0
        except Exception:
            r1y = None
    r_max = None
    try:
        first = float(closes.iloc[0])
        if first > 0:
            r_max = (last / first) - 1.0
    except Exception:
        r_max = None
    return {
        "last": last,
        "last_date": last_date,
        "dd52": dd52,
        "r1y": r1y,
        "r_max": r_max,
    }


def render_price_chart(row: dict[str, Any]):
    """장기 주가 흐름 — 기본 MAX, 기간 segmented 버튼, zoom/pan/range slider, 이벤트 마커."""
    ticker = row["ticker"]

    with st.spinner("주가 데이터 수집 중..."):
        hist_full = cached_max_history(token, ticker)

    # 폴백: max history 가 비면 1y history (market_data) 사용
    if hist_full is None or getattr(hist_full, "empty", True):
        hist_full = (row.get("market_data") or {}).get("history")

    if hist_full is None or hist_full.empty:
        st.markdown(
            '<div class="card">차트 데이터를 가져올 수 없습니다. 데이터 업데이트 후 다시 확인하세요.</div>',
            unsafe_allow_html=True,
        )
        return

    def _fmt_date(d):
        try:
            return d.strftime("%Y.%m.%d")
        except Exception:
            return str(d)[:10]

    def _fmt_money(v):
        return "-" if v is None else f"${v:,.2f}"

    # ── 기간 선택 segmented (사각 버튼) ───────────────────────────────────
    range_state_key = f"price_range::{ticker}"
    if range_state_key not in st.session_state:
        st.session_state[range_state_key] = "MAX"
    current_range = st.session_state[range_state_key]

    btn_cols = st.columns(len(_RANGE_OPTIONS))
    for i, r in enumerate(_RANGE_OPTIONS):
        with btn_cols[i]:
            if st.button(
                r,
                key=f"price_range_btn::{ticker}::{r}",
                type="primary" if current_range == r else "secondary",
                use_container_width=True,
            ):
                st.session_state[range_state_key] = r
                st.rerun()

    hist = _filter_history_by_range(hist_full, current_range)
    if hist is None or hist.empty:
        st.info("선택한 기간의 데이터가 부족합니다.")
        return

    closes = hist["Close"].dropna()
    if closes.empty:
        st.info("선택한 기간의 데이터가 부족합니다.")
        return

    # 현재 표시 기간의 최고/최저/현재가
    high_idx = closes.idxmax()
    low_idx = closes.idxmin()
    current_idx = closes.index[-1]
    high_price = float(closes.max())
    low_price = float(closes.min())
    current_price = float(closes.iloc[-1])

    # ── 가격 요약 박스 (선택 기간 기준) — 3개만 표시 ───────────────────────
    st.markdown(
        '<div style="margin-bottom:14px; padding:18px 22px; background:#1F2937; '
        'border:1px solid #2D3748; border-radius:12px;">'
        '<div style="font-size:12px; color:var(--muted); letter-spacing:1.4px; '
        'text-transform:uppercase; font-weight:700; margin-bottom:10px;">선택 기간 기준</div>'
        '<div style="display:flex; gap:42px; flex-wrap:wrap; '
        'font-variant-numeric:tabular-nums;">'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">최고가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#3B82F6;">${high_price:,.2f}</span></div>'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">최저가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#9CA3AF;">${low_price:,.2f}</span></div>'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">현재가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#F9FAFB;">${current_price:,.2f}</span>'
        f'<span style="font-size:12px; color:var(--muted); margin-left:8px;">{_fmt_date(current_idx)}</span></div>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Plotly 차트 ─────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=hist.index,
                y=hist["Close"],
                mode="lines",
                line=dict(color="#3B82F6", width=2),
                fill="tozeroy",
                fillcolor="rgba(59,130,246,0.10)",
                name="Close",
                hovertemplate="%{x|%Y.%m.%d}<br>$%{y:,.2f}<extra></extra>",
            )
        )

        # 현재가 수평선 (점선, 연한 그레이)
        fig.add_hline(
            y=current_price,
            line=dict(color="#374151", dash="dot", width=1),
        )

        # 최고/최저/현재 마커 — 통일 색상 (빨강 제거)
        fig.add_trace(go.Scatter(
            x=[high_idx], y=[high_price], mode="markers",
            marker=dict(size=8, color="#3B82F6", line=dict(color="#111827", width=2)),
            showlegend=False,
            hovertemplate=f"기간 내 최고가<br>${high_price:,.2f}<br>{_fmt_date(high_idx)}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[low_idx], y=[low_price], mode="markers",
            marker=dict(size=8, color="#9CA3AF", line=dict(color="#111827", width=2)),
            showlegend=False,
            hovertemplate=f"기간 내 최저가<br>${low_price:,.2f}<br>{_fmt_date(low_idx)}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[current_idx], y=[current_price], mode="markers",
            marker=dict(size=9, color="#06B6D4", line=dict(color="#111827", width=2)),
            showlegend=False,
            hovertemplate=f"현재가<br>${current_price:,.2f}<br>{_fmt_date(current_idx)}<extra></extra>",
        ))

        # ── 통일된 annotation (3종) ───────────────────────────────────────
        _ANN_COMMON = dict(
            showarrow=False,
            bgcolor="rgba(17,24,39,0.92)",
            bordercolor="#374151",
            borderwidth=1,
            borderpad=4,
            font=dict(family="-apple-system, sans-serif"),
        )
        fig.add_annotation(
            x=high_idx, y=high_price,
            text=f"최고 ${high_price:,.2f}",
            yshift=18,
            font=dict(size=12, color="#3B82F6"),
            **{k: v for k, v in _ANN_COMMON.items() if k != "font"},
        )
        fig.add_annotation(
            x=low_idx, y=low_price,
            text=f"최저 ${low_price:,.2f}",
            yshift=-18,
            font=dict(size=12, color="#9CA3AF"),
            **{k: v for k, v in _ANN_COMMON.items() if k != "font"},
        )
        fig.add_annotation(
            x=current_idx, y=current_price,
            text=f"현재 ${current_price:,.2f}",
            yshift=18, xshift=-12,
            font=dict(size=13, color="#06B6D4"),
            **{k: v for k, v in _ANN_COMMON.items() if k != "font"},
        )

        # 큐레이션된 주요 이벤트 마커 (현재 표시 기간 내에 들어오는 것만)
        markers = chart_event_markers(row)
        m_x = []
        m_y = []
        m_meta = []
        for m in markers:
            yy, mm, dd = m["date_tuple"]
            try:
                ts = pd.Timestamp(year=yy, month=mm, day=dd)
            except Exception:
                continue
            if ts < hist.index[0] or ts > hist.index[-1]:
                continue
            try:
                idx_pos = hist.index.get_indexer([ts], method="nearest")[0]
                actual = hist.index[idx_pos]
                price_at = float(hist.iloc[idx_pos]["Close"])
            except Exception:
                continue
            m_x.append(actual)
            m_y.append(price_at)
            m_meta.append(
                [m.get("type", ""), m.get("classification_label", ""), m.get("summary", "")]
            )

        if m_x:
            fig.add_trace(
                go.Scatter(
                    x=m_x,
                    y=m_y,
                    mode="markers",
                    marker=dict(
                        size=11,
                        symbol="diamond",
                        color="#06B6D4",
                        line=dict(color="#111827", width=2),
                    ),
                    showlegend=False,
                    name="주요 이벤트",
                    customdata=m_meta,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                        "%{x|%Y.%m.%d}<br>"
                        "$%{y:,.2f}<br>"
                        "%{customdata[2]}<extra></extra>"
                    ),
                )
            )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            margin=dict(l=10, r=10, t=24, b=10),
            height=440,
            dragmode="zoom",
            xaxis=dict(
                showgrid=False,
                zeroline=False,
                showline=False,
                color="#9CA3AF",
                tickfont=dict(color="#9CA3AF", size=12),
                rangeslider=dict(visible=True, thickness=0.05, bgcolor="#1F2937"),
                type="date",
            ),
            yaxis=dict(
                showgrid=False,
                zeroline=False,
                showline=False,
                color="#9CA3AF",
                tickfont=dict(color="#9CA3AF", size=12),
            ),
            showlegend=False,
            hoverlabel=dict(
                bgcolor="#1F2937",
                bordercolor="#374151",
                font=dict(color="#F9FAFB", size=13),
            ),
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "displayModeBar": True,
                "displaylogo": False,
                "scrollZoom": True,
                "modeBarButtonsToRemove": [
                    "select2d",
                    "lasso2d",
                    "autoScale2d",
                    "hoverClosestCartesian",
                    "hoverCompareCartesian",
                    "toggleSpikelines",
                ],
            },
        )

        # 해석 문구 (종목별 큐레이션 + 폴백)
        st.markdown(
            f'<div class="section-summary">{price_interpretation(row)}</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.info(f"차트 렌더링 실패: {e}")


def _rating_chip_class(rating: str) -> str:
    """등급 → CSS chip 클래스 매핑."""
    r = (rating or "").lower()
    if "strong" in r:
        return "chip-strengthen"
    if "rising risk" in r or "risk" in r:
        return "chip-new-risk"
    if "weak" in r:
        return "chip-weaken"
    if "medium" in r:
        return "chip-noise"
    return "chip-needs-check"


def _tier_color(tier: str) -> str:
    if tier == "Very Strong":
        return "#60A5FA"   # 밝은 블루
    if tier == "Strong":
        return "#3B82F6"
    if tier == "Moderate":
        return "#9CA3AF"
    if tier == "Weak":
        return "#FBBF24"
    if tier == "High Risk":
        return "#F87171"
    return "#9CA3AF"


def _alpha_score_color(score: float) -> str:
    """Alpha Score 점수 → 색상 (블루 강조 / 70 미만 회색)."""
    if score >= 90:
        return "#60A5FA"
    if score >= 80:
        return "#3B82F6"
    if score >= 70:
        return "#06B6D4"
    if score >= 60:
        return "#9CA3AF"
    return "#6B7280"


def render_alpha_score_section(alpha: dict | None):
    """Alpha Score — 통합 투자 매력도 점수 (종목 상세 최상단)."""
    if not alpha:
        return

    score = alpha.get("alpha_score")  # None 가능
    rating_en = alpha.get("alpha_rating_en", "Low Priority")
    rating_ko = alpha.get("alpha_rating_ko", "")
    confidence = alpha.get("data_confidence", "Medium")
    is_provisional = alpha.get("is_provisional", False)
    interpretation = alpha.get("interpretation", "")
    components = alpha.get("components", {}) or {}
    coverage_pct = alpha.get("scored_coverage_pct", 100)
    missing = alpha.get("missing_components", []) or []
    raw_alpha = alpha.get("raw_alpha_score")
    penalty = alpha.get("missing_data_penalty", 0.0)

    # 점수 표시 — None 이면 N/A
    if score is None:
        score_str = "N/A"
        color = "#F87171"
    else:
        score_str = f"{score:.0f}"
        color = _alpha_score_color(score)

    # 헤더 — Data Confidence + Coverage chip
    confidence_chip_color = {
        "Manual Override": "#C4B5FD",   # purple — 사용자 수동 큐레이션 (최우선)
        "LLM Researched": "#93C5FD",    # blue — LLM 자동 큐레이션 (SEC 10-K + 뉴스)
        "High": "#93C5FD",              # 하위 호환
        "Medium": "#9CA3AF",
        "Heuristic": "#FBBF24",         # amber — auto_profile (산업 + 정량)
        "Low": "#F87171",               # rose — 데이터 부족
    }.get(confidence, "#9CA3AF")

    # tier 별 시각 강조 — Manual Override 는 purple solid border, Heuristic 은 dashed amber
    if confidence == "Manual Override":
        confidence_extra_style = (
            "; border:1px solid #5B21B6; background:#2E1F52; padding:2px 8px; border-radius:6px;"
        )
    elif confidence == "LLM Researched":
        confidence_extra_style = (
            "; border:1px solid #1E40AF; background:#1E3A8A; padding:2px 8px; border-radius:6px;"
        )
    elif confidence == "Heuristic":
        confidence_extra_style = (
            "; border:1px dashed #D97706; background:#3A2E0A; padding:2px 8px; border-radius:6px;"
        )
    else:
        confidence_extra_style = ""

    provisional_label = " · Provisional" if is_provisional else ""

    # Coverage chip — 산정된 컴포넌트 비율
    if coverage_pct >= 85:
        cov_color = "#93C5FD"
    elif coverage_pct >= 70:
        cov_color = "#9CA3AF"
    elif coverage_pct >= 50:
        cov_color = "#FBBF24"
    else:
        cov_color = "#F87171"

    coverage_chip = (
        f'<span class="alpha-score-coverage" style="color:{cov_color}; '
        f'margin-left:12px; font-size:13px;">'
        f'Scored Coverage: {coverage_pct}% ({len(WEIGHTS_LABELS) - len(missing)}/'
        f'{len(WEIGHTS_LABELS)})'
        "</span>"
    )

    # Distribution Percentile — 전체 분포 내 위치
    percentile_chip = ""
    try:
        from src.alpha_score import compute_alpha_percentile
        pct = compute_alpha_percentile(score)
        if pct:
            top_pct = pct["top_pct"]
            rank = pct["rank"]
            total = pct["total"]
            pct_color = "#60A5FA" if top_pct <= 20 else (
                "#3B82F6" if top_pct <= 40 else (
                    "#9CA3AF" if top_pct <= 70 else "#F87171"
                )
            )
            percentile_chip = (
                f'<span style="color:{pct_color}; margin-left:12px; font-size:13px; '
                f'font-weight:600;" title="전체 {total} 종목 분포 — 중앙값 {pct["median"]}">'
                f'상위 {top_pct}% (#{rank}/{total})'
                "</span>"
            )
    except Exception:
        pass

    st.markdown(
        '<div class="alpha-score-card">'
        '<div class="alpha-score-header">'
        '<div class="alpha-score-eyebrow">Alpha Score'
        '<span class="alpha-score-eyebrow-sub"> · 통합 투자 매력도</span>'
        '</div>'
        '<div class="alpha-score-confidence" style="color:' + confidence_chip_color + confidence_extra_style + ';">'
        f'Data Confidence: {confidence}{provisional_label}'
        f'{coverage_chip}'
        f'{percentile_chip}'
        "</div>"
        "</div>"
        '<div class="alpha-score-main">'
        f'<div class="alpha-score-value" style="color:{color};">{score_str}'
        '<span class="alpha-score-denom">/ 100</span></div>'
        f'<div class="alpha-score-rating" style="color:{color};">{rating_en}</div>'
        f'<div class="alpha-score-rating-ko">{rating_ko}</div>'
        "</div>"
        f'<div class="alpha-score-interpretation">{interpretation}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # 8 컴포넌트 breakdown — score / N/A + status badge + reason tooltip
    bar_html = []
    for key, label in WEIGHTS_LABELS.items():
        comp = components.get(key)
        if not isinstance(comp, dict):
            # 하위 호환 — 옛날 dict[str, float] 구조였던 경우
            comp = {"score": comp if isinstance(comp, (int, float)) else None,
                    "status": "Scored", "confidence": "Medium", "reason": ""}

        c_score = comp.get("score")
        c_status = comp.get("status", "Scored")
        c_conf = comp.get("confidence", "Medium")
        c_reason = (comp.get("reason") or "").replace('"', "'")
        weight = WEIGHTS_PCT.get(key, 10)

        # status → badge style
        status_badge_style, status_badge_text = _component_status_badge(c_status, c_conf)

        # 라벨 셀 — 라벨 + weight 한 줄, chip 은 그 아래 별도 줄
        label_cell = (
            f'<div class="alpha-comp-label">'
            f'<div class="alpha-comp-label-main">{label}'
            f'<span class="alpha-comp-weight">{weight}%</span></div>'
            f'<div class="alpha-comp-status">{status_badge_style}</div>'
            "</div>"
        )

        if c_score is None:
            # N/A 표시
            bar_html.append(
                f'<div class="alpha-comp-row">'
                f'{label_cell}'
                '<div class="alpha-comp-bar-wrap">'
                '<div class="alpha-comp-bar alpha-comp-bar-na" style="width:100%; '
                'background:repeating-linear-gradient(45deg, #4A1F1F, #4A1F1F 6px, #6B2E2E 6px, #6B2E2E 10px);"></div>'
                "</div>"
                f'<div class="alpha-comp-value alpha-comp-na" style="color:#F87171;" title="{c_reason}">N/A</div>'
                "</div>"
            )
        else:
            v = float(c_score)
            bar_color = _alpha_score_color(v)
            bar_html.append(
                f'<div class="alpha-comp-row" title="{c_reason}">'
                f'{label_cell}'
                '<div class="alpha-comp-bar-wrap">'
                f'<div class="alpha-comp-bar" style="width:{v:.0f}%; background:{bar_color};"></div>'
                "</div>"
                f'<div class="alpha-comp-value" style="color:{bar_color};" title="{c_reason}">{v:.0f}</div>'
                "</div>"
            )

    st.markdown(
        '<div class="alpha-comp-grid">' + "".join(bar_html) + "</div>",
        unsafe_allow_html=True,
    )

    # Coverage / Penalty 안내
    if coverage_pct < 85 or missing:
        n_total = len(WEIGHTS_LABELS)
        n_scored = n_total - len(missing)
        missing_labels = ", ".join(WEIGHTS_LABELS.get(k, k) for k in missing[:5])
        if len(missing) > 5:
            missing_labels += f" 외 {len(missing) - 5}개"

        coverage_msg_html = (
            '<div class="alpha-coverage-note">'
            f'※ 총 {n_total}개 항목 중 <b>{n_scored}개</b>가 산정되었으며, '
            f'<b>{len(missing)}개</b>가 데이터 부족으로 제외되었습니다 '
            f'(Scored Coverage <b>{coverage_pct}%</b>'
        )
        if penalty < 0:
            coverage_msg_html += f', Missing Data Penalty <b>{penalty:.0f}점</b>'
        if raw_alpha is not None and raw_alpha != score and score is not None:
            coverage_msg_html += f', Raw {raw_alpha:.0f} → 조정 {score:.0f}'
        coverage_msg_html += ').'
        if missing:
            coverage_msg_html += f' 제외: {missing_labels}.'
        coverage_msg_html += "</div>"
        st.markdown(coverage_msg_html, unsafe_allow_html=True)

    # 점수 가이드 expander
    with st.expander("Alpha Score 판정 기준 보기", expanded=False):
        st.markdown(
            "**88~100** Exceptional Candidate — 최우선 정밀 검토 후보\n\n"
            "**80~87** High Conviction Candidate — 강한 비중 후보로 검토 가능\n\n"
            "**70~79** Research Now — 적극 리서치 후보\n\n"
            "**62~69** Watchlist / Wait for Better Entry — 관찰 / 진입 시점 대기\n\n"
            "**54~61** Need Thesis Check — Thesis 검증 필요\n\n"
            "**45~53** Low Priority — 현재 우선순위 낮음\n\n"
            "**0~44** Avoid / Not Enough Evidence — 회피 또는 근거 부족\n\n"
            "---\n\n"
            "**Component Status**\n\n"
            "- **Scored** — 충분한 데이터로 점수 산정됨\n"
            "- **Neutral** — 명확한 긍정/부정 근거 없음, 평균 수준 — 50점 부여\n"
            "- **Insufficient Data** — 판단에 필요한 데이터 부족 → N/A, 가중평균에서 제외\n"
            "- **Calculation Error** — 데이터 오류 또는 계산 실패 → N/A\n\n"
            "**Missing Data Penalty** — 산정 제외 비중에 따라 자동 차감\n\n"
            "- ≥ 85% 산정 → 페널티 없음\n"
            "- 70~84% → -3점\n"
            "- 50~69% → -7점\n"
            "- < 50% → -12점 + Provisional Score 표시\n\n"
            "Alpha Score 는 **자동 매수 추천** 이 아니라 Alpha 로직상 리서치 우선순위와 "
            "투자 매력도를 정량화한 보조 지표입니다. 88점 이상도 \"무조건 매수\"가 아닌 "
            "\"최우선 정밀 검토\" 의미이며, 실제 매수 전 valuation / 포지션 사이징 / "
            "리스크 체크가 필요합니다."
        )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Alpha Score 컴포넌트 라벨 / 가중치 (UI 내부 매핑)
# ---------------------------------------------------------------------------

WEIGHTS_LABELS: dict[str, str] = {
    "thesis_strength": "Thesis Strength",
    "earnings_quality": "Earnings Quality",
    "moat_lockin": "Moat / Lock-in",
    "price_opportunity": "Price Opportunity",
    "event_catalyst": "Event / Catalyst",
    "industry_bottleneck": "Industry / Bottleneck",
    "financial_quality": "Financial Quality",
    "risk_control": "Risk Control",
}

WEIGHTS_PCT: dict[str, int] = {
    "thesis_strength": 15, "earnings_quality": 15, "moat_lockin": 15,
    "price_opportunity": 15, "event_catalyst": 10, "industry_bottleneck": 10,
    "financial_quality": 10, "risk_control": 10,
}


def _component_status_badge(status: str, confidence: str) -> tuple[str, str]:
    """status / confidence → HTML chip + 텍스트.

    Returns: (html_chip, badge_text)
    """
    if status == "Scored":
        if confidence == "High":
            color, bg, label = "#86EFAC", "#14321F", "Scored · High"
        elif confidence == "Medium":
            color, bg, label = "#93C5FD", "#1E3A8A", "Scored · Med"
        else:
            color, bg, label = "#FCD34D", "#3A2E0A", "Scored · Low"
    elif status == "Neutral":
        color, bg, label = "#CBD5E1", "#1F2937", "Neutral"
    elif status == "Insufficient Data":
        color, bg, label = "#FCA5A5", "#4A1F1F", "N/A"
    elif status == "Not Applicable":
        color, bg, label = "#9CA3AF", "#1F2937", "N/A"
    elif status == "Calculation Error":
        color, bg, label = "#FCA5A5", "#4A1F1F", "Error"
    else:
        color, bg, label = "#9CA3AF", "#1F2937", status[:8]

    chip = (
        f'<span style="padding:1px 7px; border-radius:8px; '
        f'font-size:11px; font-weight:600; background:{bg}; color:{color}; '
        f'display:inline-block; white-space:nowrap;">'
        f'{label}</span>'
    )
    return chip, label


def _fetch_bull_bear_debate(ticker: str | None) -> dict | None:
    """auto_curation.fields_json 안의 bull_bear_debate 조회."""
    if not ticker:
        return None
    try:
        import json as _json
        with db.db_session() as conn:
            row = db.fetch_auto_curation(conn, ticker)
        if not row:
            return None
        fields = _json.loads(row["fields_json"])
        debate = fields.get("bull_bear_debate")
        return debate if isinstance(debate, dict) else None
    except Exception:
        return None


def render_bull_bear_debate_section(ticker: str | None):
    """Bull / Bear 사실·메커니즘 토론 — TradingAgents 리서처 토론을 매매·예측
    없이 리프레이밍한 적대적 검증 layer. auto_curation 종목에만 존재."""
    debate = _fetch_bull_bear_debate(ticker)
    if not debate:
        return

    bull_case = debate.get("bull_case", "")
    bear_case = debate.get("bear_case", "")
    bull_reb = debate.get("bull_rebuttal", "")
    bear_reb = debate.get("bear_rebuttal", "")
    swing = debate.get("swing_variables") or []
    summary = debate.get("debate_summary", "")
    if not (bull_case or bear_case):
        return

    st.markdown('<div class="section-title">Bull / Bear 토론</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="bbd-intro">두 분석가가 이 종목의 투자 논리를 사실·메커니즘 '
        '관점에서 검증합니다. 매수·매도 판단이 아니라, thesis 가 성립하는 근거와 '
        '약한 고리를 다툽니다.</div>',
        unsafe_allow_html=True,
    )

    col_bull, col_bear = st.columns(2)
    with col_bull:
        st.markdown(
            '<div class="bbd-side bbd-side-bull">'
            '<div class="bbd-side-head">Bull 분석가 — thesis 성립 근거</div>'
            f'<div class="bbd-text">{bull_case}</div>'
            + (
                '<div class="bbd-round-label">베어 지적에 대한 반론</div>'
                f'<div class="bbd-text">{bull_reb}</div>' if bull_reb else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    with col_bear:
        st.markdown(
            '<div class="bbd-side bbd-side-bear">'
            '<div class="bbd-side-head">Bear 분석가 — thesis 약한 고리</div>'
            f'<div class="bbd-text">{bear_case}</div>'
            + (
                '<div class="bbd-round-label">불 반론에 대한 재반박</div>'
                f'<div class="bbd-text">{bear_reb}</div>' if bear_reb else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )

    if swing:
        swing_items = "".join(f"<li>{s}</li>" for s in swing)
        st.markdown(
            '<div class="bbd-swing">'
            '<div class="bbd-swing-label">결론이 갈리는 핵심 관찰 변수</div>'
            f"<ul>{swing_items}</ul>"
            "</div>",
            unsafe_allow_html=True,
        )

    if summary:
        st.markdown(
            '<div class="bbd-summary">'
            '<div class="bbd-summary-label">쟁점 정리 (중립)</div>'
            f"{summary}"
            "</div>",
            unsafe_allow_html=True,
        )


def render_earnings_quality_section(eq: dict | None, ticker: str | None = None):
    """Earnings Quality & Moat Assessment 카드 그리드."""
    if not eq:
        return

    score = eq.get("earnings_durability_score", 0)
    tier = eq.get("earnings_durability_tier", "Moderate")
    color = _tier_color(tier)
    is_curated = eq.get("is_curated", False)
    is_manually_curated = eq.get("is_manually_curated", False)
    is_llm_researched = eq.get("is_llm_researched", False)
    is_auto_profiled = eq.get("is_auto_profiled", False)

    # 큐레이션 노후화 chip — Manual / LLM 모두 추적
    staleness_chip = ""
    if ticker:
        try:
            from src.curated import curation_staleness_status
            stale = curation_staleness_status(ticker)
            if stale:
                level = stale.get("level", "fresh")
                label = stale.get("label", "")
                if level == "stale":
                    color_chip = "#FCA5A5"
                    bg_chip = "#4A1F1F"
                elif level == "aging":
                    color_chip = "#FCD34D"
                    bg_chip = "#3A2E0A"
                else:
                    color_chip = "#93C5FD"
                    bg_chip = "#1E3A8A"
                staleness_chip = (
                    f'<span style="margin-left:10px; padding:2px 8px; '
                    f'border-radius:6px; font-size:12px; font-weight:500; '
                    f'background:{bg_chip}; color:{color_chip};">'
                    f'{label}</span>'
                )
        except Exception:
            pass

    st.markdown(
        '<div class="section-title">Earnings Quality & Moat Assessment'
        '<span style="font-size:13px; color:var(--muted); margin-left:8px;">이익의 질 및 해자 분석</span>'
        f'{staleness_chip}'
        '</div>',
        unsafe_allow_html=True,
    )

    # 상단 — Earnings Durability Score 배지 (4 단계 라벨)
    if is_manually_curated:
        note = (
            '<div style="font-size:12px; color:#C4B5FD; margin-top:4px; '
            'padding:4px 8px; background:#2E1F52; border:1px solid #5B21B6; '
            'border-radius:6px; display:inline-block;">'
            '※ Manual Override — 사용자가 직접 검토 / 입력한 큐레이션 데이터입니다.'
            "</div>"
        )
    elif is_llm_researched:
        note = (
            '<div style="font-size:12px; color:#93C5FD; margin-top:4px; '
            'padding:4px 8px; background:#1E3A8A; border:1px solid #1E40AF; '
            'border-radius:6px; display:inline-block;">'
            '※ LLM Researched — SEC 10-K (Item 1 / 1A / 7) + yfinance 사업 요약 + 최근 뉴스 '
            '한국어 요약을 기반으로 LLM (gpt-4o-mini) 이 자동 생성한 큐레이션입니다. '
            '60일 마다 재생성되며, 형우의 검토 후 curated.py 에 직접 입력하면 Manual Override 로 우선됩니다.'
            "</div>"
        )
    elif is_auto_profiled:
        note = (
            '<div style="font-size:12px; color:#FCD34D; margin-top:4px; '
            'padding:4px 8px; background:#3A2E0A; border:1px dashed #D97706; '
            'border-radius:6px; display:inline-block;">'
            '※ Heuristic — 큐레이션 미등록 종목으로, 산업 keyword + 정량 지표 (margin / FCF / ROE) '
            '기반 자동 추정 결과입니다. LLM 큐레이션 또는 사용자 수동 입력으로 보강 권장.'
            "</div>"
        )
    else:
        note = (
            '<div style="font-size:12px; color:var(--muted); margin-top:4px;">'
            '※ 큐레이션 미등록 종목 — 8 차원 모두 "확인 필요" 로 표시됩니다.'
            "</div>"
        )
    st.markdown(
        '<div class="card" style="margin-bottom:14px;">'
        '<div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">'
        f'<div style="font-size:14px; color:var(--muted);">Earnings Durability Score</div>'
        f'<div style="font-size:28px; font-weight:700; color:{color};">{score}</div>'
        f'<div style="font-size:14px; color:{color}; font-weight:600;">/ {tier}</div>'
        "</div>"
        f"{note}"
        "</div>",
        unsafe_allow_html=True,
    )

    # 8 차원 — 4 열 grid (모바일에서는 1 열로 자연 폴백)
    dims = eq.get("dimensions", {})
    cards_html = []
    for key, dim in dims.items():
        rating = dim.get("rating", "확인 필요")
        chip_cls = _rating_chip_class(rating)
        label_en = dim.get("label_en", key)
        label_ko = dim.get("label_ko", "")
        comment = dim.get("comment", "")
        cards_html.append(
            f'<div class="eq-dim-card">'
            f'<div class="eq-dim-head">'
            f'<span class="eq-dim-label">{label_en}<span class="eq-dim-sub"> · {label_ko}</span></span>'
            f'<span class="chip {chip_cls}">{rating}</span>'
            "</div>"
            f'<div class="eq-dim-comment">{comment}</div>'
            "</div>"
        )
    st.markdown(
        '<div class="eq-grid">' + "".join(cards_html) + "</div>",
        unsafe_allow_html=True,
    )

    # Moat Map — 7 항목 compact matrix
    moat = eq.get("moat_map", {})
    moat_cards = []
    for key, m in moat.items():
        rating = m.get("rating", "확인 필요")
        chip_cls = _rating_chip_class(rating)
        label_en = m.get("label_en", key)
        label_ko = m.get("label_ko", "")
        moat_cards.append(
            f'<div class="moat-cell">'
            f'<div class="moat-label">{label_en}<span class="moat-sub"> · {label_ko}</span></div>'
            f'<span class="chip {chip_cls}">{rating}</span>'
            "</div>"
        )
    st.markdown(
        '<div class="section-title" style="font-size:15px; margin-top:16px;">Moat Map</div>'
        '<div class="moat-grid">' + "".join(moat_cards) + "</div>",
        unsafe_allow_html=True,
    )

    # Alpha Judgment
    judgment = eq.get("alpha_judgment", "")
    if judgment:
        st.markdown(
            '<div class="judgment-card" style="margin-top:14px;">'
            '<div class="judgment-eyebrow">Alpha Judgment — 종합 판단</div>'
            f'<div class="judgment-body">{judgment}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)


def render_strategic_lens_section(lens: dict | None):
    """Strategic Lens — SWOT / PESTEL / 3C / 3P 2x2 카드."""
    if not lens:
        return

    st.markdown(
        '<div class="section-title">Strategic Lens'
        '<span style="font-size:13px; color:var(--muted); margin-left:8px;">전략적 관점 분석</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    swot = lens.get("swot", {}) or {}
    pestel = lens.get("pestel", {}) or {}
    three_c = lens.get("three_c", {}) or {}
    three_p = lens.get("three_p", {}) or {}

    def _bul(items: list[str]) -> str:
        items = items or []
        if not items:
            return '<div style="color:var(--muted);">확인 필요</div>'
        return "".join(
            f'<div style="font-size:14px; line-height:1.7; padding:2px 0;">• {x}</div>'
            for x in items
        )

    # SWOT card
    swot_html = (
        '<div class="lens-card">'
        '<div class="lens-card-title">SWOT</div>'
        '<div class="lens-swot-grid">'
        '<div class="lens-swot-cell"><div class="lens-swot-label strength">Strength</div>'
        f'{_bul(swot.get("strength"))}</div>'
        '<div class="lens-swot-cell"><div class="lens-swot-label weakness">Weakness</div>'
        f'{_bul(swot.get("weakness"))}</div>'
        '<div class="lens-swot-cell"><div class="lens-swot-label opportunity">Opportunity</div>'
        f'{_bul(swot.get("opportunity"))}</div>'
        '<div class="lens-swot-cell"><div class="lens-swot-label threat">Threat</div>'
        f'{_bul(swot.get("threat"))}</div>'
        "</div></div>"
    )

    # PESTEL — 6 항목 1줄씩
    def _row(label: str, val: str) -> str:
        return (
            '<div class="lens-row">'
            f'<span class="lens-row-label">{label}</span>'
            f'<span class="lens-row-text">{val}</span>'
            "</div>"
        )

    pestel_html = (
        '<div class="lens-card">'
        '<div class="lens-card-title">PESTEL</div>'
        + _row("Political", pestel.get("political", "확인 필요"))
        + _row("Economic", pestel.get("economic", "확인 필요"))
        + _row("Social", pestel.get("social", "확인 필요"))
        + _row("Technological", pestel.get("technological", "확인 필요"))
        + _row("Environmental", pestel.get("environmental", "확인 필요"))
        + _row("Legal", pestel.get("legal", "확인 필요"))
        + "</div>"
    )

    three_c_html = (
        '<div class="lens-card">'
        '<div class="lens-card-title">3C</div>'
        + _row("Company", three_c.get("company", "확인 필요"))
        + _row("Customer", three_c.get("customer", "확인 필요"))
        + _row("Competitor", three_c.get("competitor", "확인 필요"))
        + "</div>"
    )

    three_p_html = (
        '<div class="lens-card">'
        '<div class="lens-card-title">3P</div>'
        + _row("Product", three_p.get("product", "확인 필요"))
        + _row("Pricing", three_p.get("pricing", "확인 필요"))
        + _row("Positioning", three_p.get("positioning", "확인 필요"))
        + "</div>"
    )

    st.markdown(
        '<div class="lens-grid">'
        + swot_html + pestel_html + three_c_html + three_p_html
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)


def render_pick_card(row: dict[str, Any], idx: int, key_prefix: str = "pick"):
    md = row["market_data"]
    name = display_name(row.get("name_ko", ""), row["ticker"])
    price = fmt_money(md.get("current_price"))
    dr = md.get("daily_return")
    if dr is None:
        dr_cls, dr_str = "neutral", "-"
    elif dr > 0:
        dr_cls, dr_str = "pos", f"+{dr * 100:.2f}%"
    elif dr < 0:
        dr_cls, dr_str = "neg", f"{dr * 100:.2f}%"
    else:
        dr_cls, dr_str = "neutral", "0.00%"
    inv_type = investment_type(row)
    tag = row.get("action_tag", "Watchlist")

    # Alpha Score 배지 — Action Tag 왼쪽에 표시 (사용자 요청)
    alpha_badge_html = ""
    try:
        from src.alpha_score import calculate_alpha_score, reconcile_with_action_tag
        from src.earnings_quality import build_earnings_quality
        from src.bottleneck import build_bottleneck_thesis
        _eq = build_earnings_quality(row["ticker"], row)
        _bn_meta = {
            "ticker": row["ticker"],
            "name": row.get("name_en") or row.get("name_ko") or "",
            "sector": row.get("sector"),
            "industry": row.get("industry"),
        }
        _bn = build_bottleneck_thesis(row["ticker"], _bn_meta, md)
        _alpha = calculate_alpha_score(
            ticker=row["ticker"], market_data=md, scores=row.get("scores"),
            earnings_quality=_eq, bottleneck_thesis=_bn, news_agg=row.get("news_agg"),
            curated_events=row.get("curated_events"),
        )
        _alpha = reconcile_with_action_tag(_alpha, tag, too_crowded=(tag == "Too Crowded"))
        _score = _alpha.get("alpha_score")
        if _score is None:
            _score_str = "N/A"
            _color = "#F87171"
        else:
            _score_str = f"{_score:.0f}"
            _color = _alpha_score_color(_score)
        alpha_badge_html = (
            f'<div class="pick-alpha-badge" style="border-color:{_color}; color:{_color};">'
            f'<span class="pick-alpha-label">Alpha</span>'
            f'<span class="pick-alpha-score">{_score_str}</span>'
            "</div>"
        )
    except Exception:
        pass

    html = f"""
    <div class="pick">
      <div class="pick-head">
        <div>
          <div class="pick-name">{name}</div>
          <div class="pick-type">{inv_type}</div>
        </div>
        <div class="pick-head-right">{alpha_badge_html}{render_tag(tag)}</div>
      </div>
      <div class="pick-divider"></div>

      <div class="pick-block">
        <div class="pick-block-label">핵심 투자 포인트</div>
        <div class="pick-block-body">{core_thesis(row)}</div>
      </div>

      <div class="pick-block">
        <div class="pick-block-label">주요 리스크</div>
        <div class="pick-block-body">{key_risk(row)}</div>
      </div>

      <div class="pick-block">
        <div class="pick-block-label">확인 필요 사항</div>
        <div class="pick-block-body">{check_items(row)}</div>
      </div>

      <div class="pick-foot">
        <div class="pick-quote">
          <span class="pick-price">{price}</span>
          <span class="pick-change {dr_cls}">{dr_str}</span>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
    btn_cols = st.columns([2, 2, 6])
    with btn_cols[0]:
        if st.button("상세 보기", key=f"{key_prefix}_detail_{idx}_{row['ticker']}", use_container_width=True):
            navigate_to("detail", row["ticker"])
    with btn_cols[1]:
        if st.button("관심종목 편입", key=f"{key_prefix}_wl_{idx}_{row['ticker']}", use_container_width=True):
            res = add_to_watchlist(row["ticker"])
            if res.get("github"):
                st.toast(f"{name} 관심종목 편입 ✓ (영구 저장 완료)")
            elif res.get("github_status") == "no_pat":
                st.toast(f"{name} 관심종목 편입 (임시 저장 — GITHUB_PAT 미설정)")
            else:
                st.toast(f"{name} 관심종목 편입 (영구 저장 실패: {res.get('github_status')})")


# ---------------------------------------------------------------------------
# 화면: Portfolio review (Portfolio Regime)
# ---------------------------------------------------------------------------

# Overheat sub-score 6개 — (컬럼명, 한국어 라벨)
_REGIME_SUBSCORES: list[tuple[str, str]] = [
    ("valuation_stretch_score", "밸류에이션 과열"),
    ("sentiment_speculation_score", "심리 · 투기"),
    ("market_concentration_score", "시장 집중도"),
    ("liquidity_credit_score", "유동성 · 크레딧"),
    ("earnings_revision_risk_score", "이익 추정 리스크"),
    ("technical_extension_score", "기술적 과열"),
]


def _regime_row_get(row: Any, key: str) -> Any:
    """sqlite3.Row / dict 안전 접근 — 키 없거나 None 이면 None 반환."""
    if row is None:
        return None
    try:
        keys = row.keys()
    except Exception:
        keys = []
    if key in keys:
        return row[key]
    return None


def _fmt_regime_value(val: Any) -> str:
    """None 이면 대시, 숫자면 정수 표기, 그 외 문자열."""
    if val is None:
        return "—"
    if isinstance(val, (int, float)):
        return f"{val:.0f}" if float(val).is_integer() else f"{val:.1f}"
    return str(val)


_ACTION_PRIORITY = {
    "high":   ("#EF4444", "우선"),
    "medium": ("#F59E0B", "점검"),
    "low":    ("#64748B", "참고"),
}


# ---------------------------------------------------------------------------
# 평이한 한국어 표현 레이어 — 표시 전용 (계산은 절대 바꾸지 않음)
# ---------------------------------------------------------------------------

def _plain_overheat(score: Any) -> str:
    """Market Overheat Score(0~100, None 가능)를 평이한 한 마디로.

    호출자가 숫자를 붙이고 싶으면 따로 붙인다 — 여기선 표현만 반환.
    """
    if score is None:
        return "확인 필요"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "확인 필요"
    if s < 35:
        return "안정 — 시장이 차분한 편"
    if s < 50:
        return "중립"
    if s < 65:
        return "주의 — 다소 비싼 편"
    if s < 80:
        return "과열 — 평소보다 비쌈"
    return "극단 과열 — 매우 비쌈"


# market_regime.py 의 6개 current_regime 값 → 짧은 평이한 한국어
_PLAIN_REGIME: dict[str, str] = {
    "Risk-On": "위험선호 — 시장 분위기가 양호",
    "Expensive but Stable": "고평가지만 안정 — 비싸도 흔들림은 적음",
    "Overheated": "과열 — 단기 쏠림이 강함",
    "Correction Watch": "조정 경계 — 약세 조짐을 살필 때",
    "Dislocation": "디스로케이션 — 큰 낙폭, 분할 매수 구간",
    "Crisis": "위기 — 방어가 최우선",
}


def _plain_regime(regime_str: Any) -> str:
    """current_regime 문자열을 짧은 평이한 한국어 글로스로.

    알 수 없거나 None 이면 원문 또는 '확인 필요' 반환.
    """
    if not regime_str:
        return "확인 필요"
    return _PLAIN_REGIME.get(str(regime_str), str(regime_str))


_DECISION_DUP_KEYWORDS = ("낙폭", "과열", "Overheat", "익절")


def render_today_decision(regime: Any, crash: Any):
    """오늘의 판단 — '오늘 뭘 해야 하는지' 단일 통합 블록.

    기존의 세 surface(금일 핵심 판단 / 데일리 액션 플랜 / 백테스트 대응)를
    하나로 합친다. 헤드라인 → 시장 한 줄 → 오늘 할 일 → 전날 대비 변동 →
    백테스트 근거(expander) 순. 데이터가 없어도 조용히 안내만 — raise 금지.
    """
    # ── 국면 데이터 자체가 없으면 차분한 한 줄만 ──────────────────────
    if regime is None:
        st.markdown(
            '<div class="env-block" style="min-height:auto; '
            'border-left:3px solid var(--blue);">'
            '<div class="env-block-title">오늘의 판단</div>'
            '<div class="env-block-body" style="margin-top:6px;">'
            '시장 국면 데이터가 아직 없습니다 — 파이프라인 실행 후 '
            '표시됩니다.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── 데이터 수집 (전부 graceful) ──────────────────────────────────
    try:
        from src.portfolio_review import (
            load_portfolio, diagnose_portfolio, generate_daily_action_plan,
        )
    except Exception as e:
        log.debug(f"오늘의 판단 import 실패: {e}")
        return

    prev_regime = None
    prev_crash = None
    try:
        with db.db_session() as conn:
            regimes = db.fetch_recent_market_regimes(conn, 2)
            crashes = db.fetch_recent_crash_deployment_plans(conn, 2)
        if regimes and len(regimes) >= 2:
            prev_regime = regimes[1]
        if crashes and len(crashes) >= 2:
            prev_crash = crashes[1]
    except Exception as e:
        log.debug(f"오늘의 판단 전일 regime 조회 실패: {e}")

    diag: dict = {}
    holdings: list = []
    try:
        pf = load_portfolio(PROJECT_ROOT)
        if pf.get("available"):
            holdings = pf.get("holdings") or []
            diag = diagnose_portfolio(holdings)
    except Exception as e:
        log.debug(f"오늘의 판단 포트폴리오 로드 실패: {e}")

    plan: dict = {}
    try:
        plan = generate_daily_action_plan(
            diag, holdings, regime, crash, prev_regime, prev_crash) or {}
    except Exception as e:
        log.debug(f"오늘의 판단 액션플랜 생성 실패: {e}")
        plan = {}

    sol = None
    try:
        with db.db_session() as conn:
            sol = db.fetch_latest_backtest_solution(conn)
    except Exception as e:
        log.debug(f"오늘의 판단 백테스트 대응 조회 실패: {e}")
        sol = None

    sol_items: list = []
    sol_headline = ""
    sol_caveat = ""
    sol_data_mode = "rule_fallback"
    sol_cycle_position = ""
    if sol is not None:
        sol_headline = _regime_row_get(sol, "headline") or ""
        sol_caveat = _regime_row_get(sol, "caveat") or ""
        sol_data_mode = _regime_row_get(sol, "data_mode") or "rule_fallback"
        # cycle_position 컬럼은 구 DB 에 없을 수 있어 방어적으로 접근
        sol_cycle_position = (_regime_row_get(sol, "cycle_position") or "").strip()
        items_raw = _regime_row_get(sol, "items_json")
        if items_raw:
            try:
                import json as _json
                parsed = _json.loads(items_raw)
                if isinstance(parsed, list):
                    sol_items = [it for it in parsed if isinstance(it, dict)]
            except Exception as e:
                log.debug(f"오늘의 판단 items 파싱 실패: {e}")
                sol_items = []

    # ── 1) 헤드라인 ─────────────────────────────────────────────────
    headline = sol_headline.strip() if sol_headline else ""
    if not headline:
        headline = (plan.get("headline") or "—")

    # ── 2) 시장 한 줄 ───────────────────────────────────────────────
    overheat = _regime_row_get(regime, "market_overheat_score")
    market_line = f"지금 시장은 {_plain_overheat(overheat)}"
    dd_val = _regime_row_get(crash, "qqq_drawdown_from_high")
    dd_pct = None
    if dd_val is not None:
        try:
            dd_pct = float(dd_val)
            if abs(dd_pct) <= 1:
                dd_pct *= 100
        except (TypeError, ValueError):
            dd_pct = None
    if dd_pct is not None:
        market_line += f" · 나스닥은 고점 대비 {dd_pct:+.1f}%."
    else:
        market_line += "."
    cur_regime = _regime_row_get(regime, "current_regime")
    market_line += f" 국면: {_plain_regime(cur_regime)}."

    # ── 2-a-KR) KR 시장 한 줄 — KOSPI overheat + KODEX 200 사이클 ──────
    # 데이터가 모이지 않았으면 줄 자체를 조용히 생략 (KR 은 보조 시그널).
    kr_market_line = ""
    try:
        with db.db_session() as conn:
            kr_regime = db.fetch_latest_kospi_market_regime(conn)
            try:
                from src.market_cycle_analyzer import recommend_current_entry
                kr_rec = recommend_current_entry(conn, base_asset="069500")
            except Exception as e:
                log.debug(f"KR recommend_current_entry 실패: {e}")
                kr_rec = None
    except Exception as e:
        log.debug(f"KR 시장 한 줄 데이터 로드 실패: {e}")
        kr_regime, kr_rec = None, None

    if kr_regime is not None:
        kr_overheat = _regime_row_get(kr_regime, "overheat_score")
        kr_band = (_regime_row_get(kr_regime, "band") or "").strip()
        kr_regime_label = (_regime_row_get(kr_regime, "regime_ko") or "").strip()
        kr_dd = _regime_row_get(kr_regime, "kospi_drawdown_from_52w_high")
        kr_dd_pct = None
        if kr_dd is not None:
            try:
                kr_dd_pct = float(kr_dd)
                if abs(kr_dd_pct) <= 1:
                    kr_dd_pct *= 100
            except (TypeError, ValueError):
                kr_dd_pct = None
        # plain overheat band — 동일 어조로
        oh_label = _plain_overheat(kr_overheat) if kr_overheat is not None else None
        bits: list[str] = []
        if oh_label:
            bits.append(f"KOSPI {oh_label}")
        elif kr_band:
            bits.append(f"KOSPI {kr_band}")
        if kr_regime_label:
            bits.append(kr_regime_label)
        head = " · ".join(bits) if bits else "KOSPI 데이터 누적 중"
        tail_parts: list[str] = []
        if kr_dd_pct is not None:
            tail_parts.append(f"KODEX 200 {kr_dd_pct:+.1f}%")
        kr_verdict = None
        if kr_rec is not None:
            kr_verdict = (kr_rec.get("verdict") or "").strip()
        if kr_verdict and kr_verdict != "데이터 누적 중":
            tail_parts.append(kr_verdict)
        if tail_parts:
            kr_market_line = (
                '<div class="env-block-body" style="margin-top:4px; '
                'font-size:13px; color:var(--muted); line-height:1.6;">'
                f'{head}. ' + " — ".join(tail_parts) + '.'
                '</div>'
            )
        else:
            kr_market_line = (
                '<div class="env-block-body" style="margin-top:4px; '
                'font-size:13px; color:var(--muted); line-height:1.6;">'
                f'{head}.'
                '</div>'
            )

    # ── 2-b) 시장 사이클 위치 — 시장이 과거 어느 구간에 있나 ─────────
    # cycle_position 이 비었으면 줄 자체를 생략 (빈 placeholder·에러 없음).
    cycle_block = ""
    if sol_cycle_position:
        cycle_block = (
            '<div class="env-block-body" style="margin-top:6px; '
            'font-size:13px; color:var(--muted); line-height:1.6;">'
            '<span style="color:var(--text-mid); font-weight:700;">'
            '시장이 과거 어느 구간에 있나</span> · '
            f'{sol_cycle_position}</div>'
        )

    # ── 3) 3-Layer 내러티브: 포트폴리오 점검 → 시장 추적·발굴 → 리밸런싱 ──
    # 사용자 요청: 포트폴리오 우선, 시장 두 번째 (Core트래커+고확신알파+파킹 3-sub),
    # 리밸런싱은 신호 있을 때만 (소음 제거). Alpha gate: score≥80 + DD≤-10%.
    try:
        from src.today_decision import (
            build_portfolio_check, build_rebalance_actions,
            build_alpha_bet_signals,
            render_layer_0_html,
            render_layer_a_html, render_layer_b_html, render_layer_c_html,
        )
        from src.daily_tracking import (
            build_core_tracker_cards, build_alpha_candidates_strict,
            build_parking_cards,
        )
        # rows: module-level global (cached_build_rows 결과)
        rows_for_alpha = []
        try:
            rows_for_alpha = rows or []  # noqa: F824
        except NameError:
            rows_for_alpha = []

        # tracker_data fetch
        tracker_data = {}
        try:
            tracker_data = _bounded_call(
                cached_daily_trackers, token, timeout=45) or {}
        except Exception as e:
            log.warning("daily_trackers fetch 실패: %s", e)

        # market_overheat 추출 (parking sweet spot 판정용)
        overheat = None
        try:
            if regime is not None:
                overheat_raw = _regime_row_get(regime, "market_overheat_score")
                if overheat_raw is not None:
                    overheat = float(overheat_raw)
        except Exception:
            overheat = None

        # Layer 0 — Alpha Bet 신호 (사용자 본인 ledger 룰 — 최우선)
        alpha_bet_signals = build_alpha_bet_signals(holdings)

        # Layer A — 포트폴리오 점검 (alpha_bet 매칭 종목은 Layer 0 에서 처리)
        layer_a_items = build_portfolio_check(holdings, diag)

        # Layer B — 3 sub
        with db.db_session() as _conn_for_verdict:
            core_cards = build_core_tracker_cards(tracker_data, conn=_conn_for_verdict)
        alpha_candidates = build_alpha_candidates_strict(rows_for_alpha)
        parking_cards = build_parking_cards(tracker_data, overheat)

        # Layer C — STRICT alpha 만 funding pair 후보로 사용
        layer_c_items = build_rebalance_actions(
            holdings, diag, alpha_candidates, regime)

        # 동적 헤드라인 — 4-Layer (0/A/B/C) 종합. Layer 0 우선.
        from src.today_decision import synthesize_headline
        synthesized = synthesize_headline(
            layer_a_items, alpha_candidates, layer_c_items, core_cards,
            alpha_bet_signals=alpha_bet_signals)
        if synthesized and synthesized != "데이터 수집 중":
            headline = synthesized

        # 렌더 순서: Layer 0 (Alpha Bet 본인 룰) → A (포트폴리오) → B (시장) → C (액션)
        action_block = (
            render_layer_0_html(alpha_bet_signals)
            + render_layer_a_html(layer_a_items)
            + render_layer_b_html(core_cards, alpha_candidates, parking_cards)
            + render_layer_c_html(layer_c_items)
        )
    except Exception as e:
        log.warning("3-layer 내러티브 빌드 실패: %s — 기존 액션 목록으로 폴백", e)
        action_block = (
            '<div style="font-size:13px; color:var(--muted); '
            'margin:16px 0 0; line-height:1.6;">오늘의 판단 데이터 수집 중. '
            '잠시 후 새로고침 해주세요.</div>'
        )

    # ── 4) 전날 대비 변동 — 있을 때만 ───────────────────────────────
    delta_block = ""
    if plan.get("has_prev") and plan.get("deltas"):
        chip_html = ""
        for d in plan["deltas"]:
            changed = d.get("changed")
            dirn = d.get("dir")
            arrow = "▲" if dirn == "up" else "▼" if dirn == "down" else ""
            if d.get("delta"):
                dcol = ("#22C55E" if dirn == "down"
                        else "#EF4444" if dirn == "up" else "var(--muted)")
                val = (f'{d["today"]} <span style="color:{dcol}; '
                       f'font-size:12px;">{arrow}{d["delta"]}</span>')
            elif changed:
                val = f'{d["prev"]} → <b>{d["today"]}</b>'
            else:
                val = d["today"]
            border = ("#475569" if changed else "var(--line)")
            chip_html += (
                f'<div style="border:1px solid {border}; '
                'background:var(--panel-soft); border-radius:8px; '
                'padding:8px 12px;">'
                '<div style="font-size:11px; color:var(--muted); '
                f'letter-spacing:.03em;">{d["label"]}</div>'
                '<div style="font-size:14px; color:var(--text); '
                f'margin-top:3px;">{val}</div></div>'
            )
        delta_block = (
            '<div style="font-size:12px; color:var(--muted); '
            'margin:16px 0 9px; letter-spacing:.03em;">전날 대비 변동'
            + (f' · {plan.get("prev_date")} → {plan.get("today_date")}'
               if plan.get("prev_date") else "")
            + '</div>'
            '<div style="display:flex; flex-wrap:wrap; gap:8px;">'
            + chip_html + '</div>'
        )

    # ── 블록 렌더 (parts 1~4 는 하나의 env-block) ───────────────────
    st.markdown(
        '<div class="env-block" style="min-height:auto; '
        'border-left:3px solid var(--blue);">'
        '<div class="env-block-title">오늘의 판단</div>'
        '<div style="font-size:16px; font-weight:700; color:var(--text); '
        f'margin:4px 0 2px; line-height:1.5;">{headline}</div>'
        '<div class="env-block-body" style="margin-top:8px;">'
        f'{market_line}</div>'
        f'{kr_market_line}'
        f'{cycle_block}'
        f'{action_block}'
        f'{delta_block}'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── 4.5) Leveraged Opportunity Watch — 별도 env-block ─────────────
    # LESS 적용한 universe + 사용자 보유 2X profit protection 종합. 빈 신호여도
    # regime gate 한 줄은 항상 표시 (사용자가 2X 진입 가능 여부 즉시 확인).
    try:
        from src.leveraged_opportunity_watch import (
            build_leveraged_opportunity_watch, render_watch_html,
        )
        rows_for_watch = []
        try:
            rows_for_watch = rows or []  # noqa: F824
        except NameError:
            pass

        # QLD context — universe 에 QLD 가 없으면 daily_trackers core 에서 가져옴
        qld_ctx_for_watch: dict | None = None
        try:
            for c in (core_cards or []):
                if (c.get("symbol") or "").upper() == "QLD":
                    qld_ctx_for_watch = {"market_data": {
                        "available": True,
                        "current_price": c.get("price"),
                        "drawdown_from_52w_high": (c.get("dd_pct") or 0) / 100.0,
                        "daily_return": (c.get("daily_pct") or 0) / 100.0,
                    }}
                    break
        except Exception:
            pass

        watch = build_leveraged_opportunity_watch(
            rows_for_watch, holdings, qld_ctx_for_watch, regime)
        watch_html = render_watch_html(watch)
        st.markdown(
            '<div class="env-block" style="min-height:auto; '
            'border-left:3px solid #F59E0B; margin-top:14px;">'
            + watch_html +
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        log.warning("Leveraged Opportunity Watch 빌드 실패: %s", e)

    # ── 5) 백테스트 근거 expander (별도 Streamlit 호출) ─────────────
    with st.expander("이 판단의 백테스트 근거 보기"):
        basis_items = [it for it in sol_items
                       if (it.get("basis") or "").strip()]
        if sol is None or not sol_items:
            st.markdown(
                '<div style="font-size:13px; color:var(--muted); '
                'line-height:1.6;">백테스트 근거가 아직 없습니다 — '
                '파이프라인이 시장 일봉을 누적하면 표시됩니다.</div>',
                unsafe_allow_html=True,
            )
        else:
            rows_html = ""
            for it in basis_items:
                title = (it.get("title") or "").strip() or "—"
                basis = (it.get("basis") or "").strip()
                rows_html += (
                    '<div style="font-size:13px; color:var(--text-mid); '
                    'line-height:1.6; margin-bottom:6px;">'
                    f'<b>{title}</b>: {basis}</div>'
                )
            if not rows_html:
                rows_html = (
                    '<div style="font-size:13px; color:var(--muted); '
                    'line-height:1.6;">개별 항목 근거가 아직 없습니다.</div>'
                )
            extra = ""
            if sol_caveat:
                extra += (
                    '<div style="font-size:11px; color:var(--muted); '
                    'margin-top:10px; line-height:1.6;">'
                    f'{sol_caveat}</div>'
                )
            if sol_data_mode == "rule_fallback":
                extra += (
                    '<div style="font-size:11px; color:var(--muted); '
                    'margin-top:8px; line-height:1.6;">백테스트 데이터 '
                    '누적 중 — 일부 항목은 룰 기준 잠정 권고입니다.</div>'
                )
            st.markdown(rows_html + extra, unsafe_allow_html=True)


def render_portfolio_regime():
    render_back_button("regime")
    page_header(
        "Portfolio review",
        meta="Portfolio Regime · Market Overheat Score · Beta Allocation · Crash Deployment",
    )

    try:
        with db.db_session() as conn:
            regime = db.fetch_latest_market_regime(conn)
            crash = db.fetch_latest_crash_deployment_plan(conn)
    except Exception as e:
        regime, crash = None, None
        st.markdown(
            f'<div class="card">Portfolio Regime 데이터 조회 중 오류가 발생했습니다: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    if regime is None:
        st.markdown(
            '<div class="card">'
            '<div class="pick-name" style="font-size:18px;">아직 Portfolio Regime 데이터가 없습니다</div>'
            '<div class="pick-type">파이프라인(run_research) 실행이 필요합니다. '
            '데이터 업데이트가 완료되면 시장 국면 · Overheat Score · 권장 포트폴리오 모드가 표시됩니다.</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    regime_date = _regime_row_get(regime, "date")

    # '오늘의 판단' 블록은 Daily Brief 에만 노출 — 중복 제거 (사용자 요청).

    # ── 1) 상단 요약 ────────────────────────────────────────────────
    cur_regime = _regime_row_get(regime, "current_regime")
    overheat = _regime_row_get(regime, "market_overheat_score")
    portfolio_mode = _regime_row_get(regime, "portfolio_mode")
    beta_level = _regime_row_get(regime, "recommended_beta_level")

    st.markdown(
        '<div class="section-title first">시장 국면 요약'
        + (f' · 기준일 {regime_date}' if regime_date else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    summary_cells = [
        ("CURRENT REGIME", cur_regime if cur_regime else "확인 필요"),
        ("MARKET OVERHEAT SCORE", _fmt_regime_value(overheat) + " / 100"),
        ("PORTFOLIO MODE", portfolio_mode if portfolio_mode else "확인 필요"),
        ("RECOMMENDED BETA LEVEL", beta_level if beta_level else "확인 필요"),
    ]
    sum_cols = st.columns(len(summary_cells))
    for i, (label, value) in enumerate(summary_cells):
        with sum_cols[i]:
            st.markdown(
                '<div class="metric-card">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value" style="font-size:24px;">{value}</div>'
                "</div>",
                unsafe_allow_html=True,
            )

    # ── 2) Market Overheat Score 분해 — 6 sub-score ─────────────────
    # C1: 위 요약 박스와의 세로 여백 확보 (상단 26px / 하단 14px)
    st.markdown(
        '<div class="section-title" style="margin-top:26px; margin-bottom:14px;">'
        'Market Overheat Score 분해</div>',
        unsafe_allow_html=True,
    )
    sub_cols = st.columns(3)
    for i, (col_key, label) in enumerate(_REGIME_SUBSCORES):
        val = _regime_row_get(regime, col_key)
        with sub_cols[i % 3]:
            if val is None:
                body = (
                    f'<div class="metric-value" style="font-size:18px;">'
                    f'<span class="tag tag-data-unavailable">확인 필요</span></div>'
                )
            else:
                pct = max(0.0, min(100.0, float(val)))
                body = (
                    f'<div class="metric-value">{_fmt_regime_value(val)} / 100</div>'
                    '<div style="margin-top:10px; background:var(--panel-soft); '
                    'border:1px solid var(--line); border-radius:6px; height:10px; '
                    'overflow:hidden;">'
                    f'<div style="width:{pct:.0f}%; height:100%; background:var(--blue);">'
                    '</div></div>'
                )
            st.markdown(
                '<div class="metric-card" style="min-height:130px;">'
                f'<div class="metric-label">{label}</div>'
                f"{body}"
                "</div>",
                unsafe_allow_html=True,
            )

    # ── 3) 권장 포트폴리오 모드 + Beta Allocation ───────────────────
    commentary = _regime_row_get(regime, "commentary_ko")
    st.markdown('<div class="section-title">권장 포트폴리오 모드 · Beta Allocation</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="env-block" style="min-height:auto;">'
        f'<div class="env-block-title">PORTFOLIO MODE</div>'
        f'<div class="env-block-body" style="font-size:16px; font-weight:700; '
        f'color:var(--text); margin-bottom:8px;">'
        f'{portfolio_mode if portfolio_mode else "확인 필요"}'
        f' · 권장 베타 {beta_level if beta_level else "확인 필요"}</div>'
        f'<div class="env-block-body">'
        f'{commentary if commentary else "코멘터리가 아직 생성되지 않았습니다."}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 3b) Phase 3 — 사이클 심리 체크리스트 + Buffett 기회 필터 ──────
    render_cycle_and_buffett(regime)

    # ── 4) Nasdaq Drawdown Deployment Plan ──────────────────────────
    st.markdown('<div class="section-title">Nasdaq Drawdown Deployment Plan</div>',
                unsafe_allow_html=True)
    if crash is None:
        st.markdown(
            '<div class="card">Crash Deployment Plan 데이터가 아직 없습니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        dd = _regime_row_get(crash, "qqq_drawdown_from_high")
        zone = _regime_row_get(crash, "deployment_zone")
        instrument = _regime_row_get(crash, "recommended_instrument")
        action = _regime_row_get(crash, "suggested_action")
        credit = _regime_row_get(crash, "credit_stress_status")
        crash_comment = _regime_row_get(crash, "commentary_ko")

        dd_str = "확인 필요"
        if dd is not None:
            try:
                dd_str = f"{float(dd):+.1f}%"
            except (TypeError, ValueError):
                dd_str = str(dd)

        kv_rows = [
            ("QQQ 고점 대비 낙폭", dd_str),
            ("Deployment Zone", zone if zone else "확인 필요"),
            ("권장 수단", instrument if instrument else "확인 필요"),
            ("권장 액션", action if action else "확인 필요"),
            ("크레딧 스트레스", credit if credit else "확인 필요"),
        ]
        kv_html = "".join(
            f'<div class="kv"><span class="kv-k">{k}</span>'
            f'<span class="kv-v">{v}</span></div>'
            for k, v in kv_rows
        )
        st.markdown(
            '<div class="card">'
            f"{kv_html}"
            + (
                f'<div class="env-block-body" style="margin-top:14px;">'
                f"{crash_comment}</div>"
                if crash_comment else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── 5) 내 포트폴리오 리뷰 ────────────────────────────────────────
    render_my_portfolio_review(regime)


def _regime_row_to_dict(regime: Any, crash: Any = None) -> dict:
    """market_regime / crash_deployment_plan Row → Phase 3 평가용 dict.

    marks_cycle / buffett_filter 가 읽는 키 (sub-score, overheat, regime,
    qqq_drawdown_from_high) 로 정규화한다.
    """
    keys = (
        "market_overheat_score", "current_regime",
        "valuation_stretch_score", "sentiment_speculation_score",
        "market_concentration_score", "liquidity_credit_score",
        "earnings_revision_risk_score", "technical_extension_score",
    )
    out: dict = {k: _regime_row_get(regime, k) for k in keys}
    out["qqq_drawdown_from_high"] = _regime_row_get(crash, "qqq_drawdown_from_high")
    return out


def render_cycle_and_buffett(regime: Any):
    """Phase 3 — Howard Marks 사이클 심리 체크리스트 + Buffett 기회 필터.

    DB market_regime 행의 sub-score 로부터 rule-based 재평가해 표시.
    데이터 부족 시 graceful '확인 필요'.
    """
    try:
        with db.db_session() as conn:
            crash = db.fetch_latest_crash_deployment_plan(conn)
    except Exception:
        crash = None

    rdict = _regime_row_to_dict(regime, crash)

    cycle: dict = {}
    buffett: dict = {}
    try:
        from src.marks_cycle import evaluate_cycle_psychology
        cycle = evaluate_cycle_psychology(rdict)
    except Exception as e:
        log.debug(f"사이클 심리 평가 실패: {e}")
    try:
        from src.buffett_filter import evaluate_buffett_opportunity
        buffett = evaluate_buffett_opportunity(rdict)
    except Exception as e:
        log.debug(f"Buffett 기회 필터 평가 실패: {e}")

    # ── 사이클 심리 체크리스트 ──────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="margin-top:26px;">'
        '사이클 심리 체크리스트 · Howard Marks 식</div>',
        unsafe_allow_html=True,
    )
    if not cycle:
        st.markdown('<div class="card">사이클 심리 데이터를 평가할 수 없습니다.</div>',
                    unsafe_allow_html=True)
    else:
        c_cells = [
            ("CYCLE PSYCHOLOGY SCORE",
             (_fmt_regime_value(cycle.get("cycle_psychology_score")) + " / 100")
             if cycle.get("cycle_psychology_score") is not None else "확인 필요"),
            ("MARKET MOOD", cycle.get("market_mood") or "확인 필요"),
            ("RISK POSTURE", cycle.get("risk_posture") or "확인 필요"),
        ]
        ccols = st.columns(len(c_cells))
        for i, (label, value) in enumerate(c_cells):
            with ccols[i]:
                st.markdown(
                    '<div class="metric-card" style="min-height:96px;">'
                    f'<div class="metric-label">{label}</div>'
                    f'<div class="metric-value" style="font-size:17px;">{value}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
        # 10개 질문 체크리스트
        rows_html = ""
        for c in (cycle.get("checklist") or []):
            sc = c.get("score")
            if sc is None:
                v_html = '<span class="tag tag-data-unavailable">확인 필요</span>'
            else:
                v_html = (f'<b>{sc:.0f}</b> '
                          f'<span style="color:var(--muted);">· {c.get("verdict")}</span>')
            rows_html += (
                '<div class="kv"><span class="kv-k">'
                f'{c.get("ko") or c.get("label")}</span>'
                f'<span class="kv-v">{v_html}</span></div>'
            )
        st.markdown(
            '<div class="card" style="margin-top:14px;">'
            f"{rows_html}"
            f'<div class="env-block-body" style="margin-top:14px;">'
            f'{cycle.get("commentary_ko") or ""}</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    # ── Buffett 기회 필터 ───────────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="margin-top:26px;">'
        'Buffett 기회 필터</div>',
        unsafe_allow_html=True,
    )
    if not buffett:
        st.markdown('<div class="card">Buffett 기회 데이터를 평가할 수 없습니다.</div>',
                    unsafe_allow_html=True)
    else:
        do_nothing = buffett.get("do_nothing_recommended")
        b_cells = [
            ("BUFFETT OPPORTUNITY SCORE",
             (_fmt_regime_value(buffett.get("buffett_opportunity_score")) + " / 100")
             if buffett.get("buffett_opportunity_score") is not None else "확인 필요"),
            ("OPPORTUNITY", buffett.get("opportunity_band") or "확인 필요"),
            ("DO NOTHING 권고", "예 — 현금 보존 우위" if do_nothing else "아니오"),
        ]
        bcols = st.columns(len(b_cells))
        for i, (label, value) in enumerate(b_cells):
            with bcols[i]:
                st.markdown(
                    '<div class="metric-card" style="min-height:96px;">'
                    f'<div class="metric-label">{label}</div>'
                    f'<div class="metric-value" style="font-size:17px;">{value}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
        rows_html = ""
        for c in (buffett.get("checklist") or []):
            sc = c.get("score")
            if sc is None:
                v_html = (f'<span class="tag tag-data-unavailable">'
                          f'{c.get("verdict") or "확인 필요"}</span>')
            else:
                v_html = (f'<b>{sc:.0f}</b> '
                          f'<span style="color:var(--muted);">· {c.get("verdict")}</span>')
            rows_html += (
                '<div class="kv"><span class="kv-k">'
                f'{c.get("ko") or c.get("label")}</span>'
                f'<span class="kv-v">{v_html}</span></div>'
            )
        cash_comment = buffett.get("cash_optionality_comment") or ""
        st.markdown(
            '<div class="card" style="margin-top:14px;">'
            f"{rows_html}"
            f'<div class="env-block-body" style="margin-top:14px;">'
            f'<b>Cash Optionality:</b> {cash_comment}</div>'
            f'<div class="env-block-body" style="margin-top:8px;">'
            f'{buffett.get("commentary_ko") or ""}</div>'
            "</div>",
            unsafe_allow_html=True,
        )


def _fmt_krw(v: Any) -> str:
    """KRW 금액 — 억/만 단위 한국어 표기."""
    n = None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "확인 필요"
    if n is None:
        return "확인 필요"
    eok = n / 1e8
    if abs(eok) >= 1:
        return f"{eok:,.2f}억 원"
    man = n / 1e4
    return f"{man:,.0f}만 원"


def render_capital_efficiency_section(ticker: str):
    """종목상세 — Capital Efficiency / Profit Protection / Parking / QLD Relative
    보조 점수 표시 (Phase 2). DB 데이터 없으면 graceful 안내.
    """
    try:
        with db.db_session() as conn:
            ce = db.fetch_capital_efficiency_score(conn, ticker)
            pp = db.fetch_profit_protection(conn, ticker)
            parking_rows = db.fetch_parking_candidates(conn)
    except Exception as e:
        log.debug(f"capital efficiency 섹션 조회 실패: {e}")
        return

    parking = None
    for pr in (parking_rows or []):
        try:
            if pr["ticker"] == ticker:
                parking = pr
                break
        except Exception:
            pass

    if ce is None and pp is None and parking is None:
        return  # Phase 2 데이터 없으면 섹션 자체를 생략 (조용히)

    def _g(r, k):
        if r is None:
            return None
        try:
            return r[k] if k in r.keys() else None
        except Exception:
            return None

    st.markdown('<div class="section-title">Capital Efficiency · 보조 점수</div>',
                unsafe_allow_html=True)

    ce_score = _g(ce, "capital_efficiency_score")
    pp_score = _g(pp, "profit_protection_score")
    pk_score = _g(parking, "parking_score")
    qld_view = _g(ce, "qld_relative_view")

    cells = [
        ("CAPITAL EFFICIENCY",
         f"{float(ce_score):.0f} / 100" if ce_score is not None else "확인 필요"),
        ("PROFIT PROTECTION",
         f"{float(pp_score):.0f} / 100" if pp_score is not None else "확인 필요"),
        ("PARKING SUITABILITY",
         f"{float(pk_score):.0f} / 100" if pk_score is not None else "확인 필요"),
        ("QLD RELATIVE VIEW", qld_view if qld_view else "확인 필요"),
    ]
    ccols = st.columns(len(cells))
    for i, (label, value) in enumerate(cells):
        with ccols[i]:
            st.markdown(
                '<div class="metric-card" style="min-height:96px;">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value" style="font-size:18px;">{value}</div>'
                "</div>",
                unsafe_allow_html=True,
            )

    notes: list[str] = []
    ce_comment = _g(ce, "commentary_ko")
    pp_comment = _g(pp, "commentary_ko")
    pk_why = _g(parking, "why_parking_ko")
    if ce_comment:
        notes.append(ce_comment)
    if pp_comment:
        notes.append(pp_comment)
    if pk_why:
        notes.append(pk_why + " " + (_g(parking, "risk_ko") or ""))
    if notes:
        body = "<br><br>".join(notes)
        st.markdown(
            '<div class="card" style="font-size:13px; color:var(--muted); '
            f'line-height:1.7;">{body}</div>',
            unsafe_allow_html=True,
        )


def _render_holdings_briefing_section(holdings: list[dict]):
    """보유 종목 브리핑 섹션 — 엔진이 생성한 의미 비중 종목별 일일 리서치 브리핑.

    holdings_briefing 테이블(최신일)을 읽어 종목별 카드로 렌더한다.
    - 브리핑 행이 없는 종목: '브리핑 대기' 안내 카드.
    - 테이블 자체가 비었거나 DB 읽기 실패: 안내 카드 1개 후 조용히 종료.
    어떤 경우에도 예외를 위로 던지지 않는다.
    """
    import json as _json

    st.markdown(
        '<div class="section-title" style="margin-top:26px;">보유 종목 브리핑</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-size:13px; color:var(--muted); margin:6px 0 12px;">'
        '엔진이 매일 생성하는 보유 종목 리서치 브리핑 — 순자산 비중 1% 이상 종목 대상. '
        '단발성 뉴스가 아닌 구조적·테마 관점의 분석입니다.'
        "</div>",
        unsafe_allow_html=True,
    )

    try:
        from src.holdings_briefing import select_meaningful_holdings
        meaningful = select_meaningful_holdings(holdings, 1.0)
    except Exception:
        meaningful = sorted(
            [h for h in (holdings or [])
             if (h.get("net_worth_pct") or 0) >= 1.0],
            key=lambda h: h.get("net_worth_pct") or 0, reverse=True,
        )

    # 브리핑 행 조회 (최신일)
    briefings_by_ticker: dict[str, Any] = {}
    table_available = True
    try:
        with db.db_session() as conn:
            rows = db.fetch_holdings_briefings(conn)
        for r in (rows or []):
            try:
                briefings_by_ticker[r["ticker"]] = r
            except Exception:
                pass
    except Exception as e:
        log.debug(f"holdings_briefing 조회 실패: {e}")
        table_available = False

    if not table_available:
        st.markdown(
            '<div class="card">'
            '<div class="pick-type">보유 종목 브리핑이 아직 없습니다. '
            '파이프라인(run_research) 실행 후 표시됩니다.</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    if not meaningful:
        st.markdown(
            '<div class="card"><div class="pick-type">'
            '의미 비중(1% 이상) 보유 종목이 없습니다.</div></div>',
            unsafe_allow_html=True,
        )
        return

    if not briefings_by_ticker:
        st.markdown(
            '<div class="card">'
            '<div class="pick-type">보유 종목 브리핑이 아직 없습니다. '
            '파이프라인(run_research) 실행 후 표시됩니다.</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        # 테이블은 있으나 데이터가 없는 경우 — 대기 안내만 하고 종료
        return

    def _g(row, k):
        if row is None:
            return None
        try:
            return row[k] if k in row.keys() else None
        except Exception:
            return None

    for h in meaningful:
        ticker = (h.get("ticker") or "").strip()
        name = h.get("name") or ticker
        nw = h.get("net_worth_pct")
        ret = h.get("return_pct")
        is_lev = bool(h.get("leverage"))

        nw_str = f"{nw:.1f}%" if nw is not None else "—"
        ret_str = f"{ret:+.1f}%" if ret is not None else "확인 필요"
        ret_color = ("#22C55E" if (ret or 0) > 0
                     else "#EF4444" if (ret or 0) < 0 else "var(--muted)")
        lev_badge = (
            '<span class="tag" style="background:#7F1D1D; color:#FCA5A5; '
            'border:1px solid #991B1B;">레버리지</span>' if is_lev else ""
        )

        header = (
            '<div style="display:flex; align-items:center; '
            'justify-content:space-between; gap:10px; flex-wrap:wrap;">'
            f'<div><b style="font-size:15px;">{name}</b> '
            f'<span style="color:var(--muted); font-size:12px;">{ticker}</span> '
            f'{lev_badge}</div>'
            f'<div style="font-size:13px;">비중 {nw_str} · '
            f'보유 수익률 <b style="color:{ret_color};">{ret_str}</b></div>'
            "</div>"
        )

        row = briefings_by_ticker.get(ticker)
        if row is None:
            st.markdown(
                '<div class="card" style="margin-bottom:10px;">'
                + header
                + '<div style="margin-top:10px;">'
                '<span class="tag tag-data-unavailable">'
                '브리핑 대기 — 다음 파이프라인 실행 시 생성됩니다.</span>'
                "</div></div>",
                unsafe_allow_html=True,
            )
            continue

        theme = _g(row, "exposure_theme") or "—"
        summary = _g(row, "summary_ko") or ""
        risks = _g(row, "risks_ko") or ""
        pf_note = _g(row, "portfolio_note_ko") or ""
        model_used = _g(row, "model_used") or ""
        # Stage 3 — KR 기초자산 라이브 시세 한 줄 (있을 때만)
        underlying_snap = (_g(row, "underlying_snapshot_ko") or "").strip()

        # key_drivers — JSON 문자열을 list 로 방어적 파싱
        raw_kd = _g(row, "key_drivers_ko")
        drivers: list[str] = []
        if isinstance(raw_kd, list):
            drivers = raw_kd
        elif raw_kd:
            try:
                parsed = _json.loads(raw_kd)
                drivers = parsed if isinstance(parsed, list) else []
            except Exception:
                drivers = []

        _lbl = ('font-size:11px; color:var(--muted); '
                'text-transform:uppercase; letter-spacing:0.04em; '
                'margin:12px 0 4px;')
        _txt = 'font-size:13px; color:var(--text-mid); line-height:1.7;'

        drivers_html = ""
        if drivers:
            items = "".join(
                f'<li style="margin-bottom:3px;">{d}</li>' for d in drivers
            )
            drivers_html = (
                f'<div style="{_lbl}">주목할 구조적 변수</div>'
                f'<ul style="{_txt} margin:0; padding-left:18px;">{items}</ul>'
            )

        model_tag = (
            '<span style="font-size:11px; color:var(--muted);">'
            f'{"엔진 룰 기반" if model_used == "rule-based" else f"LLM: {model_used}"}'
            "</span>"
        )

        # 기초자산 스냅샷 — 한 줄, 구조적 브리핑 아래 차분히. 없으면 생략.
        underlying_html = (
            f'<div style="{_lbl}">기초자산 라이브 시세</div>'
            f'<div style="{_txt}">{underlying_snap}</div>'
            if underlying_snap else ""
        )

        st.markdown(
            '<div class="card" style="margin-bottom:10px;">'
            + header
            + f'<div style="{_lbl}">익스포저 테마</div>'
            + f'<div style="{_txt}"><b>{theme}</b></div>'
            + (f'<div style="{_lbl}">핵심 요약</div>'
               f'<div style="{_txt}">{summary}</div>' if summary else "")
            + drivers_html
            + (f'<div style="{_lbl}">리스크</div>'
               f'<div style="{_txt}">{risks}</div>' if risks else "")
            + (f'<div style="{_lbl}">포트폴리오 관점</div>'
               f'<div style="{_txt}">{pf_note}</div>' if pf_note else "")
            + underlying_html
            + f'<div style="margin-top:10px; text-align:right;">{model_tag}</div>'
            + "</div>",
            unsafe_allow_html=True,
        )


def render_my_portfolio_review(regime: Any | None):
    """data/portfolio.json 의 실제 보유 종목을 읽어 포트폴리오 리뷰를 렌더.

    - 포트폴리오 레벨 진단 (총평가액·집중도·레버리지·수익분포)
    - 포지션별 리뷰 (return_pct / leverage / Profit Protection·Capital Efficiency·QLD Relative)
    - Market Regime 연계 rule-based 한국어 코멘트
    portfolio.json 없거나 깨지면 graceful 안내.
    """
    st.markdown('<div class="section-title">내 포트폴리오 리뷰</div>',
                unsafe_allow_html=True)

    try:
        from src.portfolio_review import (
            load_portfolio, diagnose_portfolio, generate_portfolio_commentary,
        )
        pf = load_portfolio(PROJECT_ROOT)
    except Exception as e:
        st.markdown(
            f'<div class="card">포트폴리오 데이터 로드 중 오류가 발생했습니다: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    if not pf.get("available"):
        st.markdown(
            '<div class="card">'
            '<div class="pick-name" style="font-size:16px;">보유 종목 데이터가 없습니다</div>'
            f'<div class="pick-type">{pf.get("error") or "data/portfolio.json 을 확인하십시오."}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    holdings = pf.get("holdings") or []
    diag = diagnose_portfolio(holdings)

    # ── 5-0) C4: rule-based 포트폴리오 코멘트 — 지표 박스 *위*로 이동 ──
    # 사용자가 요약을 먼저 읽고 숫자를 보도록. position_reviews 는 holdings
    # 에서 직접 구성 (DB 점수 조회 전에도 코멘트 생성 가능).
    early_reviews: list[dict] = [
        {
            "ticker": (h.get("ticker") or "").upper(),
            "name": h.get("name") or (h.get("ticker") or "").upper(),
            "return_pct": h.get("return_pct"),
            "leverage": bool(h.get("leverage")),
            # 중요성(materiality) 필터를 위해 절대 평가액도 전달.
            "value_krw": h.get("value_krw"),
        }
        for h in holdings
    ]
    try:
        overview_commentary = generate_portfolio_commentary(
            diag, regime, early_reviews)
    except Exception as e:
        overview_commentary = f"코멘트 생성 중 오류: {e}"
    st.markdown(
        '<div class="env-block" style="min-height:auto;">'
        '<div class="env-block-title">PORTFOLIO REVIEW · 시장 국면 연계</div>'
        f'<div class="env-block-body">{overview_commentary}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 5-1) 포트폴리오 레벨 진단 ────────────────────────────────────
    top = diag.get("top_holding") or {}
    top_name = top.get("name") or top.get("ticker") or "—"
    top_pct = diag.get("top_holding_pct")
    lev_pct = diag.get("leverage_exposure_pct")
    tot_ret = diag.get("total_return_pct")

    diag_cells = [
        ("총 평가액", _fmt_krw(diag.get("total_value_krw")), None),
        ("보유 종목 수", f'{diag.get("n_holdings", 0)} 종목', None),
        ("최대 비중 (집중도)",
         f"{top_pct:.1f}%" if top_pct is not None else "확인 필요",
         top_name),
        ("레버리지 노출",
         f"{lev_pct:.1f}%" if lev_pct is not None else "확인 필요",
         None),
    ]
    # C3: 위 코멘트 박스(5-0)와의 세로 여백 확보
    st.markdown('<div style="height:20px;"></div>', unsafe_allow_html=True)
    dcols = st.columns(len(diag_cells))
    for i, (label, value, sub) in enumerate(diag_cells):
        with dcols[i]:
            # C2: Streamlit columns 는 형제 높이를 자동 정렬하지 않으므로
            # height 를 명시해 4개 박스를 동일 높이로 통일한다.
            sub_html = f'<div class="metric-sub">{sub}</div>' if sub else ""
            st.markdown(
                '<div class="metric-card" style="height:150px; '
                'min-height:0; box-sizing:border-box;">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div>'
                f"{sub_html}"
                "</div>",
                unsafe_allow_html=True,
            )

    # 수익/손실 분포 + 총 수익률
    pnl_line = (
        f'평가손익 {_fmt_krw(diag.get("total_pnl_krw"))}'
        + (f' (총 수익률 {tot_ret:+.1f}%)' if tot_ret is not None else "")
        + f' · 수익 종목 {diag.get("n_winners", 0)} / 손실 종목 {diag.get("n_losers", 0)}'
    )
    # C3: 위 지표 박스와의 여백 확보 (margin-top:18px)
    st.markdown(
        f'<div class="card" style="margin-top:18px; padding:12px 16px; '
        f'font-size:14px; color:var(--muted);">{pnl_line}</div>',
        unsafe_allow_html=True,
    )

    # ── 5-1.5) 보유 종목 브리핑 — 엔진 생성 일일 리서치 브리핑 ────────
    _render_holdings_briefing_section(holdings)

    # ── 5-2) 포지션별 리뷰 — Phase 2 점수 DB 조회 ────────────────────
    position_reviews: list[dict] = []
    pp_map: dict[str, Any] = {}
    ce_map: dict[str, Any] = {}
    try:
        with db.db_session() as conn:
            for h in holdings:
                disp = (h.get("ticker") or "").upper()
                yft = h.get("yf_ticker")
                if not yft:
                    continue
                # portfolio.json 의 disp ticker 로 저장됨 (run_research step 참고)
                lookup = disp
                try:
                    pp = db.fetch_profit_protection(conn, lookup)
                    if pp is not None:
                        pp_map[disp] = pp
                    ce = db.fetch_capital_efficiency_score(conn, lookup)
                    if ce is not None:
                        ce_map[disp] = ce
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"포트폴리오 Phase2 점수 조회 실패: {e}")

    def _row_get(r, k):
        if r is None:
            return None
        try:
            return r[k] if k in r.keys() else None
        except Exception:
            return None

    # 비중 큰 순 정렬
    sorted_h = sorted(
        holdings,
        key=lambda h: (h.get("net_worth_pct") if h.get("net_worth_pct") is not None
                       else (h.get("value_krw") or 0)),
        reverse=True,
    )

    st.markdown(
        '<div style="font-size:13px; color:var(--muted); margin:6px 0 12px;">'
        '포지션별 리뷰 — 비중 큰 순. yf_ticker 없는 한국 ETF 는 yfinance 분석 불가로 '
        "'확인 필요' 로 표시됩니다."
        "</div>",
        unsafe_allow_html=True,
    )

    for h in sorted_h:
        disp = (h.get("ticker") or "").upper()
        name = h.get("name") or disp
        ret = h.get("return_pct")
        nw = h.get("net_worth_pct")
        is_lev = bool(h.get("leverage"))
        yft = h.get("yf_ticker")

        position_reviews.append({
            "ticker": disp, "name": name, "return_pct": ret, "leverage": is_lev,
        })

        ret_str = f"{ret:+.1f}%" if ret is not None else "확인 필요"
        ret_color = ("#22C55E" if (ret or 0) > 0
                     else "#EF4444" if (ret or 0) < 0 else "var(--muted)")
        nw_str = f"{nw:.1f}%" if nw is not None else "—"
        lev_badge = (
            '<span class="tag" style="background:#7F1D1D; color:#FCA5A5; '
            'border:1px solid #991B1B;">레버리지</span>' if is_lev else ""
        )

        # Phase 2 점수
        pp = pp_map.get(disp)
        ce = ce_map.get(disp)
        if not yft:
            score_html = (
                '<span class="tag tag-data-unavailable">한국 ETF — 확인 필요</span>'
            )
        else:
            pp_score = _row_get(pp, "profit_protection_score")
            ce_score = _row_get(ce, "capital_efficiency_score")
            qld_view = _row_get(ce, "qld_relative_view")
            chips: list[str] = []
            if pp_score is not None:
                chips.append(
                    f'<span class="tag" style="background:var(--panel-soft); '
                    f'border:1px solid var(--line);">Profit Protection '
                    f'{float(pp_score):.0f}/100</span>'
                )
            if ce_score is not None:
                chips.append(
                    f'<span class="tag" style="background:var(--panel-soft); '
                    f'border:1px solid var(--line);">Capital Efficiency '
                    f'{float(ce_score):.0f}/100</span>'
                )
            if qld_view:
                chips.append(
                    f'<span class="tag" style="background:var(--panel-soft); '
                    f'border:1px solid var(--line);">QLD: {qld_view}</span>'
                )
            if chips:
                score_html = " ".join(chips)
            else:
                score_html = (
                    '<span class="tag tag-data-unavailable">'
                    'Phase 2 점수 미산정 — 확인 필요</span>'
                )

        # profit protection suggested action (있으면)
        pp_action = _row_get(pp, "suggested_action")
        action_html = (
            f'<div style="font-size:13px; color:var(--muted); margin-top:8px;">'
            f'{pp_action}</div>'
            if pp_action else ""
        )

        st.markdown(
            '<div class="card" style="margin-bottom:10px;">'
            '<div style="display:flex; align-items:center; justify-content:space-between; '
            'gap:10px; flex-wrap:wrap;">'
            f'<div><b style="font-size:15px;">{name}</b> '
            f'<span style="color:var(--muted); font-size:12px;">{disp}</span> '
            f'{lev_badge}</div>'
            f'<div style="font-size:13px;">비중 {nw_str} · '
            f'보유 수익률 <b style="color:{ret_color};">{ret_str}</b></div>'
            "</div>"
            f'<div style="margin-top:8px;">{score_html}</div>'
            f'{action_html}'
            "</div>",
            unsafe_allow_html=True,
        )

    # ── 5-3) Market Regime 연계 종합 코멘트 ──────────────────────────
    # C4: 코멘트는 지표 박스 위(5-0)로 이동 — 여기서는 중복 출력하지 않는다.
    _ = position_reviews  # 포지션별 리뷰 수집 결과 (현재 추가 표시 없음)


def render_brief_regime_section():
    """Daily Brief 내 '오늘의 액션 플랜 + Portfolio review' 섹션.

    Phase 4-C — 브리프를 '아침·저녁 통합 허브' 로: 사용자는 브리프만 봐도
    오늘의 액션 플랜(전날 대비 변동 + 신경 쓸 것)과 시장 국면 요약을
    한 화면에서 파악할 수 있어야 한다.
    데이터가 없으면 조용히 생략한다 (브리프 흐름을 깨지 않음).
    """
    try:
        with db.db_session() as conn:
            regime = db.fetch_latest_market_regime(conn)
            crash = db.fetch_latest_crash_deployment_plan(conn)
    except Exception:
        return

    if regime is None:
        return

    # '오늘의 판단' 통합 블록을 브리프 최상단 영역에 노출.
    # Portfolio review 페이지와 동일한 블록을 재사용해 두 화면을 일관되게 유지.
    render_today_decision(regime, crash)

    cur_regime = _regime_row_get(regime, "current_regime")
    overheat = _regime_row_get(regime, "market_overheat_score")
    portfolio_mode = _regime_row_get(regime, "portfolio_mode")
    beta_level = _regime_row_get(regime, "recommended_beta_level")
    commentary = _regime_row_get(regime, "commentary_ko")

    st.markdown('<div class="section-title">오늘의 Portfolio review</div>',
                unsafe_allow_html=True)

    head = (
        f'시장 국면 <b>{cur_regime if cur_regime else "확인 필요"}</b> · '
        f'Overheat Score <b>{_fmt_regime_value(overheat)} / 100</b> · '
        f'권장 모드 <b>{portfolio_mode if portfolio_mode else "확인 필요"}</b> · '
        f'권장 베타 <b>{beta_level if beta_level else "확인 필요"}</b>'
    )

    crash_line = ""
    if crash is not None:
        zone = _regime_row_get(crash, "deployment_zone")
        dd = _regime_row_get(crash, "qqq_drawdown_from_high")
        dd_str = ""
        if dd is not None:
            try:
                dd_str = f"QQQ 고점 대비 {float(dd):+.1f}% · "
            except (TypeError, ValueError):
                dd_str = ""
        if zone:
            crash_line = (
                '<div class="env-block-body" style="margin-top:8px;">'
                f'Deployment: {dd_str}{zone}</div>'
            )

    st.markdown(
        '<div class="env-block" style="min-height:auto;">'
        f'<div class="env-block-body" style="font-size:15px; color:var(--text);">{head}</div>'
        + (
            f'<div class="env-block-body" style="margin-top:8px;">{commentary}</div>'
            if commentary else ""
        )
        + crash_line
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 화면: 오늘의 투자 브리프
# ---------------------------------------------------------------------------

def render_today_brief():
    if not rows:
        page_header("오늘의 투자 브리프")
        st.warning("투자 유니버스 데이터가 비어있습니다. data/universe.csv 를 확인하거나 데이터를 업데이트하세요.")
        return

    page_header(
        "오늘의 투자 브리프",
        meta=f"미국 상장주식 투자 유니버스 {len(rows)}종목",
    )
    render_data_health_warning()

    brief = build_daily_brief(rows, proxies, market_summary)

    # ════ Zone 1 — 오늘의 판단 ═══════════════════════════════════════
    # '오늘의 판단' 통합 블록(헤드라인이 기존 금일 핵심 판단을 대체) +
    # 가벼운 시장 국면 요약. 브리프 최상단의 단일 결론 영역.
    render_brief_regime_section()

    # ('내 보유 종목 오늘 포커스' 블록은 portfolio.json 이 수동 스냅샷이라
    # 매일 갱신되는 정보가 아니어서 브리프에서 제거 — Portfolio review 의
    # 보유 종목 브리핑에는 그대로 남아있음.)

    # ════ Zone 2 — 오늘의 시장 이슈 ══════════════════════════════════
    st.markdown(
        '<div class="section-title">오늘의 시장 이슈</div>',
        unsafe_allow_html=True,
    )

    # 금일 시장 환경 — 3블록 카드 (첫 블록은 자산 테이블)
    st.markdown('<div class="section-title">금일 시장 환경</div>', unsafe_allow_html=True)
    blocks = brief.get("market_blocks") or []
    if blocks:
        env_cols = st.columns(len(blocks))
        for i, blk in enumerate(blocks):
            with env_cols[i]:
                if i == 0:
                    table_html = _market_table_html(proxies)
                    st.markdown(
                        '<div class="env-block">'
                        f'<div class="env-block-title">{blk["title"]}</div>'
                        f"{table_html}"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="env-block">'
                        f'<div class="env-block-title">{blk["title"]}</div>'
                        f'<div class="env-block-body">{blk["body"]}</div>'
                        "</div>",
                        unsafe_allow_html=True,
                    )

    # BTC 추적은 '오늘의 판단 → 시장에서 추적·발굴 → Core Trackers' 안에 통합됨
    # (이전 별도 카드는 중복이라 제거)

    # 전날 글로벌 브리핑 — 어젯밤 글로벌 이벤트 리캡 (4 카테고리)
    briefing = brief.get("overnight_briefing") or []
    st.markdown(
        '<div class="section-title">전날 글로벌 브리핑</div>',
        unsafe_allow_html=True,
    )
    _has_events = any((c.get("events") for c in briefing))
    if not _has_events:
        st.markdown(
            '<div class="ob-empty">전날 글로벌 브리핑이 아직 생성되지 않았습니다. '
            '데이터 업데이트(자동 리서치)가 실행되면 어젯밤 전 세계에서 일어난 '
            '지정학·기업 실적·정책·시장 이벤트가 카테고리별로 정리됩니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        for cat in briefing:
            events = cat.get("events") or []
            if events:
                rows_html = ""
                for ev in events:
                    rows_html += (
                        '<div class="ob-event">'
                        f'<div class="ob-event-headline">{ev.get("headline", "")}</div>'
                        f'<div class="ob-event-detail">{ev.get("detail", "")}</div>'
                        '<div class="para-row">'
                        '<span class="para-label">시장 함의</span>'
                        f'<span class="para-text">{ev.get("implication", "")}</span>'
                        "</div>"
                        "</div>"
                    )
            else:
                rows_html = (
                    '<div class="ob-noevent">전날 시장에 영향을 줄 만한 '
                    "특이사항이 보고되지 않았습니다.</div>"
                )
            st.markdown(
                '<div class="ob-cat">'
                f'<div class="ob-cat-label">{cat.get("label", "")}</div>'
                f"{rows_html}"
                "</div>",
                unsafe_allow_html=True,
            )

    # ════ Zone 3 — 투자 아이디어 ═════════════════════════════════════
    st.markdown(
        '<div class="section-title">투자 아이디어</div>',
        unsafe_allow_html=True,
    )

    # 금일 추천 종목 — 통합 단일 섹션 (사용자 요구 2026-05-03)
    # 큐레이션 / LLM Researched / Heuristic 구분 없이 Alpha Score 상위만 표시.
    # 큐레이션은 시드 예시일 뿐, 우대 안 받음. 모든 종목 동등 평가.
    render_unified_top_picks(brief["picks"])

    # Mag 7 Laggard — cohort rotation strategy 지원 (사용자 요구 2026-05-03)
    # Mag 7 중 cohort 평균 대비 -10%p 이상 후행 + thesis 유지 종목 발굴
    render_mag7_laggard_section()

    # 금일 신규 발굴 후보 (Discovery 큐별 raw 시그널 — 정밀 검토 전 단계)
    render_brief_discovery_section()

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown('<div class="section-title">금일 주요 알림</div>', unsafe_allow_html=True)
        if brief["alerts"]:
            for a in brief["alerts"]:
                st.markdown(f'<div class="info-row alert">{a}</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="info-row alert">금일 특이 알림이 없습니다.</div>',
                unsafe_allow_html=True,
            )
    with col_right:
        st.markdown('<div class="section-title">금일 점검 사항</div>', unsafe_allow_html=True)
        if brief["check_items"]:
            for a in brief["check_items"]:
                st.markdown(f'<div class="info-row check">{a}</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="info-row check">점검 후보가 비어있습니다.</div>',
                unsafe_allow_html=True,
            )

    # 관찰 후보 저장
    if brief["picks"]:
        st.markdown('<div class="section-title">관찰 후보 저장</div>', unsafe_allow_html=True)
        cols = st.columns(min(5, len(brief["picks"])) or 1)
        for i, r in enumerate(brief["picks"]):
            with cols[i % len(cols)]:
                if st.button(
                    f"{display_name(r['name_ko'], r['ticker'])} 저장",
                    key=f"save_{r['ticker']}",
                    use_container_width=True,
                ):
                    append_decision(r, reason="brief picks")
                    st.toast("decision_log.csv 에 저장했습니다.")

    # (Graduation Tracker 는 시스템 복잡도 대비 가치가 낮아 제거 — 사용자 요청.)


# ---------------------------------------------------------------------------
# 화면: 종목 상세 (Executive Summary)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Valuation 탭 — IC Memo + Financial Model (PE/IC 스타일)
# ---------------------------------------------------------------------------

def render_valuation():
    """Valuation 탭 — 회사명 입력 → IC Memo + Financial Model 생성·노출.

    PE/IC Memo 양식 기반. 사용자 본인 워크플로 도구이므로 JKL 등 로고/이름 없음.
    Excel/PDF 미생성 — HTML + Python 으로 한 화면에 통합.

    데이터 입력 흐름:
      1. 사용자 회사명 입력 + Build 클릭
      2. session_state["valuation_company"] 저장 + 채팅에 "회사명: XXX" 보내라는 안내
      3. Claude 가 다음 턴에 분석 → session_state["valuation_data"] 채움 (dict)
      4. 이 함수가 HTML 렌더 (상단 IC Memo / 하단 Model)

    빈 상태에서는 안내 + empty template preview.
    """
    page_header("Valuation", meta="IC Memo + Financial Model — PE/IC 스타일")

    # ───── 회사 입력 ─────
    with st.container():
        st.markdown(
            '<div style="background:var(--panel,#0F172A); border-radius:8px; '
            'padding:16px 20px; margin-bottom:14px;">'
            '<div style="font-size:13px; color:var(--text,#F8FAFC); font-weight:600; margin-bottom:8px;">'
            '회사 입력</div>'
            '<div style="font-size:12px; color:var(--muted,#94A3B8); line-height:1.55; margin-bottom:8px;">'
            '회사명 입력 후 채팅창에 <code style="background:rgba(59,130,246,0.15); padding:2px 6px; '
            'border-radius:3px;">회사명: XXX</code> 형식으로 보내주세요. Claude 가 분석 후 이 페이지에 결과를 노출합니다.'
            '</div></div>',
            unsafe_allow_html=True,
        )

        col1, col2, col3 = st.columns([3, 1, 2])
        with col1:
            ticker_or_name = st.text_input(
                "회사명 또는 ticker",
                value=st.session_state.get("valuation_company", ""),
                placeholder="예: 현대건설, OXY, CRDO, 파마리서치",
                key="valuation_company_input",
                label_visibility="collapsed",
            )
        with col2:
            if st.button("저장", type="primary", use_container_width=True, key="valuation_save_btn"):
                if ticker_or_name.strip():
                    st.session_state["valuation_company"] = ticker_or_name.strip()
                    st.success(f"채팅창에 '회사명: {ticker_or_name.strip()}' 입력 → 분석 시작")
                else:
                    st.warning("회사명 입력 필요")
        with col3:
            if st.button("초기화", use_container_width=True, key="valuation_clear_btn"):
                for k in ("valuation_company", "valuation_data"):
                    st.session_state.pop(k, None)
                st.rerun()

    # ───── 데이터 가져오기 ─────
    # 1) session_state["valuation_data"] (in-session 분석 결과)
    # 2) data/valuations/{회사명}.json (영구 저장된 분석 결과)
    valuation_data = st.session_state.get("valuation_data")
    saved_company = st.session_state.get("valuation_company", "")

    # 회사명 으로 영구 저장 JSON 찾기 (in-session data 없을 때)
    if not valuation_data and saved_company:
        try:
            import json as _json
            from pathlib import Path as _Path
            vpath = _Path(PROJECT_ROOT) / "data" / "valuations" / f"{saved_company}.json"
            if vpath.exists():
                valuation_data = _json.loads(vpath.read_text(encoding="utf-8"))
        except Exception as e:
            log.debug(f"valuation JSON 로드 실패 ({saved_company}): {e}")

    # 사용 가능한 회사 리스트 (data/valuations/*.json 스캔)
    available_companies = []
    try:
        from pathlib import Path as _Path
        vdir = _Path(PROJECT_ROOT) / "data" / "valuations"
        if vdir.exists():
            available_companies = sorted([p.stem for p in vdir.glob("*.json")])
    except Exception:
        pass
    if available_companies:
        st.markdown(
            '<div style="font-size:11.5px; color:var(--muted,#94A3B8); margin-bottom:10px;">'
            f'분석 완료 회사: <b style="color:var(--text,#F8FAFC);">{", ".join(available_companies)}</b> '
            '— 위 입력란에 회사명 입력 시 자동 로드'
            '</div>',
            unsafe_allow_html=True,
        )

    if not valuation_data:
        # Empty state — empty template 으로 UI 골격 보여줌
        try:
            from src.valuation_template import make_empty_template, render_ic_memo_html
            empty = make_empty_template()
            if saved_company:
                empty["company"]["name_ko"] = saved_company
                empty["ic_memo"]["verdict_oneliner"] = (
                    f"'{saved_company}' 분석 대기 중. "
                    "채팅창에 '회사명: " + saved_company + "' 입력 → Claude 가 IC Memo + Model 자동 생성."
                )
            st.markdown(
                '<div style="background:var(--panel,#0F172A); border-radius:8px; padding:18px 22px;">'
                + render_ic_memo_html(empty)
                + '</div>',
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.warning(f"템플릿 렌더 실패: {e}")
        return

    # ───── 데이터 있음 — IC Memo + Model 통합 노출 ─────
    try:
        from src.valuation_template import render_ic_memo_html, render_model_html

        st.markdown(
            '<div style="background:var(--panel,#0F172A); border-radius:8px; padding:18px 22px; margin-bottom:14px;">'
            + render_ic_memo_html(valuation_data)
            + '</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="background:var(--panel,#0F172A); border-radius:8px; padding:18px 22px;">'
            + render_model_html(valuation_data)
            + '</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.error(f"Valuation 렌더 실패: {e}")
        log.exception("valuation render error")


def render_stock_detail():
    # scroll_to_top 처리는 라우팅 직전에 통합 처리되므로 여기서는 별도 처리 없음
    render_back_button("stock_detail")
    if not rows:
        page_header("종목 상세")
        st.warning("투자 유니버스 데이터가 비어있습니다.")
        return

    universe_tickers = [r["ticker"] for r in rows]
    selected = st.session_state.get("selected_ticker", universe_tickers[0])
    if selected not in universe_tickers:
        selected = universe_tickers[0]

    page_header("종목 상세", meta="Executive Summary 포맷")
    render_data_health_warning()

    labels = {r["ticker"]: display_name(r["name_ko"], r["ticker"]) for r in rows}
    ticker = st.selectbox(
        "종목 선택",
        options=universe_tickers,
        index=universe_tickers.index(selected),
        format_func=lambda t: labels.get(t, t),
        key="stock_detail_select",
        label_visibility="collapsed",
    )
    # selectbox 자체는 자동 rerun 되므로 navigate_to 호출하지 않고 플래그만 set
    if ticker != st.session_state.get("selected_ticker"):
        st.session_state["scroll_to_top"] = True
    st.session_state["selected_ticker"] = ticker

    row = next((r for r in rows if r["ticker"] == ticker), None)
    if row is None:
        st.error("선택된 종목이 유니버스에 없습니다.")
        return
    md = row["market_data"]
    if not md.get("available"):
        st.markdown(
            f'<div class="card"><b>{display_name(row["name_ko"], row["ticker"])}</b> — 데이터 확인 필요. '
            f'({md.get("error") or "수집 실패"})</div>',
            unsafe_allow_html=True,
        )
        return

    # Phase 5: QLD ctx + regime 컨텍스트 — LESS 계산용
    _qld_ctx_for_detail: dict | None = None
    _regime_for_detail = None
    try:
        # daily_trackers 의 QLD 데이터 활용 (이미 cached)
        _trk = _bounded_call(cached_daily_trackers, token, timeout=20) or {}
        for entry in (_trk.get("core") or []):
            if (entry.get("meta") or {}).get("symbol") == "QLD":
                _qld_ctx_for_detail = {"market_data": entry.get("data") or {}}
                break
    except Exception:
        pass
    try:
        with db.db_session() as _cn:
            _regime_for_detail = db.fetch_latest_market_regime(_cn)
    except Exception:
        pass

    detail = build_stock_detail(row, qld_ctx=_qld_ctx_for_detail, regime=_regime_for_detail)

    # 헤더 블록 (종목명 / 투자 판단 / 종목 분류)
    st.markdown(
        '<div class="card">'
        '<div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">'
        f'<div><div class="pick-name" style="font-size:22px;">{detail["name_kr"]}</div>'
        f'<div class="pick-type">{detail["investment_type"]}'
        f' · <b style="color:var(--navy);">분류: {detail.get("company_type","Structural Growth")}</b></div></div>'
        f'<div>{render_tag(detail["judgment_tag"])}</div>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # 데이터 품질 경고 — split-adjusted / 가격 오류 가능성
    _md = row.get("market_data") or {}
    _dq_flag = _md.get("data_quality_flag")
    _dq_reason = _md.get("data_quality_reason")
    if _dq_flag and _dq_flag != "OK":
        st.markdown(
            '<div class="data-quality-warning">'
            f'<b>가격 데이터 확인 필요</b> — {_dq_flag}'
            + (f': {_dq_reason}' if _dq_reason else '')
            + ' (split-adjusted 또는 yfinance 데이터 소스 점검 권장)'
            "</div>",
            unsafe_allow_html=True,
        )

    # ================== Alpha Score (통합 투자 매력도) ==================
    render_alpha_score_section(detail.get("alpha_score"))

    # ============ Phase 5 — Leveraged ETF Suitability + Use Case ============
    _lev_info = detail.get("leveraged_etf_info") or {}
    _tax_info = detail.get("taxonomy_info") or {}
    if _lev_info.get("available"):
        _less = _lev_info.get("less_score")
        _less_str = f"{_less:.0f}" if _less is not None else "—"
        _verdict = _lev_info.get("less_verdict") or "—"
        _use_case = _lev_info.get("suggested_use_case") or "—"
        _body_vs_2x = _lev_info.get("body_vs_2x") or "—"
        _pp = _lev_info.get("profit_protection_trigger") or "—"
        _qld_view = _lev_info.get("qld_view") or "—"
        _lev_tickers = _lev_info.get("leveraged_etf_tickers") or []
        _lev_str = ", ".join(_lev_tickers) if _lev_tickers else "(없음)"

        # 카테고리 + bottleneck layer
        _cats = _tax_info.get("categories") or []
        _bottle = _tax_info.get("bottleneck_layer")
        _cat_str = " · ".join(_cats) if _cats else "—"
        _bottle_str = f"  [{_bottle}]" if _bottle else ""

        # block flag 적용 여부 표시
        _blocks = [b for b in (_lev_info.get("block_flags") or []) if b.get("triggered") is True]
        _block_html = ""
        if _blocks:
            _block_lines = "".join(
                f'<li style="color:#EF4444;">{b["rule"].split(". ", 1)[-1]}</li>' for b in _blocks
            )
            _block_html = (
                '<div style="margin-top:8px; padding:8px 10px; background:rgba(239,68,68,0.06); '
                'border-left:3px solid #EF4444; border-radius:4px;">'
                '<div style="font-size:11px; color:#EF4444; font-weight:700; margin-bottom:4px;">'
                '🚫 신규 진입 차단 신호</div>'
                f'<ul style="margin:0; padding-left:18px; font-size:12px; line-height:1.6;">{_block_lines}</ul>'
                '</div>'
            )

        _verdict_color = (
            "#22C55E" if (_less or 0) >= 80 else
            "#F59E0B" if (_less or 0) >= 60 else
            "#94A3B8" if (_less or 0) >= 40 else
            "#EF4444"
        )

        st.markdown(
            '<div class="card" style="margin-top:14px;">'
            '<div style="display:flex; justify-content:space-between; align-items:center; '
            'gap:12px; flex-wrap:wrap; margin-bottom:10px;">'
            '<div>'
            '<div style="font-size:11px; color:var(--muted); letter-spacing:.03em;">'
            'LEVERAGED ETF SUITABILITY · 산업 분류</div>'
            f'<div style="font-size:15px; color:var(--text); font-weight:600; margin-top:3px;">'
            f'{_cat_str}{_bottle_str}</div>'
            '</div>'
            f'<div style="text-align:right;">'
            f'<div style="font-size:11px; color:var(--muted);">LESS Score</div>'
            f'<div style="font-size:24px; font-weight:700; color:{_verdict_color}; line-height:1;">{_less_str}</div>'
            f'<div style="font-size:11px; color:{_verdict_color}; margin-top:2px;">{_verdict}</div>'
            '</div>'
            '</div>'
            '<div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px; '
            'font-size:12.5px; line-height:1.65;">'
            '<div>'
            '<div style="color:var(--muted); font-size:11px;">2X ETF</div>'
            f'<div style="color:var(--text); font-weight:500;">{_lev_str}</div>'
            '</div>'
            '<div>'
            '<div style="color:var(--muted); font-size:11px;">QLD 상대 매력도</div>'
            f'<div style="color:var(--text); font-weight:500;">{_qld_view}</div>'
            '</div>'
            '<div>'
            '<div style="color:var(--muted); font-size:11px;">본주 vs 2X 판단</div>'
            f'<div style="color:var(--text); font-weight:500;">{_body_vs_2x}</div>'
            '</div>'
            '<div>'
            '<div style="color:var(--muted); font-size:11px;">Suggested Use Case</div>'
            f'<div style="color:var(--text); font-weight:600;">{_use_case}</div>'
            '</div>'
            '</div>'
            '<div style="margin-top:10px; padding-top:8px; border-top:1px solid var(--line);">'
            '<div style="font-size:11px; color:var(--muted); margin-bottom:3px;">Profit Protection Trigger</div>'
            f'<div style="font-size:12.5px; color:var(--text); line-height:1.55;">{_pp}</div>'
            '</div>'
            + _block_html +
            '</div>',
            unsafe_allow_html=True,
        )

    # ============ Capital Efficiency (Phase 2) 보조 점수 ============
    render_capital_efficiency_section(ticker)

    # Mag 7 Cohort Relative Performance chip — Mag 7 종목만 표시
    try:
        from src.cohort import (
            MAG7_TICKERS, compute_mag7_cohort_returns, get_relative_performance,
            detect_mag7_laggard,
        )
        if ticker in MAG7_TICKERS:
            cohort = compute_mag7_cohort_returns(rows)
            rel = get_relative_performance(ticker, row.get("market_data") or {}, cohort)
            laggard_check = detect_mag7_laggard(
                ticker, row.get("market_data") or {}, cohort, row.get("news_agg"),
            )

            def _fmt(v):
                if v is None:
                    return "—"
                color = "#22C55E" if v >= 0 else "#EF4444"
                return f'<b style="color:{color};">{v*100:+.1f}%p</b>'

            laggard_pill = ""
            if laggard_check.get("is_laggard"):
                laggard_pill = (
                    '<span style="margin-left:12px; padding:2px 8px; '
                    'border-radius:6px; font-size:12px; font-weight:600; '
                    'background:#1E3A8A; color:#93C5FD; '
                    'border:1px solid #1E40AF;">'
                    f'Mag 7 Laggard · score {laggard_check.get("score", 0):.0f}'
                    "</span>"
                )

            cohort_html = (
                '<div class="card" style="background:#1F2937; border-color:#2D3748; '
                'margin-top:-6px; margin-bottom:14px;">'
                '<div style="display:flex; justify-content:space-between; '
                'align-items:center; flex-wrap:wrap; gap:12px;">'
                '<div style="font-size:13px; color:var(--muted);">'
                'Mag 7 cohort 대비 상대 수익률'
                f'{laggard_pill}'
                "</div>"
                '<div style="font-size:13px;">'
                f'<span style="margin-right:14px;">1Y {_fmt(rel.get("rel_1y"))}</span>'
                f'<span style="margin-right:14px;">3M {_fmt(rel.get("rel_3m"))}</span>'
                f'<span>1M {_fmt(rel.get("rel_1m"))}</span>'
                "</div>"
                "</div>"
                '<div style="font-size:11px; color:var(--muted); margin-top:6px;">'
                f'Cohort 평균: 1Y {(cohort.get("avg_1y") or 0)*100:+.1f}% · '
                f'3M {(cohort.get("avg_3m") or 0)*100:+.1f}% · '
                f'1M {(cohort.get("avg_1m") or 0)*100:+.1f}% '
                f'(Mag 7 중 {cohort.get("n_available", 0)} 종목 평균)'
                "</div>"
                "</div>"
            )
            st.markdown(cohort_html, unsafe_allow_html=True)
    except Exception as e:
        log.debug(f"cohort chip 렌더 실패: {e}")

    # 이 회사는 쉽게 말해
    st.markdown(
        '<div class="simple-card">'
        '<div class="simple-eyebrow">이 회사는 쉽게 말해</div>'
        f'<div class="simple-body">{detail["simple_explanation"]}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # 핵심 투자 논리
    st.markdown(
        '<div class="card">'
        '<div class="pick-block-label">핵심 투자 논리</div>'
        f'<div style="font-size:15px; color:var(--text); line-height:1.7;">{detail["thesis_full"]}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ================== Earnings Quality & Moat Assessment ==================
    render_earnings_quality_section(detail.get("earnings_quality"), ticker=ticker)

    # ================== Strategic Lens ==================
    render_strategic_lens_section(detail.get("strategic_lens"))

    # ================== Bull / Bear 토론 ==================
    render_bull_bear_debate_section(ticker)

    # ================== 장기 주가 흐름 (이동: 핵심 투자 논리 직후) ==================
    st.markdown(
        '<div class="section-title" style="margin-bottom:6px;">장기 주가 흐름</div>'
        '<div style="font-size:14px; color:var(--muted); margin-bottom:16px;">'
        '상장 이후 주가 흐름과 주요 이벤트를 함께 확인할 수 있습니다.'
        "</div>",
        unsafe_allow_html=True,
    )
    render_price_chart(row)

    # 최근 주요 이벤트
    events = detail.get("recent_events") or []
    if events:
        st.markdown('<div class="section-title">최근 주요 이벤트</div>', unsafe_allow_html=True)
        for ev in events:
            cls_key = (ev.get("classification") or "needs_check").replace("_", "-")
            cls_label = ev.get("classification_label") or "확인 필요"
            status = ev.get("status", "확인 필요")
            # 상태 배지 클래스 (한국어 첫 단어 매핑)
            status_first = status.split()[0] if status else "확인"
            status_cls = {
                "진행": "status-진행",
                "종료": "status-종료",
                "완료": "status-완료",
                "무산": "status-무산",
                "확인": "status-확인",
            }.get(status_first, "status-확인")

            sources_str = " · ".join(ev.get("sources") or []) or "확인 필요"
            confidence = ev.get("confidence") or "Low"
            last_updated = ev.get("last_updated") or ev.get("date") or "—"

            st.markdown(
                '<div class="event-card">'
                '<div class="event-head">'
                '<div class="event-meta">'
                f'<b>{ev.get("date","")}</b> {ev.get("type","")}'
                f'<span class="status-badge {status_cls}">상태: {status}</span>'
                "</div>"
                f'<span class="chip chip-{cls_key}">{cls_label}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">요약</span>'
                f'<span class="para-text">{ev.get("summary","")}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">투자적 의미</span>'
                f'<span class="para-text">{ev.get("impact","")}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">확인 필요</span>'
                f'<span class="para-text">{ev.get("check","")}</span>'
                "</div>"
                '<div class="event-foot">'
                f'<b>출처</b>{sources_str}'
                f'<span class="sep">·</span><b>업데이트</b>{last_updated} 기준'
                f'<span class="sep">·</span><b>Confidence</b>{confidence}'
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # 핵심 지표 6 카드
    st.markdown('<div class="section-title">핵심 지표</div>', unsafe_allow_html=True)
    m_cols = st.columns(6)
    metrics = [
        ("현재가", detail["current_price"], detail["daily_return"]),
        ("52주 고점 대비", detail["drawdown"], ""),
        (
            "Final Score",
            f'{detail["final_score"] if detail["final_score"] is not None else "-"}',
            detail["score_label"],
        ),
        ("Upside 잠재력", detail["upside"], ""),
        ("투자 판단", detail["judgment_tag"], ""),
        ("리스크 등급", detail["risk_grade"], ""),
    ]
    for i, (label, value, sub) in enumerate(metrics):
        with m_cols[i]:
            st.markdown(
                '<div class="metric-card">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div>'
                + (f'<div class="metric-sub">{sub}</div>' if sub else "")
                + "</div>",
                unsafe_allow_html=True,
            )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # ================== [NEW] 투자 지표 ==================
    st.markdown('<div class="section-title">투자 지표</div>', unsafe_allow_html=True)
    val_main, val_extras = valuation_metrics_cards(row)
    # 6개 카드를 3열×2행으로 배치 (좁은 화면에서도 carding 안정)
    for row_start in (0, 3):
        row_cols = st.columns(3, gap="large")
        for j in range(3):
            idx = row_start + j
            if idx >= len(val_main):
                break
            m = val_main[idx]
            with row_cols[j]:
                st.markdown(
                    '<div class="metric-card">'
                    f'<div class="metric-label">{m["label"]}</div>'
                    f'<div class="metric-value">{m["value"]}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
    with st.expander("더 보기", expanded=False):
        ex_cols = st.columns(4, gap="large")
        for i, m in enumerate(val_extras):
            with ex_cols[i]:
                st.markdown(
                    '<div class="metric-card">'
                    f'<div class="metric-label">{m["label"]}</div>'
                    f'<div class="metric-value">{m["value"]}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # ================== [NEW] 가치평가 지표 ==================
    st.markdown('<div class="section-title">가치평가 지표</div>', unsafe_allow_html=True)
    val_tabs = st.tabs(["PER", "PBR", "PSR", "EV/EBITDA"])
    for i, metric_name in enumerate(["PER", "PBR", "PSR", "EV/EBITDA"]):
        with val_tabs[i]:
            comp = valuation_comparison(row, metric_name, rows)
            try:
                import plotly.graph_objects as go

                labels = []
                values = []
                colors = []
                if comp["company"] is not None and comp["company"] > 0:
                    labels.append(detail["name_kr"])
                    values.append(comp["company"])
                    colors.append("#3B82F6")
                if comp["industry_avg"] is not None:
                    labels.append("산업 평균")
                    values.append(comp["industry_avg"])
                    colors.append("#9CA3AF")
                if comp["peer_avg"] is not None:
                    labels.append("Peer 평균")
                    values.append(comp["peer_avg"])
                    colors.append("#6B7280")

                if values:
                    fig = go.Figure(
                        go.Bar(
                            y=labels,
                            x=values,
                            orientation="h",
                            marker=dict(
                                color=colors,
                                line=dict(width=0),
                            ),
                            text=[f"{v:.1f}배" for v in values],
                            textposition="outside",
                            textfont=dict(size=12, color="#F9FAFB"),
                            hovertemplate="%{y}<br>%{x:.1f}배<extra></extra>",
                            width=0.35,
                        )
                    )
                    # x축 max를 약간 더 길게 잡아 라벨이 잘리지 않게
                    x_max = max(values) * 1.18 if values else 1
                    fig.update_layout(
                        template="plotly_dark",
                        paper_bgcolor="#111827",
                        plot_bgcolor="#111827",
                        height=260 if len(labels) <= 2 else 300,
                        margin=dict(l=130, r=60, t=20, b=20),
                        bargap=0.55,
                        xaxis=dict(
                            range=[0, x_max],
                            showgrid=False,
                            zeroline=False,
                            showline=False,
                            showticklabels=False,        # ← x축 숫자 제거
                            ticks="",
                        ),
                        yaxis=dict(
                            showgrid=False,
                            zeroline=False,
                            showline=False,
                            tickfont=dict(color="#CBD5E1", size=14),
                        ),
                        showlegend=False,
                    )
                    st.plotly_chart(
                        fig,
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )
                else:
                    st.info("비교 가능한 데이터가 부족합니다.")
            except Exception as e:
                st.info(f"차트 렌더링 실패: {e}")
            st.markdown(
                f'<div class="section-summary">{comp["interpretation"]}</div>',
                unsafe_allow_html=True,
            )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # ================== [NEW] 주요 재무정보 ==================
    st.markdown('<div class="section-title">주요 재무정보</div>', unsafe_allow_html=True)
    fin_tabs = st.tabs(["연간", "분기"])
    for tab_i, kind in enumerate(["annual", "quarterly"]):
        with fin_tabs[tab_i]:
            with st.spinner("재무 데이터 수집 중..."):
                if kind == "annual":
                    periods = cached_annual_financials(token, row["ticker"])
                else:
                    periods = cached_quarterly_financials(token, row["ticker"])
            if not periods:
                st.info("데이터 확인 필요 — yfinance에서 재무 데이터를 가져올 수 없습니다.")
                continue
            try:
                import plotly.graph_objects as go

                xs = [p["period"] for p in periods]
                rev = [p.get("revenue") for p in periods]
                op = [p.get("operating_income") for p in periods]
                net = [p.get("net_income") for p in periods]
                opm = [p.get("operating_margin") for p in periods]

                # 단위 정규화: 1B 이상이면 단위 십억$ 표기
                def _scale_y(vals):
                    safe = [v for v in vals if v is not None]
                    if not safe:
                        return vals, "$"
                    m = max(abs(v) for v in safe)
                    if m >= 1e9:
                        return [v / 1e9 if v is not None else None for v in vals], "B$"
                    if m >= 1e6:
                        return [v / 1e6 if v is not None else None for v in vals], "M$"
                    return vals, "$"

                rev_s, unit = _scale_y(rev)
                op_s, _ = _scale_y(op) if False else ([v / 1e9 if v is not None and unit == "B$" else (v / 1e6 if v is not None and unit == "M$" else v) for v in op], unit)
                net_s = [v / 1e9 if v is not None and unit == "B$" else (v / 1e6 if v is not None and unit == "M$" else v) for v in net]

                fig = go.Figure()
                fig.add_trace(
                    go.Bar(
                        name="매출액",
                        x=xs,
                        y=rev_s,
                        marker=dict(color="#374151"),
                        hovertemplate="%{x} 매출액<br>%{y:.2f} " + unit + "<extra></extra>",
                    )
                )
                fig.add_trace(
                    go.Bar(
                        name="영업이익",
                        x=xs,
                        y=op_s,
                        marker=dict(color="#3B82F6"),
                        hovertemplate="%{x} 영업이익<br>%{y:.2f} " + unit + "<extra></extra>",
                    )
                )
                fig.add_trace(
                    go.Bar(
                        name="당기순이익",
                        x=xs,
                        y=net_s,
                        marker=dict(color="#06B6D4"),
                        hovertemplate="%{x} 당기순이익<br>%{y:.2f} " + unit + "<extra></extra>",
                    )
                )
                # ── 기간별 y_top / y_bottom 계산 (음수 값 포함) ───────────
                y_tops: list[float] = []
                y_bottoms: list[float] = []
                for i in range(len(xs)):
                    vals = [
                        v for v in [rev_s[i], op_s[i], net_s[i]] if v is not None
                    ]
                    y_tops.append(max(vals) if vals else 0.0)
                    y_bottoms.append(min(vals) if vals else 0.0)

                all_vals = [
                    v
                    for v in (list(rev_s) + list(op_s) + list(net_s))
                    if v is not None
                ]
                if all_vals:
                    min_v = min(all_vals)
                    max_v = max(all_vals)
                else:
                    min_v, max_v = 0.0, 1.0
                if max_v == min_v:
                    pad = abs(max_v) * 0.25 if max_v != 0 else 1.0
                else:
                    pad = (max_v - min_v) * 0.20
                y_axis_max = max(0.0, max_v + pad * 1.10)
                y_axis_min = min(0.0, min_v - pad * 0.5)

                # ── 0 기준선 (음수 구간이 있을 때만 명시 표시) ────────────
                if y_axis_min < 0:
                    fig.add_hline(
                        y=0,
                        line=dict(color="#374151", width=1),
                        layer="below",
                    )

                # ── OPM 라벨 (보더/박스 제거, 14px bold 네이비) ──────────
                for i, xlabel in enumerate(xs):
                    opm_v = opm[i]
                    grp_top = y_tops[i]
                    grp_bot = y_bottoms[i]
                    # 라벨 위치: 그룹 상단 위쪽 여백
                    if grp_top > 0:
                        label_y = grp_top + pad * 0.45
                    elif grp_bot < 0:
                        # 모든 막대가 음수인 기간 → 0선 위쪽에 표시
                        label_y = pad * 0.30
                    else:
                        # 데이터 없는 기간 (Forward) → 차트 중간 상단
                        label_y = max(y_axis_max * 0.55, pad * 0.4)

                    if opm_v is None:
                        text = "추정치 없음"
                        color = "#9CA3AF"
                    elif opm_v < 0:
                        text = f"OPM {opm_v * 100:.1f}%"
                        color = "#EF4444"
                    else:
                        text = f"OPM {opm_v * 100:.1f}%"
                        color = "#60A5FA"

                    fig.add_annotation(
                        x=xlabel,
                        y=label_y,
                        text=f"<b>{text}</b>",
                        showarrow=False,
                        font=dict(
                            size=14,
                            color=color,
                            family="-apple-system, sans-serif",
                        ),
                        bgcolor="rgba(255,255,255,0)",
                        borderwidth=0,
                        yanchor="bottom",
                    )

                # Forward 기간 음영 (카테고리 인덱스 기반 vrect)
                forward_idx = [i for i, p in enumerate(periods) if p.get("is_forward")]
                for fi in forward_idx:
                    fig.add_vrect(
                        x0=fi - 0.5,
                        x1=fi + 0.5,
                        fillcolor="rgba(59,130,246,0.10)",
                        line_width=0,
                        layer="below",
                    )

                fig.update_layout(
                    barmode="group",
                    template="plotly_dark",
                    paper_bgcolor="#111827",
                    plot_bgcolor="#111827",
                    height=420,
                    margin=dict(l=50, r=30, t=60, b=70),
                    bargap=0.30,
                    font=dict(size=13, color="#CBD5E1"),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=-0.22,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=14, color="#CBD5E1"),
                    ),
                    xaxis=dict(
                        showgrid=False,
                        zeroline=False,
                        showline=False,
                        color="#9CA3AF",
                        tickfont=dict(size=14, color="#9CA3AF"),
                    ),
                    yaxis=dict(
                        showgrid=False,            # ← 가로 gridline 완전 제거
                        zeroline=False,
                        showline=False,
                        color="#9CA3AF",
                        range=[y_axis_min, y_axis_max],  # 음수 구간 포함, OPM 라벨 여유
                        tickfont=dict(size=13, color="#9CA3AF"),
                        title=dict(
                            text=f"단위: {unit}",
                            font=dict(size=12, color="#6B7280"),
                        ),
                    ),
                    hoverlabel=dict(
                        bgcolor="#1F2937",
                        bordercolor="#374151",
                        font=dict(color="#F9FAFB", size=13),
                    ),
                )
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={"displayModeBar": False},
                )
            except Exception as e:
                st.info(f"차트 렌더링 실패: {e}")

            # 최근 기준 요약
            summary = latest_annual_summary(periods) if kind == "annual" else None
            if summary:
                def _f(v):
                    if v is None:
                        return "데이터 확인 필요"
                    if abs(v) >= 1e9:
                        return f"${v / 1e9:,.2f}B"
                    if abs(v) >= 1e6:
                        return f"${v / 1e6:,.1f}M"
                    return f"${v:,.0f}"
                opm_str = (
                    f"{summary['operating_margin'] * 100:.1f}%"
                    if summary.get("operating_margin") is not None
                    else "데이터 확인 필요"
                )
                st.markdown(
                    '<div class="fin-summary">'
                    f'<div class="item"><span class="k">최근 연간 ({summary["period"]}) · 매출액</span><span class="v">{_f(summary["revenue"])}</span></div>'
                    f'<div class="item"><span class="k">영업이익</span><span class="v">{_f(summary["operating_income"])}</span></div>'
                    f'<div class="item"><span class="k">당기순이익</span><span class="v">{_f(summary["net_income"])}</span></div>'
                    f'<div class="item"><span class="k">영업이익률</span><span class="v">{opm_str}</span></div>'
                    "</div>",
                    unsafe_allow_html=True,
                )

            # 해석
            st.markdown(
                f'<div class="section-summary">{financials_interpretation(periods)}</div>',
                unsafe_allow_html=True,
            )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # 핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<div class="section-title first">핵심 투자 포인트</div>', unsafe_allow_html=True)
        body = "".join(
            f'<div class="bullet"><span class="bullet-num">{i+1}.</span><span>{b}</span></div>'
            for i, b in enumerate(detail["key_points"])
        )
        st.markdown(f'<div class="card">{body}</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="section-title first">주요 리스크</div>', unsafe_allow_html=True)
        body = "".join(
            f'<div class="bullet"><span class="bullet-num">{i+1}.</span><span>{b}</span></div>'
            for i, b in enumerate(detail["key_risks"])
        )
        st.markdown(f'<div class="card">{body}</div>', unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="section-title first">확인 필요 사항</div>', unsafe_allow_html=True)
        body = "".join(
            f'<div class="bullet"><span class="bullet-num">{i+1}.</span><span>{b}</span></div>'
            for i, b in enumerate(detail["check_items"])
        )
        st.markdown(f'<div class="card">{body}</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # 시나리오 분석 / 투자 현인 Lens — v2 에서 제거
    # (기존 데이터는 build_stock_detail이 하위호환을 위해 유지하지만 UI 노출 안 함)

    # Anti-Thesis
    st.markdown('<div class="section-title">Anti-Thesis</div>', unsafe_allow_html=True)
    anti_body = "".join(
        f'<div class="bullet"><span class="bullet-num">·</span><span>{b}</span></div>'
        for b in detail["anti_thesis"]
    )
    st.markdown(f'<div class="card">{anti_body}</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # ================== Bottleneck Thesis (해당될 때만) ==================
    try:
        from src.bottleneck import build_bottleneck_thesis
        _bn_meta = {
            "ticker": row.get("ticker"),
            "name": row.get("name_en") or row.get("name_ko") or "",
            "sector": row.get("sector"),
            "industry": row.get("industry"),
        }
        # core_universe 는 sector / industry 비어 있을 수 있어 wide_universe 매핑에서 보강
        if not (_bn_meta["sector"] or _bn_meta["industry"]):
            _wm = _wide_universe_name_map.__wrapped__() if hasattr(_wide_universe_name_map, "__wrapped__") else None
            try:
                from src.universe import load_wide_universe
                for _u in load_wide_universe():
                    if (_u.get("ticker") or "").upper() == (row.get("ticker") or "").upper():
                        _bn_meta["sector"] = _u.get("sector")
                        _bn_meta["industry"] = _u.get("industry")
                        _bn_meta["name"] = _bn_meta["name"] or _u.get("name") or ""
                        break
            except Exception:
                pass
        bn = build_bottleneck_thesis(row.get("ticker", ""), _bn_meta, row.get("market_data") or {})
    except Exception:
        bn = None

    if bn:
        st.markdown(
            '<div class="section-title">Bottleneck Thesis'
            '<span style="font-size:13px; color:var(--muted); margin-left:8px;">밸류체인 병목 투자 논리</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        score_color = (
            "#60A5FA" if bn["score"] >= 75 else
            "#3B82F6" if bn["score"] >= 60 else
            "#9CA3AF"
        )
        st.markdown(
            '<div class="card">'
            '<div style="display:flex; gap:18px; align-items:center; margin-bottom:14px; flex-wrap:wrap;">'
            f'<span style="font-size:13px; color:var(--muted);">Bottleneck Alpha Score</span>'
            f'<span style="font-size:26px; font-weight:700; color:{score_color};">{bn["score"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Target Industry</span>'
            f'<span class="para-text">{bn["target_industry"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Value Chain Position</span>'
            f'<span class="para-text">{bn["value_chain_position"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Bottleneck Description</span>'
            f'<span class="para-text">{bn["bottleneck_description"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Why It Matters</span>'
            f'<span class="para-text">{bn["why_it_matters"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Who Benefits</span>'
            f'<span class="para-text">{bn["who_benefits"]}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">Key Risk</span>'
            f'<span class="para-text">{bn["key_risk"]}</span>'
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="judgment-card" style="margin-top:6px;">'
            '<div class="judgment-eyebrow">Alpha Judgment — 밸류체인 병목 관점</div>'
            f'<div class="judgment-body">{bn["alpha_judgment"]}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # ================== [NEW] 주요 뉴스 ==================
    st.markdown('<div class="section-title">주요 뉴스</div>', unsafe_allow_html=True)
    news_items = news_with_impact(row, limit=5)

    # DB 에서 한국어 요약 보강 (run_research 가 채운 detailed_summary_ko 등)
    try:
        with db.db_session() as conn:
            for n in news_items:
                nid = n.get("news_id") or db.make_news_id(
                    row["ticker"], n.get("link"), n.get("title"), n.get("published_at")
                )
                row_db = db.fetch_news_by_id(conn, nid)
                if row_db:
                    n["news_id"] = nid
                    if row_db["detailed_summary_ko"]:
                        n["detailed_summary_ko"] = row_db["detailed_summary_ko"]
                    if row_db["investment_implication_ko"]:
                        n["investment_implication_ko"] = row_db["investment_implication_ko"]
                    if row_db["thesis_impact_ko"]:
                        n["thesis_impact_ko"] = row_db["thesis_impact_ko"]
                    if row_db["confidence_level_ko"]:
                        n["confidence_level_ko"] = row_db["confidence_level_ko"]
                    try:
                        if "follow_up_items_ko" in row_db.keys() and row_db["follow_up_items_ko"]:
                            n["follow_up_items_ko"] = db.load_json(
                                row_db["follow_up_items_ko"], default=[]
                            )
                    except Exception:
                        pass
                    try:
                        if "content_availability" in row_db.keys() and row_db["content_availability"]:
                            n["content_availability"] = row_db["content_availability"]
                    except Exception:
                        pass

            # DB에 요약이 없는 뉴스는 즉석에서 한 번 요약 (UI 첫 진입에서도 자연스럽게)
            from src.news_summarizer import summarize_news_to_korean
            stock_ctx = {
                "ticker": row["ticker"],
                "name_ko": row.get("name_ko"),
                "theme": row.get("theme"),
                "category": row.get("category"),
            }
            for n in news_items:
                if not n.get("detailed_summary_ko"):
                    try:
                        s = summarize_news_to_korean(n, stock_context=stock_ctx)
                        n.update(s)
                    except Exception:
                        pass
    except Exception:
        pass

    if not news_items:
        st.markdown(
            '<div class="card">최근 뉴스 데이터가 없습니다. 데이터 업데이트 후 다시 확인하세요.</div>',
            unsafe_allow_html=True,
        )
    else:
        # ── 이벤트 클러스터링 (UI 전용 — 같은 토픽 + 같은 주 → 1 클러스터) ──
        from collections import defaultdict
        try:
            from src.event_processor import enrich_news, _extract_topics
        except Exception:
            enrich_news = lambda n: n
            _extract_topics = lambda _: set()

        def _week_key(d_str: str) -> str:
            try:
                d = _dt.date.fromisoformat((d_str or "")[:10])
                return f"{d.year}-W{d.isocalendar()[1]:02d}"
            except Exception:
                return "?-?"

        _groups: dict[tuple, list[dict]] = defaultdict(list)
        for n in news_items:
            e = enrich_news(n) if callable(enrich_news) else n
            text = (e.get("title") or "") + " " + (e.get("summary") or "")
            topics = _extract_topics(text) if callable(_extract_topics) else set()
            primary = sorted(topics)[0] if topics else "general"
            wk = _week_key(e.get("published_at") or n.get("published_at"))
            _groups[(primary, wk)].append(n)

        # 클러스터 → 대표 + 관련 기사 매핑
        cluster_by_news_id: dict[str, list[dict]] = {}
        single_news: list[dict] = []
        for key, members in _groups.items():
            if len(members) >= 2:
                # 한국어 요약이 풍부한 + 최신 발행일 기준 대표 선정
                rep = max(
                    members,
                    key=lambda m: (
                        len(m.get("detailed_summary_ko") or ""),
                        m.get("published_at") or "",
                    ),
                )
                rep_key = id(rep)
                related = [m for m in members if m is not rep]
                cluster_by_news_id[str(rep_key)] = related
                single_news.append(rep)
            else:
                single_news.append(members[0])

        # 정렬 — 발행일 최신 우선
        single_news.sort(
            key=lambda n: n.get("published_at") or "", reverse=True,
        )

        # 클러스터 카드 안에 관련 기사 링크 묶음을 노출하기 위해 메타 부착
        for n in single_news:
            related = cluster_by_news_id.get(str(id(n))) or []
            n["_cluster_related"] = related

        for n in single_news:
            cls_label = n.get("thesis_impact_ko") or n.get("thesis_impact_label") or "확인 필요"
            link = n.get("link") or ""
            title = n.get("title") or "(제목 없음)"
            title_html = (
                f'<a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
                if link
                else title
            )
            source = n.get("source") or "출처 확인 필요"
            published = n.get("published_at") or "날짜 확인 필요"

            detailed_ko = (
                n.get("detailed_summary_ko")
                or n.get("summary")
                or "요약 정보가 제공되지 않습니다."
            )
            implication = (
                n.get("investment_implication_ko")
                or n.get("investment_implication")
                or "추가 정밀 검토 필요"
            )
            confidence = (
                n.get("confidence_level_ko")
                or n.get("confidence_level")
                or n.get("confidence")
                or "Low"
            )

            link_btn = (
                f'<a class="news-link" href="{link}" target="_blank" rel="noopener noreferrer">기사 원문 보기 →</a>'
                if link
                else '<span class="news-link" style="opacity:0.5;">원문 링크 확인 필요</span>'
            )
            confidence_chip = (
                f'<span class="chip chip-needs-check" style="margin-left:8px; font-size:11px;">'
                f'Confidence {confidence}</span>'
            )

            cls_label_safe = (cls_label or "확인 필요").replace("Thesis ", "")
            chip_cls_key = {
                "강화": "strengthen",
                "약화": "weaken",
                "리스크 해소": "strengthen",
                "신규 리스크": "new-risk",
                "단기 노이즈": "noise",
                "확인 필요": "needs-check",
            }.get(cls_label_safe, "needs-check")

            # ── Follow-up Items: bullet 리스트 (Key points 섹션은 폐기) ──
            follow_ups = n.get("follow_up_items_ko") or []
            follow_html = ""
            if follow_ups:
                fu_items = "".join(
                    f'<li>{p}</li>' for p in follow_ups[:6]
                )
                follow_html = (
                    '<div class="para-row kpts-row">'
                    '<div class="para-label">Follow-up Items</div>'
                    f'<div class="para-text"><ul class="news-followups">{fu_items}</ul></div>'
                    "</div>"
                )

            # ── 관련 기사 묶음 (이벤트 클러스터) ──
            related = n.get("_cluster_related") or []
            related_html = ""
            if related:
                links = []
                for r in related[:5]:
                    rl = (r.get("link") or "").strip()
                    rs = (r.get("source") or "출처 미상").strip()
                    rt = (r.get("title") or "")[:80]
                    if rl:
                        links.append(
                            f'<a href="{rl}" target="_blank" rel="noopener noreferrer" '
                            f'class="related-news-link" title="{rt}">{rs}</a>'
                        )
                    else:
                        links.append(f'<span class="related-news-link" style="opacity:0.5;">{rs}</span>')
                related_html = (
                    '<div class="para-row kpts-row">'
                    '<div class="para-label">관련 기사</div>'
                    '<div class="para-text">'
                    + " · ".join(links)
                    + f'<span style="margin-left:8px; color:var(--text-2); font-size:13px;">'
                    + f'(같은 이벤트 {len(related) + 1}개 기사)</span>'
                    + "</div></div>"
                )

            st.markdown(
                '<div class="news-card">'
                '<div class="news-head">'
                f'<div class="news-title">{title_html}</div>'
                f'<span class="chip chip-{chip_cls_key}">{cls_label}</span>'
                "</div>"
                f'<div class="news-meta">{source} · {published}{confidence_chip}</div>'
                '<div class="para-row">'
                '<span class="para-label">Summary</span>'
                f'<span class="para-text">{detailed_ko}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">Key Thesis</span>'
                f'<span class="para-text">{implication}</span>'
                "</div>"
                f"{follow_html}"
                f"{related_html}"
                f"{link_btn}"
                "</div>",
                unsafe_allow_html=True,
            )
    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # 리서치 품질 카드 (종합 판단 직전)
    rq = detail.get("research_quality") or {}
    if rq:
        st.markdown('<div class="section-title">리서치 품질</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="quality-card">'
            '<div class="quality-grid">'
            f'<div class="quality-item"><div class="k">최신성</div><div class="v">{rq.get("staleness","Outdated")}</div></div>'
            f'<div class="quality-item"><div class="k">출처 신뢰도</div><div class="v">{rq.get("source_quality","Low")}</div></div>'
            f'<div class="quality-item"><div class="k">이벤트 상태</div><div class="v">{rq.get("status","확인 필요")}</div></div>'
            f'<div class="quality-item"><div class="k">Confidence</div><div class="v">{rq.get("confidence","Low")}</div></div>'
            "</div>"
            f'<div class="quality-foot"><b>업데이트</b>{rq.get("last_updated","—")} <span style="margin:0 10px; color:var(--line-strong);">·</span> '
            f'<b>출처</b>{rq.get("sources","확인 필요")}</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    # 종합 판단
    st.markdown('<div class="section-title">종합 판단</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="judgment-card">'
        '<div class="judgment-eyebrow">Conclusion</div>'
        f'<div class="judgment-body">{detail["final_judgment"]}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # 액션 버튼
    cb1, cb2, cb3 = st.columns([2, 2, 4])
    with cb1:
        if st.button("관찰 후보로 저장", use_container_width=True, type="primary"):
            append_decision(row, reason="manual save")
            st.toast("decision_log.csv 에 저장했습니다.")
    with cb2:
        wl = load_watchlist()
        if row["ticker"] in wl:
            if st.button("관심종목에서 제거", use_container_width=True):
                res = remove_from_watchlist(row["ticker"])
                if res.get("github"):
                    st.toast("관심종목 제거 ✓ (영구 저장 완료)")
                else:
                    st.toast(f"관심종목 제거 (github: {res.get('github_status')})")
                st.rerun()
        else:
            if st.button("관심종목 편입", use_container_width=True):
                res = add_to_watchlist(row["ticker"])
                if res.get("github"):
                    st.toast("관심종목 편입 ✓ (영구 저장 완료)")
                else:
                    st.toast(f"관심종목 편입 (github: {res.get('github_status')})")
                st.rerun()


# ---------------------------------------------------------------------------
# 화면: 우량주 과매도
# ---------------------------------------------------------------------------

def render_dislocation_card(row: dict[str, Any], idx: int):
    md = row["market_data"]
    name = display_name(row.get("name_ko", ""), row["ticker"])
    dd = md.get("drawdown_from_52w_high")
    dd_str = fmt_pct(dd) if dd is not None else "-"
    cause = key_risk(row)  # 하락 원인은 key_risk 텍스트를 활용
    thesis_kept = "일부 유지" if row.get("action_tag") == "Quality Dislocation" else "재확인 필요"
    interpretation = core_thesis(row)
    tag = row.get("action_tag", "Watchlist")

    html = f"""
    <div class="pick">
      <div class="pick-head">
        <div class="pick-name">{name}</div>
        {render_tag(tag)}
      </div>
      <div class="pick-divider"></div>
      <div class="kv"><span class="kv-k">52주 고점 대비 하락률</span><span class="kv-v">{dd_str}</span></div>
      <div class="kv"><span class="kv-k">하락 원인</span><span class="kv-v" style="text-align:right; max-width:65%;">{cause}</span></div>
      <div class="kv"><span class="kv-k">투자 논리 유지 여부</span><span class="kv-v">{thesis_kept}</span></div>
      <div class="pick-block" style="margin-top:14px;">
        <div class="pick-block-label">핵심 해석</div>
        <div class="pick-block-body">{interpretation}</div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
    cols = st.columns([2, 8])
    with cols[0]:
        if st.button("상세 보기", key=f"dislo_detail_{idx}_{row['ticker']}", use_container_width=True):
            navigate_to("detail", row["ticker"])


@st.cache_data(show_spinner=False)
def _wide_universe_name_map() -> dict[str, str]:
    """ticker → company name 매핑 (wide_universe.csv + core_universe.csv 통합)."""
    name_map: dict[str, str] = {}
    try:
        from src.universe import load_wide_universe, load_universe
        for u in load_wide_universe():
            t = (u.get("ticker") or "").upper()
            n = u.get("name") or ""
            if t and n:
                name_map[t] = n
        # core 의 한국어 이름이 우선
        for u in load_universe():
            t = (u.get("ticker") or "").upper()
            ko = u.get("name_ko") or ""
            en = u.get("name_en") or ""
            if t and (ko or en):
                name_map[t] = ko or en
    except Exception:
        pass
    return name_map


def _fetch_discovery_data():
    """DB 에서 Discovery 큐별 / Promotion 데이터 로드 (실패 시 빈 dict)."""
    try:
        from src import database as _db
        with _db.db_session() as conn:
            queues = {}
            for q in (
                "Quality Dislocation",
                "Earnings Revision",
                "Unusual Volume",
                "Civilization Alpha",
                "Bottleneck Supplier",
            ):
                items = [dict(r) for r in _db.fetch_discovery_scores(conn, queue_type=q, limit=20)]
                queues[q] = items
            promoted = [dict(r) for r in _db.fetch_promotion_candidates(conn, promoted_only=True, limit=20)]
            all_promotion = [dict(r) for r in _db.fetch_promotion_candidates(conn, promoted_only=False, limit=50)]
        return {"queues": queues, "promoted": promoted, "all_promotion": all_promotion}
    except Exception as e:
        return {"queues": {}, "promoted": [], "all_promotion": [], "error": str(e)}


def _render_discovery_card(c: dict, idx: int, *, key_prefix: str = "disc"):
    """Discovery / Promotion 후보 카드 — 종목명 + 큐 + 사유 + 핵심 지표 + 추천.

    Phase 6: taxonomy (사용자 카테고리·bottleneck layer) + LESS (2X ETF 가능성) chip 추가.
    """
    ticker = (c.get("ticker") or "?").upper()
    # name 이 ticker 와 같거나 비어있으면 wide universe 매핑에서 보강
    raw_name = c.get("name")
    if not raw_name or raw_name.upper() == ticker:
        name = _wide_universe_name_map().get(ticker, ticker)
    else:
        name = raw_name
    queue = c.get("queue_type") or "—"
    reason = c.get("reason") or c.get("signal_summary") or ""
    impact = c.get("thesis_impact") or "확인 필요"
    recommendation = c.get("action_recommendation") or ""
    promo_score = c.get("promotion_score")
    disc_score = c.get("discovery_score") or c.get("score")
    latest = c.get("latest_event_summary") or ""

    score_chips = ""
    if promo_score is not None:
        score_chips += f'<span class="chip chip-strengthen">Promo {promo_score:.0f}</span>'
    if disc_score is not None:
        score_chips += f'<span class="chip chip-needs-check">Disc {disc_score:.0f}</span>'

    # Phase 6 — Taxonomy chip (사용자 관심 카테고리 자동 매칭)
    taxonomy_chip = ""
    bottleneck_chip = ""
    try:
        from src.universe_taxonomy import (
            get_categories_for, get_bottleneck_layer, UNIVERSE_TAXONOMY,
            has_leveraged_etf,
        )
        cats = get_categories_for(ticker)
        if cats:
            # 첫 카테고리의 label 표시 (M7 + Robotics 같이 겹치는 경우 첫 번째만)
            first_cat = cats[0]
            cat_label = UNIVERSE_TAXONOMY.get(first_cat, {}).get("label", first_cat)
            # 짧게 (긴 라벨 줄이기)
            cat_short = cat_label.split(" / ")[0] if " / " in cat_label else cat_label
            taxonomy_chip = f'<span class="chip chip-strengthen">{cat_short}</span>'
            # bottleneck layer (있을 때만)
            for cat in cats:
                bl = get_bottleneck_layer(ticker, cat)
                if bl:
                    bottleneck_chip = f'<span class="chip chip-needs-check" style="font-size:10.5px;">{bl}</span>'
                    break
    except Exception:
        pass

    # Phase 6 — LESS score chip (2X ETF 가능 종목만)
    less_chip = ""
    try:
        if has_leveraged_etf(ticker):
            # rows 글로벌에서 해당 ticker row 찾아 LESS 계산
            row = None
            try:
                row = next((r for r in (rows or []) if (r.get("ticker") or "").upper() == ticker), None)  # noqa: F824
            except Exception:
                row = None
            if row and (row.get("market_data") or {}).get("available"):
                # QLD context + regime 가져오기 (재시도 없이 None 가능)
                qld_ctx = None
                try:
                    _trk = st.session_state.get("_daily_trackers_cache") or {}
                    for e in (_trk.get("core") or []):
                        if (e.get("meta") or {}).get("symbol") == "QLD":
                            qld_ctx = {"market_data": e.get("data") or {}}
                            break
                except Exception:
                    pass
                regime_for_less = None
                try:
                    with db.db_session() as _cn:
                        regime_for_less = db.fetch_latest_market_regime(_cn)
                except Exception:
                    pass
                try:
                    from src.leveraged_etf_score import score_leveraged_etf
                    _less = score_leveraged_etf(row, qld_ctx, regime_for_less, None)
                    _ls = _less.get("score")
                    if _ls is not None:
                        _color_cls = (
                            "chip-strengthen" if _ls >= 80 else
                            "chip-needs-check" if _ls >= 60 else
                            "chip-noise"
                        )
                        less_chip = f'<span class="chip {_color_cls}">LESS {_ls:.0f}</span>'
                except Exception:
                    pass
    except Exception:
        pass

    badges_html = (
        '<div style="display:flex; flex-direction:column; gap:6px; '
        'align-items:flex-end; flex-shrink:0;">'
        f'<span class="chip chip-needs-check">{queue}</span>'
        f'{taxonomy_chip}'
        f'{bottleneck_chip}'
        f'{less_chip}'
        f'{score_chips}'
        "</div>"
    )

    body = (
        '<div class="card">'
        '<div class="news-head" style="align-items:flex-start;">'
        f'<div class="news-title">{name} ({ticker})</div>'
        f'{badges_html}'
        "</div>"
        '<div class="para-row">'
        '<span class="para-label">Signal</span>'
        f'<span class="para-text">{reason or "—"}</span>'
        "</div>"
    )
    if latest:
        body += (
            '<div class="para-row">'
            '<span class="para-label">최근 이벤트</span>'
            f'<span class="para-text">{latest}</span>'
            "</div>"
        )
    body += (
        '<div class="para-row">'
        '<span class="para-label">Thesis 영향</span>'
        f'<span class="para-text">{impact}</span>'
        "</div>"
    )
    if recommendation:
        body += (
            '<div class="para-row">'
            '<span class="para-label">권장</span>'
            f'<span class="para-text">{recommendation}</span>'
            "</div>"
        )
    body += "</div>"
    st.markdown(body, unsafe_allow_html=True)


def render_mag7_laggard_section():
    """Mag 7 Laggard — cohort 평균 대비 후행하는 Mag 7 종목 발굴.

    형우의 cohort rotation strategy 지원:
    - "시장이 가는데 Mag 7 중 안 가는 종목은 결국 시장이 끌고 간다"
    - 인덱스 일부 매도 → laggard Mag 7 매수 → 평균회귀 시 outperform

    조건:
        - ticker in Mag 7 (AAPL/MSFT/GOOGL/GOOG/AMZN/META/NVDA/TSLA)
        - 3M return < cohort 평균 - 10%p
        - urgent risk 부재 (thesis 유지)
    """
    try:
        from src.cohort import find_all_laggards
        laggards = find_all_laggards(rows)
    except Exception as e:
        log.warning(f"Mag 7 Laggard 계산 실패: {e}")
        return

    if not laggards:
        # Laggard 없음 — 안내 정보만 (전체 Mag 7 cohort 상황 요약)
        try:
            from src.cohort import compute_mag7_cohort_returns, MAG7_TICKERS
            cohort = compute_mag7_cohort_returns(rows)
            n = cohort.get("n_available", 0)
            if n == 0:
                return  # 데이터 부재 시 섹션 자체 표시 X

            avg_3m = cohort.get("avg_3m")
            avg_3m_str = f"{avg_3m*100:+.1f}%" if avg_3m is not None else "N/A"
            st.markdown(
                '<div class="section-title">Mag 7 Laggard'
                '<span style="font-size:13px; color:var(--muted); margin-left:8px;">'
                '— cohort 평균 대비 후행하는 Mag 7 발굴'
                "</span></div>"
                f'<div class="card" style="background:#14321F; border-color:#1E5235; '
                f'color:#86EFAC;">'
                f'현재 Mag 7 cohort 가 균형 — 평균 3M {avg_3m_str}, '
                f'cohort 대비 -10%p 이상 후행 종목 없음. '
                "구조적 rotation 기회는 시장 변동 후 다시 점검."
                "</div>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass
        return

    # Laggard 발견 — 카드 렌더
    from src.cohort import compute_mag7_cohort_returns
    cohort = compute_mag7_cohort_returns(rows)
    avg_3m = cohort.get("avg_3m")
    avg_3m_str = f"{avg_3m*100:+.1f}%" if avg_3m is not None else "N/A"

    st.markdown(
        '<div class="section-title">Mag 7 Laggard'
        '<span style="font-size:13px; color:var(--muted); margin-left:8px;">'
        '— cohort 평균 대비 후행하는 Mag 7 발굴 (mean reversion 후보)'
        "</span></div>"
        '<div class="info-row check" style="background:#1E3A8A; border-left-color:#1E40AF; '
        'color:#93C5FD; margin-bottom:8px;">'
        f'※ 현재 Mag 7 cohort 평균 3M 수익률 {avg_3m_str}. 아래는 cohort 대비 -10%p 이상 후행 + '
        'thesis 유지 종목입니다. 형우의 cohort rotation strategy (시장이 가는데 안 가는 종목 매수) 적용 후보.'
        "</div>",
        unsafe_allow_html=True,
    )

    for i, lg in enumerate(laggards):
        ticker = lg["ticker"]
        name_ko = lg.get("name_ko", "")
        lag_3m = lg["lag_3m"]
        lag_1m = lg.get("lag_1m")
        score = lg["score"]
        reason = lg["reason"]
        row_data = lg.get("row")

        name_full = display_name(name_ko, ticker) if name_ko else ticker

        lag_3m_pct = lag_3m * 100
        lag_1m_pct = (lag_1m * 100) if lag_1m is not None else None

        # 절대 수익률 (참고용)
        md = (row_data or {}).get("market_data") or {}
        r_3m = md.get("3m_return")
        r_1m = md.get("1m_return")
        r_3m_str = f"{r_3m*100:+.1f}%" if r_3m is not None else "N/A"
        r_1m_str = f"{r_1m*100:+.1f}%" if r_1m is not None else "N/A"

        # Score color
        score_color = "#60A5FA" if score >= 70 else (
            "#3B82F6" if score >= 50 else "#9CA3AF"
        )

        body = (
            '<div class="card" style="border-left:3px solid #3B82F6;">'
            '<div style="display:flex; justify-content:space-between; align-items:flex-start; '
            'gap:12px; margin-bottom:8px; flex-wrap:wrap;">'
            f'<div><div style="font-size:16px; font-weight:600;">{name_full}</div>'
            f'<div style="margin-top:4px;">'
            f'<span class="chip chip-needs-check" style="font-size:11px;">Mag 7 Laggard</span>'
            "</div></div>"
            '<div style="text-align:right; flex-shrink:0;">'
            f'<div style="font-size:24px; font-weight:700; color:{score_color}; line-height:1.1;">'
            f'{score:.0f}<span style="font-size:13px; color:var(--muted); font-weight:500;">/100</span>'
            "</div>"
            '<div style="font-size:11px; color:var(--muted); margin-top:2px;">Laggard Score</div>'
            "</div></div>"
            '<div class="para-row">'
            '<span class="para-label">cohort 대비</span>'
            '<span class="para-text">'
            f'3M <b style="color:#F87171;">{lag_3m_pct:+.1f}%p</b>'
            + (f' · 1M <b style="color:#F87171;">{lag_1m_pct:+.1f}%p</b>'
               if lag_1m_pct is not None else "")
            + " 후행"
            "</span></div>"
            '<div class="para-row">'
            '<span class="para-label">절대 수익률</span>'
            f'<span class="para-text">3M {r_3m_str} · 1M {r_1m_str}</span>'
            "</div>"
            '<div class="para-row">'
            '<span class="para-label">판정 근거</span>'
            f'<span class="para-text">{reason}</span>'
            "</div>"
            "</div>"
        )
        st.markdown(body, unsafe_allow_html=True)

        if st.button(f"{ticker} 상세 보기", key=f"mag7lag_{ticker}_{i}",
                     use_container_width=True):
            navigate_to("detail", ticker=ticker)
            st.rerun()


def render_unified_top_picks(manual_picks: list[dict] | None):
    """금일 추천 종목 — 단일 통합 섹션 (큐레이션 우대 폐지).

    데이터 소스 (모두 동등 평가, Alpha Score 로 정렬):
        1. 큐레이션 watchlist 의 manual_picks (build_daily_brief 결과)
        2. Promoted Candidate (DB)
        3. auto_curation 종목 (LLM Researched)

    사용자 요구 (2026-05-03): "내 큐레이션과 엔진이 고른 탑픽이 굳이 구분 안됐으면
    좋겠어. 큐레이션은 시드 예시일 뿐, 모든 종목이 공평하게 평가받았으면."

    Source chip (Manual Override / LLM Researched / Heuristic) 은 정보 표시만 —
    선정 우선순위에는 영향 없음.
    """
    pool: list[dict] = []
    seen: set[str] = set()

    # ── 1) Manual picks (큐레이션 + 시장 데이터로 Alpha Score 산출 가능한 rows) ──
    for r in (manual_picks or []):
        ticker = (r.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            continue

        # render_pick_card 와 같은 방식으로 Alpha Score 계산
        try:
            from src.alpha_score import calculate_alpha_score, reconcile_with_action_tag
            from src.earnings_quality import build_earnings_quality
            from src.bottleneck import build_bottleneck_thesis

            md = r.get("market_data") or {}
            _eq = build_earnings_quality(ticker, r)
            _bn_meta = {
                "ticker": ticker,
                "name": r.get("name_en") or r.get("name_ko") or "",
                "sector": r.get("sector"),
                "industry": r.get("industry"),
            }
            _bn = build_bottleneck_thesis(ticker, _bn_meta, md)
            _alpha = calculate_alpha_score(
                ticker=ticker, market_data=md, scores=r.get("scores"),
                earnings_quality=_eq, bottleneck_thesis=_bn,
                news_agg=r.get("news_agg"), curated_events=r.get("curated_events"),
            )
            tag = r.get("action_tag", "Watchlist")
            _alpha = reconcile_with_action_tag(_alpha, tag, too_crowded=(tag == "Too Crowded"))
            score = _alpha.get("alpha_score")
            if score is None:
                continue
            seen.add(ticker)
            pool.append({
                "ticker": ticker, "row": r,
                "alpha_score": score,
                "alpha_rating_en": _alpha.get("alpha_rating_en", ""),
                "data_confidence": _alpha.get("data_confidence", ""),
                "_source": "manual_pick",
            })
        except Exception:
            continue

    # ── 2) Promoted Candidate + auto_curation (DB 에서) ──
    try:
        from src import database as _db
        from src.curated import is_manually_curated as _is_manual

        with _db.db_session() as conn:
            promoted = [
                dict(r) for r in _db.fetch_promotion_candidates(
                    conn, promoted_only=True, limit=20,
                )
            ]
            try:
                auto_rows = [
                    dict(r) for r in _db.fetch_all_auto_curation(conn, limit=50)
                ]
            except Exception:
                auto_rows = []

            db_candidates: list[dict] = []
            for p in promoted:
                t = (p.get("ticker") or "").upper()
                if not t or t in seen:
                    continue
                seen.add(t)
                db_candidates.append({
                    "ticker": t, "name": p.get("name") or t,
                    "queue_type": p.get("queue_type"),
                    "_source": "promoted",
                })
            for ac in auto_rows:
                t = (ac.get("ticker") or "").upper()
                if not t or t in seen:
                    continue
                seen.add(t)
                db_candidates.append({
                    "ticker": t, "name": t, "queue_type": None,
                    "_source": "auto_curation",
                })

            for c in db_candidates:
                t = c["ticker"]
                row = _db.fetch_stock_research(conn, t)
                if not row:
                    continue
                try:
                    alpha = json.loads(row["alpha_score_json"] or "{}")
                except Exception:
                    alpha = {}
                score = alpha.get("alpha_score")
                if score is None:
                    continue
                pool.append({
                    "ticker": t, "name": c.get("name"),
                    "queue_type": c.get("queue_type"),
                    "alpha_score": score,
                    "alpha_rating_en": alpha.get("alpha_rating_en", ""),
                    "alpha_rating_ko": alpha.get("alpha_rating_ko", ""),
                    "data_confidence": alpha.get("data_confidence", "Low"),
                    "easy_explanation": (
                        row["easy_explanation"] or row["core_thesis"] or ""
                    )[:200],
                    "_source": c["_source"],
                })
    except Exception as e:
        log.warning(f"unified picks DB fetch 실패: {e}")

    # ── Alpha Score 로 정렬 + 상위 7 ──
    pool.sort(key=lambda x: x.get("alpha_score") or 0, reverse=True)
    top = pool[:7]

    if not top:
        st.markdown(
            '<div class="section-title">금일 추천 종목</div>'
            '<div class="card">금일 명확히 부각되는 후보가 부족합니다. '
            '데이터 업데이트 후 다시 확인하세요.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="section-title">금일 추천 종목'
        '<span style="font-size:13px; color:var(--muted); margin-left:8px;">'
        '— Alpha Score 상위 (큐레이션 / LLM / Heuristic 동등 평가)'
        "</span></div>",
        unsafe_allow_html=True,
    )

    for i, p in enumerate(top):
        # manual_pick 은 full row 가 있어서 render_pick_card 로 렌더 가능
        if p["_source"] == "manual_pick" and p.get("row"):
            render_pick_card(p["row"], i, key_prefix="brief_unified")
        else:
            # DB-only 후보 — 간소 카드
            _render_db_pick_card(p, i)


def _render_db_pick_card(p: dict, idx: int) -> None:
    """DB 에서 가져온 picks (Promoted / auto_curation) 의 간소 카드.

    full row data 없이 alpha_score + easy_explanation + chips 만으로 구성.
    상세 보기 버튼으로 종목 상세 페이지 이동.
    """
    ticker = p["ticker"]
    name = p.get("name") or ticker
    score = p["alpha_score"]
    rating_en = p.get("alpha_rating_en", "")
    confidence = p.get("data_confidence", "")
    easy = p.get("easy_explanation", "")
    queue = p.get("queue_type")
    source = p.get("_source", "")

    chip_color = {
        "LLM Researched": "#93C5FD",
        "Heuristic": "#FBBF24",
        "Manual Override": "#C4B5FD",
        "Low": "#F87171",
    }.get(confidence, "#9CA3AF")
    score_color = _alpha_score_color(score)

    source_label = "Promoted Candidate" if source == "promoted" else "Auto-Curation"
    source_chip = (
        f'<span class="chip chip-needs-check" style="font-size:11px;">{source_label}</span>'
    )
    if queue:
        source_chip += (
            f'<span class="chip chip-needs-check" style="font-size:11px; margin-left:4px;">'
            f'{queue}</span>'
        )

    body = (
        '<div class="card" style="border-left:3px solid #6B7280;">'
        '<div style="display:flex; justify-content:space-between; align-items:flex-start; '
        'gap:12px; margin-bottom:8px; flex-wrap:wrap;">'
        f'<div><div style="font-size:16px; font-weight:600;">{name} ({ticker})</div>'
        f'<div style="margin-top:4px;">{source_chip}</div></div>'
        '<div style="text-align:right; flex-shrink:0;">'
        f'<div style="font-size:24px; font-weight:700; color:{score_color}; line-height:1.1;">'
        f'{score:.0f}<span style="font-size:13px; color:var(--muted); font-weight:500;">/100</span>'
        "</div>"
        f'<div style="font-size:12px; color:{score_color}; font-weight:600;">{rating_en}</div>'
        f'<div style="font-size:11px; color:{chip_color}; margin-top:2px;">{confidence}</div>'
        "</div>"
        "</div>"
    )
    if easy:
        body += (
            '<div style="font-size:13px; line-height:1.55; color:var(--text); '
            'margin-top:6px;">'
            f'{easy}'
            "</div>"
        )
    body += "</div>"
    st.markdown(body, unsafe_allow_html=True)

    if st.button(
        f"{ticker} 상세 보기",
        key=f"unified_{ticker}_{idx}",
        use_container_width=True,
    ):
        navigate_to("detail", ticker=ticker)
        st.rerun()


def render_outsider_top_picks():
    """Outsider Top picks — 큐레이션 외 발굴 종목 중 Alpha Score 상위 카드.

    데이터 소스 우선순위 (Echo Chamber 방지):
        1. Promoted Candidate (DB) — Discovery 엔진이 wide universe 에서 승격한 종목
        2. auto_curation 테이블 — LLM 자동 큐레이션 받은 종목 (큐레이션 42 종목 제외)
        3. 위 두 풀에서 stock_research.alpha_score 상위 N 개

    형우의 큐레이션 watchlist 는 의도적으로 제외 — 매몰 방지.
    매일 다른 종목이 등장 가능 — 진짜 alpha 발굴 엔진의 의미가 살아남.
    """
    try:
        from src import database as _db
        from src.curated import is_manually_curated as _is_manual

        with _db.db_session() as conn:
            # 1) Promoted Candidate (Discovery → Promotion 통과)
            promoted = [
                dict(r) for r in _db.fetch_promotion_candidates(
                    conn, promoted_only=True, limit=20,
                )
            ]
            # 2) auto_curation 종목 — manual 제외
            try:
                auto_rows = [
                    dict(r) for r in _db.fetch_all_auto_curation(conn, limit=50)
                ]
            except Exception:
                auto_rows = []

            # 후보 합치기 (중복 제거 — promoted 우선)
            seen: set[str] = set()
            candidates: list[dict] = []
            for p in promoted:
                t = (p.get("ticker") or "").upper()
                if not t or t in seen or _is_manual(t):
                    continue
                seen.add(t)
                candidates.append({
                    "ticker": t, "name": p.get("name") or t,
                    "queue_type": p.get("queue_type"),
                    "promotion_score": p.get("promotion_score"),
                    "thesis_impact": p.get("thesis_impact"),
                    "reason": p.get("reason"),
                    "_source": "promoted",
                })
            for ac in auto_rows:
                t = (ac.get("ticker") or "").upper()
                if not t or t in seen or _is_manual(t):
                    continue
                seen.add(t)
                candidates.append({
                    "ticker": t, "name": t, "queue_type": None,
                    "_source": "auto_curation",
                })

            # 각 후보에 대해 stock_research 의 alpha_score 가져오기
            scored: list[dict] = []
            for c in candidates:
                t = c["ticker"]
                row = _db.fetch_stock_research(conn, t)
                if not row:
                    continue
                try:
                    alpha = json.loads(row["alpha_score_json"] or "{}")
                except Exception:
                    alpha = {}
                score = alpha.get("alpha_score")
                if score is None:
                    continue
                # Provisional / Low confidence 도 포함하되 점수 조회만
                c["alpha_score"] = score
                c["alpha_rating_en"] = alpha.get("alpha_rating_en", "")
                c["alpha_rating_ko"] = alpha.get("alpha_rating_ko", "")
                c["data_confidence"] = alpha.get("data_confidence", "Low")
                # 핵심 thesis 1줄 — easy_explanation / core_thesis 우선
                c["easy_explanation"] = (
                    row["easy_explanation"] or row["core_thesis"] or ""
                )[:200]
                scored.append(c)

            # alpha_score 상위로 정렬 + 상위 5
            scored.sort(key=lambda x: x["alpha_score"], reverse=True)
            top = scored[:5]

    except Exception as e:
        log.warning(f"render_outsider_top_picks 실패: {e}")
        return

    if not top:
        # 데이터 부족 — 안내 표시만
        st.markdown(
            '<div class="section-title">Outsider Top picks'
            '<span style="font-size:13px; color:var(--muted); margin-left:8px;">'
            '— 큐레이션 외 발굴 종목 중 Alpha Score 상위'
            "</span></div>"
            '<div class="card" style="background:#3A2E0A; border-color:#6B541E;">'
            '아직 Promoted Candidate / auto_curation 데이터가 충분하지 않습니다. '
            'GitHub Actions 의 다음 run 이후 (또는 며칠 누적 뒤) 등장하기 시작합니다.'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="section-title">Outsider Top picks'
        '<span style="font-size:13px; color:var(--muted); margin-left:8px;">'
        '— 큐레이션 외 발굴 종목 중 Alpha Score 상위 (Echo Chamber 방지)'
        "</span></div>"
        '<div class="info-row check" style="background:#2E1F52; border-left-color:#5B21B6; '
        'color:#C4B5FD; margin-bottom:8px;">'
        '※ 형우의 큐레이션 watchlist 외 종목 중 Discovery + LLM 자동 큐레이션 기반으로 '
        'Alpha Score 상위만 노출. 매일 변동될 수 있으며, 익숙하지 않은 종목이 보이는 게 정상입니다.'
        "</div>",
        unsafe_allow_html=True,
    )

    for i, c in enumerate(top):
        ticker = c["ticker"]
        name = c.get("name") or ticker
        score = c["alpha_score"]
        rating_en = c.get("alpha_rating_en", "")
        confidence = c.get("data_confidence", "")
        easy = c.get("easy_explanation", "")
        queue = c.get("queue_type")
        source = c.get("_source", "")

        # confidence chip color
        chip_color = {
            "LLM Researched": "#93C5FD",
            "Heuristic": "#FBBF24",
            "Manual Override": "#C4B5FD",
            "Low": "#F87171",
        }.get(confidence, "#9CA3AF")
        score_color = _alpha_score_color(score)

        source_label = "Promoted Candidate" if source == "promoted" else "Auto-Curation"
        source_chip = (
            f'<span class="chip chip-needs-check" style="font-size:11px;">{source_label}</span>'
        )
        if queue:
            source_chip += (
                f'<span class="chip chip-needs-check" style="font-size:11px; margin-left:4px;">'
                f'{queue}</span>'
            )

        body = (
            '<div class="card" style="border-left:3px solid #5B21B6;">'
            '<div style="display:flex; justify-content:space-between; align-items:flex-start; '
            'gap:12px; margin-bottom:8px; flex-wrap:wrap;">'
            f'<div><div style="font-size:16px; font-weight:600;">{name} ({ticker})</div>'
            f'<div style="margin-top:4px;">{source_chip}</div></div>'
            f'<div style="text-align:right; flex-shrink:0;">'
            f'<div style="font-size:24px; font-weight:700; color:{score_color}; line-height:1.1;">'
            f'{score:.0f}<span style="font-size:13px; color:var(--muted); font-weight:500;">/100</span>'
            "</div>"
            f'<div style="font-size:12px; color:{score_color}; font-weight:600;">{rating_en}</div>'
            f'<div style="font-size:11px; color:{chip_color}; margin-top:2px;">{confidence}</div>'
            "</div>"
            "</div>"
        )
        if easy:
            body += (
                '<div style="font-size:13px; line-height:1.55; color:var(--text); '
                'margin-top:6px;">'
                f'{easy}'
                "</div>"
            )
        body += "</div>"
        st.markdown(body, unsafe_allow_html=True)

        # 상세 보기 버튼
        if st.button(
            f"{ticker} 상세 보기",
            key=f"outsider_{ticker}_{i}",
            use_container_width=True,
        ):
            navigate_to("detail", ticker=ticker)
            st.rerun()


def render_brief_discovery_section():
    """Daily Brief 안의 신규 발굴 후보 섹션.

    Promoted Candidate 가 있으면 그것을 표시. 없으면 Wide Scan 통과 후
    Discovery Candidate 큐별 상위 후보를 "예비 발굴 후보" 로 표시.
    """
    data = _fetch_discovery_data()
    promoted = data.get("promoted") or []
    queues = data.get("queues") or {}

    queue_counts = {q: len(items) for q, items in queues.items()}
    total_disc = sum(queue_counts.values())

    st.markdown('<div class="section-title">금일 신규 발굴 후보</div>', unsafe_allow_html=True)
    if total_disc == 0 and not promoted:
        st.markdown(
            '<div class="card">'
            '아직 Discovery 데이터가 없습니다. <code>python3 run_research.py</code> 를 실행해 wide universe 스캔을 수행하세요.'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    # 큐 요약 (5 개 큐)
    summary_html = (
        '<div class="card">'
        '<div class="para-row">'
        '<span class="para-label">큐별 후보 수</span>'
        '<span class="para-text">'
        f'Quality Dislocation {queue_counts.get("Quality Dislocation", 0)} · '
        f'Earnings Revision {queue_counts.get("Earnings Revision", 0)} · '
        f'Unusual Volume {queue_counts.get("Unusual Volume", 0)} · '
        f'Civilization Alpha {queue_counts.get("Civilization Alpha", 0)} · '
        f'<b>Bottleneck Supplier {queue_counts.get("Bottleneck Supplier", 0)}</b>'
        "</span></div>"
        "</div>"
    )
    st.markdown(summary_html, unsafe_allow_html=True)

    if promoted:
        # ── Promoted Candidate (Deep Dive 권장) ──
        st.markdown(
            '<div class="info-row check" style="margin-bottom:8px;">'
            'Promoted Candidate — Deep Dive 권장 후보'
            "</div>",
            unsafe_allow_html=True,
        )
        for i, c in enumerate(promoted[:5]):
            _render_discovery_card(c, i, key_prefix="brief_disc")
        return

    # ── Promoted Candidate 없음 — Discovery Candidate 예비 후보 표시 ──
    st.markdown(
        '<div class="info-row alert" style="margin-bottom:8px;">'
        'Promoted Candidate 없음 — 아래는 <b>정밀 검토 전 예비 후보</b> 입니다 '
        '(Wide Scan 정량 시그널만 통과한 Discovery Candidate). '
        '추천이 아니며, Discovery 페이지에서 큐별 전체 후보를 확인하실 수 있습니다.'
        "</div>",
        unsafe_allow_html=True,
    )

    # 큐별 상위 후보 모아서 카드로 표시
    preliminary: list[dict] = []
    seen: set[str] = set()
    queue_order = [
        "Quality Dislocation",
        "Bottleneck Supplier",  # 사용자가 가장 보고 싶어하는 큐 — 우선 노출
        "Earnings Revision",
        "Civilization Alpha",
        "Unusual Volume",
    ]
    # 1차 — 각 큐 1등씩
    for q in queue_order:
        for it in (queues.get(q) or [])[:5]:
            t = it.get("ticker")
            if not t or t in seen:
                continue
            seen.add(t)
            preliminary.append({
                "ticker": t,
                "name": it.get("ticker"),  # _render_discovery_card 가 wide_universe 매핑
                "queue_type": q,
                "reason": it.get("signal_summary"),
                "discovery_score": it.get("score"),
                "thesis_impact": "정밀 검토 전 (Wide Scan 시그널만 통과)",
                "action_recommendation": "Promotion 미통과 — 후속 뉴스 / 이벤트 확인 후 Deep Dive 검토",
            })
            break  # 큐당 1개만
    # 2차 — 부족하면 큐별 2등으로 보충 (총 5개 목표)
    for q in queue_order:
        if len(preliminary) >= 5:
            break
        for it in (queues.get(q) or [])[1:5]:
            t = it.get("ticker")
            if not t or t in seen:
                continue
            seen.add(t)
            preliminary.append({
                "ticker": t,
                "name": it.get("ticker"),
                "queue_type": q,
                "reason": it.get("signal_summary"),
                "discovery_score": it.get("score"),
                "thesis_impact": "정밀 검토 전 (Wide Scan 시그널만 통과)",
                "action_recommendation": "Promotion 미통과 — 후속 뉴스 / 이벤트 확인 후 Deep Dive 검토",
            })
            if len(preliminary) >= 5:
                break

    if preliminary:
        for i, c in enumerate(preliminary):
            _render_discovery_card(c, i, key_prefix="brief_prelim")
    else:
        st.markdown(
            '<div class="card">큐별 후보 수는 있으나 표시할 항목이 없습니다.</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Validation Lab — Phase 4-A 백테스트 기반
# ---------------------------------------------------------------------------

_VL_CONF_CLASS = {
    "High": "tag-research-now",
    "Med": "tag-watchlist",
    "Low": "tag-need-thesis-check",
    "computed": "tag-watchlist",
    "Sample Limited": "tag-need-thesis-check",
    "Data Unavailable": "tag-data-unavailable",
}


def _vl_conf_tag(conf: Any) -> str:
    """confidence 값을 색 태그 HTML 로."""
    if conf is None:
        return ""
    label = str(conf)
    if label == "High":
        label = "신뢰도 High"
    elif label == "Med":
        label = "신뢰도 Med"
    elif label == "Low":
        label = "Sample Limited / 신뢰도 Low"
    cls = _VL_CONF_CLASS.get(str(conf), "tag-watchlist")
    return f'<span class="tag {cls}">{label}</span>'


def _vl_pct(v: Any, digits: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:+.{digits}f}%"
    except Exception:
        return "—"


def _vl_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "—"


def _vl_empty_card(msg: str):
    st.markdown(
        '<div class="card">'
        f'<div class="pick-type">{msg}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_validation_lab():
    """Validation Lab — 백테스트 기반 과거 검증 페이지 (Phase 4-A)."""
    import pandas as _pd

    render_back_button("validation")
    page_header(
        "Validation Lab",
        meta="과거 검증 · Overheat/Regime Forward Return · Drawdown Deployment Backtest",
    )

    st.markdown(
        '<div class="env-block" style="min-height:auto;">'
        '<div class="env-block-title">백테스트 기반 — 과거 검증</div>'
        '<div class="env-block-body">'
        '이 페이지는 과거 시장 데이터에 Overheat Score · Regime 분류 · Drawdown '
        'Deployment 룰을 재적용한 <b>과거 검증</b> 결과입니다. 미래 수익을 보장하지 '
        '않으며, 표본이 부족한 항목은 신뢰도를 낮게 표기합니다. 레버리지 ETF'
        '(QLD/TQQQ)는 변동성 끌림(decay)과 깊은 MDD 위험이 있습니다.'
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ── 데이터 로드 ─────────────────────────────────────────────────
    try:
        with db.db_session() as conn:
            bt_results = [dict(r) for r in db.fetch_backtest_results(conn)]
            rfr_rows = [dict(r) for r in db.fetch_regime_forward_returns(conn)]
            price_tickers = db.fetch_price_history_tickers(conn)
    except Exception as e:
        st.markdown(
            f'<div class="card">백테스트 데이터 조회 중 오류: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    if not bt_results and not rfr_rows:
        _vl_empty_card(
            "백테스트 데이터 없음 — 파이프라인 실행이 필요합니다. "
            "run_research 가 시장 일봉을 수집하고 백테스트를 계산하면 "
            "여기에 Overheat/Regime Forward Return · Drawdown Deployment "
            "결과가 표시됩니다."
        )
        if price_tickers:
            st.caption(f"수집된 일봉 티커: {', '.join(price_tickers)}")
        return

    # ── 1) Overheat Score Backtest — Regime Forward Returns 에서 파생 ──
    # regime_forward_returns 에는 overheat_score 가 함께 저장돼 있어
    # 구간별로 재집계한다.
    st.markdown('<div class="section-title first">1 · Overheat Score Backtest</div>',
                unsafe_allow_html=True)
    if not rfr_rows:
        _vl_empty_card("Overheat 백테스트 데이터 없음 — 파이프라인 실행 필요.")
    else:
        bands = [(0, 30, "0-30 정상"), (30, 50, "30-50 주의"),
                 (50, 70, "50-70 과열경계"), (70, 85, "70-85 과열"),
                 (85, 101, "85-100 FOMO")]
        band_table: list[dict] = []
        for lo, hi, name in bands:
            for asset in ("QQQ", "QLD"):
                sel = [r for r in rfr_rows
                       if r.get("asset") == asset
                       and r.get("overheat_score") is not None
                       and lo <= r["overheat_score"] < hi]
                if not sel:
                    continue
                def _avg(key):
                    vals = [r[key] for r in sel if r.get(key) is not None]
                    return sum(vals) / len(vals) if vals else None
                def _wr(key):
                    vals = [r[key] for r in sel if r.get(key) is not None]
                    return (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
                n = len(sel)
                band_table.append({
                    "Overheat 구간": name,
                    "자산": asset,
                    "표본": n,
                    "1M 평균": _vl_pct(_avg("forward_1m")),
                    "3M 평균": _vl_pct(_avg("forward_3m")),
                    "6M 평균": _vl_pct(_avg("forward_6m")),
                    "3M 승률": _vl_pct(_wr("forward_3m"), 0),
                    "평균 MDD(3M)": _vl_pct(_avg("mdd_3m")),
                    "신뢰도": ("High" if n >= 60 else "Med" if n >= 20
                              else "Sample Limited"),
                })
        if band_table:
            st.dataframe(_pd.DataFrame(band_table), use_container_width=True,
                         hide_index=True)
            st.caption("과거 각 날짜의 Overheat Score(technical extension 기반 "
                       "재구성)를 구간으로 묶어 forward 수익률·MDD 를 집계.")
        else:
            _vl_empty_card("Overheat 구간별 표본이 부족합니다 (Sample Limited).")

    # ── 2) Regime Forward Returns ───────────────────────────────────
    st.markdown('<div class="section-title">2 · Regime Forward Returns</div>',
                unsafe_allow_html=True)
    if not rfr_rows:
        _vl_empty_card("Regime 백테스트 데이터 없음 — 파이프라인 실행 필요.")
    else:
        regime_table: list[dict] = []
        regimes_seen = sorted({r.get("regime") for r in rfr_rows if r.get("regime")})
        for regime in regimes_seen:
            for asset in ("QQQ", "QLD", "TQQQ"):
                sel = [r for r in rfr_rows
                       if r.get("regime") == regime and r.get("asset") == asset]
                if not sel:
                    continue
                def _avg2(key):
                    vals = [r[key] for r in sel if r.get(key) is not None]
                    return sum(vals) / len(vals) if vals else None
                def _wr2(key):
                    vals = [r[key] for r in sel if r.get(key) is not None]
                    return (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
                def _worst(key):
                    vals = [r[key] for r in sel if r.get(key) is not None]
                    return min(vals) if vals else None
                n = len(sel)
                regime_table.append({
                    "Regime": regime,
                    "자산": asset,
                    "표본": n,
                    "1M 평균": _vl_pct(_avg2("forward_1m")),
                    "3M 평균": _vl_pct(_avg2("forward_3m")),
                    "6M 평균": _vl_pct(_avg2("forward_6m")),
                    "12M 평균": _vl_pct(_avg2("forward_12m")),
                    "3M 승률": _vl_pct(_wr2("forward_3m"), 0),
                    "최악 MDD(3M)": _vl_pct(_worst("mdd_3m")),
                    "신뢰도": ("High" if n >= 60 else "Med" if n >= 20
                              else "Sample Limited"),
                })
        if regime_table:
            st.dataframe(_pd.DataFrame(regime_table), use_container_width=True,
                         hide_index=True)
            st.caption("각 regime 발생일 기준 SPY/QQQ/QLD/TQQQ 의 forward "
                       "수익률·MDD. TQQQ 는 MDD 를 반드시 함께 확인하세요.")
        else:
            _vl_empty_card("Regime 별 표본이 부족합니다 (Sample Limited).")

    # ── 3) Nasdaq Drawdown Deployment Backtest ──────────────────────
    st.markdown('<div class="section-title">3 · Nasdaq Drawdown Deployment '
                'Backtest</div>', unsafe_allow_html=True)
    deploy = [r for r in bt_results
              if (r.get("strategy_name") or "").startswith(
                  ("Buy&Hold", "Drawdown Deployment", "현금대기", "적립식"))]
    if not deploy:
        _vl_empty_card("Drawdown Deployment 백테스트 데이터 없음 — "
                       "파이프라인 실행 필요.")
    else:
        dep_table = []
        for r in deploy:
            dep_table.append({
                "전략": r.get("strategy_name"),
                "자산": r.get("asset"),
                "기간": f"{r.get('start_date') or '—'} ~ {r.get('end_date') or '—'}",
                "Total Return": _vl_pct(r.get("total_return"), 0),
                "CAGR": _vl_pct(r.get("cagr")),
                "MaxDD": _vl_pct(r.get("max_drawdown"), 0),
                "Sharpe": _vl_num(r.get("sharpe")),
                "Sortino": _vl_num(r.get("sortino")),
                "Calmar": _vl_num(r.get("calmar")),
                "회복(영업일)": (str(int(r["recovery_time"]))
                              if r.get("recovery_time") is not None
                              else "미회복"),
            })
        st.dataframe(_pd.DataFrame(dep_table), use_container_width=True,
                     hide_index=True)
        st.markdown(
            '<div class="card" style="border-color:var(--amber);">'
            '<div class="pick-type">⚠ 레버리지 경고 — QLD(2x)/TQQQ(3x)는 일간 '
            '리밸런싱 구조로 횡보장에서 변동성 끌림(decay)이 발생하며, 약세장에서 '
            'MDD 가 매우 깊고 회복에 오래 걸립니다. 위 Buy&Hold TQQQ 의 MaxDD·'
            '회복 영업일을 반드시 확인하세요. 거래비용·세금은 미반영입니다.'
            "</div></div>",
            unsafe_allow_html=True,
        )

    # ── 4) Parking Strategy Backtest ────────────────────────────────
    st.markdown('<div class="section-title">4 · Parking Strategy Backtest</div>',
                unsafe_allow_html=True)
    parking = [r for r in bt_results
               if (r.get("strategy_name") or "") == "Parking Buy&Hold"]
    if not parking:
        _vl_empty_card("Parking 백테스트 데이터 없음 — 파이프라인 실행 필요.")
    else:
        park_table = []
        for r in parking:
            details = db.load_json(r.get("details_json")) or {}
            park_table.append({
                "Parking 종목": r.get("asset"),
                "Total Return": _vl_pct(r.get("total_return"), 0),
                "CAGR": _vl_pct(r.get("cagr")),
                "MaxDD": _vl_pct(r.get("max_drawdown"), 0),
                "Sharpe": _vl_num(r.get("sharpe")),
                "신뢰도": details.get("confidence") or "—",
            })
        st.dataframe(_pd.DataFrame(park_table), use_container_width=True,
                     hide_index=True)
        st.caption("비싼 국면의 방어적 파킹 후보(MCD·KO·COST 등) Buy&Hold 성과 — "
                   "QQQ 와 비교해 변동성·MDD 가 낮은지 확인하는 용도입니다.")

    # ── 5) Profit Protection Backtest ───────────────────────────────
    st.markdown('<div class="section-title">5 · Profit Protection Backtest</div>',
                unsafe_allow_html=True)
    pp_rows = [r for r in bt_results
               if (r.get("strategy_name") or "").startswith("ProfitProtection")]
    if not pp_rows:
        _vl_empty_card("Profit Protection 백테스트 데이터 없음 — "
                       "파이프라인 실행 필요.")
    else:
        pp_table = []
        for r in pp_rows:
            name = r.get("strategy_name")
            label = ("익절 룰 적용" if "with-rule" in name
                     else "룰 없음(QLD Buy&Hold)")
            pp_table.append({
                "케이스": label,
                "Total Return": _vl_pct(r.get("total_return"), 0),
                "CAGR": _vl_pct(r.get("cagr")),
                "MaxDD": _vl_pct(r.get("max_drawdown"), 0),
                "Sharpe": _vl_num(r.get("sharpe")),
                "Calmar": _vl_num(r.get("calmar")),
            })
        st.dataframe(_pd.DataFrame(pp_table), use_container_width=True,
                     hide_index=True)
        st.caption("고베타(QLD) 보유 중 Overheat 85+ 진입 시 QQQ 로 비중을 옮기는 "
                   "익절 룰이 과거에 MDD 를 줄였는지 검증.")

    _render_market_cycle_sections(_pd)


def _render_market_cycle_sections(_pd):
    """Validation Lab 안의 Market Cycle 섹션 (Stage A).

    장기 시장 history 에서 추출한 실증 base rate — 연간 조정 빈도, 낙폭·회복
    기간, 상승장 분석, 신고가 근접 매수 성과, 추세 상태별 성과, 현재 시장
    위치 vs 과거. 모든 표에 sample_count·신뢰도 표기.
    """
    st.markdown(
        '<div class="env-block" style="min-height:auto;margin-top:24px;">'
        '<div class="env-block-title">Market Cycle Research Engine — Stage A</div>'
        '<div class="env-block-body">'
        '아래는 사용자 룰을 검증하는 것이 아니라, 장기 시장 history(QQQ ~1999, '
        'SPY ~1993) 로부터 시장 자체의 <b>실증 base rate</b> 를 추출한 결과입니다. '
        '상승·하락 양쪽 조건을 모두 다룹니다. 1999년 이후 독립적인 대형 사이클은 '
        '4~6개뿐 — 깊은 낙폭 통계는 표본이 작으니 <b>표본 수</b>를 반드시 함께 '
        '보십시오. 데이터가 "신고가 근처 매수도 나쁘지 않았다" 고 하면 그대로 '
        '표기합니다 (편향 강화 없음).'
        "</div></div>",
        unsafe_allow_html=True,
    )

    try:
        with db.db_session() as conn:
            mc_cycles = [dict(r) for r in db.fetch_market_cycles(conn, "QQQ")]
            spy_cycles = [dict(r) for r in db.fetch_market_cycles(conn, "SPY")]
            mc_annual = [dict(r) for r in db.fetch_annual_correction_stats(conn, "QQQ")]
            mc_runs = [dict(r) for r in db.fetch_bull_run_stats(conn, "QQQ")]
            mc_ath = [dict(r) for r in db.fetch_ath_forward_returns(conn, "QQQ")]
    except Exception as e:
        st.markdown(
            f'<div class="card">시장 사이클 데이터 조회 중 오류: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    _empty_note = "데이터 누적 중 — 파이프라인 실행 필요 (run_research 가 Stage A 분석을 계산)."

    # ── 6) 연간 조정 빈도 ───────────────────────────────────────────
    st.markdown('<div class="section-title">6 · 연간 조정 빈도 (QQQ)</div>',
                unsafe_allow_html=True)
    if not mc_annual:
        _vl_empty_card(_empty_note)
    else:
        ann_table = []
        for r in sorted(mc_annual, key=lambda x: x.get("year") or 0):
            ann_table.append({
                "연도": r.get("year"),
                "-3% 조정": r.get("correction_3pct_count"),
                "-5% 조정": r.get("correction_5pct_count"),
                "-10% 조정": r.get("correction_10pct_count"),
                "-15% 조정": r.get("correction_15pct_count"),
                "-20% 조정": r.get("correction_20pct_count"),
                "연 최대낙폭": _vl_pct(r.get("max_drawdown"), 0),
                "연 수익률": _vl_pct(r.get("annual_return"), 0),
            })
        st.dataframe(_pd.DataFrame(ann_table), use_container_width=True,
                     hide_index=True)
        n_years = len(mc_annual)
        m10 = (sum(r.get("correction_10pct_count") or 0 for r in mc_annual)
               / n_years) if n_years else 0
        st.caption(
            f"조정 *이벤트* 수 (cluster-aware — 한 하락 다리가 -5/-10/-15% 를 "
            f"통과해도 1건). 전체 {n_years}개 연도, 연평균 -10% 조정 {m10:.2f}건. "
            "표본이 연 단위라 적으니 추세 참고용으로만 보십시오.")

    # ── 7) 낙폭·회복 기간 ────────────────────────────────────────────
    st.markdown('<div class="section-title">7 · 낙폭·회복 기간 (QQQ '
                'drawdown cycle)</div>', unsafe_allow_html=True)
    if not mc_cycles:
        _vl_empty_card(_empty_note)
    else:
        cyc_table = []
        for r in sorted(mc_cycles, key=lambda x: x.get("drawdown_depth") or 0):
            cyc_table.append({
                "고점일": r.get("peak_date"),
                "저점일": r.get("trough_date"),
                "낙폭": _vl_pct(r.get("drawdown_depth"), 1),
                "유형": r.get("cycle_type"),
                "고점→저점(영업일)": r.get("days_peak_to_trough"),
                "저점→회복(영업일)": (r.get("days_trough_to_recovery")
                                  if r.get("days_trough_to_recovery") is not None
                                  else "미회복"),
                "회복일": r.get("recovery_date") or "—",
                "저점후 3M": _vl_pct(r.get("forward_return_3m_from_trough")),
            })
        deep = [r for r in mc_cycles if (r.get("drawdown_depth") or 0) <= -0.10]
        st.dataframe(_pd.DataFrame(cyc_table[:30]), use_container_width=True,
                     hide_index=True)
        st.caption(
            f"전체 -3%+ 조정 이벤트 {len(mc_cycles)}건 중 -10%+ 는 {len(deep)}건. "
            "낙폭 깊은 순 상위 30건 표시. -20%+ 대형 약세장은 표본이 4~6개로 "
            "극히 작아 회복 기간 통계의 신뢰도가 낮습니다 (과거≠미래).")
        if spy_cycles:
            st.caption(f"참고 — SPY 는 {len(spy_cycles)}건의 -3%+ 조정 이벤트.")

    # ── 8) 상승장(Bull Run) 분석 ────────────────────────────────────
    st.markdown('<div class="section-title">8 · 상승장(Bull Run) 분석 (QQQ)</div>',
                unsafe_allow_html=True)
    if not mc_runs:
        _vl_empty_card(_empty_note)
    else:
        run_table = []
        for r in sorted(mc_runs, key=lambda x: x.get("start_date") or ""):
            run_table.append({
                "시작": r.get("start_date"),
                "종료": r.get("end_date"),
                "기간(영업일)": r.get("duration_days"),
                "총 수익률": _vl_pct(r.get("total_return"), 0),
                "구간 내 최악 눌림": _vl_pct(r.get("max_pullback"), 0),
                "종료 사유": r.get("end_reason"),
            })
        st.dataframe(_pd.DataFrame(run_table), use_container_width=True,
                     hide_index=True)
        durs = [r.get("duration_days") for r in mc_runs
                if r.get("duration_days") is not None]
        avg_dur = sum(durs) / len(durs) if durs else 0
        st.caption(
            f"상승장 정의 — 직전 고점 회복일부터 다음 -10% 조정의 직전 고점까지. "
            f"표본 {len(mc_runs)}개, 평균 길이 {avg_dur:.0f}영업일. "
            "표본이 작으니(사이클 4~6개) 평균은 참고용입니다.")

    # ── 9) 신고가 근접 매수 성과 ────────────────────────────────────
    st.markdown('<div class="section-title">9 · 신고가 근접 매수 성과 (QQQ)</div>',
                unsafe_allow_html=True)
    if not mc_ath:
        _vl_empty_card(_empty_note)
    else:
        _order = {"전고점(ATH)": 0, "52주 신고가": 1, "52주 고점 -0~3%": 2,
                  "52주 고점 -3~5%": 3, "52주 고점 -5~10%": 4}
        ath_table = []
        for r in sorted(mc_ath, key=lambda x: _order.get(
                x.get("ath_proximity_bucket"), 9)):
            n = r.get("sample_count") or 0
            ath_table.append({
                "ATH 근접도": r.get("ath_proximity_bucket"),
                "표본": n,
                "1M 평균": _vl_pct(r.get("forward_1m")),
                "3M 평균": _vl_pct(r.get("forward_3m")),
                "6M 평균": _vl_pct(r.get("forward_6m")),
                "12M 평균": _vl_pct(r.get("forward_12m")),
                "3M 승률": _vl_pct(r.get("win_rate"), 0),
                "평균 MDD(3M)": _vl_pct(r.get("mdd_3m")),
                "신뢰도": ("High" if n >= 60 else "Med" if n >= 20
                          else "Sample Limited"),
            })
        st.dataframe(_pd.DataFrame(ath_table), use_container_width=True,
                     hide_index=True)
        st.caption(
            "이 표는 '신고가 근처 매수는 나쁘다' 는 직관을 직접 검증합니다. "
            "전고점(ATH) 행의 forward return 이 다른 행보다 낮지 않다면, "
            "데이터상 신고가 매수가 불리한 진입이 아니었다는 뜻입니다 — "
            "데이터가 말하는 그대로 받아들이십시오.")

    # ── 10) 추세 상태별 성과 ─────────────────────────────────────────
    st.markdown('<div class="section-title">10 · 추세 상태별 성과 (QQQ)</div>',
                unsafe_allow_html=True)
    try:
        with db.db_session() as conn:
            from src.market_cycle_analyzer import (
                _load_series, calculate_forward_returns_by_trend_state,
            )
            _c, _d = _load_series(conn, "QQQ")
        trend_res = (calculate_forward_returns_by_trend_state(_c, _d)
                     if _c else {"by_state": {}})
    except Exception as e:
        trend_res = {"by_state": {}}
        log.debug("trend state 계산 실패: %s", e)
    by_state = trend_res.get("by_state") or {}
    if not by_state:
        _vl_empty_card(_empty_note)
    else:
        ts_table = []
        for state, s in by_state.items():
            n = s.get("sample_count") or 0
            ts_table.append({
                "추세 상태": state,
                "표본(일)": n,
                "1M 평균": _vl_pct((s.get("1m") or {}).get("avg")),
                "3M 평균": _vl_pct((s.get("3m") or {}).get("avg")),
                "6M 평균": _vl_pct((s.get("6m") or {}).get("avg")),
                "3M 승률": _vl_pct((s.get("3m") or {}).get("win_rate"), 0),
                "평균 MDD(3M)": _vl_pct((s.get("mdd_3m") or {}).get("avg")),
                "신뢰도": s.get("confidence") or "—",
            })
        st.dataframe(_pd.DataFrame(ts_table), use_container_width=True,
                     hide_index=True)
        st.caption("20/60/200일선·200DMA 기울기·이격·RSI·고점대비 낙폭으로 "
                   "분류한 추세 상태별 forward return. 표본은 일 단위라 자기상관이 "
                   "있어 독립 표본 수는 더 적습니다.")

    # ── 11) 현재 시장 위치 vs 과거 ──────────────────────────────────
    st.markdown('<div class="section-title">11 · 현재 시장 위치 vs 과거</div>',
                unsafe_allow_html=True)
    try:
        with db.db_session() as conn:
            from src.market_cycle_analyzer import locate_current_market
            cur = locate_current_market(conn, "QQQ")
    except Exception as e:
        cur = {"verdict_ko": f"현재 위치 분석 실패: {e}"}
    f3 = cur.get("similar_forward_3m")
    n = cur.get("similar_sample_count") or 0
    st.markdown(
        '<div class="card">'
        f'<div class="pick-type">QQQ 현재 위치</div>'
        f'<div class="env-block-body" style="margin-top:8px;">'
        f'전고점 대비 낙폭 <b>{_vl_pct(cur.get("drawdown_pct"), 1)}</b> · '
        f'추세 상태 <b>{cur.get("trend_state") or "—"}</b> · '
        f'ATH 근접 버킷 <b>{cur.get("ath_bucket") or "—"}</b><br>'
        f'과거 유사 구간 {n}개 — 3개월 평균 '
        f'<b>{_vl_pct(f3) if f3 is not None else "표본 부족"}</b> · '
        f'배치 힌트: {cur.get("deploy_zone_hint") or "—"}<br>'
        f'<span style="color:var(--text-dim);">{cur.get("verdict_ko")}</span>'
        "</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="card" style="border-color:var(--amber);margin-top:8px;">'
        '<div class="pick-type">⚠ Stage A 정직성 주의 — 과거 ≠ 미래. '
        '대형 사이클 표본 4~6개로 깊은 낙폭 통계 신뢰도 낮음. '
        'running ATH·52주 고점은 사후 계산(look-ahead 주의). '
        '레버리지 ETF(QLD/TQQQ)는 변동성 끌림·경로 의존성 위험. '
        '자동 룰 발굴은 과적합 함정으로 의도적으로 제외했습니다.'
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ── 12) 데이터 사다리 — 낙폭 버킷별 실증 forward return ─────────────
    st.markdown(
        '<div class="section-title">12 · 데이터 사다리 — 낙폭 버킷별 실증 '
        'forward return</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "QQQ 52주 rolling 고점 대비 낙폭 버킷마다 QQQ / QLD / TQQQ 의 3개월(63영업일) "
        "forward return·승률·표본수를 실증 집계합니다. 하드코딩 단계매수 사다리가 "
        "아니라, 데이터 자체가 말하는 자산별 우월 구간을 그대로 노출합니다 — "
        "예) TQQQ 의 sweet spot 은 데이터상 -10~-15% 구간입니다.")
    try:
        with db.db_session() as conn:
            etb_rows = [dict(r) for r in db.fetch_entry_timing_buckets(
                conn, base_asset="QQQ")]
    except Exception as e:
        etb_rows = []
        log.debug("entry_timing_buckets fetch 실패: %s", e)

    # 3M(63d) 만 표 형태로 노출 — 1M/6M 은 별도 expander
    bucket_order = [
        "0~-2%", "-2~-5%", "-5~-10%", "-10~-15%",
        "-15~-20%", "-20~-25%", "-25~-35%", "-35%+",
    ]
    rows_3m = [r for r in etb_rows if int(r.get("window_days") or 0) == 63]
    if not rows_3m:
        _vl_empty_card(
            "데이터 누적 중 — 데이터 사다리 통계가 아직 계산되지 않았습니다. "
            "월초 파이프라인 실행 시 entry_timing_buckets 가 채워지면 표시됩니다.")
    else:
        # bucket × asset 매핑
        by_b: dict[str, dict[str, dict]] = {}
        for r in rows_3m:
            b = r.get("bucket_label") or ""
            a = r.get("target_asset") or ""
            by_b.setdefault(b, {})[a] = r
        etb_table = []
        for b in bucket_order:
            row_map = by_b.get(b) or {}
            qqq = row_map.get("QQQ") or {}
            qld = row_map.get("QLD") or {}
            tqq = row_map.get("TQQQ") or {}
            etb_table.append({
                "낙폭 버킷": b,
                "표본(QQQ)": qqq.get("sample_count") or 0,
                "QQQ 3M 평균": _vl_pct(qqq.get("avg_return")),
                "QQQ 승률": _vl_pct(qqq.get("win_rate"), 0),
                "QLD 3M 평균": _vl_pct(qld.get("avg_return")),
                "QLD 승률": _vl_pct(qld.get("win_rate"), 0),
                "QLD n": qld.get("sample_count") or 0,
                "TQQQ 3M 평균": _vl_pct(tqq.get("avg_return")),
                "TQQQ 승률": _vl_pct(tqq.get("win_rate"), 0),
                "TQQQ n": tqq.get("sample_count") or 0,
            })
        st.dataframe(_pd.DataFrame(etb_table), use_container_width=True,
                     hide_index=True)
        st.caption(
            "표본 수가 작은 깊은 낙폭(-20% 이하)은 신뢰도가 낮습니다. "
            "QLD 는 2006-06~, TQQQ 는 2010-02~ 이라 같은 버킷에서도 QQQ 표본 "
            "대비 자연스럽게 작습니다.")

        # 오늘의 진입 추천 한 줄
        try:
            with db.db_session() as conn:
                from src.market_cycle_analyzer import recommend_current_entry
                rec = recommend_current_entry(conn, "QQQ")
        except Exception as e:
            rec = {"rationale_ko": f"추천 계산 실패: {e}"}
        dd_now = rec.get("current_drawdown_pct")
        bucket_now = rec.get("current_bucket") or "—"
        best = rec.get("best_asset") or "—"
        verdict = rec.get("verdict") or "—"
        st.markdown(
            '<div class="card" style="margin-top:10px;">'
            '<div class="pick-type">데이터 사다리 — 오늘의 판단</div>'
            '<div class="env-block-body" style="margin-top:8px;">'
            f'QQQ 52주 낙폭 <b>{_vl_pct(dd_now, 1)}</b> · 버킷 <b>{bucket_now}</b> · '
            f'데이터상 best <b>{best}</b><br>'
            f'<b>판정: {verdict}</b><br>'
            f'<span style="color:var(--text-dim);">{rec.get("rationale_ko","")}</span>'
            "</div></div>",
            unsafe_allow_html=True,
        )

    # ── 13) 한국 시장 — 우량주 + KR 데이터 사다리 + KOSPI 사이클 ─────────
    _render_validation_lab_kr_section(_pd)


def _render_validation_lab_kr_section(_pd):
    """Validation Lab 의 한국 시장 region — 세 가지 sub-block.

    1) 한국 우량주 관찰 — kr_universe.csv 기반
    2) KR 데이터 사다리 — entry_timing_buckets (base_asset='069500')
    3) KOSPI 사이클 위치 — kospi_market_regime + recommend_current_entry

    데이터 없으면 차분한 "데이터 누적 중" 카드. 절대 raise 하지 않는다.
    """
    st.markdown(
        '<div class="section-title">13 · 한국 시장 — 우량주 + KR 데이터 '
        '사다리 + KOSPI 사이클</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Stage 2 KR 시장 확장의 산출물 — KOSPI 우량주 universe, KODEX 200 "
        "기반 데이터 사다리, KOSPI Overheat Score 와 KR 사이클 verdict."
    )

    # ── 13-1) 한국 우량주 관찰 ────────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="font-size:15px; margin-top:16px;">'
        '13-1 · 한국 우량주 관찰</div>',
        unsafe_allow_html=True,
    )
    try:
        from src.kr_universe import load_kr_universe
        kr_rows = load_kr_universe() or []
    except Exception as e:
        log.debug(f"kr_universe 로드 실패: {e}")
        kr_rows = []

    # CSV 헤더의 SKIPPED 필터 노트(있을 때) — 정직성
    kr_skipped_notes: list[str] = []
    try:
        from src.utils import DATA_DIR as _DATA_DIR
        kr_csv = _DATA_DIR / "kr_universe.csv"
        if kr_csv.exists():
            in_skipped = False
            with kr_csv.open("r", encoding="utf-8") as f:
                for line in f:
                    s = line.lstrip()
                    if not s.startswith("#"):
                        break
                    body = s.lstrip("#").strip()
                    if "SKIPPED" in body:
                        in_skipped = True
                        continue
                    if in_skipped and body and body.startswith("-"):
                        kr_skipped_notes.append(body.lstrip("- ").strip())
                    elif in_skipped and not body.startswith("-"):
                        in_skipped = False
    except Exception as e:
        log.debug(f"kr_universe header note 추출 실패: {e}")

    if not kr_rows:
        _vl_empty_card(
            "한국 우량주 universe 데이터 누적 중 — "
            "`python scripts/build_kr_universe.py` 실행 후 표시됩니다."
        )
    else:
        # 시총 내림차순 top 30
        sorted_rows = sorted(
            kr_rows,
            key=lambda r: (r.get("market_cap_krw") or 0),
            reverse=True,
        )
        top = sorted_rows[:30]
        extra_n = max(0, len(sorted_rows) - len(top))

        # 1Y price return — market_price_history 가 있으면 계산
        ret_by_ticker: dict[str, float | None] = {}
        try:
            with db.db_session() as conn:
                for r in top:
                    t = r["ticker"]
                    try:
                        prs = db.fetch_market_price_history(conn, t)
                    except Exception:
                        prs = []
                    if not prs:
                        ret_by_ticker[t] = None
                        continue
                    closes: list[float] = []
                    for pr in prs:
                        v = None
                        try:
                            keys = pr.keys() if hasattr(pr, "keys") else None
                        except Exception:
                            keys = None
                        if keys:
                            v = pr["adj_close"] if "adj_close" in keys else None
                            if v is None and "close" in keys:
                                v = pr["close"]
                        if v is None:
                            continue
                        try:
                            closes.append(float(v))
                        except (TypeError, ValueError):
                            continue
                    if len(closes) < 30:
                        ret_by_ticker[t] = None
                        continue
                    # 1Y ≈ 252 영업일, 부족하면 가용 첫 종가 대비
                    base_idx = max(0, len(closes) - 252)
                    base = closes[base_idx]
                    last = closes[-1]
                    ret_by_ticker[t] = (last / base - 1.0) if base > 0 else None
        except Exception as e:
            log.debug(f"kr 1Y return 계산 실패: {e}")

        def _fmt_mcap(v):
            try:
                if v is None:
                    return "—"
                v = float(v)
                if v >= 1e12:
                    return f"{v / 1e12:.1f}조"
                if v >= 1e8:
                    return f"{v / 1e8:.0f}억"
                return f"{v:,.0f}"
            except Exception:
                return "—"

        def _pct(v):
            if v is None:
                return "—"
            try:
                return f"{float(v) * 100:+.1f}%"
            except Exception:
                return "—"

        def _pct_simple(v):
            if v is None:
                return "—"
            try:
                # roe_5y_avg / debt_ratio 는 이미 분수 형태로 저장됨.
                return f"{float(v) * 100:.1f}%"
            except Exception:
                return "—"

        kr_table = []
        for r in top:
            kr_table.append({
                "이름": r.get("name_ko") or "—",
                "티커": r.get("ticker") or "—",
                "시총": _fmt_mcap(r.get("market_cap_krw")),
                "5Y ROE": _pct_simple(r.get("roe_5y_avg")),
                "부채비율": _pct_simple(r.get("debt_ratio")),
                "1Y 수익률": _pct(ret_by_ticker.get(r.get("ticker"))),
            })
        st.dataframe(
            _pd.DataFrame(kr_table), use_container_width=True, hide_index=True,
        )
        footer_bits: list[str] = []
        if extra_n > 0:
            footer_bits.append(f"외 {extra_n}개")
        if kr_skipped_notes:
            footer_bits.append("SKIPPED 필터: " + "; ".join(kr_skipped_notes))
        if footer_bits:
            st.caption(" · ".join(footer_bits))

    # ── 13-2) KR 데이터 사다리 ────────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="font-size:15px; margin-top:18px;">'
        '13-2 · KR 데이터 사다리 (069500 / 122630)</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "KODEX 200(069500) 52주 rolling 고점 대비 낙폭 버킷마다 "
        "KODEX 200 / KODEX 레버리지(122630) 의 3개월 forward return·승률·표본수."
    )
    try:
        with db.db_session() as conn:
            kr_etb_rows = [
                dict(r) for r in db.fetch_entry_timing_buckets(
                    conn, base_asset="069500")
            ]
    except Exception as e:
        kr_etb_rows = []
        log.debug(f"KR entry_timing_buckets fetch 실패: {e}")

    bucket_order_kr = [
        "0~-2%", "-2~-5%", "-5~-10%", "-10~-15%",
        "-15~-20%", "-20~-25%", "-25~-35%", "-35%+",
    ]
    kr_rows_3m = [r for r in kr_etb_rows if int(r.get("window_days") or 0) == 63]
    if not kr_rows_3m:
        _vl_empty_card(
            "데이터 누적 중 — KR 데이터 사다리 통계가 아직 계산되지 않았습니다. "
            "KR 일봉이 누적되면 entry_timing_buckets(base_asset='069500') 가 "
            "채워져 표시됩니다."
        )
    else:
        by_b_kr: dict[str, dict[str, dict]] = {}
        for r in kr_rows_3m:
            b = r.get("bucket_label") or ""
            a = r.get("target_asset") or ""
            by_b_kr.setdefault(b, {})[a] = r
        kr_table_etb = []
        for b in bucket_order_kr:
            row_map = by_b_kr.get(b) or {}
            kx = row_map.get("069500") or {}
            kl = row_map.get("122630") or {}
            kr_table_etb.append({
                "낙폭 버킷": b,
                "표본(KODEX 200)": kx.get("sample_count") or 0,
                "KODEX 200 3M 평균": _vl_pct(kx.get("avg_return")),
                "KODEX 200 승률": _vl_pct(kx.get("win_rate"), 0),
                "레버리지 3M 평균": _vl_pct(kl.get("avg_return")),
                "레버리지 승률": _vl_pct(kl.get("win_rate"), 0),
                "레버리지 n": kl.get("sample_count") or 0,
            })
        st.dataframe(
            _pd.DataFrame(kr_table_etb), use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "KR 레버리지 ETF (122630) 는 표본이 작아 깊은 낙폭 신뢰도가 "
            "낮습니다 — 데이터가 누적되면 자연스럽게 정밀해집니다."
        )

    # ── 13-3) KOSPI 사이클 위치 ──────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="font-size:15px; margin-top:18px;">'
        '13-3 · KOSPI 사이클 위치</div>',
        unsafe_allow_html=True,
    )
    try:
        with db.db_session() as conn:
            kr_reg = db.fetch_latest_kospi_market_regime(conn)
            try:
                from src.market_cycle_analyzer import recommend_current_entry
                kr_rec = recommend_current_entry(conn, base_asset="069500")
            except Exception as e:
                log.debug(f"KR recommend_current_entry 실패: {e}")
                kr_rec = None
    except Exception as e:
        log.debug(f"KR regime 조회 실패: {e}")
        kr_reg, kr_rec = None, None

    if kr_reg is None and (kr_rec is None or not kr_rec.get("available")):
        _vl_empty_card(
            "데이터 누적 중 — KOSPI Overheat Score 와 KR 사이클 verdict 가 "
            "아직 없습니다. 파이프라인이 KR 일봉을 누적하면 표시됩니다."
        )
    else:
        kr_overheat = _regime_row_get(kr_reg, "overheat_score") if kr_reg else None
        kr_band = (_regime_row_get(kr_reg, "band") or "—") if kr_reg else "—"
        kr_regime_label = (
            (_regime_row_get(kr_reg, "regime_ko") or "—") if kr_reg else "—"
        )
        kr_dd = (
            _regime_row_get(kr_reg, "kospi_drawdown_from_52w_high") if kr_reg else None
        )
        kr_commentary = (
            (_regime_row_get(kr_reg, "commentary_ko") or "").strip()
            if kr_reg else ""
        )
        rec_verdict = (kr_rec or {}).get("verdict") or "—"
        rec_best = (kr_rec or {}).get("best_asset") or "—"
        rec_bucket = (kr_rec or {}).get("current_bucket") or "—"
        rec_rationale = (kr_rec or {}).get("rationale_ko") or ""
        # similar_forward_3m 은 base recommend 의 evidence 안에 없을 수도 있다.
        # locate_current_market 의 결과가 더 정확하지만, recommend_current_entry
        # 의 evidence n 합산을 차분히 노출한다.
        ev_n_total = 0
        for ev in ((kr_rec or {}).get("evidence") or []):
            try:
                ev_n_total += int(ev.get("n") or 0)
            except Exception:
                continue

        oh_str = (
            f"{kr_overheat:.0f}/100" if kr_overheat is not None else "확인 필요"
        )
        dd_str = "—"
        if kr_dd is not None:
            try:
                ddf = float(kr_dd)
                if abs(ddf) <= 1:
                    ddf *= 100
                dd_str = f"{ddf:+.1f}%"
            except (TypeError, ValueError):
                dd_str = "—"

        st.markdown(
            '<div class="card">'
            '<div class="pick-type">KOSPI 200 — 오늘</div>'
            '<div class="env-block-body" style="margin-top:8px;">'
            f'52주 낙폭 <b>{dd_str}</b> · Overheat <b>{oh_str}</b> · '
            f'밴드 <b>{kr_band}</b> · 국면 <b>{kr_regime_label}</b><br>'
            f'KR 사이클 — 버킷 <b>{rec_bucket}</b> · 데이터상 best '
            f'<b>{rec_best}</b><br>'
            f'<b>판정: {rec_verdict}</b>'
            + (f' · 유사 표본 합계 n={ev_n_total}' if ev_n_total else '')
            + '</div></div>',
            unsafe_allow_html=True,
        )
        if kr_commentary:
            st.markdown(
                '<div class="card" style="margin-top:8px;">'
                f'<div class="pick-type">KOSPI 코멘트</div>'
                '<div class="env-block-body" style="margin-top:6px; '
                'color:var(--text-dim);">'
                f'{kr_commentary}</div></div>',
                unsafe_allow_html=True,
            )
        if rec_rationale:
            st.markdown(
                '<div class="card" style="margin-top:8px;">'
                f'<div class="pick-type">KR 사이클 rationale</div>'
                '<div class="env-block-body" style="margin-top:6px; '
                'color:var(--text-dim);">'
                f'{rec_rationale}</div></div>',
                unsafe_allow_html=True,
            )


def render_discovery():
    """전용 Discovery 페이지 — 큐별 + 승격 후보 + 필터."""
    render_back_button("discovery")
    page_header(
        "Discovery",
        meta="미국 상장주식 Wide Scan → Discovery Candidate / Promoted Candidate",
    )

    data = _fetch_discovery_data()
    if data.get("error"):
        st.error(f"Discovery 데이터 로드 실패: {data['error']}")
    queues = data.get("queues") or {}
    promoted = data.get("promoted") or []
    all_promo = data.get("all_promotion") or []

    has_any = any(queues.values()) or promoted or all_promo
    if not has_any:
        st.markdown(
            '<div class="card">'
            "Discovery 데이터가 비어 있습니다. <code>python3 run_research.py</code> 를 실행하면 "
            "wide universe (~300 종목) 를 스크리닝해 큐별 후보 + 승격 후보를 채웁니다."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    # 1) Promoted Candidate (Deep Dive 권장)
    st.markdown(
        '<div class="section-title">Promoted Candidate — Deep Dive 권장</div>',
        unsafe_allow_html=True,
    )
    if promoted:
        for i, c in enumerate(promoted):
            _render_discovery_card(c, i, key_prefix="disc_promoted")
    else:
        st.markdown(
            '<div class="card">아직 deep dive 까지 승격된 후보가 없습니다.</div>',
            unsafe_allow_html=True,
        )

    # 2) 필터 (큐 선택)
    st.markdown('<div class="section-title">Queue 별 후보</div>', unsafe_allow_html=True)
    queue_keys = list(queues.keys())
    sel = st.radio(
        "큐 선택",
        queue_keys + ["전체"],
        index=len(queue_keys),
        horizontal=True,
        key="discovery_queue_filter",
    )
    if sel == "전체":
        # 큐별 상위 3개씩
        for q in queue_keys:
            items = queues.get(q, [])
            if not items:
                continue
            st.markdown(f'<div class="section-title">{q}</div>', unsafe_allow_html=True)
            for i, it in enumerate(items[:5]):
                # discovery_scores 행은 키 이름이 다름
                normalized = {
                    "ticker": it.get("ticker"),
                    "name": it.get("ticker"),
                    "queue_type": q,
                    "reason": it.get("signal_summary"),
                    "discovery_score": it.get("score"),
                    "thesis_impact": "—",
                }
                _render_discovery_card(normalized, i, key_prefix=f"qall_{q}")
    else:
        items = queues.get(sel, [])
        if not items:
            st.markdown(
                f'<div class="card">{sel} 큐에 후보가 없습니다.</div>',
                unsafe_allow_html=True,
            )
        else:
            for i, it in enumerate(items[:20]):
                normalized = {
                    "ticker": it.get("ticker"),
                    "name": it.get("ticker"),
                    "queue_type": sel,
                    "reason": it.get("signal_summary"),
                    "discovery_score": it.get("score"),
                    "thesis_impact": "—",
                }
                _render_discovery_card(normalized, i, key_prefix=f"q_{sel}")


def render_dislocation():
    render_back_button("dislocation")
    page_header(
        "우량주 과매도",
        meta="Quality Dislocation · 52주 고점 대비 -20% ~ -55% 구간의 카테고리 리더",
    )
    render_data_health_warning()
    cands = list_dislocation(rows, limit=15)
    if not cands:
        st.markdown(
            '<div class="card">현재 우량주 과매도 후보가 부족합니다. 관심종목과 종목 상세에서 개별 점검을 권합니다.</div>',
            unsafe_allow_html=True,
        )
        return
    for i, r in enumerate(cands):
        render_dislocation_card(r, i)


# ---------------------------------------------------------------------------
# 화면: 관심종목
# ---------------------------------------------------------------------------

def render_watchlist():
    render_back_button("watchlist")
    page_header("관심종목", meta="사용자 등록 종목 우선 추적")
    render_data_health_warning()

    wl = load_watchlist()
    universe_map = {r["ticker"]: r for r in rows}

    cols = st.columns([5, 1])
    with cols[0]:
        labels = {r["ticker"]: display_name(r["name_ko"], r["ticker"]) for r in rows}
        candidates = [r["ticker"] for r in rows if r["ticker"] not in wl]
        add_ticker = st.selectbox(
            "투자 유니버스에서 종목 추가",
            options=candidates or [""],
            format_func=lambda t: labels.get(t, t),
            key="add_wl_select",
            label_visibility="collapsed",
        )
    with cols[1]:
        if st.button("편입", use_container_width=True, type="primary") and add_ticker:
            res = add_to_watchlist(add_ticker)
            if res.get("github"):
                st.toast(f"{labels.get(add_ticker, add_ticker)} 편입 ✓ (영구 저장)")
            elif res.get("github_status") == "no_pat":
                st.toast(f"{labels.get(add_ticker, add_ticker)} 편입 (임시 — PAT 미설정)")
            else:
                st.toast(f"{labels.get(add_ticker, add_ticker)} 편입 (영구 저장 실패)")
            st.rerun()

    st.markdown(
        '<div style="border-bottom:1px solid var(--line); margin: 14px 0 18px 0;"></div>',
        unsafe_allow_html=True,
    )

    if not wl:
        st.markdown(
            '<div class="card">아직 관심종목으로 편입된 종목이 없습니다.</div>',
            unsafe_allow_html=True,
        )
        return

    for i, t in enumerate(wl):
        r = universe_map.get(t)
        if r is None:
            st.markdown(
                f'<div class="card">{t} — 투자 유니버스 외 종목 (universe.csv 에 추가 필요)</div>',
                unsafe_allow_html=True,
            )
            continue
        render_pick_card(r, i, key_prefix="wl")
        if st.button(f"관심종목에서 제거 ({t})", key=f"rmwl_{t}"):
            res = remove_from_watchlist(t)
            if res.get("github"):
                st.toast(f"{t} 제거 ✓ (영구 저장)")
            else:
                st.toast(f"{t} 제거 (github: {res.get('github_status')})")
            st.rerun()


# ---------------------------------------------------------------------------
# 화면: Decision Journal (Phase 4-B)
# ---------------------------------------------------------------------------

# 액션별 한국어 라벨 + 뱃지 색상
_DJ_ACTION_LABELS: list[tuple[str, str]] = [
    ("BUY", "BUY 신규매수"),
    ("ADD", "ADD 추가매수"),
    ("TRIM", "TRIM 일부매도"),
    ("SELL", "SELL 전량매도"),
    ("HOLD", "HOLD 보유유지"),
    ("SKIP", "SKIP 매수보류"),
    ("WATCH", "WATCH 관망"),
]
_DJ_ACTION_LABEL_MAP = {k: v for k, v in _DJ_ACTION_LABELS}

# 액션 뱃지 색 — 매수 계열 greenish / 매도·축소 reddish / 보류·관망 neutral
_DJ_ACTION_BADGE: dict[str, tuple[str, str]] = {
    "BUY": ("#14352A", "#86EFAC"),
    "ADD": ("#14352A", "#86EFAC"),
    "HOLD": ("#1E3A4A", "#93C5FD"),
    "TRIM": ("#4A2A1F", "#FCA5A5"),
    "SELL": ("#4A1F1F", "#FCA5A5"),
    "SKIP": ("#2A2A33", "#9CA3AF"),
    "WATCH": ("#2A2A33", "#9CA3AF"),
}

# 채점 등급 칩 색
_DJ_GRADE_CHIP: dict[str, tuple[str, str]] = {
    "좋은 결정": ("#14352A", "#86EFAC"),
    "중립": ("#4A3A12", "#FCD34D"),
    "아쉬운 결정": ("#4A1F1F", "#FCA5A5"),
    "채점 보류": ("#26262E", "#9CA3AF"),
}


def _dj_badge(action: str) -> str:
    bg, fg = _DJ_ACTION_BADGE.get(action, ("#2A2A33", "#9CA3AF"))
    label = _DJ_ACTION_LABEL_MAP.get(action, action or "—")
    return (f'<span class="tag" style="background:{bg};color:{fg};'
            f'border-color:{bg};">{label}</span>')


def _dj_grade_chip(grade: str) -> str:
    bg, fg = _DJ_GRADE_CHIP.get(grade, ("#26262E", "#9CA3AF"))
    return (f'<span class="tag" style="background:{bg};color:{fg};'
            f'border-color:{bg};">{grade}</span>')


def render_decision_journal():
    """Decision Journal — 사용자 투자 의사결정 기록 + 1·3·6개월 사후 채점 (Phase 4-B)."""
    from src import decision_journal as dj

    render_back_button("journal")
    page_header(
        "Decision Journal",
        meta="투자 의사결정 기록 · 1·3·6개월 사후 채점",
    )

    st.markdown(
        '<div class="env-block" style="min-height:auto;">'
        '<div class="env-block-title">의사결정 기록 — 정직한 사후 채점</div>'
        '<div class="env-block-body">'
        '매수·매도·보류 등 실제 내린 결정을 그 근거와 함께 기록하면, 엔진이 '
        '1·3·6개월 뒤 QQQ 대비 성과로 사후 채점합니다. 등급은 좋은 결정·중립·'
        '아쉬운 결정의 거친 버킷일 뿐이며, 미래 수익을 예측하지 않습니다. '
        '내 결정이 실제로 가치를 더했는지를 시간이 지난 뒤 정직하게 비춰보기 위한 '
        '기록장입니다.'
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ── 데이터 로드 ─────────────────────────────────────────────────
    try:
        decisions = dj.load_decisions()
    except Exception as e:
        decisions = []
        st.markdown(
            f'<div class="card">결정 기록을 불러오는 중 오류: {e} — 확인 필요</div>',
            unsafe_allow_html=True,
        )

    grades_by_id: dict[str, list] = {}
    try:
        with db.db_session() as conn:
            grades_by_id = db.fetch_all_decision_grades_map(conn)
    except Exception as e:
        log.warning("decision_grades 조회 실패: %s", e)
        grades_by_id = {}

    # ── 스코어보드 요약 ─────────────────────────────────────────────
    try:
        summary = dj.summarize_decisions(decisions, grades_by_id)
    except Exception:
        summary = {"total": len(decisions), "graded": 0, "n_good": 0,
                   "n_neutral": 0, "n_poor": 0, "hit_rate": None}

    st.markdown('<div class="section-title first">스코어보드</div>',
                unsafe_allow_html=True)
    sc = st.columns(4)
    _dj_metrics = [
        ("총 기록", str(summary["total"])),
        ("채점 완료", str(summary["graded"])),
        ("좋은 결정", str(summary["n_good"])),
        ("아쉬운 결정", str(summary["n_poor"])),
    ]
    for col, (label, value) in zip(sc, _dj_metrics):
        with col:
            st.markdown(
                '<div class="metric-card">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value" style="font-size:24px;">{value}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
    if summary["graded"] == 0:
        st.caption("채점 완료된 결정 없음 — 첫 결정 후 1개월 뒤 채점됩니다.")
    elif summary.get("hit_rate") is not None:
        st.caption(
            f"적중률 {summary['hit_rate'] * 100:.0f}% "
            f"(좋은 결정 {summary['n_good']} / 우열 있는 결정 "
            f"{summary['n_good'] + summary['n_poor']} · 중립 "
            f"{summary['n_neutral']} 제외) — 표본이 적을수록 신뢰도는 낮습니다."
        )

    # ── 결정 기록 폼 ────────────────────────────────────────────────
    st.markdown('<div class="section-title">새 결정 기록</div>',
                unsafe_allow_html=True)
    with st.form("dj_add_form", clear_on_submit=True):
        fc = st.columns([2, 3])
        with fc[0]:
            f_ticker = st.text_input("티커", key="dj_ticker",
                                     placeholder="예: NVDA")
        with fc[1]:
            f_action = st.selectbox(
                "결정 유형",
                options=[k for k, _ in _DJ_ACTION_LABELS],
                format_func=lambda k: _DJ_ACTION_LABEL_MAP.get(k, k),
                key="dj_action",
            )
        f_conviction = st.selectbox(
            "확신도", options=["High", "Med", "Low"], index=1, key="dj_conviction",
        )
        f_rationale = st.text_area(
            "결정 근거", key="dj_rationale",
            placeholder="이 결정을 내린 이유를 적어주세요. 시간이 지난 뒤 채점과 함께 돌아봅니다.",
        )
        submitted = st.form_submit_button("결정 기록", type="primary")

    if submitted:
        ticker_clean = (f_ticker or "").upper().strip()
        if not ticker_clean:
            st.toast("티커를 입력해 주세요.")
        else:
            # 현재 시장 국면 캡처
            regime_val = None
            overheat_val = None
            try:
                with db.db_session() as conn:
                    mr = db.fetch_latest_market_regime(conn)
                if mr is not None:
                    try:
                        regime_val = mr["current_regime"]
                    except Exception:
                        regime_val = None
                    try:
                        overheat_val = mr["market_overheat_score"]
                    except Exception:
                        overheat_val = None
            except Exception as e:
                log.warning("market_regime 캡처 실패: %s", e)

            entry = {
                "decision_date": _dt.date.today().isoformat(),
                "ticker": ticker_clean,
                "name": None,
                "action": f_action,
                "conviction": f_conviction,
                "rationale": (f_rationale or "").strip(),
                "regime_at_decision": regime_val,
                "overheat_at_decision": overheat_val,
            }
            try:
                res = dj.add_decision(entry)
                if res.get("github"):
                    st.toast(f"{ticker_clean} 결정 기록 ✓ (영구 저장)")
                elif res.get("github_status") == "no_pat":
                    st.toast(f"{ticker_clean} 결정 기록 (임시 — PAT 미설정)")
                else:
                    st.toast(f"{ticker_clean} 결정 기록 (영구 저장 실패 — "
                             f"{res.get('github_status')})")
            except Exception as e:
                st.toast(f"결정 기록 실패: {e}")
            st.rerun()

    # ── 결정 목록 ───────────────────────────────────────────────────
    st.markdown('<div class="section-title">기록된 결정</div>',
                unsafe_allow_html=True)

    if not decisions:
        st.markdown(
            '<div class="card">아직 기록된 결정이 없습니다. '
            '위 양식에서 첫 투자 의사결정을 기록해 보세요.</div>',
            unsafe_allow_html=True,
        )
        return

    # 최신순 — created_at 우선, 없으면 decision_date
    def _sort_key(d: dict):
        return (str(d.get("created_at") or ""), str(d.get("decision_date") or ""))

    decisions_sorted = sorted(decisions, key=_sort_key, reverse=True)
    today = _dt.date.today()
    milestones = [("1M", 30), ("3M", 91), ("6M", 182)]

    for d in decisions_sorted:
        did = str(d.get("id") or "")
        ticker = str(d.get("ticker") or "—")
        name = d.get("name") or ticker
        action = str(d.get("action") or "")
        conviction = d.get("conviction") or "—"
        decision_date = str(d.get("decision_date") or "—")
        rationale = (d.get("rationale") or "").strip() or "(근거 미기재)"
        regime = d.get("regime_at_decision")
        overheat = d.get("overheat_at_decision")

        regime_ctx = ""
        if regime:
            regime_ctx = f"국면 {regime}"
            if overheat is not None:
                try:
                    regime_ctx += f" · Overheat {float(overheat):.0f}"
                except (TypeError, ValueError):
                    pass
        else:
            regime_ctx = "국면 정보 없음"

        # 헤더 + 근거
        header = (
            '<div class="card">'
            '<div style="display:flex;justify-content:space-between;'
            'align-items:center;flex-wrap:wrap;gap:8px;">'
            f'<div class="pick-name" style="font-size:18px;">{name} '
            f'<span style="color:var(--text-mid);font-size:14px;">{ticker}</span></div>'
            f'<div>{_dj_badge(action)}</div>'
            "</div>"
            f'<div class="pick-type">{decision_date} · 확신도 {conviction} · '
            f'{regime_ctx}</div>'
            '<div class="para-row" style="margin-top:8px;">'
            '<span class="para-label">근거</span>'
            f'<span class="para-text">{rationale}</span>'
            "</div>"
        )

        # milestone 채점
        try:
            decision_dt = _dt.date.fromisoformat(decision_date)
        except Exception:
            decision_dt = None

        grade_rows = grades_by_id.get(did) or []
        grades_by_ms = {}
        for r in grade_rows:
            try:
                grades_by_ms[r["milestone"]] = r
            except Exception:
                continue

        ms_html_parts: list[str] = []
        for ms, ms_days in milestones:
            row = grades_by_ms.get(ms)
            if row is not None:
                grade = row["grade"] or "채점 보류"
                note = row["grade_note"] or ""
                ret = row["return_pct"]
                rel = row["relative_pct"]
                detail = ""
                if ret is not None and rel is not None:
                    detail = (f'<span style="color:var(--text-mid);'
                              f'font-size:12px;">수익률 {ret:+.1f}% · '
                              f'QQQ 대비 {rel:+.1f}%p</span>')
                ms_html_parts.append(
                    '<div style="padding:6px 0;border-top:1px solid var(--line);">'
                    f'<span style="color:var(--text-mid);font-size:12px;'
                    f'margin-right:8px;">{ms}</span>'
                    f'{_dj_grade_chip(grade)} {detail}'
                    f'<div style="color:var(--text-mid);font-size:12px;'
                    f'margin-top:4px;">{note}</div>'
                    "</div>"
                )
            else:
                # 채점 행 없음 — milestone 도래 여부로 분기
                if decision_dt is not None:
                    elapsed = (today - decision_dt).days
                    if elapsed < ms_days:
                        d_n = ms_days - elapsed
                        label = f"D-{d_n} · {ms} 채점 예정"
                    else:
                        label = f"{ms} 채점 대기 — 다음 파이프라인 실행 시"
                else:
                    label = f"{ms} 채점 대기 — 결정일 확인 필요"
                ms_html_parts.append(
                    '<div style="padding:6px 0;border-top:1px solid var(--line);'
                    'color:var(--text-mid);font-size:12px;">'
                    f'{label}</div>'
                )

        card_html = (
            header
            + '<div style="margin-top:10px;">'
            + "".join(ms_html_parts)
            + "</div></div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 화면: 회고 리포트
# ---------------------------------------------------------------------------

def render_news_detail():
    """뉴스 상세 페이지 — selected_news_id 기준."""
    render_back_button("news_detail")
    page_header("뉴스 상세", meta="원문 + 한국어 리서치 메모")

    nid = st.session_state.get("selected_news_id")
    ticker = st.session_state.get("selected_ticker")
    if not nid:
        st.markdown('<div class="card">선택된 뉴스가 없습니다.</div>', unsafe_allow_html=True)
        return

    # DB 우선 조회
    try:
        with db.db_session() as conn:
            row_db = db.fetch_news_by_id(conn, nid)
    except Exception:
        row_db = None

    if not row_db:
        # 메모리 rows 에서 fallback
        if rows and ticker:
            cur_row = next((r for r in rows if r["ticker"] == ticker), None)
            if cur_row:
                for n in cur_row.get("news") or []:
                    cand = db.make_news_id(
                        ticker, n.get("link"), n.get("title"), n.get("published_at")
                    )
                    if cand == nid:
                        row_db = type("Obj", (), {"__getitem__": lambda s, k: n.get(k)})()
                        break
    if not row_db:
        st.markdown(
            '<div class="card">해당 뉴스를 DB에서 찾을 수 없습니다. '
            'run_research.py 를 실행해 뉴스 요약을 갱신해 주세요.</div>',
            unsafe_allow_html=True,
        )
        return

    title = row_db["title"] or "(제목 없음)"
    source = row_db["source"] or "출처 확인 필요"
    published = row_db["published_at"] or "날짜 확인 필요"
    link = row_db["link"] or ""
    detailed = row_db["detailed_summary_ko"] or row_db["summary"] or "본문 요약이 제공되지 않습니다."
    implication = row_db["investment_implication_ko"] or "추가 정밀 검토 필요"
    impact = row_db["thesis_impact_ko"] or "확인 필요"
    confidence = row_db["confidence_level_ko"] or "Low"

    # Follow-up Items 파싱
    follow_ups: list[str] = []
    try:
        fu_raw = row_db["follow_up_items_ko"]
        if fu_raw:
            follow_ups = db.load_json(fu_raw, default=[]) or []
    except Exception:
        follow_ups = []

    # 종목명 표시
    universe_map = {r["ticker"]: r for r in (rows or [])}
    name_display = display_name(
        (universe_map.get(ticker) or {}).get("name_ko", "") if ticker else "",
        ticker or "",
    ) if ticker else "—"

    st.markdown(
        '<div class="card">'
        f'<div class="pick-name" style="font-size:20px;">{title}</div>'
        f'<div class="pick-type">관련 종목: {name_display} · {source} · {published} · Confidence {confidence}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="judgment-card">'
        '<div class="judgment-eyebrow">Summary</div>'
        f'<div class="judgment-body">{detailed}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="card">'
        '<div class="para-row">'
        '<span class="para-label">Key Thesis</span>'
        f'<span class="para-text">{implication}</span>'
        "</div>"
        '<div class="para-row">'
        '<span class="para-label">Thesis 영향</span>'
        f'<span class="para-text">{impact}</span>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if follow_ups:
        items_html = "".join(f"<li>{x}</li>" for x in follow_ups[:8])
        st.markdown(
            '<div class="card">'
            '<div class="section-title first">Follow-up Items</div>'
            f'<ul class="news-followups">{items_html}</ul>'
            "</div>",
            unsafe_allow_html=True,
        )

    if link:
        st.markdown(
            f'<a class="news-link" href="{link}" target="_blank" rel="noopener noreferrer">기사 원문 보기 →</a>',
            unsafe_allow_html=True,
        )


def render_retrospective():
    render_back_button("retro")
    page_header("회고 리포트", meta="DB performance_tracking + decision_log")

    # ── DB 기반 성과 요약 (신규) ──────────────────────────────────────
    try:
        with db.db_session() as conn:
            report = build_retrospective_report(conn)
    except Exception as e:
        report = None
        st.info(f"DB 조회 실패 (legacy CSV 기반으로 표시): {e}")

    if report and report["summary"]["total"] > 0:
        s = report["summary"]
        avg_ret = s.get("avg_return")
        avg_str = f"{avg_ret * 100:+.1f}%" if avg_ret is not None else "—"
        outcome_str = " · ".join(f"{k} {v}" for k, v in s["outcomes"].items())
        st.markdown(
            '<div class="card">'
            f'<div class="pick-name" style="font-size:18px;">결정 누적 {s["total"]}건</div>'
            f'<div class="pick-type">평균 수익률: {avg_str} · {outcome_str}</div>'
            "</div>",
            unsafe_allow_html=True,
        )

        if s.get("by_action_tag"):
            st.markdown('<div class="section-title">Action Tag별 성과</div>', unsafe_allow_html=True)
            tag_rows = []
            for tag, info in s["by_action_tag"].items():
                hr = info.get("hit_ratio")
                ar = info.get("avg_return")
                tag_rows.append({
                    "Action Tag": tag,
                    "건수": info["count"],
                    "맞음": info["wins"],
                    "틀림": info["losses"],
                    "Hit Ratio": f"{hr * 100:.0f}%" if hr is not None else "—",
                    "평균 수익률": f"{ar * 100:+.1f}%" if ar is not None else "—",
                })
            st.dataframe(pd.DataFrame(tag_rows), use_container_width=True)

        if report.get("missed_opportunities"):
            st.markdown('<div class="section-title">놓친 기회 (Watchlist 였는데 +20% ↑)</div>',
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(report["missed_opportunities"]),
                          use_container_width=True)
        if report.get("false_positives"):
            st.markdown('<div class="section-title">False Positive (Research Now 였는데 −10% ↓)</div>',
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(report["false_positives"]),
                          use_container_width=True)
        st.markdown('<div class="section-title">최근 결정 50건</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(report["decisions"][:50]), use_container_width=True)

    # ── Legacy CSV 기반 (하위 호환) ──────────────────────────────────
    if not DAILY_SNAPSHOTS_CSV.exists():
        return

    try:
        df = pd.read_csv(DAILY_SNAPSHOTS_CSV)
    except Exception as e:
        st.error(f"snapshots 로드 실패: {e}")
        return

    if df.empty:
        st.markdown('<div class="card">스냅샷이 비어있습니다.</div>', unsafe_allow_html=True)
        return

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    by_date = (
        df.groupby("date")
        .agg(
            snapshots=("ticker", "count"),
            avg_score=("final_score", "mean"),
            research_now=("action_tag", lambda s: int((s == "Research Now").sum())),
            quality_dis=("action_tag", lambda s: int((s == "Quality Dislocation").sum())),
            avoid=("action_tag", lambda s: int((s == "Avoid").sum())),
        )
        .reset_index()
    )

    st.markdown('<div class="section-title">기간별 요약</div>', unsafe_allow_html=True)
    st.dataframe(
        by_date.sort_values("date", ascending=False).rename(
            columns={
                "date": "기준일",
                "snapshots": "수집 종목",
                "avg_score": "평균 Final Score",
                "research_now": "Research Now",
                "quality_dis": "Quality Dislocation",
                "avoid": "Avoid",
            }
        ),
        use_container_width=True,
    )

    st.markdown(
        '<div class="section-title">최근 7일 종목별 투자 판단 변화</div>',
        unsafe_allow_html=True,
    )
    recent = df[df["date"] >= (df["date"].max() - pd.Timedelta(days=7))]
    if recent.empty:
        st.markdown('<div class="card">최근 7일 데이터가 부족합니다.</div>', unsafe_allow_html=True)
    else:
        pivot = recent.pivot_table(
            index="ticker", columns="date", values="action_tag", aggfunc="last"
        )
        st.dataframe(pivot, use_container_width=True)


# ---------------------------------------------------------------------------
# 라우팅
# ---------------------------------------------------------------------------

# ── 페이지 최상단 anchor + 모든 페이지 진입 직전 통합 scroll 처리 ─────────
# 사이드바 메뉴 / 상세 보기 / 우량주 과매도·관심종목·회고 리포트 카드의 모든
# 이동 경로가 navigate_to() 또는 selectbox 핸들러를 통해 scroll_to_top
# 플래그를 세우므로, 여기 한 곳에서 처리하면 모든 페이지에 일관 적용된다.
st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)
if st.session_state.get("scroll_to_top", False):
    scroll_to_top()
    st.session_state["scroll_to_top"] = False

if nav == "brief":
    render_today_brief()
elif nav == "regime":
    render_portfolio_regime()
elif nav == "validation":
    render_validation_lab()
elif nav == "journal":
    render_decision_journal()
elif nav == "discovery":
    render_discovery()
elif nav == "detail":
    render_stock_detail()
elif nav == "dislocation":
    render_dislocation()
elif nav == "watchlist":
    render_watchlist()
elif nav == "retro":
    render_retrospective()
elif nav == "news_detail":
    render_news_detail()
