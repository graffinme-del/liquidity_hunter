"""
Фаза 1 — PRE-PUMP (Liquidity Hunter): 5m скан, подтверждение 15m.

Логика v2: жёсткие гейты (сжатие, поджим к хаю, ускорение CVD) + балльная оценка 0–100
(порог PHASE1_MIN_SCORE, по умолчанию 70). Без прогноза «гарантированного пампа» —
отбор сетапов по разобранным закономерностям.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
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


def _ema_series(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    out: list[float | None] = [None] * len(values)
    ema_val = sum(values[:period]) / period
    out[period - 1] = ema_val
    for i in range(period, len(values)):
        ema_val = values[i] * k + ema_val * (1 - k)
        out[i] = ema_val
    return out


def _macd_hist_last(closes: list[float]) -> float | None:
    """Гистограмма MACD на последнем закрытии (12/26/9)."""
    if len(closes) < 35:
        return None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    macd_vals: list[float] = []
    for i in range(26, len(closes)):
        a, b = e12[i], e26[i]
        if a is None or b is None:
            return None
        macd_vals.append(a - b)
    if len(macd_vals) < 9:
        return None
    sig = _ema_series(macd_vals, 9)
    if sig[-1] is None:
        return None
    return macd_vals[-1] - sig[-1]


def _squeeze_high_ok(
    last_bars: list[dict],
    range_h: float,
    range_l: float,
    top_frac: float,
) -> bool:
    """Последние свечи «поджимают» верх диапазона: high у верхней границы коридора."""
    span = range_h - range_l
    if span <= 0:
        return False
    floor_h = range_h - top_frac * span
    for b in last_bars:
        if float(b["high"]) < floor_h:
            return False
    return True


def _compute_score(
    *,
    oi_growth: float,
    range_pct: float,
    range_max: float,
    vol_ratio: float,
    cvd_last3: float,
    cvd_prev3: float,
    oi_acc: bool,
    squeeze_ok: bool,
    macd_hist: float | None,
    use_macd: bool,
) -> tuple[int, dict[str, float]]:
    """Возвращает (score 0..100, разбивка по компонентам)."""
    parts: dict[str, float] = {}

    # OI: 0.02 → 0, 0.04+ → 25 (линейно)
    oi_lo = _cfg_float("PHASE1_SCORE_OI_LO", 0.02)
    oi_hi = _cfg_float("PHASE1_SCORE_OI_HI", 0.045)
    if oi_growth <= oi_lo:
        oi_pts = 0.0
    elif oi_growth >= oi_hi:
        oi_pts = 25.0
    else:
        oi_pts = 25.0 * (oi_growth - oi_lo) / (oi_hi - oi_lo)
    parts["oi"] = round(oi_pts, 1)

    # Vol: 1.5 → 0, 2.5+ → 25
    v_lo = _cfg_float("PHASE1_SCORE_VOL_LO", 1.5)
    v_hi = _cfg_float("PHASE1_SCORE_VOL_HI", 2.5)
    if vol_ratio <= v_lo:
        v_pts = 0.0
    elif vol_ratio >= v_hi:
        v_pts = 25.0
    else:
        v_pts = 25.0 * (vol_ratio - v_lo) / (v_hi - v_lo)
    parts["volume"] = round(v_pts, 1)

    # Узость диапазона: чем меньше range_pct относительно max — тем лучше
    if range_max <= 0:
        r_pts = 0.0
    else:
        r_pts = 15.0 * max(0.0, (range_max - range_pct) / range_max)
    parts["range"] = round(r_pts, 1)

    # CVD acceleration
    denom = abs(cvd_prev3) + 1e-9
    accel = (cvd_last3 - cvd_prev3) / denom
    accel = max(0.0, min(3.0, accel))
    c_pts = 15.0 * (accel / 3.0)
    parts["cvd_accel"] = round(c_pts, 1)

    # Поджим
    sq_pts = 10.0 if squeeze_ok else 0.0
    parts["squeeze"] = sq_pts

    # OI ускорение
    oa_pts = 10.0 if oi_acc else 0.0
    parts["oi_accel"] = oa_pts

    mac_pts = 0.0
    if use_macd and macd_hist is not None and macd_hist > 0:
        mac_pts = 10.0
    parts["macd"] = mac_pts

    raw = oi_pts + v_pts + r_pts + c_pts + sq_pts + oa_pts + mac_pts
    total = int(round(min(100.0, raw)))
    return total, parts


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
    entry_score: int = 0
    score_parts: dict[str, float] = field(default_factory=dict)

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
            "entry_score": self.entry_score,
            "score_parts": self.score_parts,
        }


async def evaluate_phase1_symbol(client: BinanceClient, symbol: str) -> Phase1Snapshot | None:
    range_max = _cfg_float("PHASE1_RANGE_PCT_MAX", 0.01)
    impulse_max = _cfg_float("PHASE1_LAST_IMPULSE_MAX", 0.007)
    vol_hard_min = _cfg_float("PHASE1_VOL_HARD_MIN", 1.05)
    buffer = _cfg_float("PHASE1_TRAP_BUFFER", 0.002)
    range_bars = max(4, _cfg_int("PHASE1_RANGE_BARS", 12))
    hour_bars = max(4, _cfg_int("PHASE1_HOUR_BARS", 12))
    pump_skip = _cfg_float("PHASE1_SKIP_RECENT_MOVE_PCT", 0.05)
    oi_period = _cfg_str("PHASE1_OI_PERIOD", "5m") or "5m"
    oi_limit = max(6, _cfg_int("PHASE1_OI_LOOKBACK", 13))
    require_15m = _cfg_bool("PHASE1_REQUIRE_15M_QUIET", True)
    m15_body_max = _cfg_float("PHASE1_15M_BODY_MAX", 0.008)

    squeeze_bars = max(2, min(5, _cfg_int("PHASE1_SQUEEZE_BARS", 3)))
    squeeze_top = _cfg_float("PHASE1_SQUEEZE_TOP_FRAC", 0.15)
    min_score = _cfg_int("PHASE1_MIN_SCORE", 70)
    use_macd = _cfg_bool("PHASE1_USE_MACD_SCORE", True)

    raw_5m = await client.get_klines(symbol, "5m", 150)
    closed = _closed_only(raw_5m)
    need = max(50, range_bars + hour_bars + 8)
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
    if vol_ratio < vol_hard_min:
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

    cvd_last3 = sum(deltas[-3:])
    cvd_prev3 = sum(deltas[-6:-3])
    if cvd_last3 <= cvd_prev3 or cvd_last3 <= 0:
        return None

    last_for_squeeze = closed[-squeeze_bars:]
    squeeze_ok = _squeeze_high_ok(last_for_squeeze, range_h, range_l, squeeze_top)
    if not squeeze_ok:
        return None

    if require_15m:
        raw_15 = await client.get_klines(symbol, "15m", 16)
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

    closes_5m = [float(c["close"]) for c in closed]
    macd_hist = _macd_hist_last(closes_5m) if use_macd else None

    oi_acc = False
    if len(oi_ser) >= 7:
        ovals = [float(x.get("open_interest", 0) or 0) for x in oi_ser]
        if ovals[-4] > 0 and ovals[-7] > 0:
            g1 = (ovals[-1] - ovals[-4]) / ovals[-4]
            g0 = (ovals[-4] - ovals[-7]) / ovals[-7]
            oi_acc = g1 > g0

    score, parts = _compute_score(
        oi_growth=oi_growth,
        range_pct=range_pct,
        range_max=range_max,
        vol_ratio=vol_ratio,
        cvd_last3=cvd_last3,
        cvd_prev3=cvd_prev3,
        oi_acc=oi_acc,
        squeeze_ok=True,
        macd_hist=macd_hist,
        use_macd=use_macd,
    )
    if score < min_score:
        return None

    bias = 0
    if cvd_last3 > cvd_prev3:
        bias += 1
    span = range_h - range_l
    if span > 0 and (c_last - range_l) / span > 0.55:
        bias += 1
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
        entry_score=score,
        score_parts=parts,
    )


async def run_phase1_loop() -> None:
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
                                "[PHASE1] sent %s score=%s OI=%.2f%% range=%.2f%% vol_x=%.2f",
                                sym,
                                snap.entry_score,
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
