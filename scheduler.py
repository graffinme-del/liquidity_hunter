"""
Планировщик: ежедневный отчёт в 21:00 Мск; по воскресеньям — неделя + окна 2–7 дн.;
в последний день месяца — итог месяца.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import config
from report import (
    build_daily_report,
    build_monthly_report,
    build_rolling_windows_report,
    build_weekly_report,
)
from telegram_notify import send_telegram

MOSCOW = timezone(timedelta(hours=3))
REPORT_HOUR = 21
REPORT_MINUTE = 0


def _next_report_at() -> datetime:
    now = datetime.now(MOSCOW)
    run_at = now.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return run_at


async def run_scheduler():
    """Ждёт 21:00, резолвит открытые сигналы, отправляет отчёты."""
    last_sent_date = None
    last_weekly_sent_date = None
    last_monthly_sent_date = None
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

            # Окно по дате создания OPEN (часы). По умолчанию OUTCOME_RESOLVER_WINDOW_HOURS (720 = ~30 дн.).
            await run_resolver()
        except Exception as e:
            print(f"[SCHEDULER] Резолвер: {e}")

        report = build_daily_report()
        await send_telegram(report, parse_mode=None)
        last_sent_date = now.date()
        print("[SCHEDULER] Дневной отчёт отправлен")

        if (
            getattr(config, "PUMP_STATS_AUTO_REPORT", True)
            and getattr(config, "PUMP_STATS_ENABLED", True)
        ):
            try:
                from pump_stats import pump_stats_report_text

                await send_telegram(pump_stats_report_text(), parse_mode="HTML")
                print("[SCHEDULER] Отчёт статистики пампов (pump_stats) отправлен")
            except Exception as e:
                print(f"[SCHEDULER] Отчёт pump stats: {e}")

        if now.weekday() == 6 and last_weekly_sent_date != now.date():
            try:
                await send_telegram(build_weekly_report(), parse_mode=None)
                await send_telegram(build_rolling_windows_report(), parse_mode=None)
                last_weekly_sent_date = now.date()
                print("[SCHEDULER] Недельные отчёты отправлены")
            except Exception as e:
                print(f"[SCHEDULER] Недельный отчёт: {e}")

        tomorrow = now.date() + timedelta(days=1)
        if tomorrow.month != now.month and last_monthly_sent_date != now.date():
            try:
                await send_telegram(build_monthly_report(), parse_mode=None)
                last_monthly_sent_date = now.date()
                print("[SCHEDULER] Месячный отчёт отправлен")
            except Exception as e:
                print(f"[SCHEDULER] Месячный отчёт: {e}")
