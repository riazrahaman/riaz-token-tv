"""Combo Cyber: The 3-in-1 Animated Hybrid Face.

Combines:
1. Deep navy night sky with twinkling ambient stars.
2. Pulsing emerald radar beacons on AI USAGE and MARKET headers.
3. Specular light-wave shimmer sweeping across the quota gauge blocks.
4. Peaceful mini pixel bot strolling back and forth along the center divider rule.
5. Bobbing directional trend arrows next to stock change percentages.
"""
import io
import math
from PIL import Image, ImageDraw

from token_tv.combo import (AI_PITCH, AI_TOP, MARKET_LABEL_Y, MARKET_PITCH, MARKET_TOP,
                            RULE_Y, SIZE, market_rows)
from token_tv.combo_patrol import _draw_mini_bot
from token_tv.combo_pulse import _animated_horizontal_gauge, _pulse_dot
from token_tv.combo_twinkle import NIGHT_BG
from token_tv.display import (MUTED, TEXT, account_label, gauge_color,
                              overview_rows, pixel_text, reading, render_pixel)
from token_tv.stocks import PALETTE, change_ink, price_text

FPS = 8
FRAMES = 36  # 4.5-second loop

# Prominent star coordinates in empty margins
PROMINENT_STARS = [
    (10, 4), (170, 4), (230, 4),
    (232, 42), (234, 76),
    (14, 102), (228, 102),
    (10, 230), (120, 234), (228, 230)
]


def _draw_twinkling_star(draw, x, y, intensity):
    """Draw a 3-pixel cross star that pulses vividly."""
    core = (intensity, intensity, min(255, intensity + 30))
    draw.point((x, y), fill=core)
    if intensity > 180:
        halo = (intensity // 2, intensity // 2, min(255, intensity // 2 + 20))
        draw.point([(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)], fill=halo)


def _cyber_market_row(draw, y, row, frame):
    symbol = str(row.get('symbol', '?'))[:4].upper()
    pixel_text(draw, (6, y + 3), symbol, scale=2, color=PALETTE['text'])
    pixel_text(draw, (142, y + 3), price_text(row.get('price')), scale=2, color=TEXT, align='right')

    percent = row.get('percent')
    if percent is None:
        pixel_text(draw, (SIZE - 6, y + 3), '--', scale=2, color=MUTED, align='right')
        return

    # Animated arrow indicator
    bob = 1 if (frame % 8) >= 4 else 0
    if percent > 0:
        arrow_y = (y + 4) - bob
        arrow_color = (68, 224, 120) if bob else (38, 178, 86)
        draw.polygon([(SIZE - 62, arrow_y + 4), (SIZE - 59, arrow_y), (SIZE - 56, arrow_y + 4)], fill=arrow_color)
    elif percent < 0:
        arrow_y = (y + 4) + bob
        arrow_color = (255, 90, 95) if bob else (214, 43, 60)
        draw.polygon([(SIZE - 62, arrow_y), (SIZE - 59, arrow_y + 4), (SIZE - 56, arrow_y)], fill=arrow_color)

    text = f'{percent:+.2f}%'
    pixel_text(draw, (SIZE - 6, y + 3), text, scale=2, color=change_ink(percent), align='right')


def _cyber_ai_row(draw, y, row, frame):
    used, period, old, _ = reading(row)
    pixel_text(draw, (6, y + 4), account_label(row).replace(' ', '')[:3].upper(), scale=2, color=TEXT)
    _animated_horizontal_gauge(draw, (48, y + 7), used, frame, step=12, width=10, height=9)
    number = '--' if used is None else str(round(used)) + '%'
    ink = MUTED if used is None else '#%02x%02x%02x' % gauge_color(used)
    pixel_text(draw, (SIZE - 6, y + 2), number, scale=2, color=ink, align='right')
    pixel_text(draw, (SIZE - 6, y + 19), (period + ' OLD') if old else period, scale=1, color=PALETTE['dim'],
               align='right')


def render_cyber_frame(snapshot, frame):
    rows = market_rows(snapshot)
    half = FRAMES // 2
    facing_right = frame < half
    step = frame if facing_right else (FRAMES - frame)

    if rows is None:
        base = render_pixel(snapshot)
        draw = ImageDraw.Draw(base)
        _pulse_dot(draw, (4, 4), frame, is_green=True)
        bot_x = int(50 + (step / half) * 140)
        _draw_mini_bot(draw, bot_x, 230, frame, facing_right)
        return base

    image = Image.new('RGB', (SIZE, SIZE), NIGHT_BG)
    draw = ImageDraw.Draw(image)

    # 1. Twinkling stars in margins
    for i, (sx, sy) in enumerate(PROMINENT_STARS):
        intensity = int(100 + 155 * math.sin(2 * math.pi * ((frame + i * 3) % FRAMES) / FRAMES))
        _draw_twinkling_star(draw, sx, sy, intensity)

    # 2. Pulse radar beacon on headers
    _pulse_dot(draw, (6, 4), frame, is_green=True)
    pixel_text(draw, (16, 2), 'AI USAGE', scale=1, color=PALETTE['dim'])

    # 3. Gauge shimmer wave across AI accounts
    accounts = overview_rows(snapshot)
    for index, account in enumerate(accounts[:3]):
        _cyber_ai_row(draw, AI_TOP + index * AI_PITCH, account, frame)

    # 4. Center divider rule
    draw.line([(6, RULE_Y), (SIZE - 6, RULE_Y)], fill=PALETTE['dim'])

    # 5. Peaceful strolling bot along the rule
    bot_x = int(80 + (step / half) * 80)
    _draw_mini_bot(draw, bot_x, RULE_Y, frame, facing_right)

    # Market header with live pulse dot
    session = str((snapshot.get('stocks') or {}).get('market', {}).get('session') or 'OPEN').upper()
    is_open = bool((snapshot.get('stocks') or {}).get('market', {}).get('is_open'))
    _pulse_dot(draw, (6, MARKET_LABEL_Y + 2), frame, is_green=is_open)
    pixel_text(draw, (16, MARKET_LABEL_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['dim'])
    pixel_text(draw, (SIZE - 6, MARKET_LABEL_Y), 'FINNHUB', scale=1, color=PALETTE['dim'], align='right')

    # 6. Animated market rows with bobbing trend arrows
    for index, row in enumerate(rows):
        _cyber_market_row(draw, MARKET_TOP + index * MARKET_PITCH, row, frame)

    return image


def render_combo_cyber(snapshot, scale=1):
    frames = [render_cyber_frame(snapshot, f) for f in range(FRAMES)]
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
