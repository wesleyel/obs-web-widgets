# OBS 网易云歌词 Web Endpoint

这个目录是一套本机小服务：

1. `now_playing.swift` 读取 macOS 系统级 Now Playing 元数据，拿到网易云音乐当前播放的歌名、歌手、时长、进度。
2. `server.py` 用“歌名 + 歌手”搜索网易云歌曲 ID，拉取 LRC 歌词。
3. OBS 添加 Browser Source，加载本地页面，页面通过 SSE 实时收到当前歌词行。

## 启动

```bash
uv run python server.py
```

服务会直接调用系统 `swift now_playing.swift` 读取 Now Playing。这里刻意不编译成自定义二进制，因为当前 macOS 上自定义 `swiftc` 二进制读取 `MediaRemote.framework` 会返回空数据，而 Apple 自带 `swift` 解释执行可以正常返回网易云播放信息。

## OBS 设置

在 OBS 里添加：

- 来源类型：Browser
- URL：`http://127.0.0.1:17363/`
- Width：`1920`
- Height：`360`
- 背景：页面本身是透明背景

如果你只想调试接口：

```bash
curl http://127.0.0.1:17363/state
```

## AMLL 适配

AMLL 相关 endpoint：

```bash
curl http://127.0.0.1:17363/amll/manifest.json
curl http://127.0.0.1:17363/amll/lyrics.lrc
curl http://127.0.0.1:17363/amll/lyrics.ttml
curl http://127.0.0.1:17363/amll/lyrics.yrc
curl http://127.0.0.1:17363/amll/translation.lrc
curl http://127.0.0.1:17363/amll/roman.lrc
```

- `manifest.json`：当前曲目、网易云 song ID、可用歌词格式 URL。
- `lyrics.lrc`：AMLL 支持的普通 LRC，保留网易云原始 LRC 并补 `ti/ar/al/length/netease` 元数据。
- `lyrics.ttml`：按 AMLL TTML 约定生成的行级 TTML，`itunes:timing="Line"`。
- `lyrics.yrc`：网易云返回 YRC 逐字歌词时才可用；当前歌曲没有 YRC 时返回 404。
- `translation.lrc` / `roman.lrc`：网易云返回翻译或音译时才可用；TTML 会自动把这些挂到 `x-translation` / `x-roman`。

## 可调环境变量

```bash
OBS_LYRICS_PORT=17363 uv run python server.py
OBS_LYRICS_OFFSET=0.3 uv run python server.py
OBS_LYRICS_SOURCE_BUNDLE=com.netease.163music uv run python server.py
```

- `OBS_LYRICS_OFFSET`：歌词整体偏移，单位秒。正数让歌词提前，负数让歌词延后。
- `OBS_LYRICS_SOURCE_BUNDLE`：默认只接受网易云 `com.netease.163music` 的 Now Playing 数据。

## 兼容性变更

- 这套方案只支持 macOS，因为当前播放信息来自 macOS 私有 `MediaRemote.framework`。
- 不接 obs-websocket；OBS 端只需要 Browser Source。
- 网易云歌词接口是非官方接口，接口变更或版权限制时可能只能显示“暂无歌词”。
- AMLL 的 TTML 适配是行级 TTML；只有网易云接口实际返回 YRC 时才会提供逐字歌词。
