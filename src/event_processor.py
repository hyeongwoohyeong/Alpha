"""뉴스/이벤트 메타데이터 분류기.

뉴스가 단순 제목 수집으로 끝나지 않게, 다음 메타를 모두 부여한다.

- event_status: 진행 중 / 완료 / 종료 / 무산 / 확인 필요  (키워드 기반)
- staleness: fresh / aging / stale / outdated  (날짜 기반)
- source_quality: High / Medium / Low  (출처명 기반)
- confidence: High / Medium / Low  (종합)

오래된 뉴스가 Daily Brief나 Action Tag에 잘못 반영되지 않도록 필터링하는
근거가 된다.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 상태 키워드 사전
# ---------------------------------------------------------------------------

# 거래/사건 완료
CLOSED_KEYWORDS: set[str] = {
    "completed", "completes", "closes the deal", "deal closed", "deal completed",
    "finalized", "regulatory approval received",
    "merger completed", "acquisition completed",
    "완료", "거래 완료", "인수 완료", "합병 완료", "승인 완료",
}

# 거래/사건 종료·무산·철회
TERMINATED_KEYWORDS: set[str] = {
    "terminated", "abandoned", "withdrew", "withdraws bid", "drops bid",
    "walks away", "ends pursuit", "no longer pursuing", "deal terminated",
    "failed bid", "bid withdrawn", "rival bid withdrawn",
    "철회", "무산", "포기", "인수전 종료", "불발", "더 이상 추진하지 않",
}

# 진행 중
ONGOING_KEYWORDS: set[str] = {
    "considering", "explores", "explore", "weighs", "in talks",
    "reportedly interested", "potential acquisition", "potential merger",
    "regulatory review", "pending approval", "antitrust review",
    "검토", "논의", "추진", "가능성", "인수설", "제안",
    "승인 대기", "규제 심사", "반독점 심사",
}

# Urgent risk (anti-thesis 고려 대상)
URGENT_RISK_KEYWORDS: set[str] = {
    "fraud", "investigation", "subpoena", "bankruptcy", "going concern",
    "lawsuit", "restatement", "secondary offering", "dilution", "halt",
    "recall", "probe",
}


# ---------------------------------------------------------------------------
# 이벤트 상태 분류
# ---------------------------------------------------------------------------

def classify_event_status(text: str) -> str:
    """뉴스 제목/요약 텍스트에서 event_status 판단.

    우선순위: 완료 > 종료/무산 > 진행 중 > 확인 필요
    """
    if not text:
        return "확인 필요"
    s = text.lower()
    if any(k in s for k in CLOSED_KEYWORDS):
        return "완료"
    if any(k in s for k in TERMINATED_KEYWORDS):
        return "종료"
    if any(k in s for k in ONGOING_KEYWORDS):
        return "진행 중"
    return "확인 필요"


def is_urgent_risk(text: str) -> bool:
    if not text:
        return False
    s = text.lower()
    return any(k in s for k in URGENT_RISK_KEYWORDS)


# ---------------------------------------------------------------------------
# 출처 신뢰도
# ---------------------------------------------------------------------------

HIGH_QUALITY_SOURCES: set[str] = {
    "reuters", "bloomberg", "wsj", "wall street journal", "financial times",
    "ft.com", "cnbc", "press release", "company release", "ir release",
    "sec filing", "edgar", "nasdaq", "nyse",
    "회사 공식", "프레스 릴리스", "공시", "company press release",
}

MEDIUM_QUALITY_SOURCES: set[str] = {
    "yahoo finance", "yahoo", "marketwatch", "seeking alpha",
    "investing.com", "barron", "barrons", "the information", "fortune",
    "business insider", "the motley fool", "fool.com",
}


def source_quality_from_name(source: str) -> str:
    if not source:
        return "Low"
    s = source.lower()
    if any(k in s for k in HIGH_QUALITY_SOURCES):
        return "High"
    if any(k in s for k in MEDIUM_QUALITY_SOURCES):
        return "Medium"
    return "Low"


def aggregate_source_quality(sources: Iterable[str]) -> str:
    qualities = [source_quality_from_name(s) for s in (sources or [])]
    if "High" in qualities:
        return "High"
    if "Medium" in qualities:
        return "Medium"
    return "Low" if qualities else "Low"


# ---------------------------------------------------------------------------
# Staleness (시간 기반)
# ---------------------------------------------------------------------------

STALENESS_LABEL: dict[str, str] = {
    "fresh": "Fresh",
    "aging": "Aging",
    "stale": "Stale",
    "outdated": "Outdated",
}


def _parse_date(s) -> _dt.date | None:
    if not s:
        return None
    if isinstance(s, _dt.date) and not isinstance(s, _dt.datetime):
        return s
    if isinstance(s, _dt.datetime):
        return s.date()
    s = str(s).strip().replace("/", "-").replace(".", "-")
    # ISO 우선
    try:
        return _dt.date.fromisoformat(s[:10])
    except Exception:
        pass
    # RFC2822 (Google News RSS published 형식)
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return _dt.datetime.strptime(str(s), fmt).date()
        except Exception:
            continue
    # 'YYYY-MM' 같은 부분 형식 — 월 중순으로 처리
    parts = s.split("-")
    try:
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            y, m = int(parts[0]), int(parts[1])
            d = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 15
            return _dt.date(y, m, min(d, 28))
    except Exception:
        pass
    return None


def compute_staleness(published_at, today: _dt.date | None = None) -> str:
    today = today or _dt.date.today()
    d = _parse_date(published_at)
    if d is None:
        return "outdated"
    days = (today - d).days
    if days < 0:
        days = 0
    if days <= 7:
        return "fresh"
    if days <= 30:
        return "aging"
    if days <= 90:
        return "stale"
    return "outdated"


def staleness_label(key: str) -> str:
    return STALENESS_LABEL.get(key, "Outdated")


# ---------------------------------------------------------------------------
# Confidence 종합
# ---------------------------------------------------------------------------

def confidence_from(
    source_quality: str,
    status: str,
    source_count: int = 1,
    staleness: str = "fresh",
) -> str:
    if source_quality == "High" and source_count >= 1:
        # 단, outdated면 한 단계 약화
        if staleness == "outdated":
            return "Medium"
        return "High"
    if source_quality == "Medium":
        if staleness == "outdated":
            return "Low"
        return "Medium" if source_count >= 2 else "Low"
    if status == "확인 필요":
        return "Low"
    return "Low"


# ---------------------------------------------------------------------------
# 뉴스 enrich (한 번에)
# ---------------------------------------------------------------------------

def enrich_news(news: dict[str, Any]) -> dict[str, Any]:
    title = news.get("title", "") or ""
    summary = news.get("summary", "") or ""
    source = news.get("source", "") or ""
    text = f"{title} {summary}"

    status = classify_event_status(text)
    src_q = source_quality_from_name(source)
    staleness = compute_staleness(news.get("published_at"))
    conf = confidence_from(src_q, status, source_count=1, staleness=staleness)
    urgent = is_urgent_risk(text)

    return {
        **news,
        "event_status": status,
        "staleness": staleness,
        "staleness_label": staleness_label(staleness),
        "source_quality": src_q,
        "confidence": conf,
        "is_urgent": urgent,
    }


def enrich_news_list(news_list: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_news(n) for n in (news_list or [])]


# ---------------------------------------------------------------------------
# Thesis Impact 변환표 (status × score → thesis_impact)
# ---------------------------------------------------------------------------

THESIS_IMPACT_VALUES: tuple[str, ...] = (
    "Thesis 강화",
    "Thesis 약화",
    "리스크 해소",
    "신규 리스크",
    "단기 노이즈",
    "확인 필요",
)


def thesis_impact_from(
    event_status: str,
    importance_score: float,
    is_urgent: bool = False,
    staleness: str = "fresh",
) -> str:
    """status × score 결합으로 thesis_impact 자동 결정.

    - 종료/완료 + 부정 키워드(과거) → 리스크 해소 (이전 부정 이벤트 종료)
    - 종료/완료 + 긍정 키워드 → Thesis 강화
    - 진행 중 + 부정/urgent → 신규 리스크
    - 진행 중 + 긍정 강함 → Thesis 강화
    - outdated → 단기 노이즈
    - 그 외 → 확인 필요
    """
    if staleness == "outdated":
        return "단기 노이즈"
    if event_status in ("종료", "완료"):
        if importance_score < -0.5 or is_urgent:
            return "리스크 해소"
        if importance_score >= 0.5:
            return "Thesis 강화"
        return "확인 필요"
    if event_status == "진행 중":
        if is_urgent or importance_score < -1.0:
            return "신규 리스크"
        if importance_score >= 1.0:
            return "Thesis 강화"
        if importance_score < -0.3:
            return "Thesis 약화"
        return "확인 필요"
    if event_status == "무산":
        return "리스크 해소" if importance_score < 0 else "확인 필요"
    return "확인 필요"


def aggregate_thesis_impact(events: Iterable[dict[str, Any]]) -> str:
    """종목별 종합 thesis_impact (가장 강한 신호 우선)."""
    impacts = [e.get("thesis_impact") for e in (events or []) if e.get("thesis_impact")]
    if not impacts:
        return "확인 필요"
    # 우선순위: 신규 리스크 > 리스크 해소 > Thesis 강화 > Thesis 약화 > 확인 필요 > 단기 노이즈
    priority = ["신규 리스크", "리스크 해소", "Thesis 강화", "Thesis 약화", "확인 필요", "단기 노이즈"]
    for p in priority:
        if p in impacts:
            return p
    return "확인 필요"


# ---------------------------------------------------------------------------
# 뉴스 클러스터링 — 같은 이벤트의 여러 뉴스를 event_id로 묶음
# ---------------------------------------------------------------------------

import hashlib
import re as _re


_TOPIC_KEYWORDS: dict[str, set[str]] = {
    "ma": {
        "acquisition", "acquires", "acquired", "merger", "merges", "buyout",
        "deal", "bid", "takeover", "인수", "합병", "거래", "입찰",
    },
    "earnings": {
        "earnings", "results", "quarter", "q1", "q2", "q3", "q4",
        "실적", "분기", "연간", "guidance", "가이던스",
    },
    "regulation": {
        "regulator", "regulation", "antitrust", "doj", "ftc", "sec",
        "tariff", "sanction", "regulatory",
        "규제", "심사", "관세", "제재", "공정위",
    },
    "lawsuit": {
        "lawsuit", "sued", "court", "settlement", "ruling",
        "소송", "판결", "합의", "법적",
    },
    "product": {
        "launch", "unveil", "release", "rollout", "introduces",
        "출시", "발표", "공개",
    },
    "personnel": {
        "ceo", "cfo", "resigns", "appointed", "step down", "departure",
        "사임", "임명", "교체", "퇴임",
    },
    "buyback": {
        "buyback", "repurchase", "dividend",
        "자사주", "배당", "환원",
    },
}


def _extract_topics(text: str) -> set[str]:
    if not text:
        return set()
    s = text.lower()
    out: set[str] = set()
    for topic, kws in _TOPIC_KEYWORDS.items():
        if any(kw in s for kw in kws):
            out.add(topic)
    return out


_STOPWORDS: set[str] = {
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "by",
    "and", "or", "is", "are", "was", "were", "be", "been", "as", "at",
    "from", "this", "that", "it", "its", "into", "after", "before",
    "회사", "공시", "보도", "발표", "관련",
}


def _signature_tokens(title: str, max_tokens: int = 6) -> tuple[str, ...]:
    if not title:
        return ()
    s = _re.sub(r"[^a-zA-Z0-9가-힣\s]", " ", title.lower())
    tokens = [t for t in s.split() if t and t not in _STOPWORDS and len(t) >= 2]
    return tuple(sorted(set(tokens))[:max_tokens])


def _make_event_id(ticker: str, topics: set[str], sig: tuple[str, ...]) -> str:
    raw = f"{ticker.upper()}|{'+'.join(sorted(topics))}|{'+'.join(sig)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def cluster_news_by_event(
    news_list: Iterable[dict[str, Any]],
    ticker: str = "",
) -> list[dict[str, Any]]:
    """뉴스 리스트를 event_id 기반 클러스터로 묶는다.

    각 클러스터:
    - event_id: ticker + topic 시그니처 + 핵심 토큰 hash
    - members: 원본 뉴스 list
    - latest: 가장 최신 뉴스 (status / 보드 결정의 근거)
    - status: members 중 가장 최신 'completed/terminated' 우선순위
    - thesis_impact: status + score 기반
    - confidence: members source quality 평균
    """
    items = list(news_list or [])
    if not items:
        return []

    # 1) topic + signature → event_id
    enriched: list[dict[str, Any]] = []
    for n in items:
        e = enrich_news(n)
        title = e.get("title", "") or ""
        summary = e.get("summary", "") or ""
        text = f"{title} {summary}"
        topics = _extract_topics(text)
        sig = _signature_tokens(title)
        eid = _make_event_id(
            ticker or e.get("ticker", "") or "?",
            topics or {"general"},
            sig or (str(len(title)),),
        )
        e["event_id"] = eid
        e["topics"] = topics
        enriched.append(e)

    # 2) event_id 별로 그룹
    clusters: dict[str, list[dict[str, Any]]] = {}
    for e in enriched:
        clusters.setdefault(e["event_id"], []).append(e)

    out: list[dict[str, Any]] = []
    for eid, members in clusters.items():
        # 가장 최신 뉴스 결정 (published_at 기준)
        def _key(m):
            d = _parse_date(m.get("published_at"))
            return d or _dt.date.min
        members_sorted = sorted(members, key=_key, reverse=True)
        latest = members_sorted[0]
        # status는 멤버 중 종료/완료가 있으면 그것을 우선
        statuses = [m.get("event_status") for m in members]
        if "완료" in statuses:
            cluster_status = "완료"
        elif "종료" in statuses:
            cluster_status = "종료"
        elif "진행 중" in statuses:
            cluster_status = "진행 중"
        else:
            cluster_status = latest.get("event_status", "확인 필요")
        # 종합 importance score (멤버 평균 가중)
        score_sum = sum(m.get("importance_score", 0) or 0 for m in members)
        is_urgent = any(m.get("is_urgent") for m in members)
        cluster_staleness = latest.get("staleness", "outdated")
        impact = thesis_impact_from(
            cluster_status, score_sum, is_urgent, cluster_staleness
        )
        # 출처 신뢰도 평균
        src_q = aggregate_source_quality([m.get("source", "") for m in members])
        conf = confidence_from(
            src_q, cluster_status, source_count=len(members), staleness=cluster_staleness
        )
        out.append(
            {
                "event_id": eid,
                "members": members_sorted,
                "latest": latest,
                "status": cluster_status,
                "thesis_impact": impact,
                "confidence": conf,
                "staleness": cluster_staleness,
                "source_quality": src_q,
                "score_sum": round(score_sum, 3),
                "title": latest.get("title", ""),
                "topics": list(latest.get("topics", [])),
            }
        )
    # 최신 순 정렬
    out.sort(
        key=lambda c: _parse_date(c["latest"].get("published_at")) or _dt.date.min,
        reverse=True,
    )
    return out


# ---------------------------------------------------------------------------
# 큐레이션 이벤트 보강 (status/last_updated/confidence 누락 시 채움)
# ---------------------------------------------------------------------------

def enrich_curated_event(ev: dict[str, Any], today: _dt.date | None = None) -> dict[str, Any]:
    out = dict(ev)
    # status 기본값
    if not out.get("status"):
        out["status"] = classify_event_status(
            f"{out.get('type','')} {out.get('summary','')}"
        )
    # last_updated 기본값 (큐레이션 데이터에 last_updated가 없으면 date 사용)
    if not out.get("last_updated"):
        out["last_updated"] = out.get("date", "")
    # confidence 기본값
    if not out.get("confidence"):
        sources = out.get("sources") or []
        agg_q = aggregate_source_quality(sources) if sources else "Medium"
        staleness = compute_staleness(out.get("last_updated"), today=today)
        out["confidence"] = confidence_from(
            agg_q,
            out["status"],
            source_count=len(sources) if sources else 1,
            staleness=staleness,
        )
        out["staleness"] = staleness
        out["staleness_label"] = staleness_label(staleness)
    else:
        if not out.get("staleness"):
            staleness = compute_staleness(out.get("last_updated"), today=today)
            out["staleness"] = staleness
            out["staleness_label"] = staleness_label(staleness)
    if not out.get("source_quality"):
        out["source_quality"] = aggregate_source_quality(out.get("sources") or [])
    return out
