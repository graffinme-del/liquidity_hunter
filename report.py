"""
Формирование отчётов по статистике сигналов (signals.jsonl).
"""
from datetime import datetime

from storage.outcome_stats import (
    compute_stats,
    filter_open_signals_in_date_range,
    filter_records_for_open_signals_in_current_month,
    filter_records_for_open_signals_in_last_days,
    load_all_records,
    load_last,
)


def _stats_to_lines(title: str, stats: dict) -> list[str]:
    if not stats or stats.get("signals_total", 0) == 0:
        return [title, "", "Сигналов: 0"]

    o = stats.get("outcomes", {})
    tp = o.get("TP", 0)
    sl = o.get("SL", 0)
    pending = stats.get("pending", 0)
    wr = stats.get("winrate")
    wr_s = f"{wr}%" if wr is not None else "n/a"
    avg_rr = stats.get("avg_rr_planned") or "n/a"
    avg_mfe = stats.get("avg_mfe_pct") or "n/a"
    avg_mae = stats.get("avg_mae_pct") or "n/a"

    lines = [
        title,
        "",
        f"Сигналов: {stats.get('signals_total', 0)}",
        f"TP: {tp} | SL: {sl} | В отслеживании (ещё не TP/SL): {pending}",
        f"Winrate: {wr_s} — доля TP среди закрытых (TP+SL)",
        f"Ср. RR: {avg_rr} | MFE: {avg_mfe}% | MAE: {avg_mae}%",
    ]

    by_strat = stats.get("winrate_by_strategy") or {}
    if by_strat:
        lines.append("")
        lines.append("По стратегиям:")
        for strat, wr_v in sorted(by_strat.items(), key=lambda x: -x[1] if x[1] else 0):
            lines.append(f"  {strat}: {wr_v}%")

    return lines


def build_winrate_range_report(start: datetime, end: datetime) -> str:
    """Winrate за произвольный период (дата OPEN сигнала, МСК)."""
    raw = load_all_records()
    rec = filter_open_signals_in_date_range(raw, start, end)
    if start.date() == end.date():
        title = f"📊 Winrate за {start.strftime('%d.%m.%Y')}"
    else:
        title = (
            f"📊 Winrate за период {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"
        )
    if not rec:
        return f"{title}\n\nСигналов: 0"
    stats = compute_stats(rec)
    return "\n".join(_stats_to_lines(title, stats))


def build_daily_report() -> str:
    """Текст отчёта за день (до 21:00 Москва)."""
    records = load_last(1)
    if not records:
        return "📊 Отчёт за день\n\nСигналов: 0"

    stats = compute_stats(records)
    return "\n".join(_stats_to_lines("📊 Отчёт за день", stats))


def build_weekly_report() -> str:
    """Итог за последние 7 дней (скользящее окно)."""
    raw = load_last(10)
    rec = filter_records_for_open_signals_in_last_days(raw, 7)
    if not rec:
        return "📊 Итог недели (7 дней)\n\nСигналов: 0"
    stats = compute_stats(rec)
    return "\n".join(_stats_to_lines("📊 Итог недели (последние 7 дней)", stats))


def build_monthly_report() -> str:
    """С 1-го числа текущего месяца до сейчас."""
    raw = load_last(40)
    rec = filter_records_for_open_signals_in_current_month(raw)
    if not rec:
        return "📊 Итог месяца\n\nСигналов: 0"
    stats = compute_stats(rec)
    return "\n".join(_stats_to_lines("📊 Итог месяца (с 1-го числа)", stats))


def build_rolling_windows_report(windows: tuple[int, ...] = (2, 3, 4, 5, 6, 7)) -> str:
    """Компактные строки winrate по окнам 2…7 дней."""
    max_w = max(windows)
    raw = load_last(max_w + 3)
    lines = [
        "📊 Winrate по окнам (OPEN за период, исходы из лога)",
        "",
    ]
    for d in windows:
        rec = filter_records_for_open_signals_in_last_days(raw, d)
        stats = compute_stats(rec)
        o = stats.get("outcomes", {})
        tp = o.get("TP", 0)
        sl = o.get("SL", 0)
        pend = stats.get("pending", 0)
        wr = stats.get("winrate")
        wr_s = f"{wr}%" if wr is not None else "n/a"
        n = stats.get("signals_total", 0)
        lines.append(
            f"• {d} дн.: сигналов {n} | TP={tp} SL={sl} | в отслеж.={pend} | WR={wr_s}"
        )
    return "\n".join(lines)
