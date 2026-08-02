using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;

namespace VRCForge.Launcher;

internal sealed record ProjectInspection(
    bool IsValid,
    string Message,
    bool HasLegacyFolder,
    bool HasVrcForgePlugin,
    string InstallState,
    bool IsSameVersionInstalled,
    string PayloadChecksum);

internal sealed record InstallResult(bool Success, string Message, string Detail);

internal sealed class UnityProjectInstaller
{
    private readonly LauncherPaths paths;

    public UnityProjectInstaller(LauncherPaths paths)
    {
        this.paths = paths;
    }

    public ProjectInspection Inspect(string projectPath)
    {
        try
        {
            DirectoryInfo project = ValidateProjectRoot(projectPath);
            string payloadChecksum = ComputePayloadChecksum(paths.UnityPluginDir);
            JsonObject state = ReadInstallState(project);
            bool sameVersion = string.Equals((string?)state["version"], paths.Version, StringComparison.OrdinalIgnoreCase)
                && string.Equals((string?)state["payloadChecksum"], payloadChecksum, StringComparison.OrdinalIgnoreCase)
                && string.Equals((string?)state["status"], "installed", StringComparison.OrdinalIgnoreCase);

            return new ProjectInspection(
                true,
                "Unity project root is valid.",
                Directory.Exists(Path.Combine(project.FullName, "Assets", "VRCAutoRig")),
                Directory.Exists(Path.Combine(project.FullName, "Assets", "VRCForge", "Editor")),
                (string?)state["status"] ?? "unknown",
                sameVersion,
                payloadChecksum);
        }
        catch (Exception ex)
        {
            return new ProjectInspection(false, ex.Message, false, false, "invalid", false, "");
        }
    }

    public bool RedetectManualInstall(string projectPath, out string message)
    {
        ProjectInspection inspection = Inspect(projectPath);
        if (!inspection.IsValid)
        {
            message = inspection.Message;
            return false;
        }

        if (!inspection.HasVrcForgePlugin)
        {
            message = "Assets/VRCForge/Editor was not found.";
            return false;
        }

        message = "Manual import detected successfully.";
        return true;
    }

    public InstallResult InstallOrUpdate(string projectPath)
    {
        string? vrcForgeBackup = null;
        string? targetVrcForge = null;

        try
        {
            DirectoryInfo project = ValidateProjectRoot(projectPath);
            ProjectInspection inspection = Inspect(projectPath);
            if (inspection.IsSameVersionInstalled
                && inspection.HasVrcForgePlugin
                && !inspection.HasLegacyFolder)
            {
                return new InstallResult(true, "Unity plugin already installed.", "Same VRCForge version and payload checksum detected; no files were changed.");
            }

            DirectoryInfo backupRoot = new(Path.Combine(project.FullName, ".vrcforge", "backups"));
            backupRoot.Create();

            DirectoryInfo sourceAssets = new(Path.Combine(paths.UnityPluginDir.FullName, "Assets", "VRCForge"));
            if (!sourceAssets.Exists)
            {
                throw new InvalidOperationException($"Release payload is missing {sourceAssets.FullName}");
            }
            WriteInstallState(project, "partial", "Install started.", inspection.PayloadChecksum);

            string legacyPath = Path.Combine(project.FullName, "Assets", "VRCAutoRig");
            if (Directory.Exists(legacyPath))
            {
                string legacyBackup = NewBackupPath(backupRoot, "VRCAutoRig");
                Directory.Move(legacyPath, legacyBackup);
                if (Directory.Exists(legacyPath))
                {
                    throw new InvalidOperationException("Legacy Assets/VRCAutoRig still exists after migration. Stop before installing the new plugin.");
                }
            }

            targetVrcForge = Path.Combine(project.FullName, "Assets", "VRCForge");
            if (Directory.Exists(targetVrcForge))
            {
                vrcForgeBackup = NewBackupPath(backupRoot, "VRCForge");
                Directory.Move(targetVrcForge, vrcForgeBackup);
            }

            CopyDirectory(sourceAssets.FullName, targetVrcForge);

            WriteInstallState(project, "installed", "Install completed.", inspection.PayloadChecksum);
            return new InstallResult(true, "Unity plugin installed.", $"Backups: {backupRoot.FullName}");
        }
        catch (Exception ex)
        {
            RestoreDirectory(vrcForgeBackup, targetVrcForge);
            try
            {
                DirectoryInfo project = ValidateProjectRoot(projectPath);
                WriteInstallState(project, "failed", ex.Message, ComputePayloadChecksum(paths.UnityPluginDir));
            }
            catch
            {
                // Best effort only; the original install error is more useful to the user.
            }
            return new InstallResult(false, "Automatic Unity plugin install failed.", ex.Message);
        }
    }

    public InstallResult Uninstall(string projectPath)
    {
        try
        {
            DirectoryInfo project = ValidateProjectRoot(projectPath);
            DirectoryInfo backupRoot = new(Path.Combine(project.FullName, ".vrcforge", "backups"));
            backupRoot.Create();

            string targetVrcForge = Path.Combine(project.FullName, "Assets", "VRCForge");
            if (Directory.Exists(targetVrcForge))
            {
                Directory.Move(targetVrcForge, NewBackupPath(backupRoot, "VRCForge_uninstalled"));
            }

            WriteInstallState(project, "uninstalled", "Unity plugin uninstalled by Launcher.", ComputePayloadChecksum(paths.UnityPluginDir));
            return new InstallResult(true, "Unity plugin uninstalled.", $"Backups: {backupRoot.FullName}");
        }
        catch (Exception ex)
        {
            return new InstallResult(false, "Unity plugin uninstall failed.", ex.Message);
        }
    }

    private static void RestoreDirectory(string? backup, string? target)
    {
        if (string.IsNullOrWhiteSpace(backup) || string.IsNullOrWhiteSpace(target) || !Directory.Exists(backup))
        {
            return;
        }

        if (Directory.Exists(target))
        {
            Directory.Delete(target, true);
        }

        Directory.Move(backup, target);
    }

    private static DirectoryInfo ValidateProjectRoot(string projectPath)
    {
        if (string.IsNullOrWhiteSpace(projectPath))
        {
            throw new InvalidOperationException("Unity project path is empty.");
        }

        DirectoryInfo project = new(projectPath);
        if (!project.Exists)
        {
            throw new InvalidOperationException($"Unity project path does not exist: {project.FullName}");
        }

        Dictionary<string, string> required = new()
        {
            ["Assets"] = Path.Combine(project.FullName, "Assets"),
            ["Packages/manifest.json"] = Path.Combine(project.FullName, "Packages", "manifest.json"),
            ["ProjectSettings/ProjectVersion.txt"] = Path.Combine(project.FullName, "ProjectSettings", "ProjectVersion.txt"),
        };

        foreach ((string label, string path) in required)
        {
            if (!File.Exists(path) && !Directory.Exists(path))
            {
                throw new InvalidOperationException($"Target Unity project is missing {label}: {path}");
            }
        }

        return project;
    }

    private static JsonObject ReadInstallState(DirectoryInfo project)
    {
        FileInfo statePath = InstallStatePath(project);
        if (!statePath.Exists)
        {
            return new JsonObject();
        }

        try
        {
            return JsonNode.Parse(File.ReadAllText(statePath.FullName)) as JsonObject ?? new JsonObject();
        }
        catch
        {
            return new JsonObject { ["status"] = "broken" };
        }
    }

    private void WriteInstallState(DirectoryInfo project, string status, string message, string payloadChecksum)
    {
        FileInfo statePath = InstallStatePath(project);
        statePath.Directory?.Create();
        JsonObject payload = new()
        {
            ["status"] = status,
            ["message"] = message,
            ["version"] = paths.Version,
            ["payloadChecksum"] = payloadChecksum,
            ["updatedAt"] = DateTimeOffset.UtcNow.ToString("O"),
        };
        File.WriteAllText(statePath.FullName, payload.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    }

    private static FileInfo InstallStatePath(DirectoryInfo project)
    {
        return new FileInfo(Path.Combine(project.FullName, ".vrcforge", "install_state.json"));
    }

    private static string ComputePayloadChecksum(DirectoryInfo payloadRoot)
    {
        if (!payloadRoot.Exists)
        {
            return "";
        }

        using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        foreach (string file in Directory.EnumerateFiles(payloadRoot.FullName, "*", SearchOption.AllDirectories).OrderBy(item => item, StringComparer.OrdinalIgnoreCase))
        {
            string relative = Path.GetRelativePath(payloadRoot.FullName, file).Replace('\\', '/');
            byte[] nameBytes = System.Text.Encoding.UTF8.GetBytes(relative);
            hash.AppendData(nameBytes);
            hash.AppendData(File.ReadAllBytes(file));
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static string NewBackupPath(DirectoryInfo backupRoot, string prefix)
    {
        string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
        string candidate = Path.Combine(backupRoot.FullName, $"{prefix}_{timestamp}");
        int suffix = 1;
        while (Directory.Exists(candidate) || File.Exists(candidate))
        {
            candidate = Path.Combine(backupRoot.FullName, $"{prefix}_{timestamp}_{suffix}");
            suffix++;
        }
        return candidate;
    }

    private static void CopyDirectory(string source, string destination)
    {
        if (Directory.Exists(destination))
        {
            Directory.Delete(destination, true);
        }
        Directory.CreateDirectory(destination);

        foreach (string directory in Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories))
        {
            Directory.CreateDirectory(directory.Replace(source, destination));
        }

        foreach (string file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            File.Copy(file, file.Replace(source, destination), true);
        }
    }
}
