# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.4.1]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.1
[0.4.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.4.0
[0.3.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.3.0
[0.2.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.2.0
[0.1.0]: https://github.com/riazrahaman/riaz-token-tv/releases/tag/v0.1.0
