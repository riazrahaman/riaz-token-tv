"""Confirmed SD_PRO photo API, plus the SmallTV-Ultra image API. Never flash or delete existing photographs."""
import json
import uuid
from http.client import HTTPException
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


FILES = ("tokentv.jpg", "tokentv.gif")
# The SD_PRO slideshow re-reads the album on rotation, never while a single photo is enabled, so
# a one-file album froze the panel between content writes even as fresh frames uploaded. JPEG
# frames now live in two identical slots; the rotation between them re-reads both every cycle.
# `tokentv-b.jpg` is our own second frame, not a user photo, and is never deleted either.
ROTATION_FILES = ("tokentv.jpg", "tokentv-b.jpg")
# The album page accepts GIFs up to 240×240; keep both well under the free space.
LIMITS = {"tokentv.jpg": (60000, "image/jpeg"), "tokentv-b.jpg": (60000, "image/jpeg"),
          "tokentv.gif": (400000, "image/gif")}
LEGACY_FILES = ("tokentv-c.jpg", "tokentv-m.jpg")
MODEL_NAMES = {"sdpro": "an SD_PRO", "ultra": "a SmallTV-Ultra"}
# The clock writes settings with one name and reports them under another, so a read-and-compare has
# to map between the two. Only the display settings are listed: Wi-Fi, the weather key and the
# colours are deliberately absent, because nothing here should be able to write them.
SETTING_READS = {"lcd_brightness": "brightness", "nightmode": "nightmode", "starttime": "starttime",
                 "stoptime": "stoptime", "nightbrightness": "nightbrightness"}
SETTING_RANGE = {"lcd_brightness": (2, 99), "nightbrightness": (2, 99), "nightmode": (0, 1),
                 "starttime": (0, 23), "stoptime": (0, 23)}


def _as_int(value):
    """The clock reports some settings as numbers and some as booleans."""
    if isinstance(value, bool):
        return int(value)
    return value if isinstance(value, int) else None


def may_write(last_write, now, budget):
    """True when enough time has passed since `last_write` to spend another flash write.

    Spacing, not a quota: counting writes inside a trailing hour lets a busy hour spend the whole
    allowance in its first minutes and then freeze the clock until the window slides, which is
    exactly when the reading matters. Spacing keeps the frame at most one gap old. `budget` is
    writes per hour; 0 means no cap."""
    if not budget:
        return True
    return last_write is None or (now - last_write) >= 3600 / budget


def lost_rotation(present, name=FILES[0]):
    """True when the clock is missing our frame or, for a JPEG, its rotation twin.

    Reassert-time detection against the album's file list, so the byte dedupe cannot keep hiding
    a lost slot. `present` reads as unknown when the album is unreadable (an Ultra reports none),
    and unknown is never treated as lost."""
    if not isinstance(present, list):
        return False
    names = {f["name"] for f in present if isinstance(f, dict) and isinstance(f.get("name"), str)}
    return name not in names or (name == FILES[0] and ROTATION_FILES[1] not in names)


class SettingsUnsupported(Exception):
    """This clock profile has no equivalent of the setting being asked for."""


class BackupMismatch(ValueError):
    """The saved original state belongs to a different kind of clock."""


class PhotoDisplay:
    MODEL = "sdpro"

    def __init__(self, base_url):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.path not in ("", "/"):
            raise ValueError("A plain local display URL is required")
        self.base_url = base_url.rstrip("/")

    def request(self, path, data=None, headers=None):
        with urlopen(Request(self.base_url + path, data=data, headers=headers or {}), timeout=20) as response:
            body = response.read()
            return response.status, body

    def identity(self):
        """The clock's MAC address, its one stable name.

        An address can move when the router hands out a new lease, and these clocks are
        indistinguishable by colour over HTTP, so publishing to the wrong one is otherwise easy and
        silent."""
        _, raw = self.request("/mac")
        return raw.decode("ascii", "replace").strip()

    def settings(self):
        """The clock's own settings, as it reports them."""
        _, raw = self.request("/config")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    def apply_settings(self, desired, current=None):
        """Write only the settings that differ, and report what was written.

        These are stored in the ESP8266's emulated EEPROM: a single 4 KB sector, rewritten whole on
        every commit, with no wear levelling, and it holds the Wi-Fi credentials too. So this is for
        settings that change occasionally and never for anything resembling a data feed — hence the
        read-and-compare rather than writing the same value every cycle."""
        current = self.settings() if current is None else current
        written = {}
        for key, value in desired.items():
            if key not in SETTING_READS:
                raise ValueError("Unsupported clock setting")
            value = int(value)
            low, high = SETTING_RANGE[key]
            if not low <= value <= high:
                raise ValueError("Clock setting out of range")
            if _as_int(current.get(SETTING_READS[key])) == value:
                continue  # already what we want; writing it again would only wear the sector
            self.request("/api/set?" + urlencode({"key": key, "value": value}, quote_via=quote))
            written[key] = value
        return written

    def check_backup(self, original):
        # SD_PRO backups predate the model key, so a missing key means SD_PRO.
        saved = original.get("model", "sdpro") if isinstance(original, dict) else None
        if saved != self.MODEL:
            raise BackupMismatch(f"The saved display backup is for {MODEL_NAMES.get(saved, 'an unknown')} clock, "
                                 f"but this clock was detected as {MODEL_NAMES[self.MODEL]}.")

    def capture(self):
        _, raw = self.request("/theme/list")
        themes = json.loads(raw)
        _, raw = self.request("/photo/list")
        photos = json.loads(raw)
        if not any(t["id"] == 2 for t in themes["themes"]):
            raise ValueError("The confirmed photo theme is missing")
        return {"themes": themes["themes"], "theme_interval": themes["interval"],
                "files": [{"name": f["name"], "enabled": f["enabled"]} for f in photos["files"]],
                "photo_interval": photos["interval"]}

    def upload(self, name, image):
        limit, mime = LIMITS.get(name, (0, ""))
        if not image or len(image) > limit:
            raise ValueError("Unexpected display image")
        boundary = "TokenTV" + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode() + image + f"\r\n--{boundary}--\r\n".encode()
        status, _ = self.request("/photo/upload", body,
                                 {"Content-Type": "multipart/form-data; boundary=" + boundary})
        return {"file": name, "status": status, "bytes": len(image)}

    def upload_rotation(self, image, name=FILES[0]):
        """A fresh frame uploaded to the main file and, for JPEG frames, the second slot too.

        The SD_PRO slideshow only re-reads the album when files change state, so one enabled photo
        freezes the panel between content writes; two enabled files make it re-read on every
        rotation. Both uploads share one write-budget decision upstream. The firmware auto-enables
        a newly uploaded file: nothing here toggles after an upload (that request is the known
        unreliable one), and no photo is ever deleted."""
        receipts = [self.upload(name, image)]
        if name == FILES[0]:
            receipts.append(self.upload(ROTATION_FILES[1], image))
        return receipts

    def toggle(self, category, key, value, enabled):
        self.request("/" + category + "/toggle?" + urlencode({key: value, "state": int(enabled)}, quote_via=quote))

    def activate(self, original, name=FILES[0]):
        # Show only our frames — both rotation slots for a JPEG frame, one for the GIF — then
        # select the photo theme; the user's own photos are only disabled, never deleted, and a
        # frame already sitting enabled and alone costs nothing.
        current = self.capture()
        enabled = {f['name']: f['enabled'] for f in current['files']}
        wanted = ROTATION_FILES if name == FILES[0] else (name,)
        for wanted_name in wanted:
            if wanted_name in enabled and not enabled[wanted_name]:  # truthy check: a raw `0` sleeps too
                self.toggle("photo", "name", wanted_name, True)
        for f in current['files']:
            if f["name"] not in wanted and f['enabled']:
                self.toggle("photo", "name", f["name"], False)
        if current['photo_interval'] != 10:
            self.request("/photo/interval?val=10")
        if not any(t['id'] == 2 and t['enabled'] for t in current['themes']):
            self.toggle("theme", "id", 2, True)
        for t in current["themes"]:
            if t["id"] != 2 and t['enabled']:
                self.toggle("theme", "id", t["id"], False)

    def restore(self, original):
        for f in original["files"]:
            self.toggle("photo", "name", f["name"], f["enabled"])
        _, raw = self.request("/photo/list")
        present = {f["name"] for f in json.loads(raw)["files"]}
        for name in set(FILES) | set(ROTATION_FILES) | set(LEGACY_FILES):
            if name in present and not any(f["name"] == name for f in original["files"]):
                self.toggle("photo", "name", name, False)
        self.request("/photo/interval?val=" + str(original["photo_interval"]))
        self.request("/theme/interval?val=" + str(original["theme_interval"]))
        for enabled in (True, False):
            for t in original["themes"]:
                if t["enabled"] == enabled:
                    self.toggle("theme", "id", t["id"], enabled)


class UltraDisplay(PhotoDisplay):
    """SmallTV-Ultra (Ultra-V9) image API, read from the clock's own image.html and settings.html."""
    MODEL = "ultra"
    PHOTO_THEME = 3

    def identity(self):
        raise SettingsUnsupported("This clock does not report a MAC address")

    def settings(self):
        raise SettingsUnsupported("This clock does not expose /config")

    def apply_settings(self, desired, current=None):
        # The Ultra firmware writes settings through /set, not /api/set, and the key names differ.
        raise SettingsUnsupported("Brightness and night mode are not supported on this clock")

    def capture(self):
        _, raw = self.request("/app.json")
        theme = json.loads(raw)["theme"]
        _, raw = self.request("/album.json")
        album = json.loads(raw)
        return {"model": self.MODEL, "theme": theme, "autoplay": album["autoplay"], "i_i": album["i_i"]}

    def upload(self, name, image):
        limit, mime = LIMITS.get(name, (0, ""))
        if not image or len(image) > limit:
            raise ValueError("Unexpected display image")
        boundary = "TokenTV" + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode() + image + f"\r\n--{boundary}--\r\n".encode()
        status, _ = self.request("/doUpload?dir=/image/", body,
                                 {"Content-Type": "multipart/form-data; boundary=" + boundary})
        return {"file": name, "status": status, "bytes": len(image)}

    def upload_rotation(self, image, name=FILES[0]):
        # The Ultra shows one hard-set image; there is no album rotation to refresh the panel.
        return [self.upload(name, image)]

    def activate(self, original, name=FILES[0]):
        # Stop the slideshow so the user's own photos never rotate in, select the album theme, show ours.
        # The image is chosen after the theme, as geekmagic-hacs does on the same firmware.
        current = self.capture()
        if current["autoplay"]:
            self.request(f"/set?i_i={int(current['i_i'])}&autoplay=0")
        if current["theme"] != self.PHOTO_THEME:
            self.request(f"/set?theme={self.PHOTO_THEME}")
        self.request("/set?img=" + quote("/image/" + name, safe=""))

    def restore(self, original):
        # There is no known way to read the previously shown image, so only the album settings and theme return.
        self.request(f"/set?i_i={int(original['i_i'])}&autoplay={int(original['autoplay'])}")
        self.request(f"/set?theme={int(original['theme'])}")


def open_display(base_url):
    display = PhotoDisplay(base_url)
    try:
        _, raw = display.request("/v.json")
        model = json.loads(raw).get("m")
    except (OSError, ValueError, AttributeError, HTTPException):
        return display  # model checked once at start; an Ultra offline at launch falls back to SD_PRO until restart
    return UltraDisplay(base_url) if isinstance(model, str) and model.startswith("SmallTV-Ultra") else display
