"""EMA/MACD по ряду close — без внешних зависимостей (отдельное имя модуля, не `indicators`)."""

from __future__ import annotations


def ema_series(values: list[float], period: int) -> list[float]:
    n = len(values)
    if n < period:
        return []
    k = 2.0 / (period + 1)
    out = [0.0] * n
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, n):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def macd_components(closes: list[float]) -> tuple[list[float], list[float], list[float]] | None:
    n = len(closes)
    if n < 35:
        return None
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    if len(e12) != n or len(e26) != n:
        return None
    macd_line = [e12[i] - e26[i] for i in range(n)]
    signal = ema_series(macd_line, 9)
    if len(signal) != n:
        return None
    hist = [macd_line[i] - signal[i] for i in range(n)]
    return macd_line, signal, hist


def closes_from_candles(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for c in candles:
        try:
            out.append(float(c.get("close", 0.0) or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out
