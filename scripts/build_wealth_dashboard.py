"""대시보드 wealth_dashboard.json 빌더.

입력:
  - data/portfolio.json (보유 종목)
  - data/wealth_inputs.json (수동 입력: 부동산/부채/실현손익/월별스냅샷)

출력:
  - ../AlphaDashboard/data/wealth_dashboard.json (대시보드 fetch 대상)

워크플로:
  1. 토스 스크린샷 등으로 portfolio.json 갱신
  2. (필요 시) wealth_inputs.json 의 부동산/부채/실현손익 수정
  3. python scripts/build_wealth_dashboard.py 실행
  4. Alpha + Alpha_research 두 repo push → 대시보드 자동 반영

설계 원칙:
  - portfolio.json 은 Alpha 엔진과 공유 (단일 진실 소스)
  - wealth_inputs.json 은 대시보드 전용 정적 데이터 (부동산/부채 등 자주 안 바뀜)
  - wealth_dashboard.json 은 빌드 결과물 (직접 편집 금지 — 빌더로만 갱신)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT / "data" / "portfolio.json"
INPUTS_PATH = ROOT / "data" / "wealth_inputs.json"
# AlphaDashboard 는 부모 디렉토리에 있음 (Alpha/AlphaDashboard/)
OUT_PATH = ROOT.parent / "AlphaDashboard" / "data" / "wealth_dashboard.json"


# ---------------------------------------------------------------------------
# 카테고리 분류 룰
# ---------------------------------------------------------------------------

PENSION_TYPES = {"DC 예금", "DC 이율보증형 보험", "DC 정기예금", "현금성자산"}
CASH_TYPES = {"현금"}

# Holdings.type → SubClass (consolidated 테이블 표시용)
TYPE_TO_SUBCLASS = {
    "단일종목 레버리지 ETF": "레버리지 ETF",
    "지수 ETF": "지수 ETF",
    "섹터 레버리지 ETF": "레버리지 ETF (섹터)",
    "레버리지 ETF": "레버리지 ETF",
    "테마 ETF": "테마 ETF",
    "DC 예금": "DC 예금",
    "DC 이율보증형 보험": "DC 이율보증형 보험",
    "DC 정기예금": "DC 정기예금",
    "개별주": "개별주",
    "원자재 ETF": "원자재 ETF",
    "인컴 ETF": "인컴/배당 ETF",
    "배당 ETF": "배당주 ETF",
    "현금성자산": "현금성자산",
    "현금": "현금",
}

# Risk monitor 12 항목 — 정량 규칙 (interpretation/action 은 정적, current/status 는 동적)
RISK_RULES = [
    # (category, item, current_fn, threshold, interp_factory, action)
    ("부채", "마이너스통장 / 순자산",
     lambda ctx: ctx["debt"] / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.5,
     lambda v: f"순자산 대비 {v*100:.1f}% — 임계 {50}% 비교",
     "추가 차입 자제 / 일부 상환 검토"),
    ("부채", "마이너스통장 / 총자산",
     lambda ctx: ctx["debt"] / ctx["total_asset"] if ctx["total_asset"] else 0,
     0.4,
     lambda v: f"총자산 대비 약 {v*100:.1f}% — 안전 라인 임계 {40}%",
     "자산 매각 없이도 부채비율 관리 가능"),
    ("유동성", "현금성 자산 / 월 생활비(₩3M)",
     lambda ctx: ctx["cash"] / 3_000_000,
     1.0,
     lambda v: f"현금이 약 ₩{ctx_cash_str(v)} — 월 생활비의 {v:.1f}배",
     "비상자금 우선 ₩5M (1.5~2개월) 라인 마련"),
    ("유동성", "현금성 자산 / 투자자산",
     lambda ctx: ctx["cash"] / ctx["investment"] if ctx["investment"] else 0,
     0.05,
     lambda v: f"약 {v*100:.2f}% — 시장 급락 시 추가매수 여력",
     "분할매도 / 신규 입금으로 5% 라인 회복"),
    ("암호자산", "비트코인 / 순자산",
     lambda ctx: ctx["btc"] / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.2,
     lambda v: f"순자산 대비 {v*100:.1f}% — 사이클 변동성",
     "비중 모니터링 / 임계 초과 시 분할 차익실현"),
    ("단일종목", "Top 1 단일 종목 / 순자산",
     lambda ctx: ctx["top1_value"] / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.15,
     lambda v: f"Top 1 ({ctx_top1_name(v)}) {v*100:.1f}% — 단일 종목 위험",
     "분할매도 임계 정의 (예: +15% 추가 시 1/3 매도)"),
    ("레버리지", "레버리지 ETF / 순자산",
     lambda ctx: ctx["leverage_total"] / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.1,
     lambda v: f"순자산 대비 {v*100:.1f}% — 임계 {10}%",
     "추가 매수 시 임계 라인 모니터링"),
    ("위험자산", "고위험자산 / 순자산",
     lambda ctx: (ctx["leverage_total"] + ctx["btc"]) / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.5,
     lambda v: f"고위험(레버리지+BTC) {v*100:.1f}% — 임계 50%",
     "분할매도로 임계 라인 목표"),
    ("위험자산", "주식+ETF+BTC / 순자산",
     lambda ctx: (ctx["stocks_etf"] + ctx["btc"]) / ctx["net_worth"] if ctx["net_worth"] else 0,
     0.7,
     lambda v: f"위험자산 {v*100:.1f}% — 변동성 노출도",
     "현금/예금 비중 회복으로 70% 이하 권장"),
    ("집중도", "반도체 익스포저 / 투자자산",
     lambda ctx: ctx["semi_exposure"] / ctx["investment"] if ctx["investment"] else 0,
     0.5,
     lambda v: f"반도체 테마 {v*100:.1f}% — 섹터 집중도",
     "하이닉스 ₩2.5M 도달 시 단계적 수익실현"),
    ("환노출", "USD 자산 / 총자산",
     lambda ctx: ctx["usd_assets"] / ctx["total_asset"] if ctx["total_asset"] else 0,
     0.3,
     lambda v: f"USD 자산 {v*100:.1f}% — 환율 -10% 시 순자산 약 {-v*10:.1f}% 영향",
     "원화 강세 위험 모니터링"),
    ("해외자산", "해외 자산(USD+BTC) / 총자산",
     lambda ctx: (ctx["usd_assets"] + ctx["btc"]) / ctx["total_asset"] if ctx["total_asset"] else 0,
     0.3,
     lambda v: f"해외 익스포저 {v*100:.1f}% — 글로벌 매크로 영향",
     "글로벌 매크로 헤지 도구(원화 자산) 검토"),
]


def ctx_cash_str(ratio: float) -> str:
    """ratio = cash / 3M, return readable cash amount."""
    cash_amount = ratio * 3_000_000
    return f"{cash_amount:,.0f}"


_TOP1_NAME_HOLDER = {"name": "?"}


def ctx_top1_name(_v: float) -> str:
    return _TOP1_NAME_HOLDER["name"]


def status_from_ratio(current: float, threshold: float, lower_is_safer: bool = True) -> str:
    """current vs threshold → 정상/주의/위험."""
    if lower_is_safer:
        if current < threshold * 0.7:
            return "정상"
        if current < threshold:
            return "주의"
        return "위험"
    else:  # 높을수록 안전 (예: 유동성)
        if current >= threshold:
            return "정상"
        if current >= threshold * 0.5:
            return "주의"
        return "위험"


# ---------------------------------------------------------------------------
# 빌드
# ---------------------------------------------------------------------------

def classify_subclass(h: dict) -> str:
    name = h.get("name", "")
    typ = h.get("type", "")
    if "S&P500" in name:
        return "S&P500 ETF (국내상장)"
    if "나스닥100" in name:
        return "NASDAQ100 ETF (국내상장)" if "TIGER" in name else "NASDAQ100 ETF (해외)"
    if "하이닉스" in name:
        return "단일종목 레버리지 ETF (SK하이닉스 2X)"
    if "반도체TOP10" in name:
        return "레버리지 ETF (반도체 2X)"
    if "AI반도체" in name:
        return "테마 ETF (AI 반도체)"
    if "피지컬AI" in name:
        return "테마 ETF (피지컬 AI)"
    if "TSLL" in h.get("ticker", ""):
        return "레버리지 ETF (TSLA 2X)"
    if "NFXL" in h.get("ticker", ""):
        return "레버리지 ETF (NFLX 2X)"
    if h.get("ticker") == "QLD":
        return "레버리지 ETF (NASDAQ 2X)"
    if h.get("ticker") == "TQQQ":
        return "레버리지 ETF (NASDAQ 3X)"
    if h.get("ticker") == "SOXL":
        return "레버리지 ETF (반도체 3X)"
    if h.get("ticker") == "QQQ":
        return "NASDAQ100 ETF (해외)"
    if h.get("ticker") in ("SCHD",):
        return "배당주 ETF"
    if h.get("ticker") in ("JEPQ",):
        return "인컴/배당 ETF"
    if h.get("ticker") in ("GLD",):
        return "원자재 ETF (금)"
    if h.get("ticker") in ("SLV",):
        return "원자재 ETF (은)"
    if h.get("ticker") in ("NASA",):
        return "테마 ETF (우주/항공)"
    if typ == "개별주":
        return "개별주"
    return TYPE_TO_SUBCLASS.get(typ, typ or "기타")


def is_semi_exposure(h: dict) -> bool:
    name = h.get("name", "")
    return any(kw in name for kw in ["하이닉스", "반도체", "Semi", "AI반도체", "SOXL"])


def is_usd_asset(h: dict) -> bool:
    """yf_ticker가 .KS suffix 없거나, account 가 '토스 해외' 면 USD 자산."""
    yf = h.get("yf_ticker") or ""
    account = h.get("account", "")
    if "토스 해외" in account:
        return True
    if yf and not yf.endswith(".KS"):
        return True
    return False


def build_holdings_consolidated(holdings: list[dict], total_asset: float, net_worth: float, investment: float) -> list[dict]:
    """portfolio.json holdings → dashboard consolidated 형식 변환."""
    result = []
    for h in holdings:
        value = h.get("value_krw", 0)
        cost = h.get("cost_krw", 0)
        pnl = h.get("pnl_krw", 0)
        result.append({
            "name": h.get("name", ""),
            "ticker": h.get("ticker", ""),
            "assetClass": "주식/ETF" if h.get("type") not in PENSION_TYPES and h.get("type") not in CASH_TYPES else (
                "현금성" if h.get("type") in CASH_TYPES else "퇴직연금"),
            "subClass": classify_subclass(h),
            "accountCount": 1,
            "value": value,
            "cost": cost,
            "pnl": pnl,
            "ret": (h.get("return_pct", 0) / 100.0) if h.get("return_pct") is not None else 0,
            "totalAssetPct": value / total_asset if total_asset else 0,
            "netWorthPct": value / net_worth if net_worth else 0,
            "investmentPct": value / investment if investment else 0,
            "highVol": "Y" if h.get("high_vol") else "N",
            "leverage": "Y" if h.get("leverage") else "N",
            "concentration": "Y" if value >= 5_000_000 else "N",
            "memo": h.get("memo") or h.get("account") or "",
        })
    # Value desc 정렬
    result.sort(key=lambda r: -r["value"])
    return result


def build_top5(consolidated: list[dict], net_worth: float) -> list[dict]:
    top5 = []
    for r in consolidated[:5]:
        pct = r["value"] / net_worth if net_worth else 0
        status = "과도" if pct >= 0.3 else ("주의" if pct >= 0.15 else "정상")
        top5.append({
            "name": r["name"],
            "ticker": r["ticker"],
            "value": r["value"],
            "pct": pct,
            "status": status,
        })
    return top5


def build_asset_group(stocks_etf: float, pension: float, cash: float, real_estate: float, deposit: float, btc: float, total_asset: float) -> list[dict]:
    items = [
        ("현금", cash),
        ("부동산", real_estate),
        ("보증금", deposit),
        ("주식/ETF", stocks_etf + pension),  # 통합 표시
        ("암호자산", btc),
        ("퇴직연금/예금", 0),  # 위 주식/ETF 에 합산했으므로 0
    ]
    return [{"name": n, "value": v, "pct": (v / total_asset if total_asset else 0)} for n, v in items]


def build_inv_group(holdings: list[dict], investment: float) -> list[dict]:
    """투자 자산 카테고리별 그룹."""
    groups: dict[str, float] = {}
    for h in holdings:
        name = h.get("name", "")
        ticker = h.get("ticker", "")
        value = h.get("value_krw", 0)
        # 카테고리 분류
        if "S&P500" in name:
            key = "국내 S&P500 ETF (TIGER)"
        elif "하이닉스" in name or "반도체TOP10" in name or "AI반도체" in name:
            key = "반도체 ETF (하이닉스 2X + 반도체 + AI)"
        elif ticker == "SOXL":
            key = "반도체 ETF (하이닉스 2X + 반도체 + AI)"
        elif "나스닥100" in name or ticker in ("QQQ", "QLD", "TQQQ"):
            key = "NASDAQ 계열 (QQQ/나스닥100 + QLD/TQQQ)"
        elif ticker in ("TSLL", "NFXL"):
            key = "단일종목 레버리지 ETF (TSLL/NFXL 등)"
        elif h.get("type") == "개별주":
            key = "개별주 (해외)"
        elif "피지컬AI" in name:
            key = "테마 ETF (피지컬 AI)"
        elif h.get("type") in PENSION_TYPES:
            key = "퇴직연금 예금/보험/현금성"
        elif h.get("type") == "현금":
            key = "현금성 (Toss)"
        else:
            key = "기타 (인컴/배당/원자재/액티브)"
        groups[key] = groups.get(key, 0) + value
    result = [{"name": k, "value": v, "pct": v / investment if investment else 0} for k, v in groups.items()]
    result.sort(key=lambda r: -r["value"])
    return result


def build_risk_monitor(ctx: dict) -> list[dict]:
    monitor = []
    for cat, item, current_fn, threshold, interp_factory, action in RISK_RULES:
        try:
            current = current_fn(ctx)
        except Exception:
            current = 0
        lower_safer = True
        if cat == "유동성":
            lower_safer = False
        status = status_from_ratio(current, threshold, lower_is_safer=lower_safer)
        monitor.append({
            "category": cat,
            "item": item,
            "current": current,
            "threshold": threshold,
            "status": status,
            "interpretation": interp_factory(current),
            "action": action,
        })
    return monitor


def main() -> None:
    portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    inputs = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))

    holdings = portfolio["holdings"]

    # 분류
    pension_total = sum(h["value_krw"] for h in holdings if h.get("type") in PENSION_TYPES)
    toss_cash = sum(h["value_krw"] for h in holdings if h.get("type") in CASH_TYPES)
    investment = sum(h["value_krw"] for h in holdings)
    stocks_etf = investment - pension_total - toss_cash

    # Balance sheet
    bs = inputs["balance_sheet"]
    real_estate = bs["real_estate_krw"]
    deposit = bs["deposit_krw"]
    cash_outside = bs["cash_outside_krw"]
    btc = bs["btc_krw"]
    debt = bs["debt_krw"]

    cash_total = cash_outside + toss_cash

    total_asset = stocks_etf + pension_total + cash_total + real_estate + deposit + btc
    net_worth = total_asset - debt

    # Leverage / 위험자산
    leverage_total = sum(h["value_krw"] for h in holdings if h.get("leverage"))
    semi_exposure = sum(h["value_krw"] for h in holdings if is_semi_exposure(h))
    usd_assets = sum(h["value_krw"] for h in holdings if is_usd_asset(h))

    # Top 1
    sorted_h = sorted(holdings, key=lambda h: -h["value_krw"])
    top1_value = sorted_h[0]["value_krw"] if sorted_h else 0
    _TOP1_NAME_HOLDER["name"] = sorted_h[0]["name"] if sorted_h else "?"

    # Risk context
    ctx = {
        "debt": debt, "net_worth": net_worth, "total_asset": total_asset,
        "cash": cash_total, "investment": investment, "btc": btc,
        "top1_value": top1_value, "leverage_total": leverage_total,
        "stocks_etf": stocks_etf, "semi_exposure": semi_exposure,
        "usd_assets": usd_assets,
    }

    # KPI
    kpi = {
        "총자산": total_asset, "순자산": net_worth, "총부채": debt,
        "투자자산": investment, "부채비율": debt / total_asset if total_asset else 0,
    }
    sub_kpi = {
        "현금성 자산": cash_total,
        "부동산+보증금": real_estate + deposit,
        "비트코인": btc,
        "주식/ETF (통합)": stocks_etf,
        "퇴직연금/예금": pension_total,
    }
    risk_ratios = {
        "부채/순자산": debt / net_worth if net_worth else 0,
        "위험자산/순자산": (leverage_total + btc) / net_worth if net_worth else 0,
        "레버리지/순자산": leverage_total / net_worth if net_worth else 0,
        "CRDU/순자산": 0,
        "비트코인/순자산": btc / net_worth if net_worth else 0,
    }

    # Holdings 변환
    consolidated = build_holdings_consolidated(holdings, total_asset, net_worth, investment)
    top5 = build_top5(consolidated, net_worth)
    asset_group = build_asset_group(stocks_etf, pension_total, cash_total, real_estate, deposit, btc, total_asset)
    inv_group = build_inv_group(holdings, investment)
    risk_monitor = build_risk_monitor(ctx)

    # 실현손익 / 월별 / 캐시플로 (정적 입력)
    realized_kpi = inputs.get("realized_pnl", {}).get("kpi", {})
    realized_monthly = inputs.get("realized_pnl", {}).get("monthly", [])
    realized_recent = inputs.get("realized_pnl", {}).get("recent_ledger", [])
    monthly_all = inputs.get("monthly_snapshots", [])
    cashflow_kpi = inputs.get("cashflow", {})

    # 월별 bridge 변환: monthly_bridges (dict) → bridges (per-month array list)
    monthly_bridges_raw = inputs.get("monthly_bridges", {})
    bridges: dict[str, list[dict]] = {}
    for month_key, mb in monthly_bridges_raw.items():
        if month_key.startswith("_"):
            continue
        bridge_arr: list[dict] = [
            {"name": mb.get("opening_label", "기초 순자산"), "value": mb["opening_nw"]},
        ]
        bridge_arr.extend(mb.get("flows", []))
        bridge_arr.append({"name": "기말 순자산", "value": mb["ending_nw"]})
        bridges[month_key] = bridge_arr
    # Legacy single-bridge: latest month
    latest_month = max(bridges.keys()) if bridges else None
    bridge = bridges.get(latest_month, []) if latest_month else []

    # 출력
    snapshot = {
        "asof": portfolio.get("as_of", datetime.now().strftime("%Y-%m-%d")),
        "kpi": kpi,
        "subKpi": sub_kpi,
        "riskRatios": risk_ratios,
        "assetGroup": asset_group,
        "invGroup": inv_group,
        "consolidated": consolidated,
        "top5Concentration": top5,
        "riskMonitor": risk_monitor,
        "pnl": [c for c in consolidated if c["value"] > 0],  # legacy field
        "monthlySnap": monthly_all[-1:] if monthly_all else [],
        "realizedKpi": realized_kpi,
        "realizedMonthly": realized_monthly,
        "realizedRecent": realized_recent,
        "cashflowKpi": cashflow_kpi,
        "bridge": bridge,           # latest month (legacy)
        "bridges": bridges,         # 월별 navigation 용
        "monthlyAll": monthly_all,
        "_meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "builder": "scripts/build_wealth_dashboard.py",
            "warning": "이 파일은 빌더로만 생성됩니다 — 직접 편집 금지",
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {OUT_PATH}")
    print(f"  총자산: ₩{total_asset:,.0f}  순자산: ₩{net_worth:,.0f}  부채비율: {debt/total_asset*100:.1f}%")
    print(f"  투자자산: ₩{investment:,.0f}  레버리지: ₩{leverage_total:,.0f}  반도체: ₩{semi_exposure:,.0f}")


if __name__ == "__main__":
    main()
