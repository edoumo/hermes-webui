"use strict";

// H6.2 is a narrow human-UAT polish layer. It extends the H6.1 Harness without
// taking ownership of session/worker state, SSE, task orchestration, auth or
// CSRF. The rich model inventory comes from Hermes' existing /api/model/options
// endpoint; only models belonging to the active provider are offered because a
// Durable Worker currently persists a model id, not a provider credential set.
state.h62ModelCatalog = { provider: "", currentModel: "", models: [] };

const H62_CUSTOM_MODEL = "__custom_model__";

function h62UniqueStrings(values) {
  const result = [];
  for (const value of values || []) {
    const cleaned = String(value || "").trim();
    if (cleaned && !result.includes(cleaned)) result.push(cleaned);
  }
  return result;
}

function h62ProviderSlug(row) {
  return String(row?.slug || row?.provider || row?.id || row?.name || "").trim();
}

function h62CatalogFromPayload(payload) {
  const providers = Array.isArray(payload?.providers) ? payload.providers : [];
  const currentProvider = String(payload?.provider || "").trim();
  const currentModel = String(payload?.model || "").trim();
  const normalizedProvider = currentProvider.toLowerCase();

  let row = providers.find((candidate) => h62ProviderSlug(candidate).toLowerCase() === normalizedProvider);
  if (!row && currentModel) {
    row = providers.find((candidate) =>
      Array.isArray(candidate?.models) && candidate.models.some((model) => String(model) === currentModel)
    );
  }

  const models = h62UniqueStrings(row?.models || []);
  if (currentModel && !models.includes(currentModel)) models.unshift(currentModel);
  return {
    provider: currentProvider || h62ProviderSlug(row),
    currentModel,
    models,
  };
}

function h62FillModelSelect(selectId, customInputId, currentValue = "") {
  const select = $(selectId);
  const custom = $(customInputId);
  if (!select || !custom) return;

  const models = h62UniqueStrings(state.h62ModelCatalog?.models || state.models || []);
  select.replaceChildren();
  select.add(new Option(t("gatewayDefault"), ""));
  for (const model of models) select.add(new Option(model, model));
  select.add(new Option(t("customModel"), H62_CUSTOM_MODEL));

  const value = String(currentValue || "").trim();
  if (!value) {
    select.value = "";
    custom.value = "";
    custom.classList.add("hidden");
  } else if (models.includes(value)) {
    select.value = value;
    custom.value = "";
    custom.classList.add("hidden");
  } else {
    select.value = H62_CUSTOM_MODEL;
    custom.value = value;
    custom.classList.remove("hidden");
  }
}

function h62SyncCustomModelInput(selectId, customInputId) {
  const select = $(selectId);
  const custom = $(customInputId);
  if (!select || !custom) return;
  const isCustom = select.value === H62_CUSTOM_MODEL;
  custom.classList.toggle("hidden", !isCustom);
  if (isCustom) custom.focus();
}

function h62SelectedModel(selectId, customInputId) {
  const select = $(selectId);
  const custom = $(customInputId);
  if (!select) return null;
  if (select.value === H62_CUSTOM_MODEL) {
    const explicit = String(custom?.value || "").trim();
    return explicit || null;
  }
  return String(select.value || "").trim() || null;
}

function h62UpdateModelHelp() {
  const provider = String(state.h62ModelCatalog?.provider || "").trim();
  const text = provider
    ? `${t("modelHelp")} ${t("activeProvider", { provider })}`
    : t("modelCatalogUnavailable");
  for (const id of ["workerModelHelp", "workerSettingsModelHelp"]) {
    const node = $(id);
    if (node) node.textContent = text;
  }
}

const h62FoundationLoadModels = loadModels;
loadModels = async function h62LoadModels() {
  try {
    const payload = await api("/api/harness/model-options");
    state.h62ModelCatalog = h62CatalogFromPayload(payload);
    state.models = state.h62ModelCatalog.models.slice();
  } catch (_) {
    await h62FoundationLoadModels();
    state.h62ModelCatalog = {
      provider: "",
      currentModel: state.models[0] || "",
      models: h62UniqueStrings(state.models),
    };
  }
  h62FillModelSelect("workerModelSelect", "workerCustomModelInput", state.h62ModelCatalog.currentModel);
  h62FillModelSelect("workerSettingsModelSelect", "workerSettingsCustomModelInput", "");
  h62UpdateModelHelp();
};

createSession = function h62OpenSessionDialog() {
  const form = $("sessionForm");
  if (form) form.reset();
  $("sessionDialog")?.showModal();
};

async function h62CreateSessionFromDialog(event) {
  event.preventDefault();
  const form = $("sessionForm");
  const title = String($("sessionTitleInput")?.value || "").trim();
  const body = title ? { title } : {};
  try {
    const payload = await api("/api/harness/sessions", { method: "POST", body });
    const created = sessionFrom(payload?.session || payload);
    $("sessionDialog")?.close();
    form?.reset();
    await loadSessions({ selectFirst: false });
    if (created) await selectSession(created.id);
    showToast(t("sessionCreated"));
  } catch (error) { showToast(error.message, true); }
}

openWorkerSettings = function h62OpenWorkerSettings() {
  const worker = state.currentWorker || state.workers.find((item) => item.worker_id === state.workerId);
  if (!worker) return;
  $("workerSettingsLabel").value = worker.label || "";
  $("workerSettingsToolsets").value = (worker.toolsets || []).join(", ");
  h62FillModelSelect("workerSettingsModelSelect", "workerSettingsCustomModelInput", worker.model || "");
  h62UpdateModelHelp();
  const archived = worker.status === "DISABLED";
  $("archiveWorkerBtn").hidden = archived;
  $("restoreWorkerBtn").hidden = !archived;
  $("saveWorkerSettingsBtn").disabled = worker.status === "RUNNING" || archived;
  $("workerSettingsLabel").disabled = archived;
  $("workerSettingsModelSelect").disabled = archived;
  $("workerSettingsCustomModelInput").disabled = archived;
  $("workerSettingsToolsets").disabled = archived;
  $("workerSettingsDialog").showModal();
};

saveWorkerSettings = async function h62SaveWorkerSettings(event) {
  event.preventDefault();
  const worker = state.currentWorker;
  if (!state.sessionId || !worker) return;
  const toolsets = $("workerSettingsToolsets").value.split(",").map((value) => value.trim()).filter(Boolean);
  const model = h62SelectedModel("workerSettingsModelSelect", "workerSettingsCustomModelInput");
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(worker.worker_id)}/edit`, {
      method: "POST",
      body: {
        label: $("workerSettingsLabel").value.trim(),
        model,
        toolsets: [...new Set(toolsets)],
        expected_revision: worker.revision,
      },
    });
    state.currentWorker = result.worker || state.currentWorker;
    $("workerSettingsDialog").close();
    await loadSessionData();
    showToast(t("workerUpdated"));
  } catch (error) { showToast(error.message, true); }
};

createWorkerFromDialog = async function h62CreateWorkerFromDialog(event) {
  event.preventDefault();
  const form = $("workerForm"), data = new FormData(form);
  const body = {
    label: String(data.get("label") || "").trim(),
    role: String(data.get("role") || "leaf"),
  };
  const model = h62SelectedModel("workerModelSelect", "workerCustomModelInput");
  if (model) body.model = model;
  const toolsets = String(data.get("toolsets") || "").split(",").map((value) => value.trim()).filter(Boolean);
  if (toolsets.length) body.toolsets = [...new Set(toolsets)];
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers`, { method: "POST", body });
    $("workerDialog").close();
    form.reset();
    h62FillModelSelect("workerModelSelect", "workerCustomModelInput", state.h62ModelCatalog.currentModel);
    await loadSessionData();
    if (result.worker?.worker_id) await selectWorker(result.worker.worker_id);
    showToast(t("workerCreated"));
  } catch (error) { showToast(error.message, true); }
};

const h62FoundationWire = wire;
wire = function h62Wire() {
  h62FoundationWire();
  $("sessionForm")?.addEventListener("submit", h62CreateSessionFromDialog);
  $("workerModelSelect")?.addEventListener("change", () => h62SyncCustomModelInput("workerModelSelect", "workerCustomModelInput"));
  $("workerSettingsModelSelect")?.addEventListener("change", () => h62SyncCustomModelInput("workerSettingsModelSelect", "workerSettingsCustomModelInput"));
  window.addEventListener("hermes-harness-ui-change", (event) => {
    if (event.detail?.kind !== "locale") return;
    const createValue = h62SelectedModel("workerModelSelect", "workerCustomModelInput") || state.h62ModelCatalog.currentModel;
    const settingsValue = h62SelectedModel("workerSettingsModelSelect", "workerSettingsCustomModelInput");
    h62FillModelSelect("workerModelSelect", "workerCustomModelInput", createValue);
    h62FillModelSelect("workerSettingsModelSelect", "workerSettingsCustomModelInput", settingsValue);
    h62UpdateModelHelp();
  });
};
