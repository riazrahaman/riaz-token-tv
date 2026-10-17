"""The usage face's animated variant: the provider gauges shimmer and the mascots twinkle.

The user asked for animation on the usage screen's progress bars. `render_pixel`'s layout is
reproduced line for line (this face is the same parallel-universe pattern as the rest), with the
one call into `combo_pulse`'s specular-shimmer gauge — the light wave sweeps the filled segments
each frame while the labels, numbers and countdown text stay put. The mascots ride the same four
frames: each provider has `-pixel-1..3` phase sprites next to its base (Claude's shading orbits,
Ollama blinks, Antigravity breathes) so the GIF carries a second, slower kind of life without
more bytes than the shimmer alone. Nothing here contacts a provider or a clock.
"""
import io
import math

from PIL import Image, ImageDraw

from token_tv.calendar import CALENDAR_ROW, calendar_parts, draw_calendar_parts
from token_tv.combo_pulse import _animated_horizontal_gauge
from token_tv.combo_stocks import PHASE_MS, _gif_bytes
from token_tv.display import (BACKGROUND, MUTED, RULE, STATUS, TEXT, mascot, overview_rows,
                              pixel_text, primary_window, quota_period, row_shift, time_left)

SHIMMER_STEP = 3  # gauge segments the highlight walks per phase — a clearly visible distance
SHIMMER_MS = 300  # per sweep phase: obvious movement, not a strobe — a ~1.2 s walk per bar
USAGE_PHASES = tuple(offset for offset in range(0, 10, SHIMMER_STEP))  # 0, 3, 6, 9: four stages
USAGE_FRAMES = len(USAGE_PHASES)
_UNSET = object()  # no shared reading yet: compute the calendar line at draw time


def render_usage_frame(snapshot, frame, calendar=_UNSET):
    """One shimmer frame of the usage face.

    The sweep phases are ~300 ms apart — obvious movement, not a strobe — and each one moves the
    highlight three filled segments along, so the walk across the bar is unmistakable."""
    shimmer = USAGE_PHASES[frame % USAGE_FRAMES]
    image = Image.new('RGB', (240, 240), BACKGROUND)
    draw = ImageDraw.Draw(image)
    rows = overview_rows(snapshot)
    for index, row in enumerate(rows):
        y = 8 + index * 78 + row_shift(len(rows), 78)
        if index:
            for x in range(14, 227, 4):
                draw.point((x, y - 6), fill=RULE)
        mascot(image, row['provider'], (14, y + 10), pixel=True, phase=frame % 4)
        label = row['alias']
        if not label.startswith(row['provider'].upper() + ' '):
            label = row['provider'].upper() + ' A'
        pixel_text(draw, (68, y + 3), label)
        value = primary_window(row)
        used = max(0, min(100, value['used_percent'])) if value else None
        old = row['status'] != 'ok'
        number = '--' if used is None else str(round(used)) + '%'
        pixel_text(draw, (68, y + 21), number, scale=3, color=TEXT)
        if value:
            period = quota_period(value)
            pixel_text(draw, (226, y + 3), period + (' OLD' if old else ' USED'), color=MUTED, align='right')
            pixel_text(draw, (226, y + 25), time_left(value.get('resets_at')).replace(' ', ''), scale=2,
                       color=MUTED, align='right')
        else:
            pixel_text(draw, (226, y + 25), STATUS.get(row['status'], 'NO DATA'), color=MUTED, align='right')
        _animated_horizontal_gauge(draw, (68, y + 52), used, shimmer)  # the shimmer: the animated part
    # The fourth row: the next event, under the three usage rows, on the combo family's honesty
    # terms — absent with no snapshot, OLD when the feed went stale, never a countdown invented.
    draw_calendar_parts(draw, calendar_parts(snapshot) if calendar is _UNSET else calendar,
                        CALENDAR_ROW, TEXT, countdown_ink=MUTED)
    return image


def render_usage_gif(snapshot):
    """The usage face as a GIF: the gauges shimmer, the mascots twinkle, the calendar line sits still.

    The calendar reading is computed once for the whole GIF, so every frame carries the same
    line and no frame can drift onto the other side of a countdown bucket during encoding."""
    parts = calendar_parts(snapshot)
    frames = [render_usage_frame(snapshot, f, calendar=parts) for f in range(USAGE_FRAMES)]
    return _gif_bytes(frames, ms=SHIMMER_MS)
