from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
WRITERS = {
    "part": "Assets/VRCForge/Editor/WardrobeOutfitPartWriter.cs",
    "outfit": "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs",
    "manager": "Assets/VRCForge/Editor/WardrobeManagerWriter.cs",
}


def _git_or_worktree(relative: str) -> str:
    revision = os.environ.get("VRCFORGE_WARDROBE_AVATAR_GIT_REF", "").strip()
    if revision:
        return subprocess.check_output(
            ["git", "show", f"{revision}:{relative}"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _extract(source: str, marker: str, start: int = 0) -> str:
    begin = source.index(marker, start)
    brace = source.index("{", begin)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[begin : index + 1]
    raise AssertionError(f"unterminated method: {marker}")


def _public(source: str, marker: str) -> str:
    return (
        _extract(source, marker)
        .replace("private static", "public static", 1)
        .replace("internal static", "public static", 1)
    )


def _program(product: str, shared: str) -> str:
    shared_resolver = _public(shared, "internal static VRCAvatarDescriptor ResolveAvatarDescriptor")
    shared_path = _public(shared, "internal static string GetTransformPath")
    shared_normalize = _public(shared, "internal static string NormalizePath")
    methods = []
    for key, relative in WRITERS.items():
        body = _public(product[key], "private static VRCAvatarDescriptor ResolveAvatarDescriptor")
        path = _public(product[key], "private static string GetTransformPath")
        normalize = _public(product[key], "private static string NormalizePath")
        methods.append(f"public static class {key} {{ {body} {path} {normalize} }}")
    return f'''
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEditor;
using VRC.SDK3.Avatars.Components;

namespace UnityEngine {{
    public class Object {{ }}
    public class Scene {{ public bool IsValid() => true; public bool isLoaded = true; }}
    public class GameObject : Object {{ public Scene scene = new Scene(); public string name; }}
    public class Transform : Object {{ public string name; public Transform parent; }}
    public static class Resources {{
        public static List<VRCAvatarDescriptor> Items = new List<VRCAvatarDescriptor>();
        public static T[] FindObjectsOfTypeAll<T>() where T : class => Items.Cast<T>().ToArray();
    }}
}}
namespace UnityEditor {{ public static class EditorUtility {{ public static bool IsPersistent(object value) => false; }} }}
namespace VRC.SDK3.Avatars.Components {{
    public class VRCAvatarDescriptor {{
        public string name; public UnityEngine.GameObject gameObject; public UnityEngine.Transform transform;
    }}
}}
public static class AvatarAuthoringCrudCore {{ {shared_resolver} {shared_path} {shared_normalize} }}
{''.join(methods)}
public static class Probe {{
    static int failures;
    static void Check(bool value, string label) {{ if (!value) {{ Console.WriteLine("FAIL " + label); failures++; }} }}
    static VRCAvatarDescriptor Avatar(string path) {{
        UnityEngine.Transform parent = null;
        foreach (var segment in path.Split('/')) {{
            var next = new UnityEngine.Transform {{ name = segment, parent = parent }};
            parent = next;
        }}
        return new VRCAvatarDescriptor {{ name = parent.name, transform = parent,
            gameObject = new UnityEngine.GameObject {{ name = parent.name }} }};
    }}
    static void CheckResolver(string label, Func<string, VRCAvatarDescriptor> resolve) {{
        var cases = new[] {{
            ("empty-many", "", new[] {{ "Root/A", "Other/B" }}, false),
            ("same-leaf", "Avatar", new[] {{ "One/Avatar", "Two/Avatar" }}, false),
            ("duplicate-path", "Root/Avatar", new[] {{ "Root/Avatar", "Root/Avatar" }}, false),
            ("empty-one", "", new[] {{ "Root/Avatar" }}, true),
            ("full-path", "Root/Avatar", new[] {{ "Root/Avatar", "Other/Avatar" }}, true),
        }};
        foreach (var item in cases) {{
            UnityEngine.Resources.Items = item.Item3.Select(Avatar).ToList();
            try {{ var resolved = resolve(item.Item2); Check(item.Item4 == (resolved != null), label + ":" + item.Item1); }}
            catch {{ Check(!item.Item4, label + ":" + item.Item1); }}
        }}
    }}
    public static int Main() {{
        CheckResolver("part", part.ResolveAvatarDescriptor);
        CheckResolver("outfit", outfit.ResolveAvatarDescriptor);
        CheckResolver("manager", manager.ResolveAvatarDescriptor);
        return failures == 0 ? 0 : 1;
    }}
}}
'''


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    product = {key: _git_or_worktree(path) for key, path in WRITERS.items()}
    project = tmp_path / "probe.csproj"
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(_program(product, _git_or_worktree(
        "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
    )), encoding="utf-8")
    return subprocess.run(
        ["dotnet", "run", "--project", str(project), "--nologo"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_wardrobe_writers_share_strict_avatar_identity_resolution(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
