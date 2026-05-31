"""Valuation 자동 검증 — 발굴 종목 과대평가 필터.

원칙:
  - +100% 가능 hyper-growth 종목은 *어느 정도 valuation premium* 정상 (PER 30~80x)
  - 단 *극단 과대평가* (PER 200+ or PSR 50+) 는 risk 너무 큼
  - 분기 단위 EPS 가속 시 PER 빠르게 정상화 → high PER 자체가 deal breaker X
  - 하지만 *cliff edge* (PSR 50+ / 적자 + PSR 30+) 는 제외

이번 빌드 — *과대평가 적색 신호만* 필터링:
  - PSR 50+ (적자) OR 100+ (흑자): 극단
  - PER 200+ (흑자) : 극단
  - 그 외: 통과 (hyper-growth 정상)
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("valuation_filter")

# 극단 과대평가 임계값 (filter out)
EXTREME_PSR_PROFITABLE = 100.0    # 흑자 종목 PSR 100+
EXTREME_PSR_LOSS = 50.0           # 적자 종목 PSR 50+
EXTREME_PER = 200.0               # PER 200+

# Hyper-growth 정상 zone (caution but OK)
HYPER_PSR_PROFITABLE = 30.0
HYPER_PER = 80.0


def fetch_valuation(ticker: str) -> dict[str, Any]:
    """yfinance fast_info / info 로 valuation metric fetch."""
    out: dict[str, Any] = {
        "ticker": ticker, "available": False,
        "per": None, "psr": None, "pbr": None,
        "is_profitable": None,
    }
    try:
        import yfinance as yf
        t = ticker.strip().upper()
        if t.isdigit() and len(t) == 6:
            t = t + ".KS"
        tk = yf.Ticker(t)
        info = tk.info if hasattr(tk, "info") else {}
        if not info:
            return out

        # PER (trailing)
        per = info.get("trailingPE") or info.get("forwardPE")
        # PSR
        psr = info.get("priceToSalesTrailing12Months")
        # PBR
        pbr = info.get("priceToBook")
        # 흑자 여부
        net_income = info.get("netIncomeToCommon") or info.get("netIncome")
        is_profitable = (net_income or 0) > 0

        out.update({
            "available": True,
            "per": float(per) if per else None,
            "psr": float(psr) if psr else None,
            "pbr": float(pbr) if pbr else None,
            "is_profitable": is_profitable,
        })
    except Exception as e:
        log.debug("valuation fetch %s 실패: %s", ticker, e)
    return out


def evaluate_valuation_risk(val: dict) -> dict[str, Any]:
    """Valuation 데이터 → risk level + 통과 여부."""
    out = {
        "risk_level": "unknown",
        "passes_filter": True,
        "warnings": [],
    }
    if not val.get("available"):
        out["risk_level"] = "no_data"
        return out

    per = val.get("per")
    psr = val.get("psr")
    is_prof = val.get("is_profitable")

    extreme = False
    hyper = False

    if psr is not None:
        threshold = EXTREME_PSR_PROFITABLE if is_prof else EXTREME_PSR_LOSS
        if psr >= threshold:
            extreme = True
            out["warnings"].append(
                f"PSR {psr:.1f}x 극단 (임계 {threshold:.0f}x, "
                f"{'흑자' if is_prof else '적자'})"
            )
        elif psr >= HYPER_PSR_PROFITABLE:
            hyper = True
            out["warnings"].append(f"PSR {psr:.1f}x hyper-growth zone")

    if per is not None and is_prof:
        if per >= EXTREME_PER:
            extreme = True
            out["warnings"].append(f"PER {per:.0f}x 극단 (임계 {EXTREME_PER:.0f}x)")
        elif per >= HYPER_PER:
            hyper = True
            out["warnings"].append(f"PER {per:.0f}x hyper zone")

    if extreme:
        out["risk_level"] = "extreme"
        out["passes_filter"] = False  # 극단 과대평가 — 제외
    elif hyper:
        out["risk_level"] = "hyper"
        out["passes_filter"] = True   # 정상 hyper zone
    else:
        out["risk_level"] = "normal"
        out["passes_filter"] = True

    return out


def filter_extreme_valuation(tickers: list[str]) -> list[dict]:
    """ticker list → 극단 과대평가 제외한 list 반환.

    Returns: [{ticker, val_data, risk_eval}, ...]
    """
    out = []
    for ticker in tickers:
        val = fetch_valuation(ticker)
        risk = evaluate_valuation_risk(val)
        out.append({
            "ticker": ticker,
            "valuation": val,
            "risk": risk,
        })
    return out


if __name__ == "__main__":
    import sys, json
    ticker = sys.argv[1] if len(sys.argv) > 1 else "PLTR"
    val = fetch_valuation(ticker)
    risk = evaluate_valuation_risk(val)
    print(json.dumps({"valuation": val, "risk": risk}, indent=2, ensure_ascii=False))
