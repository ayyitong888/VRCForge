# VRCForge

[![Version](https://img.shields.io/badge/release-v1.4.0-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v1.4.0)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

Official repository: https://github.com/ayyitong888/VRCForge

VRCForge is a local AI workbench for VRChat avatar editing. It connects a Tauri desktop agent workspace, a local FastAPI runtime, and Unity Editor tools so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接 Tauri 桌面 Agent 工作区、本地 FastAPI 运行时和 Unity Editor 工具，让用户可以在明确审查后应用或恢复 Avatar 改动。

> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

Current source / target release: `1.4.0`. Latest published stable release:
`1.4.0` (`v1.4.0`).

## Install / 安装

Download the latest release / 下载最新 Release:
https://github.com/ayyitong888/VRCForge/releases/latest

1. Download `VRCForge.unitypackage` and run `VRCForge_Web_Installer_x64.exe`
   (or `VRCForge_Offline_Installer_x64.exe` for offline install).
2. Open the target Unity project, import all of `VRCForge.unitypackage`, and
   wait for compilation and `[VRCForge MCP] Core Ready` to finish.
3. Start `VRCForge.exe`, select that project, and connect. The App discovers the
   project-owned Core directly; no separate MCP install, MCP token copy, or
   manual Core configuration is required.

`1.4.0` is a breaking install boundary and does not support overwrite install
or Unity package import over `1.3.6`. Close VRCForge and Unity, remove the old
VRCForge App/runtime and old project integration, then install and import
`1.4.0` fresh. Do not delete `%LOCALAPPDATA%\VRCForge\agentic-app` or unrelated
Unity project content: configured API keys, user-owned `AGENTS.md`, chats,
memories, checkpoints, and unrelated assets must be preserved.

Program files: `%ProgramFiles%\VRCForge`. User data:
`%LOCALAPPDATA%\VRCForge\agentic-app` (preserved during update/uninstall).

Portable zip (`VRCForge_Windows_x64_1.4.0.zip`) is also available for no-install/debug use.

## Features / 功能概览

**MCP 2.0 (`2026-07-28`) / MCP 2.0（`2026-07-28`）：** In the `v1.4.0` release
package, the project Core advertises 64 VRCForge Unity tools over protocol
revision `2026-07-28` and connects directly to the App. The release provenance
scan reported no bundled third-party Unity MCP runtime or package.

**Avatar editing / Avatar 编辑:** BlendShape scan, face tuning (natural-language and reference-image), shader/material tuning (lilToon, Poiyomi, Generic), vision review with Gesture Manager screenshots.

**Safety / 安全流程:** `Scan → Plan → Preview → Approval → Checkpoint → Apply → Validate → Restore`. App-mediated Unity asset writes use this supervised flow by default. Auto-approve and Advanced Power Mode are optional, visibly confirmed modes with broader permissions; back up the project before using any write feature. Restore remains a separate decision.

Approval requests replace only the conversation composer, so prior chat remains
visible. The primary button allows once; eligible future-category approval is
under its chevron, and restore remains a separate approval. Actionable Windows
notifications use the VRCForge name and icon.

**Optimization / 优化:** VRAM, material, mesh, and parameter audits with conservative one-step optimization planning.

**Wardrobe / 衣柜管理:** Integer-parameter-based wardrobe scan, outfit import planning (`.unitypackage`, Booth folder, loose prefab), and supervised apply.

**Agent gateway / Agent 接入:** Local MCP + REST gateway for external agents (Codex, Claude Code, etc.). Read/plan/request-only; writes require desktop approval. Connector templates can be installed for supported local clients detected on the machine; availability depends on the client and its configuration.

**Agentic runtime / Agent 运行时:** Scheduled Goals with durable restart delivery, explicit user/project Memory controls, allowlisted `/delegate` skill dispatch, reviewed sub-agent Adopt/Dismiss handoffs, explicit-user-only Computer Use, and automatic context compaction with exact-usage gates, visible cancellation, and restart recovery.

**Skill packages / 技能包:** `.vsk` community skill packages with manifest and SHA-256 lock validation, Ed25519 signing/trust governance, atomic import/projection, Path-to-Skill capture, SDK scaffolding, and searchable runtime audit evidence.

**Doctor / 诊断:** Startup health checks, live log-level controls, redacted timestamped local logs, one-click log-folder access, and redacted support bundle export.

**Avatar Encryption / Anti-Rip (preview):** lilToon and Poiyomi scan/plan/preview with private-addon connector request interfaces. Windows PC-only; requires separately installed private module for execution.

## CLI / 命令行

VRCForge includes a local CLI that talks to the desktop runtime at `http://127.0.0.1:8757`. Open VRCForge Desktop first.

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

Write commands (`apply`, `rollback`) create approval requests; actual writes still go through the desktop approval path.
For a generated write skill, pass `--writes`, the explicit target tool, and a
matching mutating permission. The SDK emits a request-only package with no
direct write entrypoint; approval, checkpoint, and rollback remain mandatory.

## Unity Plugin / Unity 插件

For the `v1.4.0` release, `VRCForge.unitypackage` contains the project-scoped
MCP 2.0 Core (`2026-07-28`), lifecycle bootstrap, and the 64 product tools under
`Assets/VRCForge`. After import, the App discovers and connects the selected
project directly. No separate MCP server/package, manifest edit, command, MCP
token copy, or manual Core configuration is required, and normal in-Editor Core
startup does not open a separate console window. This release accepts protocol
revision `2026-07-28`; older clients receive an update error, while known
third-party MCP packages produce a conflict warning. App-mediated writes use
the selected permission mode and the supervised checkpoint/readback/restore
path. Reimporting the same `1.4.0` integration is supported as a repair path;
it is not an overwrite-upgrade path from `1.3.6`. Package import is performed
in Unity; the App connects after the project Core reports ready.

To remove the Unity integration, use `VRCForge > Uninstall VRCForge...` and
confirm the dialog. The command stops the bundled Core, removes only the
versioned VRCForge auto-connect EditorPrefs key, and removes the product-owned
`Assets/VRCForge` root. If Unity cannot remove that root, it preserves the
remaining files and reports an error for manual review.

## Privacy / 隐私

VRCForge is local-first and is designed to keep API keys, gateway tokens, paid
asset payloads, and private files local by default. Product-generated connector
config and `.vsk` exports omit plaintext secrets; model and external-agent data
still depends on the action and content the user selects. Support bundles apply
redaction rules, but review them before sharing.

## Developer / 源码调试

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

This path is for development only. Normal users should use the installer.

## Documentation / 文档

- [USER_MANUAL.md](USER_MANUAL.md)
- [DEPENDENCIES.md](DEPENDENCIES.md)
- [NOTICE](NOTICE)
- [docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md)
- [docs/OPTIMIZATION_STRATEGY.md](docs/OPTIMIZATION_STRATEGY.md)
- [packaging/README.md](packaging/README.md)

## License / 许可

GPL-3.0-only. The Unity MCP 2.0 Core runtime, command catalogue, input schema
metadata, and tool-result contract are VRCForge-owned implementations. The
`v1.4.0` release provenance scan reported no bundled third-party Unity MCP code
or runtime. Binary releases may also bundle the uv runtime (MIT OR Apache-2.0). See
[LICENSE](LICENSE) and [NOTICE](NOTICE).

VRCForge 以 GPL-3.0-only 发布。Unity MCP 2.0 Core、命令目录、输入 Schema 元数据和
工具结果契约均为 VRCForge 自有实现；`v1.4.0` 发行包的来源扫描未报告捆绑的第三方
Unity MCP 代码或运行时。二进制发行包也可能包含采用 MIT OR Apache-2.0 许可证的 uv 运行时。
