"""Execute exact production validation blocks; fake material metadata is not Unity proof."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/MaterialShaderTool.cs"


def block(source, marker):
 start = source.index(marker)
 opening = source.index("{", start)
 depth, end = 1, opening + 1
 while depth:
  depth += (source[end] == "{") - (source[end] == "}")
  end += 1
 return source[start:end]


def run_guards(source_path, output):
 dotnet = shutil.which("dotnet")
 base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
 compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
 references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
 if not dotnet or not compilers or not references: pytest.skip("Local .NET SDK required")
 source = source_path.read_text(encoding="utf-8-sig").split("public static class MaterialTextureTool",1)[1]
 declarations = ""
 if "AllowedTextureProperties" in source:
  start = source.index("private static readonly HashSet<string> AllowedTextureProperties")
  declarations = source[start:source.index(";",start)+1]
  name_guard = block(source, "if (!AllowedTextureProperties.Contains(propertyName))")
 else: name_guard = block(source, "if (string.IsNullOrWhiteSpace(propertyName))")
 type_guard = block(source, "if (!material.HasProperty(propertyName)")
 program = "using System; using System.Linq; using System.Collections.Generic; class Probe {" + declarations + "static void Validate(Material material, string propertyName) {" + name_guard + type_guard + "}" + r'''
 class Material {
  public bool HasProperty(string name) { return new[]{"_MainTex", "_DissolveMask", "_OtherShaderTexture", "_Float", "_Color", "_Vector"}.Contains(name); }
  public string[] GetTexturePropertyNames() { return new[]{"_MainTex", "_DissolveMask", "_OtherShaderTexture"}; }
 }
 static int failures;
 static void Expect(string name, bool expected) {
  bool accepted = true;
  try { Validate(new Material(), name); } catch (InvalidOperationException) { accepted = false; }
  Console.WriteLine((accepted == expected ? "PASS " : "FAIL ") + name);
  if (accepted != expected) failures++;
 }
 static int Main() {
  Expect("_MainTex", true); Expect("_DissolveMask", true); Expect("_OtherShaderTexture", true);
  Expect("_Unknown", false); Expect("_Float", false); Expect("_Color", false); Expect("_Vector", false);
  Expect("", false); Expect("_dissolvemask", false);
  return failures == 0 ? 0 : 1;
 }
 }'''
 output.mkdir(parents=True, exist_ok=True)
 (output / "Probe.cs").write_text(program, encoding="utf-8")
 dll = output / "Probe.dll"
 command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
 command += [f"-r:{p}" for p in references[-1].glob("*.dll")]
 command.append(str(output / "Probe.cs"))
 compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
 assert compiled.returncode == 0, compiled.stdout + compiled.stderr
 (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
 return subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)


def test_actual_texture_validation_blocks(tmp_path):
 result = run_guards(SOURCE, tmp_path / "compiled")
 assert result.returncode == 0, result.stdout + result.stderr
 assert result.stdout.count("PASS ") == 9
