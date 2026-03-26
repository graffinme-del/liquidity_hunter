"""
Статистика по сигналам из signals.jsonl.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORAGE_PATH = Path(__file__).resolve().parent / "signals.jsonl"
MOSCOW = timezone(timedelta(hours=3))


def load_all_records() -> list[dict]:
    """Все строки из signals.jsonl (для отчёта за произвольный период)."""
    if not STORAGE_PATH.exists():
        return []
    rows: list[dict] = []
    with STORAGE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
    return rows


def filter_open_signals_in_date_range(
    records: list[dict],
    start: datetime,
    end: datetime,
) -> list[dict]:
    """
    OPEN-сигналы с created_at в интервале [начало дня start, конец дня end] включительно (Москва).
    start/end — как из парсера DD.MM (полночь); end — последний день диапазона включительно.
    """
    start_m = start.astimezone(MOSCOW) if start.tzinfo else start.replace(tzinfo=MOSCOW)
    end_m = end.astimezone(MOSCOW) if end.tzinfo else end.replace(tzinfo=MOSCOW)
    lower = start_m.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_m.replace(hour=0, minute=0, second=0, microsecond=0)
    upper = end_day + timedelta(days=1)

    open_ids: set[str] = set()
    for row in records:
        if row.get("status") != "OPEN":
            continue
        sid = row.get("signal_id")
        if not sid:
            continue
        ts_val = row.get("created_at") or row.get("ts")
        if not isinstance(ts_val, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_val)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=MOSCOW)
        else:
            ts = ts.astimezone(MOSCOW)
        if lower <= ts < upper:
            open_ids.add(str(sid))
    return [r for r in records if r.get("signal_id") and str(r.get("signal_id")) in open_ids]


def load_last(days: int) -> list[dict]:
    """Загружает все строки за последние days дней."""
    if not STORAGE_PATH.exists():
        return []
    cutoff = datetime.now(MOSCOW) - timedelta(days=days)
    rows = []
    with STORAGE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_val = row.get("created_at") or row.get("resolved_at")
            if isinstance(ts_val, str):
                try:
                    ts = datetime.fromisoformat(ts_val)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=MOSCOW)
                    if ts >= cutoff:
                        rows.append(row)
                except ValueError:
                    pass
    return rows


def filter_records_for_open_signals_in_last_days(records: list[dict], days: int) -> list[dict]:
    """Оставляет строки только для signal_id, у которых OPEN попал в последние days дней (Москва)."""
    cutoff = datetime.now(MOSCOW) - timedelta(days=days)
    open_ids: set[str] = set()
    for row in records:
        if row.get("status") != "OPEN":
            continue
        sid = row.get("signal_id")
        if not sid:
            continue
        ts_val = row.get("created_at") or row.get("ts")
        if not isinstance(ts_val, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_val)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=MOSCOW)
        else:
            ts = ts.astimezone(MOSCOW)
        if ts >= cutoff:
            open_ids.add(str(sid))
    return [r for r in records if r.get("signal_id") and str(r.get("signal_id")) in open_ids]


def filter_records_for_open_signals_in_current_month(records: list[dict]) -> list[dict]:
    """OPEN с 1-го числа текущего месяца (Москва)."""
    now = datetime.now(MOSCOW)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    open_ids: set[str] = set()
    for row in records:
        if row.get("status") != "OPEN":
            continue
        sid = row.get("signal_id")
        if not sid:
            continue
        ts_val = row.get("created_at") or row.get("ts")
        if not isinstance(ts_val, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_val)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=MOSCOW)
        else:
            ts = ts.astimezone(MOSCOW)
        if ts >= month_start:
            open_ids.add(str(sid))
    return [r for r in records if r.get("signal_id") and str(r.get("signal_id")) in open_ids]


def compute_stats(records: list[dict]) -> dict:
    """TP/SL и «в отслеживании»; winrate = TP/(TP+SL) по закрытым."""
    by_signal: dict[str, dict] = {}
    for row in records:
        sid = row.get("signal_id")
        if not sid:
            continue
        slot = by_signal.setdefault(str(sid), {})
        if row.get("status") == "OPEN":
            slot["open"] = row
        if row.get("resolved") or row.get("result") in ("TP", "SL", "NO_OUTCOME"):
            slot["resolved"] = row

    outcomes = {"TP": 0, "SL": 0}
    pending = 0
    per_strategy: dict[str, dict] = {}
    rr_values: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []

    for item in by_signal.values():
        open_row = item.get("open")
        if not open_row:
            continue
        strategy = str(open_row.get("strategy", "UNKNOWN"))
        st = per_strategy.setdefault(
            strategy,
            {"tp": 0, "sl": 0, "pending": 0},
        )

        rr = open_row.get("rr_planned")
        if isinstance(rr, (int, float)):
            rr_values.append(float(rr))

        resolved = item.get("resolved")
        if not isinstance(resolved, dict):
            pending += 1
            st["pending"] += 1
            continue

        res = resolved.get("result")
        if res == "TP":
            outcomes["TP"] += 1
            st["tp"] += 1
            mfe = resolved.get("mfe_pct")
            mae = resolved.get("mae_pct")
            if isinstance(mfe, (int, float)):
                mfe_values.append(float(mfe))
            if isinstance(mae, (int, float)):
                mae_values.append(float(mae))
        elif res == "SL":
            outcomes["SL"] += 1
            st["sl"] += 1
            mfe = resolved.get("mfe_pct")
            mae = resolved.get("mae_pct")
            if isinstance(mfe, (int, float)):
                mfe_values.append(float(mfe))
            if isinstance(mae, (int, float)):
                mae_values.append(float(mae))
        else:
            # NO_OUTCOME в старом логе или неизвестный — считаем «ещё не TP/SL»
            pending += 1
            st["pending"] += 1

    winrate_by_strategy: dict[str, float | None] = {}
    for strat, st in per_strategy.items():
        tp_c, sl_c = st["tp"], st["sl"]
        denom = tp_c + sl_c
        winrate_by_strategy[strat] = round((tp_c / denom) * 100, 2) if denom else None

    closed = outcomes["TP"] + outcomes["SL"]
    winrate = round((outcomes["TP"] / closed) * 100, 2) if closed else None

    signals_total = len([x for x in by_signal.values() if x.get("open")])

    return {
        "outcomes": outcomes,
        "pending": pending,
        "winrate": winrate,
        "winrate_by_strategy": winrate_by_strategy,
        "avg_rr_planned": round(sum(rr_values) / len(rr_values), 4) if rr_values else None,
        "avg_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "avg_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
        "signals_total": signals_total,
    }
