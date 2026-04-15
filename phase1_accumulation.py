"""
Фаза 1 — накопление перед импульсом (Liquidity Hunter): скан 5m, контроль 15m, без 1h.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

import config
from data.binance_client import BinanceClient
from telegram_notify import format_phase1_accumulation_message, send_telegram

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}


def _cfg_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(config, name, default))


def _cfg_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        return float(getattr(config, name, default))
    except (TypeError, ValueError):
        return default


def _cfg_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except ValueError:
            pass
    try:
        return int(getattr(config, name, default))
    except (TypeError, ValueError):
        return default


def _cfg_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    return str(getattr(config, name, default))


def _closed_only(candles: list[dict]) -> list[dict]:
    now_ms = int(time.time() * 1000)
    out: list[dict] = []
    for c in candles:
        ct = int(c.get("close_time") or c.get("open_time") or 0)
        if ct < now_ms:
            out.append(c)
    return out


def _taker_delta(bar: dict) -> float:
    try:
        v = float(bar.get("volume", 0))
        tb = bar.get("taker_buy_volume")
        if tb is None:
            return 0.0
        tb = float(tb)
    except (TypeError, ValueError):
        return 0.0
    return 2.0 * tb - v


@dataclass
class Phase1Snapshot:
    symbol: str
    oi_growth: float
    range_pct: float
    last_impulse: float
    vol_ratio: float
    recent_width_pct: float
    cvd_rel: float
    range_high: float
    range_low: float
    long_entry: float
    short_entry: float
    bias_points: int
    long_pct: int
    short_pct: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "oi_growth_pct": self.oi_growth * 100.0,
            "range_pct": self.range_pct * 100.0,
            "last_impulse_pct": self.last_impulse * 100.0,
            "vol_ratio": self.vol_ratio,
            "recent_width_pct": self.recent_width_pct * 100.0,
            "cvd_rel": self.cvd_rel,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "long_entry": self.long_entry,
            "short_entry": self.short_entry,
            "bias_points": self.bias_points,
            "long_pct": self.long_pct,
            "short_pct": self.short_pct,
        }


async def evaluate_phase1_symbol(client: BinanceClient, symbol: str) -> Phase1Snapshot | None:
    oi_min = _cfg_float("PHASE1_OI_GROWTH_MIN", 0.015)
    range_max = _cfg_float("PHASE1_RANGE_PCT_MAX", 0.015)
    impulse_max = _cfg_float("PHASE1_LAST_IMPULSE_MAX", 0.007)
    vol_min = _cfg_float("PHASE1_VOL_RATIO_MIN", 1.1)
    buffer = _cfg_float("PHASE1_TRAP_BUFFER", 0.002)
    range_bars = max(4, _cfg_int("PHASE1_RANGE_BARS", 12))
    hour_bars = max(4, _cfg_int("PHASE1_HOUR_BARS", 12))
    pump_skip = _cfg_float("PHASE1_SKIP_RECENT_MOVE_PCT", 0.05)
    cvd_rel_max = _cfg_float("PHASE1_CVD_REL_MAX", 0.35)
    oi_period = _cfg_str("PHASE1_OI_PERIOD", "5m") or "5m"
    oi_limit = max(6, _cfg_int("PHASE1_OI_LOOKBACK", 13))
    require_15m = _cfg_bool("PHASE1_REQUIRE_15M_QUIET", True)
    m15_body_max = _cfg_float("PHASE1_15M_BODY_MAX", 0.008)

    raw_5m = await client.get_klines(symbol, "5m", 120)
    closed = _closed_only(raw_5m)
    need = max(45, range_bars + hour_bars + 6)
    if len(closed) < need:
        return None

    oi_ser = await client.get_open_interest_hist(symbol, period=oi_period, limit=oi_limit)
    if len(oi_ser) < 4:
        return None
    o_first = float(oi_ser[0].get("open_interest", 0) or 0.0)
    o_last = float(oi_ser[-1].get("open_interest", 0) or 0.0)
    if o_first <= 0:
        return None
    oi_growth = (o_last - o_first) / o_first
    if oi_growth <= oi_min:
        return None

    seg = closed[-range_bars:]
    highs = [float(r["high"]) for r in seg]
    lows = [float(r["low"]) for r in seg]
    close_ref = float(closed[-1]["close"])
    range_h = max(highs)
    range_l = min(lows)
    if close_ref <= 0:
        return None
    range_pct = (range_h - range_l) / close_ref
    if range_pct >= range_max:
        return None

    o_last_c = float(closed[-1]["open"])
    c_last = float(closed[-1]["close"])
    last_impulse = abs(c_last - o_last_c) / o_last_c if o_last_c else 1.0
    if last_impulse >= impulse_max:
        return None

    vols_pre = [float(r["volume"]) for r in closed[-21:-1]]
    if len(vols_pre) < 20:
        return None
    sma_v = sum(vols_pre) / 20.0
    vol_last = float(closed[-1]["volume"])
    vol_ratio = vol_last / sma_v if sma_v > 0 else 0.0
    if vol_ratio <= vol_min:
        return None

    hour_seg = closed[-hour_bars:]
    hh = max(float(r["high"]) for r in hour_seg)
    ll = min(float(r["low"]) for r in hour_seg)
    recent_width = (hh - ll) / close_ref if close_ref else 1.0
    if recent_width > pump_skip:
        return None

    cseg = closed[-6:]
    deltas = [_taker_delta(r) for r in cseg]
    vol_sum_6 = sum(float(r["volume"]) for r in cseg)
    cvd_sum = sum(deltas)
    cvd_rel = abs(cvd_sum) / vol_sum_6 if vol_sum_6 > 0 else 0.0
    if cvd_rel > cvd_rel_max:
        return None

    if require_15m:
        raw_15 = await client.get_klines(symbol, "15m", 12)
        c15 = _closed_only(raw_15)
        if not c15:
            return None
        lr = c15[-1]
        o15, c15f = float(lr["open"]), float(lr["close"])
        if o15 <= 0:
            return None
        body15 = abs(c15f - o15) / o15
        if body15 >= m15_body_max:
            return None

    bias = 0
    cvd_growth = sum(deltas[-3:]) - sum(deltas[:3]) if len(deltas) >= 6 else 0.0
    if cvd_growth > 0:
        bias += 1
    span = range_h - range_l
    if span > 0 and (c_last - range_l) / span > 0.6:
        bias += 1

    oi_acc = False
    if len(oi_ser) >= 7:
        ovals = [float(x.get("open_interest", 0) or 0) for x in oi_ser]
        if ovals[-4] > 0 and ovals[-7] > 0:
            g1 = (ovals[-1] - ovals[-4]) / ovals[-4]
            g0 = (ovals[-4] - ovals[-7]) / ovals[-7]
            oi_acc = g1 > g0
    if oi_acc:
        bias += 1

    long_pct = min(92, max(8, 50 + bias * 10))
    short_pct = 100 - long_pct

    long_ent = range_h * (1.0 + buffer)
    short_ent = range_l * (1.0 - buffer)

    return Phase1Snapshot(
        symbol=symbol,
        oi_growth=oi_growth,
        range_pct=range_pct,
        last_impulse=last_impulse,
        vol_ratio=vol_ratio,
        recent_width_pct=recent_width,
        cvd_rel=cvd_rel,
        range_high=range_h,
        range_low=range_l,
        long_entry=long_ent,
        short_entry=short_ent,
        bias_points=bias,
        long_pct=long_pct,
        short_pct=short_pct,
    )


async def run_phase1_loop() -> None:
    """Бесконечный цикл PRE-PUMP / Фаза 1."""
    if not _cfg_bool("PHASE1_ACCUM_ENABLED", True):
        log.info("[PHASE1] disabled (PHASE1_ACCUM_ENABLED=0)")
        return

    interval = max(60, _cfg_int("PHASE1_INTERVAL_SEC", 180))
    max_sym = max(10, _cfg_int("PHASE1_MAX_SYMBOLS", 80))
    dedup = max(300, _cfg_int("PHASE1_DEDUP_SEC", 7200))
    concurrent = max(1, min(20, _cfg_int("PHASE1_CONCURRENCY", 8)))
    delay_start = max(0, _cfg_int("PHASE1_START_DELAY_SEC", 120))

    if delay_start > 0:
        await asyncio.sleep(delay_start)

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        while True:
            try:
                symbols = await client.get_top_symbols(limit=max_sym)
                random.shuffle(symbols)
                now = time.time()
                for k, ts in list(_last_sent.items()):
                    if now - ts > dedup * 4:
                        del _last_sent[k]

                sem = asyncio.Semaphore(concurrent)

                async def _one(sym: str) -> None:
                    async with sem:
                        t = time.time()
                        la = _last_sent.get(sym)
                        if la is not None and t - la < dedup:
                            return
                        snap = await evaluate_phase1_symbol(client, sym)
                        if snap is None:
                            return
                        _last_sent[sym] = time.time()
                        ok = await send_telegram(
                            format_phase1_accumulation_message(snap.as_payload()),
                            delete_after_sec=None,
                        )
                        if ok:
                            log.info(
                                "[PHASE1] sent %s OI=%.2f%% range=%.2f%% vol_x=%.2f",
                                sym,
                                snap.oi_growth * 100.0,
                                snap.range_pct * 100.0,
                                snap.vol_ratio,
                            )

                await asyncio.gather(*(_one(s) for s in symbols))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[PHASE1] loop error")

            await asyncio.sleep(interval)
