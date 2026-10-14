# TokenTV project page

A static page explaining the TokenTV project: design, code flow, hardware, what it was forked
from, and what was contributed on top.

## Deploying on Railway

This folder is self-contained. In the Railway service settings set:

- **Root Directory** → `site/tokentv`
- **Branch** → `site-tokentv-page`

Railway's Node builder then runs `npm start`, which serves this folder on `$PORT`. There is no
build step and no dependency to install. Point a custom domain at the service when it is up.

## Running it locally

```bash
node server.js            # http://localhost:3000
PORT=4000 node server.js  # or pick a port
```

## Design

The page deliberately reuses `assets/guide.css` from the Stack Field Guide so the two sub-pages
share one design language; `assets/project.css` only adds components this page needs and never
redefines a token. Clock faces in `assets/` are rendered from the project's sample data, never
from live readings.
