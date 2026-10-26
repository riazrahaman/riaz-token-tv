"""The usage screen's publishable bytes.

This module used to own the face itself: `render_pixel`'s layout reproduced line for line, with
a shimmer walking the gauges and the mascots twinkling across four frames. The face moved to
`usage_face`, which draws the true-black Manrope grid the countdown and movers faces share, and
the shimmer went with it -- the operator asked for the animation to go, and a still frame is
smaller and byte-stable between publishes, so the clock spends no flash redrawing a moving
highlight. What stays here is the name every publisher already imports.

`render_pixel` and `display.render_usage_rows` are deliberately untouched: the `pixel` gallery
style and the four combo faces' closed-market fallback still draw the older layout, and a test
asserts combo and pixel stay byte-identical.
"""
from token_tv.usage_face import render_usage_face, render_usage_gif  # noqa: F401

SHIMMER_MS = 500   # kept so a publisher pinning the frame duration still reads a sane value
USAGE_FRAMES = 1   # the face is a still frame now, not a four-phase shimmer
_UNSET = object()


def render_usage_frame(snapshot, frame=0, calendar=_UNSET):
    """One frame of the usage face. There is only one; `frame` and `calendar` are accepted so
    callers written against the animated face keep working, and the calendar line is read from
    the snapshot exactly as `usage_face` reads it."""
    return render_usage_face(snapshot)
