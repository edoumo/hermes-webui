"use strict";

(function installHarnessPreferences() {
  const PREFIX = "hermesHarness.ui.";
  const KEYS = {
    locale: PREFIX + "locale",
    theme: PREFIX + "theme",
    sessionsWidth: PREFIX + "sessionsWidth",
    workersWidth: PREFIX + "workersWidth",
    sessionsCollapsed: PREFIX + "sessionsCollapsed",
    workersCollapsed: PREFIX + "workersCollapsed",
    archivedSessions: PREFIX + "archivedSessions",
    showArchivedSessions: PREFIX + "showArchivedSessions",
    showArchivedWorkers: PREFIX + "showArchivedWorkers",
  };

  const LOCALES = window.HermesHarnessLocales || {};
  const FALLBACK_LOCALE = Object.prototype.hasOwnProperty.call(LOCALES, "en") ? "en" : Object.keys(LOCALES)[0];
  if (!FALLBACK_LOCALE) throw new Error("Hermes Harness locale catalog is empty");

  function storageGet(key, fallback = null) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  function storageSet(key, value) {
    try { window.localStorage.setItem(key, String(value)); }
    catch (_) {}
  }

  function storageJson(key, fallback) {
    try {
      const raw = storageGet(key, "");
      const parsed = raw ? JSON.parse(raw) : fallback;
      return parsed ?? fallback;
    } catch (_) {
      return fallback;
    }
  }

  function normalizeLocale(value) {
    const text = String(value || "").trim().toLowerCase().replace(/_/g, "-");
    if (!text) return "";
    if (LOCALES[text]) return text;
    const base = text.split("-", 1)[0];
    return LOCALES[base] ? base : "";
  }

  function browserLocale() {
    const requested = [
      ...(Array.isArray(navigator.languages) ? navigator.languages : []),
      navigator.language,
    ];
    for (const candidate of requested) {
      const normalized = normalizeLocale(candidate);
      if (normalized) return normalized;
    }
    return FALLBACK_LOCALE;
  }

  let locale = normalizeLocale(storageGet(KEYS.locale, "")) || browserLocale();
  let theme = storageGet(KEYS.theme, "dark");
  if (!new Set(["dark", "light"]).has(theme)) theme = "dark";

  function messagesFor(code) {
    return LOCALES[code]?.messages || LOCALES[FALLBACK_LOCALE]?.messages || {};
  }

  function t(key, vars = {}) {
    const active = messagesFor(locale);
    const fallback = messagesFor(FALLBACK_LOCALE);
    const template = active[key] ?? fallback[key] ?? key;
    return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (_m, name) =>
      Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : `{${name}}`
    );
  }

  function statusLabel(value) {
    const normalized = String(value || "").toLowerCase();
    const map = {
      ready: "ready", blocked: "blocked", running: "running",
      in_progress: "running", completed: "completed", complete: "completed",
      succeeded: "completed", failed: "failed", cancelled: "cancelled",
      dormant: "dormant", disabled: "disabled",
    };
    return map[normalized] ? t(map[normalized]) : String(value || "");
  }

  function roleLabel(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "leaf") return t("leaf");
    if (normalized === "orchestrator") return t("orchestrator");
    return String(value || "");
  }

  function emitChange(kind) {
    window.dispatchEvent(new CustomEvent("hermes-harness-ui-change", { detail: { kind } }));
  }

  function populateLocaleSelect() {
    const selector = document.getElementById("localeSelect");
    if (!selector) return;
    const previous = selector.value;
    selector.replaceChildren();
    for (const [code, definition] of Object.entries(LOCALES)) {
      selector.add(new Option(definition?.label || code.toUpperCase(), code));
    }
    selector.value = LOCALES[locale] ? locale : (LOCALES[previous] ? previous : FALLBACK_LOCALE);
  }

  function applyTheme() {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const button = document.getElementById("themeToggle");
    if (button) {
      button.textContent = theme === "dark" ? "☀" : "☾";
      button.title = theme === "dark" ? t("themeLight") : t("themeDark");
      button.setAttribute("aria-label", button.title);
    }
  }

  function setTheme(next) {
    theme = next === "light" ? "light" : "dark";
    storageSet(KEYS.theme, theme);
    applyTheme();
    emitChange("theme");
  }

  function setLocale(next) {
    const normalized = normalizeLocale(next);
    if (!normalized) return;
    locale = normalized;
    storageSet(KEYS.locale, locale);
    applyStaticTranslations();
    applyTheme();
    emitChange("locale");
  }

  function applyStaticTranslations(root = document) {
    document.documentElement.lang = locale;
    root.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
      node.setAttribute("placeholder", t(node.dataset.i18nPlaceholder));
    });
    root.querySelectorAll("[data-i18n-title]").forEach((node) => {
      node.setAttribute("title", t(node.dataset.i18nTitle));
      if (node.tagName === "BUTTON") node.setAttribute("aria-label", t(node.dataset.i18nTitle));
    });
    const selector = document.getElementById("localeSelect");
    if (selector) selector.value = locale;
  }

  function numericPref(key, fallback, min, max) {
    const value = Number(storageGet(key, fallback));
    return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
  }

  function boolPref(key, fallback = false) {
    const value = storageGet(key, fallback ? "1" : "0");
    return value === "1" || value === "true";
  }

  function applyRailPreferences() {
    const layout = document.querySelector(".layout");
    if (!layout) return;
    layout.style.setProperty("--sessions-width", `${numericPref(KEYS.sessionsWidth, 250, 150, 520)}px`);
    layout.style.setProperty("--workers-width", `${numericPref(KEYS.workersWidth, 310, 180, 620)}px`);
    layout.classList.toggle("sessions-collapsed", boolPref(KEYS.sessionsCollapsed));
    layout.classList.toggle("workers-collapsed", boolPref(KEYS.workersCollapsed));
    for (const railName of ["sessions", "workers"]) {
      const button = document.querySelector(`[data-collapse-rail="${railName}"]`);
      if (!button) continue;
      const collapsed = layout.classList.contains(`${railName}-collapsed`);
      button.textContent = collapsed ? "›" : "‹";
      button.title = collapsed ? t("expandPanel") : t("collapsePanel");
      button.setAttribute("aria-label", button.title);
    }
  }

  function toggleRail(name) {
    if (!new Set(["sessions", "workers"]).has(name)) return;
    const layout = document.querySelector(".layout");
    if (!layout) return;
    const className = `${name}-collapsed`;
    const collapsed = !layout.classList.contains(className);
    layout.classList.toggle(className, collapsed);
    storageSet(name === "sessions" ? KEYS.sessionsCollapsed : KEYS.workersCollapsed, collapsed ? "1" : "0");
    applyRailPreferences();
    emitChange("layout");
  }

  function installRailResizers() {
    document.querySelectorAll("[data-rail-resizer]").forEach((handle) => {
      const name = handle.dataset.railResizer;
      const key = name === "sessions" ? KEYS.sessionsWidth : KEYS.workersWidth;
      const min = name === "sessions" ? 150 : 180;
      const max = name === "sessions" ? 520 : 620;
      handle.addEventListener("pointerdown", (event) => {
        if (window.matchMedia("(max-width: 780px)").matches) return;
        const rail = handle.closest(".rail");
        if (!rail) return;
        const startX = event.clientX;
        const startWidth = rail.getBoundingClientRect().width;
        handle.setPointerCapture(event.pointerId);
        document.body.classList.add("rail-resizing");
        const move = (moveEvent) => {
          const width = Math.min(max, Math.max(min, startWidth + moveEvent.clientX - startX));
          document.querySelector(".layout")?.style.setProperty(`--${name}-width`, `${width}px`);
          storageSet(key, Math.round(width));
          emitChange("layout");
        };
        const stop = () => {
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", stop);
          handle.removeEventListener("pointercancel", stop);
          document.body.classList.remove("rail-resizing");
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", stop);
        handle.addEventListener("pointercancel", stop);
      });
      handle.addEventListener("dblclick", () => {
        const fallback = name === "sessions" ? 250 : 310;
        storageSet(key, fallback);
        applyRailPreferences();
        emitChange("layout");
      });
    });
  }

  function archivedSessionIds() {
    const values = storageJson(KEYS.archivedSessions, []);
    return new Set(Array.isArray(values) ? values.map(String) : []);
  }

  function setSessionArchived(sessionId, archived) {
    const values = archivedSessionIds();
    if (archived) values.add(String(sessionId));
    else values.delete(String(sessionId));
    storageSet(KEYS.archivedSessions, JSON.stringify([...values]));
    emitChange("sessions");
  }

  function isSessionArchived(sessionId) {
    return archivedSessionIds().has(String(sessionId));
  }

  function showArchivedSessions() { return boolPref(KEYS.showArchivedSessions); }
  function showArchivedWorkers() { return boolPref(KEYS.showArchivedWorkers); }

  function toggleArchivedVisibility(kind) {
    const key = kind === "sessions" ? KEYS.showArchivedSessions : KEYS.showArchivedWorkers;
    storageSet(key, boolPref(key) ? "0" : "1");
    emitChange(kind);
  }

  function init() {
    populateLocaleSelect();
    applyStaticTranslations();
    applyTheme();
    applyRailPreferences();
    installRailResizers();
    document.getElementById("localeSelect")?.addEventListener("change", (event) => setLocale(event.target.value));
    document.getElementById("themeToggle")?.addEventListener("click", () => setTheme(theme === "dark" ? "light" : "dark"));
    document.querySelectorAll("[data-collapse-rail]").forEach((button) => {
      button.addEventListener("click", () => toggleRail(button.dataset.collapseRail));
    });
    document.getElementById("showArchivedSessionsBtn")?.addEventListener("click", () => toggleArchivedVisibility("sessions"));
    document.getElementById("showArchivedWorkersBtn")?.addEventListener("click", () => toggleArchivedVisibility("workers"));
  }

  window.HarnessUI = {
    KEYS,
    t,
    statusLabel,
    roleLabel,
    locale: () => locale,
    locales: () => Object.keys(LOCALES),
    theme: () => theme,
    setLocale,
    setTheme,
    applyStaticTranslations,
    applyRailPreferences,
    setSessionArchived,
    isSessionArchived,
    showArchivedSessions,
    showArchivedWorkers,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
