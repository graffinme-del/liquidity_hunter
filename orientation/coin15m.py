"""
Подтверждение 15m: EMA + MACD на последней закрытой свече (как pulse_pilot coin_indicator_filter).
Включение: COIN_INDICATOR_FILTER_ENABLED=1
"""

from __future__ import annotations

import os

from .ema_macd import closes_from_candles, ema_series, macd_components


def _truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default


def _last_closed_index(candles: list[dict]) -> int:
    if len(candles) < 2:
        return -1
    return -2


def should_skip_coin_indicators(direction: str, candles_15m: list[dict] | None) -> tuple[bool, str]:
    if not _truthy("COIN_INDICATOR_FILTER_ENABLED", "0"):
        return False, ""
    if not candles_15m:
        return False, ""

    min_bars = _env_int("COIN_CONTEXT_MIN_BARS", 50)
    if len(candles_15m) < min_bars:
        return True, f"need_{min_bars}_bars_15m"

    closes = closes_from_candles(candles_15m)
    ema_period = _env_int("COIN_CONTEXT_EMA_PERIOD", 20)
    ema_row = ema_series(closes, ema_period)
    if len(ema_row) != len(closes):
        return False, ""

    macd = macd_components(closes)
    if macd is None:
        return True, "macd_warmup"
    _m, _s, hist = macd

    idx = _last_closed_index(candles_15m)
    idx_abs = len(closes) + idx
    if idx_abs < 0 or idx_abs >= len(closes):
        return False, ""

    close_i = closes[idx]
    ema_i = ema_row[idx]
    hist_i = hist[idx]
    d = str(direction or "").upper()
    eps = max(close_i * 1e-9, 1e-12)

    if d == "LONG":
        if close_i <= ema_i - eps:
            return True, "close_below_ema"
        if hist_i <= 0:
            return True, "macd_hist_not_bull"
    elif d == "SHORT":
        if close_i >= ema_i + eps:
            return True, "close_above_ema"
        if hist_i >= 0:
            return True, "macd_hist_not_bear"

    recent = _env_int("COIN_CONTEXT_RECENT_BARS", 0)
    min_align = _env_float("COIN_CONTEXT_RECENT_MIN_ALIGN", 0.55)
    if recent > 0:
        n = len(closes)
        span = min(recent, max(0, n - 1))
        start = max(-n, -1 - span)
        ok = 0
        total = 0
        for j in range(start, -1):
            if j < -n:
                continue
            cj = closes[j]
            ej = ema_row[j]
            hj = hist[j]
            total += 1
            if d == "LONG":
                if cj > ej and hj > 0:
                    ok += 1
            else:
                if cj < ej and hj < 0:
                    ok += 1
        if total > 0 and (ok / total) < min_align:
            return True, f"recent_align_{ok}/{total}"

    return False, ""
