#!/usr/bin/env python3
"""
Liquidity Hunter v1 — точка входа.
Запуск: python main.py
"""
import asyncio

from scanner import run_scanner


def main():
    asyncio.run(run_scanner())


if __name__ == "__main__":
    main()
