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
from token_tv.combo_stocks import PHASE_MS, _gif_bytes
from token_tv.display import BACKGROUND, MUTED, TEXT, render_usage_rows

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
    phase = frame % 4
    image = Image.new('RGB', (240, 240), BACKGROUND)
    draw = ImageDraw.Draw(image)
    render_usage_rows(image, draw, snapshot, shimmer=shimmer, phase=phase)
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
