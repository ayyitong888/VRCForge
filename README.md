# VRCForge

[![Version](https://img.shields.io/badge/source-v1.2.0-blue)](https://github.com/ayyitong888/VRCForge)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

Official repository: https://github.com/ayyitong888/VRCForge

VRCForge is a local AI workbench for VRChat avatar editing. It connects a Tauri desktop agent workspace, a local FastAPI runtime, and Unity Editor tools so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接 Tauri 桌面 Agent 工作区、本地 FastAPI 运行时和 Unity Editor 工具，让用户可以在明确审查后应用或恢复 Avatar 改动。

> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

Current source / target release: `1.2.0`. Latest published stable release:
`1.1.2` (`v1.1.2`) until the 1.2.0 release gate and publication complete.

## Install / 安装

Download the latest release / 下载最新 Release:
https://github.com/ayyitong888/VRCForge/releases/latest

1. Download and run `VRCForge_Web_Installer_x64.exe` (or `VRCForge_Offline_Installer_x64.exe` for offline install).
2. Start `VRCForge.exe` from the desktop or Start Menu.
3. Select a Unity VRChat Avatar project root in the first-run setup.
4. The app installs the Unity plugin, starts the backend, and opens the agent workspace.

Program files: `%ProgramFiles%\VRCForge`. User data: `%LOCALAPPDATA%\VRCForge\agentic-app` (preserved during update/uninstall).

Portable zip (`VRCForge_Windows_x64_1.2.0.zip`) is also available for no-install/debug use after the 1.2.0 release is published.

## Features / 功能概览

**Avatar editing / Avatar 编辑:** BlendShape scan, face tuning (natural-language and reference-image), shader/material tuning (lilToon, Poiyomi, Generic), vision review with Gesture Manager screenshots.

**Safety / 安全流程:** `Scan → Plan → Preview → Approval → Checkpoint → Apply → Validate → Restore`. All writes go through approval, pre-write checkpoint, and rollback. Three permission tiers: approval (default), auto-approve, and Roslyn full-auto.

**Optimization / 优化:** VRAM, material, mesh, and parameter audits with conservative one-step optimization planning.

**Wardrobe / 衣柜管理:** Int-exclusive wardrobe scan, outfit import planning (`.unitypackage`, Booth folder, loose prefab), and supervised apply.

**Agent gateway / Agent 接入:** Local MCP + REST gateway for external agents (Codex, Claude Code, etc.). Read/plan/request-only; writes require desktop approval. One-click connector install for Codex App, Codex CLI, Claude Code CLI, and Claude Cowork.

**Agentic runtime / Agent 运行时:** Scheduled Goals with durable restart delivery, explicit user/project Memory controls, allowlisted `/delegate` skill dispatch, reviewed sub-agent Adopt/Dismiss handoffs, and explicit-user-only Computer Use.

**Skill packages / 技能包:** `.vsk` community skill packages with manifest validation, SHA-256 lock-file checks, optional Ed25519 signature verification, import/export/preflight.

**Doctor / 诊断:** Startup health checks, debug logging, redacted support bundle export.

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

The desktop app auto-installs VRCForge Unity tools to `Assets/VRCForge/Editor` and the Unity MCP package to `Packages/com.coplaydev.unity-mcp`, with backups under `.vrcforge/backups/`. Install is idempotent and includes fallback to manual `VRCForge.unitypackage` import.

## Privacy / 隐私

VRCForge is local-first. API keys, gateway tokens, paid asset payloads, and private files are never copied into model context, external-agent config, or `.vsk` export output. Support bundles are redacted; review before sharing.

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

GPL-3.0. Binary releases bundle CoplayDev Unity MCP (MIT) and may bundle the uv runtime (MIT OR Apache-2.0). See [LICENSE](LICENSE).

VRCForge 使用 GPL-3.0 发布。二进制 Release 包含 CoplayDev Unity MCP (MIT) 和 uv 运行时 (MIT OR Apache-2.0)。
