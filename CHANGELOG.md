# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **The moon cameo endures the weekend.** The closed-market cameos read the stocks snapshot's reading stamp (`updated_at`) for feeder aliveness, but an idle closed wake re-writes the payload with `updated_at` still at the board's last real fetch — so about ten minutes into any closed day, the staleness gate (600 s) went dark for good and the moon face stopped appearing exactly when there was time to appreciate it. The feeder now stamps every payload it builds — idle wakes included — with its own wake time, `published_at`: the reading stamp keeps its fetch semantics, and the cameo's gate (and anything else that must distinguish "feeder alive, board retained" from "feeder dead") reads the wake stamp, falling back to `updated_at` for older feeds. The digest that guards the write budget folds neither stamp, so re-stamping the payload still buys no flash writes.

## [0.5.2] — 2026-10-17

### Fixed

- **The calendar feeder tolerates a syncing calendar.** Right after a restart, Calendar.app's Apple Events scans can take far longer than the calm 0.5–3 s steady state (observed 25–160 s while iCloud syncs), so the feeder's hard subprocess timeout is raised from 10 to 150 seconds — still bounded, now wide enough to cover osascript's own two-minute AppleEvent timeout, which the query script itself lifts with a `with timeout` block (osascript's -1712 otherwise fires at 120 s and no subprocess ceiling could help it). A failed tick now retries after a minute rather than burying the next chance in the full five-minute window (bounded, as ever, by `MIN_INTERVAL`). A query that outlives even all of that keeps the last-good snapshot, as before. Diagnosing this was the hard part: the feeder swallowed the real error, so failures now print the osascript line to the log.

## [0.5.1] — 2026-10-17

### Added

- **The clock knows your next appointment.** A new feeder (`token-tv calendar`) polls Apple Calendar — scoped to the `riazrahaman` calendar, timed events only, all-day entries skipped — and writes a credential-free snapshot `{updated_at, title, start_iso}` beside the stocks feed; a failed query leaves the last snapshot standing (`calendar_snapshot` joins both config allowlists, and `sources.load_config` validates it). The publisher merges it under `snapshot['calendar']`, and every combo face draws the appointment as its second line, title left and a quantised countdown right — `in 2h14m`, then `h+mm`, whole hours, days, weeks. The usage rest face carries the same line as its fourth row. The line admits its age: a missing snapshot prints nothing rather than an invented appointment, a stale one keeps the title and adds `OLD`, an appointment in the past is not carried over. A changed event updates the digest, so it reaches the clock even inside the write budget; the `token-tv calendar` CLI passthrough mirrors `token-tv stocks`.

### Changed

- **A closed market rests the clock, and the moon face became a cameo.** The closed-market rest is back on the usage screen — the two stock slots carry the usage face (calendar row included, so weekends and nights still show the next appointment) — and the dedicated reopened night face now appears as a cameo: the publisher wakes the third slot for one publish tick about every five minutes, then sleeps it again. Honesty gates keep the cameo quiet: a missing or stale feed, a past stamp, a unknown exchange, all keep the clock resting.

- **The mascots are better and now move.** The usage face's mascot sprites were redone as brand-true 20×20 pixel art (Claude's eight-ray sunburst with orbiting shade, Ollama's llama profile, Antigravity's sparkle-and-companion double), and they ride the usage GIF's existing shimmer frames instead of sitting still: `mascot()` takes a phase and loads a `-pixel-1..3.png` variant next to each base sprite, so Claude's shading quarter-turns each frame, Ollama blinks mid-cycle, and Antigravity's star breathes while its companion diamond twinkles. Phase variants keep the base sprite's exact full-bleed bounding box, so every cell stays one square pixel, and the GIF still encodes under ten kilobytes.

- **A scheduled close has its own face.** The feeder's snapshot now carries the reopen on every closed publish (`market.next_open_at` and `market.next_open_exchange`, computed from the same schedule the feeder sleeps on), and the dashboard's closed-market rest follows it: a dedicated closed night face — the Night Cards sky, a crescent moon, and the reopening bell dead centre in the reopening exchange's own clock ("REOPENS 09:30", "TOMORROW", "NSE"), with a `LAST BOARD` stamp in the shut market's clock in the footer. The frame is one byte-stable still GIF, so the overnight rest costs no flash writes beyond the first swap. Honesty gates keep it quiet: a missing or stale feed, a past stamp, an unknown exchange or a missing timezone database — anything the schedule cannot promise — keeps the previous rest on the usage screen, and the gallery's static preview keeps the dash cards for exactly those cases too.

- **The stocks feeder sleeps on the schedule; a closed market asks for nothing.** The open and close times of both markets are known — 09:30–16:00 ET and 09:15–15:30 IST, weekdays, DST from the timezone database — so the feeder no longer polls a market that is asleep. `next_market_transition` returns the countdown to the earliest open or close boundary of either market, and the loop sleeps on those appointments: while a session is on it refreshes every `interval` but never sleeps past its closing bell, and while both are closed it fetches nothing at all — the idle wakes (every 5 minutes, as before) just re-publish the kept board so the photo theme stays asserted, and the next open boundary starts the polling again. A cold start while closed makes one catch-up fetch to fill the overnight board; the history records only fetch cycles, so a night's silence leaves no identical entries behind; `market_poll_interval` is gone. Holidays do not travel with the wall clock: the Finnhub status the watchlist fetches on each open-market cycle still rules them out, and in movers mode a holiday board freezes and goes `OLD`, exactly as a session that stopped trading. The dashboard's `movers_count` was and remains a setting, not a live count.

### Fixed

- **The Indian movers board has been broken since v0.4.8.** `fetch_movers('NSE')` raised `invalid_response` on every poll, so an open Indian session showed no Indian stocks at all — it fell back to whatever board was last fetched, which was the US one. The screener was answering correctly the whole time — twenty-five ranked NSE rows — and the client threw them away: the India quality bars are enforced client-side, and they read `intradaymarketcap` and `dayvolume`, which are the names the screener *query* uses. A response carries `marketCap` and `regularMarketVolume`, so cap and volume were `None` on every row and every row failed its own threshold. The test fixture built quotes with the query's names too, so the test double agreed with the mistake instead of catching it; it shipped that way in v0.4.8 through v0.5.0. The page-size guard also rejected the answer outright: Yahoo honours `count` on the predefined GETs but answers the custom POST with its own page, so that guard is now a flake ceiling rather than the requested count.

- **A frozen board no longer rides an open session as live.** `fetch_movers('NSE')` was failing every poll (the cause is the entry above — our own field names, not Yahoo); the feeder kept the previous board — the US closing prices — and published it under the open NSE session as `is_open: true, session: regular`. The clock therefore showed four dead US movers, labelled a live regular session, right through the Indian trading day, and never rested on the usage screen as it should. The snapshot now names the market the rows actually came from, reports that market shut, and ages the rows from their own fetch instead of from the poll that failed, so a board that stopped arriving goes stale rather than looking refreshed every five minutes.

- **A closed market no longer writes the same usage screen into three slots.** The stocks face rests the clock on the usage screen outside market hours, as intended — but it did so by filling all three album slots with identical bytes, so every reading that moved cost three flash writes instead of two. Two enabled photos is the fewest the firmware will re-read (a single enabled photo freezes the panel between uploads, verified on hardware in 0.4.10), so the rest now uses the two stock slots and puts the third to sleep. The combo family's static slots got the same economy.
- **The slot set is re-asserted the moment it changes**, rather than on the next periodic reassert. When a market shuts, the third slot stops being written; leaving it awake rotated a frozen usage screen between two fresh ones for up to `reassert_cycles`.
- **The dashboard's clock panel no longer previews a face the clock is not showing.** Its preview rendered the stocks board directly, so a closed market displayed four dim dash cards labelled *On the clock* while the clock was actually resting on the usage screen. The panel now asks for what is really published (`/frame/0.jpg?live=1`); the gallery thumbnail still renders the face itself, which is what a gallery is for.
- **The frame preview never saw the stock feed.** `store.snapshot()` builds a fresh dict per call and knows nothing about stocks — only the publisher's own cycle merged the feeder's file in. So every stock face previewed as dim dash cards on the dashboard, mid-session included. The preview now reads the feed the way the publisher does.

## [0.5.0] — 2026-10-16

### Changed

- **The dashboard was rebuilt around one gauge per account.** A percentage bar reads as "how far along is this"; the question a quota dashboard answers is "how much is left", which is what a gauge reads as. Every account now gets its own card, sized to be compared with the others at a glance, and the account tabs are gone — a tab put a reading one click behind a button and misaligned the gauges it sat above. Severity is written out (`LOW USAGE` … `LIMIT REACHED`) as well as coloured, so a reading survives greyscale; the bands are unchanged. A stale reading greys its arc and says `OLD, not a current reading`; an account with no reading shows `––` and what to do about it, never a zero. Second windows became quiet rows under *Other windows*, and the clock panel no longer hides behind a toggle.
- **One appearance, taken from the project page.** The web theme picker offered Digital Terminal, Neon Cyberpunk, Pixel Retro and Sci-Fi HUD — the same four choices the clock face dropdown already offers, two rows further down the same page. The costumes belong on the clock. The single design now follows token-tv.riazrahaman.com: IBM Plex, the 28 px hairline grid, the 2 px masthead rule over mono small caps, pill controls and 16 px cards.
- **Light, dark and auto.** Every colour token is `light-dark(light, dark)`, so the control switches nothing but `color-scheme`.

### Removed

- **Gallery likes.** The counts were reactions on the upstream project's issues, which do not exist in this repository; they said nothing about whether a face is any good. The Popular/New sort went with them — without counts, Popular sorted identically to New. Faces sort newest first.

### Fixed

- **The Combo gallery thumbnail was byte-identical to Pixel.** `build_demo.py` handed the sampled stock board to the `stocks` style alone, so the whole combo family drew its closed-market fallback and the one face whose point is usage and the market on one screen advertised neither. It now gates on `display.STOCK_STYLES`, the list `live.py` already uses for the album alternation.
- **The gallery repainted every five seconds.** `/display` is polled on that cadence and `live.py` serves `/frame/` with `Cache-Control: no-store`, so an always-visible clock panel would have re-rendered all thirteen faces inside the publisher's own process every five seconds — and dropped the revealed Apply button with them. It now repaints only when something visible changes.
- **The clock panel reported the preview as if it were on the clock.** `Showing …` now names `applied_style`, which is what the gallery's *On the clock* marker already used.
- **Site visit counter survives redeploys** — Railway containers are ephemeral, so the counter file was wiped on every push (resetting to 2–3). `server.js` now stores `.visits.json` on the attached Railway volume via `RAILWAY_VOLUME_MOUNT_PATH`, with the repo-local file as the local-development fallback.


## [0.4.11] — 2026-10-15

### Fixed

- **Animations retimed to the panel's native GIF pacing** (user feedback: the 2.8 s phases of 0.4.10 animated but read as "very very slow"; the panel plays GIF frame timings natively, as the reference GIF's rapid bounce always proved). The stocks arrows now breathe a full edge-to-edge loop in 2.0 s (four distinct 500 ms phases) and the usage gauges shimmer a full bar walk in ~1.2 s (four distinct 300 ms phases) — amplitudes, phase walks, slots, GIF sizes and the anti-coalesce merge guard are unchanged.

## [0.4.10] — 2026-10-15

### Fixed

- **Animations now register on the e-paper**: the first shipped GIFs paced their phases at 100-400 ms (and the encoder coalesced some away), far below the SD_PRO's ~2.5 s panel repaint cadence, so the board read as static. Every phase now lasts ~2.8 s — one per panel repaint: the stocks arrows swing edge-to-edge between full ink and near-dark across four distinct phases (coalesce-guarded: adjacent frames always differ), and the usage gauges' shimmer highlight walks three segments per phase. Slots stay `tokentv.gif` + `tokentv-b.gif` + `tokentv-usage.gif` in the 20 s/10 s loop (stocks ~14 KB, usage ~5 KB, both far inside the 400 KB budget).

## [0.4.9] — 2026-10-15

### Added

- **Animated screens**: the stocks face's up/down arrows and their glows breathe across five GIF frames (the Night Cards board stays otherwise still, so the encoding stays small), and the usage screen's provider gauges gain the shimmer light-wave — slots became `tokentv.gif` + `tokentv-b.gif` (stocks) and `tokentv-usage.gif` (usage), each within its 400 KB GIF budget (the clocks now rotate five seconds of breathing arrows into the 20 s/10 s alternating loop). A closed market rests the clock on the shimmering usage GIF in all three slots; non-stock styles keep their static two-frame JPEG slots, which a reassert puts back on while the GIF slots sleep; `restore()` puts every slot of ours to sleep.

## [0.4.8] — 2026-10-15

### Fixed

- **Movers board no longer shows mildest losers**: Yahoo's custom screener POST intermittently flakes (ignoring its query operands and sort, answering with the whole unfiltered universe — verified live, and no crumb fixes it). The US movers now read Yahoo's crumb-free predefined screens (`day_gainers`/`day_losers` GETs, verified sorted and filtered), with one guarded custom-POST fallback; India keeps the custom POST. Every board page passes a client-side guard before ranking (row count, whole-universe total, missing fields, each region's thresholds, and a tolerant sort check — a genuinely live page keeps ticking while it is read, so a small minority of adjacent inversions stays honest, a grossly shuffled sample never passes) — a degraded answer gets exactly one fresh-crumb retry and then the publisher keeps the last-good board and backs off, never fabricating anything.

### Added

- **Stocks-usage alternation (20 s / 10 s)**: gated on the chosen style actually drawing the stock board (`stocks` and the combo family) — while a stock market is open (combo staleness conventions), the publisher fills the rotation album with that style's stocks frame in `tokentv.jpg` and `tokentv-b.jpg` and a usage-only face in a third slot `tokentv-usage.jpg` — at the slideshow's 10-second interval that is the user's 20-seconds-stocks / 10-seconds-usage loop, driven by the hardware's rotation and refreshed by every publisher cycle. A closed, missing or stale market rests the clock on a continuous usage screen by filling all three slots with the usage frame (the usage-only-when-closed choice follows the user's stated intent). A non-stock style keeps 0.4.7's behaviour: its chosen face in the two stock slots, never the usage slot (a stale one left by an earlier run is disabled on the next reassert, so a feeder-owned clock can no longer see a false lost frame either — its wanted set is the two stock slots). Dedupe is per slot (only changed slots re-upload), one write-budget decision still gates each event — an allowed live event now costs up to three uploads (default budget 6 → ≤18 uploads/hour; an uncapped feeder keeps ~240 uploads/hour for its two stock slots). GIF frames stay single-file; an Ultra keeps its single hard-set image.


## [0.4.7] — 2026-10-15

### Fixed
- Two-frame album rotation (tokentv.jpg + tokentv-b.jpg): the SD_PRO slideshow only re-reads album files on rotation or state mutation, so a single enabled photo froze the e-paper panel between uploads. The publishers now upload both rotation slots per write event; receipts, local mirrors and lost-upload recovery are twin-aware; GIF frames stay single-file (they repaint themselves). The uncapped stocks feeder budget now costs ~2 uploads per event (~240 uploads/h at the 30 s open-market poll — set max_writes_per_hour to cap).

### Added

- **Two-frame rotation for the SD_PRO panel**: the firm's slideshow only re-reads the album when files change state, so a single enabled photo froze the panel between content writes. JPEG frames are now uploaded to `tokentv.jpg` and an identical `tokentv-b.jpg` (the auto-enable does the enabling; no toggles after upload), `activate()` keeps exactly those two enabled and the rest of the album asleep (a no-op when already correct), and reasserts additionally redownload when the clock has lost either slot. Write budgets gate whole two-file events (`max_writes_per_hour` semantics unchanged; the live default 6 still means ≤12 uploads/hour; an uncapped stocks feeder at its 30-second open-market poll spends ~120 events × 2 = **~240 uploads/hour** — set `max_writes_per_hour` in `stocks.json` to cap that).

## [0.4.6] — 2026-10-15

### Changed

- **Night Cards for the `stocks` face**: the stocks-only board is rebuilt in the approved Night Cards design — dark-navy sky with star margins, four rounded cards tinted to each row's sign, the arrowed percent as the biggest text with a dim glow, the symbol and price at scale 2, and the 5%-fill magnitude bar tinted to the card. Closed or stale feeds still read CLOSED over dim dash cards.

## [0.4.5] — 2026-10-15
### Added
- New `stocks` display face: full-screen 4-row gainers/losers board (no AI usage rows); works in watchlist and movers modes.

## [0.4.4] — 2026-10-15

### Added

- **Market Movers Mode**: `"mode": "movers"` in the stocks config turns the watchlist face into a whole-market movers board — the top `movers_count` gainers and losers of the active market (US and India, keyless Yahoo screener, deduped across `.NS`/`.BO` listings) re-ranked every cycle. The stock config hot-reloads on mtime change, and the dashboard's clock panel gains a stocks mode toggle plus a four-slot ticker editor (`POST /stocks/mode`, `POST /stocks/symbols`) that writes through to the feeder's config.
- **Stocks-Only Face**: a new `stocks` clock style that shows only the four market rows, big, with no AI-usage rows — selectable from the dashboard's style list like any other face, and drawing a CLOSED dash board when the feed is closed, missing or stale.

## [0.4.3] — 2026-10-14

### Added

- **Two Factory Channels Architectural Documentation**: Formally documented the clock's two factory operational channels (`Channel 1: Photo Mode` vs `Channel 2: Clock/Weather Mode`) across `README.md`, `docs/architecture.md`, and `docs/clock-firmware.md`.
- **Custom GIF Slot & RAM Wall Analysis**: Documented the hidden custom 80×80 GIF slot in Channel 2 and the ESP8266 ~40 KB memory wall that enforces mutual exclusion between modes.
- **Host-Side Multi-Frame Compositing**: Documented how TokenTV resolves the memory wall by pre-compositing animated walking pixel mascots, starfields, and live telemetry on the host into a single animated GIF stream for Channel 1.
- **Follow-the-Sun Multi-Market Support**: Added Indian stock market (NSE/BSE) support with automatic time-based market routing. When Indian markets open (09:15–15:30 IST / 03:45–10:00 UTC), the stock feeder automatically transitions to Indian tickers; when US markets open (09:30–16:00 ET / 13:30–20:00 UTC), it switches to US tickers; during inter-market closed periods, it preserves off-hours full-screen AI usage.
- **Zero-Config Yahoo Finance Integration**: Indian equities and indices (`RELIANCE.NS`, `TCS.NS`, `INFY.NS`, `HDFCBANK.NS`, `^NSEI`, `^BSESN`) fetch quotes via Yahoo Finance's v8 chart endpoint with zero credentials or API keys, while US equities continue using Finnhub.
- **Display & Typography Optimizations for INR**: Clean symbol translation (stripping `.NS`/`.BO`, aliasing `^NSEI` $\rightarrow$ `NIFTY`, `^BSESN` $\rightarrow$ `SENSEX`), dynamic exchange badges (`NSE` vs `US`), and adaptive price font scaling (dropping to scale 2 for $\ge 7$-char prices) to eliminate visual overlap on 240×240 panels.
- **Dedicated Red/Green Arrow Glyphs**: Native 5×7 `▲` (green) and `▼` (red) triangle indicators with color-coded stock price changes, eliminating overlapping digit artifacts.
- **Two Factory Channels High-Res Diagram**: Generated 1600×1000 technical schematic (`site/tokentv/assets/two-channels-illustration.png` and `docs/assets/two-channels-illustration.png`) depicting the ESP8266 RAM wall, Channel 1 host-composed stream, and Channel 2 firmware clock & 80×80 slot.
- **Direct Hardware Sourcing**: Added verified AliExpress hardware listing links (~$5–$8 USD) across documentation and website.
- **Viewport Anchor Scroll Fix**: Corrected initial scroll behavior in `site/tokentv/assets/theme.js` so visiting `#idea` directly defaults to the top hero header rather than snapping abruptly into lower sections.
- **Interactive Dual Showcase on Website**: Added responsive side-by-side comparison ("The Vision vs The Reality") in Chapter 01 featuring the 3D concept teaser (with audio unmute and HTTP 206 range streaming) alongside the latest live desk clock recording.

## [0.4.2] — 2026-10-14

### Added

- `site/tokentv/`, a public project page explaining the design, the hardware, the code flow, what
  the fork was built from and what was contributed on top, with two inline SVG diagrams drawn in
  the host site's own design tokens. It reuses that site's stylesheet rather than imitating it, so
  the pages are one design language. Deployed at https://tokentv.pages.dev.
- The page states plainly that the work was done in sessions with an AI agent, and separates what
  came out of that from the decisions and hardware that did not.

### Notes

- Attribution on the page uses the non-merge commit count and the Python-only line delta rather
  than the flattering totals, and credits upstream, the SmallTV-Ultra contributor and the research
  prior art by name.
- The folder is self-contained and also serves from a dependency-free `server.js`, so it can be
  hosted anywhere that runs Node or serves static files.

## [0.4.1] — 2026-10-14

Documentation catching up with 0.4.0, plus a hardware behaviour found while using it.

### Changed

- `CLAUDE.md` and `docs/architecture.md` now cover the two flash regions and why only one of them
  is forgiving: images go to wear-levelled LittleFS, while everything written through `/api/set`
  lands in a single unlevelled 4 KB EEPROM sector that also holds the Wi-Fi credentials. That
  asymmetry is why the settings path reads before it writes, and why the accent colours are not a
  cheap status channel.
- Both documents also describe `device_mac` and the `SettingsUnsupported` behaviour of the Ultra
  profile.

### Fixed

- Documented that **`GET /photo/delete` re-enables every remaining photo**, observed on an SD_PRO
  unit: deleting one leftover file left all the factory samples enabled and the clock rotating
  through them. Re-assert the selection straight afterwards, or wait for the `reassert_cycles`
  watchdog.

## [0.4.0] — 2026-10-13

The clock's own brightness, night window and identity, which TokenTV had never touched.

### Added

- `brightness` (2–99) and a `night` block (`enabled`, `start`, `stop`, `brightness`) are applied to
  the clock through `/api/set` at startup. Declaring them in the config makes the settings
  reproducible and survivable rather than something set by hand in the clock's web UI.
- `device_mac`. The clock's MAC is its one stable name: an address can move when the router hands
  out a new lease, and these clocks are indistinguishable over HTTP. When set, the publisher reads
  `/mac` and refuses to start if the clock at that address is not the expected one.
- `device.identity()`, `device.settings()` and `device.apply_settings()`.

### Notes

- **These settings live in the ESP8266's emulated EEPROM**: a single 4 KB sector, rewritten whole
  on every commit, with no wear levelling, holding the Wi-Fi credentials alongside them.
  `apply_settings` therefore reads first and writes only what actually differs, and these keys must
  never be driven from changing data. Only display settings are writable; Wi-Fi, the weather key
  and the accent colours are deliberately not in the table.
- The SmallTV-Ultra profile raises `SettingsUnsupported` rather than calling routes it does not
  have; the publisher reports that and carries on.

## [0.3.0] — 2026-10-13

A `combo` face, so one clock can carry both AI usage and a stock watchlist.

### Added

- A `combo` display style that draws three AI usage rows and four watchlist rows on one 240×240
  frame. It shows each account's code, a usage gauge, the percentage and the window that
  percentage is of, then each symbol with its price and day change.
- `stocks_snapshot`, the path to the stock feeder's own snapshot file. The publisher reads it and
  draws the market section from it, so the feeder never touches the clock.
- `render_seconds` (default 30 when a feed is configured), a short re-draw tick. Prices move faster
  than `poll_seconds`, and this re-renders the frame without re-reading any provider.

### Changed

- **One clock, one owner.** Point `stocks.json` at no `device_url` and it becomes a pure feeder,
  writing only its snapshot. `live.py` is then the single process that backs up, uploads to and
  re-asserts the clock, which removes the two-publishers-one-device problem by construction rather
  than by coordination. Two publishers on one clock previously fought over `tokentv.jpg` and over
  which `display-original.json` was authoritative.
- The market section only appears while the market is open and the feed is fresh. Outside the
  session `combo` renders the full-size usage face, so the readable layout is what the clock shows
  for most of the day and no screen space is spent on prices that stopped moving hours ago.
- `data_digest` now covers the watchlist, so a price move counts as changed data and is never
  deferred by the write budget.
- A dashboard-initiated publish is identified explicitly rather than inferred from whether the
  cycle refreshed, so the new fast re-draw tick is not mistaken for a user action.

### Notes

- The composite drops the provider mascots and the reset countdown; at this row height they would
  not be legible. The window label is kept, because 75% of a five-hour window and 75% of a week are
  different facts.
- Consolidating two clocks onto one roughly doubles that device's flash write load.

## [0.2.0] — 2026-10-13

The clock keeps every frame in flash, and the only guard against rewriting it was a check for
identical bytes. That check never fires while a short window leads, because the countdown and the
used percentage both move on every poll. This release bounds the writes without hiding a reading
that actually changed.

### Added

- `countdown_seconds` (default 900) rounds the rendered countdown up to whole buckets, so the face
  changes when the remaining time crosses a bucket edge rather than every minute. A steady hour
  drops from 12 writes to 4. Usage faces only; it has no effect on a watchlist.
- `max_writes_per_hour` (default 6 for usage, 0 — no cap — for the watchlist) spaces uploads
  instead of spending a quota, so a frame is never more than one gap old. A capped watchlist also
  refreshes no faster than its cap, which is why the ticker ships uncapped and the choice is
  documented rather than imposed.
- `reassert_cycles` (default 12) re-selects the photo theme periodically. `activate()` previously
  ran only when the file name changed, i.e. once, so a clock that rebooted or was touched through
  its own web UI fell back to its theme while TokenTV kept uploading into a photo nobody saw.
- A JPEG quality ladder (92 → 85 → 78, then chroma subsampling) that steps down until the frame
  fits the firmware's size limit, recording the chosen setting in the receipt. The `retro` face sat
  at 76% of the 60 000-byte limit with no fallback, and a stock watchlist runs closer still.
- `display-receipt.json` now reports `throttled`, `reasserted`, `data_changed` and `encoded`.
- A `CHANGELOG.md`, and the flash-wear section of `docs/clock-firmware.md` now carries measured
  numbers instead of an unverified claim.

### Changed

- A changed reading is never deferred. The publisher fingerprints the readings themselves
  (status, label, percentage at the precision the faces print) and ignores the countdown, so a
  moved percentage, a changed status, a style you applied, or a clock that lost our file upload at
  once; only a countdown that merely ticked waits for the budget.
- `MAX_SYMBOLS` is 4, matching the four rows `render_image` draws. Validation previously accepted
  six symbols and then silently never drew the fifth or sixth; too many symbols is now a startup
  error.
- A clock that has lost our file is repaired in the same cycle rather than the next, and bypasses
  the write budget.

### Fixed

- Throttling no longer reports a delivery failure. It previously called
  `preferences.delivered(style, False)`, which the dashboard renders as
  "Clock upload failed. Please retry."
- `countdown_seconds`, `max_writes_per_hour` and `reassert_cycles` are accepted by the
  configuration allowlists in `token_tv/sources.py` and `token_tv/stocks.py`. Without this, adding
  any of them raised `Unsupported configuration field` at startup.

### Notes

- Countdowns round up, so one minute remaining prints as `0h15m` at the default bucket.
- A usage frame can be up to one budget gap old — ten minutes at the default — but only when
  nothing in the reading has changed.
- Measured on macOS against an SD_PRO clock: 106 → 116 tests.

## [0.1.0]

First tagged release.

[0.5.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.5.0
[0.4.11]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.11
[0.4.10]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.10
[0.4.9]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.9
[0.4.8]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.8
[0.4.7]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.7
[0.4.6]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.6
[0.4.5]: https://github.com/riazrahaman/riaz-token-tv/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.4
[0.4.3]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.3
[0.4.2]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.2
[0.4.1]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.1
[0.4.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.0
[0.3.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.3.0
[0.2.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.2.0
[0.1.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.1.0
