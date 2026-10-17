"""The next Apple Calendar event as a feeder snapshot, in the stocks feeder's own pattern.

One scoped ``osascript`` query per poll reads the next future event of a single named calendar
(``riazrahaman``). Scope matters: a naive all-calendars ``whose`` query hangs for minutes on a
big library, but a query scoped with ``tell calendar ...`` answers in about 0.5-3 s, so the
query runs under a hard ``subprocess`` timeout and a failure degrades to last-good — the
previous snapshot file simply stands until a query succeeds, and the consumer renders the age
it can prove (the honesty conventions: absent, or OLD) rather than inventing an event.

All-day events are skipped (an operator decision): the line is a next appointment, not a blank
day block. The snapshot is ``{updated_at, title, start_iso}``, written with the same atomic
tmp-then-rename convention as the stocks feeder, into ``calendar_snapshot`` from the config —
by default beside the stocks feeder's own snapshot in its state directory. The renderer half of
this module (`calendar_parts`, `countdown_text`, `draw_calendar_line`) needs no Calendar access
at all: it draws the quantised countdown line on the combo faces and the usage rest face.

    token-tv calendar --config stocks.json
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from token_tv.display import pixel_text

CALENDAR_NAME = 'riazrahaman'  # the calendar the scoped query reads; a per-calendar scope is proven fast
QUERY_TIMEOUT = 10  # seconds; a scoped query answers in 0.5-3 s, so 10 is a ceiling, not a wait
HORIZON_DAYS = 45  # how far ahead the query looks; recurring instances keep it well populated
DEFAULT_INTERVAL = 300  # the line is a next appointment, not a live feed: five minutes is plenty
MIN_INTERVAL = 60  # a bad config can never hammer the TCC-gated osascript
STALE_AFTER = 600  # an update older than this ends its countdown with OLD, like every other feed
SIZE = 240
CALENDAR_ROW = 231  # the next-event row on every face that draws one: glyphs end at 238, inside the panel

APPLESCRIPT = '''on pad(n)
	if n < 10 then return "0" & n
	return n as text
end pad

on stamp(d)
	return ("" & year of d) & "-" & my pad(month of d as integer) & "-" & my pad(day of d) & "T" & my pad(hours of d) & ":" & my pad(minutes of d) & ":" & my pad(seconds of d)
end stamp

set out to ""
tell application "Calendar"
	tell calendar "%(name)s"
		set now to current date
		set horizon to now + %(horizon)d * days
		repeat with e in (every event whose start date > now and start date < horizon and allday event is false)
			set out to out & my stamp(start date of e) & "	" & (summary of e) & linefeed
		end repeat
	end tell
end tell
return out'''


def build_script(name=CALENDAR_NAME):
    """The scoped calendar query. One calendar named, one window: never an unscoped `whose`."""
    return APPLESCRIPT % {'name': str(name).replace('"', ''), 'horizon': HORIZON_DAYS}


def parse_events(text, now=None):
    """The earliest future event from osascript output, or None. Junk rows are skipped.

    AppleScript answers local wall-clock stamps (naive ISO), so a naive stamp is read on this
    host's own clock — the same clock the operator reads the face with. A past or just-started
    event is not a next event: it is dropped here rather than rendered as a countdown from
    yesterday."""
    now = time.time() if now is None else now
    best = None
    for line in str(text or '').splitlines():
        head, sep, summary = line.partition('\t')
        if not sep:
            continue
        try:
            start = datetime.fromisoformat(head.strip())
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.astimezone()  # naive means local: the feeder's own clock
        stamp = start.timestamp()
        if stamp <= now:
            continue
        title = summary.strip()
        if not title:
            continue
        if best is None or stamp < best[0]:
            best = (stamp, title)
    if best is None:
        return None
    start = datetime.fromtimestamp(best[0]).astimezone()  # aware, host-local
    return {'title': best[1], 'start_epoch': best[0], 'start_iso': start.isoformat(timespec='seconds')}


def query_next(now=None, calendar=CALENDAR_NAME, timeout=QUERY_TIMEOUT):
    """The next timed event, or None on any failure: a timeout, a TCC refusal, a locked calendar.

    Never raises and never touches the network: the only out-of-process call is the local
    `osascript`, which Calendar's own automation permission already covers on this machine."""
    now = time.time() if now is None else now
    try:
        result = subprocess.run(['osascript', '-e', build_script(calendar)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return parse_events(result.stdout, now)


# ---------------------------------------------------------------------------
# The shared next-event line (the combo family's second line, the usage rest's fourth row)
# ---------------------------------------------------------------------------
def countdown_text(remaining):
    """A quantised countdown rounded up, so the line moves on bucket edges, not every minute.

    Under an hour it counts minutes; under ten hours hours-and-minutes; then whole hours to
    the day line, whole days, and beyond two weeks, weeks — the coarsest quantisation that
    still fits 240 px honestly."""
    minutes = max(0, int((remaining + 59) // 60))  # round up like every other countdown here
    if minutes < 60:
        return f'in {minutes}m'
    if minutes < 600:
        return f'in {minutes // 60}h{minutes % 60:02d}m'
    if minutes < 1440:
        return f'in {int(math.ceil(minutes / 60))}h'
    days = minutes // 1440
    if days < 14:
        return f'in {days}d'
    return f'in {days // 7}w'


def calendar_parts(snapshot, now=None):
    """`(title, countdown)` for the next-event line, or None when there is nothing honest to show.

    None covers a missing or junky snapshot (a dash/absent line, never a fabricated event) and a
    past or just-started appointment (no countdown from yesterday). A stale snapshot keeps its
    line but ends with OLD, following the combo family's staleness conventions."""
    calendar = snapshot.get('calendar') if isinstance(snapshot, dict) else None
    if not isinstance(calendar, dict):
        return None
    title = calendar.get('title')
    start_iso = calendar.get('start_iso')
    if not isinstance(title, str) or not title.strip() or not isinstance(start_iso, str):
        return None
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.astimezone()  # a naive stamp means this host's clock
    now = time.time() if now is None else now
    remaining = start.timestamp() - now
    if remaining <= 0:
        return None
    countdown = countdown_text(remaining)
    updated = calendar.get('updated_at')
    if isinstance(updated, bool) or not isinstance(updated, (int, float)) or (now - updated) > STALE_AFTER:
        countdown += ' OLD'
    return (title.strip(), countdown)


def fit_title(title, countdown, width=SIZE, x=6, scale=1):
    """The title truncated so it clears the right-aligned countdown with one glyph to spare."""
    room = (width - 2 * x) - (len(str(countdown)) * 6 - 1) * scale - 6 * scale
    chars = max(0, (room // scale + 1) // 6)
    if chars == 0:
        return ''
    return str(title)[:chars].rstrip()


def draw_calendar_parts(draw, parts, y, ink, *, x=6, width=SIZE, scale=1, countdown_ink=None):
    """Draw pre-computed `(title, countdown)` parts on row `y`; False when they are None.

    Multi-frame GIFs compute the reading once and hand the same parts to every frame, so no
    frame can drift onto the other side of a bucket edge while it is being encoded."""
    if parts is None:
        return False
    title, countdown = parts
    title = fit_title(title, countdown, width, x, scale)
    if title:
        pixel_text(draw, (x, y), title, scale, ink)
    pixel_text(draw, (width - x, y), countdown, scale, countdown_ink if countdown_ink is not None else ink,
               'right')
    return True


def draw_calendar_line(draw, snapshot, y, ink, *, now=None, x=6, width=SIZE, scale=1,
                       countdown_ink=None):
    """Draw title left and countdown right on row `y`; False when there is no honest line."""
    return draw_calendar_parts(draw, calendar_parts(snapshot, now), y, ink, x=x, width=width,
                               scale=scale, countdown_ink=countdown_ink)


# ---------------------------------------------------------------------------
# Feeder (mirrors the stocks runner: its own loop, atomic writes, keep last-good on failure)
# ---------------------------------------------------------------------------
def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def state_directory(args, config):
    if args.state_dir:
        return Path(args.state_dir).expanduser()
    if config.get('state_dir'):
        return Path(config['state_dir']).expanduser()
    return Path(args.config).expanduser().resolve().parent / 'state-calendar'


def snapshot_path(args, config):
    """The snapshot file: `calendar_snapshot` from the config, else the state directory's own."""
    if config.get('calendar_snapshot'):
        return Path(config['calendar_snapshot']).expanduser()
    return state_directory(args, config) / 'calendar-snapshot.json'


def build_snapshot(event, now):
    """The credential-free snapshot; the shape the publisher and every face read."""
    return {'updated_at': int(now), 'title': event['title'], 'start_iso': event['start_iso']}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='token-tv calendar',
                                     description='Write the next Apple Calendar event as a feeder snapshot')
    parser.add_argument('--config', required=True,
                        help='feeder config (the stocks config; `calendar_snapshot` names the snapshot)')
    parser.add_argument('--state-dir', default=None, help='where the calendar snapshot goes')
    parser.add_argument('--interval', type=int, default=None,
                        help=f'seconds between calendar queries (default {DEFAULT_INTERVAL})')
    parser.add_argument('--once', action='store_true', help='query once, write, then exit')
    args = parser.parse_args(argv)

    # The stocks loader is the allowlist this module lives in (STOCK_KEYS): no third config
    # dialect, no credentials, and a device_url is optional because this feeder owns no clock.
    from token_tv.stocks import load_stock_config
    try:
        config = load_stock_config(args.config)
    except OSError as error:
        print(f'Could not read {args.config}: {error.strerror or error}', file=sys.stderr)
        return 1
    except ValueError as error:
        print(f'{args.config} is not a valid calendar config ({error}).', file=sys.stderr)
        return 1

    path = snapshot_path(args, config)
    interval = max(MIN_INTERVAL, int(args.interval if args.interval is not None else DEFAULT_INTERVAL))
    warned = False
    try:
        while True:
            now = time.time()
            event = query_next(now)
            if event is None:
                # Degrade to last-good: the previous snapshot file stands until a query succeeds,
                # so consumers age it (OLD) instead of losing the event on one refused query.
                if not warned:
                    print(f'Calendar query failed; keeping {path} unchanged.', file=sys.stderr, flush=True)
                    warned = True
            else:
                warned = False
                write_json(path, build_snapshot(event, now))
            if args.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
