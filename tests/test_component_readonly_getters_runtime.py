"""Known instantiating Unity getters reject before native access, using real types."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_component_property_batch_runtime import toolchain


ROOT = Path(__file__).resolve().parents[1]


def test_real_unity_instantiating_getters_reject_and_object_references_remain_readable(tmp_path):
    base, compiler, refs, newtonsoft = toolchain()
    managed = Path("E:/unity/Unity 2022.3.22f1/Editor/Data/Managed/UnityEngine")
    modules = [managed / name for name in ("UnityEngine.CoreModule.dll", "UnityEngine.PhysicsModule.dll", "UnityEngine.SharedInternalsModule.dll")]
    if not all(path.exists() for path in modules):
        pytest.skip("Unity 2022.3 reference assemblies required")
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
    start = source.index("internal static object GetMemberValue(")
    end = source.index("internal static void SetMemberValue(", start)
    actual = source[start:end]
    runner = r'''
using System;using System.Reflection;using System.Runtime.CompilerServices;using UnityEngine;
class Probe {
    ACTUAL
    class Holder {
        internal int calls;
        internal UnityEngine.Object reference;
        public UnityEngine.Object mesh { get { calls++; return reference; } }
    }
    static void Check(bool condition,string message){if(!condition)throw new Exception(message);}
    static void Blocked(Type type,string name,string alternative) {
        // No constructors or Unity native engine are run. Reaching a native getter
        // would fail outside Unity; the expected managed guard must reject first.
        var instance=RuntimeHelpers.GetUninitializedObject(type);
        var member=type.GetProperty(name,BindingFlags.Public|BindingFlags.Instance);
        Check(member!=null,"Real Unity property missing: "+type.Name+"."+name);
        try { GetReadOnlyMemberValue(instance,member); throw new Exception("Getter was not rejected"); }
        catch(InvalidOperationException error) {
            Check(error.Message.Contains("instantiates a resource")&&error.Message.Contains(alternative),"Expected managed guard, not native access failure");
        }
    }
    static int Main() {
        Blocked(typeof(MeshRenderer),"material","sharedMaterial");
        Blocked(typeof(MeshRenderer),"materials","sharedMaterials");
        Blocked(typeof(MeshFilter),"mesh","sharedMesh");
        Blocked(typeof(BoxCollider),"material","sharedMaterial");
        var holder=new Holder { reference=(UnityEngine.Object)RuntimeHelpers.GetUninitializedObject(typeof(Mesh)) };
        var value=GetReadOnlyMemberValue(holder,typeof(Holder).GetProperty("mesh"));
        Check(ReferenceEquals(value,holder.reference)&&holder.calls==1,"Ordinary object reference getter must remain readable");
        Console.WriteLine("PASS real Unity Renderer/MeshFilter/Collider guards before native getters; ordinary references unchanged");
        return 0;
    }
}
'''.replace("ACTUAL", actual)
    cs = tmp_path / "Probe.cs"
    cs.write_text(runner, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compiler), "/nologo", "/target:exe", f"/out:{dll}"]
    command += [f"/r:{path}" for path in refs.glob("*.dll")]
    command += [f"/r:{path}" for path in modules] + [str(cs)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    for module in modules:
        shutil.copy2(module, tmp_path / module.name)
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}
    }}), encoding="utf-8")
    result = subprocess.run([str(base / "dotnet.exe"), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scalar_and_batch_use_the_shared_readonly_entrypoint():
    scalar = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
    scalar = scalar.split("public static class GetPropertyTool", 1)[1].split("public static class InspectSkinnedMeshDeformationTool", 1)[0]
    batch = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentPropertyBatch.cs").read_text(encoding="utf-8")
    assert "ComponentCrudCore.GetReadOnlyMemberValue(component, member)" in scalar
    assert "ComponentCrudCore.GetReadOnlyMemberValue(component, member)" in batch
    assert "ComponentCrudCore.GetMemberValue(component, member)" not in scalar
    assert "ComponentCrudCore.GetMemberValue(component, member)" not in batch
