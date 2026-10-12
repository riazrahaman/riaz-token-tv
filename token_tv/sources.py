"""Use credentials in their owning CLI homes; export only approved fields."""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from token_tv.usage import normalize_claude, normalize_ollama, normalize_antigravity, timestamp, window
from token_tv.display import STYLES

AUTH_WORDS = ("auth", "login", "sign in", "token")


def exe(name):
    """Full path of a CLI. On Windows npm installs CLIs as .cmd shims that a bare name can't start."""
    return shutil.which(name) or name


class SourceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def scoped_env(provider, root):
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                "XAI_API_KEY", "CLAUDECODE"):
        env.pop(key, None)
    variable = {"claude": "CLAUDE_CONFIG_DIR",
                "antigravity": "ANTIGRAVITY_EXECUTABLE_DATA_DIR"}[provider]
    env[variable] = str(root)
    if provider == "claude":
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(root)
    return env


def load_config(path):
    data = json.loads(Path(path).read_text())
    if set(data) - {"accounts", "device_url", "poll_seconds", "font", "display_style"}:
        raise ValueError("Unsupported configuration field")
    if data.get('display_style', 'pixel') not in STYLES:
        raise ValueError('Unknown display style')
    seen = set()
    allowed = {"key", "alias", "provider", "source_home", "email", "snapshot_file", "fallback_snapshot_file",
               "refresh_with_cli", "ollama_allowance_usd"}
    for account in data.get("accounts", []):
        if set(account) - allowed:
            raise ValueError("Only credential-free account metadata is allowed")
        if account.get("provider") not in ("claude", "ollama", "antigravity"):
            raise ValueError("Unknown provider")
        key = account.get("key", "")
        if not re.fullmatch(r"[a-z0-9_-]{1,48}", key) or key in seen:
            raise ValueError("Account keys must be unique")
        if not isinstance(account.get("alias"), str) or not 1 <= len(account["alias"]) <= 32:
            raise ValueError("Account alias is required")
        if not account.get("email") and account.get("provider") not in ("ollama", "antigravity"):
            raise ValueError("Expected account identity is required")
        if "ollama_allowance_usd" in account:
            if account.get("provider") != "ollama":
                raise ValueError("ollama_allowance_usd is only for ollama accounts")
            allowance = account["ollama_allowance_usd"]
            if (isinstance(allowance, bool) or not isinstance(allowance, (int, float))
                    or not math.isfinite(allowance) or allowance <= 0):
                raise ValueError("ollama_allowance_usd must be a positive number")
        seen.add(key)
    if not seen:
        raise ValueError("At least one account is required")
    return data


def json_get(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=20) as response:
        return json.load(response)


def keychain_services(root):
    """macOS Keychain item names Claude Code uses for a CLI home (read from Claude Code 2.1.281)."""
    home = str(root)
    services = ["Claude Code-credentials-" + hashlib.sha256(unicodedata.normalize("NFC", home).encode()).hexdigest()[:8]]
    if root == Path("~/.claude").expanduser():
        services.append("Claude Code-credentials")
    return services


def claude_token(root):
    """Access token from the CLI home's file, or on macOS from the login Keychain (read only)."""
    path = root / ".credentials.json"
    if path.is_file() or sys.platform != "darwin":
        return json.loads(path.read_text())["claudeAiOauth"]["accessToken"]
    user = os.environ.get("USER") or "claude-code-user"
    for service in keychain_services(root):
        result = subprocess.run(["security", "find-generic-password", "-a", user, "-w", "-s", service],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)["claudeAiOauth"]["accessToken"]
    raise SourceError("auth_required")


def claude_payload(account):
    root = Path(account["source_home"]).expanduser()
    token = claude_token(root)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
               "User-Agent": "claude-code/2.1.287", "anthropic-beta": "oauth-2025-04-20"}
    try:
        profile = json_get("https://api.anthropic.com/api/oauth/profile", headers)
        usage = json_get("https://api.anthropic.com/api/oauth/usage", headers)
    except HTTPError as error:
        if error.code != 401 or not account.get("refresh_with_cli"):
            raise
        # The CLI owns refresh/rotation. No manual refresh-token writes.
        env = scoped_env("claude", root)
        subprocess.run([exe("claude"), "-p", "Reply only OK.", "--tools", "",
                        "--no-session-persistence", "--max-turns", "1"],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        token = claude_token(root)
        headers["Authorization"] = "Bearer " + token
        profile = json_get("https://api.anthropic.com/api/oauth/profile", headers)
        usage = json_get("https://api.anthropic.com/api/oauth/usage", headers)
    return profile, usage


def email_from_profile(profile):
    for item in (profile.get("account") or {}, profile):
        for key in ("emailAddress", "email_address", "email"):
            if isinstance(item.get(key), str):
                return item[key]
    return None


def identity_matches(expected, actual):
    return bool(actual and expected.strip().casefold() == actual.strip().casefold())


def ollama_token():
    """Ollama Cloud API key from the environment, or on macOS from the login Keychain (read only).

    The key is never written to the TokenTV config and never printed; only usage numbers leave
    this process. To store it in the Keychain: security add-generic-password -s token-tv-ollama -w
    """
    token = os.environ.get("OLLAMA_API_KEY")
    if token and token.strip():
        return token.strip()
    if sys.platform == "darwin":
        result = subprocess.run(["security", "find-generic-password", "-s", "token-tv-ollama", "-w"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise SourceError("auth_required")


def ollama_payload(account):
    """Ollama Cloud's own usage API. It reports spend only, with no email and no plan limit."""
    token = ollama_token()
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    return json_get("https://ollama.com/api/usage?range=7d", headers)


def antigravity_payload(account):
    """Antigravity's own local `/usage` command, printed as JSON. No model call, so no quota is spent."""
    root = Path(account["source_home"]).expanduser()
    env = scoped_env("antigravity", root)
    command = os.environ.get("TOKEN_TV_ANTIGRAVITY_BIN", "agy")
    try:
        result = subprocess.run([exe(command), "-p", "/usage", "--output-format", "json"],
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=30)
    except FileNotFoundError:
        raise SourceError("cli_unavailable") from None
    except subprocess.TimeoutExpired:
        raise SourceError("timeout") from None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        text = (result.stdout + result.stderr).lower()
        raise SourceError("auth_required" if any(word in text for word in AUTH_WORDS) else "error")
    return payload


def logged_in_email(provider, home):
    """The email of the account already signed in to this CLI home, or None. Prints nothing secret."""
    root = Path(home).expanduser()
    try:
        if provider == "claude":
            headers = {"Authorization": "Bearer " + claude_token(root), "Accept": "application/json",
                       "User-Agent": "claude-code/2.1.287", "anthropic-beta": "oauth-2025-04-20"}
            return email_from_profile(json_get("https://api.anthropic.com/api/oauth/profile", headers))
        return None  # Ollama and Antigravity report no email, so setup can't detect one.
    except (SourceError, OSError, ValueError, KeyError, TypeError, HTTPError, subprocess.SubprocessError):
        return None


def antigravity_identity(account):
    """Antigravity's CLI keeps its login in the OS keychain and reports no email, so there is none to check."""
    return None


def ollama_identity(account):
    """Ollama Cloud's usage API reports no email, so there is none to check."""
    return None


def imported_row(account):
    data = json.loads(Path(account["snapshot_file"]).expanduser().read_text())
    row = data["accounts"][account["key"]]
    if row.get("provider") != account["provider"] or row.get("alias") != account["alias"]:
        raise SourceError("identity_mismatch")
    fetched = timestamp(row.get("fetched_at"))
    if fetched is None or fetched > time.time() + 60:
        raise SourceError("invalid_snapshot")
    allowed_status = {"ok", "quota_unavailable", "auth_required", "identity_mismatch", "error", "rate_limited", "stale"}
    if row.get("status") not in allowed_status:
        raise SourceError("invalid_snapshot")
    clean = {"key": account["key"], "alias": account["alias"], "provider": account["provider"],
             "status": row["status"], "windows": [], "fetched_at": fetched,
             "identity_verified": row.get("identity_verified") is True, "source": "laptop"}
    last_success = timestamp(row.get("last_success_at"))
    if last_success and last_success <= fetched:
        clean["last_success_at"] = last_success
    for value in row.get("windows", []):
        if value.get("label") not in {"5H", "WEEK", "WINDOW", "BUDGET"}:
            continue
        item = window(value["label"], value.get("used_percent"), value.get("resets_at"),
                      value.get("duration_minutes"))
        if item:
            clean["windows"].append(item)
    if time.time() - fetched > 900:
        clean["status"] = "stale"
        clean["error_code"] = "laptop_offline"
    return clean


def fetch_account(account):
    row = {"key": account["key"], "alias": account["alias"], "provider": account["provider"],
           "status": "auth_required", "windows": [], "fetched_at": time.time(),
           "identity_verified": False, "source": "mini"}
    try:
        if account.get("snapshot_file"):
            return imported_row(account)
        if account["provider"] == "claude":
            profile, data = claude_payload(account)
            if not identity_matches(account["email"], email_from_profile(profile)):
                raise SourceError("identity_mismatch")
            row["windows"] = normalize_claude(data)
            row["identity_verified"] = True
        elif account["provider"] == "ollama":
            # Ollama's usage API reports no email, so this source is always unverified.
            row["windows"] = normalize_ollama(ollama_payload(account), account.get("ollama_allowance_usd"))
        else:
            # Antigravity's CLI reports no email, so this source is always unverified.
            row["windows"] = normalize_antigravity(antigravity_payload(account))
        row["status"] = "ok" if row["windows"] else "quota_unavailable"
    except SourceError as error:
        row["status"] = error.code if error.code in {"auth_required", "identity_mismatch", "stale"} else "error"
        row["error_code"] = error.code
    except HTTPError as error:
        row["status"] = {401: "auth_required", 403: "auth_required", 429: "rate_limited"}.get(error.code, "error")
        row["error_code"] = "http_" + str(error.code)
    except FileNotFoundError:
        row["status"] = "auth_required"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        row["status"] = "error"
        row["error_code"] = "source_unavailable"
    row["fetched_at"] = time.time()
    if row["status"] not in {"ok", "quota_unavailable", "identity_mismatch"} and account.get("fallback_snapshot_file"):
        try:
            fallback = imported_row(dict(account, snapshot_file=account["fallback_snapshot_file"]))
            fallback["mini_auth_status"] = row["status"]
            return fallback
        except (SourceError, OSError, ValueError, KeyError, TypeError):
            pass
    return row
