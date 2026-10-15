'use strict';
/* TokenTV web dashboard.
 *
 * One card per account, every reading visible at once: a gauge reads as "how full is this",
 * which is the question, where a bar reads as "how far along is this". Severity is carried by
 * the band word as well as the colour, so a reading survives greyscale.
 *
 * Every appearance renders the same nodes; style.css skins them. */
const STATUS = {ok:'Connected', loading:'Loading', auth_required:'Login needed', identity_mismatch:'Check identity', quota_unavailable:'No quota reported', stale:'Old reading', rate_limited:'Retry later', error:'Fetch failed'};
const EXPLAIN = {auth_required:'sign in to the CLI', identity_mismatch:'this account does not match the config', quota_unavailable:'connected, but the service reports no limit', rate_limited:'the service asked us to wait', error:'could not read this account', loading:'first reading on its way'};
const PROVIDERS = ['claude', 'ollama', 'antigravity'];
const LABELS = {claude:'Claude', ollama:'Ollama', antigravity:'Antigravity'};
const svgNS = 'http://www.w3.org/2000/svg';
const $ = s => document.querySelector(s);
const readPreference = (k, f) => {try {return JSON.parse(localStorage.getItem(k)) ?? f} catch {return f}};
const savePreference = (k, v) => {try {localStorage.setItem(k, JSON.stringify(v))} catch {/* Private browsing may disallow storage. */}};
// The static demo (scripts/build_demo.py) serves sample data and pre-rendered clock frames.
const DEMO = document.documentElement.hasAttribute('data-demo');
let snapshot = null, offline = false, polling = false, displayInfo = null, clockChoice = null, applying = false, clockError = false;
const el = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n};
function svgNode(tag, attrs) {const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n}
const clamp = v => Math.max(0, Math.min(100, v));
const pad = n => String(n).padStart(2, '0');

/* Provider marks: identity only. The reading itself is never colour-coded by provider. */
function icon(provider) {
 const s = svgNode('svg', {viewBox:'0 0 48 48', 'aria-hidden':'true', fill:'none', stroke:'currentColor', 'stroke-linecap':'round', 'stroke-linejoin':'round', class:'glyph'});
 if (provider === 'claude') {
  const reach = [19.5, 15, 18, 14, 19, 15.5, 18.5, 14, 19.5, 15, 18, 14.5];
  reach.forEach((r, i) => s.append(svgNode('path', {d:`M24 ${24 - 6} V${24 - r}`, 'stroke-width':3.4, transform:`rotate(${i * 30 + (i % 2 ? 4 : 0)} 24 24)`})));
 } else if (provider === 'ollama') {
  const block = (x, y, w, h, r) => s.append(svgNode('rect', {x, y, width:w, height:h, rx:r || 0, fill:'currentColor'}));
  block(16, 5, 4, 8);      // left ear
  block(26, 5, 4, 8);      // right ear
  block(14, 11, 19, 9, 3); // head
  block(7, 15, 8, 6, 2);   // muzzle
  block(20, 20, 7, 20, 2); // long neck
 } else {
  s.append(svgNode('circle', {cx:24, cy:24, r:12.5, 'stroke-width':3.4}));
  s.append(svgNode('path', {d:'M5 43C17 33 31 19 43 5 34 19 21 32 5 43Z', fill:'currentColor', stroke:'currentColor', 'stroke-width':1.6}));
 }
 return s;
}
function clockGlyph() {
 const s = svgNode('svg', {viewBox:'0 0 24 24', 'aria-hidden':'true', fill:'none', stroke:'currentColor', 'stroke-width':1.8, 'stroke-linecap':'round', class:'clock-icon'});
 s.append(svgNode('circle', {cx:12, cy:12, r:9}), svgNode('path', {d:'M12 7.5V12l3 2'}));
 const wrap = el('span', undefined, 'clock-mark'); wrap.setAttribute('aria-hidden', 'true'); wrap.append(s); return wrap;
}

function resetTime(epoch) {if (!Number.isFinite(epoch) || epoch <= 0) return '—'; let m = Math.ceil((epoch - Date.now() / 1000) / 60); if (m <= 0) return 'Awaiting reset'; return m >= 1440 ? `${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h` : `${Math.floor(m / 60)}h ${pad(m % 60)}m`}
/* A window's label is '5H', 'WEEK' — or, for Ollama Cloud, the budget itself ('$102'). */
const budget = w => w && String(w.label || '').startsWith('$');
function period(w) {if (!w) return 'QUOTA'; return w.label === 'BUDGET' ? 'CREDIT BUDGET' : w.label === 'WEEK' ? 'WEEKLY' : w.label === '5H' ? '5-HOUR' : String(w.label || 'QUOTA')}
function windowName(w) {
 if (!w) return 'no window';
 if (budget(w)) return 'monthly credit';
 return {'5H':'5-hour window', WEEK:'weekly window', DAY:'daily window', MONTH:'monthly window'}[w.label] || period(w).toLowerCase();
}
function windowShort(w) {if (!w) return '—'; if (budget(w)) return 'credit'; return {'5H':'5-hour', WEEK:'weekly', DAY:'daily', MONTH:'monthly'}[w.label] || period(w).toLowerCase()}
/* The bands the clock faces and the legend share: <50 low, 50–79 medium, 80–89 high, 90+ critical. */
function level(v) {return v < 50 ? 'low' : v < 80 ? 'medium' : v < 90 ? 'high' : 'critical'}
function levelText(v) {return v >= 100 ? 'Limit reached' : v >= 90 ? 'Near limit' : v >= 80 ? 'High usage' : v >= 50 ? 'Moderate' : 'Low usage'}

/* The gauge. --pct drives stroke-dasharray in CSS; the number carries the same value in text. */
function arc(value, label) {
 const figure = el('div', undefined, 'figure');
 const s = svgNode('svg', {class:'arc', viewBox:'0 0 120 120', 'aria-hidden':'true'});
 s.append(svgNode('circle', {class:'arc-track', cx:60, cy:60, r:52}));
 if (Number.isFinite(value)) {const fill = svgNode('circle', {class:'arc-fill', cx:60, cy:60, r:52}); fill.style.setProperty('--pct', String(clamp(value))); s.append(fill)}
 const num = el('div', undefined, 'percentage');
 if (Number.isFinite(value)) {
  const digits = el('span', String(Math.round(clamp(value))), 'pct-num');
  num.dataset.digits = String(digits.textContent.length);  // '100' must stay inside the ring
  num.append(digits, el('span', '%', 'percent-sign'));
  num.setAttribute('role', 'meter'); num.setAttribute('aria-label', label);
  num.setAttribute('aria-valuemin', '0'); num.setAttribute('aria-valuemax', '100'); num.setAttribute('aria-valuenow', String(Math.round(clamp(value))));
 } else {
  num.append(el('span', '––', 'pct-num no-reading'));
  num.setAttribute('role', 'img'); num.setAttribute('aria-label', label + ' unavailable');
 }
 figure.append(s, num); return figure;
}

/* 'ANTIGRAVITY B' next to the word Antigravity is the provider name twice, and it is what
 * overflows a 320px card. Drop the duplicated prefix and keep what distinguishes the account. */
function shortAlias(a, provider) {
 const alias = String(a.alias || '').trim(), name = (LABELS[provider] || provider).toUpperCase();
 if (!alias) return '';
 const rest = alias.toUpperCase().startsWith(name) ? alias.slice(name.length).trim() : alias;
 return rest || alias;
}
/* One account, one card. No tabs: a tab hides a reading behind a click. */
function readingCard(account, siblings) {
 const a = account, provider = a.provider;
 const stale = offline || a.status === 'stale';
 const valid = (a.windows || []).filter(w => Number.isFinite(w.used_percent));
 // A mismatched identity must not expose another person's quota as this account.
 const windows = a.status === 'identity_mismatch' ? [] : valid;
 const primary = windows.reduce((p, w) => !p || w.used_percent > p.used_percent ? w : p, null);
 const value = primary ? clamp(primary.used_percent) : null;

 const card = el('article', undefined, 'read');
 Object.assign(card.dataset, {provider, key:a.key || provider, state: value === null ? 'blank' : stale ? 'stale' : 'ok'});
 if (value !== null && !stale) card.dataset.level = level(value);

 const head = el('header');
 const who = el('span', undefined, 'who'); who.append(el('i', undefined, 'mark'), el('span', LABELS[provider] || provider, 'who-name'));
 who.querySelector('.mark').append(icon(provider));
 if (siblings > 1) who.append(el('span', shortAlias(a, provider), 'alias'));
 head.append(who, el('span', value === null ? '—' : windowName(primary), 'win'));

 const label = (a.alias || LABELS[provider] || provider) + ' ' + period(primary) + ' used quota';
 const used = el('p', undefined, 'used');
 if (value === null) used.append(el('span', STATUS[a.status] || 'No reading', 'band flag'), el('span', EXPLAIN[a.status] || 'no reading available', 'used-note'));
 else if (stale) used.append(el('span', 'OLD', 'band flag'), el('span', 'not a current reading', 'used-note'));
 else used.append(el('span', levelText(value), 'band'), el('span', budget(primary) ? `of your ${primary.label} monthly budget` : 'used of your limit', 'used-note'));

 const foot = el('footer'), timer = el('div', undefined, 'timer');
 timer.append(clockGlyph());
 if (value === null) timer.append(el('span', 'no reading', 'timer-value'));
 else {
  const t = primary ? resetTime(primary.resets_at) : '—';
  if (stale) timer.append(el('span', 'last read ' + checkedText(a, true), 'timer-value'));
  else if (t === '—') timer.append(el('span', 'no reset time reported', 'timer-value'));
  else {
   timer.append(el('span', value >= 90 ? 'clears in' : 'resets in', 'timer-label'));
   timer.append(el('span', t, 'timer-value'));
  }
 }
 foot.append(timer);
 card.append(head, arc(value, label), used, foot);
 return card;
}
function checkedText(a, relative) {
 const at = a.status === 'stale' ? a.last_success_at : a.fetched_at;
 if (!Number.isFinite(at)) return 'an unknown time ago';
 if (!relative) return new Date(at * 1000).toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});
 const m = Math.max(0, Math.round(Date.now() / 1000 - at) / 60);
 return m < 1 ? 'just now' : m < 60 ? `${Math.round(m)}m ago` : `${Math.floor(m / 60)}h ago`;
}

/* Second windows are detail, so they are quiet rows rather than more cards. */
function otherWindows(rows) {
 const list = $('#other-list'), fragment = document.createDocumentFragment();
 let count = 0;
 for (const a of rows) {
  if (a.status === 'identity_mismatch') continue;
  const valid = (a.windows || []).filter(w => Number.isFinite(w.used_percent));
  const primary = valid.reduce((p, w) => !p || w.used_percent > p.used_percent ? w : p, null);
  for (const w of valid) {
   if (w === primary) continue;
   const v = clamp(w.used_percent), li = el('li'); li.dataset.level = level(v);
   const who = el('span', undefined, 'who'); who.append(el('i', undefined, 'mark'), el('span', a.alias || LABELS[a.provider] || a.provider, 'who-name'));
   who.querySelector('.mark').append(icon(a.provider));
   const track = el('span', undefined, 'track'), fill = el('i');
   fill.style.setProperty('--pct', String(v)); track.append(fill);
   track.setAttribute('role', 'meter'); track.setAttribute('aria-label', (a.alias || a.provider) + ' ' + period(w) + ' used quota');
   track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.setAttribute('aria-valuenow', String(Math.round(v)));
   const when = resetTime(w.resets_at);
   li.append(who, el('span', windowShort(w), 'win'), track, el('b', Math.round(v) + '%'),
             el('span', offline || a.status === 'stale' ? 'OLD' : when === '—' ? 'no reset time' : 'resets ' + when, 't'));
   fragment.append(li); count++;
  }
 }
 list.replaceChildren(fragment);
 $('#other-windows').hidden = !count;
}

function renderAccounts() {
 if (!snapshot) return;
 const rows = Object.values(snapshot.accounts || {});
 const order = new Map(PROVIDERS.map((p, i) => [p, i]));
 rows.sort((a, b) => (order.get(a.provider) ?? 9) - (order.get(b.provider) ?? 9) || String(a.key).localeCompare(String(b.key)));
 const per = {};
 for (const a of rows) per[a.provider] = (per[a.provider] || 0) + 1;
 const fragment = document.createDocumentFragment();
 if (!rows.length) for (const provider of PROVIDERS) fragment.append(readingCard({key:provider, alias:LABELS[provider], provider, status:'loading', windows:[]}, 1));
 else for (const a of rows) fragment.append(readingCard(a, per[a.provider]));
 $('#providers').replaceChildren(fragment);
 otherWindows(rows);
 const reporting = offline ? 0 : rows.filter(a => a.status === 'ok' && (a.windows || []).some(w => Number.isFinite(w.used_percent))).length;
 $('#foot-reporting').textContent = `${reporting} of ${rows.length} account${rows.length === 1 ? '' : 's'} reporting`;
}

/* Demo data stores resets as seconds from now so the timers stay plausible on any day. */
function demoSnapshot(data) {
 const now = Date.now() / 1000;
 for (const a of Object.values(data.accounts)) {a.fetched_at = a.last_success_at = now; for (const w of a.windows) w.resets_at = Number.isFinite(w.resets_in) ? now + w.resets_in : w.resets_at}
 return {...data, updated_at: now};
}
async function update() {
 if (polling) return; polling = true;
 try {
  const r = await fetch(DEMO ? '/demo-snapshot.json' : '/snapshot', {cache:'no-store'}); if (!r.ok) throw Error();
  const data = DEMO ? demoSnapshot(await r.json()) : await r.json(); if (!data.accounts || !Number.isFinite(data.updated_at)) throw Error();
  snapshot = data; offline = false;
  const staleCount = Object.values(data.accounts).filter(a => a.status === 'stale').length;
  $('#connection').textContent = DEMO ? 'Demo · sample data' : staleCount ? `${staleCount} old` : 'Live';
  $('#notice').hidden = !staleCount; $('#notice').textContent = staleCount + ' account' + (staleCount === 1 ? ' has' : 's have') + ' an older reading. Check that account’s source and last successful update.';
  $('#updated').textContent = data.updated_at ? 'checked ' + new Date(data.updated_at * 1000).toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'}) : 'waiting for the first reading';
 } catch {
  offline = true; $('#connection').textContent = 'Offline'; $('#notice').hidden = false;
  $('#notice').textContent = snapshot ? 'Connection lost. Showing the last received readings as OLD.' : 'Cannot reach TokenTV. Is token-tv still running?';
  if (!snapshot) $('#providers').replaceChildren(el('p', 'No usage data received.', 'loading'));
 } finally {polling = false; document.body.dataset.offline = String(offline); renderAccounts()}
}

/* ---- the clock: preview, face, apply ---- */
const styleName = s => s === 'retro' ? 'Pixel Retro' : s === 'hud' ? 'Sci-Fi HUD' : s ? s[0].toUpperCase() + s.slice(1) : 'Unknown';
let lastImageStyle = null, lastImageAt = 0;
function paintClock(forceImage = false) {
 const style = clockChoice || displayInfo?.style;
 $('#clock-style').disabled = applying || !displayInfo;
 $('#apply').disabled = DEMO || applying || !displayInfo || (style === displayInfo.style && displayInfo.status !== 'error' && !clockError);
 $('#apply').textContent = applying ? 'Sending image…' : 'Apply to clock';
 $('#apply').dataset.state = applying ? 'loading' : clockError || displayInfo?.status === 'error' ? 'error' : displayInfo?.status === 'ok' ? 'success' : 'default';
 $('#display-state').textContent = DEMO ? 'Demo · install TokenTV to drive a real clock' : clockError ? 'Could not apply. Please retry.' : !displayInfo ? 'Clock status unavailable' : style !== displayInfo.style ? 'Preview only · Apply to send' : displayInfo.status === 'queued' ? 'Sending image…' : displayInfo.status === 'error' ? 'Clock upload failed. Please retry.' : displayInfo.status === 'preview_only' ? 'Preview only · no clock connected' : 'Image sent · ' + styleName(displayInfo.applied_style);
 $('#clock-face').textContent = displayInfo?.applied_style ? styleName(displayInfo.applied_style) : '—';
 if (style && (forceImage || style !== lastImageStyle || Date.now() - lastImageAt > 30000)) {
  lastImageStyle = style; lastImageAt = Date.now();
  $('#frame').src = DEMO ? displayInfo.frames?.[style] || `/frames/${encodeURIComponent(style)}.jpg` : '/frame/0.jpg?style=' + encodeURIComponent(style) + '&t=' + lastImageAt;
  $('#frame').alt = styleName(style) + ' live clock preview';
 }
}
let displayPolling = false;
async function refreshDisplay(forceImage = false) {
 if (displayPolling) return; displayPolling = true;
 try {
  const r = await fetch(DEMO ? '/demo-display.json' : '/display', {cache:'no-store'}); if (!r.ok) throw Error();
  const data = await r.json();
  const dirty = displayInfo && clockChoice !== displayInfo.style;
  displayInfo = data; if (!clockChoice || !dirty) clockChoice = data.style;
  const select = $('#clock-style');
  if (!select.options.length) for (const s of data.styles) {const o = el('option', styleName(s)); o.value = s; select.append(o)}
  select.value = clockChoice; paintClock(forceImage); paintStocks();
  if (themeData) paintThemes();
 } catch {$('#display-state').textContent = 'Clock status unavailable'} finally {displayPolling = false}
}
async function applyStyle(style) {
 if (applying || DEMO || !style) return;
 applying = true; clockError = false; clockChoice = style; $('#clock-style').value = style; paintClock(); paintThemes();
 try {
  const r = await fetch('/display/style', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({style})});
  if (!r.ok) throw Error();
  displayInfo = await r.json();
 } catch {clockError = true} finally {applying = false; paintClock(true); paintStocks(); paintThemes()}
}
$('#clock-style').onchange = e => {clockChoice = e.target.value; clockError = false; paintClock(true); paintThemes()};
$('#apply').onclick = () => applyStyle(clockChoice);
$('#frame').onerror = () => {$('#display-state').textContent = 'Clock preview unavailable. It will retry.'};

/* Stocks face controls: a mode toggle and, in watchlist mode, a 4-slot ticker editor.
 * Shown only when /display reports a stocks payload, i.e. when the face actually uses them. */
const stockSlots = () => [...document.querySelectorAll('#stocks-symbols .stocks-ticker')];
let stocksBusy = false, stocksDirty = false, stocksError = false;
function paintStocks() {
  const stocks = displayInfo?.stocks, panel = $('#stocks-controls');
  panel.hidden = !stocks; if (panel.hidden) return;
  const mode = stocks.mode === 'movers' ? 'movers' : 'watchlist';
  $('#stocks-watchlist').disabled = $('#stocks-movers').disabled = DEMO || stocksBusy;
  $('#stocks-watchlist').setAttribute('aria-pressed', String(mode === 'watchlist'));
  $('#stocks-movers').setAttribute('aria-pressed', String(mode === 'movers'));
  $('#stocks-editor').hidden = mode === 'movers';
  const note = $('#stocks-movers-note');
  note.hidden = mode !== 'movers';
  if (mode === 'movers') note.textContent = `Top ${stocks.movers_count || 2} gainers + top ${stocks.movers_count || 2} losers, re-ranked every cycle`;
  const slots = stockSlots();
  if (mode === 'watchlist' && !stocksDirty && !slots.some(s => s === document.activeElement))
    slots.forEach((input, i) => input.value = (stocks.symbols || [])[i] || '');
  const edited = slots.map(s => s.value.trim().toUpperCase()).filter(Boolean).join(',') !== (stocks.symbols || []).join(',');
  $('#stocks-apply').disabled = DEMO || stocksBusy || mode === 'movers' || (!edited && !stocksDirty);
  $('#stocks-apply').textContent = stocksBusy ? 'Sending…' : 'Apply tickers';
  $('#stocks-state').textContent = DEMO ? 'Demo · install TokenTV to drive the stocks face' : stocksError ? 'Could not save. Please retry.' : '';
}
async function postStocks(path, body) {
  if (stocksBusy || DEMO) return; stocksBusy = true; stocksError = false; paintStocks();
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!r.ok) throw Error();
    displayInfo = await r.json();
  } catch {stocksError = true} finally {stocksBusy = false; stocksDirty = false; paintStocks()}
}
$('#stocks-watchlist').onclick = () => postStocks('/stocks/mode', {mode:'watchlist'});
$('#stocks-movers').onclick = () => postStocks('/stocks/mode', {mode:'movers'});
for (const input of stockSlots()) input.addEventListener('input', () => {input.value = input.value.toUpperCase(); stocksDirty = true; paintStocks()});
$('#stocks-apply').onclick = () => {
  const symbols = stockSlots().map(s => s.value.trim().toUpperCase()).filter(Boolean).slice(0, 4);
  if (!symbols.length) {$('#stocks-state').textContent = 'Add at least one ticker'; return}
  postStocks('/stocks/symbols', {symbols});
};

/* ---- gallery: the thumbnail is the product, so it gets the space ---- */
let themeData = null, themeSort = 'popular', revealedId = null, themePaintSig = null;
const REPO_URL = 'https://github.com/click6067-ship-it/token-tv';
const day = s => new Date(s).toLocaleDateString();
/* A count only when there is one to show: '♥ 0' is noise, and a local face is already labelled. */
const likeText = t => (t.likes_state === 'counted' || t.likes_state === 'stale') && t.likes ? `♥ ${t.likes}` : '';
function sortThemes(list, mode) {
 const added = t => t.added_at ? -Date.parse(t.added_at) : Infinity;
 const fresh = [...list].sort((a, b) => added(a) - added(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
 return mode === 'new' ? fresh : [...fresh.filter(t => t.likes !== null).sort((a, b) => b.likes - a.likes), ...fresh.filter(t => t.likes === null)];
}
function thumbSrc(id) {
 if (DEMO) return displayInfo?.frames?.[id] || `/frames/${encodeURIComponent(id)}.jpg`;
 return '/frame/0.jpg?style=' + encodeURIComponent(id);
}
function paintThemes(force = false) {
 const list = $('#theme-list');
 document.querySelectorAll('.theme-sort button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sort === themeSort)));
 if (!themeData) return;
 /* Every card holds a <img src="/frame/0.jpg?style=…">, which the server renders on request and
  * sends no-store. refreshDisplay runs every five seconds, so repainting unconditionally would
  * re-render all thirteen faces in the publisher's own process — and would drop the revealed
  * Apply button and the keyboard focus with it. Repaint only when something visible changed. */
 const sig = [themeSort, displayInfo?.applied_style, clockChoice, applying, revealedId, themeData.themes.length].join('\u0000');
 if (!force && sig === themePaintSig) return;
 themePaintSig = sig;
 const ordered = sortThemes(themeData.themes, themeSort);
 const onClock = displayInfo?.applied_style;
 const fragment = document.createDocumentFragment();
 $('#gallery-count').textContent = `${ordered.length} face${ordered.length === 1 ? '' : 's'}`;
 $('#likes-as-of').textContent = themeData.last_attempt_at ? 'Likes last counted ' + day(themeData.last_attempt_at) : 'Likes not counted yet';
 for (const t of ordered) {
  const item = el('li', undefined, 'theme-card'); item.dataset.id = t.id;
  const usable = t.installed && !t.needs_update;
  const current = usable && t.id === onClock;
  if (current) item.classList.add('current');
  if (usable && t.id === clockChoice && !current) item.classList.add('previewing');
  if (usable && t.id === revealedId) item.classList.add('revealed');
  const figure = el('figure');
  const src = usable ? thumbSrc(t.id) : t.preview_url;
  // The thumbnail is a preview button: looking costs nothing, applying writes to flash.
  const look = el('button', undefined, 'thumb-button'); look.type = 'button';
  look.setAttribute('aria-label', 'Preview ' + t.name + ' on the clock panel');
  if (src) {const img = el('img', undefined, 'theme-thumb'); img.src = src; img.alt = t.name + ' clock face'; img.width = 240; img.height = 240; img.loading = 'lazy'; look.append(img)}
  else look.append(el('span', 'No preview', 'no-thumb'));
  look.onclick = () => {
   if (!usable) return;
   // First tap previews and reveals Apply; applying needs a second, deliberate tap.
   revealedId = t.id; clockChoice = t.id; clockError = false; $('#clock-style').value = t.id;
   paintClock(true); paintThemes();
   $('#clock-panel').scrollIntoView({block:'nearest', behavior:'smooth'});
  };
  figure.append(look);
  if (usable && !current && !DEMO) {
   const apply = el('button', applying && clockChoice === t.id ? 'Sending…' : 'Apply', 'apply'); apply.type = 'button';
   apply.setAttribute('aria-label', 'Apply ' + t.name + ' to the clock');
   apply.disabled = applying;
   apply.onclick = () => applyStyle(t.id);
   figure.append(apply);
  }
  item.append(figure);
  const meta = el('div', undefined, 'theme-title');
  meta.append(el('strong', t.name));
  if (current) meta.append(el('span', 'On the clock', 'now'));
  else if (!usable) meta.append(el('span', `Needs v${t.min_version}`, 'theme-update'));
  else meta.append(el('span', [t.local ? 'Local' : t.author, likeText(t)].filter(Boolean).join(' · '), 'theme-author'));
  item.append(meta);
  if (!usable) item.append(el('p', 'Update TokenTV to use this face', 'theme-update'));
  const links = el('div', undefined, 'theme-actions');
  const link = (text, href) => {const a = el('a', text); a.href = href; a.rel = 'noopener'; a.target = '_blank'; links.append(a)};
  if (DEMO) link('Get', REPO_URL + '/blob/main/docs/setup.md');
  if (t.like_url) link('Like on GitHub', t.like_url);
  if (t.source_url) link('Source', t.source_url);
  if (links.childElementCount) item.append(links);
  fragment.append(item);
 }
 const hadFocus = list.contains(document.activeElement);
 list.replaceChildren(fragment);
 if (hadFocus && revealedId) list.querySelector(`[data-id="${revealedId}"] .thumb-button`)?.focus({preventScroll:true});
}
async function loadThemes() {try {const r = await fetch(DEMO ? '/demo-themes.json' : '/themes', {cache:'no-store'}); if (!r.ok) throw Error(); themeData = await r.json(); paintThemes()} catch {$('#likes-as-of').textContent = 'Themes unavailable · your clock styles still work'}}
document.querySelectorAll('.theme-sort button').forEach(b => {b.onclick = () => {themeSort = b.dataset.sort; paintThemes()}});

/* ---- appearance ----
 * One design. The page used to offer four costumes mirroring four clock faces, which was the
 * same choice twice: the faces are on the clock, where they belong. What is left is the only
 * preference a reader actually has — light, dark, or follow the system. Every token is
 * light-dark(), so the whole page turns on one color-scheme declaration. */
const MODES = ['auto', 'light', 'dark'];
function setMode(mode) {
 const m = MODES.includes(mode) ? mode : 'auto';
 document.documentElement.dataset.mode = m; savePreference('tokentv.mode', m);
 for (const b of document.querySelectorAll('[data-mode-choice]')) b.setAttribute('aria-pressed', String(b.dataset.modeChoice === m));
}
for (const b of document.querySelectorAll('[data-mode-choice]')) b.onclick = () => setMode(b.dataset.modeChoice);
setMode(readPreference('tokentv.mode', 'auto'));


update(); refreshDisplay(); loadThemes();
setInterval(update, 30000); setInterval(() => refreshDisplay(), 5000);
