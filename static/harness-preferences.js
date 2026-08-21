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

  const MESSAGES = {
    en: {
      brandSubtitle: "Durable worker control plane",
      connecting: "Connecting",
      refresh: "Refresh",
      scope: "Scope",
      sessions: "Sessions",
      workersEyebrow: "Durable identities",
      workers: "Workers",
      durableVisible: "Durable work, visible.",
      selectWorkerHelp: "Select a Hermes session and a worker to inspect its durable transcript, activations and task graph.",
      runNext: "Run next",
      workerSettings: "Worker settings",
      newDurableMessage: "New durable message",
      messagePlaceholder: "Give this worker its next mandate…",
      idempotencyPlaceholder: "Optional idempotency key",
      queueMessage: "Queue message",
      boundedHistory: "Bounded history",
      messages: "Messages",
      runtimeInstances: "Runtime instances",
      activations: "Activations",
      latest50: "latest 50",
      dependencyGraph: "Dependency graph",
      tasks: "Tasks",
      newTask: "New task",
      createWorker: "Create durable worker",
      editWorker: "Worker settings",
      label: "Label",
      role: "Role",
      leaf: "Leaf",
      orchestrator: "Orchestrator",
      model: "Model",
      gatewayDefault: "Gateway default",
      modelHelp: "Choose an advertised model or type an explicit model id.",
      toolsets: "Toolsets",
      cancel: "Cancel",
      create: "Create",
      save: "Save",
      archiveWorker: "Archive worker",
      restoreWorker: "Restore worker",
      archiveWorkerConfirm: "Archive this worker? Its durable history will be preserved and the worker can be restored later.",
      archiveSession: "Archive selected session",
      archiveSessionConfirm: "Archive this session from the Harness list? This only hides it in this browser; Hermes session data is not deleted.",
      showArchivedSessions: "Show archived sessions",
      hideArchivedSessions: "Hide archived sessions",
      showArchivedWorkers: "Show archived workers",
      hideArchivedWorkers: "Hide archived workers",
      restoreSession: "Restore session",
      collapsePanel: "Collapse panel",
      expandPanel: "Expand panel",
      themeLight: "Use light theme",
      themeDark: "Use dark theme",
      language: "Language",
      createTask: "Create task",
      subject: "Subject",
      description: "Description",
      assignWorker: "Assign worker",
      unassigned: "Unassigned",
      noSessions: "No Hermes API sessions yet.",
      noWorkers: "No durable workers in this session.",
      selectSession: "Select a session.",
      noMessages: "No durable messages.",
      noActivations: "No activations yet.",
      noTasks: "No tasks for this session.",
      apiConnected: "Hermes API connected",
      apiUnavailable: "Hermes API unavailable",
      eventsIdle: "events idle",
      eventsConnecting: "events connecting",
      eventsLive: "events live",
      eventsReconnecting: "events reconnecting",
      workerCount: "{count} worker{suffix}",
      messageEmpty: "Message is empty",
      messageQueued: "Message queued durably",
      sessionCreated: "Hermes API session created",
      workerCreated: "Durable worker created",
      workerUpdated: "Worker settings saved",
      workerArchived: "Worker archived",
      workerRestored: "Worker restored",
      taskCreated: "Task created",
      activationStarted: "Activation {id}",
      stage: "Stage {number}",
      ready: "READY",
      blocked: "BLOCKED",
      running: "RUNNING",
      completed: "COMPLETED",
      failed: "FAILED",
      cancelled: "CANCELLED",
      dormant: "DORMANT",
      disabled: "ARCHIVED",
      edit: "Edit",
      dispatch: "Dispatch",
      completeBlockers: "Complete blockers first",
      assignWorkerFirst: "Assign a worker first",
      workerNotDormant: "Worker is not dormant",
      dispatchReady: "Dispatch ready task",
      taskUpdated: "Task updated",
      addDependency: "Add dependency…",
      add: "Add",
      blockedBy: "Blocked by",
      none: "none",
      worker: "Worker",
      resetPending: "Reset to pending",
      recoverTask: "Recover task",
      recoverTaskTitle: "Restore the failed task, its worker and durable message for redispatch",
      graphSummary: "{ready} ready · {blocked} blocked · {running} running · {done} done",
      graphTruncated: "Graph truncated to 100 tasks.",
      taskDispatched: "Task dispatched as {id}",
      taskRecoveryReady: "Task recovery ready ({id})",
      retryFailed: "Retry failed",
      cancelActivation: "Cancel activation",
      cancellationRequested: "Cancellation requested",
      opsIdle: "ops idle",
      opsUnavailable: "ops unavailable",
      opsSummary: "session active {active} · failed {failed} · cap {cap}",
      workerRetryReady: "Worker ready to retry ({id})",
      cancelActivationConfirm: "Cancel activation {id}? The durable message will only be requeued after the child is confirmed cancelled.",
      cancellationWaiting: "Cancellation requested; waiting for terminal child state",
      cancellationAcknowledged: "Cancellation acknowledged",
      showArchived: "Show archived",
      archive: "Archive",
      restore: "Restore",
      noDescription: "No description",
      readyDescription: "Ready",
      removeDependency: "Remove dependency",
    },
    fr: {
      brandSubtitle: "Plan de contrôle des workers durables",
      connecting: "Connexion…",
      refresh: "Actualiser",
      scope: "Périmètre",
      sessions: "Sessions",
      workersEyebrow: "Identités durables",
      workers: "Workers",
      durableVisible: "Le travail durable, visible.",
      selectWorkerHelp: "Sélectionnez une session Hermes et un worker pour consulter son historique durable, ses activations et son graphe de tâches.",
      runNext: "Exécuter la suivante",
      workerSettings: "Paramètres du worker",
      newDurableMessage: "Nouveau message durable",
      messagePlaceholder: "Donnez à ce worker son prochain mandat…",
      idempotencyPlaceholder: "Clé d’idempotence facultative",
      queueMessage: "Mettre en file",
      boundedHistory: "Historique borné",
      messages: "Messages",
      runtimeInstances: "Instances d’exécution",
      activations: "Activations",
      latest50: "50 derniers",
      dependencyGraph: "Graphe de dépendances",
      tasks: "Tâches",
      newTask: "Nouvelle tâche",
      createWorker: "Créer un worker durable",
      editWorker: "Paramètres du worker",
      label: "Nom",
      role: "Rôle",
      leaf: "Feuille",
      orchestrator: "Orchestrateur",
      model: "Modèle",
      gatewayDefault: "Modèle par défaut de la passerelle",
      modelHelp: "Choisissez un modèle annoncé ou saisissez directement son identifiant.",
      toolsets: "Jeux d’outils",
      cancel: "Annuler",
      create: "Créer",
      save: "Enregistrer",
      archiveWorker: "Archiver le worker",
      restoreWorker: "Restaurer le worker",
      archiveWorkerConfirm: "Archiver ce worker ? Son historique durable sera conservé et il pourra être restauré plus tard.",
      archiveSession: "Archiver la session sélectionnée",
      archiveSessionConfirm: "Archiver cette session dans la liste Harness ? Elle sera seulement masquée dans ce navigateur ; les données Hermes ne seront pas supprimées.",
      showArchivedSessions: "Afficher les sessions archivées",
      hideArchivedSessions: "Masquer les sessions archivées",
      showArchivedWorkers: "Afficher les workers archivés",
      hideArchivedWorkers: "Masquer les workers archivés",
      restoreSession: "Restaurer la session",
      collapsePanel: "Rabattre le volet",
      expandPanel: "Déplier le volet",
      themeLight: "Utiliser le thème clair",
      themeDark: "Utiliser le thème sombre",
      language: "Langue",
      createTask: "Créer une tâche",
      subject: "Sujet",
      description: "Description",
      assignWorker: "Affecter un worker",
      unassigned: "Non affectée",
      noSessions: "Aucune session Hermes API pour le moment.",
      noWorkers: "Aucun worker durable dans cette session.",
      selectSession: "Sélectionnez une session.",
      noMessages: "Aucun message durable.",
      noActivations: "Aucune activation pour le moment.",
      noTasks: "Aucune tâche dans cette session.",
      apiConnected: "Hermes API connecté",
      apiUnavailable: "Hermes API indisponible",
      eventsIdle: "événements inactifs",
      eventsConnecting: "connexion aux événements",
      eventsLive: "événements en direct",
      eventsReconnecting: "reconnexion aux événements",
      workerCount: "{count} worker{suffix}",
      messageEmpty: "Le message est vide",
      messageQueued: "Message mis en file durablement",
      sessionCreated: "Session Hermes API créée",
      workerCreated: "Worker durable créé",
      workerUpdated: "Paramètres du worker enregistrés",
      workerArchived: "Worker archivé",
      workerRestored: "Worker restauré",
      taskCreated: "Tâche créée",
      activationStarted: "Activation {id}",
      stage: "Étape {number}",
      ready: "PRÊTE",
      blocked: "BLOQUÉE",
      running: "EN COURS",
      completed: "TERMINÉE",
      failed: "ÉCHEC",
      cancelled: "ANNULÉE",
      dormant: "EN VEILLE",
      disabled: "ARCHIVÉ",
      edit: "Modifier",
      dispatch: "Lancer",
      completeBlockers: "Terminez d’abord les tâches bloquantes",
      assignWorkerFirst: "Affectez d’abord un worker",
      workerNotDormant: "Le worker n’est pas en veille",
      dispatchReady: "Lancer la tâche prête",
      taskUpdated: "Tâche mise à jour",
      addDependency: "Ajouter une dépendance…",
      add: "Ajouter",
      blockedBy: "Bloquée par",
      none: "aucune",
      worker: "Worker",
      resetPending: "Remettre en attente",
      recoverTask: "Récupérer la tâche",
      recoverTaskTitle: "Restaurer la tâche en échec, son worker et son message durable pour la relancer",
      graphSummary: "{ready} prêtes · {blocked} bloquées · {running} en cours · {done} terminées",
      graphTruncated: "Graphe limité aux 100 premières tâches.",
      taskDispatched: "Tâche lancée via {id}",
      taskRecoveryReady: "Tâche prête à être récupérée ({id})",
      retryFailed: "Réessayer après échec",
      cancelActivation: "Annuler l’activation",
      cancellationRequested: "Annulation demandée",
      opsIdle: "opérations inactives",
      opsUnavailable: "opérations indisponibles",
      opsSummary: "session actives {active} · échecs {failed} · capacité {cap}",
      workerRetryReady: "Worker prêt à réessayer ({id})",
      cancelActivationConfirm: "Annuler l’activation {id} ? Le message durable ne sera remis en file qu’après confirmation de l’arrêt du child.",
      cancellationWaiting: "Annulation demandée ; attente de l’état terminal du child",
      cancellationAcknowledged: "Annulation prise en compte",
      showArchived: "Afficher les archivés",
      archive: "Archiver",
      restore: "Restaurer",
      noDescription: "Aucune description",
      readyDescription: "Prête",
      removeDependency: "Supprimer la dépendance",
    },
  };

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

  let locale = storageGet(KEYS.locale, "");
  if (!MESSAGES[locale]) {
    locale = String(navigator.language || "en").toLowerCase().startsWith("fr") ? "fr" : "en";
  }
  let theme = storageGet(KEYS.theme, "dark");
  if (!new Set(["dark", "light"]).has(theme)) theme = "dark";

  function t(key, vars = {}) {
    const template = MESSAGES[locale]?.[key] ?? MESSAGES.en[key] ?? key;
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
    if (!MESSAGES[next]) return;
    locale = next;
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
