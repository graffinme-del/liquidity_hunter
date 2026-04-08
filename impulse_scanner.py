"""
Быстрые импульсы на 15m: рост цены за 1–3 закрытые свечи (раньше, чем «памп» по EMA20 1h).
Не сигнал входа — наблюдение / раннее предупреждение.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from typing import Any, Optional

import aiohttp

import config
from data.binance_client import BinanceClient
from telegram_notify import ephemeral_delete_seconds, send_telegram

log = logging.getLogger(__name__)

_last_impulse_alert_at: dict[str, float] = {}


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _impulse_long_pct_last_closed(closed: list[dict]) -> tuple[float, int]:
    """
    Максимальный лонг-импульс за последние 1–3 закрытые свечи 15m.
    k=1: тело последней свечи (close vs open).
    k=2: от open предпоследней до close последней.
    k=3: от open третьей с конца до close последней.
    Возвращает (pct, k) или (0, 0) если нет смысла.
    """
    if len(closed) < 3:
        return 0.0, 0
    o = lambda b: _to_float(b.get("open"))
    c = lambda b: _to_float(b.get("close"))
    b1, b2, b3 = closed[-3], closed[-2], closed[-1]
    if o(b3) <= 0:
        return 0.0, 0
    k1 = (c(b3) - o(b3)) / o(b3) * 100.0
    k2 = (c(b3) - o(b2)) / o(b2) * 100.0 if o(b2) > 0 else 0.0
    k3 = (c(b3) - o(b1)) / o(b1) * 100.0 if o(b1) > 0 else 0.0
    k1 = max(0.0, k1)
    k2 = max(0.0, k2)
    k3 = max(0.0, k3)
    scores = [(k1, 1), (k2, 2), (k3, 3)]
    best_pct, which = max(scores, key=lambda x: x[0])
    if best_pct <= 0:
        return 0.0, 0
    return best_pct, which


def _analyze_impulse_15m(candles: list[dict]) -> Optional[dict]:
    """Только закрытые свечи; последняя в данных может быть незакрыта — отбрасываем."""
    if len(candles) < 5:
        return None
    closed = candles[:-1] if len(candles) > 1 else candles
    if len(closed) < 3:
        return None
    last = closed[-1]
    close = _to_float(last.get("close"))
    if close < getattr(config, "MIN_PRICE", 0.01):
        return None
    pct, k = _impulse_long_pct_last_closed(closed)
    mn = getattr(config, "IMPULSE_15M_MIN_PCT", 15.0)
    if pct < mn:
        return None
    return {
        "pct": round(pct, 2),
        "candles": k,
        "close": round(close, 8),
    }


def build_impulse_alert_text(hits: list[dict]) -> str:
    mn = getattr(config, "IMPULSE_15M_MIN_PCT", 15.0)
    if not hits:
        return (
            f"<b>Импульс 15m (≥{mn:.0f}% за 1–3 свечи)</b>\n"
            "<i>Сейчас нет пар под порог.</i>"
        )
    lines = [
        f"<b>Импульс 15m (≥{mn:.0f}% за 1–3 свечи)</b>",
        "Лонг: от open начала окна до close последней закрытой свечи.",
        "",
    ]
    for h in hits[:25]:
        sym = html.escape(str(h.get("symbol", "?")))
        pct = h.get("pct", 0)
        k = h.get("candles", 0)
        lines.append(f"  <b>{sym}</b> +{pct}% ({k}×15m подряд)")
    if len(hits) > 25:
        lines.append(f"... и ещё {len(hits) - 25}")
    return "\n".join(lines)


async def scan_impulse_hits(*, respect_dedup: bool = True) -> list[dict]:
    from dotenv import load_dotenv

    load_dotenv()

    hits: list[dict] = []
    now = time.time()
    dedup_sec = getattr(config, "IMPULSE_15M_DEDUP_MIN", 45) * 60

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        max_sym = getattr(config, "IMPULSE_15M_MAX_SYMBOLS", 200)
        min_qv = getattr(config, "IMPULSE_15M_MIN_QUOTE_VOL_24H", 50_000.0)
        sort_by = getattr(config, "IMPULSE_15M_SYMBOL_SORT", "abs_change_24h")
        symbols = await client.get_symbols_for_movement_scan(
            min_qv,
            0 if max_sym <= 0 else 99999,
            sort_by=sort_by,
        )
        if getattr(config, "IMPULSE_15M_SHUFFLE", False):
            random.shuffle(symbols)
        if max_sym > 0:
            symbols = symbols[:max_sym]

        for symbol in symbols:
            try:
                if (
                    respect_dedup
                    and symbol in _last_impulse_alert_at
                    and now - _last_impulse_alert_at[symbol] < dedup_sec
                ):
                    pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                    if pause > 0:
                        await asyncio.sleep(pause)
                    continue

                candles = await client.get_klines(symbol, "15m", 100)
                info = _analyze_impulse_15m(candles)
                if info:
                    info["symbol"] = symbol
                    hits.append(info)
                    if respect_dedup:
                        _last_impulse_alert_at[symbol] = now

            except Exception as e:
                print(f"[IMPULSE] {symbol}: {e}")
            finally:
                pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                if pause > 0:
                    await asyncio.sleep(pause)

    cutoff = now - 86400
    for k in list(_last_impulse_alert_at.keys()):
        if _last_impulse_alert_at[k] < cutoff:
            del _last_impulse_alert_at[k]

    hits.sort(key=lambda x: x.get("pct", 0), reverse=True)
    return hits


async def run_impulse_scan(send_tg: bool = True) -> tuple[list[dict], bool]:
    hits = await scan_impulse_hits(respect_dedup=True)

    if not send_tg or not hits:
        return hits, True

    text = build_impulse_alert_text(hits)
    sec = ephemeral_delete_seconds()
    ok = await send_telegram(text, parse_mode="HTML", delete_after_sec=sec if sec > 0 else None)
    if ok:
        log.info(
            "[IMPULSE] Отправлено в TG (%s пар), удаление через %s с",
            len(hits),
            sec,
        )
    else:
        log.error("[IMPULSE] send_telegram не удалось (%s пар)", len(hits))
    return hits, ok
