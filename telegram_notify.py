"""
Отправка в Telegram: опционально deleteMessage через N секунд (шумные списки).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)


def ephemeral_delete_seconds() -> int:
    """TELEGRAM_DELETE_ALERTS_AFTER_SEC (по умолчанию 300); 0 = не удалять."""
    try:
        return max(0, int(os.getenv("TELEGRAM_DELETE_ALERTS_AFTER_SEC", "300")))
    except ValueError:
        return 300


async def _delete_message_after(chat_id: str, message_id: int, token: str, delay_sec: float) -> None:
    await asyncio.sleep(delay_sec)
    await delete_message_now(chat_id, message_id, token)


def schedule_delete_message(chat_id: str, message_id: int, token: str, delay_sec: float) -> None:
    """Удалить сообщение через delay_sec секунд (фоновая задача)."""
    if delay_sec <= 0:
        asyncio.create_task(delete_message_now(chat_id, message_id, token))
    else:
        asyncio.create_task(_delete_message_after(chat_id, message_id, token, float(delay_sec)))


async def delete_message_now(chat_id: str, message_id: int, token: str | None = None) -> bool:
    """Мгновенное deleteMessage (сообщения пользователя или бота)."""
    tok = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not tok:
        return False
    url = f"https://api.telegram.org/bot{tok}/deleteMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=15) as r:
                await r.text()
                return r.status == 200
    except Exception:
        logger.debug("deleteMessage failed", exc_info=True)
        return False


async def send_telegram(
    text: str,
    *,
    chat_id: str | None = None,
    parse_mode: str | None = "HTML",
    delete_after_sec: int | None = None,
) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not cid:
        print("[TG] TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {"chat_id": cid, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as r:
                body = await r.text()
                ok = r.status == 200
                if ok and delete_after_sec and delete_after_sec > 0:
                    try:
                        data = json.loads(body)
                        if not data.get("ok"):
                            return ok
                        mid = data.get("result", {}).get("message_id")
                        if isinstance(mid, int):
                            schedule_delete_message(str(cid), mid, token, float(delete_after_sec))
                    except (json.JSONDecodeError, TypeError):
                        pass
                return ok
    except Exception as e:
        print(f"[TG] Ошибка отправки: {e}")
        return False
