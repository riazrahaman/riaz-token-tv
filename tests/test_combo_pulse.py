import io
import time
import unittest
from PIL import Image

from token_tv.combo_pulse import render_combo_pulse
from token_tv.device import LIMITS


class ComboPulseTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "updated_at": int(time.time()),
            "accounts": {
                "claude_a": {
                    "key": "claude_a",
                    "alias": "CLAUDE A",
                    "provider": "claude",
                    "status": "ok",
                    "windows": [{"label": "5H", "used_percent": 45.0, "resets_at": int(time.time()) + 3600}],
                },
                "ollama_a": {
                    "key": "ollama_a",
                    "alias": "OLLAMA A",
                    "provider": "ollama",
                    "status": "ok",
                    "windows": [{"label": "$74", "used_percent": 24.0, "resets_at": int(time.time()) + 7200}],
                },
                "antigravity_a": {
                    "key": "antigravity_a",
                    "alias": "ANTIGRAVITY A",
                    "provider": "antigravity",
                    "status": "ok",
                    "windows": [{"label": "WEEK", "used_percent": 82.0, "resets_at": int(time.time()) + 14400}],
                },
            },
            "stocks": {
                "updated_at": int(time.time()),
                "market": {"is_open": True, "session": "regular"},
                "symbols": [
                    {"symbol": "AAPL", "price": 345.11, "change": 3.84, "percent": 1.13},
                    {"symbol": "MSFT", "price": 538.80, "change": -4.65, "percent": -0.86},
                    {"symbol": "NVDA", "price": 223.97, "change": -4.55, "percent": -1.99},
                    {"symbol": "TSLA", "price": 368.06, "change": -7.17, "percent": -1.91},
                ],
            },
        }

    def test_renders_animated_gif_within_size_limit(self):
        gif_bytes = render_combo_pulse(self.snapshot)
        self.assertTrue(gif_bytes.startswith(b"GIF89a"), "Payload must be a GIF89a image")
        self.assertLess(len(gif_bytes), LIMITS["tokentv.gif"][0], "Must be under 400 KB limit")

        image = Image.open(io.BytesIO(gif_bytes))
        self.assertEqual(image.size, (240, 240))
        self.assertTrue(getattr(image, "is_animated", False))
        self.assertGreaterEqual(image.n_frames, 10)

    def test_renders_fallback_when_market_closed(self):
        snapshot_closed = dict(self.snapshot, stocks={"updated_at": int(time.time()), "market": {"is_open": False}})
        gif_bytes = render_combo_pulse(snapshot_closed)
        self.assertTrue(gif_bytes.startswith(b"GIF89a"))
        image = Image.open(io.BytesIO(gif_bytes))
        self.assertEqual(image.size, (240, 240))
        self.assertTrue(getattr(image, "is_animated", False))

    def test_supports_scaled_output(self):
        gif_bytes = render_combo_pulse(self.snapshot, scale=2)
        image = Image.open(io.BytesIO(gif_bytes))
        self.assertEqual(image.size, (480, 480))
