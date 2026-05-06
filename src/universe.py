"""Universe 로딩/관리.

universe.csv를 읽어 ticker, name_ko, name_en, theme, category dict 리스트로 반환한다.
"""
from __future__ import annotations

import csv
import json
from typing import Any

from .utils import (
    CORE_UNIVERSE_CSV,
    UNIVERSE_CSV,
    WATCHLIST_JSON,
    WIDE_UNIVERSE_CSV,
    ensure_data_dir,
    get_logger,
)

log = get_logger("universe")


# 테마별 가중치(테마 적합도 점수에 사용)
THEME_WEIGHTS: dict[str, float] = {
    "ai_semiconductor": 1.00,
    "ai_networking": 1.00,
    "data_center_power": 0.95,
    "public_safety": 0.95,
    "defense": 0.90,
    "space": 0.85,
    "healthcare_infra": 0.85,
    "platform": 0.80,
    "ecommerce_platform": 0.80,
    "travel_mobility": 0.70,
    "mobility_consumer": 0.70,
    "consumer_brand": 0.55,
}

# 테마 한국어 라벨
THEME_LABEL_KO: dict[str, str] = {
    "ai_semiconductor": "AI 반도체",
    "ai_networking": "AI 네트워킹/인프라",
    "data_center_power": "데이터센터 전력",
    "public_safety": "공공안전 플랫폼",
    "defense": "방산",
    "space": "우주",
    "healthcare_infra": "헬스케어 인프라",
    "platform": "프리미엄 플랫폼",
    "ecommerce_platform": "이커머스 플랫폼",
    "travel_mobility": "여행/모빌리티",
    "mobility_consumer": "모빌리티/소비",
    "consumer_brand": "컨슈머 브랜드",
}

CATEGORY_LABEL_KO: dict[str, str] = {
    "AI Infrastructure": "AI 인프라",
    "Energy Security": "에너지 안보",
    "Public Safety": "공공안전",
    "Defense": "방산",
    "Space": "우주",
    "Healthcare Infrastructure": "헬스케어 인프라",
    "Quality Platform": "프리미엄 플랫폼",
    "Consumer Mobility": "소비/모빌리티",
    "Consumer Brand": "컨슈머 브랜드",
}


def load_universe() -> list[dict[str, Any]]:
    """Core watchlist (Tier 3 deep dive 대상) 로드.

    우선순위:
        1) data/core_universe.csv
        2) data/universe.csv (legacy)
    """
    target = CORE_UNIVERSE_CSV if CORE_UNIVERSE_CSV.exists() else UNIVERSE_CSV
    if not target.exists():
        log.warning("core/legacy universe.csv 둘 다 없습니다: %s", target)
        return []

    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r.get("ticker"):
                continue
            ticker = r["ticker"].strip().upper()
            rows.append(
                {
                    "ticker": ticker,
                    "name_ko": (r.get("name_ko") or "").strip(),
                    "name_en": (r.get("name_en") or "").strip(),
                    "theme": (r.get("theme") or "").strip(),
                    "category": (r.get("category") or "").strip(),
                }
            )
    return rows


def get_universe_map() -> dict[str, dict[str, Any]]:
    return {row["ticker"]: row for row in load_universe()}


def load_wide_universe() -> list[dict[str, Any]]:
    """Discovery 대상 wide universe 로드.

    필터:
        - is_active == 1
        - is_etf == 0
        - is_spac == 0
    """
    if not WIDE_UNIVERSE_CSV.exists():
        log.warning("wide_universe.csv 가 없습니다 — Discovery 단계가 비활성화됩니다")
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with WIDE_UNIVERSE_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ticker = (r.get("ticker") or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            if (r.get("is_active") or "1").strip() == "0":
                continue
            if (r.get("is_etf") or "0").strip() == "1":
                continue
            if (r.get("is_spac") or "0").strip() == "1":
                continue
            seen.add(ticker)
            rows.append(
                {
                    "ticker": ticker,
                    "name": (r.get("name") or "").strip(),
                    "sector": (r.get("sector") or "").strip(),
                    "industry": (r.get("industry") or "").strip(),
                    "market_cap_tier": (r.get("market_cap_tier") or "").strip(),
                    "exchange": (r.get("exchange") or "").strip(),
                    "is_adr": (r.get("is_adr") or "0").strip() == "1",
                }
            )
    return rows


def theme_weight(theme: str) -> float:
    return THEME_WEIGHTS.get(theme, 0.6)


def theme_label_ko(theme: str) -> str:
    return THEME_LABEL_KO.get(theme, theme or "기타")


def category_label_ko(category: str) -> str:
    return CATEGORY_LABEL_KO.get(category, category or "기타")


# ---------------------------------------------------------------------------
# 사용자 관심종목 (watchlist) - 별도 json 파일에 저장
# ---------------------------------------------------------------------------

def _get_pat() -> str | None:
    """GitHub PAT 가져오기 — 환경변수 우선, Streamlit secrets fallback."""
    import os
    pat = os.environ.get("GITHUB_PAT")
    if pat:
        return pat
    try:
        import streamlit as _st
        return _st.secrets.get("GITHUB_PAT")  # type: ignore
    except Exception:
        return None


def _get_repo_meta() -> tuple[str, str]:
    """GitHub repo owner / name — secrets 또는 default."""
    import os
    owner = os.environ.get("GITHUB_REPO_OWNER")
    name = os.environ.get("GITHUB_REPO_NAME")
    if not owner or not name:
        try:
            import streamlit as _st
            owner = owner or _st.secrets.get("GITHUB_REPO_OWNER")  # type: ignore
            name = name or _st.secrets.get("GITHUB_REPO_NAME")  # type: ignore
        except Exception:
            pass
    return owner or "hyeongwoohyeong", name or "Alpha"


def _commit_watchlist_to_github(tickers: list[str]) -> tuple[bool, str]:
    """Streamlit Cloud → GitHub Contents API 자동 commit (영구 보존).

    requests 라이브러리 사용 — urllib 의 SSL 인증서 이슈 회피.
    PAT 미설정 시 silently skip. 실패해도 local file 저장은 그대로 진행됨.
    """
    import base64

    pat = _get_pat()
    if not pat:
        return False, "no_pat"

    # requests 라이브러리 시도
    try:
        import requests
    except ImportError:
        log.warning("requests 라이브러리 import 실패 — urllib fallback 시도")
        return _commit_watchlist_via_urllib(tickers, pat)

    owner, repo = _get_repo_meta()
    file_path = "data/watchlist.json"
    content_str = json.dumps(
        sorted(set(t.upper() for t in tickers)), ensure_ascii=False, indent=2,
    )
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Alpha-Engine-Watchlist-Sync",
    }

    # 1) 기존 파일의 sha 조회 (update 시 필수)
    sha: str | None = None
    try:
        r = requests.get(api_url, headers=headers, timeout=15)
        if r.status_code == 200:
            try:
                sha = r.json().get("sha")
            except Exception as e:
                log.warning("GitHub API sha JSON parse 실패: %s — body: %s", e, r.text[:200])
                return False, "sha_json_parse_error"
        elif r.status_code == 404:
            # 파일 없음 → create 로 진행
            sha = None
        elif r.status_code == 401:
            log.warning("GitHub API 401 Unauthorized — PAT 유효성 점검 필요")
            return False, "auth_401_invalid_pat"
        elif r.status_code == 403:
            log.warning("GitHub API 403 Forbidden — PAT 권한 부족 (Contents: read+write 필요)")
            return False, "auth_403_no_permission"
        else:
            log.warning("GitHub API sha fetch HTTP %d: %s", r.status_code, r.text[:200])
            return False, f"sha_http_{r.status_code}"
    except requests.exceptions.SSLError as e:
        log.warning("GitHub API SSL 에러: %s", e)
        return False, f"ssl_error: {str(e)[:80]}"
    except requests.exceptions.ConnectionError as e:
        log.warning("GitHub API 연결 실패: %s", e)
        return False, "connection_error"
    except requests.exceptions.Timeout:
        log.warning("GitHub API timeout (sha fetch)")
        return False, "timeout"
    except Exception as e:
        log.warning("GitHub API sha fetch 예외: %s — %s", type(e).__name__, e)
        return False, f"sha_exception: {type(e).__name__}"

    # 2) PUT — create or update
    body: dict[str, Any] = {
        "message": f"chore: update watchlist ({len(tickers)} tickers)",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    try:
        r = requests.put(
            api_url,
            json=body,
            headers={**headers, "Content-Type": "application/json"},
            timeout=20,
        )
        if r.status_code in (200, 201):
            return True, "ok"
        if r.status_code == 401:
            return False, "put_auth_401"
        if r.status_code == 403:
            return False, "put_auth_403_permission"
        if r.status_code == 422:
            log.warning("GitHub API 422: %s", r.text[:300])
            return False, "put_422_invalid_request"
        log.warning("GitHub API PUT HTTP %d: %s", r.status_code, r.text[:300])
        return False, f"put_http_{r.status_code}"
    except requests.exceptions.SSLError as e:
        log.warning("GitHub API PUT SSL 에러: %s", e)
        return False, f"put_ssl_error: {str(e)[:80]}"
    except Exception as e:
        log.warning("GitHub API PUT 예외: %s — %s", type(e).__name__, e)
        return False, f"put_exception: {type(e).__name__}"


def _commit_watchlist_via_urllib(tickers: list[str], pat: str) -> tuple[bool, str]:
    """requests 가 없을 때 urllib fallback — SSL context 명시 지정."""
    import base64
    import ssl
    import urllib.error
    import urllib.request

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()

    owner, repo = _get_repo_meta()
    file_path = "data/watchlist.json"
    content_str = json.dumps(
        sorted(set(t.upper() for t in tickers)), ensure_ascii=False, indent=2,
    )
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Alpha-Engine-Watchlist-Sync",
    }

    sha: str | None = None
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
            sha = json.loads(resp.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return False, f"urllib_sha_http_{e.code}"
    except Exception as e:
        return False, f"urllib_sha_exception: {type(e).__name__}"

    body: dict[str, Any] = {
        "message": f"chore: update watchlist ({len(tickers)} tickers)",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as resp:
            ok = resp.status in (200, 201)
            return ok, "ok" if ok else f"urllib_put_status_{resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"urllib_put_http_{e.code}"
    except Exception as e:
        return False, f"urllib_put_exception: {type(e).__name__}"


def load_watchlist() -> list[str]:
    """관심종목 리스트 로드 — local file 우선.

    Streamlit Cloud 가 컨테이너 재시작 시 git 에서 받은 watchlist.json 으로 초기화되므로,
    save 시 GitHub commit 한 변경사항은 다음 컨테이너 부팅 시에도 보존됨.
    """
    ensure_data_dir()
    if not WATCHLIST_JSON.exists():
        return []
    try:
        return list(json.loads(WATCHLIST_JSON.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning("watchlist 로드 실패: %s", e)
        return []


def save_watchlist(tickers: list[str]) -> dict[str, Any]:
    """관심종목 저장 — local file + GitHub Contents API 자동 commit.

    Returns: {"local": bool, "github": bool, "github_status": str, "tickers": list[str]}
    """
    ensure_data_dir()
    sorted_tickers = sorted(set(t.upper() for t in tickers))
    # 1) Local file (즉시 UI 반영)
    local_ok = True
    try:
        WATCHLIST_JSON.write_text(
            json.dumps(sorted_tickers, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("watchlist local save 실패: %s", e)
        local_ok = False

    # 2) GitHub commit (영구 보존)
    gh_ok, gh_status = _commit_watchlist_to_github(sorted_tickers)
    if gh_ok:
        log.info("watchlist GitHub commit OK (%d tickers)", len(sorted_tickers))
    elif gh_status == "no_pat":
        log.info("watchlist GitHub commit skip — GITHUB_PAT 미설정 (local-only)")
    else:
        log.warning("watchlist GitHub commit 실패: %s", gh_status)

    return {
        "local": local_ok, "github": gh_ok, "github_status": gh_status,
        "tickers": sorted_tickers,
    }


def add_to_watchlist(ticker: str) -> dict[str, Any]:
    wl = set(load_watchlist())
    wl.add(ticker.upper())
    return save_watchlist(sorted(wl))


def remove_from_watchlist(ticker: str) -> dict[str, Any]:
    wl = set(load_watchlist())
    wl.discard(ticker.upper())
    return save_watchlist(sorted(wl))
