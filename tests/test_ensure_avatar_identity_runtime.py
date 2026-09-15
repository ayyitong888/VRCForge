"""Execute the shared resolver reached by all three ensure tools and previews."""
from pathlib import Path

import test_parameter_writer_runtime as harness


ROOT = Path(__file__).resolve().parents[1]


def test_ensure_resolver_rejects_ambiguity_and_preserves_unique_selection(tmp_path, monkeypatch):
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8-sig")
    resolver = harness._extract_method(source, "internal static VRCAvatarDescriptor ResolveAvatarDescriptor")
    path = harness._extract_method(source, "internal static string GetTransformPath")
    normalize = harness._extract_method(source, "internal static string NormalizePath")
    # Reuse the existing dynamic selection scenarios. Both harness entry points
    # execute this single shared production method, not separate tool bodies.
    extracted = "\n".join([resolver, resolver, path, normalize]).replace("internal static", "private static")
    monkeypatch.setattr(harness, "_source", lambda: extracted)
    result = harness._compile_and_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
