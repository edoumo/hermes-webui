"use strict";

// H4 is loaded after the qualified H3 harness.js and before its synthetic
// DOMContentLoaded boot event. It extends the existing global lexical bindings
// instead of duplicating session, worker, SSE, auth or CSRF state.
state.operations = null;
state.h4Activations = [];

function h4InstallControls() {
  const summary = document.querySelector(".workers-rail .summary-strip");
  if (summary && !$("opsState")) {
    summary.append(el("span", "", t("opsIdle")));
    summary.lastElementChild.id = "opsState";
  }

  const run = $("runWorkerBtn");
  if (run && !$("retryWorkerBtn")) {
    const retry = el("button", "ghost", t("retryFailed"));
    retry.id = "retryWorkerBtn";
    retry.type = "button";
    retry.hidden = true;
    run.before(retry);
  }
  if (run && !$("cancelActivationBtn")) {
    const cancel = el("button", "ghost", t("cancelActivation"));
    cancel.id = "cancelActivationBtn";
    cancel.type = "button";
    cancel.hidden = true;
    run.before(cancel);
  }

  $("retryWorkerBtn")?.addEventListener("click", h4RetryFailedWorker);
  $("cancelActivationBtn")?.addEventListener("click", h4CancelActivation);
}

function h4CurrentWorker() {
  if (!state.workerId) return null;
  return state.workers.find((worker) => worker.worker_id === state.workerId) || null;
}

function h4CurrentActivation() {
  const worker = h4CurrentWorker();
  if (!worker?.last_activation_id) return null;
  return state.h4Activations.find(
    (activation) => activation.activation_id === worker.last_activation_id
  ) || null;
}

function h4RenderOperationsSummary() {
  const target = $("opsState");
  if (!target) return;
  const operations = state.operations;
  if (!state.sessionId) {
    target.textContent = t("opsIdle");
    return;
  }
  if (!operations) {
    target.textContent = t("opsUnavailable");
    return;
  }
  const active = Number(operations.activations?.STARTING || 0)
    + Number(operations.activations?.RUNNING || 0)
    + Number(operations.activations?.CANCEL_REQUESTED || 0);
  const failed = Number(operations.workers?.FAILED || 0);
  const cap = Number(operations.configured_max_concurrent_activations || 0);
  target.textContent = t("opsSummary", { active, failed, cap });
}

function h4RenderWorkerControls() {
  const run = $("runWorkerBtn");
  const retry = $("retryWorkerBtn");
  const cancel = $("cancelActivationBtn");
  if (!run || !retry || !cancel) return;

  const worker = h4CurrentWorker();
  const activation = h4CurrentActivation();
  if (!worker) {
    retry.hidden = true;
    cancel.hidden = true;
    return;
  }

  retry.textContent = t("retryFailed");
  retry.hidden = worker.status !== "FAILED";
  retry.disabled = worker.status !== "FAILED";

  // The live lifecycle handle is registered only after durable bind. A
  // STARTING row is therefore intentionally not offered as cancellable in UI;
  // once RUNNING, the backend can prove local supervision before interrupting.
  const cancelable = worker.status === "RUNNING"
    && activation
    && ["RUNNING", "CANCEL_REQUESTED"].includes(activation.state);
  cancel.hidden = !cancelable;
  cancel.disabled = !cancelable || activation?.state === "CANCEL_REQUESTED";
  cancel.textContent = activation?.state === "CANCEL_REQUESTED"
    ? t("cancellationRequested")
    : t("cancelActivation");

  // H3 already relies on backend serialization; H4 makes the operator state
  // explicit so obviously invalid runs are not offered from FAILED/RUNNING.
  run.disabled = worker.status !== "DORMANT";
}

async function h4LoadOperations(sessionId = state.sessionId) {
  if (!sessionId) {
    state.operations = null;
    h4RenderOperationsSummary();
    return;
  }
  const sid = safeId(sessionId);
  try {
    const operations = await api(`/api/harness/sessions/${sid}/worker-operations`);
    if (state.sessionId !== sid) return;
    state.operations = operations;
  } catch (error) {
    if (state.sessionId !== sid) return;
    state.operations = null;
    if (error?.status !== 404) showToast(error.message, true);
  }
  h4RenderOperationsSummary();
}

async function h4RetryFailedWorker() {
  const worker = h4CurrentWorker();
  if (!state.sessionId || !worker || worker.status !== "FAILED") return;
  const button = $("retryWorkerBtn");
  if (button) button.disabled = true;
  try {
    const result = await api(
      `/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(worker.worker_id)}/retry`,
      {
        method: "POST",
        body: { expected_revision: worker.revision },
      },
    );
    showToast(t("workerRetryReady", { id: result.message_id || "message requeued" }));
    await loadSessionData();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    h4RenderWorkerControls();
  }
}

async function h4CancelActivation() {
  const worker = h4CurrentWorker();
  const activation = h4CurrentActivation();
  if (!state.sessionId || !worker || !activation) return;
  if (activation.state !== "RUNNING") return;
  const confirmed = window.confirm(
    t("cancelActivationConfirm", { id: activation.activation_id }),
  );
  if (!confirmed) return;

  const button = $("cancelActivationBtn");
  if (button) button.disabled = true;
  try {
    const result = await api(
      `/api/harness/sessions/${safeId(state.sessionId)}/workers/${safeId(worker.worker_id)}/activations/${safeId(activation.activation_id)}/cancel`,
      {
        method: "POST",
        body: { reason: "Harness operator requested durable worker cancellation" },
      },
    );
    showToast(result.status === "CANCEL_REQUESTED"
      ? t("cancellationWaiting")
      : String(result.status || t("cancellationAcknowledged")));
    scheduleRefresh(state.sessionId);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    h4RenderWorkerControls();
  }
}

// Capture H3 implementations before replacing only the projection hooks H4
// needs. SSE ownership remains entirely H3/B3 code and is not reimplemented.
const h4FoundationRenderActivations = renderActivations;
renderActivations = function h4RenderActivations(items) {
  state.h4Activations = Array.isArray(items) ? items.slice(0, 50) : [];
  const result = h4FoundationRenderActivations(items);
  h4RenderWorkerControls();
  return result;
};

const h4FoundationLoadWorkerDetail = loadWorkerDetail;
loadWorkerDetail = async function h4LoadWorkerDetail() {
  const result = await h4FoundationLoadWorkerDetail();
  h4RenderWorkerControls();
  return result;
};

const h4FoundationClearWorkerView = clearWorkerView;
clearWorkerView = function h4ClearWorkerView() {
  state.h4Activations = [];
  const result = h4FoundationClearWorkerView();
  h4RenderWorkerControls();
  return result;
};

const h4FoundationLoadSessionData = loadSessionData;
loadSessionData = async function h4LoadSessionData() {
  const sid = state.sessionId;
  const result = await h4FoundationLoadSessionData();
  if (sid && state.sessionId === sid) await h4LoadOperations(sid);
  h4RenderWorkerControls();
  return result;
};

h4InstallControls();
h4RenderOperationsSummary();
h4RenderWorkerControls();
