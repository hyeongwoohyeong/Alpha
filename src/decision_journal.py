"""Decision Journal (Phase 4-B) — 사용자가 직접 내린 투자 의사결정을 기록하고,
엔진이 1·3·6개월 뒤 사후 채점해 결정의 예측력을 정직하게 드러낸다.

원칙 (Phase 1·2·4-A 모듈과 동일):
- Rule-based. LLM 없이 완전 동작.
- decision_journal.json 이 없거나 깨져도 예외를 위로 던지지 않는다 — graceful.
- 채점은 거친 버킷 (좋은 결정 / 중립 / 아쉬운 결정 / 채점 보류) — false precision 금지.

영속 구조:
- 사용자 입력 결정 → data/decision_journal.json (GitHub Contents API commit).
  Streamlit Cloud 런타임 alpha.db 는 파이프라인마다 덮어쓰여 사라지므로
  watchlist 와 동일하게 JSON + GitHub commit 으로 보존한다.
- 채점 결과 → DB 테이블 decision_grades (파이프라인이 alpha.db 를 commit 하므로 보존).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("decision_journal")

# 채점 등급 (한국어 — false precision 금지, 거친 버킷)
GRADE_GOOD = "좋은 결정"
GRADE_NEUTRAL = "중립"
GRADE_POOR = "아쉬운 결정"
GRADE_PENDING = "채점 보류"

# 결정 액션 종류
ACTIONS = ("BUY", "ADD", "TRIM", "SELL", "HOLD", "SKIP", "WATCH")

# 노출을 취했/유지한 결정 vs 줄인/보류한 결정
_EXPOSURE_ACTIONS = {"BUY", "ADD", "HOLD"}
_REDUCE_ACTIONS = {"TRIM", "SELL", "SKIP", "WATCH"}


# ---------------------------------------------------------------------------
# decision_journal.json 로드 / 저장
# ---------------------------------------------------------------------------

def _journal_path(project_root: Path | str | None = None) -> Path:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    return Path(project_root) / "data" / "decision_journal.json"


def load_decisions(project_root: Path | str | None = None) -> list[dict]:
    """data/decision_journal.json (JSON 리스트) 로드.

    파일이 없거나 깨졌으면 경고 로그만 남기고 [] 반환 — 절대 raise 하지 않는다.
    """
    path = _journal_path(project_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("decision_journal.json 파싱 실패: %s", e)
        return []
    if not isinstance(raw, list):
        log.warning("decision_journal.json 이 리스트가 아님 — [] 로 처리")
        return []
    return [d for d in raw if isinstance(d, dict)]


def save_decisions(decisions: list[dict]) -> dict[str, Any]:
    """결정 리스트를 data/decision_journal.json 에 저장 + GitHub Contents API commit.

    Returns: {"local": bool, "github": bool, "github_status": str}
    """
    project_root = Path(__file__).resolve().parent.parent
    path = _journal_path(project_root)
    content_str = json.dumps(decisions, ensure_ascii=False, indent=2)

    # 1) Local file (즉시 UI 반영)
    local_ok = True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content_str, encoding="utf-8")
    except Exception as e:
        log.warning("decision_journal local save 실패: %s", e)
        local_ok = False

    # 2) GitHub commit (영구 보존)
    gh_ok, gh_status = False, "no_pat"
    try:
        from .universe import commit_json_to_github
        gh_ok, gh_status = commit_json_to_github(
            "data/decision_journal.json",
            content_str,
            f"chore: update decision journal ({len(decisions)} entries)",
        )
    except Exception as e:
        log.warning("decision_journal GitHub commit 예외: %s", e)
        gh_ok, gh_status = False, f"commit_exception: {type(e).__name__}"

    if gh_ok:
        log.info("decision_journal GitHub commit OK (%d entries)", len(decisions))
    elif gh_status == "no_pat":
        log.info("decision_journal GitHub commit skip — GITHUB_PAT 미설정 (local-only)")
    else:
        log.warning("decision_journal GitHub commit 실패: %s", gh_status)

    return {"local": local_ok, "github": gh_ok, "github_status": gh_status}


def add_decision(entry: dict) -> dict[str, Any]:
    """결정 항목에 안정적 고유 id 와 created_at 을 부여하고 저장.

    id 형식: f"{decision_date}-{ticker}-{seq}" — 같은 날짜+티커 내에서 seq 로 고유화.
    Returns: save_decisions 의 결과 + {"id": ...}.
    """
    decisions = load_decisions()

    entry = dict(entry)  # 호출자 dict 변형 방지
    decision_date = str(entry.get("decision_date") or _dt.date.today().isoformat())
    ticker = str(entry.get("ticker") or "").upper().strip()
    entry["decision_date"] = decision_date
    entry["ticker"] = ticker

    # 같은 날짜+티커 내 seq 결정 — 기존 id 들에서 prefix 매칭 후 max+1
    prefix = f"{decision_date}-{ticker}-"
    existing_seqs: list[int] = []
    for d in decisions:
        did = str(d.get("id") or "")
        if did.startswith(prefix):
            tail = did[len(prefix):]
            try:
                existing_seqs.append(int(tail))
            except ValueError:
                continue
    seq = (max(existing_seqs) + 1) if existing_seqs else 1
    entry["id"] = f"{prefix}{seq}"

    if not entry.get("created_at"):
        entry["created_at"] = _dt.datetime.now().isoformat(timespec="seconds")

    decisions.append(entry)
    result = save_decisions(decisions)
    result["id"] = entry["id"]
    return result


# ---------------------------------------------------------------------------
# 채점 — 순수 rule-based
# ---------------------------------------------------------------------------

def _fmt_pp(x: float) -> str:
    """상대 수익률을 +X.Xp 형식으로."""
    return f"{x:+.1f}%p"


def _fmt_pct(x: float) -> str:
    return f"{x:+.1f}%"


def generate_decision_grade(
    action: str,
    price_at_decision: float | None,
    price_at_milestone: float | None,
    benchmark_return_pct: float | None,
) -> dict[str, Any]:
    """결정 채점 — 순수 rule-based.

    window_return = price_at_milestone / price_at_decision - 1 (%, QQQ 대비 상대 = relative).
    benchmark_return_pct = QQQ 의 동일 구간 수익률 (%).

    - 가격/벤치마크 누락 → "채점 보류".
    - BUY/ADD/HOLD (노출 취득·유지): 오르고 QQQ 이상 → 좋은 결정,
      내리고 QQQ 미달 → 아쉬운 결정, 그 외 중립.
    - TRIM/SELL (노출 축소): 이후 종목이 부진하면 좋은 결정.
    - SKIP/WATCH (매수 보류·관망): SELL 과 동일 — 종목이 내리거나 뒤처지면 좋은 결정.

    Returns: {"return_pct", "relative_pct", "grade", "grade_note"}
    """
    action = (action or "").upper().strip()

    if price_at_decision is None or price_at_milestone is None \
            or benchmark_return_pct is None:
        return {
            "return_pct": None,
            "relative_pct": None,
            "grade": GRADE_PENDING,
            "grade_note": "가격 데이터를 확인할 수 없어 채점을 보류합니다.",
        }
    try:
        pd_ = float(price_at_decision)
        pm_ = float(price_at_milestone)
        bench = float(benchmark_return_pct)
    except (TypeError, ValueError):
        return {
            "return_pct": None,
            "relative_pct": None,
            "grade": GRADE_PENDING,
            "grade_note": "가격 데이터 형식 오류로 채점을 보류합니다.",
        }
    if pd_ <= 0:
        return {
            "return_pct": None,
            "relative_pct": None,
            "grade": GRADE_PENDING,
            "grade_note": "결정 시점 가격이 유효하지 않아 채점을 보류합니다.",
        }

    window_return = (pm_ / pd_ - 1.0) * 100.0
    relative = window_return - bench

    if action in _EXPOSURE_ACTIONS:
        if window_return > 0 and relative >= 0:
            grade = GRADE_GOOD
        elif window_return < 0 and relative < 0:
            grade = GRADE_POOR
        else:
            grade = GRADE_NEUTRAL
        verb = "매수·보유" if action != "HOLD" else "보유 유지"
    elif action in _REDUCE_ACTIONS:
        # 노출을 줄였으니, 이후 종목이 부진해야 좋은 결정
        if window_return < 0 or relative < 0:
            grade = GRADE_GOOD
        elif window_return > 0 and relative > 0:
            grade = GRADE_POOR
        else:
            grade = GRADE_NEUTRAL
        verb = "매도·축소" if action in ("TRIM", "SELL") else "매수 보류"
    else:
        # 알 수 없는 action — 노출 취득으로 보수적 처리
        if window_return > 0 and relative >= 0:
            grade = GRADE_GOOD
        elif window_return < 0 and relative < 0:
            grade = GRADE_POOR
        else:
            grade = GRADE_NEUTRAL
        verb = "결정"

    # grade_note — 숫자를 인용한 한 문장 한국어 설명
    if grade == GRADE_GOOD:
        if action in _EXPOSURE_ACTIONS:
            grade_note = (
                f"{verb} 후 QQQ 대비 {_fmt_pp(relative)} "
                f"({_fmt_pct(window_return)}) — 좋은 결정"
            )
        else:
            stance = ("노출을 줄인 것이" if action in ("TRIM", "SELL")
                      else "매수를 보류한 것이")
            grade_note = (
                f"{verb} 이후 종목이 {_fmt_pct(window_return)} "
                f"(QQQ 대비 {_fmt_pp(relative)}) — {stance} 적절했던 좋은 결정"
            )
    elif grade == GRADE_POOR:
        if action in _EXPOSURE_ACTIONS:
            grade_note = (
                f"{verb} 후 QQQ 대비 {_fmt_pp(relative)} "
                f"({_fmt_pct(window_return)}) — 아쉬운 결정"
            )
        else:
            stance = ("노출을 줄인 것이" if action in ("TRIM", "SELL")
                      else "매수를 보류한 것이")
            grade_note = (
                f"{verb} 이후 종목이 {_fmt_pct(window_return)} 상승 "
                f"(QQQ 대비 {_fmt_pp(relative)}) — {stance} 아쉬운 결정"
            )
    else:
        grade_note = (
            f"{verb} 후 {_fmt_pct(window_return)} "
            f"(QQQ 대비 {_fmt_pp(relative)}) — 뚜렷한 우열 없음, 중립"
        )

    return {
        "return_pct": round(window_return, 2),
        "relative_pct": round(relative, 2),
        "grade": grade,
        "grade_note": grade_note,
    }


# ---------------------------------------------------------------------------
# 스코어보드 요약
# ---------------------------------------------------------------------------

def summarize_decisions(
    decisions: list[dict], grades_by_id: dict[str, list]
) -> dict[str, Any]:
    """Decision Journal 스코어보드 요약.

    grades_by_id: decision_id -> 채점 행 리스트 (sqlite3.Row 또는 dict).
    각 결정의 '대표 등급' 은 가장 긴 milestone (6M>3M>1M) 의 채점을 우선 사용한다.

    Returns: {total, graded, n_good, n_neutral, n_poor, hit_rate}
    """
    total = len(decisions)
    n_good = n_neutral = n_poor = 0
    graded = 0

    milestone_rank = {"6M": 3, "3M": 2, "1M": 1}

    for d in decisions:
        did = str(d.get("id") or "")
        rows = grades_by_id.get(did) or []
        # 채점 보류가 아닌, 가장 긴 milestone 의 등급을 대표값으로
        best_row = None
        best_rank = -1
        for r in rows:
            grade = r["grade"] if hasattr(r, "keys") else r.get("grade")
            ms = r["milestone"] if hasattr(r, "keys") else r.get("milestone")
            if grade in (None, GRADE_PENDING):
                continue
            rank = milestone_rank.get(ms, 0)
            if rank > best_rank:
                best_rank = rank
                best_row = r
        if best_row is None:
            continue
        grade = best_row["grade"] if hasattr(best_row, "keys") \
            else best_row.get("grade")
        graded += 1
        if grade == GRADE_GOOD:
            n_good += 1
        elif grade == GRADE_POOR:
            n_poor += 1
        else:
            n_neutral += 1

    # hit-rate = 좋은 결정 / (좋은 + 아쉬운) — 중립 제외
    decisive = n_good + n_poor
    hit_rate = (n_good / decisive) if decisive > 0 else None

    return {
        "total": total,
        "graded": graded,
        "n_good": n_good,
        "n_neutral": n_neutral,
        "n_poor": n_poor,
        "hit_rate": hit_rate,
    }
