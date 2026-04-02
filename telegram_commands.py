"""
Команды Telegram: winrate за период (как pnl_range в binance_pnl_bot).
Долгий polling getUpdates; запускается параллельно со сканером в main.py.

Удаление: сообщения пользователя (команды и даты) — сразу;
ответы бота (подсказка, отчёт, ошибки) — через TELEGRAM_WINRATE_BOT_MSG_DELETE_SEC (по умолчанию 120 с).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv
from pathlib import Path

from movement_scanner import send_volatile_digest_manual
from report import build_winrate_range_report
from telegram_notify import (
    VOLATILE_DIGEST_CALLBACK,
    answer_callback_query,
    delete_message_now,
    schedule_delete_message,
    send_telegram,
)

log = logging.getLogger(__name__)

_volatile_last_ts: dict[str, float] = {}


def _volatile_cooldown_sec() -> float:
    try:
        return max(5.0, float(os.getenv("VOLATILE_DIGEST_MANUAL_COOLDOWN_SEC", "30") or "30"))
    except (TypeError, ValueError):
        return 30.0

MOSCOW = timezone(timedelta(hours=3))

# chat_id -> ожидаем ввод дат следующим сообщением
_pending_winrate_dates: set[str] = set()


def _parse_dates(text: str, *, strip_command: bool = True) -> tuple[datetime, datetime] | None:
    """
    DD.MM.YYYY, DD.MM, D.M — одна дата или две (диапазон).
    strip_command: убрать /winrate_range из начала.
    """
    if strip_command:
        parts = (text or "").split(maxsplit=1)
        args = (parts[1] if len(parts) > 1 else "").strip()
    else:
        args = (text or "").strip()
    if not args:
        return None
    tokens = args.split()
    if not tokens:
        return None

    def parse_one(s: str) -> datetime | None:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$", s.strip())
        if not m:
            return None
        d, mon = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else datetime.now(MOSCOW).year
        try:
            return datetime(y, mon, d, tzinfo=MOSCOW)
        except ValueError:
            return None

    dt1 = parse_one(tokens[0])
    if not dt1:
        return None
    dt2 = parse_one(tokens[1]) if len(tokens) >= 2 else dt1
    if not dt2:
        return None
    if dt1 > dt2:
        dt1, dt2 = dt2, dt1
    return dt1, dt2


def _allowed_chat(chat_id: str) -> bool:
    """Пустой TELEGRAM_CHAT_ID = все чаты. Иначе — один или несколько id через запятую (группа / личка)."""
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not allowed:
        return True
    cid = (chat_id or "").strip()
    allowed_ids = {x.strip() for x in allowed.split(",") if x.strip()}
    return cid in allowed_ids


def _winrate_bot_msg_ttl_sec() -> int:
    try:
        return max(0, int(os.getenv("TELEGRAM_WINRATE_BOT_MSG_DELETE_SEC", "120")))
    except ValueError:
        return 120


def _delete_user_message_later(chat_id: str, user_message_id: int | None, token: str) -> None:
    if user_message_id is None:
        return
    asyncio.create_task(delete_message_now(chat_id, user_message_id, token))


def _schedule_bot_message_delete(
    chat_id: str, bot_message_id: int | None, token: str, ttl_sec: int,
) -> None:
    if bot_message_id is None or ttl_sec <= 0:
        return
    schedule_delete_message(chat_id, bot_message_id, token, float(ttl_sec))


async def _reply(
    session: aiohttp.ClientSession, token: str, chat_id: str, text: str,
) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    async with session.post(url, json=payload, timeout=30) as r:
        body = await r.text()
    try:
        data = json.loads(body)
        if data.get("ok"):
            mid = data.get("result", {}).get("message_id")
            return int(mid) if isinstance(mid, int) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


async def _telegram_prepare_polling(session: aiohttp.ClientSession, token: str) -> None:
    """Сброс webhook — иначе getUpdates не получает апдейты (частая причина «вчера работало, сегодня нет»)."""
    info_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    del_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        async with session.get(info_url, timeout=20) as r:
            body = await r.text()
        print(f"[TG] getWebhookInfo: {body[:400]}", flush=True)
        async with session.post(del_url, json={"drop_pending_updates": False}, timeout=20) as r:
            body = await r.text()
        print(f"[TG] deleteWebhook: {body[:300]}", flush=True)
    except Exception:
        log.exception("[TG] prepare polling")


async def run_telegram_listener() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[TG] ОШИБКА: TELEGRAM_BOT_TOKEN пустой — listener не работает", flush=True)
        log.warning("TELEGRAM_BOT_TOKEN не задан — слушатель команд не запущен.")
        while True:
            await asyncio.sleep(3600)

    offset = 0
    base = f"https://api.telegram.org/bot{token}"
    get_updates_url = f"{base}/getUpdates"
    set_commands_url = f"{base}/setMyCommands"
    webhook_info_url = f"{base}/getWebhookInfo"
    delete_webhook_url = f"{base}/deleteWebhook"
    allowed_updates_json = json.dumps(["message", "callback_query"])

    async with aiohttp.ClientSession() as http_session:
        await _telegram_prepare_polling(http_session, token)
        async with http_session.post(
            set_commands_url,
            json={
                "commands": [
                    {
                        "command": "winrate_range",
                        "description": "Winrate за период (DD.MM или DD.MM DD.MM)",
                    },
                    {
                        "command": "winrate",
                        "description": "То же, что winrate_range (короткий вызов)",
                    },
                    {"command": "cancel", "description": "Отменить ввод дат"},
                    {
                        "command": "volatile",
                        "description": "Топ волатильных (как авто-алерт, с кнопкой обновления)",
                    },
                ],
            },
            timeout=20,
        ) as r:
            body = await r.text()
            if r.status != 200:
                log.warning("setMyCommands HTTP %s: %s", r.status, body[:200])
            else:
                log.info(
                    "Меню команд Telegram зарегистрировано (winrate_range, winrate, cancel, volatile)",
                )

        print("[TG] long polling запущен (одна сессия, GET getUpdates)", flush=True)
        poll_n = 0
        while True:
            poll_n += 1
            try:
                if poll_n % 30 == 1:
                    async with http_session.get(webhook_info_url, timeout=25) as wr:
                        wraw = await wr.text()
                    try:
                        wj = json.loads(wraw)
                        wurl = (wj.get("result") or {}).get("url") or ""
                        if wurl:
                            print(f"[TG] ВНИМАНИЕ: снова висит webhook {wurl!r} — удаляю", flush=True)
                            async with http_session.post(delete_webhook_url, json={"drop_pending_updates": False}, timeout=25) as dr:
                                await dr.text()
                    except (json.JSONDecodeError, TypeError):
                        pass

                params = {
                    "offset": offset,
                    "timeout": 50,
                    "allowed_updates": allowed_updates_json,
                }
                async with http_session.get(get_updates_url, params=params, timeout=aiohttp.ClientTimeout(total=70)) as response:
                    raw = await response.text()
                    if response.status != 200:
                        print(f"[TG] getUpdates HTTP {response.status}: {raw[:600]}", flush=True)
                        await asyncio.sleep(3)
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"[TG] getUpdates не JSON: {raw[:400]}", flush=True)
                        await asyncio.sleep(3)
                        continue
            except Exception:
                log.exception("getUpdates network error")
                await asyncio.sleep(3)
                continue

            if not isinstance(data, dict) or not data.get("ok"):
                err = str(data)[:800]
                print(f"[TG] getUpdates FAILED: {err}", flush=True)
                log.warning("getUpdates not ok: %s", err)
                await asyncio.sleep(2)
                continue

            if poll_n % 12 == 0:
                print(f"[TG] heartbeat poll={poll_n} offset={offset} (listener жив)", flush=True)

            results = data.get("result") or []
            if results:
                print(f"[TG] получено updates: {len(results)}", flush=True)

            ttl = _winrate_bot_msg_ttl_sec()

            for update in data.get("result", []):
                offset = max(offset, update.get("update_id", 0) + 1)

                cq = update.get("callback_query")
                if cq:
                    data_c = str(cq.get("data") or "").strip()
                    cq_id = str(cq.get("id") or "")
                    msg = cq.get("message") or {}
                    chat = msg.get("chat") or {}
                    raw_cid = chat.get("id")
                    chat_id = str(raw_cid).strip() if raw_cid is not None else ""
    
                    print(
                        f"[TG] callback button data={data_c!r} chat_id={chat_id!r} allowed={_allowed_chat(chat_id)}",
                        flush=True,
                    )
    
                    if not cq_id:
                        continue
    
                    # Без answerCallbackQuery Telegram показывает «загрузку» на кнопке бесконечно.
                    if not chat_id:
                        await answer_callback_query(
                            cq_id,
                            token,
                            text="Не удалось определить чат. Отправьте /volatile",
                            show_alert=True,
                        )
                        log.warning("callback без message.chat.id: %s", str(cq)[:400])
                        continue
    
                    if not _allowed_chat(chat_id):
                        await answer_callback_query(
                            cq_id,
                            token,
                            text="Чат не в TELEGRAM_CHAT_ID в .env — добавьте этот id",
                            show_alert=True,
                        )
                        log.warning("callback: chat_id=%s не в TELEGRAM_CHAT_ID", chat_id)
                        continue
    
                    if data_c != VOLATILE_DIGEST_CALLBACK:
                        await answer_callback_query(
                            cq_id,
                            token,
                            text="Устаревшая кнопка. Запросите /volatile снова.",
                            show_alert=False,
                        )
                        continue
    
                    import time as time_mod
    
                    now = time_mod.time()
                    cd = _volatile_cooldown_sec()
                    last = _volatile_last_ts.get(chat_id, 0.0)
                    if now - last < cd:
                        wait = int(cd - (now - last) + 0.5)
                        await answer_callback_query(
                            cq_id,
                            token,
                            text=f"Подождите {wait} с",
                            show_alert=False,
                        )
                        continue
    
                    _volatile_last_ts[chat_id] = now
                    await answer_callback_query(cq_id, token)
                    try:
                        await send_volatile_digest_manual(chat_id=chat_id)
                        print(f"[TG] callback: дайджест отправлен (chat_id={chat_id})", flush=True)
                    except Exception:
                        log.exception("volatile digest (callback)")
                        await _reply(
                            http_session,
                            token,
                            chat_id,
                            "⚠️ Не удалось собрать список. См. journalctl / лог сервиса.",
                        )
                    continue
    
                message = update.get("message", {})
                text = (message.get("text") or "").strip()
                chat = message.get("chat", {})
                chat_id = str(chat.get("id", "")).strip()
                user_msg_id = message.get("message_id")
                if isinstance(user_msg_id, float):
                    user_msg_id = int(user_msg_id)
                elif not isinstance(user_msg_id, int):
                    user_msg_id = None
    
                if not chat_id or not text:
                    continue
                if not _allowed_chat(chat_id):
                    if os.getenv("TELEGRAM_CHAT_ID", "").strip():
                        log.warning(
                            "telegram: chat_id=%s не в TELEGRAM_CHAT_ID — команды игнорируются",
                            chat_id,
                        )
                    continue
    
                cmd = text.split()[0].split("@")[0].lower()
                print(f"[TG] входящее сообщение chat_id={chat_id} text={text[:120]!r}", flush=True)
    
                if cmd == "/cancel":
                    _pending_winrate_dates.discard(chat_id)
                    _delete_user_message_later(chat_id, user_msg_id, token)
                    bot_mid = await _reply(http_session, token, chat_id, "Отменено.")
                    _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                    continue

                if cmd in ("/volatile", "/volitile"):
                    import time as time_mod

                    _delete_user_message_later(chat_id, user_msg_id, token)
                    now = time_mod.time()
                    cd = _volatile_cooldown_sec()
                    last = _volatile_last_ts.get(chat_id, 0.0)
                    if now - last < cd:
                        wait = int(cd - (now - last) + 0.5)
                        bot_mid = await _reply(
                            http_session,
                            token,
                            chat_id,
                            f"⏳ Подождите ~{wait} с перед следующим запросом.",
                        )
                        _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                        continue
                    _volatile_last_ts[chat_id] = now
                    try:
                        await send_volatile_digest_manual(chat_id=chat_id)
                        print(f"[TG] /volatile: дайджест отправлен (chat_id={chat_id})", flush=True)
                    except Exception:
                        log.exception("volatile digest (/volatile)")
                        bot_mid = await _reply(
                            http_session,
                            token,
                            chat_id,
                            "⚠️ Не удалось собрать список. См. journalctl / лог сервиса.",
                        )
                        _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                    continue

                if cmd.startswith("/") and cmd not in (
                    "/cancel",
                    "/volatile",
                    "/volitile",
                    "/winrate_range",
                    "/winrate",
                ):
                    bot_mid = await _reply(
                        http_session,
                        token,
                        chat_id,
                        "Неизвестная команда.\n"
                        "Волатильность: <code>/volatile</code> (не <code>/volitile</code>).",
                    )
                    _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                    continue

                if cmd in ("/winrate_range", "/winrate"):
                    parsed = _parse_dates(text, strip_command=True)
                    _delete_user_message_later(chat_id, user_msg_id, token)
                    if not parsed:
                        _pending_winrate_dates.add(chat_id)
                        bot_mid = await _reply(
                            http_session,
                            token,
                            chat_id,
                            "Введите даты следующим сообщением.\n"
                            "Формат: <code>DD.MM</code> или <code>DD.MM DD.MM</code>\n"
                            "Например: <code>22.03</code> или <code>10.03 22.03</code>\n\n"
                            "Отмена: /cancel",
                        )
                        _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                    else:
                        report_text = build_winrate_range_report(parsed[0], parsed[1])
                        await send_telegram(
                            report_text,
                            chat_id=chat_id,
                            parse_mode=None,
                            delete_after_sec=ttl if ttl > 0 else None,
                        )
                    continue

                if chat_id in _pending_winrate_dates:
                    _pending_winrate_dates.discard(chat_id)
                    _delete_user_message_later(chat_id, user_msg_id, token)
                    parsed = _parse_dates(text, strip_command=False)
                    if not parsed:
                        bot_mid = await _reply(
                            http_session,
                            token,
                            chat_id,
                            "Не удалось распознать даты. Формат: DD.MM или DD.MM DD.MM",
                        )
                        _schedule_bot_message_delete(chat_id, bot_mid, token, ttl)
                    else:
                        report_text = build_winrate_range_report(parsed[0], parsed[1])
                        await send_telegram(
                            report_text,
                            chat_id=chat_id,
                            parse_mode=None,
                            delete_after_sec=ttl if ttl > 0 else None,
                        )
                    continue
