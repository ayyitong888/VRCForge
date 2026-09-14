"""Compile exact production payloads and pass them through the public result contract."""
import json, os, shutil, subprocess
from pathlib import Path
from agent_tool_result_contract import normalize_agent_tool_result
ROOT=Path(__file__).resolve().parents[1]

def test_rename_reparent_success_and_preview_receipts(tmp_path):
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs").read_text(encoding="utf-8-sig")
    bodies=[]
    for name in ("RenameGameObjectTool", "ReparentGameObjectTool"):
        section=source.split("public static class "+name,1)[1].split("public static class ",1)[0]
        for variable in ("payload", "previewPayload"):
            start=section.index("var "+variable+" = new")
            body=section[start:section.index(";",start)+1]
            bodies.append("{"+body+" Console.WriteLine(JsonConvert.SerializeObject("+variable+"));}")
        assert section.index("persisted readback was not exact") < section.index("var payload = new")
    program='using System; using Newtonsoft.Json; class Probe { static void Main(){\nstring oldName="Old",newName="New",oldPath="Avatar/Old",newPath="Avatar/New",oldParentPath="Avatar",resolvedNewParentPath="Avatar/Next";\nbool worldPositionStays=true; var readback=new {name="New"}; var beforeScene=new {FileDigest="before"}; var afterScene=new {Path="Assets/Main.unity",FileDigest="after"};\n'+"".join(bodies)+"}}"
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compiler=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll";dotnet=shutil.which("dotnet")
    (tmp_path/"Probe.cs").write_text(program,encoding="utf-8")
    cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+",f"-out:{tmp_path/'Probe.dll'}",f"-r:{newtonsoft}"]+[f"-r:{p}" for p in refs.glob("*.dll")]+[str(tmp_path/"Probe.cs")]
    built=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    ran=subprocess.run([dotnet,str(tmp_path/"Probe.dll")],capture_output=True,text=True,timeout=30)
    assert ran.returncode==0,ran.stdout+ran.stderr
    rows=[json.loads(line) for line in ran.stdout.splitlines()]
    assert len(rows)==4
    for row in rows:
        preview=row["preview"]
        assert row.get("mutationStarted") is (not preview)
        assert row.get("committed") is (not preview)
        assert row.get("commitState")==("not_started" if preview else "committed")
        outcome=normalize_agent_tool_result({"ok":True,**row},write=True,fallback_summary="GameObject edit")
        assert outcome["commitState"]==("not_started" if preview else "complete")
