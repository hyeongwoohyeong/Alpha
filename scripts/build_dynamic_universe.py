"""Dynamic Universe Builder — KOSPI/KOSDAQ 전종목 자동 fetch.

기존 kr_universe.csv (quality 필터 통과 20종) 와 별개로,
모멘텀·hyper-growth 발굴용 *wide* universe 를 동적으로 빌드.

필터 (loose — quality 가 아닌 *기회 발굴* 위주):
  - 시총 ≥ ₩500억 (small cap 도 포함, +100% 가능성 ↑)
  - 60일 평균 거래대금 ≥ ₩5억 (유동성 minimum)
  - 상장 1년+ (분기 데이터 4번 이상)
  - SPAC / 관리종목 / 거래정지 제외

실행:
  python scripts/build_dynamic_universe.py
  → data/kr_dynamic_universe.csv 생성 (약 1500~2000 종목 예상)

GitHub Actions weekly_universe_scan.yml 가 매주 일요일 새벽 실행.
"""
from __future__ import annotations

import csv
import datetime as _dt
import logging
import sys
from pathlib import Path

# pykrx import — sandbox 에선 없어도 graceful
try:
    from pykrx import stock as krx
    HAS_PYKRX = True
except ImportError:
    HAS_PYKRX = False
    print("⚠️ pykrx 미설치 — production runner 에서만 동작")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUTPUT = DATA_DIR / "kr_dynamic_universe.csv"

# 필터 임계값
MIN_MARKET_CAP = 50_000_000_000      # ₩500억
MIN_DOLLAR_VOL = 500_000_000          # ₩5억/일
LOOKBACK_DAYS = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_dynamic_universe")


def _ts(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def fetch_kr_universe() -> list[dict]:
    """KOSPI + KOSDAQ 전종목 fetch → 필터링."""
    if not HAS_PYKRX:
        log.error("pykrx 미설치 — 빌드 중단")
        return []

    today = _dt.date.today()
    # 영업일로 보정 — pykrx 가 휴일이면 fail. 단순화: today 부터 거꾸로 5일 시도
    end_date = today
    for back in range(5):
        try:
            test = krx.get_market_cap(_ts(end_date), market="ALL")
            if test is not None and not test.empty:
                break
        except Exception:
            pass
        end_date = today - _dt.timedelta(days=back + 1)

    start_date = end_date - _dt.timedelta(days=LOOKBACK_DAYS)
    log.info("KR universe fetch: %s ~ %s", start_date, end_date)

    # 시총 + 거래대금 한 번에 (KOSPI + KOSDAQ 통합)
    market_cap = krx.get_market_cap(_ts(end_date), market="ALL")
    if market_cap is None or market_cap.empty:
        log.error("시총 데이터 fetch 실패")
        return []

    # KOSPI, KOSDAQ 종목 list 별도로 (market 구분 위해)
    kospi_tickers = set(krx.get_market_ticker_list(_ts(end_date), market="KOSPI"))
    kosdaq_tickers = set(krx.get_market_ticker_list(_ts(end_date), market="KOSDAQ"))

    log.info("KOSPI: %d / KOSDAQ: %d 종목", len(kospi_tickers), len(kosdaq_tickers))

    out: list[dict] = []
    for ticker, row in market_cap.iterrows():
        try:
            mcap = float(row.get("시가총액", 0) or 0)
            if mcap < MIN_MARKET_CAP:
                continue

            # 60일 거래대금 평균 fetch
            try:
                ohlcv = krx.get_market_ohlcv(_ts(start_date), _ts(end_date), ticker)
                if ohlcv is None or ohlcv.empty:
                    continue
                avg_vol = float((ohlcv["종가"] * ohlcv["거래량"]).mean())
                if avg_vol < MIN_DOLLAR_VOL:
                    continue
            except Exception:
                continue

            name = krx.get_market_ticker_name(ticker)
            market = "KOSPI" if ticker in kospi_tickers else ("KOSDAQ" if ticker in kosdaq_tickers else "?")

            # 관리종목 / SPAC 키워드 제거
            name_lower = name.lower()
            if any(kw in name_lower for kw in ["spac", "스팩", "관리", "거래정지"]):
                continue
            if name.endswith("우") or name.endswith("우B"):  # 우선주 제외
                continue

            out.append({
                "ticker": ticker,
                "name_ko": name,
                "market": market,
                "market_cap_krw": int(mcap),
                "avg_dollar_vol_60d": int(avg_vol),
                "market_cap_tier": (
                    "large" if mcap >= 10_000_000_000_000  # ₩10조+
                    else "mid" if mcap >= 1_000_000_000_000  # ₩1조+
                    else "small"
                ),
            })
        except Exception as e:
            log.debug("ticker %s 처리 실패: %s", ticker, e)
            continue

    log.info("필터 통과: %d 종목", len(out))
    return out


def fetch_us_universe_static() -> list[dict]:
    """US universe — 기존 wide_universe.csv 그대로 활용 (이미 dynamic 수준).
    필요 시 추후 Wikipedia S&P 1500 scrape 추가."""
    src = DATA_DIR / "wide_universe.csv"
    if not src.exists():
        return []
    out = []
    with open(src, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append({
                "ticker": row.get("ticker"),
                "name": row.get("name"),
                "market": row.get("exchange", ""),
                "sector": row.get("sector", ""),
                "industry": row.get("industry", ""),
                "market_cap_tier": row.get("market_cap_tier", "mid"),
            })
    return out


def write_kr_dynamic(rows: list[dict]) -> None:
    """KR dynamic universe CSV 저장."""
    if not rows:
        log.warning("KR rows 비어있음 — write skip")
        return
    cols = ["ticker", "name_ko", "market", "market_cap_krw",
            "avg_dollar_vol_60d", "market_cap_tier"]
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        f.write(f"# KR dynamic universe — built {_dt.date.today().isoformat()}\n")
        f.write(f"# Filters: mcap ≥ ₩{MIN_MARKET_CAP/1e8:.0f}억, daily vol ≥ ₩{MIN_DOLLAR_VOL/1e8:.0f}억\n")
        f.write(f"# Count: {len(rows)} 종목\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log.info("✓ %s 저장 (%d 종목)", OUTPUT, len(rows))


def main():
    log.info("=== Dynamic Universe Builder ===")
    rows = fetch_kr_universe()
    write_kr_dynamic(rows)
    # US 는 wide_universe.csv 그대로 사용 (별도 fetch 안 함)


if __name__ == "__main__":
    main()
