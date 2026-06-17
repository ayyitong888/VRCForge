using System.Diagnostics;
using System.Net.Http.Json;
using System.Text.Json;

namespace VRCForge.Launcher;

internal sealed class BackendProcess : IDisposable
{
    private static readonly object LogLock = new();

    private readonly LauncherPaths paths;
    private readonly int port;
    private Process? process;

    public BackendProcess(LauncherPaths paths, int port = 8757)
    {
        this.paths = paths;
        this.port = port;
    }

    public Uri DashboardUri => new($"http://127.0.0.1:{port}/");
    public Uri HealthUri => new($"http://127.0.0.1:{port}/api/health");
    public Uri DashboardAliasUri => new($"http://127.0.0.1:{port}/dashboard/");

    public void Start(string unityProjectPath)
    {
        paths.EnsureUserData();
        if (!paths.BackendExe.Exists)
        {
            throw new FileNotFoundException("Backend executable is missing.", paths.BackendExe.FullName);
        }

        ProcessStartInfo info = new(paths.BackendExe.FullName)
        {
            WorkingDirectory = paths.ProgramDir.FullName,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        info.ArgumentList.Add("--host");
        info.ArgumentList.Add("127.0.0.1");
        info.ArgumentList.Add("--port");
        info.ArgumentList.Add(port.ToString());
        info.Environment["VRCFORGE_APP_DIR"] = paths.ProgramDir.FullName;
        info.Environment["VRCFORGE_USER_DATA_DIR"] = paths.UserDataDir.FullName;
        info.Environment["VRCFORGE_CONFIG_DIR"] = paths.ConfigDir.FullName;
        info.Environment["VRCFORGE_LOG_DIR"] = paths.LogsDir.FullName;
        info.Environment["VRCFORGE_ARTIFACTS_DIR"] = paths.ArtifactsDir.FullName;
        info.Environment["VRCFORGE_DASHBOARD_DIR"] = paths.DashboardDir.FullName;
        info.Environment["VRCFORGE_SETTINGS_PATH"] = paths.SettingsPath.FullName;
        info.Environment["VRCFORGE_APP_SESSION_TOKEN"] = paths.AppSessionToken();
        info.Environment["UV_PYTHON_INSTALL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "python");
        info.Environment["UV_TOOL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-tools");
        info.Environment["UV_CACHE_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-cache");
        info.Environment["PATH"] = string.Join(
            Path.PathSeparator,
            new[] { paths.UvDir.FullName, paths.BundledUvDir.FullName, info.Environment["PATH"] ?? "" });

        process = new Process { StartInfo = info, EnableRaisingEvents = true };
        process.Start();
        _ = Pump(process.StandardOutput, paths.BackendLogPath.FullName);
        _ = Pump(process.StandardError, paths.BackendLogPath.FullName);
    }

    public void StartViaCmdFallback(string unityProjectPath)
    {
        paths.EnsureUserData();
        if (!paths.StartDashboardCmdPath.Exists)
        {
            throw new FileNotFoundException("Dashboard fallback command is missing.", paths.StartDashboardCmdPath.FullName);
        }

        ProcessStartInfo info = new("cmd.exe")
        {
            WorkingDirectory = paths.ProgramDir.FullName,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        info.ArgumentList.Add("/d");
        info.ArgumentList.Add("/c");
        info.ArgumentList.Add(paths.StartDashboardCmdPath.FullName);
        AddPortableEnvironment(info, unityProjectPath);

        process = new Process { StartInfo = info, EnableRaisingEvents = true };
        process.Start();
        AppendLog($"Launcher started Dashboard fallback command: {paths.StartDashboardCmdPath.FullName}");
        _ = Pump(process.StandardOutput, paths.BackendLogPath.FullName);
        _ = Pump(process.StandardError, paths.BackendLogPath.FullName);
    }

    public async Task<JsonDocument> WaitForHealthAsync(string unityProjectPath, IProgress<string>? progress, CancellationToken cancellationToken)
    {
        using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(3) };
        Exception? lastError = null;
        for (int attempt = 0; attempt < 75; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (process is { HasExited: true })
            {
                throw new InvalidOperationException(
                    $"Backend exited before Dashboard became ready. Exit code: {process.ExitCode}\r\nLogs: {paths.BackendLogPath.FullName}\r\n\r\n{TailLog()}");
            }

            try
            {
                progress?.Report($"Waiting for Dashboard HTTP server... attempt {attempt + 1}/75");
                using HttpResponseMessage dashboardResponse = await client.GetAsync(DashboardUri, cancellationToken);
                if (!dashboardResponse.IsSuccessStatusCode)
                {
                    throw new HttpRequestException($"Dashboard returned HTTP {(int)dashboardResponse.StatusCode} {dashboardResponse.ReasonPhrase}");
                }

                try
                {
                    await client.PostAsJsonAsync(
                        new Uri($"http://127.0.0.1:{port}/api/state"),
                        new { settings_path = paths.SettingsPath.FullName, project_path = unityProjectPath },
                        cancellationToken);
                }
                catch (Exception stateEx)
                {
                    AppendLog($"Launcher warning: /api/state update failed but Dashboard is reachable: {stateEx.Message}");
                }

                try
                {
                    string payload = await client.GetStringAsync(HealthUri, cancellationToken);
                    return JsonDocument.Parse(payload);
                }
                catch (Exception healthEx)
                {
                    AppendLog($"Launcher warning: /api/health failed but Dashboard is reachable: {healthEx.Message}");
                    return JsonDocument.Parse(
                        $$"""
                        {
                          "ok": true,
                          "components": {
                            "dashboard": {
                              "status": "ok",
                              "message": "Dashboard HTTP page is reachable.",
                              "detail": "{{DashboardUri}}"
                            },
                            "healthApi": {
                              "status": "warning",
                              "message": "Health API did not respond during launcher startup.",
                              "detail": "{{EscapeJson(healthEx.Message)}}"
                            }
                          }
                        }
                        """);
                }
            }
            catch (Exception ex)
            {
                lastError = ex;
                await Task.Delay(1000, cancellationToken);
            }
        }

        throw new TimeoutException(
            $"Dashboard did not become ready. Last error: {lastError?.Message}\r\nLogs: {paths.BackendLogPath.FullName}\r\n\r\n{TailLog()}");
    }

    public void Dispose()
    {
        try
        {
            if (process is { HasExited: false })
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Closing the launcher should make a best effort to stop the backend.
        }
        finally
        {
            process?.Dispose();
        }
    }

    private static async Task Pump(StreamReader reader, string logPath)
    {
        while (!reader.EndOfStream)
        {
            string? line = await reader.ReadLineAsync();
            if (line is not null)
            {
                lock (LogLock)
                {
                    File.AppendAllText(logPath, line + Environment.NewLine);
                }
            }
        }
    }

    private void AddPortableEnvironment(ProcessStartInfo info, string unityProjectPath)
    {
        info.Environment["VRCFORGE_APP_DIR"] = paths.ProgramDir.FullName;
        info.Environment["VRCFORGE_USER_DATA_DIR"] = paths.UserDataDir.FullName;
        info.Environment["VRCFORGE_CONFIG_DIR"] = paths.ConfigDir.FullName;
        info.Environment["VRCFORGE_LOG_DIR"] = paths.LogsDir.FullName;
        info.Environment["VRCFORGE_ARTIFACTS_DIR"] = paths.ArtifactsDir.FullName;
        info.Environment["VRCFORGE_DASHBOARD_DIR"] = paths.DashboardDir.FullName;
        info.Environment["VRCFORGE_SETTINGS_PATH"] = paths.SettingsPath.FullName;
        info.Environment["VRCFORGE_APP_SESSION_TOKEN"] = paths.AppSessionToken();
        info.Environment["VRCFORGE_SELECTED_UNITY_PROJECT"] = unityProjectPath;
        info.Environment["UV_PYTHON_INSTALL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "python");
        info.Environment["UV_TOOL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-tools");
        info.Environment["UV_CACHE_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-cache");
        info.Environment["PATH"] = string.Join(
            Path.PathSeparator,
            new[] { paths.UvDir.FullName, paths.BundledUvDir.FullName, info.Environment["PATH"] ?? "" });
    }

    private string TailLog(int maxLines = 80)
    {
        try
        {
            if (!paths.BackendLogPath.Exists)
            {
                return "backend.log does not exist yet.";
            }

            string[] lines = File.ReadAllLines(paths.BackendLogPath.FullName);
            return string.Join(Environment.NewLine, lines.TakeLast(maxLines));
        }
        catch (Exception ex)
        {
            return $"Could not read backend log: {ex.Message}";
        }
    }

    private void AppendLog(string line)
    {
        paths.LogsDir.Create();
        lock (LogLock)
        {
            File.AppendAllText(paths.BackendLogPath.FullName, line + Environment.NewLine);
        }
    }

    private static string EscapeJson(string value)
    {
        return value
            .Replace("\\", "\\\\", StringComparison.Ordinal)
            .Replace("\"", "\\\"", StringComparison.Ordinal)
            .Replace("\r", "\\r", StringComparison.Ordinal)
            .Replace("\n", "\\n", StringComparison.Ordinal);
    }
}
