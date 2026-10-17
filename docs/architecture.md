# Architecture

How TokenTV is put together, what each part owns, and why the publish path looks the way it does.
Three interactive diagrams accompany this page; open them from a checkout:

| Diagram | Question it answers |
| --- | --- |
| [System architecture](diagrams/tokentv-architecture.html) | What are the parts, and which of them talks to the clock? |
| [Render and publish path](diagrams/tokentv-dataflow.html) | How does a provider reading become a picture on a clock? |
| [One publish cycle](diagrams/tokentv-sequence.html) | What decides whether this cycle writes to flash at all? |

Each diagram cites the source lines it was built from, so a claim can be checked against the code
rather than taken on trust. They were generated with [Archify](https://github.com/tt-a1i/archify)
and pass its validation, artifact, and real-browser checks.

## The idea

The clock is a ~$5 GeekMagic-style 240×240 Wi-Fi desk clock running vendor firmware. TokenTV never
flashes it and never replaces that firmware. Instead it uses a feature the firmware already has: a
photo album with an HTTP upload endpoint, and a theme that displays a photo full-screen.

So a computer that stays on reads your usage, draws a 240×240 picture, uploads it to the album, and
selects the photo theme. The clock believes it is showing a holiday snap. Everything clever happens
on the host; the device stays stock, which is what makes the project safe to run on hardware with
no source and no warranty.

### The Two Factory Channels and Host-Side Compositing

<p align="center">
  <img src="assets/two-channels-illustration.png" width="800" alt="The Two Factory Channels: Photo Mode (TokenTV) vs Clock Mode (Firmware)" />
</p>

The factory firmware divides its display capabilities into two mutually exclusive channels:
- **Channel 1 (Photo Mode — what TokenTV uses)**: A full-screen 240×240 canvas fed by HTTP uploads into wear-levelled LittleFS.
- **Channel 2 (Clock / Weather Mode)**: A fixed firmware layout with digital time, NTP clock sync, OpenWeatherMap weather, and an 80×80 animated GIF sticker slot in the corner (the factory floating astronaut).

While Channel 2 contains an undocumented slot where the factory astronaut can be replaced with a custom 80×80 GIF, the ESP8266's memory wall (~40 KB usable heap vs 115 KB needed for a 240×240 16-bit framebuffer) makes hardware layer compositing physically impossible. The clock cannot blend Channel 2's GIF slot over Channel 1's telemetry canvas.

TokenTV resolves this by handling all compositing on the host computer:
1. **Dynamic Multi-Layer Rendering**: Python + Pillow renders animated pixel mascots, starfields, glowing gauges, and stock quotes.
2. **Pre-Composited Animated Stream**: Frames are compiled into a lightweight animated GIF (<200 KB).
3. **Channel 1 Playback**: The clock receives the finished composite and displays rich animations without touching the firmware.


## Technology stack

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.10+ | Already present on the machines that run the provider CLIs. |
| Imaging | Pillow | The only runtime dependency. Every face is drawn in code, not composited from assets. |
| HTTP (out) | `urllib.request` from the standard library | Both the provider APIs and the clock speak plain HTTP; a client library would earn nothing. |
| HTTP (in) | `http.server.ThreadingHTTPServer` | The dashboard is a local control panel, not a public service. |
| Concurrency | `threading` + `ThreadPoolExecutor` | One poll thread, one HTTP server, up to four parallel account reads. |
| Dashboard | Hand-written HTML, CSS and ES modules in `token_tv/web/` | No build step, so the package installs and runs unchanged. |
| Packaging | setuptools via `pyproject.toml`, plus a thin `npx` shim in `bin/` | `pip install -e .` for users with Python; `npx` for a zero-install demo. |
| Tests | `unittest` from the standard library | No test runner to install; `python3 -B -m unittest discover -s tests`. |
| Credentials | The provider's own login, read in place | TokenTV never asks for a password and never stores a token. |
| Market Data | Finnhub (US) & Yahoo Finance (India) | Finnhub free tier via Keychain for US equities; zero-config public v8 chart API for Indian NSE/BSE equities. |

There is no database, no message queue, no container, and no cloud component. The persistent state
is a handful of JSON files and the last image that was uploaded.

## The parts

`cli.py` is the entry point and owns the subcommands (`setup`, `start`, `doctor`, `demo`,
`connect`, `run`, `stocks`). `sources.py` reads each provider through its own login — a CLI
subprocess for Antigravity, HTTPS for Anthropic and Ollama — and `usage.py` normalises whatever
comes back into a common window shape of `label`, `used_percent` and `resets_at`. `state.py` holds
the result in a thread-safe `UsageStore` that the poll loop and the web handler share.

`display.py` owns the snapshot-to-picture step: the `pixel` face, the shared drawing helpers, the
`STYLES` list, and `render_frame`. The other still faces live in `themes.py`, `space.py` produces
an animated GIF instead of a JPEG, and `combo.py` draws AI usage and a stock watchlist on one
frame. `device.py` is the only module that talks to the clock;
it implements two firmware profiles and refuses to upload an oversized file.

`live.py` ties it together: it runs the dashboard on `127.0.0.1:8787` and the publish loop that
calls everything above. `stocks.py` is a feeder and publisher for share watchlists with follow-the-sun
multi-market routing: it polls US equities via Finnhub and Indian equities/indices via Yahoo Finance
v8 chart API based on active market hours, exporting normalized snapshots for combo faces and optionally
driving a dedicated stock clock. Both markets' regular hours are known (09:30–16:00 ET,
09:15–15:30 IST, weekdays), so outside a session the feeder polls nothing: it sleeps on the
schedule via `next_market_transition`, re-publishes the kept board on 5-minute idle wakes to
keep the photo theme asserted, and the next open boundary is the trigger that resumes the polls.
Holidays are ruled out by the Finnhub market status the watchlist fetches during sessions; the
movers screener has no calendar, so a holiday board freezes and goes `OLD` instead.

A third feeder, `token-tv calendar`, reads Apple Calendar through osascript — scoped to one
calendar (`riazrahaman`), timed events only, all-day skipped — and writes `{updated_at, title,
start_iso}` to its own snapshot; the `calendar_snapshot` key joins both config allowlists
(`sources.load_config` and `STOCK_KEYS`), the publisher merges it under `snapshot['calendar']`,
and the combo faces and the closed-market rest draw the next event with a quantised countdown
(`in 2h14m`). A failed or slow query keeps the last-good snapshot; the event digest folds only
`(title, start_iso)`, so a changed appointment reaches the clock even inside the write budget,
while the moon cameo's aliveness gate reads the stocks feeder's `published_at` wake stamp.

### System Architecture & Code Flow

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           HOST COMPUTER (Python 3.10+)                          │
│                                                                                 │
│   [Provider Logins / APIs]                                                      │
│    Anthropic · Ollama · Antigravity (Usage)                                     │
│    Finnhub (US Equities) · Yahoo Finance v8 (Indian Equities)                   │
│                                                                                 │
│         │                                        │                              │
│         ▼                                        ▼                              │
│    sources.py (Usage)                       stocks.py (Feeder)                  │
│    Normalizes quotas, windows               Follow-the-Sun Market Evaluator     │
│         │                                        │                              │
│         ▼                                        ▼                              │
│    state.py (UsageStore)                    snapshot.json (State file)          │
│         │                                        │                              │
│         └───────────────────┬────────────────────┘                              │
│                             ▼                                                   │
│                     combo.py / display.py                                       │
│          Multi-layer Pillow rendering (240×240)                                 │
│          Adaptive INR font scaling, exchange badges                             │
│                             │                                                   │
│                             ▼                                                   │
│                         live.py                                                 │
│          Flash-write budgeting & byte deduplication                             │
│                             │                                                   │
│                             ▼                                                   │
│                         device.py                                               │
│          HTTP POST /photo/upload (SD_PRO profile)                               │
└─────────────────────────────┼───────────────────────────────────────────────────┘
                              │ LAN only (HTTP)
                              ▼
               ┌──────────────────────────────┐
               │   $5 ESP8266 Wi-Fi Clock     │
               │   Channel 1: Photo Album     │
               │   Stock LittleFS (No Flash)  │
               └──────────────────────────────┘
```

The execution flow per cycle:
1. **Market Evaluation & Fetch**: `select_active_market()` checks current time against exchange trading hours:
   - *Indian Market (09:15–15:30 IST / 03:45–10:00 UTC)*: `fetch_yahoo_quote()` polls Yahoo Finance v8 chart API (`query1.finance.yahoo.com/v8/finance/chart`) with desktop user-agent and zero API key. Strips `.NS`/`.BO` suffixes and aliases indices (`^NSEI` → `NIFTY`, `^BSESN` → `SENSEX`).
   - *US Market (09:30–16:00 ET / 13:30–20:00 UTC)*: `fetch_quote()` polls Finnhub via Keychain-stored API key.
   - *Inter-Market Closed Periods*: Feeder marks session closed, yielding screen real estate back to full-size AI usage.
2. **Atomic State Serialization**: Normalized telemetry is written to `state-stocks/snapshot.json` and appended to `history.jsonl`.
3. **Host-Side Compositing**: `combo.py` loads `UsageStore` and `snapshot.json`. If market is open, it renders the split face with active exchange badges (`NSE` or `US`) and adaptive scale-2 font scaling for INR prices $\ge 7$ chars.
4. **Flash Preservation & Delivery**: `live.py` applies deduplication and spaced write budgets before pushing frames to the clock's LittleFS photo album endpoint.

## Why the publish loop is careful

The clock stores every uploaded frame in flash. A naive loop that uploads on each poll would rewrite
that flash a few hundred times a day, and flash wears out.

The obvious guard — skip the upload when the rendered bytes are identical — turns out not to be
enough, and the reason is worth stating plainly because it is easy to get wrong. When a short usage
window leads, the face shows a countdown in minutes. That countdown changes on *every* poll, so
every frame differs and the byte comparison never matches. Measured at the default five-minute
poll, that is twelve distinct frames an hour, every hour you are working.

Four mechanisms cooperate, and changing one in isolation usually defeats the others:

1. **Byte dedupe.** An identical frame is never rewritten.
2. **`quantise`** rounds the countdown up to whole buckets (`countdown_seconds`, default 900), so
   the face changes when the remaining time crosses a bucket edge rather than every minute. On its
   own this takes a steady hour from twelve writes to four.
3. **`may_write`** spaces uploads (`max_writes_per_hour`). It spaces rather than spending a quota
   deliberately: a quota lets a busy hour burn its whole allowance in the first twenty-five minutes
   and then freeze the clock on a stale frame, which is the opposite of useful.
4. **`data_digest`** fingerprints the readings themselves and ignores the countdown. When a
   percentage or a status actually changes, the frame bypasses the budget and goes out at once.

The fourth exists because a write budget that defers real news is worse than no budget. The cap is
there to absorb idle churn, not to withhold a number you are waiting for.

Two further guards protect delivery rather than flash. `activate()` is re-asserted every
`reassert_cycles`, because it used to run only when the output file name changed — that is, once —
and a clock that reboots or is touched through its own web UI silently reverts to its own theme
while uploads keep succeeding into a photo nobody is showing. And `encode_jpeg` steps quality down
(92 → 85 → 78, then chroma subsampling) until the frame fits the firmware's size limit, because a
busy face can otherwise exceed it and stop updating with no visible error.

## Both faces on one clock

A 240×240 panel is about an inch and a half across, so seven rows of detail is the most it will
take. The `combo` face therefore shows less per row than either dedicated face: no provider
mascots, no reset countdown. It keeps the window label, because 75% of a five-hour window and 75%
of a week are different facts and the percentage alone would mislead.

It is also conditional and follow-the-sun aware. The stock feeder evaluates market operating hours:
- **NSE/BSE (India)**: 09:15–15:30 IST (03:45–10:00 UTC) via Yahoo Finance v8 chart API (zero-config, no key).
- **US Equities**: 09:30–16:00 ET (13:30–20:00 UTC) via Finnhub.
- **Inter-Market Closed Periods**: Outside active sessions, the feeder marks the session closed — and, since the scheduled-sleep work, still names the next open (`market.next_open_at` / `next_open_exchange`) on every closed publish. The clock rests on a dedicated closed night face when that schedule is known: the Night Cards sky with a crescent moon and the reopening bell dead centre, read in the reopening exchange's own clock, byte-stable between wakes so the overnight rest costs no flash writes. A feed that fails to name a trustworthy reopen (missing or stale feed, past stamp, unknown exchange, no timezone database) keeps the earlier rest on the full-size AI usage face; the dash cards remain only as the static gallery preview for exactly those untrusted cases. No screen space is wasted on frozen, static prices.

When an active market is open, the header badge indicates the active exchange (`NSE` or `US`), ticker symbols are normalized (e.g. `^NSEI` $\rightarrow$ `NIFTY`, `RELIANCE.NS` $\rightarrow$ `RELIANCE`), and price fonts automatically step down to scale 2 when price strings reach $\ge 7$ characters (common with INR 5-digit valuations like `24150.00`) to prevent pixel collision.

The important part is ownership rather than drawing. Two publishers pointed at one clock fight over
the same filename and over which saved original is authoritative. So the stock runner drops its
`device_url` and becomes a pure feeder that only writes its snapshot, and the usage runner reads
that file through `stocks_snapshot` and remains the single process that backs up, uploads to and
re-asserts the device. The conflict is removed by construction, not by coordination.

Prices move faster than `poll_seconds`, so `render_seconds` (default 30) re-draws the frame without
re-reading any provider — the usage numbers on screen are redrawn every 30 seconds but are still
only as fresh as the last provider poll. `data_digest` covers the watchlist, so a price move counts
as changed data and is never deferred by the write budget.

Consolidating two clocks onto one roughly doubles that device's flash write load, concentrated in
the trading session.

## The clock's own settings

Two flash regions sit behind the HTTP surface, and they behave nothing alike. The photo album is
LittleFS — copy-on-write and wear-levelled, which is why rewriting a frame every thirty seconds is
sustainable. Everything written through `/api/set` lands instead in the ESP8266's emulated EEPROM:
one 4 KB sector, rewritten whole on every commit, no wear levelling, holding the Wi-Fi credentials
beside the display settings.

That asymmetry decides the design. `brightness` and the `night` block are declared in the config
and applied once at startup, after reading `/config` and comparing, so a run that changes nothing
writes nothing. The writable table covers display settings only; Wi-Fi, the weather key and the
accent colours are deliberately absent, because a bug that wrote them repeatedly would eventually
cost the clock its network. It also rules out an idea that looks attractive from the outside: using
the accent colours as a cheap status channel, since they need no image upload. They are not cheap.

`device_mac` addresses a different hazard. These clocks answer identically over HTTP and a router
can move an address between them, so without a stable name there is nothing preventing the
publisher pushing one person's usage to another device. When set, it reads `/mac` and refuses to
start rather than publishing somewhere it should not.

## Honest edges

**`stocks.py` is a near-duplicate of `live.py`.** It has its own config loader, `write_json`,
`load_original`, `mismatch_message` and `publish`. A change to the publish path usually has to be
made twice. Both publishers write the same filenames, so only one of them may own a given clock —
see the feeder arrangement above.

**Configuration is allowlisted in two places**, `sources.load_config` and `stocks.STOCK_KEYS`.
A new key added to only one of them makes the other process exit at startup.

**The clock has no authentication.** Anyone on the network can change its settings, delete its
photos, or post firmware to `/update_ota`, and `GET /config` returns the Wi-Fi password in
plaintext. Treat the device as LAN-only. TokenTV never calls `/update_ota`, and never deletes a
photo it did not create.

**Device detection happens once**, at startup, so a clock that was offline then stays misclassified
until the process restarts.

## Where state lives

Inside the `--state-dir`: `display-original.json` records the clock's theme and photo selection as
found, for `--restore-display`, and is only trustworthy if it was captured before TokenTV first took
the clock over. `display-receipt.json` describes the last publish, including whether it was
throttled, re-asserted, or driven by changed data. `snapshot.json` holds the current reading and is
overwritten each cycle, so there is no history. `tokentv.jpg` and `tokentv.gif` are written only on
a successful upload, which makes them a record of what the clock is actually showing rather than of
the most recent render.
