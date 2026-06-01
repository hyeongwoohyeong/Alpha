"""Alert engine — 30분 cron 으로 실행. 룰 trigger 체크 → 텔레그램 push.

룰 (alpha_bets.json 의 active bets + holdings + market data 기반):
  R1. Alpha Bet STOP/SELL/TRIM verdict (today_decision.build_alpha_bet_signals)
  R2. 본주 가격 target (예: SK하이닉스 ₩2.5M 도달)
  R3. 평단 돌파 (-가 0%↑ 회복 / +가 0%↓ 하락)
  R4. TQQQ sweet spot — QQQ 52W DD ∈ [-5%, -15%] 진입
  R5. BTC -70%+ drawdown — cycle deploy timing
  R6. 24h 급등락 ±5%+ (옵션, 기본 OFF — spam 위험)
  R7. Alpha 후보 신규 발견 (score≥80 + DD≤-10%) — 별도 fetch 필요

원칙:
  - dedup: 같은 rule_id 가 최근 N시간 안 발화됐으면 skip (기본 4h)
  - graceful: 텔레그램 미설정 시 log 만, 다른 룰은 계속 실행
  - empirical-only: 모든 임계값은 사용자 룰 (alpha_bets.json) 또는 실증 (entry_timing_buckets) 기반
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .utils import get_logger
from .telegram_notifier import send_telegram_plain

# Path 이미 import 됨

log = get_logger("alert_engine")

# Dedup 윈도우 (rule_id 기준)
_DEDUP_HOURS_DEFAULT = 4

# 룰 enable/disable — feature flag (사용자 변경 가능)
RULES_ENABLED: dict[str, bool] = {
    "R1_alpha_bet_signals":  True,
    "R2_underlying_target":  True,
    "R3_breakeven_cross":    True,
    "R4_tqqq_sweet_spot":    True,
    "R5_btc_drawdown_deep":  True,
    "R6_intraday_spike":     False,  # 기본 OFF (spam 방지)
    "R7_new_alpha_discovery": True,
    "R8_hyper_growth_watch": True,   # +100% Watch — Growth Momentum + Catalyst
    "R9_phase_transition":   True,   # Phase A→B→C 전환 NW 도달 알림
    "R10_tax_calendar":      True,   # 12월 양도세 250만 공제 alert
}

# Phase 전환 NW 임계 (memory: apartment_deadline.md / user_profile.md)
_PHASE_TRANSITIONS = [
    (400_000_000,   "A_to_B", "🏠 Phase A → B 전환 가능",
        "NW ₩4억 도달 — 분양 잔금 가능 영역. KODEX 하이닉스 익절·BTC 전환 검토."),
    (500_000_000,   "B_entry", "📊 Phase B 진입 (코어 적립)",
        "NW ₩5억 — 코어 적립 (QLD/JEPQ/SCHD) 자동화 시점. 알파는 위성으로 축소."),
    (1_000_000_000, "C_entry", "💰 Phase C 진입 (배당 시스템)",
        "NW ₩10억 — JEPQ/SCHD 배당으로 월 ₩400만+ 가능. 워라밸 직장 이직 가능 영역."),
    (2_000_000_000, "final",   "🎯 최종 목표 ₩20억 도달",
        "황제드라몬 팔라딘모드 — 자유 도달."),
]

# +100% Watch — Universe 별로 score 캐시 (매일 update 큰 비용이라 weekly cron)
# 실제 데이터는 GitHub Actions Daily Research 워크플로가 별도 채움
_HYPER_GROWTH_THRESHOLD = 70.0
_HYPER_GROWTH_CRITICAL = 80.0


def _now_utc_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


# Dedup state file — alpha.db 와 별개. workflow 끝에 commit 가능 (작은 JSON)
_DEDUP_FILE = Path(__file__).resolve().parents[1] / "data" / "alert_dedup.json"


def _load_dedup_state() -> dict:
    """File-based dedup — alpha.db 의 alert_log 보다 우선."""
    try:
        if _DEDUP_FILE.exists():
            with open(_DEDUP_FILE, encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        log.debug("dedup state load 실패: %s", e)
    return {}


def _save_dedup_state(state: dict) -> None:
    try:
        _DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("dedup state save 실패: %s", e)


def _was_sent_recently(conn: sqlite3.Connection, rule_id: str, hours: int = _DEDUP_HOURS_DEFAULT) -> bool:
    """rule_id 가 최근 N시간 안 발화됐는지.

    Primary: data/alert_dedup.json (workflow 간 보존)
    Fallback: alpha.db alert_log (workflow run 내)
    """
    # 1) File-based
    state = _load_dedup_state()
    entry = state.get(rule_id)
    if entry and entry.get("ok"):
        try:
            last_sent = _dt.datetime.fromisoformat(entry["sent_at"])
            if (_dt.datetime.utcnow() - last_sent).total_seconds() < hours * 3600:
                return True
        except Exception:
            pass

    # 2) DB fallback (workflow run 내)
    cur = conn.cursor()
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    cur.execute(
        "SELECT 1 FROM alert_log WHERE rule_id=? AND sent_at>=? AND ok=1 LIMIT 1",
        (rule_id, cutoff),
    )
    return cur.fetchone() is not None


def _log_alert(conn: sqlite3.Connection, rule_id: str, ticker: str | None,
               severity: str, message: str, ok: bool, meta: dict | None = None) -> None:
    """양쪽 저장: file (영구) + DB (workflow 내)."""
    # 1) File dedup state update
    state = _load_dedup_state()
    state[rule_id] = {
        "sent_at": _now_utc_iso(),
        "ok": bool(ok),
        "ticker": ticker,
        "severity": severity,
        # 오래된 항목 자동 정리 위해 마지막 30일만 유지하는 게 좋지만 일단 단순
    }
    # 30일 이상 된 entry 자동 정리
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=30)
    state = {
        k: v for k, v in state.items()
        if _try_parse_dt(v.get("sent_at")) >= cutoff
    }
    _save_dedup_state(state)

    # 2) DB (workflow run 내 — debug 용)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO alert_log (rule_id, ticker, severity, message, sent_at, ok, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rule_id, ticker, severity, message, _now_utc_iso(),
         1 if ok else 0, json.dumps(meta or {}, ensure_ascii=False)),
    )
    conn.commit()


def _try_parse_dt(s: str | None) -> _dt.datetime:
    """ISO datetime 파싱 — 실패 시 0001-01-01 (즉 정리 대상)."""
    if not s:
        return _dt.datetime(1, 1, 1)
    try:
        return _dt.datetime.fromisoformat(s)
    except Exception:
        return _dt.datetime(1, 1, 1)


def _send_or_skip(conn: sqlite3.Connection, rule_id: str, ticker: str | None,
                  severity: str, message: str, dedup_hours: int = _DEDUP_HOURS_DEFAULT,
                  meta: dict | None = None) -> dict[str, Any]:
    """dedup 체크 후 텔레그램 send + log."""
    if _was_sent_recently(conn, rule_id, dedup_hours):
        log.debug("dedup skip: %s", rule_id)
        return {"sent": False, "skipped": True, "reason": "dedup"}
    result = send_telegram_plain(message)
    _log_alert(conn, rule_id, ticker, severity, message, result.get("ok", False), meta)
    return {"sent": result.get("ok", False), "skipped": False, "result": result}


# ---------------------------------------------------------------------------
# R1 — Alpha Bet STOP/SELL/TRIM
# ---------------------------------------------------------------------------

def check_alpha_bet_signals(conn: sqlite3.Connection, holdings: list[dict]) -> list[dict]:
    """today_decision.build_alpha_bet_signals 활용 — STOP/SELL/TRIM 만 push."""
    if not RULES_ENABLED.get("R1_alpha_bet_signals"):
        return []
    out: list[dict] = []
    try:
        from .today_decision import build_alpha_bet_signals
        signals = build_alpha_bet_signals(holdings)
    except Exception as e:
        log.warning("R1 build_alpha_bet_signals 실패: %s", e)
        return []
    for s in signals:
        verdict = s.get("verdict")
        if verdict not in ("STOP", "SELL", "TRIM"):
            continue
        bet_id = s.get("bet_id", "unknown")
        rule_id = f"R1:alpha_bet:{bet_id}:{verdict}"
        emoji = {"STOP": "🛑", "SELL": "📤", "TRIM": "✂️"}.get(verdict, "⚠️")
        msg = (f"{emoji} Alpha Bet {verdict}\n"
               f"{s.get('label','')}\n{s.get('detail','')}\n\n"
               f"→ Daily Brief 확인 → 액션 결정")
        result = _send_or_skip(conn, rule_id, None, "warn", msg)
        out.append({"rule": rule_id, **result})
    return out


# ---------------------------------------------------------------------------
# R2 — 본주 가격 target (예: SK하이닉스 ₩2.5M)
# ---------------------------------------------------------------------------

def check_underlying_target(conn: sqlite3.Connection) -> list[dict]:
    """alpha_bets 에 target_underlying_price 설정된 bet — 본주 실시간 가격 fetch + 비교."""
    if not RULES_ENABLED.get("R2_underlying_target"):
        return []
    out: list[dict] = []
    from .today_decision import _load_active_alpha_bets
    from .realtime_prices import fetch_kr_ticker
    bets = _load_active_alpha_bets()

    # 매핑: bet_id → (본주 ticker, target_price)
    # 단순화 — name 에 '하이닉스' 있으면 000660, 본주 target 파싱
    for b in bets:
        er = b.get("exit_rules") or {}
        target_underlying = er.get("target_underlying_price")
        if not target_underlying:
            continue
        # 패턴: "SK하이닉스 ₩2,500,000" → ticker=000660, target=2_500_000
        ticker = None
        target_price = None
        name_l = (b.get("name") or "").lower()
        if "하이닉스" in name_l or "hynix" in name_l:
            ticker = "000660"
        # 가격 파싱
        import re
        m = re.search(r"₩\s*([\d,]+)", target_underlying)
        if m:
            try:
                target_price = float(m.group(1).replace(",", ""))
            except Exception:
                pass
        if not ticker or not target_price:
            continue
        quote = fetch_kr_ticker(ticker)
        if not quote.get("available"):
            continue
        price = quote["price"]
        # 도달 95% 이내 → trigger
        ratio = price / target_price
        rule_id = f"R2:underlying:{b.get('id')}:{ticker}"
        if ratio >= 0.95:
            margin = (target_price - price) / target_price
            if ratio >= 1.0:
                msg = (f"🎯 본주 target 도달!\n{b.get('name','')}\n"
                       f"{quote['name']} 현재가 ₩{int(price):,} (target ₩{int(target_price):,})\n\n"
                       f"→ KODEX 본주 단계 익절 발화 검토")
                _send_or_skip(conn, rule_id + ":HIT", ticker, "critical", msg, dedup_hours=24)
                out.append({"rule": rule_id, "status": "HIT"})
            else:
                msg = (f"📊 본주 target 임박\n{b.get('name','')}\n"
                       f"{quote['name']} ₩{int(price):,} → target ₩{int(target_price):,} "
                       f"까지 {-margin*100:.1f}% 남음")
                _send_or_skip(conn, rule_id + ":NEAR", ticker, "info", msg, dedup_hours=24)
                out.append({"rule": rule_id, "status": "NEAR"})
    return out


# ---------------------------------------------------------------------------
# R3 — 평단 돌파 (-→+ 회복 또는 +→- 하락)
# ---------------------------------------------------------------------------

def check_breakeven_cross(conn: sqlite3.Connection, holdings: list[dict]) -> list[dict]:
    """평단 돌파 — 손실→이익 회복, 이익→손실 하락. ₩5M+ 포지션만."""
    if not RULES_ENABLED.get("R3_breakeven_cross"):
        return []
    out: list[dict] = []
    cur = conn.cursor()
    for h in holdings:
        ret = float(h.get("return_pct") or 0)
        value = float(h.get("value_krw") or 0)
        if value < 5_000_000:
            continue
        ticker = (h.get("ticker") or "").strip()
        name = h.get("name") or ticker
        rule_id_recover = f"R3:breakeven_recover:{ticker}"
        rule_id_fall = f"R3:breakeven_fall:{ticker}"

        # 직전 발화 상태 lookup — 마지막 평단 cross 상태 확인 (단순화: dedup_hours=24)
        if ret >= 0 and ret <= 2.0:
            # 이익 0~+2% 진입 — 회복 진입 trigger (이전에 손실이었다면)
            msg = (f"📈 평단 회복!\n{name} ({ticker}) {ret:+.2f}%\n"
                   f"평가액 ₩{int(value/1e6)}M\n\n→ 익절/홀딩 결정 검토")
            res = _send_or_skip(conn, rule_id_recover, ticker, "info", msg, dedup_hours=24)
            out.append({"rule": rule_id_recover, **res})
        elif ret < 0 and ret >= -2.0:
            # 손실 0~-2% — 평단 깨기 trigger
            msg = (f"📉 평단 하락\n{name} ({ticker}) {ret:+.2f}%\n"
                   f"평가액 ₩{int(value/1e6)}M\n\n→ stop 룰 확인")
            res = _send_or_skip(conn, rule_id_fall, ticker, "info", msg, dedup_hours=24)
            out.append({"rule": rule_id_fall, **res})
    return out


# ---------------------------------------------------------------------------
# R4 — TQQQ sweet spot (QQQ DD -5~-15%)
# ---------------------------------------------------------------------------

def check_tqqq_sweet_spot(conn: sqlite3.Connection) -> list[dict]:
    """QQQ 52W DD 가 -5~-15% sweet spot 진입 시 alert."""
    if not RULES_ENABLED.get("R4_tqqq_sweet_spot"):
        return []
    from .realtime_prices import fetch_us_ticker
    q = fetch_us_ticker("QQQ")
    if not q.get("available") or q.get("drawdown_from_52w_high") is None:
        return []
    dd = q["drawdown_from_52w_high"]
    if -0.15 <= dd <= -0.05:
        rule_id = f"R4:tqqq_sweet_spot:dd_{int(dd*100)}"
        msg = (f"🎯 TQQQ Sweet Spot 진입!\n"
               f"QQQ 52W DD {dd*100:+.1f}% — 실증 sweet spot (-5~-15%)\n"
               f"3M 평균 +22% (n=661, win 84%)\n\n"
               f"→ Daily Brief / Alpha Bet 후보 확인")
        res = _send_or_skip(conn, rule_id, "QQQ", "critical", msg, dedup_hours=48)
        return [{"rule": rule_id, **res}]
    return []


# ---------------------------------------------------------------------------
# R5 — BTC -70%+ drawdown (cycle deploy timing)
# ---------------------------------------------------------------------------

def check_btc_drawdown_deep(conn: sqlite3.Connection) -> list[dict]:
    """BTC 52W DD -70%+ 도달 시 alert (cycle bottom deploy 신호)."""
    if not RULES_ENABLED.get("R5_btc_drawdown_deep"):
        return []
    from .realtime_prices import fetch_upbit
    q = fetch_upbit("KRW-BTC")
    if not q.get("available") or q.get("drawdown_from_52w_high") is None:
        return []
    dd = q["drawdown_from_52w_high"]
    if dd <= -0.70:
        rule_id = f"R5:btc_dd_deep:{int(abs(dd)*100)}"
        msg = (f"🪙 BTC 깊은 하락!\n"
               f"BTC ₩{int(q['price']):,} · 52W DD {dd*100:+.1f}%\n"
               f"Cycle 1~3 historical trough -78~85% — deploy 검토 영역")
        res = _send_or_skip(conn, rule_id, "BTC", "critical", msg, dedup_hours=72)
        return [{"rule": rule_id, **res}]
    return []


# ---------------------------------------------------------------------------
# R6 — 24h 급등락 ±5%+ (기본 OFF)
# ---------------------------------------------------------------------------

def check_intraday_spike(conn: sqlite3.Connection, holdings: list[dict]) -> list[dict]:
    if not RULES_ENABLED.get("R6_intraday_spike"):
        return []
    out: list[dict] = []
    from .realtime_prices import fetch_holdings_prices
    quotes = fetch_holdings_prices(holdings)
    for ticker, q in quotes.items():
        if not q.get("available") or q.get("change_pct_24h") is None:
            continue
        ch = q["change_pct_24h"]
        if abs(ch) < 0.05:
            continue
        # 매칭 holding
        h = next((x for x in holdings if (x.get("ticker") or "").lower() == ticker.lower()), None)
        if not h or float(h.get("value_krw") or 0) < 5_000_000:
            continue
        sign = "🚀" if ch > 0 else "💧"
        rule_id = f"R6:spike:{ticker}:{int(abs(ch)*100)}"
        msg = (f"{sign} 24h 급변동\n{q.get('name', ticker)} {ch*100:+.1f}%\n"
               f"현재 {q['currency']} {q['price']:,.2f}")
        res = _send_or_skip(conn, rule_id, ticker, "info", msg, dedup_hours=6)
        out.append({"rule": rule_id, **res})
    return out


# ---------------------------------------------------------------------------
# R7 — Alpha 후보 신규 발견 (engine universe rows)
# ---------------------------------------------------------------------------

def check_new_alpha_discovery(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """score≥80 + DD≤-10% 신규 발견 시 alert."""
    if not RULES_ENABLED.get("R7_new_alpha_discovery"):
        return []
    out: list[dict] = []
    for r in (rows or []):
        scores = r.get("scores") or {}
        md = r.get("market_data") or {}
        score = scores.get("final_score")
        dd = md.get("drawdown_from_52w_high")
        if not score or score < 80 or dd is None or dd > -0.10:
            continue
        ticker = r.get("ticker") or "?"
        name = r.get("name_ko") or r.get("name_en") or ticker
        rule_id = f"R7:alpha:{ticker}"
        msg = (f"💎 신규 Alpha 발견\n{name} ({ticker})\n"
               f"Score {score:.0f}/100 · DD {dd*100:+.1f}%\n\n"
               f"→ Discovery 탭 확인 후 deep-dive")
        res = _send_or_skip(conn, rule_id, ticker, "info", msg, dedup_hours=24)
        out.append({"rule": rule_id, **res})
    return out


# ---------------------------------------------------------------------------
# R8 — +100% Watch (Hyper-Growth + Catalyst)
# ---------------------------------------------------------------------------

def check_hyper_growth_watch(conn: sqlite3.Connection) -> list[dict]:
    """+100% 가능 후보 자동 발굴 — 단일 source.

    데이터 소스:
      1. DB growth_scores (weekly_scan 이 저장한 confluence ≥60 종목)
      2. 신규 진입 (오늘 score ≥60, 지난 30일엔 X) 우선 surface

    이 함수가 *유일한* hyper-growth alert 발화. weekly_scan 은 DB 저장만.
    """
    if not RULES_ENABLED.get("R8_hyper_growth_watch"):
        return []
    out: list[dict] = []

    # 신규 진입 종목 DB 에서 직접 detect — single source
    try:
        import datetime as _dt
        cur = conn.cursor()
        today_iso = _dt.date.today().isoformat()
        cutoff_30d = (_dt.date.today() - _dt.timedelta(days=30)).isoformat()

        # 신규 진입: 최근 7일 안 score ≥ 40 + 30일 이전엔 < 40 (debug 단계 임계 낮춤)
        cur.execute("""
            SELECT t.ticker, t.name, t.market, t.catalyst, t.score, t.yoy_recent
            FROM growth_scores t
            WHERE t.scan_date >= datetime('now', '-7 days')
              AND t.score >= 40
              AND NOT EXISTS (
                  SELECT 1 FROM growth_scores p
                  WHERE p.ticker = t.ticker
                    AND p.scan_date < t.scan_date
                    AND p.scan_date >= ?
                    AND p.score >= 40
              )
            ORDER BY t.score DESC LIMIT 8
        """, (cutoff_30d,))
        new_entrants = cur.fetchall()
    except Exception as e:
        log.debug("R8 신규 진입 DB 조회 실패: %s", e)
        new_entrants = []

    if new_entrants:
        # 한 번에 한 메시지로 묶음 + position sizing 권장
        try:
            from .position_sizing import recommend_size_for_bet
            from .nw_snapshot import calculate_current_nw
            nw_krw = calculate_current_nw().get("nw_krw", 0)
        except Exception:
            nw_krw = 0

        lines = ["💎 신규 +100% Watch 후보 + 권장 사이즈"]
        for row in new_entrants[:8]:
            ticker, name, market, cat, score, yoy = row
            yoy_str = f" · YoY {yoy*100:+.0f}%" if yoy is not None else ""
            cat_str = f" · {cat}" if cat else ""
            # Position size 권장
            size_str = ""
            if nw_krw > 0:
                try:
                    rec = recommend_size_for_bet(nw_krw=nw_krw, confluence_score=score)
                    krw_m = rec["recommended_krw"] / 1e6
                    pct = rec["recommended_pct"] * 100
                    if krw_m > 0.5:  # ₩0.5M 이상만 표시
                        size_str = f"\n     💼 권장: ₩{krw_m:.0f}M ({pct:.0f}% NW)"
                except Exception:
                    pass
            lines.append(f"  • {name} ({ticker}) Score {score:.0f}{yoy_str}{cat_str}{size_str}")
        lines.append("")
        lines.append("→ 대시보드 Discovery 탭 + Valuation 검토 후 알파 베팅 후보로 승격")
        msg = "\n".join(lines)

        # dedup: 같은 ticker set 대해 24h 한 번만
        ticker_key = ",".join(sorted([r[0] for r in new_entrants]))[:60]
        rule_id = f"R8:new_entrants:{ticker_key}"
        result = _send_or_skip(conn, rule_id, None, "critical", msg, dedup_hours=24)
        out.append({"rule": rule_id, "n_entrants": len(new_entrants), **result})
        return out

    # 신규 진입 없으면 침묵 — weekly_scan 이 다시 DB 업데이트할 때까지 대기
    return out


# ---------------------------------------------------------------------------
# R9 — Phase 전환 NW 도달 알림
# ---------------------------------------------------------------------------

def check_phase_transition(conn: sqlite3.Connection) -> list[dict]:
    """NW snapshot 기반 Phase 전환 도달 자동 감지.

    어제까지 < 임계, 오늘 ≥ 임계 인 경우 alert.
    매 cycle 한 번만 (dedup_hours=720 = 30일).
    """
    if not RULES_ENABLED.get("R9_phase_transition"):
        return []
    out: list[dict] = []
    try:
        from .nw_snapshot import calculate_current_nw, get_recent_snapshots
        snaps = get_recent_snapshots(days=7)
        if len(snaps) < 2:
            # snapshot 데이터 부족 — 첫 진입은 알림 X (false trigger 방지)
            return out
        today_nw = snaps[0]["nw_krw"]
        yesterday_nw = snaps[1]["nw_krw"]
        for threshold, key, title, body in _PHASE_TRANSITIONS:
            if yesterday_nw < threshold <= today_nw:
                rule_id = f"R9:phase:{key}"
                msg = (f"{title}\n\n"
                       f"NW ₩{today_nw/1e6:.0f}M 도달 (어제 ₩{yesterday_nw/1e6:.0f}M)\n\n"
                       f"{body}\n\n"
                       f"→ 대시보드 portfolio + alpha_bets 검토")
                # 30일 dedup — 같은 임계 한 번만
                result = _send_or_skip(conn, rule_id, None, "critical", msg, dedup_hours=720)
                out.append({"rule": rule_id, "threshold": threshold, **result})
    except Exception as e:
        log.debug("R9 phase transition 실패: %s", e)
    return out


# ---------------------------------------------------------------------------
# R10 — 세금 캘린더 (12월 양도세 공제)
# ---------------------------------------------------------------------------

def check_tax_calendar(conn: sqlite3.Connection) -> list[dict]:
    """12월 첫주 텔레그램: 양도세 공제 한도 활용 reminder.

    Korea: 해외주식 양도세 연 ₩250만 공제. 매년 12월 한도 안 채우면 사라짐.
    """
    if not RULES_ENABLED.get("R10_tax_calendar"):
        return []
    import datetime as _dt
    now_kst = _dt.datetime.utcnow() + _dt.timedelta(hours=9)
    # 12월 1~7일 사이 한 번만
    if now_kst.month != 12 or now_kst.day > 7:
        return []
    rule_id = f"R10:tax:{now_kst.year}"
    msg = (f"💵 해외주식 양도세 reminder ({now_kst.year}년)\n\n"
           f"연 ₩250만 공제 한도 — 12월 안 채우면 사라짐.\n"
           f"올해 실현 PnL 확인 후 ₩250만 한도까지 익절 검토.\n\n"
           f"→ 토스증권 매도 + 손익 확인 (당년 결제일 기준 12/30)")
    # 1년 dedup — 매년 한 번
    result = _send_or_skip(conn, rule_id, None, "info", msg, dedup_hours=8760)
    return [{"rule": rule_id, **result}]


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

def run_alert_cycle(holdings: list[dict] | None = None,
                    rows: list[dict] | None = None) -> dict[str, Any]:
    """한 번의 alert cycle 실행. GitHub Actions cron 이 30분마다 호출."""
    from . import database as db
    summary: dict[str, Any] = {"runs": [], "ts": _now_utc_iso()}
    # holdings auto-load
    if holdings is None:
        try:
            path = Path(__file__).resolve().parents[1] / "data" / "portfolio.json"
            with open(path, encoding="utf-8") as f:
                holdings = (json.load(f) or {}).get("holdings", [])
        except Exception as e:
            log.warning("portfolio.json 로드 실패: %s", e)
            holdings = []
    with db.db_session() as conn:
        db.init_schema(conn)
        summary["runs"].extend(check_alpha_bet_signals(conn, holdings))
        summary["runs"].extend(check_underlying_target(conn))
        summary["runs"].extend(check_breakeven_cross(conn, holdings))
        summary["runs"].extend(check_tqqq_sweet_spot(conn))
        summary["runs"].extend(check_btc_drawdown_deep(conn))
        summary["runs"].extend(check_intraday_spike(conn, holdings))
        if rows:
            summary["runs"].extend(check_new_alpha_discovery(conn, rows))
        # R8 — 항시 실행 (dedup 24h 가 같은 ticker 중복 방지)
        # 단 DB 조회만이라 비용 ↓
        summary["runs"].extend(check_hyper_growth_watch(conn))
        # R9 — Phase 전환 (매시간 체크, dedup 30일)
        summary["runs"].extend(check_phase_transition(conn))
        # R10 — 세금 캘린더 (12월 첫주만, dedup 1년)
        summary["runs"].extend(check_tax_calendar(conn))
    sent = sum(1 for r in summary["runs"] if r.get("sent"))
    summary["sent_count"] = sent
    summary["total_rules_evaluated"] = len(summary["runs"])
    return summary


if __name__ == "__main__":
    # python -m src.alert_engine — 수동 실행 (디버그)
    result = run_alert_cycle()
    print(json.dumps(result, indent=2, ensure_ascii=False))
