"""Combo Twinkle: Subtle Twinkle & Pulse (Option 3).

An animated 240×240 GIF face combining AI usage and stocks with a starry
twinkling night aesthetic in the margins and headers.
"""
import io
import math
from PIL import Image, ImageDraw

from token_tv.combo import (AI_PITCH, AI_TOP, CALENDAR_Y, MARKET_LABEL_Y, MARKET_PITCH, MARKET_TOP,
                            RULE_Y, SIZE, _ai_row, _market_row, _rule, market_rows)
from token_tv.calendar import draw_calendar_line
from token_tv.combo_pulse import _pulse_dot
from token_tv.display import (overview_rows, pixel_text, render_pixel)
from token_tv.stocks import PALETTE

FPS = 10
FRAMES = 10

NIGHT_BG = (10, 14, 28)
STARS = [(18, 6), (72, 4), (190, 8), (228, 52), (12, 100), (228, 102), (8, 230), (120, 234), (225, 228)]
# The calendar line's ink: the same pale slate combo_stocks reads as HEADER_INK (this module
# cannot import combo_stocks for it — combo_stocks imports this one's sky).
CALENDAR_INK = (150, 160, 190)


def render_twinkle_frame(snapshot, frame):
    rows = market_rows(snapshot)
    if rows is None:
        base = render_pixel(snapshot)
        draw = ImageDraw.Draw(base)
        for i, (sx, sy) in enumerate(STARS):
            intensity = int(120 + 130 * math.sin(2 * math.pi * ((frame + i * 2) % FRAMES) / FRAMES))
            draw.point((sx, sy), fill=(intensity, intensity, min(255, intensity + 30)))
        return base

    image = Image.new('RGB', (SIZE, SIZE), NIGHT_BG)
    draw = ImageDraw.Draw(image)

    # Twinkling stars
    for i, (sx, sy) in enumerate(STARS):
        intensity = int(120 + 130 * math.sin(2 * math.pi * ((frame + i * 2) % FRAMES) / FRAMES))
        draw.point((sx, sy), fill=(intensity, intensity, min(255, intensity + 30)))

    _pulse_dot(draw, (6, 4), frame, is_green=True)
    pixel_text(draw, (16, 2), 'AI USAGE', scale=1, color=PALETTE['dim'])

    accounts = overview_rows(snapshot)
    for index, account in enumerate(accounts[:3]):
        _ai_row(draw, AI_TOP + index * AI_PITCH, account)

    _rule(draw, RULE_Y)

    market_info = (snapshot.get('stocks') or {}).get('market', {})
    session = str(market_info.get('session') or 'OPEN').upper()
    is_open = bool(market_info.get('is_open'))
    _pulse_dot(draw, (6, MARKET_LABEL_Y + 2), frame, is_green=is_open)
    pixel_text(draw, (16, MARKET_LABEL_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['dim'])
    source_label = market_info.get('exchange') or (snapshot.get('stocks') or {}).get('source') or 'FINNHUB'
    pixel_text(draw, (SIZE - 6, MARKET_LABEL_Y), str(source_label).upper(), scale=1, color=PALETTE['dim'], align='right')

    for index, row in enumerate(rows):
        _market_row(draw, MARKET_TOP + index * MARKET_PITCH, row)

    # The next-event line, static across every frame (drawn after the stars: text wins overlap).
    draw_calendar_line(draw, snapshot, CALENDAR_Y, CALENDAR_INK)

    return image


def render_combo_twinkle(snapshot, scale=1):
    frames = [render_twinkle_frame(snapshot, f) for f in range(FRAMES)]
    if scale != 1:
        frames = [f.resize((SIZE * scale, SIZE * scale), Image.Resampling.NEAREST) for f in frames]

    colours = set()
    for f in frames:
        colors = f.getcolors(maxcolors=f.width * f.height)
        if colors:
            colours.update(c for _, c in colors)
    palette = sorted(colours)[:256]

    reference = Image.new('P', (1, 1))
    palette_bytes = [v for c in palette for v in c] + [0] * (768 - 3 * len(palette))
    reference.putpalette(palette_bytes[:768])
    indexed = [f.quantize(palette=reference, dither=Image.Dither.NONE) for f in frames]

    buffer = io.BytesIO()
    indexed[0].save(
        buffer,
        format='GIF',
        save_all=True,
        append_images=indexed[1:],
        duration=1000 // FPS,
        loop=0,
        disposal=1,
        optimize=False,
    )
    return buffer.getvalue()
