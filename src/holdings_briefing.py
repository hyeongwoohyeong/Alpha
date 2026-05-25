"""보유 종목 브리핑 — 사용자 실제 보유 종목(data/portfolio.json)에 대한
일일 LLM 리서치 브리핑 생성기.

원칙 (news_summarizer.py 와 동일한 컨벤션):
- LLM 키가 없거나 LLM_MODE=none 이어도 rule-based 폴백으로 완전 동작한다.
- 어떤 실패도 위로 던지지 않는다 — graceful, 빈 카드 절대 금지.
- 한국 ETF 처럼 시장 데이터를 못 받는 종목(yf_ticker=null)도
  name/type/memo 만으로 '익스포저 테마' 수준의 구조적 분석을 제공한다.
- 브리핑은 STRUCTURAL/THEMATIC 분석이다 — LLM 의 knowledge cutoff 때문에
  '오늘의 뉴스' 를 지어내지 않는다. 구조적 변수로 프레이밍한다.

우선순위:
1) LLM_PROVIDER=openai → OpenAI 호출
2) LLM_PROVIDER=anthropic → Anthropic 호출
3) auto(기본) → OpenAI 먼저, 실패 시 Anthropic 폴백
4) LLM 비활성 / budget 소진 / 모든 호출 실패 → rule-based 폴백
"""
from __future__ import annotations

import json
import os
from typing import Any

from .utils import get_logger

log = get_logger("holdings_briefing")


# ---------------------------------------------------------------------------
# 보조 — 안전 접근
# ---------------------------------------------------------------------------

def _rget(row: Any, key: str) -> Any:
    """sqlite3.Row / dict / None 안전 접근."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        keys = row.keys()
    except Exception:
        return None
    return row[key] if key in keys else None


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except (TypeError, ValueError):
        return None


def _regime_fields(regime: Any | None) -> tuple[str | None, float | None]:
    """market_regime 행에서 (current_regime, market_overheat_score) 안전 추출."""
    if regime is None:
        return None, None
    return _rget(regime, "current_regime"), _f(_rget(regime, "market_overheat_score"))


# ---------------------------------------------------------------------------
# 의미 있는 보유 종목 선별
# ---------------------------------------------------------------------------

def select_meaningful_holdings(
    holdings: list[dict], min_net_worth_pct: float = 1.0
) -> list[dict]:
    """순자산 비중이 임계치 이상인 보유 종목만 비중 내림차순으로 반환.

    net_worth_pct 가 None 인 종목은 제외한다 (의미 비중 판단 불가).
    """
    holdings = holdings or []
    out: list[dict] = []
    for h in holdings:
        nw = _f(h.get("net_worth_pct"))
        if nw is not None and nw >= min_net_worth_pct:
            out.append(h)
    out.sort(key=lambda h: _f(h.get("net_worth_pct")) or 0.0, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Rule-based 테마 추론
# ---------------------------------------------------------------------------

# (테마 키, 키워드 목록, 테마 라벨, 구조적 변수 목록, 요약 한 줄)
_THEME_BUCKETS: list[tuple[str, list[str], str, list[str], str]] = [
    (
        "korea_semi",
        ["반도체", "semi", "semiconductor"],
        "한국 반도체 (삼성전자·SK하이닉스 등 메모리·파운드리)",
        [
            "AI 서버 수요에 연동된 HBM·DDR5 등 고부가 메모리 사이클",
            "메모리 현물·고정거래 가격 추세와 감산/증설 동향",
            "삼성전자·SK하이닉스의 설비투자(CAPEX)와 공급 규율",
            "원/달러 환율 — 수출 비중이 큰 한국 반도체 실적 레버리지",
        ],
        "한국 반도체 대형주에 집중된 익스포저로, 글로벌 메모리 사이클과 AI 투자 강도에 "
        "실적이 직접 연동되는 구조입니다.",
    ),
    (
        "ai_semi",
        ["ai반도체", "ai 반도체", "ai semi", "physical ai", "피지컬"],
        "AI 반도체 / AI 인프라 테마",
        [
            "하이퍼스케일러의 AI 인프라 CAPEX 사이클 강도",
            "GPU·가속기·HBM 등 AI 핵심 부품의 공급/수요 균형",
            "AI 수요의 실제 매출 전환 속도와 ROI 검증 진행도",
            "테마 ETF 특성상 소수 핵심 종목 비중 쏠림",
        ],
        "AI 반도체·인프라 테마에 대한 익스포저로, AI 투자 사이클의 강도와 지속성이 "
        "핵심 변수입니다.",
    ),
    (
        "us_index",
        ["s&p", "sp500", "s&p500", "나스닥", "nasdaq", "qqq", "미국s&p"],
        "미국 대형주 지수 (S&P500 / 나스닥100)",
        [
            "미국 대형 기술주 이익 모멘텀과 지수 내 비중 쏠림",
            "연준 통화정책 경로와 장기금리 — 지수 밸류에이션의 핵심 변수",
            "AI 관련 빅테크 CAPEX·실적 사이클",
            "원/달러 환율 — 한국 상장 미국 지수 ETF 의 환노출",
        ],
        "미국 대형주 지수에 대한 패시브 익스포저로, 빅테크 이익 모멘텀과 금리 환경이 "
        "성과를 좌우합니다.",
    ),
    (
        "dividend",
        ["배당", "schd", "dividend"],
        "미국 배당성장 주식",
        [
            "배당성장주의 이익·현금흐름 안정성과 배당 인상 여력",
            "금리 환경 — 배당주의 상대 매력도에 직접 영향",
            "경기 방어 섹터(필수소비·헬스케어 등) 비중 구성",
            "원/달러 환율에 따른 원화 환산 수익",
        ],
        "배당성장 중심의 미국 주식 익스포저로, 변동성 국면에서 방어적 성격을 갖는 "
        "포지션입니다.",
    ),
    (
        "income",
        ["커버드콜", "covered call", "jepq", "jepi", "인컴"],
        "커버드콜 인컴 전략 (나스닥 등)",
        [
            "기초지수 변동성(VIX) — 커버드콜 옵션 프리미엄 수준의 핵심 변수",
            "콜옵션 매도로 인한 상승 잠재력 제한(capped upside) 구조",
            "월배당 인컴의 안정성과 분배율 추세",
            "기초지수 자체의 방향성 — 하락 시 원금 손실 위험은 그대로",
        ],
        "옵션 프리미엄으로 인컴을 추구하는 커버드콜 전략 익스포저로, 강세장에서는 "
        "상승이 제한되고 하락은 그대로 노출되는 구조입니다.",
    ),
    (
        "commodity",
        ["금", "은", "gold", "silver", "gld", "slv", "원자재"],
        "원자재 (금·은 등)",
        [
            "실질금리와 달러 인덱스 — 귀금속 가격의 핵심 거시 변수",
            "지정학적 리스크·안전자산 수요",
            "산업용 수요(은의 경우 태양광 등) vs 투자 수요",
            "포트폴리오 내 분산·헤지 자산으로서의 역할",
        ],
        "원자재(귀금속) 익스포저로, 실질금리·달러·안전자산 수요에 가격이 연동되며 "
        "포트폴리오 분산 역할을 합니다.",
    ),
    (
        "space",
        ["우주", "항공우주", "space", "nasa", "방산"],
        "우주·항공/방산 테마",
        [
            "정부 우주·국방 예산과 발사·위성 수주 사이클",
            "민간 우주 산업의 상업화 진행도",
            "테마 ETF 특성상 소수 종목 비중 쏠림과 변동성",
            "장기 성장 테마로서 실적 가시성은 아직 제한적",
        ],
        "우주·항공/방산 테마에 대한 익스포저로, 장기 성장 스토리이나 실적 가시성과 "
        "변동성 관리가 핵심 과제입니다.",
    ),
    (
        "tsla",
        ["tsla", "테슬라", "tesla", "tsll"],
        "테슬라 (전기차·자율주행·AI)",
        [
            "전기차 인도량·가격정책과 자동차 부문 마진",
            "FSD(자율주행)·로보택시·휴머노이드 등 AI 옵션 가치",
            "에너지 저장 사업의 성장 기여",
            "단일 종목 익스포저 — 개별 이벤트 리스크 집중",
        ],
        "테슬라에 연동된 익스포저로, 전기차 본업 실적과 자율주행·AI 옵션 가치가 "
        "주가를 좌우하는 변동성 높은 포지션입니다.",
    ),
    (
        "nflx",
        ["nflx", "netflix", "넷플릭스", "nfxl"],
        "넷플릭스 (스트리밍·미디어)",
        [
            "구독자 순증과 광고형 요금제의 매출 기여",
            "콘텐츠 투자 효율과 영업레버리지",
            "글로벌 스트리밍 경쟁 강도",
            "단일 종목 익스포저 — 분기 실적 이벤트 리스크 집중",
        ],
        "넷플릭스에 연동된 익스포저로, 구독자 성장과 광고 사업 확장이 핵심 변수인 "
        "단일 종목 포지션입니다.",
    ),
]


# 버킷 평가 우선순위 — 구체적인 테마를 먼저, 포괄적인(지수 등) 테마를 나중에.
# 예: JEPQ 는 memo 에 "나스닥" 이 있어 us_index 와도 겹치지만 income 이 먼저여야 하고,
#     "SOL AI반도체" 는 korea_semi("반도체")보다 ai_semi 가 먼저여야 한다.
_BUCKET_PRIORITY: list[str] = [
    "income", "dividend", "commodity", "space", "tsla", "nflx",
    "ai_semi", "korea_semi", "us_index",
]


def _infer_theme_bucket(name: str, type_: str, memo: str) -> tuple[str, list[str], str]:
    """name/type/memo 키워드 매칭으로 (테마 라벨, 구조적 변수, 요약 한 줄) 추론.

    구체적인 테마(개별주·전략 ETF)를 포괄적인 지수 테마보다 먼저 평가한다.
    """
    text = f"{name} {type_} {memo}".lower()
    by_key = {b[0]: b for b in _THEME_BUCKETS}
    # 1) 우선순위 순서대로 평가
    for key in _BUCKET_PRIORITY:
        b = by_key.get(key)
        if b and any(kw.lower() in text for kw in b[1]):
            return b[2], list(b[3]), b[4]
    # 2) 우선순위 목록에 없는 버킷도 안전하게 평가 (버킷 추가 대비)
    for _key, kws, label, drivers, summary in _THEME_BUCKETS:
        if _key not in _BUCKET_PRIORITY and any(kw.lower() in text for kw in kws):
            return label, list(drivers), summary
    # 폴백 — 토픽 미분류
    return (
        "개별 종목 / 기타 익스포저",
        [
            "해당 종목·섹터의 이익 모멘텀과 밸류에이션 수준",
            "거시 환경(금리·환율·경기)과의 민감도",
            "포트폴리오 내 비중과 분산 효과",
        ],
        "개별 종목 수준의 익스포저로, 종목·섹터 고유의 이익 흐름과 거시 민감도가 "
        "성과를 좌우합니다.",
    )


def _briefing_rule_based(holding: dict, regime: Any | None) -> dict[str, Any]:
    """LLM 없이 동작하는 결정론적 폴백 브리핑.

    name/type/memo 키워드로 테마를 추론하고, 레버리지·비중·현재 국면을
    엮은 정직하고 얇은 구조적 메모를 생성한다. 어떤 필드도 비우지 않는다.
    """
    name = (holding.get("name") or holding.get("ticker") or "해당 종목").strip()
    type_ = (holding.get("type") or "").strip()
    memo = (holding.get("memo") or "").strip()
    is_lev = bool(holding.get("leverage"))
    nw = _f(holding.get("net_worth_pct"))
    ret = _f(holding.get("return_pct"))
    cur_regime, overheat = _regime_fields(regime)

    label, drivers, theme_summary = _infer_theme_bucket(name, type_, memo)

    # 테마 라벨에 레버리지 표기 부착
    exposure_theme = label
    if is_lev:
        exposure_theme = f"{label} · 레버리지(일일 배수 추종)"

    # 요약 — 테마 한 줄 + 레버리지/타입 보강
    summary_parts = [theme_summary]
    if is_lev:
        summary_parts.append(
            "레버리지 상품은 일일 수익률에 배수를 적용하므로, 횡보·변동성 구간에서는 "
            "변동성 손실(volatility decay)로 누적 수익률이 기초지수와 괴리될 수 있습니다."
        )
    if type_:
        summary_parts.append(f"상품 유형은 '{type_}' 입니다.")
    summary_ko = " ".join(summary_parts)

    # 구조적 변수 — 테마 변수 + 레버리지 변수 보강
    key_drivers = list(drivers)
    if is_lev and not any("레버리지" in d or "변동성 손실" in d for d in key_drivers):
        key_drivers.append("레버리지 배수 — 기초지수 변동성이 누적 수익률에 미치는 영향")

    # 리스크 — 레버리지/비중/국면 연계
    risk_parts: list[str] = []
    if is_lev:
        risk_parts.append(
            "레버리지 상품으로 하락 국면에서 손실이 증폭되며, 장기 보유 시 변동성 손실이 "
            "누적될 수 있습니다."
        )
    if nw is not None:
        if nw >= 35:
            risk_parts.append(
                f"순자산의 {nw:.1f}% 를 차지하는 최대 비중급 포지션으로, 단일 종목 "
                "과집중에 따른 변동성 위험이 큽니다."
            )
        elif nw >= 15:
            risk_parts.append(
                f"순자산의 {nw:.1f}% 로 비중이 큰 편이라 포트폴리오 성과에 미치는 "
                "영향이 큽니다."
            )
        else:
            risk_parts.append(f"순자산 비중은 {nw:.1f}% 입니다.")
    if cur_regime:
        oh_str = f"{overheat:.0f}/100" if overheat is not None else "확인 필요"
        if overheat is not None and overheat >= 50:
            risk_parts.append(
                f"현재 시장 국면은 '{cur_regime}'(Overheat {oh_str})로 다소 과열 쪽에 "
                "있어, 레버리지·고변동성 포지션은 보수적으로 관리할 필요가 있습니다."
            )
        else:
            risk_parts.append(
                f"현재 시장 국면은 '{cur_regime}'(Overheat {oh_str}) 입니다."
            )
    else:
        risk_parts.append(
            "현재 시장 국면 데이터가 없어, 파이프라인 실행 후 국면 연계 점검이 필요합니다."
        )
    risks_ko = " ".join(risk_parts)

    # 포트폴리오 노트 — 비중·수익률·국면 관점
    note_parts: list[str] = []
    if nw is not None:
        note_parts.append(f"이 종목은 순자산의 {nw:.1f}% 를 차지합니다.")
    if ret is not None:
        if ret >= 25:
            note_parts.append(
                f"현재 보유 수익률은 {ret:+.1f}% 로 큰 수익이 났습니다 — 레버리지·고변동성 "
                "포지션이라면 부분 익절 등 수익 보호를 검토할 만합니다."
                if is_lev else
                f"현재 보유 수익률은 {ret:+.1f}% 입니다."
            )
        elif ret <= -10:
            note_parts.append(
                f"현재 보유 수익률은 {ret:+.1f}% 로 손실 구간이며, 투자 논리가 유효한지 "
                "점검이 필요합니다."
            )
        else:
            note_parts.append(f"현재 보유 수익률은 {ret:+.1f}% 입니다.")
    if cur_regime:
        note_parts.append(
            f"'{cur_regime}' 국면에서 이 포지션의 비중과 성격(레버리지 여부)이 현재 "
            "권장 베타와 정합적인지 점검하십시오."
        )
    if not note_parts:
        note_parts.append(
            "비중·수익률 데이터가 부족해 포트폴리오 관점 코멘트가 제한적입니다."
        )
    portfolio_note_ko = " ".join(note_parts)

    return {
        "exposure_theme": exposure_theme,
        "summary_ko": summary_ko,
        "key_drivers_ko": key_drivers,
        "risks_ko": risks_ko,
        "portfolio_note_ko": portfolio_note_ko,
        "model_used": "rule-based",
    }


# ---------------------------------------------------------------------------
# LLM 프롬프트
# ---------------------------------------------------------------------------

_BRIEFING_PROMPT = """너는 국내 탑티어 증권사 리서치센터의 애널리스트다.
사용자가 실제로 보유 중인 한 종목에 대해, 매일 읽을 한국어 리서치 브리핑을 작성한다.

[보유 종목 정보]
- 종목명: {name}
- 상품 유형: {type}
- 사용자 메모: {memo}
- 순자산 비중: {net_worth_pct}
- 레버리지 상품 여부: {leverage}
- 현재 보유 수익률: {return_pct}

[현재 시장 국면]
- 시장 국면(regime): {regime}
- Overheat Score: {overheat}

[작성 규칙]
- 종목명/유형/메모를 보고 이 종목의 '실제 익스포저 테마' 를 네가 직접 추론하라.
  (예: "TIGER 반도체TOP10 레버리지" + "레버리지 ETF" → 한국 반도체 대형주 2배 레버리지,
   삼성전자·SK하이닉스 AI 메모리 사이클)
- 이것은 STRUCTURAL / THEMATIC 분석이다. 너는 knowledge cutoff 가 있으므로
  '오늘의 뉴스', 최근 며칠간의 구체적 주가 변동을 지어내지 마라.
- key_drivers 는 '지금 주목할 구조적 변수' 로 프레이밍하라 (단발 뉴스 X).
- risks 는 이 종목의 레버리지 여부·포트폴리오 비중·현재 시장 국면과 반드시 연결하라.
- portfolio_note 는 이 종목이 사용자 포트폴리오에서 갖는 의미를
  비중·수익률·시장 국면 관점에서 구체적으로 코멘트하라.
- 모든 출력은 한국어. 거짓 정밀(false precision) 금지.
- "원문/공시를 확인하라" 같은 무의미한 안내 문구 금지.
- summary 는 2~4문장, key_drivers 는 3~5개.

[출력 형식 — JSON 만, 다른 텍스트 절대 금지]
{{
  "exposure_theme": "이 종목이 실제로 노출된 테마를 한 줄로 (레버리지면 명시)",
  "summary_ko": "핵심 요약 2~4문장",
  "key_drivers_ko": ["지금 주목할 구조적 변수", "...", "..."],
  "risks_ko": "리스크 — 레버리지·비중·현재 국면과 연계",
  "portfolio_note_ko": "이 종목이 포트폴리오에서 갖는 의미 — 비중·수익률·국면 관점"
}}
"""


def _format_briefing_prompt(holding: dict, regime: Any | None) -> str:
    cur_regime, overheat = _regime_fields(regime)
    nw = _f(holding.get("net_worth_pct"))
    ret = _f(holding.get("return_pct"))
    return _BRIEFING_PROMPT.format(
        name=holding.get("name") or holding.get("ticker") or "(미상)",
        type=holding.get("type") or "(미상)",
        memo=holding.get("memo") or "(없음)",
        net_worth_pct=f"{nw:.1f}%" if nw is not None else "확인 필요",
        leverage="예 (레버리지)" if holding.get("leverage") else "아니오",
        return_pct=f"{ret:+.1f}%" if ret is not None else "확인 필요",
        regime=cur_regime or "확인 필요",
        overheat=f"{overheat:.0f}/100" if overheat is not None else "확인 필요",
    )


def _normalize_briefing_payload(
    data: dict, holding: dict, regime: Any | None
) -> dict[str, Any]:
    """LLM 응답 키 정규화 — 키 변형 흡수 + 빈 필드는 rule-based 로 보강."""
    out: dict[str, Any] = {}
    out["exposure_theme"] = (
        data.get("exposure_theme") or data.get("theme") or data.get("exposure") or ""
    )
    out["summary_ko"] = (
        data.get("summary_ko") or data.get("summary") or data.get("summary_kr") or ""
    )
    kd = (
        data.get("key_drivers_ko")
        or data.get("key_drivers")
        or data.get("drivers")
        or []
    )
    if isinstance(kd, str):
        kd = [s.strip("- •·\t ").strip() for s in kd.splitlines() if s.strip()]
    out["key_drivers_ko"] = [str(x).strip() for x in kd if str(x).strip()][:6]
    out["risks_ko"] = (
        data.get("risks_ko") or data.get("risks") or data.get("risk_ko") or ""
    )
    out["portfolio_note_ko"] = (
        data.get("portfolio_note_ko")
        or data.get("portfolio_note")
        or data.get("portfolio_comment_ko")
        or ""
    )

    # 빈 필드는 rule-based 폴백으로 보강 — UI 빈 카드 방지
    if not all(
        [
            out["exposure_theme"],
            out["summary_ko"],
            out["key_drivers_ko"],
            out["risks_ko"],
            out["portfolio_note_ko"],
        ]
    ):
        rb = _briefing_rule_based(holding, regime)
        if not out["exposure_theme"]:
            out["exposure_theme"] = rb["exposure_theme"]
        if not out["summary_ko"]:
            out["summary_ko"] = rb["summary_ko"]
        if not out["key_drivers_ko"]:
            out["key_drivers_ko"] = rb["key_drivers_ko"]
        if not out["risks_ko"]:
            out["risks_ko"] = rb["risks_ko"]
        if not out["portfolio_note_ko"]:
            out["portfolio_note_ko"] = rb["portfolio_note_ko"]
    return out


# ---------------------------------------------------------------------------
# LLM 호출
# ---------------------------------------------------------------------------

def _briefing_with_openai(holding: dict, regime: Any | None) -> dict[str, Any]:
    """OpenAI API 호출 (openai SDK 필요)."""
    import openai

    client = openai.OpenAI()
    prompt = _format_briefing_prompt(holding, regime)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    try:
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning("OpenAI 브리핑 응답 JSON 파싱 실패: %s", e)
        return _briefing_rule_based(holding, regime)
    return _normalize_briefing_payload(data, holding, regime)


def _briefing_with_anthropic(holding: dict, regime: Any | None) -> dict[str, Any]:
    """Claude API 호출 (anthropic SDK 필요)."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = _format_briefing_prompt(holding, regime)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception as e:
        log.warning("Anthropic 브리핑 응답 JSON 파싱 실패: %s", e)
        return _briefing_rule_based(holding, regime)
    return _normalize_briefing_payload(data, holding, regime)


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------

def generate_holding_briefing(
    holding: dict,
    regime: Any | None,
    *,
    budget=None,
    cfg=None,
    conn=None,
) -> dict[str, Any]:
    """보유 종목 1개 → 한국어 리서치 브리핑.

    Parameters
    ----------
    holding : dict
        portfolio.json holdings 의 한 항목.
    regime : sqlite3.Row | dict | None
        최신 market_regime 행 (current_regime / market_overheat_score 안전 읽기).
    budget, cfg : LLM 예산 / 설정 (미지정 시 새로 생성).
    conn : 미사용 (시그니처 일관성 — 향후 캐시 확장 여지).

    Returns
    -------
    dict — exposure_theme / summary_ko / key_drivers_ko(list) / risks_ko /
           portfolio_note_ko / model_used.
    어떤 경우에도 예외를 던지지 않으며, 모든 필드가 채워진다.
    """
    # cfg / budget lazy import (순환 import 회피)
    from .config import load_config, make_budget

    cfg = cfg or load_config()
    if budget is None:
        budget = make_budget(cfg)

    payload: dict[str, Any] | None = None
    model_used = "rule-based"
    provider = (os.environ.get("LLM_PROVIDER") or "auto").strip().lower()

    def _try_openai() -> str | None:
        nonlocal payload
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        try:
            payload = _briefing_with_openai(holding, regime)
            budget.record()
            return "gpt-4o-mini" if not cfg.use_high_quality_llm else "gpt-4o"
        except Exception as e:
            log.warning("OpenAI 브리핑 실패: %s", e)
            return None

    def _try_anthropic() -> str | None:
        nonlocal payload
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        try:
            payload = _briefing_with_anthropic(holding, regime)
            budget.record()
            return "claude-haiku-4-5" if not cfg.use_high_quality_llm else "claude-opus-4"
        except Exception as e:
            log.warning("Anthropic 브리핑 실패: %s", e)
            return None

    try:
        if cfg.llm_enabled and budget.can_call():
            if provider == "openai":
                model_used = _try_openai() or "rule-based"
            elif provider == "anthropic":
                model_used = _try_anthropic() or "rule-based"
            else:
                model_used = _try_openai() or _try_anthropic() or "rule-based"
    except Exception as e:
        log.warning("브리핑 LLM 디스패치 실패 — rule-based 폴백: %s", e)
        payload = None
        model_used = "rule-based"

    if payload is None:
        payload = _briefing_rule_based(holding, regime)
        model_used = "rule-based"

    payload["model_used"] = model_used
    return payload
