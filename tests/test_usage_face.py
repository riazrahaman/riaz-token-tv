"""The usage face's guarantees: the window model, legibility, and true black.

Two of these encode defects the previous face had. They are written against the window model
rather than against pixels, because that model is what makes them impossible rather than fixed.
"""
import io
import time
import unittest

from PIL import Image

from token_tv.usage_face import (UF_FOOT, UF_TIME, UF_WARN, FOOT_RULE, NUMERAL_GAP, TEXT_L,
                                 SAFE_BOTTOM, _pitch_for, ink_for, render_usage_face,
                                 render_usage_gif, span_hours, windows_of)


def account(provider, windows, status='ok'):
    return {'provider': provider, 'alias': provider.upper() + ' A',
            'status': status, 'windows': windows}


def window(label, percent, resets_in, now):
    return {'label': label, 'used_percent': percent, 'resets_at': now + resets_in}


class TestWindowModel(unittest.TestCase):
    def test_a_provider_with_one_window_yields_one_row(self):
        """The shipped face fell back to windows[0] for its second row, so Ollama's single
        budget window printed twice. One window must mean one row."""
        now = time.time()
        row = account('ollama', [window('$139', 46.6, 86400, now)])
        self.assertEqual(len(windows_of(row)), 1)

    def test_the_session_window_is_the_top_line_whatever_the_feed_says(self):
        """The two accounts list their windows in opposite orders, so reading the top line off
        the feed's own order made it mean different things per provider. Shortest window first
        puts the session line on top for both."""
        now = time.time()
        weekly_first = account('antigravity', [window('WEEK', 1.0, 6 * 86400, now),
                                               window('5H', 2.0, 3600, now)])
        session_first = account('claude', [window('5H', 3.0, 3600, now),
                                           window('WEEK', 4.0, 6 * 86400, now)])
        self.assertEqual([w['label'] for w in windows_of(weekly_first)], ['5H', 'WEEK'])
        self.assertEqual([w['label'] for w in windows_of(session_first)], ['5H', 'WEEK'])

    def test_a_session_with_no_open_window_still_leads(self):
        """Claude reports 5H with no reset time when no session is running. Ordering by reset
        clock sent that line to the bottom, so Claude read WEEK-then-5H while Antigravity read
        5H-then-WEEK -- the same two rows meaning different things on one panel."""
        now = time.time()
        row = account('claude', [{'label': '5H', 'used_percent': 0.0, 'resets_at': None},
                                 window('WEEK', 35.0, 4 * 86400, now)])
        self.assertEqual([w['label'] for w in windows_of(row)], ['5H', 'WEEK'])

    def test_a_span_is_read_from_the_window_name(self):
        self.assertEqual(span_hours('5H'), 5.0)
        self.assertEqual(span_hours('WEEK'), 168.0)
        self.assertEqual(span_hours('7D'), 168.0)
        self.assertIsNone(span_hours('$6'), 'an amount is not a duration')
        self.assertIsNone(span_hours('$1,234'))

    def test_a_missing_percentage_stays_unknown(self):
        now = time.time()
        row = account('claude', [window('5H', None, 3600, now)])
        self.assertIsNone(windows_of(row)[0]['percent'])

    def test_a_window_that_names_an_amount_sorts_after_the_ones_that_name_a_duration(self):
        """A budget window has no span to place it by, so it sits under the windows that do
        rather than displacing the session line."""
        now = time.time()
        row = account('claude', [{'label': 'BUDGET', 'used_percent': 10.0, 'resets_at': None},
                                 window('5H', 20.0, 3600, now)])
        self.assertEqual([w['label'] for w in windows_of(row)], ['5H', 'BUDGET'])

    def test_the_ink_warns_before_it_alarms(self):
        self.assertEqual(ink_for(10.0), ink_for(49.9))
        self.assertNotEqual(ink_for(49.9), ink_for(50.0))
        self.assertNotEqual(ink_for(50.0), ink_for(80.0))
        self.assertEqual(ink_for(None), UF_FOOT)


class TestUsageFaceRender(unittest.TestCase):
    def _snapshot(self, now=None, **extra):
        now = time.time() if now is None else now
        return dict({'accounts': {
            'c': account('claude', [window('5H', 18.0, 6000, now),
                                    window('WEEK', 17.0, 4 * 86400, now)]),
            'o': account('ollama', [window('$139', 46.6, 86400, now)]),
            'a': account('antigravity', [window('WEEK', 2.0, 6 * 86400, now),
                                         window('5H', 5.0, 17000, now)]),
        }}, **extra)

    def test_background_is_true_black_through_the_gif_encoder(self):
        data = render_usage_gif(self._snapshot())
        image = Image.open(io.BytesIO(data)).convert('RGB')
        for point in ((0, 0), (239, 0), (0, 239), (239, 239)):
            self.assertEqual(image.getpixel(point), (0, 0, 0), point)

    def test_the_frame_stays_under_the_palette_limit(self):
        colours = render_usage_face(self._snapshot()).getcolors(maxcolors=240 * 240)
        self.assertIsNotNone(colours)
        self.assertLessEqual(len(colours), 256)

    def test_the_rest_face_is_one_byte_stable_frame(self):
        """A still rest face costs no flash writes beyond the first swap."""
        snapshot = self._snapshot()
        data = render_usage_gif(snapshot)
        self.assertEqual(render_usage_gif(snapshot), data)
        self.assertEqual(Image.open(io.BytesIO(data)).n_frames, 1)
        self.assertLessEqual(len(data), 400000)

    def test_the_secondary_text_is_far_brighter_than_the_face_it_replaced(self):
        """The old face drew window names and reset times at #4a4a4a, which disappears on a
        panel dimmed to 19 overnight. Every secondary ink must clear that by a wide margin.

        Measured as luminance, not as the smallest channel: the reset time is a light blue
        whose red channel is low while the colour itself is bright."""
        def luminance(colour):
            r, g, b = colour
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        was = luminance((0x4a, 0x4a, 0x4a))
        self.assertGreater(luminance(UF_TIME), was * 2)
        self.assertGreater(luminance(UF_FOOT), was * 1.5)

    def test_a_stale_provider_says_so_in_the_warning_ink(self):
        """Asserted as "amber is on the face", not as an exact RGB: Pillow's fixed-palette
        match can return an ink a few units off, which is invisible on the panel but would make
        an equality check a test of the quantiser rather than of the face."""
        now = time.time()
        fresh = {'accounts': {'c': account('claude', [window('5H', 20.0, 3600, now)])}}
        stale = {'accounts': {'c': account('claude', [window('5H', 20.0, 3600, now)],
                                           status='stale')}}

        def amber(image):
            return sum(count for count, colour in image.getcolors(maxcolors=240 * 240)
                       if abs(colour[0] - UF_WARN[0]) <= 12
                       and abs(colour[1] - UF_WARN[1]) <= 12 and colour[2] <= 12)

        self.assertEqual(amber(render_usage_face(fresh)), 0, 'a healthy row must not warn')
        self.assertGreater(amber(render_usage_face(stale)), 50)

    def test_rows_never_reach_the_footer_however_many_windows_arrive(self):
        """The pitch shrinks instead of letting the block grow into the calendar line."""
        now = time.time()
        many = [window(f'W{i}', 10.0 * i, 3600 * (i + 1), now) for i in range(4)]
        snapshot = {'accounts': {'c': account('claude', many), 'o': account('ollama', many),
                                 'a': account('antigravity', many)}}
        image = render_usage_face(snapshot)
        band = image.crop((0, FOOT_RULE - 4, 240, FOOT_RULE - 1))
        self.assertEqual(band.getbbox(), None, 'a row crossed into the footer')

    def test_an_account_with_no_windows_reports_its_status_rather_than_a_zero(self):
        snapshot = {'accounts': {'c': account('claude', [], status='auth_required')}}
        image = render_usage_face(snapshot)
        self.assertIsNotNone(image.getbbox())  # it drew something
        self.assertEqual(render_usage_face(snapshot).tobytes(), image.tobytes())


class TestCrowdedRows(unittest.TestCase):
    """The worst row the feeds can produce: a long window name, a long countdown, a
    three-digit percentage and a stale account, all at once."""

    def _worst(self):
        now = time.time()
        return {'accounts': {
            'c': account('claude', [window('BUDGET', 100.0, 14 * 86400, now)]),
            'o': account('ollama', [window('$1,234', 99.9, 10 * 86400, now)], status='stale'),
            'a': account('antigravity', [window('WEEK', 100.0, 6 * 86400, now)]),
        }}

    def test_the_label_never_runs_into_the_percentage(self):
        """Checked in pixels, because this is exactly how it broke: the numeral grew with the
        row and drew straight through the label beside it."""
        image = render_usage_face(self._worst())
        rows = 3
        pitch, top0 = _pitch_for(rows)   # the real geometry, including the MAX_PITCH cap
        for index in range(rows):
            top = top0 + index * pitch
            # From TEXT_L rightwards only: the mascot gutter holds Claude's salmon sprite,
            # which would read as alarmed-numeral ink. The strip also stops short of the row's
            # proportion bar, which at 100% is a full-width band in that same red.
            strip = image.crop((TEXT_L, top + 2, 240, top + pitch - 10))
            label_right = red_left = None
            for x in range(strip.width):
                column = [strip.getpixel((x, y)) for y in range(strip.height)]
                if any(min(px) > 170 for px in column):            # white label ink
                    label_right = x
                if red_left is None and any(px[0] > 170 and px[1] < 120 for px in column):
                    red_left = x                                    # the alarmed numeral
            if label_right is not None and red_left is not None:
                self.assertLess(label_right, red_left,
                                f'row {index}: label ink overlaps the percentage')

    def test_the_old_tag_survives_a_row_too_narrow_for_its_countdown(self):
        """When the row runs out of width the countdown is abbreviated -- the staleness marker
        never is. A face that quietly dropped OLD would be claiming freshness it cannot prove."""
        image = render_usage_face(self._worst())
        amber = sum(count for count, colour in image.getcolors(maxcolors=240 * 240)
                    if abs(colour[0] - UF_WARN[0]) <= 12
                    and abs(colour[1] - UF_WARN[1]) <= 12 and colour[2] <= 12)
        self.assertGreater(amber, 20, 'the OLD tag was truncated away')


class TestNumeralClearance(unittest.TestCase):
    """A percentage must stand clear of the rule under it.

    Found on hardware: every numeral on the panel came back with its feet sliced off. The
    numeral was positioned from `glyph.size`, but Manrope's digits sit low in their font box --
    at 34px the ink ends 2px *below* the box -- so the digits reached into the row's own track
    rule. The numeral is placed from its measured bbox now, which is why this is checked in
    pixels across several row counts rather than asserted of one layout.
    """

    def _cases(self):
        now = time.time()
        five = {'accounts': {
            'c': account('claude', [window('WEEK', 21.0, 4 * 86400, now),
                                    window('5H', None, 6000, now)]),
            'o': account('ollama', [window('$144', 48.0, 70000, now)]),
            'a': account('antigravity', [window('5H', 0.0, 17000, now),
                                         window('WEEK', 2.0, 6 * 86400, now)])}}
        three = {'accounts': {
            'c': account('claude', [window('5H', 100.0, 600, now)]),
            'o': account('ollama', [window('$1,234', 99.9, 10 * 86400, now)]),
            'a': account('antigravity', [window('WEEK', 7.0, 6 * 86400, now)])}}
        one = {'accounts': {'c': account('claude', [window('5H', 100.0, 600, now)])}}
        return {'five rows': (five, 5), 'three rows': (three, 3), 'one row': (one, 1)}

    def test_no_row_rule_cuts_through_its_percentage(self):
        for name, (snapshot, rows) in self._cases().items():
            with self.subTest(name):
                image = render_usage_face(snapshot)
                pitch, top0 = _pitch_for(rows)
                for index in range(rows):
                    rule_y = top0 + index * pitch + pitch - 5
                    for y in range(rule_y - NUMERAL_GAP + 1, rule_y):
                        lit = [x for x in range(TEXT_L, 240)
                               if max(image.getpixel((x, y))) > 60]
                        self.assertEqual(lit, [], f'{name} row {index}: ink at y={y} '
                                                  f'sits inside the rule\'s clearance')


    def test_a_row_with_no_reading_keeps_the_same_baseline(self):
        """AGENTS.md: a missing reading is a dash. The dash is four rows of ink sitting low in
        its box, so deriving the drop from the figure stood it on the rule and pulled its sign
        down across it -- the same cut, on the one row that has nothing else to say. Every row
        is measured from a reference digit now, so all five share a baseline."""
        now = time.time()
        snapshot = {'accounts': {
            'c': account('claude', [window('WEEK', 21.0, 4 * 86400, now),
                                    window('5H', None, 6000, now)]),
            'o': account('ollama', [window('$144', None, 70000, now)]),
            'a': account('antigravity', [window('5H', 0.0, 17000, now),
                                         window('WEEK', 2.0, 6 * 86400, now)])}}
        image = render_usage_face(snapshot)
        pitch, top0 = _pitch_for(5)
        signs, dashes = [], []
        for index in range(5):
            top = top0 + index * pitch
            rule_y = top + pitch - 5
            lit = [y for y in range(top, rule_y)
                   if any(max(image.getpixel((x, y))) > 60 for x in range(212, 227))]
            self.assertTrue(lit, f'row {index}: no percent sign drawn')
            signs.append(lit[0] - top)
            figure = [y for y in range(top, rule_y)
                      if any(max(image.getpixel((x, y))) > 60 for x in range(150, 210))]
            self.assertTrue(figure, f'row {index}: no figure drawn')
            if index in (0, 2):                       # the two rows with no reading
                dashes.append(rule_y - figure[-1])
        self.assertEqual(len(set(signs)), 1, f'the percent sign wanders between rows: {signs}')
        for clearance in dashes:
            self.assertGreater(clearance, 8, 'the dash is resting on the row rule')


class TestPanelSafeArea(unittest.TestCase):
    """Nothing may be drawn below SAFE_BOTTOM.

    Found on hardware, not in a render: the calendar line ran to row 237 -- inside a 240-pixel
    frame, and still visibly clipped in a photograph of the clock, because the case overlaps the
    last few rows. The frame's edge is not the usable edge.
    """

    def _ink_bottom(self, snapshot):
        image = render_usage_face(snapshot)
        lit = [y for y in range(240)
               if any(max(image.getpixel((x, y))) > 60 for x in range(240))]
        return lit[-1] if lit else 0

    def test_no_face_draws_below_the_safe_area(self):
        now = time.time()
        cases = {
            'five rows': {'accounts': {
                'c': account('claude', [window('5H', 35.0, 4000, now),
                                        window('WEEK', 20.0, 4 * 86400, now)]),
                'o': account('ollama', [window('$140', 47.0, 86400, now)]),
                'a': account('antigravity', [window('5H', 5.0, 15000, now),
                                             window('WEEK', 2.0, 6 * 86400, now)])}},
            'one row': {'accounts': {'c': account('claude', [window('5H', 1.0, 600, now)])}},
            'stale and crowded': {'accounts': {
                'o': account('ollama', [window('$1,234', 99.9, 10 * 86400, now)],
                             status='stale')}},
            'no windows': {'accounts': {'c': account('claude', [], status='auth_required')}},
        }
        for name, snapshot in cases.items():
            with self.subTest(name):
                self.assertLessEqual(self._ink_bottom(snapshot), SAFE_BOTTOM,
                                     f'{name}: ink reaches the clipped edge of the panel')


if __name__ == '__main__':
    unittest.main()
