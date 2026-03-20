"""
liquidity_sweep_reversal: вынос high/low + возврат внутрь → вход в сторону возврата.
"""
from typing import Optional

import config
from structure import (
    atr_pct,
    compute_tp_zone_long,
    compute_tp_zone_short,
    ema20,
    structural_sl_long,
    structural_sl_short,
)


def _check_1h_structure(candles_1h: list[dict], direction: str, price: float) -> bool:
    """1h: LONG — есть поддержка (цена у swing/EMA), SHORT — сопротивление."""
    if len(candles_1h) < 20:
        return True
    ema_1h = ema20(candles_1h)
    last_1h = candles_1h[-1]
    close_1h = float(last_1h.get("close", 0) or 0)
    low_1h = float(last_1h.get("low", 0) or 0)
    high_1h = float(last_1h.get("high", 0) or 0)
    rng_1h = high_1h - low_1h
    if rng_1h <= 0:
        return True
    close_pos_1h = (close_1h - low_1h) / rng_1h
    if direction == "LONG":
        # Поддержка: 1h close не в самом низу (хотя бы 30% от лоу) или выше EMA
        if ema_1h and price >= ema_1h * 0.995:
            return True
        return close_pos_1h >= 0.3
    else:  # SHORT
        if ema_1h and price <= ema_1h * 1.005:
            return True
        return close_pos_1h <= 0.7


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
    oi_ctx: Optional[dict] = None,
) -> Optional[dict]:
    """
    Возвращает кандидат или None.
    Охота на ликвидность — только когда есть OI и волатильность.
    """
    if len(candles_15m) < config.SWEEP_MIN_CANDLES:
        return None

    if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
        return None

    # Sweep требует волатильности — не флет
    if atr_pct_1h is not None and atr_pct_1h < config.SWEEP_ATR_MIN_1H:
        return None

    # OI: при SWEEP_OI_REQUIRED без данных — стоп; иначе без OI пропускаем, с OI — проверяем порог
    oi_change = (oi_ctx or {}).get("oi_change_pct")
    if oi_change is None:
        if config.SWEEP_OI_REQUIRED:
            return None
    elif abs(oi_change) < config.SWEEP_OI_MIN_CHANGE_PCT:
        return None

    last = candles_15m[-1]
    close = _to_float(last.get("close"))
    if close < config.MIN_PRICE:
        return None
    prev_range = candles_15m[-config.SWEEP_LOOKBACK - 1 : -1]

    high = _to_float(last.get("high"))
    low = _to_float(last.get("low"))
    open_ = _to_float(last.get("open"))

    prev_high = max(_to_float(c.get("high")) for c in prev_range) if prev_range else high
    prev_low = min(_to_float(c.get("low")) for c in prev_range) if prev_range else low

    body = abs(close - open_)
    rng = high - low
    if rng <= 0:
        return None

    wick_up = high - max(open_, close)
    wick_down = min(open_, close) - low

    atr_15m = atr_pct(candles_15m, 14)
    atr_val = (close * (atr_15m or 0) / 100) if atr_15m else (rng * 1.5)
    ema_val = ema20(candles_15m)

    # Позиция закрытия в теле: LONG — close в верхних 50%, SHORT — в нижних 50%
    close_position = (close - low) / rng if rng > 0 else 0.5

    # SHORT sweep — длинная верхняя тень
    wick_up_pct = wick_up / rng if rng > 0 else 0
    if high > prev_high and close < prev_high and wick_up >= body * config.SWEEP_MIN_WICK_TO_BODY and wick_up_pct >= config.SWEEP_MIN_WICK_PCT_OF_RANGE:
        if close_position > (1 - config.SWEEP_CLOSE_POSITION_MIN):
            return None  # для SHORT нужен close в нижней части
        if config.SWEEP_1H_STRUCTURE and not _check_1h_structure(candles_1h, "SHORT", close):
            return None
        entry = close
        sweep_high = high * 1.002
        stop = structural_sl_short(candles_15m, high, ema_val, entry)
        risk = stop - entry
        if risk <= 0 or risk < entry * 0.002:
            return None

        structural_tp = None  # для SHORT — swing low ниже
        from structure import nearest_swing_low_below
        structural_tp = nearest_swing_low_below(candles_15m, entry)
        tp_zone = compute_tp_zone_short(entry, stop, config.SWEEP_RR_TARGET, atr_val, structural_tp)

        score = config.SWEEP_BASE_SCORE
        if atr_pct_1h and atr_pct_1h > config.ATR_PUMP_BONUS_PCT:
            score += 10

        vol_last = _to_float(last.get("volume", 0))
        vol_avg = sum(_to_float(c.get("volume", 0)) for c in candles_15m[-21:-1]) / 20 if len(candles_15m) >= 21 else vol_last
        if vol_avg > 0 and vol_last >= vol_avg * 1.5:
            score += 10

        rr = (entry - (tp_zone[0] + tp_zone[1]) / 2) / risk if risk > 0 else 0
        if rr < config.SWEEP_RR_MIN:
            return None

        return {
            "strategy": "liquidity_sweep_reversal",
            "symbol": symbol,
            "direction": "SHORT",
            "trigger_price": entry,
            "entry": entry,
            "stop": stop,
            "tp_zone": tp_zone,
            "reason_ru": "Цена вынесла хай, откатилась — ждём движение вниз",
            "score": score,
            "rr": rr,
            "atr_pct_1h": atr_pct_1h,
        }

    # LONG sweep — длинная нижняя тень, не мелочевка
    wick_down_pct = wick_down / rng if rng > 0 else 0
    if low < prev_low and close > prev_low and wick_down >= body * config.SWEEP_MIN_WICK_TO_BODY and wick_down_pct >= config.SWEEP_MIN_WICK_PCT_OF_RANGE:
        if close_position < config.SWEEP_CLOSE_POSITION_MIN:
            return None  # для LONG нужен close в верхней части диапазона
        if config.SWEEP_1H_STRUCTURE and not _check_1h_structure(candles_1h, "LONG", close):
            return None
        entry = close
        sweep_low = low * 0.998
        stop = structural_sl_long(candles_15m, low, ema_val, entry)
        risk = entry - stop
        if risk <= 0 or risk < entry * 0.002:
            return None

        from structure import nearest_swing_high_above
        structural_tp = nearest_swing_high_above(candles_15m, entry)
        tp_zone = compute_tp_zone_long(entry, stop, config.SWEEP_RR_TARGET, atr_val, structural_tp)

        score = config.SWEEP_BASE_SCORE
        if atr_pct_1h and atr_pct_1h > config.ATR_PUMP_BONUS_PCT:
            score += 10

        vol_last = _to_float(last.get("volume", 0))
        vol_avg = sum(_to_float(c.get("volume", 0)) for c in candles_15m[-21:-1]) / 20 if len(candles_15m) >= 21 else vol_last
        if vol_avg > 0 and vol_last >= vol_avg * 1.5:
            score += 10

        rr = ((tp_zone[0] + tp_zone[1]) / 2 - entry) / risk if risk > 0 else 0
        if rr < config.SWEEP_RR_MIN:
            return None

        return {
            "strategy": "liquidity_sweep_reversal",
            "symbol": symbol,
            "direction": "LONG",
            "trigger_price": entry,
            "entry": entry,
            "stop": stop,
            "tp_zone": tp_zone,
            "reason_ru": "Цена вынесла лоу, вернулась внутрь — ждём отскок вверх",
            "score": score,
            "rr": rr,
            "atr_pct_1h": atr_pct_1h,
        }

    return None
