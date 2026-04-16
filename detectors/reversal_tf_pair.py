"""
Двухтаймфреймовый разворот: «перегрев» на старшем TF + отказ (новый экстремум + медвежья/бычья свеча) на младшем.
Закрытые свечи: последняя свеча с биржи отбрасывается (формирующаяся).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from structure import atr_pct

Direction = Literal["SHORT", "LONG"]


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def closed_only(raw: list[dict]) -> list[dict]:
    if len(raw) < 2:
        return []
    return raw[:-1]


def _range_pct(bar: dict) -> float:
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    if c <= 0:
        return 0.0
    return (h - l) / c * 100.0


def _upper_wick_ratio(bar: dict) -> float:
    o = _to_float(bar.get("open"))
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    rng = h - l
    if rng <= 1e-12:
        return 0.0
    body_top = max(o, c)
    return (h - body_top) / rng


def _lower_wick_ratio(bar: dict) -> float:
    o = _to_float(bar.get("open"))
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    rng = h - l
    if rng <= 1e-12:
        return 0.0
    body_bot = min(o, c)
    return (body_bot - l) / rng


def _close_pos_in_range(bar: dict) -> float:
    """0 = у low, 1 = у high."""
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    rng = h - l
    if rng <= 1e-12:
        return 0.5
    return (c - l) / rng


def context_uptrend_ok(
    candles: list[dict],
    *,
    atr_min_pct: float,
    roc_lookback: int,
    roc_min_pct: float,
) -> bool:
    """Старший TF: волатильность и вытянутый рост (для шорта на пике)."""
    if len(candles) < max(roc_lookback + 2, 20):
        return False
    ap = atr_pct(candles, 14)
    if ap is None or ap < atr_min_pct:
        return False
    if roc_lookback < 1 or len(candles) <= roc_lookback:
        return False
    c0 = _to_float(candles[-1 - roc_lookback].get("close"))
    c1 = _to_float(candles[-1].get("close"))
    if c0 <= 0:
        return False
    roc = (c1 - c0) / c0 * 100.0
    return roc >= roc_min_pct


def context_downtrend_ok(
    candles: list[dict],
    *,
    atr_min_pct: float,
    roc_lookback: int,
    roc_min_abs_pct: float,
) -> bool:
    """Старший TF: волатильность и вытянутое падение (для лонга от дна)."""
    if len(candles) < max(roc_lookback + 2, 20):
        return False
    ap = atr_pct(candles, 14)
    if ap is None or ap < atr_min_pct:
        return False
    if roc_lookback < 1 or len(candles) <= roc_lookback:
        return False
    c0 = _to_float(candles[-1 - roc_lookback].get("close"))
    c1 = _to_float(candles[-1].get("close"))
    if c0 <= 0:
        return False
    roc = (c1 - c0) / c0 * 100.0
    return roc <= -roc_min_abs_pct


def trigger_short_at_high(
    candles: list[dict],
    *,
    swing_lookback: int,
    min_range_pct: float,
    wick_ratio_min: float,
) -> bool:
    """Младший TF: новый максимум окна + медвежья свеча или длинная верхняя тень + закрытие слабее."""
    if len(candles) < swing_lookback + 2:
        return False
    last = candles[-1]
    prev = candles[-swing_lookback - 1 : -1]
    if not prev:
        return False
    hi_last = _to_float(last.get("high"))
    max_prev = max(_to_float(p.get("high")) for p in prev)
    if hi_last < max_prev * 1.0000001:
        return False
    if _range_pct(last) < min_range_pct:
        return False
    o = _to_float(last.get("open"))
    c = _to_float(last.get("close"))
    bearish_body = c < o
    wick_ok = _upper_wick_ratio(last) >= wick_ratio_min and _close_pos_in_range(last) <= 0.55
    return bearish_body or wick_ok


def trigger_long_at_low(
    candles: list[dict],
    *,
    swing_lookback: int,
    min_range_pct: float,
    wick_ratio_min: float,
) -> bool:
    """Младший TF: новый минимум окна + бычья свеча или длинная нижняя тень."""
    if len(candles) < swing_lookback + 2:
        return False
    last = candles[-1]
    prev = candles[-swing_lookback - 1 : -1]
    if not prev:
        return False
    lo_last = _to_float(last.get("low"))
    min_prev = min(_to_float(p.get("low")) for p in prev)
    if lo_last > min_prev * 0.9999999:
        return False
    if _range_pct(last) < min_range_pct:
        return False
    o = _to_float(last.get("open"))
    c = _to_float(last.get("close"))
    bullish_body = c > o
    wick_ok = _lower_wick_ratio(last) >= wick_ratio_min and _close_pos_in_range(last) >= 0.45
    return bullish_body or wick_ok


def evaluate_pair(
    candles_ctx: list[dict],
    candles_trig: list[dict],
    *,
    ctx_label: str,
    trig_label: str,
    ctx_atr_min: float,
    ctx_roc_lookback: int,
    ctx_roc_min_pct: float,
    trig_swing_lookback: int,
    trig_min_range_pct: float,
    trig_wick_min: float,
    want_short: bool,
    want_long: bool,
) -> list[dict]:
    """
    Возвращает 0–2 сигна (SHORT и/или LONG) для одной пары TF.
    """
    out: list[dict] = []
    cc = closed_only(candles_ctx)
    ct = closed_only(candles_trig)
    if len(cc) < 20 or len(ct) < trig_swing_lookback + 3:
        return out

    if want_short and context_uptrend_ok(
        cc,
        atr_min_pct=ctx_atr_min,
        roc_lookback=ctx_roc_lookback,
        roc_min_pct=ctx_roc_min_pct,
    ):
        if trigger_short_at_high(
            ct,
            swing_lookback=trig_swing_lookback,
            min_range_pct=trig_min_range_pct,
            wick_ratio_min=trig_wick_min,
        ):
            last = ct[-1]
            out.append(
                {
                    "direction": "SHORT",
                    "ctx_tf": ctx_label,
                    "trig_tf": trig_label,
                    "ctx_atr_pct": atr_pct(cc, 14),
                    "trig_high": _to_float(last.get("high")),
                    "trig_close": _to_float(last.get("close")),
                    "trig_range_pct": _range_pct(last),
                }
            )

    if want_long and context_downtrend_ok(
        cc,
        atr_min_pct=ctx_atr_min,
        roc_lookback=ctx_roc_lookback,
        roc_min_abs_pct=ctx_roc_min_pct,
    ):
        if trigger_long_at_low(
            ct,
            swing_lookback=trig_swing_lookback,
            min_range_pct=trig_min_range_pct,
            wick_ratio_min=trig_wick_min,
        ):
            last = ct[-1]
            out.append(
                {
                    "direction": "LONG",
                    "ctx_tf": ctx_label,
                    "trig_tf": trig_label,
                    "ctx_atr_pct": atr_pct(cc, 14),
                    "trig_low": _to_float(last.get("low")),
                    "trig_close": _to_float(last.get("close")),
                    "trig_range_pct": _range_pct(last),
                }
            )

    return out
