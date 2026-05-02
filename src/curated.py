"""큐레이션된 정적 컨텐츠.

종목별 회사 분류(company_type), thesis_pillars, "이 회사는 쉽게 말해" 설명,
key_metrics_to_watch, 최근 주요 이벤트, 그리고 매크로·정책·지정학 이슈
정적 데이터를 모은다.

41종목 모두 풀 큐레이션 — 사용자 경험 일관성 + 모든 종목에서 동일 품질의
리서치 코멘트를 보장한다.

향후 LLM/RSS 기반 자동 업데이트로 교체할 수 있도록 함수 시그니처를 분리한다.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 종목 분류 (7유형) — Action Tag 결정의 1차 기준
# ---------------------------------------------------------------------------

COMPANY_TYPES: tuple[str, ...] = (
    "Civilization Alpha",      # 인간 욕망/병목/플랫폼 전환 가능성
    "Quality Dislocation",     # 우량주 단기 조정 (현재 시점에 따라 동적)
    "Re-rating Candidate",     # 시장이 회사를 다른 정체성으로 재평가
    "Structural Growth",       # 중장기 성장성 명확
    "Turnaround",              # 구조적 회복 가능성, 검증 필요
    "Too Crowded",             # 좋은 회사이나 기대 반영 과도 (동적)
    "Avoid",                   # 리스크가 thesis보다 큼 (동적)
)


# 각 종목의 정적 분류
COMPANY_TYPE_BY_TICKER: dict[str, str] = {
    # AI 인프라 — 대부분 Structural Growth
    "NVDA": "Structural Growth",
    "AVGO": "Structural Growth",
    "AMD": "Structural Growth",
    "MU": "Structural Growth",
    "TSM": "Structural Growth",
    "ANET": "Structural Growth",
    "CRDO": "Re-rating Candidate",      # 구리 → 광 확장
    "ALAB": "Structural Growth",
    "CLS": "Re-rating Candidate",       # EMS → AI 인프라 파트너
    # 데이터센터 전력
    "VRT": "Structural Growth",
    "ETN": "Structural Growth",
    "CEG": "Re-rating Candidate",       # 원자력 → AI 데이터센터 PPA
    "VST": "Re-rating Candidate",       # 발전 → AI PPA 모멘텀
    "CCJ": "Structural Growth",
    # 공공안전·방산
    "AXON": "Civilization Alpha",       # 공공안전 OS
    "PLTR": "Civilization Alpha",       # 데이터 운영체제
    "LMT": "Structural Growth",
    "NOC": "Structural Growth",
    "RTX": "Structural Growth",
    "HII": "Structural Growth",
    # 우주
    "RKLB": "Structural Growth",
    "LUNR": "Structural Growth",
    # 헬스케어
    "TMDX": "Civilization Alpha",       # 장기이식 병목
    "ISRG": "Structural Growth",
    "NTRA": "Structural Growth",
    "TMO": "Structural Growth",
    "DHR": "Structural Growth",
    # 빅테크 / 플랫폼
    "NFLX": "Quality Dislocation",      # M&A 종료 후 본업 회복 검토
    "META": "Structural Growth",
    "AMZN": "Structural Growth",
    "MSFT": "Structural Growth",
    "GOOGL": "Structural Growth",
    # 모빌리티 / 소비
    "TSLA": "Turnaround",               # robotaxi 검증 단계
    "PYPL": "Turnaround",               # 성장 둔화 회복 검증
    "SBUX": "Turnaround",               # 동일점포 매출 회복
    "DIS": "Turnaround",                # DTC 흑자 전환
    # 이커머스 / 여행
    "SHOP": "Structural Growth",
    "MELI": "Structural Growth",
    "UBER": "Structural Growth",
    "ABNB": "Structural Growth",
    "BKNG": "Structural Growth",
    "SPOT": "Structural Growth",
}


def company_type(ticker: str) -> str:
    """종목별 정적 분류. 동적 분류(Too Crowded/Avoid)는 scoring 단계에서 덮어씀."""
    return COMPANY_TYPE_BY_TICKER.get(ticker, "Structural Growth")


# ---------------------------------------------------------------------------
# Thesis Pillars — 종목별 핵심 투자 논리 3가지
# ---------------------------------------------------------------------------

THESIS_PILLARS: dict[str, list[str]] = {
    # AI 반도체
    "NVDA": [
        "AI GPU/CUDA 생태계의 사실상 독점적 카테고리 리더십",
        "Hyperscaler capex 사이클의 가장 직접적 수혜",
        "차세대 GPU(Rubin) 로드맵과 추론 시장 확장",
    ],
    "AVGO": [
        "Hyperscaler 맞춤형 ASIC 사업의 새로운 성장축",
        "통신용 반도체 + 인프라 SW의 안정적 캐시플로우",
        "VMware 통합 후 SW 매출 비중 확대",
    ],
    "AMD": [
        "MI 시리즈 데이터센터 GPU의 NVDA 대안 채택 가속",
        "EPYC CPU 데이터센터 점유율 지속 확장",
        "PC/게이밍 사이클 회복 옵션",
    ],
    "MU": [
        "AI 학습용 HBM 수요의 직접 수혜 카테고리 리더",
        "메모리 사이클 회복기의 평균판가 상승",
        "장기 AI 추론 메모리 수요 증가",
    ],
    "TSM": [
        "첨단 로직 파운드리 사실상 독점",
        "AI 사이클 가장 깊은 곳의 인프라 기업",
        "지정학 리스크 vs 가격결정력의 트레이드오프",
    ],
    # AI 네트워킹
    "ANET": [
        "AI 클러스터 고속 이더넷 카테고리 리더",
        "Hyperscaler 주문 강도 가시성",
        "Software-defined 네트워킹의 안정적 마진",
    ],
    "CRDO": [
        "Active Electrical Cable / DSP 중심 AI 인터커넥트",
        "최근 광통신 인수로 광 영역 확장 가능성",
        "데이터센터 고속 연결 병목 수혜",
    ],
    "ALAB": [
        "PCIe/CXL 차세대 인터커넥트 표준 선도",
        "AI 데이터센터 CPU-GPU-메모리 연결 칩",
        "고성장 초기 단계의 카테고리 진입 우위",
    ],
    "CLS": [
        "Hyperscaler 향 AI 서버 ODM 매출 가속",
        "EMS에서 AI 인프라 파트너로 정체성 재평가",
        "안정적 매출 베이스 + AI 사이클 옵션",
    ],
    # 데이터센터 전력
    "VRT": [
        "AI 데이터센터 전력/냉각 인프라 카테고리 리더",
        "액침냉각 등 신규 솔루션 채택 가속",
        "데이터센터 capex 사이클의 직접 수혜",
    ],
    "ETN": [
        "산업용 전력관리 글로벌 카테고리 리더",
        "AI 데이터센터/전기화 사이클 동시 수혜",
        "안정적 backlog와 마진 확장",
    ],
    "CEG": [
        "미국 최대 무탄소 원자력 발전 자산",
        "AI 데이터센터 PPA 체결 가시성",
        "안정 베이스 캐시플로우 + 전력 가격 옵션",
    ],
    "VST": [
        "가스/원자력/배터리 다양한 전원 보유",
        "AI 데이터센터 PPA + 전력 가격 동시 수혜",
        "지역 전력 수급 타이트화의 옵션",
    ],
    "CCJ": [
        "글로벌 우라늄 카테고리 리더",
        "원전 르네상스 + AI 전력 수요의 직접 수혜",
        "장기 계약 가격 인상 옵션",
    ],
    # 공공안전·방산
    "AXON": [
        "테이저·바디캠 하드웨어 매출 안정성",
        "Evidence Cloud SaaS ARR 가속과 plat폼화",
        "AI 리포팅/분석 모듈 attach rate 확장",
    ],
    "PLTR": [
        "정부·국방 매출 베이스의 안정성",
        "AIP 민간 도입 가속과 ARR 확장",
        "데이터 통합 플랫폼의 lock-in 효과",
    ],
    "LMT": [
        "F-35 / 미사일 방어 핵심 프로그램",
        "다수의 장기 backlog와 안정적 FCF",
        "지정학 리스크 확대 구간의 수혜",
    ],
    "NOC": [
        "차세대 ICBM(Sentinel) / B-21 폭격기 핵심 컨트랙터",
        "전략 자산 현대화 사이클의 직접 수혜",
        "우주 시스템 부문 성장 옵션",
    ],
    "RTX": [
        "미사일 방어 + 항공기 엔진 양대 축",
        "지정학 리스크 + 항공 수요 회복 동시 수혜",
        "안정적 backlog와 cycle 분산",
    ],
    "HII": [
        "미국 항공모함/잠수함 사실상 독점 조선",
        "함정 backlog 확장과 해군 예산 수혜",
        "안정 매출 + 가격결정력",
    ],
    # 우주
    "RKLB": [
        "소형 위성 발사 + 시스템 수직 통합",
        "Neutron 로켓 진입으로 중대형 발사 시장 확장",
        "정부 task order 누적과 매출 다각화",
    ],
    "LUNR": [
        "NASA CLPS 프로그램의 핵심 수행자",
        "우주 운송 + 데이터 사업 확장 옵션",
        "정부 매출 비중의 안정성",
    ],
    # 헬스케어
    "TMDX": [
        "OCS 장기 보존 솔루션의 카테고리 독점",
        "기증 장기 활용률 개선의 사회적 임팩트",
        "장기이식 병목 해결의 구조적 성장",
    ],
    "ISRG": [
        "다빈치 로봇 수술 시스템 사실상 독점",
        "razor-and-blade 구조의 누적 매출",
        "신제품(Ion / SP) 카테고리 확장",
    ],
    "NTRA": [
        "Signatera 암 재발 모니터링 채택 가속",
        "산전 진단 카테고리 리더",
        "신규 검사 파이프라인 확장",
    ],
    "TMO": [
        "생명과학 연구·임상·진단 인프라 카테고리 리더",
        "팬데믹 후 정상화 + 베이스 매출 재가속",
        "M&A 통한 카테고리 확장",
    ],
    "DHR": [
        "생명과학·진단·바이오프로세싱 quality compounder",
        "바이오프로세싱 회복 사이클 수혜",
        "M&A 운영의 역사적 성공 트랙",
    ],
    # 빅테크 / 플랫폼
    "NFLX": [
        "글로벌 스트리밍 카테고리 리더 / 콘텐츠 IP 보유",
        "광고 요금제 확장과 ARPU 가속",
        "FCF 창출력 + 자사주 매입 여력",
    ],
    "META": [
        "글로벌 광고 플랫폼 사용자 베이스 견고",
        "AI 추천 알고리즘으로 광고 효율 가속",
        "AI/XR capex의 장기 옵션",
    ],
    "AMZN": [
        "AWS의 AI 워크로드 가속",
        "광고 부문의 빠른 수익성 확대",
        "이커머스 마진 회복",
    ],
    "MSFT": [
        "Azure의 AI 워크로드 견인 ARR 가속",
        "OpenAI 협력 기반 Copilot 통합",
        "Office/365 base의 안정적 캐시플로우",
    ],
    "GOOGL": [
        "검색 광고 베이스의 견고함",
        "Cloud (GCP) AI 워크로드 가속",
        "Waymo / Gemini 옵션 가치",
    ],
    # 모빌리티 / 소비
    "TSLA": [
        "Robotaxi 진척과 자율주행 옵션",
        "EV 마진 회복 가능성",
        "에너지 사업 확장",
    ],
    "PYPL": [
        "글로벌 결제 플랫폼 베이스",
        "ARPU 회복과 마진 개선 잠재력",
        "Turnaround 진척 검증 단계",
    ],
    "SBUX": [
        "글로벌 프리미엄 커피 카테고리 리더",
        "동일점포 매출 회복 가능성",
        "중국 시장 회복 옵션",
    ],
    "DIS": [
        "글로벌 IP 라이브러리의 압도적 우위",
        "DTC 흑자 전환과 마진 개선",
        "테마파크 안정 베이스",
    ],
    # 이커머스 / 여행 / 미디어
    "SHOP": [
        "중소·중견 기업 이커머스 인프라 카테고리 리더",
        "결제/광고 등 부가 매출 비중 확대",
        "GMV 성장 + take-rate 개선",
    ],
    "MELI": [
        "라틴 이커머스·결제·물류 카테고리 리더",
        "결제(Mercado Pago) ARPU 확장",
        "물류 네트워크 효율 개선",
    ],
    "UBER": [
        "글로벌 모빌리티 + 음식 배달 플랫폼",
        "광고 부문 수익성 확대",
        "Robotaxi 파트너십 옵션",
    ],
    "ABNB": [
        "글로벌 단기 숙박 카테고리 리더",
        "여행 수요 정상화의 직접 수혜",
        "경험(Experiences) 카테고리 옵션",
    ],
    "BKNG": [
        "글로벌 OTA 카테고리 리더 quality compounder",
        "안정적 광고 효율과 캐시플로우",
        "여행 수요 안정성",
    ],
    "SPOT": [
        "글로벌 음악 스트리밍 카테고리 리더",
        "단가 인상과 광고 사업 마진 개선",
        "콘텐츠 비용 효율화",
    ],
}


def thesis_pillars(ticker: str) -> list[str]:
    return list(THESIS_PILLARS.get(ticker, []))


# ---------------------------------------------------------------------------
# Key metrics to watch — 종목별 모니터링 지표 3~5개
# ---------------------------------------------------------------------------

KEY_METRICS: dict[str, list[str]] = {
    "NVDA": ["데이터센터 매출 증가율", "Hyperscaler capex revision", "Rubin 로드맵 cadence"],
    "AVGO": ["AI ASIC 매출 비중", "VMware 통합 진척", "Free cash flow"],
    "AMD": ["MI 시리즈 데이터센터 매출", "EPYC 점유율", "Gross margin"],
    "MU": ["HBM 매출 비중", "DRAM ASP 추세", "Inventory days"],
    "TSM": ["3nm/2nm 노드 가동률", "Capex 가이던스", "AI 매출 비중"],
    "ANET": ["AI 클러스터 ethernet 채택률", "Hyperscaler 주문 강도", "Gross margin"],
    "CRDO": ["AEC 매출 성장률", "광통신 매출 인식 시점", "신규 hyperscaler 고객"],
    "ALAB": ["PCIe/CXL 채택 가속", "ASP 추세", "Gross margin"],
    "CLS": ["AI 서버 ODM 매출 비중", "Hyperscaler 고객 다변화", "Operating margin"],
    "VRT": ["액침냉각 매출 인식", "Backlog 변화", "Gross margin"],
    "ETN": ["전력관리 부문 매출", "Backlog book-to-bill", "Operating margin"],
    "CEG": ["PPA 체결 단가", "Capacity factor", "신규 capacity 추가"],
    "VST": ["PPA 단가", "전력 가격 추세", "ERCOT/PJM 노출"],
    "CCJ": ["우라늄 spot 가격", "장기 계약 가격", "신규 계약 체결"],
    "AXON": ["소프트웨어 매출 비중", "ARR 성장률", "AI 모듈 attach rate"],
    "PLTR": ["AIP 신규 고객 수", "민간 매출 가속", "Net Dollar Retention"],
    "LMT": ["F-35 backlog", "DoD 예산안", "Free cash flow"],
    "NOC": ["Sentinel 진척", "B-21 매출 인식", "Backlog"],
    "RTX": ["엔진 backlog", "미사일 매출", "FCF 가이던스"],
    "HII": ["함정 backlog", "해군 예산", "Operating margin"],
    "RKLB": ["발사 cadence", "Neutron 진척", "Gross margin"],
    "LUNR": ["NASA task order 수주", "Gross margin", "운송 매출 비중"],
    "TMDX": ["OCS 볼륨 성장", "장기 대체 기술 임상 진척", "Reimbursement 환경"],
    "ISRG": ["프로시저 볼륨", "신제품 채택", "Operating margin"],
    "NTRA": ["Signatera 채택률", "신규 검사 파이프라인", "Gross margin"],
    "TMO": ["베이스 매출 가속", "M&A 통합 진척", "Operating margin"],
    "DHR": ["바이오프로세싱 회복", "Operating margin", "FCF"],
    "NFLX": ["광고 요금제 성장률", "FCF 가이던스", "자사주 매입 규모"],
    "META": ["광고 ARPU", "AI 인프라 capex 효율", "Free cash flow"],
    "AMZN": ["AWS 성장률", "광고 매출 비중", "Operating margin"],
    "MSFT": ["Azure 성장률", "Copilot ARR", "Operating margin"],
    "GOOGL": ["검색 광고 안정성", "Cloud 성장률", "Capex 효율"],
    "TSLA": ["Robotaxi 진척", "자동차 마진", "Energy 매출"],
    "PYPL": ["TPV 성장률", "Take-rate", "Operating margin"],
    "SBUX": ["미국 동일점포 매출", "중국 회복", "Operating margin"],
    "DIS": ["DTC 영업이익", "Parks 마진", "콘텐츠 투자 효율"],
    "SHOP": ["GMV 성장률", "Take-rate", "Operating margin"],
    "MELI": ["GMV / TPV 성장", "Mercado Pago ARPU", "Logistics 효율"],
    "UBER": ["Bookings 성장률", "Take-rate", "광고 매출 비중"],
    "ABNB": ["Nights booked 성장", "ADR 안정성", "FCF"],
    "BKNG": ["Bookings 성장", "광고 효율", "FCF 마진"],
    "SPOT": ["MAU 성장", "Premium ARPU", "Gross margin"],
}


def key_metrics(ticker: str) -> list[str]:
    return list(KEY_METRICS.get(ticker, ["실적 가이던스", "주요 catalyst", "Valuation 변화"]))


# ---------------------------------------------------------------------------
# 종목별 투자 thesis 한 단락 (Daily Brief / 종목 상세의 핵심 투자 포인트)
# 템플릿 문장 금지 — 종목 고유의 사업 구조 / 최근 이벤트 / 핵심 변수 반영.
# ---------------------------------------------------------------------------

INVESTMENT_THESIS_KO: dict[str, str] = {
    # ── AI 반도체 ──────────────────────────────────────────────
    "NVDA": (
        "엔비디아는 AI 컴퓨팅 인프라의 핵심 병목을 장악한 기업입니다. "
        "단기적으로는 hyperscaler CAPEX 와 GPU/HBM 수급이 핵심이고, "
        "중장기적으로는 CUDA 생태계 lock-in 과 고객 자체칩(ASIC) 리스크의 균형, "
        "그리고 추론(inference) 시장 확대 속도가 주요 변수입니다."
    ),
    "AVGO": (
        "브로드컴은 hyperscaler 맞춤형 AI ASIC 신규 성장축과 통신용 반도체·인프라 SW 의 "
        "안정적 캐시플로우가 결합된 구조입니다. VMware 통합 이후 SW 매출 비중 상승이 "
        "multiple 재평가의 주요 변수이며, ASIC 매출 가시성이 thesis 강화의 핵심 신호입니다."
    ),
    "AMD": (
        "AMD 는 데이터센터 GPU(MI 시리즈) 의 NVDA 대안 채택 가속과 EPYC CPU 점유율 확장이 "
        "동시에 진행되는 구간입니다. AI GPU 매출 인식 속도와 hyperscaler 수주 가시성이 "
        "재평가의 핵심이며, PC/게이밍 사이클은 시클리컬 옵션입니다."
    ),
    "MU": (
        "마이크론은 AI 학습용 HBM 수요의 직접 수혜 카테고리 리더입니다. "
        "메모리 사이클 회복기의 평균판가(ASP) 상승, HBM 점유율, 그리고 장기 AI 추론 메모리 "
        "수요 증가가 thesis 의 세 축입니다. 사이클 변곡점 인식 시점이 핵심 변수."
    ),
    "TSM": (
        "TSMC 는 첨단 로직 파운드리를 사실상 독점하며 AI 사이클 가장 깊은 곳의 인프라 기업으로 "
        "자리잡고 있습니다. 3nm/2nm 가동률, AI 매출 비중, 가격결정력이 thesis 의 핵심이고, "
        "지정학 리스크가 가장 큰 anti-thesis 입니다."
    ),
    # ── AI 네트워킹 ────────────────────────────────────────────
    "ANET": (
        "아리스타는 AI 클러스터 고속 이더넷 카테고리 리더로, hyperscaler 주문 강도와 "
        "Software-defined 네트워킹의 안정적 마진이 결합된 구조입니다. AI 데이터센터 capex "
        "사이클의 직접 수혜와 NVIDIA InfiniBand 대비 ethernet 채택률이 핵심 변수."
    ),
    "CRDO": (
        "크리도는 AI 데이터센터 내부의 고속 연결 병목을 해결하는 connectivity layer 기업입니다. "
        "구리 기반 Active Electrical Cable 투자 논리에 더해 광통신 관련 인수로 optical "
        "interconnect 영역까지 확장할 수 있는지가 핵심입니다."
    ),
    "ALAB": (
        "아스테라 랩스는 PCIe/CXL 차세대 인터커넥트 표준의 선도주자로, AI 데이터센터 내 "
        "CPU-GPU-메모리 연결 칩을 만듭니다. 카테고리 진입 우위가 thesis 이며, 고객 다변화 "
        "속도와 ASP 추세가 주요 점검 포인트입니다."
    ),
    "CLS": (
        "셀레스티카는 EMS 사업자에서 hyperscaler 향 AI 서버 ODM 매출이 가속되며 정체성이 "
        "재평가되는 구간입니다. AI 서버 ODM 매출 비중과 고객 다변화가 multiple 재평가의 "
        "핵심 변수입니다."
    ),
    # ── 데이터센터 전력 ──────────────────────────────────────
    "VRT": (
        "버티브는 AI 데이터센터 전력/냉각 인프라 카테고리 리더입니다. 액침냉각 등 신규 솔루션 "
        "채택 가속과 데이터센터 capex 사이클의 직접 수혜가 결합되며, backlog 변화가 매출 "
        "가시성의 핵심 지표입니다."
    ),
    "ETN": (
        "이튼은 산업용 전력관리 글로벌 카테고리 리더로, AI 데이터센터와 전기화 사이클을 "
        "동시에 수혜합니다. 안정적 backlog 와 마진 확장이 thesis 이며, 산업 수요 둔화가 "
        "주요 anti-thesis 입니다."
    ),
    "CEG": (
        "컨스텔레이션 에너지는 미국 최대 무탄소 전력 생산자로, AI 데이터센터의 안정적 전력 "
        "수요를 PPA(전력구매계약) 단가 상승으로 현금화할 수 있는 구간입니다. PPA 체결 단가, "
        "원전 capacity factor, 신규 capacity 추가가 핵심 변수입니다."
    ),
    "VST": (
        "비스트라는 텍사스 중심 발전 사업자로, 데이터센터 신규 입지 수요와 천연가스 가격 "
        "전망의 균형이 핵심입니다. PPA 계약 단가, 발전소 capacity, 그리고 AI 관련 신규 "
        "수요 가시성이 주요 변수."
    ),
    "CCJ": (
        "카메코는 캐나다 최대 우라늄 생산자로, 전 세계 원전 회귀 흐름의 직접 수혜 종목입니다. "
        "우라늄 가격, 신규 원전 승인 추세, SMR(소형모듈원자로) 도입 속도가 핵심 변수."
    ),
    # ── Public Safety ─────────────────────────────────────────
    "AXON": (
        "액손은 테이저·바디캠 중심의 장비업체에서 공공안전 데이터, 증거관리, AI 리포팅을 "
        "포괄하는 Public Safety OS 로 전환될 가능성이 있는 종목입니다. 하드웨어 판매보다 "
        "소프트웨어 매출 비중과 Evidence Cloud lock-in 이 핵심 투자 포인트입니다."
    ),
    "PLTR": (
        "팔란티어는 정부·국방 데이터 분석에서 출발해 상업용 AIP(AI Platform) 로 사업 영역을 "
        "확장하는 구간입니다. 상업용 매출 성장률과 정부 부문 안정성, 그리고 AIP 채택 속도가 "
        "thesis 의 핵심 변수입니다."
    ),
    # ── 디펜스 ───────────────────────────────────────────────
    "LMT": (
        "록히드마틴은 미사일 방어 / 전투기 (F-35) / 우주 사업이 결합된 미국 최대 방산주로, "
        "지정학 리스크와 정부 예산 사이클의 직접 수혜 구조입니다. 수주 backlog 와 F-35 인도 "
        "속도, 신규 무기 시스템 수주가 핵심 변수."
    ),
    "NOC": (
        "노스롭그루먼은 차세대 폭격기(B-21) / 무인기 / 우주 시스템에 강점을 가진 방산주로, "
        "장기 무기 현대화 사이클의 핵심 수혜자입니다. B-21 양산 일정과 우주·미사일 부문 "
        "수주가 핵심 변수."
    ),
    "RTX": (
        "RTX 는 항공엔진(Pratt & Whitney) + 미사일 방어(Raytheon) + 항공우주 사업이 결합된 "
        "방산 / 항공 복합주로, 민항기 회복과 무기 현대화의 동시 수혜 구조입니다. GTF 엔진 "
        "리콜 영향 해소와 backlog 변화가 핵심 변수."
    ),
    "HII": (
        "헌팅턴 잉걸스는 미국 해군 함정 건조의 사실상 독점 사업자로, 대중 견제 강화에 따른 "
        "해군 capacity 확장 사이클의 직접 수혜 종목입니다. backlog 길이, 함정 인도 속도, "
        "그리고 인건비 / 자재비 흡수 능력이 핵심 변수."
    ),
    # ── Space ────────────────────────────────────────────────
    "RKLB": (
        "로켓랩은 소형 위성 발사 사업과 위성 서비스 부문이 결합된 우주 인프라 후발주자로, "
        "Neutron 로켓 개발 진척이 valuation 재평가의 핵심입니다. 정부 우주 예산 수혜와 "
        "위성 사업 매출 가시성이 주요 변수."
    ),
    "LUNR": (
        "인튜이티브 머신스는 NASA Artemis 프로그램과 연계된 달 탐사 인프라 기업으로, "
        "착륙선 미션 수주가 매출 가시성의 핵심입니다. 정부 계약 수주와 미션 성공률이 "
        "재평가의 결정 변수입니다."
    ),
    # ── Healthcare Infra ─────────────────────────────────────
    "TMDX": (
        "트랜스메딕스는 장기이식용 장기 보존 기술의 카테고리 리더로, 폐 / 심장 / 간 OCS 시스템 "
        "채택 확대가 매출 성장의 핵심입니다. 병원 채택률, 기계당 procedure 수, 그리고 "
        "신규 장기 종류 확대가 주요 변수."
    ),
    "ISRG": (
        "인튜이티브 서지컬은 다빈치 로봇수술 시스템의 사실상 독점 사업자로, 로봇수술 채택률 "
        "확장과 da Vinci 5 신모델 출시 사이클이 핵심입니다. 시스템 설치 수, procedure 수 "
        "증가율이 주요 KPI."
    ),
    "NTRA": (
        "내터라는 비침습 산전검사(NIPT) 와 종양학 액체생검(Signatera) 의 카테고리 리더로, "
        "Signatera 의 보험 수가 확대가 매출 가속의 핵심 변수입니다. 검사 volume 증가와 "
        "수가 수렴이 thesis 의 두 축입니다."
    ),
    "TMO": (
        "써모 피셔는 라이프사이언스 도구·시약·서비스의 글로벌 1위 사업자로, 바이오 R&D / "
        "임상 / CDMO 사업이 결합된 안정적 캐시플로우 구조입니다. 코로나 후 정상화된 base "
        "효과 이후 본업 성장률 회복이 핵심 변수."
    ),
    "DHR": (
        "다나허는 라이프사이언스 / 바이오프로세싱 / 진단 사업이 결합된 헬스케어 인프라 "
        "복합 기업으로, 바이오프로세싱 수주 사이클 회복이 thesis 재가동의 핵심 변수입니다. "
        "재고 정상화 속도와 신규 R&D 수요가 주요 점검 포인트."
    ),
    # ── Quality Platform ─────────────────────────────────────
    "NFLX": (
        "넷플릭스는 Warner Bros. 관련 대형 M&A 리스크가 해소된 이후, 투자 초점이 다시 "
        "광고 요금제 성장, 가격 인상 여력, 콘텐츠 투자 효율, FCF 창출력으로 이동한 구간입니다. "
        "고점 대비 주가 조정이 본업 훼손이 아니라 이벤트성 우려 반영이었다면 Quality "
        "Dislocation 후보로 재평가될 수 있습니다."
    ),
    "META": (
        "메타는 광고 노출 수와 광고 평균 단가가 동시에 개선되는 강한 본업 흐름을 보이고 "
        "있으나, 2026년 AI CAPEX 가이던스 상향으로 FCF 와 증분 ROIC 에 대한 우려가 커진 "
        "구간입니다. 핵심은 AI 투자가 광고 효율과 매출 성장으로 회수되는지 여부입니다."
    ),
    "AMZN": (
        "아마존은 AWS 의 AI 워크로드 가속, 광고 사업 성장, 그리고 retail margin 회복이 "
        "동시에 진행되는 구간입니다. AWS 매출 성장률과 retail OPM 추세, 그리고 AI 투자비 "
        "회수 시점이 thesis 의 핵심 변수입니다."
    ),
    "MSFT": (
        "마이크로소프트는 Azure 의 AI 매출 가속과 Copilot 의 enterprise SW 가격 인상 "
        "여력이 결합된 구조입니다. Azure 성장률, Copilot 채택 속도, 그리고 AI CAPEX 회수 "
        "속도가 multiple 유지의 핵심입니다."
    ),
    "GOOGL": (
        "알파벳은 검색 사업의 AI 전환 (Search Generative Experience) 영향과 Cloud / Waymo / "
        "AI 모델(Gemini) 의 성장이 동시에 평가되는 구간입니다. 검색 광고 매출 안정성과 "
        "Cloud 흑자 전환 속도가 핵심 변수입니다."
    ),
    # ── E-commerce / Travel / Mobility ───────────────────────
    "SHOP": (
        "쇼피파이는 SMB 이커머스 인프라의 글로벌 사업자로, GMV 성장률과 Subscription / "
        "MerSol 매출 비중이 thesis 의 핵심입니다. payments take-rate 와 cross-border / "
        "B2B 신규 매출이 주요 점검 포인트."
    ),
    "MELI": (
        "메르카도리브레는 라틴아메리카 1위 이커머스 + 핀테크 (MercadoPago) 결합 사업자로, "
        "GMV 성장률, MercadoPago TPV 성장률, 그리고 신용 사업의 연체율이 핵심 변수입니다."
    ),
    "TSLA": (
        "테슬라는 자동차 본업 (마진 압박) 과 Robotaxi / FSD / Energy / Optimus 옵션이 "
        "결합된 valuation 구조입니다. 자동차 마진 회복 vs. 자율주행 옵션 가치의 균형이 "
        "thesis 의 핵심이며, Robotaxi 사업 진척 속도가 가장 큰 변수입니다."
    ),
    "PYPL": (
        "페이팔은 결제 시장 점유율 회복과 Braintree (B2B) / Venmo 매출 다변화 사이클의 "
        "전환점에 있는 종목입니다. take-rate 안정화와 영업레버리지 회복이 thesis 의 두 축이며, "
        "Apple Pay 등 경쟁 심화가 주요 anti-thesis."
    ),
    "UBER": (
        "우버는 ride-share 의 안정적 캐시플로우 + 음식배달(Eats) + 광고 사업의 결합으로, "
        "FCF 창출력 확대와 광고 매출 비중 상승이 multiple 재평가의 핵심입니다. Robotaxi "
        "도입에 따른 사업 모델 변화가 장기 변수."
    ),
    "ABNB": (
        "에어비앤비는 글로벌 단기 임대 카테고리 리더로, 거래 비중 점유율 확장과 ADR 안정성이 "
        "thesis 의 두 축입니다. 호텔 vs. 단기임대 가격 차이 회복과 신규 시장 진출 속도가 "
        "주요 변수입니다."
    ),
    "BKNG": (
        "부킹홀딩스는 글로벌 OTA 1위 사업자로, 글로벌 여행 수요 회복과 광고 매출 / connected "
        "trip 사업 확장이 thesis 입니다. 환율 영향과 take-rate 안정성, 그리고 alternative "
        "accommodation (Airbnb 와의 경쟁) 가 주요 변수."
    ),
    "SPOT": (
        "스포티파이는 글로벌 음원 스트리밍 카테고리 리더로, 가격 인상 후 churn 안정화와 "
        "광고 / 팟캐스트 매출 다변화가 thesis 의 핵심입니다. 영업레버리지 회복 속도와 "
        "콘텐츠 투자 효율이 주요 점검 포인트."
    ),
    # ── Consumer Brand ───────────────────────────────────────
    "SBUX": (
        "스타벅스는 미국 본업 same-store sales 회복 / 중국 사업 정상화 / 신규 CEO 의 "
        "operational reset 가 동시에 진행되는 turnaround 후보입니다. 주문 / 매장 처리 효율 "
        "개선과 신규 메뉴 mix 가 핵심 변수."
    ),
    "DIS": (
        "디즈니는 스트리밍(Disney+) 흑자 전환, 테마파크 본업 회복, 콘텐츠 IP 사이클이 "
        "결합된 turnaround 구간입니다. DTC 영업이익률 개선 속도와 박스오피스 / 테마파크 "
        "매출 추세가 핵심 변수입니다."
    ),
}


def investment_thesis(ticker: str) -> str | None:
    return INVESTMENT_THESIS_KO.get(ticker)


# ---------------------------------------------------------------------------
# 종목별 핵심 KPI (확인 필요 사항) — 사용자가 후속 리서치할 수 있는 구체 항목
# ---------------------------------------------------------------------------

CORE_KPIS_KO: dict[str, list[str]] = {
    "NVDA": [
        "데이터센터 매출 성장률", "Hyperscaler CAPEX revision",
        "HBM/GPU 공급 상황", "Networking 매출 성장",
        "중국 수출 규제 영향", "고객 자체칩 리스크",
    ],
    "AVGO": [
        "AI ASIC 매출 비중", "VMware 통합 진척",
        "Free cash flow", "Operating margin", "ASIC 신규 고객 수주",
    ],
    "AMD": [
        "MI 시리즈 데이터센터 매출", "EPYC 점유율",
        "AI GPU 수주 가시성", "Gross margin", "PC/게이밍 사이클",
    ],
    "MU": [
        "HBM 매출 비중", "DRAM ASP 추세",
        "Inventory days", "Capex 가이던스", "AI 메모리 capacity 확장",
    ],
    "TSM": [
        "3nm/2nm 가동률", "AI 매출 비중",
        "Capex 가이던스", "ASP 추세", "지정학 리스크 변화",
    ],
    "ANET": [
        "AI 클러스터 ethernet 채택률", "Hyperscaler 주문 강도",
        "Gross margin", "Backlog 변화",
    ],
    "CRDO": [
        "AEC 매출 성장률", "광통신 매출 인식 시점",
        "신규 hyperscaler 고객", "Gross margin",
        "광통신 인수 시너지 가시성", "고객 집중도",
    ],
    "ALAB": [
        "PCIe/CXL 채택 가속", "ASP 추세",
        "고객 다변화 진척", "Gross margin",
    ],
    "CLS": [
        "AI 서버 ODM 매출 비중", "Hyperscaler 고객 다변화",
        "Operating margin", "Backlog 변화",
    ],
    "VRT": [
        "액침냉각 매출 인식", "Backlog book-to-bill",
        "Gross margin", "신규 hyperscaler 수주",
    ],
    "ETN": [
        "전력관리 부문 매출", "Backlog book-to-bill",
        "Operating margin", "데이터센터 / 전기화 매출 비중",
    ],
    "CEG": [
        "PPA 체결 단가", "Capacity factor",
        "신규 capacity 추가", "AI 데이터센터 PPA 수주",
    ],
    "VST": [
        "PPA 계약 단가", "발전소 capacity factor",
        "AI 신규 수요 수주", "천연가스 가격 추세",
    ],
    "CCJ": [
        "우라늄 spot / contract 가격", "광산 생산량",
        "신규 원전 승인 추세", "장기 계약 가격",
    ],
    "AXON": [
        "소프트웨어 매출 비중", "Evidence Cloud ARR",
        "AI 리포팅 유료화 지표", "Net retention",
        "B2G 계약 기간 및 renewal", "공공안전 예산 사이클",
    ],
    "PLTR": [
        "상업용 매출 성장률", "정부 부문 안정성",
        "AIP 채택 속도", "Net retention", "Customer 수 증가",
    ],
    "LMT": [
        "F-35 인도 속도", "수주 Backlog",
        "신규 무기 수주", "마진율 추세",
    ],
    "NOC": [
        "B-21 양산 일정", "우주 / 미사일 부문 수주",
        "Backlog 변화", "마진율",
    ],
    "RTX": [
        "GTF 엔진 리콜 영향", "Backlog 변화",
        "민항기 사이클 회복", "Raytheon 수주",
    ],
    "HII": [
        "함정 인도 속도", "Backlog 길이",
        "인건비 / 자재비 흡수 능력", "신규 함정 수주",
    ],
    "RKLB": [
        "Neutron 로켓 개발 진척", "정부 우주 예산 수혜",
        "위성 사업 매출 가시성", "Electron 발사 빈도",
    ],
    "LUNR": [
        "정부 계약 수주", "미션 성공률",
        "신규 NASA Artemis 발주", "현금 소진 속도",
    ],
    "TMDX": [
        "병원 채택률", "기계당 procedure 수",
        "신규 장기 종류 확대", "Gross margin",
    ],
    "ISRG": [
        "시스템 설치 수", "Procedure 수 증가율",
        "da Vinci 5 채택", "International expansion",
    ],
    "NTRA": [
        "Signatera 검사 volume", "보험 수가 수렴",
        "NIPT 점유율", "Operating margin 회복",
    ],
    "TMO": [
        "본업 매출 성장률 회복", "바이오 R&D 수요",
        "CDMO 매출", "Operating margin",
    ],
    "DHR": [
        "바이오프로세싱 수주", "재고 정상화 속도",
        "Bioprocessing book-to-bill", "Gross margin",
    ],
    "NFLX": [
        "광고 요금제 매출 성장률", "유료 가입자당 매출(ARPU)",
        "콘텐츠 투자비 대비 시청/가입자 효율", "FCF margin",
        "가격 인상 후 churn 변화", "대형 M&A 리스크 해소 여부",
    ],
    "META": [
        "광고 노출 수 증가율", "광고 평균 단가 변화",
        "AI CAPEX 증가율", "FCF margin",
        "Reality Labs 영업손실", "AI 투자 이후 광고 효율 개선 여부",
    ],
    "AMZN": [
        "AWS 매출 성장률", "AWS 영업이익률",
        "Retail OPM 추세", "광고 매출 성장률",
        "AI 투자비 회수 시점",
    ],
    "MSFT": [
        "Azure 매출 성장률", "Copilot 채택 속도",
        "Operating margin", "AI CAPEX 회수 속도",
    ],
    "GOOGL": [
        "검색 광고 매출", "Cloud 매출 성장률",
        "Cloud 흑자 전환 속도", "Search AI 전환 영향",
    ],
    "SHOP": [
        "GMV 성장률", "Subscription / MerSol 매출 비중",
        "Payments take-rate", "B2B / cross-border 신규 매출",
    ],
    "MELI": [
        "GMV 성장률", "MercadoPago TPV",
        "신용 사업 연체율", "Operating margin",
    ],
    "TSLA": [
        "자동차 마진 (Auto GPM)", "Robotaxi 진척",
        "FSD 가입자 / 가격", "Energy 매출", "Optimus 진척",
    ],
    "PYPL": [
        "Take-rate 안정화", "Branded checkout 거래량",
        "Operating margin", "Venmo 매출",
    ],
    "UBER": [
        "Mobility 거래량", "Eats Take-rate",
        "광고 매출 비중", "FCF 창출력",
    ],
    "ABNB": [
        "Bookings 거래량", "ADR 추세",
        "신규 시장 진출 속도", "광고 매출",
    ],
    "BKNG": [
        "Room nights", "Take-rate",
        "Connected trip 매출", "환율 영향",
    ],
    "SPOT": [
        "MAU 증가율", "Premium ARPU",
        "광고 / 팟캐스트 매출", "Operating margin",
    ],
    "SBUX": [
        "미국 SSS (Same Store Sales)", "중국 SSS",
        "주문 / 매장 처리 효율", "신규 메뉴 mix",
    ],
    "DIS": [
        "Disney+ 영업이익률", "박스오피스 매출",
        "테마파크 매출 추세", "DTC 가입자",
    ],
}


def core_kpis(ticker: str) -> list[str]:
    """종목별 핵심 KPI. 없으면 KEY_METRICS fallback."""
    return list(CORE_KPIS_KO.get(ticker) or KEY_METRICS.get(ticker, [
        "실적 가이던스", "주요 catalyst", "Valuation 변화",
    ]))


# ---------------------------------------------------------------------------
# 종목별 Anti-Thesis (주요 리스크) — 투자 논리가 틀릴 수 있는 구체 사유
# ---------------------------------------------------------------------------

ANTI_THESIS_KO: dict[str, list[str]] = {
    "NVDA": [
        "Hyperscaler 자체칩 (ASIC) 확대로 점유율 잠식",
        "중국 수출 규제 강화로 매출 캡 발생",
        "HBM 공급 병목 / 가격 급등",
        "AI CAPEX 사이클 둔화",
        "과도한 Valuation 기대 반영",
    ],
    "AVGO": [
        "ASIC 사업 신규 고객 부재", "VMware 통합 비용 / 이탈",
        "통신용 반도체 사이클 둔화", "고객 집중도 (구글 등 의존)",
    ],
    "AMD": [
        "AI GPU 매출 인식 지연", "EPYC 시장 경쟁 심화",
        "PC / 게이밍 회복 지연", "마진 압박",
    ],
    "MU": [
        "메모리 사이클 재고 누적 재발", "HBM 경쟁 심화 (SK하이닉스)",
        "ASP 하락 전환", "Capex 부담",
    ],
    "TSM": [
        "지정학 리스크 (중국-대만)", "AI 사이클 둔화",
        "Capex 부담", "환율 / 지정학에 따른 multiple 하향",
    ],
    "ANET": [
        "InfiniBand 의 ethernet 대체 둔화", "Hyperscaler 주문 변동",
        "Gross margin 압박", "고객 집중도",
    ],
    "CRDO": [
        "구리 기반 interconnect 에서 optical 로의 빠른 전환",
        "고객 집중도 (소수 hyperscaler 의존)",
        "Hyperscaler 주문 변동성", "경쟁 심화", "인수 시너지 불확실",
    ],
    "ALAB": [
        "PCIe / CXL 채택 지연", "고객 집중도",
        "경쟁 칩 출시", "ASP 하락",
    ],
    "CLS": [
        "Hyperscaler 주문 변동성", "EMS 본업 둔화",
        "Operating margin 압박", "고객 집중도",
    ],
    "VRT": [
        "데이터센터 capex 사이클 둔화", "Backlog 인식 지연",
        "신규 솔루션 채택 지연", "원자재 / 인건비 부담",
    ],
    "ETN": [
        "산업 수요 둔화", "Backlog 인식 지연",
        "원자재 / 인건비 부담",
    ],
    "CEG": [
        "PPA 단가 상승 정체", "원전 capacity factor 하락",
        "전력 도매가 하락", "신규 원전 승인 지연",
    ],
    "VST": [
        "천연가스 가격 변동성", "PPA 계약 단가 상승 둔화",
        "AI 신규 수요 지연",
    ],
    "CCJ": [
        "우라늄 가격 변동성", "광산 생산 차질",
        "신규 원전 승인 지연", "공급 측 (러시아 등) 변동",
    ],
    "AXON": [
        "B2G 예산 사이클 둔화", "고밸류에이션 부담",
        "로봇 / 드론 경찰 시대의 하드웨어 대체 가능성",
        "소프트웨어 전환 속도 미흡", "공공부문 정치 / 규제 리스크",
    ],
    "PLTR": [
        "상업용 매출 성장 둔화", "정부 계약 의존도",
        "고밸류에이션 부담", "AIP 채택 속도 미흡",
    ],
    "LMT": [
        "F-35 프로그램 비용 / 일정 지연", "예산 우선순위 변화",
        "마진 압박",
    ],
    "NOC": [
        "B-21 양산 지연 / 비용 초과", "정부 예산 우선순위 변화",
    ],
    "RTX": [
        "GTF 엔진 리콜 비용", "민항기 회복 지연",
        "방산 부문 마진 압박",
    ],
    "HII": [
        "함정 건조 지연", "인건비 / 자재비 부담",
        "정부 예산 우선순위 변화",
    ],
    "RKLB": [
        "Neutron 개발 지연", "발사 실패 리스크",
        "현금 소진 / 희석", "위성 사업 매출 인식 지연",
    ],
    "LUNR": [
        "미션 실패 리스크", "정부 계약 의존도",
        "현금 소진 / 희석",
    ],
    "TMDX": [
        "병원 채택 둔화", "기계당 procedure 수 정체",
        "경쟁 솔루션 등장", "보험 수가 변경",
    ],
    "ISRG": [
        "Procedure 성장 둔화", "경쟁 로봇수술 등장",
        "고밸류에이션 부담",
    ],
    "NTRA": [
        "보험 수가 인하 압박", "검사 volume 정체",
        "경쟁 검사 등장",
    ],
    "TMO": [
        "본업 회복 지연", "코로나 base 효과 재현 어려움",
        "환율 영향",
    ],
    "DHR": [
        "바이오프로세싱 회복 지연", "재고 사이클 장기화",
        "환율 영향",
    ],
    "NFLX": [
        "콘텐츠 투자 효율 저하", "광고 요금제 성장 둔화",
        "가격 인상에 따른 churn 증가",
        "경쟁 플랫폼의 스포츠 / 라이브 콘텐츠 강화",
        "FCF margin 둔화",
    ],
    "META": [
        "AI CAPEX 확대에 따른 FCF 훼손", "감가상각비 증가로 OPM 압박",
        "Reality Labs 적자 지속", "개인정보 / 광고 규제",
        "AI 투자 회수 시점 불확실",
    ],
    "AMZN": [
        "AWS 성장 둔화", "Retail margin 회복 지연",
        "AI 투자비 부담", "광고 매출 둔화",
    ],
    "MSFT": [
        "Azure 성장 둔화", "Copilot 채택 부진",
        "AI CAPEX 회수 지연",
    ],
    "GOOGL": [
        "검색 광고 매출 잠식 (AI 전환)", "Cloud 성장 둔화",
        "규제 / 분할 리스크", "AI 모델 경쟁 심화",
    ],
    "SHOP": [
        "GMV 성장 둔화", "Take-rate 압박",
        "신규 매출 카테고리 (B2B) 의 매출 인식 지연",
    ],
    "MELI": [
        "신용 사업 연체율 상승", "환율 / 매크로 변동",
        "이커머스 경쟁 심화",
    ],
    "TSLA": [
        "자동차 마진 압박 지속", "Robotaxi 사업 지연",
        "FSD 진척 미흡", "Energy 사업 변동성",
        "Optimus 사업 가치 의문",
    ],
    "PYPL": [
        "Apple Pay 등 경쟁 심화", "Take-rate 하락",
        "Branded checkout 점유율 하락",
    ],
    "UBER": [
        "Robotaxi 도입에 따른 사업 모델 변화", "Mobility 경쟁 심화",
        "광고 매출 성장 둔화",
    ],
    "ABNB": [
        "단기임대 규제 강화", "ADR 하락",
        "호텔 가격 회복으로 차이 축소",
    ],
    "BKNG": [
        "환율 영향", "Take-rate 압박",
        "Alternative accommodation 경쟁",
    ],
    "SPOT": [
        "콘텐츠 투자 효율 저하", "가격 인상 후 churn 증가",
        "팟캐스트 / 광고 매출 둔화",
    ],
    "SBUX": [
        "미국 SSS 회복 지연", "중국 사업 회복 지연",
        "신규 CEO 의 operational reset 지연",
    ],
    "DIS": [
        "DTC 영업이익률 개선 지연", "박스오피스 부진",
        "테마파크 수요 둔화",
    ],
}


def anti_thesis_specific(ticker: str) -> list[str]:
    """종목별 anti-thesis. 없으면 빈 리스트 (caller 가 fallback)."""
    return list(ANTI_THESIS_KO.get(ticker, []))


# ---------------------------------------------------------------------------
# 종목별 핵심 논쟁 (Core Debate) — 종합 판단 / news_summarizer 등이 참조
# 한 줄짜리 정수 — "이 종목의 매수 판단이 무엇으로 갈리는가"
# ---------------------------------------------------------------------------

CORE_DEBATE_KO: dict[str, str] = {
    "NVDA": "AI 인프라 점유율 지속 vs. 고객 자체칩(ASIC) 확산 + 수출 규제",
    "AVGO": "AI ASIC 신규 성장축의 매출 가시성 vs. VMware 통합 비용",
    "AMD": "MI 시리즈 데이터센터 GPU 의 NVDA 대안 채택 가속 vs. 마진 압박",
    "MU": "HBM 점유율 + 메모리 사이클 회복 vs. 재고 누적 재발",
    "TSM": "AI 사이클 수혜 + 가격결정력 vs. 지정학 리스크",
    "ANET": "AI 클러스터 ethernet 채택률 vs. NVDA InfiniBand 와의 경쟁",
    "CRDO": "AEC 점유율 + 광통신 확장 vs. optical 빠른 대체",
    "ALAB": "PCIe / CXL 표준 선도 우위 vs. 고객 집중도",
    "CLS": "AI 서버 ODM 매출 가속 vs. EMS 정체성 한계",
    "VRT": "데이터센터 전력 / 냉각 backlog vs. capex 사이클 둔화",
    "ETN": "AI / 전기화 동시 수혜 vs. 산업 사이클 변동",
    "CEG": "PPA 단가 상승 + AI 데이터센터 신규 수주 vs. 전력 도매가 변동",
    "VST": "AI 신규 수요 가시성 vs. 천연가스 가격 변동",
    "CCJ": "원전 회귀 + 우라늄 가격 상승 vs. 공급 측 변동",
    "AXON": "Public Safety OS 전환 (소프트웨어 매출 비중) vs. 고밸류 부담",
    "PLTR": "상업용 매출 가속 + AIP 채택 vs. 정부 의존도 + 고밸류",
    "LMT": "F-35 수주 / 무기 현대화 vs. 예산 우선순위 변화",
    "NOC": "B-21 양산 가시성 vs. 비용 / 일정 지연",
    "RTX": "민항기 회복 + 방산 수주 vs. GTF 엔진 리콜 비용",
    "HII": "함정 capacity 확장 사이클 vs. 인건비 / 자재비 부담",
    "RKLB": "Neutron 개발 진척 vs. 발사 실패 / 현금 소진",
    "LUNR": "NASA Artemis 수주 vs. 미션 실패 리스크",
    "TMDX": "장기이식 OCS 채택 확대 vs. procedure 정체",
    "ISRG": "다빈치 5 채택 + procedure 성장 vs. 고밸류 부담",
    "NTRA": "Signatera 수가 수렴 vs. 검사 volume 정체",
    "TMO": "본업 매출 회복 vs. 환율 / 코로나 base 효과 부재",
    "DHR": "바이오프로세싱 수주 회복 vs. 재고 사이클 장기화",
    "NFLX": (
        "Warner Bros. M&A 리스크 해소 후 광고 요금제 / FCF 정상 궤도 복귀 vs. "
        "콘텐츠 투자 효율 / churn"
    ),
    "META": (
        "AI CAPEX 의 증분 ROIC — AI 투자가 광고 노출 수 / 단가 / ROI 개선으로 "
        "회수되는가 vs. FCF 훼손과 multiple 압박"
    ),
    "AMZN": "AWS AI 가속 + retail margin 회복 vs. AI 투자 부담",
    "MSFT": "Azure AI 매출 가속 vs. CAPEX 회수 속도",
    "GOOGL": "Cloud 흑자 전환 + Gemini vs. 검색 광고 AI 잠식",
    "SHOP": "GMV 성장 + Subscription 매출 vs. take-rate 압박",
    "MELI": "라틴아메리카 e-commerce + 핀테크 가속 vs. 신용 사업 연체율",
    "TSLA": "자동차 마진 회복 vs. Robotaxi / FSD / Optimus 옵션 가치 검증",
    "PYPL": "Take-rate 안정화 + Branded checkout 회복 vs. Apple Pay 경쟁",
    "UBER": "FCF 가속 + 광고 매출 비중 상승 vs. Robotaxi 사업 모델 변화",
    "ABNB": "ADR 안정 + 신규 시장 vs. 단기임대 규제 강화",
    "BKNG": "Take-rate 안정 + connected trip 확장 vs. 환율 / 경쟁",
    "SPOT": "가격 인상 후 churn 안정 + 광고 매출 vs. 콘텐츠 투자 효율",
    "SBUX": "신규 CEO operational reset + 미국 SSS 회복 vs. 중국 부진",
    "DIS": "Disney+ OPM 개선 + 박스오피스 회복 vs. 콘텐츠 투자 효율",
}


def core_debate(ticker: str) -> str | None:
    return CORE_DEBATE_KO.get(ticker)


# ---------------------------------------------------------------------------
# 종목별 가치평가 컨텍스트 — Forward PER 등 multiple 해석을 종목별 논쟁과 연결
# ---------------------------------------------------------------------------

VALUATION_CONTEXT_KO: dict[str, str] = {
    "META": (
        "Meta 는 Forward PER 기준 대형 플랫폼 평균 대비 부담이 낮아 보일 수 있으나, "
        "이는 AI CAPEX 확대에 따른 FCF 둔화와 증분 ROIC 불확실성이 반영된 결과로 해석할 수 "
        "있습니다. 단순 저평가로 보기보다는, 광고 매출 성장과 FCF 방어가 확인될 경우 "
        "multiple 회복 여지가 있는 Quality Dislocation 구간으로 보는 것이 적절합니다."
    ),
    "NFLX": (
        "Netflix 는 글로벌 OTT 카테고리 리더로서 Forward PER 프리미엄이 형성돼 있습니다. "
        "Warner Bros. 관련 M&A 리스크 해소 이후 multiple 의 핵심 변수는 광고 요금제 매출 "
        "성장률과 FCF margin 확장입니다. 콘텐츠 투자비 대비 가입자 / 시청 효율이 유지된다면 "
        "현재 multiple 이 정당화됩니다."
    ),
    "NVDA": (
        "NVDA 는 데이터센터 매출 가속에 따라 Forward PER 가 빠르게 정상화되는 구간입니다. "
        "Multiple 부담보다는 'AI 사이클이 몇 분기 더 갈 수 있는가' 와 '자체칩 잠식 속도' 가 "
        "valuation 의 핵심 변수입니다."
    ),
    "AXON": (
        "Axon 은 소프트웨어 전환 thesis 에 의해 SaaS 멀티플이 부여되는 구간으로, EV/Revenue "
        "기준 부담이 큽니다. 소프트웨어 매출 비중과 Net retention 이 multiple 정당화의 핵심 "
        "변수이며, 하드웨어 매출 둔화가 동반되면 derating 위험이 커집니다."
    ),
    "CRDO": (
        "Credo 는 AI 인터커넥트 카테고리 진입 구간으로, EV/Revenue 기준 high growth multiple "
        "이 부여돼 있습니다. AEC + 광통신 매출 가속이 확인되면 multiple 정당화가 가능하나, "
        "고객 집중도가 높아 발주 변동성이 valuation 의 핵심 변수입니다."
    ),
    "PLTR": (
        "Palantir 는 정부 + AIP 결합 가속에 SaaS / AI 멀티플이 동시 적용되는 구조로, "
        "Forward EV/Revenue 가 매우 높습니다. 상업용 매출 성장 가속이 확인되지 않으면 multiple "
        "재평가 압박이 클 수 있습니다."
    ),
    "VST": (
        "Vistra 는 텍사스 발전 사업의 안정 cash flow + AI 신규 수요 옵션이 결합된 valuation "
        "구조입니다. AI PPA 신규 수주가 확인될 때마다 multiple 이 한 단계씩 재평가되는 "
        "구간입니다."
    ),
    "CEG": (
        "Constellation Energy 는 무탄소 전력 + 데이터센터 PPA 수혜로 utility 평균 대비 "
        "multiple 이 높게 형성돼 있습니다. PPA 단가 상승 / 신규 capacity 가시성이 multiple "
        "정당화의 핵심 변수입니다."
    ),
    "TSLA": (
        "Tesla 는 자동차 사업 P/E 와 자율주행 / Energy / Optimus 옵션 가치가 합쳐진 sum-of-the-"
        "parts multiple 구조입니다. 자동차 마진 회복 vs. Robotaxi 진척이 multiple 의 양 갈래 "
        "변수입니다."
    ),
}


def valuation_context(ticker: str) -> str | None:
    return VALUATION_CONTEXT_KO.get(ticker)


# ---------------------------------------------------------------------------
# 종목별 재무 컨텍스트 — OPM / FCF / CAPEX 같은 종목별 점검 포인트
# ---------------------------------------------------------------------------

FINANCIAL_CONTEXT_KO: dict[str, str] = {
    "META": (
        "Meta 는 Family of Apps 광고 사업이 40%대 영업이익률을 유지하며 강한 영업레버리지를 "
        "보여주고 있습니다. 다만 향후 AI CAPEX 확대가 감가상각비와 FCF 에 미치는 영향이 커질 "
        "수 있어, 매출 성장률뿐 아니라 FCF margin 과 OPM 방어 여부를 함께 점검해야 합니다."
    ),
    "NFLX": (
        "Netflix 는 콘텐츠 투자비 대비 매출 / 가입자 효율이 본업의 핵심 지표이며, FCF 가 "
        "정상 궤도로 진입한 이후 자사주 매입 여력이 multiple 의 한 축입니다. 광고 요금제 "
        "매출 성장 속도가 FCF margin 확장 여부의 핵심 변수입니다."
    ),
    "NVDA": (
        "NVDA 는 데이터센터 매출이 전체 매출의 절대 비중을 차지하며, gross margin 75% 수준의 "
        "강한 수익성을 유지 중입니다. 자체칩 / 수출 규제 영향이 본격화되면 마진 압박 가능성 "
        "이 핵심 변수입니다."
    ),
    "AXON": (
        "Axon 은 하드웨어(테이저 / 카메라) 와 소프트웨어(Evidence Cloud) 매출 비중 변화가 "
        "OPM 에 직접 영향을 줍니다. 소프트웨어 매출 비중 상승이 마진 확장의 핵심 변수입니다."
    ),
    "CRDO": (
        "Credo 는 매출 성장이 빠른 high-growth 단계로, gross margin 추세와 R&D 비중 변화가 "
        "수익성 가시성의 핵심입니다. 광통신 인수 통합 비용 / 시너지 인식 시점이 OPM 변동 "
        "요인입니다."
    ),
    "VST": (
        "Vistra 는 발전 사업의 EBITDA 가 핵심 수익 지표이며, AI PPA 단가 상승이 EBITDA 가시성 "
        "확장의 핵심 변수입니다. 천연가스 가격 변동 영향도 함께 봐야 합니다."
    ),
    "CEG": (
        "Constellation Energy 는 PPA 단가 + capacity factor 가 EBITDA 의 두 축입니다. AI "
        "데이터센터 PPA 수주 시점에 따라 EBITDA 가시성이 단계적으로 확장됩니다."
    ),
    "PLTR": (
        "Palantir 는 매출 성장 + Operating margin 확장이 동시에 진행되는 구간으로, Rule of 40 "
        "기준이 Forward 멀티플 정당화의 기준점입니다. 상업용 매출 성장 가속과 마진 확장이 "
        "동반돼야 multiple 유지가 가능합니다."
    ),
}


def financial_context(ticker: str) -> str | None:
    return FINANCIAL_CONTEXT_KO.get(ticker)


# ---------------------------------------------------------------------------
# 종목별 종합 판단 (Final View) — 매수 / 보유 / 관망 판단의 중심 논리
# ---------------------------------------------------------------------------

FINAL_VIEW_KO: dict[str, str] = {
    "META": (
        "현 시점에서 Meta 는 단순 성장주가 아니라 AI CAPEX 의 증분 ROIC 를 검증해야 하는 "
        "Quality Dislocation 후보로 분류됩니다. 본업 광고 플랫폼의 매출 성장과 영업이익률은 "
        "여전히 견조하지만, 시장은 2026년 CAPEX 가이던스 상향이 FCF 와 valuation 에 미칠 "
        "영향을 우려하고 있습니다. 따라서 매수 판단의 핵심은 AI 투자가 광고 노출 수 / 광고 단가 "
        "/ 광고주 ROI 개선으로 이어져 FCF 를 방어할 수 있는지 여부입니다. 이 증거가 확인되면 "
        "현재 주가 조정은 기회가 될 수 있지만, CAPEX 증가가 이익률과 현금흐름을 구조적으로 "
        "훼손한다면 단순 과매도로 보기 어렵습니다."
    ),
    "NFLX": (
        "현 시점에서 Netflix 는 Warner Bros. 관련 대형 M&A 리스크가 해소된 이후 투자 초점이 "
        "다시 본업으로 이동한 Quality Dislocation 후보입니다. 매수 판단의 핵심은 "
        "(1) 광고 요금제 매출 성장률, (2) FCF margin 확장, (3) 콘텐츠 투자비 대비 가입자 / 시청 "
        "효율 유지 여부입니다. 본업 지표가 견조하게 확인되면 multiple 회복 여지가 큽니다."
    ),
    "NVDA": (
        "현 시점에서 NVDA 는 단순 momentum 추격이 아니라 'AI 사이클 지속 가능성' 과 '자체칩 / "
        "수출 규제 잠식' 의 균형으로 보는 단계입니다. 데이터센터 매출 가시성과 hyperscaler "
        "CAPEX revision 이 매수 판단의 핵심이며, hyperscaler 자체칩 확대 속도가 가장 큰 "
        "anti-thesis 입니다."
    ),
    "AXON": (
        "Axon 은 Public Safety OS 로의 전환 (하드웨어 → 소프트웨어 + AI) 이 핵심 thesis 인 "
        "구간입니다. 매수 판단의 핵심은 소프트웨어 매출 비중과 Evidence Cloud Net retention "
        "이며, B2G 예산 사이클과 고밸류 부담이 anti-thesis 의 두 축입니다."
    ),
    "CRDO": (
        "Credo 는 AI 인터커넥트 카테고리에서 AEC + 광통신 확장 가능성을 검증해야 하는 high-"
        "growth 단계입니다. 매수 판단의 핵심은 hyperscaler 매출 비중 변화와 광통신 인수 시너지 "
        "인식 시점이며, 고객 집중도가 가장 큰 anti-thesis 입니다."
    ),
    "PLTR": (
        "Palantir 는 정부 부문 안정성 + 상업용 AIP 채택 가속이 결합된 high-growth 단계로, "
        "매수 판단의 핵심은 상업용 매출 성장률과 Operating margin 확장 동시 달성 여부입니다. "
        "고밸류 부담이 가장 큰 anti-thesis 입니다."
    ),
    "VST": (
        "Vistra 는 텍사스 발전 사업의 안정 cash flow 위에 AI 신규 수요 옵션이 결합된 종목입니다. "
        "매수 판단의 핵심은 AI 데이터센터 PPA 신규 수주이며, 천연가스 가격 변동성이 "
        "anti-thesis 의 한 축입니다."
    ),
    "CEG": (
        "Constellation Energy 는 무탄소 전력 + AI 데이터센터 PPA 수혜의 직접 수혜 종목으로, "
        "매수 판단의 핵심은 PPA 단가 상승 추세와 신규 capacity 가시성입니다."
    ),
    "TSLA": (
        "Tesla 는 자동차 본업 마진과 자율주행 / Energy / Optimus 옵션의 균형이 핵심인 종목입니다. "
        "매수 판단의 핵심은 자동차 마진 회복과 Robotaxi 진척의 가시성이며, 한쪽이 약화되면 "
        "multiple 재평가 압박이 즉시 발생합니다."
    ),
}


def final_view_curated(ticker: str) -> str | None:
    return FINAL_VIEW_KO.get(ticker)


# ---------------------------------------------------------------------------
# 분류 라벨
# ---------------------------------------------------------------------------

EVENT_CLASSIFICATION = {
    "strengthen": "Thesis 강화",
    "weaken": "Thesis 약화",
    "new_risk": "신규 리스크",
    "noise": "단기 노이즈",
    "needs_check": "확인 필요",
}


def event_classification_label(key: str) -> str:
    return EVENT_CLASSIFICATION.get(key, "확인 필요")


# ---------------------------------------------------------------------------
# 이 회사는 쉽게 말해
# ---------------------------------------------------------------------------

SIMPLE_EXPLANATION: dict[str, str] = {
    "CRDO": (
        "크리도는 데이터센터 안에서 서버와 서버를 빠르게 연결해주는 고속 연결 솔루션을 만드는 회사입니다. "
        "쉽게 말하면 AI 데이터센터 내부에서 데이터가 막히지 않도록 \"고속 통로\"를 깔아주는 역할을 합니다. "
        "기존에는 구리 기반 연결이 핵심이었으나, 최근 광통신 관련 회사를 인수하면서 광 기반 연결 영역까지 "
        "확장하려는 움직임을 보이고 있습니다. 데이터센터 연결이 구리에서 광으로 빠르게 넘어가면 "
        "기존 thesis가 약해질 수 있다는 우려와, 광통신 확장성으로 오히려 thesis가 넓어진다는 해석이 공존하는 구간입니다."
    ),
    "AXON": (
        "액손은 경찰이 사용하는 테이저와 바디캠으로 알려진 회사지만, 투자 포인트는 단순 장비 판매에 있지 않습니다. "
        "이 회사는 경찰이 현장에서 확보한 영상, 증거, 사건 기록을 클라우드로 관리하고 AI로 보고서 작성까지 "
        "지원하는 공공안전 운영체제로 확장하고 있습니다. 즉, 장비 회사에서 공공안전 데이터 플랫폼으로 "
        "재평가될 수 있는지가 핵심입니다."
    ),
    "TMDX": (
        "트랜스메딕스는 장기이식 과정에서 기증 장기를 더 오래, 더 안전하게 보존하고 운송할 수 있도록 돕는 회사입니다. "
        "장기이식의 가장 큰 병목은 기증 장기 부족뿐 아니라, 장기를 사용할 수 있는 시간과 운송 가능 거리의 제약입니다. "
        "이 회사는 장기 보존 장비(OCS)와 물류 네트워크를 결합해 이식 가능한 장기의 활용률을 높이려는 회사로 "
        "이해할 수 있습니다."
    ),
    "NFLX": (
        "넷플릭스는 단순한 스트리밍 서비스가 아니라 글로벌 콘텐츠 유통 플랫폼입니다. "
        "최근 시장에서 거론되었던 Warner Bros. 인수전은 추가 제안 없이 종료된 것으로 파악되며, 이에 따라 "
        "대규모 인수에 따른 부채 확대·통합·규제 리스크는 단기 투자 논리에서 상당 부분 해소된 이벤트로 "
        "분류됩니다. 투자 판단의 초점은 다시 글로벌 가입자 성장, 광고 요금제 확장, 콘텐츠 IP 효율, "
        "FCF 창출력, 자사주 매입 여력으로 이동하는 단계로 정리됩니다."
    ),
    "PLTR": (
        "팔란티어는 국방·정보기관·대기업이 흩어져 있는 데이터를 한곳에 모아 의사결정에 쓸 수 있도록 만들어주는 "
        "데이터 통합·운영 플랫폼 회사입니다. 최근에는 자체 LLM 기반 AIP 플랫폼으로 민간 기업 도입이 확장되며 "
        "성장률이 가속되고 있습니다. 다만 매출 가속에 비해 주가 반응이 더 빨라 multiple 부담이 확대된 구간으로, "
        "Momentum과 Valuation의 정합성 점검이 핵심입니다."
    ),
    "NVDA": (
        "엔비디아는 AI 학습/추론에 필수적인 GPU와 그 주변 소프트웨어 스택을 사실상 독점하고 있는 회사입니다. "
        "쉽게 말하면 AI 시대의 \"발전소 중 하나\"이며, 빅테크 capex의 가장 큰 수혜자로 자리잡고 있습니다. "
        "투자 논리의 핵심은 hyperscaler capex revision 방향성과 ASIC 대체 위협의 균형입니다."
    ),
    "AVGO": (
        "브로드컴은 통신용 반도체와 인프라 소프트웨어를 동시에 보유한 카테고리 리더로, 최근에는 hyperscaler "
        "맞춤형 ASIC 사업이 새로운 성장축으로 부각되고 있습니다. AI 인프라 사이클 안에서 NVDA의 GPU와 함께 "
        "또 다른 핵심 부품 공급자로 자리잡고 있습니다."
    ),
    "AMD": (
        "AMD는 CPU와 GPU를 모두 만드는 종합 반도체 회사로, 최근에는 MI 시리즈 데이터센터 GPU로 NVDA의 "
        "독주에 균열을 내려는 챌린저 위치에 있습니다. 데이터센터 매출 비중과 GPU 채택 가속 여부가 thesis의 핵심입니다."
    ),
    "MU": (
        "마이크론은 AI 학습에 필수적인 HBM(High Bandwidth Memory) 메모리를 만드는 회사입니다. AI 데이터센터의 "
        "메모리 병목이 부각될 때 가장 직접적인 수혜를 받는 카테고리 리더 중 하나입니다."
    ),
    "TSM": (
        "TSMC는 전 세계 첨단 로직 반도체 위탁 생산을 사실상 독점하고 있는 파운드리 회사입니다. NVDA·AMD·AAPL 등 "
        "거의 모든 첨단 칩이 이 회사의 팹에서 만들어지며, AI 사이클의 가장 깊은 곳에 위치한 인프라 기업입니다."
    ),
    "ANET": (
        "아리스타 네트웍스는 데이터센터 네트워크 장비의 카테고리 리더로, 최근에는 AI 클러스터에서 GPU 간 통신을 "
        "위한 고속 이더넷 스위치 수요가 급증하면서 핵심 수혜주로 부각되고 있습니다."
    ),
    "ALAB": (
        "아스테라 랩스는 AI 데이터센터 내 CPU·GPU·메모리 간 고속 연결을 담당하는 \"커넥티비티 칩\"을 만드는 회사입니다. "
        "PCIe·CXL 등 차세대 인터커넥트 표준을 선도하며 AI 인프라 호황의 직접 수혜군에 속합니다."
    ),
    "CLS": (
        "셀레스티카는 hyperscaler 향 데이터센터 서버·네트워크 하드웨어를 위탁 생산하는 회사로, AI 인프라 capex가 "
        "확대되는 구간에서 기존 EMS 사업 가치가 재평가되고 있습니다."
    ),
    "VRT": (
        "버티브는 데이터센터에 필요한 전력·냉각·인프라 장비를 공급하는 회사입니다. AI 시대 데이터센터의 전력 밀도가 "
        "급증하면서 액침냉각 등 신규 솔루션 수요가 빠르게 늘고 있습니다."
    ),
    "CEG": (
        "컨스텔레이션 에너지는 미국 최대 규모의 원자력 발전소 운영사입니다. AI 데이터센터의 전력 수요가 급증하면서 "
        "안정적이고 무탄소 전력을 공급할 수 있는 원자력 발전 가치가 재평가되는 구간에 있습니다."
    ),
    "VST": (
        "비스트라는 가스·원자력·배터리 등 다양한 전원을 운영하는 미국 발전 사업자입니다. AI 데이터센터 PPA 체결과 "
        "전력 가격 상승의 동반 수혜 가능성이 핵심 thesis입니다."
    ),
    "CCJ": (
        "카메코는 글로벌 우라늄 채굴·정련을 담당하는 카테고리 리더입니다. 원전 르네상스와 AI 데이터센터 전력 수요 "
        "확대로 우라늄 가격과 장기 계약 흐름이 thesis의 핵심 변수입니다."
    ),
    "LMT": (
        "록히드마틴은 미국 방산의 핵심 종합 컨트랙터입니다. F-35, 미사일 방어, 우주 시스템 등 다수의 장기 backlog 사업을 "
        "보유하며, 지정학 리스크 확대 구간에서 안정적 수혜군에 속합니다."
    ),
    "NOC": (
        "노스롭그루먼은 차세대 ICBM(Sentinel)과 B-21 폭격기, 우주 시스템을 담당하는 미국 핵심 방산기업으로, "
        "전략 자산 현대화 사이클의 가장 큰 수혜자 중 하나입니다."
    ),
    "RTX": (
        "RTX는 항공·방산·우주를 아우르는 종합 그룹으로, 미사일 방어와 항공기 엔진(P&W) 사업이 양대 축입니다. "
        "지정학 리스크와 항공 수요 회복의 동시 수혜를 기대할 수 있는 구조입니다."
    ),
    "HII": (
        "헌팅턴 잉걸스는 미국 해군의 항공모함과 잠수함을 건조하는 사실상 유일한 조선소를 보유한 회사로, "
        "함정 backlog와 해군 예산이 핵심 변수입니다."
    ),
    "RKLB": (
        "로켓랩은 소형 위성 발사부터 위성 시스템 제조까지 수직 통합을 추진하는 차세대 우주 기업입니다. "
        "Neutron 로켓 개발 진척과 시스템 사업 매출 비중이 thesis 변수입니다."
    ),
    "LUNR": (
        "인튜이티브 머신스는 NASA의 달 착륙선 프로그램을 수행하는 우주 스타트업입니다. CLPS 등 정부 task order "
        "수주와 운송 데이터 사업의 확장이 thesis의 핵심입니다."
    ),
    "ISRG": (
        "인튜이티브 서지컬은 다빈치 로봇 수술 시스템을 사실상 독점하고 있는 회사로, 시술 건수가 누적될수록 "
        "소모품·서비스 매출이 함께 확장되는 razor-and-blade 구조의 quality compounder입니다."
    ),
    "NTRA": (
        "내터라는 임신·산전 검사와 암 모니터링용 유전자 분석 검사를 제공하는 진단 기업입니다. "
        "Signatera 등 암 재발 모니터링 검사의 채택률 확장이 thesis의 핵심 변수입니다."
    ),
    "TMO": (
        "써모 피셔는 생명과학 연구·임상·진단 전반에 필수적인 장비와 소모품을 공급하는 카테고리 리더입니다. "
        "팬데믹 후 정상화 구간에서 베이스 매출의 재가속 여부가 thesis의 변수입니다."
    ),
    "DHR": (
        "다나허는 생명과학·진단·바이오프로세싱 분야의 카테고리 리더 포트폴리오를 보유한 quality compounder입니다. "
        "바이오프로세싱 회복 사이클이 thesis의 핵심 변수입니다."
    ),
    "META": (
        "메타는 페이스북·인스타그램·왓츠앱 등 글로벌 사용자 기반을 보유한 광고 플랫폼이자, 동시에 AI와 XR에 "
        "대규모 capex를 집행 중인 빅테크입니다. AI 추천 알고리즘이 광고 효율을 끌어올리는 구간으로, 추정치 상향 "
        "사이클의 핵심 수혜군 중 하나입니다."
    ),
    "MSFT": (
        "마이크로소프트는 OS·오피스·클라우드(Azure)를 모두 보유한 글로벌 IT 인프라 기업입니다. OpenAI 협력을 "
        "기반으로 한 AI 기능 통합이 ARR 가속을 견인하는지가 thesis의 핵심입니다."
    ),
    "GOOGL": (
        "알파벳은 검색·유튜브·클라우드(GCP)·자율주행(Waymo) 등 다층 사업을 보유한 빅테크입니다. AI 검색 패러다임 "
        "전환 속에서 검색 광고 사업의 방어력과 Gemini·Cloud 가속이 thesis의 핵심입니다."
    ),
    "AMZN": (
        "아마존은 글로벌 이커머스와 클라우드(AWS), 광고를 모두 보유한 플랫폼 복합체입니다. AWS의 AI 워크로드 가속과 "
        "광고 부문의 수익성 확대가 thesis의 핵심 동력입니다."
    ),
    "TSLA": (
        "테슬라는 EV 제조사로 출발했지만, 투자 논리의 핵심은 자율주행·로보택시·에너지 사업의 성공 여부에 있습니다. "
        "최근에는 robotaxi 진척도와 자동차 마진 회복이 동시에 핵심 변수가 되고 있습니다."
    ),
    "PYPL": (
        "페이팔은 글로벌 디지털 결제 플랫폼이지만 최근 성장률이 둔화되며 turnaround 후보로 분류되는 구간입니다. "
        "ARPU 회복과 결제 마진 개선이 thesis의 핵심 변수입니다."
    ),
    "SBUX": (
        "스타벅스는 글로벌 프리미엄 커피 체인 카테고리 리더입니다. 미국·중국 동일점포 매출과 트래픽 회복이 thesis의 "
        "단기 핵심 변수입니다."
    ),
    "DIS": (
        "디즈니는 미디어·테마파크·스트리밍을 모두 보유한 글로벌 IP 기업입니다. DTC(Direct-to-Consumer) 부문 "
        "흑자 전환과 테마파크 마진 안정성이 thesis의 핵심 변수입니다."
    ),
    "SHOP": (
        "쇼피파이는 중소·중견 기업이 온라인 매장을 빠르게 만들 수 있도록 돕는 이커머스 인프라 플랫폼입니다. "
        "GMV 성장률과 결제·광고 등 부가 매출 비중이 thesis의 핵심 변수입니다."
    ),
    "MELI": (
        "메르카도리브레는 라틴아메리카 이커머스·결제·물류를 모두 운영하는 카테고리 리더입니다. 결제 사업 ARPU와 "
        "물류 네트워크 효율 확대가 thesis의 핵심 변수입니다."
    ),
    "UBER": (
        "우버는 글로벌 모빌리티(승차 호출)와 음식 배달, 광고를 운영하는 플랫폼 기업입니다. 최근에는 자율주행 "
        "robotaxi 파트너십을 통해 mobility 플랫폼으로서의 가치가 재조명되는 구간입니다."
    ),
    "ABNB": (
        "에어비앤비는 글로벌 단기 숙박 카테고리 리더입니다. 여행 수요 정상화 구간에서 숙박 단가(ADR)와 booking 추세가 "
        "thesis의 핵심 변수입니다."
    ),
    "BKNG": (
        "부킹홀딩스는 글로벌 OTA(Online Travel Agency) 카테고리 리더로, 호텔 예약·항공·렌터카 등 여행 전 영역을 "
        "커버합니다. 여행 수요 안정성과 광고 효율이 thesis의 핵심 변수입니다."
    ),
    "SPOT": (
        "스포티파이는 글로벌 음악 스트리밍 카테고리 리더로, 최근에는 단가 인상과 광고 사업 확장으로 마진 개선 "
        "사이클에 진입한 구간입니다."
    ),
    "ETN": (
        "이튼은 산업용 전력관리 솔루션의 글로벌 카테고리 리더입니다. AI 데이터센터·전력 인프라·전기화 사이클의 "
        "동시 수혜군에 속하는 구조적 성장 후보입니다."
    ),
}


def simple_explanation(ticker: str, fallback_theme_label: str | None = None) -> str | None:
    text = SIMPLE_EXPLANATION.get(ticker)
    if text:
        return text
    if fallback_theme_label:
        return (
            f"이 종목은 {fallback_theme_label} 카테고리에 속한 회사입니다. "
            "세부 사업 구조와 최근 이벤트는 추가 1차 자료 확인이 필요합니다."
        )
    return None


# ---------------------------------------------------------------------------
# 최근 주요 이벤트 (선별 큐레이션)
# ---------------------------------------------------------------------------

# 각 이벤트: date, type, summary, impact, check, classification
RECENT_EVENTS: dict[str, list[dict[str, Any]]] = {
    "CRDO": [
        {
            "date": "2026.04",
            "type": "M&A / 광통신 밸류체인 확장",
            "summary": (
                "크리도는 최근 광통신 관련 기업을 인수하며 AI 데이터센터 인터커넥트와 광통신 밸류체인 내 "
                "포지셔닝을 강화했습니다."
            ),
            "impact": (
                "기존 Active Electrical Cable 및 DSP 중심의 AI 인터커넥트 thesis가 광통신 영역으로 확장될 수 있으며, "
                "장기적으로 데이터센터 내 고속 연결 병목 수혜 폭이 확대될 가능성이 있습니다."
            ),
            "check": (
                "인수 대상의 기술력 / 매출 기여 시점 / 마진 영향 / 기존 제품군과의 시너지 / 고객사 확장 가능성"
            ),
            "classification": "strengthen",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Company Press Release", "Reuters"],
        }
    ],
    "NFLX": [
        {
            "date": "2026.02",
            "type": "M&A 이벤트 종료 / Warner Bros. 인수전 철수",
            "summary": (
                "넷플릭스는 Warner Bros. 인수전에서 추가 제안을 하지 않고 철수한 것으로 파악됩니다. "
                "Paramount 측 거래가 별도로 진행된 것으로 보도되었습니다."
            ),
            "impact": (
                "대규모 M&A에 따른 pro forma leverage 확대, 통합 리스크, 규제 리스크는 단기 투자 논리에서 "
                "상당 부분 해소된 이벤트로 분류됩니다. 투자 판단은 다시 본업의 가입자 성장, 광고 요금제 확장, "
                "콘텐츠 투자 효율, FCF 창출력, 자사주 매입 여력으로 이동해야 합니다. 과거 인수 우려로 주가가 "
                "조정되었다면, 해당 우려가 해소된 이후 Valuation 회복 가능성을 점검할 필요가 있습니다."
            ),
            "check": (
                "최신 M&A 관련 공시 / 자사주 매입 정책 / 광고 요금제 성장률 / FCF 가이던스 / 콘텐츠 투자비 효율"
            ),
            "classification": "strengthen",
            "status": "종료",
            "last_updated": "2026.05.01",
            "confidence": "High",
            "sources": ["Reuters", "CNBC", "Company Release"],
        }
    ],
    "AXON": [
        {
            "date": "2026.04",
            "type": "Public Safety OS 전환",
            "summary": (
                "테이저·바디캠 중심의 하드웨어 매출에서 Evidence Cloud, AI 리포팅 등 소프트웨어 매출 비중이 "
                "확대되는 구간에 진입했습니다."
            ),
            "impact": (
                "ARR 가속이 동반될 경우 장비 회사에서 공공안전 데이터 플랫폼으로 Re-rating될 여지가 있습니다."
            ),
            "check": (
                "소프트웨어 매출 비중 / ARR 성장률 / AI 모듈 attach rate / Evidence Cloud Lock-in"
            ),
            "classification": "strengthen",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Company IR", "Bloomberg"],
        }
    ],
    "TMDX": [
        {
            "date": "2026.03",
            "type": "장기 대체 기술 리스크 부각",
            "summary": (
                "xenotransplantation 등 장기 대체 기술 진척도가 부각되며 OCS 보존 솔루션의 장기 thesis 점검 필요성이 "
                "확대된 구간입니다."
            ),
            "impact": (
                "단기 OCS 볼륨 성장은 유지되나, 장기 thesis는 장기 대체 기술의 상용화 시점에 따라 재평가될 수 있습니다."
            ),
            "check": (
                "OCS 볼륨 성장률 / 장기 대체 기술 임상 진척 / 보험 reimbursement 환경"
            ),
            "classification": "needs_check",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Reuters", "STAT News"],
        }
    ],
    "PLTR": [
        {
            "date": "2026.04",
            "type": "AIP 채택 가속 / Valuation 부담",
            "summary": (
                "민간 기업 AIP 도입 가속으로 매출 성장률은 견조하나, 주가 반응이 더 빨라 forward multiple 부담이 "
                "확대된 구간에 위치합니다."
            ),
            "impact": (
                "Momentum이 유효하나 expectation이 높은 단계로, 추정치 추가 상향이 동반되지 않으면 multiple 압축 "
                "가능성이 있습니다."
            ),
            "check": (
                "AIP 신규 고객 수 / 매출 가속 지속성 / 정부 매출 mix"
            ),
            "classification": "needs_check",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Company IR", "WSJ"],
        }
    ],
    "CEG": [
        {
            "date": "2026.04",
            "type": "AI 데이터센터 PPA 확대",
            "summary": (
                "hyperscaler와의 원자력 PPA 체결 흐름이 가시화되며 안정 무탄소 전력 가치가 재평가되는 구간입니다."
            ),
            "impact": (
                "PPA 단가와 capacity factor 개선이 동반될 경우 thesis 강화 가능성이 있습니다."
            ),
            "check": (
                "PPA 체결 단가 / 신규 capacity 확장 진척 / regulatory tailwind"
            ),
            "classification": "strengthen",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Reuters", "Bloomberg"],
        }
    ],
    "VST": [
        {
            "date": "2026.04",
            "type": "전력 가격 / PPA 모멘텀",
            "summary": (
                "AI 데이터센터 전력 수요와 PPA 체결 흐름이 동시에 우호적으로 작용하는 구간입니다."
            ),
            "impact": (
                "전력 가격과 capacity factor 개선의 동시 수혜 가능성이 확대됩니다."
            ),
            "check": (
                "PPA 단가 / 전력 가격 추세 / 가스·원자력 capacity mix"
            ),
            "classification": "strengthen",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "Medium",
            "sources": ["Bloomberg", "Reuters"],
        }
    ],
    "NVDA": [
        {
            "date": "2026.04",
            "type": "Hyperscaler capex revision",
            "summary": (
                "주요 hyperscaler들의 capex 가이던스가 상향 추세를 유지하며 GPU 수요의 장기 가시성이 확보된 구간입니다."
            ),
            "impact": (
                "데이터센터 매출 growth visibility가 유지되며 카테고리 리더 프리미엄이 강화될 여지가 있습니다."
            ),
            "check": (
                "차세대 GPU(Rubin Roadmap) cadence / ASIC 대체 위협 / 중국 매출 비중 변화"
            ),
            "classification": "strengthen",
            "status": "진행 중",
            "last_updated": "2026.05.01",
            "confidence": "High",
            "sources": ["Reuters", "Bloomberg", "Company IR"],
        }
    ],
}


def recent_events(ticker: str) -> list[dict[str, Any]]:
    return list(RECENT_EVENTS.get(ticker, []))


# ---------------------------------------------------------------------------
# 매크로·정책·지정학 이슈 (정적 큐레이션)
# ---------------------------------------------------------------------------

MACRO_ISSUES: list[dict[str, str]] = [
    {
        "title": "미국 10년물 금리 반등",
        "category": "금리 / 채권 / 매크로",
        "impact": "고멀티플 성장주 할인율 부담 확대.",
        "sectors": "AI 인프라, 소프트웨어, 장기 성장주",
        "interpretation": (
            "Momentum이 강한 종목이라도 실적 추정치 상향이 동반되지 않으면 Multiple 압축 가능성이 존재합니다. "
            "단기적으로는 quality compounder와 valuation 합리적 구간을 우선 점검하는 접근이 유효합니다."
        ),
    },
    {
        "title": "이란·중동 지정학 리스크 재부각",
        "category": "지정학 / 에너지",
        "impact": "유가 및 해운 운임 상승 압력, 방산·LNG·에너지 안보 관련 종목 재평가 가능성.",
        "sectors": "에너지, LNG, 방산, 해운, 사이버보안",
        "interpretation": (
            "단기적으로는 유가 민감 섹터와 방산주에 수급이 유입될 수 있으나, 이미 반영된 종목은 Valuation 부담을 "
            "함께 점검할 필요가 있습니다."
        ),
    },
    {
        "title": "미·중 반도체 수출 규제 / 무역 긴장",
        "category": "정책 / 반도체",
        "impact": "첨단 반도체·장비 수출 통제 강화로 Greater China 매출 비중이 큰 종목의 short-term overhang 가능성.",
        "sectors": "반도체, 반도체 장비, AI 인프라",
        "interpretation": (
            "수출 통제 영향이 제한적인 종목과 미국·동맹국 매출 비중이 큰 종목 중심의 선별이 필요합니다. "
            "관세·제재 헤드라인에 의한 단기 변동성과 구조적 매출 영향을 분리 판단하는 접근이 유효합니다."
        ),
    },
]


def macro_issues() -> list[dict[str, str]]:
    return list(MACRO_ISSUES)


# ---------------------------------------------------------------------------
# 종목별 장기 주가 흐름 해석 (큐레이션)
# ---------------------------------------------------------------------------

PRICE_INTERPRETATION: dict[str, str] = {
    "CRDO": (
        "장기 주가 흐름상 동사는 AI 데이터센터 인터커넥트 투자 사이클과 함께 Re-rating이 진행된 구간이 있습니다. "
        "다만 최근 주가 조정 구간에서는 광통신 전환 우려와 Valuation 부담이 함께 반영된 것으로 보이며, "
        "신규 M&A 이벤트가 기존 투자 논리를 강화하는지 확인할 필요가 있습니다."
    ),
    "AXON": (
        "장기 주가 흐름상 동사는 하드웨어 매출 베이스 위에 소프트웨어/클라우드 매출 비중이 확대되며 "
        "구조적 Re-rating이 진행된 종목으로 평가됩니다. 다만 최근 구간은 expectation이 높아진 단계로, "
        "AI 리포팅 유료화와 ARR 가속의 정합성 점검이 필요합니다."
    ),
    "TMDX": (
        "장기 주가 흐름상 동사는 OCS 기반 장기 보존 솔루션의 빠른 채택과 함께 고성장 사이클을 누렸으나, "
        "최근 구간은 장기 대체 기술 우려로 변동성이 확대된 단계입니다. 단기 주가 변동성과 구조적 thesis "
        "유효성 점검을 분리해서 볼 필요가 있습니다."
    ),
    "NFLX": (
        "장기 주가 흐름상 동사는 글로벌 스트리밍 카테고리 리더로서 다년간 quality compounder의 흐름을 "
        "보여왔습니다. 최근 거론되었던 Warner Bros. 인수전은 추가 제안 없이 종료된 것으로 파악되며, 이에 "
        "따라 대규모 M&A에 따른 부채·통합·규제 리스크는 상당 부분 해소된 구간입니다. 투자 판단의 초점은 "
        "다시 가입자 성장, 광고 요금제 확장, FCF 창출력, 자사주 매입 여력으로 이동하는 단계로 정리됩니다."
    ),
    "NVDA": (
        "장기 주가 흐름상 동사는 GPU/AI 사이클의 가장 큰 수혜주로서 다단계 Re-rating을 거친 종목입니다. "
        "현 구간은 expectation이 매우 높아진 단계로, hyperscaler capex revision과 ASIC 대체 위협의 "
        "균형 점검이 thesis 유지 여부의 핵심 변수입니다."
    ),
    "PLTR": (
        "장기 주가 흐름상 동사는 정부·국방 매출 베이스 위에 민간 AIP 가속이 더해지며 multi-stage "
        "Re-rating을 진행한 종목입니다. 최근 구간은 매출 가속과 valuation 부담이 동시에 부각되는 "
        "단계로, 추정치 추가 상향이 multiple을 정당화할 수 있는지 점검이 필요합니다."
    ),
    "CEG": (
        "장기 주가 흐름상 동사는 AI 데이터센터 전력 수요 부각과 함께 무탄소 발전 가치가 재평가된 "
        "구간을 거쳐왔습니다. PPA 체결 흐름이 thesis의 핵심 변수이며, 단기 주가 조정 시에도 "
        "장기 thesis 유효성 점검이 우선 과제입니다."
    ),
}


def price_interpretation(ticker: str, fallback_theme_label: str | None = None) -> str:
    text = PRICE_INTERPRETATION.get(ticker)
    if text:
        return text
    base = (
        "장기 주가 흐름은 카테고리 사이클과 종목별 catalyst의 누적 결과로 형성됩니다. "
        "현 시점에서는 추세의 방향성과 함께 추정치 동반 여부, valuation 정합성을 함께 점검할 필요가 있습니다."
    )
    if fallback_theme_label:
        return f"{fallback_theme_label} 카테고리에 속한 종목으로, " + base
    return base


# ---------------------------------------------------------------------------
# 이벤트 날짜 파싱 (차트 마커용)
# ---------------------------------------------------------------------------

def parse_event_date(date_str):
    """'2026.04.18', '2026-04-18', '2026.04', '최근' 등을 (year, month, day) 또는 None 반환."""
    if not date_str:
        return None
    s = str(date_str).strip().replace("-", ".").replace("/", ".")
    if s in ("최근", "recent", "Recent", "TBD", ""):
        return None
    parts = [p for p in s.split(".") if p]
    try:
        if len(parts) >= 3 and parts[0].isdigit():
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) >= 2 and parts[0].isdigit():
            return (int(parts[0]), int(parts[1]), 15)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# 시장 환경 3블록 (큐레이션 + market summary 보조)
# ---------------------------------------------------------------------------

def market_environment_blocks(market_summary: str | None = None) -> list[dict[str, str]]:
    """금일 시장 환경 — 3개의 짧은 블록으로 분할.

    market_summary 가 있으면 첫 블록에 함께 표시한다.
    """
    line1 = (
        "나스닥 중심의 위험자산 선호는 유지되고 있으나, 상승폭은 일부 대형 성장주에 집중되며 "
        "시장 폭은 좁아진 구간입니다."
    )
    if market_summary and "실패" not in market_summary:
        line1 = market_summary

    return [
        {
            "title": "지수 및 Risk Appetite",
            "body": line1,
        },
        {
            "title": "금리·유동성",
            "body": (
                "미국 10년물 금리 변동성이 확대되며 고멀티플 성장주의 할인율 부담이 단기 주가의 변동성을 "
                "키우는 구간으로, 실적 추정치 상향이 동반되는 종목 중심의 선별이 유효합니다."
            ),
        },
        {
            "title": "주도 테마 및 수급",
            "body": (
                "AI 인프라, 데이터센터 전력, 방산, 우량주 과매도 후보가 시장 주도 테마로 유지되고 있으며, "
                "테마 적합도와 종목별 catalyst의 정합성을 함께 점검하는 접근이 필요합니다."
            ),
        },
    ]
