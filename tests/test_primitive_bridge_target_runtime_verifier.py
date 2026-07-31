from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

import primitive_bridge_target_runtime_verifier as verifier


_MANIFEST_TOOL_PATH = (
    Path(__file__).resolve().parents[1] / "packaging" / "bridge_target_manifest.py"
)


def _load_manifest_tool():
    spec = importlib.util.spec_from_file_location(
        "_vrcforge_bridge_target_manifest_test_tool",
        _MANIFEST_TOOL_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object], bytes, bytes, bytes]:
    payload = tmp_path / "payload"
    tree = payload / "bridge_target"
    internal = tree / "_internal"
    internal.mkdir(parents=True)
    executable = tree / verifier.BRIDGE_TARGET_EXECUTABLE_NAME
    executable.write_bytes(b"fixed-frozen-executable\0")
    (internal / "main.py").write_bytes(b"fixed-connector-entry\n")
    (internal / "runtime.dat").write_bytes(b"fixed-runtime-data\n")
    manifest_path = payload / verifier.BRIDGE_TARGET_MANIFEST_NAME
    manifest_tool = _load_manifest_tool()
    document = manifest_tool.write_manifest(tree, manifest_path)
    raw_manifest = manifest_path.read_bytes()
    return (
        executable,
        manifest_path,
        document,
        hashlib.sha256(raw_manifest).digest(),
        bytes.fromhex(document["treeDigest"]),
        hashlib.sha256(executable.read_bytes()).digest(),
    )


def _open_proof(tmp_path: Path) -> verifier.VerifiedBridgeTargetRuntimeDependencies:
    executable, _manifest, _document, manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )
    return verifier.preflight_frozen_bridge_target_runtime(
        manifest_digest,
        tree_digest,
        exe_digest,
        executable_path=executable,
        runtime_home=executable.parent / "_internal",
        frozen=True,
    )


def test_runtime_verifier_matches_the_build_manifest_contract(tmp_path: Path) -> None:
    executable, manifest, document, manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )

    proof = verifier.preflight_frozen_bridge_target_runtime(
        manifest_digest,
        tree_digest,
        exe_digest,
        executable_path=executable,
        runtime_home=executable.parent / "_internal",
        frozen=True,
    )
    try:
        assert proof.bridge_manifest_digest == manifest_digest
        assert proof.bridge_tree_digest == tree_digest
        assert proof.adapter_executable_digest == exe_digest
        assert proof.tree_root == executable.parent
        assert proof.runtime_home == executable.parent / "_internal"
        assert proof.manifest_path == manifest
        assert proof._manifest_document == document
        assert proof.verification_count == 1
        proof.verify_unchanged()
        assert proof.verification_count == 2
    finally:
        proof.close()


def test_runtime_verifier_default_path_uses_current_frozen_executable_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable, manifest, _document, manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )
    monkeypatch.setattr(verifier.sys, "frozen", True, raising=False)
    monkeypatch.setattr(verifier.sys, "executable", str(executable))
    monkeypatch.setattr(
        verifier.sys,
        "_MEIPASS",
        str(executable.parent / "_internal"),
        raising=False,
    )

    proof = verifier.preflight_frozen_bridge_target_runtime(
        manifest_digest,
        tree_digest,
        exe_digest,
    )
    try:
        assert proof.executable_path == executable
        assert proof.tree_root == executable.parent
        assert proof.runtime_home == executable.parent / "_internal"
        assert proof.manifest_path == manifest
    finally:
        proof.close()


@pytest.mark.parametrize("binding", ["manifest", "tree", "executable"])
def test_runtime_verifier_rejects_parent_binding_drift(
    tmp_path: Path,
    binding: str,
) -> None:
    executable, _manifest, _document, manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )
    values = {
        "manifest": manifest_digest,
        "tree": tree_digest,
        "executable": exe_digest,
    }
    values[binding] = bytes([values[binding][0] ^ 0x80]) + values[binding][1:]

    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="binding",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            values["manifest"],
            values["tree"],
            values["executable"],
            executable_path=executable,
            runtime_home=executable.parent / "_internal",
            frozen=True,
        )


def test_runtime_verifier_rejects_noncanonical_manifest_even_when_parent_hash_matches(
    tmp_path: Path,
) -> None:
    executable, manifest, document, _manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )
    noncanonical = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    manifest.write_bytes(noncanonical)

    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="canonical JSON",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            hashlib.sha256(noncanonical).digest(),
            tree_digest,
            exe_digest,
            executable_path=executable,
            runtime_home=executable.parent / "_internal",
            frozen=True,
        )


@pytest.mark.parametrize("mutation", ["extra", "tree_leaf", "manifest", "executable"])
def test_held_runtime_proof_rejects_post_preflight_replacement_or_tree_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    proof = _open_proof(tmp_path)
    try:
        replacement_blocked_by_held_handle = False
        if mutation == "extra":
            (proof.tree_root / "unexpected.bin").write_bytes(b"unexpected")
        elif mutation == "tree_leaf":
            (proof.tree_root / "_internal" / "runtime.dat").write_bytes(b"changed")
        elif mutation == "manifest":
            replacement = proof.manifest_path.with_suffix(".replacement")
            replacement.write_bytes(proof.manifest_path.read_bytes())
            try:
                os.replace(replacement, proof.manifest_path)
            except PermissionError:
                replacement_blocked_by_held_handle = True
        else:
            replacement = proof.executable_path.with_suffix(".replacement")
            replacement.write_bytes(proof.executable_path.read_bytes())
            try:
                os.replace(replacement, proof.executable_path)
            except PermissionError:
                replacement_blocked_by_held_handle = True

        assert mutation not in {"manifest", "executable"} or (
            replacement_blocked_by_held_handle
            or not replacement.exists()
        )
        with pytest.raises(
            verifier.BridgeTargetRuntimeVerificationError,
            match="drift|changed|match|identity",
        ):
            proof.verify_unchanged()
    finally:
        proof.close()


def test_held_runtime_proof_blocks_or_rejects_tree_root_replacement(
    tmp_path: Path,
) -> None:
    proof = _open_proof(tmp_path)
    moved_tree = proof.tree_root.with_name("bridge_target_replaced")
    replacement_blocked_by_held_handle = False
    try:
        try:
            os.replace(proof.tree_root, moved_tree)
        except PermissionError:
            replacement_blocked_by_held_handle = True

        if replacement_blocked_by_held_handle:
            proof.verify_unchanged()
        else:
            assert moved_tree.is_dir()
            with pytest.raises(
                verifier.BridgeTargetRuntimeVerificationError,
                match="unavailable|drift|changed|identity",
            ):
                proof.verify_unchanged()
    finally:
        proof.close()


def test_runtime_verifier_rejects_source_mode_wrong_leaf_and_path_drift(
    tmp_path: Path,
) -> None:
    executable, _manifest, _document, manifest_digest, tree_digest, exe_digest = (
        _runtime_fixture(tmp_path)
    )
    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="not a frozen executable",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            manifest_digest,
            tree_digest,
            exe_digest,
            executable_path=executable,
            frozen=False,
        )

    wrong_leaf = executable.with_name("bridge-target.exe")
    wrong_leaf.write_bytes(executable.read_bytes())
    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="executable leaf",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            manifest_digest,
            tree_digest,
            exe_digest,
            executable_path=wrong_leaf,
            frozen=True,
        )

    wrong_tree = executable.parent.with_name("bridge_runtime")
    wrong_tree.mkdir()
    wrong_tree_executable = wrong_tree / verifier.BRIDGE_TARGET_EXECUTABLE_NAME
    wrong_tree_executable.write_bytes(executable.read_bytes())
    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="tree path",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            manifest_digest,
            tree_digest,
            exe_digest,
            executable_path=wrong_tree_executable,
            frozen=True,
        )

    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="runtime home is not bound",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            manifest_digest,
            tree_digest,
            exe_digest,
            executable_path=executable,
            runtime_home=executable.parent,
            frozen=True,
        )

    drifted_path = executable.parent / "_internal" / ".." / executable.name
    with pytest.raises(
        verifier.BridgeTargetRuntimeVerificationError,
        match="not canonical",
    ):
        verifier.preflight_frozen_bridge_target_runtime(
            manifest_digest,
            tree_digest,
            exe_digest,
            executable_path=drifted_path,
            frozen=True,
        )
