const STORAGE_KEY = "theme";
const BLACK_PANTHER = "black-panther";
const WHITE_PANTHER = "white-panther";

function getThemeMetaColor(theme) {
  return theme === WHITE_PANTHER ? "#f5f5f5" : "#000000";
}

export function normalizeTheme(value) {
  if (value === WHITE_PANTHER || value === "light") {
    return WHITE_PANTHER;
  }

  return BLACK_PANTHER;
}

export function getStoredTheme() {
  if (typeof window === "undefined") {
    return BLACK_PANTHER;
  }

  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored ? normalizeTheme(stored) : BLACK_PANTHER;
}

export function applyTheme(theme) {
  if (typeof document === "undefined" || typeof window === "undefined") {
    return BLACK_PANTHER;
  }

  const nextTheme = normalizeTheme(theme);
  document.documentElement.classList.toggle(
    "dark",
    nextTheme === BLACK_PANTHER,
  );
  window.localStorage.setItem(STORAGE_KEY, nextTheme);

  const metaThemeColor = document.querySelector('meta[name="theme-color"]');
  if (metaThemeColor) {
    metaThemeColor.setAttribute("content", getThemeMetaColor(nextTheme));
  }

  return nextTheme;
}

export function applyStoredTheme() {
  return applyTheme(getStoredTheme());
}

export function isBlackPantherTheme(theme) {
  return normalizeTheme(theme) === BLACK_PANTHER;
}

export function getThemeLabel(theme) {
  return isBlackPantherTheme(theme) ? "Black Panther" : "White Panther";
}
