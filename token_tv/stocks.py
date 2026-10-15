"""A 240×240 stock watchlist face, pushed to the SD_PRO clock like any other TokenTV face.

Quotes come from Finnhub (US equities, free tier) and Yahoo Finance (Indian NSE/BSE equities,
zero-config v8 chart API). The Finnhub API key is read at runtime from ``FINNHUB_API_KEY``,
else from the macOS login Keychain (service ``token-tv-finnhub``), and is never written to the
config, a log, a receipt, a snapshot or the rendered image. Indian stock queries require no API key.
This module is self-contained: it reuses ``token_tv.device`` for the clock and mirrors the capture
→ backup → upload → activate flow of ``token_tv.live``.

Follow-the-sun mode automatically switches between US tickers and Indian tickers based on which
market is currently open.

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

from token_tv.device import (FILES, LIMITS, ROTATION_FILES, STOCKS_FILES, BackupMismatch,
                             lost_rotation, may_write, open_display)
from token_tv.display import encode_jpeg, pixel_text
from token_tv.history import append_history

QUOTE_URL = 'https://finnhub.io/api/v1/quote'
STATUS_URL = 'https://finnhub.io/api/v1/stock/market-status'
YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart'
YAHOO_SCREENER_URL = 'https://query1.finance.yahoo.com/v1/finance/screener'
YAHOO_COOKIE_URL = 'https://fc.yahoo.com'
YAHOO_CRUMB_URL = 'https://query1.finance.yahoo.com/v1/test/getcrumb'
YAHOO_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
SCREENER_PAGE = 12     # candidates per screener POST; the board keeps the top few of each side
KEYCHAIN_SERVICE = 'token-tv-finnhub'
DEFAULT_SYMBOLS_US = ('AAPL', 'MSFT', 'NVDA', 'TSLA')
DEFAULT_SYMBOLS_IN = ('RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS')
DEFAULT_SYMBOLS = DEFAULT_SYMBOLS_US
MAX_SYMBOLS = 4    # the face is laid out for four rows; see render_image
MIN_INTERVAL = 15     # a hard floor: a bad config can never hammer the clock or the API
MAX_INTERVAL = 3600
OPEN_INTERVAL = 30    # seconds between refreshes while the market is open
CLOSED_INTERVAL = 300  # seconds between market-status checks while it is closed
STALE_AFTER = 900     # a live quote older than this is marked OLD
MAGNITUDE_FULL = 5.0  # |change %| that fills the row's magnitude bar
SIZE = 240
DEFAULT_STYLE = 'pixel'
STOCK_MODES = ('watchlist', 'movers')
MOVERS_COUNT = 2      # top-N gainers and top-N losers; the board is 2N rows


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


def _request_json(url, headers=None):
    """Fetch JSON, and on any failure raise a StockError whose text never contains the URL.

    The key travels in the query string, so an HTTPError's own message (which embeds the URL)
    must never escape. ``from None`` also drops the original from the traceback.
    """
    try:
        h = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        if headers:
            h.update(headers)
        with urlopen(Request(url, headers=h), timeout=20) as response:
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


def fetch_yahoo_quote(symbol):
    """Fetch a single stock quote from Yahoo Finance v8 chart API."""
    query = urlencode({'interval': '1d', 'range': '1d'})
    url = f'{YAHOO_CHART_URL}/{symbol}?{query}'
    data = _request_json(url)
    try:
        result = data['chart']['result'][0]
        meta = result['meta']
    except (KeyError, IndexError, TypeError):
        raise StockError('invalid_response') from None

    price = _number(meta.get('regularMarketPrice'))
    if price is None or price <= 0:
        return {'symbol': symbol, 'price': None, 'change': None, 'percent': None,
                'previous_close': None, 'at': None}

    prev_close = _number(meta.get('chartPreviousClose') or meta.get('previousClose'))
    change = (price - prev_close) if (price is not None and prev_close is not None) else _number(meta.get('regularMarketChange'))
    percent = ((change / prev_close) * 100) if (change is not None and prev_close) else _number(meta.get('regularMarketChangePercent'))
    stamp = _number(meta.get('regularMarketTime'))
    return {'symbol': symbol, 'price': price, 'change': change, 'percent': percent,
            'previous_close': prev_close, 'at': int(stamp) if stamp and stamp > 0 else None}


def fetch_market_status(token):
    query = urlencode({'exchange': 'US', 'token': token})
    data = _request_json(STATUS_URL + '?' + query)
    if not isinstance(data, dict):
        raise StockError('status_unavailable')
    is_open = data.get('isOpen')
    session = data.get('session')
    return {'is_open': is_open if isinstance(is_open, bool) else None,
            'session': session if isinstance(session, str) else None}


def is_indian_market_open(epoch=None):
    """True if epoch (or now) falls within regular NSE trading hours (09:15 - 15:30 IST, Mon-Fri)."""
    epoch = time.time() if epoch is None else epoch
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(float(epoch), ZoneInfo('Asia/Kolkata'))
    except Exception:
        dt = datetime.fromtimestamp(float(epoch) + 19800, timezone.utc)
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False
    minute_of_day = dt.hour * 60 + dt.minute
    return (9 * 60 + 15) <= minute_of_day < (15 * 60 + 30)


def is_us_market_open(epoch=None):
    """True if epoch (or now) falls within regular US trading hours (09:30 - 16:00 ET, Mon-Fri)."""
    epoch = time.time() if epoch is None else epoch
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(float(epoch), ZoneInfo('America/New_York'))
    except Exception:
        dt = datetime.fromtimestamp(float(epoch) - 18000, timezone.utc)
    if dt.weekday() >= 5:
        return False
    minute_of_day = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= minute_of_day < (16 * 60)


def select_active_market(config, token=None, now=None, us_status=None):
    """Determine whether the US or Indian market is currently active.

    Returns dict with:
      'exchange': 'US' or 'NSE',
      'is_open': True or False,
      'session': 'regular', 'closed', etc.
      'source': 'FINNHUB' or 'YAHOO',
      'symbols': active symbol list,
      'tz': 'America/New_York' or 'Asia/Kolkata'
    """
    now = time.time() if now is None else now
    us_open = _status_open(us_status) if us_status is not None else is_us_market_open(now)
    in_open = is_indian_market_open(now)

    symbols_us = config.get('symbols', list(DEFAULT_SYMBOLS_US))
    symbols_in = config.get('symbols_in', list(DEFAULT_SYMBOLS_IN))

    if us_open:
        return {
            'exchange': 'US',
            'is_open': True,
            'session': (us_status or {}).get('session') or 'regular',
            'source': 'FINNHUB',
            'symbols': symbols_us,
            'tz': 'America/New_York',
        }
    elif in_open and symbols_in:
        return {
            'exchange': 'NSE',
            'is_open': True,
            'session': 'regular',
            'source': 'YAHOO',
            'symbols': symbols_in,
            'tz': 'Asia/Kolkata',
        }
    else:
        # Both closed: Determine which market closed most recently
        try:
            dt_utc = datetime.fromtimestamp(float(now), timezone.utc)
            hour_minute = dt_utc.hour * 60 + dt_utc.minute
            recent_in = (10 * 60) <= hour_minute < (13 * 60 + 30)
        except Exception:
            recent_in = False

        if recent_in and symbols_in:
            return {
                'exchange': 'NSE',
                'is_open': False,
                'session': 'closed',
                'source': 'YAHOO',
                'symbols': symbols_in,
                'tz': 'Asia/Kolkata',
            }
        else:
            return {
                'exchange': 'US',
                'is_open': False,
                'session': (us_status or {}).get('session') or 'closed',
                'source': 'FINNHUB',
                'symbols': symbols_us,
                'tz': 'America/New_York',
            }


def _status_open(status):
    """True only when a status object says so, whether it is raw or normalised."""
    if isinstance(status, dict):
        if isinstance(status.get('is_open'), bool):
            return status['is_open']
        if isinstance(status.get('isOpen'), bool):
            return status['isOpen']
    return False


def market_poll_interval(status):
    """Seconds between polls: 30 while the market is open, 300 while it is closed."""
    return OPEN_INTERVAL if _status_open(status) else CLOSED_INTERVAL


def collect(symbols, token, source='FINNHUB'):
    """Rows for every symbol; a symbol that fails becomes a dash row. Returns (rows, failures)."""
    rows, failures = [], 0
    for symbol in symbols:
        try:
            if source == 'YAHOO':
                rows.append(fetch_yahoo_quote(symbol))
            else:
                rows.append(fetch_quote(symbol, token))
        except StockError:
            failures += 1
            rows.append(quote_row(symbol, {}))
    return rows, failures


# ---------------------------------------------------------------------------
# Whole-market movers (Yahoo custom screener; no API key, one cookie+crumb per process)
# ---------------------------------------------------------------------------
_yahoo_session_state = None  # cached (cookie, crumb); the crumb is rate-limited, so never per-cycle


def _fetch_yahoo_session():
    """A fresh Yahoo cookie and crumb. Any failure is a StockError; no URL ever escapes."""
    cookie = ''
    try:
        with urlopen(Request(YAHOO_COOKIE_URL, headers={'User-Agent': YAHOO_UA}), timeout=20) as response:
            cookie = response.headers.get('Set-Cookie') or ''
    except HTTPError as error:
        cookie = error.headers.get('Set-Cookie') or ''  # fc.yahoo.com answers 404 with the cookie
    except (URLError, OSError, TimeoutError):
        cookie = ''
    headers = {'User-Agent': YAHOO_UA}
    if cookie:
        headers['Cookie'] = cookie
    try:
        with urlopen(Request(YAHOO_CRUMB_URL, headers=headers), timeout=20) as response:
            crumb = response.read().decode('utf-8', 'replace').strip()
    except (HTTPError, URLError, OSError, TimeoutError):
        raise StockError('crumb_unavailable') from None
    if not crumb or len(crumb) > 32:
        raise StockError('crumb_unavailable')
    return cookie, crumb


def yahoo_session(refresh=False):
    """The process's cached (cookie, crumb), fetched once; ``refresh`` swaps in a fresh crumb."""
    global _yahoo_session_state
    if refresh or _yahoo_session_state is None:
        _yahoo_session_state = _fetch_yahoo_session()
    return _yahoo_session_state


def screener_query(region, sort_type):
    """The screener POST body. Filters mirror Yahoo's own day_gainers/day_losers screens."""
    if region == 'in':
        # India screens that return real NSE/BSE liquidity, verified against the live API.
        operands = [
            {'operator': 'gte', 'operands': ['intradaymarketcap', 100_000_000_000]},
            {'operator': 'gte', 'operands': ['intradayprice', 20]},
            {'operator': 'gt', 'operands': ['dayvolume', 100_000]},
        ]
    else:
        # A mirror of Yahoo's public day_gainers / day_losers screens.
        operands = [
            {'operator': 'gt', 'operands': ['percentchange', 3.0 if sort_type == 'DESC' else -2.5]},
            {'operator': 'gte', 'operands': ['intradaymarketcap', 2_000_000_000]},
            {'operator': 'gte', 'operands': ['intradayprice', 5]},
            {'operator': 'gt', 'operands': ['dayvolume', 15_000 if sort_type == 'DESC' else 20_000]},
        ]
    return {'offset': 0, 'count': SCREENER_PAGE, 'sortField': 'percentchange', 'sortType': sort_type,
            'userId': '', 'userIdType': 'guid', 'quoteType': 'EQUITY',
            'query': {'operator': 'and', 'operands': [{'operator': 'eq', 'operands': ['region', region]}]
                      + operands}}


def _request_post_json(url, payload, headers=None):
    """POST JSON and get JSON back; on any failure raise a URL-free StockError."""
    try:
        h = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': YAHOO_UA}
        if headers:
            h.update(headers)
        body = json.dumps(payload).encode()
        with urlopen(Request(url, data=body, headers=h, method='POST'), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise StockError('http_' + str(error.code)) from None
    except (URLError, OSError, ValueError, TimeoutError):
        raise StockError('unavailable') from None


def screener_quotes(region, sort_type):
    """The raw quote dicts ranked percentchange DESC (gainers) or ASC (losers)."""
    cookie, crumb = yahoo_session()
    headers = {'Cookie': cookie} if cookie else None
    query = urlencode({'crumb': crumb, 'corsDomain': 'finance.yahoo.com', 'formatted': 'false',
                       'lang': 'en-US', 'region': 'US'})
    data = _request_post_json(f'{YAHOO_SCREENER_URL}?{query}', screener_query(region, sort_type),
                              headers=headers)
    try:
        quotes = data['finance']['result'][0]['quotes']
    except (KeyError, IndexError, TypeError):
        raise StockError('invalid_response') from None
    return quotes if isinstance(quotes, list) else []


def screener_rows(quotes):
    """Display rows from screener quotes. Dead or missing quotes are dropped, never fabricated."""
    rows = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = quote.get('symbol')
        price = _number(quote.get('regularMarketPrice'))
        percent = _number(quote.get('regularMarketChangePercent'))
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if price is None or price <= 0 or percent is None:
            continue
        stamp = _number(quote.get('regularMarketTime'))
        rows.append({'symbol': symbol.strip().upper(), 'price': price, 'change': _number(quote.get('regularMarketChange')),
                     'percent': percent, 'previous_close': _number(quote.get('regularMarketPreviousClose')),
                     'at': int(stamp) if stamp and stamp > 0 else None})
    return rows


def dedupe_movers(rows):
    """One row per company: the .NS listing wins over its .BO twin; SME listings are dropped.

    Rows keep their ranked order; a .BO row that later gains an .NS twin is replaced in place."""
    best, order = {}, []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get('symbol') or '').strip().upper()
        if not symbol or symbol.endswith('-SM'):
            continue
        base = display_symbol(symbol)
        if base in best:
            if symbol.endswith('.NS') and not best[base]['symbol'].endswith('.NS'):
                best[base] = dict(row, symbol=symbol)
            continue
        best[base] = dict(row, symbol=symbol)
        order.append(base)
    return [best[base] for base in order]


CRUMB_RETRIES_FOR = ('http_401', 'http_403', 'invalid_response')


def fetch_movers(exchange, count=MOVERS_COUNT):
    """Top gainers then top losers, deduped, as display rows: [G1..Gn, L1..Ln].

    Only auth/contract failures are worth one fresh crumb (getcrumb is rate-limited, so a 429
    or a plain outage must never re-hit it — the caller keeps last-good and backs off instead)."""
    region = 'in' if exchange == 'NSE' else 'us'

    def side(sort_type, descending):
        board = dedupe_movers(screener_rows(screener_quotes(region, sort_type)))
        board.sort(key=lambda row: row['percent'], reverse=descending)  # ranked, whatever the page says
        return board[:max(1, int(count))]

    try:
        gainers, losers = side('DESC', True), side('ASC', False)
    except StockError as error:
        if error.code not in CRUMB_RETRIES_FOR:
            raise
        yahoo_session(refresh=True)  # one fresh crumb, at most
        gainers, losers = side('DESC', True), side('ASC', False)
    return gainers + losers


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


def display_symbol(symbol):
    """Clean symbol for 240x240 display: strip exchange suffix or translate index symbol."""
    s = str(symbol).strip().upper()
    if s == '^NSEI':
        return 'NIFTY'
    if s == '^BSESN':
        return 'SENSEX'
    if s.startswith('^'):
        s = s[1:]
    for suffix in ('.NS', '.BO'):
        if s.endswith(suffix):
            return s[:-len(suffix)]
    return s


def market_time(epoch, tz='America/New_York'):
    """HH:MM in the exchange's clock, or UTC if the timezone database is absent."""
    moment = None
    try:
        from zoneinfo import ZoneInfo
        moment = datetime.fromtimestamp(float(epoch), ZoneInfo(tz))
    except Exception:
        moment = datetime.fromtimestamp(float(epoch), timezone.utc)
    return moment.strftime('%H:%M')


def freshness_text(now, rows, tz='America/New_York'):
    """The newest last-trade time, so a stale frame is obvious. Empty rows are a dash."""
    stamps = [row.get('at') for row in rows if isinstance(row.get('at'), (int, float)) and row.get('at')]
    return market_time(max(stamps), tz=tz) if stamps else '--'


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
    for index, row in enumerate(list(rows)[:MAX_SYMBOLS]):
        y = top + index * pitch
        if index:
            _rule(draw, y - 7, palette)
        sym = display_symbol(row.get('symbol', '?'))
        pixel_text(draw, (8, y), sym, scale=2, color=palette['text'])
        p_str = price_text(row.get('price'))
        p_scale = 2 if len(p_str) >= 7 else 3
        pixel_text(draw, (SIZE - 8, y - (1 if p_scale == 3 else -2)), p_str, scale=p_scale,
                   color=palette['text'], align='right')
        percent = row.get('percent')
        ink = change_ink(percent, palette)
        pixel_text(draw, (8, y + 23), change_text(row.get('change'), percent), scale=2, color=ink)
        _bar(draw, 156, y + 25, SIZE - 8, y + 32, magnitude(percent), ink, palette['dim'])

    _rule(draw, 221, palette)
    source_label = meta.get('exchange') or meta.get('source') or 'FINNHUB'
    pixel_text(draw, (8, 227), str(source_label).upper(), scale=1, color=palette['dim'])
    freshness = freshness_text(meta.get('now'), rows, tz=meta.get('tz', 'America/New_York'))
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
            'market': {'is_open': meta.get('is_open'), 'session': meta.get('session'),
                       'exchange': meta.get('exchange', 'US')},
            'symbols': [dict(row) for row in rows]}


# ---------------------------------------------------------------------------
# Config (its own strict loader; never ``sources.load_config``)
# ---------------------------------------------------------------------------
STOCK_KEYS = ('device_url', 'symbols', 'symbols_in', 'interval', 'palette', 'state_dir', 'reassert_cycles',
              'max_writes_per_hour', 'mode', 'movers_count')
PALETTE_ROLES = ('background', 'text', 'rise', 'fall', 'dim')
CREDENTIAL_WORDS = ('token', 'api_key', 'apikey', 'secret', 'password', 'credential', 'auth', 'key')
SYMBOL_PATTERN = re.compile(r'\^?[A-Z][A-Z0-9.\-]{0,15}')
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
    symbols = data.get('symbols', list(DEFAULT_SYMBOLS_US))
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

    symbols_in = data.get('symbols_in', list(DEFAULT_SYMBOLS_IN))
    if symbols_in is not None:
        if not isinstance(symbols_in, list):
            raise ValueError('symbols_in must be a list of tickers')
        if len(symbols_in) > MAX_SYMBOLS:
            raise ValueError(f'At most {MAX_SYMBOLS} symbols_in are supported')
        seen_in = []
        for symbol in symbols_in:
            if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError(f'{symbol!r} is not an uppercase ticker')
            if symbol in seen_in:
                raise ValueError(f'{symbol} is listed twice')
            seen_in.append(symbol)
        config['symbols_in'] = seen_in

    interval = data.get('interval', OPEN_INTERVAL)
    if (isinstance(interval, bool) or not isinstance(interval, int)
            or not MIN_INTERVAL <= interval <= MAX_INTERVAL):
        raise ValueError(f'interval must be a whole number of seconds from {MIN_INTERVAL} to {MAX_INTERVAL}')
    config['interval'] = interval
    mode = data.get('mode', 'watchlist')
    if mode not in STOCK_MODES:
        raise ValueError(f'mode must be one of: {", ".join(STOCK_MODES)}')
    config['mode'] = mode
    count = data.get('movers_count', MOVERS_COUNT)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_SYMBOLS:
        raise ValueError(f'movers_count must be a whole number from 1 to {MAX_SYMBOLS}')
    config['movers_count'] = count
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
    caps flash-write *events* per hour: prices move on every poll, so the byte dedupe never
    fires while the market is open and a 30-second interval would otherwise refresh continuously.
    The feeder carries no usage screen: its frame goes into the two stock slots only, and the
    usage slot belongs to whichever process owns the device. Each event still shares one
    write-budget decision.
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
            wanted = STOCKS_FILES if name == FILES[0] else (name,)  # the feeder owns no usage slot
            if lost_rotation(files, name, wanted):  # one of the stock slots is gone
                lost = True
                last_upload = {}  # the clock lost a frame; the dedupe must not hide that
        phase = 'upload'
        throttled = False
        if name == FILES[1]:  # a GIF stays a single hard-set file
            events = [(name, image, digest)]
        else:
            events = [(ROTATION_FILES[0], image, digest), (ROTATION_FILES[1], image, digest)]
        pending = [event for event in events if event[2] != (last_upload or {}).get(event[0])]
        if pending:  # only the slots whose bytes changed are rewritten
            # A clock showing nothing bypasses the budget; the cap is there to spare flash, not
            # to withhold the frame.
            if lost or may_write(last_write, at, budget):
                # The local mirrors are written as the publish event starts, just before the
                # upload requests — a failed upload leaves a mirror ahead of the clock, which
                # the receipt's error record shows.
                for slot, frame, _ in pending:
                    (state_dir / slot).write_bytes(frame)
                for upload in device.upload_rotation([(slot, frame) for slot, frame, _ in pending]):
                    receipts.append(dict(upload, sha256=digest))
                last_upload = dict((last_upload or {}), **{slot: digest for slot, _, _ in pending})
                last_write = at
                uploaded = True
            else:
                throttled = True  # last_upload is left alone, so the newest frame retries
        if reasserted:
            phase = 'activate'
            device.activate(original, name, wanted)
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
RELOAD_KEYS = ('symbols', 'symbols_in', 'mode', 'movers_count', 'interval')


def reload_stock_config(path, config, mtime):
    """Re-read the stock config when its file changed; a bad edit keeps the last-good one.

    Mid-run changes to the tickers, mode, movers count and interval take effect the next
    cycle; the device, state directory and palette are decided at startup. Startup itself
    still exits on an invalid file, as before."""
    path = Path(path)
    try:
        fresh = path.stat().st_mtime
    except OSError:
        return config, mtime
    if mtime is not None and fresh == mtime:
        return config, mtime
    try:
        updated = load_stock_config(path)
    except (OSError, ValueError) as error:
        print(f'Ignoring stock config change: {error}', flush=True)
        return config, fresh
    config.update({key: updated[key] for key in RELOAD_KEYS if key in updated})
    return config, fresh


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

    try:
        config_mtime = Path(args.config).stat().st_mtime
    except OSError:
        config_mtime = None

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

    if config.get('mode', 'watchlist') == 'movers':
        token = None  # the screener is keyless; movers mode needs no credentials
    else:
        try:
            token = finnhub_token()
        except StockError:
            print('No Finnhub API key. Set FINNHUB_API_KEY, or store it in the macOS Keychain '
                  f'(security add-generic-password -s {KEYCHAIN_SERVICE} -w).', file=sys.stderr)
            return 1

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
    last_movers = []  # the previous board, kept whole when a screener poll fails
    warned_no_token = False
    try:
        while True:
            now = time.time()
            cycles += 1
            config, config_mtime = reload_stock_config(args.config, config, config_mtime)
            mode = config.get('mode', 'watchlist')
            interval = args.interval if args.interval is not None else config.get('interval', OPEN_INTERVAL)
            interval = max(MIN_INTERVAL, int(interval))
            if mode == 'watchlist' and token is None:
                # A hot edit switched back to the watchlist: pick up a token if one appeared.
                try:
                    token = finnhub_token()
                except StockError:
                    if not warned_no_token:
                        print('No Finnhub API key for the watchlist. Set FINNHUB_API_KEY, or store it in the '
                              f'macOS Keychain (security add-generic-password -s {KEYCHAIN_SERVICE} -w); '
                              'rows show dashes until a key appears.', file=sys.stderr, flush=True)
                        warned_no_token = True
            try:
                status = fetch_market_status(token) if token is not None else None
            except StockError:
                # Keep the last known status; the frame must not flip to CLOSED on one hiccup.
                pass
            active = select_active_market(config, token, now=now, us_status=status)
            if mode == 'movers':
                active['source'] = 'YAHOO'  # the screener serves both markets, via Yahoo
                try:
                    rows = fetch_movers(active['exchange'], config.get('movers_count', MOVERS_COUNT))
                    last_movers = rows
                    quote_failures = 0
                except StockError:
                    # The screener failed twice: keep the previous board.
                    rows = last_movers
                    quote_failures = 1
            elif token is None:
                rows = [quote_row(symbol, {}) for symbol in active['symbols']]  # dashes, never zeros
                quote_failures = len(rows)
            else:
                rows, quote_failures = collect(active['symbols'], token, source=active['source'])
            is_open = active['is_open']
            meta = {'is_open': is_open, 'session': active['session'], 'source': active['source'],
                    'exchange': active['exchange'], 'tz': active['tz'],
                    'now': now, 'palette': config.get('palette')}
            image = render_watchlist(rows, meta, style=DEFAULT_STYLE, limit=LIMITS[FILES[0]][0])
            payload = snapshot_payload(rows, meta)
            write_json(state_dir / 'snapshot.json', payload)
            append_history(state_dir / 'history.jsonl', payload)
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
            wait = interval if is_open else market_poll_interval({'is_open': is_open})
            failures = failures + 1 if (quote_failures or (token is not None and not status)) else 0
            if failures:
                wait = min(max(wait, interval * (2 ** min(failures, 3))), 600)
            time.sleep(max(MIN_INTERVAL, wait))
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
