"""One 240×240 face carrying both AI usage and a stock watchlist, for a single clock.

The screen is about an inch and a half across, so seven rows of detail is the most it will take.
This face therefore shows less per row than either dedicated face: no mascots, no countdown, no
per-row sparkline. It also only exists while the market is open — outside the session there is
nothing moving to report, so `render_combo` hands back the full-size usage face instead, and the
clock spends most of the day on the readable layout.

The watchlist rows arrive through `snapshot['stocks']`, which the publisher fills from the feeder's
own snapshot file. Nothing here contacts a provider or a clock.
"""
import time

from PIL import Image, ImageDraw

from token_tv.display import (BACKGROUND, MUTED, TEXT, account_label, gauge_color, horizontal_gauge,
                              overview_rows, pixel_text, reading, render_pixel)
from token_tv.stocks import PALETTE, change_ink, display_symbol, price_text
from token_tv.calendar import CALENDAR_ROW, draw_calendar_line
from token_tv.display import (BACKGROUND, MUTED, TEXT, account_label, gauge_color, horizontal_gauge,
                              overview_rows, pixel_text, reading, render_pixel)
from token_tv.stocks import PALETTE, change_ink, display_symbol, price_text

SIZE = 240
# A reading older than this is not worth showing as live; the market section is dropped instead.
STALE_AFTER = 600
AI_TOP, AI_PITCH = 14, 30
RULE_Y = 104
MARKET_LABEL_Y = 108
MARKET_TOP, MARKET_PITCH = 122, 29
CALENDAR_Y = CALENDAR_ROW  # the next-event line's row, shared with the usage rest face's fourth row


def market_rows(snapshot):
    """The watchlist rows to draw, or None when the market section does not belong on screen.

    None means closed, missing, or stale: the caller then renders the usage face full size rather
    than showing prices that stopped moving hours ago."""
    stocks = snapshot.get('stocks')
    if not isinstance(stocks, dict):
        return None
    if not (stocks.get('market') or {}).get('is_open'):
        return None
    updated = stocks.get('updated_at')
    if not updated or (time.time() - updated) > STALE_AFTER:
        return None
    rows = [row for row in stocks.get('symbols') or [] if isinstance(row, dict)]
    return rows[:4] or None


def _rule(draw, y):
    draw.line([(6, y), (SIZE - 6, y)], fill=PALETTE['dim'])


def _ai_row(draw, y, row):
    """Provider code, a usage gauge, the percentage, and the window the percentage is of.

    The window label matters: 75% of a five-hour window is not 75% of a week. The countdown is the
    one thing dropped here — at this row height it would be unreadable, and the full-size face
    still carries it."""
    used, period, old, _ = reading(row)
    pixel_text(draw, (6, y + 4), account_label(row).replace(' ', '')[:3].upper(), scale=2, color=TEXT)
    horizontal_gauge(draw, (48, y + 7), used, step=12, width=10, height=9)
    number = '--' if used is None else str(round(used)) + '%'
    ink = MUTED if used is None else '#%02x%02x%02x' % gauge_color(used)
    pixel_text(draw, (SIZE - 6, y + 2), number, scale=2, color=ink, align='right')
    pixel_text(draw, (SIZE - 6, y + 19), (period + ' OLD') if old else period, scale=1, color=PALETTE['dim'],
               align='right')


def _market_row(draw, y, row):
    sym = display_symbol(row.get('symbol', '?'))[:5].upper()
    pixel_text(draw, (6, y + 3), sym, scale=2, color=PALETTE['text'])
    pixel_text(draw, (146, y + 3), price_text(row.get('price')), scale=2, color=TEXT, align='right')
    percent = row.get('percent')
    if percent is None:
        pixel_text(draw, (SIZE - 6, y + 3), '--', scale=2, color=MUTED, align='right')
    elif percent > 0:
        pixel_text(draw, (SIZE - 6, y + 3), f'▲{abs(percent):.2f}%', scale=2, color=(8, 247, 66), align='right')
    elif percent < 0:
        pixel_text(draw, (SIZE - 6, y + 3), f'▼{abs(percent):.2f}%', scale=2, color=(255, 68, 68), align='right')
    else:
        pixel_text(draw, (SIZE - 6, y + 3), f' {abs(percent):.2f}%', scale=2, color=PALETTE['dim'], align='right')


def render_combo(snapshot):
    """The composite while the market is open, otherwise the ordinary full-size usage face."""
    rows = market_rows(snapshot)
    if rows is None:
        return render_pixel(snapshot)
    image = Image.new('RGB', (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)

    accounts = overview_rows(snapshot)
    pixel_text(draw, (6, 2), 'AI USAGE', scale=1, color=PALETTE['dim'])
    for index, account in enumerate(accounts[:3]):
        _ai_row(draw, AI_TOP + index * AI_PITCH, account)

    _rule(draw, RULE_Y)
    market_info = (snapshot.get('stocks') or {}).get('market', {})
    session = str(market_info.get('session') or 'OPEN').upper()
    pixel_text(draw, (6, MARKET_LABEL_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['dim'])
    source_label = market_info.get('exchange') or (snapshot.get('stocks') or {}).get('source') or 'FINNHUB'
    pixel_text(draw, (SIZE - 6, MARKET_LABEL_Y), str(source_label).upper(), scale=1, color=PALETTE['dim'], align='right')
    for index, row in enumerate(rows):
        _market_row(draw, MARKET_TOP + index * MARKET_PITCH, row)
    # The next event, in its own row under the whole layout: absent when the feed has none,
    # OLD when its snapshot went stale — never a countdown nobody published.
    draw_calendar_line(draw, snapshot, CALENDAR_Y, PALETTE['text'])
    return image
