#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
Сканер + планировщик (отчёт в 21:00).
"""
import asyncio

from scanner import run_scanner
from scheduler import run_scheduler


async def main():
    await asyncio.gather(run_scanner(), run_scheduler())


if __name__ == "__main__":
    asyncio.run(main())
