namespace VRCForge.Launcher;

internal sealed record RuntimeDependencyResult(bool Success, string Message, string Detail);

internal sealed class RuntimeDependencyManager
{
    public RuntimeDependencyManager(LauncherPaths paths)
    {
        _ = paths ?? throw new ArgumentNullException(nameof(paths));
    }

    public Task<RuntimeDependencyResult> EnsureUnityMcpRuntimeAsync(
        IProgress<string>? progress,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        progress?.Report("VRCForge MCP2 Core is bundled with the Unity package; no external runtime is required.");
        return Task.FromResult(new RuntimeDependencyResult(
            true,
            "VRCForge MCP2 Core is bundled.",
            "Import Assets/VRCForge and open the selected Unity project; Core starts automatically."));
    }
}
