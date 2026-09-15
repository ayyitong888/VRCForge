<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![稳定版](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=稳定版&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![当前版本](https://img.shields.io/badge/当前版本-v1.8.0-d9487c?style=flat-square)
[![许可证 GPL-3.0-only](https://img.shields.io/badge/许可证-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![平台 Windows x64](https://img.shields.io/badge/平台-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

**简体中文** · [English](README.en.md) · [日本語](README.ja.md)

🌙 **VRCForge 创作工房** · 把灵感带进 Unity，把每一步改模留在掌控之中 ✨

**[🌸 粉色创作工房官网](https://ayyitong888.github.io/VRCForge/)**

</div>

# VRCForge：用 AI Agent + MCP 辅助 VRChat Avatar 改模

VRCForge 是给 VRChat Avatar 创作者使用的开源改模工具。你可以用自然语言说出想改的
脸型、衣服、材质或动画，让助手查看工程、提出修改并实际应用。
**AI-assisted VRChat avatar editor · Unity MCP tools · アバター改変支援**

从检查模型到换装、调材质、编辑动画，以及头部和部件移植，VRCForge 会显示要改的对象，
帮助你在应用后检查结果。你可以选择每次修改前确认，也可以让符合条件的修改自动进行；
恢复始终需要再次确认。不同 Avatar、插件和 Unity 工程的支持情况各不相同，
请以自己的工程结果为准。

> 使用任何会写入 Unity 资产的功能前，请先备份 Unity / VRChat Avatar 工程。

本页对应已发布的 [v1.8.0 稳定版](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)。
安装与升级请获取同一 Release 的配套安装器与 Unity 包。

**[下载 VRCForge v1.8.0](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)** · [查看 Release Notes](https://github.com/ayyitong888/VRCForge/releases)

## VRCForge 能做什么

从调整细节到搭配整套造型，把重复的操作交给 AI，留更多时间给创作。

| 你想做什么 | VRCForge 怎么帮你 |
| --- | --- |
| 调整外形与表情 | 调整模型已有的脸型、身材和表情选项，一边看效果，一边改到喜欢。 |
| 搭配衣服与配饰 | 给模型穿上衣服、加上配饰，整理好游戏里的换装菜单。 |
| 制作换装动画 | 让衣服随着动画切换，为换装加入渐变、溶解等演出效果。 |
| 调整颜色与质感 | 修改衣服、头发和配饰的颜色、贴图与材质，让整套搭配更协调。 |
| 组合模型与部件 | 跟着换头和部件移植工作流，把喜欢的模型部件组合起来，并检查连接效果。 |
| 减轻模型负担 | 查看哪些贴图或部件占用较多资源，调整贴图大小和压缩，配合已安装的插件继续优化。 |
| 选择顺手的 AI | 填入 API Key 使用内置 AI，或通过 MCP 连接你常用的 AI 助手。你可以选择逐步确认，也可以允许它自动操作。 |
| 保存常用做法 | 把常用操作保存成技能，下次继续用；也可以导入、导出 .vsk 技能包。 |
| 查看效果，恢复修改 | 查看修改后的画面和动画，保存阶段性备份；需要恢复时，选好备份并确认。 |

能调整的内容取决于模型本身。比如，捏脸需要模型带有脸型调节选项，部分换装和优化功能还需要配套插件。

## 按改模任务选择入口

| 你想做什么 | 对应流程 |
| --- | --- |
| 捏脸、调整表情（BlendShape editing） | 先扫描形态键，再预览小范围脸部调整并复核效果。 |
| 换装、整理衣柜（Outfits / avatar wardrobe） | 绑定衣物，编辑衣柜、菜单和参数，再制作和检查换装动画。 |
| 调整材质与着色器（Shader / material editing） | 读取 Shader 属性，修改材质、贴图与材质槽，再检查效果。 |
| 优化模型（Avatar optimization） | 根据诊断调整贴图导入设置，或配置已安装的优化插件。 |
| 用 AI 操作 Unity（Unity MCP / AI agent） | [连接 MCP 客户端](#连接-codexclaude-code-或其他-mcp-agent)，复用同一套审批与验证流程。 |

> 日本語：VRCForge は VRChat アバター改変を支援するオープンソースの Unity ツールです。
> 表情・BlendShape 調整、衣装・着せ替え、マテリアル確認、最適化診断を AI Agent と MCP で支援します。
> 導入手順は [日本語 README](README.ja.md) を参照してください。

## 工作方式

VRCForge 对 Unity 资产写入采用以下受监督流程：

```text
读取 → 规划 → 按权限确认 → 执行修改 → 读回与效果检查 → 按需恢复
```

- 默认在本机保存项目索引、聊天、记忆、检查点和连接配置。
- 支持逐项确认、自动和完全权限模式；自动模式保留高风险操作的确认，恢复检查点始终单独确认。
- 写入目标绑定到具体项目和 Unity Editor 实例，完成后通过读回或验证结果确认状态。
- 恢复是独立操作，需要再次确认；检查点不能代替工程备份。
- 外部模型服务会接收哪些内容，取决于你选择的 Provider、模型和具体操作。

## 快速开始

### 1. 安装并连接 Unity

从 [最新 Release](https://github.com/ayyitong888/VRCForge/releases/latest) 下载：

- 1.8.0 请使用 `VRCForge_Web_Installer_x64_Hotfix1.exe`，或离线安装器 `VRCForge_Offline_Installer_x64_Hotfix1.exe`（修复程序运行时的关闭提示与重试安装）
- `VRCForge.unitypackage`

然后完成三步连接：

1. 安装 VRCForge，但先不要启动 App。
2. 在目标 Unity 2022.3 LTS / VRChat SDK3 Avatar 工程中完整导入 `VRCForge.unitypackage`，等待编译结束并出现 `[VRCForge MCP] Core Ready`。
3. 启动 `VRCForge.exe`，选择该工程并连接。App 会发现工程内的 VRCForge Core，无需单独安装 MCP Server 或复制 MCP Token。

升级旧版本前请阅读对应 [Release Notes](https://github.com/ayyitong888/VRCForge/releases)。
`1.4.0` 是破坏性安装边界，不能直接覆盖 `1.3.6`。

### 2. 做第一次安全检查

1. 在 VRCForge 中选择项目和 Avatar。
2. 运行 Doctor，确认 App、Provider、Unity 和 MCP 连接状态。
3. 先运行只读扫描或 Validation Report。
4. 请求一个小范围修改，审查目标、方案和审批卡。
5. 应用后查看检查点、读回结果和验证差异；需要时单独申请恢复。

完整操作流程见 [用户手册](USER_MANUAL.md)。

## 连接 Codex、Claude Code 或其他 MCP Agent

VRCForge 同时支持内置 Agent 和外部 MCP 客户端。外部客户端通过本地 MCP + REST
网关访问同一套公开工具契约：规划阶段提供读取与规划能力，执行阶段可修改 Unity，遵循所选权限模式；需要人工确认的操作会显示审批请求。

在 VRCForge 中打开 **设置 → 连接器 → 通用 MCP 客户端**：

1. 找到客户端实际使用的 MCP 配置文件，并确认格式是 JSON、TOML 还是 YAML。
2. 本地桌面或 CLI 客户端优先选择 **STDIO**；只有客户端明确支持时才使用 **Streamable HTTP**。
3. 保持 VRCForge 运行，重启或重新连接客户端，确认出现名为 `vrcforge` 的 Server 和工具列表。

自动安装接受 JSON 配置文件的完整路径。TOML/YAML 客户端请复制配置块后手动加入。
HTTP 模式还需要启用 Agent Gateway，并把所需 Token 传给客户端进程；不要把明文凭据提交到仓库。

更多细节见 [External Agent Connectors](USER_MANUAL.md#external-agent-connectors)。

## 命令行工具

VRCForge Desktop 运行后，可以用本地 CLI 做诊断、检查点查询和 Validation Report：

```powershell
# 安装版
backend\vrcforge_backend.exe --cli doctor
backend\vrcforge_backend.exe --cli checkpoint list --project C:\Path\To\UnityProject

# 源码版
python tools\vrcforge_cli.py doctor
python tools\vrcforge_cli.py validation run --project C:\Path\To\UnityProject
```

`apply`、`rollback` 默认创建审批请求；添加 `--execute` 可在终端确认并执行，仍经过同一套后端审批与恢复流程。

## 适用范围与当前边界

- 目标平台是 Windows x64、Unity 2022.3 LTS 和 VRChat SDK3 Avatar 工程。
- VRCForge 可以在无 Provider 模式下完成部分只读检查；AI 对话、规划和视觉推理需要已配置的兼容 Provider。
- 优化插件集成需要已安装的兼容版本；配置 AAO 等组件不等于已经完成其构建阶段优化。换头与部件移植工作流不能代替必要的网格接缝、权重或 UV 编辑。
- Avatar 保护连接器提供检查、规划与预览；公开包不包含私有保护执行组件。
- `v1.8.0` 已发布；协议、源码测试或工具调用成功仍不能替代具体模型的视觉与恢复验收。
- Quest/Android、第三方资产许可和付费依赖由具体 Avatar 与资源决定。

## 文档入口

- [用户手册 / User Manual](USER_MANUAL.md)
- [v1.8.0 稳定版说明](docs/RELEASE_NOTES_1.8.0.md)
- [v1.7.10 稳定版说明](docs/RELEASE_NOTES_1.7.10.md)
- [兼容性矩阵](docs/COMPATIBILITY_MATRIX.md)
- [产品回归契约](docs/PRODUCT_REGRESSION_CONTRACT.md)
- [优化策略](docs/OPTIMIZATION_STRATEGY.md)
- [Unity Package 打包说明](packaging/README.md)
- [依赖与许可证](DEPENDENCIES.md) · [NOTICE](NOTICE) · [SECURITY](SECURITY.md)

## 源码开发

普通用户应优先使用 Release 安装器。源码调试可在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 隐私与许可证

VRCForge 采用 local-first 设计。API Key、Gateway Token、付费资产内容和私有文件默认不应进入
仓库或公开诊断材料；分享 Support Bundle 前仍应人工检查。

项目使用 [GPL-3.0-only](LICENSE) 许可证。VRCForge 的 Unity MCP Core、命令目录、
输入 Schema 元数据和工具结果契约为项目自有实现；发行门禁要求公开包不捆绑第三方 Unity MCP 运行时代码。
