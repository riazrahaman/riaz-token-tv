/* ================= TokenTV Site Behavior =================
   Adapted from Stack Field Guide (SFG):
   - Three-state theme control: Auto -> Light -> Dark -> Auto with localStorage persistence.
   - Synchronized hero (#themeBtn) and floating (.fab-theme) controls.
   - Sticky chapter bar (.toc) with scroll-spy setting aria-current="location".
   - Wide-screen side rail (>=1500px) built dynamically from chapters.
   - Floating Action Buttons (.fab): Back-to-origin chip, Theme toggle, Top button.
   - Pinch-zoom visual viewport pinning (iOS / mobile visualViewport scale).
   - In-page jump history navigation with cameFrom / navOrigin tracking.
   - Version sync from <meta name="version">.
   - Privacy-friendly visit counter via Abacus API.
   ========================================================= */

const root = document.documentElement;

/* ---------------- Theme Toggle ---------------- */
const THEME_NEXT = { auto: "light", light: "dark", dark: "auto" };
const THEME_LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
let themeMode = (root.dataset.theme === "dark" || root.dataset.theme === "light") ? root.dataset.theme : "auto";

function applyTheme() {
  const next = THEME_NEXT[themeMode];
  const txt = "Theme: " + THEME_LABEL[themeMode] + (themeMode === "auto" ? " (follows your system)" : "") +
              ". Switch to " + THEME_LABEL[next];
  document.querySelectorAll("#themeBtn, .fab-theme").forEach(b => {
    b.setAttribute("aria-label", txt);
    b.title = txt;
    b.removeAttribute("aria-pressed");
  });
  const m = document.querySelector("#themeBtn .theme-mode");
  if (m) m.textContent = THEME_LABEL[themeMode];
}

function toggleTheme() {
  themeMode = THEME_NEXT[themeMode];
  if (themeMode === "auto") {
    delete root.dataset.theme;
    try { localStorage.removeItem("theme"); } catch {}
  } else {
    root.dataset.theme = themeMode;
    try { localStorage.setItem("theme", themeMode); } catch {}
  }
  applyTheme();
}

const themeBtn = document.getElementById("themeBtn");
if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

/* ---------------- Navigation: Chapter Bar & Scroll-Spy ---------------- */
const toc = document.querySelector(".toc");
const strip = toc ? toc.querySelector(".wrap") : null;
const pills = strip ? [...strip.querySelectorAll('a[href^="#"]')] : [];
const chapters = pills.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
const motion = () => matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";

let tocH = toc ? toc.offsetHeight : 0;
const readLine = () => tocH + Math.min(160, innerHeight * 0.25);

/* Phones: horizontal scrolling row edge fade */
const stripFade = () => {
  if (!strip) return;
  strip.classList.toggle("at-end", strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 4);
};
if (strip) {
  strip.addEventListener("scroll", stripFade, { passive: true });
}

let spy = null, current, rail = null;

function setCurrent(sec) {
  if (sec === current) return;
  current = sec;
  pills.forEach(a => {
    if (a.hash === "#" + (sec && sec.id)) {
      a.setAttribute("aria-current", "location");
    } else {
      a.removeAttribute("aria-current");
    }
  });
  const a = sec && pills.find(p => p.hash === "#" + sec.id);
  if (a && strip && strip.scrollWidth > strip.clientWidth) {
    const r = a.getBoundingClientRect(), sr = strip.getBoundingClientRect();
    if (r.left < sr.left + 16) strip.scrollBy({ left: r.left - sr.left - 16, behavior: motion() });
    else if (r.right > sr.right - 40) strip.scrollBy({ left: r.right - sr.right + 40, behavior: motion() });
  }
  if (rail) rail.setChapter(sec);
}

function spyNow() {
  const y = readLine();
  const atBottom = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
  let hit = null;
  for (const sec of chapters) {
    const r = sec.getBoundingClientRect();
    if (r.top <= (atBottom ? innerHeight - 1 : y) && r.bottom > 0) hit = sec;
  }
  if (!hit && scrollY < y + 200 && chapters.length) hit = chapters[0];
  if (hit && hit.getBoundingClientRect().bottom < tocH) hit = null;
  setCurrent(hit || (scrollY < 300 && chapters.length ? chapters[0] : null));
}

function startSpy() {
  if (spy) spy.disconnect();
  if (!chapters.length) return;
  const y = Math.round(readLine());
  spy = new IntersectionObserver(spyNow, {
    rootMargin: `-${y}px 0px -${Math.max(0, innerHeight - y - 1)}px 0px`
  });
  chapters.forEach(c => spy.observe(c));
}

if (toc) {
  new ResizeObserver(() => {
    tocH = toc.offsetHeight;
    root.style.setProperty("--toc-h", tocH + "px");
    stripFade();
    startSpy();
    spyNow();
  }).observe(toc);
  addEventListener("resize", () => { startSpy(); stripFade(); });
}

/* ---------------- Floating Action Buttons (.fab) ---------------- */
const fab = document.createElement("div");
fab.className = "fab";
fab.innerHTML = '<button type="button" class="fab-back" hidden></button>' +
                '<button type="button" class="fab-theme" aria-label="Theme" hidden>◐</button>' +
                '<button type="button" class="fab-top" aria-label="Back to top" hidden>↑ Top</button>';
document.body.append(fab);

const backBtn = fab.querySelector(".fab-back");
const themeFab = fab.querySelector(".fab-theme");
const topBtn = fab.querySelector(".fab-top");

const hero = document.querySelector(".hero");
if (hero) {
  new IntersectionObserver(([e]) => {
    themeFab.hidden = topBtn.hidden = e.isIntersecting;
  }).observe(hero);
}

topBtn.addEventListener("click", () => {
  scrollTo({ top: 0, behavior: motion() });
  if (strip) strip.scrollTo({ left: 0, behavior: motion() });
  if (pills[0]) pills[0].focus({ preventScroll: true });
});

themeFab.addEventListener("click", () => {
  toggleTheme();
  themeFab.focus({ preventScroll: true });
});

applyTheme();

/* ---------------- Wide-Screen Side Rail (>=1500px) ---------------- */
const railEl = chapters.length ? document.createElement("nav") : null;
const railEmoji = a => {
  const m = a.textContent.match(/^\s*(\p{Extended_Pictographic}[\uFE0E\uFE0F\u{1F3FB}-\u{1F3FF}]?)/u);
  return m ? m[1] : "";
};
const scrollRailTo = el => {
  if (!el || !railEl) return;
  const r = el.getBoundingClientRect(), rr = railEl.getBoundingClientRect();
  if (r.top < rr.top + 8) railEl.scrollBy({ top: r.top - rr.top - 8, behavior: motion() });
  else if (r.bottom > rr.bottom - 8) railEl.scrollBy({ top: r.bottom - rr.bottom + 8, behavior: motion() });
};

if (railEl) {
  railEl.className = "rail";
  railEl.setAttribute("aria-label", "Chapters");
  const ul = document.createElement("ul");
  chapters.forEach(sec => {
    const a = pills.find(p => p.hash === "#" + sec.id);
    if (!a) return;
    const li = document.createElement("li");
    li.className = "r-ch";
    li.dataset.sec = sec.id;
    const link = document.createElement("a");
    link.href = "#" + sec.id;
    link.innerHTML = railEmoji(a) + " " + a.textContent.replace(/^\s*\S+\s*/, "").trim();
    li.append(link);
    ul.append(li);
  });
  railEl.append(ul);
  document.body.append(railEl);
}

rail = {
  setChapter(sec) {
    if (!railEl) return;
    const id = sec ? sec.id : (chapters[0] && chapters[0].id);
    const li = railEl.querySelector('.r-ch[data-sec="' + id + '"]');
    [...railEl.querySelectorAll(".r-ch")].forEach(x => {
      if (x === li) x.setAttribute("aria-current", "location");
      else x.removeAttribute("aria-current");
    });
    scrollRailTo(li);
  }
};

/* ---------------- Pinch-Zoom Visual Viewport Pinning ---------------- */
const vv = window.visualViewport;
if (vv && toc) {
  const heroWrap = toc.previousElementSibling;
  let vvQueued = false;
  const pinToVisual = () => {
    vvQueued = false;
    const s = vv.width > 0 ? document.documentElement.clientWidth / vv.width : 1;
    const naturalTop = heroWrap ? Math.max(0, heroWrap.getBoundingClientRect().bottom) : 0;
    if (s > 1.01 && naturalTop < vv.offsetTop + 0.5) {
      toc.style.transformOrigin = "0 0";
      toc.style.transform = `translate(${vv.offsetLeft}px, ${vv.offsetTop - naturalTop}px) scale(${1 / s})`;
    } else {
      toc.style.transform = "";
    }
    if (s > 1.01) {
      const de = document.documentElement, cs = getComputedStyle(fab);
      const gapR = parseFloat(cs.right) || 16, gapB = parseFloat(cs.bottom) || 16;
      const dx = vv.offsetLeft + vv.width - de.clientWidth + gapR - gapR / s;
      const dy = vv.offsetTop + vv.height - de.clientHeight + gapB - gapB / s;
      fab.style.transformOrigin = "100% 100%";
      fab.style.transform = `translate(${dx}px, ${dy}px) scale(${1 / s})`;
    } else {
      fab.style.transform = "";
    }
  };
  const queuePin = () => {
    if (!vvQueued) {
      vvQueued = true;
      requestAnimationFrame(pinToVisual);
    }
  };
  vv.addEventListener("resize", queuePin, { passive: true });
  vv.addEventListener("scroll", queuePin, { passive: true });
  addEventListener("scroll", queuePin, { passive: true });
}

/* ---------------- "← Back to …" In-Page Jump Chip ---------------- */
let origin = null;
let ownNav = null;

function labelFor(el) {
  const pill = pills.find(a => a.hash === "#" + el.id);
  if (pill) return pill.textContent.replace(/^\S+\s/, "");
  const h = el.querySelector("h3,h2");
  return h ? h.textContent.trim() : "where you were";
}

let autoScroll = false, idleT = 0, focusAfterBack = null;
const markAuto = () => {
  autoScroll = true;
  clearTimeout(idleT);
  idleT = setTimeout(() => { autoScroll = false; }, 200);
};

function hideBack() {
  origin = null;
  backBtn.hidden = true;
}

function showBack(el) {
  origin = { el, label: labelFor(el), left: false };
  backBtn.textContent = "← Back to " + origin.label;
  backBtn.hidden = false;
}

document.addEventListener("click", ev => {
  const a = ev.target.closest('a[href^="#"]');
  if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const target = document.getElementById(decodeURIComponent(a.hash.slice(1)));
  if (!target && a.hash !== "#idea") return;
  markAuto();
  const sameHash = location.hash === a.hash, prevState = history.state;
  ownNav = a.hash;
  setTimeout(() => { if (ownNav === a.hash) ownNav = null; }, 0);
  if (sameHash) setTimeout(() => history.replaceState(prevState, ""), 0);
  const from = a.closest(".toc, .rail") ? current : (a.closest("section.chapter[id]") || current);

  // If clicking "The idea", scroll cleanly to the top of the page to show the Hero header
  if (a.hash === "#idea") {
    ev.preventDefault();
    scrollTo({ top: 0, behavior: motion() });
    if (from && from !== chapters[0]) showBack(from);
    if (!sameHash) {
      history.pushState({ ...prevState, navOrigin: from ? from.id : null }, "", "#idea");
      setTimeout(() => { if (location.hash === "#idea") history.replaceState({ ...history.state, cameFrom: from ? from.id : null }, ""); }, 0);
    }
    setCurrent(chapters[0]);
    return;
  }

  if (!from || from === target || from.contains(target) || target.contains(from)) return;
  showBack(from);
  if (sameHash) return;
  history.replaceState({ ...prevState, navOrigin: from.id }, "");
  setTimeout(() => { if (location.hash === a.hash) history.replaceState({ ...history.state, cameFrom: from.id }, ""); }, 0);
});

backBtn.addEventListener("click", () => {
  if (!origin) return;
  const el = origin.el;
  markAuto();
  if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
  if (history.state && history.state.cameFrom === el.id) {
    focusAfterBack = el;
    history.back();
  } else {
    if (el.id === "idea") scrollTo({ top: 0, behavior: motion() });
    else el.scrollIntoView({ behavior: motion(), block: "start" });
  }
  hideBack();
});

addEventListener("hashchange", () => { if (location.hash === ownNav) ownNav = null; });
addEventListener("popstate", () => {
  if (ownNav && location.hash === ownNav) { ownNav = null; return; }
  markAuto();
  const st = history.state, el = st && st.navOrigin && document.getElementById(st.navOrigin);
  hideBack();
  const prev = st && st.cameFrom && document.getElementById(st.cameFrom);
  if (prev) showBack(prev);
  const f = focusAfterBack;
  focusAfterBack = null;
  requestAnimationFrame(() => {
    if (location.hash === "#idea" || (!location.hash && (!el || el.id === "idea"))) {
      scrollTo({ top: 0, behavior: motion() });
    } else if (el) {
      el.scrollIntoView({ block: "start" });
    }
    if (f) f.focus({ preventScroll: true });
  });
});

/* ---------------- Initial Scroll Position ---------------- */
if (location.hash === "#idea" || !location.hash) {
  if ("scrollRestoration" in history) {
    history.scrollRestoration = "manual";
  }
  window.scrollTo(0, 0);
}

let ticking = false, wasBottom = false;
addEventListener("scroll", () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    const atBottom = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
    if (atBottom || wasBottom) spyNow();
    wasBottom = atBottom;
    if (autoScroll) markAuto();
    if (!origin) return;
    const r = origin.el.getBoundingClientRect(), y = readLine();
    if (r.bottom < tocH || r.top > innerHeight) origin.left = true;
    else if (!autoScroll && origin.left && ((r.top <= y && r.bottom > y) || atBottom)) hideBack();
  });
}, { passive: true });

/* ---------------- Version Synchronization ---------------- */
const siteVersion = document.querySelector('meta[name="version"]')?.content;
if (siteVersion) {
  const heroV = document.getElementById("heroVersion");
  const siteV = document.getElementById("siteVersion");
  if (heroV) heroV.textContent = "v" + siteVersion;
  if (siteV) siteV.textContent = "v" + siteVersion;
}

/* ---------------- Visit Counter ---------------- */
async function initVisitCounter() {
  const BASE = "https://abacus.jasoncameron.dev",
        NAMESPACE = "token-tv.riazrahaman.com",
        KEY = (document.documentElement.dataset.visitKey || document.body.dataset.visitKey || "pageviews"),
        FLAG = "visit-counted";
  let already = false;
  try { already = sessionStorage.getItem(FLAG) === "1"; } catch {}
  if (!already) { try { sessionStorage.setItem(FLAG, "1"); } catch {} }

  let value = null;
  // 1. Try local /api/visits endpoint
  try {
    const localRes = await fetch(`/api/visits?hit=${already ? 0 : 1}`);
    if (localRes.ok) {
      const data = await localRes.json();
      if (typeof data.value === "number") value = data.value;
    }
  } catch {}

  // 2. Fallback to Abacus cloud counter
  if (value === null) {
    try {
      const res = await fetch(`${BASE}/${already ? "get" : "hit"}/${NAMESPACE}/${KEY}`, { referrerPolicy: "no-referrer" });
      if (res.ok) {
        const d = await res.json();
        if (typeof d.value === "number") value = d.value;
      }
    } catch {}
  }

  if (value === null) return;
  const formatted = `${value.toLocaleString()} ${value === 1 ? "visit" : "visits"}`;

  const footEl = document.getElementById("visits");
  if (footEl) {
    footEl.textContent = formatted;
    footEl.title = already ? "Visit count so far" : "This visit was counted";
    footEl.hidden = false;
  }

  const heroEl = document.getElementById("heroVisits");
  if (heroEl) {
    heroEl.textContent = formatted;
    heroEl.title = already ? "Visit count so far" : "This visit was counted";
  }
}
if (/^https?:$/.test(location.protocol)) initVisitCounter();

/* ---------------- Concept Video Sound Toggle ---------------- */
(function() {
  const conceptVid = document.getElementById("conceptVideo");
  const soundToggle = document.getElementById("soundToggle");
  if (!conceptVid || !soundToggle) return;

  function updateSoundUI(isMuted) {
    if (isMuted) {
      soundToggle.innerHTML = '<span class="sound-icon">🔇</span> <span class="sound-text">Unmute</span>';
      soundToggle.setAttribute("aria-pressed", "false");
      soundToggle.classList.remove("is-unmuted");
    } else {
      soundToggle.innerHTML = '<span class="sound-icon">🔊</span> <span class="sound-text">Mute</span>';
      soundToggle.setAttribute("aria-pressed", "true");
      soundToggle.classList.add("is-unmuted");
    }
  }

  async function toggleAudio(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    const isCurrentlyMuted = conceptVid.muted || conceptVid.volume === 0;
    if (isCurrentlyMuted) {
      // Unmute: rewind so voiceover plays from beginning
      conceptVid.currentTime = 0;
      conceptVid.removeAttribute("muted");
      conceptVid.muted = false;
      conceptVid.defaultMuted = false;
      conceptVid.volume = 1.0;
      try {
        await conceptVid.play();
      } catch (err) {
        console.warn("Autoplay audio unlock failed:", err);
      }
      updateSoundUI(false);
    } else {
      conceptVid.muted = true;
      updateSoundUI(true);
    }
  }

  soundToggle.addEventListener("click", toggleAudio);
  conceptVid.addEventListener("click", toggleAudio);
})();
