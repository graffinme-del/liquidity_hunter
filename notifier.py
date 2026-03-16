"""
Форматирование сигналов для Telegram — всё на русском, простые обоснования.
"""
from typing import Any


def format_signal(signal: dict) -> str:
    """
    Формат:
    🟢 LONG / 🔴 SHORT  [СИМВОЛ]
    Почему: ...
    Вход: когда цена достигнет X — покупай/продавай по рынку
    Стоп: X — тренд сломается, если цена уйдёт туда
    Цель: зона X–X — туда вероятно дойдёт
    RR: ... | ATR%: ...
    """
    direction = signal.get("direction", "")
    symbol = signal.get("symbol", "")
    reason = signal.get("reason_ru", "")
    trigger = signal.get("trigger_price", 0)
    stop = signal.get("stop", 0)
    tp_zone = signal.get("tp_zone", (0, 0))
    rr = signal.get("rr", 0)
    atr_pct = signal.get("atr_pct_1h") or 0

    emoji = "🟢" if direction == "LONG" else "🔴"
    action = "покупай" if direction == "LONG" else "продавай"

    tp_low = min(tp_zone) if isinstance(tp_zone, (list, tuple)) else tp_zone
    tp_high = max(tp_zone) if isinstance(tp_zone, (list, tuple)) else tp_zone

    lines = [
        f"{emoji} {direction}  {symbol}",
        "",
        f"Почему: {reason}",
        "",
        f"Вход: когда цена достигнет {_fmt(trigger)} — {action} по рынку",
        f"Стоп: {_fmt(stop)} — тренд сломается, если цена уйдёт туда",
        f"Цель: зона {_fmt(tp_low)}–{_fmt(tp_high)} — туда вероятно дойдёт",
        "",
        f"RR: {rr:.1f} | ATR%: {atr_pct:.2f}%" if atr_pct else f"RR: {rr:.1f}",
    ]
    return "\n".join(lines)


def _fmt(x: Any) -> str:
    try:
        v = float(x)
        if v >= 1000:
            return f"{v:,.0f}"
        if v >= 1:
            return f"{v:,.2f}"
        return f"{v:.4f}"
    except (TypeError, ValueError):
        return str(x)
