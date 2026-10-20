from pathlib import Path
import uuid
import pytest

import agent_scan_result_store as store


def test_only_core_declared_metadata_source_avoids_content_budget(tmp_path, monkeypatch):
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    texture = project / "Assets/Texture.png"
    texture.write_bytes(b"pixels" * 1024)
    meta = Path(str(texture) + ".meta")
    meta.write_text("guid: texture")
    monkeypatch.setattr(store, "MAX_HASH_BYTES", 64)
    observed = store._fingerprints(str(project), ["Assets/Texture.png"], ["Assets/Texture.png"])
    assert store._valid_fingerprint(observed["Assets/Texture.png"], True)
    assert observed["Assets/Texture.png"]["asset"]["kind"] == "metadata"
    assert "sha256" not in observed["Assets/Texture.png"]["asset"]
    assert "sha256" in observed["Assets/Texture.png"]["meta"]
    assert not store._valid_fingerprint(observed["Assets/Texture.png"])
    assert not store._valid_fingerprint(store._fingerprints(str(project), ["Assets/Texture.png"])["Assets/Texture.png"])
    meta.write_text("guid: changed")
    assert store._fingerprints(str(project), ["Assets/Texture.png"], ["Assets/Texture.png"]) != observed
    texture.unlink()
    assert not store._valid_fingerprint(store._fingerprints(str(project), ["Assets/Texture.png"], ["Assets/Texture.png"])["Assets/Texture.png"], True)


def test_metadata_scope_subset_is_validated_before_it_can_select_fingerprints():
    proof = {"schema": store.SCAN_SCOPE_SCHEMA, "verified": True, "dependencyCoverage": "complete",
             "token": "scope_" + uuid.uuid4().hex, "editorSession": str(uuid.uuid4()),
             "assetPaths": ["Assets/Texture.png"], "capturedAt": "2026-10-18T00:00:00Z"}
    assert store._valid_scope_proof(proof)
    assert store._valid_scope_proof({**proof, "metadataOnlyAssetPaths": ["Assets/Texture.png"]})
    for invalid in (["Assets/Other.png"], ["Assets/Texture.png"] * 2, [None], "Assets/Texture.png"):
        assert not store._valid_scope_proof({**proof, "metadataOnlyAssetPaths": invalid})
    assert not store._valid_scope_proof({**proof, "assetPaths": [{}]})


@pytest.mark.parametrize("reason", ["core_unavailable", "unity_core_timeout", "scope_changed"])
def test_metadata_fingerprint_never_substitutes_for_failed_core_validation(tmp_path, monkeypatch, reason):
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets/Texture.png").write_bytes(b"pixels" * 1024)
    (project / "Assets/Texture.png.meta").write_text("guid: texture")
    proof = {"schema": store.SCAN_SCOPE_SCHEMA, "verified": True, "dependencyCoverage": "complete",
             "token": "scope_" + uuid.uuid4().hex, "editorSession": str(uuid.uuid4()),
             "assetPaths": ["Assets/Texture.png"], "metadataOnlyAssetPaths": ["Assets/Texture.png"],
             "capturedAt": "2026-10-18T00:00:00Z"}

    class Core:
        valid = True

        def capture(self, *_args):
            return proof

        def validate(self, _token):
            return {"valid": self.valid, "editorSession": proof["editorSession"], "reason": "" if self.valid else reason}

    core = Core()
    monkeypatch.setattr(store, "AgentScanScopeClient", lambda _root: core)
    monkeypatch.setattr(store, "MAX_HASH_BYTES", 64)
    snapshot = store.ScanResultStore(tmp_path / "store")
    arguments = {"avatarPath": "Avatar", "indexOnly": True}
    context = snapshot.start(project, "vrcforge_scan_materials", arguments)
    step = {"id": "scan", "tool": "vrcforge_scan_materials", "status": "ok", "result": {"ok": True, "index": []}}
    metadata = snapshot.capture("session", "turn", project, step, arguments, expected_generation=context)
    assert metadata["freshness"] == "validated"
    core.valid = False
    result = snapshot.lookup("session", "turn", project, metadata["resultRef"])
    assert result["expired"] is True
    assert result.get("scopeValidated") is not True
