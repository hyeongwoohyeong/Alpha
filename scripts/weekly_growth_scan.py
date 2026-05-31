"""Weekly Universe-wide Growth Scan.

매주 일요일 새벽 (UTC 18:00 토요일 = KST 03:00 일요일) 실행.

작업:
  1. kr_dynamic_universe.csv (1500~2000 종목) + wide_universe.csv (300 종목) 로드
  2. 각 종목 Growth Momentum Score 계산 (yfinance 분기 매출 fetch)
  3. catalyst_auto_match 로 catalyst tag 자동 부여
  4. DB growth_scores 테이블 저장
  5. 직전 주 vs 이번 주 비교 → 신규 진입 (전엔 score 낮았는데 이번에 ≥70) → 텔레그램 alert
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("weekly_growth_scan")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import database as db
from src.growth_momentum import score_ticker
from src.catalyst_auto_match import match_catalyst, enrich_with_yfinance
from src.telegram_notifier import send_telegram_plain
from src.confluence_score import calculate_confluence_score, is_plus_100_candidate

DATA_DIR = ROOT / "data"
MIN_SCORE_TO_SAVE = 40.0      # 40+ 만 DB 저장 (저점 noise 제거)
HYPER_GROWTH_THRESHOLD = 70.0
SAMPLE_SIZE = 200              # GitHub Actions timeout 고려 (전체 cover 못 함 — N주 rotation)


def load_kr_dynamic() -> list[dict]:
    """KR dynamic universe 로드 — 없으면 빈 list."""
    path = DATA_DIR / "kr_dynamic_universe.csv"
    if not path.exists():
        log.warning("kr_dynamic_universe.csv 없음 — build_dynamic_universe.py 먼저 실행 필요")
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            break
        # reset file
    with open(path, encoding="utf-8") as f:
        # skip comment lines
        lines = [l for l in f if not l.startswith("#")]
    reader = csv.DictReader(lines)
    for row in reader:
        out.append({
            "ticker": row.get("ticker"),
            "name": row.get("name_ko"),
            "market": row.get("market"),
            "market_cap_krw": float(row.get("market_cap_krw") or 0),
            "tier": row.get("market_cap_tier"),
        })
    return out


def load_us_wide() -> list[dict]:
    """US universe 로드 — dynamic (NASDAQ+NYSE+AMEX 전종목) + manual curated 합집합."""
    seen = set()
    out = []
    # 1) US dynamic (fdr fetched, daily rebuild)
    dyn_path = DATA_DIR / "us_dynamic_universe.csv"
    if dyn_path.exists():
        with open(dyn_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                break
        # reset, re-read skipping comments
        with open(dyn_path, encoding="utf-8") as f:
            lines = [l for l in f if not l.startswith("#")]
        for row in csv.DictReader(lines):
            t = row.get("ticker", "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append({
                "ticker": t,
                "name": row.get("name"),
                "market": row.get("market"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "tier": row.get("market_cap_tier"),
            })
    # 2) Manual curated (wide_universe.csv) — 빠진 catalyst 종목 보완
    manual_path = DATA_DIR / "wide_universe.csv"
    if manual_path.exists():
        with open(manual_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                t = row.get("ticker", "").strip()
                if not t or t in seen:
                    continue
                seen.add(t)
                out.append({
                    "ticker": t,
                    "name": row.get("name"),
                    "market": row.get("exchange"),
                    "sector": row.get("sector"),
                    "industry": row.get("industry"),
                    "tier": row.get("market_cap_tier"),
                })
    return out


def score_and_save(rows: list[dict], scan_date: str, day_index: int) -> list[dict]:
    """Sampling + score + DB 저장.

    day_index (day-of-year 1~365): rotation 으로 매일 다른 subset cover.
    전체 universe 약 12일에 1회 cover (2300종 / 200/일).
    """
    if not rows:
        return []
    days_to_cover = max(1, (len(rows) // SAMPLE_SIZE) + 1)
    rot = day_index % days_to_cover
    sliced = rows[rot * SAMPLE_SIZE : (rot + 1) * SAMPLE_SIZE]
    log.info("Today scanning %d/%d (rotation %d/%d, 전체 cover 주기 %d일)",
             len(sliced), len(rows), rot + 1, days_to_cover, days_to_cover)

    hits = []
    with db.db_session() as conn:
        db.init_schema(conn)
        cur = conn.cursor()
        for i, r in enumerate(sliced):
            ticker = r["ticker"]
            if (i + 1) % 20 == 0:
                log.info("  진행 %d/%d", i + 1, len(sliced))
            try:
                # Confluence — 4 신호 + valuation 통합
                cf = calculate_confluence_score(ticker)
            except Exception as e:
                log.debug("confluence %s 실패: %s", ticker, e)
                continue
            score = cf.get("total_score", 0) or 0
            if score < MIN_SCORE_TO_SAVE:
                continue
            # 극단 과대평가 제외
            if not cf.get("valuation_pass", True):
                log.info("  %s 극단 과대평가 — skip", ticker)
                continue
            cur.execute(
                "INSERT OR REPLACE INTO growth_scores "
                "(scan_date, ticker, name, market, catalyst, score, yoy_recent, "
                "is_accelerating, components_json, market_cap_krw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (scan_date, ticker, r.get("name"), r.get("market"),
                 cf.get("catalyst"), score, cf.get("yoy_recent"),
                 1 if cf.get("is_accelerating") else 0,
                 json.dumps(cf.get("breakdown", {}), ensure_ascii=False),
                 r.get("market_cap_krw"))
            )
            hits.append({
                "ticker": ticker, "name": r.get("name"),
                "score": score, "catalyst": cf.get("catalyst"),
                "is_high_confidence": cf.get("is_high_confidence"),
                "breakdown": cf.get("breakdown"),
            })
        conn.commit()
    log.info("Confluence ≥ %.0f: %d / %d", MIN_SCORE_TO_SAVE, len(hits), len(sliced))
    return hits


def detect_new_entrants(scan_date: str, prev_week_offset: int = 7) -> list[dict]:
    """직전 주 vs 이번 주 비교 — 신규 진입 (이번 주 ≥70, 직전엔 X)."""
    prev_date = (_dt.date.fromisoformat(scan_date) - _dt.timedelta(days=prev_week_offset)).isoformat()
    with db.db_session() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.ticker, t.name, t.market, t.catalyst, t.score, t.yoy_recent "
            "FROM growth_scores t "
            "WHERE t.scan_date = ? AND t.score >= ? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM growth_scores p "
            "  WHERE p.ticker = t.ticker AND p.scan_date < ? "
            "  AND p.scan_date >= ? AND p.score >= ?"
            ")",
            (scan_date, HYPER_GROWTH_THRESHOLD, scan_date,
             (_dt.date.fromisoformat(scan_date) - _dt.timedelta(days=30)).isoformat(),
             HYPER_GROWTH_THRESHOLD)
        )
        rows = cur.fetchall()
    return [{"ticker": r[0], "name": r[1], "market": r[2],
             "catalyst": r[3], "score": r[4], "yoy_recent": r[5]} for r in rows]


def alert_new_entrants(new_hits: list[dict]) -> None:
    """DEPRECATED — alert_engine.R8 가 DB 조회로 자동 발화.

    weekly_scan 은 *데이터 저장만*. 실제 텔레그램 발화는 alert_engine 단일 책임.
    Dedup 도 alert_engine 이 자체 alert_log 로 처리.
    """
    if not new_hits:
        log.info("신규 진입 hyper-growth 종목 없음")
        return
    log.info("신규 진입 %d 건 DB 저장 완료 — alert_engine R8 가 다음 cycle 에 발화",
             len(new_hits))


def main():
    scan_date = _dt.date.today().isoformat()
    log.info("=== Weekly Growth Scan — %s ===", scan_date)

    # Rotation day index (day-of-year 1~365)
    day_index = _dt.date.today().timetuple().tm_yday

    kr_rows = load_kr_dynamic()
    us_rows = load_us_wide()
    all_rows = kr_rows + us_rows
    log.info("Universe 총: KR %d + US %d = %d", len(kr_rows), len(us_rows), len(all_rows))

    # Score + 저장
    hits = score_and_save(all_rows, scan_date, day_index)

    # 신규 진입 alert
    new_entrants = detect_new_entrants(scan_date)
    alert_new_entrants(new_entrants)

    log.info("=== 완료 — hits %d, new entrants %d ===", len(hits), len(new_entrants))


if __name__ == "__main__":
    main()
