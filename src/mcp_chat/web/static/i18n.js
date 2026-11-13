/** Client locale catalogs and language persistence for the chat page. */

const SUPPORTED_LANGS = Object.freeze(["en", "cs", "de", "fr", "it", "es"]);
const STORAGE_KEY = "mcpChatLang";

const catalogs = Object.create(null);
let activeLang = "en";
let englishCatalog = null;

function normalizeLang(code) {
  const raw = String(code || "")
    .trim()
    .toLowerCase()
    .replace("_", "-");
  if (!raw) return "";
  const primary = raw.split("-")[0];
  return SUPPORTED_LANGS.includes(primary) ? primary : "";
}

function detectInitialLang() {
  try {
    const stored = normalizeLang(localStorage.getItem(STORAGE_KEY));
    if (stored) return stored;
  } catch {
    /* ignore storage errors */
  }
  const nav = normalizeLang(navigator.language || navigator.userLanguage);
  return nav || "en";
}

async function loadCatalog(lang) {
  if (catalogs[lang]) return catalogs[lang];
  const response = await fetch(`/static/locales/${lang}.json`);
  if (!response.ok) {
    throw new Error(`locale ${lang} missing`);
  }
  const data = await response.json();
  catalogs[lang] = data;
  return data;
}

function formatTemplate(template, vars) {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : match,
  );
}

function t(key, vars) {
  const primary = catalogs[activeLang] || {};
  const fallback = englishCatalog || catalogs.en || {};
  const template = primary[key] ?? fallback[key] ?? key;
  return formatTemplate(template, vars);
}

function applyStaticI18n() {
  document.documentElement.lang = activeLang;
  document.title = t("title");
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    if (key) node.textContent = t(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    const key = node.getAttribute("data-i18n-placeholder");
    if (key) node.setAttribute("placeholder", t(key));
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    const key = node.getAttribute("data-i18n-aria");
    if (key) node.setAttribute("aria-label", t(key));
  });
  const select = document.querySelector("#lang");
  if (select && select.value !== activeLang) select.value = activeLang;
}

async function initI18n() {
  activeLang = detectInitialLang();
  englishCatalog = await loadCatalog("en");
  if (activeLang !== "en") {
    try {
      await loadCatalog(activeLang);
    } catch {
      activeLang = "en";
    }
  }
  applyStaticI18n();
  return activeLang;
}

async function setLanguage(lang) {
  const next = normalizeLang(lang) || "en";
  if (next !== "en") {
    await loadCatalog(next);
  } else if (!catalogs.en) {
    await loadCatalog("en");
  }
  activeLang = next;
  try {
    localStorage.setItem(STORAGE_KEY, activeLang);
  } catch {
    /* ignore storage errors */
  }
  applyStaticI18n();
  return activeLang;
}

function getLanguage() {
  return activeLang;
}

window.McpI18n = {
  SUPPORTED_LANGS,
  initI18n,
  setLanguage,
  getLanguage,
  t,
  applyStaticI18n,
  normalizeLang,
};
