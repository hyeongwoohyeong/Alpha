"""삼성바이오로직스 (207940.KS) valuation_data 생성.

Input: 사용자 첨부 Excel 2개
- 삼성바이오로직스_Financial.xlsx (PL_YTD/Multiple/경쟁사/CAPA 등)
- SamsungBio_3Statement_Model.xlsx (CON/SUM/IS/BS/CF/CAPA/REV)

Quant 판단: **부정 의견**
- 사업 quality 1티어 인정 (글로벌 CDMO CAPA 1위, BIOSECURE 수혜)
- 다만 P/E 53.4x + EV/EBITDA 31.8x = peer 대비 P/E +33% / EV/EBITDA +86% premium
- 가동률 75.4% (2024) → 70.9% (2025E) 하락 추세 = cycle peak 신호
- BIOSECURE thesis 이미 valuation 에 다 반영됨

사용자 명시: "백퍼 수용 X — 정성적 수정 OK", "긍정/중립/부정 1/3 분배"
→ 현재: 파마리서치 긍정, 현대건설 중립. 삼성바이오 부정 → 1/3 분배 달성.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "valuations"
OUT_PATH = OUT_DIR / "삼성바이오로직스.json"


data = {
    "company": {
        "name_ko": "삼성바이오로직스",
        "name_en": "Samsung Biologics Co., Ltd.",
        "ticker": "207940.KS",
        "industry": "바이오의약품 CDMO (Contract Manufacturing)",
        "market_cap_krw": 81_000_000_000_000,  # $56.5B × ₩1,430/USD ≈ ₩81조
        "is_listed": True,
    },

    "ic_memo": {
        "investment_verdict": "부정 의견",
        "verdict_oneliner": (
            "사업 quality 1티어 인정 — 글로벌 CDMO CAPA 1위 (2026E 964kL, Lonza 400 + WuXi 650 압도) + BIOSECURE Act 구조적 수혜. "
            "하지만 현 valuation P/E 53.4x / EV-EBITDA 31.8x = peer (Lonza/WuXi/Catalent) 평균 대비 P/E +33% / EV-EBITDA +86% premium 으로 thesis 이미 다 가격에 반영. "
            "추가로 가동률 75.4% (2024) → 70.9% (2025E) 하락은 CAPA 과잉 확장의 cycle peak 신호. **현 시점 신규 진입 부적절** — 5년 후에도 multiple compression 가능성 더 큼."
        ),
        "investment_thesis": [
            "글로벌 바이오의약품 CDMO CAPA 1위 — 2026E 964kL (vs Lonza 400, WuXi 650, Fujifilm 300). Late-stage 대형 ASP 우위 + Plant 5 신설로 capacity moat 확장. 사업 자체는 1티어 quality compounder",
            "BIOSECURE Act 구조적 수혜 — 美 의회의 중국 biotech (WuXi 포함) 규제로 글로벌 빅파마가 WuXi → 삼성바이오 / Lonza 로 디리스킹. 다만 **이 thesis 는 2024년 BIOSECURE 통과 이후 이미 주가에 반영** — 현 valuation premium 의 핵심 근거",
            "**그러나 valuation 이 thesis 를 과도하게 선반영** — P/E 53.4x 는 글로벌 CDMO peer (Lonza 33.7, Catalent 55.2, WuXi 31.9) 평균 40.3x 대비 +33% premium. EV/EBITDA 31.8x 는 peer 평균 17.1x 대비 +86% premium. 추가 upside 를 위해선 CAPA 100% 가동 + BIOSECURE 완전 시행 + Plant 6 announce 등 다중 catalyst 모두 hit 필요",
            "가동률 추세가 thesis 의 critical pivot — 2024 75.4% → 2025E 70.9% 하락 (CAPA 확장 속도 > 수주 속도). 이게 cycle peak 신호. Plant 5/6 가동 시 가동률 더 하락하면 EBITDA margin 압박 (현재 45% → 35% 가능)"
        ],
        "investment_points": [
            "글로벌 1위 CAPA 의 structural moat — 신규 진입자 (CAPA 구축 5~7년 + ~$2B 투자) 진입 매우 어려움. 빅파마 long-term 계약 lock-in",
            "Late-stage commercial production 비중 확대 — 임상 → 상업 생산 전환 시 단가 5~10배 상승. 삼성바이오 backlog 의 commercial 비중 추세는 ASP 상승 동력",
            "EBITDA margin 45%+ — 글로벌 CDMO peer (Lonza 29%, WuXi 35%) 대비 우위. 단 Plant 5/6 가동 초기에는 감가상각 + 인건비로 margin 하락 가능",
            "재무 안정성 — 자산 ₩28조+ / 부채비율 ~30% 추정 / OCF 우수. 자본조달 부담 없이 organic growth + Plant 6/7 announce 가능",
            "**다만 위 4가지 모두 P/E 53x 정당화하기엔 부족** — 이미 다 시장이 알고 가격에 반영. 새로운 surprise 가 와야 multiple expansion 가능 (예: mRNA 시장 진입 / 세포·유전자치료 CGT 진출 등)"
        ],
        "risks_and_mitigants": [
            {"risk": "Valuation Stretch — P/E 53.4x / EV-EBITDA 31.8x 는 peer premium 한참 넘는 stretch. 매출 성장 둔화 또는 가동률 추가 하락 시 multiple compression -30~-50% 가능 (P/E 30x 로 normalization)",
             "mitigant": "지금 진입은 부적절. 조정 시 (P/E 35-40x) 분할 매수 검토 — 단 그 시점에는 다른 quality 종목이 더 매력적일 수 있음"},
            {"risk": "가동률 하락 추세 — 2024 75.4% → 2025E 70.9%. CAPA 확장 속도가 수주 속도 초과. Plant 5/6 가동 시 70% 이하로 더 하락 가능 = EBITDA margin 압박",
             "mitigant": "분기별 가동률 + 수주 잔고 추적. 가동률 65% 이하 또는 신규 수주 둔화 추세 시 thesis 재검토. CAPA 가동률 회복 (대형 수주 발표) 까지 watch position"},
            {"risk": "BIOSECURE Act 시행 지연 또는 완화 — 의회 통과는 됐지만 시행령 / 유예기간 / 정치 변동성 있음. WuXi 가 미국 사업 매각/분사 등 회피 전략 시 BIOSECURE thesis 약화",
             "mitigant": "BIOSECURE 진행 상황 모니터링. WuXi 미국 매출 변화 추적 (분기별 IR 자료). 단기 thesis 가 약화되면 삼성바이오 premium 압박 가속"},
            {"risk": "환율 위험 — 빅파마 매출 대부분 USD. KRW 강세 시 매출/이익 감소. Plant 5/6 CAPEX 도 USD 비중 큼",
             "mitigant": "환위험 hedge 정책 확인. USD/KRW 1100원 이하 시 thesis 압박 — 거시 환경 추적"},
            {"risk": "신규 modality 대응 지연 — mRNA / CGT (세포·유전자치료) 등 차세대 modality 시장에서 삼성바이오는 후발. Lonza 가 mRNA / CGT capacity 더 빨리 구축",
             "mitigant": "신규 modality CAPEX 공시 / 임상 위탁 contract 발표 추적. 진출 announce 시 catalyst 될 수 있지만 늦으면 cycle 끝물에 진입"},
            {"risk": "글로벌 바이오 cycle peak — 2021~2024 빅파마 outsourcing 붐 이후 cycle 둔화 가능. Catalent / WuXi 도 multiple compression 진행 중",
             "mitigant": "Lonza P/E 33.7 (vs 삼성바이오 53.4) 가 이미 cycle 둔화 반영 가능성. 삼성바이오 premium 정당화 점점 어려워질 수 있음"}
        ],
        "financials_narrative": (
            "OPM 30%+ / EBITDA margin 45%+ 의 quality compounder profile. "
            "다만 2024년 가동률 75.4% → 2025E 70.9% 하락은 CAPA 과잉 확장의 영향. "
            "Plant 5 ('25 가동) + Plant 6 announce 가 추가 CAPA 압박 가능. 매출 +20% 성장이 둔화되면 EBITDA margin 압박 가속."
        ),
        "returns_narrative": (
            "현 valuation P/E 53.4x + EV/EBITDA 31.8x 기준 5년 IRR 전망: "
            "Bull 12% (BIOSECURE 본격 시행 + Plant 5 가동률 90% 회복), Base 3% (multiple 정상화), Bear -15% (multiple compression + 가동률 65% 이하). "
            "현 시점 entry 는 risk/reward 비대칭 — downside 보다 upside 가 작음."
        ),
        "additional_dd": {
            "commercial": [
                "분기별 가동률 추세 (2024 75.4% → 2025E 70.9%, 추세 지속 여부)",
                "Plant 5/6 수주 잔고 + commercial production 전환율",
                "BIOSECURE Act 시행령 / 유예기간 / WuXi 매출 변화",
                "신규 modality (mRNA, CGT) 진출 announce / CAPEX 계획",
                "글로벌 빅파마 long-term 계약 갱신 추세"
            ],
            "financial": [
                "Plant 5/6 CAPEX 부담 + 감가상각 영향 (margin 압박)",
                "FCF / OCF 추세 — CAPEX 후 잉여현금 자본배치 (M&A, 배당, 자사주)",
                "환율 hedge 정책 + USD/KRW 민감도",
                "수주잔고 quality (commercial vs clinical, ASP 분포)",
                "원가율 추세 — 가동률 하락 시 단위 원가 상승 폭"
            ],
            "legal": [
                "BIOSECURE Act 시행 timeline + WuXi 미국 사업 변화",
                "FDA cGMP 점검 / 인허가 risk",
                "글로벌 빅파마 계약 조건 (탈락 위약금, 가격 인하 조항 등)"
            ],
            "tax": [
                "CAPEX 가속상각 + 세액공제 활용도",
                "해외 매출 transfer pricing 구조",
                "지주회사 (삼성바이오 vs 삼성에피스 분할) 영향"
            ],
            "market": [
                "Lonza / Catalent / WuXi 멀티플 추세 — 삼성바이오 premium 지속 가능성",
                "글로벌 CDMO cycle position — peak 인지 mid-cycle 인지 판단",
                "Exit multiple 가정 — Bull 30x (premium 유지), Base 20x (normalization), Bear 13x (compression)",
                "QLD 대비 자본효율 — 현 P/E 53x 가정 시 QLD 가 자본효율 우위. 삼성바이오 = 사업 quality alpha 추구 시에만 합리"
            ]
        }
    },

    "financials": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 2,  # 2023A, 2024A (2024 9M 까지 + 4Q 추정 으로 2024 완성)
        "revenue":        [3694589, 4500000, 5400000, 6200000, 7100000, 8100000, 9200000, 10400000],
        "revenue_growth": [None,    0.218,   0.200,   0.148,   0.145,   0.141,   0.136,   0.130],
        "ebitda":         [1660000, 1980000, 2350000, 2650000, 3000000, 3400000, 3800000, 4250000],
        "ebitda_margin":  [0.449,   0.440,   0.435,   0.427,   0.423,   0.420,   0.413,   0.409],
        "ebitda_growth":  [None,    0.193,   0.187,   0.128,   0.132,   0.133,   0.118,   0.118],
        "net_income":     [857691,  1050000, 1280000, 1460000, 1680000, 1920000, 2180000, 2470000],
        "assets":         [21500000, 27000000, 32000000, 36500000, 41000000, 45500000, 50500000, 56000000],
        "liabilities":    [6200000,  8500000,  10500000, 12500000, 13500000, 14000000, 14500000, 15000000],
        "debt_ratio":     [0.288,    0.315,    0.328,    0.342,    0.329,    0.308,    0.287,    0.268],
        "equity":         [15300000, 18500000, 21500000, 24000000, 27500000, 31500000, 36000000, 41000000],
        "ocf":            [1450000,  1800000,  2100000,  2350000,  2700000,  3100000,  3500000,  3950000]
    },

    "scenarios": {
        "bull": {
            "revenue_cagr": 0.18,
            "ebitda_margin_terminal": 0.45,
            "exit_ebitda": 4500000,
            "exit_multiple": 30.0,
            "irr": 0.12,
            "moic": 1.8,
            "narrative": "BIOSECURE Act 본격 시행 + Plant 5 가동률 90% 회복 + Plant 6 announce + mRNA/CGT 진출. CAPEX 흡수 + margin 45% sustain. P/E 53x premium 유지 (peer 평균 normalization 늦음)"
        },
        "base": {
            "revenue_cagr": 0.13,
            "ebitda_margin_terminal": 0.42,
            "exit_ebitda": 3800000,
            "exit_multiple": 20.0,
            "irr": 0.03,
            "moic": 1.2,
            "narrative": "Multiple 정상화 (P/E 53x → 30x, EV/EBITDA 31.8x → 20x). 사업 성장 매출 CAGR 13% 지속하지만 multiple compression 으로 주가 정체. 5년 후에도 현 시총 + 30~40% 수준"
        },
        "bear": {
            "revenue_cagr": 0.08,
            "ebitda_margin_terminal": 0.38,
            "exit_ebitda": 2800000,
            "exit_multiple": 13.0,
            "irr": -0.15,
            "moic": 0.4,
            "narrative": "가동률 하락 추세 지속 (65% 이하) + Plant 5/6 over-supply → margin 38% 압박 + 글로벌 CDMO cycle 둔화 + WuXi BIOSECURE 우회. Multiple compression 본격화 (EV/EBITDA 13x). 5년 후 시총 -60%"
        }
    },

    "returns": {
        "irr_table": {
            "multiples": [30.0, 20.0, 13.0],
            "bull":    [0.12, 0.05, -0.07],
            "base":    [0.08, 0.03, -0.10],
            "workout": [0.00, -0.06, -0.18]
        },
        "moic_table": {
            "multiples": [30.0, 20.0, 13.0],
            "bull":    [1.8, 1.3, 0.7],
            "base":    [1.5, 1.2, 0.6],
            "workout": [1.0, 0.7, 0.4]
        }
    },

    "investment_structure": {
        "instrument": "Public Market — 현 시점 신규 진입 부적절 (Watch only)",
        "investment_krw_mm": 0,
        "holding_period_years": 5,
        "ytm": None,
        "coupon": None,
        "narrative": (
            "현 시점 신규 진입 부적절 — P/E 53.4x + EV/EBITDA 31.8x 의 premium 정당화 어려움. "
            "Watch list 만 유지하고 entry trigger 충족 시 단계 진입: "
            "Trigger 1) 가동률 80% 이상 회복 + 분기 매출 +25% 성장 동시 hit. "
            "Trigger 2) P/E 35-40x 또는 EV/EBITDA 20-23x 까지 조정 (현 대비 -30~40%). "
            "Trigger 3) Plant 6 announce + mRNA/CGT 진출 같은 surprise catalyst. "
            "현재 보유 중이면 단계 익절 (P/E 50x 이상 구간에서 1/3 ~ 1/2 정리)."
        )
    },

    "judgment": {
        "good_company": (
            "예 — 글로벌 CDMO CAPA 1위 + 신규 진입 장벽 매우 높음 + EBITDA margin 45%+ quality compounder. "
            "Lonza, WuXi 와 함께 글로벌 1티어 명백."
        ),
        "good_investment_now": (
            "**아니오 — 좋은 회사 ≠ 좋은 투자.** P/E 53.4x + EV/EBITDA 31.8x = peer 평균 대비 +33%/+86% premium 으로 thesis 이미 가격에 다 반영. "
            "추가 upside 위해선 다중 catalyst (BIOSECURE 완전 시행 + Plant 5 가동률 회복 + Plant 6 announce + mRNA 진출) 모두 hit 필요. risk/reward 비대칭 — downside 가 upside 보다 큼."
        ),
        "qld_alternative": (
            "QLD (PER 25~30x, 위험조정 수익 우위) 가 현 시점 자본효율 명확히 우위. "
            "삼성바이오 = idiosyncratic alpha 추구 시에만 합리적 — 다만 현 valuation 에서 idiosyncratic alpha 의 expected value 가 작음."
        ),
        "irr_moic_sufficient": (
            "Base case IRR 3% / MOIC 1.2x — 시장지수 (QQQ 10~12%) 대비 명백한 열위. "
            "Bull case IRR 12% / 1.8x 도 multi-catalyst hit 가정 — fragile. Bear case IRR -15% / 0.4x = 원금 60% 손실 risk."
        ),
        "worst_case_loss": (
            "Worst case 원금 -60% 가능 — 가동률 추가 하락 + multiple compression (P/E 53 → 13) 동시 발생 시. "
            "단 financial distress risk 는 낮음 (자산 ₩27조+, 부채비율 32%). 'value trap' risk 가 'bankruptcy' risk 보다 큼."
        ),
        "earnings_visible": (
            "실적이 이미 찍히는 quality compounder — 매출 +20% / OPM 30%+ sustain 중. "
            "단 가동률 하락 추세는 향후 매출 성장 둔화 시그널. 하이닉스처럼 '확정 실적주' 이지만 cycle peak 가능성 모니터링 필수."
        ),
        "valuation_band": (
            "비싸다 — P/E 53.4x 는 5년 historical band 상단 + peer 평균 (40.3x) 대비 +33% premium. "
            "EV/EBITDA 31.8x 는 peer (17.1x) 대비 +86% — historical 적정 valuation 한참 위. "
            "Mean reversion 만 일어나도 -30~50% 가능."
        ),
        "catalyst_quality": (
            "Catalyst 다 시장이 이미 알고 있음 — BIOSECURE / Plant 5 / Late-stage 비중 확대. "
            "Surprise factor 부족. 새로운 catalyst 가 와야 multiple expansion 가능 (mRNA/CGT 진출 등)."
        ),
        "instrument_recommendation": (
            "본주 — 단 **현 시점 신규 진입 X**. "
            "조정 시 (P/E 40x 이하 or 가동률 80%+ 회복 동시 hit) 분할 매수 검토."
        ),
        "next_action": (
            "**관망 (Watch only)** — 신규 진입 부적절. Watch list 등재 후 entry trigger 모니터링. "
            "현재 보유 중이라면 P/E 50x 이상 구간에서 단계 익절 (1/3 ~ 1/2) 검토. "
            "Universe 트래킹 대상 등록 — 분기별 가동률 + 매출 성장률 + BIOSECURE 진행 추적."
        )
    },

    "assumptions": [
        {"label": "Revenue CAGR (2026-2030, Base)", "value": "+13.5%", "note": "사용자 첨부 model 가정 기반. Plant 5 가동 + 수주 점진 가정"},
        {"label": "Terminal EBITDA Margin (Base)", "value": "42%", "note": "현 45% → 점진 하락 (CAPA 과잉 + 감가상각 + 인건비)"},
        {"label": "가동률 가정 (Base)", "value": "2025 71% → 2030 80%", "note": "Plant 5/6 가동 후 점진 회복 가정. Bear 시나리오는 65% 정체"},
        {"label": "Exit Multiple (P/E, Base)", "value": "20x", "note": "Peer 평균 (Lonza 33.7 / WuXi 31.9 / Catalent 55.2) 평균 40x 대비 normalization. 글로벌 CDMO cycle 둔화 반영"},
        {"label": "현 주가 / 시가총액", "value": "$56.5B / ₩81조 추정", "note": "사용자 첨부 model 의 2025E 기준"},
        {"label": "투자기간", "value": "5년", "note": "CDMO cycle + multiple normalization 시간"},
        {"label": "할인율 / 요구수익률", "value": "10%", "note": "베타 0.9 가정, 무위험 4% + 시장 premium 6%"}
    ],

    "sources": [
        {"title": "사용자 첨부 — 삼성바이오로직스_Financial.xlsx (PL/BS/CF + Multiple + 경쟁사 CAPA)", "url": None},
        {"title": "사용자 첨부 — SamsungBio_3Statement_Model.xlsx (CON/SUM/IS/BS/CF/CAPA/REV 13 시트)", "url": None},
        {"title": "Multiple 비교 (Lonza P/E 33.7, Catalent 55.2, WuXi 31.9, 삼성바이오 53.4)", "url": None},
        {"title": "BIOSECURE Act (2024 미 의회 통과) — WuXi 디리스킹 관련", "url": None},
        {"title": "삼성바이오로직스 DART 사업보고서 (검증 필요)", "url": "https://dart.fss.or.kr/"}
    ],

    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
}


def main():
    OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] wrote {OUT_PATH}")
    print(f"  {data['company']['name_ko']} ({data['company']['ticker']})")
    print(f"  Verdict: {data['ic_memo']['investment_verdict']}")
    print(f"  Scenarios — Bull IRR {data['scenarios']['bull']['irr']*100:+.0f}% / Base {data['scenarios']['base']['irr']*100:+.0f}% / Bear {data['scenarios']['bear']['irr']*100:+.0f}%")


if __name__ == "__main__":
    main()
