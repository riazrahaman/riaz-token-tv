import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from PIL import Image

from token_tv import cli, stocks

TOKEN = 'DO-NOT-EXPORT-FINNHUB-KEY'


def sample_rows():
    return [
        {'symbol': 'AAPL', 'price': 339.58, 'change': 2.94, 'percent': 0.8733, 'previous_close': 336.64, 'at': 1791828095},
        {'symbol': 'MSFT', 'price': 512.34, 'change': -3.21, 'percent': -0.62, 'previous_close': 515.55, 'at': 1791828095},
        {'symbol': 'NVDA', 'price': 188.42, 'change': 4.55, 'percent': 2.47, 'previous_close': 183.87, 'at': 1791828095},
        {'symbol': 'TSLA', 'price': 421.10, 'change': -8.30, 'percent': -1.93, 'previous_close': 429.40, 'at': 1791828095},
    ]


def sample_meta(is_open=True, session='regular'):
    return {'is_open': is_open, 'session': session, 'source': 'FINNHUB', 'now': 1791828095 + 12}


class FakeDevice:
    """Minimal SD_PRO stand-in: counts uploads, activations and captures."""

    MODEL = 'sdpro'

    def __init__(self):
        self.uploads = []
        self.activations = 0
        self.captures = 0
        self.restored = None
        self.album = [{'name': 'original.jpg', 'enabled': True}]

    def capture(self):
        self.captures += 1
        files = [dict(f) for f in self.album]
        return {'themes': [{'id': 0, 'enabled': True}, {'id': 2, 'enabled': False}], 'theme_interval': 10,
                'files': files, 'photo_interval': 10}

    def check_backup(self, original):
        return None

    def upload(self, name, image):
        self.uploads.append((name, image))
        return {'file': name, 'status': 200, 'bytes': len(image)}

    def upload_rotation(self, frames):
        return [self.upload(name, image) for name, image in frames]

    def activate(self, original, name='tokentv.jpg', wanted=None):
        self.activations += 1

    def restore(self, original):
        self.restored = original


class PaletteTests(unittest.TestCase):
    def test_rgb565_decodes_the_clock_native_colours(self):
        self.assertEqual(stocks.rgb565_to_rgb(64388), (255, 113, 33))
        self.assertEqual(stocks.rgb565_to_rgb(19199), (74, 93, 255))
        self.assertEqual(stocks.rgb565_to_rgb(4008), (8, 247, 66))
        self.assertEqual(stocks.rgb565_to_rgb(0), (0, 0, 0))
        self.assertEqual(stocks.rgb565_to_rgb(0xFFFF), (255, 255, 255))
        # The defaults are those decoded colours, exactly.
        self.assertEqual(stocks.PALETTE['text'], (255, 113, 33))
        self.assertEqual(stocks.PALETTE['rise'], (8, 247, 66))
        self.assertEqual(stocks.PALETTE['fall'], (74, 93, 255))
        self.assertEqual(stocks.PALETTE['background'], (0, 0, 0))


class QuoteTests(unittest.TestCase):
    def test_a_missing_quote_is_a_dash_and_never_zero(self):
        for data in ({}, {'c': 0, 'd': 0, 'dp': 0, 'h': 0, 'l': 0, 'o': 0, 'pc': 0, 't': 0},
                     {'c': None, 'd': None, 'dp': None, 't': None}, {'c': -1, 'd': 0}):
            row = stocks.quote_row('AAPL', data)
            self.assertIsNone(row['price'], data)
            self.assertIsNone(row['percent'], data)
            self.assertEqual(stocks.price_text(row['price']), '--')
            self.assertEqual(stocks.change_text(row['change'], row['percent']), '--')
            self.assertNotIn('0', stocks.price_text(row['price']))
        # A real quote keeps its numbers.
        row = stocks.quote_row('AAPL', {'c': 339.58, 'd': 2.94, 'dp': 0.8733, 'pc': 336.64, 't': 1791828095})
        self.assertEqual(stocks.price_text(row['price']), '339.58')
        self.assertEqual(stocks.change_text(row['change'], row['percent']), '+2.94 +0.87%')
        self.assertEqual(row['at'], 1791828095)

    def test_a_rising_change_is_green_and_a_falling_one_is_blue(self):
        self.assertEqual(stocks.change_ink(1.2), stocks.PALETTE['rise'])
        self.assertEqual(stocks.change_ink(-0.4), stocks.PALETTE['fall'])
        self.assertEqual(stocks.change_ink(0), stocks.PALETTE['text'])
        self.assertEqual(stocks.change_ink(None), stocks.PALETTE['dim'])
        # And it reaches the pixels: the up row has green, the down row has blue.
        up = stocks.render_image([sample_rows()[0]], sample_meta())
        down = stocks.render_image([sample_rows()[1]], sample_meta())
        self.assertIn(stocks.PALETTE['rise'], {colour for _, colour in up.getcolors(240 * 240)})
        self.assertIn(stocks.PALETTE['fall'], {colour for _, colour in down.getcolors(240 * 240)})

    def test_magnitude_saturates_at_five_percent(self):
        self.assertEqual(stocks.magnitude(None), 0.0)
        self.assertEqual(stocks.magnitude(0), 0.0)
        self.assertAlmostEqual(stocks.magnitude(2.5), 0.5)
        self.assertEqual(stocks.magnitude(5.0), 1.0)
        self.assertEqual(stocks.magnitude(-9.0), 1.0)

    def test_market_poll_interval_is_thirty_open_and_three_hundred_closed(self):
        self.assertEqual(stocks.OPEN_INTERVAL, 30)
        self.assertEqual(stocks.CLOSED_INTERVAL, 300)
        for open_status in ({'isOpen': True}, {'is_open': True}):
            self.assertEqual(stocks.market_poll_interval(open_status), 30, open_status)
        for closed_status in ({'isOpen': False}, {'is_open': False}, None, {}):
            self.assertEqual(stocks.market_poll_interval(closed_status), 300, closed_status)

    def test_status_parsing_keeps_only_bools_and_strings(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def read(self):
                return b'{"exchange":"US","holiday":null,"isOpen":true,"session":"regular","timezone":"America/New_York"}'

        with patch('token_tv.stocks.urlopen', return_value=Response()):
            self.assertEqual(stocks.fetch_market_status('key'),
                             {'is_open': True, 'session': 'regular'})

    def test_missing_token_is_a_source_error_and_the_keychain_is_used_on_macos(self):
        with patch.dict('os.environ', {}, clear=True), patch('token_tv.stocks.sys.platform', 'linux'):
            with self.assertRaises(stocks.StockError):
                stocks.finnhub_token()
        with patch.dict('os.environ', {'FINNHUB_API_KEY': TOKEN}):
            self.assertEqual(stocks.finnhub_token(), TOKEN)
        found = Mock(returncode=0, stdout=TOKEN + '\n')
        with patch.dict('os.environ', {}, clear=True), patch('token_tv.stocks.sys.platform', 'darwin'), \
                patch('token_tv.stocks.subprocess.run', return_value=found) as call:
            self.assertEqual(stocks.finnhub_token(), TOKEN)
        self.assertEqual(call.call_args[0][0],
                         ['security', 'find-generic-password', '-s', 'token-tv-finnhub', '-w'])


class RenderTests(unittest.TestCase):
    def test_render_watchlist_is_a_240_jpeg_with_many_colours(self):
        body = stocks.render_watchlist(sample_rows(), sample_meta())
        self.assertEqual(body[:3], b'\xff\xd8\xff')
        image = Image.open(io.BytesIO(body))
        self.assertEqual(image.size, (240, 240))
        self.assertEqual(image.format, 'JPEG')
        self.assertLess(len(body), 60000)
        self.assertGreater(len(image.getcolors(240 * 240)), 20)

    def test_different_data_renders_different_bytes(self):
        base = stocks.render_watchlist(sample_rows(), sample_meta())
        self.assertNotEqual(base, stocks.render_watchlist(sample_rows(), sample_meta(is_open=False, session='closed')))
        changed = [dict(row) for row in sample_rows()]
        changed[0]['price'] = 999.99
        self.assertNotEqual(base, stocks.render_watchlist(changed, sample_meta()))
        # The closed frame is byte-stable when nothing changed: the guard has something to catch.
        closed = sample_meta(is_open=False, session='closed')
        self.assertEqual(stocks.render_watchlist(sample_rows(), closed),
                         stocks.render_watchlist(sample_rows(), closed))

    def test_empty_and_numeric_market_are_drawn(self):
        for meta in ({'is_open': None, 'session': None}, {'is_open': False, 'session': 'post-market'}):
            image = stocks.render_image([], meta)
            self.assertEqual(image.size, (240, 240))
        with self.assertRaises(ValueError):
            stocks.render_image(sample_rows(), sample_meta(), style='neon')


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'stocks.json'

    def tearDown(self):
        self.directory.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_accepts_a_good_file_and_applies_defaults(self):
        state = Path(self.directory.name) / 'state'
        self.write({'device_url': 'http://192.168.0.50', 'symbols': ['AAPL', 'MSFT'], 'interval': 60,
                    'palette': {'rise': '#12FF34', 'dim': 4008}, 'state_dir': str(state)})
        config = stocks.load_stock_config(self.path)
        self.assertEqual(config['device_url'], 'http://192.168.0.50')
        self.assertEqual(config['symbols'], ['AAPL', 'MSFT'])
        self.assertEqual(config['interval'], 60)
        self.assertEqual(config['palette']['rise'], (0x12, 0xFF, 0x34))
        self.assertEqual(config['palette']['dim'], stocks.GREEN)
        self.assertEqual(config['state_dir'], str(state))
        self.write({})
        defaults = stocks.load_stock_config(self.path)
        self.assertEqual(defaults['symbols'], list(stocks.DEFAULT_SYMBOLS))
        self.assertEqual(defaults['interval'], 30)

    def test_rejects_unknown_credentials_and_bad_symbols_intervals(self):
        cases = [
            {'nope': 1},
            {'finnhub_api_key': 'DO-NOT-EXPORT'},
            {'token': 'DO-NOT-EXPORT'},
            {'api_key': 'DO-NOT-EXPORT'},
            {'key': 'DO-NOT-EXPORT'},
            {'symbols': ['aapl']},
            {'symbols': ['AaPl']},
            {'symbols': ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOG']},
            {'symbols': []},
            {'symbols': ['AAPL', 'AAPL']},
            {'symbols': 'AAPL'},
            {'interval': 0},
            {'interval': 5},
            {'interval': -30},
            {'interval': 999999},
            {'interval': True},
            {'interval': '30'},
            {'palette': {'nope': '#ffffff'}},
            {'palette': {'text': 'orange'}},
            {'palette': {'text': '#ff7121ff'}},
            {'palette': {'text': 70000}},
            {'state_dir': ''},
            {'state_dir': 5},
            {'device_url': 'ftp://clock'},
            {'device_url': 'http://192.168.0.50/photo'},
        ]
        for case in cases:
            self.write(case)
            with self.assertRaises(ValueError, msg=case):
                stocks.load_stock_config(self.path)
        self.path.write_text('["not", "an", "object"]')
        with self.assertRaises(ValueError):
            stocks.load_stock_config(self.path)

    def test_config_handles_symbols_in(self):
        self.write({'symbols_in': ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS']})
        config = stocks.load_stock_config(self.path)
        self.assertEqual(config['symbols_in'], ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS'])
        # Default symbols_in
        self.write({})
        defaults = stocks.load_stock_config(self.path)
        self.assertEqual(defaults['symbols_in'], list(stocks.DEFAULT_SYMBOLS_IN))
        # Too many symbols
        self.write({'symbols_in': ['A.NS', 'B.NS', 'C.NS', 'D.NS', 'E.NS']})
        with self.assertRaises(ValueError):
            stocks.load_stock_config(self.path)
        # Duplicate symbol
        self.write({'symbols_in': ['A.NS', 'A.NS']})
        with self.assertRaises(ValueError):
            stocks.load_stock_config(self.path)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_identical_bytes_are_not_uploaded_twice(self):
        device = FakeDevice()
        backup = self.state / 'display-original.json'
        image = stocks.render_watchlist(sample_rows(), sample_meta())
        original = active = last = None
        original, active, last, uploaded = stocks.publish(device, image, self.state, backup,
                                                          original, active, last, now=100)
        self.assertTrue(uploaded)
        self.assertEqual([name for name, _ in device.uploads], ['tokentv.jpg', 'tokentv-b.jpg'])  # both slots
        self.assertEqual(device.uploads[0][1], device.uploads[1][1])  # the same fresh bytes in each
        self.assertEqual(device.activations, 1)
        # Same bytes again: nothing is rewritten to the clock.
        original, active, last, uploaded = stocks.publish(device, image, self.state, backup,
                                                          original, active, last, now=101)
        self.assertFalse(uploaded)
        self.assertEqual(len(device.uploads), 2)
        self.assertEqual(device.activations, 1)
        # A changed frame uploads, but does not re-activate the same file — again to both slots.
        other = stocks.render_watchlist(sample_rows(), sample_meta(is_open=False, session='closed'))
        original, active, last, uploaded = stocks.publish(device, other, self.state, backup,
                                                          original, active, last, now=102)
        self.assertTrue(uploaded)
        self.assertEqual([name for name, _ in device.uploads],
                         ['tokentv.jpg', 'tokentv-b.jpg', 'tokentv.jpg', 'tokentv-b.jpg'])
        self.assertEqual(device.activations, 1)
        # The backup read, plus one album read on the activating cycle to notice a lost file.
        self.assertEqual(device.captures, 2)
        self.assertTrue(backup.is_file())
        receipt = json.loads((self.state / 'display-receipt.json').read_text())
        self.assertEqual(receipt['at'], 102)
        self.assertEqual(receipt['uploads'][0]['sha256'], __import__('hashlib').sha256(other).hexdigest())
        self.assertEqual((self.state / 'tokentv-b.jpg').read_bytes(), other)  # the local mirror too

    def test_a_gif_frame_upload_keeps_the_single_file_identity(self):
        device = FakeDevice()
        gif = b'GIF8' + b'x' * 40
        stocks.publish(device, gif, self.state, self.state / 'display-original.json',
                       None, None, None, now=100, style='retro')
        self.assertEqual([name for name, _ in device.uploads], ['tokentv.gif'])  # no rotation twin

    def test_the_write_budget_gates_a_whole_two_file_cycle(self):
        # Movers mode can mark ~120 changed cycles an hour at a 30-second poll; the budget spaces
        # upload EVENTS, and each event is one may_write decision spent on both slots.
        device = FakeDevice()
        _, _, last, uploaded = stocks.publish(device, b'image-a', self.state, self.state / 'display-original.json',
                                              None, None, None, now=0, budget=1, last_write=None)
        self.assertTrue(uploaded)
        self.assertEqual(len(device.uploads), 2)  # one decision, two files
        _, _, last, uploaded = stocks.publish(device, b'image-b', self.state, self.state / 'display-original.json',
                                              last, 'tokentv.jpg', last, now=600, budget=1, last_write=0)
        self.assertFalse(uploaded)  # 600 >= 3600/1 waits; the budget spaced the event out
        _, _, last, uploaded = stocks.publish(device, b'image-c', self.state, self.state / 'display-original.json',
                                              last, 'tokentv.jpg', last, now=3610, budget=1, last_write=0)
        self.assertTrue(uploaded)
        self.assertEqual([name for name, _ in device.uploads],
                         ['tokentv.jpg', 'tokentv-b.jpg', 'tokentv.jpg', 'tokentv-b.jpg'])

    def test_the_clock_losing_the_second_slot_forces_a_two_file_redownload(self):
        device = FakeDevice()
        device.album = [{'name': 'original.jpg', 'enabled': True},
                        {'name': 'tokentv.jpg', 'enabled': True},
                        {'name': 'tokentv-b.jpg', 'enabled': True}]
        digest = __import__('hashlib').sha256(b'frame-a').hexdigest()
        last = {'tokentv.jpg': digest, 'tokentv-b.jpg': digest}
        stocks.publish(device, b'frame-a', self.state, self.state / 'display-original.json',
                       {'themes': [{'id': 2, 'enabled': True}]}, 'tokentv.jpg', dict(last), now=10)
        self.assertEqual(len(device.uploads), 0)  # the content and the file state are as they were
        device.album = [f for f in device.album if f['name'] != 'tokentv-b.jpg']  # the clock lost a slot
        _, _, _, uploaded = stocks.publish(device, b'frame-a', self.state, self.state / 'display-original.json',
                                           {'themes': [{'id': 2, 'enabled': True}]}, 'tokentv.jpg',
                                           dict(last), now=20, reassert=True)
        self.assertTrue(uploaded)  # the dedupe must not hide a lost twin
        self.assertEqual([name for name, _ in device.uploads], ['tokentv.jpg', 'tokentv-b.jpg'])

    def test_a_feeder_owned_clock_reasserts_again_without_a_false_lost_frame(self):
        device = FakeDevice()
        device.album = [{'name': 'original.jpg', 'enabled': True},
                        {'name': 'tokentv.jpg', 'enabled': True},
                        {'name': 'tokentv-b.jpg', 'enabled': True},
                        {'name': 'tokentv-usage.jpg', 'enabled': True}]  # a stale slot from an earlier live run
        digest = __import__('hashlib').sha256(b'frame-a').hexdigest()
        last = {'tokentv.jpg': digest, 'tokentv-b.jpg': digest}
        # Reassert #1: the feeder wants only its two stock slots — not lost, nothing uploaded, and
        # the stray usage slot is handed to activate to be put to sleep.
        stocks.publish(device, b'frame-a', self.state, self.state / 'display-original.json',
                       {'themes': [{'id': 2, 'enabled': True}]}, 'tokentv.jpg', dict(last),
                       now=10, reassert=True)
        self.assertEqual(len(device.uploads), 0)  # no false lost frame; the dedupe held
        # Reassert #2: still not lost, so the write budget still gates the event (1/h; 10 s waits).
        _, _, returned_last, uploaded = stocks.publish(device, b'frame-a', self.state,
                                                       self.state / 'display-original.json',
                                                       {'themes': [{'id': 2, 'enabled': True}]},
                                                       'tokentv.jpg', dict(last),
                                                       now=20, reassert=True, budget=1, last_write=0)
        self.assertFalse(uploaded)  # the budget was not bypassed by a phantom lost frame
        self.assertEqual(returned_last, last)  # last-good digests intact

    def test_the_api_key_never_reaches_a_receipt_snapshot_or_the_image(self):
        rows, meta = sample_rows(), sample_meta()
        body = stocks.render_watchlist(rows, meta)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertNotIn(TOKEN, json.dumps(stocks.snapshot_payload(rows, meta)))
        device = FakeDevice()
        stocks.publish(device, body, self.state, self.state / 'display-original.json',
                       None, None, None, now=100)
        for name in ('display-receipt.json', 'display-original.json', 'tokentv.jpg'):
            self.assertNotIn(TOKEN.encode(), (self.state / name).read_bytes(), name)

    def test_a_failed_request_never_leaks_the_key(self):
        error = HTTPError('https://finnhub.io/api/v1/quote?symbol=AAPL&token=' + TOKEN,
                          429, 'Too Many Requests', {}, None)
        with patch('token_tv.stocks.urlopen', side_effect=error):
            with self.assertRaises(stocks.StockError) as caught:
                stocks.fetch_quote('AAPL', TOKEN)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(TOKEN, repr(caught.exception))
        self.assertEqual(caught.exception.code, 'http_429')




class IndianMarketTests(unittest.TestCase):
    def test_display_symbol_cleans_suffixes_and_indices(self):
        self.assertEqual(stocks.display_symbol('RELIANCE.NS'), 'RELIANCE')
        self.assertEqual(stocks.display_symbol('TCS.BO'), 'TCS')
        self.assertEqual(stocks.display_symbol('^NSEI'), 'NIFTY')
        self.assertEqual(stocks.display_symbol('^BSESN'), 'SENSEX')
        self.assertEqual(stocks.display_symbol('AAPL'), 'AAPL')

    def test_is_indian_market_open_checks_ist_trading_hours(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # Thursday 2026-10-15: 09:14 IST (closed), 09:15 IST (open), 15:29 (open), 15:30 (closed)
        t_pre = datetime(2026, 10, 15, 9, 14, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        t_open = datetime(2026, 10, 15, 9, 15, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        t_mid = datetime(2026, 10, 15, 12, 0, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        t_close_pre = datetime(2026, 10, 15, 15, 29, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        t_closed = datetime(2026, 10, 15, 15, 30, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        t_sat = datetime(2026, 10, 17, 12, 0, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        self.assertFalse(stocks.is_indian_market_open(t_pre))
        self.assertTrue(stocks.is_indian_market_open(t_open))
        self.assertTrue(stocks.is_indian_market_open(t_mid))
        self.assertTrue(stocks.is_indian_market_open(t_close_pre))
        self.assertFalse(stocks.is_indian_market_open(t_closed))
        self.assertFalse(stocks.is_indian_market_open(t_sat))

    def test_is_us_market_open_checks_et_trading_hours(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # Wednesday 2026-10-14: 09:29 ET (closed), 09:30 ET (open), 15:59 (open), 16:00 (closed)
        t_pre = datetime(2026, 10, 14, 9, 29, tzinfo=ZoneInfo('America/New_York')).timestamp()
        t_open = datetime(2026, 10, 14, 9, 30, tzinfo=ZoneInfo('America/New_York')).timestamp()
        t_close_pre = datetime(2026, 10, 14, 15, 59, tzinfo=ZoneInfo('America/New_York')).timestamp()
        t_closed = datetime(2026, 10, 14, 16, 0, tzinfo=ZoneInfo('America/New_York')).timestamp()
        self.assertFalse(stocks.is_us_market_open(t_pre))
        self.assertTrue(stocks.is_us_market_open(t_open))
        self.assertTrue(stocks.is_us_market_open(t_close_pre))
        self.assertFalse(stocks.is_us_market_open(t_closed))

    def test_fetch_yahoo_quote_parses_chart_response(self):
        sample_response = {
            'chart': {
                'result': [{
                    'meta': {
                        'regularMarketPrice': 1174.6,
                        'chartPreviousClose': 1166.7,
                        'regularMarketTime': 1791971993,
                        'symbol': 'RELIANCE.NS'
                    }
                }]
            }
        }
        with patch('token_tv.stocks._request_json', return_value=sample_response):
            row = stocks.fetch_yahoo_quote('RELIANCE.NS')
            self.assertEqual(row['symbol'], 'RELIANCE.NS')
            self.assertEqual(row['price'], 1174.6)
            self.assertAlmostEqual(row['change'], 7.9)
            self.assertAlmostEqual(row['percent'], (7.9 / 1166.7) * 100)
            self.assertEqual(row['previous_close'], 1166.7)
            self.assertEqual(row['at'], 1791971993)

    def test_fetch_yahoo_quote_empty_or_zero_returns_dash_row(self):
        sample_empty = {'chart': {'result': [{'meta': {'regularMarketPrice': None}}]}}
        with patch('token_tv.stocks._request_json', return_value=sample_empty):
            row = stocks.fetch_yahoo_quote('EMPTY.NS')
            self.assertIsNone(row['price'])
            self.assertIsNone(row['percent'])

    def test_select_active_market_switches_between_us_and_india(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        cfg = {'symbols': ['AAPL', 'MSFT'], 'symbols_in': ['RELIANCE.NS', 'TCS.NS']}

        # When US market is open
        t_us_open = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo('America/New_York')).timestamp()
        active_us = stocks.select_active_market(cfg, now=t_us_open, us_status={'is_open': True, 'session': 'regular'})
        self.assertEqual(active_us['exchange'], 'US')
        self.assertTrue(active_us['is_open'])
        self.assertEqual(active_us['source'], 'FINNHUB')
        self.assertEqual(active_us['symbols'], ['AAPL', 'MSFT'])

        # When US is closed and India is open (e.g. Thu 11:00 IST)
        t_in_open = datetime(2026, 10, 15, 11, 0, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
        active_in = stocks.select_active_market(cfg, now=t_in_open, us_status={'is_open': False})
        self.assertEqual(active_in['exchange'], 'NSE')
        self.assertTrue(active_in['is_open'])
        self.assertEqual(active_in['source'], 'YAHOO')
        self.assertEqual(active_in['symbols'], ['RELIANCE.NS', 'TCS.NS'])

    def test_render_image_handles_indian_stocks_and_large_prices(self):
        rows = [
            {'symbol': '^NSEI', 'price': 22622.70, 'change': 259.40, 'percent': 1.16, 'previous_close': 22363.30, 'at': 1791972000},
            {'symbol': 'RELIANCE.NS', 'price': 1174.60, 'change': 7.90, 'percent': 0.68, 'previous_close': 1166.70, 'at': 1791972000},
            {'symbol': 'TCS.NS', 'price': 2304.90, 'change': 35.60, 'percent': 1.57, 'previous_close': 2269.30, 'at': 1791972000},
            {'symbol': 'HDFCBANK.NS', 'price': 710.70, 'change': -3.30, 'percent': -0.46, 'previous_close': 714.00, 'at': 1791972000},
        ]
        meta = {'is_open': True, 'session': 'regular', 'source': 'YAHOO', 'exchange': 'NSE', 'now': 1791972000, 'tz': 'Asia/Kolkata'}
        img = stocks.render_image(rows, meta)
        self.assertEqual(img.size, (240, 240))
        jpeg = stocks.render_watchlist(rows, meta)
        self.assertEqual(jpeg[:3], b'\xff\xd8\xff')
        self.assertLess(len(jpeg), 60000)


class CliTests(unittest.TestCase):
    def test_stocks_subcommand_forwards_a_default_state_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'stocks.json'
            config.write_text(json.dumps({'device_url': 'http://192.168.0.50', 'symbols': ['AAPL']}))
            seen = {}
            with patch.object(stocks, 'main', side_effect=lambda: seen.update(argv=list(sys.argv))):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = cli.main(['stocks', '--config', str(config)])
            self.assertEqual(code, 0)
            self.assertIn('--state-dir', seen['argv'])
            self.assertEqual(seen['argv'][seen['argv'].index('--state-dir') + 1],
                             str(Path(directory) / 'state-stocks'))
            self.assertIn('--config', seen['argv'])

    def test_stocks_subcommand_rejects_a_usage_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'accounts': [{'key': 'a', 'alias': 'A', 'provider': 'claude',
                                                         'email': 'a@example.com'}]}))
            err = io.StringIO()
            with patch.object(stocks, 'main') as run, contextlib.redirect_stderr(err):
                code = cli.main(['stocks', '--config', str(config)])
            self.assertEqual(code, 1)
            self.assertIn('not a valid stock config', err.getvalue())
            run.assert_not_called()


class MoversTests(unittest.TestCase):
    """The whole-market movers board: parse, flake guard, dedupe, ranking and fallback."""

    def setUp(self):
        stocks._yahoo_session_state = None  # tests must not share a cached session

    def tearDown(self):
        stocks._yahoo_session_state = None

    @staticmethod
    def quote(symbol, price, percent, change=None, at=1791900000, cap=150_000_000_000.0, volume=200_000.0):
        """A raw screener quote; the India threshold fields ride along (the US never reads them)."""
        return {'symbol': symbol, 'regularMarketPrice': price,
                'regularMarketChange': price * percent / 100 if (change is None and percent is not None) else change,
                'regularMarketChangePercent': percent, 'regularMarketTime': at,
                'intradaymarketcap': cap, 'dayvolume': volume}

    @staticmethod
    def gainers():
        """A Yahoo gainers page: sorted DESC as it answers, per the captured shape."""
        return [MoversTests.quote('G1.NS', 21.0, 6.0), MoversTests.quote('G2.NS', 25.0, 5.5),
                MoversTests.quote('G3.NS', 30.0, 5.0)]

    @staticmethod
    def losers():
        return [MoversTests.quote('L1.NS', 25.0, -3.0), MoversTests.quote('L2.BO', 22.0, -2.6)]

    def test_dead_quotes_are_dropped_and_never_a_fabricated_zero(self):
        quotes = [self.quote('AAPL', 340.0, 1.2), {'symbol': 'NO-FIELDS'},
                  self.quote('ZERO', 0, 5.0), self.quote('NOPCT', 12.0, None), 'junk']
        rows = stocks.screener_rows(quotes)
        self.assertEqual([row['symbol'] for row in rows], ['AAPL'])
        for row in rows:
            self.assertEqual(set(row), set(sample_rows()[0]))  # the row contract combo faces read
            self.assertNotIn('0', stocks.price_text(row['price'])[:1])

    def test_dedupe_keeps_the_ns_listing_over_its_bo_twin_and_drops_sme_junk(self):
        rows = stocks.screener_rows([self.quote('TCS.BO', 100.0, 4.0), self.quote('TCS.NS', 110.0, 4.0),
                                     self.quote('INFY.NS', 50.0, 3.0), self.quote('SMEJUNK-SM', 9.0, 9.9)])
        deduped = stocks.dedupe_movers(rows)
        self.assertEqual([row['symbol'] for row in deduped], ['TCS.NS', 'INFY.NS'])
        self.assertEqual(deduped[0]['price'], 110.0)
        # An .NS twin that arrives after its .BO twin takes the slot without disturbing the order.
        flipped = stocks.screener_rows([self.quote('INFY.BO', 50.0, 3.0), self.quote('TCS.BO', 110.0, 4.0),
                                        self.quote('TCS.NS', 110.0, 4.0)])
        self.assertEqual([row['symbol'] for row in stocks.dedupe_movers(flipped)], ['INFY.BO', 'TCS.NS'])

    def test_screener_query_mirrors_the_verified_gainers_and_losers_screens(self):
        gainers = stocks.screener_query('us', 'DESC')
        self.assertEqual((gainers['count'], gainers['sortField'], gainers['sortType']), (12, 'percentchange', 'DESC'))
        self.assertEqual(gainers['query']['operands'][0], {'operator': 'eq', 'operands': ['region', 'us']})
        self.assertIn({'operator': 'gt', 'operands': ['percentchange', 3.0]}, gainers['query']['operands'])
        self.assertIn({'operator': 'gt', 'operands': ['dayvolume', 15000]}, gainers['query']['operands'])
        losers = stocks.screener_query('us', 'ASC')
        self.assertIn({'operator': 'gt', 'operands': ['percentchange', -2.5]}, losers['query']['operands'])
        self.assertIn({'operator': 'gt', 'operands': ['dayvolume', 20000]}, losers['query']['operands'])
        india = stocks.screener_query('in', 'DESC')
        self.assertEqual(india['query']['operands'][0], {'operator': 'eq', 'operands': ['region', 'in']})
        self.assertIn({'operator': 'gte', 'operands': ['intradaymarketcap', 100_000_000_000]}, india['query']['operands'])
        self.assertIn({'operator': 'gte', 'operands': ['intradayprice', 20]}, india['query']['operands'])
        self.assertIn({'operator': 'gt', 'operands': ['dayvolume', 100_000]}, india['query']['operands'])

    def test_a_flaked_whole_universe_response_is_rejected_before_it_can_rank(self):
        # The captured flake shape: boundary-cluster rows at -2.52/-2.49, unsorted, total in thousands.
        flake = [self.quote('MAN', 4.9, -2.52), self.quote('PENG', 3.4, -2.49), self.quote('RKLB', 5.0, -9.95)]
        self.assertFalse(stocks.valid_board(flake, 'us', False, 1916), 'whole-universe total + boundary rows')
        self.assertFalse(stocks.valid_board(flake, 'us', False, 176), 'still the unsorted unfiltered sample')
        # India's bars: cap/price/volume are client-enforced there.
        self.assertFalse(stocks.valid_board([self.quote('SMALL.NS', 25.0, 4.0, cap=5e10)], 'in', True, 40))
        self.assertFalse(stocks.valid_board([self.quote('THIN.NS', 88.0, 4.0, volume=5e4)], 'in', True, 40))
        self.assertFalse(stocks.valid_board([self.quote('CHEAP.NS', 12.0, 4.0)], 'in', True, 40))
        self.assertFalse(stocks.valid_board(self.gainers() + self.losers(), 'in', True, 4))  # cross-side junk
        self.assertFalse(stocks.valid_board([self.quote(f'S{i}', 10.0 + i, 4.05) for i in range(13)],
                                           'in', True, 20))  # more rows than the request
        self.assertFalse(stocks.valid_board({'symbol': 'X'}, 'us', True, 3))  # a missing quotes list
        self.assertFalse(stocks.valid_board([{'symbol': 'X'}], 'us', True, 3))  # fields entirely missing
        # A correct page — including the pre-defined rows whose cap fields are 0 — stays valid.
        self.assertTrue(stocks.valid_board(self.gainers(), 'in', True, 4))
        self.assertTrue(stocks.valid_board(self.losers(), 'in', False, 4))
        self.assertTrue(stocks.valid_board([self.quote('AAPL', 300.0, 4.2, cap=0, volume=0)], 'us', True, 188))
        self.assertTrue(stocks.valid_board([self.quote('MAN', 4.9, -17.07, cap=0, volume=0)], 'us', False, 176))

    def test_us_movers_read_the_crumbs_free_predefined_screens(self):
        sides = []
        with patch('token_tv.stocks.predefined_quotes',
                   side_effect=lambda side: (sides.append(side),
                                             (self.gainers() if side == 'gainers' else self.losers(), 176))[1]), \
                patch('token_tv.stocks._request_post_json',
                      side_effect=AssertionError('the predefined GET answered; no POST may run')):
            rows = stocks.fetch_movers('US', 2)
        self.assertEqual(sides, ['gainers', 'losers'])  # two crumb-free GETs, in order
        self.assertEqual([row['symbol'] for row in rows], ['G1.NS', 'G2.NS', 'L1.NS', 'L2.BO'])

    def test_a_flaked_predefined_screen_falls_back_to_one_custom_post(self):
        def flaked(side):
            return ([self.quote('BROKEN', 12.0, -2.4)] * 2, 1916)  # the unsorted universe sample

        with patch('token_tv.stocks.predefined_quotes', side_effect=flaked), \
                patch('token_tv.stocks.screener_quotes',
                      side_effect=lambda region, sort_type:
                      (self.gainers(), 189) if sort_type == 'DESC' else (self.losers(), 189)):
            rows = stocks.fetch_movers('US', 2)
        self.assertEqual([row['symbol'] for row in rows], ['G1.NS', 'G2.NS', 'L1.NS', 'L2.BO'])

    def test_fetch_movers_returns_the_top_gainers_then_losers_in_board_order(self):
        with patch('token_tv.stocks.screener_quotes',
                   side_effect=lambda region, sort_type:
                   (self.gainers(), 4) if sort_type == 'DESC' else (self.losers(), 2)):
            rows = stocks.fetch_movers('NSE', 2)  # India reads the custom POST (predefined ignores region=in)
            self.assertEqual([row['symbol'] for row in rows], ['G1.NS', 'G2.NS', 'L1.NS', 'L2.BO'])
        with patch('token_tv.stocks.predefined_quotes',
                   side_effect=lambda side: (self.gainers(), 4) if side == 'gainers' else (self.losers(), 2)):
            # A larger count keeps the whole ranked page.
            self.assertEqual(len(stocks.fetch_movers('US', 4)), 5)

    def test_an_india_degraded_page_retries_once_with_a_fresh_crumb_then_raises(self):
        posts = []

        def degraded(region, sort_type):
            posts.append(sort_type)
            return ([self.quote('SMALL.NS', 25.0, 4.0, cap=5e10)], 40)  # a sub-threshold cap: never ranked

        with patch('token_tv.stocks.screener_quotes', side_effect=degraded), \
                patch('token_tv.stocks._fetch_yahoo_session', return_value=('c=1', 'crumb')) as fetch:
            with self.assertRaises(stocks.StockError):
                stocks.fetch_movers('NSE', 2)  # the caller keeps last-good and backs off
        self.assertEqual(fetch.call_count, 1)  # exactly one fresh-crumb fetch; never more
        self.assertEqual(posts.count('DESC'), 2)  # the retry re-ran both sides

    def test_a_qualifying_failure_retries_once_with_a_fresh_crumb_then_succeeds(self):
        posts = []

        def predefined(side):
            raise stocks.StockError('unavailable')  # the predefined screens are down: one POST instead

        def flaky(url, payload, headers=None):
            posts.append(payload['sortType'])
            if len(posts) == 1:
                raise stocks.StockError('http_401')  # a stale crumb fails the first custom POST
            quotes = [self.quote('AAPL', 340.0, 4.07)] if payload['sortType'] == 'DESC' \
                else [self.quote('TSLA', 100.0, -3.0)]
            return {'finance': {'result': [{'quotes': quotes}]}}

        with patch('token_tv.stocks.predefined_quotes', side_effect=predefined), \
                patch('token_tv.stocks._request_post_json', side_effect=flaky), \
                patch('token_tv.stocks._fetch_yahoo_session', side_effect=[('c=1', 'old'), ('c=1', 'new')]) as fetch:
            rows = stocks.fetch_movers('US', 1)
        self.assertEqual([row['symbol'] for row in rows], ['AAPL', 'TSLA'])
        # The crumb fetch itself ran exactly twice: the cold start, then one fresh crumb.
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(posts, ['DESC', 'DESC', 'ASC'])

    def test_non_qualifying_failures_never_refresh_the_crumb(self):
        for code in ('http_429', 'unavailable', 'crumb_unavailable', ''):  # rate limit, outage, no crumb, unknown
            stocks._yahoo_session_state = None
            with patch('token_tv.stocks.predefined_quotes', side_effect=stocks.StockError(code)), \
                    patch('token_tv.stocks._request_post_json', side_effect=stocks.StockError(code)), \
                    patch('token_tv.stocks._fetch_yahoo_session', return_value=('c=1', 'crumb')) as fetch:
                with self.assertRaises(stocks.StockError):
                    stocks.fetch_movers('US', 2)
            self.assertEqual(fetch.call_count, 1, code)  # the cold start only; no fresh-crumb fetch

    def test_two_screener_failures_raise_so_the_runner_keeps_the_last_board(self):
        with patch('token_tv.stocks.predefined_quotes', side_effect=stocks.StockError('unavailable')), \
                patch('token_tv.stocks.screener_quotes', side_effect=stocks.StockError('unavailable')), \
                patch('token_tv.stocks.yahoo_session', return_value=('', 'crumb')):
            with self.assertRaises(stocks.StockError):
                stocks.fetch_movers('US', 2)

    def test_the_predefined_get_carries_the_captured_shape_and_no_crumb(self):
        holder = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def read(self):
                return json.dumps({'finance': {'total': 176, 'result': [{'quotes': [{'symbol': 'MAN'}]}]}}).encode()

        def fake_urlopen(request, timeout=None):
            holder['url'] = request.full_url
            holder['method'] = request.get_method()
            return Response()

        with patch('token_tv.stocks.urlopen', side_effect=fake_urlopen):
            quotes, total = stocks.predefined_quotes('losers')
        self.assertEqual((total, quotes), (176, [{'symbol': 'MAN'}]))
        self.assertEqual(holder['method'], 'GET')
        url = holder['url']
        self.assertIn('scrIds=day_losers', url)
        self.assertNotIn('crumb', url.lower())  # crumb-free
        self.assertIn('region=US', url)

    def test_a_yahoo_cookie_and_crumb_are_cached_for_the_process(self):
        with patch('token_tv.stocks._fetch_yahoo_session', return_value=('c=1', 'crumb')) as fetch:
            self.assertEqual(stocks.yahoo_session(), ('c=1', 'crumb'))  # the cold start fetches exactly once
            self.assertEqual(stocks.yahoo_session(), ('c=1', 'crumb'))  # no second fetch
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(stocks.yahoo_session(refresh=True), ('c=1', 'crumb'))
            self.assertEqual(fetch.call_count, 2)


class StockConfigTests(unittest.TestCase):
    quote = MoversTests.__dict__['quote']

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'stocks.json'

    def tearDown(self):
        self.directory.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_mode_and_movers_count_validate(self):
        self.write({'mode': 'movers', 'movers_count': 2})
        config = stocks.load_stock_config(self.path)
        self.assertEqual((config['mode'], config['movers_count']), ('movers', 2))
        self.write({})
        defaults = stocks.load_stock_config(self.path)
        self.assertEqual((defaults['mode'], defaults['movers_count']), ('watchlist', stocks.MOVERS_COUNT))
        for bad in ({'mode': 'banana'}, {'mode': 2}, {'mode': None}, {'mode': 'Movers'},
                    {'movers_count': 0}, {'movers_count': 5}, {'movers_count': True},
                    {'movers_count': '2'}, {'movers_count': 2.0}, {'movers_count': -1}):
            bad_case = dict({'mode': 'watchlist'}, **bad)
            self.write(bad_case)
            with self.assertRaises(ValueError, msg=bad):
                stocks.load_stock_config(self.path)

    def test_the_movers_row_contract_is_the_watchlist_row_contract(self):
        # combo*.py faces read these rows unchanged: symbol/price/change/percent/previous_close/at.
        row = stocks.screener_rows([self.quote('RELIANCE.NS', 1174.6, 0.68, change=7.9)])[0]
        self.assertEqual(sorted(row), sorted(sample_rows()[0]))
        self.assertEqual(row['symbol'], 'RELIANCE.NS')
        self.assertAlmostEqual(row['change'], 7.9)
        self.assertEqual(stocks.display_symbol(row['symbol']), 'RELIANCE')


class ReloadTests(unittest.TestCase):
    """Mid-run config edits take effect next cycle; a bad edit keeps the last-good config."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'stocks.json'

    def tearDown(self):
        self.directory.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))
        stamp = time.time() + 3600
        os.utime(self.path, (stamp, stamp))  # a distinctive mtime; equal files must not reload
        return stamp

    def test_an_unchanged_file_is_not_reloaded(self):
        mtime = self.write({'symbols': ['AAPL']})
        config = stocks.load_stock_config(self.path)
        config['device_url'] = 'http://192.168.0.50'  # a never-touch key from the live process
        new_config, new_mtime = stocks.reload_stock_config(self.path, config, mtime)
        self.assertIs(new_config, config)  # a same-mtime cycle reuses the live object
        self.assertEqual(new_mtime, mtime)

    def test_a_valid_edit_reloads_its_keys_and_keeps_never_touch_keys(self):
        mtime = self.write({'symbols': ['AAPL'], 'symbols_in': ['TCS.NS'], 'mode': 'watchlist'})
        config = stocks.load_stock_config(self.path)
        config['device_url'], config['state_dir'] = 'http://192.168.0.50', 'kept'
        mtime = self.write({'symbols': ['MSFT', 'NVDA'], 'mode': 'movers', 'movers_count': 1, 'interval': 60})
        config, fresh = stocks.reload_stock_config(str(self.path), config, mtime - 60)
        self.assertEqual(config['symbols'], ['MSFT', 'NVDA'])
        self.assertEqual((config['mode'], config['movers_count'], config['interval']), ('movers', 1, 60))
        self.assertEqual((config['device_url'], config['state_dir']), ('http://192.168.0.50', 'kept'))
        self.assertEqual(fresh, mtime)

    def test_an_invalid_edit_keeps_the_last_good_config_without_crashing(self):
        mtime = self.write({'symbols': ['AAPL']})
        config, _ = stocks.reload_stock_config(str(self.path), stocks.load_stock_config(self.path), None)
        mtime = self.write({'symbols': ['aapl'], 'mode': 'nope'})  # an invalid edit
        last_good, stamp = stocks.reload_stock_config(str(self.path), config, mtime - 60)
        self.assertEqual(last_good['symbols'], ['AAPL'])
        self.assertEqual(stamp, mtime)  # one warning per edit, not one per cycle
        config, stamp2 = stocks.reload_stock_config(str(self.path), config, stamp)
        self.assertIs(config, last_good)


class MoversRunnerTests(unittest.TestCase):
    """In movers mode the per-symbol Finnhub/Yahoo collect loop is skipped entirely."""

    def test_the_movers_runner_publishes_the_board_and_needs_no_credentials(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        frozen = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo('America/New_York')).timestamp()  # mid-session ET
        board = [
            {'symbol': 'G1', 'price': 110.0, 'change': 6.0, 'percent': 6.0, 'previous_close': None, 'at': 1791900000},
            {'symbol': 'G2', 'price': 120.0, 'change': 5.0, 'percent': 5.0, 'previous_close': None, 'at': 1791900000},
            {'symbol': 'L1', 'price': 10.0, 'change': -3.0, 'percent': -3.0, 'previous_close': None, 'at': 1791900000},
            {'symbol': 'L2', 'price': 20.0, 'change': -2.0, 'percent': -2.0, 'previous_close': None, 'at': 1791900000},
        ]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            config = Path(directory) / 'stocks.json'
            config.write_text(json.dumps({'symbols': ['AAPL'], 'mode': 'movers', 'state_dir': str(state)}))
            with patch('token_tv.stocks.finnhub_token', side_effect=AssertionError('no credentials in movers mode')), \
                    patch('token_tv.stocks.fetch_market_status', side_effect=AssertionError('no Finnhub in movers mode')), \
                    patch('token_tv.stocks.time.time', return_value=frozen), \
                    patch('token_tv.stocks.collect', side_effect=AssertionError('the per-symbol loop is skipped')) as collect, \
                    patch('token_tv.stocks.fetch_movers', return_value=board) as movers:
                code = stocks.main(['--once', '--config', str(config)])
            self.assertEqual(code, 0)
            movers.assert_called_once()
            self.assertEqual(movers.call_args[0], ('US', 2))  # the pinned, active market and movers_count
            self.assertEqual(collect.call_count, 0)
            payload = json.loads((state / 'snapshot.json').read_text())
            self.assertEqual(payload['symbols'], board)
            self.assertEqual(payload['source'], 'YAHOO')  # the screener serves every market
            self.assertEqual(payload['market']['is_open'], True)

    def test_a_screener_failure_keeps_the_previous_board_in_the_snapshot(self):
        board = [{'symbol': 'AAPL', 'price': 340.0, 'change': 4.0, 'percent': 4.0,
                  'previous_close': None, 'at': 1791900000}]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            config = Path(directory) / 'stocks.json'
            config.write_text(json.dumps({'symbols': ['AAPL'], 'mode': 'movers', 'state_dir': str(state)}))
            with patch('token_tv.stocks.fetch_movers', side_effect=[board, stocks.StockError('unavailable')]), \
                    patch('token_tv.stocks.time.sleep', side_effect=[None, KeyboardInterrupt]):
                self.assertEqual(stocks.main(['--config', str(config)]), 0)  # cycle 2 failed; the loop then ended
            payload = json.loads((state / 'snapshot.json').read_text())  # last-good board, never an empty one
            self.assertEqual(payload['symbols'], board)

    def test_a_watchlist_hot_reload_without_a_finnhub_key_warns_once_and_shows_dashes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            config = Path(directory) / 'stocks.json'
            config.write_text(json.dumps({'symbols': ['AAPL'], 'mode': 'movers', 'state_dir': str(state)}))
            board = [{'symbol': 'AAPL', 'price': 340.0, 'change': 4.0, 'percent': 4.0,
                      'previous_close': None, 'at': 1791900000}]

            edited = []

            def hot_edit_then_stop(_seconds):
                # Mock's callable side_effect uses the function's return value, so the first
                # sleep performs the edit itself and the second ends the run.
                if not edited:
                    edited.append(True)
                    config.write_text(json.dumps({'symbols': ['AAPL', 'MSFT'], 'mode': 'watchlist',
                                                  'state_dir': str(state)}))
                    import os as _os
                    stamp = time.time() + 3600
                    _os.utime(config, (stamp, stamp))
                    return None
                raise KeyboardInterrupt

            with patch('token_tv.stocks.finnhub_token', side_effect=stocks.StockError('auth_required')), \
                    patch('token_tv.stocks.fetch_movers',
                          side_effect=[board, AssertionError('no movers after the reload')]) as movers, \
                    patch('token_tv.stocks.time.sleep', side_effect=hot_edit_then_stop):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(stocks.main(['--config', str(config)]), 0)
            self.assertEqual(movers.call_count, 1)  # cycle 1 only; cycle 2 ran the watchlist
            self.assertIn('No Finnhub API key', err.getvalue())
            payload = json.loads((state / 'snapshot.json').read_text())
            self.assertEqual([row['symbol'] for row in payload['symbols']], ['AAPL', 'MSFT'])
            for row in payload['symbols']:
                self.assertIsNone(row['price'])  # dashes, never a fabricated zero
                self.assertEqual(stocks.change_text(row['change'], row['percent']), '--')


if __name__ == '__main__':
    unittest.main()
