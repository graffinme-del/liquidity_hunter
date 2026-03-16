"""
liquidity_sweep_continuation: ложный вынос + импульс в другую сторону.
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


def detect(
    symbol: str,
    candles_15m: list[dict],
    candles_1h: list[dict],
    atr_pct_1h: Optional[float],
) -> Optional[dict]:
    if len(candles_15m) < config.CONT_RANGE_LOOKBACK + config.CONT_FAKE_SWEEP_BARS + 5:
        return None

    if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
        return None

    closed = candles_15m[:-1] if len(candles_15m) > 1 else candles_15m
    range_candles = closed[-config.CONT_RANGE_LOOKBACK:]
    range_high = max(_to_float(c.get("high")) for c in range_candles)
    range_low = min(_to_float(c.get("low")) for c in range_candles)
    range_size = range_high - range_low
    if range_size <= 0:
        return None

    last = closed[-1]
    close = _to_float(last.get("close"))
    open_ = _to_float(last.get("open"))
    high = _to_float(last.get("high"))
    low = _to_float(last.get("low"))
    vol_last = _to_float(last.get("volume", 0))
    vol_avg = sum(_to_float(c.get("volume", 0)) for c in closed[-21:-1]) / 20 if len(closed) >= 21 else vol_last

    if vol_avg <= 0 or vol_last < vol_avg * config.CONT_VOLUME_MULT:
        return None

    body_pct = abs(close - open_) / close * 100 if close > 0 else 0
    if body_pct < config.CONT_BODY_MIN_PCT:
        return None

    atr_15m = atr_pct(candles_15m, 14)
    atr_val = (close * (atr_15m or 0) / 100) if atr_15m else range_size

    # Ищем ложный вынос в последних N свечах
    fake_sweep_candles = closed[-config.CONT_FAKE_SWEEP_BARS - 1:-1]
    fake_long = False
    fake_short = False
    fake_low = range_low
    fake_high = range_high

    for c in fake_sweep_candles:
        cl = _to_float(c.get("low"))
        ch = _to_float(c.get("high"))
        cc = _to_float(c.get("close"))
        co = _to_float(c.get("open"))
        if cl < range_low and cc > range_low:
            fake_long = True
            fake_low = min(fake_low, cl)
        if ch > range_high and cc < range_high:
            fake_short = True
            fake_high = max(fake_high, ch)

    # LONG: ложный вынос лоу + импульс вверх
    if fake_long and close > open_ and close > range_high:
        entry = close
        stop = min(range_low, fake_low) * 0.998
        risk = entry - stop
        if risk <= 0:
            return None

        structural_tp = nearest_swing_high_above(candles_15m, entry)
        tp_zone = compute_tp_zone_long(entry, stop, config.CONT_RR_TARGET, atr_val, structural_tp, max_atr_mult=2.5)

        score = config.CONT_BASE_SCORE
        rr = ((tp_zone[0] + tp_zone[1]) / 2 - entry) / risk if risk > 0 else 0

        return {
            "strategy": "liquidity_sweep_continuation",
            "symbol": symbol,
            "direction": "LONG",
            "trigger_price": entry,
            "entry": entry,
            "stop": stop,
            "tp_zone": tp_zone,
            "reason_ru": "Ложный пробой вниз, потом резкий разворот вверх — входим в лонг",
            "score": score,
            "rr": rr,
            "atr_pct_1h": atr_pct_1h,
        }

    # SHORT: ложный вынос хая + импульс вниз
    if fake_short and close < open_ and close < range_low:
        entry = close
        stop = max(range_high, fake_high) * 1.002
        risk = stop - entry
        if risk <= 0:
            return None

        structural_tp = nearest_swing_low_below(candles_15m, entry)
        tp_zone = compute_tp_zone_short(entry, stop, config.CONT_RR_TARGET, atr_val, structural_tp, max_atr_mult=2.5)

        score = config.CONT_BASE_SCORE
        rr = (entry - (tp_zone[0] + tp_zone[1]) / 2) / risk if risk > 0 else 0

        return {
            "strategy": "liquidity_sweep_continuation",
            "symbol": symbol,
            "direction": "SHORT",
            "trigger_price": entry,
            "entry": entry,
            "stop": stop,
            "tp_zone": tp_zone,
            "reason_ru": "Ложный пробой вверх, потом резкий разворот вниз — входим в шорт",
            "score": score,
            "rr": rr,
            "atr_pct_1h": atr_pct_1h,
        }

    return None
