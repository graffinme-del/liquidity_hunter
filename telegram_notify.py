"""
Отправка в Telegram: опционально deleteMessage через N секунд (шумные списки).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)


def _load_dotenv_from_project() -> None:
    """Подтягиваем .env из каталога проекта (на случай другого cwd у процесса)."""
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(env_path)
    except Exception:
        pass


def _telegram_chat_id_resolved() -> str | None:
    raw = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].strip()
    return raw or None


def _strip_html_for_plain_fallback(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


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


# Inline-кнопка для принудительного обновления дайджеста волатильности (movement_scanner).
VOLATILE_DIGEST_CALLBACK = "lh_volatile_top"
VOLATILE_INLINE_KEYBOARD = {
    "inline_keyboard": [[{"text": "🔥 Обновить список", "callback_data": VOLATILE_DIGEST_CALLBACK}]]
}


async def answer_callback_query(
    callback_query_id: str,
    token: str | None = None,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """
    Обязательно вызвать для каждого callback_query (иначе у кнопки «вечная загрузка»).
    text — короткая подсказка (до ~200 симв.); show_alert — всплывающее окно вместо тоста.
    """
    tok = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not tok or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{tok}/answerCallbackQuery"
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    if show_alert:
        payload["show_alert"] = True
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as r:
                await r.text()
    except Exception:
        logger.debug("answerCallbackQuery failed", exc_info=True)


async def send_telegram(
    text: str,
    *,
    chat_id: str | None = None,
    parse_mode: str | None = "HTML",
    delete_after_sec: int | None = None,
    reply_markup: dict | None = None,
    timeout_sec: int = 120,
) -> bool:
    _load_dotenv_from_project()
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    cid = chat_id or _telegram_chat_id_resolved()
    if not token or not cid:
        print("[TG] ОШИБКА: TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы после загрузки .env", flush=True)
        return False
    # Явно видно, в какой чат ушло сообщение (частая путаница: .env = группа, смотрите личку и наоборот).
    print(f"[TG] sendMessage → chat_id={cid!r} символов={len(text)}", flush=True)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    async def _post(payload: dict) -> tuple[bool, dict | None, str]:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=timeout_sec) as r:
                body = await r.text()
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    return False, None, body
                return bool(data.get("ok")), data, body

    payload: dict = {"chat_id": cid, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        ok, data, body = await _post(payload)
        if not ok and parse_mode and data:
            desc = str((data.get("description") or data.get("parameters", {}))).lower()
            if "parse" in desc or "entities" in desc or "can't find" in desc:
                plain = _strip_html_for_plain_fallback(text)
                payload2 = {"chat_id": cid, "text": plain}
                if reply_markup is not None:
                    payload2["reply_markup"] = reply_markup
                ok, data, body = await _post(payload2)
                if ok:
                    print("[TG] повтор без HTML (ошибка разметки)", flush=True)

        if not ok:
            err = body[:1200] if body else ""
            print(f"[TG] sendMessage FAILED: {err}", flush=True)
            logger.warning("send_telegram: %s", err[:500])
            return False

        result = data.get("result") or {} if data else {}
        mid = result.get("message_id")
        chat_obj = result.get("chat") or {}
        chat_resp = chat_obj.get("id")
        print(
            f"[TG] sendMessage OK message_id={mid} chat_id={chat_resp} (отправлено в этот чат)",
            flush=True,
        )

        if delete_after_sec and delete_after_sec > 0:
            if isinstance(mid, int):
                schedule_delete_message(str(cid), mid, token, float(delete_after_sec))
                print(
                    f"[TG] запланировано удаление сообщения через {delete_after_sec} с",
                    flush=True,
                )
        return True
    except Exception as e:
        print(f"[TG] send_telegram исключение: {e}", flush=True)
        logger.warning("send_telegram: %s", e)
        return False
