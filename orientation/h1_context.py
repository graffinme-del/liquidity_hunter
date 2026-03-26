"""
Ориентир 1h: EMA20/EMA50 на последней свече + импульс цены за N баров (разворот).
По умолчанию блок против тренда выключен (H1_ORIENT_BLOCK_MISMATCH=0).
"""

from __future__ import annotations

import os

from .ema_macd import closes_from_candles, ema_series


def _truthy(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


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


def _safe_float(x: object, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def classify_h1_context(candles_1h: list[dict] | None) -> str:
    if not candles_1h:
        return "NEUTRAL"
    closes = closes_from_candles(candles_1h)
    if len(closes) < 55:
        return "NEUTRAL"
    ema20_row = ema_series(closes, 20)
    ema50_row = ema_series(closes, 50)
    if not ema20_row or len(ema20_row) != len(closes) or len(ema50_row) != len(closes):
        return "NEUTRAL"
    i = -1
    close = closes[i]
    e20 = ema20_row[i]
    e50 = ema50_row[i]
    if close <= 0 or e20 <= 0 or e50 <= 0:
        return "NEUTRAL"
    eps = max(close * 1e-9, 1e-12)
    if close > e20 + eps and e20 > e50 + eps:
        return "BULLISH"
    if close < e20 - eps and e20 < e50 - eps:
        return "BEARISH"
    return "NEUTRAL"


def _momentum_pct(candles_1h: list[dict] | None, lookback: int) -> float:
    if not candles_1h or len(candles_1h) < lookback + 1:
        return 0.0
    c_old = _safe_float(candles_1h[-1 - lookback].get("close"), 0.0)
    c_new = _safe_float(candles_1h[-1].get("close"), 0.0)
    if c_old <= 0:
        return 0.0
    return (c_new - c_old) / c_old * 100.0


def apply_h1_orientation(candidate: dict, candles_1h: list[dict] | None) -> bool:
    if not _truthy("H1_ORIENT_ENABLED", "1"):
        return False
    if not isinstance(candidate, dict):
        return False

    ctx = classify_h1_context(candles_1h)
    lookback = _env_int("H1_REVERSAL_LOOKBACK_BARS", 4)
    min_rev = _env_float("H1_REVERSAL_MIN_PCT", 0.15)
    mom = _momentum_pct(candles_1h, lookback)

    reversal_up = ctx == "BEARISH" and mom >= min_rev
    reversal_down = ctx == "BULLISH" and mom <= -min_rev

    meta = candidate.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        candidate["meta"] = meta
    meta["h1_context"] = ctx
    meta["h1_momentum_pct"] = mom
    meta["h1_reversal_up"] = reversal_up
    meta["h1_reversal_down"] = reversal_down

    base = {
        "BULLISH": "1h: бычий стек EMA — ориентир лонг",
        "BEARISH": "1h: медвежий стек EMA — ориентир шорт",
        "NEUTRAL": "1h: без явного тренда",
    }.get(ctx, ctx)
    if reversal_up:
        base += f" | импульс +{mom:.2f}% за {lookback}ч — возможный разворот вверх"
    elif reversal_down:
        base += f" | импульс {mom:.2f}% за {lookback}ч — возможный разворот вниз"

    hints = candidate.setdefault("orientation_hints", [])
    hints.append(base)

    direction = str(candidate.get("direction", "")).upper()
    adjust = _truthy("H1_ORIENT_SCORE_ADJUST", "1")
    bonus = _env_float("H1_ORIENT_BONUS_SCORE", 2.0)
    penalty = _env_float("H1_ORIENT_PENALTY_SCORE", 0.25)
    rev_bonus = _env_float("H1_REVERSAL_BONUS_SCORE", 1.5)

    score = float(candidate.get("score", 0.0) or 0.0)

    if _truthy("H1_ORIENT_BLOCK_MISMATCH", "0"):
        if direction == "LONG" and ctx == "BEARISH" and not reversal_up:
            return True
        if direction == "SHORT" and ctx == "BULLISH" and not reversal_down:
            return True

    if not adjust or ctx == "NEUTRAL":
        return False

    delta = 0.0
    if direction == "LONG" and ctx == "BULLISH":
        delta = bonus
    elif direction == "SHORT" and ctx == "BEARISH":
        delta = bonus
    elif direction == "LONG" and ctx == "BEARISH":
        delta = rev_bonus if reversal_up else -penalty
    elif direction == "SHORT" and ctx == "BULLISH":
        delta = rev_bonus if reversal_down else -penalty

    if delta != 0.0:
        candidate["score"] = score + delta
        meta["h1_score_delta"] = delta

    return False
