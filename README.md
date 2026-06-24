# VRCForge

[![Version](https://img.shields.io/badge/version-v1.0.0-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v1.0.0)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

Official repository: https://github.com/ayyitong888/VRCForge

Current stable release: `1.0.0`.

1.0.0 release artifacts are published as the public stable release. Use the
installer for normal Windows x64 installs, or the portable zip when you need a
no-install/debug payload.

VRCForge is a local AI workbench for VRChat avatar editing. It connects a Tauri desktop agent workspace, a local FastAPI runtime, and Unity Editor tools so users can review, apply, and restore avatar changes with explicit control.

VRCForge 是面向 VRChat Avatar 编辑的本地 AI 工作台。它连接 Tauri 桌面 Agent 工作区、本地 FastAPI 运行时和 Unity Editor 工具，让用户可以在明确审查后应用或恢复 Avatar 改动。

> Public stable release / 公开稳定版
>
> Back up your Unity / VRChat Avatar project before using asset-writing features.
> 使用任何会写入 Unity 资产的功能前，请先备份 Avatar 工程。

## Install / 安装

For normal Windows x64 users, download the latest release:

普通 Windows x64 用户请下载最新 Release：

https://github.com/ayyitong888/VRCForge/releases/tag/v1.0.0

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

- `VRCForge_Windows_x64_1.0.0.zip`
- `start_dashboard.cmd`, PowerShell scripts, and `quickstart/` remain available for development and troubleshooting.
- End users do not need to install Python, Git, uv, or run `pip install` when using the installer. `VRCForge.exe` checks the Unity MCP runtime at startup, uses bundled `uvx` when available, and otherwise downloads uv into `%LOCALAPPDATA%\VRCForge\tools`.
- The installer writes program files to `%ProgramFiles%\VRCForge`; user data is under `%LOCALAPPDATA%\VRCForge\agentic-app` and is preserved during update/uninstall unless removed manually.

安装器会把程序安装到 `%ProgramFiles%\VRCForge`，用户数据放在 `%LOCALAPPDATA%\VRCForge\agentic-app`。用户数据包括 `config/`、`logs/`、`artifacts/` 和 `backups/`，更新时不会覆盖。

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

If automatic install fails, the desktop app shows the error and log path, then offers a fallback `VRCForge.unitypackage` manual import flow and a re-check button. The 0.8.0 package builder is regression-tested so folder entries import into a fresh Unity project instead of being copied as empty asset files.

如果自动安装失败，桌面 app 会显示错误原因和日志路径，并提供 `VRCForge.unitypackage` 手动导入 fallback 与重新检测按钮。

Unity plugin install is idempotent per Unity project. After a successful install it writes `.vrcforge/install_state.json`; the same VRCForge version and payload checksum will not reinstall on the next launch. If the state is partial or failed, the desktop app stops and shows repair/uninstall options instead of blindly touching `Assets/` or `Packages/manifest.json` again.

The project picker merges manual folders, VCC user projects, Unity Hub recent projects, and active Unity MCP instances. Active MCP instances are shown first so a running Unity project can be selected even if it was not under the default scan root.

`VRCForge.exe` opens the Tauri desktop app directly and starts or reconnects to the local FastAPI runtime. The legacy WebView2 launcher and `start_dashboard.cmd` path remain debug/compatibility surfaces only.

Startup degrades instead of hard-failing. Optional failures in user-data `AGENTS.md` creation, project scanning, Unity/MCP discovery, skill registry loading, or external-agent MCP startup are surfaced as warnings or setup actions while the desktop remains usable as an ordinary agent chat when the backend is online. If startup/bootstrap fails or environment health degrades, the desktop points the user to Startup Doctor; healthy launches stay quiet. Settings also includes a Debug logging switch that records local API/MCP/runtime interactions to redacted logs, and Doctor can export a redacted Support Bundle for troubleshooting. When filing a public GitHub issue, upload or paste the relevant support bundle artifact manually after reviewing it for private data.

## 1.0 Stable Workflow

1.0.0 is the public stable line for Golden Path Matrix coverage, rollback audit
surfacing, Path-to-Skill export, `.vsk` governance, compatibility evidence, and
support hardening. Release evidence is recorded from reusable smoke commands
and published artifact hashes, with local sample-project proofs kept separate
from general installer/support gates.

1.0 stable keeps these public golden paths documented and testable:

- Install and first run.
- Connect Unity.
- Provider / BYOK / local-only / no-provider setup.
- Doctor startup checks.
- First validation report.
- First rollback.
- Booth outfit import planning and supervised apply.
- Safe model optimization with one conservative step.
- External agents using read/plan/write-request only.
- `.vsk` import/export, dry-run, disable, and uninstall.
- Support bundle export and manual issue upload.

Stable release focus:

- Golden Path Matrix: install, Doctor, Unity connection, avatar validation,
  supervised write request, checkpoint, rollback, external-agent request, and
  `.vsk` import/disable/uninstall coverage.
- Proof viewer: human-reviewable proof artifacts for screenshots,
  checkpoints, validation deltas, and rollback evidence.
- Support flow: Doctor support bundle export, then manual upload or paste into
  the GitHub issue template.

The desktop app also includes uninstall actions:
- Unity-side uninstall moves `Assets/VRCForge` and `Packages/com.coplaydev.unity-mcp` to project-root `.vrcforge/backups/`, then removes the manifest dependency with rollback on failure.
- Program uninstall opens the NSIS uninstaller when installed from the x64 installer; user data under `%LOCALAPPDATA%\VRCForge` is preserved unless removed manually.

## CLI / 命令行

VRCForge 1.0.0 includes a local CLI for diagnostics, read-only scans, and
request-based write flows. It talks to the same local desktop runtime at
`http://127.0.0.1:8757`; open VRCForge Desktop first so the backend and app
session token are available.

```powershell
# Source checkout
python tools\vrcforge_cli.py doctor
python tools\vrcforge_cli.py validation run --project C:\Path\To\UnityProject
python tools\vrcforge_cli.py build-test readiness --project C:\Path\To\UnityProject
python tools\vrcforge_cli.py optimization plan --project C:\Path\To\UnityProject --target-profile pc_conservative
python tools\vrcforge_cli.py plan outfit C:\Path\To\outfit.unitypackage --project C:\Path\To\UnityProject --out %TEMP%\vrcforge-plan.json

# Packaged build
backend\vrcforge_backend.exe --cli doctor
backend\vrcforge_backend.exe --cli --json doctor
backend\vrcforge_backend.exe --cli checkpoint list --project C:\Path\To\UnityProject
```

Safe write flow:

```powershell
python tools\vrcforge_cli.py apply --request plan.json
python tools\vrcforge_cli.py rollback --request ckpt_20260621_114108_780917_eeb9ca
```

By default, `apply` and `rollback` only create a VRCForge approval request.
Actual Unity writes still run through VRCForge Desktop approval, pre-write
checkpoint, apply, validation, and rollback. Passing `--execute` requires an
additional terminal confirmation and still approves the same queued VRCForge
request; it does not bypass the safety path.

CLI plan files are local developer artifacts, not Unity assets. By default the
CLI refuses to write `--out` inside the selected Unity project; use a temp
folder or an external notes folder for plan JSON.

## Features / 功能状态

Model optimization integration strategy and release sequencing are documented in [`docs/OPTIMIZATION_STRATEGY.md`](docs/OPTIMIZATION_STRATEGY.md).

| Feature | 功能 | Status |
| --- | --- | --- |
| Avatar and facial Blendshape loading | Avatar 与脸部 Blendshape 读取 | Available / 可用 |
| Manual Blendshape editing and undo | 手动形态键调整与撤销 | Available / 可用 |
| Natural-language Blendshape planning | 自然语言生成形态键调整方案 | Available / 可用 |
| Reference-image assisted face editing | 参考图辅助捏脸 | Available / 可用 |
| AI face tuning history and presets | AI 捏脸历史与预设 | Available / 可用 |
| Locked Blendshapes for partial reroll | 锁定形态键后局部重算 | Available / 可用 |
| Shader / Material tuning MVP | Shader / 材质调参 MVP | Available: lilToon, Poiyomi, and conservative Generic semantic fallback |
| Vision review with Unity screenshots | Unity 截图识图复核 | Available / 可用 |
| Model Optimization Planner and proof release | VRAM / material / mesh / parameter audits, one-step optimization planning, conservative delegated apply requests, validation delta, rollback proof, and persistent screenshot evidence | Available: 1.0 stable line; conservative writes remain approval/checkpoint/rollback gated |
| Phase 2 Unity editor tools | Phase 2 Unity 编辑器工具层 | Available / 可用 |
| Agent workspace (multi-chat UI) | Agent 工作台（多会话界面） | Available / 可用 |
| First-run resilient normal-agent fallback | 首启韧性普通 Agent 兜底 | Available / 可用 |
| Startup Doctor | Environment-only health report for backend, Unity bridge/MCP, providers, SDK/dependency versions, gateway, skills, and checkpoint backend | Available / 可用 |
| Debug logging + Support Bundle | User-controlled local interaction logs plus redacted Doctor export for startup/runtime troubleshooting | Available / 可用 |
| Provider/BYOK test surface | Provider capability badges plus explicit text / JSON / vision-safe tests; no API key required for manual/read-only mode | Available / 可用 |
| Provider reasoning/thinking trace | API-returned visible reasoning, thinking, or thought-summary items are passed through to chat as a default-collapsed row | Available / 可用 |
| Project memory / incremental scan | Local project index for `Assets`, `Packages`, and `ProjectSettings`, surfaced in the desktop chat as added / modified / deleted deltas | Available / 可用 |
| Outfit package import planning | Local `.unitypackage`, Booth folder, and loose prefab/texture folder inspection plus supervised import request UI; direct `VRCForge.unitypackage` import is covered by a fresh-project regression smoke | Beta, approval/checkpoint required |
| Package/plugin install diagnostics | Read-only VPM/ALCOM/vrc-get status, install-output classification, and Unity compile-error context for repair planning | Beta, fixes remain supervised plans |
| Delegated sub-agent workers | Parallel read-only / plan-only workers with avatar-name display labels, lifecycle logs, cancel/retry/inspect, and parent-thread summary merge | Beta |
| Tool Registry v1 | Standardized metadata for Desktop, MCP, and future CLI surfaces, including risk, approval/checkpoint requirements, schemas, and fallbacks | Beta |
| CLI read-only + request-based apply | `doctor`, provider test, Unity status, project/avatar scan, validation, Build/Test readiness, checkpoint, skill, outfit plan, request apply, and request rollback commands | Beta, local CLI tests pass |
| Build/Test readiness gate | Read-only Unity compile, SDK/avatar, validation severity, and optional VRChat SDK validation status for deciding whether an avatar is ready to test | Beta, fixes remain supervised plans |
| Three-tier permission model (approval / auto / Roslyn full-auto) | 三档权限（审批 / 自动 / Roslyn 全自动） | Available / 可用 |
| Chat persistence and history replay across restarts | 会话持久化与重启后历史回放 | Available / 可用 |
| `/compact` history compaction (LLM summary with local fallback) | `/compact` 历史压缩（模型摘要，失败回退本地摘要） | Available / 可用 |
| Slash-command skill invocation with autocomplete | 斜杠命令直接调用 skill（带补全菜单） | Available / 可用 |
| Steering queue and per-turn run visualization | 插队队列与每轮运行可视化（运行行/耗时） | Available / 可用 |
| Roslyn Advanced Power Mode (in-memory compile, zero-install CodeDom fallback) | Roslyn 高级模式（内存编译，免安装 CodeDom 兜底） | Available / 可用 |
| Unity compile-error reading (`vrc_get_compile_errors`) | Unity 编译错误读取（agent 自修闭环基础） | Available / 可用 |
| External Agent Gateway (MCP + REST, supervised writes) | 外部 Agent Gateway（MCP + REST，受监督写入） | Available / 可用 |
| Agent Connector Settings | Gateway toggle, token revoke, connector config copy, recent calls, and write-request separation for external agents | Available / 可用 |
| External agent connector templates and smoke | HTTP + stdio MCP snippets without plaintext tokens, plus supervised write/rollback smoke | Available / 可用 |
| `.vsk` community skill packages | `.vsk` 社区 skill 包导入/导出/校验 | Available / 可用 |
| Skill Manager UI for `.vsk` packages | List/import/preflight/export .vsk packages with risk, permissions, signature status, signer fingerprint, and no “verified” label | Available / 可用 |
| Generic Unity CRUD tools (component, GameObject, asset/prefab) | 通用 Unity CRUD 工具（组件、GameObject、资产/Prefab） | Beta, local tests pass |
| Generic avatar authoring primitives (parameters, menus, FX animator states) | Expression parameters / menu controls / animator states | Beta, local tests pass; preview path covered by wardrobe workflow |
| Modular Avatar and VRCFury read-only scans | Modular Avatar / VRCFury 只读扫描 | Available / 可用 |
| Outfit setup wrapper and VPM package status/install | Outfit 安装封装与 VPM 包状态/安装 | Available / 可用 |
| Modular Avatar component writer | MergeArmature / BoneProxy / MenuInstaller / MergeAnimator / Parameters | Beta, Unity live previews pass |
| Avatar performance scan | Avatar 性能扫描 | Available / 可用 |
| Validation Report v1 (`vrcforge.validation.v1`) | Read-only compile/avatar/parameter/menu/FX/bindings/material/wardrobe/performance report with severity gate; fixes remain separate approved plans | Beta, local and Unity live smoke pass |
| Int-exclusive wardrobe scan/create/add/manage tools | int wardrobe scan/create/add/remove/rename/reorder/default/delete | Beta, local tests pass; Unity live scan/preview smoke passed |
| Outfit-part writer | Add an int-gated accessory toggle to one wardrobe outfit | Beta, Unity live preview and rollback smoke pass |
| Semantic add-outfit workflow | Prefab search -> instantiate -> Setup Outfit -> scan/create wardrobe if missing -> wardrobe binding | Beta, local tests pass; candidate wardrobe auto-selection guarded |
| Pre-write checkpoint timeline | Git or archive checkpoint before gateway and legacy REST writes, plus incremental preview/restore UI | Beta, Unity live write/restore smoke passed |

Wardrobe scanning is intentionally conservative. `wardrobes` contains only
high-confidence int-exclusive wardrobes backed by FX Animator Any-State
`Equals` transitions and AnimationClip `m_IsActive` evidence. Renamed or
partially customized structures may appear as `wardrobeCandidates` for explicit
user selection. Naked-base accessory/clothing-off toggles appear as
`looseControls` and are never used automatically by the add-outfit workflow.

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

The connector generator emits copyable loopback MCP snippets for external coding agents and uses environment-variable placeholders such as `VRCFORGE_AGENT_TOKEN`; it does not print or write plaintext access tokens into generated client config.

External MCP clients can request writes, but VRCForge owns approval and execution. The MCP server advertises read, plan, and request tools; direct apply is kept on the desktop approval path.

Use HTTP config when VRCForge Desktop is already running. Use the stdio bridge config for local MCP clients that need token-free local startup, but generated stdio configs include `--no-start` and require the VRCForge runtime to already be online. Installed builds generate a stdio command that runs `backend/vrcforge_backend.exe --agent-mcp-stdio --no-start`; source checkouts use `python tools/vrcforge_agent_mcp_stdio.py --no-start`. The stdio bridge reads the gateway token from the VRCForge user-data config or `VRCFORGE_AGENT_TOKEN`; copied configs must not contain plaintext tokens.

Settings > Agent Connectors also provides one-click install/remove/copy actions for four MCP client targets: Codex App, Codex CLI, Claude Code CLI, and Claude Cowork App. Codex App and Codex CLI share the user `~/.codex/config.toml` server entry; Claude Code writes the selected project's `.mcp.json`; Claude Cowork writes `%APPDATA%\Claude\claude_desktop_config.json` and marks the server as `type: sdk`. Installs merge instead of overwriting existing servers, create a backup before writing, reject invalid JSON/TOML without partial writes, and run a real stdio MCP `initialize` + `tools/list` self-test before reporting success. When a client is not installed, VRCForge still writes valid config and reports the missing client separately.

Connector success is gated by smoke tests:

```powershell
npm run smoke:external-agent
npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject
```

The preflight smoke temporarily enables the gateway, checks connector config, runs stdio bridge preflight, performs a real stdio MCP `initialize` + `tools/list`, reads the manifest, and lists HTTP MCP tools without writing Unity. The live smoke additionally forces approval mode, requests a supervised Unity write through MCP, approves it through the desktop app API, creates a pre-write checkpoint, runs validation, requests checkpoint restore through MCP, approves rollback, verifies the temporary scene object is gone, checks Unity compile errors, and restores the previous gateway/permission state. A passing report is written under `artifacts/external-agent-smoke/`. Success requires `vrcforge_request_apply` to be advertised, direct apply tools to be absent, a checkpoint id to be created for live writes, rollback to complete, no temporary object residue, and compile errors to remain zero.

Common failure meanings:

- `Gateway token was not found`: start VRCForge Desktop once or use the stdio bridge so it can read user-data config.
- `connector.config` 404: the running backend is older than this connector/smoke layer; install or start a backend built from the current commit.
- `stdio.mcp_tools_list` failed with `No module named 'mcp'`: a source-checkout bridge is using a Python environment without the MCP package; use the packaged build or install source dependencies.
- `directApplyAdvertised`: the backend is exposing internal approval execution to external agents; do not treat that runtime as release-ready.
- `mcp.tools_list` 403: the gateway is disabled or the token is invalid.
- `rollback.verify_no_residue` failed: stop release work and fix rollback before shipping.

Gateway 默认关闭。请在桌面设置中启用并复制本地 token/config。外部 agent 可以读取日志、截图、Unity 状态并生成方案；真正写入 Unity 前仍必须等待用户 approval，approval token 只由 VRCForge 内部使用，不会写进复制给外部 agent 的配置。

## Community Skill Packages

VRCForge supports `.vsk` skill packages for community distribution. Package import performs manifest validation, lock-file SHA-256 checks, optional Ed25519 signature verification, duplicate/update checks, and ZIP safety checks for traversal, absolute paths, drive paths, symlinks, duplicate entries, and oversized payloads. Export can build dev or release packages from installed user skills. Imported packages can be projected into the user skill directory so they appear in slash-command and gateway skill lists.

The desktop Skill Manager can list installed packages, inspect preflight results, import packages by path, export dev/release packages, and show risk, permissions, signature status, signer fingerprint, and manifest details. Signature labels only mean package integrity and signer continuity; VRCForge does not label community packages as verified.

## Privacy Boundary

VRCForge is local-first. API key values, gateway token values, paid asset
payloads, Booth package contents, FBX files, textures, material binaries, and
private files must not be copied into model context, external-agent config, or
`.vsk export` output. Support bundle exports are redacted, but users should
review them before uploading or pasting them into an issue.

## Safety / 安全原则

VRCForge follows a supervised workflow for write operations:

VRCForge 对写入操作采用受监督流程：

```text
Scan -> Plan -> Preview -> Approval -> Checkpoint -> Apply -> Validate -> Restore
```

Core app workflows use predefined Unity tools. Roslyn is preserved only as Advanced Power Mode and every call still requires `confirmAdvancedPowerMode=true`. The first full-permission confirmation is persisted by the desktop app and synchronized to Unity; direct calls that bypass the app retain a one-time Unity warning fallback. Snippets are compiled fully in memory: the primary backend is Roslyn (only 4 DLLs, installed by `tools/install-roslyn-support.ps1`), with a zero-install CodeDom fallback when those DLLs are absent. Compile errors are returned with user-relative line numbers, and the read-only tool `vrc_get_compile_errors` reports project compile errors from the last Unity compilation pass.

Gateway writes and legacy desktop REST write endpoints create a pre-write checkpoint after saving open Unity scenes and dirty assets. Git worktrees use git-backed checkpoints; other projects use a local compressed baseline. Restore previews and applies only the changed, added, or deleted files, then reloads restored scenes and refreshes Unity assets. The desktop Checkpoints view lists, previews, and requests restore through the same approval path as other writes. Direct raw Unity MCP calls made outside VRCForge cannot be intercepted; use the supervised gateway or desktop write paths when rollback is required.

核心 app 流程使用预定义 Unity 工具。Roslyn 只作为 Advanced Power Mode 保留，每次调用仍必须传 `confirmAdvancedPowerMode=true`。桌面端首次确认完全权限后会把永久确认状态同步到 Unity；绕过桌面端直接调用时，Unity 仍保留一次性警告兜底。Snippet 在内存中完整编译：主后端为 Roslyn（仅 4 个 DLL，由 `tools/install-roslyn-support.ps1` 安装），未装 DLL 时自动回退到免安装的 CodeDom。编译错误带用户视角行号返回；只读工具 `vrc_get_compile_errors` 可读取最近一次 Unity 编译的错误列表。

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
- [docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md)
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
