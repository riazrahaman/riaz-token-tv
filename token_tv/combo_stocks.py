"""One 240×240 face carrying only the Night Cards stock board: no usage rows.

The user asked for stocks alone and picked the Night Cards direction: a dark-navy sky
(twinkle's NIGHT_BG and star margins) under four rounded cards, one per market row. The
percent is the biggest text on each card, with a dim glow under it and a solid arrow; the
symbol drops to scale 2. The row's magnitude bar keeps the 5%-fill convention, tinted to
the card's sign. Rows arrive through `snapshot['stocks']` exactly as for `combo` — gainers
first, then losers in movers mode. A closed, missing or stale feed draws dim dash cards
under a CLOSED header, never a fabricated 0.00, and claims no exchange. When the feeder
knows the schedule — it names the next open on a shut market — a dedicated closed night
face renders instead: a crescent moon over the reopening bell's time, drawn from the feed
alone. Nothing here contacts a provider or a clock.
"""
import io
from PIL import Image, ImageDraw

from token_tv.combo import SIZE, market_rows
from token_tv.display import TEXT, pixel_text
from token_tv.combo_twinkle import NIGHT_BG, STARS
from token_tv.stocks import PALETTE, display_symbol, magnitude, market_time, price_text, reopen_text

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


def _arrow_and_percent(draw, right, y, percent, scale, colour, amplitude=None):
    """Arrow + magnitude right-aligned at `right`, with the dim glow copy beneath.

    `amplitude` (0..1, one step per 500 ms) breathes the arrow and its glow across big, slow
    swings: an edge-to-edge ink swing (mix .25 ↔ 1.0) with the glow riding .0 ↔ .5 — visible at
    a glance. The percent text itself is drawn identically on every frame, so
    the GIF's frames stay compressible."""
    text = f'{abs(percent):.2f}%'
    pixel_text(draw, (right + 1, y + 1), text, scale, mix(colour, .5 * amplitude) if amplitude is not None
               else mix(colour, .35), 'right')  # glow: one dim offset copy
    pixel_text(draw, (right, y), text, scale, colour, 'right')
    arrow(draw, right - ((len(text) * 6 - 1) * scale) - 8 - 5 * scale, y + scale, percent > 0, scale,
          colour if amplitude is None else mix(colour, .25 + .75 * amplitude))


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


def _card(draw, y, row, amplitude=None):
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
    change = row.get('change')
    pixel_text(draw, (SIZE - 14, y + 30), '--' if change is None else f'{change:+.2f}', 2, mix(row_ink, .8), 'right')
    if percent == 0:  # flat: a dim percent, no glow, and no arrow polygon at all
        pixel_text(draw, (SIZE - 14, y + 7), f'{abs(percent):.2f}%', 3, PALETTE['dim'], 'right')
    else:
        _arrow_and_percent(draw, SIZE - 14, y + 7, percent, 3, row_ink, amplitude=amplitude)
    _bar(draw, row_ink, 14, y + 47, SIZE - 14, percent)


def _centred(draw, y, text, scale, colour):
    pixel_text(draw, ((SIZE - (len(str(text)) * 6 - 1) * scale) // 2, y), text, scale, colour)


def closed_schedule(snapshot):
    """The market dict for a scheduled close with a still-future named reopen, or None.

    Honesty gates, in order: the feed must exist and say the market is shut (an open board
    that is merely stale is a frozen feed, not a quiet market); the reopen must carry a
    future stamp on a known exchange; and the clock math must exist. Consumers rest on the
    usage screen or the dash cards when this is None rather than promising a nobody-published
    open, so the closed face only ever names a bell the feeder itself computed.
    """
    stocks = snapshot.get('stocks') if isinstance(snapshot, dict) else None
    market = (stocks or {}).get('market') if isinstance(stocks, dict) else None
    if not isinstance(market, dict) or market.get('is_open') is not False:
        return None
    text = reopen_text(market.get('next_open_at'), market.get('next_open_exchange'))
    if text is None:
        return None
    return dict(market, day=text[0], opens=text[1])


def render_combo_stocks_closed(snapshot):
    """The closed night face: a crescent moon over the next bell, no board and no dashes.

    Where the live board's closed state showed dash cards for hours on end, this face
    keeps what an overnight clock actually wants to know: the sky is quiet, and the stock
    board returns at a named time — on the host's own clock, so a 09:15 IST bell that opens
    at 07:45 on the operator's wall says 07:45, with NSE's name kept as the label. The
    feed says nothing honest about a reopen? The
    caller keeps resting on the usage screen instead; this face refuses to draw a schedule
    it does not know. The stamp stays still on purpose (no ticking countdown → no flash
    writes): the frame it draws is stable until the bell itself moves.
    """
    schedule = closed_schedule(snapshot)
    if schedule is None:
        return None
    image = Image.new('RGB', (SIZE, SIZE), NIGHT_BG)
    draw = ImageDraw.Draw(image)

    # Star margins: the same nine fixed twinkles plus the two the mockup sheet added.
    for i, (sx, sy) in enumerate(STARS + [(150, 12), (100, 238)]):
        draw.point((sx, sy), fill=mix(PRICE_INK, (.6 + (i * 37) % 40 / 100)))
    # The quiet header: dot and MARKET CLOSED as the live board has them, the shut market
    # top-right — whose board the feed is still carrying (LAST BOARD in the footer).
    draw.ellipse((6, 5, 10, 9), fill=mix(HEADER_INK, .35), outline=NIGHT_BG)
    pixel_text(draw, (16, 4), 'MARKET CLOSED', 1, mix(HEADER_INK, .5))
    closed_exchange = str(schedule.get('exchange') or '').upper()
    if closed_exchange:
        pixel_text(draw, (SIZE - 6, 4), closed_exchange, 1, mix(HEADER_INK, .5), 'right')

    # A waxing crescent: a pale disc with the sky easing a bite from its upper right.
    draw.ellipse((162, 48, 208, 94), fill=mix(PRICE_INK, .55))
    draw.ellipse((176, 40, 218, 82), fill=NIGHT_BG)

    # The schedule, one bell, dead centre: small label, big time, day, whose bell it is.
    # The bell renders on the host's own clock — a 09:15 IST open is 05:45 on a UAE wall, and
    # the operator reads the wall, not the exchange — while the label below keeps NSE's name.
    _centred(draw, 96, 'REOPENS', 1, HEADER_INK)
    _centred(draw, 110, schedule['opens'], 4, TEXT)
    _centred(draw, 154, schedule['day'], 2, HEADER_INK)
    _centred(draw, 180, schedule.get('next_open_exchange') or '', 1, mix(HEADER_INK, .7))

    # Footer: what the board last knew, on the host's own clock either — never refetched
    # (the idle wakes publish no reading), so a stamped time stays put until the bell.
    pixel_text(draw, (8, 227), 'LAST BOARD', 1, mix(HEADER_INK, .5))
    tz = None  # host-local: the feed's zone describes its market, not this operator's clock
    stocks = (snapshot.get('stocks') or {}) if isinstance(snapshot, dict) else {}
    pixel_text(draw, (SIZE - 8, 227), market_time(stocks.get('updated_at') or 0, tz=tz), 1,
               mix(HEADER_INK, .5), 'right')
    return image


def render_combo_stocks(snapshot, amplitude=None):
    """The Night Cards board: header dot, star margins, then four rounded market cards.

    A closed, missing or stale market feed reads CLOSED over dim dash cards — the face still
    occupies its panel rather than handing it back (it is the only face the clock would show).
    `amplitude` (0..1) drives the animated variant's arrow-and-glow breathing; None draws static."""
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
        _card(draw, TOP + index * PITCH, rows[index] if rows and index < len(rows) else None, amplitude=amplitude)
    return image


ARROW_PHASES = (1.0, 0.5, 0.0, 0.5)  # the breathe cycle: full, mid, edge, mid — big, unmistakable swings
ARROW_FRAMES = len(ARROW_PHASES)
PHASE_MS = 500  # one breathe phase per half-second: a 2.0 s full loop — fast enough to read as alive


def _gif_bytes(frames, ms=PHASE_MS):
    """A 256-colour quantised GIF (no dither, cyber's encoding) paced by the caller (`ms`/phase).

    Adjacent pixel-identical frames are merged with their durations added before encoding, so no
    encoder can coalesce a phase away: every stored frame lasts `ms` or longer, and every stored
    frame differs from its neighbour."""
    colours = set()
    for frame in frames:
        colors = frame.getcolors(maxcolors=frame.width * frame.height)
        if colors:
            colours.update(colour for _, colour in colors)
    reference = Image.new('P', (1, 1))
    palette = sorted(colours)[:256]
    reference.putpalette(([v for colour in palette for v in colour] + [0] * 768)[:768])
    indexed = [frame.quantize(palette=reference, dither=Image.Dither.NONE) for frame in frames]
    merged, durations = [], []
    for frame in indexed:
        if merged and frame.tobytes() == merged[-1].tobytes():
            durations[-1] += ms  # the encoder cannot drop what is already one frame
        else:
            merged.append(frame)
            durations.append(ms)
    buffer = io.BytesIO()
    merged[0].save(buffer, format='GIF', save_all=True, append_images=merged[1:],
                   duration=durations, loop=0, disposal=1, optimize=False)
    return buffer.getvalue()


def render_combo_stocks_closed_gif(snapshot):
    """The closed night face as GIF bytes for the stock slots: one still, calm frame.

    The slots are the firmware's GIF files, so the face travels the same encoding as the
    live board — a single quantised frame — and stays byte-stable between wakes, which the
    publisher's dedupe turns into near-zero flash traffic overnight."""
    frame = render_combo_stocks_closed(snapshot)
    if frame is None:
        return None
    return _gif_bytes([frame])


def render_stocks_frame(snapshot, frame):
    """One frame of the animated Night Cards board: the arrows and their glow breathe."""
    return render_combo_stocks(snapshot, amplitude=ARROW_PHASES[frame % ARROW_FRAMES])


def render_combo_stocks_gif(snapshot):
    """The Night Cards board as a GIF: the arrow-and-glow layer breathes, everything else is still."""
    frames = [render_stocks_frame(snapshot, f) for f in range(ARROW_FRAMES)]
    return _gif_bytes(frames)
