from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs"


def _source() -> str:
    revision = os.environ.get("VRCFORGE_PARAMETER_WRITER_GIT_REF", "").strip()
    if revision:
        return subprocess.check_output(
            ["git", "show", f"{revision}:Assets/VRCForge/Editor/AvatarParameterWriter.cs"],
            text=True,
        )
    return SOURCE_PATH.read_text(encoding="utf-8-sig")


def _extract_method(source: str, marker: str, start: int = 0) -> str:
    begin = source.index(marker, start)
    brace = source.index("{", begin)
    depth = 0
    end = brace
    while end < len(source):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[begin : end + 1]
        end += 1
    raise AssertionError(f"unterminated method: {marker}")


def _harness_source(product: str) -> str:
    resolver_marker = "private static VRCAvatarDescriptor ResolveAvatarDescriptor"
    first = _extract_method(product, resolver_marker)
    second = _extract_method(product, resolver_marker, product.index(resolver_marker) + 1)
    path_method = _extract_method(product, "private static string GetTransformPath")
    normalize_method = _extract_method(product, "private static string NormalizePath")
    first = first.replace("private static", "public static", 1).replace(
        "ResolveAvatarDescriptor", "ResolveAvatarDescriptorApply", 1
    )
    second = second.replace("private static", "public static", 1).replace(
        "ResolveAvatarDescriptor", "ResolveAvatarDescriptorRollback", 1
    )
    path_method = path_method.replace("private static", "public static", 1)
    normalize_method = normalize_method.replace("private static", "public static", 1)
    return f'''
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;
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
public static class Harness {{
    {first}
    {second}
    {path_method}
    {normalize_method}

    private static VRC.SDK3.Avatars.Components.VRCAvatarDescriptor Avatar(string path) {{
        UnityEngine.Transform parent = null;
        foreach (var segment in path.Split('/')) {{
            var next = new UnityEngine.Transform {{ name = segment, parent = parent }};
            parent = next;
        }}
        return new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor {{
            name = parent.name, transform = parent, gameObject = new UnityEngine.GameObject {{ name = parent.name }}
        }};
    }}

    private static int Check(Func<string, VRC.SDK3.Avatars.Components.VRCAvatarDescriptor> resolve) {{
        var cases = new[] {{
            ("empty-ambiguous", "", new[] {{ "Root/A", "Other/B" }}, false),
            ("same-leaf-ambiguous", "Avatar", new[] {{ "One/Avatar", "Two/Avatar" }}, false),
            ("duplicate-exact-path", "Root/Avatar", new[] {{ "Root/Avatar", "Root/Avatar" }}, false),
            ("empty-unique", "", new[] {{ "Root/Avatar" }}, true),
            ("full-path-unique", "Root/Avatar", new[] {{ "Root/Avatar", "Other/Avatar" }}, true),
        }};
        foreach (var item in cases) {{
            UnityEngine.Resources.Items = item.Item3.Select(Avatar).ToList();
            try {{
                var result = resolve(item.Item2);
                if (!item.Item4 || result == null) return 10;
            }} catch {{ if (item.Item4) return 11; }}
        }}
        return 0;
    }}
    public static int Main() {{
        var first = Check(ResolveAvatarDescriptorApply);
        var second = Check(ResolveAvatarDescriptorRollback);
        return first == 0 && second == 0 ? 0 : first != 0 ? first : second;
    }}
}}
'''


def _compile_and_run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "harness.csproj"
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(_harness_source(_source()), encoding="utf-8")
    return subprocess.run(
        ["dotnet", "run", "--project", str(project), "--nologo"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_both_parameter_writer_resolvers_cover_ambiguity_and_unique_selection(tmp_path: Path) -> None:
    result = _compile_and_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
