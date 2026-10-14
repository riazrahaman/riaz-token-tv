/* Three-state theme: Auto (follow the OS, nothing stored) -> Light -> Dark -> Auto.
   The inline <head> script applies a stored "light"/"dark" before paint; Auto clears it.
   The storage key is shared with the other guides on this domain, so the choice carries across. */
const root = document.documentElement;
const THEME_NEXT = { auto: "light", light: "dark", dark: "auto" };
const THEME_LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
let themeMode = (root.dataset.theme === "dark" || root.dataset.theme === "light") ? root.dataset.theme : "auto";

function applyTheme() {
  const next = THEME_NEXT[themeMode];
  const txt = "Theme: " + THEME_LABEL[themeMode] + (themeMode === "auto" ? " (follows your system)" : "") +
              ". Switch to " + THEME_LABEL[next];
  document.querySelectorAll("#themeBtn,.fab-theme").forEach(b => {
    b.setAttribute("aria-label", txt); b.title = txt; b.removeAttribute("aria-pressed");
  });
  const m = document.querySelector("#themeBtn .theme-mode");
  if (m) m.textContent = THEME_LABEL[themeMode];
}

function toggleTheme() {
  themeMode = THEME_NEXT[themeMode];
  if (themeMode === "auto") delete root.dataset.theme; else root.dataset.theme = themeMode;
  try {
    if (themeMode === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", themeMode);
  } catch {}
  applyTheme();
}

const themeBtn = document.getElementById("themeBtn");
if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
applyTheme();

/* Chapter bar: highlight the section currently in view. */
const toc = document.querySelector(".toc");
if (toc) {
  const pills = [...toc.querySelectorAll('a[href^="#"]')];
  const chapters = pills.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  const spy = () => {
    const line = toc.offsetHeight + 24;
    let active = 0;
    chapters.forEach((c, i) => { if (c.getBoundingClientRect().top <= line) active = i; });
    pills.forEach((p, i) => p.classList.toggle("active", i === active));
  };
  addEventListener("scroll", spy, { passive: true });
  addEventListener("resize", spy);
  spy();
}

/* Floating "back to site" and "top" buttons, added by JS so no-JS keeps the plain anchors. */
const fab = document.createElement("div");
fab.className = "fab";
fab.innerHTML = '<button type="button" class="fab-top" hidden>&uarr; Top</button>';
document.body.appendChild(fab);
const topBtn = fab.querySelector(".fab-top");
topBtn.addEventListener("click", () => scrollTo({
  top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"
}));
const fabVis = () => { topBtn.hidden = scrollY < 600; };
addEventListener("scroll", fabVis, { passive: true });
fabVis();
