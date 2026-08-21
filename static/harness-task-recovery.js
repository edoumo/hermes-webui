"use strict";

// H5 recovery loads after harness-tasks.js and before boot. Failed task
// recovery is task-aware; cancelled task reset keeps the older generic path.
// The DAG itself is session-level, so move its card out of the worker detail
// pane without changing the H3/H4 worker transcript/activation layout.
(function h5PromoteTaskGraphToSessionWorkspace() {
  const workspace = document.querySelector(".workspace");
  const card = document.querySelector(".tasks-card");
  if (workspace && card && card.parentElement?.id === "workerView") {
    workspace.append(card);
    card.dataset.h5SessionGraph = "true";
  }
})();

const h5FoundationResetTask = h5ResetTask;
h5ResetTask = async function h5RecoverOrResetTask(task) {
  if (task.status !== "failed") return h5FoundationResetTask(task);
  try {
    const result = await api(
      `/api/harness/sessions/${safeId(state.sessionId)}/worker-tasks/${safeId(task.task_id)}/recover`,
      {
        method: "POST",
        body: { expected_revision: task.revision },
      },
    );
    showToast(t("taskRecoveryReady", { id: result.message_id || "message restored" }));
    await loadSessionData();
  } catch (error) {
    showToast(error.message, true);
  }
};

const h5RecoveryFoundationTaskNode = h5TaskNode;
h5TaskNode = function h5RecoveryTaskNode(task, allTasks) {
  const node = h5RecoveryFoundationTaskNode(task, allTasks);
  if (task.status === "failed") {
    // Failed task nodes have a single action in the H5 base renderer.  Select
    // it structurally rather than matching its translated text so recovery is
    // independent of the active Harness locale.
    const button = node.querySelector(".h5-task-actions button");
    if (button) {
      button.textContent = t("recoverTask");
      button.title = t("recoverTaskTitle");
      button.dataset.h5Action = "recover";
    }
  }
  return node;
};
