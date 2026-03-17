"""
Формирование дневного отчёта по статистике сигналов.
"""
from storage.outcome_stats import load_last, compute_stats


def build_daily_report() -> str:
    """Текст отчёта за день (до 21:00 Москва)."""
    records = load_last(1)
    if not records:
        return "📊 Отчёт за день\n\nСигналов: 0"

    stats = compute_stats(records)
    o = stats.get("outcomes", {})
    tp = o.get("TP", 0)
    sl = o.get("SL", 0)
    no = o.get("NO_OUTCOME", 0)
    total = tp + sl + no
    wr = stats.get("winrate") or "n/a"
    avg_rr = stats.get("avg_rr_planned") or "n/a"
    avg_mfe = stats.get("avg_mfe_pct") or "n/a"
    avg_mae = stats.get("avg_mae_pct") or "n/a"

    lines = [
        "📊 Отчёт за день",
        "",
        f"Сигналов: {stats.get('signals_total', 0)}",
        f"TP: {tp} | SL: {sl} | Без исхода: {no}",
        f"Winrate: {wr}%",
        f"Ср. RR: {avg_rr} | MFE: {avg_mfe}% | MAE: {avg_mae}%",
    ]

    by_strat = stats.get("winrate_by_strategy") or {}
    if by_strat:
        lines.append("")
        lines.append("По стратегиям:")
        for strat, wr_s in sorted(by_strat.items(), key=lambda x: -x[1] if x[1] else 0):
            lines.append(f"  {strat}: {wr_s}%")

    return "\n".join(lines)
