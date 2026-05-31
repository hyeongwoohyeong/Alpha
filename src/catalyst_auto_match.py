"""Catalyst 자동 매칭 — 종목 정보 → catalyst tag 자동 분류.

Manual mapping 제거. Dynamic universe 의 *발견 안 된* 종목도 자동 tagging.

Match sources (우선순위):
  1. 회사 영문/한글명 keyword
  2. Industry / sector
  3. yfinance Ticker.info 의 longBusinessSummary (사업 영역 textual)

Returns: catalyst tag (str) or None
"""
from __future__ import annotations

import re
from typing import Any

from .utils import get_logger
from .catalyst_tags import CATALYSTS, ACTIVE_CATALYSTS

log = get_logger("catalyst_auto_match")

# Keyword 패턴 — catalyst 별. 매칭 우선순위 순서.
# 더 specific 한 것 (예: "HBM bonding") 이 generic ("semi") 보다 먼저.
_KEYWORD_MAP: list[tuple[str, list[str]]] = [
    # AI 하이퍼스케일 (specific catalyst 매칭 우선)
    ("ai_hyperscale", [
        "palantir", "AIP", "데이터 플랫폼", "AI platform",
        "AppLovin", "광고 AI", "ad tech AI", "AXON",
    ]),
    ("ai_software", [
        "AI software", "machine learning platform", "generative AI",
        "인공지능 소프트웨어", "MLOps",
    ]),
    ("ai_drug_discovery", [
        "AI drug", "AI 신약", "drug discovery AI", "Recursion",
    ]),
    ("ai_infra", [
        "AI infrastructure", "AI server", "AI 인프라", "AI PCB",
        "high-end PCB", "data center cooling", "liquid cooling",
        "Nebius", "Argan",
    ]),
    # HBM·AI 반도체 — 매우 specific
    ("hbm_ai_semi", [
        "HBM", "bonding machine", "본드머신",
        "반도체 패키징", "advanced packaging", "TC bonding",
        "반도체 후공정", "ASIC", "HBM PCB",
    ]),
    # 양자
    ("quantum_computing", [
        "quantum computing", "양자 컴퓨팅", "qubit",
        "IonQ", "Rigetti", "D-Wave", "quantum processor",
    ]),
    ("quantum_security", [
        "quantum encryption", "양자 암호", "QKD",
        "post-quantum", "Arqit",
    ]),
    # 의료
    ("obesity_drug", [
        "GLP-1", "obesity", "비만 치료", "tirzepatide", "semaglutide",
        "Ozempic", "Wegovy", "Mounjaro", "long-acting GLP",
        "펩트론", "일동제약",
    ]),
    ("adc_oncology", [
        "ADC", "antibody-drug conjugate", "항체 약물 접합체",
        "이중항체", "bispecific", "Daiichi Sankyo", "Enhertu",
        "알테오젠", "리가켐바이오", "ImmunoGen",
    ]),
    ("biosimilar", [
        "biosimilar", "바이오시밀러", "Remsima", "Truxima",
        "셀트리온",
    ]),
    # 산업
    ("shipbuilding", [
        "조선", "shipbuilding", "marine engine", "LNG carrier",
        "FPSO", "선박", "고부가가치 선박",
    ]),
    ("defense", [
        "defense", "방산", "무기 수출", "K2 전차", "K9 자주포",
        "missile", "유도 무기", "Lockheed", "RTX",
        "한화에어로", "LIG넥스원", "한화시스템",
    ]),
    ("robotics", [
        "로보틱스", "robotics", "industrial robot",
        "협동 로봇", "두산로보틱스", "humanoid robot",
        "Boston Dynamics",
    ]),
    ("ev_battery", [
        "EV battery", "2차전지", "lithium-ion",
        "양극재", "cathode", "음극재", "anode",
        "전해질", "electrolyte", "배터리 셀",
        "에코프로", "엘앤에프", "LG에너지", "SK이노",
    ]),
    ("energy_epc", [
        "EPC", "원전 EPC", "nuclear EPC", "건설 플랜트",
        "Saudi Aramco", "해외 건설", "삼성E&A", "현대건설",
    ]),
    # 원자력
    ("nuclear_power", [
        "nuclear power", "원자력 발전", "SMR", "small modular reactor",
        "Vistra", "Constellation", "Talen", "Oklo", "NuScale",
        "한수원", "데이터센터 전력",
    ]),
    # 크립토
    ("btc_treasury", [
        "BTC treasury", "bitcoin treasury", "비트코인 보유",
        "MicroStrategy", "Saylor", "BTC reserves",
    ]),
    ("btc_mining", [
        "BTC mining", "bitcoin mining", "비트코인 채굴",
        "CleanSpark", "Riot", "Marathon", "Bitfarms",
        "hash rate", "마이닝",
    ]),
    # 소비
    ("k_food", [
        "K-food", "한식 글로벌", "K-푸드",
        "라면 수출", "삼양식품", "농심 글로벌",
    ]),
    ("k_culture", [
        "K-pop", "K-팝", "엔터테인먼트",
        "하이브", "JYP", "SM", "YG",
    ]),
    ("k_beauty_med", [
        "K-beauty", "K-뷰티", "botox", "보톡스",
        "필러", "rejuran", "리쥬란", "에스테틱",
        "휴젤", "파마리서치", "한국콜마",
    ]),
    # 우주
    ("space", [
        "space launch", "위성", "satellite",
        "Rocket Lab", "AST SpaceMobile", "SpaceX",
        "우주 발사체",
    ]),
    # 반도체 소재·부품
    ("semi_material", [
        "semiconductor material", "반도체 소재",
        "photoresist", "포토레지스트", "wet chemical",
        "솔브레인", "동진쎄미켐", "test socket", "프로브 카드",
        "ISC", "리노공업",
    ]),
    # 게임
    ("gaming_crypto", [
        "blockchain game", "play to earn", "P2E",
        "위메이드", "WEMIX",
    ]),
    ("gaming", [
        "mobile game", "MMORPG", "엔씨", "넥슨",
        "카카오게임즈", "Krafton",
    ]),
]

# Fix syntax (None entry above)
_KEYWORD_MAP = [(c, kws) for c, kws in _KEYWORD_MAP if c is not None and isinstance(kws, list)]
# fintech_crypto 명시 재추가
_KEYWORD_MAP.append(("fintech_crypto", [
    "crypto exchange", "암호화폐 거래소", "digital banking",
    "Robinhood", "SoFi", "Coinbase", "Hood",
]))


def match_catalyst(
    *,
    name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    business_summary: str | None = None,
) -> str | None:
    """종목 정보 → catalyst tag 자동 매칭. 첫 번째 hit 반환.

    Active catalyst 만 매칭 (inactive 는 skip — 알림 우선순위 차원).
    """
    haystack_parts = [s for s in (name, sector, industry, business_summary) if s]
    if not haystack_parts:
        return None
    haystack = " | ".join(haystack_parts).lower()

    for catalyst, keywords in _KEYWORD_MAP:
        if catalyst not in ACTIVE_CATALYSTS:
            continue
        for kw in keywords:
            if kw.lower() in haystack:
                return catalyst
    return None


def auto_tag_universe(rows: list[dict]) -> list[dict]:
    """Universe rows 에 catalyst 자동 tag 추가.

    rows: dict list with keys (name, sector, industry, ticker, ...)
    Returns: same rows with 'auto_catalyst' field added.
    """
    out = []
    for r in rows:
        tag = match_catalyst(
            name=r.get("name") or r.get("name_ko"),
            sector=r.get("sector"),
            industry=r.get("industry"),
            business_summary=r.get("business_summary"),
        )
        r_new = dict(r)
        r_new["auto_catalyst"] = tag
        out.append(r_new)
    return out


def enrich_with_yfinance(ticker: str) -> dict[str, str | None]:
    """yfinance Ticker.info 로 sector/industry/business summary 가져옴 — auto catalyst 매칭용."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker if ticker.isdigit() == False else ticker + ".KS")
        info = tk.info if hasattr(tk, "info") else {}
        return {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "business_summary": (info.get("longBusinessSummary") or "")[:500],
        }
    except Exception as e:
        log.debug("yfinance enrich %s 실패: %s", ticker, e)
        return {"sector": None, "industry": None, "business_summary": None}


if __name__ == "__main__":
    # 단위 테스트
    samples = [
        {"name": "한미반도체", "industry": "HBM Bonding Machine"},
        {"name": "AppLovin", "industry": "광고 AI"},
        {"name": "IonQ", "industry": "Quantum Computing"},
        {"name": "삼양식품", "industry": "라면 수출"},
        {"name": "Random Co", "industry": "Unknown"},
    ]
    for s in samples:
        tag = match_catalyst(**s)
        print(f"{s['name']} → {tag}")
