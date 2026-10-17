# 从只下载了 exe 开始

用于用户明确表示“刚下载，Unity、工程、SDK、模型都还没有”的情况。目标是教用户正确准备 Avatar 工程，再连接 VRCForge；不是让普通用户拉 VRCForge 源码、装 Git/Python/Rust 或编译应用。不要开始反复扫描不存在的工程。

## 权威来源

- VRCForge 的安装器、配套 Core 与使用方式：查看[当前 Release](https://github.com/ayyitong888/VRCForge/releases)及对应版本的 [README](https://github.com/ayyitong888/VRCForge/blob/main/README.md)。main 可能超前于安装版。
- VCC、Unity 安装：[VRChat Creator Companion 官方入门](https://vcc.docs.vrchat.com/guides/getting-started/)。
- ALCOM：使用[作者官网](https://vrc-get.anatawa12.com/en/alcom/)及其链接的 [vrc-get/vrc-get 仓库](https://github.com/vrc-get/vrc-get)。这是社区维护的 VCC 替代工具，不称为 VRChat 官方产品；无需为使用它卸载 VCC。
- 创建、添加和打开工程：[VRChat SDK 官方入门](https://creators.vrchat.com/sdk/)。
- 模型准备、导入与放入场景：[VRChat 首个 Avatar 官方指南](https://creators.vrchat.com/avatars/creating-your-first-avatar/)。

仅打开当前步骤需要的页面，确认实际按钮和下载项；不要仅凭搜索摘要操作。Unity 版本随官方支持变化，跟随 VCC 当前兼容版本，不让用户随意装 Unity Hub 推荐的最新编辑器。

## 所有用户：使用准确的 Unity 产品和版本，避开团结引擎

无论用户所在地区，本流程统一引导 Unity Hub 与 VRChat 支持的 Unity Editor，避开团结 Hub / Tuanjie Hub 或团结引擎 / Tuanjie Editor。不要只说“去 Unity 官网下载”而不核对实际产品，也不要把中文界面当作产品身份：Unity 的中文语言包与团结引擎不是一回事。

先读 [VRChat 当前支持版本](https://creators.vrchat.com/sdk/upgrade/current-unity-version/)，再使用 [Unity 全球下载入口](https://unity.com/download)及[全球编辑器归档](https://unity.com/releases/editor/archive)。如果页面重定向到团结产品，停止该下载路线；从 VRChat 支持版本页面进入对应 Unity 全球版本详情，检查官方 Manual installs → Windows 或 Download Assistant 链接，不从搜索广告、网盘或未知镜像找替代包。不要凭记忆拼 CDN 下载地址或版本 hash。

Hub 路线不可用时，可从该官方版本详情页手动安装编辑器，再在 Unity Hub / VCC 设置中定位实际 `Unity.exe`。Unity 的[官方安装说明](https://docs.unity.com/en-us/hub/add-editor)介绍归档版本与定位已有安装；VCC 入门说明也允许手动指定编辑器。

继续前检查安装程序的产品名、实际编辑器 About/version 和 VCC 选定的编辑器路径，精确对应当时 VRChat 官方支持版本；只看到“2022 LTS”或“安装成功”不够。发现 Tuanjie 标识或版本不符时先纠正选择，不让用户用它创建目标工程。不要为此自动卸载已有软件或转换已有工程。官方来源暂不可达时，说明卡在下载并给出已验证页面，不声称地区重定向或安装问题已经解决。

## 带用户走完准备流程

1. **先确认 exe 是什么。** 如果是 VRCForge 官方 Web/Offline Installer，运行安装器并保留完整安装目录；如果只是从完整程序目录单独拿出的 `VRCForge.exe`，回到相应 Release 获取完整安装器/程序包，不把单个可执行文件当作全部依赖。用户已经能打开 App 就直接继续。
2. **准备能协助的 Agent。** 内置 AI 需要在 App 中配置并测试所选模型；已有外部 AI 则按 App 连接器生成的配置连接，不要求额外配置 App 模型。没有可用 Agent 时，用明确的界面步骤引导，不声称尚未工作的 Agent 能自修。
3. **选择管理器，准备 Unity。** 已用 VCC 或 ALCOM 就沿用；全新用户简述两者并让其选择一个，不要求两个都装。VCC 来自 [VRChat 官方下载入口](https://vrchat.com/download/vcc)，ALCOM 来自上面的作者官网；不要把 ALCOM 的包名、便携 exe 和 VRCForge exe 混为一谈。按所选工具的实际界面准备 Unity Hub、兼容编辑器及必要的账户/许可证；所有用户均遵循上面的 Unity 路线及产品核验，避开团结引擎。登录和系统安装确认由用户完成。
4. **创建或添加 Avatar 工程。** VCC：Create New Project / New → Avatar → 名称和用户选择的保存目录 → Create Project。ALCOM：按实际版本的 Create New Project / 创建工程入口，选择 Avatar 模板、准确 Unity 版本、名称和保存位置再创建；核对界面，不照搬 VCC 按钮。已有完整工程则在所选管理器中添加现有工程，选择含 `Assets`、`Packages`、`ProjectSettings` 的根目录。不要把模型文件夹或 `.unitypackage` 当成工程，也不选 World/UdonSharp/URP/HDRP 模板。
5. **打开并等待就绪。** 用所选 VCC / ALCOM 的 Open Project / 打开 Unity 动作打开精确工程，等待首次导入和编译。区分正在处理与实际 Console 错误。此阶段不用“VRCForge 连不上”来判断工程创建失败，也不把打开文件夹说成启动 Unity。
6. **准备用户的模型。** 引导从已购买/获授权的模型下载中获取 Unity 资产包，按模型作者要求先装实际依赖，再导入包、打开作者场景或将正确 prefab 放入场景。不要让用户下载“所有常见插件”。尚未选模型时，可询问是否先用官方示例学习；须明确它是演示模型，不声称用户自己的 Avatar 已准备好。导入、保存或修改 Unity 资产仍需相应审批。
7. **接入 VRCForge。** 在 App 添加/选择这个精确工程，优先使用实际可用的监督 Core 安装流程；否则获取同版本 `VRCForge.unitypackage`，在正确 Unity 窗口完整导入。不要额外安装第三方 MCP Server。此时再按 repair guide 验证匹配工程/实例、Core、工具和编译状态。
8. **确认可用。** 实际读取场景内目标 Avatar，才报告“已连接，可开始改模”。空工程没有 Avatar 要继续完成模型准备；安装成功、端口开启或项目已登记都不能替代目标读取。上传另由用户决定，不纳入本次连接验收。

先简述整体顺序，再只给当前可操作的一步：去哪里下载、点哪个按钮、完成后应看到什么。已获授权且工具可做的步骤由 Agent 执行；需要账户、系统安装、选择付费模型或实际打开软件时，让用户完成并读回结果。不要一次要求用户理解全部 MCP/SDK/Core 术语。

## Agent 代下载安装包

当用户请求从零配置或明确同意代下载，Agent 可以代取 Unity Hub、所需 Unity 编辑器以及用户选定的 VCC / ALCOM 安装包。先从上面的官方/作者页面取得当前 Windows 架构对应的真实下载链接；检查重定向终点和产品身份，避免团结或非作者再分发包。不要凭记忆拼 CDN 地址。

先确认能力归属：VRCForge 内置 Agent 可走其普通主机 Shell；Codex 可用 Codex 自己的 Shell；其他外部 Agent 必须已有自己的二进制下载/主机执行能力。VRCForge 外部 MCP 不开放内部 host Shell，不能靠加载工具块或本 Skill 的 allowed-tools 把它开放出来。外部 Agent 自身也不能下载时，才给用户精确手动步骤。

优先使用已曝光、支持二进制落盘的下载工具。现有 `vrcforge_web_fetch` 只读取有上限的文本，不能把大型 exe 当网页读取。如果没有专用下载器，可通过已授权的普通主机 Shell 下载到用户选定或现有下载目录，例如使用有超时的 `Invoke-WebRequest -OutFile`；先发现实际 Shell 契约并保留原有权限检查。这是普通主机文件下载，不是绕过 Unity 写入路径。下载到本次独有的临时文件，完成后确认结果再给出最终文件；不覆盖旧安装包。若返回进程会话，保留并轮询该会话，不重复启动下载。

报告最终来源 URL、保存路径、大小与 SHA-256，结合官方公布的校验值或 Windows Authenticode 签名/产品信息核对身份；本地 hash 只绑定文件，不单独证明官方真实性。未完成、拿到 HTML 错误页、产品不符或校验失败时，不称为下载成功或继续运行。不要把下载和启动安装器串成一个命令；安装、UAC、登录和许可证按已有授权及用户交互继续，不能把“代下载”当作提权或静默安装授权。仅下载失败时才转为精确手动下载步骤，不预先把 Agent 能完成的下载全部交回用户。
