from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_NAME = "obs-web-widgets"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17363
DEFAULT_SOURCE_BUNDLE_ID = "com.netease.163music"
DEFAULT_POLL_INTERVAL = 0.6
DEFAULT_LYRIC_OFFSET = 0.0


@dataclass(frozen=True)
class AppConfig:
    lyric_offset: float = DEFAULT_LYRIC_OFFSET
    now_playing_bundle_id: str = DEFAULT_SOURCE_BUNDLE_ID
    poll_interval: float = DEFAULT_POLL_INTERVAL
    countdown_name: str = "Counting"
    countdown_target: str = "2026-12-20 00:00:00"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "lyricOffset": self.lyric_offset,
            "nowPlayingBundleID": self.now_playing_bundle_id,
            "pollInterval": self.poll_interval,
            "countdownName": self.countdown_name,
            "countdownTarget": self.countdown_target,
        }


def default_config_dir() -> Path:
    override = os.environ.get("OBS_WEB_WIDGETS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_NAME


def default_config_path() -> Path:
    override = os.environ.get("OBS_WEB_WIDGETS_CONFIG")
    if override:
        return Path(override).expanduser()
    return default_config_dir() / "config.json"


def config_from_mapping(mapping: dict[str, Any] | None) -> AppConfig:
    mapping = mapping or {}
    defaults = AppConfig()
    return AppConfig(
        lyric_offset=_float_value(mapping.get("lyricOffset", mapping.get("lyric_offset")), defaults.lyric_offset),
        now_playing_bundle_id=str(
            mapping.get("nowPlayingBundleID", mapping.get("now_playing_bundle_id", defaults.now_playing_bundle_id))
            or ""
        ).strip(),
        poll_interval=_positive_float(
            mapping.get("pollInterval", mapping.get("poll_interval")),
            defaults.poll_interval,
            minimum=0.1,
            maximum=60.0,
        ),
        countdown_name=str(mapping.get("countdownName", mapping.get("countdown_name", defaults.countdown_name)) or ""),
        countdown_target=str(
            mapping.get("countdownTarget", mapping.get("countdown_target", defaults.countdown_target)) or ""
        ).strip(),
    )


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.exists():
        return AppConfig()

    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    if not isinstance(payload, dict):
        return AppConfig()
    return config_from_mapping(payload)


def save_config(config: AppConfig, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(config.to_public_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(path)


def update_config(current: AppConfig, payload: dict[str, Any]) -> AppConfig:
    merged = current.to_public_dict()
    for key in ("lyricOffset", "nowPlayingBundleID", "pollInterval", "countdownName", "countdownTarget"):
        if key in payload:
            merged[key] = payload[key]
    return config_from_mapping(merged)


def _float_value(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _positive_float(value: Any, fallback: float, *, minimum: float, maximum: float) -> float:
    parsed = _float_value(value, fallback)
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def config_to_json(config: AppConfig, *, path: Path, host: str, port: int) -> dict[str, Any]:
    return {
        "ok": True,
        "config": config.to_public_dict(),
        "configPath": str(path),
        "server": {
            "host": host,
            "port": port,
            "baseURL": f"http://{host}:{port}",
        },
    }
