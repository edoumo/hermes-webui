"""H6.3 acceptance gates derived from Ed's third human UAT."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_h63_is_loaded_after_h62_and_before_models_and_boot():
    boot = recovery._H5_RECOVERY_BOOT
    assert boot.index("h62.src='/harness-polish2.js'") < boot.index(
        "h63.src='/harness-polish3.js'"
    ) < boot.index("hm.src='/harness-models.js'") < boot.index(
        "document.dispatchEvent(new Event('DOMContentLoaded'))"
    )
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(encoding="utf-8")
    assert '"/harness-polish3.js": "harness-polish3.js"' in recovery_source
    # The standalone HTTP entrypoint delegates static resolution to recovery.
    assert "serve_harness_asset(self, parsed.path)" in (ROOT / "harness_server.py").read_text(
        encoding="utf-8"
    )


def test_h63_model_catalog_infers_only_a_deterministic_provider():
    js = _static("harness-polish3.js")

    assert "payload?.provider" in js
    assert "h63RowsContainingModels" in js
    assert "row?.active === true" in js
    assert "row?.authenticated === true || row?.configured === true" in js
    assert "authenticated.length === 1" in js
    assert "return null" in js
    assert "h63RuntimeModelHints" in js
    assert "state.workers" in js
    assert 'api("/api/harness/model-options")' in js
    # Never flatten every provider into one unsafe cross-provider picker.
    assert "providers.flatMap" not in js
    assert "for (const provider of providers)" not in js


def test_h63_model_catalog_normalizes_string_or_object_models():
    js = _static("harness-polish3.js")

    assert 'if (typeof item === "string")' in js
    assert "item?.id || item?.model || item?.name" in js
    assert "h63ProviderModels" in js
    assert "h63ApplyModelCatalog" in js


def test_h63_locale_selector_has_flags_for_all_shipped_locales():
    js = _static("harness-polish3.js")

    for code, flag in {
        "en": "🇬🇧",
        "fr": "🇫🇷",
        "es": "🇪🇸",
        "pt": "🇵🇹",
        "de": "🇩🇪",
        "it": "🇮🇹",
    }.items():
        assert f'{code}: "{flag}"' in js
    assert "h63DecorateLocaleSelect" in js
    assert "definition.label" in js


def test_h63_single_stage_dag_is_left_aligned_and_cannot_scroll_horizontally():
    js = _static("harness-polish3.js")

    assert ".h5-dag-canvas.h63-single-stage{overflow-x:hidden!important" in js
    assert "grid-template-columns:minmax(0,1fr)!important" in js
    assert "min-width:0!important" in js
    assert 'canvas.classList.toggle("h63-single-stage", singleStage)' in js
    assert "canvas.scrollLeft = 0" in js
    assert "MutationObserver" in js
    assert "requestAnimationFrame(h63NormalizeDag)" in js


def test_h63_keeps_browser_security_and_sse_boundaries():
    js = _static("harness-polish3.js")

    for forbidden in (
        "new EventSource",
        "localStorage",
        "Authorization",
        "Bearer ",
        "API_SERVER_KEY",
        "HERMES_HARNESS_GATEWAY_API_KEY",
        "HERMES_WEBUI_GATEWAY_API_KEY",
        "durable-workers.db",
    ):
        assert forbidden not in js
