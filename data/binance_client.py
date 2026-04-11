"""
Binance Futures USDT-M клиент для Liquidity Hunter.
Только публичные данные — ключи не нужны.
"""
import time
from typing import Any

import aiohttp


class BinanceClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._base = "https://fapi.binance.com"
        self._symbols_cache: list[str] = []
        self._symbols_cached_at: float = 0.0
        self._symbols_ttl = 300

    async def get_top_symbols(self, limit: int = 50) -> list[str]:
        now = time.time()
        if self._symbols_cache and now - self._symbols_cached_at <= self._symbols_ttl:
            return self._symbols_cache[:limit]

        try:
            async with self._session.get(f"{self._base}/fapi/v1/ticker/24hr", timeout=20) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return self._symbols_cache[:limit] if self._symbols_cache else []

        if not isinstance(data, list):
            return self._symbols_cache[:limit] if self._symbols_cache else []

        rows: list[tuple[str, float]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol", ""))
            if not sym.endswith("USDT"):
                continue
            try:
                qv = float(row.get("quoteVolume", 0.0))
            except (TypeError, ValueError):
                continue
            rows.append((sym, qv))

        rows.sort(key=lambda x: x[1], reverse=True)
        self._symbols_cache = [s for s, _ in rows]
        self._symbols_cached_at = now
        return self._symbols_cache[:limit]

    async def get_symbols_for_movement_scan(
        self,
        min_quote_volume_24h: float,
        max_symbols: int,
        sort_by: str = "abs_change_24h",
    ) -> list[str]:
        """
        Список USDT perpetual для сканера движения — не «топ по объёму».
        sort_by: abs_change_24h — сначала кто уже шевелится за сутки;
                 low_volume — мелкий объём 24h (мелкие пары вперёд).
        """
        try:
            async with self._session.get(f"{self._base}/fapi/v1/ticker/24hr", timeout=30) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        rows: list[tuple[str, float, float]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol", ""))
            if not sym.endswith("USDT"):
                continue
            try:
                qv = float(row.get("quoteVolume", 0.0))
                pcp = float(row.get("priceChangePercent", 0.0))
            except (TypeError, ValueError):
                continue
            if qv < min_quote_volume_24h:
                continue
            rows.append((sym, qv, abs(pcp)))

        if not rows:
            return []

        if sort_by == "low_volume":
            rows.sort(key=lambda x: x[1])
        else:
            rows.sort(key=lambda x: x[2], reverse=True)

        out = [s for s, _, _ in rows]
        if max_symbols > 0:
            out = out[:max_symbols]
        return out

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list[dict]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            async with self._session.get(f"{self._base}/fapi/v1/klines", params=params, timeout=20) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        result: list[dict] = []
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                continue
            item: dict[str, Any] = {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            if len(row) >= 11:
                try:
                    item["taker_buy_volume"] = float(row[9])
                except (TypeError, ValueError, IndexError):
                    item["taker_buy_volume"] = None
            else:
                item["taker_buy_volume"] = None
            result.append(item)
        return result

    async def get_open_interest(self, symbol: str) -> float | None:
        try:
            async with self._session.get(
                f"{self._base}/fapi/v1/openInterest",
                params={"symbol": symbol},
                timeout=10,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return float(data.get("openInterest", 0))
        except (TypeError, ValueError):
            return None

    async def get_open_interest_hist(self, symbol: str, period: str = "15m", limit: int = 5) -> list[dict]:
        params = {
            "symbol": symbol,
            "contractType": "PERPETUAL",
            "period": period,
            "limit": limit,
        }
        try:
            async with self._session.get(
                f"{self._base}/futures/data/openInterestHist",
                params=params,
                timeout=10,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        out: list[dict] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                out.append({
                    "timestamp": int(row.get("timestamp", 0)),
                    "open_interest": float(row.get("sumOpenInterest", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out

    async def get_taker_long_short(self, symbol: str, period: str = "15m", limit: int = 2) -> list[dict]:
        """Taker buy/sell volume - аналог Aggressive Longs/Shorts."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        try:
            async with self._session.get(
                f"{self._base}/futures/data/takerlongshortRatio",
                params=params,
                timeout=10,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        out: list[dict] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                out.append({
                    "timestamp": int(row.get("timestamp", 0)),
                    "buy_vol": float(row.get("buyVol", 0)),
                    "sell_vol": float(row.get("sellVol", 0)),
                    "buy_sell_ratio": float(row.get("buySellRatio", 1)),
                })
            except (TypeError, ValueError):
                continue
        return out
