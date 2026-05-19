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

        process = new Process { StartInfo = info, EnableRaisingEvents = true };
        process.Start();
        _ = Pump(process.StandardOutput, paths.BackendLogPath.FullName);
        _ = Pump(process.StandardError, paths.BackendLogPath.FullName);
    }

    public async Task<JsonDocument> WaitForHealthAsync(string unityProjectPath, CancellationToken cancellationToken)
    {
        using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(3) };
        Exception? lastError = null;
        for (int attempt = 0; attempt < 60; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                await client.PostAsJsonAsync(
                    new Uri($"http://127.0.0.1:{port}/api/state"),
                    new { settings_path = paths.SettingsPath.FullName, project_path = unityProjectPath },
                    cancellationToken);
                string payload = await client.GetStringAsync(HealthUri, cancellationToken);
                return JsonDocument.Parse(payload);
            }
            catch (Exception ex)
            {
                lastError = ex;
                await Task.Delay(1000, cancellationToken);
            }
        }

        throw new TimeoutException($"Backend health check did not become ready. Last error: {lastError?.Message}");
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
}
