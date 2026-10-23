"""Offline tests for the calendar feeder, its line renderer and the two config allowlists."""
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from token_tv import calendar as cal
from token_tv import display, sources, stocks


class FakeTimeZone:
    """Freeze the host timezone for the block, then put it back exactly as it was."""

    def __init__(self, zone):
        self.zone = zone

    def __enter__(self):
        self.previous = os.environ.get('TZ')
        os.environ['TZ'] = self.zone
        time.tzset()

    def __exit__(self, *error):
        if self.previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.previous
        time.tzset()


OUTPUT = '2026-10-29T16:00:00\tAbu Dhabi Gaming Connect\n2026-11-02T09:30:00\tWeekly Sync\n'


def event(now, title='Abu Dhabi Gaming Connect', offset=8040):
    """A calendar snapshot as the feeder writes it, with the appointment `offset` seconds out."""
    start = datetime.fromtimestamp(now + offset).astimezone().isoformat(timespec='seconds')
    return {'updated_at': int(now), 'title': title, 'start_iso': start}


def fake_run(output, returncode=0):
    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess('osascript', returncode, stdout=output, stderr='')
    return runner


class QueryTests(unittest.TestCase):
    def test_the_query_is_scoped_and_never_an_unbounded_whose(self):
        script = cal.build_script()
        self.assertIn('tell calendar "%s"' % cal.CALENDAR_NAME, script)  # the proven fast shape
        self.assertIn('allday event is false', script)  # all-day blocks never make the line
        self.assertIn('start date > now and start date < horizon', script)  # a bounded window
        self.assertNotIn('every calendar', script)  # never the >120 s all-calendars query

    def test_the_parser_keeps_the_soonest_future_timed_event(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc).timestamp()
        with FakeTimeZone('Asia/Dubai'):
            found = cal.parse_events(OUTPUT, now)
            # AppleScript answers wall-clock stamps on this host, so a naive 16:00 means
            # 16:00 in the frozen host zone (12:00 UTC), not UTC.
            expected = datetime(2026, 10, 29, 16, 0).timestamp()
        self.assertEqual(found['title'], 'Abu Dhabi Gaming Connect')
        self.assertEqual(found['start_epoch'], expected)
        start = datetime.fromisoformat(found['start_iso'])
        self.assertEqual(start.timestamp(), expected)

    def test_the_parser_skips_past_events_and_junk_rows(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc).timestamp()
        text = ('2026-10-17T08:00:00\tAlready started\n'
                '2026-10-16T08:00:00\tYesterday\n'
                'junk without a tab\n'
                '\tNo title\n'
                '31-02-2026T99:99:99\tA broken date\n'
                '2026-10-18T08:00:00\tTomorrow\n')
        with FakeTimeZone('Asia/Dubai'):
            found = cal.parse_events(text, now)
        self.assertEqual(found['title'], 'Tomorrow')

    def test_the_parser_answers_none_for_an_empty_or_garbled_answer(self):
        for answer in ('', None, 'one line without tabs\n'):
            self.assertIsNone(cal.parse_events(answer), answer)

    def test_query_next_degrades_to_none_on_any_failure(self):
        for side_effect in (subprocess.TimeoutExpired('osascript', cal.QUERY_TIMEOUT),
                            OSError('osascript missing')):
            with patch('token_tv.calendar.subprocess.run', side_effect=side_effect):
                self.assertIsNone(cal.query_next())
        with patch('token_tv.calendar.subprocess.run',
                   side_effect=fake_run('error, permission refused', returncode=1)):
            self.assertIsNone(cal.query_next())
        with patch('token_tv.calendar.subprocess.run', side_effect=fake_run('')):
            self.assertIsNone(cal.query_next())

    def test_query_next_parses_a_good_answer(self):
        now = time.time()
        with patch('token_tv.calendar.subprocess.run', side_effect=fake_run(OUTPUT)), \
                patch('token_tv.calendar.time.time', return_value=now):
            found = cal.query_next()
        self.assertEqual(found['title'], 'Abu Dhabi Gaming Connect')


class FeederTests(unittest.TestCase):
    def _config(self, directory):
        path = Path(directory) / 'stocks.json'
        path.write_text(json.dumps({'symbols': ['AAPL']}))
        return path

    def test_the_feeder_writes_the_snapshot_and_leaves_no_temporaries(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'calendar-snapshot.json'
            with patch('token_tv.calendar.query_next',
                       return_value={'title': 'Abu Dhabi Gaming Connect', 'start_epoch': now + 600,
                                     'start_iso': datetime.fromtimestamp(now + 600).astimezone()
                                     .isoformat(timespec='seconds')}), \
                    patch('token_tv.calendar.time.time', return_value=now):
                self.assertEqual(cal.main(['--config', str(self._config(directory)), '--once',
                                           '--state-dir', str(directory)]), 0)
            written = json.loads(path.read_text())
            self.assertEqual(written['title'], 'Abu Dhabi Gaming Connect')
            self.assertEqual(written['updated_at'], int(now))
            self.assertTrue(datetime.fromisoformat(written['start_iso']).tzinfo is not None)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ['calendar-snapshot.json', 'stocks.json'])  # atomic: no .tmp survives
            self.assertEqual(oct(os.stat(path).st_mode)[-3:], '600')

    def test_a_failed_query_keeps_the_last_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'calendar-snapshot.json'
            cal.write_json(path, {'updated_at': 5, 'title': 'Keep me', 'start_iso': 'x'})
            with patch('token_tv.calendar.query_next', return_value=None), \
                    patch('token_tv.calendar.time.time', return_value=100.0):
                self.assertEqual(cal.main(['--config', str(self._config(directory)), '--once',
                                           '--state-dir', str(directory)]), 0)
            self.assertEqual(json.loads(path.read_text())['title'], 'Keep me')  # degraded, not deleted
            self.assertEqual(json.loads(path.read_text())['updated_at'], 5)
            self.assertTrue(all(p.suffix != '.tmp' for p in Path(directory).iterdir()))

    def test_an_explicit_calendar_snapshot_path_wins_and_the_default_is_the_state_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self._config(directory)
            config.write_text(json.dumps({'state_dir': directory}))
            args = type('Args', (), {'state_dir': None, 'config': str(config)})()
            self.assertEqual(cal.snapshot_path(args, json.loads(config.read_text())),
                             Path(directory) / 'calendar-snapshot.json')
            named = Path(directory) / 'elsewhere.json'
            config.write_text(json.dumps({'calendar_snapshot': str(named)}))
            self.assertEqual(cal.snapshot_path(args, json.loads(config.read_text())), named)


class ConfigAllowlistTests(unittest.TestCase):
    """The new key must pass both allowlists, or one of the two loaders crash-loops on it."""

    def test_sources_load_config_accepts_calendar_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'accounts': [{'key': 'a', 'alias': 'A', 'provider': 'claude',
                                                      'email': 'owner@example.com'}],
                                        'calendar_snapshot': '~/state/calendar-snapshot.json'}))
            self.assertEqual(sources.load_config(path)['calendar_snapshot'],
                             '~/state/calendar-snapshot.json')

    def test_stocks_load_stock_config_accepts_calendar_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stocks.json'
            path.write_text(json.dumps({'symbols': ['AAPL'],
                                        'calendar_snapshot': 'calendar-snapshot.json'}))
            self.assertEqual(stocks.load_stock_config(path)['calendar_snapshot'],
                             'calendar-snapshot.json')


class LineRendererTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0

    def test_the_line_pair_is_the_title_and_a_quantised_countdown(self):
        self.assertEqual(cal.calendar_parts({'calendar': event(self.now)}, now=self.now),
                         ('Abu Dhabi Gaming Connect', 'in 2h14m'))

    def test_no_calendar_key_or_a_junky_one_draws_nothing(self):
        start_iso = event(self.now)['start_iso']
        for snapshot in ({}, {'calendar': None}, {'calendar': {}}, {'calendar': 'text'},
                         {'calendar': {'title': '', 'start_iso': start_iso}},
                         {'calendar': {'title': 'Event', 'start_iso': 'not-a-date'}},
                         {'calendar': {'title': 5, 'start_iso': start_iso}}):
            self.assertIsNone(cal.calendar_parts(snapshot, now=self.now), snapshot)

    def test_a_past_or_started_event_is_no_next_event(self):
        for offset in (0, -3600, -86400):
            snapshot = {'calendar': event(self.now, offset=offset)}
            self.assertIsNone(cal.calendar_parts(snapshot, now=self.now + 1), offset)

    def test_a_stale_snapshot_keeps_its_line_but_ages_it_to_OLD(self):
        snapshot = {'calendar': dict(event(self.now, offset=600), updated_at=self.now - 601)}
        self.assertEqual(cal.calendar_parts(snapshot, now=self.now),
                         ('Abu Dhabi Gaming Connect', 'in 10m OLD'))
        fresh = {'calendar': dict(event(self.now), updated_at=self.now - 5)}
        self.assertEqual(cal.calendar_parts(fresh, now=self.now)[1], 'in 2h14m')
        # A missing or non-numeric stamp is a line with no proof of age: OLD stands.
        for updated_at in (None, 'yesterday', True):
            blind = {'calendar': {'title': 'Event', 'start_iso': event(self.now)['start_iso'],
                                  'updated_at': updated_at}}
            self.assertTrue(cal.calendar_parts(blind, now=self.now)[1].endswith(' OLD'), updated_at)

    def test_countdown_buckets_move_on_edges_not_every_minute(self):
        expectations = ((59, 'in 1m'), (60, 'in 1m'), (8040, 'in 2h14m'), (17_999, 'in 5h00m'),
                        (36_000, 'in 10h'), (86_280, 'in 24h'), (86_459, 'in 1d'),
                        (3 * 86_400, 'in 3d'), (13 * 86_400 + 59, 'in 13d'),
                        (14 * 86_400, 'in 2w'), (21 * 86_400 + 59, 'in 3w'))
        for remaining, text in expectations:
            self.assertEqual(cal.countdown_text(remaining), text, remaining)

    def test_the_title_truncates_before_it_touches_the_countdown(self):
        fitted = cal.fit_title('ABU DHABI GAMING CONNECT', 'in 2h14m')
        self.assertEqual(fitted, 'ABU DHABI GAMING CONNECT')  # the real title fits whole
        self.assertLessEqual(6 * len(fitted) - 1, cal.SIZE - 12 - (len('in 2h14m') * 6 - 1) - 6)
        # With OLD riding along, the title yields exactly the remaining room (never overlapping
        # the right-aligned countdown's one-glyph gap).
        fitted = cal.fit_title('A' * 80, 'in 2h14m OLD')
        room = cal.SIZE - 12 - (len('in 2h14m OLD') * 6 - 1) - 6
        self.assertLessEqual(6 * len(fitted) - 1, room)
        self.assertGreater(6 * len(fitted) - 1 + 6, room)  # not one glyph shy for no reason
        self.assertEqual(cal.fit_title('X', 'A' * 80), '')  # no room: countdown only, title gone


class DigestTests(unittest.TestCase):
    def test_a_new_event_is_a_changed_reading_but_a_refetch_is_not(self):
        now = time.time()
        base = {'accounts': {}, 'calendar': event(now)}
        self.assertNotEqual(display.data_digest(base), display.data_digest({'accounts': {}}))
        # A new title or appointment must reach the clock even while the write budget is spent,
        # but a plain refetch of the same event must not.
        refetched = {'accounts': {}, 'calendar': dict(event(now), updated_at=now + 10)}
        self.assertEqual(display.data_digest(base), display.data_digest(refetched))
        moved = {'accounts': {}, 'calendar': event(now, title='Other Event')}
        self.assertNotEqual(display.data_digest(base), display.data_digest(moved))
        self.assertNotEqual(display.data_digest(base),
                            display.data_digest({'accounts': {}, 'calendar': event(now, offset=900)}))


class TimezoneTests(unittest.TestCase):
    """The operator reads the wall the clock hangs on, not a feed's zone."""

    now = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc).timestamp()
    # NSE's Monday 09:15 IST bell — the shape that showed '09:15' on a UAE wall yesterday.
    nse_bell = datetime(2026, 10, 19, 3, 45, tzinfo=timezone.utc).timestamp()

    def test_the_reopen_renders_in_the_host_timezone_with_the_exchange_label(self):
        with FakeTimeZone('Asia/Dubai'):
            self.assertEqual(stocks.reopen_text(self.nse_bell, 'NSE', self.now), ('MON', '07:45', 'NSE'))
        # A 16:00 ET bell lands on Tuesday midnight on the host's wall — its own weekday there.
        us_bell = datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc).timestamp()
        with FakeTimeZone('Asia/Dubai'):
            self.assertEqual(stocks.reopen_text(us_bell, 'US', self.now), ('TUE', '00:00', 'US'))

    def test_an_unknown_exchange_or_a_past_stamp_buys_no_open(self):
        for exchange in ('', None, 'LSE', '  '):
            self.assertIsNone(stocks.reopen_text(self.nse_bell, exchange, self.now), exchange)
        self.assertIsNone(stocks.reopen_text(self.now - 1, 'NSE', self.now))
        self.assertIsNone(stocks.reopen_text(True, 'NSE', self.now))

    def test_market_time_renders_host_local_when_no_zone_is_named(self):
        with FakeTimeZone('Asia/Dubai'):
            self.assertEqual(stocks.market_time(self.nse_bell, tz=None), '07:45')
            self.assertEqual(stocks.market_time(self.now, tz=None), '13:00')

    def test_the_closed_face_footer_is_host_local(self):
        from token_tv.combo_stocks import render_combo_stocks_closed
        scheduled = {'accounts': {}, 'stocks': {
            'updated_at': int(self.nse_bell) - 7200,
            'market': {'is_open': False, 'session': 'closed', 'exchange': 'US',
                       'next_open_at': int(self.nse_bell), 'next_open_exchange': 'NSE'}}}
        with patch('time.time', return_value=self.now):
            with FakeTimeZone('Asia/Dubai'):
                here = render_combo_stocks_closed(scheduled)
            with FakeTimeZone('UTC'):
                elsewhere = render_combo_stocks_closed(scheduled)
        footer = (150, 222, 236, 240)  # the LAST BOARD stamp's band
        self.assertNotEqual(here.crop(footer).tobytes(), elsewhere.crop(footer).tobytes())
        self.assertEqual(Image.open(io.BytesIO(display.encode_jpeg(here)[0])).size, (240, 240))


if __name__ == '__main__':
    unittest.main()
