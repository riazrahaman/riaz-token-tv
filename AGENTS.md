# Agent guide

TokenTV puts Claude, Ollama and Antigravity usage limits on a ~$5 AliExpress Wi-Fi desk clock (240×240, SmallTV-style) by uploading pictures to its stock photo album, so the clock keeps its firmware. A computer that stays on reads usage and draws the pictures. Python 3.10+, Pillow, no other runtime deps.

## Run without accounts or a clock

```bash
pip install -e .
token-tv demo --out token-tv-demo     # every face from sample data
python3 -B -m unittest discover -s tests
```

## Set up TokenTV for the user

When the user says "Set up TokenTV for me":

1. Install: `python3 -m venv .venv && .venv/bin/pip install -e .` (Windows: `.venv\Scripts\pip install -e .`),
   or with Node only: `npx github:click6067-ship-it/token-tv demo`.
2. Ask the user for the clock's IP address (shown on the clock's screen).
3. Run `.venv/bin/token-tv start --device-url <ip>`. It finds the Claude and Antigravity CLIs that are already
   signed in, reads each account's email through the official login, asks the user to confirm, saves the
   config and opens the dashboard at http://127.0.0.1:8787. The user then presses Clock display → Apply.
   Ollama Cloud has no CLI: add its account by hand with `OLLAMA_API_KEY` and `ollama_allowance_usd`.
4. If it says nothing is signed in, tell the user to sign in to the CLI they use (e.g. `claude`, then /login)
   and run step 3 again. A second account of the same service: `.venv/bin/token-tv connect --account <key>`.

Verified on Linux with one SD_PRO clock. Mac and Windows hosts are untested; report what fails.

## Change how things look

- Clock faces: `docs/clock-faces.md` (one Python function per face, sample data, `token-tv demo`).
- Web dashboard: one design, taken from token-tv.riazrahaman.com so the tool and the project
  page read as one thing — IBM Plex, the paper/navy palette, the hairline grid, pill controls.
  Colours and type are `token_tv/web/tokens.css`; layout is `token_tv/web/style.css`. Every
  colour token is `light-dark(light, dark)`, so the Auto/Light/Dark control switches nothing but
  `color-scheme`. There is no web theme picker: the costumes belong on the clock, not on the page.
- More services: `docs/adding-a-provider.md`.

## Rules

- Never read, print or copy credential files (`.credentials.json`, `auth.json`, Keychain items) or
  real config files. Tests use `token_tv/sample.py` and temporary folders.
- Do not publish, push, open issues or create releases on the user's behalf.
- A missing reading is a dash, an old one is marked OLD. Never turn unknown into `0`.
- `token-tv setup --yes --<provider>-email ...` is non-interactive and never overwrites a config.
  `token-tv doctor --live` contacts providers; ask the user before running it.

## Make a clock face

Follow `docs/clock-faces.md`: write a render function, add it to `RENDERERS` and `STYLES`, render
with `token-tv demo`, and look at the 240×240 result at real size. It then appears in the dashboard's
Gallery as a Local face and can be applied to the clock without any upstream change.
