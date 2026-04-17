"""
Старт пампа: тихий фон, затем первая зелёная свеча с всплеском объёма.
По умолчанию TF=5m (раньше, чем 15m: сигнал после закрытия свечи ~5 мин, не ~15).
Опционально — текущая незакрытая свеча (ещё раньше, больше шума).
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import time
from typing import Any, Optional

import aiohttp

import config
from data.binance_client import BinanceClient
from impulse_scanner import _taker_buy_sell_ratio
from structure import ema
from telegram_notify import ephemeral_delete_seconds, send_telegram

log = logging.getLogger(__name__)

_last_early_pump_at: dict[str, float] = {}


def _tf_minutes(tf: str) -> int:
    m = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
    }.get(tf.strip().lower(), 5)
    return max(1, m)


def _bars_for_minutes(minutes: int, tf: str) -> int:
    return max(8, int(minutes / _tf_minutes(tf)))


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return (s[m - 1] + s[m]) / 2 if len(s) % 2 == 0 else s[m]


def _est_quote_usdt(bar: dict) -> float:
    """Оценка notional бара: close × volume (как у Binance USDT-M)."""
    c = _to_float(bar.get("close"))
    v = _to_float(bar.get("volume"))
    if c <= 0 or v < 0:
        return 0.0
    return c * v


def _early_pump_quality_score(h: dict) -> float:
    """
    0–100: умеренный всплеск объёма, taker, тело в «середине», OI, близость к EMA.
    Не заменяет гейты — для ранжирования и опц. порога EARLY_PUMP_MIN_QUALITY_SCORE.
    """
    vr = float(h.get("vol_ratio") or 0)
    if 2.0 <= vr <= 10.0:
        vol_pts = 32.0
    elif 2.0 <= vr <= 16.0:
        vol_pts = 24.0
    elif vr <= 24.0:
        vol_pts = 14.0
    else:
        vol_pts = 0.0

    tr = h.get("taker_ratio")
    if tr is None:
        t_pts = 10.0
    else:
        tr = float(tr)
        if tr >= 1.25:
            t_pts = 26.0
        elif tr >= 1.12:
            t_pts = 18.0
        elif tr >= 1.04:
            t_pts = 11.0
        else:
            t_pts = 3.0

    pct = float(h.get("pct") or 0)
    if 0.55 <= pct <= 1.8:
        b_pts = 16.0
    elif 0.35 <= pct <= 2.5:
        b_pts = 11.0
    else:
        b_pts = 4.0

    oi = h.get("oi_change_pct")
    if oi is None:
        oi_pts = 8.0
    else:
        oi = float(oi)
        if oi >= 1.5:
            oi_pts = 16.0
        elif oi >= 0.4:
            oi_pts = 11.0
        elif oi >= 0.12:
            oi_pts = 5.0
        else:
            oi_pts = 0.0

    eap = h.get("ema_above_pct")
    if eap is None:
        ema_pts = 8.0
    else:
        eap = float(eap)
        if 0.0 <= eap <= 1.8:
            ema_pts = 10.0
        elif eap <= 3.5:
            ema_pts = 6.0
        else:
            ema_pts = 0.0

    return min(100.0, vol_pts + t_pts + b_pts + oi_pts + ema_pts)


def _bar_range_pct(bar: dict) -> float:
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    c = _to_float(bar.get("close"))
    if c <= 0:
        return 0.0
    return (h - l) / c * 100.0


def _body_pct_long(bar: dict) -> float | None:
    o = _to_float(bar.get("open"))
    c = _to_float(bar.get("close"))
    if o <= 0 or c <= o:
        return None
    return (c - o) / o * 100.0


def _elapsed_bar_fraction(bar: dict, tf_min: int) -> float:
    """Доля прошедшего времени внутри текущей свечи (для незакрытой)."""
    ot = int(bar.get("open_time") or 0)
    if ot <= 0:
        return 0.5
    elapsed = time.time() - (ot / 1000.0)
    total = float(tf_min * 60)
    return min(0.98, max(0.06, elapsed / total))


def _quiet_and_spike(
    candles: list[dict],
    *,
    tf: str,
    forming: bool,
) -> Optional[dict]:
    """
    forming=False: сигнал — последняя закрытая свеча (candles[:-1]).
    forming=True: сигнал — последняя (текущая) свеча.
    Если EARLY_PUMP_REQUIRE_QUIET — до свечи должно быть «узкое» окно по диапазону баров.
    """
    tfm = _tf_minutes(tf)
    vol_med_min = int(getattr(config, "EARLY_PUMP_VOL_MEDIAN_MINUTES", 240))
    vol_lb = _bars_for_minutes(vol_med_min, tf)
    require_quiet = getattr(config, "EARLY_PUMP_REQUIRE_QUIET", False)

    if require_quiet:
        quiet_min = int(getattr(config, "EARLY_PUMP_QUIET_MINUTES", 240))
        quiet_n = _bars_for_minutes(quiet_min, tf)
        if forming:
            if len(candles) < quiet_n + vol_lb + 4:
                return None
            quiet_bars = candles[-(quiet_n + 1) : -1]
            last = candles[-1]
            hist_slice = candles[-(vol_lb + 1) : -1]
        else:
            closed = candles[:-1] if len(candles) > 1 else candles
            if len(closed) < quiet_n + vol_lb + 3:
                return None
            quiet_bars = closed[-(quiet_n + 1) : -1]
            last = closed[-1]
            hist_slice = closed[-(vol_lb + 1) : -1]
        if len(quiet_bars) < quiet_n:
            return None
        range_med = _median([_bar_range_pct(b) for b in quiet_bars])
        max_quiet = float(getattr(config, "EARLY_PUMP_QUIET_RANGE_MAX", 0.85))
        if range_med > max_quiet:
            return None
    else:
        if forming:
            if len(candles) < vol_lb + 4:
                return None
            last = candles[-1]
            hist_slice = candles[-(vol_lb + 1) : -1]
        else:
            closed = candles[:-1] if len(candles) > 1 else candles
            if len(closed) < vol_lb + 2:
                return None
            last = closed[-1]
            hist_slice = closed[-(vol_lb + 1) : -1]
        range_med = _median([_bar_range_pct(b) for b in hist_slice]) if hist_slice else 0.0

    body = _body_pct_long(last)
    if body is None:
        return None
    bmin = float(getattr(config, "EARLY_PUMP_BODY_MIN_PCT", 0.35))
    bmax = float(getattr(config, "EARLY_PUMP_BODY_MAX_PCT", 3.0))
    if body < bmin or body > bmax:
        return None

    vol_hist = [_to_float(b.get("volume")) for b in hist_slice]
    med_v = _median(vol_hist)
    if med_v <= 1e-12:
        return None
    v_last = _to_float(last.get("volume"))
    spike = float(getattr(config, "EARLY_PUMP_VOL_SPIKE_MULT", 2.0))
    vol_ratio = v_last / med_v
    need = spike
    if forming:
        frac = _elapsed_bar_fraction(last, tfm)
        relax = float(getattr(config, "EARLY_PUMP_FORMING_VOL_RELAX", 0.55))
        need = spike * max(relax, frac)
    if vol_ratio < need:
        return None

    med_qs = [_est_quote_usdt(b) for b in hist_slice]
    median_quote = _median(med_qs) if med_qs else 0.0
    signal_quote = _est_quote_usdt(last)
    mq_min = float(getattr(config, "EARLY_PUMP_MEDIAN_QUOTE_VOL_MIN", 0.0) or 0.0)
    sq_min = float(getattr(config, "EARLY_PUMP_SIGNAL_BAR_QUOTE_VOL_MIN", 0.0) or 0.0)
    vr_max = float(getattr(config, "EARLY_PUMP_VOL_RATIO_MAX", 0.0) or 0.0)
    if mq_min > 0 and median_quote < mq_min:
        return None
    if sq_min > 0 and signal_quote < sq_min:
        return None
    if vr_max > 0 and vol_ratio > vr_max:
        return None

    close = _to_float(last.get("close"))
    if close < getattr(config, "MIN_PRICE", 0.01):
        return None

    out = {
        "pct": round(body, 2),
        "vol_ratio": round(vol_ratio, 2),
        "quiet_range_med": round(range_med, 3),
        "median_quote_est": round(median_quote, 2),
        "signal_quote_est": round(signal_quote, 2),
        "close": round(close, 8),
        "tf": tf,
        "forming": forming,
        "require_quiet": require_quiet,
    }
    return out


def _ema_detach_above_pct(candles: list[dict], period: int) -> tuple[float | None, float | None]:
    """На последней свече: (close−EMA)/EMA×100; None если мало данных."""
    closes = [_to_float(c.get("close")) for c in candles]
    if len(closes) < period + 1:
        return None, None
    ev = ema(closes, period)
    if ev is None or ev <= 0:
        return None, None
    last = closes[-1]
    pct = (last - ev) / ev * 100.0
    return pct, ev


def _cvd_proxy_sum(bars: list[dict]) -> float | None:
    """
    Прокси CVD: сумма по барам (taker buy vol − taker sell vol), sell ≈ volume − taker_buy.
    Нужны поля taker_buy_volume из klines (Binance row[9]).
    """
    if not bars:
        return None
    s = 0.0
    for b in bars:
        vol = _to_float(b.get("volume"))
        tb = b.get("taker_buy_volume")
        if tb is None or vol <= 0:
            return None
        tb = float(tb)
        taker_sell = max(0.0, vol - tb)
        s += tb - taker_sell
    return s


async def _oi_window_change_pct(client: BinanceClient, symbol: str, period: str) -> float | None:
    """Изменение OI от первой до последней точки в окне hist (period совпадает с TF)."""
    limit = int(getattr(config, "EARLY_PUMP_OI_HIST_LIMIT", 8))
    rows = await client.get_open_interest_hist(symbol, period=period, limit=limit)
    if len(rows) < 2:
        return None
    rows = sorted(rows, key=lambda r: int(r.get("timestamp", 0)))
    o0 = _to_float(rows[0].get("open_interest"))
    o1 = _to_float(rows[-1].get("open_interest"))
    if o0 <= 1e-12:
        return None
    return (o1 - o0) / o0 * 100.0


def build_early_pump_alert_text(hits: list[dict]) -> str:
    tf = getattr(config, "EARLY_PUMP_TIMEFRAME", "5m")
    bmin = float(getattr(config, "EARLY_PUMP_BODY_MIN_PCT", 0.35))
    bmax = float(getattr(config, "EARLY_PUMP_BODY_MAX_PCT", 3.0))
    sp = float(getattr(config, "EARLY_PUMP_VOL_SPIKE_MULT", 2.0))
    qr = float(getattr(config, "EARLY_PUMP_QUIET_RANGE_MAX", 0.85))
    qm = int(getattr(config, "EARLY_PUMP_QUIET_MINUTES", 240))
    req_q = getattr(config, "EARLY_PUMP_REQUIRE_QUIET", False)
    if not hits:
        if req_q:
            sub = f"тишина ~{qm} мин ≤{qr}% + "
        else:
            sub = ""
        return (
            f"<b>Старт пампа ({tf})</b>\n"
            f"<i>Нет пар: {sub}тело {bmin:.1f}–{bmax:.1f}% + vol ≥{sp:.1f}× к медиане.</i>"
        )
    if req_q:
        head2 = (
            f"Тихий фон (~{qm} мин, медиана диапазона ≤{qr}%), затем зелёная свеча "
            f"{bmin:.1f}–{bmax:.1f}% и всплеск объёма к медиане."
        )
    else:
        head2 = (
            f"Без фильтра «тишины»: зелёная свеча {bmin:.1f}–{bmax:.1f}% "
            f"и объём ≥{sp:.1f}× к медиане объёма до неё."
        )
    mq = float(getattr(config, "EARLY_PUMP_MEDIAN_QUOTE_VOL_MIN", 0.0) or 0.0)
    sq = float(getattr(config, "EARLY_PUMP_SIGNAL_BAR_QUOTE_VOL_MIN", 0.0) or 0.0)
    vr_max = float(getattr(config, "EARLY_PUMP_VOL_RATIO_MAX", 0.0) or 0.0)
    min_q = float(getattr(config, "EARLY_PUMP_MIN_QUALITY_SCORE", 0.0) or 0.0)
    filt_parts: list[str] = []
    if mq > 0:
        filt_parts.append(f"мед.объём(≈USDT)≥{mq:.0f}")
    if sq > 0:
        filt_parts.append(f"бар≥{sq:.0f} USDT")
    if vr_max > 0:
        filt_parts.append(f"vol/мед≤{vr_max:.0f}×")
    if min_q > 0:
        filt_parts.append(f"кач.≥{min_q:.0f}/100")
    lines = [
        f"<b>Старт пампа ({tf})</b>",
        head2,
    ]
    if filt_parts:
        lines.append(f"<i>Фильтры: {', '.join(filt_parts)}</i>")
    lines.append("")
    for h in hits[:25]:
        sym = html.escape(str(h.get("symbol", "?")))
        pct = h.get("pct", 0)
        vr = h.get("vol_ratio")
        qmed = h.get("quiet_range_med")
        tr = h.get("taker_ratio")
        extra = []
        qs = h.get("quality_score")
        if qs is not None:
            extra.append(f"кач.{qs:.0f}/100")
        extra.append(f"vol×{vr}")
        if h.get("require_quiet", req_q):
            extra.append(f"тишина {qmed}%")
        else:
            extra.append(f"мед.диап.фона {qmed}%")
        if h.get("forming"):
            extra.append("текущая свеча")
        if tr is not None:
            extra.append(f"taker {tr:.2f}")
        vb = h.get("vs_btc")
        if vb is not None:
            extra.append(f"vs BTC +{vb}%")
        oi = h.get("oi_change_pct")
        if oi is not None:
            extra.append(f"OI {oi:+.2f}%")
        eap = h.get("ema_above_pct")
        if eap is not None:
            extra.append(f"над EMA {eap:.2f}%")
        cv = h.get("cvd_proxy")
        if cv is not None:
            extra.append(f"CVD∑ {cv:.0f}")
        lines.append(f"  <b>{sym}</b> +{pct}% ({', '.join(extra)})")
    if len(hits) > 25:
        lines.append(f"... и ещё {len(hits) - 25}")
    return "\n".join(lines)


async def scan_early_pump_hits(*, respect_dedup: bool = True) -> list[dict]:
    from dotenv import load_dotenv

    load_dotenv()

    hits: list[dict] = []
    now = time.time()
    dedup_sec = getattr(config, "EARLY_PUMP_DEDUP_MIN", 30) * 60
    cnt_pre = 0
    cnt_after_taker = 0
    cnt_after_btc = 0
    cnt_after_ema = 0
    cnt_after_cvd = 0
    cnt_after_oi = 0

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        tf = getattr(config, "EARLY_PUMP_TIMEFRAME", "5m")
        use_btc = getattr(config, "EARLY_PUMP_USE_BTC_FILTER", False)
        btc_ref_body: float | None = None
        if use_btc:
            btc_klines = await client.get_klines("BTCUSDT", tf, 20)
            if btc_klines:
                bb = _body_pct_long(btc_klines[-1])
                btc_ref_body = None if bb is None else float(bb)

        max_sym = getattr(config, "EARLY_PUMP_MAX_SYMBOLS", 200)
        min_qv = getattr(config, "EARLY_PUMP_MIN_QUOTE_VOL_24H", 25_000.0)
        sort_by = getattr(config, "EARLY_PUMP_SYMBOL_SORT", "abs_change_24h")
        symbols = await client.get_symbols_for_movement_scan(
            min_qv,
            0 if max_sym <= 0 else 99999,
            sort_by=sort_by,
        )
        if getattr(config, "EARLY_PUMP_SHUFFLE", False):
            random.shuffle(symbols)
        if max_sym > 0:
            symbols = symbols[:max_sym]

        for symbol in symbols:
            try:
                if (
                    respect_dedup
                    and symbol in _last_early_pump_at
                    and now - _last_early_pump_at[symbol] < dedup_sec
                ):
                    pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                    if pause > 0:
                        await asyncio.sleep(pause)
                    continue

                thr_24 = float(getattr(config, "EARLY_PUMP_SKIP_IF_ABS_CHANGE_24H_PCT", 0.0) or 0.0)
                if thr_24 > 0:
                    t24 = await client.get_24hr_ticker(symbol)
                    if t24:
                        try:
                            ap = abs(float(t24.get("priceChangePercent", 0.0)))
                        except (TypeError, ValueError):
                            ap = 0.0
                        if ap >= thr_24:
                            pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                            if pause > 0:
                                await asyncio.sleep(pause)
                            continue
                    elif not getattr(config, "EARLY_PUMP_SKIP_24H_IGNORE_EMPTY", True):
                        pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                        if pause > 0:
                            await asyncio.sleep(pause)
                        continue

                candles = await client.get_klines(symbol, tf, 150)
                if len(candles) < 10:
                    continue
                use_forming = getattr(config, "EARLY_PUMP_USE_FORMING_CANDLE", True)
                fallback = getattr(config, "EARLY_PUMP_FALLBACK_CLOSED", True)
                base = None
                if use_forming:
                    base = _quiet_and_spike(candles, tf=tf, forming=True)
                if base is None and fallback:
                    base = _quiet_and_spike(candles, tf=tf, forming=False)
                if not base:
                    continue
                cnt_pre += 1

                use_taker = getattr(config, "EARLY_PUMP_USE_TAKER", True)
                ignore_empty = getattr(config, "EARLY_PUMP_TAKER_IGNORE_EMPTY", True)
                min_tr = float(getattr(config, "EARLY_PUMP_TAKER_MIN_RATIO", 1.02))
                if use_taker:
                    taker_r = await _taker_buy_sell_ratio(client, symbol, 1, period=tf)
                    if taker_r is None:
                        if not ignore_empty:
                            continue
                    elif taker_r < min_tr:
                        continue
                    if taker_r is not None:
                        base["taker_ratio"] = round(taker_r, 3)
                cnt_after_taker += 1

                if use_btc and symbol != "BTCUSDT" and btc_ref_body is not None:
                    min_out = float(getattr(config, "EARLY_PUMP_MIN_OUTPERFORM_BTC_PCT", 0.25))
                    if base["pct"] - btc_ref_body < min_out:
                        continue
                    base["vs_btc"] = round(base["pct"] - btc_ref_body, 2)
                cnt_after_btc += 1

                use_ema = getattr(config, "EARLY_PUMP_USE_EMA_FILTER", True)
                ema_period = int(getattr(config, "EARLY_PUMP_EMA_PERIOD", 20))
                max_above = float(getattr(config, "EARLY_PUMP_MAX_ABOVE_EMA_PCT", 4.0))
                ign_ema = getattr(config, "EARLY_PUMP_EMA_IGNORE_EMPTY", True)
                if use_ema:
                    d_ep, _ev = _ema_detach_above_pct(candles, ema_period)
                    if d_ep is None:
                        if not ign_ema:
                            continue
                    elif d_ep < 0 or d_ep > max_above:
                        continue
                    if d_ep is not None:
                        base["ema_above_pct"] = round(d_ep, 2)
                cnt_after_ema += 1

                use_cvd = getattr(config, "EARLY_PUMP_USE_CVD_FILTER", True)
                cvd_bars = int(getattr(config, "EARLY_PUMP_CVD_BARS", 12))
                cvd_min = float(getattr(config, "EARLY_PUMP_CVD_MIN_SUM", 0.0))
                ign_cvd = getattr(config, "EARLY_PUMP_CVD_IGNORE_EMPTY", True)
                if use_cvd:
                    chunk = candles[-cvd_bars:] if len(candles) >= cvd_bars else []
                    cvd = _cvd_proxy_sum(chunk)
                    if cvd is None:
                        if not ign_cvd:
                            continue
                    elif cvd <= cvd_min:
                        continue
                    if cvd is not None:
                        base["cvd_proxy"] = round(cvd, 2)
                cnt_after_cvd += 1

                use_oi = getattr(config, "EARLY_PUMP_USE_OI_FILTER", False)
                if use_oi:
                    oi_ch = await _oi_window_change_pct(client, symbol, tf)
                    min_oi = float(getattr(config, "EARLY_PUMP_OI_MIN_CHANGE_PCT", 0.12))
                    ign = getattr(config, "EARLY_PUMP_OI_IGNORE_EMPTY", True)
                    if oi_ch is None:
                        if not ign:
                            continue
                    elif oi_ch < min_oi:
                        continue
                    if oi_ch is not None:
                        base["oi_change_pct"] = round(oi_ch, 3)
                cnt_after_oi += 1

                base["quality_score"] = round(_early_pump_quality_score(base))
                min_q = float(getattr(config, "EARLY_PUMP_MIN_QUALITY_SCORE", 0.0) or 0.0)
                if min_q > 0 and base["quality_score"] < min_q:
                    continue

                base["symbol"] = symbol
                hits.append(base)
                if respect_dedup:
                    _last_early_pump_at[symbol] = now

            except Exception as e:
                print(f"[EARLY] {symbol}: {e}")
            finally:
                pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                if pause > 0:
                    await asyncio.sleep(pause)

    cutoff = now - 86400
    for k in list(_last_early_pump_at.keys()):
        if _last_early_pump_at[k] < cutoff:
            del _last_early_pump_at[k]

    hits.sort(
        key=lambda x: (x.get("quality_score", 0), x.get("vol_ratio", 0)),
        reverse=True,
    )

    max_alert = int(getattr(config, "EARLY_PUMP_MAX_ALERTS_PER_SCAN", 5) or 0)
    if max_alert > 0 and len(hits) > max_alert:
        hits = hits[:max_alert]

    if os.getenv("EARLY_PUMP_QUIET_DIAG", "").strip() not in ("1", "true", "yes"):
        print(
            f"[EARLY] диагностика: тишина+vol: {cnt_pre}, taker: {cnt_after_taker}, "
            f"vs BTC: {cnt_after_btc}, EMA: {cnt_after_ema}, CVD: {cnt_after_cvd}, OI: {cnt_after_oi}, "
            f"в алерт: {len(hits)}",
            flush=True,
        )
    return hits


async def run_early_pump_scan(send_tg: bool = True) -> tuple[list[dict], bool]:
    hits = await scan_early_pump_hits(respect_dedup=True)
    if not hits:
        return hits, True
    tg_ok = True
    if send_tg:
        text = build_early_pump_alert_text(hits)
        sec = ephemeral_delete_seconds()
        tg_ok = await send_telegram(text, parse_mode="HTML", delete_after_sec=sec if sec > 0 else None)
        if tg_ok:
            log.info("[EARLY] Старт пампа: отправлено в TG (%s пар)", len(hits))
        else:
            log.error("[EARLY] send_telegram не удалось (%s пар)", len(hits))
    else:
        tg_ok = False
    try:
        from pump_stats import record_early_pump_signals

        tf = str(hits[0].get("tf") or getattr(config, "EARLY_PUMP_TIMEFRAME", "5m"))
        record_early_pump_signals(hits, tf, bool(send_tg and tg_ok))
    except Exception:
        log.exception("pump_stats record")
    return hits, tg_ok
