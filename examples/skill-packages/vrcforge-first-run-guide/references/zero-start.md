# 从空白 Windows 电脑连到 Unity 工程

用户说“帮我装好并连上”时，在当前对话完成下载、安装和连接。用户只问用法就讲解，只要安装包就交付文件。用用户的语言说明当前正在装什么、哪里需要他们操作。

## 确认起点与目标版本

1. 已能聊天就沿用当前 Agent。若尚未安装 VRCForge，或只有一个脱离程序目录的 `VRCForge.exe`，先从[对应 Release](https://github.com/ayyitong888/VRCForge/releases)取得完整程序包，完成安装或完整解压后启动 App。外部 Agent 若还没接入，在 App 的连接器页按对应客户端配置 MCP，重新加载后验证能读取 VRCForge 工具。它使用自己的模型；内置 Agent 使用 App 里已配置并通过连接测试的模型。
2. 文件夹中有 `Assets`、`Packages`、`ProjectSettings` 时，读取 `ProjectSettings/ProjectVersion.txt` 的完整 `m_EditorVersion`，有 `m_EditorVersionWithRevision` 时一并保留。已有工程用这个精确版本。如果全球归档查不到该版本，或 SDK 支持版本与其不一致，说明差异，让用户决定是否另做迁移或升级。新 VRChat 工程从 [VRChat 当前支持版本](https://creators.vrchat.com/sdk/upgrade/current-unity-version/)或用户指定的官方测试渠道确定精确版本。
3. 检查已安装应用、Editor 的实际位置以及 VRCForge 的工程与模板列表。Hub 可能安装在自选目录或以 MSIX 注册，可用 `Get-AppxPackage UnityTechnologies.UnityHub` 检查后者。已有工程和合适 Editor 时直接连接，已有 VCC 或 ALCOM 时沿用用户选择。需要新建 VRChat 工程且两者都没有时，默认装 VCC；用户点名 ALCOM 就装 ALCOM。普通 Unity 工程可跳过 VRChat 管理器。

## 安装缺少的软件

**国际版 Unity Hub。** 通过 [Unity 全球安装说明](https://docs.unity.com/en-us/hub/install-hub-win-mac)进入对应架构的 Windows 下载项。有 WinGet 时可先执行 `winget show --id Unity.UnityHub --exact --source winget`，检查返回的名称、发布者及 Unity 全球发行地址。确认后可用 `winget install --id Unity.UnityHub --exact --source winget --version <刚核对的版本> --interactive`。网页被引到团结 Hub、Unity 中国版或 Tuanjie Editor 时，舍弃该下载路线，转用核实过的全球来源；两条路都拿不到国际版就说明阻塞。中文界面的国际版可以使用。已有团结产品可保留，单独定位国际版。

**Unity Editor。** 读取已安装的 Unity 官方 CLI 帮助。新版支持 `unity editors --installed` 和 `unity install <精确版本>`。较旧的国际版 Hub 3 提供 `& $hubExe -- --headless install --version <精确版本>`。归档版本如需 `--changeset`，使用项目记录或对应全球发行页给出的值。等待安装完成，再从已安装列表或实际 Editor 核对精确版本与路径。命令不可用时，从目标版本的官方归档选择 Hub 安装或 Windows 手动安装，用相同版本继续。只安装目标平台所需模块。[新版 CLI](https://docs.unity.com/en-us/unity-cli/reference/editor-project-commands) · [Hub 3 CLI](https://docs.unity.com/en-us/hub/use-hub-cli) · [全球 Editor 归档](https://unity.com/releases/editor/archive)

**VRChat 工程管理器。** VCC 使用官方 Windows 安装程序。ALCOM 从作者页面选对应架构的 Windows 安装包，若为 ZIP，解压后运行其中的安装程序；用户选择独立版时，保存并启动其可执行文件。安装后确认所选管理器能找到刚核对的 Editor。自定义安装目录按实际 `Unity.exe` 指定。[VCC 安装说明](https://vcc.docs.vrchat.com/guides/getting-started/) · [VCC 安装包](https://vrchat.com/download/vcc) · [ALCOM 官方下载](https://vrc-get.anatawa12.com/alcom/)

内置 Agent 通过普通主机 Shell 执行安装；无工程时使用实际存在且可写的主机工作目录。外部 Agent 用自己已有的主机工具。VRCForge 的外部 MCP 提供工程工具，`vrcforge_web_fetch` 只读取文本。下载二进制用主机下载器（例如 `Invoke-WebRequest -OutFile`），保存到本次独有的位置，避免覆盖用户的旧包。检查最终来源、文件类型和官方校验值或签名，报告保存位置、大小和 SHA-256。哈希只用于标识文件；下载结果是网页或身份不符时不能运行。

已授权安装时，用当前客户端的安装或进程工具启动已核实的安装包。下载与启动分步执行；发起成功只代表安装开始。长下载和安装返回进程句柄时跟随同一进程，结束后读回安装状态。碰到 Windows 授权、账户登录、许可证或当前客户端权限限制时，告诉用户具体的窗口和操作；完成后接着检查下一步。用外部客户端且它没有主机能力时，只交接当前缺少的安装动作，之后仍由 Agent 完成可用的工程步骤。

## 创建或打开工程，验证连接

- **已有工程**：从用户给的准确根目录登记、选中并打开。工具报告已在启动时，跟踪同一个 Editor。
- **需要新工程**：确认用户要建的类型、名称与位置；从 `vrcforge_project_lifecycle_status` 返回的模板里确认类型，再用 `vrcforge_create_project`。如果没有合适模板，用选定管理器的创建工程流程取得正确模板（VRChat Avatar 选 Avatars，World 选 Worlds），再登记并选中生成的根目录。工具无法操作管理器界面时，只交接这一处。保留已有目录和工程。[VRChat SDK 入门](https://creators.vrchat.com/sdk/)
- 如果内置 Agent 仍在临时对话里，登记成功后请用户从左栏打开刚登记的工程对话。给出实际工程名和接续语句，例如“连接这个工程并检查状态”。外部客户端使用已明确选定的工程路径。用 `vrcforge_open_unity_project` 打开该工程，等待首次包解析和编译。
- 需要 Core 时，在这个工程按现有审批流程执行 `vrcforge_install_unity_core`，然后读取安装和连接状态。若该版本没有安装工具，再在对应 Editor 导入同版 `VRCForge.unitypackage`。具体排障见同一 Skill 的 `references/repair-guide.md`。
- **交付**：核对目标工程路径、实际 Editor 版本与实例，确认这个工程的 Core 已响应、编译已结束，并且该任务需要的工具可用。告诉用户连上了哪个工程。若仍在编译或等待许可，给出实际进度。仅在用户还要求处理 Avatar 时，再读目标模型和作者依赖，按资产审批流程继续。[首个 Avatar 指南](https://creators.vrchat.com/avatars/creating-your-first-avatar/)
