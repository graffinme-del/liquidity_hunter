"""
Чтение сигналов и запись результатов (только TP или SL; иначе сигнал остаётся OPEN).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORAGE_PATH = Path(__file__).resolve().parent / "signals.jsonl"
TTL_HOURS = 48
MOSCOW = timezone(timedelta(hours=3))


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MOSCOW)
    return dt


def read_open_signals(start_at: datetime, end_at: datetime) -> list[dict]:
    """Читает OPEN сигналы за период создания, исключая уже резолвенные (два прохода: сначала все RESOLVED)."""
    if not STORAGE_PATH.exists():
        return []

    start_bound = _ensure_tz(start_at)
    end_bound = _ensure_tz(end_at)
    resolved_ids: set[str] = set()

    with STORAGE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = str(row.get("signal_id", ""))
            if not sid:
                continue
            if row.get("status") == "RESOLVED" or row.get("resolved") is True:
                # Закрыт только TP или SL; старые NO_OUTCOME не мешают дальше отслеживать сигнал
                res = row.get("result")
                if res in ("TP", "SL"):
                    resolved_ids.add(sid)

    open_rows: list[dict] = []
    with STORAGE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            sid = str(row.get("signal_id", ""))
            if row.get("status") != "OPEN":
                continue
            if sid in resolved_ids:
                continue

            ts_raw = row.get("created_at") or row.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            ts = _ensure_tz(ts)
            if not (start_bound <= ts <= end_bound):
                continue

            entry = float(row.get("entry_price", 0) or 0)
            open_rows.append({
                "signal_id": sid,
                "ts_unix": int(row.get("ts_unix", 0) or 0),
                "symbol": str(row.get("symbol", "")),
                "direction": str(row.get("direction", "")),
                "strategy": str(row.get("strategy", "UNKNOWN")),
                "entry_price": entry,
                "trigger_price": float(row.get("trigger_price", entry) or entry),
                "tp_price": float(row.get("tp_price", 0) or 0),
                "sl_price": float(row.get("sl_price", 0) or 0),
                "timeframe": str(row.get("timeframe", "15m") or "15m"),
            })

    return open_rows


def append_resolved(
    signal_id: str,
    result: str,
    mfe_pct: float,
    mae_pct: float,
    strategy: str,
) -> None:
    """Добавляет строку RESOLVED."""
    now = datetime.now(MOSCOW)
    payload = {
        "signal_id": signal_id,
        "strategy": strategy,
        "status": "RESOLVED",
        "resolved": True,
        "resolved_at": now.isoformat(),
        "result": result,
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "ttl_hours": TTL_HOURS,
    }
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORAGE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def resolve_outcome(
    signal: dict,
    high_price: float,
    low_price: float,
) -> str | None:
    """
    TP/SL по high/low за период. Запись в лог — только если тейк или стоп достигнут.
    Если ни один уровень не задели — None (сигнал остаётся OPEN, проверим на следующем прогоне).
    """
    entry = float(signal["entry_price"])
    tp = float(signal["tp_price"])
    sl = float(signal["sl_price"])
    direction = signal.get("direction", "LONG")

    if direction == "LONG":
        mfe = ((high_price - entry) / entry) * 100
        mae = ((low_price - entry) / entry) * 100
        tp_hit = high_price >= tp
        sl_hit = low_price <= sl
    else:
        mfe = ((entry - low_price) / entry) * 100
        mae = ((entry - high_price) / entry) * 100
        tp_hit = low_price <= tp
        sl_hit = high_price >= sl

    if tp_hit:
        result = "TP"
    elif sl_hit:
        result = "SL"
    else:
        return None

    append_resolved(
        signal_id=signal["signal_id"],
        result=result,
        mfe_pct=mfe,
        mae_pct=mae,
        strategy=signal.get("strategy", "UNKNOWN"),
    )
    return result
