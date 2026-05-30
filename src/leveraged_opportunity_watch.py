"""Leveraged Opportunity Watch — Daily Brief 의 6-블록 2X ETF surveillance.

블록 (사용자 spec 그대로):
  1. 오늘 2X ETF 사용 가능 후보 (LESS ≥ 80, no block)
  2. 본주 우선 후보 (LESS 60-79)
  3. 사용 금지 후보 (block triggered)
  4. Profit Protection 필요한 보유 2X (사용자 holdings 의 2X 종목 중 profit_protection_score 高)
  5. QLD 보다 매력적인 개별 2X 후보 (qld_view = Better)
  6. Regime gate — 시장 국면상 신규 2X 사용 가능 여부 + 한 줄 narrative

설계 원칙:
  - LESS + profit_protection 두 기존 모듈 재사용 (통일성)
  - materiality: 사용자 holdings 2X 는 평가액 ≥ ₩5M 만 surfacing (보유 잔여 0.03주 같은 거 제외)
  - 소음 제거: 빈 블록은 통째로 누락 (regime gate 만 항상 표시)
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger
from .leveraged_etf_score import score_leveraged_etf
from .universe_taxonomy import get_leveraged_etf_tickers
from . import profit_protection as pp

log = get_logger("lev_watch")


# Materiality (메모리 표준 재사용)
_HOLDING_MIN_VALUE_KRW = 5_000_000


# Regime → 신규 2X 게이트
_REGIME_GATE: dict[str, dict[str, str]] = {
    "Risk-On": {
        "allow": "selective",
        "narrative": "Risk-On — selective 2X 진입 가능. 본주 quality + setup 확인 후 단계 진입.",
        "color": "#22C55E",
    },
    "Pullback in Uptrend": {
        "allow": "selective",
        "narrative": "Pullback in Uptrend — 우상향 중 조정. 고확신 종목 2X 검토 적기.",
        "color": "#22C55E",
    },
    "Expensive but Stable": {
        "allow": "restricted",
        "narrative": "Expensive but Stable — 신규 2X 제한. Quality Dislocation 만 소액.",
        "color": "#F59E0B",
    },
    "Overheated": {
        "allow": "blocked",
        "narrative": "Overheated — 신규 2X 금지. 기존 수익 보호 우선.",
        "color": "#EF4444",
    },
    "Casino Market": {
        "allow": "blocked",
        "narrative": "Casino Market — 신규 2X 절대 금지. 베타 축소 / 현금 비축.",
        "color": "#EF4444",
    },
    "Correction Watch": {
        "allow": "restricted",
        "narrative": "Correction Watch — watchlist 준비. 아직 공격 X.",
        "color": "#F59E0B",
    },
    "Dislocation": {
        "allow": "selective",
        "narrative": "Dislocation — QLD/TQQQ 또는 고확신 개별 2X 검토. credit stress 확인 필수.",
        "color": "#22C55E",
    },
    "Crisis": {
        "allow": "blocked",
        "narrative": "Crisis — 개별 2X 금지. 현금/QQQ/SPY 중심.",
        "color": "#EF4444",
    },
}


# ---------------------------------------------------------------------------
# 보조
# ---------------------------------------------------------------------------

def _regime_label(regime: Any) -> str:
    if regime is None:
        return ""
    try:
        if hasattr(regime, "__getitem__"):
            try:
                return str(regime["current_regime"]) or ""
            except Exception:
                pass
        if isinstance(regime, dict):
            return str(regime.get("current_regime") or "")
    except Exception:
        pass
    return ""


def _regime_overheat(regime: Any) -> float | None:
    if regime is None:
        return None
    try:
        if hasattr(regime, "__getitem__"):
            try:
                v = regime["market_overheat_score"]
                return float(v) if v is not None else None
            except Exception:
                pass
        if isinstance(regime, dict):
            v = regime.get("market_overheat_score")
            return float(v) if v is not None else None
    except Exception:
        pass
    return None


def _build_reverse_2x_map() -> dict[str, str]:
    """2X ETF ticker → underlying ticker.

    사용자 holding 이 TSLL 이면 underlying TSLA 를 역추적.
    leveraged_etf_map.json 의 categories[*][underlying] = [2X_list] 를 inverse.
    """
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "leveraged_etf_map.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, str] = {}
        for cat, mapping in (raw.get("categories") or {}).items():
            for under, lev_list in mapping.items():
                for lev in lev_list:
                    out[lev.upper()] = under.upper()
        return out
    except Exception as e:
        log.debug("reverse 2x map 빌드 실패: %s", e)
        return {}


# ---------------------------------------------------------------------------
# 메인 빌더
# ---------------------------------------------------------------------------

def build_leveraged_opportunity_watch(
    rows: list[dict],
    holdings: list[dict],
    qld_ctx: dict | None,
    regime: Any,
) -> dict[str, Any]:
    """6-블록 watch 데이터 빌드.

    Args:
        rows: engine universe rows (build_rows 결과)
        holdings: 사용자 portfolio.json holdings
        qld_ctx: QLD row for LESS
        regime: 시장 국면 row

    Returns:
        dict with: regime_gate, entry_candidates, body_first, blocked,
        profit_protect_holdings, better_than_qld, total_universe_2x_eligible
    """
    market_overheat = _regime_overheat(regime)
    regime_label = _regime_label(regime)
    gate = _REGIME_GATE.get(regime_label, {
        "allow": "unknown",
        "narrative": f"국면 '{regime_label or 'Unknown'}' — gate 룰 미정의. 보수적 운용 권장.",
        "color": "#94A3B8",
    })

    # ── 1) Universe rows 중 2X ETF 가능 종목만 LESS 적용 ──────────────
    entry: list[dict] = []           # ≥80
    body_first: list[dict] = []      # 60-79
    blocked: list[dict] = []         # block triggered
    better_than_qld: list[dict] = [] # qld_view = Better than QLD
    n_eligible = 0
    for r in rows or []:
        ticker = (r.get("ticker") or "").upper()
        lev_tickers = get_leveraged_etf_tickers(ticker)
        if not lev_tickers:
            continue  # 2X ETF 없는 종목 skip
        n_eligible += 1
        less = score_leveraged_etf(r, qld_ctx, regime, market_overheat)
        score = less.get("score")
        block_flags = [b for b in less.get("block_flags") or [] if b.get("triggered") is True]
        item = {
            "ticker": ticker,
            "name": r.get("name_ko") or r.get("name_en") or ticker,
            "theme": r.get("theme"),
            "leveraged_etf_tickers": lev_tickers,
            "score": score,
            "verdict": less.get("verdict"),
            "summary": less.get("summary_ko"),
            "qld_view": less.get("qld_view"),
            "blocks": [b.get("rule") for b in block_flags],
            "sub_scores": less.get("sub_scores"),
        }
        if block_flags:
            blocked.append(item)
        elif score is not None and score >= 80:
            entry.append(item)
        elif score is not None and score >= 60:
            body_first.append(item)
        # qld better 별도 트랙
        if less.get("qld_view") == "Better than QLD" and score is not None:
            better_than_qld.append(item)

    # 정렬: score desc
    entry.sort(key=lambda x: -(x.get("score") or 0))
    body_first.sort(key=lambda x: -(x.get("score") or 0))
    blocked.sort(key=lambda x: -(x.get("score") or 0))
    better_than_qld.sort(key=lambda x: -(x.get("score") or 0))

    # ── 2) 사용자 holdings 중 2X ETF 보유분 — profit protection ──────
    reverse_map = _build_reverse_2x_map()
    rows_by_ticker = {(r.get("ticker") or "").upper(): r for r in (rows or [])}

    protect_holdings: list[dict] = []
    for h in holdings or []:
        h_ticker = (h.get("ticker") or "").upper()
        # 2X ETF 인지 확인 — reverse_map 에 있거나, leverage=True 인 단일종목 ETF
        is_2x_etf = (h_ticker in reverse_map) or (
            h.get("leverage") and "단일종목" in (h.get("type") or "")
        )
        if not is_2x_etf:
            continue
        # materiality
        value_krw = h.get("value_krw") or 0
        if value_krw < _HOLDING_MIN_VALUE_KRW:
            continue

        # underlying row (시장 데이터 끌어와 profit_protection 에 전달)
        under_ticker = reverse_map.get(h_ticker, "")
        under_row = rows_by_ticker.get(under_ticker, {})
        under_md = under_row.get("market_data") or {}

        # profit_protection 에 넘길 position dict — 보유 정보 + underlying market data
        position = {
            "ticker": h_ticker,
            "underlying_ticker": under_ticker,
            "market_data": under_md or {
                "available": False,
                "current_price": None,
            },
            "current_price": h.get("value_krw"),  # KRW (참고)
            "entry_price": h.get("cost_krw"),
            "value_krw": value_krw,
            "pnl_krw": h.get("pnl_krw"),
            "return_pct": h.get("return_pct"),
            "leverage": h.get("leverage", True),
            "high_vol": h.get("high_vol", True),
            "name": h.get("name") or h_ticker,
        }
        try:
            pp_result = pp.calculate_profit_protection_score(position)
        except Exception as e:
            log.warning("profit_protection 계산 실패 (%s): %s", h_ticker, e)
            continue

        prot_score = pp_result.get("profit_protection_score")
        # materiality: protection score ≥ 60 만 surfacing (소음 제거)
        if prot_score is None or prot_score < 60:
            continue
        protect_holdings.append({
            "ticker": h_ticker,
            "underlying_ticker": under_ticker,
            "name": h.get("name") or h_ticker,
            "value_krw": value_krw,
            "return_pct": h.get("return_pct"),
            "pnl_krw": h.get("pnl_krw"),
            "protection_score": prot_score,
            "band": pp_result.get("protection_band_ko"),
            "suggested_action": pp_result.get("suggested_action"),
        })
    protect_holdings.sort(key=lambda x: -(x.get("protection_score") or 0))

    return {
        "regime_gate": {
            "label": regime_label,
            "allow": gate["allow"],
            "narrative": gate["narrative"],
            "color": gate["color"],
        },
        "entry_candidates": entry,
        "body_first": body_first,
        "blocked": blocked,
        "protect_holdings": protect_holdings,
        "better_than_qld": better_than_qld,
        "total_universe_2x_eligible": n_eligible,
    }


# ---------------------------------------------------------------------------
# HTML 렌더
# ---------------------------------------------------------------------------

def _row_html(item: dict, color: str, show_blocks: bool = False) -> str:
    score = item.get("score")
    score_str = f"{score:.0f}" if score is not None else "—"
    lev_str = ", ".join(item.get("leveraged_etf_tickers") or [])
    blocks_str = ""
    if show_blocks and item.get("blocks"):
        first_block = item["blocks"][0].split(". ", 1)[-1] if item["blocks"] else ""
        blocks_str = (
            '<div style="font-size:11px; color:#EF4444; margin-top:2px;">'
            f'⚠ {first_block}</div>'
        )
    return (
        '<div style="display:flex; justify-content:space-between; gap:10px; '
        'padding:8px 0; border-top:1px solid var(--line);">'
        '<div style="flex:1 1 auto; min-width:0;">'
        '<div style="font-size:13.5px; color:var(--text); font-weight:600;">'
        f'{item["name"]} '
        '<span style="color:var(--muted); font-weight:400; font-size:12px;">'
        f'{item["ticker"]} → {lev_str}</span></div>'
        '<div style="font-size:11.5px; color:var(--muted); margin-top:2px;">'
        f'{item.get("theme") or ""}'
        '</div>'
        + blocks_str +
        '</div>'
        '<div style="flex:0 0 auto; text-align:right;">'
        f'<div style="font-size:11px; color:{color}; font-weight:700;">LESS {score_str}</div>'
        '<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">'
        f'{item.get("qld_view") or ""}'
        '</div>'
        '</div>'
        '</div>'
    )


def _protect_row_html(p: dict) -> str:
    prot = p.get("protection_score")
    band = p.get("band", "")
    action = p.get("suggested_action", "")
    ret = p.get("return_pct")
    pnl = p.get("pnl_krw")
    value = p.get("value_krw")
    return (
        '<div style="display:flex; justify-content:space-between; gap:10px; '
        'padding:9px 0; border-top:1px solid var(--line);">'
        '<div style="flex:1 1 auto; min-width:0;">'
        '<div style="font-size:13.5px; color:var(--text); font-weight:600;">'
        f'{p["name"]} '
        '<span style="color:var(--muted); font-weight:400; font-size:12px;">'
        f'{p["ticker"]}{" → " + p.get("underlying_ticker", "") if p.get("underlying_ticker") else ""}</span></div>'
        f'<div style="font-size:11.5px; color:var(--muted); margin-top:2px;">'
        f'평가액 ₩{(value or 0) / 1e6:.1f}M · '
        + (f'수익률 {ret:+.1f}% · PnL ₩{(pnl or 0) / 1e6:+.1f}M' if ret is not None else '수익률 데이터 없음')
        + '</div>'
        '<div style="font-size:12px; color:#F59E0B; margin-top:3px; line-height:1.5;">'
        f'💡 {action}</div>'
        '</div>'
        '<div style="flex:0 0 auto; text-align:right;">'
        f'<div style="font-size:11px; color:#EF4444; font-weight:700;">Protect {prot:.0f}</div>'
        f'<div style="font-size:10.5px; color:var(--muted); margin-top:2px;">{band}</div>'
        '</div>'
        '</div>'
    )


def render_watch_html(watch: dict) -> str:
    parts = []

    # 헤더
    parts.append(
        '<div class="env-block-title" style="margin-top:0;">Leveraged Opportunity Watch</div>'
    )

    # Regime gate (항상 표시)
    gate = watch.get("regime_gate") or {}
    parts.append(
        '<div style="padding:9px 12px; border-radius:6px; '
        f'background:rgba(148,163,184,0.06); border-left:3px solid {gate.get("color", "#94A3B8")}; '
        'margin:8px 0 12px;">'
        '<div style="font-size:11px; color:var(--muted); margin-bottom:3px;">시장 국면 게이트</div>'
        f'<div style="font-size:13px; color:var(--text); font-weight:500; line-height:1.5;">'
        f'{gate.get("narrative", "—")}'
        '</div></div>'
    )

    n_eligible = watch.get("total_universe_2x_eligible") or 0
    parts.append(
        '<div style="font-size:11px; color:var(--muted); margin:0 0 4px;">'
        f'universe 내 2X ETF 가능 종목 {n_eligible}건 모니터링</div>'
    )

    # 1) Entry candidates (LESS ≥80)
    entry = watch.get("entry_candidates") or []
    if entry:
        parts.append(
            '<div style="font-size:11px; color:#22C55E; margin:14px 0 0; letter-spacing:.02em;">'
            f'▸ 진입 검토 가능 (LESS ≥ 80) — {len(entry)}건</div>'
        )
        parts.append('<div>')
        for item in entry[:6]:
            parts.append(_row_html(item, "#22C55E"))
        parts.append('</div>')

    # 2) Body first (60-79)
    body = watch.get("body_first") or []
    if body:
        parts.append(
            '<div style="font-size:11px; color:#94A3B8; margin:14px 0 0; letter-spacing:.02em;">'
            f'▸ 본주 우선 — 2X 는 소액만 (LESS 60~79) — {len(body)}건</div>'
        )
        parts.append('<div>')
        for item in body[:6]:
            parts.append(_row_html(item, "#94A3B8"))
        parts.append('</div>')

    # 3) Blocked
    blocked = watch.get("blocked") or []
    if blocked:
        parts.append(
            '<div style="font-size:11px; color:#EF4444; margin:14px 0 0; letter-spacing:.02em;">'
            f'▸ 신규 진입 금지 (Block 조건 firing) — {len(blocked)}건</div>'
        )
        parts.append('<div>')
        for item in blocked[:5]:
            parts.append(_row_html(item, "#EF4444", show_blocks=True))
        parts.append('</div>')

    # 4) Profit Protection 필요 보유 2X
    prot = watch.get("protect_holdings") or []
    if prot:
        parts.append(
            '<div style="font-size:11px; color:#F59E0B; margin:14px 0 0; letter-spacing:.02em;">'
            f'▸ Profit Protection 필요 보유 2X — {len(prot)}건</div>'
        )
        parts.append('<div>')
        for p in prot:
            parts.append(_protect_row_html(p))
        parts.append('</div>')

    # 5) QLD Better
    qld_better = watch.get("better_than_qld") or []
    if qld_better:
        # entry/body 와 중복될 수 있어 ticker 기준 dedup
        seen = {item["ticker"] for item in (entry + body)}
        unique_qld = [item for item in qld_better if item["ticker"] not in seen]
        if unique_qld:
            parts.append(
                '<div style="font-size:11px; color:#22C55E; margin:14px 0 0; letter-spacing:.02em;">'
                f'▸ QLD 보다 매력적인 개별 2X 후보 — {len(unique_qld)}건</div>'
            )
            parts.append('<div>')
            for item in unique_qld[:5]:
                parts.append(_row_html(item, "#22C55E"))
            parts.append('</div>')

    # 모든 블록이 비면 한 줄 안내
    if not entry and not body and not blocked and not prot and not qld_better:
        parts.append(
            '<div style="font-size:12px; color:var(--muted); padding:12px 0; line-height:1.6;">'
            '현재 universe + 보유 종목 기준 surfacing 할 2X ETF 신호 없음. '
            'universe 확장 또는 시장 setup 변화 대기.'
            '</div>'
        )

    return ''.join(parts)
