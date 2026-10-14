import contextlib
import io
import json
import sys
import tempfile
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

    def capture(self):
        self.captures += 1
        return {'themes': [{'id': 0, 'enabled': True}, {'id': 2, 'enabled': False}], 'theme_interval': 10,
                'files': [{'name': 'original.jpg', 'enabled': True}], 'photo_interval': 10}

    def check_backup(self, original):
        return None

    def upload(self, name, image):
        self.uploads.append((name, image))
        return {'file': name, 'status': 200, 'bytes': len(image)}

    def activate(self, original, name='tokentv.jpg'):
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
        self.assertEqual(len(device.uploads), 1)
        self.assertEqual(device.activations, 1)
        # Same bytes again: nothing is rewritten to the clock.
        original, active, last, uploaded = stocks.publish(device, image, self.state, backup,
                                                          original, active, last, now=101)
        self.assertFalse(uploaded)
        self.assertEqual(len(device.uploads), 1)
        self.assertEqual(device.activations, 1)
        # A changed frame uploads, but does not re-activate the same file.
        other = stocks.render_watchlist(sample_rows(), sample_meta(is_open=False, session='closed'))
        original, active, last, uploaded = stocks.publish(device, other, self.state, backup,
                                                          original, active, last, now=102)
        self.assertTrue(uploaded)
        self.assertEqual(len(device.uploads), 2)
        self.assertEqual(device.activations, 1)
        # The backup read, plus one album read on the activating cycle to notice a lost file.
        self.assertEqual(device.captures, 2)
        self.assertTrue(backup.is_file())
        receipt = json.loads((self.state / 'display-receipt.json').read_text())
        self.assertEqual(receipt['at'], 102)
        self.assertEqual(receipt['uploads'][0]['sha256'], __import__('hashlib').sha256(other).hexdigest())

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


if __name__ == '__main__':
    unittest.main()
