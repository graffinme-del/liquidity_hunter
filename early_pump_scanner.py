"""
Старт пампа на 15m: перед импульсом — «тихий» участок, на последней закрытой свече —
умеренный рост + всплеск объёма к медиане. Это раньше, чем скринер по EMA20 1h.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import time
from typing import Any, Optional

import aiohttp

import config
from data.binance_client import BinanceClient
from impulse_scanner import _taker_buy_sell_ratio
from telegram_notify import ephemeral_delete_seconds, send_telegram

log = logging.getLogger(__name__)

_last_early_pump_at: dict[str, float] = {}


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return (s[m - 1] + s[m]) / 2 if len(s) % 2 == 0 else s[m]


def _bar_range_pct(bar: dict) -> float:
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    if c <= 0:
        return 0.0
    return (h - l) / c * 100.0


def _body_pct_long(bar: dict) -> float | None:
    o = _to_float(bar.get("open"))
    c = _to_float(bar.get("close"))
    if o <= 0 or c <= o:
        return None
    return (c - o) / o * 100.0


def _quiet_and_spike(closed: list[dict]) -> Optional[dict]:
    """
    Последняя свеча — кандидат на «старт»; до неё — тихое окно.
    """
    quiet_n = int(getattr(config, "EARLY_PUMP_QUIET_LOOKBACK", 16))
    need = quiet_n + 3
    if len(closed) < need:
        return None

    quiet_bars = closed[-(quiet_n + 1) : -1]
    if len(quiet_bars) < quiet_n:
        return None

    range_med = _median([_bar_range_pct(b) for b in quiet_bars])
    max_quiet = float(getattr(config, "EARLY_PUMP_QUIET_RANGE_MAX", 2.2))
    if range_med > max_quiet:
        return None

    last = closed[-1]
    body = _body_pct_long(last)
    if body is None:
        return None
    bmin = float(getattr(config, "EARLY_PUMP_BODY_MIN_PCT", 1.0))
    bmax = float(getattr(config, "EARLY_PUMP_BODY_MAX_PCT", 7.0))
    if body < bmin or body > bmax:
        return None

    vol_lb = int(getattr(config, "EARLY_PUMP_VOL_MEDIAN_LOOKBACK", 16))
    vol_hist = [_to_float(b.get("volume")) for b in closed[-(vol_lb + 1) : -1]]
    med_v = _median(vol_hist)
    if med_v <= 1e-12:
        return None
    v_last = _to_float(last.get("volume"))
    spike = float(getattr(config, "EARLY_PUMP_VOL_SPIKE_MULT", 2.2))
    vol_ratio = v_last / med_v
    if vol_ratio < spike:
        return None

    close = _to_float(last.get("close"))
    if close < getattr(config, "MIN_PRICE", 0.01):
        return None

    return {
        "pct": round(body, 2),
        "vol_ratio": round(vol_ratio, 2),
        "quiet_range_med": round(range_med, 3),
        "close": round(close, 8),
    }


def build_early_pump_alert_text(hits: list[dict]) -> str:
    bmin = float(getattr(config, "EARLY_PUMP_BODY_MIN_PCT", 1.0))
    bmax = float(getattr(config, "EARLY_PUMP_BODY_MAX_PCT", 7.0))
    sp = float(getattr(config, "EARLY_PUMP_VOL_SPIKE_MULT", 2.2))
    qr = float(getattr(config, "EARLY_PUMP_QUIET_RANGE_MAX", 2.2))
    if not hits:
        return (
            f"<b>Старт пампа 15m</b>\n"
            f"<i>Нет пар: тишина ≤{qr}% + тело {bmin:.0f}–{bmax:.0f}% + vol ≥{sp:.1f}× медианы.</i>"
        )
    lines = [
        "<b>Старт пампа 15m</b>",
        f"Тихий фон (медиана диапазона ≤{qr}%), затем зелёная свеча "
        f"{bmin:.0f}–{bmax:.0f}% и объём ≥{sp:.1f}× к медиане объёма до неё.",
        "",
    ]
    for h in hits[:25]:
        sym = html.escape(str(h.get("symbol", "?")))
        pct = h.get("pct", 0)
        vr = h.get("vol_ratio")
        qm = h.get("quiet_range_med")
        tr = h.get("taker_ratio")
        extra = [f"vol×{vr}", f"тишина {qm}%"]
        if tr is not None:
            extra.append(f"taker {tr:.2f}")
        lines.append(f"  <b>{sym}</b> +{pct}% ({', '.join(extra)})")
    if len(hits) > 25:
        lines.append(f"... и ещё {len(hits) - 25}")
    return "\n".join(lines)


async def scan_early_pump_hits(*, respect_dedup: bool = True) -> list[dict]:
    from dotenv import load_dotenv

    load_dotenv()

    hits: list[dict] = []
    now = time.time()
    dedup_sec = getattr(config, "EARLY_PUMP_DEDUP_MIN", 30) * 60
    cnt_pre = 0
    cnt_after_taker = 0

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        max_sym = getattr(config, "EARLY_PUMP_MAX_SYMBOLS", 200)
        min_qv = getattr(config, "EARLY_PUMP_MIN_QUOTE_VOL_24H", 25_000.0)
        sort_by = getattr(config, "EARLY_PUMP_SYMBOL_SORT", "abs_change_24h")
        symbols = await client.get_symbols_for_movement_scan(
            min_qv,
            0 if max_sym <= 0 else 99999,
            sort_by=sort_by,
        )
        if getattr(config, "EARLY_PUMP_SHUFFLE", False):
            random.shuffle(symbols)
        if max_sym > 0:
            symbols = symbols[:max_sym]

        for symbol in symbols:
            try:
                if (
                    respect_dedup
                    and symbol in _last_early_pump_at
                    and now - _last_early_pump_at[symbol] < dedup_sec
                ):
                    pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                    if pause > 0:
                        await asyncio.sleep(pause)
                    continue

                candles = await client.get_klines(symbol, "15m", 100)
                if len(candles) < 5:
                    continue
                closed = candles[:-1] if len(candles) > 1 else candles
                base = _quiet_and_spike(closed)
                if not base:
                    continue
                cnt_pre += 1

                use_taker = getattr(config, "EARLY_PUMP_USE_TAKER", True)
                ignore_empty = getattr(config, "EARLY_PUMP_TAKER_IGNORE_EMPTY", True)
                min_tr = float(getattr(config, "EARLY_PUMP_TAKER_MIN_RATIO", 1.02))
                if use_taker:
                    taker_r = await _taker_buy_sell_ratio(client, symbol, 1)
                    if taker_r is None:
                        if not ignore_empty:
                            continue
                    elif taker_r < min_tr:
                        continue
                    if taker_r is not None:
                        base["taker_ratio"] = round(taker_r, 3)
                cnt_after_taker += 1

                base["symbol"] = symbol
                hits.append(base)
                if respect_dedup:
                    _last_early_pump_at[symbol] = now

            except Exception as e:
                print(f"[EARLY] {symbol}: {e}")
            finally:
                pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                if pause > 0:
                    await asyncio.sleep(pause)

    cutoff = now - 86400
    for k in list(_last_early_pump_at.keys()):
        if _last_early_pump_at[k] < cutoff:
            del _last_early_pump_at[k]

    hits.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)

    if os.getenv("EARLY_PUMP_QUIET_DIAG", "").strip() not in ("1", "true", "yes"):
        print(
            f"[EARLY] диагностика: прошли тишина+тело+vol: {cnt_pre}, после taker: {cnt_after_taker}, "
            f"в алерт: {len(hits)}",
            flush=True,
        )
    return hits


async def run_early_pump_scan(send_tg: bool = True) -> tuple[list[dict], bool]:
    hits = await scan_early_pump_hits(respect_dedup=True)
    if not send_tg or not hits:
        return hits, True
    text = build_early_pump_alert_text(hits)
    sec = ephemeral_delete_seconds()
    ok = await send_telegram(text, parse_mode="HTML", delete_after_sec=sec if sec > 0 else None)
    if ok:
        log.info("[EARLY] Старт пампа: отправлено в TG (%s пар)", len(hits))
    else:
        log.error("[EARLY] send_telegram не удалось (%s пар)", len(hits))
    return hits, ok
