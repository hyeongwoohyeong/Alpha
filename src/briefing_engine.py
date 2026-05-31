"""시황 브리핑 엔진 — 하루 3회 텔레그램 다이제스트.

시간대 (KST):
  08:30 — 아침: 어젯밤 미국 결과 + 오늘 KR 진입 가이드
  18:00 — 저녁: 오늘 KR 결과 + alpha bet verdict
  22:00 — 밤:   미국 EOD 직전 + 내일 KR 준비

원칙:
  - Rule-based, LLM 토큰 안 씀
  - graceful: 가격 fetch 실패해도 사용 가능한 데이터만 노출
  - 다이제스트: 한 텔레그램 메시지 안에 핵심만
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .utils import get_logger
from .telegram_notifier import send_telegram_plain

log = get_logger("briefing_engine")

_NOW_KST = lambda: _dt.datetime.utcnow() + _dt.timedelta(hours=9)

_PORTFOLIO_PATH = Path(__file__).resolve().parents[1] / "data" / "portfolio.json"
_ALPHA_BETS_PATH = Path(__file__).resolve().parents[1] / "data" / "alpha_bets.json"

# 브리핑별 모니터 종목
_US_TRACKERS = ["QQQ", "SPY", "SOXL", "TQQQ"]
_HOLDINGS_TICKERS_US = ["SOXL"]  # holdings 중 미국 종목 (자동 발견 가능하지만 명시)


def _load_holdings() -> list[dict]:
    try:
        with open(_PORTFOLIO_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("holdings", [])
    except Exception as e:
        log.warning("portfolio.json 로드 실패: %s", e)
        return []


def _load_alpha_bets() -> list[dict]:
    try:
        with open(_ALPHA_BETS_PATH, encoding="utf-8") as f:
            return [b for b in (json.load(f) or {}).get("bets", []) if b.get("status") == "active"]
    except Exception as e:
        log.warning("alpha_bets.json 로드 실패: %s", e)
        return []


def _fmt_pct(p: float | None, digits: int = 1) -> str:
    if p is None:
        return "—"
    return f"{p*100:+.{digits}f}%"


def _fmt_krw_mm(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1_000_000:
        return f"₩{v/1e6:+.1f}M"
    return f"₩{v:,.0f}"


def _build_us_market_summary() -> list[str]:
    """US tracker 종목 변동률 요약 (yfinance)."""
    from .realtime_prices import fetch_us_ticker
    lines = []
    for t in _US_TRACKERS:
        q = fetch_us_ticker(t)
        if not q.get("available"):
            continue
        ch = q.get("change_pct_24h")
        dd = q.get("drawdown_from_52w_high")
        price = q.get("price")
        sweet = ""
        if t == "QQQ" and dd is not None and -0.15 <= dd <= -0.05:
            sweet = " 🎯 sweet spot!"
        lines.append(
            f"  {t} ${price:,.2f} · {_fmt_pct(ch)}"
            f"{' · DD ' + _fmt_pct(dd) if dd is not None else ''}{sweet}"
        )
    return lines


def _build_btc_summary() -> list[str]:
    """BTC 변동 + drawdown."""
    from .realtime_prices import fetch_upbit
    q = fetch_upbit("KRW-BTC")
    if not q.get("available"):
        return []
    ch = q.get("change_pct_24h")
    dd = q.get("drawdown_from_52w_high")
    price = q.get("price")
    return [f"  BTC ₩{price:,.0f} · 24h {_fmt_pct(ch)}"
            f"{' · 52W DD ' + _fmt_pct(dd) if dd is not None else ''}"]


def _build_holdings_summary(holdings: list[dict], min_value_mm: int = 5) -> list[str]:
    """평가액 ≥ ₩5M holdings 의 현재 상태 한 줄씩."""
    lines = []
    items = sorted(
        [h for h in holdings if (h.get("value_krw") or 0) >= min_value_mm * 1_000_000],
        key=lambda h: -(h.get("value_krw") or 0)
    )[:8]
    for h in items:
        name = h.get("name") or h.get("ticker", "?")
        # 이름 길면 줄임
        if len(name) > 22:
            name = name[:20] + ".."
        ret = h.get("return_pct") or 0
        value = (h.get("value_krw") or 0) / 1e6
        sign = "📈" if ret >= 0 else "📉"
        lines.append(f"  {sign} {name} {ret:+.1f}% (₩{value:.0f}M)")
    return lines


def _build_alpha_bet_section(holdings: list[dict]) -> list[str]:
    """Alpha bet signals (Layer 0) 요약."""
    try:
        from .today_decision import build_alpha_bet_signals
        signals = build_alpha_bet_signals(holdings)
    except Exception as e:
        log.warning("alpha bet signals 실패: %s", e)
        return []
    if not signals:
        return ["  Active bet 없음 — data/alpha_bets.json 확인"]
    verdict_emoji = {"STOP": "🛑", "SELL": "📤", "TRIM": "✂️", "ADD": "➕", "STAY": "✅"}
    lines = []
    for s in signals:
        v = s.get("verdict", "STAY")
        emoji = verdict_emoji.get(v, "•")
        bet_name = s.get("bet_name") or s.get("label", "")
        # 짧게
        if len(bet_name) > 28:
            bet_name = bet_name[:26] + ".."
        lines.append(f"  {emoji} {v} — {bet_name}")
    return lines


def _build_kr_holdings_section(holdings: list[dict]) -> list[str]:
    """KR holdings (KODEX 하이닉스 등) 의 현재 상태."""
    lines = []
    for h in holdings:
        ticker = (h.get("ticker") or "").strip()
        # KR ticker: 6자리 숫자 또는 KODEX_/TIGER_ 별칭
        is_kr = (ticker.isdigit() and len(ticker) == 6) or ticker.startswith(("KODEX_", "TIGER_"))
        if not is_kr:
            continue
        value = (h.get("value_krw") or 0) / 1e6
        if value < 5:
            continue
        ret = h.get("return_pct") or 0
        sign = "📈" if ret >= 0 else "📉"
        name = h.get("name") or ticker
        if len(name) > 22:
            name = name[:20] + ".."
        lines.append(f"  {sign} {name} {ret:+.1f}% (₩{value:.0f}M)")
    return lines


# ---------------------------------------------------------------------------
# Briefing builders — 시간대별
# ---------------------------------------------------------------------------

def build_morning_briefing() -> str:
    """08:30 KST — 어젯밤 미국 + 오늘 KR 진입 가이드."""
    now = _NOW_KST()
    holdings = _load_holdings()
    parts = [f"🌅 아침 브리핑 — {now.strftime('%Y.%m.%d (%a)')}", ""]

    parts.append("📊 어젯밤 미국 시장")
    us = _build_us_market_summary()
    parts.extend(us if us else ["  데이터 누적 중"])
    parts.append("")

    parts.append("🪙 비트코인 24h")
    btc = _build_btc_summary()
    parts.extend(btc if btc else ["  데이터 누적 중"])
    parts.append("")

    parts.append("🎯 Alpha Bet 상태")
    parts.extend(_build_alpha_bet_section(holdings))
    parts.append("")

    parts.append("⏰ 오늘 KR 액션 가이드")
    bets = _load_alpha_bets()
    has_kodex_hynix = any("하이닉스" in (b.get("name") or "") for b in bets)
    if has_kodex_hynix:
        parts.append("  • KODEX 하이닉스 본주(SK하이닉스) 가격 모니터")
        parts.append("  • 작전 정상 진행: 임계 도달 전까지 추가 매매 X")
    else:
        parts.append("  • Active alpha bet 없음 — Daily Brief 에서 신규 후보 확인")
    return "\n".join(parts)


def build_evening_briefing() -> str:
    """18:00 KST — 오늘 KR 결과 + alpha bet verdict + 저녁 watch."""
    now = _NOW_KST()
    holdings = _load_holdings()
    parts = [f"🌆 저녁 브리핑 — {now.strftime('%Y.%m.%d (%a)')} KR 마감 후", ""]

    parts.append("📊 오늘 KR 보유 종목")
    kr_lines = _build_kr_holdings_section(holdings)
    parts.extend(kr_lines if kr_lines else ["  KR 보유 종목 없음 (₩5M+)"])
    parts.append("")

    parts.append("🎯 Alpha Bet Verdict")
    parts.extend(_build_alpha_bet_section(holdings))
    parts.append("")

    parts.append("🌙 저녁 watch (미국 개장 22:30 KST)")
    us = _build_us_market_summary()  # 개장 전이므로 어제 종가 기준
    parts.extend(us if us else ["  데이터 누적 중"])
    parts.append("")

    parts.append("⏰ 액션 가이드")
    parts.append("  • 22:30 KST 미국 개장 — SOXL/QQQ 변동 모니터")
    parts.append("  • alpha bet trigger 도달 시 별도 알림 (1시간 cron)")
    return "\n".join(parts)


def build_night_briefing() -> str:
    """22:00 KST — 미국 EOD 직전 + 내일 KR 준비."""
    now = _NOW_KST()
    holdings = _load_holdings()
    parts = [f"🌃 밤 브리핑 — {now.strftime('%Y.%m.%d (%a)')} 미국 EOD 직전", ""]

    parts.append("📊 미국 시장 (EOD 직전)")
    us = _build_us_market_summary()
    parts.extend(us if us else ["  데이터 누적 중"])
    parts.append("")

    parts.append("🪙 비트코인 24h")
    btc = _build_btc_summary()
    parts.extend(btc if btc else ["  데이터 누적 중"])
    parts.append("")

    parts.append("🎯 오늘의 Alpha Bet 종합")
    parts.extend(_build_alpha_bet_section(holdings))
    parts.append("")

    parts.append("⏰ 내일 KR 09:00 준비")
    bets = _load_alpha_bets()
    has_kodex_hynix = any("하이닉스" in (b.get("name") or "") for b in bets)
    if has_kodex_hynix:
        parts.append("  • KODEX 하이닉스 — 미국 NVIDIA/TSMC 변동 확인 후 시초가 판단")
    parts.append("  • 평단 회복 / 손절 임박 시 별도 알림")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

def run_briefing(slot: str) -> dict[str, Any]:
    """slot: 'morning' | 'evening' | 'night'."""
    if slot == "morning":
        msg = build_morning_briefing()
    elif slot == "evening":
        msg = build_evening_briefing()
    elif slot == "night":
        msg = build_night_briefing()
    else:
        return {"ok": False, "error": f"unknown_slot:{slot}"}
    result = send_telegram_plain(msg)
    return {"slot": slot, "msg_preview": msg[:200], **result}


if __name__ == "__main__":
    import sys
    slot = sys.argv[1] if len(sys.argv) > 1 else "morning"
    out = run_briefing(slot)
    print(json.dumps(out, indent=2, ensure_ascii=False))
