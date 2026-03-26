"""
Команды Telegram: winrate за период (как pnl_range в binance_pnl_bot).
Долгий polling getUpdates; запускается параллельно со сканером в main.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp

from report import build_winrate_range_report
from telegram_notify import send_telegram

log = logging.getLogger(__name__)

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
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not allowed:
        return True
    return chat_id == allowed


async def _reply(session: aiohttp.ClientSession, token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    async with session.post(url, json=payload, timeout=30) as r:
        await r.text()


async def run_telegram_listener() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN не задан — слушатель команд не запущен.")
        while True:
            await asyncio.sleep(3600)

    offset = 0
    get_updates_url = f"https://api.telegram.org/bot{token}/getUpdates"
    set_commands_url = f"https://api.telegram.org/bot{token}/setMyCommands"

    async with aiohttp.ClientSession() as session:
        async with session.post(
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
                ],
                "scope": {"type": "all_private_chats"},
            },
            timeout=20,
        ) as r:
            body = await r.text()
            if r.status != 200:
                log.warning("setMyCommands HTTP %s: %s", r.status, body[:200])
            else:
                log.info("Меню команд Telegram зарегистрировано (winrate_range, winrate, cancel)")

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    get_updates_url,
                    json={"timeout": 50, "offset": offset, "allowed_updates": ["message"]},
                    timeout=60,
                ) as response:
                    data = await response.json(content_type=None)
        except Exception:
            await asyncio.sleep(3)
            continue

        for update in data.get("result", []):
            offset = max(offset, update.get("update_id", 0) + 1)
            message = update.get("message", {})
            text = (message.get("text") or "").strip()
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", "")).strip()
            if not chat_id or not text:
                continue
            if not _allowed_chat(chat_id):
                continue

            cmd = text.split()[0].split("@")[0].lower()

            async with aiohttp.ClientSession() as session:
                if cmd == "/cancel":
                    _pending_winrate_dates.discard(chat_id)
                    await _reply(session, token, chat_id, "Отменено.")
                    continue

                if cmd in ("/winrate_range", "/winrate"):
                    parsed = _parse_dates(text, strip_command=True)
                    if not parsed:
                        _pending_winrate_dates.add(chat_id)
                        await _reply(
                            session,
                            token,
                            chat_id,
                            "Введите даты следующим сообщением.\n"
                            "Формат: <code>DD.MM</code> или <code>DD.MM DD.MM</code>\n"
                            "Например: <code>22.03</code> или <code>10.03 22.03</code>\n\n"
                            "Отмена: /cancel",
                        )
                    else:
                        report_text = build_winrate_range_report(parsed[0], parsed[1])
                        await send_telegram(report_text, chat_id=chat_id, parse_mode=None)
                    continue

                if chat_id in _pending_winrate_dates:
                    _pending_winrate_dates.discard(chat_id)
                    parsed = _parse_dates(text, strip_command=False)
                    if not parsed:
                        await _reply(
                            session,
                            token,
                            chat_id,
                            "Не удалось распознать даты. Формат: DD.MM или DD.MM DD.MM",
                        )
                    else:
                        report_text = build_winrate_range_report(parsed[0], parsed[1])
                        await send_telegram(report_text, chat_id=chat_id, parse_mode=None)
                    continue
