"use strict";

// H5 loads after H4 and before the synthetic DOMContentLoaded boot event.
// It reuses H3/H4 state, auth, CSRF and SSE ownership; no EventSource is added.
state.h5Graph = null;
state.h5GraphSessionId = null;

function h5InstallStyles() {
  if ($("h5DagStyle")) return;
  const style = document.createElement("style");
  style.id = "h5DagStyle";
  style.textContent = `
    .tasks-card.h5-expanded{overflow:hidden}
    .h5-graph-summary{display:flex;gap:.55rem;flex-wrap:wrap;align-items:center;font-size:.78rem;opacity:.78}
    .h5-dag-canvas{position:relative;overflow-x:auto;overflow-y:visible;padding:.75rem 0 1rem;min-height:180px;max-width:100%;scrollbar-gutter:stable}
    .h5-dag-edges{position:absolute;inset:0;pointer-events:none;overflow:visible;opacity:.35}
    .h5-dag-levels{position:relative;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(280px,1fr);gap:1rem;width:100%;min-width:max(100%,calc(var(--h5-stage-count,1) * 300px));padding:.25rem .5rem}
    .h5-dag-level{display:flex;flex-direction:column;gap:.75rem;min-width:0;z-index:1}
    .h5-level-label{font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;opacity:.55;padding:0 .15rem}
    .h5-task-node{min-width:0;border:1px solid currentColor;border-radius:10px;padding:.75rem;background:var(--panel,#111);display:flex;flex-direction:column;gap:.55rem;overflow:hidden}
    .h5-task-node.h5-ready{box-shadow:0 0 0 1px currentColor inset}
    .h5-task-head{display:flex;justify-content:space-between;gap:.5rem;align-items:flex-start;min-width:0}
    .h5-task-title{font-weight:650;overflow-wrap:anywhere;min-width:0}
    .h5-task-meta,.h5-task-actions,.h5-dependency-row,.h5-assignment-row{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;min-width:0}
    .h5-task-desc{font-size:.82rem;opacity:.76;white-space:pre-wrap;overflow-wrap:anywhere}
    .h5-task-node select,.h5-task-node input,.h5-task-node textarea{max-width:100%;min-width:0}
    .h5-task-node select{flex:1 1 150px}
    .h5-dependency-chip{display:inline-flex;gap:.25rem;align-items:center;border:1px solid currentColor;border-radius:999px;padding:.15rem .4rem;font-size:.72rem;opacity:.8;max-width:100%}
    .h5-dependency-chip>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .h5-chip-remove{border:0;background:transparent;color:inherit;padding:0 .1rem;cursor:pointer;font-size:.9rem}
    .h5-task-editor{display:grid;gap:.4rem;padding-top:.4rem;border-top:1px solid currentColor}
    .h5-task-editor.hidden{display:none}
    .h5-task-editor textarea{resize:vertical;min-height:64px}
    .h5-truncated{font-size:.75rem;opacity:.7;padding:.5rem}
    @media(max-width:780px){.h5-dag-levels{grid-auto-columns:minmax(250px,1fr);min-width:max(100%,calc(var(--h5-stage-count,1) * 270px))}}
  `;
  document.head.appendChild(style);
}

function h5InstallSummary() {
  const card = document.querySelector(".tasks-card");
  if (card) card.classList.add("h5-expanded");
  const head = card?.querySelector(".card-head");
  if (!head || $("taskGraphSummary")) return;
  const summary = el("div", "h5-graph-summary", "");
  summary.id = "taskGraphSummary";
  const actions = head.querySelector("button")?.parentElement || head;
  actions.insertBefore(summary, actions.lastElementChild || null);
}

function h5Worker(workerId) {
  return state.workers.find((worker) => worker.worker_id === workerId) || null;
}

function h5TaskMap() {
  return new Map((state.h5Graph?.tasks || []).map((task) => [task.task_id, task]));
}

function h5Levels(tasks) {
  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const cache = new Map();
  function level(taskId, visiting = new Set()) {
    if (cache.has(taskId)) return cache.get(taskId);
    if (visiting.has(taskId)) return 0;
    const task = byId.get(taskId);
    if (!task) return 0;
    const next = new Set(visiting);
    next.add(taskId);
    const blockerLevels = (task.blocked_by || [])
      .filter((id) => byId.has(id))
      .map((id) => level(id, next));
    const value = blockerLevels.length ? Math.max(...blockerLevels) + 1 : 0;
    cache.set(taskId, value);
    return value;
  }
  const groups = new Map();
  for (const task of tasks) {
    const value = level(task.task_id);
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(task);
  }
  return [...groups.entries()].sort((a, b) => a[0] - b[0]);
}

function h5StatusLabel(task) {
  if (task.status === "pending") return task.ready ? t("ready") : t("blocked");
  return statusLabel(task.status || "unknown");
}

async function h5Post(task, suffix, body) {
  const sid = safeId(state.sessionId);
  const tid = safeId(task.task_id);
  return api(`/api/harness/sessions/${sid}/worker-tasks/${tid}/${suffix}`, {
    method: "POST",
    body,
  });
}

async function h5AssignWorker(task, workerId) {
  try {
    await h5Post(task, "edit", {
      worker_id: workerId || null,
      expected_revision: task.revision,
    });
    await loadSessionData();
  } catch (error) { showToast(error.message, true); }
}

async function h5SaveEdit(task, editor) {
  const subject = editor.querySelector('[data-h5-field="subject"]').value.trim();
  const description = editor.querySelector('[data-h5-field="description"]').value;
  if (!subject) return showToast(t("subject") + " required", true);
  try {
    await h5Post(task, "edit", {
      subject,
      description,
      expected_revision: task.revision,
    });
    await loadSessionData();
    showToast(t("taskUpdated"));
  } catch (error) { showToast(error.message, true); }
}

async function h5AddDependency(task, blockedBy) {
  if (!blockedBy) return;
  try {
    await h5Post(task, "dependencies/add", {
      blocked_by_task_id: blockedBy,
      expected_revision: task.revision,
    });
    await loadSessionData();
  } catch (error) { showToast(error.message, true); }
}

async function h5RemoveDependency(task, blockedBy) {
  try {
    await h5Post(task, "dependencies/remove", {
      blocked_by_task_id: blockedBy,
      expected_revision: task.revision,
    });
    await loadSessionData();
  } catch (error) { showToast(error.message, true); }
}

async function h5Dispatch(task) {
  if (!task.ready || task.status !== "pending" || !task.worker_id) return;
  try {
    const result = await h5Post(task, "dispatch", {
      expected_revision: task.revision,
    });
    showToast(t("taskDispatched", { id: result.activation_id || "activation" }));
    scheduleRefresh(state.sessionId);
  } catch (error) { showToast(error.message, true); }
}

async function h5ResetTask(task) {
  try {
    await api(
      `/api/harness/sessions/${safeId(state.sessionId)}/worker-tasks/${safeId(task.task_id)}/status`,
      {
        method: "POST",
        body: { status: "pending", expected_revision: task.revision },
      },
    );
    await loadSessionData();
  } catch (error) { showToast(error.message, true); }
}

function h5TaskNode(task, allTasks) {
  const node = el("article", `h5-task-node${task.ready ? " h5-ready" : ""}`);
  node.dataset.h5TaskId = task.task_id;

  const head = el("div", "h5-task-head");
  head.append(el("div", "h5-task-title", task.subject || task.task_id));
  head.append(el("span", `state ${String(task.status || "").toLowerCase()}`, h5StatusLabel(task)));
  node.append(head);

  if (task.description) node.append(el("div", "h5-task-desc", task.description));
  const meta = el("div", "h5-task-meta");
  meta.append(el("span", "", `rev ${task.revision ?? "?"}`));
  if (task.last_run?.activation_id) meta.append(el("span", "", statusLabel(task.last_run.state || "run")));
  node.append(meta);

  const assignment = el("div", "h5-assignment-row");
  assignment.append(el("span", "muted", t("worker")));
  const select = document.createElement("select");
  select.append(new Option(t("unassigned"), ""));
  for (const worker of state.workers.filter((item) => item.status !== "DISABLED")) {
    select.append(new Option(worker.label || worker.worker_id, worker.worker_id));
  }
  select.value = task.worker_id || "";
  select.disabled = task.status !== "pending";
  select.addEventListener("change", () => h5AssignWorker(task, select.value));
  assignment.append(select);
  if (task.worker_id) {
    const worker = h5Worker(task.worker_id);
    assignment.append(el("span", `state ${String(worker?.status || "").toLowerCase()}`, statusLabel(worker?.status || "unknown")));
  }
  node.append(assignment);

  const blockers = el("div", "h5-dependency-row");
  blockers.append(el("span", "muted", t("blockedBy")));
  if (!(task.blocked_by || []).length) blockers.append(el("span", "", t("none")));
  for (const blockerId of task.blocked_by || []) {
    const blocker = allTasks.get(blockerId);
    const chip = el("span", "h5-dependency-chip");
    chip.append(el("span", "", blocker?.subject || blockerId));
    if (task.status === "pending") {
      const remove = el("button", "h5-chip-remove", "×");
      remove.type = "button";
      remove.title = t("removeDependency");
      remove.addEventListener("click", () => h5RemoveDependency(task, blockerId));
      chip.append(remove);
    }
    blockers.append(chip);
  }
  node.append(blockers);

  if (task.status === "pending") {
    const dependency = el("div", "h5-dependency-row");
    const depSelect = document.createElement("select");
    depSelect.append(new Option(t("addDependency"), ""));
    const existing = new Set(task.blocked_by || []);
    for (const candidate of allTasks.values()) {
      if (candidate.task_id === task.task_id || existing.has(candidate.task_id)) continue;
      depSelect.append(new Option(candidate.subject || candidate.task_id, candidate.task_id));
    }
    const add = el("button", "ghost", t("add"));
    add.type = "button";
    add.disabled = depSelect.options.length <= 1;
    add.addEventListener("click", () => h5AddDependency(task, depSelect.value));
    dependency.append(depSelect, add);
    node.append(dependency);
  }

  const actions = el("div", "h5-task-actions");
  if (task.status === "pending") {
    const edit = el("button", "ghost", t("edit"));
    edit.type = "button";
    actions.append(edit);

    const dispatch = el("button", "primary", t("dispatch"));
    dispatch.type = "button";
    const worker = task.worker_id ? h5Worker(task.worker_id) : null;
    dispatch.disabled = !task.ready || !task.worker_id || worker?.status !== "DORMANT";
    dispatch.title = !task.ready
      ? t("completeBlockers")
      : (!task.worker_id ? t("assignWorkerFirst") : (worker?.status !== "DORMANT" ? t("workerNotDormant") : t("dispatchReady")));
    dispatch.addEventListener("click", () => h5Dispatch(task));
    actions.append(dispatch);

    const editor = el("div", "h5-task-editor hidden");
    const subject = document.createElement("input");
    subject.dataset.h5Field = "subject";
    subject.maxLength = 300;
    subject.value = task.subject || "";
    const description = document.createElement("textarea");
    description.dataset.h5Field = "description";
    description.maxLength = 16000;
    description.value = task.description || "";
    const save = el("button", "primary", t("save"));
    save.type = "button";
    save.addEventListener("click", () => h5SaveEdit(task, editor));
    editor.append(subject, description, save);
    edit.addEventListener("click", () => editor.classList.toggle("hidden"));
    node.append(actions, editor);
  } else if (["failed", "cancelled"].includes(task.status)) {
    const reset = el("button", "ghost", t("resetPending"));
    reset.type = "button";
    reset.addEventListener("click", () => h5ResetTask(task));
    actions.append(reset);
    node.append(actions);
  } else {
    node.append(actions);
  }

  return node;
}

function h5DrawEdges() {
  const canvas = $("taskList")?.querySelector(".h5-dag-canvas");
  const svg = canvas?.querySelector(".h5-dag-edges");
  if (!canvas || !svg || !state.h5Graph) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(canvas.scrollWidth, canvas.clientWidth);
  const height = Math.max(canvas.scrollHeight, canvas.clientHeight);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.replaceChildren();

  const nodes = new Map();
  canvas.querySelectorAll("[data-h5-task-id]").forEach((node) => nodes.set(node.dataset.h5TaskId, node));
  for (const edge of state.h5Graph.edges || []) {
    const from = nodes.get(edge.from), to = nodes.get(edge.to);
    if (!from || !to) continue;
    const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
    const x1 = a.right - rect.left + canvas.scrollLeft;
    const y1 = a.top + a.height / 2 - rect.top + canvas.scrollTop;
    const x2 = b.left - rect.left + canvas.scrollLeft;
    const y2 = b.top + b.height / 2 - rect.top + canvas.scrollTop;
    const mid = x1 + Math.max(24, (x2 - x1) / 2);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.5");
    svg.append(path);
  }
}

function h5RenderGraph() {
  const root = $("taskList");
  if (!root) return;
  if (!state.sessionId || state.h5GraphSessionId !== state.sessionId || !state.h5Graph) {
    return h5FoundationRenderTasks();
  }
  root.replaceChildren();
  const tasks = Array.isArray(state.h5Graph.tasks) ? state.h5Graph.tasks.slice(0, 100) : [];
  const summary = $("taskGraphSummary");
  const counts = state.h5Graph.counts || {};
  if (summary) {
    summary.textContent = t("graphSummary", {
      ready: counts.ready || 0,
      blocked: counts.blocked || 0,
      running: counts.in_progress || 0,
      done: counts.completed || 0,
    });
  }
  if (!tasks.length) {
    root.append(el("div", "muted", t("noTasks")));
    return;
  }

  const canvas = el("div", "h5-dag-canvas");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("h5-dag-edges");
  const levelsRoot = el("div", "h5-dag-levels");
  const allTasks = new Map(tasks.map((task) => [task.task_id, task]));
  const levels = h5Levels(tasks);
  levelsRoot.style.setProperty("--h5-stage-count", String(Math.max(1, levels.length)));
  for (const [level, group] of levels) {
    const column = el("div", "h5-dag-level");
    column.append(el("div", "h5-level-label", t("stage", { number: level + 1 })));
    for (const task of group) column.append(h5TaskNode(task, allTasks));
    levelsRoot.append(column);
  }
  canvas.append(svg, levelsRoot);
  root.append(canvas);
  if (state.h5Graph.truncated) root.append(el("div", "h5-truncated", t("graphTruncated")));
  canvas.scrollLeft = 0;
  requestAnimationFrame(h5DrawEdges);
}

async function h5LoadGraph(sessionId = state.sessionId) {
  if (!sessionId) {
    state.h5Graph = null;
    state.h5GraphSessionId = null;
    return;
  }
  const sid = safeId(sessionId);
  try {
    const graph = await api(`/api/harness/sessions/${sid}/worker-task-graph?limit=100`);
    if (state.sessionId !== sid) return;
    state.h5Graph = graph;
    state.h5GraphSessionId = sid;
    state.tasks = Array.isArray(graph.tasks) ? graph.tasks.slice(0, 100) : [];
    h5RenderGraph();
  } catch (error) {
    if (state.sessionId !== sid) return;
    state.h5Graph = null;
    state.h5GraphSessionId = sid;
    if (error?.status !== 404) showToast(error.message, true);
  }
}

const h5FoundationRenderTasks = renderTasks;
renderTasks = function h5RenderTasks() {
  if (state.h5Graph && state.h5GraphSessionId === state.sessionId) return h5RenderGraph();
  return h5FoundationRenderTasks();
};

const h5FoundationLoadSessionData = loadSessionData;
loadSessionData = async function h5LoadSessionData() {
  const sid = state.sessionId;
  if (state.h5GraphSessionId !== sid) {
    state.h5Graph = null;
    state.h5GraphSessionId = null;
  }
  const result = await h5FoundationLoadSessionData();
  if (sid && state.sessionId === sid) await h5LoadGraph(sid);
  return result;
};

h5InstallStyles();
h5InstallSummary();
window.addEventListener("resize", () => requestAnimationFrame(h5DrawEdges));
