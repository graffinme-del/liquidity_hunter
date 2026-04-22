"""
Отдельный цикл Liquidity Hunter: SQUEEZE 5m + OI (как Phase 1 — не в основном сканере).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import Counter, defaultdict

import aiohttp

import config
from data.binance_client import BinanceClient
from detectors.squeeze_oi_breakout import (
    attach_atr14_wilder,
    evaluate_squeeze_oi_breakout,
    squeeze_precheck_5m,
)
from telegram_notify import format_squeeze_oi_message, send_telegram

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}


def _cfg_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(config, name, default))


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


def _closed_only(candles: list[dict]) -> list[dict]:
    now_ms = int(time.time() * 1000)
    out: list[dict] = []
    for c in candles:
        ct = int(c.get("close_time") or c.get("open_time") or 0)
        if ct < now_ms:
            out.append(c)
    return out


async def _symbol_list(client: BinanceClient, max_sym: int) -> tuple[list[str], str]:
    mode = (os.getenv("SQUEEZE_OI_SYMBOL_UNIVERSE") or getattr(config, "SQUEEZE_OI_SYMBOL_UNIVERSE", "top") or "top").strip().lower()
    qv = float(os.getenv("SQUEEZE_OI_MIN_QUOTE_VOL_24H", str(getattr(config, "SQUEEZE_OI_MIN_QUOTE_VOL_24H", 25_000.0))))
    if mode in ("mover", "movers", "abs_change", "volatile"):
        syms = await client.get_symbols_for_movement_scan(qv, 99999, sort_by="abs_change_24h")
        random.shuffle(syms)
        return syms[:max_sym], f"movers(|Δ24h|), qv≥{qv:.0f}"
    syms = await client.get_top_symbols(limit=max_sym)
    return syms, "top_volume"


def _debug_rejects() -> bool:
    return os.getenv("SQUEEZE_OI_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _squeeze_telegram_overrides() -> tuple[str | None, int | None]:
    """Отдельный чат/топик только для SQUEEZE+OI (опционально)."""
    cid = (os.getenv("SQUEEZE_OI_TELEGRAM_CHAT_ID") or "").strip()
    if len(cid) >= 2 and cid[0] == cid[-1] and cid[0] in "\"'":
        cid = cid[1:-1].strip()
    tid_raw = (os.getenv("SQUEEZE_OI_TELEGRAM_TOPIC_ID") or "").strip()
    tid: int | None = None
    if tid_raw:
        try:
            tid = int(tid_raw)
        except ValueError:
            tid = None
    return (cid or None), tid


async def run_squeeze_oi_loop() -> None:
    if not _cfg_bool("SQUEEZE_OI_ENABLED", False):
        log.info("[SQUEEZE_OI] disabled (SQUEEZE_OI_ENABLED=0)")
        return

    scid, stid = _squeeze_telegram_overrides()
    log.info(
        "[SQUEEZE_OI] enabled — не все альты Binance: только USDT perpetual, "
        "фильтр объёма 24h и лимит пар за цикл (см. SQUEEZE_OI_MAX_SYMBOLS / UNIVERSE). "
        "DEBUG отсечек: SQUEEZE_OI_DEBUG=1"
    )
    if scid:
        log.info("[SQUEEZE_OI] TG → отдельный chat_id (SQUEEZE_OI_TELEGRAM_CHAT_ID)")
    if stid is not None:
        log.info("[SQUEEZE_OI] TG → topic_id=%s (форум-ветка)", stid)

    interval = max(60, _cfg_int("SQUEEZE_OI_INTERVAL_SEC", 180))
    max_sym = max(10, _cfg_int("SQUEEZE_OI_MAX_SYMBOLS", 50))
    dedup = max(120, _cfg_int("SQUEEZE_OI_DEDUP_SEC", 3600))
    concurrent = max(1, min(15, _cfg_int("SQUEEZE_OI_CONCURRENCY", 6)))
    delay_start = max(0, _cfg_int("SQUEEZE_OI_START_DELAY_SEC", 90))
    kl_limit = max(120, _cfg_int("SQUEEZE_OI_KLINES_LIMIT", 220))
    oi_limit = max(20, _cfg_int("SQUEEZE_OI_HIST_LIMIT", 60))
    compress_bars = max(1, _cfg_int("SQUEEZE_OI_COMPRESS_BARS", 36))

    if delay_start > 0:
        await asyncio.sleep(delay_start)

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        while True:
            try:
                symbols, uni_desc = await _symbol_list(client, max_sym)
                now = time.time()
                for k, ts in list(_last_sent.items()):
                    if now - ts > dedup * 4:
                        del _last_sent[k]

                sem = asyncio.Semaphore(concurrent)
                fail_counts: dict[str, int] = defaultdict(int)
                sent_n = 0
                dbg = _debug_rejects()
                miss_reasons: Counter[str] = Counter()

                async def _one(sym: str) -> None:
                    nonlocal sent_n
                    async with sem:
                        t = time.time()
                        if _last_sent.get(sym) is not None and t - _last_sent[sym] < dedup:
                            fail_counts["dedup_skip"] += 1
                            return
                        raw = await client.get_klines(sym, "5m", kl_limit)
                        closed = _closed_only(raw)
                        if not squeeze_precheck_5m(closed, compress_bars):
                            fail_counts["precheck"] += 1
                            return
                        # копия для расчёта ATR (мутируем attach_atr14)
                        c5 = [dict(c) for c in closed]
                        attach_atr14_wilder(c5)
                        oi_s = await client.get_open_interest_hist(sym, "5m", oi_limit)
                        ff: list[str] = []
                        ev = evaluate_squeeze_oi_breakout(
                            c5, oi_s, first_fail=ff if dbg else None
                        )
                        if ev is None:
                            fail_counts["no_match"] += 1
                            if dbg and ff:
                                miss_reasons[ff[0]] += 1
                            return
                        ok = await send_telegram(
                            format_squeeze_oi_message(sym, ev),
                            delete_after_sec=None,
                            chat_id=scid,
                            message_thread_id=stid,
                        )
                        if ok:
                            sent_n += 1
                            _last_sent[sym] = time.time()
                            log.info(
                                "[SQUEEZE_OI] sent %s OI=%.2f%% range=%.2f%%",
                                sym,
                                ev["oi_growth_pct"],
                                ev["range_pct"],
                            )

                await asyncio.gather(*(_one(s) for s in symbols))

                top_fail = sorted(fail_counts.items(), key=lambda x: -x[1])[:6]
                log.info(
                    "[SQUEEZE_OI] цикл: %s | пар=%s | TG=%s | отсевы: %s",
                    uni_desc,
                    len(symbols),
                    sent_n,
                    top_fail,
                )
                if dbg and miss_reasons:
                    top_miss = miss_reasons.most_common(12)
                    log.info("[SQUEEZE_OI] DEBUG первые отсечки после precheck: %s", top_miss)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[SQUEEZE_OI] loop error")

            await asyncio.sleep(interval)
