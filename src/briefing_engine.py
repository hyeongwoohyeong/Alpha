"""시황 브리핑 엔진 v2 — 하루 3회 텔레그램 다이제스트.

시간대 (KST):
  08:30 — 아침: 어젯밤 미국 + 오늘 KR 가이드 + 매크로 이벤트
  18:00 — 저녁: 오늘 KR 결과 + alpha bet verdict + 미국 개장 watch
  22:00 — 밤:   미국 EOD 직전 + 내일 KR 준비

원칙:
  - Rule-based + LLM 뉴스 요약 (선택적 — gpt-4o-mini)
  - graceful: 외부 API 실패 시 누락만, 다른 섹션은 계속
  - 진행도 anchor: 디지몬 진화 단계로 동기부여
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote as urllib_quote

from .utils import get_logger
from .telegram_notifier import send_telegram_plain, send_telegram_photo

# 디지몬 이미지 URL — GitHub Pages 의 AlphaDashboard repo
_DIGIMON_IMAGE_BASE = "https://hyeongwoohyeong.github.io/Alpha_research/digimon%20image/"

log = get_logger("briefing_engine")

_NOW_KST = lambda: _dt.datetime.utcnow() + _dt.timedelta(hours=9)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_PORTFOLIO_PATH = _DATA_DIR / "portfolio.json"
_ALPHA_BETS_PATH = _DATA_DIR / "alpha_bets.json"
_WEALTH_PATH = _DATA_DIR / "wealth_inputs.json"
_MACRO_CAL_PATH = _DATA_DIR / "macro_calendar.json"
_QUOTES_PATH = _DATA_DIR / "quotes.json"

# 디지몬 진화 단계 — research_dashboard.html 의 EVO_CHARS 와 동기화
EVO_STAGES = [
    {"stage": 1, "name": "치코몬",                "min_eok": 0,  "max_eok": 1},
    {"stage": 2, "name": "꼬마몬",                "min_eok": 1,  "max_eok": 2},
    {"stage": 3, "name": "브이몬",                "min_eok": 2,  "max_eok": 3},
    {"stage": 4, "name": "엑스브이몬",            "min_eok": 3,  "max_eok": 5},
    {"stage": 5, "name": "파일드라몬",            "min_eok": 5,  "max_eok": 8},
    {"stage": 6, "name": "황제드라몬 드래곤모드", "min_eok": 8,  "max_eok": 12},
    {"stage": 7, "name": "황제드라몬 파이터모드", "min_eok": 12, "max_eok": 20},
    {"stage": 8, "name": "황제드라몬 팔라딘모드", "min_eok": 20, "max_eok": 100},
]
FINAL_GOAL_EOK = 20

# 아파트 분양 deadline
APARTMENT_DEADLINE = _dt.date(2028, 7, 1)
APARTMENT_TARGET_KRW = 400_000_000


def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        log.warning("%s 로드 실패: %s", path.name, e)
        return {}


def _load_holdings() -> list[dict]:
    return _load_json(_PORTFOLIO_PATH).get("holdings", [])


def _load_active_bets() -> list[dict]:
    return [b for b in _load_json(_ALPHA_BETS_PATH).get("bets", []) if b.get("status") == "active"]


def _fmt_pct(p: float | None, digits: int = 1) -> str:
    if p is None:
        return "—"
    return f"{p*100:+.{digits}f}%"


def _net_worth_krw() -> float:
    """wealth_inputs.json + portfolio.json 으로 현재 NW 계산.

    가장 신뢰할 수 있는 source: wealth_inputs.balance_sheet.
    """
    wi = _load_json(_WEALTH_PATH)
    bs = wi.get("balance_sheet", {})
    # 투자자산 = portfolio.json holdings 합
    inv = sum(float(h.get("value_krw") or 0) for h in _load_holdings())
    nw = (
        inv
        + float(bs.get("real_estate_krw") or 0)
        + float(bs.get("apartment_paid_krw") or 0)
        + float(bs.get("deposit_krw") or 0)
        + float(bs.get("cash_outside_krw") or 0)
        + float(bs.get("btc_krw") or 0)
        - float(bs.get("debt_krw") or 0)
    )
    return nw


# ---------------------------------------------------------------------------
# 1) 포트폴리오 진행도 (디지몬 + 분양 + 본주 target)
# ---------------------------------------------------------------------------

def build_progress_section() -> tuple[list[str], dict]:
    """진행도 섹션 — 디지몬 단계 + 분양 + 본주 target.

    Returns (lines, meta) where meta has 'evo_stage' for image attachment.
    """
    lines = ["🎯 포트폴리오 진행도"]
    nw = _net_worth_krw()
    nw_eok = nw / 1e8
    nw_m = nw / 1e6

    # 디지몬 진화 단계
    cur = next((s for s in EVO_STAGES if s["min_eok"] <= nw_eok < s["max_eok"]), EVO_STAGES[0])
    nxt = next((s for s in EVO_STAGES if s["stage"] == cur["stage"] + 1), None)
    stage_range = cur["max_eok"] - cur["min_eok"]
    stage_progress = ((nw_eok - cur["min_eok"]) / stage_range * 100) if stage_range else 100
    lines.append(f"🦴 진화 단계: {cur['name']} (Stage {cur['stage']}/8) — 단계 {stage_progress:.0f}%")
    if nxt:
        remain_eok = nxt["min_eok"] - nw_eok
        lines.append(f"   다음: {nxt['name']} (₩{nxt['min_eok']}억) — ₩{remain_eok*100:.0f}M 남음")

    # 전체 목표 (₩20억)
    total_progress = nw_eok / FINAL_GOAL_EOK * 100
    lines.append(f"🏆 전체 목표 (₩{FINAL_GOAL_EOK}억): {total_progress:.1f}% 진행")

    # 분양 deadline
    today = _NOW_KST().date()
    d_days = (APARTMENT_DEADLINE - today).days
    apt_progress = (nw / APARTMENT_TARGET_KRW * 100) if APARTMENT_TARGET_KRW else 0
    lines.append(f"🏠 분양 D-{d_days}일 — NW ₩{nw_m:.0f}M / 목표 ₩4억 ({apt_progress:.0f}%)")

    # SK하이닉스 본주 target (alpha_bets 의 target_underlying_price 기반)
    sk_target_line = _build_hynix_target_line()
    if sk_target_line:
        lines.append(sk_target_line)

    return lines, {"evo_stage": cur}


def _build_hynix_target_line() -> str | None:
    """SK하이닉스 ₩2.5M target 까지 잔여 거리. 본주 실시간 가격 fetch."""
    bets = _load_active_bets()
    hynix_bet = next((b for b in bets if "하이닉스" in (b.get("name") or "")), None)
    if not hynix_bet:
        return None
    er = hynix_bet.get("exit_rules") or {}
    underlying = er.get("target_underlying_price")
    if not underlying:
        return None
    # 가격 파싱 (예: "SK하이닉스 ₩2,500,000")
    import re
    m = re.search(r"₩\s*([\d,]+)", underlying)
    if not m:
        return None
    target = float(m.group(1).replace(",", ""))
    # 실시간 SK하이닉스 (000660) 가격
    try:
        from .realtime_prices import fetch_kr_ticker
        q = fetch_kr_ticker("000660")
    except Exception as e:
        log.warning("hynix 가격 fetch 실패: %s", e)
        return None
    if not q.get("available"):
        return None
    price = q["price"]
    remain_pct = (target - price) / price * 100  # 양수 = 아직 도달 안 함
    return f"🎯 SK하이닉스 ₩{int(target):,} 달성: {remain_pct:.1f}% 남음 (현재 ₩{int(price):,})"


# ---------------------------------------------------------------------------
# 2) 글로벌 시장 (TQQQ 제거)
# ---------------------------------------------------------------------------

_MARKET_TICKERS = ["QQQ", "SPY", "SOXL"]


def build_market_section() -> list[str]:
    """어젯밤 글로벌 시장 — TQQQ 제거 (사용자 요청)."""
    from .realtime_prices import fetch_us_ticker, fetch_upbit
    lines = ["🌐 어젯밤 글로벌 시장"]
    parts = []
    for t in _MARKET_TICKERS:
        q = fetch_us_ticker(t)
        if not q.get("available"):
            continue
        ch = q.get("change_pct_24h")
        parts.append(f"{t} {_fmt_pct(ch)}")
    if parts:
        lines.append("  " + " / ".join(parts))
    # BTC
    btc = fetch_upbit("KRW-BTC")
    if btc.get("available"):
        ch = btc.get("change_pct_24h")
        dd = btc.get("drawdown_from_52w_high")
        price = btc.get("price")
        line = f"  BTC ₩{price/1e6:.1f}M · 24h {_fmt_pct(ch)}"
        if dd is not None:
            line += f" · 52W DD {_fmt_pct(dd)}"
        lines.append(line)
    if len(lines) == 1:
        lines.append("  데이터 누적 중")
    return lines


# ---------------------------------------------------------------------------
# 3) 매크로 이벤트 (오늘+다음 7일)
# ---------------------------------------------------------------------------

def build_macro_section(days_ahead: int = 7) -> list[str]:
    """다가오는 매크로 이벤트 — macro_calendar.json + yfinance 어닝."""
    cal = _load_json(_MACRO_CAL_PATH)
    events = list(cal.get("events", []))
    # yfinance 어닝 자동 fetch (보유 종목)
    events.extend(_fetch_holdings_earnings(days_ahead))
    if not events:
        return []
    # 오늘 부터 days_ahead 안 필터
    today = _NOW_KST().date()
    cutoff = today + _dt.timedelta(days=days_ahead)
    upcoming = []
    for e in events:
        try:
            d = _dt.date.fromisoformat(e.get("date", ""))
        except Exception:
            continue
        if today <= d <= cutoff:
            upcoming.append((d, e))
    if not upcoming:
        return []
    upcoming.sort(key=lambda x: x[0])
    lines = ["📅 다가오는 지표·이벤트 (KST)"]
    importance_emoji = {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢"}
    for d, e in upcoming[:6]:
        emoji = importance_emoji.get(e.get("importance", "medium"), "🟡")
        d_str = "오늘" if d == today else ("내일" if d == today + _dt.timedelta(days=1) else d.strftime("%m/%d"))
        t = e.get("time_kst", "")
        title = e.get("title", "")
        lines.append(f"  {emoji} {d_str} {t} — {title}")
    return lines


def _fetch_holdings_earnings(days_ahead: int) -> list[dict]:
    """보유 종목 어닝 일정 자동 fetch — yfinance Ticker.calendar."""
    events = []
    try:
        import yfinance as yf
    except Exception:
        return events
    today = _NOW_KST().date()
    # 보유 종목 중 미국 ETF/주식만 (KR 은 yfinance 어닝 부정확)
    seen = set()
    for h in _load_holdings():
        ticker = (h.get("ticker") or "").strip().upper()
        # 6자리 숫자 = KR / KODEX_*/TIGER_* = 알파 별칭, skip
        if not ticker or ticker.isdigit() or "_" in ticker:
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None or (hasattr(cal, "empty") and cal.empty):
                continue
            # cal 은 DataFrame 일 수도 dict 일 수도 — 안전하게 처리
            earnings_date = None
            if hasattr(cal, "get"):
                ed = cal.get("Earnings Date")
                if ed is not None and len(ed) > 0:
                    earnings_date = ed[0] if hasattr(ed, "__getitem__") else None
            if earnings_date is None:
                continue
            # datetime → date
            ed_date = earnings_date.date() if hasattr(earnings_date, "date") else earnings_date
            if not isinstance(ed_date, _dt.date):
                continue
            days_off = (ed_date - today).days
            if 0 <= days_off <= days_ahead:
                events.append({
                    "date": ed_date.isoformat(),
                    "time_kst": "06:00",  # 미국 EOD = KST 06:00 다음 날
                    "title": f"{ticker} 어닝",
                    "importance": "high",
                    "category": "earnings",
                })
        except Exception as e:
            log.debug("어닝 fetch %s 실패: %s", ticker, e)
    return events


# ---------------------------------------------------------------------------
# 4) Alpha Bet Verdict
# ---------------------------------------------------------------------------

def build_alpha_bet_section() -> list[str]:
    """Alpha Bet 상태 — Layer 0 와 동일 로직."""
    try:
        from .today_decision import build_alpha_bet_signals
        holdings = _load_holdings()
        signals = build_alpha_bet_signals(holdings)
    except Exception as e:
        log.warning("alpha bet signals 실패: %s", e)
        return []
    if not signals:
        return ["🎯 Alpha Bet Verdict", "  Active bet 없음"]
    verdict_emoji = {"STOP": "🛑", "SELL": "📤", "TRIM": "✂️", "ADD": "➕", "STAY": "✅"}
    lines = ["🎯 Alpha Bet Verdict"]
    for s in signals:
        v = s.get("verdict", "STAY")
        emoji = verdict_emoji.get(v, "•")
        bet_name = s.get("bet_name") or s.get("label", "")
        if len(bet_name) > 28:
            bet_name = bet_name[:26] + ".."
        lines.append(f"  {emoji} {v} — {bet_name}")
    return lines


# ---------------------------------------------------------------------------
# 5) 오늘의 체크리스트 (분양 적금 제거 — 사용자 안 가입)
# ---------------------------------------------------------------------------

def build_checklist_section() -> list[str]:
    """오늘 할 일 — 룰 + 매크로 + 본인 ledger 기반."""
    lines = ["✅ 오늘의 체크리스트"]
    today = _NOW_KST().date()
    # 1) 매크로 critical/high 이벤트
    cal = _load_json(_MACRO_CAL_PATH)
    for e in cal.get("events", []):
        try:
            d = _dt.date.fromisoformat(e.get("date", ""))
        except Exception:
            continue
        if d == today and e.get("importance") in ("critical", "high"):
            t = e.get("time_kst", "")
            lines.append(f"□ {t} {e.get('title','')} watch")
    # 2) KR 개장 모니터 (active bet 있을 때만)
    bets = _load_active_bets()
    has_kr_bet = any(("하이닉스" in (b.get("name") or "")) or ("KODEX" in (b.get("ticker") or "")) for b in bets)
    if has_kr_bet:
        lines.append("□ 09:00 KR 개장 — KODEX 하이닉스 시초 ±2% 모니터")
    # 3) 작전 정상 진행 메시지 (STAY 만 있을 때)
    try:
        from .today_decision import build_alpha_bet_signals
        signals = build_alpha_bet_signals(_load_holdings())
        all_stay = all(s.get("verdict") == "STAY" for s in signals) and signals
        if all_stay:
            lines.append("□ 매매 X (작전 정상 진행)")
        else:
            lines.append("□ Alpha Bet verdict 확인 + 트리거 시 액션")
    except Exception:
        pass
    if len(lines) == 1:
        lines.append("  특이 사항 없음")
    return lines


# ---------------------------------------------------------------------------
# 6) 자산 변동 (어제 대비)
# ---------------------------------------------------------------------------

def build_asset_delta_section() -> list[str]:
    """어제 대비 NW 변동 — 단순화 (snapshot table 없으면 skip)."""
    nw = _net_worth_krw()
    return [
        "💰 자산 변동",
        f"  NW ₩{nw/1e6:.1f}M (어제 대비 +₩X.XM)  *snapshot table 필요",
    ]


def build_quote_section() -> list[str]:
    """랜덤 명언 — 텔레그램 메시지 맨 아래.

    카테고리 가중치: consolation 30% / wisdom 25% / investing 25% / long_term 10% / discipline 10%.
    위로하는 명언이 좀 더 자주 나오도록.
    """
    import random
    data = _load_json(_QUOTES_PATH)
    quotes = data.get("quotes", [])
    if not quotes:
        return []
    weights = {
        "consolation": 30,
        "wisdom": 25,
        "investing": 25,
        "long_term": 10,
        "discipline": 10,
    }
    weighted_pool = []
    for q in quotes:
        cat = q.get("category", "wisdom")
        weighted_pool.extend([q] * weights.get(cat, 10))
    pick = random.choice(weighted_pool) if weighted_pool else quotes[0]
    return [
        "💭 오늘의 한 마디",
        f"  \"{pick['text']}\"",
        f"  — {pick.get('author', '')}",
    ]


# ---------------------------------------------------------------------------
# Top-level builders
# ---------------------------------------------------------------------------

_SEP = "━" * 18


def _join(*sections: list[str]) -> str:
    out = []
    for sec in sections:
        if not sec:
            continue
        out.append("\n".join(sec))
    return f"\n\n{_SEP}\n".join(out)


def build_news_section() -> list[str]:
    """뉴스 섹션 — LLM 요약 top 3. 실패 시 빈 list."""
    try:
        from .news_brief import get_news_for_briefing, format_news_section
        items = get_news_for_briefing()
        return format_news_section(items)
    except Exception as e:
        log.warning("뉴스 섹션 빌드 실패: %s", e)
        return []


def build_hyper_growth_section() -> list[str]:
    """+100% Watch — Growth Momentum + Catalyst hit 종목.

    DB 의 최근 R8 alert log 에서 추출 (실시간 score 안 함, 비용 큼).
    """
    try:
        from . import database as db
        import sqlite3
        # alert_log 에서 최근 7일 R8 hit
        with db.db_session() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT ticker, message FROM alert_log "
                "WHERE rule_id LIKE 'R8:%' AND ok=1 "
                "AND sent_at >= datetime('now', '-7 days') "
                "ORDER BY sent_at DESC LIMIT 5"
            )
            rows = cur.fetchall()
    except Exception as e:
        log.debug("hyper-growth section 빌드 실패: %s", e)
        return []
    if not rows:
        return []
    lines = ["💎 +100% Watch (이번 주)"]
    for ticker, msg in rows:
        # 메시지 첫 줄에서 종목명 추출
        first_line = (msg or "").split("\n")[0]
        # "💎 +100% Watch — {name} ({ticker})" 패턴
        if "—" in first_line:
            name_part = first_line.split("—", 1)[1].strip()
        else:
            name_part = ticker
        lines.append(f"  {name_part}")
    return lines


def build_morning_briefing() -> tuple[str, dict]:
    """08:30 KST — 어젯밤 미국 + 뉴스 + 오늘 KR 가이드."""
    now = _NOW_KST()
    header = [f"🌅 아침 브리핑 — {now.strftime('%Y.%m.%d (%a)')}"]
    progress_lines, meta = build_progress_section()
    msg = _join(
        header,
        progress_lines,
        build_market_section(),
        build_news_section(),
        build_macro_section(days_ahead=7),
        build_alpha_bet_section(),
        build_checklist_section(),
        build_asset_delta_section(),
        build_quote_section(),
    )
    return msg, meta


def build_evening_briefing() -> tuple[str, dict]:
    """18:00 KST — 오늘 KR 결과 + 미국 개장 직전."""
    now = _NOW_KST()
    header = [f"🌆 저녁 브리핑 — {now.strftime('%Y.%m.%d (%a)')} KR 마감 후"]
    progress_lines, meta = build_progress_section()
    msg = _join(
        header,
        progress_lines,
        build_alpha_bet_section(),
        build_market_section(),
        build_macro_section(days_ahead=3),
        ["⏰ 22:30 KST 미국 개장 — SOXL/QQQ 변동 모니터"],
        build_quote_section(),
    )
    return msg, meta


def build_night_briefing() -> tuple[str, dict]:
    """22:00 KST — 미국 EOD 직전 + 내일 KR 준비 + +100% Watch."""
    now = _NOW_KST()
    header = [f"🌃 밤 브리핑 — {now.strftime('%Y.%m.%d (%a)')} 미국 EOD 직전"]
    progress_lines, meta = build_progress_section()
    msg = _join(
        header,
        progress_lines,
        build_market_section(),
        build_alpha_bet_section(),
        build_hyper_growth_section(),
        build_macro_section(days_ahead=3),
        ["⏰ 내일 KR 09:00 준비 — 미국 NVDA/TSMC 변동 확인 후 시초가 판단"],
        build_quote_section(),
    )
    return msg, meta


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _build_digimon_image_url(evo_stage: dict | None) -> str | None:
    """진화 단계 → GitHub Pages 이미지 URL.

    파일명은 한글이라 URL encoding 필요. NFD normalize 후 quote (대시보드와 동일).
    """
    if not evo_stage:
        return None
    name = evo_stage.get("name")
    if not name:
        return None
    import unicodedata
    # macOS NFD 저장 → URL encoding (대시보드 imagePath() 와 동일 패턴)
    nfd = unicodedata.normalize("NFD", name)
    encoded = urllib_quote(nfd + ".png", safe="")
    return _DIGIMON_IMAGE_BASE + encoded


def run_briefing(slot: str) -> dict[str, Any]:
    """slot: 'morning' | 'evening' | 'night'. 텍스트 본문만 전송 (이미지 제거)."""
    builders = {
        "morning": build_morning_briefing,
        "evening": build_evening_briefing,
        "night":   build_night_briefing,
    }
    if slot not in builders:
        return {"ok": False, "error": f"unknown_slot:{slot}"}
    msg, meta = builders[slot]()
    result = send_telegram_plain(msg)
    return {"slot": slot, "msg_preview": msg[:300],
            "evo_stage": meta.get("evo_stage", {}).get("name"), **result}


if __name__ == "__main__":
    import sys
    slot = sys.argv[1] if len(sys.argv) > 1 else "morning"
    out = run_briefing(slot)
    print(json.dumps(out, indent=2, ensure_ascii=False))
