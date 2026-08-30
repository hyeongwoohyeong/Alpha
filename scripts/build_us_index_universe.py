"""Build US Index Universe — S&P 500, NASDAQ 100, Russell 1000 구성종목 fetch.

Output:  data/us_index_universe.csv
Columns: ticker, name, index_membership, sector, industry, market_cap_tier

Sources:
  S&P 500    : Wikipedia (stable)
  NASDAQ 100 : Wikipedia (stable)
  Russell 1000: iShares IWB holdings CSV → fallback S&P MidCap 400 (Wikipedia)

GitHub Actions weekly_universe_scan.yml 에서 weekly_growth_scan.py 전에 실행.
"""
from __future__ import annotations

import csv
import io
import logging
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_us_index_universe")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUTPUT = DATA_DIR / "us_index_universe.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Wikipedia URL 상수
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
MIDCAP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"

# iShares IWB (Russell 1000 ETF) holdings CSV
ISHARES_IWB_URL = (
    "https://www.ishares.com/us/products/239707/ISHARES-RUSSELL-1000-ETF/"
    "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _norm_ticker(t: str) -> str:
    """Wikipedia dot-notation → yfinance hyphen. BRK.B → BRK-B."""
    return t.strip().replace(".", "-").upper()


def _wiki_tables(url: str):
    """Wikipedia 페이지에서 pandas read_html 로 테이블 list 반환."""
    try:
        import pandas as pd
        tables = pd.read_html(url, flavor="lxml")
        return tables
    except Exception as e:
        log.warning("Wikipedia table fetch 실패 (%s): %s", url, e)
        return []


# ──────────────────────────────────────────────────────────────────────────────
# S&P 500
# ──────────────────────────────────────────────────────────────────────────────

def fetch_sp500() -> list[dict]:
    """S&P 500 구성종목 — Wikipedia Table 0."""
    tables = _wiki_tables(SP500_URL)
    if not tables:
        return []
    df = tables[0]
    # 컬럼: Symbol, Security, GICS Sector, GICS Sub-Industry, ...
    cols = {c.lower().replace(" ", "_"): c for c in df.columns}
    sym_col = next((v for k, v in cols.items() if "symbol" in k), None)
    name_col = next((v for k, v in cols.items() if "security" in k or "company" in k), None)
    sec_col  = next((v for k, v in cols.items() if "sector" in k), None)
    ind_col  = next((v for k, v in cols.items() if "sub" in k and "industry" in k), None)

    out = []
    for _, row in df.iterrows():
        t = _norm_ticker(str(row.get(sym_col, "") or ""))
        if not t or t == "NAN":
            continue
        out.append({
            "ticker": t,
            "name": str(row.get(name_col, "") or ""),
            "sector": str(row.get(sec_col, "") or ""),
            "industry": str(row.get(ind_col, "") or ""),
            "index": "SP500",
            "market_cap_tier": "large",
        })
    log.info("S&P 500: %d종목 fetch", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# NASDAQ 100
# ──────────────────────────────────────────────────────────────────────────────

def fetch_ndx100() -> list[dict]:
    """NASDAQ 100 구성종목 — Wikipedia. 테이블 인덱스 유동적이므로 컬럼명으로 탐색."""
    tables = _wiki_tables(NDX100_URL)
    if not tables:
        return []

    target = None
    for tbl in tables:
        cols_lower = [str(c).lower() for c in tbl.columns]
        if any("ticker" in c or "symbol" in c for c in cols_lower):
            if len(tbl) > 50:          # 100종목 이상 있는 테이블
                target = tbl
                break

    if target is None:
        log.warning("NASDAQ 100 테이블 못 찾음")
        return []

    cols = {str(c).lower(): c for c in target.columns}
    sym_col  = next((v for k, v in cols.items() if "ticker" in k or "symbol" in k), None)
    name_col = next((v for k, v in cols.items() if "company" in k or "security" in k), None)
    sec_col  = next((v for k, v in cols.items() if "sector" in k), None)
    ind_col  = next((v for k, v in cols.items() if "industry" in k), None)

    out = []
    for _, row in target.iterrows():
        t = _norm_ticker(str(row.get(sym_col, "") or ""))
        if not t or t == "NAN":
            continue
        out.append({
            "ticker": t,
            "name": str(row.get(name_col, "") or ""),
            "sector": str(row.get(sec_col, "") or ""),
            "industry": str(row.get(ind_col, "") or ""),
            "index": "NDX100",
            "market_cap_tier": "large",
        })
    log.info("NASDAQ 100: %d종목 fetch", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Russell 1000 — iShares IWB 우선, 실패 시 S&P MidCap 400 fallback
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_ishares_iwb() -> list[dict]:
    """iShares IWB (Russell 1000 ETF) holdings CSV."""
    try:
        r = requests.get(
            ISHARES_IWB_URL,
            headers={**HEADERS, "Referer": "https://www.ishares.com"},
            timeout=30,
        )
        if r.status_code != 200 or "Ticker" not in r.text[:500]:
            log.warning("iShares IWB CSV 응답 이상 (status=%s, 첫 200자: %s)",
                        r.status_code, r.text[:200])
            return []

        # CSV 헤더가 2행 이후부터 시작 (첫 두 줄은 메타)
        lines = r.text.splitlines()
        start = next((i for i, l in enumerate(lines) if "Ticker" in l), None)
        if start is None:
            return []

        reader = csv.DictReader(lines[start:])
        out = []
        for row in reader:
            t = _norm_ticker(row.get("Ticker", "") or "")
            if not t or t in ("-", "CASH"):
                continue
            out.append({
                "ticker": t,
                "name": row.get("Name", ""),
                "sector": row.get("Sector", ""),
                "industry": "",
                "index": "R1000",
                "market_cap_tier": "large",   # 상위 500 대형, 나머지 mid — 단순화
            })
        log.info("iShares IWB: %d종목 fetch", len(out))
        return out
    except Exception as e:
        log.warning("iShares IWB fetch 예외: %s", e)
        return []


def _fetch_sp400_fallback() -> list[dict]:
    """S&P MidCap 400 — Russell 1000 fallback (SP500 + SP400 ≈ Russell 1000 large/mid tier)."""
    tables = _wiki_tables(MIDCAP400_URL)
    if not tables:
        return []
    df = tables[0]
    cols = {str(c).lower(): c for c in df.columns}
    sym_col  = next((v for k, v in cols.items() if "ticker" in k or "symbol" in k), None)
    name_col = next((v for k, v in cols.items() if "company" in k or "security" in k), None)
    sec_col  = next((v for k, v in cols.items() if "sector" in k), None)
    ind_col  = next((v for k, v in cols.items() if "sub" in k and "industry" in k), None)

    out = []
    for _, row in df.iterrows():
        t = _norm_ticker(str(row.get(sym_col, "") or ""))
        if not t or t == "NAN":
            continue
        out.append({
            "ticker": t,
            "name": str(row.get(name_col, "") or ""),
            "sector": str(row.get(sec_col, "") or ""),
            "industry": str(row.get(ind_col, "") or ""),
            "index": "R1000",
            "market_cap_tier": "mid",
        })
    log.info("S&P MidCap 400 (R1000 fallback): %d종목 fetch", len(out))
    return out


def fetch_russell1000() -> list[dict]:
    """Russell 1000 = iShares IWB 우선. 실패 시 MidCap 400 fallback."""
    rows = _fetch_ishares_iwb()
    if rows:
        return rows
    log.info("iShares IWB 실패 → S&P MidCap 400 fallback 사용")
    return _fetch_sp400_fallback()


# ──────────────────────────────────────────────────────────────────────────────
# Merge + Deduplicate
# ──────────────────────────────────────────────────────────────────────────────

def build_universe() -> list[dict]:
    """세 인덱스를 merge. 중복 ticker는 index_membership 컬럼에 모두 기록."""
    time.sleep(0.5)
    sp500  = fetch_sp500()
    time.sleep(0.5)
    ndx100 = fetch_ndx100()
    time.sleep(0.5)
    r1000  = fetch_russell1000()

    # ticker → 데이터 병합
    merged: dict[str, dict] = {}

    for row in sp500 + ndx100 + r1000:
        t = row["ticker"]
        if t not in merged:
            merged[t] = {
                "ticker": t,
                "name": row["name"],
                "sector": row["sector"],
                "industry": row["industry"],
                "market_cap_tier": row["market_cap_tier"],
                "indices": set(),
            }
        merged[t]["indices"].add(row["index"])
        # 이름/섹터 우선순위: SP500 > NDX100 > R1000 (SP500 먼저 들어오니 이미 있으면 skip)
        if not merged[t]["name"] and row["name"]:
            merged[t]["name"] = row["name"]
        if not merged[t]["sector"] and row["sector"]:
            merged[t]["sector"] = row["sector"]

    # index_membership 문자열로 변환 (SP500,NDX100 등)
    final = []
    for t, d in merged.items():
        indices = sorted(d["indices"])
        # market_cap_tier 결정: SP500 또는 NDX100에 있으면 large, 아니면 mid
        tier = "large" if ("SP500" in indices or "NDX100" in indices) else "mid"
        final.append({
            "ticker": t,
            "name": d["name"],
            "index_membership": ",".join(indices),
            "sector": d["sector"],
            "industry": d["industry"],
            "market_cap_tier": tier,
        })

    # 알파벳 정렬
    final.sort(key=lambda x: x["ticker"])

    sp_cnt  = sum(1 for r in final if "SP500"  in r["index_membership"])
    ndx_cnt = sum(1 for r in final if "NDX100" in r["index_membership"])
    r1k_cnt = sum(1 for r in final if "R1000"  in r["index_membership"])
    log.info("최종 universe: %d종목 (SP500=%d, NDX100=%d, R1000=%d)",
             len(final), sp_cnt, ndx_cnt, r1k_cnt)
    return final


# ──────────────────────────────────────────────────────────────────────────────
# Write CSV
# ──────────────────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fieldnames = ["ticker", "name", "index_membership", "sector", "industry", "market_cap_tier"]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        f.write("# US Index Universe — S&P 500 / NASDAQ 100 / Russell 1000\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("저장 완료: %s (%d 종목)", OUTPUT, len(rows))


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    rows = build_universe()
    if not rows:
        log.error("Universe 비어 있음 — 종료")
        sys.exit(1)
    write_csv(rows)
    log.info("Done.")
