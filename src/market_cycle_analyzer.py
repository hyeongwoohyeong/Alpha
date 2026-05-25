"""Market Cycle Research Engine — Stage A (시장 사이클 실증 분석).

기존 "Phase 4" 백테스트는 사용자가 미리 정한 룰(낙폭 단계투입 사다리·과열 밴드)을
'검증'만 한다 — 가설 확인일 뿐, 시장 자체를 특성화하지 않는다. Stage A 는 장기
역사로부터 시장의 base rate(조정 빈도·하락/회복 기간·상승장 길이·신고가 근접
forward return·추세 상태별 forward return)를 *추출*하는 순수 실증 분석 모듈이다.

핵심 원칙 (소유자가 엄격히 요구함):
- 정직성 우선. 모든 통계는 `sample_count` 를 동반한다. 1999년 이후 독립적인
  대형 사이클은 4~6개뿐 — 깊은 낙폭 통계는 표본이 작다. 그 사실을 명시한다.
- 사용자 편향 강화 금지. 데이터가 "신고가 근처 매수의 forward return 이 양호했다"
  고 말하면 그대로 보고한다.
- 룰 기반·순수 파이썬 (sklearn/ML 없음). 자동 룰 발굴·feature 조합 마이닝 없음
  (과적합 함정으로 의도적으로 제외) — Stage A 는 서술적 실증 통계만.
- graceful — 모든 함수는 예외를 던지지 않고 "데이터 부족" 으로 우아하게 퇴화.
- 모든 사용자 노출 텍스트는 한국어.

대상 자산: QQQ·SPY(배치 결정을 좌우하는 지수) + QLD·TQQQ(레버리지 맥락).
가용한 가장 긴 history 사용 (SPY ~1993, QQQ ~1999, QLD ~2006, TQQQ ~2010).
상장 전 합성 레버리지 데이터는 만들지 않는다. M7 개별 종목은 Stage A 범위 밖.
"""
from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any

from .utils import get_logger

log = get_logger("market_cycle")

# backtest_engine 의 검증된 헬퍼 재사용
from .backtest_engine import (  # noqa: E402
    FORWARD_WINDOWS,
    _agg,
    _confidence_from_count,
    _forward_mdd,
    _forward_return,
    fetch_historical_market_data,
)

# 조정 깊이 임계값 (낙폭 %, 음수). 클러스터링·base rate 에 공통 사용.
_THRESHOLDS: tuple[float, ...] = (-0.03, -0.05, -0.10, -0.15, -0.20, -0.25, -0.30)

# 빠른 V자 회복 판정 기준 (영업일)
_FAST_RECOVERY_DAYS = 40

# 이벤트로 인정하는 최소 낙폭 (이보다 얕은 흔들림은 무시)
_MIN_EVENT_DEPTH = -0.03


# ---------------------------------------------------------------------------
# 0. 장기 history 확보
# ---------------------------------------------------------------------------

def ensure_long_term_history(
    conn, tickers: tuple[str, ...] = ("QQQ", "SPY", "QLD", "TQQQ")
) -> dict[str, dict[str, Any]]:
    """market_price_history 에 대상 티커의 가장 긴 일봉 history 를 확보.

    이미 충분한 history(>500행 또는 시작일이 상장 직후)면 그대로 사용.
    부족하면 data_cache 를 통해 yfinance period="max" 로 다시 받아 upsert.
    절대 예외를 던지지 않는다.

    Returns: {ticker: {"rows": n, "start": date, "end": date}}
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        from . import database as _db
    except Exception as e:  # pragma: no cover
        log.warning("database import 실패: %s", e)
        return out

    # 충분한 history 의 기대 하한 — 자산별 상장연도 고려한 보수적 행 수
    min_rows = 400

    need_fetch: list[str] = []
    for t in tickers:
        try:
            rows = _db.fetch_market_price_history(conn, t)
        except Exception as e:
            log.debug("[%s] history 조회 실패: %s", t, e)
            rows = []
        n = len(rows)
        if n >= min_rows:
            out[t] = {
                "rows": n,
                "start": (rows[0]["date"] if hasattr(rows[0], "keys") else rows[0][0]),
                "end": (rows[-1]["date"] if hasattr(rows[-1], "keys") else rows[-1][0]),
            }
        else:
            need_fetch.append(t)
            out[t] = {"rows": n, "start": None, "end": None}

    if need_fetch:
        log.info("장기 history 부족 — yfinance period=max 재수집: %s", need_fetch)
        try:
            from . import data_cache as _dc
            _dc.refresh_full_history(conn, need_fetch)
        except Exception as e:
            log.warning("장기 history 재수집 실패 (graceful): %s", e)
        # 재조회
        for t in need_fetch:
            try:
                rows = _db.fetch_market_price_history(conn, t)
                if rows:
                    out[t] = {
                        "rows": len(rows),
                        "start": (rows[0]["date"] if hasattr(rows[0], "keys")
                                  else rows[0][0]),
                        "end": (rows[-1]["date"] if hasattr(rows[-1], "keys")
                                else rows[-1][0]),
                    }
            except Exception as e:
                log.debug("[%s] 재조회 실패: %s", t, e)
    return out


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _load_series(conn, ticker: str) -> tuple[list[float], list[str]]:
    """단일 티커의 (adj_close 리스트, 날짜 리스트). 실패 시 빈 리스트."""
    try:
        data = fetch_historical_market_data(conn, [ticker])
        series = data.get(ticker) or []
        closes = [float(r["adj_close"]) for r in series
                  if r.get("adj_close") is not None]
        dates = [r["date"] for r in series if r.get("adj_close") is not None]
        return closes, dates
    except Exception as e:
        log.warning("[%s] 시계열 로드 실패: %s", ticker, e)
        return [], []


def _year_of(date_iso: str) -> int | None:
    try:
        return int(str(date_iso)[:4])
    except Exception:
        return None


def _thresholds_hit(depth: float) -> list[float]:
    """낙폭 depth(음수)가 통과한 임계 레벨 목록."""
    return [t for t in _THRESHOLDS if depth <= t]


def _rsi(closes: list[float], i: int, period: int = 14) -> float | None:
    if i < period:
        return None
    gains = losses = 0.0
    for k in range(i - period + 1, i + 1):
        delta = closes[k] - closes[k - 1]
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ma(closes: list[float], i: int, window: int) -> float | None:
    if i < window - 1:
        return None
    return sum(closes[i - window + 1:i + 1]) / window


def _drawdown_from_high(closes: list[float], i: int) -> float:
    """i 시점의 running all-time-high 대비 낙폭 (음수, 0 이하)."""
    if i < 0 or i >= len(closes):
        return 0.0
    high = max(closes[:i + 1])
    if high <= 0:
        return 0.0
    return closes[i] / high - 1.0


# ---------------------------------------------------------------------------
# 1. Drawdown event 식별 (클러스터링 필수)
# ---------------------------------------------------------------------------

def identify_drawdown_events(
    closes: list[float], dates: list[str]
) -> list[dict[str, Any]]:
    """running all-time-high 기준 peak→trough→recovery 사이클을 식별.

    **클러스터링 규칙 (필수):**
    하나의 하락 다리(down-leg)가 -5%, -10%, -15% 를 순차로 통과하면 그것은
    `max_depth=-15%` 인 **하나의 이벤트**이지, 세 개가 아니다.

    이벤트 정의 — 명확한 단일 룰:
    - running ATH 가 새 고점을 찍을 때마다 잠재 peak 갱신.
    - 종가가 ATH 대비 `_MIN_EVENT_DEPTH`(-3%) 이상 하락하면 이벤트 시작.
    - 이벤트 진행 중에는 새 저점(부분 반등 후 더 낮은 저점 포함)을 모두 같은
      이벤트의 연장으로 본다 (trough 갱신). 즉 부분 반등 후 재하락은 별개
      이벤트가 아니다.
    - 종가가 직전 peak(ATH)를 회복(>=)하면 이벤트 종료 → recovery_date 기록.
      그 회복일이 곧 새 ATH 의 시작이며, 다음 이벤트는 새 고점에서만 시작한다.
    - 시계열 끝까지 회복 못 하면 recovery_date=None (미회복 진행 이벤트).

    영업일 카운트(인덱스 거리)를 쓴다 — 달력일 아님.
    예외를 던지지 않으며, 데이터 부족 시 빈 리스트.

    Returns: list[dict] — 각 dict 의 키는 docstring 상단 명세 참조.
    """
    events: list[dict[str, Any]] = []
    n = len(closes)
    if n < 30 or len(dates) != n:
        return events

    peak_price = closes[0]
    peak_idx = 0
    in_event = False
    trough_price = closes[0]
    trough_idx = 0

    for i in range(1, n):
        px = closes[i]
        if not in_event:
            if px >= peak_price:
                peak_price = px
                peak_idx = i
            else:
                dd = px / peak_price - 1.0
                if dd <= _MIN_EVENT_DEPTH:
                    # 이벤트 시작
                    in_event = True
                    trough_price = px
                    trough_idx = i
        else:
            # 이벤트 진행 중 — 새 저점 갱신 (부분 반등 후 재하락도 같은 이벤트)
            if px < trough_price:
                trough_price = px
                trough_idx = i
            # peak 회복 → 이벤트 종료
            if px >= peak_price:
                depth = trough_price / peak_price - 1.0
                events.append(_build_event(
                    closes, dates, peak_idx, trough_idx, recovery_idx=i
                ))
                in_event = False
                # 회복일이 새 ATH 시작
                peak_price = px
                peak_idx = i

    # 시계열 끝까지 미회복인 이벤트
    if in_event:
        events.append(_build_event(
            closes, dates, peak_idx, trough_idx, recovery_idx=None
        ))
    return events


def _build_event(
    closes: list[float], dates: list[str],
    peak_idx: int, trough_idx: int, recovery_idx: int | None,
) -> dict[str, Any]:
    """단일 drawdown event dict 구성."""
    peak_price = closes[peak_idx]
    trough_price = closes[trough_idx]
    depth = (trough_price / peak_price - 1.0) if peak_price > 0 else 0.0

    days_p2t = trough_idx - peak_idx
    days_t2r: int | None = None
    total_days: int | None = None
    recovery_date: str | None = None
    if recovery_idx is not None:
        days_t2r = recovery_idx - trough_idx
        total_days = recovery_idx - peak_idx
        recovery_date = dates[recovery_idx]

    # trough 기준 forward return (영업일 수평선)
    fwd: dict[str, float | None] = {}
    for w, h in FORWARD_WINDOWS.items():
        if w == "1w":
            continue
        fwd[w] = _forward_return(closes, trough_idx, h)

    return {
        "peak_date": dates[peak_idx],
        "peak_price": round(peak_price, 4),
        "trough_date": dates[trough_idx],
        "trough_price": round(trough_price, 4),
        "recovery_date": recovery_date,
        "drawdown_depth": round(depth, 4),
        "days_peak_to_trough": days_p2t,
        "days_trough_to_recovery": days_t2r,
        "total_days_peak_to_recovery": total_days,
        "thresholds_hit": _thresholds_hit(depth),
        "forward_return_1m_from_trough": fwd.get("1m"),
        "forward_return_3m_from_trough": fwd.get("3m"),
        "forward_return_6m_from_trough": fwd.get("6m"),
        "forward_return_12m_from_trough": fwd.get("12m"),
    }


# ---------------------------------------------------------------------------
# 2. 연간 조정 빈도
# ---------------------------------------------------------------------------

def calculate_annual_correction_frequency(
    events: list[dict[str, Any]], dates: list[str]
) -> dict[str, Any]:
    """연도별 조정 *이벤트* 빈도 (cluster-aware — 임계 통과 횟수 아님).

    각 이벤트는 그 peak_date 가 속한 연도에 귀속한다 (조정이 시작된 해).
    연도별: -3/-5/-10/-15/-20% 도달 이벤트 수, 그 해 최대 낙폭, 연 수익률.
    전체: 깊이별 연평균·중앙값 조정 수, -10% 이벤트 간 평균 간격(연 단위).
    """
    out: dict[str, Any] = {"by_year": {}, "overall": {}, "sample_count": 0}
    if not events or not dates:
        return out

    depth_levels = [-0.03, -0.05, -0.10, -0.15, -0.20]
    level_key = {-0.03: "3pct", -0.05: "5pct", -0.10: "10pct",
                 -0.15: "15pct", -0.20: "20pct"}

    # 연도 범위 — 데이터가 있는 모든 연도를 채워 0건 연도도 표시
    years = sorted({y for y in (_year_of(d) for d in dates) if y is not None})
    by_year: dict[int, dict[str, Any]] = {}
    for y in years:
        by_year[y] = {f"correction_{level_key[lv]}_count": 0
                      for lv in depth_levels}
        by_year[y]["max_drawdown"] = 0.0
        by_year[y]["annual_return"] = None

    # 연 수익률 — 그 해 첫/마지막 종가 (dates 와 같은 길이의 closes 없으므로
    # dates 기준 첫/마지막 인덱스로 근사하려면 closes 필요 → 여기선 이벤트
    # 정보만 가지므로 annual_return 은 generate_market_cycle_summary 에서 보강)

    for ev in events:
        y = _year_of(ev.get("peak_date"))
        if y is None or y not in by_year:
            continue
        depth = ev.get("drawdown_depth") or 0.0
        for lv in depth_levels:
            if depth <= lv:
                by_year[y][f"correction_{level_key[lv]}_count"] += 1
        if depth < by_year[y]["max_drawdown"]:
            by_year[y]["max_drawdown"] = depth

    out["by_year"] = {str(y): v for y, v in by_year.items()}

    # 전체 집계
    overall: dict[str, Any] = {}
    for lv in depth_levels:
        counts = [v[f"correction_{level_key[lv]}_count"] for v in by_year.values()]
        overall[f"mean_{level_key[lv]}_per_year"] = (
            round(statistics.mean(counts), 2) if counts else None)
        overall[f"median_{level_key[lv]}_per_year"] = (
            statistics.median(counts) if counts else None)

    # -10% 이벤트 간 평균 간격 (연 단위) — peak_date 연도 기준
    ten_pct_years = sorted(
        _year_of(ev.get("peak_date")) for ev in events
        if (ev.get("drawdown_depth") or 0.0) <= -0.10
        and _year_of(ev.get("peak_date")) is not None
    )
    if len(ten_pct_years) >= 2:
        gaps = [ten_pct_years[i + 1] - ten_pct_years[i]
                for i in range(len(ten_pct_years) - 1)]
        overall["avg_interval_years_between_10pct"] = round(
            statistics.mean(gaps), 2)
    else:
        overall["avg_interval_years_between_10pct"] = None

    overall["n_years"] = len(years)
    out["overall"] = overall
    out["sample_count"] = len(events)
    return out


# ---------------------------------------------------------------------------
# 3. Drawdown base rate
# ---------------------------------------------------------------------------

def calculate_drawdown_base_rates(
    events: list[dict[str, Any]]
) -> dict[str, Any]:
    """이벤트 깊이 분포 + '조정이 임계를 더 깊게 통과할 실증 확률'.

    조건부 확률: 이미 -X% 에 도달한 이벤트들 중 -Y%(>X) 까지 더 깊어진 비율.
    표본이 작으므로 각 항목에 도달 이벤트 수를 함께 보고.
    """
    out: dict[str, Any] = {"depth_distribution": {}, "deepening_probability": {},
                           "sample_count": len(events)}
    if not events:
        return out

    depths = [ev.get("drawdown_depth") or 0.0 for ev in events]

    # 깊이 분포 — 버킷
    buckets = [
        ("-3~5%", -0.05, -0.03),
        ("-5~10%", -0.10, -0.05),
        ("-10~15%", -0.15, -0.10),
        ("-15~20%", -0.20, -0.15),
        ("-20~30%", -0.30, -0.20),
        ("-30%+", -10.0, -0.30),
    ]
    dist: dict[str, int] = {}
    for label, lo, hi in buckets:
        dist[label] = sum(1 for d in depths if lo < d <= hi)
    out["depth_distribution"] = dist

    # 조건부 deepening 확률
    levels = [-0.03, -0.05, -0.10, -0.15, -0.20, -0.25]
    prob: dict[str, Any] = {}
    for k, base in enumerate(levels):
        reached_base = [d for d in depths if d <= base]
        n_base = len(reached_base)
        for nxt in levels[k + 1:]:
            n_next = sum(1 for d in reached_base if d <= nxt)
            key = f"{int(base*100)}pct_to_{int(nxt*100)}pct"
            prob[key] = {
                "probability": round(n_next / n_base, 3) if n_base else None,
                "n_reached_base": n_base,
                "n_deepened": n_next,
            }
    out["deepening_probability"] = prob
    return out


# ---------------------------------------------------------------------------
# 4. 회복 기간 분포
# ---------------------------------------------------------------------------

def calculate_recovery_time_distribution(
    events: list[dict[str, Any]]
) -> dict[str, Any]:
    """깊이 버킷별 (peak→trough, trough→recovery) 영업일 평균·중앙값.

    버킷: -5/-10/-15/-20%+. 미회복 이벤트 수, 최악 회복 기간,
    빠른 V자 회복(<~40 영업일) vs 장기 회복 플래그.
    """
    out: dict[str, Any] = {"by_depth_bucket": {}, "sample_count": len(events)}
    if not events:
        return out

    buckets = [
        ("-5~10%", -0.10, -0.05),
        ("-10~15%", -0.15, -0.10),
        ("-15~20%", -0.20, -0.15),
        ("-20%+", -10.0, -0.20),
    ]
    for label, lo, hi in buckets:
        sel = [ev for ev in events
               if lo < (ev.get("drawdown_depth") or 0.0) <= hi]
        p2t = [ev["days_peak_to_trough"] for ev in sel
               if ev.get("days_peak_to_trough") is not None]
        t2r = [ev["days_trough_to_recovery"] for ev in sel
               if ev.get("days_trough_to_recovery") is not None]
        not_recovered = sum(1 for ev in sel
                            if ev.get("recovery_date") is None)
        fast_v = sum(1 for v in t2r if v < _FAST_RECOVERY_DAYS)
        slow = sum(1 for v in t2r if v >= _FAST_RECOVERY_DAYS)
        out["by_depth_bucket"][label] = {
            "n_events": len(sel),
            "avg_days_peak_to_trough": (round(statistics.mean(p2t), 1)
                                        if p2t else None),
            "median_days_peak_to_trough": (statistics.median(p2t)
                                           if p2t else None),
            "avg_days_trough_to_recovery": (round(statistics.mean(t2r), 1)
                                            if t2r else None),
            "median_days_trough_to_recovery": (statistics.median(t2r)
                                               if t2r else None),
            "worst_days_trough_to_recovery": (max(t2r) if t2r else None),
            "n_not_yet_recovered": not_recovered,
            "n_fast_v_shaped": fast_v,
            "n_long_recovery": slow,
        }
    return out


# ---------------------------------------------------------------------------
# 5. Bull run 식별
# ---------------------------------------------------------------------------

def identify_bull_runs(
    closes: list[float], dates: list[str]
) -> list[dict[str, Any]]:
    """상승장(uptrend run) 식별.

    정의 — 명확한 단일 룰:
    - bull run 은 직전 고점 회복일(= drawdown event 의 recovery_date) 에 시작
      하거나, 시계열 최초의 첫 거래일에 시작한다.
    - bull run 은 다음 -10% drawdown event 의 peak_date 에서 종료한다
      (그 peak 이 run 의 마지막 신고점). 즉 -10% 이상 깊어지는 다음 조정의
      직전 고점까지가 하나의 bull run.
    - 시계열 끝까지 -10% 조정이 안 나오면 마지막 날에 끝나며 end_reason='진행중'.

    각 run: start_date, end_date, duration_days, total_return,
            max_pullback(구간 내 -10% 미만의 최악 낙폭), n_minor_5pct_dips,
            end_reason.
    """
    runs: list[dict[str, Any]] = []
    n = len(closes)
    if n < 30 or len(dates) != n:
        return runs

    events = identify_drawdown_events(closes, dates)
    # -10% 이상 깊어진 이벤트만 bull run 의 경계로 사용
    deep_events = [ev for ev in events
                   if (ev.get("drawdown_depth") or 0.0) <= -0.10]

    date_to_idx = {d: k for k, d in enumerate(dates)}

    # 경계 인덱스 목록 구성
    segments: list[tuple[int, int, str]] = []  # (start_idx, end_idx, end_reason)
    cur_start = 0
    for ev in deep_events:
        peak_d = ev.get("peak_date")
        rec_d = ev.get("recovery_date")
        peak_i = date_to_idx.get(peak_d)
        if peak_i is None or peak_i <= cur_start:
            continue
        segments.append((cur_start, peak_i, "다음 -10% 조정 진입"))
        # 다음 run 은 회복일에 시작 (회복 못했으면 더 이상 run 없음)
        if rec_d is None:
            cur_start = -1
            break
        rec_i = date_to_idx.get(rec_d)
        cur_start = rec_i if rec_i is not None else -1
        if cur_start < 0:
            break
    if cur_start >= 0 and cur_start < n - 1:
        segments.append((cur_start, n - 1, "진행중"))

    for start_i, end_i, reason in segments:
        if end_i <= start_i:
            continue
        sub = closes[start_i:end_i + 1]
        if len(sub) < 2 or sub[0] <= 0:
            continue
        total_return = sub[-1] / sub[0] - 1.0
        # 구간 내 최악 낙폭 (running peak 대비)
        peak = sub[0]
        max_pullback = 0.0
        for px in sub:
            if px > peak:
                peak = px
            if peak > 0:
                dd = px / peak - 1.0
                if dd < max_pullback:
                    max_pullback = dd
        # -5% 소조정 횟수 (cluster-aware: -5%~-10% 사이 이벤트)
        n_minor = 0
        for ev in events:
            pi = date_to_idx.get(ev.get("peak_date"))
            if pi is None:
                continue
            if start_i <= pi <= end_i:
                d = ev.get("drawdown_depth") or 0.0
                if -0.10 < d <= -0.05:
                    n_minor += 1
        runs.append({
            "start_date": dates[start_i],
            "end_date": dates[end_i],
            "duration_days": end_i - start_i,
            "total_return": round(total_return, 4),
            "max_pullback": round(max_pullback, 4),
            "n_minor_5pct_dips": n_minor,
            "end_reason": reason,
        })
    return runs


def analyze_bull_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """bull run 평균 길이·수익률, 분포."""
    out: dict[str, Any] = {"sample_count": len(runs)}
    if not runs:
        out.update({"avg_duration_days": None, "avg_total_return": None,
                    "median_duration_days": None, "median_total_return": None,
                    "longest_run": None, "shortest_run": None})
        return out

    durations = [r["duration_days"] for r in runs
                 if r.get("duration_days") is not None]
    returns = [r["total_return"] for r in runs
               if r.get("total_return") is not None]
    out["avg_duration_days"] = round(statistics.mean(durations), 1) if durations else None
    out["median_duration_days"] = statistics.median(durations) if durations else None
    out["avg_total_return"] = round(statistics.mean(returns), 4) if returns else None
    out["median_total_return"] = (round(statistics.median(returns), 4)
                                  if returns else None)
    if durations:
        longest = max(runs, key=lambda r: r.get("duration_days") or 0)
        shortest = min(runs, key=lambda r: r.get("duration_days") or 0)
        out["longest_run"] = {"start": longest["start_date"],
                              "end": longest["end_date"],
                              "duration_days": longest["duration_days"],
                              "total_return": longest["total_return"]}
        out["shortest_run"] = {"start": shortest["start_date"],
                               "end": shortest["end_date"],
                               "duration_days": shortest["duration_days"],
                               "total_return": shortest["total_return"]}
    out["runs"] = runs
    return out


# ---------------------------------------------------------------------------
# 6. 현재 낙폭 버킷별 forward return
# ---------------------------------------------------------------------------

def calculate_forward_returns_by_drawdown_bucket(
    closes: list[float], dates: list[str]
) -> dict[str, Any]:
    """'현재 고점 대비 -X% 하락' 버킷별 forward return / MDD / 승률.

    버킷: -0~5, -5~10, -10~15, -15~20, -20~25, -25%+.
    backtest_engine 의 _forward_return / _forward_mdd / _agg 재사용.
    """
    out: dict[str, Any] = {"by_bucket": {}, "sample_count": 0}
    n = len(closes)
    if n < 60:
        return out

    buckets = [
        ("-0~5%", -0.05, 0.0001),
        ("-5~10%", -0.10, -0.05),
        ("-10~15%", -0.15, -0.10),
        ("-15~20%", -0.20, -0.15),
        ("-20~25%", -0.25, -0.20),
        ("-25%+", -10.0, -0.25),
    ]
    acc: dict[str, dict[str, list[float]]] = {b[0]: {} for b in buckets}
    mdd_acc: dict[str, dict[str, list[float]]] = {b[0]: {} for b in buckets}

    for i in range(n):
        dd = _drawdown_from_high(closes, i)
        label = None
        for lbl, lo, hi in buckets:
            if lo < dd <= hi:
                label = lbl
                break
        if label is None:
            continue
        for w, h in FORWARD_WINDOWS.items():
            fr = _forward_return(closes, i, h)
            if fr is not None:
                acc[label].setdefault(w, []).append(fr)
        for w in ("1m", "3m", "6m", "12m"):
            m = _forward_mdd(closes, i, FORWARD_WINDOWS[w])
            if m is not None:
                mdd_acc[label].setdefault(w, []).append(m)

    total = 0
    for lbl, _, _ in buckets:
        windows = acc[lbl]
        if not windows:
            continue
        summary: dict[str, Any] = {}
        for w in FORWARD_WINDOWS:
            summary[w] = _agg(windows.get(w, []))
        for w in ("1m", "3m", "6m", "12m"):
            mvals = mdd_acc[lbl].get(w, [])
            if mvals:
                summary[f"mdd_{w}"] = {
                    "avg": round(statistics.mean(mvals), 4),
                    "worst": round(min(mvals), 4),
                }
        sample = max((summary.get(w, {}).get("count", 0)
                      for w in FORWARD_WINDOWS), default=0)
        summary["sample_count"] = sample
        summary["confidence"] = _confidence_from_count(sample)
        total += sample
        out["by_bucket"][lbl] = summary
    out["sample_count"] = total
    return out


# ---------------------------------------------------------------------------
# 7. ATH 근접도별 forward return — "신고가 매수는 나쁘다" 직관 검증
# ---------------------------------------------------------------------------

def calculate_forward_returns_by_ath_proximity(
    closes: list[float], dates: list[str]
) -> dict[str, Any]:
    """ATH 근접도 버킷별 forward return / MDD / 승률.

    버킷:
    - "전고점(ATH)"            : running all-time-high 와 같음 (dd >= -0.001)
    - "52주 신고가"             : 52주 고점과 같으나 ATH 는 아님
    - "52주 고점 -0~3%"
    - "52주 고점 -3~5%"
    - "52주 고점 -5~10%"

    "신고가 근처 매수는 나쁘다" 는 직관을 직접 검증한다 — 데이터가 말하는
    그대로 보고한다 (편향 강화 금지).
    """
    out: dict[str, Any] = {"by_bucket": {}, "sample_count": 0}
    n = len(closes)
    if n < 260:
        return out

    labels = ["전고점(ATH)", "52주 신고가", "52주 고점 -0~3%",
              "52주 고점 -3~5%", "52주 고점 -5~10%"]
    acc: dict[str, dict[str, list[float]]] = {lbl: {} for lbl in labels}
    mdd_acc: dict[str, dict[str, list[float]]] = {lbl: {} for lbl in labels}

    for i in range(n):
        ath = max(closes[:i + 1])
        start52 = max(0, i - 251)
        high52 = max(closes[start52:i + 1])
        px = closes[i]
        if ath <= 0 or high52 <= 0:
            continue
        dd_ath = px / ath - 1.0
        dd_52 = px / high52 - 1.0

        if dd_ath >= -0.001:
            label = "전고점(ATH)"
        elif dd_52 >= -0.001:
            label = "52주 신고가"
        elif dd_52 >= -0.03:
            label = "52주 고점 -0~3%"
        elif dd_52 >= -0.05:
            label = "52주 고점 -3~5%"
        elif dd_52 >= -0.10:
            label = "52주 고점 -5~10%"
        else:
            continue

        for w, h in FORWARD_WINDOWS.items():
            fr = _forward_return(closes, i, h)
            if fr is not None:
                acc[label].setdefault(w, []).append(fr)
        for w in ("1m", "3m", "6m"):
            m = _forward_mdd(closes, i, FORWARD_WINDOWS[w])
            if m is not None:
                mdd_acc[label].setdefault(w, []).append(m)

    total = 0
    for label in labels:
        windows = acc[label]
        if not windows:
            continue
        summary: dict[str, Any] = {}
        for w in FORWARD_WINDOWS:
            summary[w] = _agg(windows.get(w, []))
        for w in ("1m", "3m", "6m"):
            mvals = mdd_acc[label].get(w, [])
            if mvals:
                summary[f"mdd_{w}"] = {
                    "avg": round(statistics.mean(mvals), 4),
                    "worst": round(min(mvals), 4),
                }
        sample = max((summary.get(w, {}).get("count", 0)
                      for w in FORWARD_WINDOWS), default=0)
        summary["sample_count"] = sample
        summary["confidence"] = _confidence_from_count(sample)
        total += sample
        out["by_bucket"][label] = summary
    out["sample_count"] = total
    return out


# ---------------------------------------------------------------------------
# 8. 추세 상태 분류
# ---------------------------------------------------------------------------

def classify_trend_state(closes: list[float], i: int) -> str:
    """index i 의 추세 상태를 5개 중 하나로 분류.

    사용 지표: 20/60/200일 MA, 200DMA 기울기, 200일선 이격, RSI(14),
               running ATH 대비 낙폭.

    분류 기준 (위에서부터 먼저 매칭):
    - "Trend Breakdown"      : 종가 < 200DMA  AND  200DMA 기울기 하락
                               (또는 ATH 대비 낙폭 <= -20%)
    - "Recovery from Bear"   : 종가 >= 200DMA 이지만 200DMA 기울기 아직 하락,
                               그리고 ATH 대비 낙폭 <= -10% (약세 후 회복 국면)
    - "Pullback in Uptrend"  : 200DMA 위·기울기 상승이지만 종가 < 20DMA
                               이고 ATH 대비 낙폭 <= -3% (상승추세 내 눌림)
    - "Uptrend but Extended" : 200DMA 위·상승추세이고
                               (RSI >= 70  또는  200일선 이격 >= +15%)
    - "Strong Uptrend"       : 위 어디에도 안 걸리는 정상 상승추세
                               (20>=60>=200 정렬·200DMA 상승·ATH 근처)
    데이터 부족 시 "데이터 부족" 반환.
    """
    n = len(closes)
    if i < 0 or i >= n or i < 200:
        return "데이터 부족"

    ma20 = _ma(closes, i, 20)
    ma60 = _ma(closes, i, 60)
    ma200 = _ma(closes, i, 200)
    if ma20 is None or ma60 is None or ma200 is None:
        return "데이터 부족"

    px = closes[i]
    rsi = _rsi(closes, i)
    dd = _drawdown_from_high(closes, i)

    # 200DMA 기울기 — 20영업일 전 200DMA 와 비교
    ma200_prev = _ma(closes, i - 20, 200) if i >= 220 else None
    slope_up = (ma200_prev is not None and ma200 > ma200_prev)
    slope_down = (ma200_prev is not None and ma200 < ma200_prev)

    gap200 = (px / ma200 - 1.0) if ma200 > 0 else 0.0

    # 1) Trend Breakdown
    if (px < ma200 and slope_down) or dd <= -0.20:
        return "Trend Breakdown"
    # 2) Recovery from Bear
    if px >= ma200 and slope_down and dd <= -0.10:
        return "Recovery from Bear"
    # 3) Pullback in Uptrend
    if px >= ma200 and (slope_up or ma200_prev is None) and px < ma20 and dd <= -0.03:
        return "Pullback in Uptrend"
    # 4) Uptrend but Extended
    if px >= ma200 and (slope_up or ma200_prev is None):
        if (rsi is not None and rsi >= 70) or gap200 >= 0.15:
            return "Uptrend but Extended"
        return "Strong Uptrend"
    # 5) 200DMA 아래이나 기울기 하락 아님 — 모호한 회복 초입
    if px < ma200:
        return "Recovery from Bear"
    return "Strong Uptrend"


def calculate_forward_returns_by_trend_state(
    closes: list[float], dates: list[str]
) -> dict[str, Any]:
    """추세 상태별 forward return + MDD + sample_count."""
    out: dict[str, Any] = {"by_state": {}, "sample_count": 0}
    n = len(closes)
    if n < 260:
        return out

    states = ["Strong Uptrend", "Uptrend but Extended", "Pullback in Uptrend",
              "Trend Breakdown", "Recovery from Bear"]
    acc: dict[str, dict[str, list[float]]] = {s: {} for s in states}
    mdd_acc: dict[str, dict[str, list[float]]] = {s: {} for s in states}

    for i in range(200, n):
        state = classify_trend_state(closes, i)
        if state not in acc:
            continue
        for w, h in FORWARD_WINDOWS.items():
            fr = _forward_return(closes, i, h)
            if fr is not None:
                acc[state].setdefault(w, []).append(fr)
        for w in ("1m", "3m", "6m"):
            m = _forward_mdd(closes, i, FORWARD_WINDOWS[w])
            if m is not None:
                mdd_acc[state].setdefault(w, []).append(m)

    total = 0
    for state in states:
        windows = acc[state]
        if not windows:
            continue
        summary: dict[str, Any] = {}
        for w in FORWARD_WINDOWS:
            summary[w] = _agg(windows.get(w, []))
        for w in ("1m", "3m", "6m"):
            mvals = mdd_acc[state].get(w, [])
            if mvals:
                summary[f"mdd_{w}"] = {
                    "avg": round(statistics.mean(mvals), 4),
                    "worst": round(min(mvals), 4),
                }
        sample = max((summary.get(w, {}).get("count", 0)
                      for w in FORWARD_WINDOWS), default=0)
        summary["sample_count"] = sample
        summary["confidence"] = _confidence_from_count(sample)
        total += sample
        out["by_state"][state] = summary
    out["sample_count"] = total
    return out


# ---------------------------------------------------------------------------
# 9. 현재 시장 위치 — Stage B 가 노출할 함수
# ---------------------------------------------------------------------------

def _ath_bucket_for(dd_ath: float, dd_52: float) -> str:
    if dd_ath >= -0.001:
        return "전고점(ATH)"
    if dd_52 >= -0.001:
        return "52주 신고가"
    if dd_52 >= -0.03:
        return "52주 고점 -0~3%"
    if dd_52 >= -0.05:
        return "52주 고점 -3~5%"
    if dd_52 >= -0.10:
        return "52주 고점 -5~10%"
    return "52주 고점 -10%+"


def _deploy_zone_hint(dd_ath: float) -> str:
    """현재 ATH 낙폭에 대응하는 배치 구간 힌트 (crash deployment 정합)."""
    if dd_ath <= -0.25:
        return "공격 배치 구간 (-25% 이하)"
    if dd_ath <= -0.20:
        return "적극 배치 구간 (-20%)"
    if dd_ath <= -0.15:
        return "분할 매수 구간 (-15%)"
    if dd_ath <= -0.10:
        return "관심 구간 (-10%)"
    if dd_ath <= -0.05:
        return "초기 관심 구간 (-5%)"
    return "본격 매수 구간 아님 (고점 근처)"


def locate_current_market(conn, asset: str = "QQQ") -> dict[str, Any]:
    """오늘의 시장 위치를 과거 base rate 와 대조 — Stage B 가 노출할 함수.

    오늘의 asset 에 대해: 현재 고점 대비 낙폭, 추세 상태, ATH 근접 버킷을
    구하고, 그에 일치하는 과거 base rate(forward return·표본 수)를 찾아
    정직한 한국어 한두 줄 verdict 로 반환한다.
    데이터 부족 시 verdict_ko 가 그 사실을 차분히 말한다.
    """
    out: dict[str, Any] = {
        "asset": asset,
        "drawdown_pct": None, "trend_state": None, "ath_bucket": None,
        "similar_forward_1m": None, "similar_forward_3m": None,
        "similar_forward_6m": None, "similar_sample_count": 0,
        "deploy_zone_hint": None, "verdict_ko": "데이터 부족 — 시장 위치를 판단할 수 없습니다.",
    }
    closes, dates = _load_series(conn, asset)
    if len(closes) < 260:
        out["verdict_ko"] = (
            f"{asset} 장기 일봉이 부족해(현재 {len(closes)}행) 시장 위치를 "
            f"과거와 대조할 수 없습니다 — 데이터 누적이 필요합니다.")
        return out

    i = len(closes) - 1
    ath = max(closes)
    start52 = max(0, i - 251)
    high52 = max(closes[start52:i + 1])
    dd_ath = closes[i] / ath - 1.0 if ath > 0 else 0.0
    dd_52 = closes[i] / high52 - 1.0 if high52 > 0 else 0.0

    trend = classify_trend_state(closes, i)
    bucket = _ath_bucket_for(dd_ath, dd_52)

    out["drawdown_pct"] = round(dd_ath, 4)
    out["trend_state"] = trend
    out["ath_bucket"] = bucket
    out["deploy_zone_hint"] = _deploy_zone_hint(dd_ath)

    # 과거 ATH 근접 버킷의 base rate 조회
    ath_stats = calculate_forward_returns_by_ath_proximity(closes, dates)
    bucket_stats = (ath_stats.get("by_bucket") or {}).get(bucket)
    if bucket_stats:
        out["similar_forward_1m"] = (bucket_stats.get("1m") or {}).get("avg")
        out["similar_forward_3m"] = (bucket_stats.get("3m") or {}).get("avg")
        out["similar_forward_6m"] = (bucket_stats.get("6m") or {}).get("avg")
        out["similar_sample_count"] = bucket_stats.get("sample_count", 0)

    # 정직한 한국어 verdict
    dd_txt = f"전고점 {dd_ath*100:+.1f}%"
    n = out["similar_sample_count"]
    f3 = out["similar_forward_3m"]
    parts = [dd_txt]
    if trend != "데이터 부족":
        parts.append(f"추세 상태 '{trend}'")
    if n >= 10 and f3 is not None:
        conf = "표본 충분" if n >= 60 else ("표본 보통" if n >= 20 else "표본 부족 — 신뢰도 낮음")
        parts.append(
            f"과거 유사 '{bucket}' 구간 {n}개에서 3개월 평균 {f3*100:+.1f}% ({conf})")
    elif n > 0:
        parts.append(f"과거 유사 구간 표본 {n}개 — 표본 부족으로 신뢰도 낮음")
    else:
        parts.append("과거 유사 구간 표본 없음")
    parts.append(out["deploy_zone_hint"])
    out["verdict_ko"] = " — ".join(parts) + "."
    return out


# ---------------------------------------------------------------------------
# 10. 종합 + DB 저장
# ---------------------------------------------------------------------------

_CAVEATS_KO: list[str] = [
    "과거 ≠ 미래 — 모든 통계는 과거 실증 결과이며 미래 수익을 보장하지 않습니다.",
    "표본 부족 — 1999년 이후 독립적인 대형 시장 사이클은 4~6개뿐입니다. "
    "깊은 낙폭(-20% 이하) 통계는 표본이 매우 작아 신뢰도가 낮습니다.",
    "look-ahead / survivorship 주의 — running all-time-high·52주 고점은 "
    "사후적으로 계산됩니다. 지수 ETF 라 개별 종목 생존편향은 작지만, "
    "과거 구간 선택 자체에 편향이 있을 수 있습니다.",
    "레버리지 ETF(QLD/TQQQ) 주의 — 일간 리밸런싱 구조로 변동성 끌림(decay)과 "
    "경로 의존성이 있어 장기 보유 시 지수 배수와 크게 달라집니다. "
    "Stage A 는 상장 전 합성 데이터를 만들지 않습니다.",
    "이 분석은 서술적 실증 통계입니다 — 자동 룰 발굴·feature 마이닝은 "
    "과적합 함정으로 의도적으로 제외했습니다.",
]


def _annual_returns(closes: list[float], dates: list[str]) -> dict[int, float]:
    """연도별 수익률 (그 해 첫·마지막 종가)."""
    by_year: dict[int, list[tuple[int, float]]] = {}
    for k, d in enumerate(dates):
        y = _year_of(d)
        if y is None:
            continue
        by_year.setdefault(y, []).append((k, closes[k]))
    out: dict[int, float] = {}
    for y, pairs in by_year.items():
        pairs.sort()
        first = pairs[0][1]
        last = pairs[-1][1]
        if first > 0:
            out[y] = last / first - 1.0
    return out


def _analyze_asset(conn, asset: str) -> dict[str, Any]:
    """단일 자산의 전체 사이클 분석 묶음."""
    closes, dates = _load_series(conn, asset)
    result: dict[str, Any] = {"asset": asset, "n_rows": len(closes)}
    if len(closes) < 60:
        result["status"] = "데이터 부족"
        return result

    events = identify_drawdown_events(closes, dates)
    annual = calculate_annual_correction_frequency(events, dates)

    # annual_return 보강
    ann_ret = _annual_returns(closes, dates)
    for y_str, v in (annual.get("by_year") or {}).items():
        try:
            y = int(y_str)
            if y in ann_ret:
                v["annual_return"] = round(ann_ret[y], 4)
        except Exception:
            pass

    runs = identify_bull_runs(closes, dates)
    result.update({
        "status": "ok",
        "data_start": dates[0],
        "data_end": dates[-1],
        "drawdown_events": events,
        "annual_correction_frequency": annual,
        "drawdown_base_rates": calculate_drawdown_base_rates(events),
        "recovery_time_distribution": calculate_recovery_time_distribution(events),
        "bull_runs": analyze_bull_runs(runs),
        "forward_returns_by_drawdown_bucket":
            calculate_forward_returns_by_drawdown_bucket(closes, dates),
        "forward_returns_by_ath_proximity":
            calculate_forward_returns_by_ath_proximity(closes, dates),
        "forward_returns_by_trend_state":
            calculate_forward_returns_by_trend_state(closes, dates),
    })
    return result


def generate_market_cycle_summary(conn) -> dict[str, Any]:
    """QQQ(주)·SPY 의 시장 사이클 분석을 종합해 하나의 dict 로 조립 + DB 저장.

    무겁다 — 매 파이프라인 run 마다 돌릴 필요 없이 가끔(월초·테이블 빈 경우)
    실행하도록 설계됨. 어떤 실패도 잡아 graceful 하게 진행한다.
    """
    summary: dict[str, Any] = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "assets": {},
        "caveats_ko": list(_CAVEATS_KO),
    }

    for asset in ("QQQ", "SPY"):
        try:
            summary["assets"][asset] = _analyze_asset(conn, asset)
        except Exception as e:
            log.warning("[%s] 사이클 분석 실패 (graceful): %s", asset, e)
            summary["assets"][asset] = {"asset": asset, "status": "error",
                                        "error": str(e)}

    # 현재 시장 위치 (QQQ)
    try:
        summary["current_market"] = locate_current_market(conn, "QQQ")
    except Exception as e:
        log.warning("현재 시장 위치 분석 실패: %s", e)
        summary["current_market"] = {"verdict_ko": "현재 시장 위치 분석 실패."}

    # 한국어 commentary — 정직한 톤
    comments: list[str] = []
    qqq = summary["assets"].get("QQQ") or {}
    if qqq.get("status") == "ok":
        events = qqq.get("drawdown_events") or []
        n_ev = len(events)
        deep = [e for e in events if (e.get("drawdown_depth") or 0) <= -0.20]
        worst = min(events, key=lambda e: e.get("drawdown_depth") or 0,
                    default=None)
        comments.append(
            f"QQQ {qqq.get('data_start')}~{qqq.get('data_end')} 동안 "
            f"-3% 이상 조정 이벤트 {n_ev}건 (그 중 -20% 이상 {len(deep)}건).")
        if worst:
            comments.append(
                f"최대 낙폭 이벤트는 {worst.get('peak_date')} 고점에서 "
                f"{(worst.get('drawdown_depth') or 0)*100:.1f}%, "
                f"peak→trough {worst.get('days_peak_to_trough')}영업일.")
        afreq = (qqq.get("annual_correction_frequency") or {}).get("overall") or {}
        m10 = afreq.get("mean_10pct_per_year")
        if m10 is not None:
            comments.append(f"연평균 -10% 조정 {m10}건.")
        ath = (qqq.get("forward_returns_by_ath_proximity") or {}).get("by_bucket") or {}
        ath_at = (ath.get("전고점(ATH)") or {}).get("3m") or {}
        if ath_at.get("avg") is not None:
            comments.append(
                f"전고점(ATH)에서 매수 시 과거 3개월 평균 "
                f"{ath_at['avg']*100:+.1f}% (표본 {ath_at.get('count')}) — "
                "신고가 매수가 반드시 나쁜 진입은 아니었습니다.")
    cur = summary.get("current_market") or {}
    if cur.get("verdict_ko"):
        comments.append("현재: " + cur["verdict_ko"])
    if not comments:
        comments.append("시장 사이클 분석 데이터가 부족합니다 — "
                        "장기 일봉 확보가 필요합니다.")
    summary["commentary_ko"] = " ".join(comments)

    # DB 저장
    try:
        saved = _persist_market_cycle(conn, summary)
        summary["saved"] = saved
    except Exception as e:
        log.warning("시장 사이클 DB 저장 실패 (graceful): %s", e)
        summary["saved"] = {"error": str(e)}
    return summary


def _persist_market_cycle(conn, summary: dict[str, Any]) -> dict[str, int]:
    """분석 결과를 market_cycles / annual_correction_stats /
    bull_run_stats / ath_forward_returns 테이블에 저장."""
    from . import database as _db
    saved = {"market_cycles": 0, "annual_correction_stats": 0,
             "bull_run_stats": 0, "ath_forward_returns": 0}

    for asset, res in (summary.get("assets") or {}).items():
        if not isinstance(res, dict) or res.get("status") != "ok":
            continue

        # market_cycles
        cycle_rows: list[dict[str, Any]] = []
        for ev in res.get("drawdown_events") or []:
            depth = ev.get("drawdown_depth") or 0.0
            cycle_type = ("대형 약세장" if depth <= -0.20
                          else "중간 조정" if depth <= -0.10
                          else "소조정")
            cycle_rows.append({
                "asset": asset,
                "peak_date": ev.get("peak_date"),
                "trough_date": ev.get("trough_date"),
                "recovery_date": ev.get("recovery_date"),
                "drawdown_depth": depth,
                "days_peak_to_trough": ev.get("days_peak_to_trough"),
                "days_trough_to_recovery": ev.get("days_trough_to_recovery"),
                "total_recovery_days": ev.get("total_days_peak_to_recovery"),
                "cycle_type": cycle_type,
                "forward_return_1m_from_trough":
                    ev.get("forward_return_1m_from_trough"),
                "forward_return_3m_from_trough":
                    ev.get("forward_return_3m_from_trough"),
                "forward_return_6m_from_trough":
                    ev.get("forward_return_6m_from_trough"),
                "forward_return_12m_from_trough":
                    ev.get("forward_return_12m_from_trough"),
            })
        if cycle_rows:
            saved["market_cycles"] += _db.upsert_market_cycles(conn, cycle_rows)

        # annual_correction_stats
        annual = res.get("annual_correction_frequency") or {}
        ann_rows: list[dict[str, Any]] = []
        for y_str, v in (annual.get("by_year") or {}).items():
            try:
                year = int(y_str)
            except Exception:
                continue
            ann_rows.append({
                "year": year, "asset": asset,
                "correction_3pct_count": v.get("correction_3pct_count", 0),
                "correction_5pct_count": v.get("correction_5pct_count", 0),
                "correction_10pct_count": v.get("correction_10pct_count", 0),
                "correction_15pct_count": v.get("correction_15pct_count", 0),
                "correction_20pct_count": v.get("correction_20pct_count", 0),
                "max_drawdown": v.get("max_drawdown"),
                "annual_return": v.get("annual_return"),
            })
        if ann_rows:
            saved["annual_correction_stats"] += \
                _db.upsert_annual_correction_stats(conn, ann_rows)

        # bull_run_stats
        runs = (res.get("bull_runs") or {}).get("runs") or []
        run_rows: list[dict[str, Any]] = []
        for k, r in enumerate(runs):
            run_rows.append({
                "run_id": f"{asset}-{r.get('start_date')}",
                "asset": asset,
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
                "duration_days": r.get("duration_days"),
                "total_return": r.get("total_return"),
                "max_pullback": r.get("max_pullback"),
                "trend_state": "Bull Run",
                "end_reason": r.get("end_reason"),
            })
        if run_rows:
            saved["bull_run_stats"] += _db.upsert_bull_run_stats(conn, run_rows)

        # ath_forward_returns
        ath = (res.get("forward_returns_by_ath_proximity") or {}).get("by_bucket") or {}
        ath_rows: list[dict[str, Any]] = []
        for bucket, s in ath.items():
            def _avg(w):
                return (s.get(w) or {}).get("avg")
            ath_rows.append({
                "asset": asset,
                "ath_proximity_bucket": bucket,
                "forward_1w": _avg("1w"),
                "forward_1m": _avg("1m"),
                "forward_3m": _avg("3m"),
                "forward_6m": _avg("6m"),
                "forward_12m": _avg("12m"),
                "mdd_1m": (s.get("mdd_1m") or {}).get("avg"),
                "mdd_3m": (s.get("mdd_3m") or {}).get("avg"),
                "mdd_6m": (s.get("mdd_6m") or {}).get("avg"),
                "win_rate": (s.get("3m") or {}).get("win_rate"),
                "sample_count": s.get("sample_count", 0),
            })
        if ath_rows:
            saved["ath_forward_returns"] += \
                _db.upsert_ath_forward_returns(conn, ath_rows)

    return saved
