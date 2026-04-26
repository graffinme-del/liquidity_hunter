"""
Жёсткое выравнивание направления с потоком: EMA20 (15m, последняя закрытая) + ΔOI + CVD (taker-buy vs volume).

LONG: close > EMA20, ΔOI > порога, CVD за окно «растёт» (сумма net во второй половине > первой и > 0).
SHORT: close < EMA20, ΔOI > порога, CVD за окно «падает» (новый OI открывается в сторону продаж).

Вкл: TRINITY_ORIENT_ENABLED=1 (по умолчанию в коде — 1).
"""

from __future__ import annotations

import os

from .ema_macd import closes_from_candles, ema_series


def _truthy(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


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


def _last_closed_index(candles: list[dict]) -> int:
    if len(candles) < 2:
        return -1
    return len(candles) - 2


def _taker_net_for_closed_bars(bars: list[dict]) -> list[float] | None:
    out: list[float] = []
    for c in bars:
        vol = float(c.get("volume", 0) or 0)
        tb = c.get("taker_buy_volume")
        if tb is None:
            return None
        tb_f = float(tb)
        out.append(2.0 * tb_f - vol)
    return out


def _cvd_direction_ok(nets: list[float], half: int, want_long: bool) -> bool:
    if half < 1 or len(nets) < 2:
        return False
    if len(nets) < half * 2:
        s = sum(nets[-half:])
        if want_long:
            return s > 0
        return s < 0
    prev_w = nets[-half * 2 : -half]
    recent = nets[-half:]
    sp, sr = sum(prev_w), sum(recent)
    if want_long:
        return sr > sp and sr > 0
    return sr < sp and sr < 0


def apply_trinity_orientation(candidate: dict, candles_15m: list[dict], oi_flow_ctx: dict | None) -> bool:
    """
    Мутирует candidate: meta, orientation_hints.
    Возвращает True — кандидата отбросить.
    """
    if not _truthy("TRINITY_ORIENT_ENABLED", "1"):
        return False
    if not isinstance(candidate, dict):
        return False

    direction = str(candidate.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        return False

    meta = candidate.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        candidate["meta"] = meta

    ctx = oi_flow_ctx or {}
    oi_chg = float(ctx.get("oi_change_pct", 0.0) or 0.0)
    min_oi = _env_float("TRINITY_MIN_OI_CHANGE_PCT", 0.0)
    ema_period = _env_int("TRINITY_EMA_PERIOD", 20)
    half = _env_int("TRINITY_CVD_HALF_BARS", 3)

    closed_15m = candles_15m[:-1] if len(candles_15m) > 1 else []
    min_closed = max(ema_period + 2, half * 2 + 1)
    if len(closed_15m) < min_closed:
        meta["trinity_reject"] = f"need_{min_closed}_closed_15m"
        return True

    idx = _last_closed_index(candles_15m)
    if idx < 0:
        meta["trinity_reject"] = "no_closed_15m"
        return True

    closes = closes_from_candles(candles_15m)
    ema_row = ema_series(closes, ema_period)
    if len(ema_row) != len(closes) or idx >= len(closes):
        meta["trinity_reject"] = "ema_warmup"
        return True

    close_i = closes[idx]
    ema_i = ema_row[idx]
    eps = max(close_i * 1e-9, 1e-12)

    nets = _taker_net_for_closed_bars(closed_15m)
    if nets is None:
        meta["trinity_reject"] = "no_taker_buy_on_klines"
        return True
    window = min(len(nets), half * 2)
    nets_win = nets[-window:]

    want_long = direction == "LONG"
    if want_long:
        if close_i <= ema_i + eps:
            meta["trinity_reject"] = "close_not_above_ema20"
            return True
        if oi_chg <= min_oi:
            meta["trinity_reject"] = f"oi_not_rising (ΔOI {oi_chg:+.2f}% ≤ {min_oi})"
            return True
        if not _cvd_direction_ok(nets_win, half, want_long=True):
            meta["trinity_reject"] = "cvd_not_bullish"
            return True
        tag = (
            f"Trinity: 15m закрытие выше EMA{ema_period}, ΔOI {oi_chg:+.2f}%>0, CVD↑ "
            f"(net {sum(nets_win):.0f} за {window} баров)"
        )
    else:
        if close_i >= ema_i - eps:
            meta["trinity_reject"] = "close_not_below_ema20"
            return True
        if oi_chg <= min_oi:
            meta["trinity_reject"] = f"oi_not_rising (ΔOI {oi_chg:+.2f}% ≤ {min_oi})"
            return True
        if not _cvd_direction_ok(nets_win, half, want_long=False):
            meta["trinity_reject"] = "cvd_not_bearish"
            return True
        tag = (
            f"Trinity: 15m закрытие ниже EMA{ema_period}, ΔOI {oi_chg:+.2f}%>0, CVD↓ "
            f"(net {sum(nets_win):.0f} за {window} баров)"
        )

    meta["trinity_ok"] = True
    meta["trinity_cvd_sum"] = float(sum(nets_win))
    hints = candidate.setdefault("orientation_hints", [])
    hints.append(tag)
    return False
