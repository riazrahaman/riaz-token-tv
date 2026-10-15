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

from token_tv.device import (FILES, LIMITS, ROTATION_FILES, STOCKS_FILES, USAGE_FILE,
                             BackupMismatch, SettingsUnsupported, lost_rotation, may_write,
                             open_display)
from token_tv.catalog import payload as theme_payload
from token_tv.display import (ANIMATED_STYLES, STOCK_STYLES, STYLES, data_digest, render_frame,
                              render_page, watch_payload)
from token_tv.combo import market_rows
from token_tv.web_assets import HTML, ASSETS, asset
from token_tv.sources import load_config
from token_tv.state import UsageStore
from token_tv.history import append_history, read_history


def wanted_settings(config):
    """The clock settings the config asks for, keyed as the clock writes them.

    `night` is a small object rather than five flat keys because the window and its brightness only
    make sense together. Anything absent is left exactly as the clock has it."""
    wanted = {}
    if config.get("brightness") is not None:
        wanted["lcd_brightness"] = int(config["brightness"])
    night = config.get("night")
    if isinstance(night, dict):
        if night.get("enabled") is not None:
            wanted["nightmode"] = 1 if night["enabled"] else 0
        for key, name in (("start", "starttime"), ("stop", "stoptime"), ("brightness", "nightbrightness")):
            if night.get(key) is not None:
                wanted[name] = int(night[key])
    return wanted


def read_feed(path):
    """The stock feeder's own snapshot, or None.

    The feeder runs with no clock configured, so it only writes this file and the publisher here
    stays the single owner of the device. Any problem reading it leaves the market section off the
    face: a missing feed must never stop the usage face from publishing."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
        # The stocks feeder's own config file; the stocks endpoints rewrite it, and the feeder's
        # mtime reload picks the change up next cycle.
        self.stocks_path = config.get('stocks_config') or None
        self.lock = threading.Lock()
        self.changed = threading.Event()

    def snapshot(self):
        with self.lock:
            state = {'style': self.style, 'applied_style': self.applied_style,
                     'status': self.status, 'styles': list(STYLES)}
            state['stocks'] = self.stocks_state()
            return state

    def stocks_state(self):
        """The stocks feeder's mode and tickers, or None when no stock feed is configured."""
        if not self.stocks_path:
            return None
        try:
            data = json.loads(Path(self.stocks_path).expanduser().read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        mode = data.get('mode')
        symbols = data.get('symbols')
        from token_tv.stocks import MAX_SYMBOLS
        count = data.get('movers_count')
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_SYMBOLS:
            count = 2  # anything unusable falls back to the default, not to the raw value
        return {'mode': mode if mode in ('watchlist', 'movers') else 'watchlist',
                'symbols': [symbol for symbol in symbols or [] if isinstance(symbol, str)],
                'movers_count': count}

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

    def set_stocks_mode(self, mode):
        if mode not in ('watchlist', 'movers'):
            raise ValueError('Unknown stocks mode')
        self.set_stocks_options({'mode': mode})

    def set_stocks_symbols(self, symbols):
        from token_tv.stocks import MAX_SYMBOLS, SYMBOL_PATTERN
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
            raise ValueError(f'symbols must be a list of 1 to {MAX_SYMBOLS} tickers')
        cleaned = []
        for symbol in symbols:
            if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol.strip().upper()):
                raise ValueError(f'{symbol!r} is not an uppercase ticker')
            cleaned.append(symbol.strip().upper())
        if len(set(cleaned)) != len(cleaned):
            raise ValueError('A ticker is listed twice')
        self.set_stocks_options({'symbols': cleaned})

    def set_stocks_options(self, change):
        """Merge a stocks change into the feeder's own config, written atomically (tmp + replace)."""
        if not self.stocks_path:
            raise StockFeedMissing('The live config has no stocks_config path')
        path = Path(self.stocks_path).expanduser()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            raise StockFeedMissing('The stock config cannot be read') from None
        if not isinstance(data, dict):
            raise StockFeedMissing('The stock config is not an object')
        data.update(change)
        write_json(path, data)
        self.changed.set()

    def delivered(self, style, success):
        with self.lock:
            if success:
                self.applied_style = style
            self.status = ('queued' if style != self.style else 'ok' if success else 'error')


class StockFeedMissing(Exception):
    """POST to a stocks endpoint while the live config has no stock feed."""


def handler(store, preferences=None, quantum=0, state_dir=None):
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
            elif path == "/history":
                hist_path = Path(state_dir) / "history.jsonl" if state_dir else None
                body = json.dumps(read_history(hist_path) if hist_path else [], ensure_ascii=False, separators=(",", ":")).encode()
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
            path = urlparse(self.path).path
            if path not in ('/display/style', '/stocks/mode', '/stocks/symbols'):
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
                if not isinstance(data, dict):
                    raise ValueError('Invalid JSON request')
                if path == '/display/style':
                    if set(data) != {'style'} or data['style'] not in STYLES:
                        raise ValueError('Choose a supported display style')
                elif path == '/stocks/mode':
                    if set(data) != {'mode'} or data['mode'] not in ('watchlist', 'movers'):
                        raise ValueError('stocks.mode expects {"mode":"watchlist"} or {"mode":"movers"}')
                else:
                    symbols = data['symbols']
                    from token_tv.stocks import MAX_SYMBOLS, SYMBOL_PATTERN
                    if (set(data) != {'symbols'} or not isinstance(symbols, list)
                            or not 1 <= len(symbols) <= MAX_SYMBOLS
                            or any(not isinstance(s, str) for s in symbols)
                            or len(set(s.strip().upper() for s in symbols)) != len(symbols)):
                        raise ValueError(f'stocks.symbols expects 1 to {MAX_SYMBOLS} unique tickers')
                    for symbol in symbols:
                        if not SYMBOL_PATTERN.fullmatch(symbol.strip().upper()):
                            raise ValueError(f'{symbol!r} is not an uppercase ticker')
            except (ValueError, UnicodeError, KeyError):
                self.send_error(400,
                                'Choose a supported display style' if path == '/display/style' else
                                'stocks.mode expects {"mode":"watchlist"} or {"mode":"movers"}'
                                if path == '/stocks/mode' else
                                'stocks.symbols expects 1 to 4 unique uppercase tickers')
                return
            except OSError:
                self.send_error(500, 'Could not save display choice')
                return
            try:
                if path == '/display/style':
                    preferences.set_style(data['style'])
                elif path == '/stocks/mode':
                    preferences.set_stocks_mode(data['mode'])
                else:
                    preferences.set_stocks_symbols([s.strip().upper() for s in data['symbols']])
            except StockFeedMissing as error:
                self.send_error(409, str(error) or 'No stock feed is configured')
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
    # Identity before anything is written: a lease can move an address to a different clock, and
    # these are indistinguishable over HTTP, so publishing to the wrong one is otherwise silent.
    expected_mac = str(config.get("device_mac") or "").strip().upper()
    if device and expected_mac:
        try:
            found = device.identity().upper()
        except (OSError, ValueError, SettingsUnsupported) as error:
            raise SystemExit(f"Could not read the clock's MAC address to confirm it: {type(error).__name__}")
        if found != expected_mac:
            raise SystemExit(f"The clock at this address reports {found}, but the config expects "
                             f"{expected_mac}. Refusing to publish to a clock that may not be yours.")
    # Brightness and the night window are stored in the clock's EEPROM, so they are written once at
    # startup and only where they already differ.
    if device:
        wanted = wanted_settings(config)
        if wanted:
            try:
                written = device.apply_settings(wanted)
                if written:
                    print("Clock settings updated: " + ", ".join(sorted(written)), flush=True)
            except SettingsUnsupported as error:
                print(f"Clock settings skipped: {error}.", flush=True)
            except (OSError, ValueError) as error:
                print(f"Clock settings could not be applied ({type(error).__name__}).", flush=True)
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
    # The combo face reads the stock feeder's snapshot; prices move faster than poll_seconds, so a
    # short render tick re-draws without re-reading any provider.
    feed_path = config.get("stocks_snapshot")
    render_seconds = max(0, int(config.get("render_seconds", 30 if feed_path else 0)))
    stopping = threading.Event()
    original = None
    active_file = None
    last_upload = {}  # slot name → last uploaded sha256; the whole-slot dedupe
    last_write = None
    last_style = None
    last_data = None
    cycles = 0

    def cycle(refresh=True, user=False):
        nonlocal original, active_file, last_upload, last_write, last_style, last_data, cycles
        if refresh:
            store.refresh()
        snapshot = store.snapshot()
        feed = read_feed(feed_path)
        if feed is not None:
            snapshot['stocks'] = feed
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
                                              LIMITS[FILES[0]][0] if style not in ANIMATED_STYLES else LIMITS[FILES[1]][0])
                name = FILES[1] if image[:4] == b"GIF8" else FILES[0]
                digest = hashlib.sha256(image).hexdigest()
                # The wanted rotation slots, known before any reassert check: all three while a
                # stock style alternates (closed or open — closed fills the third with usage), the
                # two stock slots for a non-stock JPEG style (0.4.7 behaviour; the usage slot is
                # not ours to want), just the GIF for the animated one.
                if name == FILES[1]:
                    wanted = (name,)
                elif style in STOCK_STYLES:
                    wanted = ROTATION_FILES
                else:
                    wanted = STOCKS_FILES
                # Re-assert the theme periodically, not only when the file name changes: a reboot
                # or a visit to the clock's own web UI drops it back to its own theme.
                due = active_file != name or cycles % reassert_after == 0
                lost = False
                if due:
                    phase = "activate"
                    # `activate` re-reads the clock and only writes what is wrong, so in steady
                    # state this is a handful of GETs. Ultra clocks report no file list.
                    files = device.capture().get("files")
                    if lost_rotation(files, name, wanted):  # any wanted rotation slot is gone
                        lost = True
                        last_upload = {}  # the clock lost a frame; the dedupe must not hide that
                phase = "upload"
                throttled = False
                # The budget may defer a countdown that merely ticked, but never a real change:
                # a new reading, a style you applied, or a clock showing nothing all go at once.
                # The cap exists to spare flash, not to withhold the frame that matters.
                changed = data_digest(snapshot)
                data_changed = changed != last_data
                forced = user or lost or style != last_style or data_changed
                if name == FILES[1]:  # the animated GIF keeps its single hard-set identity
                    events = [(name, image, digest, encoded)]
                elif style in STOCK_STYLES:
                    # The alternation the user asked for: the stocks face in the two stock slots
                    # and the usage face in the third, so the slideshow's 10-second interval shows
                    # stocks for 20 seconds, then usage for 10, then repeats with fresh frames.
                    # A closed, missing or stale market (combo conventions) rests the clock on a
                    # continuous usage screen by filling all three slots with the usage frame —
                    # the usage-only choice for the stocks face is the user's stated intent.
                    alternation = feed is not None and market_rows(snapshot) is not None
                    usage_image, usage_encoded = render_frame(
                        {key: value for key, value in snapshot.items() if key != 'stocks'},
                        0, 'pixel', quantum, LIMITS[FILES[0]][0])
                    usage_digest = hashlib.sha256(usage_image).hexdigest()
                    slot_image, slot_encoded, slot_digest = ((image, encoded, digest) if alternation
                                                             else (usage_image, usage_encoded, usage_digest))
                    events = [(ROTATION_FILES[0], slot_image, slot_digest, slot_encoded),
                              (ROTATION_FILES[1], slot_image, slot_digest, slot_encoded),
                              (USAGE_FILE, usage_image, usage_digest, usage_encoded)]
                else:
                    # A style that draws no stock board keeps 0.4.7's behavior: the chosen face in
                    # the two stock slots, and the usage slot is left alone (not uploaded, not
                    # wanted — it is disabled on the next reassert rather than shown stale).
                    events = [(ROTATION_FILES[0], image, digest, encoded),
                              (ROTATION_FILES[1], image, digest, encoded)]
                pending = [event for event in events if event[2] != (last_upload or {}).get(event[0])]
                if pending:  # only the slots whose bytes changed are rewritten
                    if forced or may_write(last_write, now, budget):
                        # The local mirrors are written as the publish event starts, just before the
                        # upload requests — a failed upload leaves a mirror ahead of the clock, which
                        # the receipt's error record shows.
                        for slot, frame, _, _ in pending:
                            (state_dir / slot).write_bytes(frame)
                        uploads = device.upload_rotation([(slot, frame) for slot, frame, _, _ in pending])
                        for event, uploaded in zip(pending, uploads):
                            receipts.append(dict(uploaded, sha256=event[2], **event[3]))
                        last_write, last_style, last_data = now, style, changed
                        last_upload = (last_upload or {}).copy()
                        last_upload.update({slot: dig for slot, _, dig, _ in pending})
                    else:
                        throttled = True  # last_upload is left alone, so the newest frame retries
                if due:
                    phase = "activate"
                    device.activate(original, name, wanted)
                    active_file = name
                write_json(state_dir / "display-receipt.json",
                           {"at": int(time.time()), "style": style, "uploads": receipts,
                            "throttled": throttled, "reasserted": due, "encoded": encoded,
                            "data_changed": data_changed})
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
        append_history(state_dir / "history.jsonl", snapshot)
        if args.output:
            write_json(args.output, snapshot)

    if args.once:
        cycle()
        if not args.output:
            print(json.dumps(store.snapshot(), ensure_ascii=False, separators=(",", ":")))
        return

    def poll():
        next_refresh, user = 0, False
        while not stopping.is_set():
            preferences.changed.clear()
            refresh = time.monotonic() >= next_refresh
            try:
                cycle(refresh, user)
            except Exception:
                # Never serialize exception text, which may include auth material.
                print("TokenTV poll failed; retained previous snapshot.", flush=True)
            if refresh:
                next_refresh = time.monotonic() + interval
            wait = max(0, next_refresh - time.monotonic())
            if render_seconds:  # re-draw the feed without re-reading any provider
                wait = min(wait, render_seconds)
            # wait() reports whether the dashboard asked for this pass, which bypasses the budget.
            user = preferences.changed.wait(wait)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        with ThreadingHTTPServer((args.host, args.port), handler(store, preferences, quantum, state_dir=state_dir)) as server:
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
