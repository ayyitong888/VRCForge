using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.WinForms;

namespace VRCForge.Launcher;

internal sealed class MainForm : Form
{
    private readonly LauncherPaths paths = new();
    private readonly UnityProjectInstaller installer;
    private readonly RuntimeDependencyManager runtimeDependencies;
    private readonly TableLayoutPanel rootLayout = new();
    private readonly TabControl wizard = new();
    private readonly ComboBox projectPicker = new();
    private readonly TextBox projectPathBox = new();
    private readonly TextBox statusBox = new();
    private readonly TextBox agentApprovalIdBox = new();
    private readonly WebView2 dashboardWebView = new();
    private BackendProcess? backend;
    private string selectedProjectPath = "";
    private string latestDiagnostics = "";

    private sealed record UnityProjectChoice(string Name, string Path, string Version, string Source)
    {
        public string DisplayName => $"{Name} ({Version})";
    }

    public MainForm()
    {
        installer = new UnityProjectInstaller(paths);
        runtimeDependencies = new RuntimeDependencyManager(paths);

        Text = "VRCForge x64 Launcher";
        Width = 1180;
        Height = 820;
        MinimumSize = new Size(960, 680);
        StartPosition = FormStartPosition.CenterScreen;

        wizard.Dock = DockStyle.Fill;
        wizard.Appearance = TabAppearance.FlatButtons;
        wizard.ItemSize = new Size(0, 1);
        wizard.SizeMode = TabSizeMode.Fixed;
        statusBox.Multiline = true;
        statusBox.ReadOnly = true;
        statusBox.ScrollBars = ScrollBars.Vertical;
        statusBox.Dock = DockStyle.Fill;
        statusBox.Font = new Font("Consolas", 9);

        rootLayout.Dock = DockStyle.Fill;
        rootLayout.RowCount = 2;
        rootLayout.ColumnCount = 1;
        rootLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 72));
        rootLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 28));
        rootLayout.Controls.Add(wizard, 0, 0);
        rootLayout.Controls.Add(statusBox, 0, 1);
        Controls.Add(rootLayout);
        wizard.SelectedIndexChanged += (_, _) => UpdateStatusPanelVisibility();

        BuildWelcomePage();
        BuildProjectPage();
        BuildCheckPage();
        BuildInstallPage();
        BuildFallbackPage();
        BuildBackendPage();
        BuildHealthPage();
        BuildAgentPage();
        BuildDashboardPage();

        FormClosing += (_, _) => backend?.Dispose();
    }

    private void BuildWelcomePage()
    {
        FlowLayoutPanel panel = Page("Welcome");
        panel.Controls.Add(Header("VRCForge Windows x64 安装向导"));
        panel.Controls.Add(TextBlock(
            $"版本: {paths.Version}\r\n" +
            $"程序目录: {paths.ProgramDir.FullName}\r\n" +
            $"用户数据目录: {paths.UserDataDir.FullName}\r\n" +
            $"日志目录: {paths.LogsDir.FullName}\r\n" +
            $"WebView2 Runtime: {(paths.HasWebView2Runtime() ? "已检测到" : "未检测到，请安装 Microsoft Edge WebView2 Runtime")}"));
        panel.Controls.Add(Button("下一步：选择 Unity 工程", () =>
        {
            paths.EnsureUserData();
            wizard.SelectedIndex = 1;
        }));
    }

    private void BuildProjectPage()
    {
        FlowLayoutPanel panel = Page("Project");
        panel.Controls.Add(Header("选择 Unity 工程目录"));
        panel.Controls.Add(TextBlock("请选择包含 Assets、Packages/manifest.json、ProjectSettings/ProjectVersion.txt 的 VRChat Avatar Unity 工程根目录。"));
        projectPicker.Width = 900;
        projectPicker.DropDownStyle = ComboBoxStyle.DropDownList;
        projectPicker.DisplayMember = nameof(UnityProjectChoice.DisplayName);
        projectPicker.ValueMember = nameof(UnityProjectChoice.Path);
        projectPicker.SelectedIndexChanged += (_, _) =>
        {
            if (projectPicker.SelectedItem is UnityProjectChoice choice)
            {
                projectPathBox.Text = choice.Path;
            }
        };
        panel.Controls.Add(projectPicker);
        projectPathBox.Width = 900;
        panel.Controls.Add(projectPathBox);
        panel.Controls.Add(Button("刷新工程列表", LoadProjectChoices));
        panel.Controls.Add(Button("浏览", () =>
        {
            using FolderBrowserDialog dialog = new() { Description = "选择 Unity project root" };
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                projectPathBox.Text = dialog.SelectedPath;
            }
        }));
        panel.Controls.Add(Button("检查工程", () =>
        {
            selectedProjectPath = projectPathBox.Text.Trim();
            ProjectInspection inspection = installer.Inspect(selectedProjectPath);
            statusBox.Text = FormatInspection(inspection);
            wizard.SelectedIndex = 2;
        }));
        LoadProjectChoices();
    }

    private void BuildCheckPage()
    {
        FlowLayoutPanel panel = Page("Check");
        panel.Controls.Add(Header("插件检查"));
        panel.Controls.Add(TextBlock("如果检测到旧 Assets/VRCAutoRig，默认迁移到项目根目录 .vrcforge/backups 后再安装新 Assets/VRCForge。"));
        panel.Controls.Add(Button("安装 Unity 插件", () => wizard.SelectedIndex = 3));
        panel.Controls.Add(Button("启动 Dashboard", () => wizard.SelectedIndex = 5));
        panel.Controls.Add(Button("卸载当前工程 Unity 插件", () =>
        {
            InstallResult result = installer.Uninstall(selectedProjectPath);
            statusBox.Text = $"{result.Message}\r\n{result.Detail}";
        }));
        panel.Controls.Add(Button("卸载 VRCForge 程序", OpenProgramUninstall));
        panel.Controls.Add(Button("重新选择工程", () => wizard.SelectedIndex = 1));
    }

    private void BuildInstallPage()
    {
        FlowLayoutPanel panel = Page("Install");
        panel.Controls.Add(Header("安装 Unity 插件"));
        panel.Controls.Add(TextBlock("安装会备份 Assets/VRCForge、Packages/manifest.json，并复制本地 CoplayDev Unity MCP package。manifest 写入失败会恢复备份并停止。"));
        panel.Controls.Add(Button("开始安装 Unity 插件", () =>
        {
            InstallResult result = installer.InstallOrUpdate(selectedProjectPath);
            statusBox.Text = $"{result.Message}\r\n{result.Detail}";
            wizard.SelectedIndex = result.Success ? 5 : 4;
        }));
    }

    private void BuildFallbackPage()
    {
        FlowLayoutPanel panel = Page("Fallback");
        panel.Controls.Add(Header("自动安装失败：手动导入 fallback"));
        panel.Controls.Add(TextBlock(
            "1. 打开 Unity 工程。\r\n" +
            "2. 双击或拖入 VRCForge.unitypackage。\r\n" +
            "3. 等待 Unity import / compile 完成。\r\n" +
            "4. 回到 Launcher 点击“重新检测安装”。"));
        panel.Controls.Add(Button("打开日志目录", () => OpenFolder(paths.LogsDir.FullName)));
        panel.Controls.Add(Button("打开 VRCForge.unitypackage 所在目录", () => OpenFolder(paths.UnityPackagePath.DirectoryName ?? paths.UnityPluginDir.FullName)));
        panel.Controls.Add(Button("重新检测安装", () =>
        {
            if (installer.RedetectManualInstall(selectedProjectPath, out string message))
            {
                statusBox.Text = message;
                wizard.SelectedIndex = 5;
            }
            else
            {
                statusBox.Text = message;
            }
        }));
    }

    private void BuildBackendPage()
    {
        FlowLayoutPanel panel = Page("Backend");
        panel.Controls.Add(Header("启动 Backend"));
        panel.Controls.Add(TextBlock("Backend 日志写入 logs/backend.log。Launcher 关闭时会终止 backend 进程。"));
        panel.Controls.Add(Button("启动 Dashboard", async () =>
        {
            RuntimeDependencyResult runtimeResult = new(
                false,
                "Unity MCP runtime check was not completed.",
                $"Runtime logs: {paths.RuntimeDependencyLogPath.FullName}");
            try
            {
                statusBox.Text = "正在检查 Unity MCP 运行时依赖...";
                try
                {
                    runtimeResult = await runtimeDependencies.EnsureUnityMcpRuntimeAsync(
                        new Progress<string>(message => statusBox.Text = message),
                        CancellationToken.None);
                }
                catch (Exception runtimeEx)
                {
                    runtimeResult = new RuntimeDependencyResult(
                        false,
                        "Unity MCP 运行时准备失败，Dashboard 仍会继续启动。",
                        $"{runtimeEx.Message}\r\nRuntime logs: {paths.RuntimeDependencyLogPath.FullName}");
                }
                statusBox.Text = $"{runtimeResult.Message}\r\n{runtimeResult.Detail}\r\n\r\n正在启动 VRCForge backend...";

                backend?.Dispose();
                backend = new BackendProcess(paths);
                backend.Start(selectedProjectPath);
                JsonDocument health = await backend.WaitForHealthAsync(
                    selectedProjectPath,
                    new Progress<string>(message => statusBox.Text = $"{runtimeResult.Message}\r\n{runtimeResult.Detail}\r\n\r\n{message}"),
                    CancellationToken.None);
                latestDiagnostics = JsonSerializer.Serialize(health.RootElement, new JsonSerializerOptions { WriteIndented = true });
                statusBox.Text = $"{runtimeResult.Message}\r\n{runtimeResult.Detail}\r\n\r\n{FormatHealth(health.RootElement)}";
                await OpenDashboardAsync();
            }
            catch (Exception ex)
            {
                await StartDashboardWithCmdFallbackAsync(runtimeResult, ex);
            }
        }));
    }

    private async Task StartDashboardWithCmdFallbackAsync(RuntimeDependencyResult runtimeResult, Exception primaryException)
    {
        statusBox.Text =
            $"Packaged backend startup failed, trying start_dashboard.cmd fallback...\r\n" +
            $"Primary error: {primaryException.Message}\r\n" +
            $"Backend logs: {paths.BackendLogPath.FullName}\r\n" +
            $"Runtime logs: {paths.RuntimeDependencyLogPath.FullName}";

        try
        {
            backend?.Dispose();
            backend = new BackendProcess(paths);
            backend.StartViaCmdFallback(selectedProjectPath);
            JsonDocument health = await backend.WaitForHealthAsync(
                selectedProjectPath,
                new Progress<string>(message => statusBox.Text =
                    $"{runtimeResult.Message}\r\n{runtimeResult.Detail}\r\n\r\n" +
                    $"Packaged backend failed, using start_dashboard.cmd fallback.\r\n" +
                    $"Primary error: {primaryException.Message}\r\n\r\n{message}"),
                CancellationToken.None);
            latestDiagnostics = JsonSerializer.Serialize(health.RootElement, new JsonSerializerOptions { WriteIndented = true });
            statusBox.Text =
                $"{runtimeResult.Message}\r\n{runtimeResult.Detail}\r\n\r\n" +
                $"Packaged backend failed, but start_dashboard.cmd fallback is running.\r\n" +
                $"Primary error: {primaryException.Message}\r\n\r\n" +
                $"{FormatHealth(health.RootElement)}";
            await OpenDashboardAsync();
        }
        catch (Exception fallbackException)
        {
            statusBox.Text =
                $"Dashboard startup failed.\r\n\r\n" +
                $"Packaged backend error: {primaryException.Message}\r\n\r\n" +
                $"start_dashboard.cmd fallback error: {fallbackException.Message}\r\n\r\n" +
                $"Backend logs: {paths.BackendLogPath.FullName}\r\n" +
                $"Runtime logs: {paths.RuntimeDependencyLogPath.FullName}\r\n" +
                $"Fallback command: {paths.StartDashboardCmdPath.FullName}";
            wizard.SelectedIndex = 6;
        }
    }

    private void BuildHealthPage()
    {
        FlowLayoutPanel panel = Page("Health");
        panel.Controls.Add(Header("状态诊断"));
        panel.Controls.Add(TextBlock("这里显示诊断信息。Dashboard 启动不再因为 Unity MCP、Provider 或工程诊断 warning/error 被阻塞。"));
        panel.Controls.Add(Button("复制诊断信息", () => Clipboard.SetText(latestDiagnostics)));
        panel.Controls.Add(Button("打开日志目录", () => OpenFolder(paths.LogsDir.FullName)));
        panel.Controls.Add(Button("打开外部 Agent 接入页", () => wizard.SelectedIndex = 7));
        panel.Controls.Add(Button("打开 Dashboard", async () => await OpenDashboardAsync()));
    }

    private void BuildAgentPage()
    {
        FlowLayoutPanel panel = Page("Agent");
        panel.Controls.Add(Header("外部 Agent 接入"));
        TextBox info = TextBlock(BuildAgentGatewayInfo());
        panel.Controls.Add(info);
        panel.Controls.Add(Button("启用 Agent Gateway", () =>
        {
            paths.SetAgentGatewayEnabled(true);
            info.Text = BuildAgentGatewayInfo();
            statusBox.Text = "Agent Gateway 已启用。外部 agent 需要使用本页 token 访问 MCP / REST。";
        }));
        panel.Controls.Add(Button("禁用 Agent Gateway", () =>
        {
            paths.SetAgentGatewayEnabled(false);
            info.Text = BuildAgentGatewayInfo();
            statusBox.Text = "Agent Gateway 已禁用。已保存 token，之后可重新启用。";
        }));
        panel.Controls.Add(Button("复制 Codex MCP 配置", () => Clipboard.SetText(BuildCodexMcpConfig())));
        panel.Controls.Add(Button("复制 Claude Code 命令", () => Clipboard.SetText(BuildClaudeMcpCommand())));
        panel.Controls.Add(Button("复制 OpenClaw MCP 配置", () => Clipboard.SetText(BuildOpenClawMcpConfig())));
        panel.Controls.Add(TextBlock("待审批写入：点击刷新后，把要处理的 approval id 填到下面，再批准或拒绝。"));
        agentApprovalIdBox.Width = 900;
        panel.Controls.Add(agentApprovalIdBox);
        panel.Controls.Add(Button("刷新待审批列表", async () => await RefreshAgentApprovalsAsync()));
        panel.Controls.Add(Button("批准当前 Approval", async () => await PostAgentApprovalAsync("approve")));
        panel.Controls.Add(Button("拒绝当前 Approval", async () => await PostAgentApprovalAsync("reject")));
        panel.Controls.Add(Button("打开日志目录", () => OpenFolder(paths.LogsDir.FullName)));
        panel.Controls.Add(Button("在内置窗口打开 Dashboard", async () => await OpenDashboardAsync()));
        panel.Controls.Add(Button("用外部浏览器打开 Dashboard", OpenExternalDashboard));
    }
    private void BuildDashboardPage()
    {
        TabPage page = new("Dashboard");
        dashboardWebView.Dock = DockStyle.Fill;
        page.Controls.Add(dashboardWebView);
        wizard.TabPages.Add(page);
    }

    private void UpdateStatusPanelVisibility()
    {
        bool dashboardPage = wizard.SelectedTab?.Text == "Dashboard";
        statusBox.Visible = !dashboardPage;
        rootLayout.RowStyles[0].Height = dashboardPage ? 100 : 72;
        rootLayout.RowStyles[1].Height = dashboardPage ? 0 : 28;
    }

    private async Task OpenDashboardAsync()
    {
        if (backend is null)
        {
            statusBox.Text = "Backend is not running yet.";
            return;
        }

        wizard.SelectedIndex = 8;
        string dashboardUrl = backend.DashboardUri.ToString();
        try
        {
            using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(5) };
            using HttpResponseMessage response = await client.GetAsync(backend.DashboardUri);
            statusBox.Text = $"Dashboard URL: {dashboardUrl}\r\nHTTP status: {(int)response.StatusCode} {response.ReasonPhrase}";
        }
        catch (Exception ex)
        {
            statusBox.Text = $"Dashboard URL: {dashboardUrl}\r\nHTTP status check failed: {ex.Message}\r\nLogs: {paths.BackendLogPath.FullName}";
        }

        if (!paths.HasWebView2Runtime())
        {
            OpenExternalDashboard();
            return;
        }

        try
        {
            await dashboardWebView.EnsureCoreWebView2Async();
            dashboardWebView.Source = backend.DashboardUri;
        }
        catch (Exception ex)
        {
            statusBox.Text += $"\r\nWebView2 failed, opening external browser instead: {ex.Message}";
            OpenExternalDashboard();
        }
    }

    private void OpenExternalDashboard()
    {
        if (backend is null)
        {
            return;
        }
        Process.Start(new ProcessStartInfo(backend.DashboardUri.ToString()) { UseShellExecute = true });
    }

    private void OpenProgramUninstall()
    {
        string uninstaller = Path.Combine(paths.ProgramDir.FullName, "Uninstall.exe");
        if (File.Exists(uninstaller))
        {
            Process.Start(new ProcessStartInfo(uninstaller) { UseShellExecute = true });
            return;
        }

        statusBox.Text = $"No NSIS uninstaller was found in this install mode.\r\nProgram directory: {paths.ProgramDir.FullName}\r\nUser data is kept in: {paths.UserDataDir.FullName}";
        OpenFolder(paths.ProgramDir.FullName);
    }

    private FlowLayoutPanel Page(string title)
    {
        TabPage page = new(title);
        FlowLayoutPanel panel = new()
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.TopDown,
            Padding = new Padding(28),
            AutoScroll = true,
            WrapContents = false,
        };
        page.Controls.Add(panel);
        wizard.TabPages.Add(page);
        return panel;
    }

    private static Label Header(string text)
    {
        return new Label
        {
            Text = text,
            AutoSize = true,
            Font = new Font("Microsoft YaHei UI", 18, FontStyle.Bold),
            Margin = new Padding(0, 0, 0, 18),
        };
    }

    private static TextBox TextBlock(string text)
    {
        return new TextBox
        {
            Text = text,
            Multiline = true,
            ReadOnly = true,
            BorderStyle = BorderStyle.None,
            Width = 980,
            Height = Math.Max(90, text.Split('\n').Length * 28),
            BackColor = SystemColors.Control,
            Font = new Font("Microsoft YaHei UI", 10),
        };
    }

    private string BuildAgentGatewayInfo()
    {
        paths.EnsureAgentGatewayConfig();
        string token = paths.AgentGatewayToken();
        return
            $"状态: {(paths.AgentGatewayEnabled() ? "已启用" : "默认关闭")}\r\n" +
            "MCP: http://127.0.0.1:8757/mcp\r\n" +
            "REST: http://127.0.0.1:8757/api/agent\r\n" +
            $"Token: {token}\r\n" +
            $"配置文件: {paths.AgentGatewayConfigPath.FullName}\r\n" +
            $"日志目录: {paths.LogsDir.FullName}\r\n\r\n" +
            "外部 agent 可以读取状态、日志、截图和生成方案；写入 Unity 前必须先创建 approval，并由用户确认后才能 apply。\r\n" +
            "Approval 确认使用 Launcher 内部 token，不会复制给外部 agent。\r\n" +
            "Roslyn Advanced Power Mode 仍需额外开启 VRCFORGE_ENABLE_ROSLYN，并通过 Unity 警告弹窗。";
    }

    private string BuildCodexMcpConfig()
    {
        string token = paths.AgentGatewayToken();
        return $$"""
{
  "mcpServers": {
    "vrcforge": {
      "url": "http://127.0.0.1:8757/mcp",
      "headers": {
        "Authorization": "Bearer {{token}}"
      }
    }
  }
}
""";
    }

    private string BuildClaudeMcpCommand()
    {
        string token = paths.AgentGatewayToken();
        return $"claude mcp add --transport http vrcforge http://127.0.0.1:8757/mcp --header \"Authorization: Bearer {token}\"";
    }

    private string BuildOpenClawMcpConfig()
    {
        string token = paths.AgentGatewayToken();
        return $$"""
{
  "servers": {
    "vrcforge": {
      "transport": "http",
      "url": "http://127.0.0.1:8757/mcp",
      "headers": {
        "Authorization": "Bearer {{token}}"
      }
    }
  }
}
""";
    }

    private async Task RefreshAgentApprovalsAsync()
    {
        try
        {
            using HttpClient client = AgentHttpClient();
            string payload = await client.GetStringAsync("http://127.0.0.1:8757/api/agent/approvals");
            statusBox.Text = payload;
        }
        catch (Exception ex)
        {
            statusBox.Text = $"刷新待审批列表失败: {ex.Message}\r\n请确认 Backend 已启动且 Agent Gateway 已启用。";
        }
    }

    private async Task PostAgentApprovalAsync(string action)
    {
        string approvalId = agentApprovalIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(approvalId))
        {
            statusBox.Text = "请先输入 approval id。";
            return;
        }

        try
        {
            using HttpClient client = AgentHttpClient();
            using HttpResponseMessage response = await client.PostAsync(
                $"http://127.0.0.1:8757/api/agent/approvals/{Uri.EscapeDataString(approvalId)}/{action}",
                new StringContent("{}", Encoding.UTF8, "application/json"));
            statusBox.Text = await response.Content.ReadAsStringAsync();
        }
        catch (Exception ex)
        {
            statusBox.Text = $"处理 approval 失败: {ex.Message}";
        }
    }

    private void LoadProjectChoices()
    {
        List<UnityProjectChoice> projects = DiscoverUnityProjects();
        projectPicker.DataSource = projects;
        if (projects.Count > 0)
        {
            projectPicker.SelectedIndex = 0;
            projectPathBox.Text = projects[0].Path;
            statusBox.Text = $"Found {projects.Count} Unity project(s) from VCC / Unity Hub.";
        }
        else
        {
            projectPathBox.Text = "";
            statusBox.Text = "No Unity projects found in VCC / Unity Hub. Use Browse to select a project manually.";
        }
    }

    private static List<UnityProjectChoice> DiscoverUnityProjects()
    {
        Dictionary<string, UnityProjectChoice> byPath = new(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string> nameIndex = new(StringComparer.OrdinalIgnoreCase);

        void Add(string path, string name, string version, string source)
        {
            path = NormalizeProjectPath(path);
            if (string.IsNullOrWhiteSpace(path) || !IsUnityProject(path))
            {
                return;
            }

            name = string.IsNullOrWhiteSpace(name) ? Path.GetFileName(path) : name.Trim();
            version = string.IsNullOrWhiteSpace(version) ? ReadUnityVersion(path) : version.Trim();
            string nameKey = name.ToLowerInvariant();
            if (nameIndex.TryGetValue(nameKey, out string? existingPath))
            {
                byPath[existingPath] = byPath[existingPath] with { Source = MergeSource(byPath[existingPath].Source, source) };
                return;
            }

            if (byPath.TryGetValue(path, out UnityProjectChoice? existing))
            {
                byPath[path] = existing with { Source = MergeSource(existing.Source, source) };
                nameIndex.TryAdd(nameKey, path);
                return;
            }

            byPath[path] = new UnityProjectChoice(name, path, version, source);
            nameIndex[nameKey] = path;
        }

        foreach (UnityProjectChoice project in ReadVccProjects())
        {
            Add(project.Path, project.Name, project.Version, project.Source);
        }
        foreach (UnityProjectChoice project in ReadUnityHubProjects())
        {
            Add(project.Path, project.Name, project.Version, project.Source);
        }

        return byPath.Values
            .OrderBy(project => project.Name, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private static IEnumerable<UnityProjectChoice> ReadVccProjects()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        string[] candidates =
        {
            Path.Combine(local, "VRChatCreatorCompanion", "settings.json"),
            Path.Combine(roaming, "VRChatCreatorCompanion", "settings.json"),
        };

        foreach (string candidate in candidates)
        {
            if (!File.Exists(candidate))
            {
                continue;
            }

            foreach (UnityProjectChoice project in ReadProjectJsonSafe(candidate, "vcc"))
            {
                yield return project;
            }
        }
    }

    private static IEnumerable<UnityProjectChoice> ReadUnityHubProjects()
    {
        string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] candidates =
        {
            Path.Combine(roaming, "UnityHub", "projects-v1.json"),
            Path.Combine(local, "UnityHub", "projects-v1.json"),
        };

        foreach (string candidate in candidates)
        {
            if (!File.Exists(candidate))
            {
                continue;
            }

            foreach (UnityProjectChoice project in ReadProjectJsonSafe(candidate, "unity-hub"))
            {
                yield return project;
            }
        }
    }

    private static IEnumerable<UnityProjectChoice> ReadProjectJson(string path, string source)
    {
        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
        JsonElement root = document.RootElement;
        if (root.TryGetProperty("data", out JsonElement data) && data.ValueKind == JsonValueKind.Object)
        {
            foreach (JsonProperty item in data.EnumerateObject())
            {
                JsonElement value = item.Value;
                string projectPath = GetJsonString(value, "path") ?? item.Name;
                string name = GetJsonString(value, "title") ?? GetJsonString(value, "name") ?? Path.GetFileName(projectPath);
                string version = GetJsonString(value, "version") ?? ReadUnityVersion(projectPath);
                yield return new UnityProjectChoice(name, projectPath, version, source);
            }
            yield break;
        }

        if (root.TryGetProperty("userProjects", out JsonElement userProjects))
        {
            foreach (UnityProjectChoice project in ReadProjectArray(userProjects, source))
            {
                yield return project;
            }
        }
        if (root.TryGetProperty("projects", out JsonElement projects))
        {
            foreach (UnityProjectChoice project in ReadProjectArray(projects, source))
            {
                yield return project;
            }
        }
    }

    private static IEnumerable<UnityProjectChoice> ReadProjectJsonSafe(string path, string source)
    {
        try
        {
            return ReadProjectJson(path, source).ToList();
        }
        catch
        {
            return Array.Empty<UnityProjectChoice>();
        }
    }

    private static IEnumerable<UnityProjectChoice> ReadProjectArray(JsonElement projects, string source)
    {
        IEnumerable<JsonElement> items = projects.ValueKind switch
        {
            JsonValueKind.Array => projects.EnumerateArray(),
            JsonValueKind.Object => projects.EnumerateObject().Select(property => property.Value),
            _ => Array.Empty<JsonElement>(),
        };

        foreach (JsonElement item in items)
        {
            string projectPath = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : GetJsonString(item, "path") ?? "";
            string name = item.ValueKind == JsonValueKind.Object ? GetJsonString(item, "name") ?? GetJsonString(item, "title") ?? Path.GetFileName(projectPath) : Path.GetFileName(projectPath);
            string version = item.ValueKind == JsonValueKind.Object ? GetJsonString(item, "version") ?? ReadUnityVersion(projectPath) : ReadUnityVersion(projectPath);
            yield return new UnityProjectChoice(name, projectPath, version, source);
        }
    }

    private static string? GetJsonString(JsonElement value, string propertyName)
    {
        return value.ValueKind == JsonValueKind.Object
            && value.TryGetProperty(propertyName, out JsonElement property)
            && property.ValueKind == JsonValueKind.String
                ? property.GetString()
                : null;
    }

    private static bool IsUnityProject(string path)
    {
        return Directory.Exists(Path.Combine(path, "Assets"))
            && File.Exists(Path.Combine(path, "Packages", "manifest.json"))
            && File.Exists(Path.Combine(path, "ProjectSettings", "ProjectVersion.txt"));
    }

    private static string NormalizeProjectPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return "";
        }
        try
        {
            return Path.GetFullPath(path.Trim());
        }
        catch
        {
            return path.Trim();
        }
    }

    private static string ReadUnityVersion(string projectPath)
    {
        string versionFile = Path.Combine(projectPath, "ProjectSettings", "ProjectVersion.txt");
        if (!File.Exists(versionFile))
        {
            return "Unknown";
        }
        string line = File.ReadLines(versionFile).FirstOrDefault(line => line.StartsWith("m_EditorVersion:", StringComparison.OrdinalIgnoreCase)) ?? "";
        return line.Split(':', 2).LastOrDefault()?.Trim() ?? "Unknown";
    }

    private static string MergeSource(string left, string right)
    {
        HashSet<string> parts = new((left + "+" + right).Split('+', StringSplitOptions.RemoveEmptyEntries), StringComparer.OrdinalIgnoreCase);
        return string.Join("+", parts.OrderBy(item => item, StringComparer.OrdinalIgnoreCase));
    }

    private HttpClient AgentHttpClient()
    {
        HttpClient client = new() { Timeout = TimeSpan.FromSeconds(10) };
        client.DefaultRequestHeaders.TryAddWithoutValidation("Authorization", $"Bearer {paths.AgentGatewayToken()}");
        client.DefaultRequestHeaders.TryAddWithoutValidation("X-VRCForge-Approval-Token", paths.AgentGatewayApprovalToken());
        return client;
    }

    private static Button Button(string text, Action action)
    {
        Button button = new()
        {
            Text = text,
            Width = 260,
            Height = 42,
            Margin = new Padding(0, 8, 0, 8),
        };
        button.Click += (_, _) => action();
        return button;
    }

    private static Button Button(string text, Func<Task> action)
    {
        Button button = new()
        {
            Text = text,
            Width = 300,
            Height = 42,
            Margin = new Padding(0, 8, 0, 8),
        };
        button.Click += async (_, _) =>
        {
            button.Enabled = false;
            try
            {
                await action();
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "VRCForge", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                button.Enabled = true;
            }
        };
        return button;
    }

    private static string FormatInspection(ProjectInspection inspection)
    {
        if (!inspection.IsValid)
        {
            return inspection.Message;
        }

        return new StringBuilder()
            .AppendLine(inspection.Message)
            .AppendLine($"旧 Assets/VRCAutoRig: {(inspection.HasLegacyFolder ? "检测到，需要迁移" : "未检测到")}")
            .AppendLine($"Assets/VRCForge/Editor: {(inspection.HasVrcForgePlugin ? "已存在" : "未安装")}")
            .AppendLine($"Packages/com.coplaydev.unity-mcp: {(inspection.HasMcpPackageFolder ? "已存在" : "未安装")}")
            .AppendLine($"manifest dependency: {(inspection.HasMcpManifestDependency ? "已配置" : "未配置")}")
            .AppendLine($"manifest writable: {(inspection.ManifestWritable ? "可写" : "不可写")}")
            .ToString();
    }

    private static string FormatHealth(JsonElement root)
    {
        StringBuilder builder = new();
        builder.AppendLine($"ok: {root.GetProperty("ok").GetBoolean()}");
        if (root.TryGetProperty("components", out JsonElement components))
        {
            foreach (JsonProperty property in components.EnumerateObject())
            {
                JsonElement component = property.Value;
                builder.AppendLine($"{property.Name}: {component.GetProperty("status").GetString()} - {component.GetProperty("message").GetString()}");
            }
        }
        return builder.ToString();
    }

    private static void OpenFolder(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", path) { UseShellExecute = true });
    }
}
