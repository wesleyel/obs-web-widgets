from __future__ import annotations

import datetime as dt
import time
from typing import Any

from .config import AppConfig


def parse_countdown_target(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = dt.datetime.combine(dt.date.fromisoformat(raw), dt.time.min)
            except ValueError:
                return None
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    return None


def countdown_snapshot(config: AppConfig, *, now: dt.datetime | None = None) -> dict[str, Any]:
    target = parse_countdown_target(config.countdown_target)
    if target is None:
        return {
            "ok": False,
            "name": config.countdown_name,
            "target": config.countdown_target,
            "message": "倒数日配置无效，请在配置页检查目标时间",
            "updatedAt": time.time(),
        }

    now = now or dt.datetime.now()
    remaining_seconds = max(0, int((target - now).total_seconds()))
    days, remainder = divmod(remaining_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)

    return {
        "ok": True,
        "name": config.countdown_name,
        "target": target.isoformat(sep=" ", timespec="seconds"),
        "expired": remaining_seconds == 0,
        "remainingSeconds": remaining_seconds,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
        "updatedAt": time.time(),
    }
