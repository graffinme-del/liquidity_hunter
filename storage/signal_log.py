"""
Логирование сигналов в storage/signals.jsonl для статистики и outcome.
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

STORAGE_PATH = Path(__file__).resolve().parent / "signals.jsonl"
TTL_HOURS = 48


def _tp_from_zone(tp_zone: tuple, direction: str) -> float:
    if not tp_zone or len(tp_zone) < 2:
        return 0.0
    low, high = float(tp_zone[0]), float(tp_zone[1])
    # LONG: TP когда цена выше; SHORT: TP когда цена ниже
    if direction == "LONG":
        return high  # цель — верх зоны
    return low  # цель — низ зоны


def log_signal(signal: dict) -> None:
    """Записывает сигнал со статусом OPEN в signals.jsonl."""
    signal_id = str(uuid.uuid4())[:8]
    entry = float(signal.get("entry", 0) or signal.get("trigger_price", 0))
    stop = float(signal.get("stop", 0))
    tp_zone = signal.get("tp_zone", (0, 0))
    tp = _tp_from_zone(tp_zone, signal.get("direction", ""))
    if tp <= 0 and tp_zone:
        tp = max(float(tp_zone[0]), float(tp_zone[1])) if signal.get("direction") == "LONG" else min(float(tp_zone[0]), float(tp_zone[1]))

    moscow = timezone(timedelta(hours=3))
    now = datetime.now(moscow)

    risk = abs(entry - stop)
    reward = abs(tp - entry) if tp else 0
    rr_planned = round(reward / risk, 4) if risk > 0 else None

    payload = {
        "signal_id": signal_id,
        "symbol": signal.get("symbol", ""),
        "strategy": signal.get("strategy", ""),
        "direction": signal.get("direction", ""),
        "entry_price": entry,
        "trigger_price": float(signal.get("trigger_price", entry) or entry),
        "tp_price": tp,
        "sl_price": stop,
        "rr_planned": rr_planned,
        "created_at": now.isoformat(),
        "ts_unix": int(now.timestamp()),
        "timeframe": "15m",
        "score_total": signal.get("score", 0),
        "reason_ru": signal.get("reason_ru", ""),
        "status": "OPEN",
        "resolved": False,
        "result": None,
        "mfe_pct": None,
        "mae_pct": None,
    }
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORAGE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
