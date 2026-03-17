"""
Планировщик: ежедневный отчёт в 21:00 Москва.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiohttp

from report import build_daily_report

MOSCOW = timezone(timedelta(hours=3))
REPORT_HOUR = 21
REPORT_MINUTE = 0


def _next_report_at() -> datetime:
    now = datetime.now(MOSCOW)
    run_at = now.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return run_at


async def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[SCHEDULER] TELEGRAM не настроен, отчёт не отправлен")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "text": text}) as r:
                return r.status == 200
    except Exception as e:
        print(f"[SCHEDULER] Ошибка: {e}")
        return False


async def run_scheduler():
    """Ждёт 21:00, резолвит открытые сигналы, отправляет отчёт."""
    last_sent_date = None
    while True:
        now = datetime.now(MOSCOW)
        run_at = _next_report_at()
        wait_sec = max((run_at - now).total_seconds(), 0)
        print(f"[SCHEDULER] Следующий отчёт в {run_at.strftime('%d.%m %H:%M')} Мск")
        await asyncio.sleep(min(wait_sec, 3600) if wait_sec > 0 else 60)

        now = datetime.now(MOSCOW)
        if now.hour != REPORT_HOUR or last_sent_date == now.date():
            continue

        try:
            from outcome_resolver import run_resolver
            await run_resolver(window_hours=24)
        except Exception as e:
            print(f"[SCHEDULER] Резолвер: {e}")

        report = build_daily_report()
        await send_telegram(report)
        last_sent_date = now.date()
        print("[SCHEDULER] Отчёт отправлен")
