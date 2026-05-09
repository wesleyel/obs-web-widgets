# obs-web-widgets

macOS-only local web widgets for OBS Browser Source.

The CLI starts one localhost service at `127.0.0.1:17363` and serves:

- `/config`: local configuration page
- `/lyrics`: simple lyrics overlay
- `/amll`: Apple Music-like Lyrics widget
- `/countdown`: countdown widget

Now Playing metadata is read through macOS `MediaRemote.framework` by running the bundled
`now_playing.swift` with the system `swift` command.

## Install

Development checkout:

```bash
uv sync
uv run obs-web-widgets --open
```

Homebrew distribution target:

```bash
brew install <tap>/obs-web-widgets
obs-web-widgets --open
```

For a Homebrew service, the formula should run `obs-web-widgets` directly and keep the service alive.
The in-app autostart switch writes `~/Library/LaunchAgents/local.obs-web-widgets.plist` pointing to
the currently running CLI executable.

## OBS URLs

Add Browser Source entries in OBS:

```text
http://127.0.0.1:17363/lyrics
http://127.0.0.1:17363/amll
http://127.0.0.1:17363/countdown
```

The pages use transparent-friendly dark overlays. AMLL loads
`@applemusic-like-lyrics/core@0.4.2` from `esm.sh`, so that OBS source needs network access.

## Configuration

Configuration is stored at:

```text
~/Library/Application Support/obs-web-widgets/config.json
```

Fields:

- `lyricOffset`: lyrics offset in seconds
- `nowPlayingBundleID`: target Now Playing app bundle id, default `com.netease.163music`
- `pollInterval`: Now Playing polling interval in seconds
- `countdownName`: countdown title
- `countdownTarget`: `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`

CLI options:

```bash
obs-web-widgets --host 127.0.0.1 --port 17363
obs-web-widgets --config ~/Library/Application\ Support/obs-web-widgets/config.json
obs-web-widgets --open
```

## API

Supported endpoints:

```text
GET  /api/state
GET  /api/countdown
GET  /api/amll/lines
GET  /api/config
POST /api/config
GET  /api/autostart
POST /api/autostart
GET  /events
```

`POST /api/config` accepts JSON using the same field names as the config file.
`POST /api/autostart` accepts `{"enabled": true}` or `{"enabled": false}`.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run pytest
uv build
```

CI runs lint, tests, and Python package build on macOS. Tagged builds upload the Python
distribution artifacts from `dist/`; no `.app`, PyInstaller bundle, code signing, or notarization
is part of this project.
