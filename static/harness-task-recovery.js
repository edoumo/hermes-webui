"use strict";

// H5 recovery loads after harness-tasks.js and before boot. Failed task
// recovery is task-aware; cancelled task reset keeps the older generic path.
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
    showToast(`Task recovery ready (${result.message_id || "message restored"})`);
    await loadSessionData();
  } catch (error) {
    showToast(error.message, true);
  }
};

const h5RecoveryFoundationTaskNode = h5TaskNode;
h5TaskNode = function h5RecoveryTaskNode(task, allTasks) {
  const node = h5RecoveryFoundationTaskNode(task, allTasks);
  if (task.status === "failed") {
    for (const button of node.querySelectorAll("button")) {
      if (button.textContent === "Reset to pending") {
        button.textContent = "Recover task";
        button.title = "Restore the failed task, its worker and durable message for redispatch";
      }
    }
  }
  return node;
};
