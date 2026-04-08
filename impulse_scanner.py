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


def _mean_volume(bars: list[dict]) -> float:
    if not bars:
        return 0.0
    s = sum(_to_float(b.get("volume")) for b in bars)
    return s / len(bars)


def _volume_impulse_vs_ma(closed: list[dict], k: int, ma_lookback: int) -> tuple[float | None, float, float]:
    """
    Средний объём на свечу в окне импульса (последние k) vs средний объём в базе (ma_lookback свечей до окна).
    Возвращает (ratio или None если мало данных, mean_impulse, mean_baseline).
    """
    if k < 1 or ma_lookback < 1 or len(closed) < k + ma_lookback:
        return None, 0.0, 0.0
    baseline_bars = closed[-(k + ma_lookback) : -k]
    impulse_bars = closed[-k:]
    mb = _mean_volume(baseline_bars)
    mi = _mean_volume(impulse_bars)
    if mb <= 0:
        return None, mi, mb
    return mi / mb, mi, mb


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


def _analyze_impulse_15m_price_volume(closed: list[dict]) -> Optional[dict]:
    """
    Цена + объём (без taker). Только закрытые свечи; последняя в klines может быть незакрыта — не в closed.
    """
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

    ma_lb = getattr(config, "IMPULSE_15M_VOL_MA_LOOKBACK", 20)
    use_vol = getattr(config, "IMPULSE_15M_USE_VOLUME_MA", True)
    vol_ratio: float | None = None
    vol_mean_imp = 0.0
    vol_mean_base = 0.0
    if use_vol:
        vol_ratio, vol_mean_imp, vol_mean_base = _volume_impulse_vs_ma(closed, k, ma_lb)
        min_vr = getattr(config, "IMPULSE_15M_VOL_MIN_RATIO", 1.8)
        if vol_ratio is None or vol_ratio < min_vr:
            return None

    return {
        "pct": round(pct, 2),
        "candles": k,
        "close": round(close, 8),
        "vol_ratio": None if vol_ratio is None else round(vol_ratio, 2),
        "vol_mean_impulse": round(vol_mean_imp, 2),
        "vol_mean_baseline": round(vol_mean_base, 2),
    }


async def _taker_buy_sell_ratio(
    client: BinanceClient, symbol: str, k: int,
) -> float | None:
    """
    Агрегированный buyVol/sellVol за k последних по времени интервалов 15m.
    """
    limit = max(k, 3)
    rows = await client.get_taker_long_short(symbol, "15m", limit=limit)
    if not rows:
        return None
    rows_sorted = sorted(rows, key=lambda r: int(r.get("timestamp", 0)), reverse=True)
    chunk = rows_sorted[:k]
    buy_v = sum(_to_float(r.get("buy_vol")) for r in chunk)
    sell_v = sum(_to_float(r.get("sell_vol")) for r in chunk)
    if sell_v <= 1e-12:
        return None
    return buy_v / sell_v


def build_impulse_alert_text(hits: list[dict]) -> str:
    mn = getattr(config, "IMPULSE_15M_MIN_PCT", 15.0)
    use_vol = getattr(config, "IMPULSE_15M_USE_VOLUME_MA", True)
    use_tk = getattr(config, "IMPULSE_15M_USE_TAKER", True)
    if not hits:
        return (
            f"<b>Импульс 15m (≥{mn:.0f}% + объём + taker)</b>\n"
            "<i>Сейчас нет пар под порог.</i>"
        )
    filt = []
    if use_vol:
        filt.append(f"vol ≥{getattr(config, 'IMPULSE_15M_VOL_MIN_RATIO', 1.8):.1f}× к базе")
    if use_tk:
        filt.append(f"taker buy/sell ≥{getattr(config, 'IMPULSE_15M_TAKER_MIN_RATIO', 1.08):.2f}")
    sub = "; ".join(filt) if filt else "только цена"
    lines = [
        f"<b>Импульс 15m (≥{mn:.0f}% за 1–3 свечи)</b>",
        f"Фильтры: {sub}",
        "Лонг: от open начала окна до close последней закрытой свечи.",
        "",
    ]
    for h in hits[:25]:
        sym = html.escape(str(h.get("symbol", "?")))
        pct = h.get("pct", 0)
        k = h.get("candles", 0)
        vr = h.get("vol_ratio")
        tr = h.get("taker_ratio")
        extra = []
        if vr is not None:
            extra.append(f"vol×{vr}")
        if tr is not None:
            extra.append(f"taker {tr:.2f}")
        tail = f" — {' '.join(extra)}" if extra else ""
        lines.append(f"  <b>{sym}</b> +{pct}% ({k}×15m){tail}")
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
                if len(candles) < 5:
                    continue
                closed = candles[:-1] if len(candles) > 1 else candles
                info = _analyze_impulse_15m_price_volume(closed)
                if not info:
                    continue
                use_taker = getattr(config, "IMPULSE_15M_USE_TAKER", True)
                if use_taker:
                    kr = int(info["candles"])
                    taker_r = await _taker_buy_sell_ratio(client, symbol, kr)
                    min_tr = getattr(config, "IMPULSE_15M_TAKER_MIN_RATIO", 1.08)
                    if taker_r is None or taker_r < min_tr:
                        continue
                    info["taker_ratio"] = round(taker_r, 3)
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
