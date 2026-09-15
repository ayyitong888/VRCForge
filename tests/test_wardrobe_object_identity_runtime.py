from __future__ import annotations

import subprocess

from test_wardrobe_avatar_identity_runtime import WRITERS, _git_or_worktree, _public


def test_wardrobe_object_selection_rejects_stale_and_ambiguous_paths(tmp_path):
    methods = []
    for name, path in WRITERS.items():
        source = _git_or_worktree(path)
        bodies = [_public(source, marker) for marker in (
            "private static Transform ResolveUnderRoot",
            "private static string RelativePath",
            "private static string NormalizePath",
        )]
        methods.append("public static class " + name + " { " + " ".join(bodies) + " }")
    program = r'''
using System;
using System.Linq;
using System.Collections.Generic;
public class Transform {
    public string name;
    public Transform parent;
    public List<Transform> children = new List<Transform>();
    public Transform Find(string path) {
        var parts = path.Split('/');
        var next = children.FirstOrDefault(x => x.name == parts[0]);
        return parts.Length == 1 || next == null ? next : next.Find(string.Join("/", parts.Skip(1)));
    }
    public T[] GetComponentsInChildren<T>(bool inactive) {
        var nodes = new List<Transform> { this };
        foreach (var child in children) nodes.AddRange(child.GetComponentsInChildren<Transform>(inactive));
        return nodes.Cast<T>().ToArray();
    }
}
METHODS
public static class Probe {
    static int failures;
    static Transform Node(string name, Transform parent = null) {
        var node = new Transform { name = name, parent = parent };
        if (parent != null) parent.children.Add(node);
        return node;
    }
    static void Check(string label, Func<Transform,string,Transform> resolve, Transform root,
                      string path, Transform expected) {
        Transform actual = null;
        try { actual = resolve(root, path); }
        catch (InvalidOperationException) { }
        if (actual != expected) { failures++; Console.WriteLine("FAIL " + label + ":" + path); }
    }
    public static int Main() {
        var root = Node("Avatar");
        var a = Node("A", root); var b = Node("B", root);
        var first = Node("Coat", a); Node("Coat", b);
        var single = Node("Hat", a);
        foreach (var pair in new Dictionary<string,Func<Transform,string,Transform>> {
            {"part", part.ResolveUnderRoot}, {"outfit", outfit.ResolveUnderRoot},
            {"manager", manager.ResolveUnderRoot}}) {
            Check(pair.Key, pair.Value, root, "Missing/Coat", null);
            Check(pair.Key, pair.Value, root, "Coat", null);
            Check(pair.Key, pair.Value, root, "A/Coat", first);
            Check(pair.Key, pair.Value, root, "Avatar/A/Hat", single);
            Check(pair.Key, pair.Value, root, "Hat", single);
            Check(pair.Key, pair.Value, root, "Avatar/Hat", null);
        }
        var namedRoot = Node("Avatar", root);
        var namedCoat = Node("Coat", namedRoot);
        foreach (var pair in new Dictionary<string,Func<Transform,string,Transform>> {
            {"part", part.ResolveUnderRoot}, {"outfit", outfit.ResolveUnderRoot},
            {"manager", manager.ResolveUnderRoot}})
            Check(pair.Key, pair.Value, root, "Avatar/Coat", namedCoat);
        Node("Coat", root);
        foreach (var pair in new Dictionary<string,Func<Transform,string,Transform>> {
            {"part", part.ResolveUnderRoot}, {"outfit", outfit.ResolveUnderRoot},
            {"manager", manager.ResolveUnderRoot}})
            Check(pair.Key, pair.Value, root, "Avatar/Coat", null);
        Node("Coat", a);
        foreach (var pair in new Dictionary<string,Func<Transform,string,Transform>> {
            {"part", part.ResolveUnderRoot}, {"outfit", outfit.ResolveUnderRoot},
            {"manager", manager.ResolveUnderRoot}})
            Check(pair.Key, pair.Value, root, "A/Coat", null);
        return failures == 0 ? 0 : 1;
    }
}
'''.replace("METHODS", "\n".join(methods))
    (tmp_path / "Program.cs").write_text(program, encoding="utf-8")
    project = tmp_path / "probe.csproj"
    project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                       '<OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework>'
                       '</PropertyGroup></Project>', encoding="utf-8")
    result = subprocess.run(["dotnet", "run", "--project", str(project), "--nologo"],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
