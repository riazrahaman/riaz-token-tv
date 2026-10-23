import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from token_tv.widgets import widgets_payload
from token_tv.usage import normalize_chatgpt
from token_tv.live import handler
from token_tv.state import UsageStore


def account_row(provider, alias, status="ok", windows=()):
    return {"provider": provider, "alias": alias, "status": status, "windows": list(windows)}


class WidgetsPayloadTest(unittest.TestCase):
    def test_normalize_chatgpt_windows(self):
        data = {
            "session": {"label": "3H", "utilization": 25.0, "resets_at": 1791895000, "duration_minutes": 180},
            "weekly": {"label": "WEEK", "utilization": 50.0, "resets_at": 1792300000, "duration_minutes": 10080}
        }
        windows = normalize_chatgpt(data)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["label"], "3H")
        self.assertEqual(windows[0]["used_percent"], 25.0)
        self.assertEqual(windows[1]["label"], "WEEK")
        self.assertEqual(windows[1]["used_percent"], 50.0)

    def test_normalize_chatgpt_message_limits(self):
        data = {
            "used_messages": 20,
            "max_messages": 40,
            "resets_at": 1791895000
        }
        windows = normalize_chatgpt(data)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["label"], "3H")
        self.assertEqual(windows[0]["used_percent"], 50.0)

    def test_widgets_payload_all_four_providers(self):
        now = 1000000.0
        snapshot = {
            "updated_at": int(now),
            "accounts": {
                "claude_a": account_row("claude", "CLAUDE A", windows=[
                    {"label": "5H", "used_percent": 41.0, "resets_at": now + 2 * 3600},
                    {"label": "WEEK", "used_percent": 86.0, "resets_at": now + 3 * 86400}
                ]),
                "chatgpt_a": account_row("chatgpt", "CHATGPT A", windows=[
                    {"label": "3H", "used_percent": 25.0, "resets_at": now + 1.5 * 3600},
                    {"label": "WEEK", "used_percent": 50.0, "resets_at": now + 5 * 86400}
                ]),
                "antigravity_a": account_row("antigravity", "ANTIGRAVITY A", windows=[
                    {"label": "5H", "used_percent": 31.0, "resets_at": now + 3 * 3600},
                    {"label": "WEEK", "used_percent": 74.0, "resets_at": now + 2 * 86400}
                ]),
                "ollama_a": account_row("ollama", "OLLAMA A", windows=[
                    {"label": "$102", "used_percent": 34.0, "resets_at": now + 14 * 86400}
                ])
            }
        }
        payload = widgets_payload(snapshot, now=now)
        self.assertEqual(payload["updated_at"], int(now))
        self.assertEqual(len(payload["providers"]), 4)

        # Check Claude
        claude = next(p for p in payload["providers"] if p["provider"] == "claude")
        self.assertEqual(claude["name"], "Claude")
        self.assertEqual(claude["alias"], "CLAUDE A")
        self.assertIsNotNone(claude["session"])
        self.assertEqual(claude["session"]["label"], "5H")
        self.assertEqual(claude["session"]["used_percent"], 41.0)
        self.assertIsNotNone(claude["weekly"])
        self.assertEqual(claude["weekly"]["label"], "WK")
        self.assertEqual(claude["weekly"]["used_percent"], 86.0)

        # Check ChatGPT
        chatgpt = next(p for p in payload["providers"] if p["provider"] == "chatgpt")
        self.assertEqual(chatgpt["name"], "ChatGPT")
        self.assertEqual(chatgpt["alias"], "CHATGPT A")
        self.assertEqual(chatgpt["session"]["label"], "3H")
        self.assertEqual(chatgpt["session"]["used_percent"], 25.0)
        self.assertEqual(chatgpt["weekly"]["label"], "WK")
        self.assertEqual(chatgpt["weekly"]["used_percent"], 50.0)

        # Check summary contains short indicators
        self.assertIn("CLAUDE A", payload["summary"])
        self.assertIn("CHATGPT A", payload["summary"])


class WidgetsEndpointTest(unittest.TestCase):
    def test_widgets_json_endpoint(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(UsageStore([])))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/widgets.json") as response:
                self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
                data = json.load(response)
                self.assertIn("providers", data)
                self.assertIn("updated_at", data)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
