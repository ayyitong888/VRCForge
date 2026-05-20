using System.Diagnostics;
using System.IO.Compression;
using System.Net.Http;
using System.Text;

namespace VRCForge.Launcher;

internal sealed record RuntimeDependencyResult(bool Success, string Message, string Detail);

internal sealed class RuntimeDependencyManager
{
    private const string UvDownloadUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip";
    private readonly LauncherPaths paths;

    public RuntimeDependencyManager(LauncherPaths paths)
    {
        this.paths = paths;
    }

    public async Task<RuntimeDependencyResult> EnsureUnityMcpRuntimeAsync(IProgress<string>? progress, CancellationToken cancellationToken)
    {
        paths.EnsureUserData();
        AppendLog("Runtime dependency check started.");

        string? unityMcp = FindExecutableOnPath("unity-mcp.exe");
        if (!string.IsNullOrWhiteSpace(unityMcp))
        {
            paths.SetUnityMcpCommand(unityMcp);
            return new RuntimeDependencyResult(true, "Unity MCP CLI found.", $"Using existing unity-mcp.exe: {unityMcp}");
        }

        string uvx = await EnsureUvxAsync(progress, cancellationToken);
        progress?.Report("Installing/checking mcpforunityserver through uvx...");
        int exitCode = await RunProcessAsync(
            uvx,
            "--from mcpforunityserver unity-mcp --help",
            TimeSpan.FromMinutes(4),
            cancellationToken);
        if (exitCode != 0)
        {
            return new RuntimeDependencyResult(
                false,
                "Unity MCP runtime install failed.",
                $"uvx exited with code {exitCode}. See log: {paths.RuntimeDependencyLogPath.FullName}");
        }

        paths.SetUnityMcpCommand(uvx, "--from", "mcpforunityserver", "unity-mcp");
        return new RuntimeDependencyResult(
            true,
            "Unity MCP runtime ready.",
            $"Configured backend to use uvx-managed mcpforunityserver.\r\nuvx: {uvx}\r\nlog: {paths.RuntimeDependencyLogPath.FullName}");
    }

    private async Task<string> EnsureUvxAsync(IProgress<string>? progress, CancellationToken cancellationToken)
    {
        if (paths.LocalUvxExe.Exists)
        {
            return paths.LocalUvxExe.FullName;
        }

        if (paths.BundledUvxExe.Exists)
        {
            AppendLog($"Using bundled uvx from release payload: {paths.BundledUvxExe.FullName}");
            return paths.BundledUvxExe.FullName;
        }

        string? uvxOnPath = FindExecutableOnPath("uvx.exe");
        if (!string.IsNullOrWhiteSpace(uvxOnPath))
        {
            return uvxOnPath;
        }

        progress?.Report("uv/uvx not found. Downloading uv for Windows x64...");
        paths.UvDir.Create();
        string zipPath = Path.Combine(paths.ToolsDir.FullName, "uv-x86_64-pc-windows-msvc.zip");
        using (HttpClient client = new() { Timeout = TimeSpan.FromMinutes(5) })
        {
            byte[] payload = await client.GetByteArrayAsync(UvDownloadUrl, cancellationToken);
            await File.WriteAllBytesAsync(zipPath, payload, cancellationToken);
        }

        progress?.Report("Extracting uv runtime...");
        string extractRoot = Path.Combine(paths.ToolsDir.FullName, "uv_extract");
        if (Directory.Exists(extractRoot))
        {
            Directory.Delete(extractRoot, recursive: true);
        }
        ZipFile.ExtractToDirectory(zipPath, extractRoot);

        string? extractedUvx = Directory.EnumerateFiles(extractRoot, "uvx.exe", SearchOption.AllDirectories).FirstOrDefault();
        string? extractedUv = Directory.EnumerateFiles(extractRoot, "uv.exe", SearchOption.AllDirectories).FirstOrDefault();
        if (string.IsNullOrWhiteSpace(extractedUvx) || string.IsNullOrWhiteSpace(extractedUv))
        {
            throw new FileNotFoundException("Downloaded uv archive did not contain uv.exe and uvx.exe.");
        }

        File.Copy(extractedUv, Path.Combine(paths.UvDir.FullName, "uv.exe"), overwrite: true);
        File.Copy(extractedUvx, paths.LocalUvxExe.FullName, overwrite: true);
        Directory.Delete(extractRoot, recursive: true);
        AppendLog($"Installed uvx to {paths.LocalUvxExe.FullName}");
        return paths.LocalUvxExe.FullName;
    }

    private async Task<int> RunProcessAsync(string fileName, string arguments, TimeSpan timeout, CancellationToken cancellationToken)
    {
        ProcessStartInfo info = new(fileName, arguments)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        info.Environment["UV_PYTHON_INSTALL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "python");
        info.Environment["UV_TOOL_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-tools");
        info.Environment["UV_CACHE_DIR"] = Path.Combine(paths.ToolsDir.FullName, "uv-cache");

        using Process process = new() { StartInfo = info, EnableRaisingEvents = true };
        process.Start();
        Task stdout = PumpAsync(process.StandardOutput, cancellationToken);
        Task stderr = PumpAsync(process.StandardError, cancellationToken);
        using CancellationTokenSource timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(timeout);

        try
        {
            await process.WaitForExitAsync(timeoutCts.Token);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch
            {
                // Best effort timeout cleanup.
            }
            AppendLog($"Process timed out: {fileName} {arguments}");
            return -1;
        }

        await Task.WhenAll(stdout, stderr);
        AppendLog($"Process exited {process.ExitCode}: {fileName} {arguments}");
        return process.ExitCode;
    }

    private async Task PumpAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        while (!reader.EndOfStream && !cancellationToken.IsCancellationRequested)
        {
            string? line = await reader.ReadLineAsync(cancellationToken);
            if (!string.IsNullOrWhiteSpace(line))
            {
                AppendLog(line);
            }
        }
    }

    private static string? FindExecutableOnPath(string executable)
    {
        string? path = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }

        foreach (string directory in path.Split(Path.PathSeparator))
        {
            if (string.IsNullOrWhiteSpace(directory))
            {
                continue;
            }

            string candidate = Path.Combine(directory.Trim(), executable);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private void AppendLog(string line)
    {
        paths.LogsDir.Create();
        File.AppendAllText(
            paths.RuntimeDependencyLogPath.FullName,
            $"[{DateTimeOffset.Now:O}] {line}{Environment.NewLine}",
            Encoding.UTF8);
    }
}
