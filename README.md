# VRCForge

[![Version](https://img.shields.io/badge/version-v0.3.1--alpha-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v0.3.1-alpha)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

Official repository: https://github.com/ayyitong888/VRCForge

VRCForge is a local AI workbench for VRChat avatar editing. It connects a desktop dashboard, a local backend, and Unity Editor tools so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接桌面 Dashboard、本地后端和 Unity Editor 工具，让用户可以在明确审查后应用或恢复 Avatar 改动。

> WIP / 开发中
>
> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

## Install / 安装

For normal Windows x64 users, download the latest release:

普通 Windows x64 用户请下载最新 Release：

https://github.com/ayyitong888/VRCForge/releases/tag/v0.3.1-alpha

Recommended:

推荐：

1. Download `VRCForge_Web_Installer_x64.exe`.
2. Run the installer.
3. Start `VRCForge.exe` from the desktop or Start Menu.
4. In the launcher wizard, select a real Unity VRChat Avatar project root.
5. Let the launcher install or update the Unity plugin, start the backend, and open the dashboard.

Offline install:

离线安装：

1. Download `VRCForge_Offline_Installer_x64.exe`.
2. Run it on a Windows x64 machine.
3. Use the same launcher wizard flow.

Portable/debug package:

便携 / 调试包：

- `VRCForge_Windows_x64_0.3.1-alpha.zip`
- `start_dashboard.cmd`, PowerShell scripts, and `quickstart/` remain available for development and troubleshooting.
- End users do not need to install Python, Git, or run `pip install` when using the installer.

安装器会把程序安装到 `%ProgramFiles%\VRCForge`，用户数据放在 `%LOCALAPPDATA%\VRCForge`。用户数据包括 `config/`、`logs/`、`artifacts/` 和 `backups/`，更新时不会覆盖。

## Unity Plugin Flow / Unity 插件流程

The launcher validates that the selected folder is a Unity project with `Assets/`, `Packages/manifest.json`, and `ProjectSettings/ProjectVersion.txt`.

Launcher 会确认所选目录是 Unity 工程，并包含 `Assets/`、`Packages/manifest.json` 和 `ProjectSettings/ProjectVersion.txt`。

It installs:

它会安装：

- VRCForge Unity tools to `Assets/VRCForge/Editor`
- the pinned Unity MCP package to `Packages/com.coplaydev.unity-mcp`
- a local manifest dependency: `"com.coplaydev.unity-mcp": "file:Packages/com.coplaydev.unity-mcp"`

Before changing Unity files, the launcher backs up old plugin folders and `Packages/manifest.json` under project-root `.vrcforge/backups/`.

在修改 Unity 文件前，Launcher 会把旧插件目录和 `Packages/manifest.json` 备份到项目根目录 `.vrcforge/backups/`。

If automatic install fails, the launcher shows the error and log path, then offers a fallback `VRCForge.unitypackage` manual import flow and a re-check button.

如果自动安装失败，Launcher 会显示错误原因和日志路径，并提供 `VRCForge.unitypackage` 手动导入 fallback 与重新检测按钮。

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
| Wardrobe FX authoring | 衣柜 FX 生成 | In development / 开发中 |
| MA / VRCFury integration reading | MA / VRCFury 集成读取 | Planned / 计划中 |

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

VRCForge includes a local Agent Gateway for Codex, Claude Code, OpenClaw, and other MCP-capable agents. It exposes `http://127.0.0.1:8757/mcp` plus REST diagnostics under `/api/agent/*`.

VRCForge 提供本地 Agent Gateway，可接入 Codex、Claude Code、OpenClaw 等支持 MCP 的外部 agent。它会暴露 `http://127.0.0.1:8757/mcp`，并提供 `/api/agent/*` REST 调试接口。

The gateway is disabled by default. Enable it from the Launcher external-agent page, copy the local token/config, then let the agent read logs, capture screenshots, inspect Unity state, generate plans, and request supervised writes. Actual writes still require user approval before `apply`; the approval token is kept inside the Launcher and is not included in copied agent configs.

Gateway 默认关闭。请在 Launcher 的“外部 Agent 接入”页启用并复制本地 token/config。外部 agent 可以读取日志、截图、Unity 状态并生成方案；真正写入 Unity 前仍必须等待用户 approval，approval token 只由 Launcher 内部使用，不会写进复制给外部 agent 的配置。

## Safety / 安全原则

VRCForge follows a supervised workflow for write operations:

VRCForge 对写入操作采用受监督流程：

```text
Scan -> Plan -> Preview -> Backup -> Apply -> Validate -> Restore
```

Core dashboard workflows use predefined Unity tools. Roslyn is preserved only as Advanced Power Mode: disabled by default, opt-in via `VRCFORGE_ENABLE_ROSLYN`, and guarded by `confirmAdvancedPowerMode=true` plus a Unity warning dialog.

核心 Dashboard 流程使用预定义 Unity 工具。Roslyn 只作为 Advanced Power Mode 保留：默认禁用，需要 `VRCFORGE_ENABLE_ROSLYN` 显式开启，并且执行前必须通过 `confirmAdvancedPowerMode=true` 和 Unity 警告弹窗。

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

Binary releases bundle a modified vendored copy of CoplayDev Unity MCP under the MIT License. The upstream MIT license is preserved in the package and copied into release `licenses/` output together with VRCForge distribution notes.

二进制 Release 会随包分发一个经过裁剪的 CoplayDev Unity MCP vendored copy，许可证为 MIT。上游 MIT LICENSE 会保留在包内，并与 VRCForge 分发说明一起复制到 release 的 `licenses/` 目录。

[LICENSE](LICENSE)
