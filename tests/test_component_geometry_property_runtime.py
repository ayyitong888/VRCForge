"""Run production DescribeValue with real Unity value types, without starting Unity."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"


@pytest.fixture(scope="module")
def geometry_probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("component-geometry-property")
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    unity = Path("E:/unity/Unity 2022.3.22f1/Editor/Data/Managed/UnityEngine/UnityEngine.CoreModule.dll")
    if not compilers or not references or not unity.exists():
        pytest.skip("Local .NET SDK and Unity 2022.3 reference assembly required")
    newtonsoft = compilers[-1].parents[2] / "Newtonsoft.Json.dll"
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("internal static object DescribeValue(object value)")
    end = source.index("internal static object DescribeValue(\n", start)
    actual = source[start:end]
    runner = r'''
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
class Probe {
    // Scene/asset identity resolution is outside this value-type-only test.
    private static object DescribeUnityObject(UnityEngine.Object value) => throw new Exception("Unexpected object lookup");
    static void Check(bool valid, string message) { if (!valid) throw new Exception(message); }
    static JToken Read(object value) => JToken.Parse(JsonConvert.SerializeObject(DescribeValue(value)));
    static void Number(JToken value, float expected, string location) {
        Check(value != null && (value.Type == JTokenType.Float || value.Type == JTokenType.Integer), location + " must be numeric");
        Check((float)value == expected, location + " lost float precision or changed component position");
    }
    static void Vector(JToken value, Vector3 expected, string location) {
        Check(value is JObject && ((JObject)value).Count == 3, location + " shape");
        Number(value["x"], expected.x, location + ".x");
        Number(value["y"], expected.y, location + ".y");
        Number(value["z"], expected.z, location + ".z");
    }
    static void BoundsCase() {
        var bounds = new Bounds();
        bounds.center = new Vector3(-0.00039123456f, 0.78956318f, 123.456789f);
        bounds.extents = new Vector3(0.00001234567f, 1.23456789f, 9.87654321f);
        var result = Read(bounds);
        Check(result is JObject, "Bounds must be structured, not a rounded display string");
        Check(((JObject)result).Properties().Select(p => p.Name).OrderBy(n => n).SequenceEqual(new[]{"center", "extents"}), "Bounds field contract");
        Vector(result["center"], bounds.center, "center");
        Vector(result["extents"], bounds.extents, "extents");
        var nested = Read(new object[]{bounds});
        Check(JToken.DeepEquals(nested[0], result), "Bounds collection dispatch");
    }
    static void MatrixCase() {
        var matrix = new Matrix4x4();
        for (var row = 0; row < 4; row++)
            for (var column = 0; column < 4; column++)
                matrix[row, column] = (row * 4 + column + 1) * -0.123456789f;
        matrix.m03 = -0.00039123456f;
        matrix.m13 = 0.78956318f;
        matrix.m23 = 0.0254219876f;
        matrix.m30 = 0.00001234567f;
        var result = Read(matrix);
        Check(result is JObject, "Matrix4x4 must be structured, not a rounded display string");
        Check(((JObject)result).Count == 16, "Matrix must expose exactly 16 named components");
        for (var row = 0; row < 4; row++)
            for (var column = 0; column < 4; column++) {
                var name = "m" + row + column;
                Number(result[name], matrix[row, column], name);
            }
    }
    static void ExistingCase() {
        Vector(Read(new Vector3(1, 2, 3)), new Vector3(1, 2, 3), "Vector3");
        Check(Read(new Vector2(1, 2)).ToString(Formatting.None) == "{\"x\":1.0,\"y\":2.0}", "Vector2 shape");
        Check(((JObject)Read(new Vector4(1, 2, 3, 4))).Count == 4, "Vector4 shape");
        Check(((JObject)Read(new Quaternion(1, 2, 3, 4))).Count == 4, "Quaternion shape");
        var color = Read(new Color(.123456789f, .2f, .3f, 1f));
        Check(((JObject)color).Properties().Select(p => p.Name).SequenceEqual(new[]{"r", "g", "b", "a"}), "Color shape");
        Number(color["r"], .123456789f, "Color.r");
        Check(Read(null).Type == JTokenType.Null, "Null dispatch");
        Check((string)Read("text") == "text" && (bool)Read(true), "Scalar dispatch");
    }
    static int Main(string[] args) {
        CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("fr-FR");
        if (args[0] == "bounds") BoundsCase();
        else if (args[0] == "matrix") MatrixCase();
        else ExistingCase();
        Console.WriteLine("PASS " + args[0]);
        return 0;
    }
ACTUAL
}
'''.replace("ACTUAL", actual)
    cs = output / "Probe.cs"
    cs.write_text(runner, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [str(base / "dotnet.exe"), str(compilers[-1]), "/nologo", "/target:exe", f"/out:{dll}"]
    command += [f"/r:{path}" for path in references[-1].glob("*.dll")]
    command += [f"/r:{unity}", f"/r:{newtonsoft}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(unity, output / unity.name)
    shared_internals = unity.with_name("UnityEngine.SharedInternalsModule.dll")
    shutil.copy2(shared_internals, output / shared_internals.name)
    shutil.copy2(newtonsoft, output / newtonsoft.name)
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}
    }), encoding="utf-8")
    return base / "dotnet.exe", dll


@pytest.mark.parametrize("case", ["bounds", "matrix", "existing"])
def test_component_geometry_property_numeric_contract(geometry_probe, case):
    dotnet, dll = geometry_probe
    result = subprocess.run([str(dotnet), str(dll), case], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
