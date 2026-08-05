from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpSourceMigration.cs"


HARNESS = r'''
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace UnityEngine {
    public static class Application { public static string dataPath; }
    public static class Debug {
        public static readonly List<string> Warnings = new List<string>();
        public static void LogWarning(string message) { Warnings.Add(message); }
        public static void Log(string message) { }
    }
}

namespace UnityEditor {
    [AttributeUsage(AttributeTargets.Class)] public sealed class InitializeOnLoadAttribute : Attribute { }
    public static class EditorApplication { public static event Action delayCall; }
    public static class AssetDatabase {
        public static string ProjectRoot;
        public static int StartCount;
        public static int StopCount;
        public static readonly HashSet<string> ThrowDelete = new HashSet<string>(StringComparer.Ordinal);
        public static void StartAssetEditing() { StartCount++; }
        public static void StopAssetEditing() { StopCount++; }
        public static bool DeleteAsset(string path) {
            if (ThrowDelete.Contains(path)) throw new IOException("injected delete failure");
            var full = Path.Combine(ProjectRoot, path.Replace('/', Path.DirectorySeparatorChar));
            if (File.Exists(full)) {
                File.Delete(full);
                if (File.Exists(full + ".meta")) File.Delete(full + ".meta");
                return true;
            }
            if (Directory.Exists(full)) {
                Directory.Delete(full);
                if (File.Exists(full + ".meta")) File.Delete(full + ".meta");
                return true;
            }
            return false;
        }
        public static void Reset() { StartCount = 0; StopCount = 0; ThrowDelete.Clear(); }
    }
    public static class FileUtil {
        public static readonly HashSet<string> ThrowDelete = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public static void DeleteFileOrDirectory(string path) {
            if (ThrowDelete.Contains(path)) throw new IOException("injected orphan metadata delete failure");
            if (File.Exists(path)) File.Delete(path);
            else if (Directory.Exists(path)) Directory.Delete(path, true);
        }
    }
}

internal static class Program {
    private const string NoticeRoot = "Assets/VRCForge/ThirdPartyNotices";
    private static readonly Type Migration = typeof(VRCForge.Editor.MCP.VRCForgeMcpSourceMigration);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateSymbolicLink(string linkPath, string targetPath, int flags);

    private static string Sha(string value) {
        using (var hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(value))).Replace("-", "");
    }

    private static IDictionary ResetRetired(params string[] pairs) {
        var field = Migration.GetField("RetiredPaths", BindingFlags.Static | BindingFlags.NonPublic);
        var retired = (IDictionary)field.GetValue(null);
        retired.Clear();
        for (var index = 0; index < pairs.Length; index += 2) {
            retired.Add(Sha(NoticeRoot + "/" + pairs[index]), new HashSet<string>(new[] { pairs[index + 1] }, StringComparer.Ordinal));
        }
        return retired;
    }

    private static IDictionary ResetRetiredMeta(string assetPath, string metaPath) {
        var field = Migration.GetField("RetiredMetaPaths", BindingFlags.Static | BindingFlags.NonPublic);
        var retired = (IDictionary)field.GetValue(null);
        retired.Clear();
        using (var stream = new FileStream(metaPath, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var hash = SHA256.Create()) {
            var digest = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "");
            retired.Add(Sha(assetPath), new HashSet<string>(new[] { digest }, StringComparer.Ordinal));
        }
        return retired;
    }

    private static IDictionary ResetRetiredEmptyFolder(string assetPath, string metaPath) {
        var field = Migration.GetField("RetiredEmptyFolderPaths", BindingFlags.Static | BindingFlags.NonPublic);
        var retired = (IDictionary)field.GetValue(null);
        retired.Clear();
        using (var stream = new FileStream(metaPath, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var hash = SHA256.Create()) {
            var digest = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "");
            retired.Add(Sha(assetPath), new HashSet<string>(new[] { digest }, StringComparer.Ordinal));
        }
        return retired;
    }

    private static void Invoke(string root) {
        UnityEngine.Application.dataPath = Path.Combine(root, "Assets");
        UnityEditor.AssetDatabase.ProjectRoot = root;
        UnityEngine.Debug.Warnings.Clear();
        Migration.GetMethod("RemoveRetiredAssets", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
    }

    private static string Write(string root, string relative, string content) {
        var full = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(full));
        File.WriteAllText(full, content);
        return full;
    }

    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private static bool WarningContains(string needle) {
        foreach (var warning in UnityEngine.Debug.Warnings) if (warning.IndexOf(needle, StringComparison.Ordinal) >= 0) return true;
        return false;
    }

    private static void PreserveCases(string root) {
        // Exact known name but wrong digest: do not delete.
        ResetRetired("Exact.txt", Sha("expected"));
        var exact = Write(root, NoticeRoot + "/Exact.txt", "different");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(exact), "exact-name hash mismatch was deleted");
        Require(WarningContains("Preserved modified retired asset"), "exact-name hash mismatch was silent: " + String.Join(" | ", UnityEngine.Debug.Warnings));

        // Same bytes under an unknown name: do not delete merely by content.
        ResetRetired("Exact.txt", Sha("same-bytes"));
        var renamed = Write(root, NoticeRoot + "/RenamedByUser.txt", "same-bytes");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(renamed) && WarningContains("Preserved unknown or renamed retired asset"), "same-byte renamed unknown notice was deleted or was silent");

        // Unknown bytes in the exact retired folder are preserved and reported.
        var unknown = Write(root, NoticeRoot + "/Unknown.txt", "unrecognized");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(unknown) && WarningContains("Preserved unknown or renamed retired asset"), "unknown retired-folder entry was deleted or was silent");

        // Exact known name with modified content is preserved and reported.
        ResetRetired("Modified.txt", Sha("original"));
        var modified = Write(root, NoticeRoot + "/Modified.txt", "edited-by-user");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(modified) && WarningContains("Preserved modified retired asset"), "modified notice was deleted or was silent");
    }

    private static void IsolationCase(string root) {
        ResetRetired("First.txt", Sha("first"), "Second.txt", Sha("second"));
        var first = Write(root, NoticeRoot + "/First.txt", "first");
        var second = Write(root, NoticeRoot + "/Second.txt", "second");
        UnityEditor.AssetDatabase.Reset();
        UnityEditor.AssetDatabase.ThrowDelete.Add(NoticeRoot + "/First.txt");
        Invoke(root);
        Require(File.Exists(first), "failed first delete was not preserved");
        Require(!File.Exists(second), "later item did not migrate after first failure");
        Require(UnityEditor.AssetDatabase.StartCount == 1 && UnityEditor.AssetDatabase.StopCount == 1, "asset editing was not balanced");
        Require(WarningContains("could not be verified") && WarningContains("First.txt"), "per-item failure was not warned");
    }

    private static void OrphanMetaCases(string root) {
        ResetRetired();
        var exactMeta = Write(root, NoticeRoot + "/Retired.cs.meta", "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n");
        ResetRetiredMeta(NoticeRoot + "/Retired.cs", exactMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(!File.Exists(exactMeta), "byte-exact retired orphan metadata was preserved");

        var modifiedMeta = Write(root, NoticeRoot + "/Modified.cs.meta", "fileFormatVersion: 2\nguid: fedcba9876543210fedcba9876543210\n");
        ResetRetiredMeta(NoticeRoot + "/Modified.cs", modifiedMeta);
        File.AppendAllText(modifiedMeta, "userData: changed\n");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(modifiedMeta), "modified retired orphan metadata was deleted");
        Require(WarningContains("Preserved modified retired orphan metadata"), "modified orphan metadata was silent");

        var pairedAsset = Write(root, NoticeRoot + "/Paired.cs", "// retained asset");
        var pairedMeta = Write(root, NoticeRoot + "/Paired.cs.meta", "fileFormatVersion: 2\nguid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n");
        ResetRetiredMeta(NoticeRoot + "/Paired.cs", pairedMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(pairedAsset) && File.Exists(pairedMeta), "metadata for a present asset was removed");

        var unknownMeta = Write(root, NoticeRoot + "/Unknown.cs.meta", "fileFormatVersion: 2\nguid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n");
        ResetRetiredMeta(NoticeRoot + "/Different.cs", unknownMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(unknownMeta), "unknown orphan metadata was removed");

        var failedMeta = Write(root, NoticeRoot + "/Failed.cs.meta", "fileFormatVersion: 2\nguid: cccccccccccccccccccccccccccccccc\n");
        var laterMeta = Write(root, NoticeRoot + "/Later.cs.meta", "fileFormatVersion: 2\nguid: dddddddddddddddddddddddddddddddd\n");
        var field = Migration.GetField("RetiredMetaPaths", BindingFlags.Static | BindingFlags.NonPublic);
        var retired = (IDictionary)field.GetValue(null);
        retired.Clear();
        ResetRetiredMeta(NoticeRoot + "/Failed.cs", failedMeta);
        using (var stream = new FileStream(laterMeta, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var hash = SHA256.Create()) {
            retired.Add(Sha(NoticeRoot + "/Later.cs"), new HashSet<string>(new[] {
                BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "")
            }, StringComparer.Ordinal));
        }
        UnityEditor.FileUtil.ThrowDelete.Clear();
        UnityEditor.FileUtil.ThrowDelete.Add(failedMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(failedMeta), "failed orphan metadata deletion was not isolated");
        Require(!File.Exists(laterMeta), "later orphan metadata did not migrate after one failure");
        Require(WarningContains("could not be verified") && WarningContains("Failed.cs.meta"), "orphan metadata failure was not warned");
        UnityEditor.FileUtil.ThrowDelete.Clear();
    }

    private static void EmptyFolderCases(string root) {
        ResetRetired();
        var exactPath = NoticeRoot + "/ExactFolder";
        var exact = Path.Combine(root, exactPath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(exact);
        var exactMeta = Write(root, exactPath + ".meta", "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\nfolderAsset: yes\n");
        ResetRetiredEmptyFolder(exactPath, exactMeta);
        ResetRetiredMeta(exactPath, exactMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(!Directory.Exists(exact) && !File.Exists(exactMeta), "byte-exact empty retired folder was preserved");

        var modifiedPath = NoticeRoot + "/ModifiedFolder";
        var modified = Path.Combine(root, modifiedPath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(modified);
        var modifiedMeta = Write(root, modifiedPath + ".meta", "fileFormatVersion: 2\nguid: fedcba9876543210fedcba9876543210\nfolderAsset: yes\n");
        ResetRetiredEmptyFolder(modifiedPath, modifiedMeta);
        File.AppendAllText(modifiedMeta, "userData: changed\n");
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(Directory.Exists(modified) && File.Exists(modifiedMeta), "modified retired folder metadata was deleted");
        Require(WarningContains("unknown or modified metadata"), "modified retired folder metadata was silent");

        var nonEmptyPath = NoticeRoot + "/NonEmptyFolder";
        var nonEmpty = Path.Combine(root, nonEmptyPath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(nonEmpty);
        var nonEmptyMeta = Write(root, nonEmptyPath + ".meta", "fileFormatVersion: 2\nguid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nfolderAsset: yes\n");
        Write(root, nonEmptyPath + "/User.txt", "keep-me");
        ResetRetiredEmptyFolder(nonEmptyPath, nonEmptyMeta);
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(Directory.Exists(nonEmpty) && File.Exists(Path.Combine(nonEmpty, "User.txt")), "non-empty retired folder was deleted");
        Require(WarningContains("Preserved non-empty retired folder"), "non-empty retired folder was silent");
    }

    private static void ReparseCase(string root) {
        var outside = Path.Combine(root, "outside");
        Directory.CreateDirectory(outside);
        var sentinel = Write(outside, "ThirdPartyNotices/Exact.txt", "reparse-bytes");
        var assets = Path.Combine(root, "Assets");
        Directory.CreateDirectory(assets);
        if (!CreateSymbolicLink(Path.Combine(assets, "VRCForge"), outside, 0x1 | 0x2)) {
            Console.WriteLine("REPARSE_UNAVAILABLE:" + Marshal.GetLastWin32Error());
            return;
        }
        ResetRetired("Exact.txt", Sha("reparse-bytes"));
        UnityEditor.AssetDatabase.Reset();
        Invoke(root);
        Require(File.Exists(sentinel), "reparse path reached the external sentinel");
        Require(WarningContains("reparse point"), "reparse preservation was not warned");
        Console.WriteLine("REPARSE_OK");
    }

    public static int Main(string[] args) {
        var root = args[0];
        Directory.CreateDirectory(Path.Combine(root, "Assets"));
        if (args.Length > 1 && args[1] == "reparse") {
            ReparseCase(root);
            Console.WriteLine("OK");
            return 0;
        }
        PreserveCases(root);
        IsolationCase(root);
        OrphanMetaCases(root);
        EmptyFolderCases(root);
        Console.WriteLine("OK");
        return 0;
    }
}
'''


@pytest.fixture(scope="module")
def migration_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("mcp-source-migration-runtime")
    (work / "Harness.cs").write_text(textwrap.dedent(HARNESS), encoding="utf-8")
    dotnet = shutil.which("dotnet")
    if dotnet:
        sdk_probe = subprocess.run([dotnet, "--list-sdks"], text=True, capture_output=True, timeout=15)
        if sdk_probe.returncode == 0 and sdk_probe.stdout.strip():
            (work / "Harness.csproj").write_text(
                textwrap.dedent(
                    f"""
                    <Project Sdk="Microsoft.NET.Sdk">
                      <PropertyGroup>
                        <OutputType>Exe</OutputType>
                        <TargetFramework>net8.0</TargetFramework>
                        <ImplicitUsings>disable</ImplicitUsings>
                        <Nullable>disable</Nullable>
                      </PropertyGroup>
                      <ItemGroup>
                        <Compile Include="{MIGRATION.as_posix()}" Link="VRCForgeMcpSourceMigration.cs" />
                      </ItemGroup>
                    </Project>
                    """
                ),
                encoding="utf-8",
            )
            build = subprocess.run(
                [dotnet, "build", "--nologo", "-c", "Release"], cwd=work, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=90
            )
            assert build.returncode == 0, build.stdout + build.stderr
            return work / "bin" / "Release" / "net8.0" / "Harness.dll"

    compiler_candidates = [
        (
            Path(r"E:\unity\Unity 2022.3.22f1\Editor\Data\MonoBleedingEdge\bin\mono.exe"),
            Path(r"E:\unity\Unity 2022.3.22f1\Editor\Data\MonoBleedingEdge\lib\mono\msbuild\Current\bin\Roslyn\csc.exe"),
        ),
        (
            Path(r"C:\Program Files\Unity\Hub\Editor\2022.3.22f1\Editor\Data\MonoBleedingEdge\bin\mono.exe"),
            Path(r"C:\Program Files\Unity\Hub\Editor\2022.3.22f1\Editor\Data\MonoBleedingEdge\lib\mono\msbuild\Current\bin\Roslyn\csc.exe"),
        ),
    ]
    compiler_pair = next(
        ((mono, compiler) for mono, compiler in compiler_candidates if mono.is_file() and compiler.is_file()),
        None,
    )
    if compiler_pair is None:
        pytest.skip("neither a dotnet SDK nor a Unity Roslyn compiler is available")
    mono, compiler = compiler_pair
    output = work / "Harness.exe"
    build = subprocess.run(
        [
            str(mono),
            str(compiler),
            "/nologo",
            "/target:exe",
            "/langversion:latest",
            f"/out:{output}",
            str(work / "Harness.cs"),
            str(MIGRATION),
        ],
        cwd=work,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return output


def _run_harness(harness: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [str(harness)] if harness.suffix.lower() == ".exe" else ["dotnet", str(harness)]
    return subprocess.run(
        [*command, str(root), *args], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30
    )


def test_migration_preserves_hash_mismatch_renamed_same_bytes_and_modified_content(migration_harness: Path, tmp_path: Path) -> None:
    result = _run_harness(migration_harness, tmp_path / "project")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_migration_isolates_per_item_failure_warns_and_always_stops_asset_editing(migration_harness: Path, tmp_path: Path) -> None:
    result = _run_harness(migration_harness, tmp_path / "project")
    assert result.returncode == 0, result.stdout + result.stderr


def test_migration_reparse_path_preserves_external_sentinel_or_skips_when_unavailable(migration_harness: Path, tmp_path: Path) -> None:
    result = _run_harness(migration_harness, tmp_path / "project", "reparse")
    if "REPARSE_UNAVAILABLE:" in result.stdout:
        error_code = int(result.stdout.split("REPARSE_UNAVAILABLE:", 1)[1].splitlines()[0])
        if error_code in {1, 5, 87, 120, 1314}:
            pytest.skip(result.stdout.strip())
        pytest.fail(f"unexpected symlink creation failure: {result.stdout.strip()}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REPARSE_OK" in result.stdout
