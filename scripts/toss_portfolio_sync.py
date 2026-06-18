"""
토스증권 Open API portfolio sync — 3시간마다 cron.

OAuth 2.0 Client Credentials Grant 로 access_token 발급 후
잔고 / 보유종목 / 예수금 fetch → portfolio.json 자동 update.

환경변수 (GitHub Secrets):
  TOSS_CLIENT_ID         — 토스증권 API client_id
  TOSS_CLIENT_SECRET     — client_secret
  TOSS_ACCOUNT_NUMBER    — 사용자 계좌번호 (예: "12345678-01")
  TOSS_OAUTH_HOST        — (optional) 기본 https://openapi.tossinvest.com
  TOSS_API_HOST          — (optional) 기본 https://openapi.tossinvest.com

reference: https://developers.tossinvest.com/docs

기존 portfolio.json 의 hand-curated 필드 (memo, account, type, leverage, high_vol)
는 ticker 기준으로 *보존*. value/shares/cost/return 만 갱신.
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

OAUTH_HOST = os.environ.get("TOSS_OAUTH_HOST", "https://openapi.tossinvest.com")
API_HOST = os.environ.get("TOSS_API_HOST", "https://openapi.tossinvest.com")


# ─────────────────────────────────────────────────────────────
# OAuth (Client Credentials Grant)
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

    url = f"{OAUTH_HOST}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "accounts:read balance:read",
    }
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = payload.get("expires_in", 3600)

    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": int(time.time()) + expires_in,
        "issued_at": int(time.time()),
    }))
    return token


# ─────────────────────────────────────────────────────────────
# Toss API calls
# ─────────────────────────────────────────────────────────────

def _api(path: str, token: str, account: str | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if account:
        headers["X-Tossinvest-Account"] = account
    url = f"{API_HOST}{path}"
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"Toss API {path} → {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def fetch_account_summary(token: str, account: str) -> dict:
    """예수금 + 평가 총액."""
    return _api(f"/v1/accounts/{account}/balance", token, account)


def fetch_positions(token: str, account: str) -> list[dict]:
    """보유 종목 list."""
    payload = _api(f"/v1/accounts/{account}/positions", token, account)
    # 응답 schema 가 확정될 때까지 graceful:
    if isinstance(payload, dict):
        return payload.get("positions") or payload.get("data") or []
    return payload if isinstance(payload, list) else []


# ─────────────────────────────────────────────────────────────
# Portfolio merge (hand-curated 필드 보존)
# ─────────────────────────────────────────────────────────────

PRESERVE_FIELDS = ("yf_ticker", "type", "account", "memo", "leverage", "high_vol")


def _normalize_toss_position(pos: dict) -> dict:
    """토스 응답 → portfolio.json holding shape."""
    # 응답 schema 가 확정되면 mapping 정확화 필요. 현재는 best-effort.
    ticker = (
        pos.get("ticker")
        or pos.get("symbol")
        or pos.get("isin")
        or pos.get("stockCode")
        or "UNKNOWN"
    )
    name = pos.get("name") or pos.get("stockName") or ticker
    shares = float(pos.get("quantity") or pos.get("shares") or 0)
    value = float(pos.get("evaluationAmount") or pos.get("marketValue") or pos.get("value") or 0)
    cost = float(pos.get("purchaseAmount") or pos.get("cost") or pos.get("averageCost") or 0)
    if cost == 0 and shares > 0 and pos.get("avgPrice"):
        cost = float(pos["avgPrice"]) * shares
    pnl = value - cost if (value and cost) else 0
    ret = (pnl / cost * 100) if cost else 0
    currency = pos.get("currency", "KRW")
    # USD 자산은 환율 적용 별도 필요 (API 가 KRW 환산값 제공 시 그대로 사용)
    return {
        "ticker": ticker,
        "name": name,
        "shares": shares,
        "value_krw": int(round(value)),
        "cost_krw": int(round(cost)),
        "pnl_krw": int(round(pnl)),
        "return_pct": round(ret, 2),
        "currency": currency,
    }


def merge_holdings(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Ticker key 매칭. hand-curated 필드 보존, 가격/수량/PnL 만 update."""
    existing_by_ticker = {h["ticker"]: h for h in existing}
    merged = []
    for new in fresh:
        ticker = new["ticker"]
        old = existing_by_ticker.get(ticker, {})
        out = dict(old)  # preserve everything
        out.update({
            "ticker": ticker,
            "name": new["name"] or old.get("name", ticker),
            "shares": new["shares"],
            "value_krw": new["value_krw"],
            "cost_krw": new["cost_krw"],
            "pnl_krw": new["pnl_krw"],
            "return_pct": new["return_pct"],
        })
        # ensure preserve fields exist (don't blank them)
        for f in PRESERVE_FIELDS:
            if f not in out and f in old:
                out[f] = old[f]
        merged.append(out)
    # Add holdings that were in existing but not in fresh (stale — keep but flag)
    fresh_tickers = {h["ticker"] for h in merged}
    for old_ticker, old in existing_by_ticker.items():
        if old_ticker not in fresh_tickers:
            stale = dict(old)
            stale["_stale_since"] = datetime.now(KST).isoformat()
            merged.append(stale)
    # Recompute net_worth_pct
    total = sum(h.get("value_krw", 0) for h in merged if "_stale_since" not in h)
    for h in merged:
        if total > 0 and "_stale_since" not in h:
            h["net_worth_pct"] = round(h["value_krw"] / total * 100, 1)
    return merged


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def sync(dry_run: bool = False) -> dict:
    account = os.environ.get("TOSS_ACCOUNT_NUMBER")
    if not account:
        raise RuntimeError("TOSS_ACCOUNT_NUMBER 환경변수 누락")

    token = get_access_token()
    summary = fetch_account_summary(token, account)
    positions = fetch_positions(token, account)

    existing = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8")) if PORTFOLIO_PATH.exists() else {"holdings": []}
    fresh_holdings = [_normalize_toss_position(p) for p in positions]
    merged = merge_holdings(existing.get("holdings", []), fresh_holdings)

    total_value = sum(h.get("value_krw", 0) for h in merged if "_stale_since" not in h)
    out = {
        "as_of": datetime.now(KST).isoformat(),
        "base_currency": "KRW",
        "total_value_krw": total_value,
        "_sync_source": "toss_open_api",
        "_sync_summary": {
            "deposit_krw": summary.get("depositAmount") or summary.get("cashKrw"),
            "total_assets": summary.get("totalAssets"),
            "holdings_count": len([h for h in merged if "_stale_since" not in h]),
            "stale_count": len([h for h in merged if "_stale_since" in h]),
        },
        "note": existing.get("note", "자동 sync — 토스증권 Open API"),
        "holdings": merged,
    }

    if dry_run:
        print(json.dumps(out, indent=2, ensure_ascii=False)[:2000])
        return out

    PORTFOLIO_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Log
    log_entry = {
        "ts": datetime.now(KST).isoformat(),
        "ok": True,
        "total_value_krw": total_value,
        "holdings_count": len(merged),
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
        print(f"  ✓ synced — total ₩{result['total_value_krw']:,}, "
              f"{result['_sync_summary']['holdings_count']} holdings")
        return 0
    except Exception as e:
        print(f"  ✗ toss sync failed: {e}", file=sys.stderr)
        # Log failure
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
