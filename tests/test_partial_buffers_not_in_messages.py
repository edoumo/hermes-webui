"""Tests for the fallback partial-evidence scan from streaming buffers.

When the provider streams text or tool calls but the turn ends without a
clean finish, the WebUI classifies the failure.  The classifier distinguishes
``interrupted`` (provider was active) from ``no_response`` (provider never
responded).

The primary scan looks for a ``_partial`` message in ``s.messages``.  But that
message is materialised by ``_snapshot_and_append_partial_on_error`` *after*
the classification runs.  When the scan finds nothing in ``s.messages``, it must
fall back to the live streaming buffers (``STREAM_PARTIAL_TEXT``,
``STREAM_LIVE_TOOL_CALLS``) and the ``_token_sent`` flag so a turn that
streamed text or tool calls is classified as ``interrupted`` instead of a
false ``no_response``.

Regression test for #5512.
"""

from __future__ import annotations

import pytest

from api.streaming import _classify_provider_error


class _FakeExc(Exception):
    pass


def test_silent_failure_no_partial_evidence_is_no_response():
    """Baseline: no partial message, no live buffers, no tokens → no_response."""
    result = _classify_provider_error("", None, silent_failure=True)
    assert result["type"] == "no_response"
    assert result["label"] == "No response from provider"


def test_silent_failure_with_live_text_is_interrupted():
    """When s.messages has no _partial but live buffers had text,
    the classifier must receive partial_has_content=True → interrupted."""
    result = _classify_provider_error(
        "",
        None,
        silent_failure=True,
        partial_has_content=True,
    )
    assert result["type"] == "interrupted"
    assert result["label"] == "Response interrupted"


def test_silent_failure_with_live_tool_calls_is_interrupted():
    """When s.messages has no _partial but live buffers had tool calls,
    the classifier must receive partial_tool_calls → interrupted."""
    result = _classify_provider_error(
        "",
        None,
        silent_failure=True,
        partial_tool_calls=[{"name": "terminal", "args": {}, "done": True}],
    )
    assert result["type"] == "interrupted"
    assert result["label"] == "Response interrupted"


def test_silent_failure_with_token_sent_only_is_interrupted():
    """When buffers were cleared but on_token fired (_token_sent=True),
    the provider WAS responding → interrupted, not no_response."""
    result = _classify_provider_error(
        "",
        None,
        silent_failure=True,
        partial_has_content=True,  # derived from _token_sent fallback
    )
    assert result["type"] == "interrupted"
    assert result["label"] == "Response interrupted"