#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
Сканер + планировщик (отчёт в 21:00) + пампы (раз в час).
"""
import asyncio
from datetime import datetime, timezone, timedelta

import config
from pump_screener import run_screener
from scanner import run_scanner
from scheduler import run_scheduler


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
                pumped = await run_screener(send_tg=True)
                if pumped:
                    print(f"[PUMP] Найдено {len(pumped)} пампов, отправлено в TG")
            except Exception as e:
                print(f"[PUMP] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def main():
    await asyncio.gather(run_scanner(), run_scheduler(), run_pump_loop())


if __name__ == "__main__":
    asyncio.run(main())
