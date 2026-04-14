"""
Общая дедупликация сигналов сканера между процессами (файл data/scanner_dedup.json).
Иначе два экземпляра main.py / старый и новый сервис шлют один и тот же план дважды.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DEDUP_PATH = Path(__file__).resolve().parent.parent / "data" / "scanner_dedup.json"
_PRUNE_EXTRA_SEC = 3600.0  # храним записи на dedup_sec + 1ч, дальше чистим


def dedup_file_path() -> Path:
    return _DEDUP_PATH


def load_state() -> dict[str, float]:
    _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _DEDUP_PATH.is_file():
        return {}
    try:
        raw = _DEDUP_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        log.warning("scanner_dedup load: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def save_state(state: dict[str, float], *, dedup_sec: float) -> None:
    """Сохраняет только свежие ключи (старше dedup_sec + запас — удаляются)."""
    _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    keep_before = now - (dedup_sec + _PRUNE_EXTRA_SEC)
    pruned = {k: ts for k, ts in state.items() if ts >= keep_before}
    try:
        _DEDUP_PATH.write_text(
            json.dumps(pruned, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("scanner_dedup save: %s", e)


def is_recent_plan(fp: str, state: dict[str, float], dedup_sec: float) -> bool:
    ts = state.get(fp)
    if ts is None:
        return False
    return (time.time() - ts) < dedup_sec


def mark_plan_sent(fp: str, *, dedup_sec: float) -> None:
    state = load_state()
    state[fp] = time.time()
    save_state(state, dedup_sec=dedup_sec)


def clear_plan(fp: str, *, dedup_sec: float) -> None:
    """Убрать ключ (например если Telegram не принял сообщение — повторить позже)."""
    state = load_state()
    if state.pop(fp, None) is None:
        return
    save_state(state, dedup_sec=dedup_sec)
