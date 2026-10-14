"""Combo Pulse: Sci-Fi Terminal Pulse.

An animated 240×240 GIF face combining AI usage and the stock watchlist with a live
pulse radar dot, gauge shimmer wave, and animated stock trend indicators.
"""
import io
import math
import time

from PIL import Image, ImageDraw

from token_tv.combo import AI_PITCH, AI_TOP, MARKET_LABEL_Y, MARKET_PITCH, MARKET_TOP, RULE_Y, SIZE, _rule, market_rows
from token_tv.display import (BACKGROUND, MUTED, TEXT, TRACK, account_label, gauge_color,
                              overview_rows, pixel_text, reading, render_pixel)
from token_tv.stocks import PALETTE, change_ink, display_symbol, price_text

FPS = 10
FRAMES = 10  # 1.0-second seamless loop


def _pulse_dot(draw, xy, frame, is_green=True):
    """Draw a 4x4 pulsating radar beacon with dynamic brightness."""
    x, y = xy
    # Keyframe intensity across 10 frames
    phase = (1 + math.cos(2 * math.pi * frame / FRAMES)) / 2  # 1.0 down to 0.0
    if is_green:
        r = int(18 + phase * (34 - 18))
        g = int(60 + phase * (210 - 60))
        b = int(28 + phase * (94 - 28))
    else:
        r = int(60 + phase * (220 - 60))
        g = int(18 + phase * (40 - 18))
        b = int(22 + phase * (40 - 22))

    core_color = (r, g, b)
    draw.rectangle((x + 1, y + 1, x + 2, y + 2), fill=core_color)

    # Halo aura at peak brightness
    if phase > 0.65:
        halo_color = (r // 2, g // 2, b // 2)
        draw.point([(x + 1, y), (x + 2, y), (x + 1, y + 3), (x + 2, y + 3),
                    (x, y + 1), (x, y + 2), (x + 3, y + 1), (x + 3, y + 2)], fill=halo_color)


def _animated_horizontal_gauge(draw, xy, used, frame, step=12, width=10, height=9):
    """Horizontal gauge with a specular light-wave sweep across active segments."""
    active_count = math.ceil(used / 10) if used is not None else 0
    shimmer_segment = frame % 10

    for segment in range(10):
        x, y = xy[0] + segment * step, xy[1]
        box = (x, y, x + width - 1, y + height - 1)
        draw.rectangle(box, fill=TRACK)

        filled = round(max(0, min(1, used / 10 - segment)) * width) if used is not None else 0
        if filled:
            inner_box = (x, y, x + filled - 1, y + height - 1)
            color = gauge_color(used, segment)
            # Specular highlight when shimmer wave passes
            if segment == shimmer_segment and segment < active_count:
                # Blend 60% towards crisp highlight
                color = tuple(min(255, int(c * 0.45 + 245 * 0.55)) for c in color)
            draw.rectangle(inner_box, fill=color)


def _ai_row(draw, y, row, frame):
    used, period, old, _ = reading(row)
    pixel_text(draw, (6, y + 4), account_label(row).replace(' ', '')[:3].upper(), scale=2, color=TEXT)
    _animated_horizontal_gauge(draw, (48, y + 7), used, frame, step=12, width=10, height=9)
    number = '--' if used is None else str(round(used)) + '%'
    ink = MUTED if used is None else '#%02x%02x%02x' % gauge_color(used)
    pixel_text(draw, (SIZE - 6, y + 2), number, scale=2, color=ink, align='right')
    pixel_text(draw, (SIZE - 6, y + 19), (period + ' OLD') if old else period, scale=1, color=PALETTE['dim'],
               align='right')


def _market_row(draw, y, row, frame):
    symbol = display_symbol(row.get('symbol', '?'))[:5].upper()
    pixel_text(draw, (6, y + 3), symbol, scale=2, color=PALETTE['text'])
    pixel_text(draw, (142, y + 3), price_text(row.get('price')), scale=2, color=TEXT, align='right')

    percent = row.get('percent')
    if percent is None:
        pixel_text(draw, (SIZE - 6, y + 3), '--', scale=2, color=MUTED, align='right')
        return

    if percent > 0:
        text = f'▲{abs(percent):.2f}%'
        color = (8, 247, 66)
    elif percent < 0:
        text = f'▼{abs(percent):.2f}%'
        color = (255, 68, 68)
    else:
        text = f' {abs(percent):.2f}%'
        color = PALETTE['dim']

    pixel_text(draw, (SIZE - 6, y + 3), text, scale=2, color=color, align='right')


def render_pulse_frame(snapshot, frame):
    rows = market_rows(snapshot)
    if rows is None:
        # Fallback to animated full-height usage face
        base = render_pixel(snapshot)
        draw = ImageDraw.Draw(base)
        _pulse_dot(draw, (4, 4), frame, is_green=True)
        return base

    image = Image.new('RGB', (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)

    # Header with pulsing radar dot
    _pulse_dot(draw, (6, 4), frame, is_green=True)
    pixel_text(draw, (16, 2), 'AI USAGE', scale=1, color=PALETTE['dim'])

    accounts = overview_rows(snapshot)
    for index, account in enumerate(accounts[:3]):
        _ai_row(draw, AI_TOP + index * AI_PITCH, account, frame)

    _rule(draw, RULE_Y)

    # Market section header with pulsing dot
    market_info = (snapshot.get('stocks') or {}).get('market', {})
    session = str(market_info.get('session') or 'OPEN').upper()
    is_open = bool(market_info.get('is_open'))
    _pulse_dot(draw, (6, MARKET_LABEL_Y + 2), frame, is_green=is_open)
    pixel_text(draw, (16, MARKET_LABEL_Y), ('MARKET ' + session)[:20], scale=1, color=PALETTE['dim'])
    source_label = market_info.get('exchange') or (snapshot.get('stocks') or {}).get('source') or 'FINNHUB'
    pixel_text(draw, (SIZE - 6, MARKET_LABEL_Y), str(source_label).upper(), scale=1, color=PALETTE['dim'], align='right')

    for index, row in enumerate(rows):
        _market_row(draw, MARKET_TOP + index * MARKET_PITCH, row, frame)

    return image


def render_combo_pulse(snapshot, scale=1):
    frames = [render_pulse_frame(snapshot, f) for f in range(FRAMES)]
    if scale != 1:
        frames = [f.resize((SIZE * scale, SIZE * scale), Image.Resampling.NEAREST) for f in frames]

    colours = set()
    for f in frames:
        colors = f.getcolors(maxcolors=f.width * f.height)
        if colors:
            colours.update(c for _, c in colors)
    palette = sorted(colours)
    if len(palette) > 256:
        # Quantize to at most 128 colors if too many
        palette = palette[:256]

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
