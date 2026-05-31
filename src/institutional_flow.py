"""외국인/기관 매수 momentum (KR).

학술 검증: 한국 시장에서 외국인 순매수 → 다음 30일 outperform +3~5%
기관 + 외국인 동반 매수는 *큰 cycle 진입* 신호 (한미반도체 2023.8 패턴)

Source:
  - pykrx get_market_trading_value_by_date / get_market_net_purchases_of_equities

신호 정의:
  - 최근 20거래일 외국인 순매수 누적 > 시총의 1% 이상
  - + 5거래일 가속 (점진 증가)
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .utils import get_logger

log = get_logger("institutional_flow")

# 임계값
FOREIGN_NET_THRESHOLD_PCT = 0.01    # 시총의 1% 이상 순매수
LOOKBACK_DAYS = 20


def fetch_foreign_institutional_flow(ticker: str) -> dict[str, Any]:
    """단일 KR ticker 외국인/기관 매수 분석.

    Returns:
        {
          "ticker": str,
          "available": bool,
          "foreign_net_buy_krw": float,
          "institutional_net_buy_krw": float,
          "foreign_pct_of_mcap": float,
          "is_accelerating": bool,
          "signal_strength": float,    # 0~100
        }
    """
    out: dict[str, Any] = {
        "ticker": ticker, "available": False,
        "foreign_net_buy_krw": 0.0,
        "institutional_net_buy_krw": 0.0,
        "foreign_pct_of_mcap": 0.0,
        "is_accelerating": False,
        "signal_strength": 0.0,
    }

    try:
        from pykrx import stock as krx
    except ImportError:
        log.debug("pykrx 미설치 — institutional_flow skip")
        return out

    # KR ticker 검증 (6자리 숫자)
    t = ticker.strip()
    if not (t.isdigit() and len(t) == 6):
        return out

    today = _dt.date.today()
    end_date = today
    # 영업일 보정
    for back in range(5):
        try:
            test = krx.get_market_ohlcv(end_date.strftime("%Y%m%d"),
                                         end_date.strftime("%Y%m%d"), t)
            if test is not None and not test.empty:
                break
        except Exception:
            pass
        end_date = today - _dt.timedelta(days=back + 1)

    start_date = end_date - _dt.timedelta(days=LOOKBACK_DAYS * 2)  # 영업일 ~ 캘린더일 buffer

    try:
        # 투자자별 누적 순매수 (외국인, 기관 등)
        flows = krx.get_market_trading_value_by_date(
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            t
        )
        if flows is None or flows.empty:
            return out
        # 컬럼: 기관합계, 기타법인, 개인, 외국인합계, 전체
        recent_20 = flows.tail(LOOKBACK_DAYS)
        foreign_net = float(recent_20["외국인합계"].sum())
        inst_net = float(recent_20["기관합계"].sum())

        # 시총 fetch
        mcap_row = krx.get_market_cap(end_date.strftime("%Y%m%d"), t)
        mcap = float(mcap_row.iloc[0]["시가총액"]) if mcap_row is not None and not mcap_row.empty else 0

        out["foreign_net_buy_krw"] = foreign_net
        out["institutional_net_buy_krw"] = inst_net
        out["foreign_pct_of_mcap"] = (foreign_net / mcap) if mcap else 0
        out["available"] = True

        # 가속 — 최근 5일 vs 그 전 15일
        last_5 = float(recent_20["외국인합계"].tail(5).sum())
        prev_15 = float(recent_20["외국인합계"].head(15).sum())
        # 일평균 비교
        last_5_avg = last_5 / 5
        prev_15_avg = prev_15 / 15 if prev_15 != 0 else 0
        out["is_accelerating"] = last_5_avg > prev_15_avg * 1.5 and last_5_avg > 0

        # Signal strength
        s = 0.0
        pct = out["foreign_pct_of_mcap"]
        if pct > 0:
            s += min(50, pct * 5000)  # 1% = 50pt
        if inst_net > 0:
            s += 20
        if out["is_accelerating"]:
            s += 30
        out["signal_strength"] = round(min(100.0, s), 1)
    except Exception as e:
        log.debug("flow fetch %s 실패: %s", ticker, e)

    return out


def is_strong_flow_signal(result: dict) -> bool:
    """Strong 외국인 매수 + 가속 + 기관 동반 매수."""
    return (
        result.get("available")
        and result.get("foreign_pct_of_mcap", 0) >= FOREIGN_NET_THRESHOLD_PCT
        and result.get("is_accelerating")
        and result.get("institutional_net_buy_krw", 0) > 0
    )


if __name__ == "__main__":
    import sys, json
    ticker = sys.argv[1] if len(sys.argv) > 1 else "042700"
    out = fetch_foreign_institutional_flow(ticker)
    print(json.dumps(out, indent=2, ensure_ascii=False))
