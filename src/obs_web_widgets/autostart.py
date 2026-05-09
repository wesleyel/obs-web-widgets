from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

from .config import APP_NAME, DEFAULT_HOST, DEFAULT_PORT

LAUNCH_AGENT_LABEL = "local.obs-web-widgets"


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launch_agent_path() -> Path:
    return launch_agents_dir() / f"{LAUNCH_AGENT_LABEL}.plist"


def logs_dir() -> Path:
    return Path.home() / "Library" / "Logs" / APP_NAME


def service_log_path() -> Path:
    return logs_dir() / "service.log"


def generate_launch_agent_plist(
    command_path: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: Path | None = None,
) -> bytes:
    arguments = [command_path, "--host", host, "--port", str(port), "--no-open"]
    environment = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"),
    }
    if config_path is not None:
        environment["OBS_WEB_WIDGETS_CONFIG"] = str(config_path)

    payload: dict[str, Any] = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": arguments,
        "EnvironmentVariables": environment,
        "StandardOutPath": str(service_log_path()),
        "StandardErrorPath": str(service_log_path()),
        "RunAtLoad": True,
        "KeepAlive": {"Crashed": True},
    }
    return plistlib.dumps(payload, sort_keys=False)


def install_autostart(
    command_path: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: Path | None = None,
) -> Path:
    launch_agents_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    path = launch_agent_path()
    path.write_bytes(generate_launch_agent_plist(command_path, host=host, port=port, config_path=config_path))
    return path


def uninstall_autostart() -> None:
    try:
        launch_agent_path().unlink()
    except FileNotFoundError:
        return


def autostart_status() -> dict[str, Any]:
    path = launch_agent_path()
    return {
        "ok": True,
        "enabled": path.exists(),
        "label": LAUNCH_AGENT_LABEL,
        "plistPath": str(path),
        "logPath": str(service_log_path()),
    }
