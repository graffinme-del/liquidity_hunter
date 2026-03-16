"""
volatility_expansion: сжатие → расширение ATR + объёмный пробой.
"""
from typing import Optional

import config
from structure import (
    atr_pct,
    compute_tp_zone_long,
    compute_tp_zone_short,
    nearest_swing_high_above,
    nearest_swing_low_below,
)


def _to_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _atr_pct(candles: list[dict], period: int) -> Optional[float]:
    return atr_pct(candles, period)


def detect(
    symbol: str,
    candles_15m: list[dict],
    candles_1h: list[dict],
    atr_pct_1h: Optional[float],
    oi_ctx: Optional[dict] = None,
) -> Optional[dict]:
    if len(candles_15m) < max(30, config.EXP_ATR_LOOKBACK + config.EXP_ATR_COOLDOWN + 5):
        return None

    if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
        return None

    closed = candles_15m[:-1] if len(candles_15m) > 1 else candles_15m
    if len(closed) < config.EXP_ATR_LOOKBACK + config.EXP_ATR_COOLDOWN + 1:
        return None

    atr_now = _atr_pct(closed, config.EXP_ATR_LOOKBACK)
    prev_window = closed[: -config.EXP_ATR_COOLDOWN]
    atr_prev = _atr_pct(prev_window, config.EXP_ATR_LOOKBACK) if len(prev_window) >= config.EXP_ATR_LOOKBACK else None

    if atr_now is None or atr_prev is None:
        return None
    if atr_now < config.EXP_ATR_MIN_LEVEL_PCT:
        return None
    if atr_now - atr_prev < config.EXP_ATR_MIN_GROWTH_PCT:
        return None

    range_candles = closed[-config.EXP_RANGE_LOOKBACK:]
    range_high = max(_to_float(c.get("high")) for c in range_candles)
    range_low = min(_to_float(c.get("low")) for c in range_candles)
    last_close = _to_float(range_candles[-1].get("close"))
    range_pct = (range_high - range_low) / last_close * 100 if last_close > 0 else 100
    if range_pct > config.EXP_MAX_RANGE_PCT:
        return None

    last = closed[-1]
    vol_last = _to_float(last.get("volume", 0))
    vol_prev = closed[-config.EXP_VOL_LOOKBACK - 1:-1] if len(closed) >= config.EXP_VOL_LOOKBACK + 1 else closed[:-1]
    vol_avg = sum(_to_float(c.get("volume", 0)) for c in vol_prev) / len(vol_prev) if vol_prev else vol_last
    if vol_avg <= 0 or vol_last < vol_avg * config.EXP_VOL_MULT:
        return None

    close = _to_float(last.get("close"))
    atr_val = (close * atr_now / 100) if atr_now else (range_high - range_low)

    oi_ok = False
    if oi_ctx and isinstance(oi_ctx, dict):
        oi_ch = oi_ctx.get("oi_change_pct") or oi_ctx.get("change_15m")
        if oi_ch is not None and float(oi_ch) >= config.EXP_OI_MIN_ACCUM_PCT:
            oi_ok = True

    direction = None
    if close > range_high:
        direction = "LONG"
    elif close < range_low:
        direction = "SHORT"

    if direction is None:
        return None

    score = config.EXP_BASE_SCORE
    if oi_ok:
        score += 10
    if atr_now - atr_prev > 0.2:
        score += 5

    if direction == "LONG":
        entry = range_high
        stop = range_low
        risk = entry - stop
        if risk <= 0:
            return None
        structural_tp = nearest_swing_high_above(candles_15m, entry)
        tp_zone = compute_tp_zone_long(entry, stop, config.EXP_RR_MIN, atr_val, structural_tp, max_atr_mult=2.0)
        rr = ((tp_zone[0] + tp_zone[1]) / 2 - entry) / risk if risk > 0 else 0
        reason = "Долгое затишье, потом объёмный пробой вверх — начало движения"
    else:
        entry = range_low
        stop = range_high
        risk = stop - entry
        if risk <= 0:
            return None
        structural_tp = nearest_swing_low_below(candles_15m, entry)
        tp_zone = compute_tp_zone_short(entry, stop, config.EXP_RR_MIN, atr_val, structural_tp, max_atr_mult=2.0)
        rr = (entry - (tp_zone[0] + tp_zone[1]) / 2) / risk if risk > 0 else 0
        reason = "Долгое затишье, потом объёмный пробой вниз — начало движения"

    return {
        "strategy": "volatility_expansion",
        "symbol": symbol,
        "direction": direction,
        "trigger_price": entry,
        "entry": entry,
        "stop": stop,
        "tp_zone": tp_zone,
        "reason_ru": reason,
        "score": score,
        "rr": rr,
        "atr_pct_1h": atr_pct_1h,
    }
