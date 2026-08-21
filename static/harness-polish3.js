"use strict";

// H6.3 is the third human-UAT polish layer. It deliberately stays browser-only:
// it improves model discovery, locale presentation and single-stage DAG layout
// without taking ownership of auth, SSE, task state or durable-worker storage.

const H63_LOCALE_FLAGS = Object.freeze({
  en: "🇬🇧",
  fr: "🇫🇷",
  es: "🇪🇸",
  pt: "🇵🇹",
  de: "🇩🇪",
  it: "🇮🇹",
});

function h63ProviderSlug(row) {
  return String(row?.slug || row?.provider || row?.id || row?.name || "").trim();
}

function h63ModelId(item) {
  if (typeof item === "string") return item.trim();
  return String(item?.id || item?.model || item?.name || "").trim();
}

function h63ProviderModels(row) {
  return h62UniqueStrings((Array.isArray(row?.models) ? row.models : []).map(h63ModelId));
}

function h63RuntimeModelHints() {
  const hints = [];
  const add = (value) => {
    const clean = String(value || "").trim();
    if (clean && !hints.includes(clean)) hints.push(clean);
  };
  add(state.currentWorker?.model);
  for (const worker of state.workers || []) add(worker?.model);
  const selectedSession = (state.sessions || []).find((session) => session.id === state.sessionId);
  add(selectedSession?.model || selectedSession?.model_id);
  add(state.h62ModelCatalog?.currentModel);
  return hints;
}

function h63RowsContainingModels(providers, hints) {
  if (!hints.length) return [];
  return providers.filter((row) => {
    const models = new Set(h63ProviderModels(row));
    return hints.some((hint) => models.has(hint));
  });
}

function h63PreferredProviderRow(payload) {
  const providers = Array.isArray(payload?.providers) ? payload.providers : [];
  const rootProvider = String(payload?.provider || "").trim().toLowerCase();
  const rootModel = String(payload?.model || "").trim();

  if (rootProvider) {
    const exact = providers.find((row) => h63ProviderSlug(row).toLowerCase() === rootProvider);
    if (exact) return exact;
  }

  const modelHints = h62UniqueStrings([rootModel, ...h63RuntimeModelHints()]);
  const matching = h63RowsContainingModels(providers, modelHints);
  if (matching.length === 1) return matching[0];

  const explicitlyActive = providers.filter((row) =>
    h63ProviderModels(row).length &&
    (row?.active === true || row?.current === true || row?.selected === true)
  );
  if (explicitlyActive.length === 1) return explicitlyActive[0];

  // The stock Hermes picker exposes authentication/configuration on provider
  // rows even when the API root does not announce provider/model. Selecting a
  // unique authenticated/configured provider is deterministic and avoids ever
  // presenting another provider's models with the current worker credentials.
  const authenticated = providers.filter((row) =>
    h63ProviderModels(row).length && (row?.authenticated === true || row?.configured === true)
  );
  if (authenticated.length === 1) return authenticated[0];

  return null;
}

function h63CatalogFromPayload(payload) {
  const row = h63PreferredProviderRow(payload);
  const rootModel = String(payload?.model || "").trim();
  const runtimeHints = h63RuntimeModelHints();
  const models = h63ProviderModels(row);
  let currentModel = rootModel;
  if (!currentModel) currentModel = runtimeHints.find((model) => models.includes(model)) || "";
  if (currentModel && !models.includes(currentModel)) models.unshift(currentModel);
  return {
    provider: h63ProviderSlug(row) || String(payload?.provider || "").trim(),
    currentModel,
    models,
  };
}

function h63ApplyModelCatalog(catalog) {
  state.h62ModelCatalog = catalog;
  state.models = h62UniqueStrings(catalog?.models || []);
  h62FillModelSelect("workerModelSelect", "workerCustomModelInput", catalog?.currentModel || "");
  const settingsValue = state.currentWorker?.model || h62SelectedModel("workerSettingsModelSelect", "workerSettingsCustomModelInput") || "";
  h62FillModelSelect("workerSettingsModelSelect", "workerSettingsCustomModelInput", settingsValue);
  h62UpdateModelHelp();
}

state.h63ModelOptionsPayload = null;

loadModels = async function h63LoadModels() {
  try {
    const payload = await api("/api/harness/model-options");
    state.h63ModelOptionsPayload = payload;
    h63ApplyModelCatalog(h63CatalogFromPayload(payload));
  } catch (_) {
    state.h63ModelOptionsPayload = null;
    state.h62ModelCatalog = { provider: "", currentModel: "", models: [] };
    state.models = [];
    h63ApplyModelCatalog(state.h62ModelCatalog);
  }
};

function h63RefreshCatalogFromKnownWorkers() {
  if (!state.h63ModelOptionsPayload) return;
  const next = h63CatalogFromPayload(state.h63ModelOptionsPayload);
  const current = state.h62ModelCatalog || {};
  if (next.provider !== current.provider || next.models.join("\n") !== (current.models || []).join("\n")) {
    h63ApplyModelCatalog(next);
  }
}

const h63PreviousLoadSessionData = loadSessionData;
loadSessionData = async function h63LoadSessionData() {
  const result = await h63PreviousLoadSessionData();
  h63RefreshCatalogFromKnownWorkers();
  return result;
};

function h63DecorateLocaleSelect() {
  const selector = $("localeSelect");
  const locales = window.HermesHarnessLocales || {};
  if (!selector) return;
  for (const option of selector.options) {
    const definition = locales[option.value] || {};
    const flag = H63_LOCALE_FLAGS[option.value] || "🌐";
    const label = definition.label || option.value.toUpperCase();
    option.textContent = `${flag} ${label}`;
  }
}

function h63InstallDagStyles() {
  if ($("h63DagStyle")) return;
  const style = document.createElement("style");
  style.id = "h63DagStyle";
  style.textContent = `
    .h5-dag-canvas.h63-single-stage{overflow-x:hidden!important;scrollbar-gutter:auto!important;width:100%;max-width:100%}
    .h5-dag-canvas.h63-single-stage .h5-dag-levels{grid-auto-flow:row!important;grid-template-columns:minmax(0,1fr)!important;grid-auto-columns:auto!important;width:100%!important;min-width:0!important;max-width:100%!important}
    .h5-dag-canvas.h63-single-stage .h5-dag-level{width:100%;min-width:0;max-width:100%}
    .h5-dag-canvas.h63-single-stage .h5-task-node{width:100%;min-width:0;max-width:100%}
  `;
  document.head.appendChild(style);
}

function h63NormalizeDag() {
  const canvas = $("taskList")?.querySelector(".h5-dag-canvas");
  if (!canvas) return;
  const levels = canvas.querySelectorAll(".h5-dag-level");
  const singleStage = levels.length <= 1;
  canvas.classList.toggle("h63-single-stage", singleStage);
  if (singleStage || canvas.scrollWidth <= canvas.clientWidth + 1) canvas.scrollLeft = 0;
  requestAnimationFrame(() => {
    if (singleStage) canvas.scrollLeft = 0;
    if (typeof h5DrawEdges === "function") h5DrawEdges();
  });
}

function h63ObserveTaskGraph() {
  const root = $("taskList");
  if (!root || root.__h63Observer) return;
  const observer = new MutationObserver(() => requestAnimationFrame(h63NormalizeDag));
  observer.observe(root, { childList: true, subtree: true });
  root.__h63Observer = observer;
}

function h63Init() {
  h63DecorateLocaleSelect();
  h63InstallDagStyles();
  h63ObserveTaskGraph();
  h63NormalizeDag();
  window.addEventListener("resize", () => requestAnimationFrame(h63NormalizeDag));
  window.addEventListener("hermes-harness-ui-change", (event) => {
    if (event.detail?.kind === "locale") h63DecorateLocaleSelect();
    if (["locale", "layout"].includes(event.detail?.kind)) requestAnimationFrame(h63NormalizeDag);
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", h63Init, { once: true });
else h63Init();
