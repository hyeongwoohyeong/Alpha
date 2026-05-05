"""Auto-Curation Engine — LLM 기반 자동 종목 큐레이션 생성.

설계 원칙 (사용자 요구 — 2026-05-03):
    1. 사용자가 curated.py 에 종목별 데이터를 직접 작성하지 않아도 작동.
    2. SEC EDGAR 10-K 1차 자료 + yfinance + 최근 뉴스 → LLM 합성.
    3. 결과는 60일 캐시 후 재생성 (변경 트리거: 신규 10-K 공시, anti-thesis 충돌 뉴스).
    4. curated.py 의 수동 입력은 항상 우선 (Manual Override).
    5. EDGAR 실패 시 yfinance + 뉴스만으로 fallback (가용성 우선).

산출 12 필드:
    easy_explanation       — "이 회사는 쉽게 말해" 2~3 문장
    core_thesis            — 핵심 투자 논리 한 단락
    thesis_pillars         — 투자 논리 3 항목
    core_kpis              — 점검할 KPI 6 항목
    key_risks              — 주요 리스크 5 항목
    anti_thesis            — 반대 논리 5 항목
    earnings_quality       — 8 차원 (rating + comment)
    moat_map               — 7 차원 (rating)
    alpha_judgment         — 종합 한 단락 (~250자)
    data_confidence        — High | Medium | Low (LLM self-report)
    uncertainty_flags      — LLM 이 자신없는 부분 self-report
    sec_filing_date        — 사용한 10-K 의 filing date

비용:
    GPT-4o-mini 기준 종목당 약 $0.007 — 캐시 60일 적용 시 월 $5 미만.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from . import database as db
from .utils import get_logger, safe_float

log = get_logger("auto_curation")


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT",
    "Alpha Engine Research alpha-engine@example.com",
)
EDGAR_TIMEOUT = 20  # SEC 권장 — 무거운 10-K 본문은 시간 좀 걸림
NEWS_CONTEXT_LIMIT = 10  # 최근 뉴스 N건 LLM 에 전달
MAX_SECTION_CHARS = 30000  # 10-K 한 섹션 최대 char (token 폭주 방지)


# OpenAI 가격 (per 1M tokens)
PRICE_INPUT_PER_M = 0.15   # gpt-4o-mini
PRICE_OUTPUT_PER_M = 0.60


# ---------------------------------------------------------------------------
# Step 1 — SEC EDGAR fetch
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: int = EDGAR_TIMEOUT) -> str:
    """SEC EDGAR 호환 HTTP GET. UA 필수."""
    req = urllib.request.Request(
        url, headers={"User-Agent": EDGAR_USER_AGENT, "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # gzip 처리
        encoding = r.headers.get("Content-Encoding", "")
        raw = r.read()
        if encoding == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


_CIK_CACHE: dict[str, str] = {}


def get_cik(ticker: str) -> str | None:
    """ticker → 10-digit CIK 변환 (in-memory cache)."""
    ticker = ticker.upper()
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]
    try:
        body = _http_get("https://www.sec.gov/files/company_tickers.json")
        data = json.loads(body)
        for _, entry in data.items():
            if entry.get("ticker") == ticker:
                cik = str(entry["cik_str"]).zfill(10)
                _CIK_CACHE[ticker] = cik
                return cik
    except Exception as e:
        log.warning("[%s] CIK fetch 실패: %s", ticker, e)
    return None


def get_latest_10k_meta(cik: str) -> dict[str, Any] | None:
    """CIK 의 최신 10-K filing 메타데이터 반환."""
    try:
        body = _http_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        sub = json.loads(body)
    except Exception as e:
        log.warning("CIK %s submissions fetch 실패: %s", cik, e)
        return None

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form == "10-K":
            return {
                "filing_date": dates[i],
                "accession": accessions[i],
                "primary_doc": primary_docs[i],
                "company_name": sub.get("name"),
            }
    return None


def fetch_10k_html(cik: str, accession: str, primary_doc: str) -> str | None:
    """10-K 의 primary HTML 본문 fetch."""
    acc_no_dashes = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary_doc}"
    try:
        return _http_get(url, timeout=EDGAR_TIMEOUT * 2)  # 큰 파일 — timeout ↑
    except Exception as e:
        log.warning("10-K HTML fetch 실패 (%s): %s", url, e)
        return None


def _strip_html(html: str) -> str:
    """간단한 HTML → text 변환 (BeautifulSoup 없이도 작동)."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        return BeautifulSoup(html, "html.parser").get_text(separator="\n")
    except ImportError:
        # fallback — regex 로 태그 제거
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        return text


def extract_10k_sections(html: str) -> dict[str, str]:
    """10-K 본문에서 Item 1 / Item 1A / Item 7 만 추출.

    회사마다 마크업이 달라 100% 보장은 어려움 — 추출 실패 시 빈 string.
    """
    text = _strip_html(html)
    # 공백 정규화 (Item 1 / Item 1A 사이 공백 변동 흡수)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    sections: dict[str, str] = {"item_1": "", "item_1a": "", "item_7": ""}

    # 패턴 — "Item 1." 또는 "ITEM 1" 또는 "Item 1\\b" 다양
    # 대문자 / 소문자 / 점 / 공백 변동 모두 흡수
    patterns = {
        "item_1": (
            r"(?im)^\s*item\s*1\.?\s+business\b",
            r"(?im)^\s*item\s*1a\.?\s+risk",  # 다음 섹션 = 종료
        ),
        "item_1a": (
            r"(?im)^\s*item\s*1a\.?\s+risk\s*factors\b",
            r"(?im)^\s*item\s*1b\.?",  # 또는 Item 2
        ),
        "item_7": (
            r"(?im)^\s*item\s*7\.?\s+management",
            r"(?im)^\s*item\s*7a\.?",
        ),
    }

    for key, (start_pat, end_pat) in patterns.items():
        try:
            # 각 섹션은 보통 2번 이상 등장 (목차 + 본문) — 두 번째 매치를 본문으로 가정
            starts = list(re.finditer(start_pat, text))
            if len(starts) < 2:
                if not starts:
                    continue
                start_idx = starts[0].start()
            else:
                start_idx = starts[1].start()

            # end 패턴은 start 이후에서 첫 매치
            end_match = re.search(end_pat, text[start_idx + 50:])
            if end_match:
                end_idx = start_idx + 50 + end_match.start()
            else:
                end_idx = start_idx + MAX_SECTION_CHARS

            section_text = text[start_idx:end_idx].strip()
            sections[key] = section_text[:MAX_SECTION_CHARS]
        except Exception as e:
            log.debug("[10-K] %s 추출 실패: %s", key, e)

    return sections


def fetch_sec_10k_sections(ticker: str) -> dict[str, Any] | None:
    """ticker 의 최신 10-K Item 1 / 1A / 7 추출.

    Returns: {"item_1": str, "item_1a": str, "item_7": str,
              "filing_date": str, "company_name": str} or None.
    """
    cik = get_cik(ticker)
    if not cik:
        return None
    meta = get_latest_10k_meta(cik)
    if not meta:
        return None
    html = fetch_10k_html(cik, meta["accession"], meta["primary_doc"])
    if not html:
        return None
    sections = extract_10k_sections(html)
    sections["filing_date"] = meta["filing_date"]
    sections["company_name"] = meta.get("company_name", "")
    return sections


# ---------------------------------------------------------------------------
# Step 2 — yfinance business summary fetch
# ---------------------------------------------------------------------------

def fetch_yfinance_summary(ticker: str) -> dict[str, Any]:
    """yfinance 의 business summary + 핵심 메타. 실패 시 빈 dict."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        log.warning("[%s] yfinance info fetch 실패: %s", ticker, e)
        return {}
    return {
        "long_business_summary": (info.get("longBusinessSummary") or "")[:3000],
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "country": info.get("country") or "",
        "website": info.get("website") or "",
        "full_time_employees": info.get("fullTimeEmployees"),
        "market_cap": info.get("marketCap"),
    }


# ---------------------------------------------------------------------------
# Step 3 — 최근 뉴스 컨텍스트 수집
# ---------------------------------------------------------------------------

def fetch_recent_news_context(
    conn, ticker: str, limit: int = NEWS_CONTEXT_LIMIT
) -> list[dict[str, Any]]:
    """DB 에서 ticker 의 최근 뉴스 + 한국어 요약 N건."""
    try:
        cur = conn.execute(
            """
            SELECT title, source, published_at, detailed_summary_ko,
                   investment_implication_ko, thesis_impact_ko
            FROM news_raw
            WHERE ticker=? AND published_at IS NOT NULL
            ORDER BY published_at DESC LIMIT ?
            """,
            (ticker, limit),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            # title 이 빈 경우 skip
            if not d.get("title"):
                continue
            rows.append(d)
        return rows
    except Exception as e:
        log.warning("[%s] news context fetch 실패: %s", ticker, e)
        return []


# ---------------------------------------------------------------------------
# Step 4 — LLM 프롬프트 빌드
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an experienced equity analyst writing in Korean for a Korean retail
investor (intermediate level, fluent in English tickers). Your job is to
analyze a US-listed company using ONLY the source materials provided
(10-K excerpts, business summary, recent news). Do NOT invent specific
financial figures or quotes — if a fact is uncertain, mark it with
"확인 필요" instead.

Output strictly valid JSON matching the schema. All Korean text must be
natural, fluent Korean — not translated-from-English style.

Tone: 신중한 애널리스트 — confidently bullish 도 아니고 reflexively bearish 도
아닌 균형 잡힌 톤. 사용자가 모르는 alpha 를 발굴하는 게 목적이며,
"좋은 회사" 와 "좋은 주식" 을 분리해서 평가한다.

8 EQ 차원 등급은 다음 중 하나로만: Strong | Medium~Strong | Medium | Weak~Medium | Weak
7 Moat 차원 등급은 다음 중 하나로만: Strong | Medium~Strong | Medium | Weak~Medium | Weak
data_confidence: High | Medium | Low

Prose 필드 (core_debate / valuation_context / financial_context / final_view /
price_interpretation) 는 모두 한국어 자연스러운 문장. 길이는:
    - core_debate: 1~2 문장 (~80자) — "이 종목 매수 판단이 무엇으로 갈리는가"
    - valuation_context: 한 단락 (~200자) — PE / EV/EBITDA 등 가치평가 해석
    - financial_context: 한 단락 (~200자) — 매출 성장 / OPM / FCF 등 재무 추세
    - final_view: 한 단락 (~250자) — 종합 판단 (alpha_judgment 와 다름; 더 실행 지향)
    - price_interpretation: 1~2 문장 (~120자) — 최근 1~5년 장기 주가 흐름 해석
"""


# JSON Schema for structured output
CURATION_SCHEMA = {
    "type": "object",
    "properties": {
        "easy_explanation": {"type": "string", "description": "2~3 문장 한국어"},
        "core_thesis": {"type": "string", "description": "한 단락 (~150자)"},
        "thesis_pillars": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3, "maxItems": 3,
        },
        "core_kpis": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 5, "maxItems": 6,
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 4, "maxItems": 5,
        },
        "anti_thesis": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 4, "maxItems": 5,
        },
        "earnings_quality": {
            "type": "object",
            "properties": {
                k: {
                    "type": "object",
                    "properties": {
                        "rating": {"type": "string"},
                        "comment": {"type": "string"},
                    },
                    "required": ["rating", "comment"],
                    "additionalProperties": False,
                }
                for k in (
                    "customer_diversification", "recurring_revenue", "lock_in",
                    "pricing_power", "margin_quality", "cash_conversion",
                    "capital_intensity", "incremental_roic",
                )
            },
            "required": [
                "customer_diversification", "recurring_revenue", "lock_in",
                "pricing_power", "margin_quality", "cash_conversion",
                "capital_intensity", "incremental_roic",
            ],
            "additionalProperties": False,
        },
        "moat_map": {
            "type": "object",
            "properties": {
                k: {"type": "string"} for k in (
                    "network_effect", "switching_cost", "scale_advantage",
                    "brand", "data_advantage", "regulatory_barrier", "cost_advantage",
                )
            },
            "required": [
                "network_effect", "switching_cost", "scale_advantage",
                "brand", "data_advantage", "regulatory_barrier", "cost_advantage",
            ],
            "additionalProperties": False,
        },
        "alpha_judgment": {"type": "string", "description": "한 단락 종합 판단 (~250자)"},
        "core_debate": {"type": "string", "description": "1~2 문장 — 매수 판단이 무엇으로 갈리는가"},
        "valuation_context": {"type": "string", "description": "한 단락 — PE / EV/EBITDA 등 가치평가 해석"},
        "financial_context": {"type": "string", "description": "한 단락 — 매출 / OPM / FCF 추세"},
        "final_view": {"type": "string", "description": "한 단락 — 종합 실행 판단 (alpha_judgment 와 다른 각도)"},
        "price_interpretation": {"type": "string", "description": "1~2 문장 — 장기 주가 흐름 해석"},
        "data_confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "uncertainty_flags": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
    },
    "required": [
        "easy_explanation", "core_thesis", "thesis_pillars", "core_kpis",
        "key_risks", "anti_thesis", "earnings_quality", "moat_map",
        "alpha_judgment", "core_debate", "valuation_context", "financial_context",
        "final_view", "price_interpretation",
        "data_confidence", "uncertainty_flags",
    ],
    "additionalProperties": False,
}


def build_user_prompt(
    ticker: str,
    yf_summary: dict[str, Any],
    sec_sections: dict[str, Any] | None,
    news: list[dict[str, Any]],
    market_cap: float | None = None,
) -> str:
    """LLM user prompt 조립."""
    parts: list[str] = []

    # META
    company_name = (sec_sections or {}).get("company_name") or ""
    sector = yf_summary.get("sector", "")
    industry = yf_summary.get("industry", "")
    mcap = safe_float(market_cap or yf_summary.get("market_cap"))
    mcap_str = f"${mcap/1e9:.1f}B" if mcap else "N/A"

    parts.append(f"[META]")
    parts.append(f"Ticker: {ticker}")
    parts.append(f"Name: {company_name}")
    parts.append(f"Sector: {sector} / Industry: {industry}")
    parts.append(f"Market Cap: {mcap_str}")
    parts.append("")

    # SEC 10-K (있을 때만)
    if sec_sections:
        filing_date = sec_sections.get("filing_date", "")
        if sec_sections.get("item_1"):
            parts.append(f"[10-K Item 1 — Business] (filed {filing_date})")
            parts.append(sec_sections["item_1"][:MAX_SECTION_CHARS])
            parts.append("")
        if sec_sections.get("item_1a"):
            parts.append("[10-K Item 1A — Risk Factors]")
            parts.append(sec_sections["item_1a"][:MAX_SECTION_CHARS // 2])  # risk 는 절반만
            parts.append("")
        if sec_sections.get("item_7"):
            parts.append("[10-K Item 7 — MD&A]")
            parts.append(sec_sections["item_7"][:MAX_SECTION_CHARS // 2])
            parts.append("")
    else:
        parts.append("[NOTE] 10-K 자료 fetch 실패 — yfinance + 뉴스만으로 분석.")
        parts.append("")

    # yfinance summary
    if yf_summary.get("long_business_summary"):
        parts.append("[Business Summary (yfinance)]")
        parts.append(yf_summary["long_business_summary"])
        parts.append("")

    # 뉴스 (최근 한국어 요약된 것 위주)
    if news:
        parts.append("[Recent News — Korean summaries]")
        for i, n in enumerate(news, 1):
            title = (n.get("title") or "").strip()
            date = (n.get("published_at") or "")[:10]
            ko = n.get("detailed_summary_ko") or n.get("investment_implication_ko") or ""
            parts.append(f"{i}. ({date}) {title}")
            if ko:
                parts.append(f"   요약: {ko[:300]}")
        parts.append("")

    parts.append(
        "[Required Output] — 위 자료만 근거로 JSON 으로 답하세요. 추측 / 외부 지식 금지. "
        "구체 재무 수치는 위 자료에 명시된 것만 사용. "
        "uncertainty_flags 는 자신없는 부분 1~3 건 자기 신고."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Step 5 — OpenAI 호출
# ---------------------------------------------------------------------------

def call_openai_for_curation(
    user_prompt: str, *, model: str = "gpt-4o-mini",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """OpenAI structured output 호출.

    Returns: (parsed_json or None, meta dict with token usage).
    """
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY 미설정 — auto_curation 호출 skip")
        return None, {"error": "no_api_key"}

    try:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "stock_curation",
                    "schema": CURATION_SCHEMA,
                    "strict": True,
                },
            },
            temperature=0.3,
        )
        text = resp.choices[0].message.content
        usage = resp.usage
        meta = {
            "model": model,
            "token_input": int(getattr(usage, "prompt_tokens", 0) or 0),
            "token_output": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        meta["cost_estimate_usd"] = round(
            meta["token_input"] / 1_000_000 * PRICE_INPUT_PER_M
            + meta["token_output"] / 1_000_000 * PRICE_OUTPUT_PER_M,
            5,
        )
        try:
            parsed = json.loads(text)
            return parsed, meta
        except json.JSONDecodeError as e:
            log.warning("OpenAI JSON parse 실패: %s\n%s", e, text[:500])
            return None, meta
    except Exception as e:
        log.warning("OpenAI 호출 실패: %s", e)
        return None, {"error": str(e)}


# ---------------------------------------------------------------------------
# Step 6 — 통합 entry point
# ---------------------------------------------------------------------------

def generate_auto_curation(
    conn,
    ticker: str,
    *,
    market_cap: float | None = None,
    force: bool = False,
    max_age_days: int = 60,
) -> dict[str, Any] | None:
    """ticker 에 대한 자동 큐레이션 생성 (캐시 hit 시 skip).

    Args:
        force: True 면 캐시 무시하고 재생성.
        max_age_days: 캐시 fresh 판정 (60일 default).

    Returns: fields dict (12 항목) 또는 None (실패).
    """
    ticker = ticker.upper()

    # 캐시 hit ?
    if not force and db.auto_curation_is_fresh(conn, ticker, max_age_days=max_age_days):
        log.info("[%s] auto_curation cache hit (fresh)", ticker)
        cached = db.fetch_auto_curation(conn, ticker)
        if cached:
            return json.loads(cached["fields_json"])

    log.info("[%s] auto_curation 신규 생성 시작", ticker)

    # 1. yfinance summary
    yf_summary = fetch_yfinance_summary(ticker)

    # 2. SEC 10-K (실패 시 fallback)
    sec_sections = None
    try:
        sec_sections = fetch_sec_10k_sections(ticker)
        if sec_sections:
            log.info("[%s] 10-K fetched: filed %s", ticker, sec_sections.get("filing_date"))
    except Exception as e:
        log.warning("[%s] EDGAR fetch 실패 → fallback: %s", ticker, e)

    # 3. 최근 뉴스
    news = fetch_recent_news_context(conn, ticker)

    # 4. 최소 자료 검증 — yfinance summary 가 없고 sec 도 없고 뉴스도 없으면 포기
    if not yf_summary.get("long_business_summary") and not sec_sections and not news:
        log.warning("[%s] 모든 자료 부재 — auto_curation skip", ticker)
        return None

    # 5. prompt 조립
    prompt = build_user_prompt(
        ticker, yf_summary, sec_sections, news, market_cap=market_cap,
    )
    prompt_chars = len(prompt)
    log.info("[%s] prompt %d chars (~%d tokens)", ticker, prompt_chars, prompt_chars // 4)

    # 6. LLM 호출
    parsed, meta = call_openai_for_curation(prompt)
    if not parsed:
        log.warning("[%s] LLM 호출 실패 — skip", ticker)
        return None

    # 7. 저장
    sources = {
        "yfinance": bool(yf_summary.get("long_business_summary")),
        "sec_10k": bool(sec_sections and sec_sections.get("item_1")),
        "news_count": len(news),
    }
    sec_filing_date = (sec_sections or {}).get("filing_date")

    try:
        db.upsert_auto_curation(
            conn,
            ticker=ticker,
            fields=parsed,
            model_used=meta.get("model", "gpt-4o-mini"),
            token_input=meta.get("token_input", 0),
            token_output=meta.get("token_output", 0),
            cost_estimate_usd=meta.get("cost_estimate_usd", 0.0),
            sources=sources,
            sec_filing_date=sec_filing_date,
            data_confidence=parsed.get("data_confidence", "Medium"),
            uncertainty_flags=parsed.get("uncertainty_flags") or [],
        )
        conn.commit()
        log.info("[%s] auto_curation saved — cost ~$%.4f, %d input / %d output tokens",
                 ticker, meta.get("cost_estimate_usd", 0),
                 meta.get("token_input", 0), meta.get("token_output", 0))
    except Exception as e:
        log.warning("[%s] auto_curation save 실패: %s", ticker, e)

    return parsed


# ---------------------------------------------------------------------------
# 캐시 lookup helper — UI / lookup 함수에서 사용
# ---------------------------------------------------------------------------

def get_cached_field(conn, ticker: str, field_key: str) -> Any:
    """auto_curation 의 한 필드 값 조회 (없으면 None)."""
    row = db.fetch_auto_curation(conn, ticker)
    if not row:
        return None
    try:
        fields = json.loads(row["fields_json"])
        return fields.get(field_key)
    except Exception:
        return None


def get_cached_fields(conn, ticker: str) -> dict[str, Any] | None:
    """auto_curation 의 전체 fields dict 조회."""
    row = db.fetch_auto_curation(conn, ticker)
    if not row:
        return None
    try:
        return json.loads(row["fields_json"])
    except Exception:
        return None
