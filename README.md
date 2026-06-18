# VRCForge

[![Version](https://img.shields.io/badge/version-v0.5.0--beta-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v0.5.0-beta)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

Official repository: https://github.com/ayyitong888/VRCForge

VRCForge is a local AI workbench for VRChat avatar editing. It connects a Tauri desktop agent workspace, a local FastAPI runtime, and Unity Editor tools so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接 Tauri 桌面 Agent 工作区、本地 FastAPI 运行时和 Unity Editor 工具，让用户可以在明确审查后应用或恢复 Avatar 改动。

> WIP / 开发中
>
> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

## Install / 安装

For normal Windows x64 users, download the latest release:

普通 Windows x64 用户请下载最新 Release：

https://github.com/ayyitong888/VRCForge/releases/tag/v0.5.0-beta

Recommended:

推荐：

1. Download `VRCForge_Web_Installer_x64.exe`.
2. Run the installer.
3. Start `VRCForge.exe` from the desktop or Start Menu.
4. In the first-run setup, select a real Unity VRChat Avatar project root.
5. Let the desktop app install or update the Unity plugin, start the backend, and open the agent workspace.

Offline install:

离线安装：

1. Download `VRCForge_Offline_Installer_x64.exe`.
2. Run it on a Windows x64 machine.
3. Use the same launcher wizard flow.

Portable/debug package:

便携 / 调试包：

- `VRCForge_Windows_x64_0.5.0-beta.zip`
- `start_dashboard.cmd`, PowerShell scripts, and `quickstart/` remain available for development and troubleshooting.
- End users do not need to install Python, Git, uv, or run `pip install` when using the installer. `VRCForge.exe` checks the Unity MCP runtime at startup, uses bundled `uvx` when available, and otherwise downloads uv into `%LOCALAPPDATA%\VRCForge\tools`.

安装器会把程序安装到 `%ProgramFiles%\VRCForge`，用户数据放在 `%LOCALAPPDATA%\VRCForge`。用户数据包括 `config/`、`logs/`、`artifacts/` 和 `backups/`，更新时不会覆盖。

## Unity Plugin Flow / Unity 插件流程

The desktop app validates that the selected folder is a Unity project with `Assets/`, `Packages/manifest.json`, and `ProjectSettings/ProjectVersion.txt`.

桌面 app 会确认所选目录是 Unity 工程，并包含 `Assets/`、`Packages/manifest.json` 和 `ProjectSettings/ProjectVersion.txt`。

It installs:

它会安装：

- VRCForge Unity tools to `Assets/VRCForge/Editor`
- the pinned Unity MCP package to `Packages/com.coplaydev.unity-mcp`
- a local manifest dependency: `"com.coplaydev.unity-mcp": "file:Packages/com.coplaydev.unity-mcp"`

Before changing Unity files, VRCForge backs up old plugin folders and `Packages/manifest.json` under project-root `.vrcforge/backups/`.

在修改 Unity 文件前，VRCForge 会把旧插件目录和 `Packages/manifest.json` 备份到项目根目录 `.vrcforge/backups/`。

If automatic install fails, the desktop app shows the error and log path, then offers a fallback `VRCForge.unitypackage` manual import flow and a re-check button.

如果自动安装失败，桌面 app 会显示错误原因和日志路径，并提供 `VRCForge.unitypackage` 手动导入 fallback 与重新检测按钮。

Unity plugin install is idempotent per Unity project. After a successful install it writes `.vrcforge/install_state.json`; the same VRCForge version and payload checksum will not reinstall on the next launch. If the state is partial or failed, the desktop app stops and shows repair/uninstall options instead of blindly touching `Assets/` or `Packages/manifest.json` again.

The project picker merges manual folders, VCC user projects, Unity Hub recent projects, and active Unity MCP instances. Active MCP instances are shown first so a running project such as `milltina` can be selected even if it was not under the default scan root.

`VRCForge.exe` opens the Tauri desktop app directly and starts or reconnects to the local FastAPI runtime. The legacy WebView2 launcher and `start_dashboard.cmd` path remain debug/compatibility surfaces only.

The desktop app also includes uninstall actions:
- Unity-side uninstall moves `Assets/VRCForge` and `Packages/com.coplaydev.unity-mcp` to project-root `.vrcforge/backups/`, then removes the manifest dependency with rollback on failure.
- Program uninstall opens the NSIS uninstaller when installed from the x64 installer; user data under `%LOCALAPPDATA%\VRCForge` is preserved unless removed manually.

## Features / 功能状态

| Feature | 功能 | Status |
| --- | --- | --- |
| Avatar and facial Blendshape loading | Avatar 与脸部 Blendshape 读取 | Available / 可用 |
| Manual Blendshape editing and undo | 手动形态键调整与撤销 | Available / 可用 |
| Natural-language Blendshape planning | 自然语言生成形态键调整方案 | Available / 可用 |
| Reference-image assisted face editing | 参考图辅助捏脸 | Available / 可用 |
| AI face tuning history and presets | AI 捏脸历史与预设 | Available / 可用 |
| Locked Blendshapes for partial reroll | 锁定形态键后局部重算 | Available / 可用 |
| Shader / Material tuning MVP | Shader / 材质调参 MVP | Available / 可用 |
| Vision review with Unity screenshots | Unity 截图识图复核 | Available / 可用 |
| Phase 2 Unity editor tools | Phase 2 Unity 编辑器工具层 | Available / 可用 |
| Agent workspace (multi-chat UI) | Agent 工作台（多会话界面） | Available / 可用 |
| Three-tier permission model (approval / auto / Roslyn full-auto) | 三档权限（审批 / 自动 / Roslyn 全自动） | Available / 可用 |
| Chat persistence and history replay across restarts | 会话持久化与重启后历史回放 | Available / 可用 |
| `/compact` history compaction (LLM summary with local fallback) | `/compact` 历史压缩（模型摘要，失败回退本地摘要） | Available / 可用 |
| Slash-command skill invocation with autocomplete | 斜杠命令直接调用 skill（带补全菜单） | Available / 可用 |
| Steering queue and per-turn run visualization | 插队队列与每轮运行可视化（运行行/耗时） | Available / 可用 |
| Roslyn Advanced Power Mode (in-memory compile, zero-install CodeDom fallback) | Roslyn 高级模式（内存编译，免安装 CodeDom 兜底） | Available / 可用 |
| Unity compile-error reading (`vrc_get_compile_errors`) | Unity 编译错误读取（agent 自修闭环基础） | Available / 可用 |
| External Agent Gateway (MCP + REST, supervised writes) | 外部 Agent Gateway（MCP + REST，受监督写入） | Available / 可用 |
| Generic Unity CRUD tools (component, GameObject, asset/prefab) | 通用 Unity CRUD 工具（组件、GameObject、资产/Prefab） | Beta, local tests pass; Unity live validation pending |
| Generic avatar authoring primitives (parameters, menus, FX animator states) | Expression parameters / menu controls / animator states | Beta, local tests pass; Unity live validation pending |
| Modular Avatar and VRCFury read-only scans | Modular Avatar / VRCFury 只读扫描 | Available / 可用 |
| Outfit setup wrapper and VPM package status/install | Outfit 安装封装与 VPM 包状态/安装 | Available / 可用 |
| Avatar performance scan | Avatar 性能扫描 | Available / 可用 |
| Int-exclusive wardrobe scan/create/add-outfit tools | int wardrobe scan/create/add outfit | Beta, local tests pass; Unity live validation pending |
| Semantic add-outfit workflow | Prefab search -> instantiate -> Setup Outfit -> scan/create wardrobe if missing -> wardrobe binding | Beta, local tests pass; Unity live validation pending |
| Pre-write checkpoint timeline | Git checkpoint before gateway writes, plus checkpoint list/preview/restore UI | Development branch, local tests pass; Unity live validation pending |

## Vision Review / 识图复核

For accurate visual review, prefer Gesture Manager Play Mode screenshots. Unity Scene view can differ from VRChat because lighting, camera perspective, and shader execution are not the same.

为了让识图复核更接近 VRChat 游戏内效果，建议使用 Gesture Manager 的 Play Mode 截图。Unity Scene 视图会受到光照、相机焦距和 shader 运行状态差异影响。

Recommended workflow:

推荐流程：

1. Apply Blendshape or shader/material changes.
2. Enter Play Mode with Gesture Manager active.
3. Adjust Gesture Manager / Game View to the desired face angle.
4. Capture Before / After screenshots from the Vision Review panel.
5. Run Vision Review and treat the result as advisory.

When Unity is in Play Mode, VRCForge captures the current Game View. Outside Play Mode, the original Scene View capture path remains available with a reminder.

Unity 进入 Play Mode 时，VRCForge 会截取当前 Game View。未进入 Play Mode 时，原 Scene View 截图路径仍可用，但会显示提醒。

## Providers / 模型接入

Supported providers include Google AI Studio, OpenAI, Anthropic, Ollama, Google Vertex AI, DeepSeek, OpenRouter, and custom OpenAI-compatible endpoints. Image input depends on the selected provider and model.

支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 以及自定义 OpenAI-compatible endpoint。图片输入能力取决于所选 provider 和模型。

## External Agent Gateway / 外部 Agent 接入

VRCForge includes a local Agent Gateway for MCP-capable external agents. It exposes `http://127.0.0.1:8757/mcp` plus REST diagnostics under `/api/agent/*`.

VRCForge 提供本地 Agent Gateway，可接入支持 MCP 的外部 agent。它会暴露 `http://127.0.0.1:8757/mcp`，并提供 `/api/agent/*` REST 调试接口。

The gateway is disabled by default. Enable it from the desktop settings, copy the local token/config, then let the agent read logs, capture screenshots, inspect Unity state, generate plans, and request supervised writes. Actual writes still require user approval before `apply`; the approval token is kept inside VRCForge and is not included in copied agent configs.

Gateway 默认关闭。请在桌面设置中启用并复制本地 token/config。外部 agent 可以读取日志、截图、Unity 状态并生成方案；真正写入 Unity 前仍必须等待用户 approval，approval token 只由 VRCForge 内部使用，不会写进复制给外部 agent 的配置。

## Safety / 安全原则

VRCForge follows a supervised workflow for write operations:

VRCForge 对写入操作采用受监督流程：

```text
Scan -> Plan -> Preview -> Backup -> Apply -> Validate -> Restore
```

Core app workflows use predefined Unity tools. Roslyn is preserved only as Advanced Power Mode, guarded by `confirmAdvancedPowerMode=true` plus a Unity warning dialog. Snippets are compiled fully in memory: the primary backend is Roslyn (only 4 DLLs, installed by `tools/install-roslyn-support.ps1`), with a zero-install CodeDom fallback when those DLLs are absent. Compile errors are returned with user-relative line numbers, and the read-only tool `vrc_get_compile_errors` reports project compile errors from the last Unity compilation pass.

Development-branch gateway writes create a pre-write checkpoint when the selected Unity project is a git worktree. The desktop Checkpoints view can list, preview, and request restore for those checkpoints through the same approval path as other writes. If the project is not a git worktree, checkpoint creation is recorded as unavailable instead of pretending rollback is possible.

核心 app 流程使用预定义 Unity 工具。Roslyn 只作为 Advanced Power Mode 保留，执行前必须通过 `confirmAdvancedPowerMode=true` 和 Unity 警告弹窗。Snippet 在内存中完整编译：主后端为 Roslyn（仅 4 个 DLL，由 `tools/install-roslyn-support.ps1` 安装），未装 DLL 时自动回退到免安装的 CodeDom。编译错误带用户视角行号返回；只读工具 `vrc_get_compile_errors` 可读取最近一次 Unity 编译的错误列表。

## Developer / Debug Start

Source checkout users can still run the debug path:

源码调试用户仍可使用脚本路径：

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

Or use:

也可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File quickstart\setup-and-run.ps1
```

This path is for development and troubleshooting. The installer is the preferred path for normal users.

此路径主要用于开发和排障。普通用户优先使用安装器。

## Documentation / 文档

- [USER_MANUAL.md](USER_MANUAL.md)
- [DEPENDENCIES.md](DEPENDENCIES.md)
- [NOTICE](NOTICE)
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
- [docs/FACE_TUNING_ACCEPTANCE_TEST.md](docs/FACE_TUNING_ACCEPTANCE_TEST.md)
- [SHADER_TUNING_PLAN.md](SHADER_TUNING_PLAN.md)
- [docs/SHADER_TUNING_CHECKPOINTS.md](docs/SHADER_TUNING_CHECKPOINTS.md)
- [packaging/README.md](packaging/README.md)

## License / 许可

VRCForge is distributed under GPL-3.0. If you obtained VRCForge from a third-party source, verify that the copyright notice, GPL-3.0 license, and source code access are preserved.

VRCForge 使用 GPL-3.0 发布。如果你从第三方渠道获得 VRCForge，请确认其保留版权声明、GPL-3.0 许可和源代码获取方式。

Binary releases bundle a modified vendored copy of CoplayDev Unity MCP under the MIT License. The upstream MIT license is preserved in the package and copied into release `licenses/` output together with VRCForge distribution notes. Windows x64 releases may also bundle the official uv runtime under `MIT OR Apache-2.0`; its license files and VRCForge distribution notes are copied into release `licenses/`.

二进制 Release 会随包分发一个经过裁剪的 CoplayDev Unity MCP vendored copy，许可证为 MIT。上游 MIT LICENSE 会保留在包内，并与 VRCForge 分发说明一起复制到 release 的 `licenses/` 目录。

[LICENSE](LICENSE)
