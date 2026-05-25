"""백테스트 엔진 — Phase 4-A 백테스트 기반.

핵심 원칙:
- Rule-based / data-based. 수익률·MDD·regime 분류·deployment rule 에 LLM 안 씀.
- "예측"이 아니라 "과거 검증" 톤. 레버리지(QLD/TQQQ) MDD·decay 경고 포함.
- 외부 데이터 실패해도 예외 비전파 — graceful.
- 과거 Overheat Score 는 확실히 계산 가능한 항목(technical extension·drawdown)
  중심으로 재구성하고 confidence(High/Med/Low)를 함께 산출.

데이터 출처: market_price_history 테이블 (data_cache 가 채움).
"""
from __future__ import annotations

import datetime as _dt
import math
import statistics
from typing import Any

from . import market_regime as _mr
from .utils import get_logger

log = get_logger("backtest_engine")

# 백테스트 forward 기간 (영업일)
FORWARD_WINDOWS: dict[str, int] = {
    "1w": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252,
}

# Overheat Score 구간
OVERHEAT_BANDS: list[tuple[float, float, str]] = [
    (0.0, 30.0, "0-30 정상"),
    (30.0, 50.0, "30-50 주의"),
    (50.0, 70.0, "50-70 과열경계"),
    (70.0, 85.0, "70-85 과열"),
    (85.0, 100.01, "85-100 FOMO"),
]

RISK_FREE_ANNUAL = 0.02  # Sharpe/Sortino 용 (보수적 고정)


# ---------------------------------------------------------------------------
# 데이터 확보
# ---------------------------------------------------------------------------

def fetch_historical_market_data(
    conn, tickers: list[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """market_price_history 에서 과거 일봉을 dict 로 확보.

    Returns: {ticker: [{"date","close","adj_close","high","low","volume"}, ...]}
    날짜 오름차순. 데이터 없으면 빈 dict.
    """
    from . import database as _db
    from . import data_cache as _dc

    tickers = tickers or _dc.BACKTEST_TICKERS
    out: dict[str, list[dict[str, Any]]] = {}
    for t in tickers:
        try:
            rows = _db.fetch_market_price_history(conn, t)
            series: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r) if hasattr(r, "keys") else r
                close = d.get("adj_close") or d.get("close")
                if close is None:
                    continue
                series.append({
                    "date": d.get("date"),
                    "close": float(d.get("close") or close),
                    "adj_close": float(close),
                    "high": d.get("high"),
                    "low": d.get("low"),
                    "volume": d.get("volume"),
                })
            if series:
                out[t] = series
        except Exception as e:
            log.warning("[%s] historical data fetch 실패: %s", t, e)
    return out


# ---------------------------------------------------------------------------
# 수치 헬퍼 — 수익률 / MDD / 위험조정 지표
# ---------------------------------------------------------------------------

def _closes(series: list[dict[str, Any]], key: str = "adj_close") -> list[float]:
    return [float(r[key]) for r in series if r.get(key) is not None]


def _forward_return(closes: list[float], i: int, horizon: int) -> float | None:
    """i 시점 기준 horizon 영업일 후 수익률."""
    j = i + horizon
    if j >= len(closes) or i < 0:
        return None
    base = closes[i]
    if base is None or base <= 0:
        return None
    return closes[j] / base - 1.0


def _forward_mdd(closes: list[float], i: int, horizon: int) -> float | None:
    """i 시점부터 horizon 영업일 구간의 최대낙폭 (음수)."""
    if i < 0 or i >= len(closes):
        return None
    end = min(len(closes), i + horizon + 1)
    window = closes[i:end]
    if len(window) < 2:
        return None
    peak = window[0]
    mdd = 0.0
    for px in window:
        if px > peak:
            peak = px
        if peak > 0:
            dd = px / peak - 1.0
            if dd < mdd:
                mdd = dd
    return mdd


def _series_mdd(closes: list[float]) -> float:
    """전체 시계열 최대낙폭 (음수)."""
    if len(closes) < 2:
        return 0.0
    peak = closes[0]
    mdd = 0.0
    for px in closes:
        if px > peak:
            peak = px
        if peak > 0:
            dd = px / peak - 1.0
            if dd < mdd:
                mdd = dd
    return mdd


def _cagr(start_val: float, end_val: float, n_days: int) -> float | None:
    if start_val <= 0 or end_val <= 0 or n_days <= 0:
        return None
    years = n_days / 252.0
    if years <= 0:
        return None
    try:
        return (end_val / start_val) ** (1.0 / years) - 1.0
    except Exception:
        return None


def _daily_returns(equity: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            out.append(equity[i] / equity[i - 1] - 1.0)
    return out


def _sharpe(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 20:
        return None
    try:
        mean = statistics.mean(daily_rets)
        sd = statistics.pstdev(daily_rets)
        if sd <= 0:
            return None
        rf_daily = RISK_FREE_ANNUAL / 252.0
        return (mean - rf_daily) / sd * math.sqrt(252.0)
    except Exception:
        return None


def _sortino(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 20:
        return None
    try:
        mean = statistics.mean(daily_rets)
        rf_daily = RISK_FREE_ANNUAL / 252.0
        downside = [r for r in daily_rets if r < 0]
        if not downside:
            return None
        dd_dev = math.sqrt(sum(r * r for r in downside) / len(daily_rets))
        if dd_dev <= 0:
            return None
        return (mean - rf_daily) / dd_dev * math.sqrt(252.0)
    except Exception:
        return None


def _recovery_time(equity: list[float]) -> int | None:
    """최대낙폭 저점에서 직전 고점 회복까지 걸린 영업일 수. 미회복 시 None."""
    if len(equity) < 2:
        return None
    peak = equity[0]
    peak_idx = 0
    mdd = 0.0
    trough_idx = 0
    for i, px in enumerate(equity):
        if px > peak:
            peak = px
            peak_idx = i
        if peak > 0:
            dd = px / peak - 1.0
            if dd < mdd:
                mdd = dd
                trough_idx = i
                mdd_peak = peak
                mdd_peak_idx = peak_idx
    if mdd == 0.0:
        return 0
    # 저점 이후 mdd_peak 회복까지
    for i in range(trough_idx, len(equity)):
        if equity[i] >= mdd_peak:
            return i - mdd_peak_idx
    return None  # 미회복


def _summarize_equity(equity: list[float], n_days: int) -> dict[str, Any]:
    """equity curve → CAGR/Total/MDD/Sharpe/Sortino/Calmar/Recovery."""
    if len(equity) < 2:
        return {}
    total = equity[-1] / equity[0] - 1.0 if equity[0] > 0 else None
    cagr = _cagr(equity[0], equity[-1], n_days)
    mdd = _series_mdd(equity)
    rets = _daily_returns(equity)
    sharpe = _sharpe(rets)
    sortino = _sortino(rets)
    calmar = (cagr / abs(mdd)) if (cagr is not None and mdd < 0) else None
    rec = _recovery_time(equity)
    win_rate = (sum(1 for r in rets if r > 0) / len(rets)) if rets else None
    return {
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "recovery_time": rec,
        "win_rate": win_rate,
    }


def _agg(values: list[float]) -> dict[str, Any]:
    """수익률 리스트 → avg/median/win_rate/count."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"avg": None, "median": None, "win_rate": None, "count": 0}
    return {
        "avg": statistics.mean(vals),
        "median": statistics.median(vals),
        "win_rate": sum(1 for v in vals if v > 0) / len(vals),
        "count": len(vals),
    }


def _confidence_from_count(n: int) -> str:
    if n >= 60:
        return "High"
    if n >= 20:
        return "Med"
    return "Low"


# ---------------------------------------------------------------------------
# 과거 Overheat Score 재구성
# ---------------------------------------------------------------------------

def _rsi(closes: list[float], i: int, period: int = 14) -> float | None:
    if i < period:
        return None
    gains = 0.0
    losses = 0.0
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


def _ma_gap(closes: list[float], i: int, window: int = 200) -> float | None:
    if i < window - 1:
        return None
    ma = sum(closes[i - window + 1:i + 1]) / window
    if ma <= 0:
        return None
    return closes[i] / ma - 1.0


def _pos_52w(closes: list[float], i: int) -> float | None:
    """52주 고점 대비 위치 (drawdown, 음수)."""
    if i < 1:
        return None
    start = max(0, i - 251)
    window = closes[start:i + 1]
    high = max(window)
    if high <= 0:
        return None
    return closes[i] / high - 1.0


def calculate_historical_overheat_scores(conn) -> list[dict[str, Any]]:
    """과거 각 날짜의 Overheat Score 를 재구성.

    과거 valuation·sentiment 데이터는 부분적이므로, 과거에 확실히 계산 가능한
    technical extension(200일선 이격·RSI·52주 위치)을 중심으로 점수를 만들고
    confidence(High/Med/Low)를 함께 산출한다.

    confidence 규칙:
    - High : SPY·QQQ 둘 다 200일선 이격·RSI·52주위치 전부 계산 가능 (>=200일 데이터)
    - Med  : 일부만 계산 가능 (한쪽 ETF 만, 또는 200일 미만으로 RSI/52주만)
    - Low  : technical 항목 1개 이하만 산출

    market_regime 의 기존 sub-score 매핑(_lerp_score)을 재사용한다.
    Returns: [{"date","overheat_score","confidence","tech_score",
               "qqq_drawdown","components": {...}}, ...]
    """
    data = fetch_historical_market_data(conn, ["SPY", "QQQ"])
    spy = data.get("SPY") or []
    qqq = data.get("QQQ") or []
    if not qqq:
        log.info("과거 Overheat Score 재구성 — QQQ 데이터 없음")
        return []

    spy_closes = _closes(spy)
    qqq_closes = _closes(qqq)
    # SPY 를 QQQ 날짜에 정렬하기 위한 인덱스 맵
    spy_idx_by_date = {r["date"]: k for k, r in enumerate(spy)}

    out: list[dict[str, Any]] = []
    for i, row in enumerate(qqq):
        date_iso = row["date"]
        tech_parts: list[float] = []
        n_components = 0

        # QQQ technical
        for closes_ref, idx in ((qqq_closes, i),):
            gap = _ma_gap(closes_ref, idx, 200)
            if gap is not None:
                tech_parts.append(_mr._lerp_score(gap, -0.10, 0.20))
                n_components += 1
            rsi = _rsi(closes_ref, idx)
            if rsi is not None:
                tech_parts.append(_mr._lerp_score(rsi, 35.0, 80.0))
                n_components += 1
            pos = _pos_52w(closes_ref, idx)
            if pos is not None:
                tech_parts.append(_mr._lerp_score(pos, -0.20, 0.0))
                n_components += 1

        # SPY technical (날짜 정렬)
        si = spy_idx_by_date.get(date_iso)
        spy_ok = False
        if si is not None and si < len(spy_closes):
            gap = _ma_gap(spy_closes, si, 200)
            if gap is not None:
                tech_parts.append(_mr._lerp_score(gap, -0.10, 0.20))
                n_components += 1
                spy_ok = True
            rsi = _rsi(spy_closes, si)
            if rsi is not None:
                tech_parts.append(_mr._lerp_score(rsi, 35.0, 80.0))
                n_components += 1

        if not tech_parts:
            continue

        tech_score = sum(tech_parts) / len(tech_parts)
        qqq_dd = _pos_52w(qqq_closes, i)

        # confidence — technical 항목 수 + SPY 동반 여부
        qqq_full = (_ma_gap(qqq_closes, i, 200) is not None
                    and _rsi(qqq_closes, i) is not None)
        if qqq_full and spy_ok and n_components >= 5:
            confidence = "High"
        elif n_components >= 3:
            confidence = "Med"
        else:
            confidence = "Low"

        # overheat 근사 — technical extension 단독 점수.
        # (과거 valuation/sentiment 미가용 → technical 을 overheat proxy 로 사용)
        overheat = round(max(0.0, min(100.0, tech_score)), 1)

        out.append({
            "date": date_iso,
            "overheat_score": overheat,
            "tech_score": round(tech_score, 1),
            "confidence": confidence,
            "qqq_drawdown": qqq_dd,
            "n_components": n_components,
        })
    log.info("과거 Overheat Score 재구성 완료: %d일", len(out))
    return out


# ---------------------------------------------------------------------------
# 과거 Regime 분류
# ---------------------------------------------------------------------------

def classify_historical_regimes(conn) -> list[dict[str, Any]]:
    """과거 각 날짜를 market_regime.classify_market_regime 로직으로 분류.

    과거 신용 데이터는 부분적이므로 credit_stress 는 'unknown' 으로 처리하고
    overheat·drawdown 으로 분류 (rule 은 동일하게 재사용).
    """
    overheat_rows = calculate_historical_overheat_scores(conn)
    out: list[dict[str, Any]] = []
    for r in overheat_rows:
        dd = r.get("qqq_drawdown")
        regime = _mr.classify_market_regime(
            r.get("overheat_score"), dd,
            credit_stress="unknown",  # 과거 신용 데이터 미가용
            breadth_pct=None,
        )
        out.append({
            "date": r["date"],
            "regime": regime,
            "overheat_score": r.get("overheat_score"),
            "confidence": r.get("confidence"),
            "qqq_drawdown": dd,
        })
    return out


# ---------------------------------------------------------------------------
# Regime / Overheat forward return 백테스트
# ---------------------------------------------------------------------------

_FWD_ASSETS = ["SPY", "QQQ", "QLD", "TQQQ"]


def backtest_regime_forward_returns(conn) -> dict[str, Any]:
    """각 regime 발생일 기준 SPY/QQQ/QLD/TQQQ forward 수익률·MDD 집계.

    Returns: {"by_regime": {regime: {asset: {window: agg, mdd_*}}},
              "rows": [regime_forward_returns row dict ...],
              "confidence": ...}
    """
    regimes = classify_historical_regimes(conn)
    if not regimes:
        return {"by_regime": {}, "rows": [], "confidence": "Data Unavailable"}

    data = fetch_historical_market_data(conn, _FWD_ASSETS)
    closes_map: dict[str, list[float]] = {}
    idx_map: dict[str, dict[str, int]] = {}
    for a in _FWD_ASSETS:
        series = data.get(a) or []
        closes_map[a] = _closes(series)
        idx_map[a] = {r["date"]: k for k, r in enumerate(series)}

    # 누적: regime → asset → window → list[float]
    acc: dict[str, dict[str, dict[str, list[float]]]] = {}
    mdd_acc: dict[str, dict[str, dict[str, list[float]]]] = {}
    rows: list[dict[str, Any]] = []

    for rec in regimes:
        regime = rec["regime"]
        date_iso = rec["date"]
        row_assets: dict[str, dict[str, Any]] = {}
        for a in _FWD_ASSETS:
            ai = idx_map[a].get(date_iso)
            if ai is None:
                continue
            ac = closes_map[a]
            fwd: dict[str, float | None] = {}
            for w, h in FORWARD_WINDOWS.items():
                fr = _forward_return(ac, ai, h)
                fwd[w] = fr
                if fr is not None:
                    acc.setdefault(regime, {}).setdefault(a, {}).setdefault(w, []).append(fr)
            mdd: dict[str, float | None] = {}
            for w in ("1m", "3m", "6m"):
                m = _forward_mdd(ac, ai, FORWARD_WINDOWS[w])
                mdd[w] = m
                if m is not None:
                    mdd_acc.setdefault(regime, {}).setdefault(a, {}).setdefault(w, []).append(m)
            row_assets[a] = {"fwd": fwd, "mdd": mdd}
            rows.append({
                "date": date_iso, "regime": regime,
                "overheat_score": rec.get("overheat_score"), "asset": a,
                "forward_1w": fwd.get("1w"), "forward_1m": fwd.get("1m"),
                "forward_3m": fwd.get("3m"), "forward_6m": fwd.get("6m"),
                "forward_12m": fwd.get("12m"),
                "mdd_1m": mdd.get("1m"), "mdd_3m": mdd.get("3m"),
                "mdd_6m": mdd.get("6m"),
            })

    # 집계
    by_regime: dict[str, Any] = {}
    for regime, assets in acc.items():
        by_regime[regime] = {}
        for a, windows in assets.items():
            asset_summary: dict[str, Any] = {}
            for w, vals in windows.items():
                asset_summary[w] = _agg(vals)
            # MDD 집계
            for w in ("1m", "3m", "6m"):
                mvals = mdd_acc.get(regime, {}).get(a, {}).get(w, [])
                if mvals:
                    asset_summary[f"mdd_{w}"] = {
                        "avg": statistics.mean(mvals),
                        "worst": min(mvals),
                        "count": len(mvals),
                    }
            sample = max((asset_summary.get(w, {}).get("count", 0)
                          for w in FORWARD_WINDOWS), default=0)
            asset_summary["confidence"] = _confidence_from_count(sample)
            by_regime[regime][a] = asset_summary

    return {"by_regime": by_regime, "rows": rows, "confidence": "computed"}


def backtest_overheat_forward_returns(conn) -> dict[str, Any]:
    """Overheat Score 구간별 forward 수익률·MDD 집계."""
    overheat_rows = calculate_historical_overheat_scores(conn)
    if not overheat_rows:
        return {"by_band": {}, "confidence": "Data Unavailable"}

    data = fetch_historical_market_data(conn, _FWD_ASSETS)
    closes_map: dict[str, list[float]] = {}
    idx_map: dict[str, dict[str, int]] = {}
    for a in _FWD_ASSETS:
        series = data.get(a) or []
        closes_map[a] = _closes(series)
        idx_map[a] = {r["date"]: k for k, r in enumerate(series)}

    acc: dict[str, dict[str, dict[str, list[float]]]] = {}
    mdd_acc: dict[str, dict[str, dict[str, list[float]]]] = {}

    for rec in overheat_rows:
        score = rec.get("overheat_score")
        if score is None:
            continue
        band = None
        for lo, hi, name in OVERHEAT_BANDS:
            if lo <= score < hi:
                band = name
                break
        if band is None:
            continue
        date_iso = rec["date"]
        for a in _FWD_ASSETS:
            ai = idx_map[a].get(date_iso)
            if ai is None:
                continue
            ac = closes_map[a]
            for w, h in FORWARD_WINDOWS.items():
                fr = _forward_return(ac, ai, h)
                if fr is not None:
                    acc.setdefault(band, {}).setdefault(a, {}).setdefault(w, []).append(fr)
            for w in ("1m", "3m", "6m"):
                m = _forward_mdd(ac, ai, FORWARD_WINDOWS[w])
                if m is not None:
                    mdd_acc.setdefault(band, {}).setdefault(a, {}).setdefault(w, []).append(m)

    by_band: dict[str, Any] = {}
    for band, assets in acc.items():
        by_band[band] = {}
        for a, windows in assets.items():
            summary: dict[str, Any] = {}
            for w, vals in windows.items():
                summary[w] = _agg(vals)
            for w in ("1m", "3m", "6m"):
                mvals = mdd_acc.get(band, {}).get(a, {}).get(w, [])
                if mvals:
                    summary[f"mdd_{w}"] = {
                        "avg": statistics.mean(mvals),
                        "worst": min(mvals),
                        "count": len(mvals),
                    }
            sample = max((summary.get(w, {}).get("count", 0)
                          for w in FORWARD_WINDOWS), default=0)
            summary["confidence"] = _confidence_from_count(sample)
            by_band[band][a] = summary

    return {"by_band": by_band, "confidence": "computed"}


# ---------------------------------------------------------------------------
# Nasdaq Drawdown Deployment 전략 백테스트
# ---------------------------------------------------------------------------

# 낙폭 단계 → 누적 목표 투입비중 (crash_deployment 의 _DEPLOYMENT_ZONES 와 정합)
_DEPLOY_STEPS: list[tuple[float, float, str]] = [
    (-0.05, 0.20, "QQQ"),
    (-0.10, 0.45, "QLD"),
    (-0.15, 0.65, "QLD"),
    (-0.20, 0.80, "TQQQ"),
    (-0.25, 1.00, "TQQQ"),
]


def _build_qqq_drawdown_series(qqq: list[dict[str, Any]]) -> list[float]:
    """QQQ 각 시점의 52주 고점 대비 낙폭 시계열."""
    closes = _closes(qqq)
    out: list[float] = []
    for i in range(len(closes)):
        start = max(0, i - 251)
        high = max(closes[start:i + 1])
        out.append(closes[i] / high - 1.0 if high > 0 else 0.0)
    return out


def backtest_drawdown_deployment_strategy(conn) -> dict[str, Any]:
    """QQQ 고점대비 낙폭에 따라 QQQ→QLD→TQQQ 단계 투입 전략을
    Buy&Hold / 현금대기-후-투입 / 적립식 등과 비교.

    레버리지 ETF 의 MDD·decay 위험을 함께 보고한다.
    Returns: {"strategies": {name: {asset/summary}}, "rows": [...],
              "warnings": [...]}
    """
    data = fetch_historical_market_data(conn, ["QQQ", "QLD", "TQQQ", "SPY"])
    qqq = data.get("QQQ") or []
    qld = data.get("QLD") or []
    tqqq = data.get("TQQQ") or []
    if not qqq:
        return {"strategies": {}, "rows": [],
                "warnings": ["QQQ 데이터 없음 — 백테스트 불가"]}

    # 공통 날짜 — QQQ·QLD·TQQQ 모두 있는 구간으로 정렬
    qqq_by_date = {r["date"]: r for r in qqq}
    qld_by_date = {r["date"]: r for r in qld}
    tqqq_by_date = {r["date"]: r for r in tqqq}
    common = sorted(set(qqq_by_date) & set(qld_by_date) & set(tqqq_by_date))
    if len(common) < 60:
        # QLD/TQQQ 가 부족하면 QQQ 단독 구간이라도
        common = sorted(qqq_by_date)
        has_lev = False
    else:
        has_lev = True

    n_days = len(common)
    if n_days < 30:
        return {"strategies": {}, "rows": [],
                "warnings": ["공통 거래일 부족 — 백테스트 불가"]}

    qqq_c = [float(qqq_by_date[d]["adj_close"]) for d in common]
    qld_c = [float(qld_by_date[d]["adj_close"]) for d in common] if has_lev else []
    tqqq_c = [float(tqqq_by_date[d]["adj_close"]) for d in common] if has_lev else []

    # QQQ drawdown 시계열 (공통 날짜 기준)
    dd_series: list[float] = []
    for i in range(n_days):
        start = max(0, i - 251)
        high = max(qqq_c[start:i + 1])
        dd_series.append(qqq_c[i] / high - 1.0 if high > 0 else 0.0)

    strategies: dict[str, dict[str, Any]] = {}

    # --- 1) Buy & Hold QQQ / QLD / TQQQ ---
    bh_assets = {"QQQ": qqq_c}
    if has_lev:
        bh_assets["QLD"] = qld_c
        bh_assets["TQQQ"] = tqqq_c
    for a, closes in bh_assets.items():
        eq = [c / closes[0] for c in closes]
        strategies[f"Buy&Hold {a}"] = {
            "asset": a, "equity": eq,
            **_summarize_equity(eq, n_days),
        }

    # --- 2) 현금 100% 대기 → Drawdown Deployment (QQQ→QLD→TQQQ) ---
    # 현금에서 시작, 낙폭 단계 도달 시 해당 비중만큼 자산 매입.
    # 단순화: 각 단계는 한 번만 트리거, 매입 후 그 자산 수익률로 성장.
    def _run_deployment(use_leverage: bool) -> dict[str, Any]:
        cash = 1.0
        # 보유: 각 자산별 (매입가 인덱스, 투입금액)
        positions: list[tuple[str, int, float]] = []
        triggered: set[float] = set()
        equity: list[float] = []
        for i in range(n_days):
            dd = dd_series[i]
            for thr, cum_w, asset in _DEPLOY_STEPS:
                if dd <= thr and thr not in triggered:
                    triggered.add(thr)
                    # cum_w 대비 추가 투입분
                    prev_w = sum(w for t, w, _ in _DEPLOY_STEPS
                                 if t in triggered and t != thr)
                    add_w = max(0.0, cum_w - prev_w)
                    invest = min(cash, add_w)
                    if invest > 0:
                        a = asset if use_leverage else "QQQ"
                        positions.append((a, i, invest))
                        cash -= invest
            # equity 계산
            val = cash
            for a, buy_i, amt in positions:
                ref = {"QQQ": qqq_c, "QLD": qld_c, "TQQQ": tqqq_c}.get(a, qqq_c)
                if buy_i < len(ref) and ref[buy_i] > 0:
                    val += amt * (ref[i] / ref[buy_i])
            equity.append(val)
        return {"equity": equity, **_summarize_equity(equity, n_days)}

    strategies["Drawdown Deployment (QQQ→QLD→TQQQ)"] = {
        "asset": "MULTI", **_run_deployment(use_leverage=has_lev),
    }
    strategies["Drawdown Deployment (QQQ only)"] = {
        "asset": "QQQ", **_run_deployment(use_leverage=False),
    }

    # --- 3) 현금 대기 후 일괄 투입 (낙폭 -15% 도달 시 QQQ 일괄) ---
    cash_then_invest: list[float] = []
    invested = False
    buy_idx = None
    for i in range(n_days):
        if not invested and dd_series[i] <= -0.15:
            invested = True
            buy_idx = i
        if not invested or buy_idx is None:
            cash_then_invest.append(1.0)
        else:
            cash_then_invest.append(qqq_c[i] / qqq_c[buy_idx])
    strategies["현금대기 후 -15% 일괄투입 QQQ"] = {
        "asset": "QQQ", "equity": cash_then_invest,
        **_summarize_equity(cash_then_invest, n_days),
    }

    # --- 4) 적립식 (월 1회 균등 매수 QQQ) ---
    dca_equity: list[float] = []
    dca_units = 0.0           # 누적 보유 수량 (정규화)
    dca_invested = 0.0        # 누적 투입 원금
    months = max(1, n_days // 21)
    per_month = 1.0 / months
    for i in range(n_days):
        if i % 21 == 0 and dca_invested < 1.0 - 1e-9:
            amt = min(per_month, 1.0 - dca_invested)
            if qqq_c[i] > 0:
                dca_units += amt / qqq_c[i]
            dca_invested += amt
        cash_left = max(0.0, 1.0 - dca_invested)
        dca_equity.append(cash_left + dca_units * qqq_c[i])
    strategies["적립식 (월적립 QQQ)"] = {
        "asset": "QQQ", "equity": dca_equity,
        **_summarize_equity(dca_equity, n_days),
    }

    # 경고
    warnings: list[str] = []
    if not has_lev:
        warnings.append(
            "QLD/TQQQ 일봉이 부족해 레버리지 단계는 QQQ 로 대체 백테스트했습니다."
        )
    if has_lev:
        tq = strategies.get("Buy&Hold TQQQ", {})
        tq_mdd = tq.get("max_drawdown")
        tq_rec = tq.get("recovery_time")
        if tq_mdd is not None:
            warnings.append(
                f"TQQQ(3x) Buy&Hold 최대낙폭 {tq_mdd*100:.0f}%"
                + (f", 회복 {tq_rec}영업일" if tq_rec else ", 회복 미완료")
                + " — 레버리지 ETF 는 변동성 끌림(decay)과 깊은 MDD 위험이 있습니다."
            )
    warnings.append(
        "백테스트는 과거 검증이며 미래 수익을 보장하지 않습니다. "
        "거래비용·세금·슬리피지는 반영되지 않았습니다."
    )

    # DB 저장용 rows
    rows: list[dict[str, Any]] = []
    start_date, end_date = common[0], common[-1]
    for name, s in strategies.items():
        rows.append({
            "strategy_name": name,
            "asset": s.get("asset", "MULTI"),
            "start_date": start_date, "end_date": end_date,
            "cagr": s.get("cagr"), "total_return": s.get("total_return"),
            "max_drawdown": s.get("max_drawdown"), "sharpe": s.get("sharpe"),
            "sortino": s.get("sortino"), "calmar": s.get("calmar"),
            "win_rate": s.get("win_rate"), "recovery_time": s.get("recovery_time"),
            "details_json": {"n_days": n_days, "has_leverage": has_lev},
        })

    confidence = _confidence_from_count(n_days)
    return {"strategies": strategies, "rows": rows, "warnings": warnings,
            "start_date": start_date, "end_date": end_date,
            "confidence": confidence}


# ---------------------------------------------------------------------------
# Parking 전략 백테스트 (간단 버전)
# ---------------------------------------------------------------------------

_PARKING_TICKERS = ["MCD", "KO", "COST"]


def backtest_parking_strategy(conn) -> dict[str, Any]:
    """비싼 국면에 parking stock 보유 vs 현금 vs QQQ 비교 (간단 버전).

    Overheat Score 가 높은(>=70) 구간에 진입한 경우, 이후 forward 수익률을
    parking stock 평균 vs QQQ 로 비교. + 전체 기간 Buy&Hold 비교.
    """
    data = fetch_historical_market_data(conn, _PARKING_TICKERS + ["QQQ"])
    qqq = data.get("QQQ") or []
    if not qqq:
        return {"buy_hold": {}, "expensive_regime": {},
                "confidence": "Data Unavailable"}

    # 전체 기간 Buy&Hold 비교
    buy_hold: dict[str, Any] = {}
    for t in _PARKING_TICKERS + ["QQQ"]:
        series = data.get(t) or []
        closes = _closes(series)
        if len(closes) < 30:
            continue
        eq = [c / closes[0] for c in closes]
        buy_hold[t] = _summarize_equity(eq, len(closes))

    # 비싼 국면(overheat>=70) 진입 후 forward 비교
    overheat_rows = calculate_historical_overheat_scores(conn)
    expensive_dates = [r["date"] for r in overheat_rows
                       if (r.get("overheat_score") or 0) >= 70]

    expensive: dict[str, Any] = {}
    for t in _PARKING_TICKERS + ["QQQ"]:
        series = data.get(t) or []
        idx = {r["date"]: k for k, r in enumerate(series)}
        closes = _closes(series)
        fwd_3m: list[float] = []
        fwd_6m: list[float] = []
        for d in expensive_dates:
            i = idx.get(d)
            if i is None:
                continue
            r3 = _forward_return(closes, i, FORWARD_WINDOWS["3m"])
            r6 = _forward_return(closes, i, FORWARD_WINDOWS["6m"])
            if r3 is not None:
                fwd_3m.append(r3)
            if r6 is not None:
                fwd_6m.append(r6)
        expensive[t] = {"3m": _agg(fwd_3m), "6m": _agg(fwd_6m)}

    sample = len(expensive_dates)
    return {"buy_hold": buy_hold, "expensive_regime": expensive,
            "expensive_sample": sample,
            "confidence": _confidence_from_count(sample)}


# ---------------------------------------------------------------------------
# Profit Protection 룰 백테스트 (간단 버전)
# ---------------------------------------------------------------------------

def backtest_profit_protection_rules(conn) -> dict[str, Any]:
    """고베타 수익 포지션 익절 룰이 과거 MDD 를 줄였는지 간단 검증.

    룰: QLD(2x 고베타 proxy)를 보유 중, Overheat Score 가 85 이상이면
    QQQ 로 비중을 옮긴다 (익절·방어). vs 룰 없이 QLD Buy&Hold.
    """
    data = fetch_historical_market_data(conn, ["QLD", "QQQ"])
    qld = data.get("QLD") or []
    qqq = data.get("QQQ") or []
    if not qld or not qqq:
        return {"with_rule": {}, "without_rule": {},
                "confidence": "Data Unavailable"}

    qld_by_date = {r["date"]: r for r in qld}
    qqq_by_date = {r["date"]: r for r in qqq}
    common = sorted(set(qld_by_date) & set(qqq_by_date))
    if len(common) < 60:
        return {"with_rule": {}, "without_rule": {},
                "confidence": "Sample Limited"}

    qld_c = [float(qld_by_date[d]["adj_close"]) for d in common]
    qqq_c = [float(qqq_by_date[d]["adj_close"]) for d in common]

    overheat_rows = calculate_historical_overheat_scores(conn)
    overheat_by_date = {r["date"]: r.get("overheat_score") for r in overheat_rows}

    # without rule — QLD Buy&Hold
    eq_no = [c / qld_c[0] for c in qld_c]

    # with rule — overheat>=85 인 날은 QQQ 수익률, 아니면 QLD 수익률
    eq_rule: list[float] = [1.0]
    for i in range(1, len(common)):
        score = overheat_by_date.get(common[i - 1])
        if score is not None and score >= 85:
            ref = qqq_c
        else:
            ref = qld_c
        ret = ref[i] / ref[i - 1] - 1.0 if ref[i - 1] > 0 else 0.0
        eq_rule.append(eq_rule[-1] * (1.0 + ret))

    n = len(common)
    return {
        "with_rule": _summarize_equity(eq_rule, n),
        "without_rule": _summarize_equity(eq_no, n),
        "start_date": common[0], "end_date": common[-1],
        "confidence": _confidence_from_count(n),
    }


# ---------------------------------------------------------------------------
# 종합 + 증분 갱신
# ---------------------------------------------------------------------------

def generate_backtest_summary(conn) -> dict[str, Any]:
    """위 결과를 종합한 dict + rule-based 한국어 코멘트."""
    summary: dict[str, Any] = {"generated_at": _dt.datetime.now().isoformat(timespec="seconds")}
    comments: list[str] = []

    try:
        regime_bt = backtest_regime_forward_returns(conn)
        summary["regime_forward"] = regime_bt
    except Exception as e:
        log.warning("regime forward 백테스트 실패: %s", e)
        summary["regime_forward"] = {"by_regime": {}}

    try:
        overheat_bt = backtest_overheat_forward_returns(conn)
        summary["overheat_forward"] = overheat_bt
    except Exception as e:
        log.warning("overheat forward 백테스트 실패: %s", e)
        summary["overheat_forward"] = {"by_band": {}}

    try:
        deploy_bt = backtest_drawdown_deployment_strategy(conn)
        summary["drawdown_deployment"] = deploy_bt
    except Exception as e:
        log.warning("drawdown deployment 백테스트 실패: %s", e)
        summary["drawdown_deployment"] = {"strategies": {}}

    try:
        summary["parking"] = backtest_parking_strategy(conn)
    except Exception as e:
        log.warning("parking 백테스트 실패: %s", e)
        summary["parking"] = {}

    try:
        summary["profit_protection"] = backtest_profit_protection_rules(conn)
    except Exception as e:
        log.warning("profit protection 백테스트 실패: %s", e)
        summary["profit_protection"] = {}

    # rule-based 한국어 코멘트
    by_band = (summary.get("overheat_forward") or {}).get("by_band") or {}
    if by_band:
        hi = by_band.get("85-100 FOMO", {}).get("QQQ", {})
        lo = by_band.get("0-30 정상", {}).get("QQQ", {})
        hi3 = (hi.get("3m") or {}).get("avg")
        lo3 = (lo.get("3m") or {}).get("avg")
        if hi3 is not None and lo3 is not None:
            comments.append(
                f"과거 검증: Overheat 85+ 진입 후 QQQ 3개월 평균 {hi3*100:+.1f}%, "
                f"Overheat 30 미만 진입 시 {lo3*100:+.1f}%. "
                + ("과열 구간 진입의 기대수익이 낮았습니다."
                   if hi3 < lo3 else "표본이 제한적이니 참고용으로만 보십시오.")
            )
    deploy = (summary.get("drawdown_deployment") or {}).get("strategies") or {}
    if deploy:
        dep = deploy.get("Drawdown Deployment (QQQ→QLD→TQQQ)", {})
        bh = deploy.get("Buy&Hold QQQ", {})
        d_mdd = dep.get("max_drawdown")
        b_mdd = bh.get("max_drawdown")
        if d_mdd is not None and b_mdd is not None:
            comments.append(
                f"낙폭 단계투입 전략의 최대낙폭은 {d_mdd*100:.0f}%, "
                f"QQQ Buy&Hold 는 {b_mdd*100:.0f}% — "
                + ("단계투입이 MDD 를 줄였습니다."
                   if d_mdd > b_mdd else "표본 구간에서는 차이가 작았습니다.")
            )
    if not comments:
        comments.append(
            "백테스트 표본이 부족하거나 데이터가 없습니다 — "
            "data_cache 로 일봉을 먼저 확보하세요."
        )
    comments.append(
        "※ 모든 수치는 과거 검증 결과이며 미래 수익을 보장하지 않습니다. "
        "레버리지 ETF(QLD/TQQQ)는 변동성 끌림과 깊은 MDD 위험이 있습니다."
    )
    summary["commentary_ko"] = " ".join(comments)
    return summary


def _persist_backtest(conn, summary: dict[str, Any]) -> dict[str, int]:
    """백테스트 결과를 DB(backtest_results / regime_forward_returns)에 저장."""
    from . import database as _db
    saved = {"backtest_results": 0, "regime_forward_returns": 0}

    # drawdown deployment → backtest_results
    deploy_rows = (summary.get("drawdown_deployment") or {}).get("rows") or []
    for r in deploy_rows:
        try:
            _db.upsert_backtest_result(conn, r)
            saved["backtest_results"] += 1
        except Exception as e:
            log.debug("backtest_result 저장 실패: %s", e)

    # profit protection → backtest_results
    pp = summary.get("profit_protection") or {}
    for name, key in (("ProfitProtection with-rule", "with_rule"),
                       ("ProfitProtection no-rule", "without_rule")):
        s = pp.get(key) or {}
        if s:
            try:
                _db.upsert_backtest_result(conn, {
                    "strategy_name": name, "asset": "QLD",
                    "start_date": pp.get("start_date"),
                    "end_date": pp.get("end_date"),
                    "cagr": s.get("cagr"), "total_return": s.get("total_return"),
                    "max_drawdown": s.get("max_drawdown"),
                    "sharpe": s.get("sharpe"), "sortino": s.get("sortino"),
                    "calmar": s.get("calmar"), "win_rate": s.get("win_rate"),
                    "recovery_time": s.get("recovery_time"),
                    "details_json": {"confidence": pp.get("confidence")},
                })
                saved["backtest_results"] += 1
            except Exception as e:
                log.debug("profit protection 저장 실패: %s", e)

    # parking → backtest_results (Buy&Hold)
    parking = summary.get("parking") or {}
    for t, s in (parking.get("buy_hold") or {}).items():
        try:
            _db.upsert_backtest_result(conn, {
                "strategy_name": "Parking Buy&Hold", "asset": t,
                "cagr": s.get("cagr"), "total_return": s.get("total_return"),
                "max_drawdown": s.get("max_drawdown"), "sharpe": s.get("sharpe"),
                "sortino": s.get("sortino"), "calmar": s.get("calmar"),
                "win_rate": s.get("win_rate"),
                "recovery_time": s.get("recovery_time"),
                "details_json": {"confidence": parking.get("confidence")},
            })
            saved["backtest_results"] += 1
        except Exception as e:
            log.debug("parking 저장 실패: %s", e)

    # regime forward → regime_forward_returns
    rfr_rows = (summary.get("regime_forward") or {}).get("rows") or []
    if rfr_rows:
        try:
            saved["regime_forward_returns"] = _db.upsert_regime_forward_returns(
                conn, rfr_rows)
        except Exception as e:
            log.debug("regime_forward_returns 저장 실패: %s", e)
    return saved


def update_backtest_incrementally(conn, full: bool = False) -> dict[str, Any]:
    """새 데이터만 반영해 백테스트 갱신.

    1) data_cache 로 일봉 증분 append (full=True 면 전체 재다운로드)
    2) 백테스트 재계산 (regime/overheat/deployment/parking/profit-protection)
    3) backtest_results / regime_forward_returns 테이블 저장

    외부 데이터 실패해도 예외 비전파.
    """
    from . import data_cache as _dc

    result: dict[str, Any] = {"ok": False}
    try:
        if full:
            cache_res = _dc.refresh_full_history(conn)
        else:
            cache_res = _dc.append_new_market_data(conn)
        result["cache"] = cache_res
    except Exception as e:
        log.warning("data_cache 갱신 실패 (graceful): %s", e)
        result["cache"] = {"error": str(e)}

    try:
        summary = generate_backtest_summary(conn)
        saved = _persist_backtest(conn, summary)
        result["saved"] = saved
        result["commentary_ko"] = summary.get("commentary_ko")
        result["ok"] = True
    except Exception as e:
        log.warning("백테스트 갱신 실패 (graceful): %s", e)
        result["error"] = str(e)
    return result
