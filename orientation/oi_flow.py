"""
Ориентир OI + цена (как в pulse_pilot): flow по изменению цены и OI за окно 15m.

Ловушка лонга: цена↑ OI↓ — при OI_ORIENT_TRAP_SKIP_LONG=1 кандидат LONG отбрасывается.
"""

from __future__ import annotations

import os
from typing import Any


def _truthy(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default


def _flow_from_changes(price_change_pct: float, oi_change_pct: float) -> tuple[str, str, bool]:
    if abs(price_change_pct) < 0.05 and abs(oi_change_pct) < 0.1:
        return "UNKNOWN", "DEADZONE", True
    if price_change_pct >= 0 and oi_change_pct >= 0:
        return "PRICE_UP_OI_UP", "CLASSIFIED", False
    if price_change_pct >= 0 and oi_change_pct < 0:
        return "PRICE_UP_OI_DOWN", "CLASSIFIED", False
    if price_change_pct < 0 and oi_change_pct >= 0:
        return "PRICE_DOWN_OI_UP", "CLASSIFIED", False
    return "PRICE_DOWN_OI_DOWN", "CLASSIFIED", False


def build_oi_flow_context(candles_15m: list[dict], oi_hist: list[dict] | None) -> dict[str, Any]:
    """Собирает price_change_pct, oi_change_pct и flow по последним свечам/OI."""
    price_change_pct = 0.0
    if len(candles_15m) >= 5:
        start_close = float(candles_15m[-5].get("close", 0) or 0)
        end_close = float(candles_15m[-1].get("close", 0) or 0)
        if start_close > 0:
            price_change_pct = (end_close - start_close) / start_close * 100.0
    elif len(candles_15m) >= 2:
        start_close = float(candles_15m[-2].get("close", 0) or 0)
        end_close = float(candles_15m[-1].get("close", 0) or 0)
        if start_close > 0:
            price_change_pct = (end_close - start_close) / start_close * 100.0

    oi_change_pct = 0.0
    if oi_hist and len(oi_hist) >= 2:
        start_oi = float(oi_hist[0].get("open_interest", 0) or 0)
        end_oi = float(oi_hist[-1].get("open_interest", 0) or 0)
        if start_oi > 0:
            oi_change_pct = (end_oi - start_oi) / start_oi * 100.0

    flow, src, dead = _flow_from_changes(price_change_pct, oi_change_pct)
    return {
        "flow": flow,
        "price_change_pct": price_change_pct,
        "oi_change_pct": oi_change_pct,
        "oi_flow_source": src,
        "oi_flow_deadzone": dead,
    }


def _hint_ru(flow: str) -> str:
    return {
        "PRICE_UP_OI_UP": "OI: цена↑ OI↑ — накопление, ориентир лонг",
        "PRICE_UP_OI_DOWN": "OI: цена↑ OI↓ — ловушка для лонга",
        "PRICE_DOWN_OI_DOWN": "OI: цена↓ OI↓ — ориентир шорт",
        "PRICE_DOWN_OI_UP": "OI: цена↓ OI↑ — ориентир шорт",
        "UNKNOWN": "OI: зона шума (мало движения цены/OI)",
    }.get(flow, f"OI: {flow}")


def apply_oi_orientation(candidate: dict, oi_ctx: dict | None) -> bool:
    """
    Мутирует candidate: score, meta, orientation_hints.
    Возвращает True — кандидата выбросить (ловушка лонга).
    """
    if not _truthy("OI_ORIENT_ENABLED", "1"):
        return False
    if not isinstance(candidate, dict):
        return False

    flow = str((oi_ctx or {}).get("flow") or "UNKNOWN")
    pr = float((oi_ctx or {}).get("price_change_pct", 0.0) or 0.0)
    oi = float((oi_ctx or {}).get("oi_change_pct", 0.0) or 0.0)

    meta = candidate.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        candidate["meta"] = meta
    meta["oi_flow"] = flow
    meta["oi_price_change_pct"] = pr
    meta["oi_change_pct"] = oi

    hints = candidate.setdefault("orientation_hints", [])
    hints.append(_hint_ru(flow) + f" (Δцена {pr:+.2f}%  ΔOI {oi:+.2f}%)")

    direction = str(candidate.get("direction", "")).upper()
    adjust = _truthy("OI_ORIENT_SCORE_ADJUST", "1")
    bonus = _env_float("OI_ORIENT_BONUS_SCORE", 2.0)
    penalty = _env_float("OI_ORIENT_PENALTY_SCORE", 0.5)

    score = float(candidate.get("score", 0.0) or 0.0)

    if direction == "LONG" and flow == "PRICE_UP_OI_DOWN":
        if _truthy("OI_ORIENT_TRAP_SKIP_LONG", "0"):
            return True

    if adjust:
        delta = 0.0
        if direction == "LONG":
            if flow == "PRICE_UP_OI_UP":
                delta = bonus
            elif flow in ("PRICE_DOWN_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_UP_OI_DOWN"):
                delta = -penalty
        elif direction == "SHORT":
            if flow in ("PRICE_DOWN_OI_DOWN", "PRICE_DOWN_OI_UP"):
                delta = bonus
            elif flow == "PRICE_UP_OI_UP":
                delta = -penalty
        candidate["score"] = score + delta
        meta["oi_score_delta"] = delta

    return False
