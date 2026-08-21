"""H5 session-level task graph placement contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_h5_promotes_task_card_outside_worker_detail_without_rewriting_h3_html():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "harness-task-recovery.js").read_text(
        encoding="utf-8"
    )

    # H3 source remains unchanged: H5 performs a reversible runtime move.
    assert '<section class="card tasks-card">' in html
    assert 'card.parentElement?.id === "workerView"' in source
    assert "workspace.append(card)" in source
    assert 'card.dataset.h5SessionGraph = "true"' in source


def test_h5_session_graph_layer_does_not_own_sse_or_browser_persistence():
    source = (ROOT / "static" / "harness-task-recovery.js").read_text(
        encoding="utf-8"
    )
    assert "new EventSource" not in source
    assert "localStorage" not in source
    assert "Authorization" not in source
    assert "Bearer " not in source
