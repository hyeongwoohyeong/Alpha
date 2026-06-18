"""
토스증권 Open API portfolio sync — 3시간마다 cron.

Reference: https://openapi.tossinvest.com/openapi-docs/overview.md

환경변수 (GitHub Secrets):
  TOSS_CLIENT_ID         — API key (tsck_live_...)
  TOSS_CLIENT_SECRET     — Secret key (tssk_live_...)
  TOSS_ACCOUNT_SEQ       — accountSeq (보통 "1" — 계좌번호 X, 시퀀스)
                           legacy TOSS_ACCOUNT_NUMBER 도 호환

API endpoints (확정):
  POST /oauth2/token          — Client Credentials Grant (scope 파라미터 X)
  GET  /api/v1/accounts       — 계좌 list (Bearer only)
  GET  /api/v1/holdings       — 보유 주식 (+ X-Tossinvest-Account 헤더)
  GET  /api/v1/exchange-rate  — KRW↔USD 환율

기존 portfolio.json 의 hand-curated 필드 (memo / account / type / leverage / high_vol)
는 symbol 기준 *보존*. 가격/수량/PnL 만 update. 토스 holdings 에 없는 holding (퇴직연금 등)
은 _stale_since 표시 후 *유지*.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = REPO_ROOT / "data" / "portfolio.json"
TOKEN_CACHE = REPO_ROOT / "data" / ".toss_token_cache.json"
SYNC_LOG = REPO_ROOT / "data" / "toss_sync_log.json"

KST = timezone(timedelta(hours=9))

BASE = os.environ.get("TOSS_API_HOST", "https://openapi.tossinvest.com")


# ─────────────────────────────────────────────────────────────
# Symbol normalization (토스 symbol → 기존 portfolio.json ticker)
# ─────────────────────────────────────────────────────────────

# 토스가 주는 symbol vs 우리가 portfolio.json 에 쓰던 ticker
# (e.g. 토스 "0193T0" = KODEX SK하이닉스단일종목레버리지 = 우리 "KODEX_HYNIX_2X")
SYMBOL_ALIASES: dict[str, str] = {
    "0193T0": "KODEX_HYNIX_2X",
    "0167A0": "SOL_AI_SEMI",
    "488080": "TIGER_SEMI_2X",
    "0190C0": "RISE_HD_PHYSAI",
    # US tickers 는 그대로 (QQQ, QLD, TQQQ, SOXL, SCHD, JEPQ, NASA, GLD, SLV, TSLL, NFLX, NFXL, RL, MCD, TDG, SN ...)
    # KR ETF 도 6자리 코드 그대로 (360750, 133690 ...)
}


def normalize_symbol(symbol: str) -> str:
    return SYMBOL_ALIASES.get(symbol, symbol)


# ─────────────────────────────────────────────────────────────
# OAuth (Client Credentials Grant) — scope 파라미터 X
# ─────────────────────────────────────────────────────────────

def get_access_token() -> str:
    """Token cache 활용 (만료 5분 전 갱신)."""
    if TOKEN_CACHE.exists():
        try:
            cached = json.loads(TOKEN_CACHE.read_text())
            exp = cached.get("expires_at", 0)
            if time.time() < (exp - 300):
                return cached["access_token"]
        except Exception:
            pass

    client_id = os.environ.get("TOSS_CLIENT_ID")
    client_secret = os.environ.get("TOSS_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise RuntimeError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수 누락")

    resp = requests.post(
        f"{BASE}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OAuth fail {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))

    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": int(time.time()) + expires_in,
        "issued_at": int(time.time()),
    }))
    return token


# ─────────────────────────────────────────────────────────────
# Toss API calls
# ─────────────────────────────────────────────────────────────

def _api(path: str, token: str, account_seq: str | int | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)
    resp = requests.get(f"{BASE}{path}", headers=headers, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"Toss {path} → {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def get_account_seq(token: str) -> int:
    """env 에 없으면 /api/v1/accounts 로 자동 탐지 (첫 BROKERAGE 계좌)."""
    env_seq = os.environ.get("TOSS_ACCOUNT_SEQ") or os.environ.get("TOSS_ACCOUNT_NUMBER")
    if env_seq and env_seq.isdigit():
        return int(env_seq)
    accts = _api("/api/v1/accounts", token).get("result", [])
    for a in accts:
        if a.get("accountType") == "BROKERAGE":
            return int(a["accountSeq"])
    if accts:
        return int(accts[0]["accountSeq"])
    raise RuntimeError("계좌 X — /api/v1/accounts 결과 비어있음")


def fetch_holdings(token: str, account_seq: int) -> dict:
    return _api("/api/v1/holdings", token, account_seq)


def fetch_exchange_rate(token: str) -> float:
    """USD→KRW. 실패 시 fallback 1540 (2026-06 실제 환율 — 토스 마이데이터 USD ₩37.8M / $24,570 = ₩1,540)."""
    try:
        data = _api("/api/v1/exchange-rate", token).get("result", {})
        # docs: { "usdToKrw": "1540.0" } 등 — 정확한 key 는 응답 보고 추후 fix
        rate = (
            data.get("usdToKrw")
            or data.get("USD_KRW")
            or data.get("rate")
            or 1540
        )
        return float(rate)
    except Exception:
        return 1540.0


# ─────────────────────────────────────────────────────────────
# Holdings parse → portfolio.json item shape
# ─────────────────────────────────────────────────────────────

def _normalize_item(item: dict, usd_to_krw: float) -> dict:
    symbol_raw = item.get("symbol", "")
    ticker = normalize_symbol(symbol_raw)
    name = item.get("name", symbol_raw)
    quantity = float(item.get("quantity") or 0)
    last_price = float(item.get("lastPrice") or 0)
    avg_price = float(item.get("averagePurchasePrice") or 0)
    currency = item.get("currency", "KRW")
    mv = item.get("marketValue") or {}
    pl = item.get("profitLoss") or {}
    purchase_amount = float(mv.get("purchaseAmount") or 0)
    amount = float(mv.get("amount") or 0)
    pl_amount = float(pl.get("amount") or 0)
    pl_rate = float(pl.get("rate") or 0)

    # Convert to KRW
    if currency == "USD":
        value_krw = int(round(amount * usd_to_krw))
        cost_krw = int(round(purchase_amount * usd_to_krw))
        pnl_krw = int(round(pl_amount * usd_to_krw))
    else:
        value_krw = int(round(amount))
        cost_krw = int(round(purchase_amount))
        pnl_krw = int(round(pl_amount))

    return {
        "ticker": ticker,
        "name": name,
        "yf_ticker": symbol_raw if currency == "USD" else None,  # US ticker 그대로 yfinance 호환
        "shares": quantity,
        "last_price": last_price,
        "avg_price": avg_price,
        "value_krw": value_krw,
        "cost_krw": cost_krw,
        "pnl_krw": pnl_krw,
        "return_pct": round(pl_rate * 100, 2),
        "currency": currency,
        "toss_symbol": symbol_raw,
    }


# ─────────────────────────────────────────────────────────────
# Merge with existing portfolio.json (hand-curated 필드 보존)
# ─────────────────────────────────────────────────────────────

PRESERVE_FIELDS = ("yf_ticker", "type", "account", "memo", "leverage", "high_vol")


def merge_holdings(existing: list[dict], fresh: list[dict]) -> list[dict]:
    existing_by_ticker = {h["ticker"]: h for h in existing}
    merged = []
    fresh_tickers = set()

    for new in fresh:
        ticker = new["ticker"]
        fresh_tickers.add(ticker)
        old = existing_by_ticker.get(ticker, {})
        out = dict(old)
        out.update({
            "ticker": ticker,
            "name": new["name"] or old.get("name", ticker),
            "shares": new["shares"],
            "value_krw": new["value_krw"],
            "cost_krw": new["cost_krw"],
            "pnl_krw": new["pnl_krw"],
            "return_pct": new["return_pct"],
        })
        # Don't blank preserved fields if exist in old
        for f in PRESERVE_FIELDS:
            if f not in out and f in old:
                out[f] = old[f]
        # Remove stale flag if previously marked
        out.pop("_stale_since", None)
        merged.append(out)

    # Keep stale holdings (퇴직연금 etc. — 토스 API 가 안 가져옴)
    for old_ticker, old in existing_by_ticker.items():
        if old_ticker not in fresh_tickers:
            stale = dict(old)
            stale["_stale_since"] = stale.get("_stale_since") or datetime.now(KST).isoformat()
            merged.append(stale)

    # Recompute net_worth_pct
    total = sum(h.get("value_krw", 0) for h in merged)
    if total > 0:
        for h in merged:
            h["net_worth_pct"] = round(h.get("value_krw", 0) / total * 100, 1)
    return merged


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def sync(dry_run: bool = False) -> dict:
    token = get_access_token()
    account_seq = get_account_seq(token)
    usd_to_krw = fetch_exchange_rate(token)
    raw = fetch_holdings(token, account_seq).get("result", {})

    items = raw.get("items") or []
    fresh = [_normalize_item(item, usd_to_krw) for item in items]

    # Summary from API (KRW + USD already separated)
    mv = raw.get("marketValue") or {}
    pl_summary = raw.get("profitLoss") or {}
    krw_amount = float((mv.get("amount") or {}).get("krw") or 0)
    usd_amount = float((mv.get("amount") or {}).get("usd") or 0)
    toss_total_krw = int(round(krw_amount + usd_amount * usd_to_krw))

    existing = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8")) if PORTFOLIO_PATH.exists() else {"holdings": []}
    merged = merge_holdings(existing.get("holdings", []), fresh)
    total_value = sum(h.get("value_krw", 0) for h in merged)

    out = {
        "as_of": datetime.now(KST).isoformat(),
        "base_currency": "KRW",
        "total_value_krw": total_value,
        "_sync_source": "toss_open_api",
        "_sync_summary": {
            "account_seq": account_seq,
            "usd_to_krw": usd_to_krw,
            "toss_krw_value": int(krw_amount),
            "toss_usd_value": usd_amount,
            "toss_total_krw_only": toss_total_krw,
            "toss_pl_rate": float(pl_summary.get("rate") or 0),
            "holdings_count": len([h for h in merged if "_stale_since" not in h]),
            "stale_count": len([h for h in merged if "_stale_since" in h]),
        },
        "note": existing.get("note", "토스 Open API 자동 sync. 퇴직연금 + TIGER S&P500 등 토스 API 외 자산은 _stale_since 표시 후 유지."),
        "holdings": sorted(merged, key=lambda h: h.get("value_krw", 0), reverse=True),
    }
    # Preserve manual fields from existing portfolio.json (milestones, manual updates)
    for field in ("_milestone", "_last_manual_update_at", "_phase"):
        if field in existing:
            out[field] = existing[field]

    if dry_run:
        # 압축 출력
        summary = dict(out["_sync_summary"])
        print(json.dumps({
            "as_of": out["as_of"],
            "total_value_krw": out["total_value_krw"],
            "_sync_summary": summary,
            "holdings": [
                {
                    "ticker": h["ticker"],
                    "name": h.get("name"),
                    "shares": h.get("shares"),
                    "value_krw": h["value_krw"],
                    "return_pct": h.get("return_pct"),
                    "_stale_since": h.get("_stale_since"),
                }
                for h in out["holdings"]
            ],
        }, indent=2, ensure_ascii=False))
        return out

    PORTFOLIO_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Log
    log_entry = {
        "ts": datetime.now(KST).isoformat(),
        "ok": True,
        "total_value_krw": total_value,
        "holdings_count": out["_sync_summary"]["holdings_count"],
    }
    logs = []
    if SYNC_LOG.exists():
        try:
            logs = json.loads(SYNC_LOG.read_text())
        except Exception:
            logs = []
    logs.append(log_entry)
    SYNC_LOG.write_text(json.dumps(logs[-100:], indent=2, ensure_ascii=False))

    return out


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    try:
        result = sync(dry_run=dry_run)
        print(
            f"  ✓ synced — total ₩{result['total_value_krw']:,}, "
            f"{result['_sync_summary']['holdings_count']} live + "
            f"{result['_sync_summary']['stale_count']} stale"
        )
        return 0
    except Exception as e:
        print(f"  ✗ toss sync failed: {e}", file=sys.stderr)
        try:
            logs = json.loads(SYNC_LOG.read_text()) if SYNC_LOG.exists() else []
            logs.append({
                "ts": datetime.now(KST).isoformat(),
                "ok": False,
                "error": str(e)[:300],
            })
            SYNC_LOG.write_text(json.dumps(logs[-100:], indent=2, ensure_ascii=False))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
