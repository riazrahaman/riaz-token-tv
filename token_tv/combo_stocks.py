"""One 240×240 face carrying only the stock board: no usage rows, no provider rows.

The user asked for a screen that shows the four market rows alone, so the rows the `combo` face
shares halve its height, these fill the whole panel instead. The rows arrive through
`snapshot['stocks']` exactly as for `combo` — gainers first, then losers in movers mode, the
watchlist order otherwise. A closed, missing or stale feed draws dash rows under a CLOSED label,
never a fabricated 0.00. Nothing here contacts a provider or a clock.
"""
from PIL import Image, ImageDraw

from token_tv.combo import SIZE, market_rows
from token_tv.display import BACKGROUND, TEXT, pixel_text
from token_tv.stocks import PALETTE, display_symbol, magnitude, price_text

# Combo Cyber's board inks, so the gain/loss colours read the same across both faces.
RISE = (8, 247, 66)
FALL = (255, 68, 68)
HEADER_Y, RULE_Y = 4, 15
TOP, PITCH, MAX_ROWS = 24, 54, 4


def _bar(draw, y, percent, ink):
    """The row's magnitude bar: how full |percent| is against a 5% move."""
    fraction = magnitude(percent)
    if fraction:
        draw.rectangle((6, y, 6 + round((SIZE - 12) * fraction), y + 5), fill=ink)


def _board_row(draw, y, row):
    """A big row: the symbol and price on the first line, the arrowed change under them.

    A missing slot or a quote with no price is drawn as dashes, never zeros."""
    if not isinstance(row, dict) or not str(row.get('symbol') or '').strip():
        pixel_text(draw, (6, y + 5), '--', scale=3, color=PALETTE['dim'])
        pixel_text(draw, (SIZE - 6, y + 5), price_text(None), scale=3, color=PALETTE['dim'], align='right')
        pixel_text(draw, (SIZE - 6, y + 26), '--', scale=2, color=PALETTE['dim'], align='right')
        return
    pixel_text(draw, (6, y), display_symbol(row.get('symbol', '?'))[:6].upper(), scale=3, color=PALETTE['text'])
    price = price_text(row.get('price'))
    if price == '--':
        pixel_text(draw, (SIZE - 6, y + 5), price, scale=3, color=PALETTE['dim'], align='right')
        pixel_text(draw, (SIZE - 6, y + 26), '--', scale=2, color=PALETTE['dim'], align='right')
        return
    pixel_text(draw, (SIZE - 6, y + (0 if len(price) < 7 else 5)), price, scale=3 if len(price) < 7 else 2,
               color=TEXT, align='right')
    percent = row.get('percent')
    if percent is None:
        pixel_text(draw, (SIZE - 6, y + 26), '--', scale=2, color=PALETTE['dim'], align='right')
        return
    if percent > 0:
        text, ink = f'▲{abs(percent):.2f}%', RISE
    elif percent < 0:
        text, ink = f'▼{abs(percent):.2f}%', FALL
    else:
        text, ink = f' {abs(percent):.2f}%', PALETTE['dim']
    pixel_text(draw, (SIZE - 6, y + 26), text, scale=2, color=ink, align='right')
    _bar(draw, y + 42, percent, ink)  # two pixels clear of the percent line above


def render_combo_stocks(snapshot):
    """The stock board alone: header with exchange and session, then four big market rows.

    A closed, missing or stale market feed reads CLOSED and draws dash slots — the face still
    occupies its panel rather than handing it back (it is the only face the clock would show)."""
    rows = market_rows(snapshot)
    stocks = snapshot.get('stocks') if isinstance(snapshot, dict) else None
    market = (stocks or {}).get('market') or {}
    image = Image.new('RGB', (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)

    if rows is not None:
        session = str(market.get('session') or 'OPEN').upper()
        pixel_text(draw, (6, HEADER_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['text'])
    else:
        pixel_text(draw, (6, HEADER_Y), 'MARKET CLOSED', scale=1, color=PALETTE['dim'])
    source_label = market.get('exchange') or (stocks or {}).get('source')
    if source_label:  # no exchange or source is known without a feed: say nothing rather than FINNHUB
        pixel_text(draw, (SIZE - 6, HEADER_Y), str(source_label).upper(), scale=1, color=PALETTE['dim'],
                   align='right')

    for x in range(6, SIZE - 6, 4):
        draw.point((x, RULE_Y), fill=PALETTE['dim'])
    for index in range(MAX_ROWS):
        _board_row(draw, TOP + index * PITCH, rows[index] if rows and index < len(rows) else None)
    return image
