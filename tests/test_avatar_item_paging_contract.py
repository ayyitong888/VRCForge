"""Focused contract checks for complete, digest-bound avatar item pages."""

from pathlib import Path
import subprocess

import jsonschema

from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/GameObjectTools.cs").read_text(encoding="utf-8-sig")


def test_avatar_item_schema_accepts_continuation_and_rejects_invalid_digest_or_offset():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_avatar_items"]
    base = {"avatarPath": "Avatar", "outputPath": "", "maxItems": 2, "refreshAssets": False}
    jsonschema.validate({**base, "offset": 2}, schema)
    jsonschema.validate({**base, "offset": 2, "expectedSnapshotDigest": "a" * 64}, schema)
    for invalid in ({**base, "offset": -1}, {**base, "expectedSnapshotDigest": "short"}):
        try:
            jsonschema.validate(invalid, schema)
        except jsonschema.ValidationError:
            pass
        else:
            raise AssertionError(f"invalid continuation accepted: {invalid}")


def test_avatar_item_source_pages_after_complete_scan_and_exposes_continuation_metadata():
    assert "foreach (var transform in transforms)" in SOURCE
    assert ".Take(12)" not in SOURCE
    assert ".Take(16)" not in SOURCE
    assert "var orderedItems = items" in SOURCE
    assert "PageItems(orderedItems, offset, maxItems)" in SOURCE
    for field in ("snapshotDigest", "totalCount", "hasMore", "nextOffset", "nextRequest"):
        assert f"public {('string' if field == 'snapshotDigest' else 'int' if field in ('offset', 'maxItems', 'totalCount') else 'bool' if field == 'hasMore' else 'int?' if field == 'nextOffset' else 'JObject')} {field}" in SOURCE
    assert '["nextRequest"] = nextRequest' in SOURCE
    assert 'nextRequest["expectedSnapshotDigest"] = snapshotDigest' in SOURCE


def test_avatar_item_page_helper_reconstructs_all_rows_in_compiled_fixture(tmp_path):
    marker = "        private static List<AvatarItem> PageItems("
    start = SOURCE.index(marker)
    brace = SOURCE.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(SOURCE)):
        if SOURCE[index] == "{":
            depth += 1
        elif SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    helper = SOURCE[start:end]
    helper = helper.replace("private static", "public static", 1)
    program = f'''using System; using System.Collections.Generic; using System.Linq;
class AvatarItem {{ public int Id; }}
class Probe {{
{helper}
static void Main() {{
  var all=Enumerable.Range(0,7).Select(i=>new AvatarItem{{Id=i}}).ToList();
  var rows=new List<AvatarItem>(); rows.AddRange(PageItems(all,0,3)); rows.AddRange(PageItems(all,3,3)); rows.AddRange(PageItems(all,6,3));
  if(rows.Count!=7 || !rows.Select(x=>x.Id).SequenceEqual(Enumerable.Range(0,7))) throw new Exception("page reconstruction failed");
  Console.WriteLine("PASS");
}}
}}'''
    (tmp_path / "Program.cs").write_text(program, encoding="utf-8")
    (tmp_path / "probe.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    result = subprocess.run(["dotnet", "run", "--project", str(tmp_path / "probe.csproj"), "--nologo"], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
