"""
Резолв TP/SL для открытых сигналов. Запуск: python outcome_resolver.py --window-hours 48
"""
import asyncio
import argparse
from datetime import datetime, timedelta

import aiohttp

from storage.outcome_tracker import read_open_signals, resolve_outcome

BINANCE_BASE = "https://fapi.binance.com"


async def fetch_klines(session: aiohttp.ClientSession, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": 500}
    try:
        async with session.get(f"{BINANCE_BASE}/fapi/v1/klines", params=params, timeout=20) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            out.append({"high": float(row[2]), "low": float(row[3])})
    return out


async def run_resolver(window_hours: int = 48) -> tuple[int, int, int]:
    from datetime import timezone
    moscow = timezone(timedelta(hours=3))
    now = datetime.now(moscow)
    start_at = now - timedelta(hours=window_hours)

    signals = read_open_signals(start_at, now)
    if not signals:
        print(f"[RESOLVER] Нет открытых сигналов за последние {window_hours} ч")
        return 0, 0, 0

    tp_sl = 0
    no_outcome = 0

    async with aiohttp.ClientSession() as session:
        for sig in signals:
            start_ms = (sig["ts_unix"] - 60) * 1000
            end_ms = int(now.timestamp() * 1000)
            interval = "15m"
            if "1h" in (sig.get("timeframe") or ""):
                interval = "1h"
            elif "5m" in (sig.get("timeframe") or ""):
                interval = "5m"

            klines = await fetch_klines(session, sig["symbol"], interval, start_ms, end_ms)
            if not klines:
                continue

            high = max(c["high"] for c in klines)
            low = min(c["low"] for c in klines)
            result = resolve_outcome(sig, high, low)
            if result in ("TP", "SL"):
                tp_sl += 1
            else:
                no_outcome += 1

    print(f"[RESOLVER] Резолвлено: {len(signals)}, TP+SL: {tp_sl}, NO_OUTCOME: {no_outcome}")
    return len(signals), tp_sl, no_outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-hours", type=int, default=48)
    args = parser.parse_args()
    asyncio.run(run_resolver(args.window_hours))


if __name__ == "__main__":
    main()
