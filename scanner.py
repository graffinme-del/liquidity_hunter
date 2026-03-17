"""
Сканер: цикл тиков — свечи → вола-фильтр → детекторы → лучший сигнал → TG.
"""
import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp

import config
from data.binance_client import BinanceClient
from detectors import liquidity_sweep_reversal, liquidity_sweep_continuation, volatility_expansion
from notifier import format_signal
from structure import atr_pct
from storage.signal_log import log_signal


async def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[SCANNER] TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы, пропуск отправки")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}) as r:
                return r.status == 200
    except Exception as e:
        print(f"[SCANNER] Ошибка отправки в TG: {e}")
        return False


def _dedup_key(signal: dict) -> tuple:
    return (signal.get("symbol", ""), signal.get("direction", ""))


def _is_trading_hours() -> bool:
    # Europe/Moscow = UTC+3 (без DST с 2011)
    moscow = timezone(timedelta(hours=3))
    hour = datetime.now(moscow).hour
    return config.TRADING_START_HOUR <= hour < config.TRADING_END_HOUR


async def run_tick(
    client: BinanceClient,
    last_sent: dict[str, float],
) -> tuple[Optional[dict], float]:
    if not _is_trading_hours():
        return None, 0.0

    symbols = await client.get_top_symbols(config.UNIVERSE_TOP_N)
    if not symbols:
        return None, 0.0

    candidates: list[dict] = []
    now = time.time()

    for symbol in symbols:
        try:
            candles_15m = await client.get_klines(symbol, "15m", 100)
            candles_1h = await client.get_klines(symbol, "1h", 50)
            if len(candles_15m) < config.SWEEP_MIN_CANDLES or len(candles_1h) < 20:
                continue

            atr_pct_1h = atr_pct(candles_1h, 14)
            if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
                continue

            last_candle = candles_15m[-1]
            price = float(last_candle.get("close", 0) or 0)
            if price < config.MIN_PRICE:
                continue

            vol_last = float(last_candle.get("volume", 0) or 0)
            vol_avg = sum(float(c.get("volume", 0) or 0) for c in candles_15m[-21:-1]) / 20 if len(candles_15m) >= 21 else vol_last
            if vol_avg > 0 and vol_last < vol_avg * config.VOLUME_LAST_MIN_RATIO:
                continue

            oi_hist = await client.get_open_interest_hist(symbol, "15m", 3)
            oi_ctx = None
            if len(oi_hist) >= 2:
                oi_now = oi_hist[-1].get("open_interest", 0)
                oi_prev = oi_hist[-2].get("open_interest", 0)
                if oi_prev and oi_prev > 0:
                    oi_ctx = {"oi_change_pct": (oi_now - oi_prev) / oi_prev * 100}

            cand = liquidity_sweep_reversal.detect(symbol, candles_15m, candles_1h, atr_pct_1h)
            if cand:
                candidates.append(cand)
            cand = liquidity_sweep_continuation.detect(symbol, candles_15m, candles_1h, atr_pct_1h)
            if cand:
                candidates.append(cand)
            cand = volatility_expansion.detect(symbol, candles_15m, candles_1h, atr_pct_1h, oi_ctx)
            if cand:
                candidates.append(cand)

        except Exception as e:
            print(f"[SCANNER] Ошибка {symbol}: {e}")
            continue

    if not candidates:
        return None, 0.0

    best = max(candidates, key=lambda c: c.get("score", 0))
    key = _dedup_key(best)
    if key in last_sent and now - last_sent[key] < config.DEDUP_MINUTES * 60:
        return None, 0.0

    return best, now


async def run_scanner():
    from dotenv import load_dotenv
    load_dotenv()

    last_sent: dict[tuple, float] = {}

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        print("[SCANNER] Liquidity Hunter v1 запущен")

        while True:
            try:
                winner, _ = await run_tick(client, last_sent)
                if winner and _is_trading_hours():
                    text = format_signal(winner)
                    print(f"\n[СИГНАЛ]\n{text}\n")
                    await send_telegram(text)
                    try:
                        log_signal(winner)
                    except Exception as e:
                        print(f"[SCANNER] Ошибка логирования: {e}")
                    now = time.time()
                    last_sent[_dedup_key(winner)] = now
                    # Очистка старых записей
                    cutoff = now - config.DEDUP_MINUTES * 60
                    for k in list(last_sent.keys()):
                        if last_sent[k] < cutoff:
                            del last_sent[k]
            except Exception as e:
                print(f"[SCANNER] Ошибка тика: {e}")

            await asyncio.sleep(config.TICK_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(run_scanner())
