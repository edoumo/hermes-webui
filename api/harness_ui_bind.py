"""Pure listener-bind policy for the standalone Hermes Harness UI."""
from __future__ import annotations

from typing import Mapping, Optional

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8790
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_harness_bind(
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, int]:
    """Return the configured Harness listener while enforcing remote opt-in.

    Loopback remains the default. A LAN/non-loopback listener is accepted only
    when the operator explicitly opts in and configures password authentication.
    This function is intentionally side-effect free so it can be contract-tested
    without importing the WebUI server/auth stack.
    """

    import os

    env = os.environ if environ is None else environ
    host = str(env.get("HERMES_HARNESS_HOST", _DEFAULT_HOST)).strip() or _DEFAULT_HOST
    if host not in _LOOPBACK_HOSTS:
        if not _truthy(env.get("HERMES_HARNESS_ALLOW_REMOTE")):
            raise RuntimeError(
                "Non-loopback Harness bind requires HERMES_HARNESS_ALLOW_REMOTE=1"
            )
        if not str(env.get("HERMES_WEBUI_PASSWORD", "")).strip():
            raise RuntimeError(
                "Non-loopback Harness bind requires HERMES_WEBUI_PASSWORD authentication"
            )

    raw_port = str(env.get("HERMES_HARNESS_PORT", _DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("HERMES_HARNESS_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise RuntimeError("HERMES_HARNESS_PORT must be between 1 and 65535")
    return host, port


__all__ = [
    "_DEFAULT_HOST",
    "_DEFAULT_PORT",
    "_LOOPBACK_HOSTS",
    "resolve_harness_bind",
]
