from __future__ import annotations

import plistlib
from pathlib import Path

from obs_web_widgets import autostart


def test_generate_launch_agent_plist_points_to_cli(tmp_path) -> None:
    payload = plistlib.loads(
        autostart.generate_launch_agent_plist(
            "/opt/homebrew/bin/obs-web-widgets",
            host="127.0.0.1",
            port=17363,
            config_path=tmp_path / "config.json",
        )
    )

    assert payload["Label"] == "local.obs-web-widgets"
    assert payload["ProgramArguments"] == [
        "/opt/homebrew/bin/obs-web-widgets",
        "--host",
        "127.0.0.1",
        "--port",
        "17363",
        "--no-open",
    ]
    assert payload["EnvironmentVariables"]["OBS_WEB_WIDGETS_CONFIG"] == str(tmp_path / "config.json")
    assert payload["RunAtLoad"] is True


def test_install_and_uninstall_autostart(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    path = autostart.install_autostart("/opt/homebrew/bin/obs-web-widgets")

    assert path.exists()
    assert autostart.autostart_status()["enabled"] is True
    autostart.uninstall_autostart()
    assert autostart.autostart_status()["enabled"] is False
