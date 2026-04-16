"""
Сканер: цикл тиков — свечи → вола-фильтр → детекторы → ориентиры (OI / 1h / 15m) → лучший сигнал → TG.
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
from orientation import (
    apply_h1_orientation,
    apply_oi_orientation,
    build_oi_flow_context,
    should_skip_coin_indicators,
)
from structure import atr_pct, planned_reward_pct, signal_plan_fingerprint
from storage.scanner_dedup import clear_plan, is_recent_plan, load_state, mark_plan_sent
from storage.signal_log import log_signal
from telegram_notify import send_telegram


def _signal_min_tp_pct() -> float:
    """Порог из .env SIGNAL_MIN_TP_MOVE_PCT или config.SIGNAL_MIN_TP_MOVE_PCT."""
    raw = os.getenv("SIGNAL_MIN_TP_MOVE_PCT", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(getattr(config, "SIGNAL_MIN_TP_MOVE_PCT", 5.0))


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


def _passes_orientation_pipeline(
    cand: dict,
    candles_15m: list[dict],
    candles_1h: list[dict],
    oi_flow_ctx: dict,
) -> bool:
    """
    Ориентиры OI → 1h → 15m EMA/MACD.
    Возвращает False, если кандидата не брать.
    """
    if apply_oi_orientation(cand, oi_flow_ctx):
        return False
    if apply_h1_orientation(cand, candles_1h):
        return False
    skip_coin, _reason = should_skip_coin_indicators(str(cand.get("direction", "")), candles_15m)
    if skip_coin:
        return False
    return True


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


async def run_tick(client: BinanceClient) -> tuple[Optional[dict], float]:
    if not _is_trading_hours():
        return None, 0.0

    dedup_sec = float(config.DEDUP_MINUTES * 60)

    symbols = await client.get_top_symbols(config.UNIVERSE_TOP_N)
    if not symbols:
        return None, 0.0

    candidates: list[dict] = []
    now = time.time()
    kl_1h_limit = int(os.getenv("H1_KLINES_LIMIT", "80"))

    # Sweep: только закрытые свечи; cont/exp: всегда 15m
    tf = config.SIGNAL_TIMEFRAME
    for symbol in symbols:
        try:
            candles_15m = await client.get_klines(symbol, "15m", 100)
            candles_tf = await client.get_klines(symbol, tf, 100) if tf != "15m" else candles_15m
            candles_1h = await client.get_klines(symbol, "1h", kl_1h_limit)
            if len(candles_tf) < config.SWEEP_MIN_CANDLES or len(candles_1h) < 20:
                continue

            atr_pct_1h = atr_pct(candles_1h, 14)
            if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
                continue

            atr_min_15m = float(getattr(config, "ATR_MIN_PCT_15M", 0.28))
            atr_pct_15m = atr_pct(candles_15m, 14)
            if atr_pct_15m is not None and atr_pct_15m < atr_min_15m:
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

            # OI для детекторов — период SIGNAL_TIMEFRAME; для ориентира OI+цена нужен 15m
            oi_hist_15m = await client.get_open_interest_hist(symbol, "15m", 5)
            oi_hist = oi_hist_15m if tf == "15m" else await client.get_open_interest_hist(symbol, tf, 5)
            oi_ctx = None
            if len(oi_hist) >= 2:
                oi_now = oi_hist[-1].get("open_interest", 0)
                oi_prev = oi_hist[-2].get("open_interest", 0)
                if oi_prev and oi_prev > 0:
                    oi_ctx = {"oi_change_pct": (oi_now - oi_prev) / oi_prev * 100}

            oi_flow_ctx = build_oi_flow_context(
                candles_15m,
                oi_hist_15m if len(oi_hist_15m) >= 2 else None,
            )

            taker = await client.get_taker_long_short(symbol, "15m", 2)
            taker_ratio = None
            if taker and taker[-1].get("sell_vol", 0) > 0:
                buy_v = taker[-1].get("buy_vol", 0) or 0
                sell_v = taker[-1].get("sell_vol", 0) or 1
                taker_ratio = buy_v / sell_v

            cand = liquidity_sweep_reversal.detect(symbol, closed_tf, candles_1h, atr_pct_1h, oi_ctx)
            if cand:
                cand.setdefault("meta", {})
                _apply_taker_bonus(cand, taker_ratio)
                if _passes_orientation_pipeline(cand, candles_15m, candles_1h, oi_flow_ctx):
                    candidates.append(cand)
            cand = liquidity_sweep_continuation.detect(symbol, candles_15m, candles_1h, atr_pct_1h)
            if cand:
                cand.setdefault("meta", {})
                _apply_taker_bonus(cand, taker_ratio)
                if _passes_orientation_pipeline(cand, candles_15m, candles_1h, oi_flow_ctx):
                    candidates.append(cand)
            cand = volatility_expansion.detect(symbol, candles_15m, candles_1h, atr_pct_1h, oi_ctx)
            if cand:
                cand.setdefault("meta", {})
                _apply_taker_bonus(cand, taker_ratio)
                if _passes_orientation_pipeline(cand, candles_15m, candles_1h, oi_flow_ctx):
                    candidates.append(cand)

        except Exception as e:
            print(f"[SCANNER] Ошибка {symbol}: {e}")
            continue
        finally:
            if getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) > 0:
                await asyncio.sleep(config.SCAN_SYMBOL_PAUSE_SEC)

    if not candidates:
        return None, 0.0

    min_tp = _signal_min_tp_pct()
    before_n = len(candidates)
    candidates = [c for c in candidates if planned_reward_pct(c) >= min_tp]
    if before_n and not candidates:
        print(
            f"[SCANNER] нет сигналов: все {before_n} канд. ниже мин. цели {min_tp}% к TP",
            flush=True,
        )
        return None, 0.0

    best = max(candidates, key=lambda c: c.get("score", 0))
    fp = signal_plan_fingerprint(best)
    # Свежая загрузка: за время обхода пар другой процесс мог записать тот же план.
    plan_dedup = load_state()
    if is_recent_plan(fp, plan_dedup, dedup_sec):
        sym = best.get("symbol", "")
        print(
            f"[SCANNER] дубликат плана ({sym} {best.get('direction', '')}), "
            f"уже был в течение {config.DEDUP_MINUTES} мин — пропуск",
            flush=True,
        )
        return None, 0.0

    # Резервируем до отправки в TG — второй процесс на следующем тике увидит ключ в файле.
    mark_plan_sent(fp, dedup_sec=dedup_sec)

    return best, now


async def run_scanner():
    from dotenv import load_dotenv
    load_dotenv()

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        print("[SCANNER] Liquidity Hunter v1 запущен")
        import notifier as _notifier_mod

        print(
            f"[SCANNER] pid={os.getpid()} | notifier={getattr(_notifier_mod, '__file__', '?')}",
            flush=True,
        )
        print(
            f"[SCANNER] мин. профит к TP: {_signal_min_tp_pct()}% "
            "(переменная SIGNAL_MIN_TP_MOVE_PCT или config.py)",
            flush=True,
        )
        print(
            "[SCANNER] дедуп планов: data/scanner_dedup.json "
            f"(окно {config.DEDUP_MINUTES} мин; убедитесь, что не запущено два бота на один TG).",
            flush=True,
        )



        while True:

            try:

                winner, _ = await run_tick(client)

                if winner and _is_trading_hours():

                    _dedup_sec = float(config.DEDUP_MINUTES * 60)
                    _fp = signal_plan_fingerprint(winner)
                    min_tp = _signal_min_tp_pct()
                    pr = planned_reward_pct(winner)
                    if pr + 1e-9 < min_tp:
                        clear_plan(_fp, dedup_sec=_dedup_sec)
                        print(
                            f"[SCANNER] БЛОК перед TG: плановый профит {pr:.4f}% < {min_tp}% — не отправляем. "
                            "Если это видишь часто в логе, на сервере крутится старый код или второй процесс.",
                            flush=True,
                        )
                    else:
                        text = format_signal(winner)

                        print(f"\n[СИГНАЛ]\n{text}\n")

                        try:
                            tg_ok = await send_telegram(text)
                        except Exception:
                            clear_plan(_fp, dedup_sec=_dedup_sec)
                            raise
                        if not tg_ok:
                            clear_plan(_fp, dedup_sec=_dedup_sec)
                            print(
                                "[SCANNER] send_telegram вернул False — сообщение в TG не ушло "
                                "(проверьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID, см. строки [TG] выше).",
                                flush=True,
                            )

                        try:
                            log_signal(winner)
                        except Exception as e:
                            print(f"[SCANNER] Ошибка логирования: {e}")

            except Exception as e:

                print(f"[SCANNER] Ошибка тика: {e}")



            interval = _tick_interval()

            await asyncio.sleep(interval)





if __name__ == "__main__":

    asyncio.run(run_scanner())

