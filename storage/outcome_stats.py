"""
Статистика по сигналам из signals.jsonl.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORAGE_PATH = Path(__file__).resolve().parent / "signals.jsonl"
MOSCOW = timezone(timedelta(hours=3))


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


def compute_stats(records: list[dict]) -> dict:
    """Считает TP/SL/NO_OUTCOME, winrate, по стратегиям."""
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

    outcomes = {"TP": 0, "SL": 0, "NO_OUTCOME": 0}
    per_strategy: dict[str, dict] = {}
    rr_values = []
    mfe_values = []
    mae_values = []

    for item in by_signal.values():
        open_row = item.get("open")
        if not open_row:
            continue
        strategy = str(open_row.get("strategy", "UNKNOWN"))
        st = per_strategy.setdefault(strategy, {"total": 0, "wins": 0})
        st["total"] += 1

        rr = open_row.get("rr_planned")
        if isinstance(rr, (int, float)):
            rr_values.append(float(rr))

        resolved = item.get("resolved")
        if isinstance(resolved, dict):
            res = resolved.get("result")
            if res in outcomes:
                outcomes[res] += 1
            if res == "TP":
                st["wins"] += 1
            mfe = resolved.get("mfe_pct")
            mae = resolved.get("mae_pct")
            if isinstance(mfe, (int, float)):
                mfe_values.append(float(mfe))
            if isinstance(mae, (int, float)):
                mae_values.append(float(mae))

    winrate_by_strategy = {}
    for strat, st in per_strategy.items():
        t, w = st["total"], st["wins"]
        winrate_by_strategy[strat] = round((w / t) * 100, 2) if t else None

    total = outcomes["TP"] + outcomes["SL"] + outcomes["NO_OUTCOME"]
    winrate = round((outcomes["TP"] / total) * 100, 2) if total else None

    return {
        "outcomes": outcomes,
        "winrate": winrate,
        "winrate_by_strategy": winrate_by_strategy,
        "avg_rr_planned": round(sum(rr_values) / len(rr_values), 4) if rr_values else None,
        "avg_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "avg_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
        "signals_total": sum(st["total"] for st in per_strategy.values()),
    }
