"""프리라이프 텔레그램 채널 요약 — briefing_engine 용 섹션 빌더.

t.me/s/free_life59 공개 채널 웹 프리뷰 scrape → 신규 메시지 추출 →
LLM 요약 → list[str] 반환 (briefing_engine._join 포맷).

외부 의존: requests, openai (optional)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("tg_freelife")

CHANNEL_URL = "https://t.me/s/free_life59"
STATE_FILE   = Path(__file__).resolve().parents[1] / "data" / "tg_freelife_state.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_SYSTEM_PROMPT = """당신은 투자 리서치 어시스턴트입니다.
보유 포트폴리오: 나스닥 레버리지(QLD/TQQQ), SCHD, SK하이닉스 연계 KODEX 2X.
관심 섹터: AI 인프라, 반도체(HBM/NAND), 빅테크, 전력·에너지.

아래 텔레그램 채널 '프리라이프' 최근 메시지를 분석하여,
포트폴리오에 영향을 줄 수 있는 핵심 인사이트만 3~5개 추려 요약하세요.

형식 (항목당 2줄):
📌 [주제] 요약 (1줄)
→ 포트폴리오 영향: [영향 한 줄]

사소한 단신·단순 가격 언급은 제외하고, 구조적/장기적 변화에만 집중하세요."""


# ── Fetch & Parse ─────────────────────────────────────────────────────────────

def _fetch_html() -> str:
    import requests
    r = requests.get(CHANNEL_URL, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<a\s+href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"\2", s, flags=re.DOTALL)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return re.sub(r"\n{3,}", "\n\n", s.strip())


def _parse_messages(html: str) -> list[dict]:
    text_blocks = re.findall(
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL,
    )
    msg_ids = list(dict.fromkeys(
        int(x) for x in re.findall(r"t\.me/free_life59/(\d+)", html)
    ))

    messages = []
    for i, raw in enumerate(text_blocks):
        text = _strip_html(raw).strip()
        if len(text) < 30:
            continue
        messages.append({
            "msg_id": msg_ids[i] if i < len(msg_ids) else None,
            "text": text,
        })
    return messages


def _load_last_id() -> int:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text()).get("last_msg_id", 0)
    except Exception:
        pass
    return 0


def _save_last_id(msg_id: int) -> None:
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_msg_id": msg_id}))
    except Exception as e:
        log.warning("state 저장 실패: %s", e)


# ── LLM 요약 ─────────────────────────────────────────────────────────────────

def _summarize(messages: list[dict]) -> str:
    if not messages:
        return ""
    combined, total = [], 0
    for m in messages[-30:]:
        s = m["text"][:500]
        if total + len(s) > 7000:
            break
        combined.append(s)
        total += len(s)
    content = "\n\n---\n\n".join(combined)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": content},
                ],
                max_tokens=600,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            log.warning("OpenAI 요약 실패: %s", e)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            log.warning("Anthropic 요약 실패: %s", e)

    # LLM 없으면 최신 3개 텍스트 발췌
    snippets = [m["text"][:120].replace("\n", " ") for m in messages[-3:]]
    return "\n".join(f"• {s}" for s in snippets)


# ── Section Builder (briefing_engine 호출용) ──────────────────────────────────

def build_tg_freelife_section() -> list[str]:
    """프리라이프 채널 요약 섹션. 실패 시 빈 list 반환 (graceful)."""
    try:
        html      = _fetch_html()
        all_msgs  = _parse_messages(html)
        last_id   = _load_last_id()
        new_msgs  = [m for m in all_msgs if m.get("msg_id") and m["msg_id"] > last_id]

        # 최신 msg_id 저장
        ids = [m["msg_id"] for m in all_msgs if m.get("msg_id")]
        if ids:
            _save_last_id(max(ids))

        # 처음 실행이면 전체 최근 메시지 요약 (last_id == 0)
        target = new_msgs if new_msgs else (all_msgs if last_id == 0 else [])

        if not target:
            log.info("프리라이프: 신규 메시지 없음")
            return []

        summary = _summarize(target)
        if not summary:
            return []

        lines = [f"📡 프리라이프 채널 (신규 {len(target)}건)"]
        for line in summary.splitlines():
            if line.strip():
                lines.append(f"  {line}")
        return lines

    except Exception as e:
        log.warning("프리라이프 섹션 빌드 실패: %s", e)
        return []
