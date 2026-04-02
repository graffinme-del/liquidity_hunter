#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
Сканер + планировщик (отчёт в 21:00) + пампы (раз в час) + команды TG (/winrate_range).
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import config
from movement_scanner import run_movement_scan
from pump_screener import run_screener
from scanner import run_scanner
from scheduler import run_scheduler
from telegram_commands import run_telegram_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def _is_trading_hours() -> bool:
    moscow = timezone(timedelta(hours=3))
    hour = datetime.now(moscow).hour
    return config.TRADING_START_HOUR <= hour < config.TRADING_END_HOUR


async def run_pump_loop():
    """Раз в час проверяет пампы и шлёт в TG."""
    interval_sec = config.PUMP_CHECK_INTERVAL_MIN * 60
    await asyncio.sleep(120)  # первая проверка через 2 мин после старта
    while True:
        if _is_trading_hours():
            try:
                pumped, tg_ok = await run_screener(send_tg=True)
                if pumped:
                    if tg_ok:
                        print(f"[PUMP] Найдено {len(pumped)} пампов, отправлено в TG")
                    else:
                        print(
                            f"[PUMP] Найдено {len(pumped)} пампов, "
                            f"отправка в TG НЕ УДАЛАСЬ — см. [TG] в journalctl и TELEGRAM_* в .env",
                            flush=True,
                        )
            except Exception as e:
                print(f"[PUMP] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def run_movement_loop():
    """Резкое движение по волатильности — не сигнал входа."""
    if not getattr(config, "VOL_SCAN_ENABLED", False):
        return
    await asyncio.sleep(300)  # старт через 5 мин после запуска
    interval_sec = config.VOL_SCAN_INTERVAL_MIN * 60
    while True:
        if _is_trading_hours():
            try:
                hits, tg_ok = await run_movement_scan(send_tg=True)
                if hits:
                    if tg_ok:
                        print(f"[VOL] Резкое движение: {len(hits)} пар, отправлено в TG")
                    else:
                        print(
                            f"[VOL] Резкое движение: {len(hits)} пар, "
                            f"отправка в TG НЕ УДАЛАСЬ — проверьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env",
                            flush=True,
                        )
            except Exception as e:
                print(f"[VOL] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def main():
    await asyncio.gather(
        run_scanner(),
        run_scheduler(),
        run_pump_loop(),
        run_movement_loop(),
        run_telegram_listener(),
    )


if __name__ == "__main__":
    import traceback
    from pathlib import Path

    from dotenv import load_dotenv

    # Явный путь к .env — не зависит от cwd
    load_dotenv(Path(__file__).resolve().parent / ".env")
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise
