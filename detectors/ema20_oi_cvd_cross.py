"""
ema20_oi_cvd_cross: 15m EMA20 cross confirmed by rising OI and CVD direction.

LONG: last closed 15m candle crosses EMA20 from below, OI grows, CVD is bullish.
SHORT: mirror price/CVD direction, but OI still must grow (new positions opening).
"""
from __future__ import annotations

import os
from typing import Optional

import config
from orientation.ema_macd import closes_from_candles, ema_series
from orientation.trinity_gate import _cvd_direction_ok, _taker_net_for_closed_bars
from structure import atr_pct, nearest_swing_high_above, nearest_swing_low_below


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        return default


def _enabled() -> bool:
    raw = os.getenv("EMA_CROSS_SIGNAL_ENABLED", "1")
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def _signal_min_tp_pct() -> float:
    raw = os.getenv("SIGNAL_MIN_TP_MOVE_PCT", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(getattr(config, "SIGNAL_MIN_TP_MOVE_PCT", 2.8))


def _to_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _tp_zone_from_target(entry: float, direction: str, target_pct: float, atr_value: float) -> tuple[float, float]:
    move = entry * target_pct / 100.0
    mid = entry + move if direction == "LONG" else entry - move
    width = max(atr_value * 0.2, entry * 0.001)
    return (mid - width, mid + width)


def _target_pct(entry: float, structural_tp: Optional[float], direction: str) -> float:
    min_target = max(
        _env_float("EMA_CROSS_MIN_TP_MOVE_PCT", _signal_min_tp_pct()),
        _signal_min_tp_pct(),
    )
    if structural_tp and entry > 0:
        if direction == "LONG" and structural_tp > entry:
            return max(min_target, (structural_tp - entry) / entry * 100.0)
        if direction == "SHORT" and structural_tp < entry:
            return max(min_target, (entry - structural_tp) / entry * 100.0)
    return min_target


def detect(
    symbol: str,
    candles_15m: list[dict],
    candles_1h: list[dict],
    atr_pct_1h: Optional[float],
    oi_flow_ctx: Optional[dict] = None,
) -> Optional[dict]:
    """Return an EMA20/OI/CVD cross signal candidate or None."""
    if not _enabled():
        return None
    ema_period = _env_int("TRINITY_EMA_PERIOD", 20)
    half = _env_int("TRINITY_CVD_HALF_BARS", 3)
    min_closed = max(ema_period + 2, half * 2 + 1)

    closed = candles_15m[:-1] if len(candles_15m) > 1 else candles_15m
    if len(closed) < min_closed:
        return None
    if atr_pct_1h is not None and atr_pct_1h < config.ATR_MIN_PCT_1H:
        return None

    closes = closes_from_candles(closed)
    ema_row = ema_series(closes, ema_period)
    if len(ema_row) != len(closes):
        return None

    prev_close, last_close = closes[-2], closes[-1]
    prev_ema, last_ema = ema_row[-2], ema_row[-1]
    if last_close < config.MIN_PRICE:
        return None

    eps = max(last_close * 1e-9, 1e-12)
    long_cross = prev_close <= prev_ema + eps and last_close > last_ema + eps
    short_cross = prev_close >= prev_ema - eps and last_close < last_ema - eps
    if not long_cross and not short_cross:
        return None

    oi_chg = _to_float((oi_flow_ctx or {}).get("oi_change_pct"), 0.0)
    min_oi = _env_float("TRINITY_MIN_OI_CHANGE_PCT", 0.0)
    if oi_chg <= min_oi:
        return None

    nets = _taker_net_for_closed_bars(closed)
    if nets is None:
        return None
    window = min(len(nets), half * 2)
    nets_win = nets[-window:]

    direction = "LONG" if long_cross else "SHORT"
    want_long = direction == "LONG"
    if not _cvd_direction_ok(nets_win, half, want_long=want_long):
        return None

    last = closed[-1]
    atr_15m = atr_pct(closed, 14)
    atr_value = last_close * (atr_15m or 0.0) / 100.0
    if atr_value <= 0:
        atr_value = max(_to_float(last.get("high")) - _to_float(last.get("low")), last_close * 0.003)

    if direction == "LONG":
        stop_base = min(_to_float(last.get("low")), last_ema)
        stop = stop_base * (1 - _env_float("EMA_CROSS_STOP_BUFFER_PCT", 0.002))
        risk = last_close - stop
        structural_tp = nearest_swing_high_above(closed, last_close)
        reason = "15m цена пересекла EMA20 снизу вверх, OI растёт, CVD подтверждает покупки"
    else:
        stop_base = max(_to_float(last.get("high")), last_ema)
        stop = stop_base * (1 + _env_float("EMA_CROSS_STOP_BUFFER_PCT", 0.002))
        risk = stop - last_close
        structural_tp = nearest_swing_low_below(closed, last_close)
        reason = "15m цена пересекла EMA20 сверху вниз, OI растёт, CVD подтверждает продажи"

    if risk <= 0:
        return None

    target_pct = _target_pct(last_close, structural_tp, direction)
    tp_zone = _tp_zone_from_target(last_close, direction, target_pct, atr_value)
    reward = abs(((tp_zone[0] + tp_zone[1]) / 2.0) - last_close)
    rr = reward / risk if risk > 0 else 0.0
    rr_min = _env_float("EMA_CROSS_RR_MIN", 0.0)
    if rr < rr_min:
        return None

    meta = {
        "trinity_ok": True,
        "trinity_cvd_sum": float(sum(nets_win)),
        "ema_cross_target_pct": target_pct,
        "ema20": last_ema,
        "oi_change_pct": oi_chg,
    }
    hint = (
        f"Trinity cross: 15m EMA{ema_period} пересечена, ΔOI {oi_chg:+.2f}%>0, "
        f"CVD {'↑' if direction == 'LONG' else '↓'} (net {sum(nets_win):.0f} за {window} баров)"
    )

    return {
        "strategy": "ema20_oi_cvd_cross",
        "symbol": symbol,
        "direction": direction,
        "trigger_price": last_close,
        "entry": last_close,
        "stop": stop,
        "tp_zone": tp_zone,
        "reason_ru": reason,
        "score": int(_env_float("EMA_CROSS_BASE_SCORE", 78.0)),
        "rr": rr,
        "atr_pct_1h": atr_pct_1h,
        "meta": meta,
        "orientation_hints": [hint],
    }
