"""Feed the production C# snapshot projection into the production Python reader."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_curve_fx_authoring_runtime_contract import method
from unity_write_operation_polling import write_operation_result

ROOT = Path(__file__).resolve().parents[1]


def test_core_snapshot_is_accepted_by_python_receipt_reader(tmp_path):
    path = Path(os.environ.get("VRCFORGE_WRITE_SNAPSHOT_SOURCE", ROOT / "Assets/VRCForge/Editor/EditorStateTools.cs"))
    projection = method(path.read_text(encoding="utf-8-sig"), "private static JObject PublicMcpWriteSnapshot(")
    program = r'''
using System; using Newtonsoft.Json; using Newtonsoft.Json.Linq;
class Probe {
 METHOD
 static void Main() {
   foreach (var status in new[]{"queued","running","unknown","done"}) {
     var record = new JObject { ["job_id"]="job-1", ["operation"]=new JObject { ["execution_id"]="exec-1" },
       ["status"]=status, ["tool"]="vrc_set_gameobject_active", ["after"]=new JObject { ["isError"]=false, ["resultType"]="complete" } };
     Console.WriteLine(PublicMcpWriteSnapshot(record).ToString(Formatting.None));
   }
 }
}
'''.replace("METHOD", projection)
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    dotnet = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file() or not compilers or not refs:
        pytest.skip("Local .NET 8 SDK/reference pack required")
    newtonsoft = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    compiled = subprocess.run(
        [str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"]
        + [f"-r:{p}" for p in refs[-1].glob("*.dll")]
        + [f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(snapshots) == 4
    for snapshot in snapshots:
        readback = write_operation_result(snapshot, execution_id="exec-1", tool_name="vrc_set_gameobject_active")
        if snapshot["status"] == "done":
            assert readback == {"isError": False, "resultType": "complete"}
        else:
            assert readback is None
