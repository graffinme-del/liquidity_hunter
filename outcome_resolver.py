"""
Резолв TP/SL для открытых сигналов. Запуск: python outcome_resolver.py --window-hours 720

Свечи запрашиваются страницами по 500 — покрывается весь период от сигнала до «сейчас»
(раньше limit=500 давал только начало диапазона и ложный NO_OUTCOME).

Окно по времени создания сигнала задаётся часами (по умолчанию из OUTCOME_RESOLVER_WINDOW_HOURS,
см. scheduler). Это не «сброс раз в сутки», а «какие OPEN ещё пытаемся закрыть».
"""
import asyncio
import argparse
import os
from datetime import datetime, timedelta, timezone

import aiohttp

from storage.outcome_tracker import read_open_signals, resolve_outcome

BINANCE_BASE = "https://fapi.binance.com"

# Binance: длина интервала в мс для пагинации
_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}


def _interval_ms(interval: str) -> int:
    return _INTERVAL_MS.get(interval, 900_000)


async def fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Один запрос, до 500 свечей с start_ms."""
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": 500}
    try:
        async with session.get(f"{BINANCE_BASE}/fapi/v1/klines", params=params, timeout=30) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            out.append({"open_time": int(row[0]), "high": float(row[2]), "low": float(row[3])})
    return out


async def fetch_klines_full_range(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Все свечи от start_ms до end_ms (несколько запросов по 500)."""
    step = _interval_ms(interval)
    all_rows: list[dict] = []
    cur = start_ms
    safety = 0
    max_iters = 5000
    while cur < end_ms and safety < max_iters:
        safety += 1
        chunk = await fetch_klines(session, symbol, interval, cur, end_ms)
        if not chunk:
            break
        all_rows.extend(chunk)
        last_t = chunk[-1]["open_time"]
        cur = last_t + step
        if len(chunk) < 500:
            break
    return all_rows


def _aggregate_high_low(klines: list[dict]) -> tuple[float, float] | None:
    if not klines:
        return None
    return max(c["high"] for c in klines), min(c["low"] for c in klines)


async def run_resolver(window_hours: int | None = None) -> tuple[int, int, int]:
    moscow = timezone(timedelta(hours=3))
    now = datetime.now(moscow)
    if window_hours is None:
        try:
            window_hours = int(os.getenv("OUTCOME_RESOLVER_WINDOW_HOURS", "720"))
        except ValueError:
            window_hours = 720
    window_hours = max(24, window_hours)
    start_at = now - timedelta(hours=window_hours)

    signals = read_open_signals(start_at, now)
    if not signals:
        print(f"[RESOLVER] Нет открытых сигналов за последние {window_hours} ч (по дате создания)")
        return 0, 0, 0

    tp_sl = 0
    still_open = 0
    skipped = 0

    async with aiohttp.ClientSession() as session:
        for sig in signals:
            start_ms = (sig["ts_unix"] - 60) * 1000
            end_ms = int(now.timestamp() * 1000)
            interval = "15m"
            tf = (sig.get("timeframe") or "").lower()
            if "1h" in tf:
                interval = "1h"
            elif "5m" in tf:
                interval = "5m"

            klines = await fetch_klines_full_range(session, sig["symbol"], interval, start_ms, end_ms)
            agg = _aggregate_high_low(klines)
            if agg is None:
                skipped += 1
                continue

            high, low = agg
            result = resolve_outcome(sig, high, low)
            if result in ("TP", "SL"):
                tp_sl += 1
            else:
                still_open += 1

    print(
        f"[RESOLVER] Сигналов: {len(signals)}, закрыто TP/SL: {tp_sl}, "
        f"ещё без TP/SL (следим дальше): {still_open}, без свечей: {skipped}"
    )
    return len(signals), tp_sl, still_open


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-hours", type=int, default=None, help="По умолчанию: env OUTCOME_RESOLVER_WINDOW_HOURS или 720")
    args = parser.parse_args()
    asyncio.run(run_resolver(window_hours=args.window_hours))


if __name__ == "__main__":
    main()
