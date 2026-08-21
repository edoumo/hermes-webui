"use strict";

const cfg = window.__HERMES_HARNESS__ || {};
const ui = window.HarnessUI || {};
const t = (key, vars = {}) => typeof ui.t === "function" ? ui.t(key, vars) : key;
const statusLabel = (value) => typeof ui.statusLabel === "function" ? ui.statusLabel(value) : String(value || "");
const roleLabel = (value) => typeof ui.roleLabel === "function" ? ui.roleLabel(value) : String(value || "");

const state = {
  sessions: [],
  sessionId: null,
  workers: [],
  workerId: null,
  currentWorker: null,
  tasks: [],
  models: [],
  events: null,
  eventsSessionId: null,
  eventLastId: null,
  refreshTimer: null,
};

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = String(message || "Done");
  toast.classList.remove("hidden", "error");
  if (isError) toast.classList.add("error");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add("hidden"), 4200);
}

function setConnection(kind, label) {
  const dot = $("statusDot");
  dot.classList.remove("online", "error");
  if (kind) dot.classList.add(kind);
  $("connectionLabel").textContent = label;
}

async function api(path, options = {}) {
  const opts = { credentials: "include", ...options };
  const method = String(opts.method || "GET").toUpperCase();
  const headers = new Headers(opts.headers || {});
  headers.set("Accept", "application/json");
  if (opts.body !== undefined && opts.body !== null) {
    headers.set("Content-Type", "application/json");
    if (typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  }
  if (method !== "GET" && method !== "HEAD" && cfg.csrfToken) {
    headers.set("X-Hermes-CSRF-Token", cfg.csrfToken);
  }
  opts.headers = headers;
  const response = await fetch(path, opts);
  const text = await response.text();
  let payload = {};
  if (text) {
    try { payload = JSON.parse(text); }
    catch (_) { payload = { error: text }; }
  }
  if (!response.ok) {
    const message = payload?.error?.message || payload?.error || payload?.message || `${response.status} ${response.statusText}`;
    const error = new Error(String(message));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function safeId(value) {
  const text = String(value || "");
  if (!/^[A-Za-z0-9._:-]{1,256}$/.test(text)) throw new Error("Unsafe object id from server");
  return text;
}

function sessionFrom(item) {
  const raw = item?.session || item || {};
  const id = raw.session_id || raw.id || item?.session_id || item?.id;
  if (!id) return null;
  return {
    ...raw,
    id: String(id),
    title: raw.title || raw.name || raw.label || String(id),
  };
}

function sessionItems(payload) {
  const candidates = payload?.data || payload?.sessions || payload?.items || [];
  return Array.isArray(candidates) ? candidates.map(sessionFrom).filter(Boolean) : [];
}

function modelItems(payload) {
  const candidates = payload?.data || payload?.models || payload?.items || [];
  if (!Array.isArray(candidates)) return [];
  const values = [];
  for (const item of candidates) {
    const value = typeof item === "string" ? item : (item?.id || item?.model || item?.name);
    const cleaned = String(value || "").trim();
    if (cleaned && !values.includes(cleaned)) values.push(cleaned);
  }
  return values;
}

function visibleSessions() {
  const showArchived = typeof ui.showArchivedSessions === "function" && ui.showArchivedSessions();
  return state.sessions.filter((session) => showArchived || !(ui.isSessionArchived?.(session.id)));
}

function visibleWorkers() {
  const showArchived = typeof ui.showArchivedWorkers === "function" && ui.showArchivedWorkers();
  return state.workers.filter((worker) => showArchived || worker.status !== "DISABLED");
}

function updateArchiveControls() {
  const sessionArchive = $("archiveSessionBtn");
  if (sessionArchive) sessionArchive.disabled = !state.sessionId;
  const showSessions = $("showArchivedSessionsBtn");
  if (showSessions) {
    const showing = !!ui.showArchivedSessions?.();
    showSessions.title = t(showing ? "hideArchivedSessions" : "showArchivedSessions");
    showSessions.setAttribute("aria-label", showSessions.title);
  }
  const showWorkers = $("showArchivedWorkersBtn");
  if (showWorkers) {
    const showing = !!ui.showArchivedWorkers?.();
    showWorkers.title = t(showing ? "hideArchivedWorkers" : "showArchivedWorkers");
    showWorkers.setAttribute("aria-label", showWorkers.title);
  }
}

function renderSessions() {
  const root = $("sessionList");
  root.replaceChildren();
  const sessions = visibleSessions();
  if (!sessions.length) {
    root.append(el("div", "muted", t("noSessions")));
    updateArchiveControls();
    return;
  }
  for (const session of sessions) {
    const archived = !!ui.isSessionArchived?.(session.id);
    const btn = el("button", `list-item${session.id === state.sessionId ? " active" : ""}${archived ? " archived" : ""}`);
    btn.type = "button";
    btn.append(el("div", "list-title", session.title));
    const meta = el("div", "list-meta");
    meta.append(el("span", "", session.id));
    const model = session.model || session.model_id || "session";
    meta.append(el("span", archived ? "state disabled" : "", archived ? t("archive") : model));
    btn.append(meta);
    btn.addEventListener("click", () => selectSession(session.id));
    root.append(btn);
  }
  updateArchiveControls();
}

function renderWorkers() {
  const root = $("workerList");
  root.replaceChildren();
  const workers = visibleWorkers();
  const suffix = state.workers.length === 1 ? "" : "s";
  $("workerCount").textContent = t("workerCount", { count: state.workers.length, suffix });
  if (!state.sessionId) {
    root.append(el("div", "muted", t("selectSession")));
    updateArchiveControls();
    return;
  }
  if (!workers.length) {
    root.append(el("div", "muted", t("noWorkers")));
    updateArchiveControls();
    return;
  }
  for (const worker of workers) {
    const archived = worker.status === "DISABLED";
    const btn = el("button", `list-item${worker.worker_id === state.workerId ? " active" : ""}${archived ? " archived" : ""}`);
    btn.type = "button";
    btn.append(el("div", "list-title", worker.label || worker.worker_id));
    const meta = el("div", "list-meta");
    meta.append(el("span", `state ${String(worker.status || "").toLowerCase()}`, statusLabel(worker.status || "unknown")));
    meta.append(el("span", "", roleLabel(worker.role || "leaf")));
    btn.append(meta);
    btn.addEventListener("click", () => selectWorker(worker.worker_id));
    root.append(btn);
  }
  fillTaskWorkerSelect();
  updateArchiveControls();
}

function fillTaskWorkerSelect() {
  const select = $("taskWorkerSelect");
  select.replaceChildren(new Option(t("unassigned"), ""));
  for (const worker of state.workers.filter((item) => item.status !== "DISABLED")) {
    select.add(new Option(worker.label || worker.worker_id, worker.worker_id));
  }
}

function fillModelOptions() {
  const datalist = $("modelOptions");
  if (!datalist) return;
  datalist.replaceChildren();
  for (const model of state.models) datalist.append(new Option(model, model));
}

function eventNode(kind, timestamp, body, stateName) {
  const item = el("article", "event");
  const head = el("div", "event-head");
  head.append(el("span", "", kind));
  head.append(el("span", stateName ? `state ${String(stateName).toLowerCase()}` : "", stateName ? statusLabel(stateName) : formatTime(timestamp)));
  item.append(head);
  item.append(el("div", "event-body", body || ""));
  return item;
}

function formatTime(value) {
  if (!value) return "";
  const num = Number(value);
  const date = Number.isFinite(num) ? new Date(num * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(ui.locale?.() || undefined);
}

function renderMessages(items) {
  const root = $("messageList");
  root.replaceChildren();
  if (!items.length) return root.append(el("div", "muted", t("noMessages")));
  for (const message of items) {
    const direction = message.direction === "worker" ? "WORKER" : "PARENT";
    root.append(eventNode(direction, message.created_at, message.content, message.state));
  }
}

function renderActivations(items) {
  const root = $("activationList");
  root.replaceChildren();
  if (!items.length) return root.append(el("div", "muted", t("noActivations")));
  for (const activation of items) {
    const bits = [activation.activation_id];
    if (activation.subagent_id) bits.push(`subagent ${activation.subagent_id}`);
    if (activation.summary) bits.push(activation.summary);
    if (activation.error) bits.push(`Error: ${activation.error}`);
    root.append(eventNode("ACTIVATION", activation.started_at, bits.join("\n"), activation.state));
  }
}

function renderTasks() {
  const root = $("taskList");
  root.replaceChildren();
  if (!state.tasks.length) return root.append(el("div", "muted", t("noTasks")));
  for (const task of state.tasks) {
    const card = el("article", `task${task.ready ? " ready" : ""}`);
    card.append(el("div", "task-title", task.subject || task.task_id));
    card.append(el("div", "task-desc", task.description || (task.ready ? t("readyDescription") : t("noDescription"))));
    const meta = el("div", "list-meta");
    meta.append(el("span", `state ${String(task.status || "").toLowerCase()}`, statusLabel(task.status || "pending")));
    meta.append(el("span", "", `rev ${task.revision ?? "?"}`));
    card.append(meta);
    if (Array.isArray(task.blocked_by) && task.blocked_by.length) {
      card.append(el("div", "task-desc", `${t("blockedBy")}: ${task.blocked_by.join(", ")}`));
    }
    const actions = el("div", "task-actions");
    if (task.status === "pending") actions.append(taskAction(task, "in_progress", t("running")));
    if (task.status === "in_progress") actions.append(taskAction(task, "completed", t("completed")));
    if (!["completed", "failed", "cancelled"].includes(task.status)) actions.append(taskAction(task, "failed", t("failed")));
    card.append(actions);
    root.append(card);
  }
}

function taskAction(task, nextStatus, label) {
  const button = el("button", "ghost", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    try {
      await api(`/api/harness/sessions/${safeId(state.sessionId)}/worker-tasks/${safeId(task.task_id)}/status`, {
        method: "POST",
        body: { status: nextStatus, expected_revision: task.revision },
      });
      await loadSessionData();
    } catch (error) { showToast(error.message, true); }
  });
  return button;
}

async function loadModels() {
  try {
    const payload = await api("/api/harness/models");
    state.models = modelItems(payload);
    fillModelOptions();
  } catch (_) {
    state.models = [];
    fillModelOptions();
  }
}

async function loadSessions({ selectFirst = true } = {}) {
  const payload = await api("/api/harness/sessions");
  state.sessions = sessionItems(payload);
  const exists = state.sessions.some((s) => s.id === state.sessionId);
  if (!exists) state.sessionId = selectFirst && visibleSessions().length ? visibleSessions()[0].id : null;
  renderSessions();
  if (state.sessionId) await loadSessionData();
  else {
    closeEvents();
    clearWorkerView();
  }
}

async function createSession() {
  try {
    const payload = await api("/api/harness/sessions", { method: "POST", body: {} });
    const created = sessionFrom(payload?.session || payload);
    await loadSessions({ selectFirst: false });
    if (created) await selectSession(created.id);
    showToast(t("sessionCreated"));
  } catch (error) { showToast(error.message, true); }
}

async function archiveSelectedSession() {
  if (!state.sessionId || typeof ui.setSessionArchived !== "function") return;
  const current = state.sessionId;
  const alreadyArchived = !!ui.isSessionArchived?.(current);
  if (alreadyArchived) {
    ui.setSessionArchived(current, false);
    renderSessions();
    return;
  }
  if (!window.confirm(t("archiveSessionConfirm"))) return;
  ui.setSessionArchived(current, true);
  if (!ui.showArchivedSessions?.()) {
    const next = visibleSessions().find((session) => session.id !== current);
    state.sessionId = next?.id || null;
    state.workerId = null;
    state.currentWorker = null;
    renderSessions();
    if (state.sessionId) await loadSessionData();
    else {
      closeEvents();
      state.workers = [];
      state.tasks = [];
      renderWorkers();
      renderTasks();
      clearWorkerView();
    }
  } else {
    renderSessions();
  }
}

async function selectSession(sessionId) {
  state.sessionId = safeId(sessionId);
  state.workerId = null;
  state.currentWorker = null;
  renderSessions();
  await loadSessionData();
}

async function loadSessionData() {
  if (!state.sessionId) return;
  const sid = safeId(state.sessionId);
  try {
    const [workers, tasks] = await Promise.all([
      api(`/api/harness/sessions/${sid}/workers?limit=100`),
      api(`/api/harness/sessions/${sid}/worker-tasks?limit=100`),
    ]);
    if (state.sessionId !== sid) return;
    state.workers = Array.isArray(workers.items) ? workers.items.slice(0, 100) : [];
    state.tasks = Array.isArray(tasks.items) ? tasks.items.slice(0, 100) : [];
    if (state.workerId && !state.workers.some((w) => w.worker_id === state.workerId)) state.workerId = null;
    renderWorkers();
    renderTasks();
    startEvents();
    if (state.workerId) await loadWorkerDetail(); else clearWorkerView();
    setConnection("online", t("apiConnected"));
  } catch (error) {
    if (state.sessionId !== sid) return;
    setConnection("error", t("apiUnavailable"));
    showToast(error.message, true);
  }
}

async function selectWorker(workerId) {
  state.workerId = safeId(workerId);
  renderWorkers();
  await loadWorkerDetail();
}

function clearWorkerView() {
  state.currentWorker = null;
  $("workerView").classList.add("hidden");
  $("emptyState").classList.remove("hidden");
}

function renderWorkerIdentity(worker) {
  state.currentWorker = worker;
  $("workerTitle").textContent = worker.label || worker.worker_id;
  $("workerIdLabel").textContent = worker.worker_id;
  const chips = $("workerChips");
  chips.replaceChildren();
  const values = [statusLabel(worker.status), roleLabel(worker.role), worker.model, ...(worker.toolsets || [])].filter(Boolean);
  values.forEach((value) => chips.append(el("span", "chip", value)));
  const settings = $("workerSettingsBtn");
  if (settings) settings.disabled = worker.status === "RUNNING";
}

async function loadWorkerDetail() {
  if (!state.sessionId || !state.workerId) return clearWorkerView();
  const sid = safeId(state.sessionId), wid = safeId(state.workerId);
  try {
    const [workerPayload, messages, activations] = await Promise.all([
      api(`/api/harness/sessions/${sid}/workers/${wid}`),
      api(`/api/harness/sessions/${sid}/workers/${wid}/messages?limit=50`),
      api(`/api/harness/sessions/${sid}/workers/${wid}/activations?limit=50`),
    ]);
    if (state.sessionId !== sid || state.workerId !== wid) return;
    const worker = workerPayload.worker || workerPayload;
    $("emptyState").classList.add("hidden");
    $("workerView").classList.remove("hidden");
    renderWorkerIdentity(worker);
    renderMessages(Array.isArray(messages.items) ? messages.items.slice(0, 50) : []);
    renderActivations(Array.isArray(activations.items) ? activations.items.slice(0, 50) : []);
  } catch (error) {
    if (state.sessionId === sid && state.workerId === wid) showToast(error.message, true);
  }
}

function openWorkerSettings() {
  const worker = state.currentWorker || state.workers.find((item) => item.worker_id === state.workerId);
  if (!worker) return;
  $("workerSettingsLabel").value = worker.label || "";
  $("workerSettingsModel").value = worker.model || "";
  $("workerSettingsToolsets").value = (worker.toolsets || []).join(", ");
  const archived = worker.status === "DISABLED";
  $("archiveWorkerBtn").hidden = archived;
  $("restoreWorkerBtn").hidden = !archived;
  $("saveWorkerSettingsBtn").disabled = worker.status === "RUNNING" || archived;
  $("workerSettingsLabel").disabled = archived;
  $("workerSettingsModel").disabled = archived;
  $("workerSettingsToolsets").disabled = archived;
  $("workerSettingsDialog").showModal();
}

async function saveWorkerSettings(event) {
  event.preventDefault();
  const worker = state.currentWorker;
  if (!state.sessionId || !worker) return;
  const toolsets = $("workerSettingsToolsets").value.split(",").map((value) => value.trim()).filter(Boolean);
  const model = $("workerSettingsModel").value.trim();
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(worker.worker_id)}/edit`, {
      method: "POST",
      body: {
        label: $("workerSettingsLabel").value.trim(),
        model: model || null,
        toolsets: [...new Set(toolsets)],
        expected_revision: worker.revision,
      },
    });
    state.currentWorker = result.worker || state.currentWorker;
    $("workerSettingsDialog").close();
    await loadSessionData();
    showToast(t("workerUpdated"));
  } catch (error) { showToast(error.message, true); }
}

async function setCurrentWorkerArchived(archived) {
  const worker = state.currentWorker;
  if (!state.sessionId || !worker) return;
  if (archived && !window.confirm(t("archiveWorkerConfirm"))) return;
  try {
    await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(worker.worker_id)}/${archived ? "archive" : "restore"}`, {
      method: "POST",
      body: { expected_revision: worker.revision },
    });
    $("workerSettingsDialog").close();
    if (archived && !ui.showArchivedWorkers?.()) {
      state.workerId = null;
      state.currentWorker = null;
    }
    await loadSessionData();
    showToast(t(archived ? "workerArchived" : "workerRestored"));
  } catch (error) { showToast(error.message, true); }
}

function closeEvents() {
  if (state.events) state.events.close();
  state.events = null;
  state.eventsSessionId = null;
  state.eventLastId = null;
}

function startEvents() {
  if (!state.sessionId) {
    closeEvents();
    return;
  }
  const sid = safeId(state.sessionId);
  if (state.events && state.eventsSessionId === sid) return;

  closeEvents();
  const source = new EventSource(`/api/harness/sessions/${sid}/worker-events`, { withCredentials: true });
  state.events = source;
  state.eventsSessionId = sid;
  $("eventState").textContent = t("eventsConnecting");

  source.addEventListener("open", () => {
    if (state.events !== source || state.eventsSessionId !== sid) return;
    $("eventState").textContent = t("eventsLive");
  });
  source.addEventListener("durable_workers.changed", (event) => {
    if (state.events !== source || state.eventsSessionId !== sid || state.sessionId !== sid) return;
    const eventId = String(event.lastEventId || "").trim();
    if (eventId && eventId === state.eventLastId) return;
    if (eventId) state.eventLastId = eventId;
    scheduleRefresh(sid);
  });
  source.addEventListener("error", () => {
    if (state.events !== source || state.eventsSessionId !== sid) return;
    $("eventState").textContent = t("eventsReconnecting");
  });
}

function scheduleRefresh(sessionId = state.sessionId) {
  const sid = sessionId ? safeId(sessionId) : null;
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => {
    if (sid && state.sessionId === sid) loadSessionData();
  }, 180);
}

async function queueMessage() {
  if (!state.sessionId || !state.workerId) return;
  const message = $("messageInput").value.trim();
  if (!message) return showToast(t("messageEmpty"), true);
  const body = { message };
  const messageId = $("messageIdInput").value.trim();
  if (messageId) body.message_id = messageId;
  try {
    await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(state.workerId)}/messages`, { method: "POST", body });
    $("messageInput").value = "";
    await loadSessionData();
    showToast(t("messageQueued"));
  } catch (error) { showToast(error.message, true); }
}

async function runWorker() {
  if (!state.sessionId || !state.workerId) return;
  const button = $("runWorkerBtn");
  button.disabled = true;
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(state.workerId)}/run`, { method: "POST", body: {} });
    showToast(t("activationStarted", { id: result.activation_id || "started" }));
    scheduleRefresh();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function createWorkerFromDialog(event) {
  event.preventDefault();
  const form = $("workerForm"), data = new FormData(form);
  const body = {
    label: String(data.get("label") || "").trim(),
    role: String(data.get("role") || "leaf"),
  };
  const model = String(data.get("model") || "").trim();
  if (model) body.model = model;
  const toolsets = String(data.get("toolsets") || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (toolsets.length) body.toolsets = [...new Set(toolsets)];
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers`, { method: "POST", body });
    $("workerDialog").close();
    form.reset();
    await loadSessionData();
    if (result.worker?.worker_id) await selectWorker(result.worker.worker_id);
    showToast(t("workerCreated"));
  } catch (error) { showToast(error.message, true); }
}

async function createTaskFromDialog(event) {
  event.preventDefault();
  const form = $("taskForm"), data = new FormData(form);
  const body = {
    subject: String(data.get("subject") || "").trim(),
    description: String(data.get("description") || ""),
  };
  const workerId = String(data.get("worker_id") || "").trim();
  if (workerId) body.worker_id = workerId;
  try {
    await api(`/api/harness/sessions/${safeId(state.sessionId)}/worker-tasks`, { method: "POST", body });
    $("taskDialog").close();
    form.reset();
    await loadSessionData();
    showToast(t("taskCreated"));
  } catch (error) { showToast(error.message, true); }
}

function wire() {
  $("refreshAllBtn").addEventListener("click", () => loadSessions({ selectFirst: false }));
  $("newSessionBtn").addEventListener("click", createSession);
  $("archiveSessionBtn").addEventListener("click", archiveSelectedSession);
  $("newWorkerBtn").addEventListener("click", () => {
    if (!state.sessionId) return showToast(t("selectSession"), true);
    $("workerDialog").showModal();
  });
  $("workerSettingsBtn").addEventListener("click", openWorkerSettings);
  $("archiveWorkerBtn").addEventListener("click", () => setCurrentWorkerArchived(true));
  $("restoreWorkerBtn").addEventListener("click", () => setCurrentWorkerArchived(false));
  $("newTaskBtn").addEventListener("click", () => $("taskDialog").showModal());
  $("sendMessageBtn").addEventListener("click", queueMessage);
  $("runWorkerBtn").addEventListener("click", runWorker);
  $("workerForm").addEventListener("submit", createWorkerFromDialog);
  $("workerSettingsForm").addEventListener("submit", saveWorkerSettings);
  $("taskForm").addEventListener("submit", createTaskFromDialog);
  window.addEventListener("beforeunload", closeEvents);
  window.addEventListener("hermes-harness-ui-change", (event) => {
    const kind = event.detail?.kind;
    ui.applyStaticTranslations?.();
    updateArchiveControls();
    if (kind === "layout") {
      if (typeof h5DrawEdges === "function") requestAnimationFrame(h5DrawEdges);
      return;
    }
    renderSessions();
    renderWorkers();
    if (kind === "locale") {
      if (state.sessionId) loadSessionData();
      else renderTasks();
    }
  });
}

async function boot() {
  wire();
  ui.applyStaticTranslations?.();
  ui.applyRailPreferences?.();
  try {
    await loadModels();
    setConnection("online", t("apiConnected"));
    await loadSessions();
  } catch (error) {
    setConnection("error", t("apiUnavailable"));
    showToast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", boot, { once: true });
