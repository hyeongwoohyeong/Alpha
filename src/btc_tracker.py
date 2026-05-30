"""비트코인 전용 추적 모듈.

market_data 의 BTC-USD proxy 데이터를 받아서:
- 현재가 (USD + KRW)
- 일일 / 1개월 변동
- 52W 고가·저가·drawdown
- 단순 사이클 위치 (drawdown 기반: 신고가권 / 중립 / 하락 사이클)
- 보유 수량·평가액 (portfolio.json 의 btc_krw / btc_qty 가 있으면)

LLM 의존 없음. 실패 시 graceful.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("btc_tracker")


def _fetch_usdkrw_rate() -> float | None:
    """USD/KRW 환율 — yfinance KRW=X. 실패 시 None."""
    try:
        import yfinance as yf
        t = yf.Ticker("KRW=X")
        # fast_info 가 가장 가볍고 안정
        try:
            info = t.fast_info
            v = info.last_price if hasattr(info, "last_price") else info.get("last_price")
            if v and v > 100:  # sanity check (KRW=X ~ 1300+)
                return float(v)
        except Exception:
            pass
        # fallback: history
        hist = t.history(period="5d", auto_adjust=True)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning("USD/KRW fetch 실패: %s", e)
    return None


def _btc_cycle_stage(drawdown_from_52w: float | None) -> dict[str, Any]:
    """52W drawdown 기반 단순 사이클 분류.

    BTC 의 본질상 깊은 변동성이라 정통 -10%/-20% 룰 대신 더 큰 폭 사용:
    - dd > -10%: 신고가권 (Top zone — 과열 주의)
    - -10% > dd > -25%: 정상 (Mid-cycle)
    - -25% > dd > -50%: 하락 사이클 진입 (Drawdown)
    - dd <= -50%: 깊은 하락 / Capitulation zone (역사적 진입 기회)
    """
    if drawdown_from_52w is None:
        return {"stage": "확인 필요", "color": "#94A3B8", "tone": "데이터 부족"}
    dd = drawdown_from_52w  # 음수 (예: -0.15)
    if dd >= -0.10:
        return {"stage": "신고가권", "color": "#F59E0B", "tone": "과열·익절 라인 점검"}
    if dd >= -0.25:
        return {"stage": "정상 구간", "color": "#3B82F6", "tone": "Mid-cycle — 관망"}
    if dd >= -0.50:
        return {"stage": "하락 사이클", "color": "#EF4444", "tone": "분할매수 검토 시작"}
    return {"stage": "Capitulation", "color": "#15803D", "tone": "역사적 진입 기회"}


def _load_btc_holdings() -> dict[str, Any]:
    """portfolio.json 또는 wealth_inputs.json 에서 BTC 보유 정보 로드.

    Returns: {available, qty_btc, value_krw, source}
    """
    try:
        root = Path(__file__).resolve().parent.parent
        # 1) wealth_inputs.json balance_sheet.btc_krw
        wi = root / "data" / "wealth_inputs.json"
        if wi.exists():
            raw = json.loads(wi.read_text(encoding="utf-8"))
            bs = raw.get("balance_sheet", {})
            btc_krw = bs.get("btc_krw", 0)
            btc_qty = bs.get("btc_qty")
            if btc_krw or btc_qty:
                return {"available": True, "qty_btc": btc_qty, "value_krw": btc_krw, "source": "wealth_inputs.balance_sheet"}
        # 2) portfolio.json holdings 에 BTC 가 있으면
        pj = root / "data" / "portfolio.json"
        if pj.exists():
            raw = json.loads(pj.read_text(encoding="utf-8"))
            for h in raw.get("holdings", []):
                if h.get("ticker", "").upper() in ("BTC", "BTC-USD", "BITCOIN"):
                    return {
                        "available": True,
                        "qty_btc": h.get("shares") or h.get("qty"),
                        "value_krw": h.get("value_krw", 0),
                        "source": "portfolio.json",
                    }
    except Exception as e:
        log.warning("BTC 보유 로드 실패: %s", e)
    return {"available": False, "qty_btc": None, "value_krw": 0, "source": None}


def build_btc_snapshot(proxies: dict[str, Any]) -> dict[str, Any]:
    """BTC 데이터 + 환산 + 사이클 + 보유 정보를 하나로 묶어 반환.

    Args:
        proxies: market_data.fetch_market_proxies() 결과
    Returns: {
        available, price_usd, price_krw, usdkrw,
        daily_return, m1_return, w52_high, w52_low, drawdown,
        cycle: {stage, color, tone},
        holdings: {available, qty_btc, value_krw, source},
        narrative: 한 줄 코멘트,
    }
    """
    btc = (proxies or {}).get("BTC-USD") or {}
    if not btc.get("available"):
        return {
            "available": False,
            "narrative": "BTC 데이터 수집 실패 — yfinance 응답 없음 / 시장 휴장",
        }
    price_usd = btc.get("current_price")
    dr = btc.get("daily_return")
    r1m = btc.get("1m_return")
    w52h = btc.get("52w_high")
    w52l = btc.get("52w_low")
    dd = btc.get("drawdown_from_52w_high")

    usdkrw = _fetch_usdkrw_rate()
    price_krw = (price_usd * usdkrw) if (price_usd is not None and usdkrw is not None) else None

    cycle = _btc_cycle_stage(dd)
    holdings = _load_btc_holdings()

    # 한 줄 코멘트
    parts = []
    if price_usd is not None:
        usd_str = f"${price_usd:,.0f}"
        if price_krw is not None:
            usd_str += f" (₩{price_krw / 1e6:.1f}M)"
        parts.append(usd_str)
    if dr is not None:
        parts.append(f"일일 {dr * 100:+.2f}%")
    if dd is not None:
        parts.append(f"52W 고점 대비 {dd * 100:+.1f}%")
    parts.append(cycle["stage"])
    narrative = " · ".join(parts) if parts else "데이터 부족"

    return {
        "available": True,
        "price_usd": price_usd,
        "price_krw": price_krw,
        "usdkrw": usdkrw,
        "daily_return": dr,
        "m1_return": r1m,
        "w52_high": w52h,
        "w52_low": w52l,
        "drawdown": dd,
        "cycle": cycle,
        "holdings": holdings,
        "narrative": narrative,
    }


def render_btc_card_html(snap: dict[str, Any]) -> str:
    """Daily Brief 의 BTC 카드 HTML — Streamlit st.markdown unsafe_allow_html=True."""
    if not snap.get("available"):
        return (
            '<div class="env-block">'
            '<div class="env-block-title">비트코인 (BTC)</div>'
            '<div style="color:#94A3B8; font-size:13px;">'
            + (snap.get("narrative") or "데이터 수집 실패") +
            "</div></div>"
        )
    price_usd = snap.get("price_usd")
    price_krw = snap.get("price_krw")
    dr = snap.get("daily_return")
    r1m = snap.get("m1_return")
    w52h = snap.get("w52_high")
    dd = snap.get("drawdown")
    cycle = snap.get("cycle", {})
    holdings = snap.get("holdings", {})

    def pct(v: float | None) -> str:
        if v is None:
            return '<span style="color:#64748B;">—</span>'
        cls = "color:#22C55E;" if v >= 0 else "color:#EF4444;"
        sign = "+" if v >= 0 else ""
        return f'<span style="{cls}">{sign}{v * 100:.2f}%</span>'

    price_line = (
        f"${price_usd:,.0f}" if price_usd is not None else "—"
    )
    if price_krw is not None:
        price_line += f' <span style="color:#94A3B8; font-size:0.85em;">≈ ₩{price_krw / 1e6:.2f}M</span>'

    holdings_line = ""
    if holdings.get("available") and holdings.get("value_krw"):
        hv = holdings["value_krw"]
        qty = holdings.get("qty_btc")
        qty_str = f"{qty:.4f} BTC" if qty else ""
        holdings_line = (
            f'<div style="margin-top:6px; padding-top:6px; border-top:1px solid #1E293B; font-size:12px; color:#CBD5E1;">'
            f'보유: ₩{hv:,.0f}'
            + (f" ({qty_str})" if qty_str else "")
            + '</div>'
        )

    return (
        '<div class="env-block">'
        '<div class="env-block-title">비트코인 (BTC-USD)</div>'
        f'<div style="font-size:1.1rem; font-weight:600; color:#F8FAFC; margin-bottom:6px;">{price_line}</div>'
        f'<div style="display:flex; gap:12px; font-size:12px; color:#94A3B8;">'
        f'<span>일일 {pct(dr)}</span>'
        f'<span>1M {pct(r1m)}</span>'
        f'<span>DD {pct(dd)}</span>'
        '</div>'
        f'<div style="margin-top:8px; font-size:12px; color:{cycle.get("color", "#94A3B8")}; font-weight:500;">'
        f'{cycle.get("stage", "—")} · {cycle.get("tone", "")}'
        '</div>'
        + holdings_line +
        '</div>'
    )
