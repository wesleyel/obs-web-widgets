from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

_ADAPTER_ARCHIVE = "mediaremote-adapter.tar.gz"
_SYSTEM_PERL = Path("/usr/bin/perl")
_ADAPTER_DIR: Path | None = None
_ADAPTER_LOCK = threading.Lock()


def now_playing_adapter_archive_path() -> str:
    return str(files("obs_web_widgets.resources").joinpath(_ADAPTER_ARCHIVE))


def read_now_playing(command_override: str | None = None) -> dict[str, Any]:
    command_override = command_override if command_override is not None else os.environ.get("NOW_PLAYING_COMMAND", "")
    if command_override:
        command = shlex.split(command_override)
    else:
        perl = str(_SYSTEM_PERL) if _SYSTEM_PERL.exists() else shutil.which("perl")
        if perl is None:
            raise RuntimeError("找不到 perl，无法读取 macOS Now Playing")
        adapter_script, framework_path = _ensure_mediaremote_adapter()
        command = [perl, str(adapter_script), str(framework_path), "get", "--no-artwork"]

    completed = subprocess.run(command, capture_output=True, check=False, text=True, timeout=3)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Now Playing 读取失败"
        raise RuntimeError(message)

    payload = json.loads(completed.stdout)
    if "ok" in payload:
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error", "Now Playing 读取失败"))
        return payload
    return _normalize_adapter_payload(payload)


def _ensure_mediaremote_adapter() -> tuple[Path, Path]:
    global _ADAPTER_DIR

    with _ADAPTER_LOCK:
        if _ADAPTER_DIR is not None:
            adapter_script = _ADAPTER_DIR / "mediaremote-adapter.pl"
            framework_path = _ADAPTER_DIR / "MediaRemoteAdapter.framework"
            if adapter_script.exists() and framework_path.exists():
                return adapter_script, framework_path

        adapter_dir = Path(tempfile.mkdtemp(prefix="obs-web-widgets-mediaremote-"))
        archive = files("obs_web_widgets.resources").joinpath(_ADAPTER_ARCHIVE)
        with as_file(archive) as archive_path, tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(adapter_dir)

        _ADAPTER_DIR = adapter_dir
        atexit.register(shutil.rmtree, adapter_dir, ignore_errors=True)
        return adapter_dir / "mediaremote-adapter.pl", adapter_dir / "MediaRemoteAdapter.framework"


def _normalize_adapter_payload(payload: dict[str, Any]) -> dict[str, Any]:
    captured_at = time.time()
    timestamp = _timestamp_value(payload.get("timestamp")) or captured_at
    return {
        "ok": True,
        "appBundleID": str(payload.get("bundleIdentifier") or ""),
        "title": str(payload.get("title") or ""),
        "artist": str(payload.get("artist") or ""),
        "album": str(payload.get("album") or ""),
        "duration": _float_value(payload.get("duration")),
        "elapsed": _float_value(payload.get("elapsedTime")),
        "playbackRate": _playback_rate(payload),
        "timestamp": timestamp,
        "capturedAt": captured_at,
    }


def _timestamp_value(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _playback_rate(payload: dict[str, Any]) -> float:
    if "playbackRate" in payload:
        return _float_value(payload["playbackRate"])
    if payload.get("playing") is True:
        return 1.0
    return 0.0
