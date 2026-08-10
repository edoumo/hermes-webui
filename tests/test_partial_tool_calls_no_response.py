"""Tests for partial-tool-calls misclassification as no_response.

When the provider streams tool calls but the turn ends without a final text
response (e.g. mid-turn interruption, context compression), the WebUI
snapshots a ``_partial`` assistant message with ``_partial_tool_calls``.
The error classifier must NOT label this as ``no_response`` (the provider
*did* respond — it made tool calls) but as ``interrupted``.

Regression test for the false-positive ``no_response`` incidents reported
by the no-response-provider watchdog.
"""

from __future__ import annotations

import pytest

from api.streaming import _classify_provider_error


class _FakeExc(Exception):
    pass


def test_silent_failure_without_partial_tool_calls_is_no_response():
    """Baseline: a truly silent failure (no error, no partial tool calls)
    is still classified as ``no_response``."""
    result = _classify_provider_error("", None, silent_failure=True)
    assert result["type"] == "no_response"
    assert result["label"] == "No response from provider"


def test_silent_failure_with_partial_tool_calls_is_interrupted():
    """When the transcript has a ``_partial`` message with
    ``_partial_tool_calls``, the provider made tool calls — it responded.
    The classifier must NOT call this ``no_response``; it should be
    ``interrupted``."""
    result = _classify_provider_error(
        "",
        None,
        silent_failure=True,
        partial_tool_calls=[{"name": "terminal", "args": {}, "done": True}],
    )
    assert result["type"] == "interrupted"
    assert result["label"] == "Response interrupted"


def test_silent_failure_with_partial_content_only_is_interrupted():
    """When the transcript has a ``_partial`` message with non-empty
    content (streamed text) but no tool calls, the provider also
    responded — classify as ``interrupted``."""
    result = _classify_provider_error(
        "",
        None,
        silent_failure=True,
        partial_has_content=True,
    )
    assert result["type"] == "interrupted"
    assert result["label"] == "Response interrupted"


def test_explicit_error_with_partial_tool_calls_keeps_error_type():
    """When there IS a concrete error string (e.g. auth, quota), the
    partial tool calls should NOT override the specific classification."""
    result = _classify_provider_error(
        "401 Unauthorized",
        None,
        silent_failure=False,
        partial_tool_calls=[{"name": "terminal", "args": {}, "done": True}],
    )
    assert result["type"] == "auth_mismatch"


def test_no_silent_failure_flag_ignores_partial_tool_calls():
    """When ``silent_failure`` is False (there IS an error string), the
    partial tool calls flag must not interfere with normal classification."""
    result = _classify_provider_error(
        "some error",
        None,
        silent_failure=False,
        partial_tool_calls=[{"name": "terminal", "args": {}, "done": True}],
    )
    # Falls through to generic 'error' since "some error" matches nothing
    assert result["type"] == "error"