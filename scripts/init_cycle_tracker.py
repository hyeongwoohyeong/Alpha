"""
cycle_tracker.db 초기화 — schema 생성 + seed data insert.

사용법:
  python scripts/init_cycle_tracker.py            # 신규 생성 (이미 있으면 skip)
  python scripts/init_cycle_tracker.py --reset    # drop + 재생성

DB: data/cycle_tracker.db
Seed: data/cycle_tracker_seed.json

생성 테이블:
  - cycles               : 12 active + 15 watch list cycle metadata
  - phase_history        : cycle phase 전환 이력
  - indicator_snapshots  : 선행 지표 시계열 snapshot
  - emerging_candidates  : 새로 발견된 cycle 후보 (Phase 5 Discovery)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "cycle_tracker.db"
SEED_PATH = REPO_ROOT / "data" / "cycle_tracker_seed.json"


SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id INTEGER PRIMARY KEY,
    cycle_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('Cycle', 'Structural Theme', 'Event-driven', 'Policy-driven')),
    current_phase INTEGER CHECK(current_phase BETWEEN 0 AND 5),
    phase_label TEXT,
    phase_confidence TEXT CHECK(phase_confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    confidence_basis TEXT,                  -- JSON array of strings
    cycle_strength_score INTEGER CHECK(cycle_strength_score BETWEEN 0 AND 100),
    priced_in_score INTEGER CHECK(priced_in_score BETWEEN 0 AND 100),
    leading_indicators TEXT,                -- JSON array
    trigger_events TEXT,                    -- JSON array
    watch_date DATE,
    watch_note TEXT,
    primary_beneficiaries TEXT,             -- JSON array (tickers)
    secondary_beneficiaries TEXT,           -- JSON array
    etfs TEXT,                              -- JSON array
    korea_proxy_stocks TEXT,                -- JSON array
    leverage_options TEXT,
    update_frequency TEXT CHECK(update_frequency IN ('daily', 'weekly', 'monthly')),
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    next_review_date DATE,
    user_active BOOLEAN DEFAULT 1,          -- 1 = active 12, 0 = watch list
    user_priority TEXT,                     -- '★★★', '★★', '★', 'watch'
    discovery_source TEXT DEFAULT 'manual', -- 'manual', 'news_cluster', 'capex_spike', 'etf_launch', 'policy'
    user_note TEXT,
    reason_demoted TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS phase_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES cycles(cycle_id),
    from_phase INTEGER,
    to_phase INTEGER,
    transition_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    signals TEXT,                           -- JSON: 무엇이 transition trigger
    cycle_strength_at_transition INTEGER,
    priced_in_at_transition INTEGER,
    alerted BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS indicator_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES cycles(cycle_id),
    indicator_name TEXT NOT NULL,
    snapshot_date DATE DEFAULT CURRENT_DATE,
    value REAL,
    source TEXT,                            -- 'FRED', 'yfinance', 'ECOS', 'manual'
    raw_metadata TEXT,                      -- JSON optional
    UNIQUE(cycle_id, indicator_name, snapshot_date)
);

CREATE TABLE IF NOT EXISTS emerging_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_name TEXT NOT NULL,
    detection_source TEXT NOT NULL,         -- 'news_cluster', 'capex_spike', 'etf_launch', 'policy'
    detection_signals TEXT,                 -- JSON: 트리거 데이터
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_decision TEXT DEFAULT 'pending' CHECK(user_decision IN ('pending', 'accepted', 'rejected')),
    decided_at TIMESTAMP,
    promoted_to_cycle_id INTEGER REFERENCES cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_cycles_active ON cycles(user_active);
CREATE INDEX IF NOT EXISTS idx_cycles_phase ON cycles(current_phase);
CREATE INDEX IF NOT EXISTS idx_cycles_strength ON cycles(cycle_strength_score DESC);
CREATE INDEX IF NOT EXISTS idx_phase_history_cycle ON phase_history(cycle_id, transition_date DESC);
CREATE INDEX IF NOT EXISTS idx_indicator_cycle_date ON indicator_snapshots(cycle_id, indicator_name, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_emerging_pending ON emerging_candidates(user_decision);
"""


def _json_or_none(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def init_schema(conn: sqlite3.Connection) -> None:
    """4 tables + indexes 생성 (IF NOT EXISTS)."""
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def reset_db() -> None:
    """전체 drop + 재생성."""
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"  ✓ removed {DB_PATH.name}")


def insert_active(conn: sqlite3.Connection, cycle: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cycles (
            cycle_id, cycle_name, category, current_phase, phase_label,
            phase_confidence, confidence_basis, cycle_strength_score, priced_in_score,
            leading_indicators, trigger_events, watch_date, watch_note,
            primary_beneficiaries, secondary_beneficiaries, etfs, korea_proxy_stocks,
            leverage_options, update_frequency, user_active, user_priority,
            discovery_source, user_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            cycle["cycle_id"],
            cycle["cycle_name"],
            cycle["category"],
            cycle["current_phase"],
            cycle.get("phase_label"),
            cycle["phase_confidence"],
            _json_or_none(cycle.get("confidence_basis")),
            cycle["cycle_strength_score"],
            cycle["priced_in_score"],
            _json_or_none(cycle.get("leading_indicators")),
            _json_or_none(cycle.get("trigger_events")),
            cycle.get("watch_date"),
            cycle.get("watch_note"),
            _json_or_none(cycle.get("primary_beneficiaries")),
            _json_or_none(cycle.get("secondary_beneficiaries")),
            _json_or_none(cycle.get("etfs")),
            _json_or_none(cycle.get("korea_proxy_stocks")),
            cycle.get("leverage_options"),
            cycle.get("update_frequency", "weekly"),
            cycle.get("user_priority"),
            cycle.get("discovery_source", "manual"),
            cycle.get("user_note"),
        ),
    )


def insert_watch(conn: sqlite3.Connection, cycle: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cycles (
            cycle_id, cycle_name, category, current_phase, user_active,
            user_priority, reason_demoted, discovery_source
        ) VALUES (?, ?, ?, ?, 0, ?, ?, 'manual')
        """,
        (
            cycle["cycle_id"],
            cycle["cycle_name"],
            cycle["category"],
            cycle.get("current_phase", 0),
            cycle.get("user_priority", "watch"),
            cycle.get("reason_demoted"),
        ),
    )


def seed_from_json(conn: sqlite3.Connection) -> tuple[int, int]:
    if not SEED_PATH.exists():
        print(f"  ! seed file not found: {SEED_PATH}", file=sys.stderr)
        return 0, 0
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    active = seed.get("active_cycles", [])
    watch = seed.get("watch_list", [])
    for cycle in active:
        insert_active(conn, cycle)
    for cycle in watch:
        insert_watch(conn, cycle)
    conn.commit()
    return len(active), len(watch)


def summarize(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print()
    print("== Active 12 ==")
    rows = cur.execute(
        """
        SELECT cycle_id, cycle_name, category, current_phase,
               cycle_strength_score, priced_in_score, user_priority
        FROM cycles
        WHERE user_active = 1
        ORDER BY cycle_id
        """
    ).fetchall()
    for r in rows:
        print(
            f"  #{r[0]:>2} P{r[3]} S{r[4]:>3} P{r[5]:>3} {r[6] or '-':>5}  "
            f"[{r[2][:10]:<10}] {r[1]}"
        )

    print()
    print("== Watch List ==")
    rows = cur.execute(
        """
        SELECT cycle_id, cycle_name, current_phase, reason_demoted
        FROM cycles
        WHERE user_active = 0
        ORDER BY cycle_id
        """
    ).fetchall()
    for r in rows:
        print(f"  W{r[0]} P{r[2]}  {r[1]:<40} — {r[3] or ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop and recreate")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.reset:
        reset_db()

    fresh = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    try:
        init_schema(conn)
        if fresh or args.reset:
            active_n, watch_n = seed_from_json(conn)
            print(f"  ✓ seeded {active_n} active + {watch_n} watch")
        else:
            # Refresh seed (safe — INSERT OR REPLACE)
            active_n, watch_n = seed_from_json(conn)
            print(f"  ✓ refreshed {active_n} active + {watch_n} watch")
        summarize(conn)
    finally:
        conn.close()

    print()
    print(f"  ✓ {DB_PATH.relative_to(REPO_ROOT)} ready ({datetime.now(timezone.utc).isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
