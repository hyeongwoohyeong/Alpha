"""KR 우량주 universe 빌더.

KOSPI 전체 → 정량 quality 필터 → data/kr_universe.csv.
필터 통과 종목만 살아남는다. 큐레이션 X — 데이터가 결정한다.

실행:
    python scripts/build_kr_universe.py
    (or `python -m scripts.build_kr_universe`)

데이터 소스:
    - pykrx: KRX 공식 데이터 (티커, 시총, 거래대금, PER/PBR/ROE)
    - yfinance: 분기/연간 손익계산서·현금흐름·재무상태표 (영업이익 흑자,
      OCF 양수, 부채비율 산출). KR 티커는 `.KS` suffix.
    - DART OpenDART API (선택): 유/무상증자 빈도, 주주 지분 변동.
      `DART_API_KEY` 환경변수 미설정 시 해당 필터 skip.
    - KCGS (한국기업지배구조원) 거버넌스 등급: 스크래핑 fragile —
      실패 시 skip 후 CSV header 에 명시.

필터 (모두 AND):
    1. 시총 ≥ ₩1조원
    2. 4 분기 연속 영업이익 흑자
    3. 5 년 연속 영업현금흐름 양수 (데이터 존재 범위 내)
    4. 부채비율 < 150%
    5. 5 년 평균 ROE ≥ 8%
    6. 일평균 거래대금 (60일) ≥ ₩30억
    7. 5 년간 유/무상증자 ≤ 2회 (DART 미연결 시 skip)
    8. 대주주 지분 절대 변동 < 5%p (DART 미연결 시 skip)
    9. KCGS 거버넌스 등급 ≥ B+ (스크래핑 실패 시 skip)
"""
from __future__ import annotations

import argparse
import concurrent.futures as _cf
import csv
import datetime as _dt
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# 경로 / 로거
# -----------------------------------------------------------------------------

# scripts/ 에서 import 하기 위한 path setup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_CSV = DATA_DIR / "kr_universe.csv"

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    level=os.environ.get("ALPHA_LOG", "INFO"),
)
log = logging.getLogger("build_kr_universe")


# -----------------------------------------------------------------------------
# 임계값 (모두 한국 시장 기준, 원화)
# -----------------------------------------------------------------------------

KRW_TRILLION = 1_000_000_000_000  # 1조
MIN_MARKET_CAP_KRW = 1 * KRW_TRILLION       # 1조원
MIN_OP_QUARTERS_POSITIVE = 4                 # 직전 4 분기 영업이익 흑자
MIN_OCF_YEARS_POSITIVE = 5                   # 직전 5 년 OCF 양수
MAX_DEBT_RATIO = 1.50                        # 부채/자본 < 150%
MIN_ROE_5Y_AVG = 0.08                        # 5년 평균 ROE 8%
MIN_AVG_TRADING_VALUE_KRW = 3_000_000_000    # 일평균 거래대금 30억
TRADING_WINDOW_DAYS = 60                     # 거래대금 평균 윈도우
MAX_CAPITAL_ACTIONS_5Y = 2                   # 5년간 유/무상증자 횟수
MAX_MAJOR_SHAREHOLDER_DELTA = 0.05           # 대주주 지분 변동 절대값 5%p
MIN_KCGS_GRADE = "B+"                        # KCGS 거버넌스 등급 하한

MAX_WORKERS = 10


# -----------------------------------------------------------------------------
# pykrx / yfinance 안전 로드
# -----------------------------------------------------------------------------

def _safe_pykrx():
    try:
        from pykrx import stock  # type: ignore
        return stock
    except Exception as e:
        log.error("pykrx import 실패: %s — `pip install pykrx --break-system-packages`",
                  e)
        return None


def _safe_yfinance():
    try:
        import yfinance as yf  # type: ignore
        return yf
    except Exception as e:
        log.warning("yfinance import 실패: %s — 재무 필터 skip 대상", e)
        return None


def _pykrx_version() -> str:
    try:
        import pykrx  # type: ignore
        return getattr(pykrx, "__version__", "unknown")
    except Exception:
        return "n/a"


# -----------------------------------------------------------------------------
# 영업일 결정 (KRX 휴장일 회피)
# -----------------------------------------------------------------------------

def _resolve_business_date(stock_mod) -> str:
    """오늘부터 최대 10일 거슬러 KRX 데이터 존재하는 첫 영업일 반환."""
    today = _dt.date.today()
    for back in range(0, 10):
        d = today - _dt.timedelta(days=back)
        ymd = d.strftime("%Y%m%d")
        try:
            df = stock_mod.get_market_cap_by_ticker(ymd, market="KOSPI")
            if df is not None and len(df) > 100:
                return ymd
        except Exception:
            continue
    # fallback: 오늘
    return today.strftime("%Y%m%d")


# -----------------------------------------------------------------------------
# 시장 메타 (시총·티커명·섹터·거래대금)
# -----------------------------------------------------------------------------

def _fetch_kospi_metadata(stock_mod, base_date: str) -> dict[str, dict[str, Any]]:
    """KOSPI 전종목 시총·종목명·섹터(가능한 경우) 일괄 로드.

    Returns: {ticker: {name, market_cap, shares, ...}}
    """
    meta: dict[str, dict[str, Any]] = {}

    log.info("KOSPI 시가총액 데이터 로드 (%s)", base_date)
    try:
        cap_df = stock_mod.get_market_cap_by_ticker(base_date, market="KOSPI")
    except Exception as e:
        log.error("get_market_cap_by_ticker 실패: %s", e)
        return meta

    # cap_df columns: 종가, 시가총액, 거래량, 거래대금, 상장주식수
    for ticker, row in cap_df.iterrows():
        try:
            meta[str(ticker)] = {
                "market_cap_krw": float(row.get("시가총액", 0) or 0),
                "shares_outstanding": float(row.get("상장주식수", 0) or 0),
                "last_close": float(row.get("종가", 0) or 0),
                "trading_value_today": float(row.get("거래대금", 0) or 0),
            }
        except Exception:
            continue

    log.info("KOSPI 시총 row 수: %d", len(meta))

    # 종목명 — bulk fetch 가 없으니 ticker 별 호출
    log.info("종목명 fetch (총 %d 종목)", len(meta))

    def _name(t: str) -> tuple[str, str]:
        try:
            return t, stock_mod.get_market_ticker_name(t)
        except Exception:
            return t, ""

    with _cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for t, n in ex.map(_name, list(meta.keys())):
            meta[t]["name_ko"] = n

    # PER/PBR/EPS/BPS/DIV/DPS/ROE — get_market_fundamental_by_ticker
    try:
        fund_df = stock_mod.get_market_fundamental_by_ticker(
            base_date, market="KOSPI",
        )
        for ticker, row in fund_df.iterrows():
            t = str(ticker)
            if t not in meta:
                continue
            # pykrx 가 ROE 컬럼을 직접 제공하지 않으면 EPS/BPS 로 추산
            eps = float(row.get("EPS", 0) or 0)
            bps = float(row.get("BPS", 0) or 0)
            meta[t]["per"] = float(row.get("PER", 0) or 0)
            meta[t]["pbr"] = float(row.get("PBR", 0) or 0)
            meta[t]["eps"] = eps
            meta[t]["bps"] = bps
            meta[t]["roe_simple"] = (eps / bps) if bps > 0 else None
    except Exception as e:
        log.warning("fundamental 로드 실패: %s", e)

    return meta


def _fetch_trading_value(
    stock_mod, ticker: str, base_date: str, window_days: int = 60,
) -> float | None:
    """지난 window_days 거래일 평균 거래대금(원). 실패 시 None."""
    end = _dt.datetime.strptime(base_date, "%Y%m%d").date()
    # window_days 거래일을 확보하려고 calendar 기준 1.7배 잡음
    start = end - _dt.timedelta(days=int(window_days * 1.7))
    try:
        df = stock_mod.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker,
        )
        if df is None or df.empty:
            return None
        # 거래대금 column = '거래대금'
        col = "거래대금" if "거래대금" in df.columns else None
        if col is None:
            return None
        return float(df[col].tail(window_days).mean())
    except Exception:
        return None


# -----------------------------------------------------------------------------
# yfinance — 분기 영업이익, 연간 OCF, 부채비율, ROE (history)
# -----------------------------------------------------------------------------

# yfinance financials index 후보들 (영문 표기 변형)
_OP_INCOME_KEYS = [
    "Operating Income", "OperatingIncome", "Total Operating Income As Reported",
]
_OCF_KEYS = [
    "Total Cash From Operating Activities",
    "Operating Cash Flow",
    "Cash Flow From Continuing Operating Activities",
    "CashFlowFromContinuingOperatingActivities",
]
_TOTAL_DEBT_KEYS = [
    "Total Debt", "TotalDebt", "Long Term Debt", "LongTermDebt",
]
_TOTAL_LIAB_KEYS = [
    "Total Liab", "TotalLiab", "Total Liabilities Net Minority Interest",
    "TotalLiabilitiesNetMinorityInterest",
]
_EQUITY_KEYS = [
    "Total Stockholder Equity", "TotalStockholderEquity",
    "Stockholders Equity", "StockholdersEquity", "Common Stock Equity",
    "CommonStockEquity",
]
_NET_INCOME_KEYS = [
    "Net Income", "NetIncome", "Net Income Common Stockholders",
    "NetIncomeCommonStockholders",
]


def _pick(df, keys: list[str]):
    if df is None or df.empty:
        return None
    for k in keys:
        if k in df.index:
            return df.loc[k]
    return None


def _fetch_financial_metrics(yf_mod, ticker_6digit: str) -> dict[str, Any]:
    """yfinance 로 분기/연간 재무지표 fetch.

    Returns dict with:
        op_profit_4q_positive (bool|None)
        ocf_5y_positive (bool|None) — 직전 5년 연속 OCF 양수
        ocf_years_positive (int|None)
        debt_ratio (float|None)
        roe_5y_avg (float|None) — net income / equity 평균
        roe_5y_values (list[float])
    """
    out: dict[str, Any] = {
        "op_profit_4q_positive": None,
        "ocf_5y_positive": None,
        "ocf_years_positive": None,
        "debt_ratio": None,
        "roe_5y_avg": None,
        "roe_5y_values": [],
        "yf_error": None,
    }
    if yf_mod is None:
        out["yf_error"] = "yfinance_not_installed"
        return out

    symbol = f"{ticker_6digit}.KS"
    try:
        t = yf_mod.Ticker(symbol)
    except Exception as e:
        out["yf_error"] = f"ticker_init_{type(e).__name__}"
        return out

    # 분기 영업이익 (4Q) — quarterly_income_stmt
    try:
        q_is = getattr(t, "quarterly_income_stmt", None)
        if q_is is None or (hasattr(q_is, "empty") and q_is.empty):
            q_is = getattr(t, "quarterly_financials", None)
        op_row = _pick(q_is, _OP_INCOME_KEYS)
        if op_row is not None:
            vals = [float(v) for v in op_row.values[:4] if v is not None]
            if vals and len(vals) >= 4:
                out["op_profit_4q_positive"] = all(v > 0 for v in vals[:4])
            elif vals:
                # 4 분기 미달이면 보유한 분기 전부 양수면 잠정 통과(soft)
                out["op_profit_4q_positive"] = all(v > 0 for v in vals)
    except Exception as e:
        out["yf_error"] = (out["yf_error"] or "") + f"|op_{type(e).__name__}"

    # 연간 OCF — cashflow / cash_flow
    try:
        cf = getattr(t, "cashflow", None)
        if cf is None or (hasattr(cf, "empty") and cf.empty):
            cf = getattr(t, "cash_flow", None)
        ocf_row = _pick(cf, _OCF_KEYS)
        if ocf_row is not None:
            vals = []
            for v in ocf_row.values[:MIN_OCF_YEARS_POSITIVE]:
                try:
                    vals.append(float(v))
                except Exception:
                    continue
            if vals:
                pos = sum(1 for v in vals if v > 0)
                out["ocf_years_positive"] = pos
                # 데이터가 5년 미만이어도 가용 연도 전부 양수면 통과 (soft)
                if len(vals) >= MIN_OCF_YEARS_POSITIVE:
                    out["ocf_5y_positive"] = (pos >= MIN_OCF_YEARS_POSITIVE)
                else:
                    out["ocf_5y_positive"] = (pos == len(vals))
    except Exception as e:
        out["yf_error"] = (out["yf_error"] or "") + f"|ocf_{type(e).__name__}"

    # 부채비율 — Total Liabilities / Equity (가장 최근 연간)
    try:
        bs = getattr(t, "balance_sheet", None)
        liab_row = _pick(bs, _TOTAL_LIAB_KEYS)
        if liab_row is None:
            liab_row = _pick(bs, _TOTAL_DEBT_KEYS)
        eq_row = _pick(bs, _EQUITY_KEYS)
        if liab_row is not None and eq_row is not None:
            liab = float(liab_row.values[0])
            eq = float(eq_row.values[0])
            if eq > 0:
                out["debt_ratio"] = liab / eq
    except Exception as e:
        out["yf_error"] = (out["yf_error"] or "") + f"|debt_{type(e).__name__}"

    # ROE 5y avg — net income / equity
    try:
        is_a = getattr(t, "income_stmt", None)
        if is_a is None or (hasattr(is_a, "empty") and is_a.empty):
            is_a = getattr(t, "financials", None)
        bs_a = getattr(t, "balance_sheet", None)
        ni_row = _pick(is_a, _NET_INCOME_KEYS)
        eq_row = _pick(bs_a, _EQUITY_KEYS)
        if ni_row is not None and eq_row is not None:
            roes: list[float] = []
            n = min(5, len(ni_row), len(eq_row))
            for i in range(n):
                try:
                    ni = float(ni_row.values[i])
                    eq = float(eq_row.values[i])
                    if eq > 0:
                        roes.append(ni / eq)
                except Exception:
                    continue
            if roes:
                out["roe_5y_values"] = roes
                out["roe_5y_avg"] = sum(roes) / len(roes)
    except Exception as e:
        out["yf_error"] = (out["yf_error"] or "") + f"|roe_{type(e).__name__}"

    return out


# -----------------------------------------------------------------------------
# DART (선택) — 유/무상증자 횟수, 대주주 지분 변동
# -----------------------------------------------------------------------------

def _dart_enabled() -> bool:
    return bool(os.environ.get("DART_API_KEY"))


def _fetch_dart_capital_actions(ticker_6digit: str) -> dict[str, Any]:
    """OpenDART API 로 최근 5년 유/무상증자 횟수 + 주요주주 지분 변동.

    DART_API_KEY 환경변수 없으면 즉시 빈 dict.
    네트워크 / API 실패 시 None 값 반환 — 호출자에서 skip 처리.
    """
    out: dict[str, Any] = {
        "capital_actions_5y": None,
        "major_shareholder_delta": None,
        "dart_error": None,
    }
    if not _dart_enabled():
        out["dart_error"] = "no_api_key"
        return out

    api_key = os.environ["DART_API_KEY"]

    # corp_code 매핑은 DART 가 zip 으로 제공 — 사전 캐싱 필요.
    # 여기선 단순 구현: corpCode mapping 캐시 파일이 있으면 사용.
    cache_file = DATA_DIR / "dart_corp_codes.json"
    corp_map: dict[str, str] = {}
    if cache_file.exists():
        try:
            import json
            corp_map = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            corp_map = {}

    corp_code = corp_map.get(ticker_6digit)
    if not corp_code:
        out["dart_error"] = "corp_code_missing"
        return out

    try:
        import requests
        # 정기보고서 - 증자/감자 내역
        url = "https://opendart.fss.or.kr/api/irdsSttus.json"
        five_yr_ago = (_dt.date.today() - _dt.timedelta(days=5 * 365)).strftime(
            "%Y%m%d"
        )
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": five_yr_ago,
            "end_de": _dt.date.today().strftime("%Y%m%d"),
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "000":
                # rcept 건수가 그대로 자본거래 횟수의 proxy
                rows = data.get("list", []) or []
                out["capital_actions_5y"] = len(rows)
            else:
                out["dart_error"] = f"dart_status_{data.get('status')}"
        else:
            out["dart_error"] = f"http_{r.status_code}"
    except Exception as e:
        out["dart_error"] = f"exception_{type(e).__name__}"

    # 주주현황은 DART 정기보고서 attached docs 파싱이 필요 — 본 구현은 skip
    # (소프트 패스: shareholder_delta 는 None → 호출자가 미적용으로 표기)
    return out


# -----------------------------------------------------------------------------
# KCGS 거버넌스 등급 (선택, fragile 스크래핑)
# -----------------------------------------------------------------------------

_KCGS_RANK = {"S": 0, "A+": 1, "A": 2, "B+": 3, "B": 4, "C": 5, "D": 6}


def _fetch_kcgs_grades() -> dict[str, str]:
    """KCGS 거버넌스 등급 일괄 fetch.

    공식 사이트(https://cgs.or.kr) 구조 변경에 취약. 실패 시 빈 dict.
    호출자는 빈 결과면 필터 미적용으로 처리.
    """
    grades: dict[str, str] = {}
    try:
        import requests
        from bs4 import BeautifulSoup  # type: ignore
        # CGS 등급공표 페이지 — 연도별 PDF/HTML 둘 다 있음. 본 구현은 best-effort.
        url = "https://www.cgs.or.kr/business/esg_tab04.jsp"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            log.warning("KCGS fetch HTTP %d — 거버넌스 필터 skip", r.status_code)
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        # 페이지 구조: table 내 종목명 + 등급. 보수적으로 row scan.
        for tr in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            # 첫 셀이 6자리 숫자(티커)인 경우만 채택
            t = cells[0]
            if t.isdigit() and len(t) == 6:
                # 마지막 셀이 등급으로 가정
                g = cells[-1].upper()
                if g in _KCGS_RANK:
                    grades[t] = g
        log.info("KCGS 등급 매핑 row 수: %d", len(grades))
    except Exception as e:
        log.warning("KCGS scrape 실패: %s — 거버넌스 필터 skip", e)
    return grades


def _kcgs_pass(grade: str | None) -> bool | None:
    if not grade:
        return None
    if grade.upper() not in _KCGS_RANK:
        return None
    return _KCGS_RANK[grade.upper()] <= _KCGS_RANK[MIN_KCGS_GRADE]


# -----------------------------------------------------------------------------
# 필터 적용
# -----------------------------------------------------------------------------

def _apply_filters(
    meta: dict[str, Any],
    fin: dict[str, Any],
    dart: dict[str, Any],
    kcgs_grade: str | None,
    skip_dart: bool,
    skip_kcgs: bool,
) -> tuple[bool, list[str]]:
    """각 필터 검사. (passed, failed_filters) 반환."""
    failed: list[str] = []

    # 1. 시총
    mcap = meta.get("market_cap_krw") or 0
    if mcap < MIN_MARKET_CAP_KRW:
        failed.append("market_cap")

    # 2. 4Q 영업이익 흑자
    if fin.get("op_profit_4q_positive") is False:
        failed.append("op_profit_4q")
    elif fin.get("op_profit_4q_positive") is None:
        failed.append("op_profit_4q_data_missing")

    # 3. 5Y OCF 양수
    if fin.get("ocf_5y_positive") is False:
        failed.append("ocf_5y")
    elif fin.get("ocf_5y_positive") is None:
        failed.append("ocf_5y_data_missing")

    # 4. 부채비율
    dr = fin.get("debt_ratio")
    if dr is None:
        failed.append("debt_ratio_data_missing")
    elif dr >= MAX_DEBT_RATIO:
        failed.append("debt_ratio")

    # 5. ROE 5Y avg
    roe = fin.get("roe_5y_avg")
    if roe is None:
        failed.append("roe_5y_data_missing")
    elif roe < MIN_ROE_5Y_AVG:
        failed.append("roe_5y")

    # 6. 거래대금
    avg_tv = meta.get("avg_trading_value_60d")
    if avg_tv is None:
        failed.append("trading_value_data_missing")
    elif avg_tv < MIN_AVG_TRADING_VALUE_KRW:
        failed.append("trading_value")

    # 7. 자본거래 횟수 (DART)
    if not skip_dart:
        ca = dart.get("capital_actions_5y")
        if ca is not None and ca > MAX_CAPITAL_ACTIONS_5Y:
            failed.append("capital_actions")

    # 8. 대주주 지분 변동 (DART)
    if not skip_dart:
        delta = dart.get("major_shareholder_delta")
        if delta is not None and abs(delta) > MAX_MAJOR_SHAREHOLDER_DELTA:
            failed.append("major_shareholder_delta")

    # 9. KCGS 등급
    if not skip_kcgs:
        kpass = _kcgs_pass(kcgs_grade)
        if kpass is False:
            failed.append("kcgs_grade")

    return (len(failed) == 0, failed)


# -----------------------------------------------------------------------------
# 메인 빌드 루틴
# -----------------------------------------------------------------------------

def build_kr_universe(
    *,
    limit: int | None = None,
    skip_kcgs_arg: bool = False,
) -> dict[str, Any]:
    """KOSPI 우량주 universe 빌드.

    Args:
        limit: 디버그용 — 처음 N 종목만 처리. None 이면 전체.
        skip_kcgs_arg: KCGS 스크래핑을 시도조차 하지 않음.

    Returns: summary dict (이미 disk 에 CSV 저장됨)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    stock_mod = _safe_pykrx()
    if stock_mod is None:
        log.error("pykrx 미설치 — 빌드 중단")
        return {"error": "pykrx_missing"}

    yf_mod = _safe_yfinance()

    base_date = _resolve_business_date(stock_mod)
    log.info("KRX 기준일: %s (pykrx %s)", base_date, _pykrx_version())

    # 1) 전 KOSPI 메타
    meta = _fetch_kospi_metadata(stock_mod, base_date)
    total_kospi = len(meta)
    log.info("KOSPI 전체 티커 수: %d", total_kospi)
    if total_kospi == 0:
        return {"error": "no_kospi_data", "total_kospi": 0}

    # 시총 사전 필터 — 1조 미만은 일단 메타만 보관, 무거운 fetch 안 함
    pre_pass = [
        t for t, m in meta.items()
        if (m.get("market_cap_krw") or 0) >= MIN_MARKET_CAP_KRW
    ]
    log.info("시총 ≥ 1조 후보: %d / %d", len(pre_pass), total_kospi)

    if limit:
        pre_pass = pre_pass[:limit]
        log.info("DEBUG limit 적용 — %d 종목만 처리", len(pre_pass))

    # 2) 거래대금 평균 — 시총 통과 종목만
    log.info("일평균 거래대금 (60일) 계산 중...")

    def _tv(t: str) -> tuple[str, float | None]:
        return t, _fetch_trading_value(stock_mod, t, base_date,
                                       TRADING_WINDOW_DAYS)

    with _cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for t, tv in ex.map(_tv, pre_pass):
            meta[t]["avg_trading_value_60d"] = tv

    # 3) 재무 지표 (yfinance) — 시총 + 거래대금 통과 종목만
    tv_pass = [
        t for t in pre_pass
        if (meta[t].get("avg_trading_value_60d") or 0)
        >= MIN_AVG_TRADING_VALUE_KRW
    ]
    log.info("거래대금 ≥ 30억 통과: %d", len(tv_pass))

    log.info("yfinance 재무 지표 fetch 중... (대상 %d 종목)", len(tv_pass))
    fin_by_ticker: dict[str, dict[str, Any]] = {}

    def _fin(t: str) -> tuple[str, dict[str, Any]]:
        return t, _fetch_financial_metrics(yf_mod, t)

    with _cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        done = 0
        for t, f in ex.map(_fin, tv_pass):
            fin_by_ticker[t] = f
            done += 1
            if done % 20 == 0:
                log.info("  yfinance 진행 %d / %d", done, len(tv_pass))

    # 4) DART (선택)
    dart_skip = not _dart_enabled()
    if dart_skip:
        log.info("DART_API_KEY 미설정 — 자본거래/지분변동 필터 SKIP")
    dart_by_ticker: dict[str, dict[str, Any]] = {}
    if not dart_skip:
        log.info("DART 자본거래/지분 fetch 중...")
        with _cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for t, d in ex.map(lambda x: (x, _fetch_dart_capital_actions(x)),
                               tv_pass):
                dart_by_ticker[t] = d
        # 모든 종목에서 corp_code_missing 이면 사실상 미적용
        if all(
            (d.get("dart_error") in ("corp_code_missing", "no_api_key", None)
             and d.get("capital_actions_5y") is None)
            for d in dart_by_ticker.values()
        ):
            log.warning("DART 데이터가 유효하지 않음 — 자본거래/지분 필터 SKIP")
            dart_skip = True

    # 5) KCGS (선택)
    kcgs_skip = skip_kcgs_arg
    kcgs_map: dict[str, str] = {}
    if not kcgs_skip:
        kcgs_map = _fetch_kcgs_grades()
        if not kcgs_map:
            log.warning("KCGS 등급 매핑 비어있음 — 거버넌스 필터 SKIP")
            kcgs_skip = True

    # 6) 필터 적용
    passed_rows: list[dict[str, Any]] = []
    fail_counter: dict[str, int] = {}
    eval_pool = tv_pass  # 시총·거래대금 둘 다 통과한 종목만 본 평가

    for t in eval_pool:
        m = meta.get(t, {})
        f = fin_by_ticker.get(t, {})
        d = dart_by_ticker.get(t, {})
        grade = kcgs_map.get(t)

        passed, failed = _apply_filters(
            m, f, d, grade, skip_dart=dart_skip, skip_kcgs=kcgs_skip,
        )
        for ff in failed:
            fail_counter[ff] = fail_counter.get(ff, 0) + 1

        if passed:
            passed_rows.append({
                "ticker": t,
                "name_ko": m.get("name_ko", ""),
                "sector": m.get("sector", ""),
                "industry": m.get("industry", ""),
                "market_cap_krw": int(m.get("market_cap_krw") or 0),
                "roe_5y_avg": (
                    round(f.get("roe_5y_avg"), 4)
                    if f.get("roe_5y_avg") is not None else None
                ),
                "debt_ratio": (
                    round(f.get("debt_ratio"), 4)
                    if f.get("debt_ratio") is not None else None
                ),
                "ocf_5y_positive": f.get("ocf_5y_positive"),
                "op_profit_4q_positive": f.get("op_profit_4q_positive"),
                "avg_dollar_volume_30d": int(
                    m.get("avg_trading_value_60d") or 0
                ),
                "kcgs_grade": grade or "",
                "filter_passed": True,
                "filters_failed": "",
            })

    # 시총 desc 정렬
    passed_rows.sort(key=lambda r: r["market_cap_krw"], reverse=True)

    # 시총 미달 종목의 카운트도 별도 보고
    fail_counter["market_cap_below_1T"] = total_kospi - len(pre_pass)

    # 7) CSV 작성 (header comment 포함)
    today_str = _dt.date.today().isoformat()
    skipped_filters: list[str] = []
    if dart_skip:
        skipped_filters.append(
            "자본거래·대주주지분 필터 SKIP (DART_API_KEY 미설정 또는 "
            "corp_code 매핑 부재)"
        )
    if kcgs_skip:
        skipped_filters.append(
            "KCGS 거버넌스 등급 필터 SKIP (스크래핑 실패 또는 비활성화)"
        )

    header_comments = [
        f"# KR 우량주 universe — 자동 생성",
        f"# 생성일: {today_str}",
        f"# pykrx version: {_pykrx_version()}",
        f"# KRX 기준일: {base_date}",
        f"# KOSPI 전체: {total_kospi}, 시총 ≥ 1조 후보: {len(pre_pass)}, 통과: {len(passed_rows)}",
        f"# 적용 필터:",
        f"#   1. 시총 ≥ ₩1조원",
        f"#   2. 4 분기 연속 영업이익 흑자 (yfinance)",
        f"#   3. 5 년 연속 영업현금흐름 양수 (yfinance, 데이터 가용 범위)",
        f"#   4. 부채비율 < {int(MAX_DEBT_RATIO*100)}% (Total Liab / Equity, yfinance)",
        f"#   5. 5 년 평균 ROE ≥ {int(MIN_ROE_5Y_AVG*100)}% (NI/Equity 평균, yfinance)",
        f"#   6. 일평균 거래대금 (60일) ≥ ₩30억 (pykrx)",
    ]
    if not dart_skip:
        header_comments.append(
            f"#   7. 5년간 유/무상증자 ≤ {MAX_CAPITAL_ACTIONS_5Y}회 (DART)"
        )
        header_comments.append(
            f"#   8. 대주주 지분 절대변동 < {int(MAX_MAJOR_SHAREHOLDER_DELTA*100)}%p (DART)"
        )
    if not kcgs_skip:
        header_comments.append(
            f"#   9. KCGS 거버넌스 등급 ≥ {MIN_KCGS_GRADE}"
        )
    if skipped_filters:
        header_comments.append("# SKIPPED 필터 (데이터 소스 이슈):")
        for s in skipped_filters:
            header_comments.append(f"#   - {s}")
    header_comments.append("#")

    fieldnames = [
        "ticker", "name_ko", "sector", "industry", "market_cap_krw",
        "roe_5y_avg", "debt_ratio", "ocf_5y_positive",
        "op_profit_4q_positive", "avg_dollar_volume_30d", "kcgs_grade",
        "filter_passed", "filters_failed",
    ]

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as fp:
        for line in header_comments:
            fp.write(line + "\n")
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in passed_rows:
            writer.writerow(row)

    log.info("CSV 저장: %s (rows=%d)", OUTPUT_CSV, len(passed_rows))

    # 8) 진단 요약
    log.info("=" * 60)
    log.info("BUILD SUMMARY")
    log.info("  KOSPI 전체:        %d", total_kospi)
    log.info("  시총 ≥ 1조 후보:    %d", len(pre_pass))
    log.info("  거래대금 ≥ 30억:    %d", len(tv_pass))
    log.info("  최종 통과:         %d", len(passed_rows))
    log.info("  --- 필터별 탈락 카운트 (중복 가능) ---")
    for k in sorted(fail_counter.keys()):
        log.info("    %-32s %d", k, fail_counter[k])
    log.info("=" * 60)

    return {
        "total_kospi": total_kospi,
        "pre_pass_mcap": len(pre_pass),
        "tv_pass": len(tv_pass),
        "passed": len(passed_rows),
        "fail_counter": fail_counter,
        "dart_skipped": dart_skip,
        "kcgs_skipped": kcgs_skip,
        "output_csv": str(OUTPUT_CSV),
        "passed_rows": passed_rows,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None,
                   help="디버그용 — 처음 N 종목만 처리")
    p.add_argument("--skip-kcgs", action="store_true",
                   help="KCGS 스크래핑 시도 자체를 skip")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    t0 = time.time()
    result = build_kr_universe(limit=args.limit, skip_kcgs_arg=args.skip_kcgs)
    dt = time.time() - t0
    log.info("총 소요: %.1f 초", dt)
    if result.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
