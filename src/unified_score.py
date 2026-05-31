"""Unified Score — alpha_score (펀더멘털) + confluence_score (모멘텀) 통합.

두 score system 의 다른 차원:
  - alpha_score (0-10):
      6 차원 (value/quality/momentum/profitability/balance/governance)
      *펀더멘털 기반*, deep dive 결과
  - confluence_score (0-100):
      4 신호 (Growth/Earnings/Breakout/Flow) + Catalyst
      *모멘텀 기반*, dynamic discovery

Unified scaling (0-100):
  - alpha_normalized = alpha_score × 10
  - confluence_normalized = confluence_score (이미 0-100)

Unified blend:
  - 둘 다 있으면:  0.5 × alpha_n + 0.5 × confluence
                  → high_confidence (양쪽 검증)
  - alpha 만:     alpha_n (단일 — 펀더멘털 검증)
  - confluence 만: confluence (단일 — 모멘텀 발견)
  - 둘 다 없음:   None

Labels:
  ★ Unified ≥ 80: 양쪽 hit + 강한 신호 → 최우선 alpha bet 후보
  ◐ Unified ≥ 65: 한쪽 강, 다른 쪽 보통
  ○ Unified ≥ 50: 단일 신호 — 검증 필요
  · < 50:         skip
"""
from __future__ import annotations

from typing import Any

from .utils import get_logger

log = get_logger("unified_score")


def compute_unified_score(
    alpha_score: float | None = None,       # 0-10
    confluence_score: float | None = None,  # 0-100
    blend_weight_alpha: float = 0.5,
) -> dict[str, Any]:
    """두 score 를 unified 0-100 으로 통합.

    Returns:
        {
          "unified_score": float | None,
          "label": "★" | "◐" | "○" | "·",
          "tier": "high_confidence" | "alpha_only" | "confluence_only" | "weak" | None,
          "components": {alpha_normalized, confluence},
        }
    """
    out: dict[str, Any] = {
        "unified_score": None,
        "label": "·",
        "tier": None,
        "components": {
            "alpha_normalized": (alpha_score * 10) if alpha_score is not None else None,
            "confluence": confluence_score,
        },
    }

    alpha_n = (alpha_score * 10) if alpha_score is not None else None
    conf = confluence_score

    if alpha_n is not None and conf is not None:
        unified = blend_weight_alpha * alpha_n + (1 - blend_weight_alpha) * conf
        tier = "high_confidence"
    elif alpha_n is not None:
        unified = alpha_n
        tier = "alpha_only"
    elif conf is not None:
        unified = conf
        tier = "confluence_only"
    else:
        return out  # 둘 다 없음

    out["unified_score"] = round(unified, 1)
    out["tier"] = tier

    # Label
    if unified >= 80:
        out["label"] = "★"
    elif unified >= 65:
        out["label"] = "◐"
    elif unified >= 50:
        out["label"] = "○"
    else:
        out["label"] = "·"

    return out


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """candidates 에 unified_score 부여 + 정렬.

    candidates 각각:
        - "alpha_score" (0-10) 또는 "score" (alpha) 키 (optional)
        - "confluence_score" 키 (optional)

    Returns: unified_score 내림차순.
    """
    out = []
    for c in candidates:
        alpha = c.get("alpha_score") or c.get("score")  # 'score' = alpha (legacy)
        conf = c.get("confluence_score")
        unified = compute_unified_score(alpha_score=alpha, confluence_score=conf)
        c["unified"] = unified
        out.append(c)

    out.sort(
        key=lambda c: (
            # high_confidence > alpha_only/confluence_only > weak
            {"high_confidence": 0, "alpha_only": 1, "confluence_only": 1, "weak": 2}.get(
                (c.get("unified") or {}).get("tier"), 3),
            -((c.get("unified") or {}).get("unified_score") or 0),
        )
    )
    return out


def format_unified_chip(unified: dict) -> str:
    """텔레그램/UI 용 한 줄 chip."""
    if not unified or unified.get("unified_score") is None:
        return ""
    label = unified.get("label", "")
    score = unified.get("unified_score", 0)
    tier = unified.get("tier", "")
    tier_ko = {
        "high_confidence": "양쪽 검증",
        "alpha_only": "펀더멘털 only",
        "confluence_only": "모멘텀 only",
    }.get(tier, "")
    return f"{label} Unified {score:.0f}/100" + (f" ({tier_ko})" if tier_ko else "")


if __name__ == "__main__":
    # 단위 테스트
    samples = [
        ("Both hit", 8.5, 72),
        ("Alpha only", 8.2, None),
        ("Confluence only", None, 78),
        ("Weak", 4.5, 35),
        ("Neither", None, None),
    ]
    for name, a, c in samples:
        result = compute_unified_score(a, c)
        print(f"{name:20s} α={a}/10  conf={c}/100  → unified={result['unified_score']} {result['label']} ({result['tier']})")
