"""Countdown and Hourly Wisdom Clock for SD_PRO desk displays.

A pure-black face in three bands:
- Time: fixed-cell digits in Manrope ExtraBold with a 1 Hz blinking colon, so the
  layout never shifts as proportional digits change width.
- Date: letterspaced day-of-week in amber, day/month in grey. No year.
- Hero band: alternates a giant optically centred countdown numeral to February 1,
  2028 with an hourly English proverb, re-wrapped to fit. Neither carries a label.

Every colour is a ramp off #000000 and the frames are flattened to one fixed
palette before encoding, so the GIF palette never averages the background away
from true black.
"""
import functools
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from token_tv.combo_stocks import _gif_bytes
from token_tv.device import PhotoDisplay

TARGET_DATE = date(2028, 2, 1)

WEB = Path(__file__).parent / "web"

# Curated library of focus, discipline, time & perseverance wisdom.
# Stored as two-part tuples; the renderer re-wraps them to the hero band.
PROVERBS = [
    ("“Fear not going slowly,", "fear only standing still.”"),
    ("“Dripping water hollows", "out the stone.”"),
    ("“An inch of time is gold;", "gold cannot buy time.”"),
    ("“A journey of a thousand", "miles begins with a step.”"),
    ("“With perseverance, an iron", "rod becomes a needle.”"),
    ("“The best time to plant a tree", "was then; second best is now.”"),
    ("“Dig the well before", "you become thirsty.”"),
    ("“He who moves a mountain", "begins with small stones.”"),
    ("“Do not let yesterday", "use up too much of today.”"),
    ("“A gem cannot be polished", "without friction.”"),
    ("“Do not wait for wind;", "take boldly to the oars.”"),
    ("“Shed sweat in practice,", "weep no blood in battle.”"),
    ("“Gold is tested by fire;", "resolve is tested by time.”"),
    ("“Patience is bitter,", "but its fruit is sweet.”"),
    ("“Deep roots fear", "no harsh wind.”"),
    ("“One moment of patience", "prevents great regret.”"),
    ("“Silence all doubts with", "the sound of your work.”"),
    ("“The master has failed more", "times than beginner tried.”"),
    ("“Great souls have wills;", "feeble ones have wishes.”"),
    ("“Small steps every day", "yield miles over months.”"),
    ("“A diamond with a flaw", "beats a perfect pebble.”"),
    ("“When winds of change blow,", "wise build windmills.”"),
    ("“Better to light a candle", "than to curse the dark.”"),
    ("“To conquer yourself", "is the greatest victory.”"),
    ("“Clear your mind like water;", "hold fast to your aim.”"),
    ("“Rest when weary, but never", "stop before the summit.”"),
    ("“Knowledge is treasure,", "practice is the key.”"),
    ("“A single spark can", "ignite a roaring fire.”"),
    ("“The frog in the well", "knows not the great ocean.”"),
    ("“Look at the stars, but", "keep feet upon the earth.”"),
    ("“Every sunrise brings", "a fresh field to conquer.”"),
    ("“An unsharpened blade", "cannot cut through iron.”"),
    ("“Endure the climb to", "behold the vast horizon.”"),
    ("“A stumble today makes you", "wiser for tomorrow.”"),
    ("“Perseverance turns the", "ordinary into remarkable.”"),
    ("“True courage is quiet;", "it whispers: try again.”"),
    ("“Act with purpose; let", "results make the noise.”"),
    ("“He who conquers his mind", "conquers all obstacles.”"),
    ("“Plant good habits today;", "reap a lifetime harvest.”"),
    ("“Keep your eyes on the goal,", "not on the obstacles.”"),
]

COLOR_BG = "#000000"
COLOR_TIME = "#ffffff"
COLOR_COLON_OFF = "#1c1c1c"     # the colon ghosts rather than vanishing, so the rhythm reads
COLOR_DAY = "#ffb300"           # amber, tying the date to the countdown
COLOR_DATE = "#8e8e8e"
COLOR_RULE = "#1e1e1e"
COLOR_COUNTDOWN = "#ffb300"
COLOR_MARK = "#6b4a00"          # the short rule under the numeral
COLOR_PROVERB = "#f2ece0"       # warm white


# Band geometry. The hero band owns everything below the rule.
RULE_Y = 110
HERO_TOP, HERO_BOTTOM = 118, 232
TIME_CENTER_Y = 46
DATE_BASELINE_Y = 96


@functools.lru_cache(maxsize=None)
def _font(size, weight=800):
    """Manrope at an explicit weight.

    Manrope is a variable font whose default axis value is 200 (ExtraLight), so the
    weight must always be set or the face renders spindly.
    """
    try:
        loaded = ImageFont.truetype(str(WEB / "manrope.ttf"), size)
    except OSError:
        return ImageFont.load_default()
    try:
        loaded.set_variation_by_axes([weight])
    except (OSError, ValueError, AttributeError):
        pass
    return loaded


@functools.lru_cache(maxsize=1)
def _palette_reference():
    """A fixed ≤256-colour palette: black plus a ramp per ink colour.

    `_gif_bytes` falls back to median-cut when the frames carry more than 256
    colours, and median-cut averages its boxes — which drifts the background off
    #000000. Flattening to this palette first keeps the exact-palette path, so
    true black survives encoding.
    """
    inks = []
    for value in (COLOR_TIME, COLOR_COLON_OFF, COLOR_DAY, COLOR_DATE,
                  COLOR_RULE, COLOR_COUNTDOWN, COLOR_MARK, COLOR_PROVERB):
        rgb = tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        if rgb not in inks and rgb != (0, 0, 0):
            inks.append(rgb)

    steps = (255 - 1) // len(inks)
    entries = [(0, 0, 0)]
    for rgb in inks:
        for step in range(1, steps + 1):
            t = step / steps
            entries.append(tuple(round(channel * t) for channel in rgb))

    reference = Image.new("P", (1, 1))
    reference.putpalette(([v for colour in entries for v in colour] + [0] * 768)[:768])
    return reference


def _flatten(frame):
    """Map a rendered frame onto the fixed palette, keeping #000000 exact."""
    return frame.quantize(palette=_palette_reference(), dither=Image.Dither.NONE).convert("RGB")


def _tracked_width(draw, text, font, tracking):
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * max(0, len(text) - 1)


def _draw_tracked(draw, x, baseline, text, font, fill, tracking):
    """Letterspaced text drawn from a left x on a shared baseline."""
    for ch in text:
        draw.text((x, baseline), ch, fill=fill, font=font, anchor="ls")
        x += draw.textlength(ch, font=font) + tracking
    return x


def _ink_box(draw, text, font):
    """Ink bounding box of `text` relative to a left/baseline origin."""
    return draw.textbbox((0, 0), text, font=font, anchor="ls")


def _draw_time(draw, dt, colon_on):
    """Time in fixed cells so proportional digits cannot shift the layout.

    Each digit is centred in a cell as wide as the widest digit, which keeps
    `11:11` and `08:08` identically placed.
    """
    font = _font(58, weight=800)
    cell = max(draw.textlength(ch, font=font) for ch in "0123456789")
    colon_cell = draw.textlength(":", font=font) + 8
    digits = f"{dt.hour:02d}{dt.minute:02d}"

    total = 4 * cell + colon_cell
    x = 120 - total / 2

    # Centre the cap height, not the font metrics, on TIME_CENTER_Y.
    box = _ink_box(draw, "0", font)
    baseline = round(TIME_CENTER_Y + (box[3] - box[1]) / 2)

    for index, ch in enumerate(digits):
        if index == 2:
            colour = COLOR_TIME if colon_on else COLOR_COLON_OFF
            draw.text((x + colon_cell / 2, baseline), ":", fill=colour, font=font, anchor="ms")
            x += colon_cell
        draw.text((x + cell / 2, baseline), ch, fill=COLOR_TIME, font=font, anchor="ms")
        x += cell


def _draw_date(draw, dt):
    """`FRI · 23 OCT` — letterspaced, amber weekday, grey date, no year."""
    font = _font(14, weight=700)
    tracking = 2.0
    day = dt.strftime("%a").upper()
    rest = f"{dt.day:02d} {dt.strftime('%b').upper()}"
    sep = "·"

    gap = 7.0
    widths = (_tracked_width(draw, day, font, tracking),
              draw.textlength(sep, font=font),
              _tracked_width(draw, rest, font, tracking))
    x = 120 - (sum(widths) + 2 * gap) / 2

    x = _draw_tracked(draw, x, DATE_BASELINE_Y, day, font, COLOR_DAY, tracking) - tracking
    x += gap
    draw.text((x, DATE_BASELINE_Y), sep, fill=COLOR_DATE, font=font, anchor="ls")
    x += widths[1] + gap
    _draw_tracked(draw, x, DATE_BASELINE_Y, rest, font, COLOR_DATE, tracking)


def _draw_countdown(draw, days):
    """One giant numeral, centred on its own ink, over a short amber rule."""
    text = str(days)
    for size in (104, 92, 80, 70):
        font = _font(size, weight=800)
        box = _ink_box(draw, text, font)
        if box[2] - box[0] <= 212:
            break
    width, height = box[2] - box[0], box[3] - box[1]

    mark_y = HERO_BOTTOM - 6
    top = HERO_TOP + (mark_y - 12 - HERO_TOP - height) / 2
    draw.text((120 - width / 2 - box[0], top - box[1]), text, fill=COLOR_COUNTDOWN, font=font, anchor="ls")
    draw.line((102, mark_y, 138, mark_y), fill=COLOR_MARK, width=2)


def _wrap(draw, words, font, max_width):
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _draw_proverb(draw, proverb):
    """The proverb as large as it will go: step the size down until it fits."""
    words = " ".join(proverb).split()
    max_width, max_height = 212, HERO_BOTTOM - HERO_TOP

    for size in (22, 21, 20, 19, 18, 17, 16, 15, 14):
        font = _font(size, weight=600)
        lines = _wrap(draw, words, font, max_width)
        leading = round(size * 1.34)
        if len(lines) <= 4 and len(lines) * leading <= max_height and \
                max(draw.textlength(line, font=font) for line in lines) <= max_width:
            break

    block = len(lines) * leading
    y = HERO_TOP + (max_height - block) / 2 + leading / 2
    for line in lines:
        draw.text((120, y), line, fill=COLOR_PROVERB, font=font, anchor="mm")
        y += leading


def days_until_target(now_date=None):
    """Return remaining integer days until TARGET_DATE."""
    today = now_date or date.today()
    return max(0, (TARGET_DATE - today).days)


def hourly_proverb(now=None):
    """Deterministically pick the proverb for the current day and hour."""
    dt = now or datetime.now()
    days = days_until_target(dt.date())
    index = (days * 24 + dt.hour) % len(PROVERBS)
    return PROVERBS[index]


def render_countdown_frame(now=None, colon_on=True, state="countdown"):
    """Render a single 240x240 frame on true black.

    state: 'countdown' (the giant numeral) or 'proverb' (the hourly English quote).
    """
    dt = now or datetime.now()
    image = Image.new("RGB", (240, 240), COLOR_BG)
    draw = ImageDraw.Draw(image)

    _draw_time(draw, dt, colon_on)
    _draw_date(draw, dt)
    draw.line((14, RULE_Y, 226, RULE_Y), fill=COLOR_RULE, width=1)

    if state == "countdown":
        _draw_countdown(draw, days_until_target(dt.date()))
    else:
        _draw_proverb(draw, hourly_proverb(dt))

    return _flatten(image)


def render_countdown_gif(now=None, countdown_secs=8, proverb_secs=4):
    """Compile an animated GIF with a 1 Hz blinking colon and state rotation.

    Produces an alternating cycle:
    - countdown_secs of Countdown (with blinking colon)
    - proverb_secs of Hourly Proverb (with blinking colon)
    """
    dt = now or datetime.now()
    frames = []

    # 1 Hz blinking colon: 500ms ON, 500ms OFF
    cd_cycles = max(1, countdown_secs)
    for _ in range(cd_cycles):
        frames.append(render_countdown_frame(dt, colon_on=True, state="countdown"))
        frames.append(render_countdown_frame(dt, colon_on=False, state="countdown"))

    pr_cycles = max(1, proverb_secs)
    for _ in range(pr_cycles):
        frames.append(render_countdown_frame(dt, colon_on=True, state="proverb"))
        frames.append(render_countdown_frame(dt, colon_on=False, state="proverb"))

    return _gif_bytes(frames, ms=500)


def publish_countdown(device_url="http://192.168.1.157", now=None):
    """Render and publish the countdown animated GIF to an SD_PRO clock."""
    dt = now or datetime.now()
    gif_data = render_countdown_gif(dt)
    client = PhotoDisplay(device_url)

    upload_res = client.upload("tokentv.gif", gif_data)
    client.activate(original={"files": []}, name="tokentv.gif")

    days = days_until_target(dt.date())
    proverb = hourly_proverb(dt)

    return {
        "status": "ok",
        "device_url": device_url,
        "bytes": len(gif_data),
        "days_remaining": days,
        "current_proverb": " ".join(proverb),
        "timestamp": dt.isoformat(),
        "upload": upload_res,
    }


def main(argv=None):
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(description="Countdown and Hourly Wisdom Clock")
    parser.add_argument("--device-url", default="http://192.168.1.157", help="clock URL (default: %(default)s)")
    parser.add_argument("--interval", type=int, default=60, help="publish interval in seconds (default: %(default)s)")
    parser.add_argument("--once", action="store_true", help="publish once and exit")
    parser.add_argument("--out", help="save rendered GIF to file instead of publishing")
    args = parser.parse_args(argv)

    if args.out:
        gif = render_countdown_gif()
        Path(args.out).write_bytes(gif)
        print(f"Wrote {len(gif)} bytes to {args.out}")
        return 0

    print(f"TokenTV Countdown starting: target {TARGET_DATE}, device {args.device_url}", flush=True)
    try:
        while True:
            now = datetime.now()
            res = publish_countdown(args.device_url, now=now)
            print(f"[{now.strftime('%H:%M:%S')}] {res['days_remaining']} days remaining · {res['bytes']}B -> {args.device_url}", flush=True)
            if args.once:
                break
            # Sleep until next minute boundary (e.g. at 00s)
            remaining_in_minute = 60 - datetime.now().second
            sleep_sec = min(args.interval, max(5, remaining_in_minute))
            time.sleep(sleep_sec)
    except KeyboardInterrupt:
        print("\nCountdown stopped.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

