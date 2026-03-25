#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
Сканер + планировщик (отчёт в 21:00) + пампы (раз в час).
"""
import asyncio
from datetime import datetime, timezone, timedelta

import config
from movement_scanner import run_movement_scan
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


async def run_movement_loop():
    """Резкое движение по волатильности — не сигнал входа."""
    if not getattr(config, "VOL_SCAN_ENABLED", False):
        return
    await asyncio.sleep(300)  # старт через 5 мин после запуска
    interval_sec = config.VOL_SCAN_INTERVAL_MIN * 60
    while True:
        if _is_trading_hours():
            try:
                hits = await run_movement_scan(send_tg=True)
                if hits:
                    print(f"[VOL] Резкое движение: {len(hits)} пар, отправлено в TG")
            except Exception as e:
                print(f"[VOL] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def main():
    await asyncio.gather(run_scanner(), run_scheduler(), run_pump_loop(), run_movement_loop())


if __name__ == "__main__":
    asyncio.run(main())
