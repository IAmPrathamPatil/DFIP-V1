const THEME_KEY = "dfip.theme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";

export { THEME_KEY, THEME_DARK, THEME_LIGHT };

export function readStoredTheme() {
  try {
    return localStorage.getItem(THEME_KEY) === THEME_LIGHT ? THEME_LIGHT : THEME_DARK;
  } catch (error) {
    return THEME_DARK;
  }
}

export function applyTheme(theme, options) {
  const persist = !options || options.persist !== false;
  const next = theme === THEME_LIGHT ? THEME_LIGHT : THEME_DARK;
  const root = document.documentElement;
  if (next === THEME_LIGHT) root.setAttribute("data-theme", THEME_LIGHT);
  else root.removeAttribute("data-theme");
  root.style.colorScheme = next;
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (error) {
      /* private mode / blocked storage */
    }
  }
  for (const btn of document.querySelectorAll("[data-theme-set]")) {
    btn.setAttribute("aria-pressed", btn.getAttribute("data-theme-set") === next ? "true" : "false");
  }
  return next;
}

export function bootTheme() {
  return applyTheme(readStoredTheme(), { persist: false });
}

document.addEventListener("click", (event) => {
  const themeSet = event.target.closest("[data-theme-set]");
  if (!themeSet) return;
  event.preventDefault();
  applyTheme(themeSet.getAttribute("data-theme-set"));
});

bootTheme();
