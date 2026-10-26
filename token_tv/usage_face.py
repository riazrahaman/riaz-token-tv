"""The AI usage face, on the true-black Manrope grid the countdown and movers faces share.

The face this replaced assumed every provider had a session *and* a weekly window and drew a
fixed two-row block for each. That assumption is wrong on real readings: Ollama reports a single
budget window, so the block's second row fell back to `windows[0]` and printed the same window
twice; and the two accounts list their windows in different orders, so the top line meant
different things per provider. Both are layout bugs as much as code bugs, so this face takes the
windows a provider actually has and orders them **shortest window first**, so the session
window is the top line for every provider and the long window the one under it. One window
still yields one row, and the order is read from each window's own declared span rather than
from its position in the feed, so no two accounts can disagree about what a line means.

The ten-block gauge is gone. It could not tell 45% from 48%, it cost most of the row's width,
and the number beside it said the same thing more precisely. What remains is a hairline whose
length is the proportion, under a numeral that carries the urgency in its ink.

Secondary text (the window name, the time until it resets, the calendar line) is drawn bright:
the previous face used #4a4a4a, which is legible on a desktop monitor and all but invisible on a
240x240 IPS panel running at brightness 52 by day and 19 at night. The reset time is tinted
rather than merely lightened, so it reads as its own kind of fact beside the window name.

Nothing here contacts a provider, a clock or the network.
"""
import functools

from PIL import Image, ImageDraw

from token_tv.calendar import calendar_parts
from token_tv.combo_stocks import _gif_bytes, _manrope
from token_tv.display import STATUS, mascot, overview_rows, time_left

SIZE = 240
L, R = 14, SIZE - 14            # the page margins every face in this language shares
TOP = 4
FOOT_RULE, FOOT_Y = 210, 216    # the calendar line sits under its own hairline
SAFE_BOTTOM = 234   # the lowest row that reliably survives the bezel on the real panel.
# Photographed on hardware: a footer whose glyphs ran to 238 came back visibly clipped along its
# bottom edge, even though 238 is inside a 240-pixel frame. The case overlaps the last few rows,
# so the frame's own edge is not the usable edge.

UF_BG = (0, 0, 0)
UF_NAME = (245, 245, 245)       # the provider
UF_LABEL = (240, 240, 240)      # the window's name: 5H, WEEK, $139
UF_TIME = (125, 200, 255)       # how long until it resets
UF_FOOT = (150, 150, 150)       # the calendar line
UF_RULE = (28, 28, 28)
UF_TRACK = (34, 34, 34)
UF_CALM = (236, 240, 243)       # under half the window spent
UF_WARN = (255, 179, 0)         # and the OLD tag, which must never be quiet
UF_TIGHT = (255, 77, 77)
UF_INKS = (UF_NAME, UF_LABEL, UF_TIME, UF_FOOT, UF_RULE, UF_TRACK,
           UF_CALM, UF_WARN, UF_TIGHT)

BRAND = {'claude': (217, 119, 87), 'ollama': (235, 235, 235), 'antigravity': (110, 168, 255)}
GUTTER_X = 8        # the mascot column
TEXT_L = 56         # everything else starts clear of it
MAX_PITCH = 52      # a single window should not sprawl across half the panel
NUMERAL_MAX = 86    # the widest a percentage may grow before it steps down a size
NUMERAL_GAP = 4     # lit pixels between the bottom of a percentage and the rule it stands on
MIN_PITCH = 26      # below this the numerals stop being readable at desk distance  # the row pitch shrinks rather than letting rows reach the footer
MAX_WINDOWS = 2    # per provider: a session and a long window is the whole model any feed reports
MAX_ROWS = (FOOT_RULE - 6 - TOP) // MIN_PITCH  # the backstop: rows never reach the footer

WARN_AT, TIGHT_AT = 50.0, 80.0


def _uf_flatten(image):
    """Quantise to 256 colours with pure black pinned at entry 0.

    The movers board can use a fixed ink ramp because it draws nothing but text and rules. This
    face carries the provider mascots, and one of them is a rainbow, so a ramp of our own inks
    would mangle them. Instead the palette pins black and the inks that carry meaning, then
    fills the rest from the frame's own most common colours. That keeps the exact-palette branch
    of `_gif_bytes` and so keeps #000000 from drifting the way median-cut would drift it.

    Pillow matches against a fixed palette approximately, so an ink can come back a few units
    off (#ffb300 as #fbb000, say). Pinning bounds that error rather than eliminating it; the
    difference is invisible on the panel, and black -- the one colour that must be exact -- is
    entry 0 and comes back exact.
    """
    # The inks that carry meaning are pinned before anything else. Ordering purely by frequency
    # drops whatever is rarest, and the rarest thing on this face is usually the OLD tag -- a
    # handful of pixels that must not be allowed to drift to a neighbouring amber.
    pinned = [UF_BG]
    for colour in tuple(UF_INKS) + tuple(BRAND.values()):
        if colour not in pinned:
            pinned.append(colour)
    counts = image.getcolors(maxcolors=image.width * image.height) or []
    rest = [colour for _, colour in sorted(counts, key=lambda pair: -pair[0])
            if colour not in pinned]
    entries = (pinned + rest)[:256]
    reference = Image.new('P', (1, 1))
    reference.putpalette(([v for colour in entries for v in colour] + [0] * 768)[:768])
    return image.quantize(palette=reference, dither=Image.Dither.NONE).convert('RGB')


NAMED_SPANS = {'HOUR': 1.0, 'DAY': 24.0, 'WEEK': 168.0, 'MONTH': 720.0, 'SESSION': 5.0}
UNITS = {'H': 1.0, 'D': 24.0, 'W': 168.0, 'M': 720.0}


def span_hours(label):
    """How long a window covers, read from its own name: 5H -> 5, WEEK -> 168, $6 -> unknown.

    The span is the window's own property, not its position in the feed, which is what makes it
    safe to sort on: the shipped face's defect was indexing windows[0]/windows[1] and calling
    them session and weekly, and the two accounts list them in opposite orders."""
    text = str(label or '').upper().strip()
    if text in NAMED_SPANS:
        return NAMED_SPANS[text]
    digits = ''
    for character in text:
        if character.isdigit() or (character == '.' and digits):
            digits += character
        else:
            break
    unit = text[len(digits):].strip()
    if digits and unit in UNITS:
        return float(digits) * UNITS[unit]
    return None       # a budget window like $6 names an amount, not a duration


def windows_of(row):
    """The windows a provider actually reports, shortest window first.

    Shortest-first is the whole ordering rule: the session window is the top line and the long
    window sits under it, for every provider, so a given line means the same thing on each. The
    order comes from each window's declared span, never from its index in the feed, so this
    cannot repeat the shipped face's defect of calling windows[0] the session. A window whose
    name is an amount rather than a duration ($6) has no span and sorts after the ones that do,
    soonest reset first among themselves. A provider with one window still yields one row, and a
    missing percentage stays None -- it becomes a dash on the face, never a fabricated 0."""
    out = []
    for window in row.get('windows') or []:
        percent = window.get('used_percent')
        label = str(window.get('label') or '?').upper()
        out.append({
            'label': label,
            'span': span_hours(label),
            'percent': None if percent is None else max(0.0, min(100.0, float(percent))),
            'resets_at': window.get('resets_at'),
            'left': time_left(window.get('resets_at')).replace(' ', ''),
        })
    # A session window with no reset time means no session is open -- Claude reports exactly
    # that. It is still the session line, so it keeps its place; only windows with no span at
    # all fall back to the reset clock.
    out.sort(key=lambda w: (w['span'] is None, w['span'] or 0.0,
                            w['resets_at'] is None, w['resets_at'] or 0))
    return out


def ink_for(percent):
    """Calm, then amber past half the window, then red near the ceiling."""
    if percent is None:
        return UF_FOOT
    if percent >= TIGHT_AT:
        return UF_TIGHT
    return UF_WARN if percent >= WARN_AT else UF_CALM


def _tracked(draw, x, y, text, font, fill, extra=1.0):
    """Letterspaced small caps: at this size tracking is what makes a label read as a label."""
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + extra
    return x


def _ink_height(draw, text, font):
    """How many rows the glyphs actually light, which is not `font.size`."""
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def _right(draw, right_x, y, text, font, fill):
    draw.text((right_x - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def _rows(snapshot):
    """[(provider, name, status, window, index_within_provider)] -- one entry per drawn row."""
    out = []
    for row in overview_rows(snapshot):
        provider = row['provider']
        name = (row.get('alias') or '').upper().replace(' A', '') or provider.upper()
        windows = windows_of(row)[:MAX_WINDOWS]
        for index, window in enumerate(windows or [None]):
            out.append((provider, name, row.get('status'), window, index))
    # Shortest-first ordering already put the session window at the front of each provider, so
    # truncating here drops the longest windows rather than arbitrary ones.
    return out[:MAX_ROWS]


def _pitch_for(count):
    """Spend the whole band on the rows rather than centring them inside it.

    The earlier version picked a pitch off a fixed ladder and centred the block, so whatever did
    not divide evenly became dead margin above the first row and below the last. Dividing the
    band by the row count turns that margin into type size instead, and the remainder -- never
    more than a few pixels -- is split top and bottom."""
    room = FOOT_RULE - 6 - TOP
    pitch = max(MIN_PITCH, min(MAX_PITCH, room // max(1, count)))
    return pitch, TOP + max(0, (room - pitch * count)) // 2


def _draw_footer(draw, snapshot):
    """The next calendar event, and its honesty tag. The tag takes the width it needs and the
    title takes the rest, so a longer tag truncates the title rather than colliding with it."""
    draw.line((L, FOOT_RULE, R, FOOT_RULE), fill=UF_RULE, width=1)
    title, tail = calendar_parts(snapshot) or ('', '')
    font = _manrope(15, 600)
    tag = (tail or '').upper()
    tag_width = draw.textlength(tag, font=font) if tag else 0
    if title:
        text = title.upper()
        room = (R - tag_width - 10) - L
        if draw.textlength(text, font=font) > room:
            while text and draw.textlength(text + '…', font=font) > room:
                text = text[:-1]
            text += '…'
        draw.text((L, FOOT_Y), text, font=font, fill=UF_FOOT)
    if tag:
        draw.text((R - tag_width, FOOT_Y), tag, font=font,
                  fill=UF_WARN if 'OLD' in tag else UF_FOOT)


def render_usage_face(snapshot):
    """The usage face as an RGB image.

    The provider is named by its mascot in a left gutter -- the earlier face's one genuinely
    better idea, and far quicker to read across a room than a 6px colour dot. Everything
    secondary is set at 15px rather than 11px for the same reason: on a 240x240 panel at desk
    distance the old sizes were legible only up close.
    """
    image = Image.new('RGB', (SIZE, SIZE), UF_BG)
    draw = ImageDraw.Draw(image)

    rows = _rows(snapshot)
    pitch, top = _pitch_for(len(rows))
    secondary = max(12, min(18, round(pitch * 0.42)))
    numeral = _manrope(max(18, min(36, pitch - 6)), 800)
    percent_font = _manrope(max(10, round(secondary * 0.82)), 700)
    label_font = _manrope(secondary, 700)
    time_font = _manrope(secondary, 600)

    # The mascot belongs to the provider, not to a window, so it is centred on the whole group.
    groups = {}
    for index, (provider, _, _, _, within) in enumerate(rows):
        if within == 0:
            groups[provider] = [index, index]
        elif provider in groups:
            groups[provider][1] = index

    for provider, (first, last) in groups.items():
        centre = top + (first + last + 1) * pitch // 2
        size = max(26, min(44, pitch))
        mascot(image, provider, (GUTTER_X, centre - 24), pixel=True, size=size)

    y = top
    for provider, name, status, window, within in rows:
        # No written provider name: the mascot in the gutter is the name, which is what the
        # earlier face used it for. Dropping the duplicate is what buys every row the height
        # to set its label and reset time at a size readable across a room.
        if window is None:
            draw.text((TEXT_L, y + pitch // 2 - 10), STATUS.get(status, 'NO DATA'),
                      font=label_font, fill=UF_LABEL)
            y += pitch
            continue

        ink = ink_for(window['percent'])
        figure = '--' if window['percent'] is None else f"{round(window['percent'])}"
        # The numeral is laid out first, because what is left over is what the label and the
        # reset time get. A three-digit figure steps down rather than running into them.
        glyph = numeral
        rule_y = y + pitch - 5          # the row's track, and the floor the numeral sits on
        headroom = rule_y - NUMERAL_GAP - y
        while glyph.size > 18 and (
                draw.textlength(figure, font=glyph) > NUMERAL_MAX
                or _ink_height(draw, '0', glyph) > headroom):
            glyph = _manrope(glyph.size - 2, 800)
        sign_width = draw.textlength('%', font=percent_font)
        numeral_left = R - sign_width - 3 - draw.textlength(figure, font=glyph)

        stale = status == 'stale'
        suffix = '  OLD' if stale else ''
        left = window['left']
        label_f, time_f = label_font, time_font
        room = numeral_left - 10 - TEXT_L

        def pair_width(lf, tf, text):
            return (sum(draw.textlength(c, font=lf) for c in window['label'])
                    + 0.6 * max(0, len(window['label']) - 1)
                    + 8 + draw.textlength(text, font=tf))

        while label_f.size > 11 and pair_width(label_f, time_f, left + suffix) > room:
            label_f = _manrope(label_f.size - 1, 700)
            time_f = _manrope(time_f.size - 1, 600)
        # Still too wide: the countdown gives way, never the OLD tag. Only the reset time is
        # shortened here -- a face that abbreviates a countdown is fine, a face that quietly
        # dropped its own staleness marker would not be.
        while left and pair_width(label_f, time_f, left + suffix) > room:
            left = left[:-1]
        tail = left + suffix

        text_y = y + pitch // 2 - label_f.size
        after = _tracked(draw, TEXT_L, text_y, window['label'], label_f, UF_LABEL, extra=0.6)
        draw.text((after + 8, text_y), tail, font=time_f, fill=UF_WARN if stale else UF_TIME)

        # The numeral is placed by the ink it actually puts on the panel, not by its font box.
        # Manrope's digits sit low in that box -- at 34px the box starts 11px above the first lit
        # pixel and ends 2px below the last -- so a position derived from `glyph.size` put the
        # digits' feet through the row's own track rule, and every percentage on the panel came
        # back with its bottom sliced off. Measuring the bbox and standing the digits on the rule
        # with a fixed gap makes the clearance independent of size and of the figure drawn.
        # Measured from a reference digit rather than from the figure itself: a row with no
        # reading draws '--', whose ink is four rows tall and sits low, so sizing the drop to it
        # stood the dash on the rule and dragged the sign down across it. One baseline for every
        # row, whatever it has to say.
        numeral_box = draw.textbbox((0, 0), '0', font=glyph)
        numeral_y = rule_y - NUMERAL_GAP - numeral_box[3]
        _right(draw, R - sign_width - 3, numeral_y, figure, glyph, ink)
        sign_box = draw.textbbox((0, 0), '%', font=percent_font)
        # The sign rides as a superscript, pinned a quarter of the way down the numeral's ink so
        # the pairing holds at every step of the size ladder.
        ink_top = numeral_y + numeral_box[1]
        sign_y = ink_top + round((numeral_box[3] - numeral_box[1]) * 0.25) - sign_box[1]
        draw.text((R - sign_width, sign_y), '%', font=percent_font, fill=ink)

        base = rule_y
        draw.rectangle((TEXT_L, base, R, base + 1), fill=UF_TRACK)
        if window['percent'] is not None:
            end_x = TEXT_L + max(1, round((R - TEXT_L) * window['percent'] / 100.0))
            draw.rectangle((TEXT_L, base, end_x, base + 1), fill=ink)
        y += pitch

    _draw_footer(draw, snapshot)
    return _uf_flatten(image)


def render_usage_gif(snapshot):
    """The usage face as a single-frame GIF.

    It stays a GIF because every publisher picks the slot filename from the image's magic bytes
    rather than the style name, and the usage slot is a GIF slot. One frame rather than the
    former four: the shimmer was dropped at the operator's request, and a still frame is both
    smaller and byte-stable between publishes, which is flash the clock does not have to spend.
    """
    return _gif_bytes([render_usage_face(snapshot)])
