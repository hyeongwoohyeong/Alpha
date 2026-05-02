"""연간/분기 재무정보 수집 (yfinance income_stmt).

종목 상세 화면의 "주요 재무정보" 섹션 전용.
실패 시 빈 리스트 반환 → UI 에서 "데이터 확인 필요" 처리.
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger, safe_float

log = get_logger("financials")


def _safe_yf():
    try:
        import yfinance as yf  # type: ignore
        return yf
    except Exception:
        return None


_REVENUE_KEYS = ["Total Revenue", "TotalRevenue", "Revenue", "OperatingRevenue"]
_OP_INCOME_KEYS = ["Operating Income", "OperatingIncome", "OperatingIncomeLoss"]
_NET_INCOME_KEYS = [
    "Net Income",
    "NetIncome",
    "Net Income Common Stockholders",
    "NetIncomeCommonStockholders",
]


def _try_pick(df, col, candidates: list[str]):
    for k in candidates:
        if k in df.index:
            try:
                v = df.loc[k, col]
                return safe_float(v)
            except Exception:
                continue
    return None


def _label_annual(col, idx_from_latest: int) -> str:
    """연간 라벨 — '2024A' 형식."""
    try:
        y = col.year
        return f"{y}A"
    except Exception:
        return f"FY{idx_from_latest}A"


def _label_quarter(col) -> str:
    """분기 라벨 — '4Q24' 형식."""
    try:
        m = col.month
        q = (m - 1) // 3 + 1
        y = col.year % 100
        return f"{q}Q{y:02d}"
    except Exception:
        return str(col)[:7]


def _next_annual_label(periods: list[dict[str, Any]]) -> str:
    if not periods:
        return "차기E"
    last = periods[-1].get("period", "")
    try:
        y = int(last[:4])
        return f"{y + 1}E"
    except Exception:
        return "차기E"


def _next_quarter_label(periods: list[dict[str, Any]]) -> str:
    if not periods:
        return "차기E"
    last = periods[-1].get("period", "")
    # ex: "4Q24"
    try:
        q = int(last[0])
        y = int(last[2:])
        if q == 4:
            return f"1Q{(y + 1) % 100:02d}"
        return f"{q + 1}Q{y:02d}"
    except Exception:
        return "차기E"


def _build_periods(df, label_fn, take: int = 4) -> list[dict[str, Any]]:
    """DataFrame columns(date) → 기간 리스트 (오래된 → 최근)."""
    cols = list(df.columns)[:take]
    cols = list(reversed(cols))  # ascending
    out: list[dict[str, Any]] = []
    for i, c in enumerate(cols):
        rev = _try_pick(df, c, _REVENUE_KEYS)
        op = _try_pick(df, c, _OP_INCOME_KEYS)
        net = _try_pick(df, c, _NET_INCOME_KEYS)
        opm = (op / rev) if (op is not None and rev and rev > 0) else None
        out.append(
            {
                "period": label_fn(c, i) if label_fn is _label_annual else label_fn(c),
                "revenue": rev,
                "operating_income": op,
                "net_income": net,
                "operating_margin": opm,
                "is_forward": False,
            }
        )
    return out


def _append_forward(periods: list[dict[str, Any]], next_label_fn) -> list[dict[str, Any]]:
    if not periods:
        return periods
    fwd = {
        "period": next_label_fn(periods),
        "revenue": None,
        "operating_income": None,
        "net_income": None,
        "operating_margin": None,
        "is_forward": True,
    }
    return periods + [fwd]


def fetch_annual_financials(ticker: str) -> list[dict[str, Any]]:
    """최근 4개년 + Forward 1년 (placeholder)."""
    yf = _safe_yf()
    if yf is None:
        return []
    try:
        tk = yf.Ticker(ticker)
        df = tk.income_stmt
        if df is None or getattr(df, "empty", True):
            df = tk.financials
        if df is None or getattr(df, "empty", True):
            return []
        periods = _build_periods(df, _label_annual, take=4)
        return _append_forward(periods, _next_annual_label)
    except Exception as e:
        log.warning("[%s] annual financials 실패: %s", ticker, e)
        return []


def fetch_quarterly_financials(ticker: str) -> list[dict[str, Any]]:
    """최근 4개분기 + Forward 1분기 (placeholder)."""
    yf = _safe_yf()
    if yf is None:
        return []
    try:
        tk = yf.Ticker(ticker)
        df = tk.quarterly_income_stmt
        if df is None or getattr(df, "empty", True):
            df = tk.quarterly_financials
        if df is None or getattr(df, "empty", True):
            return []
        periods = _build_periods(df, _label_quarter, take=4)
        return _append_forward(periods, _next_quarter_label)
    except Exception as e:
        log.warning("[%s] quarterly financials 실패: %s", ticker, e)
        return []


# ---------------------------------------------------------------------------
# 해석 텍스트
# ---------------------------------------------------------------------------

def financials_interpretation(periods: list[dict[str, Any]]) -> str:
    """재무 추이 해석 (한국어 리포트 톤)."""
    historical = [p for p in periods if not p.get("is_forward")]
    if len(historical) < 2:
        return "최근 재무 데이터가 충분하지 않아 정밀 검토가 필요합니다."

    revs = [p["revenue"] for p in historical if p.get("revenue") is not None]
    ops = [p["operating_income"] for p in historical if p.get("operating_income") is not None]
    opms = [p["operating_margin"] for p in historical if p.get("operating_margin") is not None]
    forward = next((p for p in periods if p.get("is_forward")), None)

    bits: list[str] = []

    if len(revs) >= 2:
        rev_growth = (revs[-1] / revs[0] - 1) if revs[0] > 0 else None
        if rev_growth is not None and rev_growth > 0:
            bits.append("매출액이 외형적으로 성장세를 유지하고 있으며")
        elif rev_growth is not None and rev_growth < 0:
            bits.append("최근 구간 매출액 성장 둔화가 관찰되며")

    if len(ops) >= 2 and len(opms) >= 2:
        if opms[-1] > opms[0]:
            bits.append("영업이익률이 점진적으로 개선되는 흐름입니다")
        elif opms[-1] < opms[0] - 0.02:
            bits.append("영업이익률이 base 대비 하락한 구간으로 점검이 필요합니다")
        else:
            bits.append("영업이익률은 base 부근에서 유지되는 흐름입니다")

    body = ", ".join(bits) if bits else "최근 실적 흐름은 중립적으로 평가됩니다"

    if forward is None:
        suffix = "Forward 추정치가 확보되면 추정치 상향 동반 여부를 함께 점검할 필요가 있습니다."
    else:
        suffix = (
            "Forward 기간의 추정치 확보 여부와 추정치 상향 동반 여부가 multiple 정합성 판단의 핵심 변수입니다."
        )
    return f"{body}. {suffix}"


def latest_annual_summary(periods: list[dict[str, Any]]) -> dict[str, Any] | None:
    """최근 연간 기준 요약 (revenue / op / net / opm)."""
    historical = [p for p in periods if not p.get("is_forward")]
    if not historical:
        return None
    last = historical[-1]
    return {
        "period": last.get("period"),
        "revenue": last.get("revenue"),
        "operating_income": last.get("operating_income"),
        "net_income": last.get("net_income"),
        "operating_margin": last.get("operating_margin"),
    }
