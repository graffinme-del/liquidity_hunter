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
from telegram_notify import send_telegram


def _dedup_key(signal: dict) -> tuple:
    return (signal.get("symbol", ""), signal.get("direction", ""))


def _apply_taker_bonus(cand: dict, taker_ratio: Optional[float]) -> None:
    """Перекос лонгов/шортов — бонус к score (подготовка к охоте)."""
    if taker_ratio is None:
        return
    direction = cand.get("direction", "")
    score = cand.get("score", 0)
    if direction == "LONG" and taker_ratio < config.TAKER_RATIO_SHORT_TRAP:
        cand["score"] = score + config.TAKER_TRAP_BONUS
        cand["taker_trap"] = True
    elif direction == "SHORT" and taker_ratio > config.TAKER_RATIO_LONG_TRAP:
        cand["score"] = score + config.TAKER_TRAP_BONUS
        cand["taker_trap"] = True


def _is_trading_hours() -> bool:
    # Europe/Moscow = UTC+3 (без DST с 2011)
    moscow = timezone(timedelta(hours=3))
    hour = datetime.now(moscow).hour
    return config.TRADING_START_HOUR <= hour < config.TRADING_END_HOUR


def _seconds_until_candle_close(tf: str) -> int:
    """Секунд до закрытия текущей свечи (Binance UTC)."""
    utc = datetime.now(timezone.utc)
    minute = utc.minute
    second = utc.second
    if tf == "1h":
        sec_left = (60 - minute) * 60 - second
    else:  # 15m
        next_boundary = ((minute // 15) + 1) * 15
        if next_boundary >= 60:
            next_boundary = 60
        curr_frac = minute + second / 60.0
        sec_left = (next_boundary - curr_frac) * 60
    return max(0, int(sec_left))


def _tick_interval() -> float:
    """Интервал тика: чаще перед закрытием свечи — меньше задержка."""
    tf = config.SIGNAL_TIMEFRAME
    sec_to_close = _seconds_until_candle_close(tf)
    if tf == "1h":
        near_window = 300  # последние 5 минут
    else:
        near_window = 120  # последние 2 минуты
    if sec_to_close <= near_window:
        return config.TICK_INTERVAL_NEAR_CLOSE_SEC
    return config.TICK_INTERVAL_SEC


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

    # Sweep: только закрытые свечи; cont/exp: всегда 15m
    tf = config.SIGNAL_TIMEFRAME
    for symbol in symbols:
        try:
            candles_15m = await client.get_klines(symbol, "15m", 100)
            candles_tf = await client.get_klines(symbol, tf, 100) if tf != "15m" else candles_15m
            candles_1h = await client.get_klines(symbol, "1h", 50)
            if len(candles_tf) < config.SWEEP_MIN_CANDLES or len(candles_1h) < 20:
                continue

            atr_pct_1h = atr_pct(candles_1h, 14)
            if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
                continue

            # Только закрытые свечи — исключаем формирующуюся
            closed_tf = candles_tf[:-1] if len(candles_tf) > 1 else candles_tf
            if not closed_tf:
                continue
            last_closed = closed_tf[-1]
            price = float(last_closed.get("close", 0) or 0)
            if price < config.MIN_PRICE:
                continue

            vol_last = float(last_closed.get("volume", 0) or 0)
            vol_avg = sum(float(c.get("volume", 0) or 0) for c in closed_tf[-21:-1]) / 20 if len(closed_tf) >= 21 else vol_last
            if vol_avg > 0 and vol_last < vol_avg * config.VOLUME_LAST_MIN_RATIO:
                continue

            oi_hist = await client.get_open_interest_hist(symbol, tf, 3)
            oi_ctx = None
            if len(oi_hist) >= 2:
                oi_now = oi_hist[-1].get("open_interest", 0)
                oi_prev = oi_hist[-2].get("open_interest", 0)
                if oi_prev and oi_prev > 0:
                    oi_ctx = {"oi_change_pct": (oi_now - oi_prev) / oi_prev * 100}

            taker = await client.get_taker_long_short(symbol, "15m", 2)
            taker_ratio = None
            if taker and taker[-1].get("sell_vol", 0) > 0:
                buy_v = taker[-1].get("buy_vol", 0) or 0
                sell_v = taker[-1].get("sell_vol", 0) or 1
                taker_ratio = buy_v / sell_v

            cand = liquidity_sweep_reversal.detect(symbol, closed_tf, candles_1h, atr_pct_1h, oi_ctx)
            if cand:
                _apply_taker_bonus(cand, taker_ratio)
                candidates.append(cand)
            cand = liquidity_sweep_continuation.detect(symbol, candles_15m, candles_1h, atr_pct_1h)
            if cand:
                _apply_taker_bonus(cand, taker_ratio)
                candidates.append(cand)
            cand = volatility_expansion.detect(symbol, candles_15m, candles_1h, atr_pct_1h, oi_ctx)
            if cand:
                _apply_taker_bonus(cand, taker_ratio)
                candidates.append(cand)

        except Exception as e:
            print(f"[SCANNER] Ошибка {symbol}: {e}")
            continue
        finally:
            if getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) > 0:
                await asyncio.sleep(config.SCAN_SYMBOL_PAUSE_SEC)

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

            interval = _tick_interval()
            await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_scanner())
