from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from token_tv.countdown import (
    TARGET_DATE,
    PROVERBS,
    days_until_target,
    hourly_proverb,
    render_countdown_frame,
    render_countdown_gif,
    publish_countdown,
    main as countdown_main,
)


class TestCountdown(unittest.TestCase):
    def test_days_until_target(self):
        self.assertEqual(days_until_target(date(2026, 10, 23)), 466)
        self.assertEqual(days_until_target(date(2028, 2, 1)), 0)
        self.assertEqual(days_until_target(date(2028, 2, 2)), 0)
        self.assertEqual(days_until_target(date(2027, 2, 1)), 365)

    def test_hourly_proverb_rotation(self):
        dt1 = datetime(2026, 10, 23, 10, 0, 0)
        dt2 = datetime(2026, 10, 23, 11, 0, 0)
        p1 = hourly_proverb(dt1)
        p2 = hourly_proverb(dt2)

        self.assertIsInstance(p1, tuple)
        self.assertEqual(len(p1), 2)
        # Consecutive hours should advance or cycle cleanly
        self.assertNotEqual(p1, p2)
        # Deterministic
        self.assertEqual(p1, hourly_proverb(dt1))

    def test_render_countdown_frame_shapes(self):
        dt = datetime(2026, 10, 23, 19, 30, 0)
        # State: countdown
        im_cd = render_countdown_frame(now=dt, colon_on=True, state="countdown")
        self.assertEqual(im_cd.size, (240, 240))
        self.assertEqual(im_cd.mode, "RGB")

        # State: proverb
        im_pr = render_countdown_frame(now=dt, colon_on=False, state="proverb")
        self.assertEqual(im_pr.size, (240, 240))
        self.assertEqual(im_pr.mode, "RGB")

        # Check that colon off differs from colon on
        im_cd_off = render_countdown_frame(now=dt, colon_on=False, state="countdown")
        self.assertNotEqual(im_cd.tobytes(), im_cd_off.tobytes())

    def test_render_countdown_gif(self):
        dt = datetime(2026, 10, 23, 19, 30, 0)
        gif_bytes = render_countdown_gif(now=dt, countdown_secs=2, proverb_secs=1)
        self.assertIsInstance(gif_bytes, bytes)
        self.assertTrue(gif_bytes.startswith(b"GIF89a") or gif_bytes.startswith(b"GIF87a"))
        self.assertLess(len(gif_bytes), 400000)

    @patch("token_tv.countdown.PhotoDisplay")
    def test_publish_countdown(self, mock_display_cls):
        mock_client = MagicMock()
        mock_display_cls.return_value = mock_client
        mock_client.upload.return_value = {"file": "tokentv.gif", "status": 200, "bytes": 1234}

        res = publish_countdown(device_url="http://192.168.1.157", now=datetime(2026, 10, 23, 12, 0))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["days_remaining"], 466)
        self.assertEqual(res["device_url"], "http://192.168.1.157")
        mock_client.upload.assert_called_once()
        mock_client.activate.assert_called_once()

    def test_cli_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "test.gif"
            rc = countdown_main(["--out", str(out_file)])
            self.assertEqual(rc, 0)
            self.assertTrue(out_file.is_file())
            self.assertGreater(out_file.stat().st_size, 1000)


class TestCountdownDesign(unittest.TestCase):
    """The black-face guarantees: true black, stable digit cells, and text that fits."""

    def test_background_is_true_black_through_the_gif_encoder(self):
        from io import BytesIO
        from PIL import Image

        gif = render_countdown_gif(now=datetime(2026, 10, 23, 11, 11), countdown_secs=2, proverb_secs=1)
        im = Image.open(BytesIO(gif))
        for index in range(getattr(im, "n_frames", 1)):
            im.seek(index)
            rgb = im.convert("RGB")
            for point in ((0, 0), (239, 0), (0, 239), (239, 239)):
                self.assertEqual(rgb.getpixel(point), (0, 0, 0), f"frame {index} at {point}")

    def test_frames_stay_under_the_palette_limit(self):
        frame = render_countdown_frame(now=datetime(2026, 10, 23, 11, 11))
        colours = frame.getcolors(maxcolors=240 * 240)
        self.assertIsNotNone(colours)
        self.assertLessEqual(len(colours), 256)

    def test_time_digit_cells_do_not_shift_with_digit_width(self):
        """Proportional digits must not move the layout: 11:11 and 08:08 share ink columns."""
        from token_tv.countdown import _draw_time
        from PIL import Image, ImageDraw

        colon_columns, blocks = [], []
        for hour, minute in ((11, 11), (8, 8), (23, 58), (10, 0)):
            lit, dark = (Image.new("RGB", (240, 240), "#000000") for _ in range(2))
            _draw_time(ImageDraw.Draw(lit), datetime(2026, 10, 23, hour, minute), True)
            _draw_time(ImageDraw.Draw(dark), datetime(2026, 10, 23, hour, minute), False)
            from PIL import ImageChops
            colon = ImageChops.difference(lit, dark).getbbox()  # only the colon changes
            colon_columns.append((colon[0], colon[2]))
            blocks.append(lit.getbbox())

        # The colon cell — and so every digit cell — sits in the same column every minute.
        self.assertEqual(len(set(colon_columns)), 1, colon_columns)
        for box in blocks:
            self.assertAlmostEqual((box[0] + box[2]) / 2, 120, delta=4)

    def test_every_proverb_fits_the_hero_band_at_a_readable_size(self):
        from token_tv.countdown import _font, _wrap, HERO_TOP, HERO_BOTTOM
        from PIL import Image, ImageDraw

        draw = ImageDraw.Draw(Image.new("RGB", (240, 240)))
        for proverb in PROVERBS:
            words = " ".join(proverb).split()
            for size in (22, 21, 20, 19, 18, 17, 16, 15, 14):
                font = _font(size, weight=600)
                lines = _wrap(draw, words, font, 212)
                leading = round(size * 1.34)
                fits = (len(lines) <= 4
                        and len(lines) * leading <= HERO_BOTTOM - HERO_TOP
                        and max(draw.textlength(line, font=font) for line in lines) <= 212)
                if fits:
                    break
            self.assertTrue(fits, f"never fits: {proverb}")
            self.assertGreaterEqual(size, 18, f"too small to read across a desk: {proverb}")

    def test_countdown_numeral_stays_inside_the_screen(self):
        for days in (0, 7, 99, 466, 1000):
            with patch("token_tv.countdown.days_until_target", return_value=days):
                frame = render_countdown_frame(now=datetime(2026, 10, 23, 11, 11), state="countdown")
            box = frame.getbbox()
            self.assertGreaterEqual(box[0], 2)
            self.assertLessEqual(box[2], 238)


if __name__ == "__main__":
    unittest.main()
