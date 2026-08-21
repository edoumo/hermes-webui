"use strict";

const cfg = window.__HERMES_HARNESS__ || {};
const state = {
  sessions: [],
  sessionId: null,
  workers: [],
  workerId: null,
  tasks: [],
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

function renderSessions() {
  const root = $("sessionList");
  root.replaceChildren();
  if (!state.sessions.length) {
    root.append(el("div", "muted", "No Hermes API sessions yet."));
    return;
  }
  for (const session of state.sessions) {
    const btn = el("button", `list-item${session.id === state.sessionId ? " active" : ""}`);
    btn.type = "button";
    btn.append(el("div", "list-title", session.title));
    const meta = el("div", "list-meta");
    meta.append(el("span", "", session.id));
    const model = session.model || session.model_id || "session";
    meta.append(el("span", "", model));
    btn.append(meta);
    btn.addEventListener("click", () => selectSession(session.id));
    root.append(btn);
  }
}

function renderWorkers() {
  const root = $("workerList");
  root.replaceChildren();
  $("workerCount").textContent = `${state.workers.length} worker${state.workers.length === 1 ? "" : "s"}`;
  if (!state.sessionId) {
    root.append(el("div", "muted", "Select a session."));
    return;
  }
  if (!state.workers.length) {
    root.append(el("div", "muted", "No durable workers in this session."));
    return;
  }
  for (const worker of state.workers) {
    const btn = el("button", `list-item${worker.worker_id === state.workerId ? " active" : ""}`);
    btn.type = "button";
    btn.append(el("div", "list-title", worker.label || worker.worker_id));
    const meta = el("div", "list-meta");
    meta.append(el("span", `state ${String(worker.status || "").toLowerCase()}`, worker.status || "unknown"));
    meta.append(el("span", "", worker.role || "leaf"));
    btn.append(meta);
    btn.addEventListener("click", () => selectWorker(worker.worker_id));
    root.append(btn);
  }
  fillTaskWorkerSelect();
}

function fillTaskWorkerSelect() {
  const select = $("taskWorkerSelect");
  select.replaceChildren(new Option("Unassigned", ""));
  for (const worker of state.workers) {
    select.add(new Option(worker.label || worker.worker_id, worker.worker_id));
  }
}

function eventNode(kind, timestamp, body, stateName) {
  const item = el("article", "event");
  const head = el("div", "event-head");
  head.append(el("span", "", kind));
  head.append(el("span", stateName ? `state ${String(stateName).toLowerCase()}` : "", stateName || formatTime(timestamp)));
  item.append(head);
  item.append(el("div", "event-body", body || ""));
  return item;
}

function formatTime(value) {
  if (!value) return "";
  const num = Number(value);
  const date = Number.isFinite(num) ? new Date(num * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function renderMessages(items) {
  const root = $("messageList");
  root.replaceChildren();
  if (!items.length) return root.append(el("div", "muted", "No durable messages."));
  for (const message of items) {
    const direction = message.direction === "worker" ? "WORKER" : "PARENT";
    root.append(eventNode(direction, message.created_at, message.content, message.state));
  }
}

function renderActivations(items) {
  const root = $("activationList");
  root.replaceChildren();
  if (!items.length) return root.append(el("div", "muted", "No activations yet."));
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
  if (!state.tasks.length) return root.append(el("div", "muted", "No tasks for this session."));
  for (const task of state.tasks) {
    const card = el("article", `task${task.ready ? " ready" : ""}`);
    card.append(el("div", "task-title", task.subject || task.task_id));
    card.append(el("div", "task-desc", task.description || (task.ready ? "Ready" : "No description")));
    const meta = el("div", "list-meta");
    meta.append(el("span", `state ${String(task.status || "").toLowerCase()}`, task.status || "pending"));
    meta.append(el("span", "", `rev ${task.revision ?? "?"}`));
    card.append(meta);
    if (Array.isArray(task.blocked_by) && task.blocked_by.length) {
      card.append(el("div", "task-desc", `Blocked by: ${task.blocked_by.join(", ")}`));
    }
    const actions = el("div", "task-actions");
    if (task.status === "pending") actions.append(taskAction(task, "in_progress", "Start"));
    if (task.status === "in_progress") actions.append(taskAction(task, "completed", "Complete"));
    if (!["completed", "failed", "cancelled"].includes(task.status)) actions.append(taskAction(task, "failed", "Fail"));
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

async function loadSessions({ selectFirst = true } = {}) {
  const payload = await api("/api/harness/sessions");
  state.sessions = sessionItems(payload);
  const exists = state.sessions.some((s) => s.id === state.sessionId);
  if (!exists) state.sessionId = selectFirst && state.sessions.length ? state.sessions[0].id : null;
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
    showToast("Hermes API session created");
  } catch (error) { showToast(error.message, true); }
}

async function selectSession(sessionId) {
  state.sessionId = safeId(sessionId);
  state.workerId = null;
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
    setConnection("online", "Hermes API connected");
  } catch (error) {
    if (state.sessionId !== sid) return;
    setConnection("error", "Hermes API unavailable");
    showToast(error.message, true);
  }
}

async function selectWorker(workerId) {
  state.workerId = safeId(workerId);
  renderWorkers();
  await loadWorkerDetail();
}

function clearWorkerView() {
  $("workerView").classList.add("hidden");
  $("emptyState").classList.remove("hidden");
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
    $("workerTitle").textContent = worker.label || worker.worker_id;
    $("workerIdLabel").textContent = worker.worker_id;
    const chips = $("workerChips");
    chips.replaceChildren();
    [worker.status, worker.role, worker.model, ...(worker.toolsets || [])].filter(Boolean).forEach((value) => chips.append(el("span", "chip", value)));
    renderMessages(Array.isArray(messages.items) ? messages.items.slice(0, 50) : []);
    renderActivations(Array.isArray(activations.items) ? activations.items.slice(0, 50) : []);
  } catch (error) {
    if (state.sessionId === sid && state.workerId === wid) showToast(error.message, true);
  }
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
  $("eventState").textContent = "events connecting";

  source.addEventListener("open", () => {
    if (state.events !== source || state.eventsSessionId !== sid) return;
    $("eventState").textContent = "events live";
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
    $("eventState").textContent = "events reconnecting";
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
  if (!message) return showToast("Message is empty", true);
  const body = { message };
  const messageId = $("messageIdInput").value.trim();
  if (messageId) body.message_id = messageId;
  try {
    await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(state.workerId)}/messages`, { method: "POST", body });
    $("messageInput").value = "";
    await loadSessionData();
    showToast("Message queued durably");
  } catch (error) { showToast(error.message, true); }
}

async function runWorker() {
  if (!state.sessionId || !state.workerId) return;
  const button = $("runWorkerBtn");
  button.disabled = true;
  try {
    const result = await api(`/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(state.workerId)}/run`, { method: "POST", body: {} });
    showToast(`Activation ${result.activation_id || "started"}`);
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
    showToast("Durable worker created");
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
    showToast("Task created");
  } catch (error) { showToast(error.message, true); }
}

function wire() {
  $("refreshAllBtn").addEventListener("click", () => loadSessions({ selectFirst: false }));
  $("newSessionBtn").addEventListener("click", createSession);
  $("newWorkerBtn").addEventListener("click", () => {
    if (!state.sessionId) return showToast("Select or create a session first", true);
    $("workerDialog").showModal();
  });
  $("newTaskBtn").addEventListener("click", () => $("taskDialog").showModal());
  $("sendMessageBtn").addEventListener("click", queueMessage);
  $("runWorkerBtn").addEventListener("click", runWorker);
  $("workerForm").addEventListener("submit", createWorkerFromDialog);
  $("taskForm").addEventListener("submit", createTaskFromDialog);
  window.addEventListener("beforeunload", closeEvents);
}

async function boot() {
  wire();
  try {
    await api("/api/harness/models");
    setConnection("online", "Hermes API connected");
    await loadSessions();
  } catch (error) {
    setConnection("error", "Hermes API unavailable");
    showToast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", boot, { once: true });
