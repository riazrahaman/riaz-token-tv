"""Sign in to one account's own CLI home, without changing defaults."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from token_tv.sources import exe, fetch_account, load_config, scoped_env

# provider: (everyday CLI home, file present after login)
DEFAULT_HOMES = {"claude": ("~/.claude", ".credentials.json"),
                 "antigravity": ("~/Library/Application Support/Antigravity", "")}


def login_command(account, browser=False):
    provider = account["provider"]
    if provider == "claude":
        return [exe("claude"), "auth", "login", "--claudeai", "--email", account["email"]]
    raise ValueError("This provider has no login command; sign in through its own app or API key")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--account", help="Stable account key; omit to choose interactively")
    parser.add_argument("--browser", action="store_true", help="Prefer the provider's browser login when it offers one")
    args = parser.parse_args()
    accounts = load_config(args.config)["accounts"]
    key = args.account
    if key is None:
        for index, account in enumerate(accounts, 1):
            print(f"{index}. {account['provider']} · {account['alias']}")
        try:
            choice = int(input("Choose account number: "))
            if not 1 <= choice <= len(accounts):
                raise ValueError("Out of range")
            key = accounts[choice - 1]["key"]
        except (ValueError, IndexError, EOFError):
            parser.error("Choose one of the listed accounts")
    account = next((a for a in accounts if a["key"] == key), None)
    if account is None or not account.get("source_home"):
        parser.error("An account with its own CLI home (source_home) is required")
    if account["provider"] == "antigravity":
        print("Antigravity has no login command: sign in inside the Antigravity app; the agy CLI reads that\n"
              "login from the OS keychain. Verify it with: token-tv doctor --live", file=sys.stderr)
        raise SystemExit(1)
    if account["provider"] == "ollama":
        print("Ollama Cloud has no login command: set OLLAMA_API_KEY, or store the key in the macOS Keychain\n"
              "(security add-generic-password -s token-tv-ollama -w). Verify it with: token-tv doctor --live", file=sys.stderr)
        raise SystemExit(1)
    root = Path(account["source_home"]).expanduser()
    default, marker = DEFAULT_HOMES[account["provider"]]
    keychain = account["provider"] == "claude" and sys.platform == "darwin"  # Claude Code on macOS keeps it there
    if root == Path(default).expanduser() and ((root / marker).is_file() or keychain):
        state = "may already be signed in (macOS keeps it in the Keychain)" if keychain else "it is already signed in"
        print(f"{account['alias']} reuses your everyday {account['provider']} login in {default}; {state}.\n"
              "Logging in again replaces that login for every tool that uses it. To keep it,\n"
              "just run token-tv doctor --live. For a separate login, point source_home elsewhere.", flush=True)
        if not sys.stdin.isatty() or input("Type REPLACE to log in again anyway: ").strip() != "REPLACE":
            print("Kept the existing login.", flush=True)
            return
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    print("Sign in as " + account["email"] + ". Enter login codes here, not in chat.", flush=True)
    result = subprocess.run(login_command(account, args.browser), env=scoped_env(account["provider"], root))
    if result.returncode:
        raise SystemExit(result.returncode)
    # Verification must use the newly authenticated source, never its fallback.
    direct = {k: v for k, v in account.items() if k not in ("snapshot_file", "fallback_snapshot_file")}
    row = fetch_account(direct)
    print("Verification: " + row["status"], flush=True)
    if row["status"] not in ("ok", "quota_unavailable") or not row["identity_verified"]:
        raise SystemExit(1)
    print("Account verified. A running TokenTV picks up this login on its next poll.", flush=True)


if __name__ == "__main__":
    main()
