# Inside the clock's firmware

TokenTV is a client of the clock's own web UI; it never replaces it. This page records what that
firmware actually is and what its HTTP surface looks like, so nobody has to re-derive it.

Everything here was read from the pages the clock serves itself (`/`, `/photo`, `javascript.js`)
and from open-source firmware for the same board. Nothing was installed on a clock, and no setting
on any clock was changed to write this.

## What the clock runs

A 240×240 Wi-Fi desk clock of this family is an **ESP8266** (ESP-12F class) running the **Arduino
ESP8266 core** (NONOS SDK), with a 4 MB **GD25Q32C** flash chip and an **ST7789** IPS panel.

The stock firmware ships as a vendor binary with no source and no licence. It is not an ESP32
image, and it is not compressed or encrypted — the file is a two-part vendor container: a
bootloader at `0x0` (about 3.5 KB, padded to `0x1000`) followed by the application from `0x1000` to
the end of the file. There is no partition table in it, and no filesystem: the sample photos,
themes and fonts live in a separate flash region.

Community reverse-engineering has found **two stock API profiles** on this hardware. TokenTV
supports both:

| Profile | How it is recognised | How an image is set | Photo theme |
| --- | --- | --- | --- |
| **SD_PRO** (the one documented below) | answers `/theme/list` and `/photo/list`, has no `/v.json` | `POST /photo/upload`, then enable the file and switch to the photo theme | id `2` |
| **SmallTV-Ultra** | `/v.json` reports `SmallTV-Ultra` | `/doUpload?dir=/image/`, then `/set?img=` | Album theme `3` |

## The wiring

The compile-time pin defines are not recoverable from the vendor binary, but six independent
open-source ports for this board agree on them:

| Signal | GPIO |
| --- | --- |
| SCLK | 14 |
| MOSI | 13 |
| DC | 0 |
| RST | 2 |
| Backlight | 5 (active-low) |
| CS | tied to GND (one clone variant uses GPIO 15) |

Panel bring-up: **SPI mode 3, 40 MHz, 16-bit RGB565, colour order BGR, inversion on** (the panel is
IPS). Sources: `bvweerd/platformio.ini`, `aydarik` and `iodn` (same defines), `tasocuk`
(`BL_ACTIVE_LOW`, `TFT_INVERSION_ON`), `giovi321/src/board_esp8266.h`, and the ESPHome YAMLs for
the same clock. One fork of ESPHome (`rletendu`) carries a readable ST7789 init sequence if you
ever need the exact register order.

Honest caveat: this is the sequence the open-source ports use on this board, which is strong
evidence the vendor does the same, but it is not a byte-exact extraction of the stock firmware's
own init. The exact ST7789 die revision is also unproven; the variants are register-compatible for
this use.

## Why "hand it a picture" is the right interface

A full 240×240 16-bit frame buffer is 115,200 bytes. The ESP8266 has roughly 40–50 KB of usable
heap, so **no firmware in this family buffers a whole frame**. They all stream instead: JPEG is
decoded in small blocks and pushed straight to the panel (a `TJpg_Decoder` callback around
`pushImage`), and text or shapes are drawn directly. The most memory-hungry port keeps a
216×64 **4-bit** partial sprite, about 7 KB.

That is exactly why TokenTV draws the frame on the computer and uploads one finished 240×240
image. The clock never has to compose anything.

## The Two Factory Channels and the GIF Slot Discovery

The factory firmware divides its visual capability into two distinct operational "channels":

```
┌──────────────────────────────────────┐    ┌──────────────────────────────────────┐
│       CHANNEL 1: PHOTO MODE          │    │       CHANNEL 2: CLOCK MODE          │
│                                      │    │                                      │
│ [==================================] │    │   1 0 : 4 2   A M                    │
│ CLAUDE    WEEK 66%                   │    │   72°F  Sunny                        │
│ OLLAMA    WEEK 40%                   │    │                                      │
│ ANTIGRAV  WEEK 74%                   │    │  ┌───────────┐                       │
│ NVDA: $224  TSLA: $368               │    │  │Little GIF │  (Tiny 80×80          │
│                                      │    │  │box        │   animated sticker)   │
│ (Full 240×240 TokenTV canvas)        │    │  └───────────┘                       │
└──────────────────────────────────────┘    └──────────────────────────────────────┘
```

1. **Channel 1 (Photo Mode — what TokenTV uses)**:
   - Your host computer renders a full-screen, high-resolution 240×240 image or multi-frame animated GIF with AI quotas, watchlist telemetry, pixel mascots, and glowing gauges.
   - The clock displays this frame full-screen, 24/7, via `/photo/upload` into LittleFS.
2. **Channel 2 (Clock / Weather Mode)**:
   - The clock runs its built-in firmware routines: syncing time over NTP (`pool.ntp.org`) and weather metrics from OpenWeatherMap (configured via `/api/set` with an API key).
   - In the lower corner, it renders a small 80×80 animated GIF slot (conventionally showing the factory floating astronaut / spaceman).

### The Discovery: The "Hidden" Custom GIF Slot
Firmware analysis revealed an undocumented capability in Channel 2: the factory floating astronaut is not hardcoded in ROM — it resides in an internal upload slot that can be overwritten with a custom 80×80 animated GIF (e.g., an animated mascot, custom status sticker, or mini ticker).

### The Constraint: The Clock's Brain is Tiny (~40 KB RAM)
An intuitive idea is to blend the two modes: display TokenTV telemetry on Channel 1 while overlaying an animated GIF sticker from Channel 2. However, the ESP8266 architecture makes this physically impossible:
- **No Framebuffer**: A 240×240 16-bit RGB565 framebuffer requires 115,200 bytes. The ESP8266 has only ~40–50 KB of usable heap.
- **Mutual Exclusion**: The firmware cannot composite multiple visual layers. It can either tile-decode an image into Channel 1 OR run the Channel 2 clock layout with its 80×80 GIF sprite. You can only be on **Channel 1** OR **Channel 2**.

### The Solution: Host-Side Animation Compositing
TokenTV transcends this hardware bottleneck by moving the compositing pipeline entirely onto the host:
- Rather than expecting the microchip to composite graphics, the host's Python + Pillow engine builds a multi-frame animated sequence (`combo_cyber.py`):
  - Pre-renders ambient starfield twinkles.
  - Generates multi-frame walking cycles for the pixel mascot (with directional flips as it patrols the center divider rule).
  - Dynamically draws quota progress gauges and stock price indicators with custom `▲`/`▼` arrow glyphs.
  - Assembles the frames into a lean animated GIF (<200 KB).
- Uploading this pre-composited GIF to Channel 1 delivers rich, multi-layered animations to the clock's screen with zero hardware strain and zero firmware hacking.


## The HTTP surface

Read-only, safe to call:

| Endpoint | Response |
| --- | --- |
| `/mac` | plain-text MAC address — a stable device id |
| `/config` | one JSON state object (theme, brightness, night window, time format, city, colours) |
| `/theme/list` | `{interval, themes: [{id, name, enabled}]}` — seven themes, id `2` is Photo |
| `/photo/list` | `{files: [{name, size, enabled}], total, used, interval}` |

`/scanwifi` is also a `GET`, but it forces a Wi-Fi scan and can briefly disturb the radio, so treat
it as disruptive rather than read-only.

State-changing, documented from the firmware's own pages:

| Endpoint | Params | Effect |
| --- | --- | --- |
| `/api/set` | `key`, `value` | write one setting (see below) |
| `/photo/toggle` | `name`, `state=0\|1` | include or skip a photo in the slideshow |
| `/photo/interval` | `val` | slideshow interval |
| `/photo/upload` | multipart field `file` | add an image to the album |
| `/photo/delete` | `name` | **delete that file** from the album |
| `/theme/toggle` | `id`, `state=0\|1` | enable or disable a theme |
| `/theme/interval` | `val` | theme rotation interval |
| `/connect` | `new_ssid`, `password` | change Wi-Fi — can lock you out |
| `/restart` | — | reboot |
| `/update_ota` | multipart field `update` | **overwrite the firmware — do not call it** |

TokenTV uses `/theme/list`, `/photo/list`, `POST /photo/upload` and the theme toggle for id `2`.
`/photo/delete` exists if you ever want to remove a file the app uploaded.

There is **no authentication** on any of this. Any device on the same network can change settings,
delete photos, restart the clock, or post a firmware image. Treat the clock as a LAN-only device,
and do not put it on a network you do not trust.

## Settings: the write keys are not the read keys

`/config` reports some settings under a different name than the one `/api/set` accepts. This trips
up anything driving the clock directly:

| Concept | Write with `/api/set?key=` | Read from `/config` as |
| --- | --- | --- |
| Brightness (2–99) | `lcd_brightness` | `brightness` |
| 12/24-hour | `time12_24` | `hour12` |
| Sync time from the browser | `timesync` | `sync` |
| Timezone (hours) | `zone` | *(not returned)* |
| Night dimming | `nightmode`, `starttime`, `stoptime`, `nightbrightness` | same names |
| Accent colours | `color1`, `color2`, `color3` (or `ws_r`, `ws_g`, `ws_b`) | `color1`, `color2`, `color3` |

Colours are **RGB565**: `value = (R >> 3) << 11 | (G >> 2) << 5 | (B >> 3)`, with `0` meaning
unset/black. The stock picks orange, blue and green by default.

## The update path

`/update_ota` accepts a multipart field named `update` and is handled by the Arduino `Updater`
library. It requires the image to begin with the `0xE9` magic byte, the **filename to start with
`SDP`**, and an MD5 to match. That is a naming convention and a checksum, not a signature — there
is no cryptographic verification and no authentication in front of it.

TokenTV never posts firmware. It only ever sends a picture.

## Other firmwares for the same hardware

Several open-source projects already run on this board. None of them speaks the SD_PRO API — they
implement the SmallTV-Ultra API or a custom interface, which is why TokenTV keeps the two profiles
apart.

| Project | Licence | Notes |
| --- | --- | --- |
| `bvweerd/geekmagic-tv-esp8266` | MIT | the upstream original of the family; smallest and clearest |
| `aydarik/geekmagic-tv-esp8266` | MIT | a fork of the above, the most actively maintained |
| `iodn/geekmagic-tv-esp8266` | MIT | derivative with authentication and a dashboard |
| `tasocuk/smalltv-claude-monitor` | MIT | from-scratch, direct drawing |
| `Cancro29/OpenGeekMagic` | MIT | Arduino remake, low activity |
| `giovi321/smalltv-mod` | WTFPL | multi-board, documents its frameless rendering |
| `adrienbrault/geekmagic-hacs` | MIT | a Home Assistant integration, not firmware — but it is the only code that documents and implements the **SD_PRO** profile, so it is the best reference for our API |
| ESPHome configurations for this clock | mostly unlicensed | declarative alternative; the panel init lives in its patched ST7789 component |

If a custom firmware were ever wanted, the shape of the work is: fork an MIT port and **add** the
SD_PRO endpoints above, so the picture route keeps working unchanged. The hard parts — panel
bring-up, backlight, JPEG streaming, captive portal, OTA — are already solved in those projects.
The firmware binary itself carries no licence, so it should not be redistributed.

## If you ever do flash

TokenTV does not, and for the normal use there is no reason to. For completeness:

- The USB-C port is **power only**. Flashing needs the case opened (two underside screws) and a
  **3.3 V USB-UART adapter** on the six-pad header, with GPIO0 tied to ground at power-on.
- Take a full `esptool read_flash 0x0 0x400000 backup.bin` first. That is the only way to keep the
  sample photos and themes.
- Restoring from the vendor `.bin` files restores a bootable clock but **not** the filesystem
  contents, because those files are bootloader plus application only.
- Frequent image writes wear the flash. TokenTV skips a re-upload when the rendered bytes are
  unchanged, but that dedupe alone is not enough: when a short window leads, the countdown and the
  used percentage both move on every poll, so every frame differs and the dedupe never fires —
  measured at 12 distinct frames an hour, about 288 writes a day. Two settings bound it:
  - `countdown_seconds` (default 900) rounds the rendered countdown up to whole buckets, which
    alone cuts a steady hour from 12 writes to 4. Usage only; it has no effect on a watchlist.
  - `max_writes_per_hour` (default 6) is the real cap, and the one that holds while a percentage
    is climbing. It spaces writes rather than spending a quota, so the frame is never more than
    one gap old — 10 minutes at the default. A style you apply from the dashboard, and a clock
    that has lost our file, both bypass it.

  The stock watchlist writes harder while it runs: it polls every 30 seconds **only while the
  market is open**, and prices move on every poll, so the byte dedupe never fires. Measured at 120
  writes an hour, about 780 per trading day across the 6.5-hour regular session. Outside the
  session it falls back to 300-second polls on a frame that does not change, so the dedupe
  absorbs it.

  `stocks.json` accepts the same `max_writes_per_hour` key, but it **defaults to 0, meaning no
  cap**. A capped ticker refreshes no faster than the cap — 6/hour is a ten-minute ticker, which
  is not what a ticker is for — so the trade-off belongs to whoever owns the clock. If you do want
  one, pick it from the refresh you can live with: 60/hour is a one-minute ticker and halves the
  wear. For reference, uncapped is roughly 197 000 writes a year against the GD25Q32C's ~100 000
  erase cycles per sector; whether that matters depends on whether the photo region wear-levels,
  which this firmware does not document.

  Keep any other push frequency low too.

## Two behaviours worth knowing before you automate the album

- **`GET /photo/delete?name=` re-enables every remaining photo.** Observed on an SD_PRO unit: an
  album with four disabled files and one enabled one came back from a single delete with *all*
  remaining files enabled, so the clock began rotating through them instead of showing the one
  that was selected. Whatever the firmware does to the per-file state on delete, it does not
  preserve it. Re-assert the selection with `/photo/toggle?name=&state=0` immediately afterwards,
  or let the publisher's `reassert_cycles` watchdog pick it up on its next pass.
- **Settings are not in the filesystem.** The images live in LittleFS, which is copy-on-write and
  wear-levelled, but `/api/set` writes land in the ESP8266's emulated EEPROM: one 4 KB sector,
  rewritten whole on every commit, no wear levelling, and it holds the Wi-Fi credentials. Treat
  those keys as occasional settings, never as somewhere to put changing data.

## Scope

TokenTV never flashes firmware, and it only ever writes its own `tokentv.jpg` / `tokentv.gif` plus
the theme selection. Your own photos are left in the album untouched — the app deselects them
while it is showing, and `token-tv run --restore-display` puts the original theme and selection
back.
