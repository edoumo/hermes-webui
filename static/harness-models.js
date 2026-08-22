"use strict";

// Harness Models panel.
//
// Hermes remains the single source of truth. This layer reads the stock
// /api/model/options + /api/model/auxiliary projections through the Harness
// BFF and persists assignments through stock /api/model/set. It owns no
// provider catalog, credential store or parallel model configuration.
(function installHarnessModelsPanel() {
  const text = {
    en: {
      models: "Models",
      title: "Hermes models",
      intro: "Configure the Hermes main model and specialized auxiliary models. Changes apply to new sessions and auxiliary calls.",
      main: "Main model",
      provider: "Provider",
      model: "Model",
      saveMain: "Save main model",
      auxiliary: "Specialized models",
      auxiliaryHelp: "Each function inherits the main model by default. Override only where a dedicated model is useful.",
      inherit: "Use main model",
      save: "Save",
      resetAll: "Reset all to main model",
      refresh: "Refresh catalog",
      close: "Close",
      loading: "Loading Hermes model configuration…",
      unavailable: "Hermes model configuration is unavailable.",
      saved: "Model configuration saved.",
      reset: "All specialized models now inherit the main model.",
      confirmReset: "Reset every specialized Hermes model to inherit the main model?",
      newSessions: "Main-model changes apply to new sessions; an already-running worker keeps its current model unless edited separately.",
      noModels: "No models advertised by this provider.",
    },
    fr: {
      models: "Modèles",
      title: "Modèles Hermes",
      intro: "Configurez le modèle principal de Hermes et ses modèles auxiliaires spécialisés. Les modifications s’appliquent aux nouvelles sessions et aux appels auxiliaires.",
      main: "Modèle principal",
      provider: "Provider",
      model: "Modèle",
      saveMain: "Enregistrer le modèle principal",
      auxiliary: "Modèles spécialisés",
      auxiliaryHelp: "Chaque fonction hérite par défaut du modèle principal. Ne définissez un modèle spécifique que lorsque c’est utile.",
      inherit: "Utiliser le modèle principal",
      save: "Enregistrer",
      resetAll: "Tout réinitialiser sur le modèle principal",
      refresh: "Actualiser le catalogue",
      close: "Fermer",
      loading: "Chargement de la configuration des modèles Hermes…",
      unavailable: "La configuration des modèles Hermes est indisponible.",
      saved: "Configuration du modèle enregistrée.",
      reset: "Tous les modèles spécialisés héritent maintenant du modèle principal.",
      confirmReset: "Réinitialiser tous les modèles Hermes spécialisés pour qu’ils héritent du modèle principal ?",
      newSessions: "Le changement du modèle principal s’applique aux nouvelles sessions ; un worker déjà lancé conserve son modèle tant qu’il n’est pas modifié séparément.",
      noModels: "Aucun modèle annoncé par ce provider.",
    },
  };

  function language() {
    const locale = window.HarnessUI?.locale?.() || document.documentElement.lang || "en";
    return String(locale).toLowerCase().startsWith("fr") ? "fr" : "en";
  }

  function tx(key) {
    const lang = language();
    return text[lang]?.[key] || text.en[key] || key;
  }

  function modelId(item) {
    if (typeof item === "string") return item.trim();
    return String(item?.id || item?.model || item?.name || "").trim();
  }

  function providerId(row) {
    return String(row?.slug || row?.provider || row?.id || row?.name || "").trim();
  }

  function providerModels(row) {
    const values = [];
    for (const item of Array.isArray(row?.models) ? row.models : []) {
      const id = modelId(item);
      if (id && !values.includes(id)) values.push(id);
    }
    return values;
  }

  function humanTask(task) {
    return String(task || "")
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function addStyle() {
    if (document.getElementById("harnessModelsStyle")) return;
    const style = document.createElement("style");
    style.id = "harnessModelsStyle";
    style.textContent = `
      #modelsDialog{width:min(980px,calc(100vw - 32px));max-height:88vh;padding:0;overflow:hidden}
      #modelsDialog .hm-shell{display:flex;flex-direction:column;max-height:88vh}
      #modelsDialog .hm-body{padding:18px 20px 22px;overflow:auto;display:grid;gap:18px}
      #modelsDialog .hm-section{border:1px solid var(--border-color,#2f3642);border-radius:12px;padding:16px;display:grid;gap:12px}
      #modelsDialog .hm-section h3{margin:0}
      #modelsDialog .hm-note{margin:0;opacity:.72;font-size:.9rem}
      #modelsDialog .hm-grid{display:grid;grid-template-columns:minmax(150px,.8fr) minmax(220px,1.4fr);gap:10px}
      #modelsDialog .hm-grid label,#modelsDialog .hm-aux-row label{display:grid;gap:5px}
      #modelsDialog select{width:100%}
      #modelsDialog .hm-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
      #modelsDialog .hm-aux-list{display:grid;gap:10px}
      #modelsDialog .hm-aux-row{display:grid;grid-template-columns:minmax(150px,.9fr) minmax(160px,.9fr) minmax(220px,1.3fr) auto;gap:8px;align-items:end;padding:10px;border-radius:10px;background:color-mix(in srgb,currentColor 4%,transparent)}
      #modelsDialog .hm-task{align-self:center;font-weight:600}
      #modelsDialog .hm-status{min-height:1.2em;margin:0;opacity:.8}
      @media(max-width:760px){#modelsDialog .hm-grid,#modelsDialog .hm-aux-row{grid-template-columns:1fr}#modelsDialog .hm-task{margin-bottom:2px}}
    `;
    document.head.appendChild(style);
  }

  function installButton() {
    if (document.getElementById("modelsBtn")) return;
    const connection = document.querySelector(".topbar .connection");
    if (!connection) return;
    const button = document.createElement("button");
    button.id = "modelsBtn";
    button.type = "button";
    button.className = "ghost";
    button.textContent = tx("models");
    button.addEventListener("click", openModels);
    const refresh = document.getElementById("refreshAllBtn");
    if (refresh) connection.insertBefore(button, refresh);
    else connection.appendChild(button);
  }

  function buildDialog() {
    if (document.getElementById("modelsDialog")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "modelsDialog";
    dialog.innerHTML = `
      <div class="hm-shell">
        <div class="dialog-head">
          <div><h2 id="hmTitle"></h2><p class="hm-note" id="hmIntro"></p></div>
          <button type="button" class="icon-btn" id="hmCloseTop">×</button>
        </div>
        <div class="hm-body">
          <p class="hm-status" id="hmStatus"></p>
          <section class="hm-section">
            <h3 id="hmMainTitle"></h3>
            <div class="hm-grid">
              <label><span id="hmMainProviderLabel"></span><select id="hmMainProvider"></select></label>
              <label><span id="hmMainModelLabel"></span><select id="hmMainModel"></select></label>
            </div>
            <p class="hm-note" id="hmMainNote"></p>
            <div class="hm-actions"><button type="button" class="primary" id="hmSaveMain"></button></div>
          </section>
          <section class="hm-section">
            <h3 id="hmAuxTitle"></h3>
            <p class="hm-note" id="hmAuxHelp"></p>
            <div class="hm-aux-list" id="hmAuxList"></div>
            <div class="hm-actions">
              <button type="button" class="ghost" id="hmResetAux"></button>
              <button type="button" class="ghost" id="hmRefresh"></button>
            </div>
          </section>
          <div class="hm-actions"><button type="button" class="ghost" id="hmCloseBottom"></button></div>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    document.getElementById("hmCloseTop").addEventListener("click", () => dialog.close());
    document.getElementById("hmCloseBottom").addEventListener("click", () => dialog.close());
    document.getElementById("hmSaveMain").addEventListener("click", saveMain);
    document.getElementById("hmResetAux").addEventListener("click", resetAux);
    document.getElementById("hmRefresh").addEventListener("click", () => loadConfiguration(true));
    document.getElementById("hmMainProvider").addEventListener("change", fillMainModels);
  }

  function translateDialog() {
    const button = document.getElementById("modelsBtn");
    if (button) button.textContent = tx("models");
    const values = {
      hmTitle: "title", hmIntro: "intro", hmMainTitle: "main",
      hmMainProviderLabel: "provider", hmMainModelLabel: "model",
      hmMainNote: "newSessions", hmSaveMain: "saveMain", hmAuxTitle: "auxiliary",
      hmAuxHelp: "auxiliaryHelp", hmResetAux: "resetAll", hmRefresh: "refresh",
      hmCloseBottom: "close",
    };
    for (const [id, key] of Object.entries(values)) {
      const node = document.getElementById(id);
      if (node) node.textContent = tx(key);
    }
  }

  const panel = {
    options: null,
    auxiliary: null,
    providers: [],
  };

  function normalizeProviders(payload) {
    const rows = [];
    for (const row of Array.isArray(payload?.providers) ? payload.providers : []) {
      const id = providerId(row);
      if (!id) continue;
      rows.push({ id, label: String(row?.name || row?.label || id), models: providerModels(row) });
    }
    return rows;
  }

  function providerRow(id) {
    return panel.providers.find((row) => row.id.toLowerCase() === String(id || "").toLowerCase()) || null;
  }

  function fillProviderSelect(select, value, includeAuto = false) {
    select.replaceChildren();
    if (includeAuto) select.add(new Option(tx("inherit"), "auto"));
    for (const row of panel.providers) select.add(new Option(row.label, row.id));
    if (value && ![...select.options].some((option) => option.value === value)) {
      select.add(new Option(value, value));
    }
    select.value = value || (includeAuto ? "auto" : panel.providers[0]?.id || "");
  }

  function fillModelSelect(select, provider, value) {
    select.replaceChildren();
    if (provider === "auto") {
      select.add(new Option(tx("inherit"), ""));
      select.disabled = true;
      return;
    }
    select.disabled = false;
    const models = providerRow(provider)?.models || [];
    if (!models.length) select.add(new Option(tx("noModels"), ""));
    for (const id of models) select.add(new Option(id, id));
    if (value && !models.includes(value)) select.add(new Option(value, value));
    select.value = value || models[0] || "";
  }

  function fillMainModels() {
    const provider = document.getElementById("hmMainProvider").value;
    const current = panel.auxiliary?.main || {};
    const desired = provider === current.provider ? current.model : "";
    fillModelSelect(document.getElementById("hmMainModel"), provider, desired);
  }

  function renderMain() {
    const main = panel.auxiliary?.main || {};
    fillProviderSelect(document.getElementById("hmMainProvider"), main.provider || panel.options?.provider || "", false);
    fillModelSelect(
      document.getElementById("hmMainModel"),
      document.getElementById("hmMainProvider").value,
      main.model || panel.options?.model || "",
    );
  }

  function renderAuxiliary() {
    const root = document.getElementById("hmAuxList");
    root.replaceChildren();
    const tasks = Array.isArray(panel.auxiliary?.tasks) ? panel.auxiliary.tasks : [];
    for (const task of tasks) {
      const row = document.createElement("div");
      row.className = "hm-aux-row";
      const title = document.createElement("div");
      title.className = "hm-task";
      title.textContent = humanTask(task.task);

      const providerLabel = document.createElement("label");
      const providerText = document.createElement("span");
      providerText.textContent = tx("provider");
      const provider = document.createElement("select");
      fillProviderSelect(provider, task.provider || "auto", true);
      providerLabel.append(providerText, provider);

      const modelLabel = document.createElement("label");
      const modelText = document.createElement("span");
      modelText.textContent = tx("model");
      const model = document.createElement("select");
      fillModelSelect(model, provider.value, task.model || "");
      modelLabel.append(modelText, model);

      provider.addEventListener("change", () => fillModelSelect(model, provider.value, ""));

      const save = document.createElement("button");
      save.type = "button";
      save.className = "ghost";
      save.textContent = tx("save");
      save.addEventListener("click", async () => {
        try {
          save.disabled = true;
          await persistAssignment({
            scope: "auxiliary",
            task: task.task,
            provider: provider.value,
            model: provider.value === "auto" ? "" : model.value,
          });
          await loadConfiguration(false);
          setStatus(tx("saved"));
        } catch (error) {
          setStatus(error.message || String(error), true);
        } finally {
          save.disabled = false;
        }
      });

      row.append(title, providerLabel, modelLabel, save);
      root.appendChild(row);
    }
  }

  function setStatus(message, error = false) {
    const node = document.getElementById("hmStatus");
    if (!node) return;
    node.textContent = String(message || "");
    node.style.color = error ? "var(--danger,#ef6b73)" : "";
  }

  async function persistAssignment(payload) {
    let result = await api("/api/harness/model-set", { method: "POST", body: payload });
    if (result?.confirm_required) {
      if (!window.confirm(String(result.confirm_message || "Confirm model selection?"))) {
        throw new Error("Model change cancelled");
      }
      result = await api("/api/harness/model-set", {
        method: "POST",
        body: { ...payload, confirm_expensive_model: true },
      });
    }
    if (result?.ok === false) throw new Error(String(result?.error || result?.message || "Model assignment failed"));
    return result;
  }

  async function saveMain() {
    const button = document.getElementById("hmSaveMain");
    const provider = document.getElementById("hmMainProvider").value;
    const model = document.getElementById("hmMainModel").value;
    if (!provider || !model) return setStatus(tx("noModels"), true);
    try {
      button.disabled = true;
      await persistAssignment({ scope: "main", provider, model });
      await loadConfiguration(false);
      if (typeof loadModels === "function") await loadModels();
      setStatus(tx("saved"));
    } catch (error) {
      setStatus(error.message || String(error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function resetAux() {
    if (!window.confirm(tx("confirmReset"))) return;
    const button = document.getElementById("hmResetAux");
    try {
      button.disabled = true;
      await persistAssignment({ scope: "auxiliary", task: "__reset__", provider: "auto", model: "" });
      await loadConfiguration(false);
      setStatus(tx("reset"));
    } catch (error) {
      setStatus(error.message || String(error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function loadConfiguration(refreshCatalog = false) {
    setStatus(tx("loading"));
    const suffix = refreshCatalog ? "?refresh=true" : "";
    try {
      const [options, auxiliary] = await Promise.all([
        api(`/api/harness/model-options${suffix}`),
        api("/api/harness/model-auxiliary"),
      ]);
      panel.options = options;
      panel.auxiliary = auxiliary;
      panel.providers = normalizeProviders(options);
      renderMain();
      renderAuxiliary();
      setStatus("");
    } catch (error) {
      setStatus(`${tx("unavailable")} ${error.message || error}`, true);
    }
  }

  async function openModels() {
    translateDialog();
    const dialog = document.getElementById("modelsDialog");
    dialog.showModal();
    await loadConfiguration(false);
  }

  function init() {
    addStyle();
    buildDialog();
    installButton();
    translateDialog();
    window.addEventListener("hermes-harness-ui-change", (event) => {
      if (event.detail?.kind === "locale") translateDialog();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
