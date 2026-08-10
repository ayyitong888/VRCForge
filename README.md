<div align="center">

![VRCForge — Local AI Workbench for VRChat Avatar Editing](docs/assets/social-preview.svg)

[![Target](https://img.shields.io/badge/target-v1.5.1-4f46e5?style=flat-square)](docs/RELEASE_NOTES_1.5.1.md)
[![License: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-22c55e?style=flat-square)](LICENSE)
![Platform: Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)
![Status: Work in progress](https://img.shields.io/badge/status-WIP-f59e0b?style=flat-square)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

</div>

# VRCForge

VRCForge is a local AI workbench for VRChat avatar editing. It connects a
Tauri desktop agent workspace, a local FastAPI runtime, and Unity Editor tools
so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接 Tauri 桌面 Agent
工作区、本地 FastAPI 运行时和 Unity Editor 工具，让用户可以在明确审查后应用或恢复
Avatar 改动。

> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

Current source / target release: `1.5.1`. Latest published stable release:
`1.5.0` (`v1.5.0`).

Official repository: <https://github.com/ayyitong888/VRCForge>

## Table of contents / 目录

- [Install / 安装](#install--安装)
- [Features / 功能概览](#features--功能概览)
- [Safety / 安全流程](#safety--安全流程)
- [Providers and agents / 模型与 Agent 接入](#providers-and-agents--模型与-agent-接入)
- [Preview / Screenshots](#preview--screenshots)
- [CLI / 命令行](#cli--命令行)
- [Unity Plugin / Unity 插件](#unity-plugin--unity-插件)
- [Privacy / 隐私](#privacy--隐私)
- [Documentation / 文档](#documentation--文档)
- [Developer / 源码调试](#developer--源码调试)
- [License / 许可](#license--许可)

---

## Install / 安装

Download the latest release / 下载最新 Release:
<https://github.com/ayyitong888/VRCForge/releases/latest>

1. Download `VRCForge.unitypackage` and run
   `VRCForge_Web_Installer_x64.exe` (or
   `VRCForge_Offline_Installer_x64.exe` for offline install).
2. Open the target Unity project, import all of `VRCForge.unitypackage`, and
   wait for compilation and `[VRCForge MCP] Core Ready` to finish.
3. Start `VRCForge.exe`, select that project, and connect. The App discovers
   the project-owned Core directly; no separate MCP install, MCP token copy,
   or manual Core configuration is required.

`1.4.0` is a breaking install boundary and does not support overwrite install
or Unity package import over `1.3.6`. Close VRCForge and Unity, remove the old
VRCForge App/runtime and old project integration, then install and import
`1.4.0` fresh. Do not delete `%LOCALAPPDATA%\VRCForge\agentic-app` or unrelated
Unity project content: configured API keys, user-owned `AGENTS.md`, chats,
memories, checkpoints, and unrelated assets must be preserved.

Program files: `%ProgramFiles%\VRCForge`. User data:
`%LOCALAPPDATA%\VRCForge\agentic-app` (preserved during update/uninstall).

The portable zip (`VRCForge_Windows_x64_1.5.0.zip`) is also available for
no-install/debug use.

---

## Features / 功能概览

Status / 状态: **✅ Available** · **🔧 In Development** · **📋 Planned**

| Status | Area | What it does |
| --- | --- | --- |
| ✅ Available | **MCP 2.0 (`2026-07-28`)** | In the `v1.4.0` release package, the project Core advertises 64 VRCForge Unity tools over protocol revision `2026-07-28` and connects directly to the App. The release provenance scan reported no bundled third-party Unity MCP runtime or package. |
| ✅ Available | **Avatar editing / Avatar 编辑** | BlendShape scan, face tuning (natural-language and reference-image), shader/material tuning (lilToon, Poiyomi, Generic), and vision review with Gesture Manager screenshots. |
| ✅ Available | **Optimization / 优化** | VRAM, material, mesh, and parameter audits with conservative one-step optimization planning. |
| ✅ Available | **Wardrobe / 衣柜管理** | Integer-parameter-based wardrobe scan, outfit import planning (`.unitypackage`, Booth folder, loose prefab), and supervised apply. |
| ✅ Available | **Agentic runtime / Agent 运行时** | Scheduled Goals with durable restart delivery, explicit user/project Memory controls, allowlisted `/delegate` skill dispatch, reviewed sub-agent Adopt/Dismiss handoffs, explicit-user-only Computer Use, and automatic context compaction with exact-usage gates, visible cancellation, and restart recovery. |
| ✅ Available | **Skill packages / 技能包** | `.vsk` community skill packages with manifest and SHA-256 lock validation, Ed25519 signing/trust governance, atomic import/projection, Path-to-Skill capture, SDK scaffolding, and searchable runtime audit evidence. |
| ✅ Available | **Doctor / 诊断** | Startup health checks, live log-level controls, redacted timestamped local logs, one-click log-folder access, and redacted support bundle export. |
| 🔧 In Development | **Avatar Encryption / Anti-Rip (preview)** | lilToon and Poiyomi scan/plan/preview with private-addon connector request interfaces. Windows PC-only; execution requires a separately installed private module. |

---

## Safety / 安全流程

VRCForge uses the supervised flow
`Scan → Plan → Preview → Approval → Checkpoint → Apply → Validate → Restore`.
App-mediated Unity asset writes use this flow by default. Auto-approve and
Advanced Power Mode are optional, visibly confirmed modes with broader
permissions; back up the project before using any write feature. Restore
remains a separate decision.

Approval requests replace only the conversation composer, so prior chat
remains visible. The primary button allows once; eligible future-category
approval is under its chevron, and restore remains a separate approval.
Actionable Windows notifications use the VRCForge name and icon.

---

## Providers and agents / 模型与 Agent 接入

The local MCP + REST gateway supports external agents such as Codex and Claude
Code. It exposes read/plan/request-only access; writes require desktop
approval. Connector templates can be installed for supported local clients
detected on the machine, and availability depends on the client and its
configuration.

VRCForge keeps provider configuration local-first. Model and external-agent
data handling still depends on the provider, action, and content the user
selects; see [Privacy / 隐私](#privacy--隐私).

---

## Preview / Screenshots

Product screenshots will be added here as public release visuals are approved.
The reserved repository paths are:

- `docs/assets/preview-workbench.png`
- `docs/assets/preview-approval-flow.png`
- `docs/assets/preview-unity-tools.png`

These are placeholders only; the repository does not include fabricated
screenshots.

---

## CLI / 命令行

VRCForge includes a local CLI that talks to the desktop runtime at
`http://127.0.0.1:8757`. Open VRCForge Desktop first.

```powershell
# Packaged build
backend\vrcforge_backend.exe --cli doctor
backend\vrcforge_backend.exe --cli checkpoint list --project C:\Path\To\UnityProject

# Source checkout
python tools\vrcforge_cli.py doctor
python tools\vrcforge_cli.py validation run --project C:\Path\To\UnityProject

# Skill SDK (VRCForge 1.3+)
python tools\vrcforge_cli.py skill init .\my-avatar-report --id community.example.my-avatar-report --tool vrcforge_run_validation_report --permission read_project --permission unity_run_validation --permission unity_scan_scene
python tools\vrcforge_cli.py --json skill lock-validate .\my-avatar-report.vsk
```

Write commands (`apply`, `rollback`) create approval requests; actual writes
still go through the desktop approval path. For a generated write skill, pass
`--writes`, the explicit target tool, and a matching mutating permission. The
SDK emits a request-only package with no direct write entrypoint; approval,
checkpoint, and rollback remain mandatory.

---

## Unity Plugin / Unity 插件

For the `v1.4.0` release, `VRCForge.unitypackage` contains the project-scoped
MCP 2.0 Core (`2026-07-28`), lifecycle bootstrap, and the 64 product tools under
`Assets/VRCForge`. After import, the App discovers and connects the selected
project directly. No separate MCP server/package, manifest edit, command, MCP
token copy, or manual Core configuration is required, and normal in-Editor
Core startup does not open a separate console window. This release accepts
protocol revision `2026-07-28`; older clients receive an update error, while
known third-party MCP packages produce a conflict warning. App-mediated writes
use the selected permission mode and the supervised checkpoint/readback/restore
path. Reimporting the same `1.4.0` integration is supported as a repair path;
it is not an overwrite-upgrade path from `1.3.6`. Package import is performed
in Unity; the App connects after the project Core reports ready.

To remove the Unity integration, use `VRCForge > Uninstall VRCForge...` and
confirm the dialog. The command stops the bundled Core, removes only the
versioned VRCForge auto-connect EditorPrefs key, and removes the product-owned
`Assets/VRCForge` root. If Unity cannot remove that root, it preserves the
remaining files and reports an error for manual review.

---

## Privacy / 隐私

VRCForge is local-first and is designed to keep API keys, gateway tokens, paid
asset payloads, and private files local by default. Product-generated connector
config and `.vsk` exports omit plaintext secrets; model and external-agent data
still depends on the action and content the user selects. Support bundles apply
redaction rules, but review them before sharing.

---

## Documentation / 文档

- [User manual / 用户手册](USER_MANUAL.md)
- [Dependencies](DEPENDENCIES.md)
- [Notices](NOTICE)
- [Compatibility matrix](docs/COMPATIBILITY_MATRIX.md)
- [Optimization strategy](docs/OPTIMIZATION_STRATEGY.md)
- [Packaging guide](packaging/README.md)

---

## Developer / 源码调试

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

This path is for development only. Normal users should use the installer.

---

## License / 许可

GPL-3.0-only. The Unity MCP 2.0 Core runtime, command catalogue, input schema
metadata, and tool-result contract are VRCForge-owned implementations. The
`v1.4.0` release provenance scan reported no bundled third-party Unity MCP code
or runtime. Binary releases may also bundle the uv runtime (MIT OR Apache-2.0).
See [LICENSE](LICENSE) and [NOTICE](NOTICE).

VRCForge 以 GPL-3.0-only 发布。Unity MCP 2.0 Core、命令目录、输入 Schema 元数据和
工具结果契约均为 VRCForge 自有实现；`v1.4.0` 发行包的来源扫描未报告捆绑的第三方
Unity MCP 代码或运行时。二进制发行包也可能包含采用 MIT OR Apache-2.0 许可证的 uv
运行时。
