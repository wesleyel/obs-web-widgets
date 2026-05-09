from __future__ import annotations

import argparse
import shutil
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .config import DEFAULT_HOST, DEFAULT_PORT, default_config_path
from .web import WidgetRuntime, create_http_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run local OBS web widgets for lyrics, AMLL, and countdown.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host, default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port, default: {DEFAULT_PORT}")
    parser.add_argument("--config", type=Path, default=default_config_path(), help="Path to config JSON")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="Open the config page after startup")
    parser.add_argument("--no-open", action="store_false", dest="open_browser", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"obs-web-widgets {__version__}")
    args = parser.parse_args(argv)

    command_path = resolve_command_path()
    runtime = WidgetRuntime(
        host=args.host,
        port=args.port,
        config_path=args.config.expanduser(),
        command_path=command_path,
    )
    server = create_http_server(runtime)
    runtime.start_polling()

    base_url = f"http://{runtime.host}:{runtime.port}"
    print(f"Config: {base_url}/config")
    print(f"OBS lyrics URL: {base_url}/lyrics")
    print(f"OBS AMLL URL: {base_url}/amll")
    print(f"OBS countdown URL: {base_url}/countdown")
    print(f"Config file: {runtime.config_path}")
    print("Press Ctrl-C to stop.")

    if args.open_browser:
        webbrowser.open(f"{base_url}/config")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def resolve_command_path() -> str:
    executable = Path(sys.argv[0])
    if executable.exists():
        return str(executable.resolve())

    from_path = shutil.which("obs-web-widgets")
    if from_path:
        return str(Path(from_path).resolve())

    return sys.executable
