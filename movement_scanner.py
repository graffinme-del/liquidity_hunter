"""
Сканер резкого движения: направление не важно — высокая волатильность / импульс на 15m.
Не «топ по объёму»: список пар из 24h тикера с фильтром мин. оборота + сортировка (движение / мелкие).
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional

import aiohttp

import config
from data.binance_client import BinanceClient
from telegram_notify import ephemeral_delete_seconds, send_telegram
from structure import atr_pct

_last_alert_at: dict[str, float] = {}


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _format_vol_tg_line(h: dict) -> str:
    """Одна строка: тикер жирный+курсив, смысл без дублирования цифр."""
    sym = h.get("symbol", "?")
    head = f"<b><i>{sym}</i></b>"
    atr = h.get("atr_pct")
    atr_was = h.get("atr_1h_ago")
    roc = h.get("roc_1h")
    rmult = h.get("range_mult")

    parts: list[str] = []
    if atr_was is not None and atr_was > 1e-9 and atr is not None:
        x = atr / atr_was
        parts.append(f"волатильность растёт (сейчас в {x:.2f}× от уровня ~1 ч назад)")
    elif atr is not None:
        parts.append(f"ATR к цене ~{atr:.1f}%")
    if roc is not None:
        parts.append(f"за ~1 ч цена сдвинулась на ~{roc:.1f}%")
    if rmult is not None and rmult >= getattr(config, "VOL_SCAN_RANGE_SPIKE_MULT", 2) - 0.01:
        parts.append(f"последняя свеча шире обычного (~{rmult:.1f}× к медиане)")

    if not parts:
        parts.append("резкий участок")
    return f"{head} — {'; '.join(parts)}"


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return (s[m - 1] + s[m]) / 2 if len(s) % 2 == 0 else s[m]


def _analyze_closed_15m(candles: list[dict]) -> Optional[dict]:
    """closed candles only, last = -1"""
    if len(candles) < 25:
        return None
    closed = candles[:-1] if len(candles) > 1 else candles
    if len(closed) < 25:
        return None

    atr_pct_val = atr_pct(closed, 14)
    last = closed[-1]
    h = _to_float(last.get("high"))
    l = _to_float(last.get("low"))
    c = _to_float(last.get("close"))
    if c <= 0:
        return None

    last_range_pct = (h - l) / c * 100.0

    ranges: list[float] = []
    for bar in closed[-21:-1]:
        hh = _to_float(bar.get("high"))
        ll = _to_float(bar.get("low"))
        cc = _to_float(bar.get("close"))
        if cc > 0:
            ranges.append((hh - ll) / cc * 100.0)
    med_range = _median(ranges) if ranges else 0.0

    roc_1h = 0.0
    if len(closed) >= 5:
        c0 = _to_float(closed[-5].get("close"))
        if c0 > 0:
            roc_1h = abs(c - c0) / c0 * 100.0

    range_mult = (last_range_pct / med_range) if med_range > 1e-9 else 0.0

    # ATR% «как было ~1ч назад» (4 закрытые свечи 15m)
    atr_1h_ago: Optional[float] = None
    if len(closed) >= 22:
        atr_1h_ago = atr_pct(closed[:-4], 14)

    hit = False
    reasons: list[str] = []
    if atr_pct_val is not None and atr_pct_val >= config.VOL_SCAN_ATR_PCT_MIN:
        hit = True
        reasons.append(f"ATR% {atr_pct_val:.2f}")
    if roc_1h >= config.VOL_SCAN_ROC_1H_MIN:
        hit = True
        reasons.append(f"ROC1h {roc_1h:.2f}%")
    if med_range > 0 and range_mult >= config.VOL_SCAN_RANGE_SPIKE_MULT:
        hit = True
        reasons.append(f"диапазон свечи ×{range_mult:.1f} к медиане")

    if not hit:
        return None

    # --- Фильтры «не остывший хвост» ---
    if getattr(config, "VOL_SCAN_REQUIRE_ATR_EXPANDING", False) and atr_1h_ago is None:
        return None

    if atr_1h_ago is not None and atr_1h_ago > 1e-9:
        ratio = (atr_pct_val or 0) / atr_1h_ago
        if getattr(config, "VOL_SCAN_REJECT_ATR_COOLING", True):
            if atr_pct_val is not None and atr_pct_val < atr_1h_ago:
                return None
        if getattr(config, "VOL_SCAN_REQUIRE_ATR_EXPANDING", False):
            min_r = getattr(config, "VOL_SCAN_ATR_EXPANSION_MIN_RATIO", 1.02)
            if atr_pct_val is None or atr_pct_val < atr_1h_ago * min_r:
                return None
        reasons.append(f"ATR/ATR_1h={ratio:.2f}")

    if getattr(config, "VOL_SCAN_REJECT_STALE_HIGH_ATR", True):
        if (
            atr_pct_val is not None
            and atr_pct_val >= getattr(config, "VOL_SCAN_STALE_ATR_PCT", 1.5)
            and roc_1h < getattr(config, "VOL_SCAN_STALE_ROC_MAX", 0.4)
        ):
            return None

    return {
        "atr_pct": round(atr_pct_val or 0, 3),
        "atr_1h_ago": round(atr_1h_ago, 3) if atr_1h_ago is not None else None,
        "roc_1h": round(roc_1h, 3),
        "range_mult": round(range_mult, 2),
        "last_range_pct": round(last_range_pct, 3),
        "reasons": reasons,
    }


async def run_movement_scan(send_tg: bool = True) -> list[dict]:
    """
    Возвращает список {symbol, ...метрики} для пар с резким движением.
    """
    from dotenv import load_dotenv
    load_dotenv()

    hits: list[dict] = []
    now = time.time()
    dedup_sec = config.VOL_SCAN_DEDUP_MIN * 60

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        max_sym = config.VOL_SCAN_MAX_SYMBOLS
        symbols = await client.get_symbols_for_movement_scan(
            config.VOL_SCAN_MIN_QUOTE_VOL_24H,
            0 if max_sym <= 0 else 99999,
            sort_by=config.VOL_SCAN_SYMBOL_SORT,
        )
        if config.VOL_SCAN_SHUFFLE:
            random.shuffle(symbols)
        if max_sym > 0:
            symbols = symbols[:max_sym]

        for symbol in symbols:
            try:
                if symbol in _last_alert_at and now - _last_alert_at[symbol] < dedup_sec:
                    pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                    if pause > 0:
                        await asyncio.sleep(pause)
                    continue

                candles = await client.get_klines(symbol, "15m", 100)
                info = _analyze_closed_15m(candles)
                if info:
                    info["symbol"] = symbol
                    hits.append(info)
                    _last_alert_at[symbol] = now

            except Exception as e:
                print(f"[VOL] {symbol}: {e}")
            finally:
                pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                if pause > 0:
                    await asyncio.sleep(pause)

    cutoff = now - 86400
    for k in list(_last_alert_at.keys()):
        if _last_alert_at[k] < cutoff:
            del _last_alert_at[k]

    if send_tg and hits:
        lines = ["<b>Резкое движение (15m)</b>", "Направление не указано — только волатильность.", ""]
        for h in hits[:40]:
            lines.append(_format_vol_tg_line(h))
        if len(hits) > 40:
            lines.append(f"... и ещё {len(hits) - 40}")
        text = "\n".join(lines)
        sec = ephemeral_delete_seconds()
        await send_telegram(text, parse_mode="HTML", delete_after_sec=sec if sec > 0 else None)

    return hits


def main():
    hits = asyncio.run(run_movement_scan(send_tg=False))
    print(f"Найдено: {len(hits)}")
    for h in hits[:30]:
        print(h)


if __name__ == "__main__":
    main()
