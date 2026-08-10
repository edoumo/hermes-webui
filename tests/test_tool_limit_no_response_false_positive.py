"""Tests for false-positive no_response when tool-limit turns have stale _last_error.

When the agent exhausts its tool iteration budget (max_iterations_reached),
``run_conversation()`` returns ``completed=False`` with a non-empty
``final_response`` (the summary the model produced after the budget was
exhausted).  If ``agent._last_error`` retains a prior retried API error from
earlier in the same turn, the WebUI's ``_drop_replayed_assistant`` flag
drops the valid summary, the transcript appears to lack a final answer,
and the silent-failure classifier emits a false ``no_response`` error
instead of recognising the tool-limit summary.

These tests verify the fix: a non-empty ``final_response`` with
``_tool_limit_reached`` or ``completed=False`` must not be classified as
``no_response`` when the only "error" is a stale ``_last_error``.
"""

from __future__ import annotations

import pytest

from api.streaming import (
    _agent_result_tool_limit_reached,
    _result_has_authoritative_final_response,
)


# ── _result_has_authoritative_final_response ──────────────────────────────

def test_authoritative_result_completed_true_no_error():
    """Baseline: completed=True, non-empty final_response, no error → authoritative."""
    result = {
        "final_response": "Here is the answer.",
        "completed": True,
        "failed": False,
        "partial": False,
        "error": "",
    }
    assert _result_has_authoritative_final_response(result) is True


def test_authoritative_result_tool_limit_with_final_response():
    """Tool-limit turn with completed=False but non-empty final_response.

    The agent *did* produce a summary (via _handle_max_iterations), so the
    result should be treated as authoritative even though ``completed`` is
    False (the budget was exhausted, not the provider).
    """
    result = {
        "final_response": "I reached the iteration limit. Here's what I found...",
        "completed": False,  # max_iterations_reached → completed=False
        "failed": False,
        "partial": False,
        "turn_exit_reason": "max_iterations_reached(16/16)",
    }
    # The fix: tool-limit results with a non-empty final_response are
    # authoritative — the provider responded, it just ran out of budget.
    assert _result_has_authoritative_final_response(result) is True


def test_authoritative_result_empty_final_response():
    """Empty final_response → not authoritative."""
    result = {
        "final_response": "",
        "completed": True,
        "failed": False,
        "partial": False,
    }
    assert _result_has_authoritative_final_response(result) is False


def test_authoritative_result_failed():
    """failed=True → not authoritative."""
    result = {
        "final_response": "Some text",
        "completed": True,
        "failed": True,
        "partial": False,
    }
    assert _result_has_authoritative_final_response(result) is False


def test_authoritative_result_partial():
    """partial=True → not authoritative."""
    result = {
        "final_response": "Some text",
        "completed": True,
        "failed": False,
        "partial": True,
    }
    assert _result_has_authoritative_final_response(result) is False


# ── _agent_result_tool_limit_reached ──────────────────────────────────────

def test_tool_limit_reached_detection():
    """max_iterations_reached in turn_exit_reason → tool_limit_reached=True."""
    result = {
        "turn_exit_reason": "max_iterations_reached(16/16)",
        "final_response": "Summary text",
        "completed": False,
    }
    assert _agent_result_tool_limit_reached(result) is True


def test_tool_limit_not_reached_normal():
    """Normal text_response turn → tool_limit_reached=False."""
    result = {
        "turn_exit_reason": "text_response(finish_reason=stop)",
        "final_response": "Here's the answer",
        "completed": True,
    }
    assert _agent_result_tool_limit_reached(result) is False