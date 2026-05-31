"""Telegram Bot HTTPS API sender — 의존성 최소화 (requests 만).

사용법:
    from src.telegram_notifier import send_telegram
    send_telegram("🎯 KODEX 하이닉스 익절 트리거 도달!")

환경 변수 (또는 Streamlit Secrets):
    TELEGRAM_BOT_TOKEN: BotFather 에서 받은 토큰
    TELEGRAM_CHAT_ID: 본인 chat_id (or 그룹 chat_id, 음수)

설계 원칙:
- rule-based: 토큰 없으면 graceful (log 만 남기고 skip)
- markdown_v2 escape: 한글 문자열 안전
- rate limit (Telegram: 분당 30 msg / chat) 의식 — 호출부에서 dedup
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests

from .utils import get_logger

log = get_logger("telegram_notifier")

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"
_MD2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def _md2_escape(text: str) -> str:
    """Telegram MarkdownV2 escape — 특수문자 backslash."""
    return re.sub(r"([" + re.escape(_MD2_SPECIAL) + r"])", r"\\\1", text)


def _get_creds() -> tuple[str | None, str | None]:
    """환경변수 또는 streamlit secrets 에서 토큰·chat_id 로드."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    # Streamlit secrets fallback
    if not token or not chat_id:
        try:
            import streamlit as st  # type: ignore
            token = token or st.secrets.get("TELEGRAM_BOT_TOKEN", None)
            chat_id = chat_id or st.secrets.get("TELEGRAM_CHAT_ID", None)
        except Exception:
            pass
    return token, chat_id


def send_telegram(
    text: str,
    parse_mode: str = "MarkdownV2",
    disable_notification: bool = False,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    """텔레그램 메시지 송신. 토큰 없으면 graceful skip.

    Returns: {"ok": bool, "skipped": bool, "error": str|None}
    """
    token, chat_id = _get_creds()
    if not token or not chat_id:
        log.info("Telegram creds 없음 — skip (text: %s)", text[:60])
        return {"ok": False, "skipped": True, "error": "no_creds"}

    if parse_mode == "MarkdownV2":
        text = _md2_escape(text)

    url = _TG_API.format(token=token)
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=timeout_sec)
        if r.status_code == 200:
            return {"ok": True, "skipped": False, "error": None}
        log.warning("Telegram 송신 실패 status=%s body=%s", r.status_code, r.text[:200])
        return {"ok": False, "skipped": False, "error": f"http_{r.status_code}: {r.text[:160]}"}
    except Exception as e:
        log.warning("Telegram 송신 예외: %s", e)
        return {"ok": False, "skipped": False, "error": str(e)}


def send_telegram_plain(text: str, **kwargs) -> dict[str, Any]:
    """parse_mode 없는 plain text 송신 — escape 안 함, 간편."""
    return send_telegram(text, parse_mode="", **kwargs)
