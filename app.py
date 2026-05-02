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
from typing import Any

import pandas as pd
import streamlit as st

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
        --bg: #F8FAFC;
        --panel: #FFFFFF;
        --panel-soft: #F1F5F9;
        --line: #E2E8F0;
        --line-strong: #CBD5E1;
        --text: #0F172A;
        --text-mid: #334155;
        --muted: #64748B;
        --muted-soft: #94A3B8;
        --navy: #0F2A5F;
        --blue: #2563EB;
        --blue-soft: #DBEAFE;
        --blue-2: #0284C7;
        --green: #16A34A;
        --red: #DC2626;
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
        background: #FFFFFF;
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
        background: var(--navy);
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
        background: #FFFFFF;
        color: var(--text);
        border: 1px solid transparent;
        font-weight: 500;
        margin-bottom: 2px;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: var(--panel-soft);
        color: var(--navy);
        border-color: transparent;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #EFF4FB;             /* 옅은 블루 배경 */
        color: var(--navy);              /* 네이비 글씨 */
        border: 1px solid #CBDAF2;       /* 좌측 진한 보더 제거 */
        font-weight: 700;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
        background: #E2EBF7;
        color: var(--navy);
        border-color: #B6CAE9;
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
        background: #F1F5F9;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 20px 24px;
        font-size: 15px;
        line-height: 1.75;
        color: #334155;
        margin-top: 18px;
        margin-bottom: 32px;
        word-break: keep-all;
    }
    /* 차트 컨테이너 — 다음 요소와의 여백 */
    .chart-after { margin-bottom: 18px; }

    /* ---------- 금일 핵심 판단 카드 ---------- */
    .judgment-card {
        background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
        border: 1px solid var(--line);
        border-left: 4px solid var(--navy);
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
    /* 모두 "옅은 배경 + 진한 글씨" 패턴. 색 구분은 톤다운된 컬러칩으로. */
    .tag-research-now { background: #EFF4FB; color: var(--navy); border-color: #CBDAF2; }
    .tag-quality-dislocation { background: #E6F2FB; color: #075985; border-color: #BFDFF2; }
    .tag-watchlist { background: #F1F5F9; color: var(--text-mid); border-color: var(--line-strong); }
    .tag-wait-for-entry { background: #F4F1EA; color: #7C5A1E; border-color: #E5DABE; }
    .tag-too-crowded { background: #F1ECF8; color: #5B3F8A; border-color: #DCD0EC; }
    .tag-need-thesis-check { background: #F1F5F9; color: var(--text-mid); border-color: var(--line-strong); }
    .tag-avoid { background: #FBECEC; color: #8B1F1F; border-color: #F0CACA; }
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
        background: #F8FAFC;
        border: 1px solid #CBD5E1 !important;
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
    .scenario-bull { color: var(--green); background: #ECFDF5; border: 1px solid #BBF7D0; }
    .scenario-base { color: var(--navy); background: var(--blue-soft); border: 1px solid var(--blue-soft); }
    .scenario-bear { color: var(--red); background: #FEF2F2; border: 1px solid #FECACA; }
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
    .ret-positive { color: #16A34A; }
    .ret-negative { color: #DC2626; }
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
        color: #334155;
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
        color: #0F172A;
        white-space: nowrap;
        text-indent: 0;            /* 라벨 자체는 들여쓰기 영향 받지 않음 */
        vertical-align: top;
    }
    .para-text {
        color: #334155;
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
        color: #334155;
        word-break: keep-all;
        overflow-wrap: break-word;
        min-width: 0;
    }
    .para-row.kpts-row .kpts-line {
        padding: 5px 0;
        font-size: 15px;
        line-height: 1.75;
        color: #1F2937;
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
        color: #1F2937;
        padding: 3px 0;
    }
    @media (max-width: 640px) {
        .para-row.kpts-row {
            grid-template-columns: 1fr;
            row-gap: 4px;
        }
    }

    /* ---------- 이 회사는 쉽게 말해 ---------- */
    .simple-card {
        background: #FBFCFE;
        border: 1px solid var(--line);
        border-left: 3px solid var(--navy);
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
        background: #EFF4FB;
        color: var(--navy);
        border: 1px solid #CBDAF2;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 700;
        text-decoration: none;
    }
    .news-link:hover { background: #E2EBF7; text-decoration: none; }

    /* ---------- 가치평가 비교 요약 (section-summary alias) ---------- */
    .val-summary {
        background: #F1F5F9;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 18px 22px;
        font-size: 14px;
        line-height: 1.7;
        color: #334155;
        margin-top: 18px;
        margin-bottom: 8px;
        word-break: keep-all;
    }

    /* ---------- 재무 요약 row (가로 KV 바) ---------- */
    .fin-summary {
        display: flex;
        gap: 28px;
        flex-wrap: wrap;
        background: #F8FAFC;
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
    .status-진행 { background: #EFF4FB; color: var(--navy); border-color: #CBDAF2; }
    .status-종료 { background: #F1F5F9; color: var(--text-mid); border-color: var(--line-strong); }
    .status-완료 { background: #EDF6EE; color: #15803D; border-color: #C9E6CF; }
    .status-무산 { background: #F1F5F9; color: var(--text-mid); border-color: var(--line-strong); }
    .status-확인 { background: #F4F1EA; color: #7C5A1E; border-color: #E5DABE; }

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
    .chip-strengthen { background: #EDF6EE; color: #15803D; border-color: #C9E6CF; }
    .chip-weaken { background: #FBECEC; color: #8B1F1F; border-color: #F0CACA; }
    .chip-new-risk { background: #F4F1EA; color: #7C5A1E; border-color: #E5DABE; }
    .chip-noise { background: #F1F5F9; color: var(--text-mid); border-color: var(--line-strong); }
    .chip-needs-check { background: #EFF4FB; color: var(--navy); border-color: #CBDAF2; }

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
        background: #FFF7ED;
        border: 1px solid #FED7AA;
        border-radius: 10px;
        padding: 14px 16px;
        margin-bottom: 16px;
        font-size: 13.5px;
        color: var(--text);
    }
    .diagnose b { color: var(--navy); }
    .diagnose code {
        background: rgba(15,23,42,0.06);
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
        background: #EFF4FB;
        color: var(--navy);
        border-color: #B6CAE9;
        font-weight: 700;
    }
    .stButton > button[kind="primary"]:hover {
        background: #E2EBF7;
        color: var(--navy);
        border-color: #93B0DD;
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
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_build_rows(_token: int, fetch_news: bool = True) -> list[dict[str, Any]]:
    return build_rows(fetch_news=fetch_news)


@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_market_context(_token: int) -> tuple[dict[str, Any], str]:
    return fetch_market_context()


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
    """URL query params 동기화 — 브라우저 뒤로가기 보조."""
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


def get_refresh_token() -> int:
    return st.session_state.get("refresh_token", 0)


def trigger_refresh():
    st.session_state["refresh_token"] = get_refresh_token() + 1
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
    ("discovery", "Discovery"),
    ("detail", "종목 상세"),
    ("dislocation", "우량주 과매도"),
    ("watchlist", "관심종목"),
    ("retro", "회고 리포트"),
]
NAV_LABEL_BY_KEY = {k: l for k, l in NAV_ITEMS}

if "nav_key" not in st.session_state:
    # query_params 우선 → 없으면 기본
    try:
        qp = st.query_params
        st.session_state["nav_key"] = qp.get("page", "brief") or "brief"
        if qp.get("ticker"):
            st.session_state["selected_ticker"] = qp["ticker"]
        if qp.get("news_id"):
            st.session_state["selected_news_id"] = qp["news_id"]
    except Exception:
        st.session_state["nav_key"] = "brief"
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
with st.spinner("데이터 준비 중..."):
    try:
        rows = cached_build_rows(token, fetch_news=True)
        proxies, market_summary = cached_market_context(token)
    except Exception as e:
        rows = []
        proxies, market_summary = {}, "금일 시장 데이터 수집에 실패했습니다."
        load_error = str(e)

if load_error:
    st.error(f"데이터 로딩 실패: {load_error}")


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
                    import subprocess
                    import sys as _sys
                    with st.spinner("리서치 파이프라인 실행 중..."):
                        try:
                            result = subprocess.run(
                                [_sys.executable, str(PROJECT_ROOT / "run_research.py")],
                                capture_output=True, text=True, timeout=600,
                            )
                            if result.returncode == 0:
                                st.toast("DB 업데이트 완료. 화면을 갱신합니다.")
                            else:
                                st.error(
                                    f"파이프라인 실행 실패 (exit {result.returncode}). "
                                    f"마지막 출력:\n{result.stderr[-500:]}"
                                )
                        except subprocess.TimeoutExpired:
                            st.error("파이프라인 실행 타임아웃 (10분 초과).")
                        except Exception as e:
                            st.error(f"파이프라인 실행 오류: {e}")
                        trigger_refresh()
                    st.rerun()
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
        '<div style="margin-bottom:14px; padding:18px 22px; background:#F8FAFC; '
        'border:1px solid #E2E8F0; border-radius:12px;">'
        '<div style="font-size:12px; color:var(--muted); letter-spacing:1.4px; '
        'text-transform:uppercase; font-weight:700; margin-bottom:10px;">선택 기간 기준</div>'
        '<div style="display:flex; gap:42px; flex-wrap:wrap; '
        'font-variant-numeric:tabular-nums;">'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">최고가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#2563EB;">${high_price:,.2f}</span></div>'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">최저가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#64748B;">${low_price:,.2f}</span></div>'
        f'<div><span style="font-size:13px; color:var(--muted); margin-right:8px;">현재가</span>'
        f'<span style="font-size:17px; font-weight:700; color:#0F2A5F;">${current_price:,.2f}</span>'
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
                line=dict(color="#0F2A5F", width=2),
                fill="tozeroy",
                fillcolor="rgba(15,42,95,0.06)",
                name="Close",
                hovertemplate="%{x|%Y.%m.%d}<br>$%{y:,.2f}<extra></extra>",
            )
        )

        # 현재가 수평선 (점선, 연한 그레이)
        fig.add_hline(
            y=current_price,
            line=dict(color="#CBD5E1", dash="dot", width=1),
        )

        # 최고/최저/현재 마커 — 통일 색상 (빨강 제거)
        fig.add_trace(go.Scatter(
            x=[high_idx], y=[high_price], mode="markers",
            marker=dict(size=8, color="#2563EB", line=dict(color="#FFFFFF", width=2)),
            showlegend=False,
            hovertemplate=f"기간 내 최고가<br>${high_price:,.2f}<br>{_fmt_date(high_idx)}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[low_idx], y=[low_price], mode="markers",
            marker=dict(size=8, color="#64748B", line=dict(color="#FFFFFF", width=2)),
            showlegend=False,
            hovertemplate=f"기간 내 최저가<br>${low_price:,.2f}<br>{_fmt_date(low_idx)}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[current_idx], y=[current_price], mode="markers",
            marker=dict(size=9, color="#0F2A5F", line=dict(color="#FFFFFF", width=2)),
            showlegend=False,
            hovertemplate=f"현재가<br>${current_price:,.2f}<br>{_fmt_date(current_idx)}<extra></extra>",
        ))

        # ── 통일된 annotation (3종) ───────────────────────────────────────
        _ANN_COMMON = dict(
            showarrow=False,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#CBD5E1",
            borderwidth=1,
            borderpad=4,
            font=dict(family="-apple-system, sans-serif"),
        )
        fig.add_annotation(
            x=high_idx, y=high_price,
            text=f"최고 ${high_price:,.2f}",
            yshift=18,
            font=dict(size=12, color="#2563EB"),
            **{k: v for k, v in _ANN_COMMON.items() if k != "font"},
        )
        fig.add_annotation(
            x=low_idx, y=low_price,
            text=f"최저 ${low_price:,.2f}",
            yshift=-18,
            font=dict(size=12, color="#64748B"),
            **{k: v for k, v in _ANN_COMMON.items() if k != "font"},
        )
        fig.add_annotation(
            x=current_idx, y=current_price,
            text=f"현재 ${current_price:,.2f}",
            yshift=18, xshift=-12,
            font=dict(size=13, color="#0F2A5F"),
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
                        color="#2563EB",
                        line=dict(color="#FFFFFF", width=2),
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
            template="plotly_white",
            paper_bgcolor="#FFFFFF",
            plot_bgcolor="#FFFFFF",
            margin=dict(l=10, r=10, t=24, b=10),
            height=440,
            dragmode="zoom",
            xaxis=dict(
                showgrid=False,
                zeroline=False,
                showline=False,
                color="#475569",
                tickfont=dict(color="#475569", size=12),
                rangeslider=dict(visible=True, thickness=0.05, bgcolor="#F1F5F9"),
                type="date",
            ),
            yaxis=dict(
                showgrid=False,
                zeroline=False,
                showline=False,
                color="#475569",
                tickfont=dict(color="#475569", size=12),
            ),
            showlegend=False,
            hoverlabel=dict(
                bgcolor="#FFFFFF",
                bordercolor="#E2E8F0",
                font=dict(color="#0F172A", size=13),
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

    html = f"""
    <div class="pick">
      <div class="pick-head">
        <div>
          <div class="pick-name">{name}</div>
          <div class="pick-type">{inv_type}</div>
        </div>
        {render_tag(tag)}
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
            add_to_watchlist(row["ticker"])
            st.toast(f"{name} 관심종목에 편입했습니다.")


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

    # 금일 핵심 판단
    st.markdown(
        '<div class="judgment-card">'
        '<div class="judgment-eyebrow">금일 핵심 판단</div>'
        f'<div class="judgment-body">{brief["judgment"]}</div>'
        "</div>",
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

    # 금일 주요 매크로·정책·지정학 이슈
    macro = brief.get("macro_issues") or []
    if macro:
        st.markdown(
            '<div class="section-title">금일 주요 매크로·정책·지정학 이슈</div>',
            unsafe_allow_html=True,
        )
        for m in macro:
            st.markdown(
                '<div class="macro">'
                '<div class="macro-head">'
                f'<div class="macro-title">{m["title"]}</div>'
                f'<div class="macro-cat">{m["category"]}</div>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">시장 영향</span>'
                f'<span class="para-text">{m["impact"]}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">관련 섹터</span>'
                f'<span class="para-text">{m["sectors"]}</span>'
                "</div>"
                '<div class="para-row">'
                '<span class="para-label">투자적 해석</span>'
                f'<span class="para-text">{m["interpretation"]}</span>'
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # 금일 신규 발굴 후보 (Discovery — wide universe → 승격된 종목)
    render_brief_discovery_section()

    # 금일 주요 관찰 종목
    st.markdown('<div class="section-title">금일 주요 관찰 종목</div>', unsafe_allow_html=True)
    if brief["picks"]:
        for i, r in enumerate(brief["picks"]):
            render_pick_card(r, i, key_prefix="brief")
    else:
        st.markdown(
            '<div class="card">금일 명확히 부각되는 후보가 부족합니다. 관심종목과 우량주 과매도 메뉴를 함께 점검하세요.</div>',
            unsafe_allow_html=True,
        )

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


# ---------------------------------------------------------------------------
# 화면: 종목 상세 (Executive Summary)
# ---------------------------------------------------------------------------

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

    detail = build_stock_detail(row)

    # 헤더 블록 (종목명 / 투자 판단 / 종목 분류)
    st.markdown(
        '<div class="card">'
        '<div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">'
        f'<div><div class="pick-name" style="font-size:22px;">{detail["name_kr"]}</div>'
        f'<div class="pick-type">{detail["category"]} · {detail["investment_type"]}'
        f' · <b style="color:var(--navy);">분류: {detail.get("company_type","Structural Growth")}</b></div></div>'
        f'<div>{render_tag(detail["judgment_tag"])}</div>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

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
                    colors.append("#0F2A5F")
                if comp["industry_avg"] is not None:
                    labels.append("산업 평균")
                    values.append(comp["industry_avg"])
                    colors.append("#94A3B8")
                if comp["peer_avg"] is not None:
                    labels.append("Peer 평균")
                    values.append(comp["peer_avg"])
                    colors.append("#CBD5E1")

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
                            textfont=dict(size=12, color="#0F172A"),
                            hovertemplate="%{y}<br>%{x:.1f}배<extra></extra>",
                            width=0.35,
                        )
                    )
                    # x축 max를 약간 더 길게 잡아 라벨이 잘리지 않게
                    x_max = max(values) * 1.18 if values else 1
                    fig.update_layout(
                        template="plotly_white",
                        paper_bgcolor="#FFFFFF",
                        plot_bgcolor="#FFFFFF",
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
                            tickfont=dict(color="#475569", size=14),
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
                        marker=dict(color="#CBDAF2"),
                        hovertemplate="%{x} 매출액<br>%{y:.2f} " + unit + "<extra></extra>",
                    )
                )
                fig.add_trace(
                    go.Bar(
                        name="영업이익",
                        x=xs,
                        y=op_s,
                        marker=dict(color="#2563EB"),
                        hovertemplate="%{x} 영업이익<br>%{y:.2f} " + unit + "<extra></extra>",
                    )
                )
                fig.add_trace(
                    go.Bar(
                        name="당기순이익",
                        x=xs,
                        y=net_s,
                        marker=dict(color="#0F2A5F"),
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
                        line=dict(color="#CBD5E1", width=1),
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
                        color = "#94A3B8"
                    elif opm_v < 0:
                        text = f"OPM {opm_v * 100:.1f}%"
                        color = "#DC2626"
                    else:
                        text = f"OPM {opm_v * 100:.1f}%"
                        color = "#0F2A5F"

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
                        fillcolor="rgba(15,42,95,0.05)",
                        line_width=0,
                        layer="below",
                    )

                fig.update_layout(
                    barmode="group",
                    template="plotly_white",
                    paper_bgcolor="#FFFFFF",
                    plot_bgcolor="#FFFFFF",
                    height=420,
                    margin=dict(l=50, r=30, t=60, b=70),
                    bargap=0.30,
                    font=dict(size=13, color="#334155"),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=-0.22,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=14, color="#334155"),
                    ),
                    xaxis=dict(
                        showgrid=False,
                        zeroline=False,
                        showline=False,
                        color="#475569",
                        tickfont=dict(size=14, color="#475569"),
                    ),
                    yaxis=dict(
                        showgrid=False,            # ← 가로 gridline 완전 제거
                        zeroline=False,
                        showline=False,
                        color="#64748B",
                        range=[y_axis_min, y_axis_max],  # 음수 구간 포함, OPM 라벨 여유
                        tickfont=dict(size=13, color="#64748B"),
                        title=dict(
                            text=f"단위: {unit}",
                            font=dict(size=12, color="#94A3B8"),
                        ),
                    ),
                    hoverlabel=dict(
                        bgcolor="#FFFFFF",
                        bordercolor="#E2E8F0",
                        font=dict(color="#0F172A", size=13),
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
        for n in news_items:
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
                remove_from_watchlist(row["ticker"])
                st.toast("관심종목에서 제거했습니다.")
                st.rerun()
        else:
            if st.button("관심종목 편입", use_container_width=True):
                add_to_watchlist(row["ticker"])
                st.toast("관심종목에 편입했습니다.")
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
            ):
                items = [dict(r) for r in _db.fetch_discovery_scores(conn, queue_type=q, limit=20)]
                queues[q] = items
            promoted = [dict(r) for r in _db.fetch_promotion_candidates(conn, promoted_only=True, limit=20)]
            all_promotion = [dict(r) for r in _db.fetch_promotion_candidates(conn, promoted_only=False, limit=50)]
        return {"queues": queues, "promoted": promoted, "all_promotion": all_promotion}
    except Exception as e:
        return {"queues": {}, "promoted": [], "all_promotion": [], "error": str(e)}


def _render_discovery_card(c: dict, idx: int, *, key_prefix: str = "disc"):
    """Discovery / Promotion 후보 카드 — 종목명 + 큐 + 사유 + 핵심 지표 + 추천."""
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

    score_html = ""
    if promo_score is not None:
        score_html += f'<span class="chip chip-strengthen" style="margin-left:6px;">Promo {promo_score:.0f}</span>'
    if disc_score is not None:
        score_html += f'<span class="chip chip-needs-check" style="margin-left:6px;">Disc {disc_score:.0f}</span>'

    body = (
        '<div class="card">'
        '<div class="news-head">'
        f'<div class="news-title">{name} ({ticker})</div>'
        f'<span class="chip chip-needs-check">{queue}</span>'
        f'{score_html}'
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


def render_brief_discovery_section():
    """Daily Brief 안의 신규 발굴 후보 섹션."""
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

    # 큐 요약
    summary_html = (
        '<div class="card">'
        '<div class="para-row">'
        '<span class="para-label">큐별 후보 수</span>'
        '<span class="para-text">'
        f'Quality Dislocation {queue_counts.get("Quality Dislocation", 0)} · '
        f'Earnings Revision {queue_counts.get("Earnings Revision", 0)} · '
        f'Unusual Volume {queue_counts.get("Unusual Volume", 0)} · '
        f'Civilization Alpha {queue_counts.get("Civilization Alpha", 0)}'
        "</span></div>"
        "</div>"
    )
    st.markdown(summary_html, unsafe_allow_html=True)

    # 승격 후보 카드 (최대 5개)
    if promoted:
        for i, c in enumerate(promoted[:5]):
            _render_discovery_card(c, i, key_prefix="brief_disc")
    else:
        st.markdown(
            '<div class="card">'
            "Tier 2 Promotion 단계까지 통과한 후보가 없습니다. "
            "Discovery 페이지에서 큐별 후보를 직접 확인할 수 있습니다."
            "</div>",
            unsafe_allow_html=True,
        )


def render_discovery():
    """전용 Discovery 페이지 — 큐별 + 승격 후보 + 필터."""
    render_back_button("discovery")
    page_header(
        "Discovery",
        meta="미국 상장주식 wide universe 정량 스크리닝 → Tier 1 / Tier 2 후보",
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

    # 1) 승격 후보 (Tier 2 통과)
    st.markdown(
        '<div class="section-title">Tier 2 승격 후보 (Deep Dive 권장)</div>',
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
            add_to_watchlist(add_ticker)
            st.toast(f"{labels.get(add_ticker, add_ticker)} 편입")
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
            remove_from_watchlist(t)
            st.toast("제거 완료")
            st.rerun()


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
