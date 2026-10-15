"""Build the static TokenTV web demo: sample accounts and pre-rendered clock faces.

The output is plain files for any static host (the live demo runs on Vercel):
    python3 scripts/build_demo.py --out site --site-url https://example.com
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from token_tv.display import STOCK_STYLES, STYLES, render_page  # noqa: E402
from token_tv.web_assets import ASSETS, WEB  # noqa: E402
from token_tv.sample import snapshot  # noqa: E402
from token_tv.catalog import payload as theme_payload  # noqa: E402

REPO = 'https://github.com/click6067-ship-it/token-tv'
META = '''<meta name="description" content="Your AI limits, on a tiny desk clock. Claude, Ollama and Antigravity usage, several accounts, no firmware flashing.">
<meta property="og:title" content="TokenTV · Live demo">
<meta property="og:description" content="Your AI limits, on a tiny desk clock. Claude, Ollama and Antigravity · multiple accounts · no flashing.">
<meta property="og:image" content="{site}/og.png">
<meta property="og:url" content="{site}/">
<meta name="twitter:card" content="summary_large_image">'''
BAR = f'<div class="demo-bar">Live demo with sample data<a href="{REPO}">TokenTV on GitHub</a></div>'


def build(out, site):
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():  # keep the host's project link (.vercel)
        if child.name != '.vercel':
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    html = (WEB / 'index.html').read_text()
    marker = '<html lang="en"'  # whatever theme index.html defaults to, the demo adds only its flag
    assert marker in html, 'index.html opening tag changed'
    html = html.replace(marker, marker + " data-demo", 1)
    html = html.replace('<title>TokenTV · AI usage</title>', '<title>TokenTV · Live demo</title>\n' + META.format(site=site.rstrip('/')), 1)
    html = html.replace('<a class="skip" href="#providers">Skip to usage</a>', '<a class="skip" href="#providers">Skip to usage</a>\n' + BAR, 1)
    assert 'data-demo' in html and 'demo-bar' in html and 'og:image' in html, 'index.html markers changed'
    (out / 'index.html').write_text(html)
    assets = out / 'assets'
    assets.mkdir()
    for path, (file, _) in ASSETS.items():
        shutil.copy2(file, assets / path.rsplit('/', 1)[1])
    for notice in WEB.glob('*-LICENSE.txt'):
        shutil.copy2(notice, assets / notice.name)
    (out / 'demo-snapshot.json').write_text(json.dumps(snapshot(), separators=(',', ':')))
    frames = out / 'frames'
    frames.mkdir()
    rendered = snapshot(time.time())
    # Every face that draws stocks needs the feed, not just the stocks one: the combo family
    # falls back to the plain usage board when the market is closed, so without this the Combo
    # gallery thumbnail is byte-identical to Pixel and shows none of what the face is for.
    # The demo host has no feeder, so the board is sampled here.
    stocks_feed = {'updated_at': time.time(), 'source': 'YAHOO',
                   'market': {'is_open': True, 'session': 'regular', 'exchange': 'US'},
                   'symbols': [{'symbol': 'NVDA', 'price': 188.42, 'change': 9.55, 'percent': 5.33,
                                'previous_close': None, 'at': int(time.time())},
                               {'symbol': 'TSLA', 'price': 421.10, 'change': 4.10, 'percent': 0.98,
                                'previous_close': None, 'at': int(time.time())},
                               {'symbol': 'MSFT', 'price': 512.34, 'change': -3.21, 'percent': -0.62,
                                'previous_close': None, 'at': int(time.time())},
                               {'symbol': 'AAPL', 'price': 339.58, 'change': -2.94, 'percent': -0.86,
                                'previous_close': None, 'at': int(time.time())}]}
    paths = {}
    for style in STYLES:
        frame = dict(rendered, stocks=stocks_feed) if style in STOCK_STYLES else rendered
        image = render_page(frame, style=style)
        name = f"{style}.{'gif' if image[:4] == b'GIF8' else 'jpg'}"  # Space is animated
        (frames / name).write_bytes(image)
        paths[style] = f'/frames/{name}'
    display = {'style': 'digital', 'applied_style': 'digital', 'status': 'preview_only', 'styles': list(STYLES), 'frames': paths,
               'stocks': {'mode': 'watchlist', 'symbols': ['AAPL', 'MSFT', 'NVDA', 'TSLA'], 'movers_count': 2}}
    (out / 'demo-display.json').write_text(json.dumps(display))
    (out / 'demo-themes.json').write_text(json.dumps(theme_payload()))
    shutil.copy2(ROOT / 'docs' / 'images' / 'social-preview.png', out / 'og.png')
    return sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() and '.vercel' not in p.parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default='site')
    parser.add_argument('--site-url', default='https://token-tv.vercel.app', help='absolute URL for link previews')
    args = parser.parse_args()
    files = build(Path(args.out), args.site_url)
    print(f'{len(files)} files in {args.out}')


if __name__ == '__main__':
    main()
