#!/usr/bin/env python3
"""
Поиск «состоявшихся пампов» на 1h и вывод CSV с метриками A и B.

  A: (max(high) − min(low)) / min(low) × 100  по k свечам подряд (2…10).
  B: (close последней − open первой) / open первой × 100  по тому же окну.

Отбор окна по A: min_pct ≤ A ≤ max_pct (по умолчанию 8…30).
Для каждой конечной свечи индекса i берётся k∈[2..10], дающий максимальный A в допустимом коридоре
(если таких k несколько — одна строка на i с лучшим A).

Запуск из каталога liquidity_hunter:
  python tools/log_1h_pumps.py --symbol 1000PEPEUSDT --days 60 --out data/pumps_1h.csv
  python tools/log_1h_pumps.py --symbols BTCUSDT,ETHUSDT --days 30 --out pumps.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_client import BinanceClient  # noqa: E402

HOUR_MS = 60 * 60 * 1000


def _utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_1h_series(client: BinanceClient, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Все закрытые 1h свечи в [start_ms, end_ms), чанками по 1500."""
    out: list[dict] = []
    cur = int(start_ms)
    end_ms = int(end_ms)
    while cur < end_ms:
        batch = await client.get_klines_range(symbol, "1h", cur, end_ms, limit=1500)
        if not batch:
            break
        out.extend(batch)
        last_ot = int(batch[-1]["open_time"])
        cur = last_ot + HOUR_MS
        if len(batch) < 1500:
            break
    # уникальность по open_time
    by_ot: dict[int, dict] = {}
    for c in out:
        ot = int(c["open_time"])
        by_ot[ot] = c
    return [by_ot[k] for k in sorted(by_ot.keys())]


def scan_pumps(
    candles: list[dict],
    *,
    k_min: int,
    k_max: int,
    min_pct: float,
    max_pct: float,
) -> list[dict]:
    """
    Для каждого end-индекса i возвращает не более одной записи (лучший A в полосе).
    """
    n = len(candles)
    rows: list[dict] = []
    for i in range(k_min - 1, n):
        best: dict | None = None
        best_a = -1.0
        for k in range(k_min, k_max + 1):
            if i - k + 1 < 0:
                continue
            w = candles[i - k + 1 : i + 1]
            lows = [float(x["low"]) for x in w]
            highs = [float(x["high"]) for x in w]
            L = min(lows)
            H = max(highs)
            if L <= 0:
                continue
            move_a = (H - L) / L * 100.0
            if move_a < min_pct or move_a > max_pct:
                continue
            o0 = float(w[0]["open"])
            c_last = float(w[-1]["close"])
            if o0 == 0:
                continue
            move_b = (c_last - o0) / o0 * 100.0
            if move_a > best_a:
                best_a = move_a
                best = {
                    "k": k,
                    "low_min": L,
                    "high_max": H,
                    "move_pct_A": round(move_a, 4),
                    "open_first": o0,
                    "close_last": c_last,
                    "move_pct_B": round(move_b, 4),
                    "start_open_time": int(w[0]["open_time"]),
                    "end_open_time": int(w[-1]["open_time"]),
                    "end_close_time": int(w[-1]["close_time"]),
                }
        if best:
            rows.append(best)
    return rows


async def run_for_symbol(
    client: BinanceClient,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    k_min: int,
    k_max: int,
    min_pct: float,
    max_pct: float,
) -> list[dict]:
    candles = await fetch_1h_series(client, symbol, start_ms, end_ms)
    if len(candles) < k_max:
        return []
    events = scan_pumps(
        candles,
        k_min=k_min,
        k_max=k_max,
        min_pct=min_pct,
        max_pct=max_pct,
    )
    out: list[dict] = []
    for ev in events:
        out.append(
            {
                "symbol": symbol,
                "start_open_time_utc": _utc_iso(ev["start_open_time"]),
                "end_open_time_utc": _utc_iso(ev["end_open_time"]),
                "end_close_time_utc": _utc_iso(ev["end_close_time"]),
                "k": ev["k"],
                "low_min": ev["low_min"],
                "high_max": ev["high_max"],
                "move_pct_A": ev["move_pct_A"],
                "open_first": ev["open_first"],
                "close_last": ev["close_last"],
                "move_pct_B": ev["move_pct_B"],
            }
        )
    return out


async def main_async() -> int:
    p = argparse.ArgumentParser(description="Лог пампов 1h: метрики A и B в CSV")
    p.add_argument("--symbol", help="Одна пара, напр. 1000PEPEUSDT")
    p.add_argument("--symbols", help="Список через запятую")
    p.add_argument("--days", type=float, default=45.0, help="Глубина истории назад от now (дней)")
    p.add_argument("--out", default="data/pumps_1h_ab.csv", help="Путь к CSV")
    p.add_argument("--min-pct", type=float, default=8.0, help="Мин. move_pct_A")
    p.add_argument("--max-pct", type=float, default=30.0, help="Макс. move_pct_A")
    p.add_argument("--k-min", type=int, default=2, help="Мин. число свечей в окне")
    p.add_argument("--k-max", type=int, default=10, help="Макс. число свечей в окне")
    args = p.parse_args()

    if args.symbol:
        symbols = [args.symbol.strip().upper()]
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        print("Укажи --symbol или --symbols", file=sys.stderr)
        return 1

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(args.days * 24 * HOUR_MS)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "symbol",
        "start_open_time_utc",
        "end_open_time_utc",
        "end_close_time_utc",
        "k",
        "low_min",
        "high_max",
        "move_pct_A",
        "open_first",
        "close_last",
        "move_pct_B",
    ]

    total = 0
    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for sym in symbols:
                rows = await run_for_symbol(
                    client,
                    sym,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    k_min=args.k_min,
                    k_max=args.k_max,
                    min_pct=args.min_pct,
                    max_pct=args.max_pct,
                )
                for row in rows:
                    w.writerow(row)
                total += len(rows)
                print(f"{sym}: записей {len(rows)}")

    print(f"Всего строк: {total} | {out_path.resolve()}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
