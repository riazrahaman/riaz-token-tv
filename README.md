<div align="center">

# 📺 TokenTV

**Your AI quota limits, follow-the-sun global stock watchlists, and focus countdown on a $5 Wi-Fi desk clock.**

Host-side composited 240×240 pixel art, live session & weekly quotas (Claude, ChatGPT, Ollama Cloud, Antigravity), follow-the-sun market telemetry (US & India), Apple Calendar integration, and a monumental 2028 focus countdown with hourly wisdom — streaming seamlessly to a tiny ESP8266/ST7789 desk clock with **zero firmware flashing**.

[![Release](https://img.shields.io/github/v/release/riazrahaman/riaz-token-tv?label=release)](https://github.com/riazrahaman/riaz-token-tv/releases/latest)
[![Tests](https://img.shields.io/badge/tests-265%20passing-3fb950)](#development--verification)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/github/license/riazrahaman/riaz-token-tv?color=blue)](LICENSE)
[![Deploy](https://img.shields.io/badge/deploy-Railway-8b5cf6)](https://token-tv.riazrahaman.com)
[![Featured on Hackaday](https://img.shields.io/badge/featured%20on-Hackaday-yellow)](https://hackaday.com/2026/10/06/smalltv-hacking-with-surprisingly-little-fuss/)

[Quickstart](#quickstart) ·
[Project Layout](#project-layout) ·
[How It Works: The Two Channels](#how-it-works-the-two-factory-channels--the-memory-wall) ·
[Clock Faces](#clock-faces--display-modes) ·
[Stock Watchlist & Markets](#stock-watchlist--follow-the-sun-markets) ·
[2028 Focus Countdown](#2028-focus-countdown--hourly-wisdom) ·
[AI Providers & Auth](#ai-providers--auth-architecture) ·
[Mobile Widgets API](#mobile-widgets--rest-api) ·
[Deployment](#deployment) ·
[Verification](#development--verification) ·
[한국어](README.ko.md)

<p align="center">
  <img src="docs/images/real-clock.jpg" alt="TokenTV on a real tiny desk clock" width="48%">
  <img src="docs/images/clock-faces.png" alt="Six clock faces (sample data)" width="48%">
</p>

**The clock thinks it is cycling through holiday photos.** The host computer runs all math, token accounting, quote scraping, and PIL rendering — uploading animated GIFs and progressive JPEGs over local Wi-Fi HTTP.

<sub>Zero firmware flashing · Pure HTTP photo mode uploads · All compositing on host · Tested on $5 ESP8266/ST7789 IPS clocks</sub>

</div>

> [!TIP]
> **Try it without a clock or login:** Run `npx github:riazrahaman/riaz-token-tv demo` (or `uvx --from git+https://github.com/riazrahaman/riaz-token-tv token-tv demo`).
> **Setting up with an AI agent?** Point Claude Code, Codex, or Gemini CLI at this repository and say *"Set up TokenTV for me."* It follows [AGENTS.md](AGENTS.md).

---

## Project Layout

```
.
├── token_tv/                 Core Python package (CLI, live runner, and renderers)
│   ├── cli.py                Command line dispatcher (demo, setup, run, stocks, calendar, countdown)
│   ├── live.py               HTTP server (127.0.0.1:8787), dashboard, and clock publish loop
│   ├── display.py            240×240 pixel face, shared typography, gauges, and STYLES registry
│   ├── usage_gif.py          Multi-frame animated GIF generator for live quotas and shimmering bars
│   ├── countdown.py          2028 countdown face, hourly wisdom proverbs, fixed-cell digits & palette
│   ├── widgets.py            Normalized REST payloads for Android Jetpack Glance and iOS WidgetKit
│   ├── combo_stocks.py       Follow-the-sun stock board and breathing animated market GIF renderer
│   ├── stocks.py             Market data feeder: Finnhub (US) & Yahoo Finance v8 (India)
│   ├── calendar.py           Feeder polling Apple Calendar for next appointments
│   ├── sources.py            Provider usage normalizer: Claude, ChatGPT, Ollama Cloud, Antigravity
│   ├── usage.py              Telemetry window calculations (session & weekly percentages, resets)
│   ├── device.py             ESP8266 / SD_PRO clock HTTP photo uploader and album rotator
│   └── web/                  Bundled SIL OFL fonts (Manrope, IBM Plex, Silkscreen, VT323)
├── site/                     Web showcase and static live demo
│   └── tokentv/              Node.js presentation server deployed to Railway
├── docs/                     System specifications, operator guides, and deep-dives
│   ├── setup.md              Step-by-step setup and credential guide
│   ├── architecture.md       Full system architecture, data pipelines, and code flow
│   ├── clock-faces.md        Guide to designing and contributing custom clock faces
│   ├── stock-photo-display.md The HTTP photo mode protocol and flash endurance
│   ├── hardware-compatibility.md Verified compatible ESP8266/ESP32 clocks
│   └── apple-watch.md        Clock complication setup for Apple Watch
├── examples/                 Standalone reference faces (e.g. 4-color Game Boy face)
├── tests/                    265 unit & regression tests with synthetic time/network doubles
├── pyproject.toml            Python package specification (Pillow>=10.2,<13)
├── package.json              NPM launcher package specification
├── railway.json              Railway production deployment blueprint (Railpack + node server.js)
├── Makefile                  Test execution and documentation render targets
└── LICENSE                   MIT License
```

---

## Quickstart

### 1. Zero-Install Demo (No Hardware Needed)

Run the built-in gallery with synthetic telemetry:

```bash
# Via npx (Node.js)
npx github:riazrahaman/riaz-token-tv demo

# Or via uvx (Python uv)
uvx --from git+https://github.com/riazrahaman/riaz-token-tv token-tv demo
```

This renders every clock face into `token-tv-demo/` at real 240×240 resolution.

### 2. Configure Your Accounts & Clock

Install TokenTV locally via pip or pipx:

```bash
pipx install git+https://github.com/riazrahaman/riaz-token-tv
```

Run the interactive setup wizard:

```bash
token-tv setup
```

The wizard prompts for:
- Your desk clock's IP address (e.g. `http://192.168.1.157`).
- Account aliases and provider identities (Anthropic Claude, OpenAI ChatGPT, Ollama Cloud, Antigravity).

Verify your logins and credentials:

```bash
token-tv doctor --live
```

`doctor` queries each provider in real time, confirming that credentials resolve and quotas are readable.

### 3. Launch the Dashboard & Drive the Clock

```bash
token-tv run
```

This starts the local web dashboard at **`http://127.0.0.1:8787`** and begins driving the clock over Wi-Fi.

- **Open Market Hours**: Alternates animated stock watchlists (breathing arrows) with AI quota bars (20 s / 10 s loop).
- **Off-Hours / Weekends**: Displays the full-size AI usage rest face, next appointment countdown, and periodic moon cameos.
- **Web UI**: Toggle faces, switch themes (light/dark/auto), edit stock tickers, and inspect live quota percentages.

---

## How It Works: The Two Factory Channels & The Memory Wall

The clock's factory firmware divides its visual capability into two distinct operational channels:

<p align="center">
  <img src="docs/assets/two-channels-illustration.png" width="800" alt="The Two Factory Channels: Photo Mode (TokenTV) vs Clock Mode (Firmware)" />
</p>

1. **Channel 1 (Photo Mode — what TokenTV uses)**:
   The host computer renders high-resolution 240×240 multi-frame animated GIFs or progressive JPEGs with AI quotas, follow-the-sun watchlist telemetry, pixel mascots, glowing gauges, and Apple Calendar appointments, uploading them via HTTP `POST /upload`.
2. **Channel 2 (Clock / Weather Mode)**:
   Built-in firmware NTP digital clock and OpenWeatherMap temperature with a tiny 80×80 animated GIF sticker slot in the corner (the factory floating astronaut).
3. **The ~40 KB Memory Wall**:
   The clock's ESP8266 microcontroller has only ~40–50 KB of usable RAM (a full 240×240 16-bit RGB565 framebuffer requires 115 KB). Because the firmware cannot composite graphics in memory, it can only run **Channel 1** OR **Channel 2**.
4. **Host-Side Compositing**:
   TokenTV completely overcomes this hardware limitation by pre-compositing animated walking mascots, starfields, gauges, and live market quotes on the host computer into unified animated GIFs, playing smoothly without touching the firmware or risking device bricking.

---

## Clock Faces & Display Modes

TokenTV ships with a diverse suite of 240×240 clock faces:

| Face | Type | Description |
|---|---|---|
| **`pixel`** | Animated GIF | Default face. Brand-accurate animated pixel mascots (Claude sunburst, Ollama llama, Antigravity sparkle), 2-line session and weekly quota bars, and shimmering gauges. |
| **`combo`** | Animated GIF | Follow-the-sun market quotes (breathing ticker arrows) top half, AI usage quotas bottom half, and next calendar appointment. |
| **`countdown`** | Animated GIF | Monumental 2028 focus countdown, Manrope ExtraBold fixed-cell clock digits, 1 Hz blinking colon, and hourly rotating wisdom proverbs on pure `#000000` black. |
| **`digital`** | Static JPEG | Crisp high-contrast segmented typography for dark desks. |
| **`neon`** | Static JPEG | Cyberpunk glow palette with vibrant gradient telemetry bars. |
| **`retro`** | Static JPEG | Warm 1980s monochrome phosphor CRT terminal aesthetic. |
| **`hud`** | Static JPEG | Aerospace avionics telemetry interface with high-density markers. |
| **`matrix`** | Static JPEG | Flowing digital glyph matrix with green phosphor indicators. |
| **`space`** | Animated GIF | Deep space starfield with pulsing AI quota status readouts. |

<p align="center">
  <a href="examples/gameboy">
    <img src="examples/gameboy/comparison.png" width="640" alt="Change a face: original pixel style and a four-colour Game Boy example">
  </a>
</p>

> [!NOTE]
> Want to create your own face? See [docs/clock-faces.md](docs/clock-faces.md) or explore the standalone [Game Boy face example](examples/gameboy).

---

## Stock Watchlist & Follow-the-Sun Markets

`token-tv stocks` monitors global equity markets and pushes a real-time watchlist to the clock:

```bash
token-tv stocks
```

### Follow-the-Sun Market Routing
- **US Equities**: Polled from Finnhub during US market hours (09:30–16:00 ET, Mon–Fri). Requires a free API key stored in macOS Keychain (`token-tv-finnhub`) or `FINNHUB_API_KEY`.
- **Indian Equities (NSE / BSE)**: Polled from Yahoo Finance's public v8 chart API (zero-config, no API key required) during Indian market hours (09:15–15:30 IST, Mon–Fri). Indices like `^NSEI` and `^BSESN` automatically format as `NIFTY` and `SENSEX`.
- **Scheduled Sleep**: Outside market sessions, the feeder sleeps via `next_market_transition` and polls nothing, saving network bandwidth and compute.
- **Reopened Market Moon Cameo**: Overnight and during weekends, the clock rests on the AI usage screen, waking once every 5 minutes to cameo a crescent moon and the next market opening bell ("REOPENS 09:30 TOMORROW NSE").

---

## 2028 Focus Countdown & Hourly Wisdom

A dedicated full-screen focus clock targeting **February 1, 2028**, alternating with an hourly rotating collection of 40 curated English wisdom proverbs on discipline, patience, and perseverance:

```bash
token-tv countdown --device-url http://192.168.1.157
```

<div align="center">
  <img src="review-renders/shot-black-face-sheet.png" alt="Pure black 2028 countdown and hourly wisdom clock" width="60%">
</div>

- **Pure `#000000` True Black**: Hairline rule separator with zero distracting badges or cards.
- **Fixed-Cell Clock Typography**: Manrope ExtraBold 58px digits rendered in fixed column slots so digits never cause horizontal jitter as glyph widths change.
- **1 Hz Blinking Colon**: Smoothly ghosts to `#1c1c1c` at 1 second intervals.
- **Letterspaced Date**: Compact header displaying day of week in amber and grey date (`FRI · 23 OCT`) without redundant year clutter.
- **Monumental 104px Numeral**: Centered countdown numeral with dynamic size stepping.
- **Adaptive Proverb Band**: Dynamically re-wraps and steps font size (21–22px) to ensure every quote fits the hero band comfortably.
- **97-Color Palette Flattening**: Pre-quantizes frames to a fixed palette to bypass Pillow's median-cut fallback, ensuring the background never dithers away from true black on ST7789 IPS panels.

---

## AI Providers & Auth Architecture

TokenTV collects usage metrics from local CLI sessions and APIs without storing plaintext secrets in config files:

| Provider | Telemetry Extracted | Credential Discovery |
|---|---|---|
| **Anthropic Claude** | 5H session quota %, 7-day weekly quota %, reset countdowns | macOS Keychain (`Claude Code-credentials`) or `~/.claude/.credentials.json` |
| **OpenAI ChatGPT** | 3H/5H message limits, hourly spend, weekly quotas | Environment (`OPENAI_API_KEY`), macOS Keychain (`token-tv-chatgpt`), or local `usage.json` |
| **Ollama Cloud** | Monthly/weekly budget usage, active plan allowance | Environment (`OLLAMA_API_KEY`) or macOS Keychain (`token-tv-ollama`) |
| **Antigravity** | Execution budget, session duration, activity windows | macOS Keychain or application data directory |

---

## Mobile Widgets & REST API

The local daemon (`token-tv run`) provides structured JSON endpoints for home dashboards, Apple Watch, and mobile widgets:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/widgets.json` (or `/api/widgets`) | Structured telemetry broken into session and weekly windows with progress % and formatted reset times for Android Jetpack Glance and iOS WidgetKit. |
| `GET` | `/watch.json` | Lightweight payload optimized for Apple Watch complications and watchOS background refresh. |
| `GET` | `/snapshot` | Full raw JSON snapshot containing all configured account metrics, provider states, and market quotes. |
| `GET` | `/frame/:id.jpg` | Live rendered JPEG frame currently displayed on album slot `:id` (`?live=1` renders current live state). |
| `GET` | `/health` | Server liveness probe returning status `ok` and last update timestamp. |

---

## Deployment

### Railway Deployment (Web Showcase)

The repository includes a production Railpack blueprint in [`railway.json`](railway.json) that hosts the TokenTV documentation and interactive simulator at **<https://token-tv.riazrahaman.com>**:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "node server.js",
    "healthcheckPath": "/",
    "healthcheckTimeout": 120,
    "restartPolicyType": "ALWAYS"
  }
}
```

> [!IMPORTANT]
> **Railway Crash Prevention**: Railway's default start runner (`npm start`) spawns a subshell that intercepts `SIGTERM` and exits non-zero (`npm error signal SIGTERM`), causing Railway to record every container handover as a crash. TokenTV sets `"startCommand": "node server.js"` directly as PID 1, and `server.js` calls `server.closeIdleConnections()` with an immediate fallback to guarantee a zero-exit clean shutdown.

### Background Daemons via macOS LaunchAgents

To run TokenTV permanently in the background on a Mac mini or desktop:

Create `~/Library/LaunchAgents/com.tokentv.countdown.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tokentv.countdown</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/token-tv</string>
    <string>countdown</string>
    <string>--device-url</string>
    <string>http://192.168.1.157</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/tokentv-countdown.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/tokentv-countdown.err</string>
</dict>
</plist>
```

Load the service:

```bash
launchctl load ~/Library/LaunchAgents/com.tokentv.countdown.plist
```

---

## Development & Verification

TokenTV maintains 100% deterministic test coverage with mock time travel and synthetic HTTP doubles:

```bash
# Run full unit & regression test suite (265 tests)
python3 -B -m unittest discover -s tests

# Generate fresh clock face gallery snapshots
python3 scripts/render_clock_faces.py

# Render usage example previews
python3 scripts/render_usage_examples.py

# Build static demo site
python3 scripts/build_demo.py --out site
```

---

## License & Acknowledgements

- **License**: MIT License. See [LICENSE](LICENSE) for full text.
- **Hardware**: Compatible with GeekMagic / SmallTV / SD_PRO ESP8266 and ESP32 240×240 Wi-Fi desk clocks.
- **Typography**: Bundled fonts include SIL Open Font License fonts: *Manrope*, *IBM Plex*, *Silkscreen*, and *VT323*.
- **Origin**: Forked from [click6067-ship-it/token-tv](https://github.com/click6067-ship-it/token-tv) with extensive extensions for animated dual-line quotas, multi-market follow-the-sun stock telemetry, Apple Calendar appointments, 2028 focus countdown, and mobile widget APIs.
