#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
Сканер + старт пампа 15m + опц. импульс 1–3 свечи + опц. EMA-памп 1h + VOL + TG.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import config
from early_pump_scanner import run_early_pump_scan
from impulse_scanner import run_impulse_scan
from movement_scanner import run_movement_scan
from phase1_accumulation import run_phase1_loop
from pump_screener import run_screener
from squeeze_oi_scanner import run_squeeze_oi_loop
from reversal_scanner import run_reversal_loop
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


async def run_early_pump_loop():
    """Старт пампа: тишина + первая свеча с объёмом (15m)."""
    if not getattr(config, "EARLY_PUMP_ENABLED", False):
        return
    await asyncio.sleep(90)
    interval_sec = getattr(config, "EARLY_PUMP_INTERVAL_MIN", 10) * 60
    while True:
        if _is_trading_hours():
            try:
                hits, tg_ok = await run_early_pump_scan(send_tg=True)
                if hits:
                    if tg_ok:
                        print(
                            f"[EARLY] Старт пампа 15m: {len(hits)} пар, отправлено в TG",
                            flush=True,
                        )
                    else:
                        print(
                            "[EARLY] Старт пампа: отправка в TG не удалась — см. [TG] в journalctl",
                            flush=True,
                        )
            except Exception as e:
                print(f"[EARLY] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def run_pump_loop():
    """Раз в час: поздний «памп» по EMA20 1h (если PUMP_EMA_SCREEN_ENABLED)."""
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


async def run_impulse_loop():
    """15m: быстрый рост ≥N% за 1–3 свечи (config IMPULSE_15M_*)."""
    if not getattr(config, "IMPULSE_15M_ENABLED", False):
        return
    await asyncio.sleep(240)  # старт чуть позже VOL, чтобы не бить API одним фронтом
    interval_sec = getattr(config, "IMPULSE_15M_INTERVAL_MIN", 15) * 60
    while True:
        if _is_trading_hours():
            try:
                hits, tg_ok = await run_impulse_scan(send_tg=True)
                if hits:
                    if tg_ok:
                        print(
                            f"[IMPULSE] Импульс 15m: {len(hits)} пар (≥{config.IMPULSE_15M_MIN_PCT:.0f}%), "
                            f"отправлено в TG",
                            flush=True,
                        )
                    else:
                        print(
                            f"[IMPULSE] Импульс 15m: {len(hits)} пар, "
                            f"отправка в TG НЕ УДАЛАСЬ — см. [TG] в journalctl",
                            flush=True,
                        )
            except Exception as e:
                print(f"[IMPULSE] Ошибка: {e}")
        await asyncio.sleep(interval_sec)


async def run_pump_stats_loop():
    """Оценка сигналов early через 24h (max рост от цены входа), SQLite."""
    if not getattr(config, "PUMP_STATS_ENABLED", True):
        return
    import aiohttp

    from data.binance_client import BinanceClient
    from pump_stats import evaluate_pending_signals

    await asyncio.sleep(300)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                client = BinanceClient(session)
                n = await evaluate_pending_signals(client)
                if n:
                    print(f"[STATS] оценено сигналов пампа: {n}", flush=True)
        except Exception as e:
            print(f"[STATS] ошибка: {e}", flush=True)
        await asyncio.sleep(600)


async def main():
    await asyncio.gather(
        run_scanner(),
        run_scheduler(),
        run_early_pump_loop(),
        run_impulse_loop(),
        run_pump_loop(),
        run_movement_loop(),
        run_pump_stats_loop(),
        run_phase1_loop(),
        run_squeeze_oi_loop(),
        run_reversal_loop(),
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
