using Microsoft.Win32;

namespace VRCForge.Launcher;

internal sealed class LauncherPaths
{
    public string Version { get; } = "0.3.1-alpha";
    public DirectoryInfo ProgramDir { get; }
    public DirectoryInfo UserDataDir { get; }
    public DirectoryInfo ConfigDir { get; }
    public DirectoryInfo LogsDir { get; }
    public DirectoryInfo ArtifactsDir { get; }
    public DirectoryInfo BackupsDir { get; }
    public DirectoryInfo DashboardDir { get; }
    public DirectoryInfo UnityPluginDir { get; }
    public FileInfo BackendExe { get; }
    public FileInfo SettingsPath { get; }
    public FileInfo BackendLogPath { get; }
    public FileInfo UnityPackagePath { get; }

    public LauncherPaths()
    {
        ProgramDir = new DirectoryInfo(AppContext.BaseDirectory);
        UserDataDir = new DirectoryInfo(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "VRCForge"));
        ConfigDir = new DirectoryInfo(Path.Combine(UserDataDir.FullName, "config"));
        LogsDir = new DirectoryInfo(Path.Combine(UserDataDir.FullName, "logs"));
        ArtifactsDir = new DirectoryInfo(Path.Combine(UserDataDir.FullName, "artifacts"));
        BackupsDir = new DirectoryInfo(Path.Combine(UserDataDir.FullName, "backups"));
        DashboardDir = new DirectoryInfo(Path.Combine(ProgramDir.FullName, "dashboard"));
        UnityPluginDir = new DirectoryInfo(Path.Combine(ProgramDir.FullName, "unity_plugin"));
        BackendExe = new FileInfo(Path.Combine(ProgramDir.FullName, "backend", "vrcforge_backend.exe"));
        SettingsPath = new FileInfo(Path.Combine(ConfigDir.FullName, "settings.json"));
        BackendLogPath = new FileInfo(Path.Combine(LogsDir.FullName, "backend.log"));
        UnityPackagePath = new FileInfo(Path.Combine(UnityPluginDir.FullName, "VRCForge.unitypackage"));
    }

    public void EnsureUserData()
    {
        foreach (DirectoryInfo directory in new[] { UserDataDir, ConfigDir, LogsDir, ArtifactsDir, BackupsDir })
        {
            directory.Create();
        }

        if (!SettingsPath.Exists)
        {
            File.WriteAllText(SettingsPath.FullName, DefaultSettingsJson);
        }
    }

    public bool HasWebView2Runtime()
    {
        return ReadWebViewVersion(Registry.LocalMachine) || ReadWebViewVersion(Registry.CurrentUser);
    }

    private static bool ReadWebViewVersion(RegistryKey root)
    {
        using RegistryKey? key = root.OpenSubKey(
            @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}");
        return !string.IsNullOrWhiteSpace(key?.GetValue("pv")?.ToString());
    }

    private const string DefaultSettingsJson = """
{
  "llm": {
    "provider": "gemini",
    "api_key_env": "GEMINI_API_KEY",
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "model": "gemini-2.5-flash"
  },
  "unity_mcp": {
    "command": [
      "unity-mcp"
    ],
    "host": "127.0.0.1",
    "port": 8080,
    "instance": "",
    "retries": 3,
    "retry_backoff_seconds": 2.0,
    "timeout_seconds": 30,
    "export_tool_name": "vrc_export_blendshapes",
    "execute_tool_name": "vrc_execute_roslyn"
  },
  "paths": {
    "blendshape_export": "Assets/VRCForge/blendshapes_export.json"
  },
  "planning": {
    "min_confidence": 0.65
  },
  "dashboard": {
    "project_roots": [],
    "unity_editor_path": "",
    "status_push_interval_seconds": 2.5
  }
}
""";
}
