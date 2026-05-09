from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from importlib.resources import files
from typing import Any


def now_playing_swift_path() -> str:
    return str(files("obs_web_widgets.resources").joinpath("now_playing.swift"))


def read_now_playing(command_override: str | None = None) -> dict[str, Any]:
    command_override = command_override if command_override is not None else os.environ.get("NOW_PLAYING_COMMAND", "")
    if command_override:
        command = shlex.split(command_override)
    else:
        swift = shutil.which("swift")
        if swift is None:
            raise RuntimeError("找不到 swift，无法读取 macOS Now Playing")
        command = [swift, now_playing_swift_path()]

    raw = subprocess.check_output(command, text=True, timeout=3)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "Now Playing 读取失败"))
    return payload
