"""Live account dashboard and stock-firmware image publisher."""
import argparse
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from token_tv.device import FILES, LIMITS, BackupMismatch, may_write, open_display
from token_tv.catalog import payload as theme_payload
from token_tv.display import STYLES, render_frame, render_page, watch_payload
from token_tv.web_assets import HTML, ASSETS, asset
from token_tv.sources import load_config
from token_tv.state import UsageStore


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_original(device, backup_path):
    """Return the clock's original state; save it first only if no backup exists yet."""
    if backup_path.is_file():
        original = json.loads(backup_path.read_text())
        device.check_backup(original)  # a backup from another kind of clock is kept and nothing is sent
        return original
    original = device.capture()
    write_json(backup_path, original)
    return original


def mismatch_message(error, backup_path):
    return (f"{error} Nothing was changed on the clock and {backup_path} was kept. If the clock was offline "
            "when TokenTV started, start TokenTV again. If you replaced the clock, put the old one back to "
            "restore it, or move that file away so TokenTV saves a new backup.")


class DisplayPreferences:
    def __init__(self, config_path, config):
        self.path = Path(config_path)
        self.config = dict(config)
        self.style = config.get('display_style', 'pixel')
        self.applied_style = None
        self.status = 'queued' if config.get('device_url') else 'preview_only'
        self.lock = threading.Lock()
        self.changed = threading.Event()

    def snapshot(self):
        with self.lock:
            return {'style': self.style, 'applied_style': self.applied_style,
                    'status': self.status, 'styles': list(STYLES)}

    def set_style(self, style):
        if style not in STYLES:
            raise ValueError('Unknown display style')
        with self.lock:
            updated = dict(load_config(self.path), display_style=style)
            write_json(self.path, updated)
            self.config = updated
            self.style = style
            self.status = 'queued' if self.config.get('device_url') else 'preview_only'
        self.changed.set()

    def delivered(self, style, success):
        with self.lock:
            if success:
                self.applied_style = style
            self.status = ('queued' if style != self.style else 'ok' if success else 'error')


def handler(store, preferences=None, quantum=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            snapshot = store.snapshot()
            display = preferences.snapshot() if preferences else {'style': 'pixel', 'status': 'preview_only', 'styles': list(STYLES)}
            snapshot['display'] = display
            content_type = "application/json; charset=utf-8"
            if path == "/":
                body = HTML.encode()
                content_type = "text/html; charset=utf-8"
            elif path in ASSETS:
                body, content_type = asset(path)
            elif path == "/snapshot":
                body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
            elif path.startswith("/snapshot/") and path[10:] in snapshot["accounts"]:
                body = json.dumps(snapshot["accounts"][path[10:]], ensure_ascii=False, separators=(",", ":")).encode()
            elif path in ("/frame/0.jpg", "/frame/1.jpg"):
                style = parse_qs(url.query).get('style', [display['style']])[0]
                if style not in STYLES:
                    self.send_error(400, 'Unknown display style')
                    return
                # The same bucketing as the published frame, so the preview matches the clock.
                body = render_page(snapshot, int(path[7]), style, quantum)
                content_type = "image/gif" if body[:4] == b"GIF8" else "image/jpeg"
            elif path == '/themes':
                body = json.dumps(theme_payload()).encode()
            elif path == '/display':
                body = json.dumps(display).encode()
            elif path == "/watch.json":
                body = json.dumps(watch_payload(snapshot), ensure_ascii=False, separators=(",", ":")).encode()
            elif path == "/health":
                body = json.dumps({"status": "ok", "updated_at": snapshot["updated_at"]}).encode()
            else:
                self.send_error(404)
                return
            self.reply(200, body, content_type)

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path != '/display/style':
                self.send_error(404)
                return
            if preferences is None:
                self.send_error(409, 'Display settings unavailable')
                return
            origin = self.headers.get('Origin')
            if origin and (urlparse(origin).scheme not in ('http', 'https') or urlparse(origin).netloc != self.headers.get('Host')):
                self.send_error(403, 'Origin mismatch')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Invalid JSON request')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or set(data) != {'style'} or data['style'] not in STYLES:
                    raise ValueError('Unknown display style')
                preferences.set_style(data['style'])
            except (ValueError, UnicodeError):
                self.send_error(400, 'Choose a supported display style')
                return
            except OSError:
                self.send_error(500, 'Could not save display choice')
                return
            self.reply(200, json.dumps(preferences.snapshot()).encode())
    return Handler


def main():
    parser = argparse.ArgumentParser(description="TokenTV live account display")
    parser.add_argument("--config", required=True, help="Credential-free account metadata")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--output", help="Write a normalized snapshot rather than stdout")
    parser.add_argument("--state-dir", default=".runtime")
    parser.add_argument("--restore-display", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if config.get("font"):
        os.environ["TOKEN_TV_FONT"] = config["font"]
    state_dir = Path(args.state_dir)
    device = open_display(config["device_url"]) if config.get("device_url") else None
    backup_path = state_dir / "display-original.json"
    if args.restore_display:
        if not device or not backup_path.is_file():
            parser.error("A display and its original state backup are required")
        original = json.loads(backup_path.read_text())
        try:
            device.check_backup(original)
        except BackupMismatch as error:
            raise SystemExit(mismatch_message(error, backup_path))
        device.restore(original)
        print("Original display selection restored.")
        return
    store = UsageStore(config["accounts"])
    preferences = DisplayPreferences(args.config, config)
    interval = max(60, int(config.get("poll_seconds", 300)))
    # A minute-level countdown makes every frame distinct, so the byte dedupe never fires while a
    # short window leads. Bucket the countdown, and cap the writes the loop may spend per hour.
    quantum = max(0, int(config.get("countdown_seconds", 900)))
    budget = max(0, int(config.get("max_writes_per_hour", 6)))
    # Re-assert the photo theme periodically: a reboot or a visit to the clock's own web UI drops
    # it back to its theme, and `activate` only ran when the file name changed, i.e. once.
    reassert_after = max(1, int(config.get("reassert_cycles", 12)))
    stopping = threading.Event()
    original = None
    active_file = None
    last_upload = None
    last_write = None
    last_style = None
    cycles = 0

    def cycle(refresh=True):
        nonlocal original, active_file, last_upload, last_write, last_style, cycles
        if refresh:
            store.refresh()
        snapshot = store.snapshot()
        style = preferences.snapshot()['style']
        if not args.once and device:
            receipts = []
            phase = "backup"
            try:
                if original is None:
                    original = load_original(device, backup_path)
                phase = "upload"
                now = time.time()
                cycles += 1
                image, encoded = render_frame(snapshot, 0, style, quantum,
                                              LIMITS[FILES[0]][0] if style != 'space' else LIMITS[FILES[1]][0])
                name = FILES[1] if image[:4] == b"GIF8" else FILES[0]
                digest = hashlib.sha256(image).hexdigest()
                # Re-assert the theme periodically, not only when the file name changes: a reboot
                # or a visit to the clock's own web UI drops it back to its own theme.
                due = active_file != name or cycles % reassert_after == 0
                lost = False
                if due:
                    phase = "activate"
                    # `activate` re-reads the clock and only writes what is wrong, so in steady
                    # state this is a handful of GETs. Ultra clocks report no file list.
                    files = device.capture().get("files")
                    lost = files is not None and not any(f["name"] == name for f in files)
                    if lost:
                        last_upload = None  # the clock lost it; the dedupe must not hide that
                phase = "upload"
                throttled = False
                # A style the user just applied, and a clock showing nothing, both bypass the
                # budget: the cap exists to spare flash, not to withhold the frame that matters.
                forced = not refresh or lost or style != last_style
                if (name, digest) != last_upload:  # identical frames are not rewritten to flash
                    if forced or may_write(last_write, now, budget):
                        # Written only on upload, so the local copy stays a record of what the
                        # clock is actually showing.
                        (state_dir / name).write_bytes(image)
                        receipts.append(dict(device.upload(name, image), sha256=digest, **encoded))
                        last_upload, last_write, last_style = (name, digest), now, style
                    else:
                        throttled = True  # last_upload is left alone, so the newest frame retries
                if due:
                    phase = "activate"
                    device.activate(original, name)
                    active_file = name
                write_json(state_dir / "display-receipt.json",
                           {"at": int(time.time()), "style": style, "uploads": receipts,
                            "throttled": throttled, "reasserted": due, "encoded": encoded})
                # Throttling is not a delivery failure: the style is already on the clock and the
                # newest frame follows shortly. Reporting it would flash a false error.
                if not throttled:
                    preferences.delivered(style, True)
            except (OSError, ValueError, KeyError) as error:
                if isinstance(error, BackupMismatch):
                    print(mismatch_message(error, backup_path), flush=True)
                write_json(state_dir / "display-receipt.json", {"at": int(time.time()), "style": style, "status": "error", "phase": phase,
                           "uploads": receipts, "http_status": getattr(error, "code", None)})
                preferences.delivered(style, False)
        snapshot['display'] = preferences.snapshot()
        write_json(state_dir / "snapshot.json", snapshot)
        if args.output:
            write_json(args.output, snapshot)

    if args.once:
        cycle()
        if not args.output:
            print(json.dumps(store.snapshot(), ensure_ascii=False, separators=(",", ":")))
        return

    def poll():
        next_refresh = 0
        while not stopping.is_set():
            preferences.changed.clear()
            refresh = time.monotonic() >= next_refresh
            try:
                cycle(refresh)
            except Exception:
                # Never serialize exception text, which may include auth material.
                print("TokenTV poll failed; retained previous snapshot.", flush=True)
            if refresh:
                next_refresh = time.monotonic() + interval
            preferences.changed.wait(max(0, next_refresh - time.monotonic()))

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        with ThreadingHTTPServer((args.host, args.port), handler(store, preferences, quantum)) as server:
            print(f"TokenTV live listening at http://{args.host}:{args.port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        preferences.changed.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
