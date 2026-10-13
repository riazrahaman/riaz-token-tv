# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

[docs/architecture.md](docs/architecture.md) explains the design, the technology stack and the
publish-loop guards in prose, with three interactive diagrams under `docs/diagrams/`.

Read [AGENTS.md](AGENTS.md) first — it holds the behavioural rules (credentials, publishing,
unknown-vs-zero) and the user-facing setup script. This file covers commands and architecture.

## Commands

```bash
pip install -e .                                  # Python 3.10+, Pillow is the only runtime dep
python3 -B -m unittest discover -s tests          # whole suite (~11s)
python3 -B -m unittest tests.test_live            # one module
python3 -B -m unittest tests.test_display_styles.QualityLadderTests          # one class
python3 -B -m unittest tests.test_display_styles.QualityLadderTests.test_a_busy_frame_steps_quality_down_until_it_fits
token-tv demo --out token-tv-demo                 # render every face from sample data, no clock or login
```

There is no linter and no CI workflow; the test suite is the only gate. `scripts/` holds one-off
render and build helpers, not part of the test run.

Verify a visual change by looking at the 240×240 output at real size, not by reading the code —
`token-tv demo` writes both a `.jpg` and an `@3x.png` per face.

## The pipeline

Nothing is ever flashed. A long-running host process renders a 240×240 image and uploads it to the
clock's **stock photo album**, then switches the clock to its photo theme. The clock only ever
displays a picture.

```
sources.py ──▶ usage.py ──▶ state.py ──▶ display.py / themes.py / space.py ──▶ device.py ──▶ clock
 read CLIs     normalise    UsageStore      render 240×240 JPEG or GIF        HTTP upload
```

- `cli.py` — subcommands `setup`, `start`, `doctor`, `demo`, `connect`, `run`, `stocks`.
- `live.py` — the real server: dashboard on 127.0.0.1:8787 plus the publish loop (`cycle`).
- `state.py` — `UsageStore`, the thread-safe snapshot cache the loop and the web handler share.
- `catalog.py` — the dashboard's face Gallery; reads `__version__`.
- `providers/` — `base.py` and `mock.py` only; real provider reads live in `sources.py`.
- `app.py`, and the `HTML` string at the bottom of `display.py`, are dead code. The live dashboard
  is `token_tv/web/index.html` + `app.js` + `style.css`.

## Things that will bite you

**`stocks.py` is a parallel universe.** It is a near-duplicate of `live.py` with its own config
loader, `write_json`, `load_original`, `mismatch_message` and `publish`. A change to the publish
path usually has to be made twice. Both write the same filenames (`tokentv.jpg` / `tokentv.gif`),
so two instances pointed at the *same* clock will fight over the file and over which saved
`display-original.json` is authoritative.

**One clock takes one owner.** To put both faces on a single clock, `stocks.json` drops
`device_url` and becomes a pure feeder that only writes its snapshot; `live.py` reads that file via
`stocks_snapshot` and stays the only process that backs up, uploads to and re-asserts the device.
Do not solve this by coordinating two publishers — the ownership split is what makes it correct.

**Config keys are allowlisted in two places.** `sources.load_config` and `stocks.STOCK_KEYS` both
reject unknown fields with `Unsupported configuration field`. Current extras: `countdown_seconds`,
`max_writes_per_hour`, `reassert_cycles`, `stocks_snapshot`, `render_seconds`. Adding a key without updating the
right allowlist makes the process exit at startup — which, under a supervisor, is a crash-loop.

**The version lives in three files.** `token_tv/__init__.py`, `pyproject.toml` and `package.json`.
`tests/test_catalog.py` asserts `__init__` and `pyproject` agree.

**Two device profiles, sniffed once.** `open_display()` picks `PhotoDisplay` (SD_PRO: has
`/theme/list` and `/photo/list`, no `/v.json`) or `UltraDisplay` (SmallTV-Ultra). Their `capture()`
return shapes differ — SD_PRO returns a `files` list, Ultra does not — so index into it defensively.
Detection happens once at startup, so a clock that was offline then stays misclassified.

**The clock stores every frame in flash, so writes are rationed.** Four mechanisms cooperate in
`live.cycle`, and changing one in isolation usually defeats the others:

- byte-identical frames are never re-uploaded;
- `display.quantise` buckets the countdown (`countdown_seconds`) so it does not change every minute;
- `device.may_write` *spaces* uploads (`max_writes_per_hour`) rather than spending a quota, so the
  frame is never more than one gap stale;
- `display.data_digest` fingerprints the readings while ignoring the countdown, so a genuinely
  changed reading bypasses the budget and uploads at once.

A throttled cycle must not call `preferences.delivered(style, False)` — the dashboard renders that
as "Clock upload failed. Please retry."

**Size limits are hard.** `device.LIMITS` caps `tokentv.jpg` at 60 000 bytes and `tokentv.gif` at
400 000; `upload()` raises above them. `display.encode_jpeg` steps quality 92→85→78 then chroma to
fit. Busy faces (`retro`, the stock watchlist) run close to the limit.

**Row counts are fixed by the layouts.** `display.overview_rows` returns `rows[:3]` (one account per
provider, three rows per face) and `stocks.MAX_SYMBOLS` is 4, matching the four rows
`stocks.render_image` draws. Raising either without redrawing the face silently hides data.

**`activate()` must be re-asserted.** It selects the photo theme and enables only our file. The loop
calls it periodically (`reassert_cycles`), not just once, because a reboot or a visit to the clock's
own web UI drops the clock back to its own theme while uploads keep succeeding into a photo nobody
is showing.

**Fonts differ by host.** `display.py` defaults to DejaVu paths that do not exist on macOS;
`font()` then falls back to `ImageFont.load_default()`, a tiny bitmap font. The `pixel` face draws
its own glyphs and the `themes.py` faces bundle fonts from `token_tv/web/`, so they are unaffected.
Set `"font"` in the config or `TOKEN_TV_FONT` to a real `.ttf`.

## Rendering

`render_page(snapshot, page, style, quantum=0, limit=0)` returns bytes; `render_frame` returns
`(bytes, meta)` with the chosen `quality`/`subsampling` and a `fits` flag for the receipt.
`quantum` and `limit` default to off, so `demo`, `scripts/` and the doc images render exactly as
before — only the publishers pass them.

`STYLES` lives in `display.py`. The `pixel` face is in `display.py`; `digital`, `neon`, `retro`,
`hud` and `gameboy` are in `themes.py` (`RENDERERS`); `space` is in `space.py` and returns animated
GIF bytes, so callers branch on a `GIF8` magic prefix to choose the filename. `combo` is in
`combo.py` and is the one face that reads `snapshot['stocks']`; when the market is closed or the
feed is stale it returns `render_pixel(snapshot)`, so it is deliberately byte-identical to `pixel`
then — a test asserts exactly that, rather than that every style is distinct. Adding a face is
documented in `docs/clock-faces.md`.

## Runtime state

Written to the `--state-dir` (`.runtime` by default; the installed services use
`~/.config/token-tv/state` and `state-stocks`):

- `display-original.json` — the clock's theme and photo selection as found, for `--restore-display`.
  It is only trustworthy if captured *before* TokenTV first took the clock over.
- `display-receipt.json` — last publish: uploads with sha256, plus `throttled`, `reasserted`,
  `data_changed`, `encoded`.
- `snapshot.json` — the current normalised reading. It is overwritten each cycle, so there is no
  history.
- `tokentv.jpg` / `tokentv.gif` — written only on a successful upload, so they record what the
  clock is actually showing rather than the latest render.

The repository checkout is imported directly by long-running publisher processes (`python3 -m
token_tv.cli run|stocks`). Editing files in place live-patches them, and `render_page` imports
`themes`/`space` lazily, so a partially-edited tree can raise inside the poll thread. Prefer a git
worktree for non-trivial work and restart the services deliberately.

## Hardware notes

The clock has **no authentication**: anyone on the LAN can change settings, delete photos or POST
firmware to `/update_ota`. Treat it as LAN-only. TokenTV must never call `/update_ota`, and never
`/photo/delete` on files it did not create. `GET /config` returns the Wi-Fi SSID and password in
plaintext — do not copy its output into files, issues or logs.
