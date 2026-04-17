"""
Сканер разворота: контекст на старшем TF + подтверждение на младшем (4h→1h и 1h→15m).
Сигналы только по закрытым свечам (без формирующейся).
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import time
from typing import Any

import aiohttp

import config
from data.binance_client import BinanceClient
from detectors.reversal_tf_pair import evaluate_pair
from telegram_notify import ephemeral_delete_seconds, send_telegram

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}


def _cfg_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _cfg_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _cfg_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _dedup_key(symbol: str, pair_id: str, direction: str) -> str:
    return f"{symbol}|{pair_id}|{direction}"


def format_reversal_alert(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = [
        "<b>Разворот (контекст + младший TF)</b>",
        "<i>Закрытые свечи: старший TF — перегрев по ATR+ROC, младший — новый экстремум + отказ.</i>",
        "",
    ]
    for r in rows[:20]:
        sym = html.escape(str(r.get("symbol", "?")))
        d = r.get("direction", "?")
        ctx = r.get("ctx_tf", "?")
        trg = r.get("trig_tf", "?")
        extra = []
        if r.get("ctx_atr_pct") is not None:
            extra.append(f"ATR ctx ~{float(r['ctx_atr_pct']):.2f}%")
        if r.get("trig_range_pct") is not None:
            extra.append(f"диап. {trg} ~{float(r['trig_range_pct']):.2f}%")
        suf = " · ".join(extra) if extra else ""
        lines.append(
            f"{'🔴' if d == 'SHORT' else '🟢'} <b>{sym}</b> {d} · {ctx}→{trg}"
            + (f" · {suf}" if suf else "")
        )
    return "\n".join(lines)


async def _evaluate_symbol(client: BinanceClient, symbol: str) -> list[dict]:
    out: list[dict] = []
    want_s = _cfg_bool("REVERSAL_SHORT_ENABLED", getattr(config, "REVERSAL_SHORT_ENABLED", True))
    want_l = _cfg_bool("REVERSAL_LONG_ENABLED", getattr(config, "REVERSAL_LONG_ENABLED", True))
    if not want_s and not want_l:
        return out

    if _cfg_bool("REVERSAL_PAIR_4H_1H_ENABLED", getattr(config, "REVERSAL_PAIR_4H_1H_ENABLED", True)):
        k4 = await client.get_klines(symbol, "4h", 120)
        k1 = await client.get_klines(symbol, "1h", 200)
        if len(k4) >= 30 and len(k1) >= 40:
            for sig in evaluate_pair(
                k4,
                k1,
                ctx_label="4h",
                trig_label="1h",
                ctx_atr_min=_cfg_float("REVERSAL_CTX_4H_ATR_MIN", getattr(config, "REVERSAL_CTX_4H_ATR_MIN", 0.5)),
                ctx_roc_lookback=_cfg_int("REVERSAL_CTX_4H_ROC_LOOKBACK", getattr(config, "REVERSAL_CTX_4H_ROC_LOOKBACK", 6)),
                ctx_roc_min_pct=_cfg_float("REVERSAL_CTX_4H_ROC_MIN_PCT", getattr(config, "REVERSAL_CTX_4H_ROC_MIN_PCT", 5.0)),
                trig_swing_lookback=_cfg_int("REVERSAL_TRIG_1H_SWING_LOOKBACK", getattr(config, "REVERSAL_TRIG_1H_SWING_LOOKBACK", 24)),
                trig_min_range_pct=_cfg_float("REVERSAL_TRIG_1H_MIN_RANGE_PCT", getattr(config, "REVERSAL_TRIG_1H_MIN_RANGE_PCT", 0.12)),
                trig_wick_min=_cfg_float("REVERSAL_TRIG_1H_WICK_MIN", getattr(config, "REVERSAL_TRIG_1H_WICK_MIN", 0.35)),
                want_short=want_s,
                want_long=want_l,
            ):
                sig["symbol"] = symbol
                sig["pair_id"] = "4h_1h"
                out.append(sig)

    if _cfg_bool("REVERSAL_PAIR_1H_15M_ENABLED", getattr(config, "REVERSAL_PAIR_1H_15M_ENABLED", True)):
        kh = await client.get_klines(symbol, "1h", 120)
        k15 = await client.get_klines(symbol, "15m", 200)
        if len(kh) >= 30 and len(k15) >= 40:
            for sig in evaluate_pair(
                kh,
                k15,
                ctx_label="1h",
                trig_label="15m",
                ctx_atr_min=_cfg_float("REVERSAL_CTX_1H_ATR_MIN", getattr(config, "REVERSAL_CTX_1H_ATR_MIN", 0.28)),
                ctx_roc_lookback=_cfg_int("REVERSAL_CTX_1H_ROC_LOOKBACK", getattr(config, "REVERSAL_CTX_1H_ROC_LOOKBACK", 12)),
                ctx_roc_min_pct=_cfg_float("REVERSAL_CTX_1H_ROC_MIN_PCT", getattr(config, "REVERSAL_CTX_1H_ROC_MIN_PCT", 3.0)),
                trig_swing_lookback=_cfg_int("REVERSAL_TRIG_15M_SWING_LOOKBACK", getattr(config, "REVERSAL_TRIG_15M_SWING_LOOKBACK", 24)),
                trig_min_range_pct=_cfg_float("REVERSAL_TRIG_15M_MIN_RANGE_PCT", getattr(config, "REVERSAL_TRIG_15M_MIN_RANGE_PCT", 0.08)),
                trig_wick_min=_cfg_float("REVERSAL_TRIG_15M_WICK_MIN", getattr(config, "REVERSAL_TRIG_15M_WICK_MIN", 0.35)),
                want_short=want_s,
                want_long=want_l,
            ):
                sig["symbol"] = symbol
                sig["pair_id"] = "1h_15m"
                out.append(sig)

    return out


def _reversal_enabled() -> bool:
    raw = os.getenv("REVERSAL_ENABLED")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(config, "REVERSAL_ENABLED", False))


async def _reversal_symbol_list(client: BinanceClient, max_sym: int) -> tuple[list[str], str]:
    """top = крупный объём; movers = кто уже шевелится за 24h (лучше для разворотов на альтах)."""
    mode = (
        os.getenv("REVERSAL_SYMBOL_UNIVERSE")
        or getattr(config, "REVERSAL_SYMBOL_UNIVERSE", "movers")
        or "movers"
    ).strip().lower()
    qv = _cfg_float("REVERSAL_MIN_QUOTE_VOL_24H", getattr(config, "REVERSAL_MIN_QUOTE_VOL_24H", 25_000.0))
    if mode in ("mover", "movers", "abs_change", "volatile"):
        syms = await client.get_symbols_for_movement_scan(
            qv,
            99999,
            sort_by="abs_change_24h",
        )
        random.shuffle(syms)
        return syms[:max_sym], f"movers(|Δ24h|), qv≥{qv:.0f}"
    syms = await client.get_top_symbols(limit=max_sym)
    return syms, "top_volume"


async def run_reversal_scan_once() -> list[dict]:
    if not _reversal_enabled():
        return []
    max_sym = max(10, _cfg_int("REVERSAL_MAX_SYMBOLS", getattr(config, "REVERSAL_MAX_SYMBOLS", 80)))
    concurrent = max(1, min(20, _cfg_int("REVERSAL_CONCURRENCY", getattr(config, "REVERSAL_CONCURRENCY", 8))))
    dedup_sec = max(60, _cfg_int("REVERSAL_DEDUP_SEC", getattr(config, "REVERSAL_DEDUP_SEC", 7200)))

    now = time.time()
    for k in list(_last_sent.keys()):
        if now - _last_sent[k] > dedup_sec * 4:
            del _last_sent[k]

    raw: list[dict] = []
    universe_desc = ""

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        symbols, universe_desc = await _reversal_symbol_list(client, max_sym)

        sem = asyncio.Semaphore(concurrent)

        async def _one(sym: str) -> None:
            async with sem:
                try:
                    found = await _evaluate_symbol(client, sym)
                    raw.extend(found)
                except Exception as e:
                    log.debug("reversal %s: %s", sym, e)

        await asyncio.gather(*(_one(s) for s in symbols))

    hits: list[dict] = []
    for sig in raw:
        sym = str(sig.get("symbol", ""))
        key = _dedup_key(sym, str(sig.get("pair_id", "")), str(sig.get("direction", "")))
        if now - _last_sent.get(key, 0) < dedup_sec:
            continue
        hits.append(sig)

    log.info(
        "[REVERSAL] скан: %s | пар=%s | кандидатов=%s | после дедуп=%s",
        universe_desc,
        len(symbols),
        len(raw),
        len(hits),
    )

    return hits


async def run_reversal_loop() -> None:
    if not _reversal_enabled():
        log.info(
            "[REVERSAL] выключено: в .env задайте REVERSAL_ENABLED=1 (или True в config), иначе скан не крутится",
        )
        return
    interval = max(60, _cfg_int("REVERSAL_INTERVAL_SEC", getattr(config, "REVERSAL_INTERVAL_SEC", 180)))
    delay = max(0, _cfg_int("REVERSAL_START_DELAY_SEC", getattr(config, "REVERSAL_START_DELAY_SEC", 180)))
    log.info(
        "[REVERSAL] включено: каждые %ss лог строки «скан: …» — смотрите кандидатов и дедуп",
        interval,
    )
    if delay:
        await asyncio.sleep(delay)

    while True:
        try:
            hits = await run_reversal_scan_once()
            if hits:
                text = format_reversal_alert(hits)
                sec = ephemeral_delete_seconds()
                ok = await send_telegram(
                    text,
                    parse_mode="HTML",
                    delete_after_sec=sec if sec > 0 else None,
                )
                if ok:
                    ts = time.time()
                    for sig in hits:
                        k = _dedup_key(
                            str(sig.get("symbol", "")),
                            str(sig.get("pair_id", "")),
                            str(sig.get("direction", "")),
                        )
                        _last_sent[k] = ts
                    log.info("[REVERSAL] отправлено в TG: %s сигналов", len(hits))
                else:
                    log.error("[REVERSAL] send_telegram не удалось")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[REVERSAL] loop error")
        await asyncio.sleep(interval)
