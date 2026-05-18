using System.Text.Json;
using System.Text.Json.Nodes;

namespace VRCForge.Launcher;

internal sealed record ProjectInspection(
    bool IsValid,
    string Message,
    bool HasLegacyFolder,
    bool HasVrcForgePlugin,
    bool HasMcpPackageFolder,
    bool HasMcpManifestDependency,
    bool ManifestWritable);

internal sealed record InstallResult(bool Success, string Message, string Detail);

internal sealed class UnityProjectInstaller
{
    private const string McpPackageName = "com.coplaydev.unity-mcp";
    private const string McpPackageValue = "file:Packages/com.coplaydev.unity-mcp";

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
            FileInfo manifest = ManifestPath(project);
            Dictionary<string, string> dependencies = ReadDependencies(manifest);

            return new ProjectInspection(
                true,
                "Unity project root is valid.",
                Directory.Exists(Path.Combine(project.FullName, "Assets", "VRCAutoRig")),
                Directory.Exists(Path.Combine(project.FullName, "Assets", "VRCForge", "Editor")),
                Directory.Exists(Path.Combine(project.FullName, "Packages", McpPackageName)),
                dependencies.ContainsKey(McpPackageName),
                CanWrite(manifest.FullName));
        }
        catch (Exception ex)
        {
            return new ProjectInspection(false, ex.Message, false, false, false, false, false);
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

        if (!inspection.HasMcpPackageFolder && !inspection.HasMcpManifestDependency)
        {
            message = "Unity MCP package is not configured or available.";
            return false;
        }

        message = "Manual import detected successfully.";
        return true;
    }

    public InstallResult InstallOrUpdate(string projectPath)
    {
        string? vrcForgeBackup = null;
        string? mcpBackup = null;
        string? manifestBackup = null;
        string? targetVrcForge = null;
        string? targetMcp = null;

        try
        {
            DirectoryInfo project = ValidateProjectRoot(projectPath);
            DirectoryInfo backupRoot = new(Path.Combine(project.FullName, ".vrcforge", "backups"));
            backupRoot.Create();

            DirectoryInfo sourceAssets = new(Path.Combine(paths.UnityPluginDir.FullName, "Assets", "VRCForge"));
            DirectoryInfo sourceMcp = new(Path.Combine(paths.UnityPluginDir.FullName, "Packages", McpPackageName));
            if (!sourceAssets.Exists)
            {
                throw new InvalidOperationException($"Release payload is missing {sourceAssets.FullName}");
            }
            if (!sourceMcp.Exists)
            {
                throw new InvalidOperationException($"Release payload is missing {sourceMcp.FullName}");
            }

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

            targetMcp = Path.Combine(project.FullName, "Packages", McpPackageName);
            if (Directory.Exists(targetMcp))
            {
                mcpBackup = NewBackupPath(backupRoot, McpPackageName);
                Directory.Move(targetMcp, mcpBackup);
            }

            CopyDirectory(sourceMcp.FullName, targetMcp);

            FileInfo manifest = ManifestPath(project);
            manifestBackup = NewBackupPath(backupRoot, "manifest") + ".json";
            File.Copy(manifest.FullName, manifestBackup, true);

            try
            {
                WriteManifestDependency(manifest);
            }
            catch
            {
                File.Copy(manifestBackup, manifest.FullName, true);
                throw;
            }

            return new InstallResult(true, "Unity plugin installed.", $"Backups: {backupRoot.FullName}");
        }
        catch (Exception ex)
        {
            RestoreDirectory(vrcForgeBackup, targetVrcForge);
            RestoreDirectory(mcpBackup, targetMcp);
            return new InstallResult(false, "Automatic Unity plugin install failed.", ex.Message);
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

    private static FileInfo ManifestPath(DirectoryInfo project)
    {
        return new FileInfo(Path.Combine(project.FullName, "Packages", "manifest.json"));
    }

    private static Dictionary<string, string> ReadDependencies(FileInfo manifest)
    {
        JsonNode? root = JsonNode.Parse(File.ReadAllText(manifest.FullName));
        JsonObject? dependencies = root?["dependencies"]?.AsObject();
        return dependencies?.ToDictionary(item => item.Key, item => item.Value?.GetValue<string>() ?? "") ?? new();
    }

    private static void WriteManifestDependency(FileInfo manifest)
    {
        JsonNode? rootNode = JsonNode.Parse(File.ReadAllText(manifest.FullName));
        JsonObject root = rootNode?.AsObject() ?? new JsonObject();
        JsonObject dependencies = root["dependencies"] as JsonObject ?? new JsonObject();
        dependencies[McpPackageName] = McpPackageValue;
        root["dependencies"] = dependencies;

        JsonSerializerOptions options = new() { WriteIndented = true };
        File.WriteAllText(manifest.FullName, root.ToJsonString(options));
        JsonNode.Parse(File.ReadAllText(manifest.FullName));
    }

    private static bool CanWrite(string path)
    {
        try
        {
            using FileStream stream = File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.Read);
            return stream.CanWrite;
        }
        catch
        {
            return false;
        }
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
