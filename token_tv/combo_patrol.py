"""Combo Patrol: Mascot Patrol (Option 2).

An animated 240×240 GIF face combining AI usage and stocks, featuring a mini
pixel mascot traversing along the divider between sections.
"""
import io
import math
from PIL import Image, ImageDraw

from token_tv.combo import (AI_PITCH, AI_TOP, MARKET_LABEL_Y, MARKET_PITCH, MARKET_TOP,
                            RULE_Y, SIZE, _ai_row, _market_row, market_rows)
from token_tv.display import (BACKGROUND, overview_rows, pixel_text, render_pixel)
from token_tv.stocks import PALETTE

FPS = 8
FRAMES = 36  # 4.5-second relaxed loop


def _draw_mini_bot(draw, x, y, frame, facing_right=True):
    """Draw a 7x7 animated pixel bot walking leisurely along the rule."""
    bob = 1 if (frame % 4) in (1, 2) else 0
    cy = y - 4 - bob
    # Antenna sparkle
    ax = x + 4 if facing_right else x + 2
    draw.point((ax, cy - 2), fill=(138, 180, 248))
    # Head & body
    draw.rectangle((x + 1, cy - 1, x + 5, cy + 3), fill=(66, 133, 244))
    # Visor facing walking direction
    vx = (x + 3, x + 5) if facing_right else (x + 1, x + 3)
    draw.line((vx[0], cy + 1, vx[1], cy + 1), fill=(255, 255, 255))
    # Animated stepping feet
    if (frame % 2) == 0:
        draw.point([(x + 2, cy + 4), (x + 5, cy + 4)], fill=(234, 67, 53))
    else:
        draw.point([(x + 1, cy + 4), (x + 4, cy + 4)], fill=(234, 67, 53))


def render_patrol_frame(snapshot, frame):
    rows = market_rows(snapshot)
    half = FRAMES // 2
    facing_right = frame < half
    step = frame if facing_right else (FRAMES - frame)

    if rows is None:
        base = render_pixel(snapshot)
        draw = ImageDraw.Draw(base)
        bot_x = int(50 + (step / half) * 140)
        _draw_mini_bot(draw, bot_x, 230, frame, facing_right)
        return base

    image = Image.new('RGB', (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)

    pixel_text(draw, (6, 2), 'AI USAGE', scale=1, color=PALETTE['dim'])
    accounts = overview_rows(snapshot)
    for index, account in enumerate(accounts[:3]):
        _ai_row(draw, AI_TOP + index * AI_PITCH, account)

    # Base rule
    draw.line([(6, RULE_Y), (SIZE - 6, RULE_Y)], fill=PALETTE['dim'])

    # Leisurely stroll along the center of the rule (x=80 to x=160)
    bot_x = int(80 + (step / half) * 80)
    _draw_mini_bot(draw, bot_x, RULE_Y, frame, facing_right)

    market_info = (snapshot.get('stocks') or {}).get('market', {})
    session = str(market_info.get('session') or 'OPEN').upper()
    pixel_text(draw, (6, MARKET_LABEL_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['dim'])
    source_label = market_info.get('exchange') or (snapshot.get('stocks') or {}).get('source') or 'FINNHUB'
    pixel_text(draw, (SIZE - 6, MARKET_LABEL_Y), str(source_label).upper(), scale=1, color=PALETTE['dim'], align='right')

    for index, row in enumerate(rows):
        _market_row(draw, MARKET_TOP + index * MARKET_PITCH, row)

    return image


def render_combo_patrol(snapshot, scale=1):
    frames = [render_patrol_frame(snapshot, f) for f in range(FRAMES)]
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
