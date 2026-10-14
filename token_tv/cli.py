"""token-tv: set up, check, preview and run TokenTV.

    token-tv start     find your signed-in CLIs, ask for the clock, run (the easy way)
    token-tv setup     write a credential-free config (never overwrites one)
    token-tv doctor    check CLIs and login files (--live asks each provider)
    token-tv demo      render every clock face with sample data
    token-tv connect   sign an account in with its official CLI
    token-tv run       start the dashboard and drive the clock
    token-tv stocks    draw a stock watchlist and push it to the clock
"""
import argparse
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROVIDERS = {
    # provider: (CLI command, default home, file the CLI writes after login, login hint)
    'claude': ('claude', '~/.claude', '.credentials.json', 'claude auth login'),
    'ollama': ('', '~/.ollama', '', 'set OLLAMA_API_KEY, or store the key in the macOS Keychain (token-tv-ollama)'),
    'antigravity': ('agy', '~/Library/Application Support/Antigravity', '', 'sign in with the Antigravity app'),
}

# Antigravity's CLI keeps its login in the OS keychain and reports no email, so this address is
# a label only: it is never matched against anything the provider prints (see docs/setup.md).
ANTIGRAVITY_EMAIL = 'label-only@example.com'


def default_config():
    base = os.environ.get('XDG_CONFIG_HOME') or '~/.config'
    return str(Path(base).expanduser() / 'token-tv' / 'config.json')


def cli_path(provider):
    if provider == 'antigravity':
        return shutil.which(os.environ.get('TOKEN_TV_ANTIGRAVITY_BIN', PROVIDERS[provider][0]))
    return shutil.which(PROVIDERS[provider][0])


def login_state(provider, home):
    """'yes', 'no', 'keychain' or 'keyring'. Only checks that a login file exists; never opens it."""
    if provider == 'antigravity':
        return 'keyring'  # agy keeps its login in the OS keychain; there is no file to look at
    if provider == 'ollama':
        return 'keyring'  # the API key lives in the environment or the OS keychain, not a file
    root = Path(home).expanduser()
    if (root / PROVIDERS[provider][2]).is_file():
        return 'yes'
    if provider == 'claude' and sys.platform == 'darwin':
        return 'keychain'
    return 'no'


def ask(prompt, default=''):
    try:
        answer = input(f'{prompt}{f" [{default}]" if default else ""}: ').strip()
    except EOFError:
        answer = ''
    return answer or default


def device_address(text):
    """'http://host[:port]' with the trailing slash removed, or ValueError with a fix."""
    url = urlparse(text.strip())
    if url.scheme not in ('http', 'https') or not url.hostname or url.path.strip('/') or url.query:
        raise ValueError(f'{text!r} is not a clock address; use the form http://192.168.0.50')
    try:
        url.port
    except ValueError:
        raise ValueError(f'{text!r} has an invalid port') from None
    return f'{url.scheme}://{url.netloc}'


def setup(args):
    path = Path(args.config).expanduser()
    if path.exists():
        print(f'{path} already exists; TokenTV never overwrites a config.\n'
              f'Edit it, or choose another path with --config.', file=sys.stderr)
        return 1
    interactive = not args.yes and sys.stdin.isatty()
    homes = path.parent / 'homes'
    accounts = []
    for provider, (_, home, _, hint) in PROVIDERS.items():
        given = getattr(args, f'{provider}_email')
        emails = list(given or [])
        if provider == 'antigravity' and given is not None and not emails:
            emails = [ANTIGRAVITY_EMAIL]  # a bare --antigravity-email means "use the default label"
        if interactive and not emails:
            if provider == 'ollama':
                print('\nOllama Cloud: usage is read from OLLAMA_API_KEY, not a CLI; when you give an '
                      'email it is checked against the account profile.')
            else:
                print(f'\n{provider.title()}: CLI {"found" if cli_path(provider) else "not found"}')
            for letter in 'ABC':
                email = ask(f'  {provider.title()} account {letter} email (blank to {"skip" if letter == "A" else "finish"})',
                            ANTIGRAVITY_EMAIL if provider == 'antigravity' else '')
                if not email:
                    break
                emails.append(email)
        if len(emails) > 3:
            print(f'At most three {provider} accounts (A, B, C).', file=sys.stderr)
            return 1
        for index, email in enumerate(emails):
            letter = 'ABC'[index]
            key = f'{provider}_{letter.lower()}'
            reuse = index == 0 and not args.isolate
            if reuse and interactive:
                print(f'  {letter}: reuse the {provider} login in {home} (y), or keep a separate login '
                      f'for TokenTV in {homes / key} (n)?')
                reuse = ask('  Reuse', 'y').lower().startswith('y')
            accounts.append({'key': key, 'alias': f'{provider.upper()} {letter}', 'provider': provider,
                             'email': email, 'source_home': home if reuse else str(homes / key)})
    if not accounts:
        print('No accounts given. Pass --claude-email, --ollama-email or --antigravity-email (repeat for B and C), '
              'or run setup in a terminal to be asked.', file=sys.stderr)
        return 1
    device = args.device_url
    while device is None and interactive:
        device = ask('\nClock address, e.g. http://192.168.0.50 (blank for dashboard only)')
        try:
            device = device and device_address(device)
        except ValueError as error:
            print(f'  {error}')
            device = None
    try:
        device = device and device_address(device)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    config = {'poll_seconds': 300, 'accounts': accounts, 'display_style': 'digital'}
    if device:
        config['device_url'] = device
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'x') as handle:  # 'x' refuses to replace a file created meanwhile
            json.dump(config, handle, indent=2)
            handle.write('\n')
    except OSError as error:
        print(f'Could not write {path}: {error.strerror or error}', file=sys.stderr)
        return 1
    flag = '' if str(path) == default_config() else f' --config {path}'
    print(f'Wrote {path} ({len(accounts)} account{"s" * (len(accounts) != 1)}; emails and paths only, no secrets).')
    for account in accounts:
        shared = account['source_home'] == PROVIDERS[account['provider']][1]
        print(f'  {account["alias"]:<9} {"reuses your existing CLI login" if shared else "separate login"} '
              f'in {account["source_home"]}')
    print(f'Next: token-tv doctor{flag}   (separate logins need: token-tv connect{flag} --account <key>)')
    return 0


def read_config(path):
    """The validated config, or None after printing a one-line reason."""
    from token_tv.sources import load_config
    path = Path(path).expanduser()
    if not path.is_file():
        print(f'No config at {path}. Run: token-tv setup', file=sys.stderr)
        return None
    try:
        return load_config(path)
    except OSError as error:
        print(f'Could not read {path}: {error.strerror or error}', file=sys.stderr)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f'{path} is not a valid TokenTV config ({error}). Compare it with config.example.json.', file=sys.stderr)
    return None


def read_stock_config(path):
    """The validated stock watchlist config, or None after printing a one-line reason."""
    from token_tv.stocks import load_stock_config
    path = Path(path).expanduser()
    if not path.is_file():
        print(f'No stock config at {path}. Create one with symbols and device_url, or pass --config.', file=sys.stderr)
        return None
    try:
        return load_stock_config(path)
    except OSError as error:
        print(f'Could not read {path}: {error.strerror or error}', file=sys.stderr)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f'{path} is not a valid stock config ({error}).', file=sys.stderr)
    return None


def doctor(args):
    config = read_config(args.config)
    if config is None:
        return 1
    flag = '' if str(Path(args.config).expanduser()) == default_config() else f' --config {args.config}'
    ready = True
    unlabeled = []
    for account in config['accounts']:
        provider = account['provider']
        host_api = provider == 'ollama'  # no CLI: usage comes from a hosted API key
        home = account.get('source_home') or PROVIDERS[provider][1]
        found, state = (True if host_api else cli_path(provider)), login_state(provider, home)
        kind = 'API' if host_api else f'CLI {"ok" if found else "MISSING"}'
        line = f'{account["alias"]:<10} {provider:<6} {kind}  '
        if args.live:
            from token_tv.sources import fetch_account
            row = fetch_account(account)
            verified = row.get('identity_verified') or provider in ('antigravity', 'ollama')
            good = row['status'] in ('ok', 'quota_unavailable') and verified
            line += f'live check {"ok" if good else row["status"].upper()}'
            if good and not row.get('identity_verified'):
                line += (' (Antigravity reports no email)' if provider == 'antigravity'
                         else ' (no email to compare)')
                unlabeled.append(account['alias'])
            ready &= bool(good)
            if not good:
                fix = {'antigravity': 'sign in with the Antigravity app',
                       'ollama': 'set OLLAMA_API_KEY or store it in the macOS Keychain'}.get(
                           provider, f'token-tv connect{flag} --account {account["key"]}')
                line += f' -> {fix}'
        elif state == 'yes':
            line += 'login file present (not verified yet)'
        elif state == 'keychain':
            ready = False
            line += 'no login file; macOS Keychain unchecked (untested on real Macs) -> doctor --live'
        elif state == 'keyring':
            ready = False
            if provider == 'ollama':
                line += 'key comes from OLLAMA_API_KEY or the macOS Keychain (not checked yet) -> doctor --live'
            else:
                line += 'login lives in the OS keychain (not checked yet) -> doctor --live'
        else:
            ready = False
            line += f'login MISSING -> token-tv connect{flag} --account {account["key"]}'
        if not found:
            ready = False
            line += f'\n{"":<18}install the {provider} CLI, then log in ({PROVIDERS[provider][3]})'
        print(line)
    print('Clock: ' + (config.get('device_url') or 'none (dashboard only)'))
    if not ready:
        print(f'Fix the lines above, then run token-tv doctor{flag} again.')
    elif args.live and unlabeled:
        print(f'Ready, but there is no email to compare for {", ".join(unlabeled)}. '
              f'Check each reads the account you expect, then start with: token-tv run{flag}')
    elif args.live:
        print(f'Ready: every account answered with the expected email. Start with: token-tv run{flag}')
    else:
        print(f'Files are in place, but logins are not verified. Check them with: token-tv doctor{flag} --live')
    return 0 if ready else 1


def demo(args):
    from PIL import Image
    from token_tv.display import STYLES, render_page
    from token_tv.sample import snapshot
    out = Path(args.out).expanduser()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f'Could not create {out}: {error.strerror or error}', file=sys.stderr)
        return 1
    data = snapshot(time.time())
    for style in STYLES:
        image = render_page(data, style=style)
        kind = 'gif' if image[:4] == b'GIF8' else 'jpg' if image[:3] == b'\xff\xd8\xff' else 'png'
        name = out / f'demo-{style}.{kind}'
        name.write_bytes(image)
        if args.scale > 1 and kind != 'gif':
            big = Image.open(io.BytesIO(image)).resize((240 * args.scale,) * 2, Image.Resampling.NEAREST)
            big.save(out / f'demo-{style}@{args.scale}x.png')
        print(name)
    print('Sample data only (DEMO). Your real usage appears after setup, connect and run.')
    return 0


def history_cmd(args):
    """Inspect the JSONL history buffer for readings or symbol price changes."""
    from token_tv.history import read_history, query_symbol_changes
    state_sub = 'state-stocks' if getattr(args, 'stocks', False) or getattr(args, 'symbol', None) else 'state'
    state_dir = getattr(args, 'state_dir', None)
    state = Path(state_dir or (Path(args.config).expanduser().parent / state_sub))
    hist_file = state / 'history.jsonl'
    if not hist_file.exists():
        print(f"No history file found at {hist_file}", file=sys.stderr)
        return 1
    entries = read_history(hist_file, since=args.since, until=args.until)
    if args.symbol:
        res = query_symbol_changes(entries, args.symbol, since=args.since, until=args.until)
        print(f"Symbol: {res['symbol']}")
        print(f"Changed: {res['changed']}")
        print(f"Distinct Prices: {res['distinct_prices']}")
        print(f"Initial: {res['initial_price']} -> Latest: {res['latest_price']}")
        print(f"Readings Count: {len(res['readings'])}")
    else:
        print(f"Total entries: {len(entries)} in {hist_file}")
        if entries:
            first_ts = entries[0].get('updated_at')
            last_ts = entries[-1].get('updated_at')
            print(f"Span: {first_ts} to {last_ts}")
    return 0


def start(args):
    """One command: find signed-in CLIs, confirm their emails, ask for the clock, then run."""
    from token_tv.sources import logged_in_email
    path = Path(args.config).expanduser()
    interactive = not args.yes and sys.stdin.isatty()
    if not path.exists():
        accounts = []
        for provider, (_, home, _, _) in PROVIDERS.items():
            if not cli_path(provider) or login_state(provider, home) == 'no':
                continue
            email = logged_in_email(provider, home)
            if not email:
                continue
            if interactive and not ask(f'Found {provider.title()} signed in as {email}. Use it? [Y/n]', 'y').lower().startswith('y'):
                continue
            accounts.append({'key': f'{provider}_a', 'alias': f'{provider.upper()} A', 'provider': provider,
                             'email': email, 'source_home': home})
            print(f'{provider.title()}: {email}')
        if not accounts:
            print('No signed-in Claude or Antigravity CLI found. Sign in to one (for example `claude`, then /login).\n'
                  'Ollama Cloud has no CLI: add its account by hand with OLLAMA_API_KEY; its plan sets the\n'
                  'allowance (ollama_allowance_usd overrides it).\n'
                  'Or look around first with: token-tv demo', file=sys.stderr)
            return 1
        device = args.device_url
        if device is None and interactive:
            device = ask('Clock address shown on the clock screen, e.g. 192.168.0.50 (blank = dashboard only)')
        if device and not device.startswith(('http://', 'https://')):
            device = 'http://' + device
        try:
            device = device and device_address(device)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        config = {'poll_seconds': 300, 'accounts': accounts, 'display_style': 'digital'}
        if device:
            config['device_url'] = device
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'x') as handle:
            json.dump(config, handle, indent=2)
            handle.write('\n')
        print(f'Saved {path} (emails and paths only, no secrets).')
    config = read_config(path)
    if config is None:
        return 1
    if args.device_url and path.exists():
        # --device-url only fills a new config; say so rather than quietly using the saved clock.
        wanted = args.device_url if args.device_url.startswith(('http://', 'https://')) else 'http://' + args.device_url
        try:
            wanted = device_address(wanted)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        if wanted != config.get('device_url'):
            print(f'{path} already sets the clock to {config.get("device_url") or "none (dashboard only)"}, '
                  f'and --device-url is only used when that file is created.\n'
                  f'To use {wanted}, change device_url in that file, then run token-tv start again.', file=sys.stderr)
            return 1
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, webbrowser.open, ['http://127.0.0.1:8787']).start()
    print('Dashboard: http://127.0.0.1:8787  (Ctrl+C to stop)')
    from token_tv import live
    sys.argv = ['token-tv start', '--config', str(path), '--state-dir', str(path.parent / 'state')]
    live.main()
    return 0


def passthrough(module, args, extra, read=read_config):
    sys.argv = [f'token-tv {args.command}', '--config', str(Path(args.config).expanduser()), *extra]
    if not {'-h', '--help'} & set(extra) and read(args.config) is None:
        return 1
    module.main()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='token-tv', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', metavar='command')
    config = argparse.ArgumentParser(add_help=False)
    config.add_argument('--config', default=default_config(), help='config path (default: %(default)s)')

    s = commands.add_parser('setup', parents=[config], help='write a config; never overwrites one')
    for provider in PROVIDERS:
        if provider == 'antigravity':
            s.add_argument('--antigravity-email', nargs='*', action='extend', default=None,
                           help='Antigravity account email; it reports none, so the value is a label only. '
                                'Omit values to use the default address; give up to three to label accounts B and C')
        else:
            s.add_argument(f'--{provider}-email', action='append',
                           help=f'{provider.title()} account email; repeat for accounts B and C')
    s.add_argument('--isolate', action='store_true',
                   help='give every account its own login home instead of reusing your existing CLI login')
    s.add_argument('--device-url', help='clock address, e.g. http://192.168.0.50')
    s.add_argument('--yes', action='store_true', help='do not ask; use only the flags given')
    st = commands.add_parser('start', parents=[config], help='find your signed-in CLIs, ask for the clock, and run')
    st.add_argument('--device-url', help='clock address, e.g. 192.168.0.50')
    st.add_argument('--yes', action='store_true', help='accept every signed-in account without asking')
    st.add_argument('--no-browser', action='store_true', help='do not open the dashboard in a browser')
    o = commands.add_parser('doctor', parents=[config], help='check CLIs and logins')
    o.add_argument('--live', action='store_true', help='ask each provider now and compare the email; the CLI may refresh an expired login; prints status only')
    d = commands.add_parser('demo', help='render every clock face with sample data')
    d.add_argument('--out', default='token-tv-demo', help='output folder (default: %(default)s)')
    d.add_argument('--scale', type=int, default=3, help='extra enlarged PNG copy (1 = none)')
    commands.add_parser('connect', parents=[config], help='log an account in with its official CLI',
                        add_help=False)
    commands.add_parser('run', parents=[config], help='start the dashboard and drive the clock',
                        add_help=False)
    commands.add_parser('stocks', parents=[config], help='draw a stock watchlist and push it to the clock',
                        add_help=False)
    h = commands.add_parser('history', parents=[config], help='inspect reading history')
    h.add_argument('--symbol', help='stock symbol to check (e.g. NVDA, TSLA)')
    h.add_argument('--stocks', action='store_true', help='inspect stock history instead of usage')
    h.add_argument('--state-dir', help='override state directory')
    h.add_argument('--since', type=int, help='start timestamp')
    h.add_argument('--until', type=int, help='end timestamp')

    args, extra = parser.parse_known_args(argv)
    if args.command in ('connect', 'run', 'stocks'):
        reader = read_config
        if args.command == 'run':
            from token_tv import live as module
            extra = extra or []
            state = Path(args.config).expanduser().parent / 'state'
            if '--state-dir' not in extra:
                extra += ['--state-dir', str(state)]
        elif args.command == 'stocks':
            from token_tv import stocks as module
            extra = extra or []
            state = Path(args.config).expanduser().parent / 'state-stocks'
            if '--state-dir' not in extra:
                extra += ['--state-dir', str(state)]
            reader = read_stock_config
        else:
            from token_tv import connect as module
        return passthrough(module, args, extra, read=reader)
    if extra:
        parser.error('unrecognized arguments: ' + ' '.join(extra))
    if args.command is None:
        parser.print_help()
        return 0
    return {'setup': setup, 'doctor': doctor, 'demo': demo, 'start': start, 'history': history_cmd}[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
