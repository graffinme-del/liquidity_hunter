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
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=15) as r:
                await r.text()
    except Exception:
        logger.debug("deleteMessage failed", exc_info=True)


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
                            asyncio.create_task(
                                _delete_message_after(str(cid), mid, token, float(delete_after_sec))
                            )
                    except (json.JSONDecodeError, TypeError):
                        pass
                return ok
    except Exception as e:
        print(f"[TG] Ошибка отправки: {e}")
        return False
