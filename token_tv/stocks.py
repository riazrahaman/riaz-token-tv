"""A 240×240 stock watchlist face, pushed to the SD_PRO clock like any other TokenTV face.

Quotes come from the Finnhub free tier. The API key is read at runtime from ``FINNHUB_API_KEY``,
else from the macOS login Keychain (service ``token-tv-finnhub``), and is never written to the
config, a log, a receipt, a snapshot or the rendered image. This module is self-contained: it
reuses ``token_tv.device`` for the clock and mirrors the capture → backup → upload → activate
flow of ``token_tv.live``.

    token-tv stocks --config stocks.json
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from token_tv.device import FILES, LIMITS, BackupMismatch, may_write, open_display
from token_tv.display import encode_jpeg, pixel_text

QUOTE_URL = 'https://finnhub.io/api/v1/quote'
STATUS_URL = 'https://finnhub.io/api/v1/stock/market-status'
KEYCHAIN_SERVICE = 'token-tv-finnhub'
DEFAULT_SYMBOLS = ('AAPL', 'MSFT', 'NVDA', 'TSLA')
MAX_SYMBOLS = 6
MIN_INTERVAL = 15     # a hard floor: a bad config can never hammer the clock or the API
MAX_INTERVAL = 3600
OPEN_INTERVAL = 30    # seconds between refreshes while the US market is open
CLOSED_INTERVAL = 300  # seconds between market-status checks while it is closed
STALE_AFTER = 900     # a live quote older than this is marked OLD
MAGNITUDE_FULL = 5.0  # |change %| that fills the row's magnitude bar
SIZE = 240
DEFAULT_STYLE = 'pixel'


class StockError(Exception):
    """A stock source failure; the code never carries a URL, and so never a key."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# The clock's own palette, decoded from the RGB565 ints its /config reports.
# ---------------------------------------------------------------------------
def rgb565_to_rgb(value):
    """Decode a 16-bit RGB565 int to an 8-bit (r, g, b) tuple."""
    value = int(value) & 0xFFFF
    red = (value >> 11) & 0x1F
    green = (value >> 5) & 0x3F
    blue = value & 0x1F
    return (round(red * 255 / 31), round(green * 255 / 63), round(blue * 255 / 31))


# color1/color2/color3 from the SD_PRO /config endpoint.
ORANGE = rgb565_to_rgb(64388)  # #FF7121
BLUE = rgb565_to_rgb(19199)    # #4A5DFF
GREEN = rgb565_to_rgb(4008)    # #08F742
DIM = (74, 74, 74)             # dark grey for rules and dim text

PALETTE = {'background': (0, 0, 0), 'text': ORANGE, 'rise': GREEN, 'fall': BLUE, 'dim': DIM}


def finnhub_token():
    """The Finnhub API key from the environment, or on macOS from the login Keychain (read only).

    Exactly mirrors ``token_tv.sources.ollama_token``. The key is never printed and never
    written anywhere: to store it, run
    ``security add-generic-password -s token-tv-finnhub -w``.
    """
    token = os.environ.get('FINNHUB_API_KEY')
    if token and token.strip():
        return token.strip()
    if sys.platform == 'darwin':
        result = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN_SERVICE, '-w'],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise StockError('auth_required')


def _request_json(url):
    """Fetch JSON, and on any failure raise a StockError whose text never contains the URL.

    The key travels in the query string, so an HTTPError's own message (which embeds the URL)
    must never escape. ``from None`` also drops the original from the traceback.
    """
    try:
        with urlopen(Request(url, headers={'Accept': 'application/json'}), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise StockError('http_' + str(error.code)) from None
    except (URLError, OSError, ValueError, TimeoutError):
        raise StockError('unavailable') from None


def _number(value):
    """A finite float, or None. Booleans and junk are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def quote_row(symbol, data):
    """One display row from a Finnhub quote. A symbol with no data is a dash, never a 0."""
    data = data if isinstance(data, dict) else {}
    price = _number(data.get('c'))
    if price is None or price <= 0:  # Finnhub answers all-zero fields when it has no quote
        return {'symbol': symbol, 'price': None, 'change': None, 'percent': None,
                'previous_close': None, 'at': None}
    stamp = _number(data.get('t'))
    return {'symbol': symbol, 'price': price, 'change': _number(data.get('d')),
            'percent': _number(data.get('dp')), 'previous_close': _number(data.get('pc')),
            'at': int(stamp) if stamp and stamp > 0 else None}


def fetch_quote(symbol, token):
    query = urlencode({'symbol': symbol, 'token': token})
    return quote_row(symbol, _request_json(QUOTE_URL + '?' + query))


def fetch_market_status(token):
    query = urlencode({'exchange': 'US', 'token': token})
    data = _request_json(STATUS_URL + '?' + query)
    if not isinstance(data, dict):
        raise StockError('status_unavailable')
    is_open = data.get('isOpen')
    session = data.get('session')
    return {'is_open': is_open if isinstance(is_open, bool) else None,
            'session': session if isinstance(session, str) else None}


def _status_open(status):
    """True only when a status object says so, whether it is raw or normalised."""
    if isinstance(status, dict):
        if isinstance(status.get('is_open'), bool):
            return status['is_open']
        if isinstance(status.get('isOpen'), bool):
            return status['isOpen']
    return False


def market_poll_interval(status):
    """Seconds between polls: 30 while the US market is open, 300 while it is closed."""
    return OPEN_INTERVAL if _status_open(status) else CLOSED_INTERVAL


def collect(symbols, token):
    """Rows for every symbol; a symbol that fails becomes a dash row. Returns (rows, failures)."""
    rows, failures = [], 0
    for symbol in symbols:
        try:
            rows.append(fetch_quote(symbol, token))
        except StockError:
            failures += 1
            rows.append(quote_row(symbol, {}))
    return rows, failures


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def change_ink(percent, palette=None):
    """Green for a rising price, blue for a falling one, orange for flat, grey for unknown."""
    palette = palette or PALETTE
    if percent is None:
        return palette['dim']
    if percent > 0:
        return palette['rise']
    if percent < 0:
        return palette['fall']
    return palette['text']


def magnitude(percent):
    """0..1 fraction of the row bar that |change %| fills."""
    if percent is None:
        return 0.0
    return max(0.0, min(1.0, abs(percent) / MAGNITUDE_FULL))


def price_text(price):
    return '--' if price is None else f'{price:.2f}'


def change_text(change, percent):
    """The day change with its sign, or a dash when there is no quote."""
    parts = []
    if change is not None:
        parts.append(f'{change:+.2f}')
    if percent is not None:
        parts.append(f'{percent:+.2f}%')
    return ' '.join(parts) if parts else '--'


def market_time(epoch):
    """HH:MM in New York (the exchange's clock), or UTC if the timezone database is absent."""
    moment = None
    try:
        from zoneinfo import ZoneInfo
        moment = datetime.fromtimestamp(float(epoch), ZoneInfo('America/New_York'))
    except Exception:
        moment = datetime.fromtimestamp(float(epoch), timezone.utc)
    return moment.strftime('%H:%M')


def freshness_text(now, rows):
    """The newest last-trade time, so a stale frame is obvious. Empty rows are a dash."""
    stamps = [row.get('at') for row in rows if isinstance(row.get('at'), (int, float)) and row.get('at')]
    return market_time(max(stamps)) if stamps else '--'


def _is_stale(now, rows):
    if not isinstance(now, (int, float)):
        return False
    stamps = [row.get('at') for row in rows if isinstance(row.get('at'), (int, float)) and row.get('at')]
    return not stamps or (now - max(stamps)) > STALE_AFTER


def _pixel_width(text, scale=1):
    return max(0, len(str(text)) * 6 - 1) * scale


def _centred(draw, y, text, scale, color):
    text = str(text)
    pixel_text(draw, ((SIZE - _pixel_width(text, scale)) // 2, y), text, scale=scale, color=color)


def _rule(draw, y, palette):
    for x in range(8, SIZE - 8, 4):
        draw.point((x, y), fill=palette['dim'])


def _bar(draw, x0, y0, x1, y1, fraction, ink, track):
    draw.rectangle((x0, y0, x1, y1), fill=track)
    filled = round((x1 - x0 + 1) * max(0.0, min(1.0, fraction)))
    if filled > 0:
        draw.rectangle((x0, y0, x0 + filled - 1, y1), fill=ink)


def render_image(rows, meta, style=DEFAULT_STYLE):
    """The 240×240 PIL image for a watchlist frame."""
    if style != DEFAULT_STYLE:
        raise ValueError('Unknown display style')
    palette = dict(PALETTE)
    palette.update(meta.get('palette') or {})
    image = Image.new('RGB', (SIZE, SIZE), palette['background'])
    draw = ImageDraw.Draw(image)

    is_open = meta.get('is_open')
    session = str(meta.get('session') or '').upper().strip()
    if is_open is True:
        heading, heading_ink = 'MARKET OPEN', palette['text']
    elif is_open is False:
        heading, heading_ink = 'MARKET CLOSED', palette['dim']
    else:
        heading, heading_ink = 'MARKET --', palette['dim']
    _centred(draw, 4, heading, 2, heading_ink)
    if session:
        _centred(draw, 22, session, 1, palette['dim'])

    _rule(draw, 34, palette)
    top, pitch = 40, 47
    for index, row in enumerate(list(rows)[:4]):
        y = top + index * pitch
        if index:
            _rule(draw, y - 7, palette)
        pixel_text(draw, (8, y), str(row.get('symbol', '?')), scale=2, color=palette['text'])
        pixel_text(draw, (SIZE - 8, y - 1), price_text(row.get('price')), scale=3,
                   color=palette['text'], align='right')
        percent = row.get('percent')
        ink = change_ink(percent, palette)
        pixel_text(draw, (8, y + 23), change_text(row.get('change'), percent), scale=2, color=ink)
        _bar(draw, 156, y + 25, SIZE - 8, y + 32, magnitude(percent), ink, palette['dim'])

    _rule(draw, 221, palette)
    pixel_text(draw, (8, 227), 'FINNHUB', scale=1, color=palette['dim'])
    freshness = freshness_text(meta.get('now'), rows)
    if is_open and _is_stale(meta.get('now'), rows):
        freshness = 'OLD ' + freshness
    pixel_text(draw, (SIZE - 8, 227), freshness, scale=1,
               color=palette['text'] if is_open else palette['dim'], align='right')
    return image


def render_watchlist(rows, meta, style=DEFAULT_STYLE, limit=0):
    """The 240×240 JPEG bytes for a watchlist frame.

    A watchlist is the busiest face TokenTV draws, so it sits closest to the firmware's size
    limit; `limit` steps the quality down rather than letting the clock reject the upload."""
    return encode_jpeg(render_image(rows, meta, style=style), limit)[0]


def snapshot_payload(rows, meta):
    """The credential-free snapshot written beside the frame. It holds numbers only."""
    return {'updated_at': int(meta.get('now') or 0), 'source': meta.get('source', 'FINNHUB'),
            'market': {'is_open': meta.get('is_open'), 'session': meta.get('session')},
            'symbols': [dict(row) for row in rows]}


# ---------------------------------------------------------------------------
# Config (its own strict loader; never ``sources.load_config``)
# ---------------------------------------------------------------------------
STOCK_KEYS = ('device_url', 'symbols', 'interval', 'palette', 'state_dir', 'reassert_cycles',
              'max_writes_per_hour')
PALETTE_ROLES = ('background', 'text', 'rise', 'fall', 'dim')
CREDENTIAL_WORDS = ('token', 'api_key', 'apikey', 'secret', 'password', 'credential', 'auth', 'key')
SYMBOL_PATTERN = re.compile(r'[A-Z][A-Z0-9.\-]{0,9}')
COLOUR_PATTERN = re.compile(r'#[0-9a-fA-F]{6}')


def _stock_device_url(text):
    parsed = urlparse(text) if isinstance(text, str) else None
    if (parsed is None or parsed.scheme != 'http' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.path not in ('', '/')):
        raise ValueError('device_url must be a plain local http address, e.g. http://192.168.0.50')
    return text.rstrip('/')


def _parse_colour(role, value):
    if isinstance(value, bool):
        raise ValueError(f'palette.{role} must be an RGB565 integer or a #RRGGBB string')
    if isinstance(value, int):
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f'palette.{role} is not a 16-bit RGB565 value')
        return rgb565_to_rgb(value)
    if isinstance(value, str) and COLOUR_PATTERN.fullmatch(value):
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    raise ValueError(f'palette.{role} must be an RGB565 integer or a #RRGGBB string')


def parse_palette(value):
    if not isinstance(value, dict):
        raise ValueError('palette must be an object of colour roles')
    unknown = sorted(set(value) - set(PALETTE_ROLES))
    if unknown:
        raise ValueError('Unknown palette roles: ' + ', '.join(unknown))
    return {role: _parse_colour(role, colour) for role, colour in value.items()}


def load_stock_config(path):
    """The validated watchlist config. Only these keys are allowed; a credential never is."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError('The stock config must be a JSON object')
    credentials = sorted(key for key in data if any(word in key.lower() for word in CREDENTIAL_WORDS))
    if credentials:
        raise ValueError('Credentials are not allowed in the stock config; use FINNHUB_API_KEY '
                         'or the macOS Keychain (service ' + KEYCHAIN_SERVICE + ')')
    unknown = sorted(set(data) - set(STOCK_KEYS))
    if unknown:
        raise ValueError('Unsupported stock configuration field: ' + ', '.join(unknown))

    config = {}
    if 'device_url' in data:
        config['device_url'] = _stock_device_url(data['device_url'])
    symbols = data.get('symbols', list(DEFAULT_SYMBOLS))
    if not isinstance(symbols, list) or not symbols:
        raise ValueError('symbols must be a non-empty list of tickers')
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f'At most {MAX_SYMBOLS} symbols are supported')
    seen = []
    for symbol in symbols:
        if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f'{symbol!r} is not an uppercase ticker')
        if symbol in seen:
            raise ValueError(f'{symbol} is listed twice')
        seen.append(symbol)
    config['symbols'] = seen
    interval = data.get('interval', OPEN_INTERVAL)
    if (isinstance(interval, bool) or not isinstance(interval, int)
            or not MIN_INTERVAL <= interval <= MAX_INTERVAL):
        raise ValueError(f'interval must be a whole number of seconds from {MIN_INTERVAL} to {MAX_INTERVAL}')
    config['interval'] = interval
    if 'palette' in data:
        config['palette'] = parse_palette(data['palette'])
    if 'state_dir' in data:
        state = data['state_dir']
        if not isinstance(state, str) or not state.strip():
            raise ValueError('state_dir must be a non-empty path')
        config['state_dir'] = state
    return config


# ---------------------------------------------------------------------------
# Publishing (mirrors token_tv.live: capture → backup → upload → activate)
# ---------------------------------------------------------------------------
def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_original(device, backup_path):
    """The clock's original state; save it first only if no backup exists yet."""
    backup_path = Path(backup_path)
    if backup_path.is_file():
        original = json.loads(backup_path.read_text())
        device.check_backup(original)  # a backup from another kind of clock is kept, nothing sent
        return original
    original = device.capture()
    write_json(backup_path, original)
    return original


def mismatch_message(error, backup_path):
    return (f'{error} Nothing was changed on the clock and {backup_path} was kept. If the clock was '
            'offline when the stock runner started, start it again. If you replaced the clock, put the '
            'old one back to restore it, or move that file away so a new backup is saved.')


def publish(device, image, state_dir, backup_path, original, active_file, last_upload, *,
            style=DEFAULT_STYLE, now=None, reassert=False, budget=0, last_write=None):
    """Upload only new bytes, activate only a new file, and write a receipt.

    Returns (original, active_file, last_upload, uploaded). A ``BackupMismatch`` is re-raised so
    the caller can stop without touching the clock. `reassert` re-selects the photo theme even
    when the file name is unchanged, for a clock that rebooted back to its own theme. `budget`
    caps flash writes per hour: prices move on every poll, so the byte dedupe never fires while
    the market is open and a 30-second interval would otherwise rewrite flash 120 times an hour.
    """
    state_dir = Path(state_dir)
    at = int(now if now is not None else time.time())
    receipts = []
    phase = 'backup'
    uploaded = False
    try:
        if original is None:
            original = load_original(device, backup_path)
        phase = 'upload'
        name = FILES[1] if image[:4] == b'GIF8' else FILES[0]
        digest = hashlib.sha256(image).hexdigest()
        state_dir.mkdir(parents=True, exist_ok=True)
        reasserted = active_file != name or reassert
        lost = False
        if reasserted:
            phase = 'activate'
            files = device.capture().get('files')
            lost = files is not None and not any(f['name'] == name for f in files)
            if lost:
                last_upload = None  # the clock lost our file; the dedupe must not hide that
        phase = 'upload'
        throttled = False
        if (name, digest) != last_upload:  # identical frames are not rewritten to flash
            # A clock showing nothing bypasses the budget; the cap is there to spare flash, not
            # to withhold the frame.
            if lost or may_write(last_write, at, budget):
                # Written only on upload, so the local copy stays a record of what the clock is
                # actually showing.
                (state_dir / name).write_bytes(image)
                receipts.append(dict(device.upload(name, image), sha256=digest))
                last_upload = (name, digest)
                uploaded = True
            else:
                throttled = True  # last_upload is left alone, so the newest frame retries
        if reasserted:
            phase = 'activate'
            device.activate(original, name)
            active_file = name
        write_json(state_dir / 'display-receipt.json',
                   {'at': at, 'style': style, 'uploads': receipts, 'reasserted': reasserted,
                    'throttled': throttled})
    except BackupMismatch:
        write_json(state_dir / 'display-receipt.json',
                   {'at': at, 'style': style, 'status': 'error', 'phase': phase, 'uploads': receipts})
        raise
    except (OSError, ValueError, KeyError) as error:
        write_json(state_dir / 'display-receipt.json',
                   {'at': at, 'style': style, 'status': 'error', 'phase': phase,
                    'uploads': receipts, 'http_status': getattr(error, 'code', None)})
    return original, active_file, last_upload, uploaded


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _state_directory(args, config):
    if args.state_dir:
        return Path(args.state_dir).expanduser()
    if config.get('state_dir'):
        return Path(config['state_dir']).expanduser()
    return Path(args.config).expanduser().resolve().parent / 'state-stocks'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='token-tv stocks',
                                     description='Draw a stock watchlist and push it to the clock')
    parser.add_argument('--config', required=True, help='stock watchlist config (no credential)')
    parser.add_argument('--state-dir', default=None, help='where the backup, receipts and snapshots go')
    parser.add_argument('--interval', type=int, default=None,
                        help=f'seconds between refreshes while the market is open (floor {MIN_INTERVAL})')
    parser.add_argument('--once', action='store_true', help='publish one frame, then exit')
    parser.add_argument('--restore-display', action='store_true', help="put the clock back to its saved state")
    args = parser.parse_args(argv)

    try:
        config = load_stock_config(args.config)
    except OSError as error:
        print(f'Could not read {args.config}: {error.strerror or error}', file=sys.stderr)
        return 1
    except ValueError as error:
        print(f'{args.config} is not a valid stock config ({error}).', file=sys.stderr)
        return 1

    state_dir = _state_directory(args, config)
    backup_path = state_dir / 'display-original.json'
    device = open_display(config['device_url']) if config.get('device_url') else None

    if args.restore_display:
        if not device or not backup_path.is_file():
            print('A clock and its original-state backup are required to restore the display.', file=sys.stderr)
            return 1
        original = json.loads(backup_path.read_text())
        try:
            device.check_backup(original)
        except BackupMismatch as error:
            raise SystemExit(mismatch_message(error, backup_path))
        device.restore(original)
        print('Original display selection restored.')
        return 0

    try:
        token = finnhub_token()
    except StockError:
        print('No Finnhub API key. Set FINNHUB_API_KEY, or store it in the macOS Keychain '
              f'(security add-generic-password -s {KEYCHAIN_SERVICE} -w).', file=sys.stderr)
        return 1

    interval = args.interval if args.interval is not None else config.get('interval', OPEN_INTERVAL)
    interval = max(MIN_INTERVAL, int(interval))

    original = active_file = last_upload = None
    # Re-assert the photo theme periodically: a reboot or a visit to the clock's own web UI drops
    # it back to its theme, and `activate` only ran when the file name changed, i.e. once.
    reassert_after = max(1, int(config.get('reassert_cycles', 12)))
    # Off by default. Prices move on every poll, so the byte dedupe never fires while the market
    # is open and a 30-second interval rewrites flash ~120 times an hour — but a watchlist that
    # refreshes slowly is not a watchlist, so the trade-off is the owner's to make, not ours.
    budget = max(0, int(config.get('max_writes_per_hour', 0)))
    status, failures = {}, 0
    cycles, last_write = 0, None
    try:
        while True:
            now = time.time()
            cycles += 1
            try:
                status = fetch_market_status(token)
            except StockError:
                # Keep the last known status; the frame must not flip to CLOSED on one hiccup.
                pass
            rows, quote_failures = collect(config['symbols'], token)
            is_open = _status_open(status)
            meta = {'is_open': is_open, 'session': status.get('session'), 'source': 'FINNHUB',
                    'now': now, 'palette': config.get('palette')}
            image = render_watchlist(rows, meta, style=DEFAULT_STYLE, limit=LIMITS[FILES[0]][0])
            write_json(state_dir / 'snapshot.json', snapshot_payload(rows, meta))
            if device:
                try:
                    original, active_file, last_upload, uploaded = publish(
                        device, image, state_dir, backup_path, original, active_file, last_upload,
                        now=now, reassert=cycles % reassert_after == 0,
                        budget=budget, last_write=last_write)
                    if uploaded:
                        last_write = now
                except BackupMismatch as error:
                    print(mismatch_message(error, backup_path), flush=True)
                    return 1
            else:
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / FILES[0]).write_bytes(image)
                write_json(state_dir / 'display-receipt.json',
                           {'at': int(now), 'style': DEFAULT_STYLE, 'preview': True})
                print(f'No clock configured; wrote {state_dir / FILES[0]}', flush=True)
            if args.once:
                break
            wait = interval if is_open else market_poll_interval(status)
            failures = failures + 1 if (quote_failures or not status) else 0
            if failures:
                wait = min(max(wait, interval * (2 ** min(failures, 3))), 600)
            time.sleep(max(MIN_INTERVAL, wait))
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
