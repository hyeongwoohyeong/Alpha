"""Catalyst tagging 시스템 — 종목별 sector cycle catalyst 분류.

각 catalyst 는:
  - 한국어 라벨
  - 기대 cycle 기간 (단기 / 중기 / 장기)
  - 대표 +100% sample (사후 검증용)
  - 현재 active 여부 (manual update)

사용:
  from src.catalyst_tags import get_catalyst, ACTIVE_CATALYSTS
"""
from __future__ import annotations

# Catalyst master dict
# active=True 면 현재 진행중 cycle, alert_engine R8 "+100% Watch" 가 surfacing
CATALYSTS: dict[str, dict] = {
    # AI 인프라
    "ai_hyperscale": {
        "ko": "AI 하이퍼스케일", "horizon": "long",
        "examples": ["PLTR (+340% 2024)", "APP (+713% 2024)"],
        "active": True,
    },
    "ai_infra": {
        "ko": "AI 인프라 (PCB·서버)", "horizon": "mid",
        "examples": ["AGX (+150% 2024)", "이수페타시스 (+250% 2024)"],
        "active": True,
    },
    "ai_software": {
        "ko": "AI 소프트웨어", "horizon": "mid",
        "examples": ["PLTR"], "active": True,
    },
    "hbm_ai_semi": {
        "ko": "HBM·AI 반도체", "horizon": "mid",
        "examples": ["한미반도체 (+254% 2024)", "이수페타시스"],
        "active": True,
    },

    # 양자
    "quantum_computing": {
        "ko": "양자 컴퓨팅", "horizon": "long",
        "examples": ["IONQ (+239% 2024)", "RGTI (+1670% 2024)", "QBTS (+855% 2024)"],
        "active": True,
    },
    "quantum_security": {
        "ko": "양자 암호", "horizon": "long",
        "examples": ["ARQQ"], "active": True,
    },

    # 의료
    "obesity_drug": {
        "ko": "비만 치료제 (GLP-1)", "horizon": "long",
        "examples": ["펩트론 (+200%+ 2024)", "LLY"],
        "active": True,
    },
    "adc_oncology": {
        "ko": "ADC 항암 신약", "horizon": "long",
        "examples": ["알테오젠 (+260% 2024)", "리가켐바이오"],
        "active": True,
    },
    "biosimilar": {
        "ko": "바이오시밀러", "horizon": "long",
        "examples": ["셀트리온"], "active": False,
    },

    # 산업
    "shipbuilding": {
        "ko": "조선 cycle", "horizon": "long",
        "examples": ["한화엔진 (+180% 2024)", "HD현대중공업"],
        "active": True,
    },
    "defense": {
        "ko": "방산 / 무기 수출", "horizon": "mid",
        "examples": ["한화에어로 (+200% 2024)", "LIG넥스원"],
        "active": True,
    },
    "robotics": {
        "ko": "로보틱스", "horizon": "long",
        "examples": ["두산로보틱스 (+180% 2024)"],
        "active": True,
    },
    "ev_battery": {
        "ko": "EV 2차전지", "horizon": "mid",
        "examples": ["삼성SDI", "LG에너지솔루션"],
        "active": False,  # 2024 하락 cycle
    },
    "energy_epc": {
        "ko": "에너지 EPC (원전·해외)", "horizon": "mid",
        "examples": ["삼성E&A", "현대건설"],
        "active": True,
    },
    "auto": {
        "ko": "자동차", "horizon": "mid",
        "examples": ["기아", "현대차"], "active": False,
    },

    # 전력 / 원자력
    "nuclear_power": {
        "ko": "원자력 (AI 전력 수혜)", "horizon": "long",
        "examples": ["VST (+265% 2024)", "CEG (+95% 2024)", "TLN (+213% 2024)"],
        "active": True,
    },

    # 크립토
    "btc_treasury": {
        "ko": "BTC 보유 기업", "horizon": "mid",
        "examples": ["MSTR (+358% 2024)"],
        "active": True,
    },
    "btc_mining": {
        "ko": "BTC 마이닝", "horizon": "mid",
        "examples": ["CLSK", "BITF", "RIOT", "MARA"],
        "active": True,
    },

    # 소비
    "k_food": {
        "ko": "K-푸드 globalization", "horizon": "long",
        "examples": ["삼양식품 (+180% 2024)"],
        "active": True,
    },
    "k_culture": {
        "ko": "K-Pop·엔터", "horizon": "mid",
        "examples": ["하이브", "JYP"], "active": False,
    },
    "k_beauty_med": {
        "ko": "K-뷰티·미용 의료", "horizon": "mid",
        "examples": ["파마리서치", "휴젤"],
        "active": True,
    },

    # 우주
    "space": {
        "ko": "우주 / 위성", "horizon": "long",
        "examples": ["RKLB (+360% 2024)", "ASTS (+200%+ 2024)"],
        "active": True,
    },

    # AI 드러그 / 헬스
    "ai_drug_discovery": {
        "ko": "AI 신약 개발", "horizon": "long",
        "examples": ["RXRX"], "active": True,
    },

    # 금융
    "fintech_crypto": {
        "ko": "핀테크 (크립토 수혜)", "horizon": "mid",
        "examples": ["HOOD (+200% 2024)", "SOFI"],
        "active": True,
    },

    # 게임
    "gaming": {
        "ko": "게임", "horizon": "mid",
        "examples": [], "active": False,
    },
    "gaming_crypto": {
        "ko": "블록체인 게임", "horizon": "long",
        "examples": ["위메이드"], "active": False,
    },

    # 기타
    "semi_material": {
        "ko": "반도체 소재·부품", "horizon": "mid",
        "examples": ["솔브레인", "ISC"], "active": True,
    },
    "tech_holding": {
        "ko": "테크 지주", "horizon": "long",
        "examples": ["SK스퀘어"], "active": False,
    },
}

ACTIVE_CATALYSTS = [k for k, v in CATALYSTS.items() if v.get("active")]


def get_catalyst(key: str) -> dict | None:
    """Catalyst metadata 조회. 없으면 None."""
    return CATALYSTS.get(key)


def format_catalyst_chip(key: str) -> str:
    """텔레그램/UI 용 한 줄 chip."""
    c = CATALYSTS.get(key)
    if not c:
        return key
    label = c.get("ko", key)
    return f"🏷️ {label}" + (" 🔥" if c.get("active") else "")


def list_active_with_examples() -> list[str]:
    """현재 active catalyst + 대표 sample 한 줄씩."""
    lines = []
    for k in ACTIVE_CATALYSTS:
        c = CATALYSTS[k]
        examples = ", ".join(c.get("examples", [])[:2]) or "—"
        lines.append(f"  {format_catalyst_chip(k)} → {examples}")
    return lines


if __name__ == "__main__":
    # 수동 실행 — active catalyst list
    print("=== Active Catalysts ===")
    for line in list_active_with_examples():
        print(line)
