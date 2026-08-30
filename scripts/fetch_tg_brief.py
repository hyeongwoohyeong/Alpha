"""프리라이프 텔레그램 채널 요약 브리핑.

t.me/s/free_life59 공개 채널 웹 프리뷰를 scrape → 최근 메시지 추출 →
Claude/OpenAI LLM 으로 요약 → 텔레그램 Bot 으로 전송.

사용:
  python scripts/fetch_tg_brief.py          # 기본 (최근 20개 메시지)
  python scripts/fetch_tg_brief.py --dry    # 전송 없이 콘솔 출력만

환경 변수:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (전송용)
  OPENAI_API_KEY or ANTHROPIC_API_KEY   (LLM 요약)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

log = logging.getLogger("fetch_tg_brief")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHANNEL_URL = "https://t.me/s/free_life59"
STATE_FILE   = ROOT / "data" / "tg_freelife_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


# ──────────────────────────────────────────────────────────────────────────────
# Fetch + Parse
# ──────────────────────────────────────────────────────────────────────────────

def fetch_channel_html() -> str:
    """t.me/s/{channel} HTML 가져오기."""
    r = requests.get(CHANNEL_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_messages(html: str) -> list[dict]:
    """HTML에서 메시지 블록 파싱.

    Returns list of:
      {"msg_id": int, "text": str, "forwarded_from": str|None, "time": str, "links": [str]}
    """
    messages = []

    # 메시지 블록 탐색 — t.me/s/{channel}/{id} 링크를 기준으로 분리
    # 각 메시지는 href="https://t.me/free_life59/{N}" 패턴으로 시작
    block_pattern = re.compile(
        r'href="https://t\.me/free_life59/(\d+)[^"]*".*?'
        r'(?=href="https://t\.me/free_life59/\d+|$)',
        re.DOTALL,
    )

    # 더 단순한 방식: tgme_widget_message_text 클래스 블록 추출
    text_blocks = re.findall(
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL,
    )

    # msg id 추출
    msg_ids = re.findall(r't\.me/free_life59/(\d+)', html)
    msg_ids_clean = list(dict.fromkeys(int(x) for x in msg_ids))  # 중복 제거, 순서 유지

    # forwarded from 추출
    fwd_blocks = re.findall(
        r'tgme_widget_message_forwarded_from.*?href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )

    def strip_html(s: str) -> str:
        """HTML 태그 제거 + 엔티티 변환."""
        s = re.sub(r'<br\s*/?>', '\n', s)
        s = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r'\2 [\1]', s, flags=re.DOTALL)
        s = re.sub(r'<[^>]+>', '', s)
        s = s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
        s = re.sub(r'\n{3,}', '\n\n', s.strip())
        return s

    for i, raw in enumerate(text_blocks):
        text = strip_html(raw).strip()
        if len(text) < 20:           # 너무 짧은 블록 (이미지 캡션 등) 스킵
            continue

        # 링크 추출
        links = re.findall(r'https?://[^\s\]"<>]+', raw)

        # msg_id 매핑 (순서 기준)
        msg_id = msg_ids_clean[i] if i < len(msg_ids_clean) else None

        # forwarded_from (간단히 i번째)
        fwd_from = None
        if i < len(fwd_blocks):
            fwd_from = strip_html(fwd_blocks[i][1]).strip()

        messages.append({
            "msg_id": msg_id,
            "text": text,
            "forwarded_from": fwd_from,
            "links": links[:3],        # 최대 3개
        })

    return messages


def filter_new_messages(messages: list[dict]) -> list[dict]:
    """이전 실행 이후 새 메시지만 반환 (msg_id 기준)."""
    STATE_FILE.parent.mkdir(exist_ok=True)
    last_id = 0
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last_id = state.get("last_msg_id", 0)
        except Exception:
            pass

    new_msgs = [m for m in messages if m.get("msg_id") and m["msg_id"] > last_id]

    # 최신 id 저장
    all_ids = [m["msg_id"] for m in messages if m.get("msg_id")]
    if all_ids:
        STATE_FILE.write_text(json.dumps({"last_msg_id": max(all_ids)}))

    return new_msgs


# ──────────────────────────────────────────────────────────────────────────────
# LLM 요약
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 투자 리서치 어시스턴트입니다.
사용자의 포트폴리오는 다음과 같습니다:
- 코어: 나스닥 레버리지 ETF (QLD/TQQQ), SCHD
- 보유: SK하이닉스 연계 KODEX 2X (반도체)
- 관심: AI 인프라, 반도체, 빅테크, 전력/에너지

아래는 텔레그램 채널 '프리라이프'의 최근 메시지들입니다.
중요한 인사이트를 간결하게 요약하고, 포트폴리오에 미칠 영향을 한 줄로 평가해주세요.

형식:
📌 [주제] 요약 (1-2줄)
→ 포트폴리오 영향: [영향 설명]

중요도가 낮은 단순 뉴스 나열은 생략하고, 구조적/장기적 변화에 해당하는 것만 포함하세요.
최대 5개 항목."""


def summarize_with_llm(messages: list[dict]) -> str:
    """LLM으로 메시지 요약. OpenAI → Anthropic 순으로 시도."""
    if not messages:
        return "새 메시지 없음."

    # 메시지 텍스트 조합 (최대 8000자)
    combined = []
    total = 0
    for m in messages[-30:]:   # 최신 30개까지
        snippet = m["text"][:600]
        if total + len(snippet) > 8000:
            break
        combined.append(snippet)
        total += len(snippet)

    content = "\n\n---\n\n".join(combined)

    # OpenAI 시도
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                max_tokens=800,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            log.warning("OpenAI 요약 실패: %s", e)

    # Anthropic 시도
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            log.warning("Anthropic 요약 실패: %s", e)

    # LLM 없으면 단순 요약
    log.warning("LLM 없음 — 단순 텍스트 합산 반환")
    lines = []
    for m in messages[-5:]:
        snippet = m["text"][:200].replace('\n', ' ')
        lines.append(f"• {snippet}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 텔레그램 전송
# ──────────────────────────────────────────────────────────────────────────────

def send_brief(summary: str, new_count: int, total_count: int) -> None:
    """요약 텔레그램 전송."""
    from src.telegram_notifier import send_telegram_plain

    header = f"📡 프리라이프 브리핑 (신규 {new_count}/{total_count}개)\n{'─'*30}\n"
    footer = f"\n{'─'*30}\n🔗 t.me/free_life59"
    msg = header + summary + footer

    # 4096자 Telegram 한도
    if len(msg) > 4000:
        msg = msg[:3990] + "…"

    result = send_telegram_plain(msg)
    if result.get("ok"):
        log.info("텔레그램 전송 완료")
    elif result.get("skipped"):
        log.info("텔레그램 자격증명 없음 — 콘솔 출력")
        print(msg)
    else:
        log.warning("텔레그램 전송 실패: %s", result.get("error"))
        print(msg)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False, force_all: bool = False) -> None:
    log.info("프리라이프 채널 fetch 시작")
    html = fetch_channel_html()

    all_messages = parse_messages(html)
    log.info("파싱된 메시지: %d개", len(all_messages))

    if force_all:
        new_messages = all_messages
    else:
        new_messages = filter_new_messages(all_messages)

    log.info("신규 메시지: %d개", len(new_messages))

    if not new_messages:
        log.info("새 메시지 없음 — 전송 skip")
        return

    summary = summarize_with_llm(new_messages)

    if dry_run:
        print("=" * 50)
        print(f"[DRY RUN] 신규 {len(new_messages)}/{len(all_messages)}개")
        print(summary)
        print("=" * 50)
    else:
        send_brief(summary, len(new_messages), len(all_messages))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="프리라이프 텔레그램 브리핑")
    parser.add_argument("--dry", action="store_true", help="전송 없이 콘솔 출력")
    parser.add_argument("--all", action="store_true", help="신규 필터 없이 전체 메시지 요약")
    args = parser.parse_args()
    main(dry_run=args.dry, force_all=args.all)
