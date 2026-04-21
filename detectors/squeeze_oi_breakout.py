"""
Сжатие 5m + сближенные EMA + «плоский» MACD/ATR + две бычьи свечи + рост OI при плоской цене.

Свечи должны быть только закрытые; перед вызовом evaluate — attach_atr14_wilder(candles).
"""

from __future__ import annotations

import os
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default


def _closes(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for c in candles:
        try:
            out.append(float(c.get("close", 0.0) or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _ema_series(values: list[float], period: int) -> list[float]:
    n = len(values)
    if n < period:
        return []
    k = 2.0 / (period + 1)
    out = [0.0] * n
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, n):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _macd_hist_series(closes: list[float]) -> list[float] | None:
    n = len(closes)
    if n < 35:
        return None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    if len(e12) != n or len(e26) != n:
        return None
    macd_line = [e12[i] - e26[i] for i in range(n)]
    signal = _ema_series(macd_line, 9)
    if len(signal) != n:
        return None
    return [macd_line[i] - signal[i] for i in range(n)]


def attach_atr14_wilder(candles: list[dict]) -> None:
    """Wilder ATR(14) в поле atr14 для индексов >= 13."""
    n = len(candles)
    if n < 15:
        return
    trs: list[float] = []
    for i in range(n):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        if i == 0:
            trs.append(h - l)
        else:
            pc = float(candles[i - 1]["close"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    period = 14
    atr: list[float] = [0.0] * n
    atr[period - 1] = sum(trs[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    for i in range(n):
        candles[i]["atr14"] = atr[i] if i >= period - 1 else None


def squeeze_precheck_5m(candles: list[dict], compress_bars: int) -> bool:
    if len(candles) < compress_bars + 110:
        return False
    c1, c2 = candles[-2], candles[-1]
    try:
        o1, cl1 = float(c1["open"]), float(c1["close"])
        o2, cl2 = float(c2["open"]), float(c2["close"])
    except (TypeError, ValueError, KeyError):
        return False
    if o1 <= 0 or o2 <= 0:
        return False
    if cl1 <= o1 or cl2 <= o2:
        return False
    return True


def evaluate_squeeze_oi_breakout(
    candles: list[dict],
    oi_series: list[dict],
) -> dict[str, Any] | None:
    compress_bars = _env_int("SQUEEZE_OI_COMPRESS_BARS", 36)
    max_range_pct = _env_float("SQUEEZE_OI_MAX_RANGE_PCT", 1.25)
    ema_spread_max_pct = _env_float("SQUEEZE_OI_EMA_SPREAD_MAX_PCT", 0.4)
    macd_hist_max_ratio = _env_float("SQUEEZE_OI_MACD_HIST_MAX_RATIO", 0.0009)
    atr_max_pct = _env_float("SQUEEZE_OI_ATR_MAX_PCT", 0.14)
    atr_median_mult = _env_float("SQUEEZE_OI_ATR_MEDIAN_MULT", 1.35)
    min_body_pct = _env_float("SQUEEZE_OI_MIN_BODY_PCT", 0.1)
    min_oi_growth_pct = _env_float("SQUEEZE_OI_MIN_OI_GROWTH_PCT", 1.2)
    max_price_drift_pct = _env_float("SQUEEZE_OI_MAX_PRICE_DRIFT_PCT", 1.0)
    min_oi_points = _env_int("SQUEEZE_OI_MIN_OI_POINTS", 18)

    if not squeeze_precheck_5m(candles, compress_bars):
        return None
    if not oi_series or len(oi_series) < min_oi_points:
        return None

    n = len(candles)
    start = n - compress_bars - 2
    end_excl = n - 2
    if start < 0:
        return None
    comp = candles[start:end_excl]
    if len(comp) != compress_bars:
        return None

    highs = [float(c["high"]) for c in comp]
    lows = [float(c["low"]) for c in comp]
    range_high = max(highs)
    range_low = min(lows)
    mid = (range_high + range_low) / 2.0
    if mid <= 0:
        return None
    range_pct = (range_high - range_low) / mid * 100.0
    if range_pct > max_range_pct:
        return None

    closes = _closes(candles)
    if any(x <= 0 for x in closes[-compress_bars - 5 :]):
        return None

    idx_pre = n - 3
    ema20 = _ema_series(closes, 20)
    ema50 = _ema_series(closes, 50)
    ema100 = _ema_series(closes, 100)
    if len(ema20) != n or len(ema50) != n or len(ema100) != n:
        return None
    if idx_pre < 100:
        return None

    e20 = ema20[idx_pre]
    e50 = ema50[idx_pre]
    e100 = ema100[idx_pre]
    px = closes[idx_pre]
    ema_lo = min(e20, e50, e100)
    ema_hi = max(e20, e50, e100)
    ema_spread_pct = (ema_hi - ema_lo) / px * 100.0 if px else 999.0
    if ema_spread_pct > ema_spread_max_pct:
        return None

    hist = _macd_hist_series(closes)
    if hist is None or idx_pre >= len(hist):
        return None
    if abs(hist[idx_pre]) / px > macd_hist_max_ratio:
        return None

    atr_pct_window: list[float] = []
    for i in range(start, end_excl):
        c = candles[i]
        atr14 = c.get("atr14")
        if atr14 is None:
            return None
        a = float(atr14)
        cl = float(c["close"])
        if cl <= 0:
            return None
        atr_pct_window.append(a / cl * 100.0)
    atr_pre = float(candles[idx_pre].get("atr14") or 0.0)
    if atr_pre <= 0 or px <= 0:
        return None
    atr_pre_pct = atr_pre / px * 100.0
    if atr_pre_pct > atr_max_pct:
        return None
    sorted_atr_pct = sorted(atr_pct_window)
    med = sorted_atr_pct[len(sorted_atr_pct) // 2]
    if med > 0 and atr_pre_pct > med * atr_median_mult:
        return None

    for i in (-2, -1):
        c = candles[i]
        o = float(c["open"])
        cl = float(c["close"])
        if o <= 0 or cl <= o:
            return None
        body_pct = (cl - o) / o * 100.0
        if body_pct < min_body_pct:
            return None

    if float(candles[-1]["close"]) <= range_high:
        return None

    c0 = float(comp[0]["close"])
    cN = float(comp[-1]["close"])
    if c0 <= 0:
        return None
    drift_pct = abs(cN - c0) / c0 * 100.0
    if drift_pct > max_price_drift_pct:
        return None

    oi_sorted = sorted(
        (int(r.get("timestamp", 0)), float(r.get("open_interest", 0.0) or 0.0))
        for r in oi_series
        if isinstance(r, dict)
    )
    oi_sorted = [(t, v) for t, v in oi_sorted if v > 0]
    if len(oi_sorted) < min_oi_points:
        return None
    take = min(compress_bars, len(oi_sorted) - 1)
    oi_slice = oi_sorted[-(take + 1) :]
    if len(oi_slice) < 2:
        return None
    oi_a = oi_slice[0][1]
    oi_b = oi_slice[-1][1]
    oi_growth_pct = (oi_b - oi_a) / oi_a * 100.0
    if oi_growth_pct < min_oi_growth_pct:
        return None

    return {
        "range_pct": range_pct,
        "ema_spread_pct": ema_spread_pct,
        "macd_hist": hist[idx_pre],
        "atr_pre_pct": atr_pre_pct,
        "oi_growth_pct": oi_growth_pct,
        "price_drift_compress_pct": drift_pct,
        "compress_bars": compress_bars,
        "breakout_close": float(candles[-1]["close"]),
        "range_high": range_high,
    }
