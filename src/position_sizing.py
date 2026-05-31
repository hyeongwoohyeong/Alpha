"""Position Sizing — Kelly Criterion 기반 알파 베팅 사이즈 자동 권장.

원리 (Kelly Criterion, 1956):
    f* = (p × b − q) / b
  where:
    p = win probability
    q = 1 − p (loss probability)
    b = win/loss ratio (avg_win / avg_loss)

목적:
  - 큰 손실 방지 (예: SOL -₩20M = NW 10% 손실은 too 큼)
  - hit rate 80% × win 사이즈 의 *최적 사이즈* 계산
  - Full Kelly 는 단일 베팅 -100% 위험 → Fractional Kelly (0.5x) + Cap (max 20% NW)

Hit rate / win-loss 추정:
  1. 사용자 track record (alpha_bets.lifetime_stats) — 가장 정확
  2. Confluence score 기반 학술 추정 — 신규 후보용

Cap 정책:
  - Single bet max 20% NW (분산)
  - Sector concentration max 50% (one catalyst 에 모두 X)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger("position_sizing")

_ALPHA_BETS_PATH = Path(__file__).resolve().parents[1] / "data" / "alpha_bets.json"

# Kelly 정책
KELLY_FRACTION = 0.5         # Fractional (default Half Kelly — 학술 권장)
MAX_SINGLE_BET_PCT = 0.20    # 단일 베팅 NW 의 20% cap
MIN_BET_PCT = 0.02           # 너무 작으면 의미 없음 (₩2M NW 기준 ₩40k 같은 경우)

# Confluence score → expected win probability (학술 추정)
# 0~50: random / 50~60: weak / 60~70: 평균 / 70~80: 강한 / 80+: very strong
_SCORE_TO_WIN_PROB = [
    (50,  0.40),
    (60,  0.50),
    (70,  0.60),
    (80,  0.70),
    (90,  0.80),
    (100, 0.85),
]

# Score 별 expected win/loss ratio (b)
_SCORE_TO_B = [
    (50,  0.50),
    (60,  0.80),
    (70,  1.00),
    (80,  1.50),
    (90,  2.00),
    (100, 3.00),
]


def _interp(thresholds: list[tuple[float, float]], score: float) -> float:
    """단계별 linear interpolation."""
    score = max(thresholds[0][0], min(thresholds[-1][0], score))
    for i in range(len(thresholds) - 1):
        x0, y0 = thresholds[i]
        x1, y1 = thresholds[i + 1]
        if x0 <= score <= x1:
            if x1 == x0:
                return y0
            t = (score - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return thresholds[-1][1]


def kelly_fraction(p: float, b: float) -> float:
    """Full Kelly fraction. 음수면 0 (베팅 가치 X)."""
    if b <= 0:
        return 0.0
    f = (p * b - (1 - p)) / b
    return max(0.0, f)


def load_user_track_record() -> dict[str, Any]:
    """사용자 alpha_bets lifetime_stats 에서 hit rate / avg_win / avg_loss 추출."""
    out = {"hit_rate": None, "avg_win_krw": None, "avg_loss_krw": None, "available": False}
    try:
        with open(_ALPHA_BETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        stats = data.get("lifetime_stats") or {}
        bets = data.get("bets") or []

        # hit rate
        out["hit_rate"] = stats.get("hit_rate")

        # avg_win / avg_loss — realized bets 에서 직접 계산
        wins = []
        losses = []
        for b in bets:
            realized = b.get("realized") or {}
            gain = realized.get("gain_krw_mm")
            if gain is None:
                continue
            if gain > 0:
                wins.append(gain)
            elif gain < 0:
                losses.append(abs(gain))

        if wins:
            out["avg_win_krw"] = sum(wins) / len(wins) * 1e6
        if losses:
            out["avg_loss_krw"] = sum(losses) / len(losses) * 1e6
        out["available"] = bool(wins) and bool(losses)
    except Exception as e:
        log.debug("track record 로드 실패: %s", e)
    return out


def recommend_size_for_bet(
    *,
    nw_krw: float,
    confluence_score: float | None = None,
    alpha_score: float | None = None,
    use_track_record: bool = True,
) -> dict[str, Any]:
    """단일 베팅 권장 사이즈 (Kelly + Fractional + Cap).

    Inputs:
        nw_krw: 현재 순자산
        confluence_score: 0-100 (있으면 win prob 추정)
        alpha_score: 0-10 (있으면 보강)
        use_track_record: True 면 사용자 본인 hit rate 도 활용

    Returns:
        {
          "nw_krw": float, "recommended_pct": float, "recommended_krw": float,
          "method": "track_record" | "score_based" | "blend",
          "components": {p, b, full_kelly, fractional, cap_applied},
          "warnings": [...],
        }
    """
    out: dict[str, Any] = {
        "nw_krw": nw_krw,
        "recommended_pct": 0.0,
        "recommended_krw": 0.0,
        "method": None,
        "components": {},
        "warnings": [],
    }

    # 1) win prob (p) 추정
    p_from_score = None
    b_from_score = None
    if confluence_score is not None:
        p_from_score = _interp(_SCORE_TO_WIN_PROB, confluence_score)
        b_from_score = _interp(_SCORE_TO_B, confluence_score)
    elif alpha_score is not None:
        # alpha_score 0-10 → confluence 0-100 equivalent
        equiv = alpha_score * 10
        p_from_score = _interp(_SCORE_TO_WIN_PROB, equiv)
        b_from_score = _interp(_SCORE_TO_B, equiv)

    # 2) 사용자 track record (있으면)
    p_from_track = None
    b_from_track = None
    if use_track_record:
        tr = load_user_track_record()
        if tr["available"]:
            p_from_track = tr["hit_rate"]
            if tr["avg_win_krw"] and tr["avg_loss_krw"]:
                b_from_track = tr["avg_win_krw"] / tr["avg_loss_krw"]

    # 3) Blend — 둘 다 있으면 평균, 한쪽 있으면 그것
    if p_from_score is not None and p_from_track is not None:
        p = 0.5 * p_from_score + 0.5 * p_from_track
        b = 0.5 * b_from_score + 0.5 * b_from_track
        out["method"] = "blend"
    elif p_from_score is not None:
        p, b = p_from_score, b_from_score
        out["method"] = "score_based"
    elif p_from_track is not None and b_from_track is not None:
        p, b = p_from_track, b_from_track
        out["method"] = "track_record"
    else:
        out["warnings"].append("hit rate 추정 불가 — score 또는 track record 필요")
        return out

    # 4) Kelly 계산
    full_kelly = kelly_fraction(p, b)
    fractional = full_kelly * KELLY_FRACTION

    # 5) Cap 적용
    capped = min(fractional, MAX_SINGLE_BET_PCT)
    if capped < fractional:
        out["warnings"].append(
            f"Kelly {fractional*100:.0f}% → cap {MAX_SINGLE_BET_PCT*100:.0f}% 적용 (단일 베팅 분산 위해)"
        )

    if capped < MIN_BET_PCT:
        out["warnings"].append(
            f"권장 사이즈 {capped*100:.1f}% 가 minimum ({MIN_BET_PCT*100:.0f}%) 미만 — 베팅 가치 낮음 (skip 권장)"
        )

    out["recommended_pct"] = capped
    out["recommended_krw"] = capped * nw_krw
    out["components"] = {
        "p_win": round(p, 3),
        "b_winloss_ratio": round(b, 2),
        "full_kelly_pct": round(full_kelly * 100, 1),
        "fractional_kelly_pct": round(fractional * 100, 1),
        "cap_applied": capped < fractional,
        "final_pct": round(capped * 100, 1),
    }
    return out


def format_size_recommendation(rec: dict, currency_label: str = "₩") -> str:
    """텔레그램/UI 용 한 줄 권장."""
    if rec["recommended_krw"] <= 0:
        return f"💼 권장 사이즈: skip (Kelly 음수)"
    krw_m = rec["recommended_krw"] / 1e6
    pct = rec["recommended_pct"] * 100
    components = rec.get("components", {})
    p = components.get("p_win", 0) * 100
    return (
        f"💼 권장 사이즈: {currency_label}{krw_m:.1f}M (NW의 {pct:.1f}%)\n"
        f"   p={p:.0f}% × Half-Kelly × cap → {pct:.1f}%"
    )


if __name__ == "__main__":
    # 단위 테스트
    samples = [
        ("Confluence 75 (good)", {"nw_krw": 180_000_000, "confluence_score": 75}),
        ("Confluence 60 (weak)",  {"nw_krw": 180_000_000, "confluence_score": 60}),
        ("Confluence 85 (strong)", {"nw_krw": 180_000_000, "confluence_score": 85}),
        ("Alpha 8.5 only",        {"nw_krw": 180_000_000, "alpha_score": 8.5}),
        ("No score",              {"nw_krw": 180_000_000}),
    ]
    print("=== Track record (사용자) ===")
    print(json.dumps(load_user_track_record(), indent=2, ensure_ascii=False))
    print()
    print("=== Size recommendations ===")
    for name, kwargs in samples:
        rec = recommend_size_for_bet(**kwargs)
        print(f"\n--- {name} ---")
        print(format_size_recommendation(rec))
        if rec.get("warnings"):
            for w in rec["warnings"]:
                print(f"   ⚠️ {w}")
