using Microsoft.Win32;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;

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
    public FileInfo AgentGatewayConfigPath { get; }
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
        AgentGatewayConfigPath = new FileInfo(Path.Combine(ConfigDir.FullName, "agent_gateway.json"));
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

        EnsureAgentGatewayConfig();
    }

    public JsonObject EnsureAgentGatewayConfig()
    {
        JsonObject config = AgentGatewayConfigPath.Exists
            ? JsonNode.Parse(File.ReadAllText(AgentGatewayConfigPath.FullName))?.AsObject() ?? new JsonObject()
            : new JsonObject();

        bool changed = false;
        changed |= EnsureJsonValue(config, "enabled", false);
        changed |= EnsureJsonValue(config, "require_token", true);
        changed |= EnsureJsonValue(config, "allow_write_requests", true);
        changed |= EnsureJsonValue(config, "allow_roslyn_advanced", false);
        changed |= EnsureJsonValue(config, "approval_timeout_seconds", 600);
        if (!config.TryGetPropertyValue("token", out JsonNode? tokenNode) || string.IsNullOrWhiteSpace(tokenNode?.GetValue<string>()))
        {
            config["token"] = GenerateToken();
            changed = true;
        }
        if (!config.TryGetPropertyValue("approval_token", out JsonNode? approvalTokenNode) || string.IsNullOrWhiteSpace(approvalTokenNode?.GetValue<string>()))
        {
            config["approval_token"] = GenerateToken();
            changed = true;
        }

        if (changed || !AgentGatewayConfigPath.Exists)
        {
            WriteAgentGatewayConfig(config);
        }

        return config;
    }

    public void SetAgentGatewayEnabled(bool enabled)
    {
        JsonObject config = EnsureAgentGatewayConfig();
        config["enabled"] = enabled;
        WriteAgentGatewayConfig(config);
    }

    public string AgentGatewayToken()
    {
        JsonObject config = EnsureAgentGatewayConfig();
        return config["token"]?.GetValue<string>() ?? "";
    }

    public string AgentGatewayApprovalToken()
    {
        JsonObject config = EnsureAgentGatewayConfig();
        return config["approval_token"]?.GetValue<string>() ?? "";
    }

    public bool AgentGatewayEnabled()
    {
        JsonObject config = EnsureAgentGatewayConfig();
        return config["enabled"]?.GetValue<bool>() ?? false;
    }

    private void WriteAgentGatewayConfig(JsonObject config)
    {
        ConfigDir.Create();
        File.WriteAllText(
            AgentGatewayConfigPath.FullName,
            config.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    }

    private static bool EnsureJsonValue(JsonObject config, string key, bool value)
    {
        if (config.ContainsKey(key))
        {
            return false;
        }
        config[key] = value;
        return true;
    }

    private static bool EnsureJsonValue(JsonObject config, string key, int value)
    {
        if (config.ContainsKey(key))
        {
            return false;
        }
        config[key] = value;
        return true;
    }

    private static string GenerateToken()
    {
        byte[] bytes = RandomNumberGenerator.GetBytes(32);
        return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
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
    "execute_tool_name": "vrc_apply_blendshapes"
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
