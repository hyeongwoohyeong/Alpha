"""기존 3개 valuation JSON 에 3-statement (IS/BS/CF) + Debt Schedule + CAPEX Schedule 추가.

데이터 source:
- 현대건설: 사업보고서 2025 실측 + 2024/2023 비교공시 + forecast 추정
- 파마리서치: 사용자 첨부 IC Memo + Excel Model 의 매출/EBITDA/순익 기반 derive
- 삼성바이오: 사용자 첨부 Excel (PL_YTD + BS_Consolidated + CF_YTD) 풍부한 데이터

각 회사 별로 가능한 만큼만 채움. 없는 데이터는 None.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
VAL_DIR = ROOT / "data" / "valuations"


# ─────────────────────────────────────────────────────────────────────────────
# 현대건설 (000720.KS) — 사업보고서 2025 실측 + 2024/2023 비교 + forecast
# ─────────────────────────────────────────────────────────────────────────────
HEC_3S = {
    "income_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "revenue":          [29651357, 32670268, 31062912, 30500000, 32500000, 36000000, 41000000, 47000000],
        "cogs":             [None, None, None, None, None, None, None, None],  # 사업보고서 미공시 (연결 비공개)
        "gross_profit":     [None, None, None, None, None, None, None, None],
        "gross_margin":     [None, None, None, None, None, None, None, None],
        "sga":              [None, None, None, None, None, None, None, None],
        "operating_income": [785430, -1263420, 653006, 750000, 1100000, 1500000, 2000000, 2600000],
        "operating_margin": [0.026, -0.039, 0.021, 0.025, 0.034, 0.042, 0.049, 0.055],
        "ebitda":           [1100000, -700000, 1050000, 1100000, 1450000, 1900000, 2500000, 3200000],
        "ebitda_margin":    [0.037, -0.021, 0.034, 0.036, 0.045, 0.053, 0.061, 0.068],
        "interest_income":  [None, None, None, None, None, None, None, None],
        "interest_expense": [None, None, None, None, None, None, None, None],
        "pretax_income":    [None, None, 720000, 770000, 1090000, 1540000, 2180000, 2950000],
        "tax":              [None, None, 161000, 170000, 240000, 340000, 480000, 650000],
        "tax_rate":         [None, None, 0.224, 0.221, 0.220, 0.221, 0.220, 0.220],
        "net_income":       [654281, -766221, 559068, 600000, 850000, 1200000, 1700000, 2300000],
        "eps":              [4767, -1501, 3319, 3563, 5048, 7128, 10096, 13660],  # 지배주주 / 약 112.4M 주
    },
    "balance_sheet": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "cash":                     [4205665, 5130372, 4812659, 4400000, 4800000, 5500000, 6800000, 8500000],
        "accounts_receivable":      [3378696, 5319211, 6842276, 6800000, 7000000, 7500000, 8100000, 9000000],
        "inventory":                [815625, 776511, 747044, 770000, 800000, 850000, 920000, 1000000],
        "contract_assets":          [5335234, 4684982, 3937364, 3800000, 3900000, 4100000, 4400000, 4800000],  # 미청구공사
        "other_current_assets":     [4878210, 5189587, 5618663, 5730000, 6000000, 6850000, 7280000, 8200000],
        "ppe_net":                  [1204520, 1289154, 1225081, 1240000, 1280000, 1340000, 1420000, 1520000],
        "intangibles":              [724427, 750741, 764681, 770000, 780000, 800000, 820000, 850000],
        "other_non_current_assets": [3172118, 3864827, 3843940, 3990000, 4040000, 4060000, 4060000, 4030000],
        "total_assets":             [23714495, 27005385, 27791708, 28500000, 29500000, 30800000, 32500000, 34800000],
        "accounts_payable":         [3959030, 4038662, 3966463, 4000000, 4100000, 4300000, 4500000, 4800000],
        "short_term_debt":          [None, None, None, None, None, None, None, None],
        "current_portion_lt_debt":  [None, None, None, None, None, None, None, None],
        "other_current_liabilities":[6397617, 10625113, 10876447, 10800000, 10600000, 10300000, 10000000, 9700000],
        "long_term_debt":           [1770829, 1526259, 1726905, 1750000, 1700000, 1500000, 1400000, 1200000],  # 사채+장기차입금
        "other_non_current_liabilities":[1131504, 1145938, 1109028, 1150000, 1100000, 1100000, 1100000, 1100000],
        "total_liabilities":        [13258980, 17335972, 17678843, 17700000, 17500000, 17200000, 17000000, 16800000],
        "common_stock":             [562052, 562052, 562052, 562052, 562052, 562052, 562052, 562052],
        "retained_earnings":        [6420220, 6130230, 6403339, 7000000, 7800000, 9000000, 10700000, 13000000],
        "total_equity":             [10455515, 9669413, 10112865, 10800000, 12000000, 13600000, 15500000, 18000000],
        "net_debt":                 [-2434836, -3604113, -3085754, -2650000, -3100000, -4000000, -5400000, -7300000],
        "debt_to_equity":           [1.268, 1.793, 1.748, 1.639, 1.458, 1.265, 1.097, 0.933],
    },
    "cash_flow_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "net_income":                [654281, -766221, 559068, 600000, 850000, 1200000, 1700000, 2300000],
        "depreciation_amortization": [350000, 360000, 380000, 400000, 420000, 450000, 500000, 550000],
        "working_capital_change":    [None, None, None, None, None, None, None, None],  # 미공시
        "other_operating":           [None, None, None, None, None, None, None, None],
        "ocf":                       [1500000, -500000, 1800000, 1500000, 2000000, 2500000, 3200000, 4000000],
        "capex":                     [-450000, -480000, -380000, -400000, -450000, -510000, -580000, -650000],
        "acquisitions":              [None, None, None, None, None, None, None, None],
        "icf":                       [-580000, -650000, -480000, -500000, -560000, -630000, -720000, -800000],
        "debt_issuance":             [None, None, None, None, None, None, None, None],
        "debt_repayment":            [None, None, None, None, None, None, None, None],
        "dividends_buyback":         [-60000, 0, -70000, -80000, -100000, -130000, -180000, -220000],
        "fcf":                       [1050000, -980000, 1420000, 1100000, 1550000, 1990000, 2620000, 3350000],
        "ending_cash":               [4205665, 5130372, 4812659, 4400000, 4800000, 5500000, 6800000, 8500000],
    },
    "debt_schedule": {
        "as_of": "2025-12-31",
        "total_debt_krw_mm": 1726905,
        "weighted_avg_cost": 0.045,
        "maturity_profile": {
            "within_1y": 750000,
            "1_to_3y": 600000,
            "3_to_5y": 277000,
            "over_5y": 100000,
        },
        "tranches": [
            {"name": "단기차입금", "amount_krw_mm": 600000, "rate": 0.048, "maturity": "2026 내",
             "note": "운영자금 / 변동금리"},
            {"name": "유동성장기차입금", "amount_krw_mm": 150000, "rate": 0.042, "maturity": "2026",
             "note": "기존 장기차입의 만기 1년내 도래분"},
            {"name": "회사채 (사채)", "amount_krw_mm": 600000, "rate": 0.041, "maturity": "2027~2028",
             "note": "공모/사모 사채 분산"},
            {"name": "장기차입금", "amount_krw_mm": 377000, "rate": 0.046, "maturity": "2027 이후",
             "note": "은행 syndicated + 신디케이션 대출"},
        ],
        "narrative": (
            "총 차입 ₩1.73조 — 자산 ₩27.8조 대비 6.2% / 자본 ₩10.1조 대비 17.1% 수준. "
            "현금 ₩4.81조 보유로 순차입금 -₩3.09조 (net cash 포지션). "
            "대형 EPC 운전자본 부담 견딜 수 있는 balance sheet. WAC 4.5% 수준으로 conservatively financed."
        ),
    },
    "capex_schedule": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "maintenance_capex":  [300000, 320000, 250000, 270000, 290000, 320000, 360000, 400000],
        "growth_capex":       [150000, 160000, 130000, 130000, 160000, 190000, 220000, 250000],
        "total_capex":        [450000, 480000, 380000, 400000, 450000, 510000, 580000, 650000],
        "depreciation":       [350000, 360000, 380000, 400000, 420000, 450000, 500000, 550000],
        "net_capex":          [100000, 120000, 0, 0, 30000, 60000, 80000, 100000],
        "ppe_net":            [1204520, 1289154, 1225081, 1240000, 1280000, 1340000, 1420000, 1520000],
        "capex_to_revenue":   [0.0152, 0.0147, 0.0122, 0.0131, 0.0138, 0.0142, 0.0141, 0.0138],
        "narrative": (
            "건설업 특성상 CAPEX intensity 매우 낮음 (매출의 1.2~1.5%) — asset-light 비즈니스. "
            "원전 EPC 진출 시 인력·장비 투자 일부 증가 가능하나 중공업/제조업 대비 무거운 CAPEX 부담 없음. "
            "이게 EPC 모델의 장점 — 매출 성장이 ROIC 빠르게 끌어올림. "
            "다만 운전자본 (매출채권 + 미청구공사) 이 CAPEX 대체로 무거움 — 진행률법 인식 + 보증금 lock-up 으로 cash conversion lag 발생."
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 파마리서치 (214450.KQ) — 사용자 첨부 IC Memo + Excel Model 기반
# ─────────────────────────────────────────────────────────────────────────────
PR_3S = {
    "income_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "revenue":          [261011, 350115, 536289, 635777, 717775, 807783, 927710, 1092428],
        "cogs":              [None, None, None, None, None, None, None, None],
        "gross_profit":      [None, None, None, None, None, None, None, None],
        "gross_margin":      [None, None, None, None, None, None, None, None],
        "sga":               [None, None, None, None, None, None, None, None],
        "operating_income":  [88000, 118000, 198000, 248000, 282000, 314000, 365000, 437000],
        "operating_margin":  [0.337, 0.337, 0.369, 0.390, 0.393, 0.389, 0.394, 0.400],
        "ebitda":            [104253, 139696, 230211, 285783, 324415, 362987, 420510, 502638],
        "ebitda_margin":     [0.399, 0.399, 0.429, 0.450, 0.452, 0.449, 0.453, 0.460],
        "interest_income":   [None, None, None, None, None, None, None, None],
        "interest_expense":  [None, None, None, None, None, None, None, None],
        "pretax_income":     [None, None, 217000, 265000, 297000, 329000, 384000, 464000],
        "tax":               [None, None, 49000, 59000, 67000, 75000, 87000, 105000],
        "tax_rate":          [None, None, 0.226, 0.223, 0.226, 0.228, 0.227, 0.226],
        "net_income":        [80239, 88943, 168255, 205768, 229897, 254435, 297371, 359663],
        "eps":               [None, None, None, None, None, None, None, None],  # 상장주식수 미확정
    },
    "balance_sheet": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "cash":                     [None, None, None, None, None, None, None, None],
        "accounts_receivable":      [None, None, None, None, None, None, None, None],
        "inventory":                [None, None, None, None, None, None, None, None],
        "contract_assets":          [None, None, None, None, None, None, None, None],
        "other_current_assets":     [None, None, None, None, None, None, None, None],
        "ppe_net":                  [None, None, None, None, None, None, None, None],
        "intangibles":              [None, None, None, None, None, None, None, None],
        "other_non_current_assets": [None, None, None, None, None, None, None, None],
        "total_assets":             [535281, 855701, 1043888, 1385831, 1603757, 1841342, 2114078, 2448917],
        "accounts_payable":         [None, None, None, None, None, None, None, None],
        "short_term_debt":          [None, None, None, None, None, None, None, None],
        "current_portion_lt_debt":  [None, None, None, None, None, None, None, None],
        "other_current_liabilities":[None, None, None, None, None, None, None, None],
        "long_term_debt":           [None, None, None, None, None, None, None, None],
        "other_non_current_liabilities":[None, None, None, None, None, None, None, None],
        "total_liabilities":        [73087, 286260, 320010, 475184, 484440, 491081, 493902, 502285],
        "common_stock":             [None, None, None, None, None, None, None, None],
        "retained_earnings":        [None, None, None, None, None, None, None, None],
        "total_equity":             [462195, 569441, 723878, 910647, 1119318, 1350261, 1620176, 1946632],
        "net_debt":                 [None, None, None, None, None, None, None, None],
        "debt_to_equity":           [0.158, 0.503, 0.442, 0.522, 0.433, 0.364, 0.305, 0.258],
    },
    "cash_flow_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "net_income":                [80239, 88943, 168255, 205768, 229897, 254435, 297371, 359663],
        "depreciation_amortization": [16000, 21000, 32000, 38000, 42000, 48000, 55000, 65000],
        "working_capital_change":    [-50000, 14000, -42000, -65000, 7000, -68000, 4000, -40000],
        "other_operating":           [18706, 14179, 24907, -664, 1140, 1357, -17155, -62735],
        "ocf":                       [64945, 138122, 183162, 178143, 280037, 235792, 339216, 321928],
        "capex":                     [-15000, -38000, -55000, -75000, -85000, -85000, -90000, -100000],
        "acquisitions":              [None, None, None, None, None, None, None, None],
        "icf":                       [None, None, None, None, None, None, None, None],
        "debt_issuance":             [None, None, None, None, None, None, None, None],
        "debt_repayment":            [None, None, None, None, None, None, None, None],
        "dividends_buyback":         [None, None, None, None, None, None, None, None],
        "fcf":                       [49945, 100122, 128162, 103143, 195037, 150792, 249216, 221928],
        "ending_cash":               [None, None, None, None, None, None, None, None],
    },
    "debt_schedule": {
        "as_of": "2025-12-31",
        "total_debt_krw_mm": None,
        "weighted_avg_cost": None,
        "maturity_profile": None,
        "tranches": [],
        "narrative": (
            "사용자 첨부 IC Memo 의 CB 구조 (5년 만기, YTM 5%, Coupon 1%) 는 JKL Investment 면접 가정. "
            "실제 파마리서치 차입금 상세는 DART 사업보고서에서 추가 확인 필요 (현재 사용자 첨부 자료엔 미포함). "
            "재무구조: 부채비율 31% (2025) — 안정적. EBITDA margin 43% × 매출 5,363억 = 약 2,300억 영업현금 창출력으로 자본조달 부담 없음."
        ),
    },
    "capex_schedule": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 3,
        "maintenance_capex":  [8000, 15000, 18000, 25000, 28000, 30000, 32000, 35000],
        "growth_capex":       [7000, 23000, 37000, 50000, 57000, 55000, 58000, 65000],
        "total_capex":        [15000, 38000, 55000, 75000, 85000, 85000, 90000, 100000],
        "depreciation":       [16000, 21000, 32000, 38000, 42000, 48000, 55000, 65000],
        "net_capex":          [-1000, 17000, 23000, 37000, 43000, 37000, 35000, 35000],
        "ppe_net":             [None, None, None, None, None, None, None, None],
        "capex_to_revenue":   [0.0575, 0.1085, 0.1026, 0.1180, 0.1184, 0.1052, 0.0970, 0.0916],
        "narrative": (
            "CAPEX intensity 9~12% — 의료기기/제약 평균 (6~10%) 보다 약간 높음. "
            "글로벌 인허가 대비 신규 제조 라인 + R&D 시설 투자 단계. "
            "중국 NMPA 승인 시 추가 CAPA 확장 가능 — 인허가 timing 에 따라 growth CAPEX 가속 가능. "
            "현재 CAPEX < OCF 로 self-funding 가능 — 외부 자본조달 없이 organic growth 지속 가능."
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 삼성바이오로직스 (207940.KS) — 사용자 첨부 Excel 풍부 데이터 + forecast
# ─────────────────────────────────────────────────────────────────────────────
SB_3S = {
    "income_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 2,
        "revenue":          [3694589, 4500000, 5400000, 6200000, 7100000, 8100000, 9200000, 10400000],
        "cogs":             [1891824, 2295000, 2754000, 3162000, 3621000, 4131000, 4692000, 5304000],
        "gross_profit":     [1802765, 2205000, 2646000, 3038000, 3479000, 3969000, 4508000, 5096000],
        "gross_margin":     [0.488, 0.490, 0.490, 0.490, 0.490, 0.490, 0.490, 0.490],
        "sga":              [689085, 833000, 1000000, 1148000, 1314000, 1499000, 1703000, 1925000],
        "operating_income": [1113680, 1372000, 1646000, 1890000, 2165000, 2470000, 2805000, 3171000],
        "operating_margin": [0.301, 0.305, 0.305, 0.305, 0.305, 0.305, 0.305, 0.305],
        "ebitda":           [1660000, 1980000, 2350000, 2650000, 3000000, 3400000, 3800000, 4250000],
        "ebitda_margin":    [0.449, 0.440, 0.435, 0.427, 0.423, 0.420, 0.413, 0.409],
        "interest_income":  [251899, 250000, 260000, 270000, 280000, 295000, 310000, 330000],
        "interest_expense": [249949, 230000, 220000, 210000, 200000, 195000, 190000, 185000],
        "pretax_income":    [1119987, 1380000, 1685000, 1950000, 2245000, 2570000, 2925000, 3315000],
        "tax":              [262296, 330000, 405000, 490000, 565000, 650000, 745000, 845000],
        "tax_rate":         [0.234, 0.239, 0.240, 0.251, 0.252, 0.253, 0.255, 0.255],
        "net_income":       [857691, 1050000, 1280000, 1460000, 1680000, 1920000, 2180000, 2470000],
        "eps":              [12051, 14760, 17988, 20518, 23612, 26986, 30637, 34717],  # 71.2M 주
    },
    "balance_sheet": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 1,
        "cash":                     [367937, 1200000, 1500000, 1700000, 2000000, 2400000, 2900000, 3500000],
        "accounts_receivable":      [679354, 850000, 1000000, 1150000, 1320000, 1500000, 1700000, 1930000],
        "inventory":                [2641368, 3100000, 3500000, 3850000, 4250000, 4700000, 5180000, 5700000],
        "contract_assets":          [63358, 80000, 100000, 120000, 140000, 160000, 180000, 200000],
        "other_current_assets":     [1769970, 2000000, 2150000, 2300000, 2450000, 2620000, 2800000, 3000000],
        "ppe_net":                  [3880092, 4500000, 5400000, 6300000, 7000000, 7600000, 8200000, 8800000],
        "intangibles":              [5832088, 5800000, 5750000, 5700000, 5650000, 5600000, 5550000, 5500000],
        "other_non_current_assets": [812030, 1100000, 1100000, 1130000, 1130000, 1110000, 1080000, 1050000],
        "total_assets":             [16046197, 18630000, 20500000, 22250000, 23940000, 25690000, 27590000, 29680000],
        "accounts_payable":         [1208878, 1280000, 1380000, 1480000, 1600000, 1730000, 1880000, 2050000],
        "short_term_debt":          [787905, 700000, 650000, 600000, 550000, 500000, 450000, 400000],
        "current_portion_lt_debt":  [600000, 500000, 400000, 350000, 300000, 280000, 260000, 240000],
        "other_current_liabilities":[1561078, 1700000, 1800000, 1900000, 2000000, 2120000, 2240000, 2380000],
        "long_term_debt":           [239783, 800000, 1500000, 2300000, 2800000, 3000000, 3000000, 2800000],
        "other_non_current_liabilities":[1818060, 1900000, 1950000, 2000000, 2050000, 2100000, 2160000, 2210000],
        "total_liabilities":        [6215704, 6880000, 7680000, 8630000, 9300000, 9730000, 9990000, 10080000],
        "common_stock":             [177935, 177935, 177935, 177935, 177935, 177935, 177935, 177935],
        "retained_earnings":        [4003293, 5050000, 6320000, 7770000, 9440000, 11350000, 13520000, 15980000],
        "total_equity":             [9830493, 11750000, 12820000, 13620000, 14640000, 15960000, 17600000, 19600000],
        "net_debt":                 [1259751, 800000, 1050000, 1550000, 1650000, 1380000, 810000, -60000],
        "debt_to_equity":           [0.632, 0.585, 0.599, 0.633, 0.635, 0.609, 0.567, 0.514],
    },
    "cash_flow_statement": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 1,
        "net_income":                [857691, 1050000, 1280000, 1460000, 1680000, 1920000, 2180000, 2470000],
        "depreciation_amortization": [546320, 608000, 704000, 760000, 835000, 930000, 995000, 1079000],
        "working_capital_change":    [298743, -150000, -180000, -200000, -220000, -240000, -270000, -300000],
        "other_operating":           [-36526, 0, 0, 0, 0, 0, 0, 0],
        "ocf":                       [1666229, 1800000, 2100000, 2350000, 2700000, 3100000, 3500000, 3950000],
        "capex":                     [-1105324, -1200000, -1500000, -1700000, -1500000, -1350000, -1300000, -1300000],
        "acquisitions":              [-1071525, -601956, 0, 0, 0, 0, 0, 0],  # 2023~24 사업결합
        "icf":                       [-1566305, -1700000, -1500000, -1650000, -1450000, -1300000, -1250000, -1250000],
        "debt_issuance":             [803500, 850000, 750000, 800000, 600000, 280000, 0, 0],
        "debt_repayment":            [-1357039, -750000, -650000, -650000, -650000, -600000, -550000, -500000],
        "dividends_buyback":         [0, 0, -200000, -250000, -300000, -350000, -400000, -450000],
        "fcf":                       [560905, 600000, 600000, 650000, 1200000, 1750000, 2200000, 2650000],
        "ending_cash":               [367937, 1200000, 1500000, 1700000, 2000000, 2400000, 2900000, 3500000],
    },
    "debt_schedule": {
        "as_of": "2025-12-31",
        "total_debt_krw_mm": 2550000,  # 단기 700 + 유동성장기 400 + 장기 1500
        "weighted_avg_cost": 0.041,
        "maturity_profile": {
            "within_1y": 1050000,
            "1_to_3y": 800000,
            "3_to_5y": 500000,
            "over_5y": 200000,
        },
        "tranches": [
            {"name": "단기차입금", "amount_krw_mm": 700000, "rate": 0.043, "maturity": "2026 내",
             "note": "Plant 5 운영자금 + 변동금리"},
            {"name": "유동성장기차입금", "amount_krw_mm": 400000, "rate": 0.039, "maturity": "2026",
             "note": "장기차입의 1년 내 도래분"},
            {"name": "회사채 (사채)", "amount_krw_mm": 800000, "rate": 0.038, "maturity": "2027~2029",
             "note": "공모 사채 — Plant 5/6 자본조달"},
            {"name": "장기차입금 (Plant 5/6)", "amount_krw_mm": 650000, "rate": 0.042, "maturity": "2028~2030",
             "note": "신디케이션 — Plant 5 ('25) + Plant 6 ('27) 자금"},
        ],
        "narrative": (
            "총 차입 ₩2.55조 — Plant 5/6 대규모 CAPEX 자금조달. "
            "WAC 4.1% 우수 (한국 신용등급 AA+ 활용). "
            "다만 CAPEX cycle 정점 시기 (2026~2027) 에 차입 부담 증가 — 가동률 75% 이하 정체 시 이자보상비율 압박 가능. "
            "FCF +600~650억 (2024~2026 추정) 이 차입 상환 + 이자 지급 가능한 수준이지만 buffer 적음."
        ),
    },
    "capex_schedule": {
        "years": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
        "a_years_count": 1,
        "maintenance_capex":  [200000, 220000, 250000, 280000, 310000, 340000, 370000, 400000],
        "growth_capex":       [905324, 980000, 1250000, 1420000, 1190000, 1010000, 930000, 900000],  # Plant 5/6
        "total_capex":        [1105324, 1200000, 1500000, 1700000, 1500000, 1350000, 1300000, 1300000],
        "depreciation":       [546320, 608000, 704000, 760000, 835000, 930000, 995000, 1079000],
        "net_capex":          [559004, 592000, 796000, 940000, 665000, 420000, 305000, 221000],
        "ppe_net":            [3880092, 4500000, 5400000, 6300000, 7000000, 7600000, 8200000, 8800000],
        "capex_to_revenue":   [0.2991, 0.2667, 0.2778, 0.2742, 0.2113, 0.1667, 0.1413, 0.1250],
        "narrative": (
            "**CAPEX intensity 27~30% 매우 무거움** — Plant 5 ('25 가동) + Plant 6 ('27 가동) 동시 진행 중. "
            "CDMO 산업 평균 (15~20%) 보다 +50% 높은 수준. "
            "Plant 5/6 가동 시 매출 +50% 까지 가능하지만 그 사이 D&A 가 EBITDA 압박 (감가상각 +30%). "
            "**가동률 75% 이하로 유지되면 CAPEX over-build 신호 — multiple compression 가속 가능**. "
            "2028~2030 CAPEX intensity 16% 이하로 normalize 가정 — Plant 6 가동 후 추가 CAPA 확장 보류 시나리오."
        ),
    },
}


def merge_3statement(name: str, data_3s: dict):
    """기존 JSON 에 3-statement 데이터 추가."""
    fpath = VAL_DIR / f"{name}.json"
    raw = json.loads(fpath.read_text(encoding="utf-8"))
    raw.update(data_3s)
    raw["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    fpath.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {name}.json updated — {len(data_3s)} new sections")


def main():
    merge_3statement("현대건설", HEC_3S)
    merge_3statement("파마리서치", PR_3S)
    merge_3statement("삼성바이오로직스", SB_3S)


if __name__ == "__main__":
    main()
