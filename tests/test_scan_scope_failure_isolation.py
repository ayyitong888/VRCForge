import sqlite3
import uuid

from agent_scan_result_store import ScanResultStore
import agent_scan_result_store as store_module


def _verified_proof():
    return {
        "schema": store_module.SCAN_SCOPE_SCHEMA,
        "verified": True,
        "token": "scope_" + "a" * 32,
        "editorSession": str(uuid.uuid4()),
        "assetPaths": [],
        "capturedAt": "2026-10-06T00:00:00Z",
        "dependencyCoverage": "complete",
    }


def test_start_database_lookup_failure_fails_open_for_normal_scan(tmp_path, monkeypatch):
    store = ScanResultStore(tmp_path / "scan-results")
    store._path.parent.mkdir(parents=True)
    store._path.touch()

    def fail_connection(*, create=False):
        raise sqlite3.DatabaseError("corrupt scan-result database")

    monkeypatch.setattr(store, "_connection", fail_connection)
    monkeypatch.setattr(
        store_module,
        "AgentScanScopeClient",
        lambda _root: (_ for _ in ()).throw(AssertionError("scope capture must be skipped")),
    )

    context = store.start(tmp_path, "vrcforge_scan_fx_animator", {})

    assert context["generation"] == 0
    assert context["proof"]["verified"] is False
    assert context["proof"]["reason"] == "proof_store_unavailable"


def test_transient_post_scan_validation_failure_is_not_marked_validated(tmp_path, monkeypatch):
    proof = _verified_proof()

    class Core:
        def capture(self, _tool, _arguments):
            return proof

        def validate(self, _token):
            return {
                "schema": store_module.SCAN_SCOPE_SCHEMA,
                "valid": False,
                "reason": "capability_unavailable",
                "changedAssetPaths": [],
                "editorSession": "",
            }

    monkeypatch.setattr(store_module, "AgentScanScopeClient", lambda _root: Core())
    store = ScanResultStore(tmp_path / "scan-results")
    step = {
        "index": 0,
        "tool": "vrcforge_scan_fx_animator",
        "status": "ok",
        "result": {"count": 1},
    }
    arguments = {}
    start = store.start(tmp_path, step["tool"], arguments)

    metadata = store.capture("owner", "turn", tmp_path, step, arguments, expected_generation=start)

    assert metadata["scope"] == "current_turn"
    assert metadata["freshness"] == "unverified"
    assert metadata.get("expired") is not True
