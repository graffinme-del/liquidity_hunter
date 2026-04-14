"""
Структурные уровни: swing high/low, EMA, расчёт SL/TP по «слому тренда» и «зоне цели».
"""
from typing import Optional


def _to_float(x: any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def atr_pct(candles: list[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(-period - 1, -1):
        h = _to_float(candles[i].get("high"))
        l = _to_float(candles[i].get("low"))
        pc = _to_float(candles[i - 1].get("close")) if i - 1 >= -len(candles) else l
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs) / len(trs)
    close = _to_float(candles[-1].get("close"))
    if close <= 0:
        return None
    return atr / close * 100.0


def find_swing_high(candles: list[dict], lookback: int = 2) -> Optional[float]:
    if len(candles) < lookback * 2 + 1:
        return None
    for i in range(len(candles) - lookback - 1, lookback - 1, -1):
        ok = True
        h = _to_float(candles[i].get("high"))
        for j in range(1, lookback + 1):
            if _to_float(candles[i - j].get("high")) >= h or _to_float(candles[i + j].get("high")) >= h:
                ok = False
                break
        if ok:
            return h
    return None


def find_swing_low(candles: list[dict], lookback: int = 2) -> Optional[float]:
    if len(candles) < lookback * 2 + 1:
        return None
    for i in range(len(candles) - lookback - 1, lookback - 1, -1):
        ok = True
        l = _to_float(candles[i].get("low"))
        for j in range(1, lookback + 1):
            if _to_float(candles[i - j].get("low")) <= l or _to_float(candles[i + j].get("low")) <= l:
                ok = False
                break
        if ok:
            return l
    return None


def swing_highs_above(candles: list[dict], price: float, lookback: int = 2) -> list[float]:
    out: list[float] = []
    for i in range(lookback, len(candles) - lookback):
        h = _to_float(candles[i].get("high"))
        if h <= price:
            continue
        ok = True
        for j in range(1, lookback + 1):
            if _to_float(candles[i - j].get("high")) >= h or _to_float(candles[i + j].get("high")) >= h:
                ok = False
                break
        if ok:
            out.append(h)
    return sorted(out)


def swing_lows_below(candles: list[dict], price: float, lookback: int = 2) -> list[float]:
    out: list[float] = []
    for i in range(lookback, len(candles) - lookback):
        l = _to_float(candles[i].get("low"))
        if l >= price:
            continue
        ok = True
        for j in range(1, lookback + 1):
            if _to_float(candles[i - j].get("low")) <= l or _to_float(candles[i + j].get("low")) <= l:
                ok = False
                break
        if ok:
            out.append(l)
    return sorted(out, reverse=True)


def ema20(candles: list[dict]) -> Optional[float]:
    closes = [_to_float(c.get("close")) for c in candles]
    return ema(closes, 20)


def structural_sl_long(
    candles: list[dict],
    sweep_low: float,
    ema_val: Optional[float],
    entry: float,
    buffer_pct: float = 0.002,
) -> float:
    """SL для LONG: max(sweep_low, swing_low, EMA) — самый жёсткий, но обязательно НИЖЕ entry."""
    candidates = [sweep_low * (1 - buffer_pct)]
    sw = find_swing_low(candles)
    if sw and sweep_low < sw < entry:
        candidates.append(sw * (1 - buffer_pct))
    if ema_val and sweep_low < ema_val < entry:
        candidates.append(ema_val * (1 - buffer_pct))
    valid = [c for c in candidates if 0 < c < entry]
    return max(valid) if valid else sweep_low * (1 - buffer_pct)


def structural_sl_short(
    candles: list[dict],
    sweep_high: float,
    ema_val: Optional[float],
    entry: float,
    buffer_pct: float = 0.002,
) -> float:
    """SL для SHORT: min(sweep_high, swing_high, EMA) — самый жёсткий, но обязательно ВЫШЕ entry."""
    candidates = [sweep_high * (1 + buffer_pct)]
    sw = find_swing_high(candles)
    if sw and entry < sw < sweep_high:
        candidates.append(sw * (1 + buffer_pct))
    if ema_val and entry < ema_val < sweep_high:
        candidates.append(ema_val * (1 + buffer_pct))
    valid = [c for c in candidates if c > entry]
    return min(valid) if valid else sweep_high * (1 + buffer_pct)


def nearest_swing_high_above(candles: list[dict], entry: float, lookback: int = 2) -> Optional[float]:
    """Ближайший swing high выше entry (для TP LONG)."""
    highs = swing_highs_above(candles, entry, lookback)
    return min(highs) if highs else None


def nearest_swing_low_below(candles: list[dict], entry: float, lookback: int = 2) -> Optional[float]:
    """Ближайший swing low ниже entry (для TP SHORT)."""
    lows = swing_lows_below(candles, entry, lookback)
    return max(lows) if lows else None


def compute_tp_zone_long(
    entry: float,
    stop: float,
    rr_target: float,
    atr: float,
    structural: Optional[float],
    max_atr_mult: float = 2.0,
) -> tuple[float, float]:
    """TP зона для LONG: (tp_low, tp_high)."""
    risk = entry - stop
    rr_tp = entry + risk * rr_target
    atr_limit = entry + atr * max_atr_mult
    tp = min(rr_tp, atr_limit)
    if structural and structural > entry:
        tp = min(tp, structural)
    zone_width = atr * 0.2
    return (tp - zone_width, tp + zone_width)


def compute_tp_zone_short(
    entry: float,
    stop: float,
    rr_target: float,
    atr: float,
    structural: Optional[float],
    max_atr_mult: float = 2.0,
) -> tuple[float, float]:
    """TP зона для SHORT: (tp_low, tp_high)."""
    risk = stop - entry
    rr_tp = entry - risk * rr_target
    atr_limit = entry - atr * max_atr_mult
    tp = max(rr_tp, atr_limit)
    if structural and structural < entry:
        tp = max(tp, structural)
    zone_width = atr * 0.2
    return (tp - zone_width, tp + zone_width)


def planned_reward_pct(signal: dict) -> float:
    """
    Плановый профит % от цены входа до середины зоны TP (как в фильтре сканера).
    LONG: (tp_mid − entry) / entry × 100; SHORT: (entry − tp_mid) / entry × 100.
    """
    try:
        entry = float(signal.get("trigger_price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if entry <= 0:
        return 0.0
    tz = signal.get("tp_zone")
    if isinstance(tz, (list, tuple)) and len(tz) >= 2:
        tp_mid = (float(tz[0]) + float(tz[1])) / 2.0
    elif tz is not None:
        try:
            tp_mid = float(tz)
        except (TypeError, ValueError):
            return 0.0
    else:
        return 0.0
    direction = str(signal.get("direction", "")).upper()
    if direction == "LONG":
        return (tp_mid - entry) / entry * 100.0
    if direction == "SHORT":
        return (entry - tp_mid) / entry * 100.0
    return 0.0


def signal_plan_fingerprint(signal: dict) -> str:
    """
    Ключ дедупликации: символ, направление и округлённые триггер / стоп / середина зоны TP.
    Одинаковый план не шлём повторно, пока не истечёт окно DEDUP (см. scanner + файл data/scanner_dedup.json).
    """
    sym = str(signal.get("symbol", "")).strip().upper()
    direction = str(signal.get("direction", "")).strip().upper()
    try:
        entry = float(signal.get("trigger_price", 0) or 0)
        stop = float(signal.get("stop", 0) or 0)
    except (TypeError, ValueError):
        return f"{sym}|{direction}|invalid"
    tz = signal.get("tp_zone")
    if isinstance(tz, (list, tuple)) and len(tz) >= 2:
        tp_mid = (float(tz[0]) + float(tz[1])) / 2.0
    elif tz is not None:
        try:
            tp_mid = float(tz)
        except (TypeError, ValueError):
            tp_mid = 0.0
    else:
        tp_mid = 0.0
    return f"{sym}|{direction}|{entry:.8g}|{stop:.8g}|{tp_mid:.8g}"
