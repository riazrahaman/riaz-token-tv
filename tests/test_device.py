import email
import email.policy
import json
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from token_tv import live
from token_tv.device import BackupMismatch, PhotoDisplay, UltraDisplay, open_display


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
            display.activate(original)
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["tokentv.jpg"])
            # Switching to the animated face shows only the GIF; switching back restores the JPEG.
            photos["files"].append({"name": "tokentv.gif", "enabled": False})
            display.upload("tokentv.gif", b"GIF89a-EXACT")
            display.activate(original, "tokentv.gif")
            self.assertEqual([p["name"] for p in photos["files"] if p["enabled"]], ["tokentv.gif"])
            display.activate(original, "tokentv.jpg")
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
            # Already in the album with autoplay off: only the image changes.
            start = len(self.calls)
            display.activate(original, "tokentv.gif")
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
            receipts = display.upload_rotation(b"SINGLE")
        self.assertEqual([r["file"] for r in receipts], ["tokentv.jpg"])  # no rotation twin on an Ultra
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

    def test_upload_rotation_writes_both_slots_in_order_and_never_toggles(self):
        calls = []
        handler, _ = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            receipts = display.upload_rotation(b"EXACT-JPEG")
        self.assertEqual([r["file"] for r in receipts], ["tokentv.jpg", "tokentv-b.jpg"])
        uploads = [(path, body) for kind, path, body in calls
                   if kind == "POST" and path == "/photo/upload"]
        self.assertEqual([path for path, _ in uploads], ["/photo/upload"] * 2)
        self.assertIn(b'filename="tokentv.jpg"', uploads[0][1])
        self.assertIn(b"\r\n\r\nEXACT-JPEG\r\n", uploads[0][1])
        self.assertIn(b'filename="tokentv-b.jpg"', uploads[1][1])
        # The two names carry the same fresh bytes, and no toggle rides after the upload.
        self.assertEqual(uploads[0][1].split(b"\r\n\r\n")[1].rsplit(b"\r\n--", 1)[0],
                         uploads[1][1].split(b"\r\n\r\n")[1].rsplit(b"\r\n--", 1)[0])
        self.assertFalse(any("toggle" in call[1] for call in calls))
        self.assertFalse(any("delete" in call[1] for call in calls))

    def test_activate_enables_both_slots_disables_everything_else_then_is_a_noop(self):
        calls = []
        handler, photos = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            original = display.capture()
            display.upload_rotation(b"EXACT-JPEG")  # the firmware auto-enabled both
            toggles_before = [c[1] for c in calls if "toggle" in c[1]]
            self.assertEqual(toggles_before, [])  # no toggle is chained after an upload (the flaky one)
            display.activate(original)
            self.assertEqual(sorted(self.enabled(photos)), ["tokentv-b.jpg", "tokentv.jpg"])
            for name in ("original.jpg", "like.gif", "1.jpg", "space man.gif"):
                self.assertIn(f"/photo/toggle?name={name.replace(' ', '%20')}&state=0", [c[1] for c in calls])
            # Steady state: both slots already on, nothing else enabled — the next reassert writes nothing.
            start = len(calls)
            display.activate(original)
            self.assertEqual([c[1] for c in calls[start:]], ["/theme/list", "/photo/list"])  # reads only
            # A GIF cycle shows only the GIF; switching back JPEG restores both slots.
            display.upload("tokentv.gif", b"GIF89a-EXACT")
            display.activate(original, "tokentv.gif")
            self.assertEqual(self.enabled(photos), ["tokentv.gif"])
            display.activate(original, "tokentv.jpg")
            self.assertEqual(sorted(self.enabled(photos)), ["tokentv-b.jpg", "tokentv.jpg"])

    def test_restore_leaves_the_second_slot_off_as_part_of_the_baseline(self):
        calls = []
        handler, photos = self.clock(calls)
        with serve(handler) as url:
            display = PhotoDisplay(url)
            original = display.capture()
            display.upload_rotation(b"EXACT-JPEG")
            display.activate(original)
            display.restore(original)
            self.assertEqual(self.enabled(photos), ["original.jpg", "space man.gif", "like.gif", "1.jpg"])
            self.assertNotIn("tokentv-b.jpg", self.enabled(photos))  # our second slot sleeps again
            self.assertNotIn("tokentv.jpg", self.enabled(photos))

    def test_lost_rotation_detects_a_missing_primary_or_twin_only_from_a_real_album(self):
        from token_tv.device import lost_rotation
        self.assertTrue(lost_rotation([{"name": "tokentv.jpg", "enabled": True}], "tokentv.jpg"))  # twin gone
        self.assertTrue(lost_rotation([{"name": "tokentv-b.jpg", "enabled": True}], "tokentv.jpg"))  # primary gone
        self.assertTrue(lost_rotation([], "tokentv.gif"))  # the gif slot is gone
        self.assertFalse(lost_rotation([{"name": "tokentv.jpg", "enabled": True},
                                        {"name": "tokentv-b.jpg", "enabled": False}], "tokentv.jpg"))
        self.assertFalse(lost_rotation(None, "tokentv.jpg"))  # an unreadable album (an Ultra) is never "lost"


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
            self.uploads = []
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
            self.uploads.append(name)
            self.album.append({'name': name, 'enabled': True})  # the firmware's auto-enable
            if len(self.uploads) >= 4:  # second pair in: cycle 3 has re-downloaded the twin
                self.done.set()
            return {'file': name, 'status': 200, 'bytes': len(image)}

        def upload_rotation(self, image, name='tokentv.jpg'):
            receipts = [self.upload(name, image)]
            receipts.append(self.upload('tokentv-b.jpg', image))  # a JPEG frame always
            return receipts

        def activate(self, original, name='tokentv.jpg'):
            self.activations += 1
            if self.activations == 1:
                self.album = [f for f in self.album if f['name'] != 'tokentv-b.jpg']  # the twin is lost
            else:
                raise SystemExit  # enough: end the poll thread deterministically

    def test_a_missing_twin_is_lost_and_both_slots_are_resent(self):
        from token_tv import live
        from token_tv.sources import SourceError
        clock = self.FakeClock()

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
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({'accounts': [{'key': 'a', 'alias': 'A', 'provider': 'claude',
                                                        'email': 'owner@example.com', 'source_home': '/unused'}],
                                          'device_url': 'http://192.168.0.50', 'poll_seconds': 300,
                                          'reassert_cycles': 3}))
            with patch('token_tv.live.open_display', return_value=clock), \
                    patch('token_tv.live.ThreadingHTTPServer', StubServer), \
                    patch('token_tv.live.threading.Event', QuickWait), \
                    patch('token_tv.live.time.monotonic', side_effect=lambda: next(ticks)), \
                    patch('token_tv.sources.claude_token', side_effect=SourceError('auth_required')), \
                    patch('sys.argv', ['token-tv', '--config', str(config), '--state-dir', folder]):
                live.main()
        # Cycle 1 sends both (fresh start); cycle 2 is deduped; cycle 3's reassert notices the
        # lost twin and re-sends both — the dedupe never hides a lost slot.
        self.assertEqual(clock.uploads, ['tokentv.jpg', 'tokentv-b.jpg', 'tokentv.jpg', 'tokentv-b.jpg'])
