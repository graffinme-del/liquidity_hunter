"""
Статистика сигналов «старт пампа»: запись в SQLite, через 24h — оценка max роста от цены входа.
Команда /pumpstats в Telegram — сводка (сообщения в чате могут удаляться, база остаётся).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent / "data" / "pump_stats.sqlite"


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(_DB_PATH, timeout=30)


def init_db() -> None:
    con = _db()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS pump_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                tf TEXT,
                entry_price REAL NOT NULL,
                tg_ok INTEGER NOT NULL DEFAULT 1,
                max_pct_1h REAL,
                max_pct_4h REAL,
                max_pct_24h REAL,
                evaluated_at REAL,
                eval_error TEXT
            )
            """
        )
        con.commit()
    finally:
        con.close()


def record_early_pump_signals(hits: list[dict], tf: str, tg_ok: bool) -> None:
    if not getattr(config, "PUMP_STATS_ENABLED", True) or not hits:
        return
    init_db()
    now = time.time()
    tg = 1 if tg_ok else 0
    con = _db()
    try:
        for h in hits:
            sym = str(h.get("symbol") or "").strip()
            ep = h.get("close")
            if not sym or ep is None:
                continue
            con.execute(
                """
                INSERT INTO pump_signals (created_at, symbol, kind, tf, entry_price, tg_ok)
                VALUES (?, ?, 'early', ?, ?, ?)
                """,
                (now, sym, tf, float(ep), tg),
            )
        con.commit()
    finally:
        con.close()


def _max_up_pct(
    klines: list[dict], entry: float, start_ms: int, window_ms: int,
) -> float | None:
    if entry <= 0:
        return None
    end_ms = start_ms + window_ms
    max_h = entry
    for k in klines:
        ot = int(k.get("open_time", 0))
        if ot < start_ms:
            continue
        if ot >= end_ms:
            break
        max_h = max(max_h, float(k.get("high", 0)))
    return (max_h - entry) / entry * 100.0


async def evaluate_pending_signals(client) -> int:
    """
    Оценивает сигналы старше 24h (один раз): max рост от entry за 1h/4h/24h по 1m свечам.
    """
    if not getattr(config, "PUMP_STATS_ENABLED", True):
        return 0
    if not hasattr(client, "get_klines_range"):
        return 0
    init_db()
    eligible_before = time.time() - 24 * 3600
    con = _db()
    try:
        rows = con.execute(
            """
            SELECT id, created_at, symbol, entry_price FROM pump_signals
            WHERE evaluated_at IS NULL AND created_at <= ?
            ORDER BY created_at ASC
            LIMIT 25
            """,
            (eligible_before,),
        ).fetchall()
    finally:
        con.close()

    done = 0
    for row_id, created_at, symbol, entry_price in rows:
        start_ms = int(created_at * 1000)
        end_ms = start_ms + 24 * 3600 * 1000
        try:
            klines = await client.get_klines_range(symbol, "1m", start_ms, end_ms)
            if not klines:
                raise RuntimeError("no klines")
            p1 = _max_up_pct(klines, float(entry_price), start_ms, 3600 * 1000)
            p4 = _max_up_pct(klines, float(entry_price), start_ms, 4 * 3600 * 1000)
            p24 = _max_up_pct(klines, float(entry_price), start_ms, 24 * 3600 * 1000)
            now = time.time()
            con2 = _db()
            try:
                con2.execute(
                    """
                    UPDATE pump_signals SET
                        max_pct_1h = ?, max_pct_4h = ?, max_pct_24h = ?,
                        evaluated_at = ?, eval_error = NULL
                    WHERE id = ?
                    """,
                    (p1, p4, p24, now, row_id),
                )
                con2.commit()
            finally:
                con2.close()
            done += 1
        except Exception as e:
            err = str(e)[:200]
            log.warning("pump_stats evaluate id=%s %s: %s", row_id, symbol, err)
            con2 = _db()
            try:
                con2.execute(
                    "UPDATE pump_signals SET evaluated_at = ?, eval_error = ? WHERE id = ?",
                    (time.time(), err, row_id),
                )
                con2.commit()
            finally:
                con2.close()
            done += 1
    return done


def pump_stats_report_text() -> str:
    """HTML для sendMessage / _reply."""
    init_db()
    thr = float(getattr(config, "PUMP_STATS_HIT_MIN_PCT", 5.0))
    con = _db()
    try:
        total = con.execute("SELECT COUNT(*) FROM pump_signals WHERE kind='early'").fetchone()[0]
        ev = con.execute(
            "SELECT COUNT(*) FROM pump_signals WHERE kind='early' AND evaluated_at IS NOT NULL AND eval_error IS NULL",
        ).fetchone()[0]
        pend = con.execute(
            "SELECT COUNT(*) FROM pump_signals WHERE kind='early' AND evaluated_at IS NULL",
        ).fetchone()[0]
        hits = con.execute(
            """
            SELECT COUNT(*) FROM pump_signals
            WHERE kind='early' AND evaluated_at IS NOT NULL AND eval_error IS NULL
              AND max_pct_24h IS NOT NULL AND max_pct_24h >= ?
            """,
            (thr,),
        ).fetchone()[0]
        avg24 = con.execute(
            "SELECT AVG(max_pct_24h) FROM pump_signals WHERE kind='early' AND max_pct_24h IS NOT NULL",
        ).fetchone()[0]
    finally:
        con.close()

    hit_rate = (100.0 * hits / ev) if ev else 0.0
    avg_s = f"{avg24:.2f}" if avg24 is not None else "—"

    return (
        "<b>Статистика сигналов «старт пампа» (early)</b>\n"
        f"Всего записей: <code>{total}</code>\n"
        f"Ожидают оценки 24h: <code>{pend}</code>\n"
        f"Уже оценено: <code>{ev}</code>\n"
        f"«Взлёт» (max за 24h ≥ <code>{thr}%</code>): <code>{hits}</code>\n"
        f"Доля hit среди оцененных: <code>{hit_rate:.1f}%</code>\n"
        f"Средний max за 24h (оцененные): <code>{avg_s}%</code>\n"
        "<i>База: data/pump_stats.sqlite на сервере. Оценка через ~24h после сигнала.</i>"
    )
