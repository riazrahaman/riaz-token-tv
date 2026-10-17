import email
import email.policy
import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from token_tv import live
from token_tv.device import (JPEG_SLOTS, STOCKS_FILES, BackupMismatch, PhotoDisplay, UltraDisplay,
                             open_display)


class DisplayTests(unittest.TestCase):
    def test_upload_contract_preserves_existing_files_and_restores_theme(self):
        calls = []
        themes = {"interval": 10, "themes": [{"id": 0, "enabled": True}, {"id": 2, "enabled": False}]}
        photos = {"interval": 10, "files": [{"name": "original.jpg", "size": 10, "enabled": True}, {"name": "space man.gif", "size": 20, "enabled": True}], "total": 3000000, "used": 1000000}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_GET(self):
                calls.append(("GET", self.path, b""))
                url = urlparse(self.path)
                if url.path == "/photo/toggle":
                    query = parse_qs(url.query)
                    for photo in photos["files"]:
                        if photo["name"] == query["name"][0]:
                            photo["enabled"] = query["state"][0] == "1"
                if url.path == "/theme/toggle":
                    query = parse_qs(url.query)
                    identifier, enabled = int(query["id"][0]), query["state"][0] == "1"
                    proposed = [dict(t, enabled=enabled) if t["id"] == identifier else t for t in themes["themes"]]
                    if not any(t["enabled"] for t in proposed):
                        self.send_response(403)
                        self.end_headers()
                        return
                    themes["themes"] = proposed
                data = themes if self.path == "/theme/list" else photos if self.path == "/photo/list" else {"ok": True}
                body = json.dumps(data).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(("POST", self.path, body))
                self.send_response(200)
                self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            display = PhotoDisplay(f"http://127.0.0.1:{server.server_port}")
            original = display.capture()
            photos["files"].extend([{"name": name, "enabled": True} for name in ("tokentv-c.jpg", "tokentv-m.jpg", "tokentv.jpg")])
            display.upload("tokentv.jpg", b"EXACT-JPEG")
            display.activate(original, wanted=JPEG_SLOTS)  # the static two-frame slots
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["tokentv.jpg"])
            # Switching to the animated face shows only the GIF; switching back restores the JPEG.
            photos["files"].append({"name": "tokentv.gif", "enabled": False})
            display.upload("tokentv.gif", b"GIF89a-EXACT")
            display.activate(original, "tokentv.gif")
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["tokentv.gif"])
            display.activate(original, wanted=JPEG_SLOTS)
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["tokentv.jpg"])
            with self.assertRaises(ValueError):
                display.upload("other.gif", b"GIF89a")
            display.restore(original)
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["original.jpg", "space man.gif"])
            uploads = [c for c in calls if c[0] == "POST"]
            self.assertEqual(len(uploads), 2)
            self.assertEqual(uploads[0][1], "/photo/upload")
            self.assertIn(b'name="file"; filename="tokentv.jpg"', uploads[0][2])
            self.assertIn(b"\r\n\r\nEXACT-JPEG\r\n", uploads[0][2])
            self.assertIn(b'filename="tokentv.gif"\r\nContent-Type: image/gif', uploads[1][2])
            paths = [c[1] for c in calls]
            self.assertIn("/theme/toggle?id=2&state=1", paths)
            self.assertIn("/theme/toggle?id=0&state=0", paths)
            self.assertIn("/theme/toggle?id=0&state=1", paths)
            self.assertIn("/photo/toggle?name=original.jpg&state=1", paths)
            self.assertIn("/photo/toggle?name=space%20man.gif&state=0", paths)
            self.assertIn("/photo/toggle?name=space%20man.gif&state=1", paths)
            self.assertFalse(any("delete" in p or "restart" in p or "update" in p for p in paths))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def answering(status, body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)
    return Handler


def ultra_clock(state, calls):
    """SmallTV-Ultra stock web API as recorded by the contributor and adrienbrault/geekmagic-hacs."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return
        def reply(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            calls.append(("GET", self.path))
            url = urlparse(self.path)
            if url.path == "/v.json":
                return self.reply({"m": "SmallTV-Ultra", "v": "Ultra-V9.0.54"})
            if url.path == "/app.json":
                return self.reply({"theme": state["theme"]})
            if url.path == "/album.json":
                return self.reply({"autoplay": state["autoplay"], "i_i": state["i_i"]})
            if url.path == "/set":
                for key, values in parse_qs(url.query).items():
                    state[key] = values[0] if key == "img" else int(values[0])
                return self.reply("OK")
            self.reply({}, 404)
        def do_POST(self):
            calls.append(("POST", self.path))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            form = email.message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
                                            policy=email.policy.HTTP)
            for part in form.iter_parts():
                state["parts"].append((part.get_param("name", header="content-disposition"), part.get_filename(),
                                       part.get_content_type(), part.get_payload(decode=True)))
                if self.path == "/doUpload?dir=/image/" and part.get_param("name", header="content-disposition") == "file":
                    state["files"][part.get_filename()] = part.get_payload(decode=True)
            self.reply("OK")
    return Handler


SD_PRO_BACKUP = {"themes": [{"id": 0, "enabled": True}, {"id": 2, "enabled": False}], "theme_interval": 10,
                 "files": [{"name": "original.jpg", "enabled": True}], "photo_interval": 10}
ULTRA_BACKUP = {"model": "ultra", "theme": 1, "autoplay": 1, "i_i": 5}


class UltraTests(unittest.TestCase):
    def setUp(self):
        self.state = {"theme": 1, "autoplay": 1, "i_i": 5, "img": "/image/cat.jpg", "files": {"cat.jpg": b"CAT"}, "parts": []}
        self.calls = []

    def test_only_a_smalltv_ultra_answer_selects_the_ultra_api(self):
        answers = [(200, b'{"m": "SmallTV-Ultra", "v": "Ultra-V9.0.54"}', UltraDisplay),
                   (404, b"", PhotoDisplay), (200, b'{"m": null}', PhotoDisplay), (200, b'{"m": 5}', PhotoDisplay),
                   (200, b'["SmallTV-Ultra"]', PhotoDisplay), (200, b"<html></html>", PhotoDisplay),
                   (200, b"\xff", PhotoDisplay), (200, b'{"m": "SmallTV-PRO"}', PhotoDisplay),
                   (200, b'{"m": "NotUltraAtAll"}', PhotoDisplay)]
        for status, body, expected in answers:
            with self.subTest(body=body), serve(answering(status, body)) as url:
                self.assertIs(type(open_display(url)), expected)

    def test_unreachable_or_broken_clock_keeps_the_sd_pro_path(self):
        class Garbled(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_GET(self):
                self.wfile.write(b"not an http status line\r\n\r\n")
        with serve(Garbled) as url:
            self.assertIs(type(open_display(url)), PhotoDisplay)
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        self.assertIs(type(open_display(f"http://127.0.0.1:{port}")), PhotoDisplay)

    def test_ultra_contract_backup_upload_show_and_restore(self):
        with serve(ultra_clock(self.state, self.calls)) as url:
            display = open_display(url)
            self.assertIsInstance(display, UltraDisplay)
            self.assertEqual(self.calls, [("GET", "/v.json")])
            original = display.capture()
            self.assertEqual(original, ULTRA_BACKUP)
            self.assertEqual(self.calls[1:], [("GET", "/app.json"), ("GET", "/album.json")])

            start = len(self.calls)
            display.upload("tokentv.jpg", b"EXACT-JPEG")
            display.upload("tokentv.gif", b"GIF89a-EXACT")
            for name, image in (("other.jpg", b"JPEG"), ("tokentv.jpg", b""), ("tokentv.jpg", b"x" * 60001)):
                with self.assertRaises(ValueError):
                    display.upload(name, image)
            self.assertEqual(self.calls[start:], [("POST", "/doUpload?dir=/image/")] * 2)
            self.assertEqual(self.state["parts"], [("file", "tokentv.jpg", "image/jpeg", b"EXACT-JPEG"),
                                                   ("file", "tokentv.gif", "image/gif", b"GIF89a-EXACT")])

            # Stop the slideshow, switch to the Photo Album theme, then point it at our image.
            start = len(self.calls)
            display.activate(original)
            self.assertEqual(self.calls[start:], [("GET", "/app.json"), ("GET", "/album.json"), ("GET", "/set?i_i=5&autoplay=0"),
                                                  ("GET", "/set?theme=3"), ("GET", "/set?img=%2Fimage%2Ftokentv.jpg")])
            self.assertEqual((self.state["theme"], self.state["autoplay"], self.state["img"]), (3, 0, "/image/tokentv.jpg"))
            # Already in the album with autoplay off: only the image changes — and the publisher's
            # three-argument activate call (with the wanted slot list) is accepted and ignored.
            start = len(self.calls)
            display.activate(original, "tokentv.gif", [("tokentv.gif", ""), ("tokentv-b.gif", ""),
                                                       ("tokentv-usage.gif", "")])
            self.assertEqual(self.calls[start:], [("GET", "/app.json"), ("GET", "/album.json"), ("GET", "/set?img=%2Fimage%2Ftokentv.gif")])

            start = len(self.calls)
            display.restore(original)
            self.assertEqual(self.calls[start:], [("GET", "/set?i_i=5&autoplay=1"), ("GET", "/set?theme=1")])
            self.assertEqual((self.state["theme"], self.state["autoplay"], self.state["i_i"]), (1, 1, 5))
            self.assertEqual(self.state["files"], {"cat.jpg": b"CAT", "tokentv.jpg": b"EXACT-JPEG", "tokentv.gif": b"GIF89a-EXACT"})
            paths = [c[1] for c in self.calls]
            self.assertFalse(any(word in p for p in paths for word in ("delete", "clear", "reset", "reboot", "update")))

    def test_the_ultra_uploads_one_frame_only(self):
        with serve(ultra_clock(self.state, self.calls)) as url:
            display = open_display(url)
            start = len(self.calls)
            receipts = display.upload_rotation([("tokentv.jpg", b"SINGLE")])
        self.assertEqual([r["file"] for r in receipts], ["tokentv.jpg"])  # no alternation on an Ultra
        self.assertEqual(self.calls[start:], [("POST", "/doUpload?dir=/image/")])

    def test_ultra_activate_does_not_copy_clock_values_into_the_query(self):
        self.state["i_i"] = "5&theme=0"
        with serve(ultra_clock(self.state, self.calls)) as url:
            with self.assertRaises(ValueError):
                UltraDisplay(url).activate(ULTRA_BACKUP)
        self.assertFalse(any(path.startswith("/set") for _, path in self.calls))
        self.assertEqual(self.state["theme"], 1)

    def test_backup_from_another_kind_of_clock_is_refused(self):
        sd_pro, ultra = PhotoDisplay("http://127.0.0.1:9"), UltraDisplay("http://127.0.0.1:9")
        sd_pro.check_backup(SD_PRO_BACKUP)
        ultra.check_backup(ULTRA_BACKUP)
        for display, backup in ((ultra, SD_PRO_BACKUP), (sd_pro, ULTRA_BACKUP), (ultra, ["not", "a", "backup"])):
            with self.subTest(display=type(display).__name__, backup=backup), self.assertRaises(BackupMismatch):
                display.check_backup(backup)

    def test_mismatched_backup_is_kept_and_the_clock_is_not_touched(self):
        with tempfile.TemporaryDirectory() as folder, serve(ultra_clock(self.state, self.calls)) as url:
            backup = Path(folder) / "display-original.json"
            backup.write_text(json.dumps(SD_PRO_BACKUP))
            saved = backup.read_bytes()
            display = open_display(url)
            # Live cycle: refuse before any capture, upload or new backup.
            with self.assertRaises(BackupMismatch):
                live.load_original(display, backup)
            # --restore-display: a clear message instead of a KeyError traceback.
            config = Path(folder) / "config.json"
            config.write_text(json.dumps({"device_url": url, "accounts": [
                {"key": "claude", "alias": "Claude", "provider": "claude", "email": "owner@example.com"}]}))
            with patch("sys.argv", ["token-tv", "--config", str(config), "--state-dir", folder, "--restore-display"]):
                with self.assertRaises(SystemExit) as stop:
                    live.main()
            self.assertIn("SD_PRO", str(stop.exception.code))
            self.assertIn("Nothing was changed on the clock", str(stop.exception.code))
            self.assertEqual(backup.read_bytes(), saved)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["config.json", "display-original.json"])
            self.assertEqual(self.calls, [("GET", "/v.json")] * 2)
            self.assertEqual(self.state["theme"], 1)

    def test_backup_is_saved_once_and_reused_for_the_same_kind_of_clock(self):
        with tempfile.TemporaryDirectory() as folder, serve(ultra_clock(self.state, self.calls)) as url:
            backup = Path(folder) / "display-original.json"
            display = open_display(url)
            self.assertEqual(live.load_original(display, backup), ULTRA_BACKUP)
            self.assertEqual(json.loads(backup.read_text()), ULTRA_BACKUP)
            self.state["theme"] = 3
            start = len(self.calls)
            self.assertEqual(live.load_original(display, backup), ULTRA_BACKUP)
            self.assertEqual(self.calls[start:], [])
            # SD_PRO backups have no model key and keep working unchanged.
            backup.write_text(json.dumps(SD_PRO_BACKUP))
            self.assertEqual(live.load_original(PhotoDisplay(url), backup), SD_PRO_BACKUP)
            self.assertEqual(self.calls[start:], [])


class RotationTests(unittest.TestCase):
    """The SD_PRO slideshow only re-reads the album while it rotates two enabled files, so both
    rotation slots must carry the fresh frame and stay the only enabled photos."""

    @staticmethod
    def clock(calls):
        """An SD_Pro album that auto-enables an uploaded file, like the confirmed firmware."""
        photos = {"interval": 10, "files": [{"name": "original.jpg", "enabled": True},
                                            {"name": "space man.gif", "enabled": True},
                                            {"name": "like.gif", "enabled": True},
                                            {"name": "1.jpg", "enabled": True},
                                            {"name": "tokentv.jpg", "enabled": False},
                                            {"name": "tokentv-b.jpg", "enabled": False}]}
        themes = {"interval": 10, "themes": [{"id": 0, "enabled": True}, {"id": 2, "enabled": False}]}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                calls.append(("GET", self.path))
                url = urlparse(self.path)
                if url.path == "/photo/toggle":
                    query = parse_qs(url.query)
                    for photo in photos["files"]:
                        if photo["name"] == query["name"][0]:
                            photo["enabled"] = query["state"][0] == "1"
                if url.path == "/theme/toggle":
                    query = parse_qs(url.query)
                    identifier, enabled = int(query["id"][0]), query["state"][0] == "1"
                    themes["themes"] = [dict(t, enabled=enabled) if t["id"] == identifier else t
                                        for t in themes["themes"]]
                data = themes if self.path == "/theme/list" else photos
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(("POST", self.path, body))
                filename = body.split(b"\r\n\r\n")[0].split(b'filename="')[1].split(b'"')[0].decode()
                photos["files"] = [f for f in photos["files"] if f["name"] != filename]
                photos["files"].append({"name": filename, "enabled": True})  # the firmware's auto-enable
                return self.reply_ok()

            def reply_ok(self):
                self.send_response(200)
                self.end_headers()
        return Handler, photos

    def enabled(self, photos):
        return [f["name"] for f in photos["files"] if f["enabled"]]

    def test_upload_rotation_writes_the_slot_frames_in_order_and_never_toggles(self):
        calls = []
        handler, _ = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            receipts = display.upload_rotation([("tokentv.gif", b"STOCKS-GIF"),
                                                ("tokentv-b.gif", b"STOCKS-GIF"),
                                                ("tokentv-usage.gif", b"USAGE-GIF")])
        self.assertEqual([r["file"] for r in receipts], ["tokentv.gif", "tokentv-b.gif", "tokentv-usage.gif"])
        uploads = [(path, body) for kind, path, body in calls
                   if kind == "POST" and path == "/photo/upload"]
        self.assertEqual([path for path, _ in uploads], ["/photo/upload"] * 3)
        self.assertIn(b'filename="tokentv.gif"', uploads[0][1])
        self.assertIn(b"\r\n\r\nSTOCKS-GIF\r\n", uploads[0][1])
        self.assertIn(b'filename="tokentv-b.gif"', uploads[1][1])
        self.assertIn(b'filename="tokentv-usage.gif"', uploads[2][1])
        # The two stocks slots carry the same animated bytes, and no toggle rides after the upload.
        self.assertEqual(uploads[0][1].split(b"\r\n\r\n")[1].rsplit(b"\r\n--", 1)[0],
                         uploads[1][1].split(b"\r\n\r\n")[1].rsplit(b"\r\n--", 1)[0])
        self.assertNotEqual(uploads[0][1], uploads[2][1])  # the usage slot carries the usage frame
        self.assertFalse(any("toggle" in call[1] for call in calls))
        self.assertFalse(any("delete" in call[1] for call in calls))

    def test_activate_enables_every_gif_slot_and_disables_the_legacy_jpg_slots(self):
        calls = []
        handler, photos = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            original = display.capture()
            display.upload_rotation([("tokentv.gif", b"STOCKS-GIF"), ("tokentv-b.gif", b"STOCKS-GIF"),
                                     ("tokentv-usage.gif", b"USAGE-GIF")])  # the firmware auto-enabled all three
            toggles_before = [c[1] for c in calls if "toggle" in c[1]]
            self.assertEqual(toggles_before, [])  # no toggle is chained after an upload (the flaky one)
            display.activate(original)
            self.assertEqual(sorted(self.enabled(photos)), ["tokentv-b.gif", "tokentv-usage.gif", "tokentv.gif"])
            for name in ("original.jpg", "like.gif", "1.jpg", "space man.gif"):
                self.assertIn(f"/photo/toggle?name={name.replace(' ', '%20')}&state=0", [c[1] for c in calls])
            # The legacy JPEG slots of the single-photo era are asleep again (present, but not wanted).
            self.assertNotIn("tokentv.jpg", self.enabled(photos))
            self.assertNotIn("tokentv-b.jpg", self.enabled(photos))
            # Steady state: every slot already on, nothing else enabled — the next reassert writes nothing.
            start = len(calls)
            display.activate(original)
            self.assertEqual([c[1] for c in calls[start:]], ["/theme/list", "/photo/list"])  # reads only
            # A narrower wanted set (the static JPEG slots) puts the GIF slots back to sleep.
            from token_tv.device import JPEG_SLOTS
            start = len(calls)
            display.activate(original, wanted=JPEG_SLOTS)
            self.assertIn("/photo/toggle?name=tokentv-usage.gif&state=0", [c[1] for c in calls[start:]])
            self.assertNotIn("tokentv-usage.gif", self.enabled(photos))
            # The static two-frame behaviour returns: two JPEG slots, GIF slots asleep.
            self.assertEqual(sorted(self.enabled(photos)), JPEG_SLOTS and ["tokentv-b.jpg", "tokentv.jpg"])

    def test_restore_leaves_the_rotation_slots_off_as_part_of_the_baseline(self):
        calls = []
        handler, photos = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            original = display.capture()
            display.upload_rotation([("tokentv.gif", b"STOCKS-GIF"), ("tokentv-b.gif", b"STOCKS-GIF"),
                                     ("tokentv-usage.gif", b"USAGE-GIF")])
            display.activate(original)
            display.restore(original)
            self.assertEqual(self.enabled(photos), ["original.jpg", "space man.gif", "like.gif", "1.jpg"])
            for slot in ("tokentv.gif", "tokentv-b.gif", "tokentv-usage.gif",
                         "tokentv.jpg", "tokentv-b.jpg", "tokentv-usage.jpg"):
                self.assertNotIn(slot, self.enabled(photos))  # every slot of ours sleeps again

    def test_activate_pins_the_slideshow_interval_that_makes_the_20_10_loop(self):
        calls = []
        handler, photos = self.clock(calls)
        photos["interval"] = 30  # the clock's stored slideshow interval, drifted from ours
        with serve(handler) as url:
            display = PhotoDisplay(url)
            display.upload_rotation([("tokentv.gif", b"S"), ("tokentv-b.gif", b"S"),
                                     ("tokentv-usage.gif", b"U")])
            display.activate({"themes": []})
        # Three enabled slots at a 10-second interval loop 20 s stocks then 10 s usage — the
        # schedule the user asked for, driven by the firmware's rotation (now animated).
        self.assertIn("/photo/interval?val=10", [c[1] for c in calls])

    def test_lost_rotation_detects_any_missing_wanted_slot_only_from_a_real_album(self):
        from token_tv.device import JPEG_SLOTS, ROTATION_FILES, lost_rotation
        full = [{"name": "tokentv.gif", "enabled": True}, {"name": "tokentv-b.gif", "enabled": True},
                {"name": "tokentv-usage.gif", "enabled": False}]
        self.assertFalse(lost_rotation(full, "tokentv.gif", ROTATION_FILES))  # all wanted present (disabled is fine)
        for missing in ("tokentv.gif", "tokentv-b.gif", "tokentv-usage.gif"):
            self.assertTrue(lost_rotation([f for f in full if f["name"] != missing], "tokentv.gif", ROTATION_FILES),
                            missing)
        # A caller can narrow the wanted set: the feeder owns the two static JPEG slots.
        self.assertFalse(lost_rotation([*full, {"name": "tokentv.jpg", "enabled": True},
                                        {"name": "tokentv-b.jpg", "enabled": True}],
                                       "tokentv.gif", wanted=JPEG_SLOTS))
        self.assertTrue(lost_rotation([*full, {"name": "tokentv.jpg", "enabled": True}],
                                      "tokentv.gif", wanted=JPEG_SLOTS))  # the -b jpg slot is gone
        self.assertTrue(lost_rotation([], "tokentv-usage.gif"))
        self.assertFalse(lost_rotation(None, "tokentv.gif"))  # an unreadable album (an Ultra) is never "lost"


if __name__ == "__main__":
    unittest.main()


class ClockSettingsTests(unittest.TestCase):
    """Brightness and the night window live in the ESP8266's emulated EEPROM: one 4 KB sector,
    rewritten whole on every commit, no wear levelling, and it holds the Wi-Fi credentials too."""

    class Fake:
        def __init__(self, config):
            self.config = config
            self.writes = []

        def request(self, path, data=None, headers=None):
            if path == '/config':
                return 200, json.dumps(self.config).encode()
            if path == '/mac':
                return 200, b'8C:8C:29:AD:ED:9B'
            self.writes.append(path)
            return 200, b'ok'

    def _display(self, config):
        from token_tv.device import PhotoDisplay
        display = PhotoDisplay('http://127.0.0.1')
        fake = self.Fake(config)
        display.request = fake.request
        return display, fake

    def test_a_setting_already_at_the_wanted_value_is_not_rewritten(self):
        display, fake = self._display({'brightness': 52, 'nightmode': 1, 'starttime': 23,
                                       'stoptime': 7, 'nightbrightness': 19})
        written = display.apply_settings({'lcd_brightness': 52, 'nightmode': 1, 'starttime': 23,
                                          'stoptime': 7, 'nightbrightness': 19})
        self.assertEqual(written, {})
        self.assertEqual(fake.writes, [])  # nothing reached the EEPROM

    def test_only_the_differing_settings_are_written(self):
        display, fake = self._display({'brightness': 52, 'nightmode': 1, 'starttime': 23,
                                       'stoptime': 7, 'nightbrightness': 19})
        written = display.apply_settings({'lcd_brightness': 40, 'nightmode': 1, 'stoptime': 7})
        self.assertEqual(written, {'lcd_brightness': 40})
        self.assertEqual(len(fake.writes), 1)
        self.assertIn('key=lcd_brightness', fake.writes[0])
        self.assertIn('value=40', fake.writes[0])

    def test_a_boolean_reading_compares_equal_to_its_number(self):
        display, fake = self._display({'nightmode': True})
        self.assertEqual(display.apply_settings({'nightmode': 1}), {})
        self.assertEqual(fake.writes, [])

    def test_out_of_range_and_unknown_settings_are_refused_before_any_write(self):
        display, fake = self._display({'brightness': 52})
        for bad in ({'lcd_brightness': 1}, {'lcd_brightness': 100}, {'starttime': 24}):
            with self.assertRaises(ValueError):
                display.apply_settings(bad)
        with self.assertRaises(ValueError):
            display.apply_settings({'ssid': 1})       # not a display setting; must never be writable
        with self.assertRaises(ValueError):
            display.apply_settings({'weatherkey': 1})
        self.assertEqual(fake.writes, [])

    def test_identity_returns_the_bare_mac(self):
        display, _ = self._display({})
        self.assertEqual(display.identity(), '8C:8C:29:AD:ED:9B')

    def test_the_ultra_profile_refuses_rather_than_calling_a_route_it_lacks(self):
        from token_tv.device import UltraDisplay, SettingsUnsupported
        ultra = UltraDisplay('http://127.0.0.1')
        for call in (ultra.identity, ultra.settings):
            with self.assertRaises(SettingsUnsupported):
                call()
        with self.assertRaises(SettingsUnsupported):
            ultra.apply_settings({'lcd_brightness': 40})

    def test_the_config_maps_onto_the_clock_s_own_setting_names(self):
        from token_tv.live import wanted_settings
        self.assertEqual(wanted_settings({}), {})
        self.assertEqual(
            wanted_settings({'brightness': 40, 'night': {'enabled': True, 'start': 23, 'stop': 7,
                                                         'brightness': 19}}),
            {'lcd_brightness': 40, 'nightmode': 1, 'starttime': 23, 'stoptime': 7,
             'nightbrightness': 19})
        # A partial night block leaves everything it does not mention alone.
        self.assertEqual(wanted_settings({'night': {'enabled': False}}), {'nightmode': 0})


class LiveCycleTests(unittest.TestCase):
    """live.cycle must notice the clock losing the rotation twin and re-send both slots."""

    class FakeClock:
        MODEL = 'sdpro'

        def __init__(self):
            self.uploads = []  # (name, image) in upload order
            self.wanted = []  # the slot list the publisher asked activate to keep awake
            self.activations = 0
            self.album = [{'name': 'original.jpg', 'enabled': True}]
            self.done = threading.Event()

        def capture(self):
            return {'themes': [{'id': 2, 'enabled': True}], 'theme_interval': 10,
                    'files': [dict(f) for f in self.album], 'photo_interval': 10}

        def check_backup(self, original):
            return None

        def apply_settings(self, wanted):
            return {}

        def upload(self, name, image):
            self.uploads.append((name, image))
            self.album.append({'name': name, 'enabled': True})  # the firmware's auto-enable
            if len(self.uploads) >= 6:  # second rotation set: cycle 3 has re-downloaded the lost slot
                self.done.set()
            return {'file': name, 'status': 200, 'bytes': len(image)}

        def upload_rotation(self, frames):
            return [self.upload(name, image) for name, image in frames]

        def activate(self, original, name='tokentv.jpg', wanted=None):
            self.activations += 1
            from token_tv.device import wanted_slots as _slots
            self.wanted.append(list(_slots(name, wanted)))
            if self.activations == 1:
                from token_tv.device import wanted_slots
                target = wanted_slots(name, wanted)[1 if len(wanted_slots(name, wanted)) > 1 else 0]
                self.album = [f for f in self.album if f['name'] != target]  # one wanted slot is lost
            else:
                self.done.set()
                raise SystemExit  # enough: end the poll thread deterministically

    FROZEN = 1791900000.0

    def _run(self, clock, config, folder, after=None):
        from token_tv import live
        from token_tv.sources import SourceError

        class StubServer:
            def __init__(self, address, handler):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def serve_forever(self):
                clock.done.wait(15)  # the poll thread works while this "server" holds the loop
                raise KeyboardInterrupt

        class QuickWait(threading.Event):
            _real_wait = threading.Event.wait  # captured before the patch below

            def wait(self, timeout=None):
                return QuickWait._real_wait(self, 0.01)  # the dashboard wake-up path, never a real stall

        ticks = iter(range(0, 10 ** 9, 120))  # the refresh clock advances two minutes per poll-loop check
        with patch('token_tv.live.open_display', return_value=clock), \
                patch('token_tv.live.ThreadingHTTPServer', StubServer), \
                patch('token_tv.live.threading.Event', QuickWait), \
                patch('token_tv.live.time.monotonic', side_effect=lambda: next(ticks)), \
                patch('token_tv.live.time.time', return_value=self.FROZEN), \
                patch('token_tv.sources.claude_token', side_effect=SourceError('auth_required')), \
                patch('sys.argv', ['token-tv', '--config', str(config), '--state-dir', folder]):
            live.main()
            if after:  # expectation renders share the frozen clock, or feeds read stale
                after()

    def _config(self, folder, **extra):
        config = Path(folder) / 'config.json'
        config.write_text(json.dumps({'accounts': [{'key': 'a', 'alias': 'A', 'provider': 'claude',
                                                    'email': 'owner@example.com', 'source_home': '/unused'}],
                                      'device_url': 'http://192.168.0.50', 'poll_seconds': 300,
                                      'reassert_cycles': 3, **extra}))
        return config

    def test_a_non_stock_style_keeps_the_chosen_face_and_never_uploads_the_usage_slot(self):
        from token_tv.display import render_frame
        clock = self.FakeClock()
        frames = {}
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder, display_style='neon')

            def after():
                saved = json.loads((Path(folder) / 'snapshot.json').read_text())
                saved.pop('display', None)
                frames['neon'], _ = render_frame(saved, 0, 'neon', 900, 60000)
                frames['mirrors'] = sorted(p.name for p in Path(folder).iterdir())

            self._run(clock, config, folder, after=after)
        # The alternation never engaged: the chosen style's face fills the two stock slots (two
        # uploads per event, re-sent after the fake clock loses the second), and the usage slot is
        # neither uploaded nor mirrored.
        self.assertEqual([name for name, _ in clock.uploads],
                         ["tokentv.jpg", "tokentv-b.jpg", "tokentv.jpg", "tokentv-b.jpg"])
        self.assertEqual([image for _, image in clock.uploads], [frames['neon']] * 4)  # neon, not pixel
        self.assertNotIn('tokentv-usage.jpg', frames['mirrors'])

    def test_a_market_shutting_reasserts_the_slot_set_at_once(self):
        """Open, the album wants all three slots; shut, only the two the usage rest is written into.

        The change is asserted on the spot rather than at the next periodic reassert: the third
        slot stops being written when the market shuts, so leaving it awake would rotate a frozen
        usage screen between two fresh ones for up to `reassert_cycles`."""
        from token_tv.device import ROTATION_FILES

        def feed(is_open):
            return {'updated_at': int(self.FROZEN), 'source': 'YAHOO',
                    'market': {'is_open': is_open, 'session': 'regular' if is_open else 'closed',
                               'exchange': 'US'},
                    'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                                 'previous_close': None, 'at': int(self.FROZEN) - 5}]}

        with tempfile.TemporaryDirectory() as folder:
            feed_path = Path(folder) / 'stocks-snapshot.json'
            feed_path.write_text(json.dumps(feed(True)))

            class ShuttingClock(self.FakeClock):
                def upload(self, name, image):  # the market shuts after the first rotation set
                    receipt = super().upload(name, image)
                    feed_path.write_text(json.dumps(feed(False)))
                    return receipt

                def activate(self, original, name='tokentv.jpg', wanted=None):
                    from token_tv.device import wanted_slots
                    self.activations += 1
                    self.wanted.append(list(wanted_slots(name, wanted)))
                    if self.activations >= 2:
                        self.done.set()
                        raise SystemExit  # both slot sets seen

            clock = ShuttingClock()
            config = self._config(folder, display_style='stocks',
                                  stocks_snapshot=str(feed_path))
            self._run(clock, config, folder)
        self.assertEqual(clock.wanted, [list(ROTATION_FILES), list(STOCKS_FILES)])
        # The open cycle wrote three slots; the shut one only the two it rests on.
        self.assertEqual([name for name, _ in clock.uploads],
                         list(ROTATION_FILES) + list(STOCKS_FILES))

    def test_a_later_reassert_is_not_a_false_lost_frame(self):
        """A clock that keeps both stock slots runs its reasserts with no forced re-upload."""

        class SteadyClock(self.FakeClock):
            def activate(self, original, name='tokentv.jpg', wanted=None):
                self.activations += 1  # no exit here: the cycle's own receipt still gets written

            def capture(self):
                if self.activations >= 3:  # cycles 3 and 6 (the reasserts) have published
                    self.done.set()
                    raise SystemExit
                return super().capture()

        clock = SteadyClock()
        from token_tv import live
        seen_receipts = []
        real_write_json = live.write_json
        with tempfile.TemporaryDirectory() as folder:
            def collect(path, data):
                real_write_json(path, data)
                if 'receipt' in str(path):
                    seen_receipts.append(data)

            with patch('token_tv.live.write_json', collect):
                self._run(clock, self._config(folder, display_style='neon', reassert_cycles=3), folder)
        # Cycle 1 published the neon frame (fresh start, both stock slots); the reasserts at cycles
        # 3 and 6 found everything they wanted present — no lost frame invented, no re-upload —
        # and the never-uploaded usage slot is not part of the wanted set that could invent one.
        self.assertEqual([name for name, _ in clock.uploads], ["tokentv.jpg", "tokentv-b.jpg"])
        self.assertEqual(clock.activations, 3)
        due_receipts = [receipt for receipt in seen_receipts if receipt.get('reasserted')]
        self.assertEqual([len(receipt['uploads']) for receipt in due_receipts], [2, 0, 0])
        self.assertTrue(all(receipt['throttled'] is False for receipt in due_receipts))

    def test_open_market_alternates_the_stocks_face_with_the_usage_face(self):
        from token_tv.display import render_frame
        clock = self.FakeClock()
        frames = {}
        with tempfile.TemporaryDirectory() as folder:
            feed = {'updated_at': int(self.FROZEN), 'source': 'YAHOO',
                    'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
                    'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                                 'previous_close': None, 'at': int(self.FROZEN) - 5}]}
            (Path(folder) / 'stocks-snapshot.json').write_text(json.dumps(feed))
            config = self._config(folder, display_style='stocks',
                                  stocks_snapshot=str(Path(folder) / 'stocks-snapshot.json'))

            def after():
                from token_tv.usage_gif import render_usage_gif
                saved = json.loads((Path(folder) / 'snapshot.json').read_text())
                saved.pop('display', None)
                frames['stocks'], _ = render_frame(saved, 0, 'stocks', 900, 400000)  # the breathing GIF
                frames['usage'] = render_usage_gif({k: v for k, v in saved.items() if k != 'stocks'})

            self._run(clock, config, folder, after=after)
        self.assertEqual([name for name, _ in clock.uploads],
                         ["tokentv.gif", "tokentv-b.gif", "tokentv-usage.gif"] * 2)
        # The 20-second side of the loop is the animated stocks GIF in the two stock slots; the
        # 10-second side is the shimmering usage GIF in the third slot.
        self.assertEqual([image for _, image in clock.uploads[:3]],
                         [frames['stocks'], frames['stocks'], frames['usage']])
        self.assertNotEqual(frames['stocks'], frames['usage'])

    def test_a_closed_market_rests_the_clock_on_the_usage_screen(self):
        from token_tv.usage_gif import render_usage_gif
        clock = self.FakeClock()
        frames = {}
        with tempfile.TemporaryDirectory() as folder:
            feed = {'updated_at': int(self.FROZEN), 'source': 'YAHOO',
                    'market': {'is_open': False, 'session': 'closed', 'exchange': 'US'},
                    'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                                 'previous_close': None, 'at': int(self.FROZEN) - 5}]}
            (Path(folder) / 'stocks-snapshot.json').write_text(json.dumps(feed))
            config = self._config(folder, display_style='stocks',
                                  stocks_snapshot=str(Path(folder) / 'stocks-snapshot.json'))

            def after():
                saved = json.loads((Path(folder) / 'snapshot.json').read_text())
                saved.pop('display', None)
                frames['usage'] = render_usage_gif({k: v for k, v in saved.items() if k != 'stocks'})

            self._run(clock, config, folder, after=after)
        # A closed market rests the clock on the shimmering usage screen: the loop only runs
        # while open. The rest uses the two stock slots and no more — two is the fewest the
        # firmware will re-read, so the third slot is neither written nor wanted.
        self.assertEqual([image for _, image in clock.uploads],
                         [frames['usage']] * len(clock.uploads))
        self.assertEqual({name for name, _ in clock.uploads}, set(STOCKS_FILES))
        self.assertEqual(len(clock.uploads), 2 * 2)  # two slots, two publishing cycles
        self.assertEqual(clock.wanted, [list(STOCKS_FILES)] * clock.activations)

    def test_a_closed_market_takes_the_moon_cameo_on_its_one_in_ten_tick(self):
        """Closed with a trusted reopen: the stock slots rest on the usage screen every tick, and
        the moon reopen face — which used to be the whole rest — cameos in the third slot for
        exactly one tick in ten, which the wanted-set toggle enables and then re-sleeps."""
        from token_tv.device import USAGE_FILE, JPEG_SLOTS

        feed = {'updated_at': int(self.FROZEN), 'source': 'YAHOO',
                'market': {'is_open': False, 'session': 'closed', 'exchange': 'US',
                           'tz': 'America/New_York',
                           'next_open_at': int(self.FROZEN) + 26 * 3600, 'next_open_exchange': 'US'},
                'symbols': [{'symbol': 'APPN', 'price': 42.52, 'change': 4.07, 'percent': 10.59,
                             'previous_close': None, 'at': int(self.FROZEN) - 5}]}

        class CameoClock(self.FakeClock):
            def upload(self, name, image):  # like the base, but the album marker never ends the run
                self.uploads.append((name, image))
                self.album.append({'name': name, 'enabled': True})
                return {'file': name, 'status': 200, 'bytes': len(image)}

            def activate(self, original, name='tokentv.jpg', wanted=None):
                from token_tv.device import wanted_slots
                self.activations += 1
                self.wanted.append(list(wanted_slots(name, wanted)))
                if len(self.wanted) >= 2 and any(len(w) == 3 for w in self.wanted) \
                        and len(self.wanted[-1]) == 2:
                    self.done.set()
                    raise SystemExit  # cameo seen, then slept off again

        clock = CameoClock()
        frames = {}
        with tempfile.TemporaryDirectory() as folder:
            feed_path = Path(folder) / 'stocks-snapshot.json'
            feed_path.write_text(json.dumps(feed))
            config = self._config(folder, display_style='stocks',
                                  stocks_snapshot=str(feed_path), max_writes_per_hour=0)

            def after():
                from token_tv.combo_stocks import render_combo_stocks_closed_gif
                from token_tv.usage_gif import render_usage_gif
                saved = json.loads((Path(folder) / 'snapshot.json').read_text())
                saved.pop('display', None)
                frames['usage'] = render_usage_gif({k: v for k, v in saved.items() if k != 'stocks'})
                frames['moon'] = render_combo_stocks_closed_gif(saved)

            self._run(clock, config, folder, after=after)
        # The rest is always the usage pair in the two stock slots — even on cameo ticks. The
        # moon never returns to the stock slots it used to rest in.
        self.assertEqual({name for name, _ in clock.uploads if name in STOCKS_FILES}, set(STOCKS_FILES))
        for name, image in clock.uploads:
            if name in STOCKS_FILES:
                self.assertEqual(image, frames['usage'], name)  # the rest, every closed tick
            else:
                self.assertEqual(image, frames['moon'], name)   # the cameo, third slot only
        # The cameo went to the third slot, on the one-in-ten schedule: wanted gains exactly the
        # usage slot on a cameo tick and loses it on the next one — a one-slot wanted difference.
        cameo_wanted = [STOCKS_FILES[0], STOCKS_FILES[1], USAGE_FILE]
        self.assertTrue(any(w == cameo_wanted for w in clock.wanted), clock.wanted)
        self.assertNotEqual(clock.wanted[-1], clock.wanted[-2])  # tick boundaries flip the wanted set
        self.assertEqual({tuple(w) for w in clock.wanted},
                         {tuple(STOCKS_FILES), tuple(cameo_wanted)}, clock.wanted)
        cameo_uploads = [image for name, image in clock.uploads if name == USAGE_FILE]
        self.assertTrue(cameo_uploads and cameo_uploads[0] == frames['moon'], clock.uploads)
        # The cameo's wanted set equals the open-market rotation set, so what separates the rest
        # from the alternation is the bytes: the two stock slots carry the usage face, not the
        # board — and no JPEG slot is ever wanted or written in the stocks style.
        self.assertEqual([name for name, _ in clock.uploads if name in JPEG_SLOTS], [])


class MoonCameoTests(unittest.TestCase):
    """The cameo schedule and its honesty gates, decided cycle-by-cycle in live.py."""

    @staticmethod
    def _snapshot(updated_at, published_at=None, **market):
        """A closed-market feed. `published_at=None` keeps the key absent, which is exactly
        what an older feeder's payload looks like; a non-None value must land on the wire."""
        stocks = {'updated_at': updated_at, 'source': 'YAHOO',
                  'market': dict({'is_open': False, 'session': 'closed', 'exchange': 'US'}, **market)}
        if published_at is not None:
            stocks['published_at'] = published_at
        return {'accounts': {}, 'stocks': stocks}

    def test_the_cameo_fires_on_exactly_one_tick_in_ten(self):
        now = time.time()
        snapshot = self._snapshot(now, published_at=int(now), next_open_at=int(now) + 7200,
                                  next_open_exchange='US')
        self.assertTrue(live.moon_cameo(10, snapshot, now=now))
        self.assertTrue(live.moon_cameo(20, snapshot, now=now))
        for cycle in range(1, 10):
            self.assertFalse(live.moon_cameo(cycle, snapshot, now=now), cycle)
        self.assertFalse(live.moon_cameo(11, snapshot, now=now))
        self.assertFalse(live.moon_cameo(None, snapshot, now=now))
        self.assertFalse(live.moon_cameo(True, snapshot, now=now))

    def test_the_cameo_buys_nothing_on_untrusted_feeds(self):
        now = time.time()
        trusted = self._snapshot(now, published_at=int(now), next_open_at=int(now) + 7200,
                                 next_open_exchange='US')
        self.assertTrue(live.moon_cameo(10, trusted, now=now))
        no_schedule = self._snapshot(now, published_at=int(now), next_open_at=int(now) + 7200,
                                     next_open_exchange='US')
        del no_schedule['stocks']['market']['next_open_at']
        self.assertFalse(live.moon_cameo(10, no_schedule, now=now))        # no named reopen
        self.assertFalse(live.moon_cameo(10, self._snapshot(now), now=now))  # no schedule keys
        past = self._snapshot(now, published_at=int(now), next_open_at=int(now) - 5,
                              next_open_exchange='US')
        self.assertFalse(live.moon_cameo(10, past, now=now))               # the bell already rang
        unknown = self._snapshot(now, published_at=int(now), next_open_at=int(now) + 7200,
                                 next_open_exchange='LSE')
        self.assertFalse(live.moon_cameo(10, unknown, now=now))            # an exchange nobody opens
        open_market = self._snapshot(now, published_at=int(now), is_open=True,
                                     next_open_at=int(now) + 7200, next_open_exchange='US')
        self.assertFalse(live.moon_cameo(10, open_market, now=now))        # an open board is a board
        stale = self._snapshot(now - 601, published_at=int(now) - 601, next_open_at=int(now) + 7200,
                               next_open_exchange='US')
        self.assertFalse(live.moon_cameo(10, stale, now=now))              # both stamps old: a dead feeder
        self.assertFalse(live.moon_cameo(10, {'accounts': {}}, now=now))   # no feed at all

    def test_the_cameo_reads_the_wake_stamp_not_the_reading_on_idle_closed_wakes(self):
        """The weekend case: the feeder wakes on schedule, keeps the frozen board, and the
        payload's reading stamp (`updated_at`) is the last real fetch — hours old. A dead
        cameo gate would have switched the moon off for the whole weekend."""
        now = time.time()
        weekend = self._snapshot(now - 3600, published_at=int(now), next_open_at=int(now) + 7200,
                                 next_open_exchange='US')
        self.assertTrue(live.moon_cameo(10, weekend, now=now))             # feeder alive, board retained

    def test_an_older_payload_without_published_at_falls_back_to_the_reading_stamp(self):
        now = time.time()
        older = self._snapshot(now, next_open_at=int(now) + 7200, next_open_exchange='US')
        self.assertNotIn('published_at', older['stocks'])                  # the old payload shape
        self.assertTrue(live.moon_cameo(10, older, now=now))               # backward compatible
        stale_older = self._snapshot(now - 601, next_open_at=int(now) + 7200, next_open_exchange='US')
        self.assertFalse(live.moon_cameo(10, stale_older, now=now))        # and still honest about age

    def test_a_garbage_or_boolean_wake_stamp_buys_no_cameo(self):
        now = time.time()
        for published_at in ('yesterday', True, [now], {}, b'now'):
            snapshot = self._snapshot(now, published_at=published_at, next_open_at=int(now) + 7200,
                                      next_open_exchange='US')
            self.assertFalse(live.moon_cameo(10, snapshot, now=now), published_at)
        # An explicit None is the absent key: the reading stamp decides, and it is fresh.
        self.assertTrue(live.moon_cameo(10, self._snapshot(now, published_at=None,
                                                           next_open_at=int(now) + 7200,
                                                           next_open_exchange='US'), now=now))
