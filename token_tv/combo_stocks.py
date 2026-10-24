"""One 240×240 face carrying only the stock board: no usage rows.

The board and the overnight face follow the countdown clock's language — true black,
Manrope, hairline rules, one accent — so the two clocks read as one system. Four market
rows, ticker and price left, percent and change right. The sign is stated once in colour,
once in the arrow and once in the change's sign, which keeps it readable without colour
vision; the earlier Night Cards board stated it four times over (border, fill tint,
underline and arrow) and spent the space on ornament instead of the numbers.

Rows arrive through `snapshot['stocks']` exactly as for `combo` — gainers first, then
losers in movers mode. A closed, missing or stale feed draws dim dash rows under a CLOSED
header, never a fabricated 0.00, and claims no exchange. When the feeder knows the
schedule — it names the next open on a shut market — a dedicated closed night face renders
instead: a crescent moon over the reopening bell's time, drawn from the feed alone, and
high-contrast because the panel dims to brightness 19 overnight.

The AI usage screen deliberately keeps its own pixel-and-mascot identity and is not
unified with these; `usage_gif` and `display.render_usage_rows` own it.

Nothing here contacts a provider or a clock.

The pre-redesign Night Cards helpers (`_card`, `_dash_card`, `_centred`, `ink`, `arrow`,
`_arrow_and_percent`, `mix`) are retained but no longer called by any face.
"""
import functools
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


# ── Unified faces ────────────────────────────────────────────────────────────────
# The movers board and the night face share the countdown clock's language: true black,
# Manrope, hairline rules, one accent. The usage screen deliberately keeps its own
# pixel/mascot identity, so this module carries both and does not try to unify them.

UNI_BG = (0, 0, 0)
UNI_INK = (255, 255, 255)
UNI_DIM = (138, 138, 138)
UNI_FAINT = (36, 36, 36)
UNI_RULE = (28, 28, 28)
UNI_RISE = (46, 229, 123)
UNI_FALL = (255, 77, 77)
UNI_MOON = (232, 232, 240)
UNI_INKS = (UNI_INK, UNI_DIM, UNI_FAINT, UNI_RULE, UNI_RISE, UNI_FALL, UNI_MOON)
UNI_M = 14  # the shared left/right margin
UNI_TOP, UNI_PITCH = 40, 46  # the movers rows' grid


@functools.lru_cache(maxsize=None)
def _manrope(size, weight=800):
    """Manrope at an explicit weight.

    Loaded here rather than imported from `countdown`, which imports `_gif_bytes` from this
    module: going the other way would close the cycle. Manrope's default axis is 200
    (ExtraLight), so the weight is always set or the face renders spindly.
    """
    try:
        loaded = ImageFont.truetype(str(Path(__file__).parent / 'web' / 'manrope.ttf'), size)
    except OSError:
        return ImageFont.load_default()
    try:
        loaded.set_variation_by_axes([weight])
    except (OSError, ValueError, AttributeError):
        pass
    return loaded


@functools.lru_cache(maxsize=1)
def _uni_palette():
    """Black plus one ramp per ink, <=256 entries.

    `_gif_bytes` falls back to median-cut above 256 colours, and median-cut averages its
    boxes — which drifts the background off true black. Flattening to this fixed palette
    first keeps the exact-palette branch, so #000000 survives encoding.
    """
    steps = (255 - 1) // len(UNI_INKS)
    entries = [UNI_BG]
    for colour in UNI_INKS:
        entries.extend(tuple(round(c * s / steps) for c in colour) for s in range(1, steps + 1))
    reference = Image.new('P', (1, 1))
    reference.putpalette(([v for c in entries for v in c] + [0] * 768)[:768])
    return reference


def _uni_flatten(image):
    return image.quantize(palette=_uni_palette(), dither=Image.Dither.NONE).convert('RGB')


def _dim(colour, k):
    """`colour` mixed toward black; every result sits on that colour's own ramp."""
    return tuple(round(c * max(0.0, min(1.0, k))) for c in colour)


def _uni_tracked(draw, x, baseline, text, font, fill, tracking=1.6):
    for ch in text:
        draw.text((x, baseline), ch, fill=fill, font=font, anchor='ls')
        x += draw.textlength(ch, font=font) + tracking
    return x


def _uni_width(draw, text, font, tracking=1.6):
    return sum(draw.textlength(c, font=font) for c in text) + tracking * max(0, len(text) - 1)


def _uni_arrow(draw, cx, cy, up, colour, amplitude=None, size=5):
    """A solid triangle that breathes with `amplitude` (brightness, not position, so the
    row never shifts between frames)."""
    s = size
    fill = colour if amplitude is None else _dim(colour, 0.5 + 0.5 * amplitude)
    pts = [(cx, cy - s), (cx - s, cy + s * 0.7), (cx + s, cy + s * 0.7)] if up else \
          [(cx, cy + s), (cx - s, cy - s * 0.7), (cx + s, cy - s * 0.7)]
    draw.polygon(pts, fill=fill)


def render_combo_stocks_closed(snapshot):
    """The closed night face: a crescent over the next bell, on true black.

    Brightness drops to 19 overnight, so this face carries no hairlines and no ghost tones —
    only marks that survive a dim panel. `None` when the feed names no honest reopen: the
    caller rests on the usage screen rather than promising a bell nobody published.
    """
    schedule = closed_schedule(snapshot)
    if schedule is None:
        return None
    image = Image.new('RGB', (SIZE, SIZE), UNI_BG)
    draw = ImageDraw.Draw(image)

    # A crescent: a bright disc with an offset disc of background punched out of it.
    cx, cy, r = 120, 74, 30
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=UNI_MOON)
    draw.ellipse((cx - r + 13, cy - r - 5, cx + r + 13, cy + r - 5), fill=UNI_BG)

    label_font = _manrope(12, 800)
    _uni_tracked(draw, 120 - _uni_width(draw, 'REOPENS', label_font) / 2, 140,
                 'REOPENS', label_font, UNI_DIM)
    draw.text((120, 172), str(schedule.get('opens') or '--'), fill=UNI_INK,
              font=_manrope(54, 800), anchor='mm')
    tail = schedule.get('next_open_exchange') or schedule.get('exchange') or ''
    line = f"{schedule.get('day') or ''}  \u00b7  {tail}".strip(' \u00b7')
    tail_font = _manrope(13, 700)
    _uni_tracked(draw, 120 - _uni_width(draw, line, tail_font) / 2, 206, line, tail_font, UNI_DIM)

    # Footer: what the board last knew, on the host's own clock — never refetched, so a
    # stamped time stays put until the bell.
    foot = _manrope(11, 700)
    stocks = (snapshot.get('stocks') or {}) if isinstance(snapshot, dict) else {}
    stamp = market_time(stocks.get('updated_at') or 0, tz=None)
    _uni_tracked(draw, UNI_M, 231, 'LAST BOARD', foot, _dim(UNI_DIM, .62))
    _uni_tracked(draw, SIZE - UNI_M - _uni_width(draw, stamp, foot), 231, stamp, foot, _dim(UNI_DIM, .62))
    return _uni_flatten(image)


def _uni_row(draw, y, row, amplitude=None):
    """One market row: ticker and price left, percent and change right.

    A missing row draws dashes, never a fabricated 0.00. The sign is carried by colour AND
    an arrow AND the +/- on the change, so it still reads without colour vision.
    """
    if not row:
        draw.text((UNI_M, y + 15), '--', fill=UNI_DIM, font=_manrope(21, 800), anchor='lm')
        draw.text((SIZE - UNI_M, y + 17), '--', fill=UNI_DIM, font=_manrope(26, 800), anchor='rm')
        return
    percent = row.get('percent')
    up = (percent or 0) >= 0
    colour = UNI_DIM if percent is None else (UNI_RISE if percent > 0 else UNI_FALL if percent < 0 else UNI_DIM)

    draw.text((UNI_M, y + 15), display_symbol(row.get('symbol'))[:6], fill=UNI_INK,
              font=_manrope(21, 800), anchor='lm')
    draw.text((UNI_M, y + 33), price_text(row.get('price')), fill=UNI_DIM,
              font=_manrope(13, 600), anchor='lm')

    percent_text = '--' if percent is None else f'{abs(percent):.1f}%'
    percent_font = _manrope(26, 800)
    draw.text((SIZE - UNI_M, y + 17), percent_text, fill=colour, font=percent_font, anchor='rm')
    if percent is None:
        return
    width = draw.textlength(percent_text, font=percent_font)
    if percent:
        _uni_arrow(draw, SIZE - UNI_M - width - 12, y + 16, up, colour, amplitude)
    change = row.get('change')
    if change is not None:
        draw.text((SIZE - UNI_M, y + 35), f"{'+' if up else '-'}{abs(change):.2f}",
                  fill=UNI_DIM, font=_manrope(13, 600), anchor='rm')


def render_combo_stocks(snapshot, amplitude=None):
    """The movers board on true black: a hairline header, then four market rows.

    Rows rather than cards. The shipped board stated each sign four times over (border
    colour, fill tint, underline and arrow); this one states it once in colour, once in the
    arrow and once in the change's sign, and spends the space on the numbers instead.

    A closed, missing or stale feed reads CLOSED over dash rows — the face still occupies
    its panel rather than handing it back. `amplitude` (0..1) drives the animated variant's
    arrow breathing; None draws static.
    """
    rows = market_rows(snapshot)
    stocks = snapshot.get('stocks') if isinstance(snapshot, dict) else None
    market = (stocks or {}).get('market') or {}
    live = rows is not None
    image = Image.new('RGB', (SIZE, SIZE), UNI_BG)
    draw = ImageDraw.Draw(image)

    header_font = _manrope(11, 800)
    exchange = market.get('exchange') if live else None
    left = f"{str(exchange).upper()}  {str(market.get('session') or 'OPEN').upper()}" if exchange \
        else ('OPEN' if live else 'CLOSED')
    _uni_tracked(draw, UNI_M, 20, left[:22], header_font, UNI_DIM if live else _dim(UNI_DIM, .7))
    # The right slot names the feed, not the exchange again. Nothing is known without a
    # feed, so say nothing rather than naming one.
    source = (stocks or {}).get('source') or market.get('exchange')
    if source and live:
        text = str(source).upper()
        _uni_tracked(draw, SIZE - UNI_M - _uni_width(draw, text, header_font), 20,
                     text, header_font, UNI_DIM)
    draw.line((UNI_M, 28, SIZE - UNI_M, 28), fill=UNI_RULE, width=1)

    for index in range(MAX_ROWS):
        y = UNI_TOP + index * UNI_PITCH
        _uni_row(draw, y, rows[index] if rows and index < len(rows) else None, amplitude)
        if index < MAX_ROWS - 1:
            draw.line((UNI_M, y + UNI_PITCH - 6, SIZE - UNI_M, y + UNI_PITCH - 6), fill=UNI_RULE, width=1)
    return _uni_flatten(image)


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
    if len(colours) <= 256:
        reference = Image.new('P', (1, 1))
        palette = sorted(colours)
        reference.putpalette(([v for colour in palette for v in colour] + [0] * 768)[:768])
        indexed = [frame.quantize(palette=reference, dither=Image.Dither.NONE) for frame in frames]
    else:
        composite = Image.new('RGB', (frames[0].width, frames[0].height * len(frames)))
        for i, frame in enumerate(frames):
            composite.paste(frame, (0, i * frames[0].height))
        palette_ref = composite.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        indexed = [frame.quantize(palette=palette_ref, dither=Image.Dither.NONE) for frame in frames]
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
    """One frame of the animated board: the arrows breathe."""
    return render_combo_stocks(snapshot, amplitude=ARROW_PHASES[frame % ARROW_FRAMES])


def render_combo_stocks_gif(snapshot):
    """The board as a GIF: the arrows breathe, everything else is still."""
    frames = [render_stocks_frame(snapshot, f) for f in range(ARROW_FRAMES)]
    return _gif_bytes(frames)
