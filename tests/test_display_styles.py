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


class StocksControlsTests(unittest.TestCase):
    """POST /stocks/mode and /stocks/symbols follow the /display/style hardening pattern."""

    @staticmethod
    def _account():
        return {'key': 'a', 'alias': 'CLAUDE A', 'provider': 'claude', 'email': 'owner@example.com'}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def _start(self, stock_data, with_feed=True):
        """A live handler + preferences bound to a temp config and stock config file."""
        from token_tv.live import DisplayPreferences, handler
        from token_tv.state import UsageStore
        directory = Path(self.directory.name)
        account = self._account()
        main_config = {'accounts': [account], 'display_style': 'pixel'}
        if with_feed:
            stocks_path = directory / 'stocks.json'
            stocks_path.write_text(json.dumps(stock_data if stock_data is not None else {}))
            main_config['stocks_config'] = str(stocks_path)
        else:
            stocks_path = None
        path = directory / 'config.json'
        path.write_text(json.dumps(main_config))
        row = dict(account, status='ok', windows=[{'label': 'WEEK', 'used_percent': 94, 'resets_at': None}], fetched_at=100)
        store = UsageStore([account], fetch=Mock(return_value=row))
        store.refresh()
        preferences = DisplayPreferences(path, main_config)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, preferences))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        return f'http://127.0.0.1:{server.server_port}', preferences, stocks_path, server, thread

    def _stop(self, server, thread):
        server.shutdown()
        server.server_close()
        thread.join()

    def post(self, root, path, payload, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        return urlopen(Request(root + path, data=json.dumps(payload).encode(), headers=headers))

    def test_mode_and_symbols_persist_into_the_stock_config(self):
        root, _, stocks_path, server, thread = self._start({'symbols': ['AAPL']})
        from token_tv import stocks
        try:
            with urlopen(root + '/display') as response:
                display = json.load(response)
            self.assertEqual(display['stocks'], {'mode': 'watchlist', 'symbols': ['AAPL'], 'movers_count': 2})
            with self.post(root, '/stocks/mode', {'mode': 'movers'}) as response:
                state = json.load(response)
            self.assertEqual(state['stocks']['mode'], 'movers')
            self.assertEqual(json.loads(stocks_path.read_text())['mode'], 'movers')
            with self.post(root, '/stocks/symbols', {'symbols': ['msft', 'NVDA ']}) as response:
                json.load(response)
            saved = json.loads(stocks_path.read_text())
            self.assertEqual(saved['symbols'], ['MSFT', 'NVDA'])  # upper-cased, trimmed
            stocks.load_stock_config(stocks_path)  # the feeder reloads it without complaint
            with self.post(root, '/stocks/mode', {'mode': 'watchlist'}):
                pass
        finally:
            self._stop(server, thread)

    def test_symbols_validation_paths(self):
        root, _, stocks_path, server, thread = self._start({'symbols': ['AAPL']})
        try:
            for symbols in ([], ['aapl', 'AAPL'], ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD'],
                            'AAPL', ['AA PL'], [{'x': 1}], [42], ['aapl', 'msft', 'nvda', 'tsla', 'amd', 'meta', 'goog']):
                with self.assertRaises(HTTPError, msg=symbols) as raised:
                    self.post(root, '/stocks/symbols', {'symbols': symbols})
                self.assertEqual(raised.exception.code, 400)
            for data in ({}, {'mode': 'movers'}, {'symbols': ['AAPL'], 'extra': 1}):
                with self.assertRaises(HTTPError, msg=data) as raised:
                    urlopen(Request(root + '/stocks/symbols', data=json.dumps(data).encode(),
                                    headers={'Content-Type': 'application/json'}))
                self.assertEqual(raised.exception.code, 400)
            self.assertEqual(json.loads(stocks_path.read_text())['symbols'], ['AAPL'])  # nothing saved on 400
        finally:
            self._stop(server, thread)

    def test_a_stocks_post_without_a_stock_feed_is_a_conflict(self):
        root, _, _, server, thread = self._start(None, with_feed=False)
        try:
            for path, payload in (('/stocks/mode', {'mode': 'movers'}), ('/stocks/symbols', {'symbols': ['AAPL']})):
                with self.assertRaises(HTTPError) as raised:
                    self.post(root, path, payload)
                self.assertEqual(raised.exception.code, 409)
            with urlopen(root + '/display') as response:
                self.assertIsNone(json.load(response)['stocks'])  # no feed section to render
        finally:
            self._stop(server, thread)

    def test_stocks_state_clamps_an_unusable_movers_count_to_the_default(self):
        from token_tv.live import DisplayPreferences
        directory = Path(self.directory.name)
        account = self._account()
        main_config = {'accounts': [account], 'display_style': 'pixel'}
        main_config['stocks_config'] = str(directory / 'stocks.json')
        path = directory / 'config.json'
        path.write_text(json.dumps(main_config))
        preferences = DisplayPreferences(path, main_config)
        for raw, clamped in ((99, 2), (0, 2), (-1, 2), ('2', 2), (2.0, 2), (True, 2), (None, 2), (1, 1), (4, 4)):
            (directory / 'stocks.json').write_text(json.dumps({'symbols': ['AAPL'], 'movers_count': raw}))
            self.assertEqual(preferences.snapshot()['stocks']['movers_count'], clamped, raw)

    def test_a_stock_config_that_is_not_an_object_is_a_conflict(self):
        root, _, stocks_path, server, thread = self._start(None)
        stocks_path.write_text('[1, 2, 3]')
        try:
            with self.assertRaises(HTTPError) as raised:
                self.post(root, '/stocks/mode', {'mode': 'movers'})
            self.assertEqual(raised.exception.code, 409)  # nothing was corrupted into an object
        finally:
            self._stop(server, thread)

    def test_origin_mismatch_and_bad_mode_are_rejected_like_the_style_endpoint(self):
        root, _, stocks_path, server, thread = self._start({'symbols': ['AAPL']})
        try:
            # A cross-origin POST is always exactly 403, on all three POST endpoints.
            for path, payload in (('/display/style', {'style': 'pixel'}),
                                  ('/stocks/mode', {'mode': 'movers'}),
                                  ('/stocks/symbols', {'symbols': ['AAPL']})):
                headers = {'Content-Type': 'application/json', 'Origin': 'https://unrelated.example'}
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request(root + path, data=json.dumps(payload).encode(), headers=headers))
                self.assertEqual(raised.exception.code, 403, path)
            self.assertEqual(json.loads(stocks_path.read_text())['symbols'], ['AAPL'])  # nothing changed
        finally:
            self._stop(server, thread)

    def test_bad_bodies_are_400_and_unknown_paths_are_404(self):
        root, _, stocks_path, server, thread = self._start({'symbols': ['AAPL']})
        try:
            # A bad body is exactly 400, and an unknown endpoint is exactly 404.
            for path, payload, code in (('/stocks/mode', {'mode': 'banana'}, 400),
                                        ('/stocks/mode', {'mode': 'movers', 'extra': 1}, 400),
                                        ('/stocks/symbols', {'symbols': ['AAPL'] * 2}, 400),
                                        ('/stocks/nope', {'mode': 'movers'}, 404)):
                with self.assertRaises(HTTPError) as raised:
                    self.post(root, path, payload)
                self.assertEqual(raised.exception.code, code, path)
            self.assertEqual(json.loads(stocks_path.read_text())['symbols'], ['AAPL'])  # nothing changed
        finally:
            self._stop(server, thread)


class StocksOnlyFaceTests(unittest.TestCase):
    """The stocks face: only the four market rows, no usage rows, dash slots when dead."""

    @staticmethod
    def _feed(now=None, is_open=True):
        now = time.time() if now is None else now  # a feed only reads as live while it is fresh
        return {'updated_at': now, 'source': 'YAHOO',
                'market': {'is_open': is_open, 'session': 'regular', 'exchange': 'NSE'},
                'symbols': [{'symbol': 'RELIANCE.NS', 'price': 1174.6, 'change': 27.9, 'percent': 4.03,
                             'previous_close': None, 'at': now},
                            {'symbol': 'TCS.NS', 'price': 2304.9, 'change': 65.6, 'percent': 3.05,
                             'previous_close': None, 'at': now},
                            {'symbol': 'HDFCBANK.NS', 'price': 710.7, 'change': -21.3, 'percent': -3.2,
                             'previous_close': None, 'at': now},
                            {'symbol': 'INFY.NS', 'price': 812.1, 'change': -30.1, 'percent': -3.71,
                             'previous_close': None, 'at': now}]}

    def test_stocks_animates_while_the_market_is_open(self):
        from token_tv.display import ANIMATED_STYLES, STYLES, render_page
        self.assertIn('stocks', STYLES)
        self.assertNotIn('stocks', ANIMATED_STYLES)  # animated-while-alive, a member of neither list
        snapshot = {'accounts': {}, 'stocks': self._feed()}
        body = render_page(snapshot, style='stocks')
        self.assertEqual(body[:4], b'GIF8')  # an animated GIF, not one of the static styles
        image = Image.open(io.BytesIO(body))
        self.assertEqual((image.format, image.size), ('GIF', (240, 240)))
        self.assertGreaterEqual(image.n_frames, 4)
        self.assertLessEqual(len(body), 250000)
        # The arrows breathe: the frames are not all alike...
        image.seek(0); first = image.convert('RGB')
        image.seek(2); third = image.convert('RGB')
        self.assertNotEqual(list(first.getdata()), list(third.getdata()))
        # ...but both carry the board inks: a rise and a fall share the face.
        self.assertIn((8, 247, 66), {c for _, c in third.getcolors(240 * 240)})
        self.assertIn((255, 68, 68), {c for _, c in third.getcolors(240 * 240)})

    def test_a_closed_or_missing_feed_draws_dashes_and_never_zeros(self):
        from token_tv import combo_stocks
        from token_tv.combo_stocks import FALL, RISE
        from token_tv.display import render_page
        image = Image.open(io.BytesIO(render_page({'accounts': {}, 'stocks': self._feed(is_open=False)}, style='stocks')))
        self.assertNotIn(RISE, {colour for _, colour in image.getcolors(240 * 240)})
        self.assertNotIn(FALL, {colour for _, colour in image.getcolors(240 * 240)})
        # The dash glyphs are really drawn: every card carries a dim-grey '--' in its symbol slot
        # (JPEG shifts the dim ink, so this keeps the ±12 tolerance the glyph test has always used).
        for index in range(combo_stocks.MAX_ROWS):
            y = combo_stocks.TOP + index * combo_stocks.PITCH
            slot = image.crop((14, y + 7, 14 + 22, y + 7 + 14))  # a scale-2 '--' glyph box in the card
            pixels = list(slot.getdata())
            grey = [px for px in pixels if all(abs(ink - 74) <= 12 for ink in px)]
            self.assertTrue(grey, f'row {index} has no dash glyph')
            self.assertLess(len(grey), len(pixels), f'row {index} is solid ink, not a glyph')
        no_feed = Image.open(io.BytesIO(render_page({'accounts': {}}, style='stocks')))
        self.assertNotIn(RISE, {colour for _, colour in no_feed.getcolors(240 * 240)})
        # Closed or missing, the face is still its own board — never the usage face underneath.
        self.assertNotEqual(render_page({'accounts': {}}, style='stocks'), render_page({'accounts': {}}, style='pixel'))
        stale = Image.open(io.BytesIO(render_page({'accounts': {}, 'stocks': self._feed(now=0)}, style='stocks')))
        self.assertNotIn(RISE, {colour for _, colour in stale.getcolors(240 * 240)})  # stale reads like none
        self.assertNotIn((255, 68, 68), {colour for _, colour in stale.getcolors(240 * 240)})

    def test_a_closed_board_with_no_feed_claims_no_exchange(self):
        from token_tv.display import render_page
        # Without any feed there is no exchange or source: the header says nothing rather than
        # stamping the FINNHUB fallback over a market it knows nothing about.
        self.assertEqual(render_page({'accounts': {}, 'stocks': {'market': {'is_open': False}, 'symbols': []}},
                                     style='stocks'),
                         render_page({'accounts': {}}, style='stocks'))

    def test_night_cards_are_tinted_by_their_sign_on_the_night_sky(self):
        from token_tv import combo_stocks
        from token_tv.display import render_page
        cols = {c for _, c in Image.open(io.BytesIO(render_page({'accounts': {}, 'stocks': self._feed()},
                                                                style='stocks'))).convert('RGB').getcolors(240 * 240)}
        def near(target, tol=12):
            return any(all(abs(a - b) <= tol for a, b in zip(c, target)) for c in cols)
        self.assertTrue(near((10, 14, 28), 6), 'night sky missing')
        self.assertTrue(near(combo_stocks.mix(combo_stocks.RISE, .10), 8), 'no rise-tinted card fill')
        self.assertTrue(near(combo_stocks.mix(combo_stocks.FALL, .10), 8), 'no fall-tinted card fill')
        self.assertTrue(near((150, 160, 190)), 'header ink missing')

    def test_the_magnitude_bar_keeps_the_five_percent_fill_convention(self):
        from token_tv import combo_stocks
        at = time.time()
        def bar_ink(percent):
            row = {'symbol': 'APPN', 'price': 42.52, 'change': 1.0, 'percent': percent,
                   'previous_close': None, 'at': at}
            feed = {'updated_at': at, 'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
                    'symbols': [row] + self._feed()['symbols'][1:]}
            image = combo_stocks.render_combo_stocks({'accounts': {}, 'stocks': feed})  # the static board
            y = combo_stocks.TOP + 47
            strip = image.crop((14, y, 227, y + 2))
            return sum(1 for px in strip.getdata() if px[1] > px[2] + 25)  # green-ish fill pixels
        self.assertGreater(bar_ink(5.0), bar_ink(2.5))  # twice the move, twice the fill

    def test_a_flat_quote_draws_no_arrow_on_its_card(self):
        from token_tv import combo_stocks
        at = time.time()
        def board(first_row):
            feed = {'updated_at': at, 'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
                    'symbols': [first_row] + self._feed()['symbols'][1:]}
            return combo_stocks.render_combo_stocks({'accounts': {}, 'stocks': feed})  # the static board
        flat = {'symbol': 'FLAT', 'price': 10.0, 'change': 0.0, 'percent': 0.0, 'previous_close': None, 'at': at}
        down = {'symbol': 'OUST', 'price': 30.74, 'change': -1.13, 'percent': -3.55, 'previous_close': None, 'at': at}
        y = combo_stocks.TOP
        arrow_box = (100, y + 10, 125, y + 23)  # where the arrow polygon sits beside the percent
        flat_pixels = list(board(flat).crop(arrow_box).getdata())
        self.assertTrue(all(max(px) < 50 for px in flat_pixels), 'the flat card drew an arrow')
        down_pixels = list(board(down).crop(arrow_box).getdata())
        self.assertTrue(any(px[0] > 120 and px[1] < 100 for px in down_pixels), 'the control arrow is missing')

    def test_rows_fills_the_four_slots_and_a_dead_quote_is_a_dash_slot(self):
        from token_tv.display import render_page
        live = render_page({'accounts': {}, 'stocks': self._feed()}, style='stocks')
        dead = dict(self._feed(), symbols=self._feed()['symbols'][:3] + [{'symbol': 'DEAD.NS', 'price': None, 'change': None, 'percent': None, 'previous_close': None, 'at': 0}])
        with_dead = render_page({'accounts': {}, 'stocks': dead}, style='stocks')
        self.assertNotEqual(with_dead, live)  # the dead row is drawn as dashes, not silently cropped
        fewer = render_page({'accounts': {}, 'stocks': dict(self._feed(), symbols=self._feed()['symbols'][:3])}, style='stocks')
        self.assertNotEqual(fewer, with_dead)  # ...and not exactly like an empty fourth slot
        self.assertNotEqual(fewer, live)

    def test_the_stocks_face_fits_the_firmware_limit(self):
        from token_tv.device import LIMITS
        from token_tv.display import render_page
        body = render_page({'accounts': {}, 'stocks': self._feed()}, style='stocks')  # a live, open market: GIF
        self.assertLessEqual(len(body), LIMITS["tokentv.gif"][0])
        image = Image.open(io.BytesIO(body))
        self.assertEqual((image.size, image.format), ((240, 240), 'GIF'))
        # The closed board is the static JPEG side of the style, still on the JPEG budget.
        closed = render_page({'accounts': {}, 'stocks': self._feed(is_open=False)}, style='stocks',
                             limit=LIMITS["tokentv.jpg"][0])
        self.assertLessEqual(len(closed), LIMITS["tokentv.jpg"][0])
        self.assertEqual(Image.open(io.BytesIO(closed)).format, 'JPEG')

    def test_post_display_style_accepts_the_stocks_face(self):
        from token_tv.live import DisplayPreferences, handler
        from token_tv.sources import load_config
        from token_tv.state import UsageStore
        account = {'key': 'a', 'alias': 'CLAUDE A', 'provider': 'claude', 'email': 'owner@example.com'}
        row = dict(account, status='ok', windows=[{'label': 'WEEK', 'used_percent': 94, 'resets_at': None}], fetched_at=100)
        store = UsageStore([account], fetch=Mock(return_value=row))
        store.refresh()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = {'accounts': [account], 'display_style': 'pixel'}
            path.write_text(json.dumps(config))
            preferences = DisplayPreferences(path, config)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, preferences))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            root = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(root + '/frame/0.jpg?style=stocks') as response:
                    self.assertEqual(Image.open(io.BytesIO(response.read())).size, (240, 240))
                with urlopen(Request(root + '/display/style', data=b'{"style":"stocks"}',
                                     headers={'Content-Type': 'application/json'})) as response:
                    self.assertEqual(json.load(response)['style'], 'stocks')
                self.assertEqual(load_config(path)['display_style'], 'stocks')
                self.assertEqual(DisplayPreferences(path, load_config(path)).snapshot()['style'], 'stocks')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class AnimatedScreenTests(unittest.TestCase):
    """The animatic: breathing arrows on the stocks GIF, shimmering gauges on the usage GIF."""

    @staticmethod
    def _snapshot(now):
        feed = {'updated_at': int(now), 'source': 'YAHOO',
                'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
                'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                             'previous_close': None, 'at': int(now)},
                            {'symbol': 'OUST', 'price': 30.74, 'change': -1.13, 'percent': -3.55,
                             'previous_close': None, 'at': int(now)}]}
        accounts = {'claude_a': {'key': 'claude_a', 'provider': 'claude', 'alias': 'CLAUDE A', 'status': 'ok',
                                 'fetched_at': int(now),
                                 'windows': [{'label': 'WEEK', 'used_percent': 61.0, 'resets_at': None}]}}
        return {'schema': 1, 'updated_at': int(now), 'accounts': accounts, 'stocks': feed}

    def test_the_stocks_screen_animates_its_arrows(self):
        from token_tv.display import render_frame
        body, meta = render_frame(self._snapshot(time.time()), 0, 'stocks', 900, 400000)
        self.assertEqual(body[:4], b'GIF8')
        self.assertTrue(meta['fits'])
        image = Image.open(io.BytesIO(body))
        self.assertEqual((image.size, image.format), ((240, 240), 'GIF'))
        self.assertGreaterEqual(image.n_frames, 4)
        self.assertLessEqual(len(body), 250000)
        image.seek(0); first = list(image.convert('RGB').getdata())
        image.seek(2); third = list(image.convert('RGB').getdata())
        self.assertNotEqual(first, third)  # the arrow-and-glow layer breathes across the frames

    def test_the_usage_screen_animates_its_gauges(self):
        from token_tv.usage_gif import USAGE_FRAMES, render_usage_gif
        body = render_usage_gif(self._snapshot(time.time()))
        self.assertEqual(body[:4], b'GIF8')
        image = Image.open(io.BytesIO(body))
        self.assertEqual((image.size, image.format), ((240, 240), 'GIF'))
        self.assertGreaterEqual(image.n_frames, 6)
        self.assertLessEqual(len(body), 150000)
        image.seek(0); first = list(image.convert('RGB').getdata())
        image.seek(3); fourth = list(image.convert('RGB').getdata())
        self.assertNotEqual(first, fourth)  # the shimmer moves across the provider gauges

    def test_a_static_style_stays_a_static_jpeg(self):
        from token_tv.display import render_frame
        for style in ('neon', 'digital'):
            body, _ = render_frame(self._snapshot(time.time()), 0, style, 900, 60000)
            self.assertEqual(body[:3], b'\xff\xd8\xff')  # JPEG, unchanged by the alternation
            self.assertEqual(Image.open(io.BytesIO(body)).format, 'JPEG')

    def test_the_frame_preview_serves_gif_bytes_as_image_gif(self):
        from token_tv.live import handler

        class FeedStore:  # the /frame preview renders store.snapshot(): this one carries a live stock feed
            def snapshot(self):
                return self._snapshot(time.time())

        live_store = {'schema': 1, 'updated_at': int(time.time()), 'accounts': {}, 'stocks': {
            'updated_at': int(time.time()), 'source': 'YAHOO',
            'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
            'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                         'previous_close': None, 'at': int(time.time())}]}}
        FeedStore.snapshot = lambda self: live_store
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(FeedStore()))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        root = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(root + '/frame/0.jpg?style=stocks') as response:  # an alive market: GIF
                self.assertEqual(response.headers['Content-Type'], 'image/gif')
                self.assertEqual(response.read()[:4], b'GIF8')
            with urlopen(root + '/frame/0.jpg?style=neon') as response:  # a static style: JPEG
                self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                self.assertEqual(response.read()[:3], b'\xff\xd8\xff')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


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
            if style == 'stocks':  # the stock board has no countdown to bucket: it cannot tick
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
