import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from token_tv.display import watch_payload
from token_tv.live import handler
from token_tv.state import UsageStore


def row(provider, alias, status='ok', windows=()):
    return {'provider': provider, 'alias': alias, 'status': status, 'windows': list(windows)}


class WatchPayloadTest(unittest.TestCase):
    def test_fresh_old_and_missing_readings_match_the_clock(self):
        now = time.time()
        data = watch_payload({'updated_at': 1000, 'accounts': {
            'claude_a': row('claude', 'CLAUDE A', windows=[
                {'label': '5H', 'used_percent': 10, 'resets_at': now + 3600},
                {'label': 'WEEK', 'used_percent': 44.4, 'resets_at': now + 26 * 3600 + 30}]),
            'codex_a': row('codex', 'CODEX A', 'stale', [{'label': '5H', 'used_percent': 12, 'resets_at': now + 3600}]),
            'antigravity_a': row('antigravity', 'ANTIGRAVITY A', 'quota_unavailable'),
        }})
        claude, codex, antigravity = (data['accounts'][key] for key in ('claude_a', 'codex_a', 'antigravity_a'))
        # The tightest window is the one shown, as on the clock.
        self.assertEqual(claude, {'label': 'CLAUDE A', 'used_percent': 44, 'used': 0.44, 'window': 'WK',
                                  'resets_in': '1d 2h', 'resets_at': round(now + 26 * 3600 + 30),
                                  'text': '44% WK', 'state': 'ok'})
        self.assertEqual((codex['state'], codex['text']), ('old', '12% 5H OLD'))
        # Missing stays missing: never 0%.
        self.assertEqual((antigravity['used_percent'], antigravity['used'], antigravity['window'], antigravity['state'], antigravity['text'],
                          antigravity['resets_in']), (None, None, None, 'missing', 'NO DATA', '--'))
        self.assertEqual(data['summary'], 'CLAUDE A 44% WK · CODEX A 12% 5H OLD · ANTIGRAVITY A NO DATA')
        self.assertEqual(data['updated_at'], 1000)

    def test_keys_follow_clock_labels_not_config_keys(self):
        accounts = {'claude_work': row('claude', 'CLAUDE A'), 'claude_friend': row('claude', 'CLAUDE B'),
                    'claude_x': row('claude', 'Home'), 'claude_y': row('claude', 'Lab')}
        data = watch_payload({'updated_at': 0, 'accounts': accounts})
        self.assertEqual(list(data['accounts']), ['claude_a', 'claude_b', 'claude_a_2', 'claude_a_3'])
        self.assertNotIn('friend', json.dumps(data))

    def test_no_accounts_says_so(self):
        self.assertEqual(watch_payload({'updated_at': 0, 'accounts': {}}),
                         {'updated_at': 0, 'summary': 'NO ACCOUNTS', 'accounts': {}})


class WatchEndpointTest(unittest.TestCase):
    def test_watch_json_is_served_as_json(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(UsageStore([])))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/watch.json') as response:
                self.assertTrue(response.headers['Content-Type'].startswith('application/json'))
                self.assertEqual(json.load(response)['summary'], 'NO ACCOUNTS')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
