"""One 240×240 face carrying only the Night Cards stock board: no usage rows.

The user asked for stocks alone and picked the Night Cards direction: a dark-navy sky
(twinkle's NIGHT_BG and star margins) under four rounded cards, one per market row. The
percent is the biggest text on each card, with a dim glow under it and a solid arrow; the
symbol drops to scale 2. The row's magnitude bar keeps the 5%-fill convention, tinted to
the card's sign. Rows arrive through `snapshot['stocks']` exactly as for `combo` — gainers
first, then losers in movers mode. A closed, missing or stale feed draws dim dash cards
under a CLOSED header, never a fabricated 0.00, and claims no exchange. Nothing here
contacts a provider or a clock.
"""
from PIL import Image, ImageDraw

from token_tv.combo import SIZE, market_rows
from token_tv.display import TEXT, pixel_text
from token_tv.combo_twinkle import NIGHT_BG, STARS
from token_tv.stocks import PALETTE, display_symbol, magnitude, price_text

# Combo Cyber's board inks, so gain/loss reads the same across every stock face.
RISE = (8, 247, 66)
FALL = (255, 68, 68)
HEADER_INK = (150, 160, 190)
PRICE_INK = (170, 180, 210)
TOP, PITCH, CARD_H, CARD_R = 18, 55, 50, 6
MAX_ROWS = 4


def mix(colour, k, bg=NIGHT_BG):
    """A tint toward `colour` from the sky at hand — cards and inks are all night mixes."""
    return tuple(int(bg[i] + (colour[i] - bg[i]) * k) for i in range(3))


def ink(percent):
    """The card's sign ink: green up, red down, dim-74 flat or unknown."""
    if percent is None:
        return PALETTE['dim']
    return RISE if percent > 0 else FALL if percent < 0 else PALETTE['dim']


def arrow(draw, x, y, up, scale, colour):
    """A solid triangle, w=5s h=4s, top-left at x,y — a polygon, so alignment stays exact."""
    w, h = 5 * scale, 4 * scale
    pts = [(x, y + h), (x + w, y + h), (x + w / 2, y)] if up else [(x, y), (x + w, y), (x + w / 2, y + h)]
    draw.polygon(pts, fill=colour)


def _arrow_and_percent(draw, right, y, percent, scale, colour):
    """Arrow + magnitude right-aligned at `right`, with the dim glow copy beneath."""
    text = f'{abs(percent):.2f}%'
    pixel_text(draw, (right + 1, y + 1), text, scale, mix(colour, .35), 'right')  # glow: one dim offset copy
    pixel_text(draw, (right, y), text, scale, colour, 'right')
    width = (len(text) * 6 - 1) * scale
    arrow(draw, right - width - 8 - 5 * scale, y + scale, percent > 0, scale, colour)


def _bar(draw, row_ink, x0, y, x1, percent):
    """The magnitude row bar: 0..1 of |percent| against a 5% move, tinted to the card."""
    draw.rectangle((x0, y, x1, y + 1), fill=mix(row_ink, .18))
    filled = round((x1 - x0 + 1) * magnitude(percent))
    if filled:
        draw.rectangle((x0, y, x0 + filled - 1, y + 1), fill=row_ink)


def _dash_card(draw, y):
    """A card with nothing honest to show: dim dashes, never a fabricated 0.00."""
    draw.rounded_rectangle((5, y, SIZE - 6, y + CARD_H), CARD_R, fill=mix(PALETTE['dim'], .06),
                           outline=mix(PALETTE['dim'], .45))
    pixel_text(draw, (14, y + 7), '--', 2, PALETTE['dim'])
    pixel_text(draw, (14, y + 27), '--', 2, PALETTE['dim'])
    pixel_text(draw, (SIZE - 14, y + 12), '--', 2, PALETTE['dim'], 'right')


def _card(draw, y, row):
    """One night card. Dead quotes and empty slots are dash cards; live rows never re-rank."""
    if not isinstance(row, dict) or not str(row.get('symbol') or '').strip():
        return _dash_card(draw, y)
    percent = row.get('percent')
    row_ink = ink(percent)
    draw.rounded_rectangle((5, y, SIZE - 6, y + CARD_H), CARD_R, fill=mix(row_ink, .10),
                           outline=mix(row_ink, .45))
    pixel_text(draw, (14, y + 7), display_symbol(row.get('symbol', '?'))[:6].upper(), 2, TEXT)
    pixel_text(draw, (14, y + 27), price_text(row.get('price')), 2, PALETTE['dim'] if row.get('price') is None else PRICE_INK)
    if row.get('price') is None or percent is None:  # a dead quote: dashes at the price and percent anchors
        pixel_text(draw, (SIZE - 14, y + 12), '--', 2, PALETTE['dim'], 'right')
        return
    _arrow_and_percent(draw, SIZE - 14, y + 7, percent, 3, row_ink)
    change = row.get('change')
    pixel_text(draw, (SIZE - 14, y + 34), '--' if change is None else f'{change:+.2f}', 2, mix(row_ink, .8), 'right')
    _bar(draw, row_ink, 14, y + 46, SIZE - 14, percent)


def render_combo_stocks(snapshot):
    """The Night Cards board: header dot, star margins, then four rounded market cards.

    A closed, missing or stale market feed reads CLOSED over dim dash cards — the face still
    occupies its panel rather than handing it back (it is the only face the clock would show)."""
    rows = market_rows(snapshot)
    stocks = snapshot.get('stocks') if isinstance(snapshot, dict) else None
    market = (stocks or {}).get('market') or {}
    live = rows is not None
    image = Image.new('RGB', (SIZE, SIZE), NIGHT_BG)
    draw = ImageDraw.Draw(image)

    # Star margins: twinkle's nine fixed twinkles plus the two the mockup sheet added.
    for i, (sx, sy) in enumerate(STARS + [(150, 12), (100, 238)]):
        draw.point((sx, sy), fill=mix(PRICE_INK, (.6 + (i * 37) % 40 / 100)))
    # The header dot: green while a market is open, dimmed to a quiet grey otherwise.
    draw.ellipse((6, 5, 10, 9), fill=RISE if live else mix(HEADER_INK, .35),
                 outline=mix(RISE, .35) if live else NIGHT_BG)
    session = str(market.get('session') or 'OPEN').upper() if live else 'CLOSED'
    pixel_text(draw, (16, 4), ('MARKET ' + session)[:20], 1, HEADER_INK if live else mix(HEADER_INK, .5))
    source_label = market.get('exchange') or (stocks or {}).get('source')
    if source_label:  # no exchange or source is known without a feed: say nothing rather than FINNHUB
        pixel_text(draw, (SIZE - 6, 4), str(source_label).upper(), 1, HEADER_INK if live else mix(HEADER_INK, .5),
                   'right')
    for index in range(MAX_ROWS):
        _card(draw, TOP + index * PITCH, rows[index] if rows and index < len(rows) else None)
    return image