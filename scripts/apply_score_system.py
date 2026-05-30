"""3 valuation JSON 에 점수 시스템 적용.

룰:
- 0~33점 → 부정 의견
- 34~66점 → 중립 의견
- 67~100점 → 매수 의견

4축 스코어링 (각 25점):
- Quality: 사업 본질 (moat, recurring, margin, brand)
- Valuation: 가격 적정성 (multiple vs peer)
- Catalyst: 단기·중기 trigger
- Risk/Reward: 시나리오 비대칭

자동으로 investment_verdict 재계산.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
VAL_DIR = ROOT / "data" / "valuations"


def verdict_from_score(score: float) -> str:
    if score >= 67:
        return "매수 의견"
    if score >= 34:
        return "중립 의견"
    return "부정 의견"


SCORES = {
    "파마리서치": {
        "quality": {
            "score": 22,
            "rationale": "EBITDA margin 45%+ / 의사 lock-in 반복 매출 / brand power / SaaS-like sticky business / essential luxury 카테고리 — 글로벌 의료미용 1티어 candidate"
        },
        "valuation": {
            "score": 16,
            "rationale": "PER 15-20x 추정 — 한국 의료미용 peer (PER 20-35x) 대비 할인, 코스닥 평균 대비 약간 premium. 적정 valuation band 내 위치"
        },
        "catalyst": {
            "score": 17,
            "rationale": "중국 NMPA 인허가 (2027~28), 일본 시장 진입, 남미 제도권 편입 — 3중 catalyst. 각자 매출 +10~30% 기여 가능"
        },
        "risk_reward": {
            "score": 17,
            "rationale": "Bull IRR 28% vs Bear 3% — upside dominate. 재무구조 안정 (부채비율 31%, 자본 7,239억) + 40% 마진으로 downside floor"
        }
    },
    "현대건설": {
        "quality": {
            "score": 12,
            "rationale": "OPM 평균 2% + 2024년 영업적자 -1.26조 = low quality. Asset-light 인 EPC 모델은 좋지만 매출 변동성 + cyclical 특성. Quality compounder 아님"
        },
        "valuation": {
            "score": 8,
            "rationale": "PER 45.8x — 건설업 peer (PER 8~12x) 대비 4배 premium. 이미 thesis 일부 반영. 사용자 PDF 목표가 30만원은 PER 90x 필요 = 비현실"
        },
        "catalyst": {
            "score": 18,
            "rationale": "다양한 mid-term: Palisades SMR 착공 (2026 상반기), Matador FEED (2026.04), 코즐로두이 본계약, 중동 재건 발주. 카탈리스트 다양성은 강점"
        },
        "risk_reward": {
            "score": 12,
            "rationale": "Bull IRR 22% vs Bear -5% — 비대칭이 그닥 매력적이지 않음. Option value 인정하되 fragile thesis (3축 동시 hit 가정). 재무 안정성 (net cash) 으로 절대 손실 risk 제한"
        }
    },
    "삼성바이오로직스": {
        "quality": {
            "score": 18,
            "rationale": "글로벌 CDMO CAPA 1위 (2026E 964kL) + EBITDA margin 45%+ + 진입장벽 매우 높음 — 사업 quality 1티어 명백. 사용자 첨부 데이터에서도 확인"
        },
        "valuation": {
            "score": 5,
            "rationale": "P/E 53.4x / EV-EBITDA 31.8x — peer 평균 (P/E 40.3, EV/EBITDA 17.1) 대비 +33% / +86% premium. 매우 비쌈. Mean reversion 만 일어나도 -30~50%"
        },
        "catalyst": {
            "score": 8,
            "rationale": "BIOSECURE Act / Plant 5 가동 / late-stage 확대 — 이미 시장이 알고 가격에 반영. Surprise factor 부족. 새 mRNA/CGT 진출 같은 surprise 와야 multiple expansion"
        },
        "risk_reward": {
            "score": 6,
            "rationale": "Bull IRR 12% vs Bear -15% — downside dominate. 가동률 하락 (75.4% → 70.9%) + CAPEX 27~30% 무거움 + multiple compression risk. risk/reward 비대칭 명백 부정"
        }
    }
}


def apply_scores(name: str, sc: dict):
    fpath = VAL_DIR / f"{name}.json"
    raw = json.loads(fpath.read_text(encoding="utf-8"))

    total = sum(s["score"] for s in sc.values())
    new_verdict = verdict_from_score(total)

    raw["score_breakdown"] = {
        "quality":      sc["quality"],
        "valuation":    sc["valuation"],
        "catalyst":     sc["catalyst"],
        "risk_reward":  sc["risk_reward"],
        "total":        total,
        "band": (
            "매수 (67~100)" if total >= 67
            else "중립 (34~66)" if total >= 34
            else "부정 (0~33)"
        ),
    }
    raw["ic_memo"]["investment_verdict"] = new_verdict
    raw["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    fpath.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {name}: Q{sc['quality']['score']} + V{sc['valuation']['score']} + Ca{sc['catalyst']['score']} + R/R{sc['risk_reward']['score']} = {total} → {new_verdict}")


def main():
    for name, sc in SCORES.items():
        apply_scores(name, sc)


if __name__ == "__main__":
    main()
