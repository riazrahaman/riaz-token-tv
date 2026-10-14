import copy
import io
import json
import time
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

from PIL import Image

from token_tv.display import ANIMATED_STYLES, STYLES, gauge_color, render_page
from token_tv.live import DisplayPreferences, handler
from token_tv.sources import load_config
from token_tv.state import UsageStore


class DisplayStyleTests(unittest.TestCase):
    def test_style_picker_persists_without_querying_accounts(self):
        account = {'key': 'a', 'alias': 'CLAUDE A', 'provider': 'claude', 'email': 'owner@example.com'}
        row = dict(account, status='ok', windows=[{'label': 'WEEK', 'used_percent': 94, 'resets_at': None}], fetched_at=100)
        fetch = Mock(return_value=row)
        store = UsageStore([account], fetch=fetch)
        store.refresh()
        baseline = copy.deepcopy(store.snapshot())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = {'accounts': [account], 'display_style': 'pixel'}
            path.write_text(json.dumps(config))
            prefs = DisplayPreferences(path, config)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, prefs))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            root = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(root + '/frame/0.jpg?style=digital') as response:
                    preview = response.read()
                self.assertEqual(Image.open(io.BytesIO(preview)).size, (240, 240))
                self.assertEqual(prefs.snapshot()['style'], 'pixel')
                request = Request(root + '/display/style', data=b'{"style":"digital"}', headers={'Content-Type': 'application/json'})
                with urlopen(request) as response:
                    self.assertEqual(json.load(response)['style'], 'digital')
                self.assertTrue(prefs.changed.is_set())
                self.assertEqual(load_config(path)['display_style'], 'digital')
                self.assertEqual(DisplayPreferences(path, load_config(path)).snapshot()['style'], 'digital')
                for style in STYLES:
                    with urlopen(root + '/frame/0.jpg?style=' + style) as response:
                        self.assertEqual(Image.open(io.BytesIO(response.read())).size, (240, 240))
                    request = Request(root + '/display/style', data=json.dumps({'style': style}).encode(), headers={'Content-Type': 'application/json'})
                    with urlopen(request) as response:
                        self.assertEqual(json.load(response)['style'], style)
                    self.assertEqual(DisplayPreferences(path, load_config(path)).snapshot()['style'], style)
                for payload, origin in ((b'{"style":"unknown"}', None), (b'{"style":"pixel"}', 'https://unrelated.example')):
                    headers = {'Content-Type': 'application/json'}
                    if origin:
                        headers['Origin'] = origin
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(Request(root + '/display/style', data=payload, headers=headers))
                    self.assertIn(raised.exception.code, (400, 403))
                self.assertEqual(load_config(path)['display_style'], STYLES[-1])
                self.assertEqual(store.snapshot(), baseline)
                self.assertEqual(fetch.call_count, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_all_styles_keep_same_payload_contract_and_unknown_states(self):
        sample = {'accounts': {'a': {'alias': 'CLAUDE A', 'provider': 'claude', 'status': 'auth_required', 'windows': []}}}
        frames = [render_page(sample, style=style) for style in STYLES]
        # 'combo' deliberately renders the full-size pixel face when there is no open market, so it
        # is the one style allowed to match another; every other pair must stay distinct.
        self.assertEqual(len(set(frames)), len(STYLES) - 1)
        self.assertEqual(render_page(sample, style='combo'), render_page(sample, style='pixel'))
        for status in ('ok', 'stale'):
            sample['accounts']['a'].update(status=status, windows=[{'label': 'WEEK', 'used_percent': 100, 'resets_at': None}])
            frames.extend(render_page(sample, style=style) for style in STYLES)
        for frame in frames:
            image = Image.open(io.BytesIO(frame))
            self.assertEqual(image.size, (240, 240))
            if frame[:4] == b'GIF8':  # the animated Space face
                self.assertEqual(image.format, 'GIF')
                self.assertGreater(image.n_frames, 1)
                self.assertLess(len(frame), 400000)
            else:
                self.assertEqual(image.format, 'JPEG')
                self.assertLess(len(frame), 60000)
        self.assertEqual(sum(f[:4] == b'GIF8' for f in frames[:len(STYLES)]), len(ANIMATED_STYLES))
        with self.assertRaises(ValueError):
            render_page(sample, style='unknown')

    def test_gauge_enters_high_at_80_and_critical_at_90(self):
        self.assertEqual(gauge_color(49), (118, 169, 154))
        self.assertEqual(gauge_color(50), (147, 201, 185))
        self.assertEqual(gauge_color(79), (147, 201, 185))
        self.assertEqual(gauge_color(80), (209, 178, 117))
        self.assertEqual(gauge_color(89), (209, 178, 117))
        self.assertEqual(gauge_color(90), (216, 135, 126))
        self.assertEqual(gauge_color(100), (216, 135, 126))

    def test_config_rejects_unknown_display_style(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = {'accounts': [{'key': 'a', 'alias': 'CLAUDE A', 'provider': 'claude', 'email': 'owner@example.com'}], 'display_style': 'unknown'}
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == '__main__':
    unittest.main()


class ConfiguredServicesOnlyTest(unittest.TestCase):
    def test_only_services_with_accounts_get_a_row(self):
        import time
        from token_tv.display import STYLES, overview_rows, render_page
        from token_tv.sample import H, snapshot
        data = snapshot(time.time(), [('claude_a', 'CLAUDE A', 'claude', [('5H', 72, 2 * H)])])
        self.assertEqual([r['provider'] for r in overview_rows(data)], ['claude'])  # no LOGIN rows for unused services
        for style in STYLES:
            self.assertTrue(render_page(data, style=style))  # every face copes with one row
        self.assertEqual(len(overview_rows({'accounts': {}})), 3)  # empty config keeps the placeholders


class FlashWearTests(unittest.TestCase):
    """The clock stores frames in flash, so the publisher must bound how often it rewrites them."""

    @staticmethod
    def _snapshot(now, remaining, used=41.0):
        accounts = {}
        for offset, provider in enumerate(('claude', 'ollama', 'antigravity')):
            accounts[provider + '_a'] = {
                'key': provider + '_a', 'provider': provider, 'alias': provider.upper() + ' A',
                'status': 'ok', 'fetched_at': now, 'last_success_at': now,
                'windows': [{'label': '5H', 'used_percent': used + offset,
                             'resets_at': None if remaining is None else now + remaining}]}
        return {'updated_at': now, 'accounts': accounts}

    def test_bucketing_the_countdown_stops_a_frame_changing_every_poll(self):
        from unittest import mock
        base = 1791900000
        for style in STYLES:
            if style == 'space':  # its own 30-minute seed bucket already dedupes it
                continue
            frames = {0: set(), 900: set()}
            for quantum in frames:
                for poll in range(12):  # one hour at the default five-minute poll
                    now = base + poll * 300
                    with mock.patch('time.time', lambda now=now: now):
                        frames[quantum].add(render_page(self._snapshot(now, 8000 - poll * 300),
                                                        style=style, quantum=quantum))
            self.assertEqual(len(frames[0]), 12, style)  # a minute-level countdown never dedupes
            self.assertLessEqual(len(frames[900]), 4, style)

    def test_bucketing_leaves_missing_and_elapsed_resets_alone(self):
        from token_tv.display import quantise
        now = 1791900000
        for remaining in (None, -600):
            snapshot = self._snapshot(now, remaining)
            before = snapshot['accounts']['claude_a']['windows'][0]['resets_at']
            after = quantise(snapshot, 900)['accounts']['claude_a']['windows'][0]['resets_at']
            self.assertEqual(before, after)  # a dash must never become a number

    def test_bucketing_does_not_mutate_the_snapshot_the_dashboard_serves(self):
        from token_tv.display import quantise
        now = 1791900000
        snapshot = self._snapshot(now, 1000)
        original = snapshot['accounts']['claude_a']['windows'][0]['resets_at']
        quantise(snapshot, 900)
        self.assertEqual(snapshot['accounts']['claude_a']['windows'][0]['resets_at'], original)

    def test_the_write_budget_bounds_uploads_even_when_every_frame_differs(self):
        from token_tv.device import may_write
        interval, budget = 300, 6
        allowed, last = [], None
        for poll in range(12):  # an hour of five-minute polls, every frame distinct
            now = 1791900000 + poll * interval
            if may_write(last, now, budget):
                allowed.append(now)
                last = now
        self.assertEqual(len(allowed), budget)
        # Spacing, not a quota: the writes must stay spread out, so the clock never freezes on a
        # stale frame for the rest of the hour once the allowance is spent.
        gaps = [b - a for a, b in zip(allowed, allowed[1:])]
        self.assertLessEqual(max(gaps), 3600 / budget + interval)

    def test_a_zero_budget_means_no_cap(self):
        from token_tv.device import may_write
        last = None
        for poll in range(50):
            now = 1791900000 + poll * 10
            self.assertTrue(may_write(last, now, 0))
            last = now

    def test_the_first_write_is_never_throttled(self):
        from token_tv.device import may_write
        self.assertTrue(may_write(None, 1791900000, 6))


    def test_a_changed_reading_is_never_deferred_but_a_ticking_countdown_is(self):
        from token_tv.display import data_digest
        now = 1791900000
        base = self._snapshot(now, 8000, used=41.0)
        # Only the countdown moved: the budget may defer this.
        self.assertEqual(data_digest(base), data_digest(self._snapshot(now, 5000, used=41.0)))
        # The reading itself moved: this must reach the clock at once.
        self.assertNotEqual(data_digest(base), data_digest(self._snapshot(now, 8000, used=42.0)))

    def test_a_changed_status_counts_as_a_changed_reading(self):
        from token_tv.display import data_digest
        now = 1791900000
        base = self._snapshot(now, 8000)
        stale = self._snapshot(now, 8000)
        stale['accounts']['claude_a']['status'] = 'auth_required'
        self.assertNotEqual(data_digest(base), data_digest(stale))


class QualityLadderTests(unittest.TestCase):
    def test_a_busy_frame_steps_quality_down_until_it_fits(self):
        from token_tv.display import encode_jpeg
        from token_tv.themes import RENDERERS
        image = RENDERERS['retro'](FlashWearTests._snapshot(1791900000, 8000, used=88.0))
        full, quality, _ = encode_jpeg(image, 0)
        tight, lower, _ = encode_jpeg(image, len(full) - 2000)
        self.assertLessEqual(len(tight), len(full) - 2000)
        self.assertLess(lower, quality)

    def test_an_impossible_limit_returns_the_smallest_attempt_and_reports_it(self):
        from token_tv.display import render_frame
        data, meta = render_frame(FlashWearTests._snapshot(1791900000, 8000), style='retro', limit=10)
        self.assertFalse(meta['fits'])  # the caller reports it rather than the clock rejecting it
        self.assertTrue(data)


class ComboFaceTests(unittest.TestCase):
    """One clock showing both AI usage and a watchlist."""

    @staticmethod
    def _usage(now=1791900000):
        return {'updated_at': now, 'accounts': {'c': {
            'key': 'c', 'provider': 'claude', 'alias': 'CLAUDE A', 'status': 'ok',
            'fetched_at': now, 'last_success_at': now,
            'windows': [{'label': 'WEEK', 'used_percent': 61.0, 'resets_at': now + 7000}]}}}

    @staticmethod
    def _feed(now, is_open=True, price=343.0):
        return {'updated_at': now, 'source': 'FINNHUB',
                'market': {'is_open': is_open, 'session': 'regular'},
                'symbols': [{'symbol': 'AAPL', 'price': price, 'change': 1.0, 'percent': 0.3,
                             'previous_close': price - 1, 'at': now}]}

    def test_the_market_section_only_appears_while_the_market_is_open(self):
        from token_tv.combo import market_rows
        now = time.time()
        self.assertIsNotNone(market_rows(dict(self._usage(), stocks=self._feed(now))))
        self.assertIsNone(market_rows(dict(self._usage(), stocks=self._feed(now, is_open=False))))

    def test_a_stale_or_missing_feed_falls_back_rather_than_showing_old_prices(self):
        from token_tv.combo import market_rows
        now = time.time()
        self.assertIsNone(market_rows(self._usage()))                       # no feed at all
        self.assertIsNone(market_rows(dict(self._usage(), stocks={})))      # empty feed
        stale = self._feed(now - 4000)                                      # well past STALE_AFTER
        self.assertIsNone(market_rows(dict(self._usage(), stocks=stale)))

    def test_closed_market_renders_the_full_size_usage_face(self):
        now = time.time()
        closed = dict(self._usage(), stocks=self._feed(now, is_open=False))
        self.assertEqual(render_page(closed, style='combo'), render_page(closed, style='pixel'))
        open_ = dict(self._usage(), stocks=self._feed(now))
        self.assertNotEqual(render_page(open_, style='combo'), render_page(open_, style='pixel'))

    def test_a_price_move_counts_as_changed_data_so_the_budget_cannot_defer_it(self):
        from token_tv.display import data_digest
        now = time.time()
        a = dict(self._usage(), stocks=self._feed(now, price=343.0))
        b = dict(self._usage(), stocks=self._feed(now, price=343.5))
        self.assertNotEqual(data_digest(a), data_digest(b))
        self.assertEqual(data_digest(a), data_digest(dict(self._usage(), stocks=self._feed(now, price=343.0))))

    def test_a_broken_feed_file_never_stops_the_usage_face_publishing(self):
        from token_tv.live import read_feed
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            self.assertIsNone(read_feed(None))
            self.assertIsNone(read_feed(path))              # absent
            path.write_text('{not json')
            self.assertIsNone(read_feed(path))              # unreadable
            path.write_text('[]')
            self.assertIsNone(read_feed(path))              # wrong shape
            path.write_text(json.dumps({'market': {'is_open': False}}))
            self.assertEqual(read_feed(path), {'market': {'is_open': False}})

    def test_the_combo_frame_fits_the_firmware_limit(self):
        from token_tv.device import FILES, LIMITS
        now = time.time()
        frame = render_page(dict(self._usage(), stocks=self._feed(now)), style='combo',
                            limit=LIMITS[FILES[0]][0])
        self.assertLessEqual(len(frame), LIMITS[FILES[0]][0])
        self.assertEqual(Image.open(io.BytesIO(frame)).size, (240, 240))
