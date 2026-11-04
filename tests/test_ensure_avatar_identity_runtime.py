"""Execute the shared resolver reached by all three ensure tools and previews."""
from pathlib import Path
import os
import subprocess

import test_parameter_writer_runtime as harness


ROOT = Path(__file__).resolve().parents[1]


def test_ensure_resolver_rejects_ambiguity_and_preserves_unique_selection(tmp_path, monkeypatch):
    relative = "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    revision = os.environ.get("VRCFORGE_HIERARCHY_GIT_REF")
    source = subprocess.check_output(["git", "show", f"{revision}:{relative}"], cwd=ROOT, text=True, encoding="utf-8") if revision else (ROOT / relative).read_text(encoding="utf-8-sig")
    resolver = harness._extract_method(source, "internal static VRCAvatarDescriptor ResolveAvatarDescriptor")
    path = harness._extract_method(source, "internal static string GetTransformPath")
    normalize = harness._extract_method(source, "internal static string NormalizePath")
    # Reuse the existing dynamic selection scenarios. Both harness entry points
    # execute this single shared production method, not separate tool bodies.
    extracted = "\n".join([resolver, resolver, path, normalize]).replace("internal static", "private static")
    monkeypatch.setattr(harness, "_source", lambda: extracted)
    original_harness = harness._harness_source
    component_source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8-sig")
    component_normalize = harness._extract_method(component_source, "internal static string NormalizePath")
    def with_spaced_names(product):
        return (original_harness(product) + "\nclass ComponentCrudCore { " + component_normalize + " }").replace("var first = Check(ResolveAvatarDescriptorApply);", '''
        var spaced = Avatar("Root/Avatar ");
        var plain = Avatar("Root/Avatar");
        UnityEngine.Resources.Items = new List<VRCAvatarDescriptor> { spaced, plain };
        if (!ReferenceEquals(ResolveAvatarDescriptorApply("Root/Avatar "), spaced)) return 20;
        if (!ReferenceEquals(ResolveAvatarDescriptorApply("Root/Avatar"), plain)) return 21;
        UnityEngine.Resources.Items.Remove(plain);
        bool rejected = false;
        try { ResolveAvatarDescriptorApply("Root/Avatar"); } catch (InvalidOperationException) { rejected = true; }
        if (!rejected) return 22;
        var first = Check(ResolveAvatarDescriptorApply);
        ''')
    monkeypatch.setattr(harness, "_harness_source", with_spaced_names)
    result = harness._compile_and_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
