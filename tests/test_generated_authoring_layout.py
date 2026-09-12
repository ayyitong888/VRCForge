"""Execute the production generated-path policy with the installed .NET SDK."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from xml.sax.saxutils import escape

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/GeneratedAssetPaths.cs"
AUTHORING = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"


def _production_method(source: str, signature: str) -> str:
    """Compile the actual method text; the fixture supplies only Unity API doubles."""
    start = source.index(signature)
    body = source.index("{", start)
    depth = 0
    for end in range(body, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError(f"Unclosed production method: {signature}")


MENU_STUBS = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using VRCForge.Editor;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;
namespace UnityEngine {
    public class Object { public int GetInstanceID() { return GetHashCode(); } }
    public class ScriptableObject : Object {
        public static T CreateInstance<T>() where T : new() { return new T(); }
    }
}
namespace UnityEditor {
    public static class AssetDatabase {
        public static readonly List<string> Created = new List<string>();
        public static readonly List<string> ExistingFolders = new List<string>();
        public static bool GenerateReturnsEmpty;
        public static bool IsValidFolder(string path) { return ExistingFolders.Contains(path); }
        public static string GenerateUniqueAssetPath(string path) { return GenerateReturnsEmpty ? "" : path; }
        public static void CreateAsset(Object asset, string path) {
            if (Created.Contains(path)) throw new InvalidOperationException("Duplicate asset: " + path);
            Created.Add(path);
        }
    }
    public static class Undo { public static void RegisterCreatedObjectUndo(Object asset, string name) {} }
    public static class EditorUtility { public static void SetDirty(Object asset) {} }
}
internal class VRCExpressionsMenu : ScriptableObject {
    public const int MAX_CONTROLS = 8;
    public List<Control> controls = new List<Control>();
    public class Control {
        public enum ControlType { Toggle, SubMenu }
        public string name;
        public ControlType type;
        public VRCExpressionsMenu subMenu;
    }
}
internal static class AvatarAuthoringCrudCore {
    public static void EnsureAssetFolder(string path) { GeneratedAssetPaths.ValidateNewAssetPath(path); }
    SANITIZE_METHOD
}
internal static class MenuProduction {
    PRODUCTION_METHODS
    public static string Exercise(int count, int overflowCount) {
        var root = new VRCExpressionsMenu();
        for (var i = 0; i < count; i++) root.controls.Add(new VRCExpressionsMenu.Control { name = "Control" + i });
        if (overflowCount >= 0) {
            var more = new VRCExpressionsMenu();
            for (var i = 0; i < overflowCount; i++) more.controls.Add(new VRCExpressionsMenu.Control { name = "More" + i });
            root.controls[count - 1] = new VRCExpressionsMenu.Control { name = "More", type = VRCExpressionsMenu.Control.ControlType.SubMenu, subMenu = more };
        }
        var before = root.controls.ToArray();
        const string dir = "Assets/VRCForgeGenerated/Test/Menus";
        var paths = PlanMenuAssetPaths(root, "Same/Same", dir, dir + "/Root.asset", "Toggle", VRCExpressionsMenu.Control.ControlType.Toggle, false);
        if (AssetDatabase.Created.Count != 0 || !root.controls.SequenceEqual(before))
            throw new InvalidOperationException("Preview mutated the menu.");
        var queue = new Queue<string>(paths);
        var target = EnsureMenuPath(root, "Same/Same", dir, queue);
        EnsureMenuHasRoom(target, dir, queue);
        if (queue.Count != 0 || !AssetDatabase.Created.SequenceEqual(paths))
            throw new InvalidOperationException("Preview and apply created different paths.");
        return string.Join("|", paths);
    }
}
'''


@pytest.fixture(scope="module")
def path_harness(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path, dict[str, str]]:
    assert SOURCE.is_file(), "Generated authoring has no shared safe output-path policy."
    work = tmp_path_factory.mktemp("generated-authoring-paths")
    environment = os.environ.copy()
    environment.update(
        DOTNET_CLI_HOME=str(work / "dotnet-home"),
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
        NUGET_PACKAGES=str(work / "nuget"),
        NUGET_HTTP_CACHE_PATH=str(work / "nuget-http"),
    )
    candidates = [shutil.which("dotnet")]
    if os.environ.get("DOTNET_ROOT"):
        candidates.append(str(Path(os.environ["DOTNET_ROOT"]) / "dotnet.exe"))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(str(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/dotnet/dotnet.exe"))
    dotnet = None
    for candidate in dict.fromkeys(candidates):
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run([candidate, "--list-sdks"], env=environment, capture_output=True, text=True, timeout=15)
        if probe.returncode == 0 and any(line.split(".")[0].isdigit() and int(line.split(".")[0]) >= 8 for line in probe.stdout.splitlines()):
            dotnet = candidate
            break
    if dotnet is None:
        pytest.skip("A .NET 8 or newer SDK is required to execute the production C# path policy.")
    (work / "Harness.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        '<OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>'
        '<ImplicitUsings>disable</ImplicitUsings><Nullable>disable</Nullable>'
        '</PropertyGroup><ItemGroup><Compile Include="'
        + escape(SOURCE.as_posix(), {'"': "&quot;"})
        + '" /></ItemGroup></Project>',
        encoding="utf-8",
    )
    (work / "Harness.cs").write_text(
        """using System;
using VRCForge.Editor;
internal static class Harness
{
    static int Main(string[] args)
    {
        try
        {
            if (args[0] == "menu")
            {
                Console.WriteLine(MenuProduction.Exercise(int.Parse(args[1]), int.Parse(args[2])));
                return 0;
            }
            if (args[0] == "unique")
            {
                UnityEditor.AssetDatabase.GenerateReturnsEmpty = args[2] == "empty";
                if (args[3] == "existing") UnityEditor.AssetDatabase.ExistingFolders.Add("Assets/Test");
                Console.WriteLine(GeneratedAssetPaths.UniqueAssetPath(args[1]));
                return 0;
            }
            Console.WriteLine(args[0] == "path"
                ? GeneratedAssetPaths.ValidateNewAssetPath(args[1])
                : GeneratedAssetPaths.ResolveDirectory(args[1], args[2], args[3], args[4], args[0] == "categories"));
            return 0;
        }
        catch (InvalidOperationException ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 2;
        }
    }
}
""",
        encoding="utf-8",
    )
    production = AUTHORING.read_text(encoding="utf-8")
    menu_methods = "\n".join(_production_method(production, signature) for signature in (
        "private static List<string> PlanMenuAssetPaths(",
        "private static void PlanMenuRoom(",
        "private static VRCExpressionsMenu EnsureMenuPath(",
        "private static VRCExpressionsMenu EnsureMenuHasRoom(",
    ))
    (work / "MenuProduction.cs").write_text(
        MENU_STUBS.replace("SANITIZE_METHOD", _production_method(production, "internal static string Sanitize("))
        .replace("PRODUCTION_METHODS", menu_methods), encoding="utf-8",
    )
    result = subprocess.run(
        [dotnet, "build", "--nologo", "-c", "Release", "--disable-build-servers", "/p:UseSharedCompilation=false"],
        cwd=work, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dotnet, work / "bin/Release/net8.0/Harness.dll", environment


def run_policy(path_harness, *args: str) -> subprocess.CompletedProcess[str]:
    dotnet, harness, environment = path_harness
    return subprocess.run(
        [dotnet, str(harness), *args], cwd=harness.parent, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


@pytest.mark.parametrize("category", ["Animations", "Controllers", "Menus", "Parameters"])
@pytest.mark.parametrize("domain", ["", "Wardrobe", "FX"])
def test_default_output_is_avatar_scoped_and_classified(path_harness, category, domain):
    result = run_policy(path_harness, "directory", "", "Test Avatar", category, domain)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/".join(
        ["Assets/VRCForgeGenerated/Test_Avatar", *([domain] if domain else []), category]
    )


@pytest.mark.parametrize(
    "mode,requested,expected",
    [
        ("directory", "Assets/My Avatar/Clips", "Assets/My Avatar/Clips"),
        ("directory", "Assets\\User Clips\\", "Assets/User Clips"),
        ("categories", "Assets/My Avatar", "Assets/My Avatar/Animations"),
        ("categories", "Assets/My Avatar/Animations", "Assets/My Avatar/Animations"),
        ("categories", "Assets/My Avatar/Controllers", "Assets/My Avatar/Animations"),
        ("directory", "Assets/VRCForgeExtras", "Assets/VRCForgeExtras"),
    ],
)
def test_explicit_assets_directories_keep_user_scope(path_harness, mode, requested, expected):
    result = run_policy(path_harness, mode, requested, "Test", "Animations", "")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "path",
    [
        "Assets/VRCForge", "Assets/vrcforge/Generated/Test.anim",
        "Assets/VRCForgeGenerated/../VRCForge/Test.anim",
        "Assets/User/../../Outside", "Assets/./VRCForge/Test.anim",
        "Assets/VRCForge./Test.anim", "Assets/VRCForge /Test.anim",
        "Assets//VRCForge/Test.anim", "/Assets/User/Test.anim",
        "C:/Project/Assets/User", "Packages/Test", "Assets/User:stream/Test.anim",
    ],
)
def test_new_output_rejects_plugin_paths_and_path_aliases(path_harness, path):
    result = run_policy(path_harness, "path", path)
    assert result.returncode == 2, result.stdout + result.stderr


def test_existing_custom_asset_path_keeps_spelling(path_harness):
    result = run_policy(path_harness, "path", "Assets/My Avatar/Animations/衣服.anim")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Assets/My Avatar/Animations/衣服.anim"


def test_missing_parent_keeps_requested_path_when_unity_returns_empty(path_harness):
    result = run_policy(path_harness, "unique", "Assets/Test/New.asset", "empty", "missing")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Assets/Test/New.asset"


def test_existing_parent_rejects_empty_unity_unique_path(path_harness):
    result = run_policy(path_harness, "unique", "Assets/Test/New.asset", "empty", "existing")
    assert result.returncode == 2, result.stdout
    assert "empty generated asset path" in result.stderr


@pytest.mark.parametrize("count,overflow_count,expected_count", [(0, -1, 2), (8, -1, 3), (8, 0, 2), (8, 8, 3)])
def test_production_menu_preview_and_apply_share_reserved_paths(path_harness, count, overflow_count, expected_count):
    result = run_policy(path_harness, "menu", str(count), str(overflow_count))
    assert result.returncode == 0, result.stdout + result.stderr
    paths = result.stdout.strip().split("|")
    assert len(paths) == expected_count
    assert len(set(paths)) == len(paths)
    assert all(path.startswith("Assets/VRCForgeGenerated/Test/Menus/") for path in paths)
