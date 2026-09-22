from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "src/components/settings/external-agent-connectors-panel.tsx").read_text(encoding="utf-8")
API = (ROOT / "src/lib/api/connectors.ts").read_text(encoding="utf-8")


def test_connector_binding_metadata_is_exposed_to_ui() -> None:
    assert "bindingMatchesCurrent?: boolean" in API
    assert "bindingConflict?: boolean" in API
    assert "bindingTarget?: string" in API


def test_conflicting_connector_cannot_overwrite_or_remove_existing_binding() -> None:
    assert "const bindingConflict = Boolean(state?.bindingConflict);" in PANEL
    assert "const installActionDisabled = loading || !state || bindingConflict;" in PANEL
    assert "disabled={loading || !installed || bindingConflict}" in PANEL
    assert "connector.bindingConflictHint" in PANEL
