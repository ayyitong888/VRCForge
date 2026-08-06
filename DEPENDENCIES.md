# Dependencies / 依赖说明

This project has three runtime sides: the Tauri desktop app, the local FastAPI agent runtime, and the Unity avatar project.
本项目运行时分为三部分：Tauri 桌面 app、本地 FastAPI agent runtime，以及 Unity Avatar 工程。

## Python / Python 侧

Source/debug users can install the Python packages from the repository root. Normal Windows installer users do not need to run this manually.
源码/调试用户可在仓库根目录安装 Python 依赖。普通 Windows 安装器用户不需要手动执行这一步。

```powershell
python -m pip install -r requirements.txt
```

| Package | Used for | 用途 |
| --- | --- | --- |
| `fastapi` | Local app API, agent runtime, and gateway backend | 本地 app API、agent runtime 与 gateway 后端 |
| `uvicorn` | Runs the local backend server | 启动本地后端服务 |
| `pydantic` | Request, response, and plan validation | 请求、响应与调整计划校验 |
| `openai` | OpenAI-compatible providers, including OpenAI, DeepSeek, OpenRouter, Ollama-compatible HTTP endpoints | OpenAI-compatible 接口，包括 OpenAI、DeepSeek、OpenRouter、Ollama 兼容 HTTP 接口 |
| `google-genai` | Google AI Studio and Google Vertex AI Gemini calls | Google AI Studio 与 Google Vertex AI Gemini 调用 |
| `anthropic` | Anthropic Claude calls | Anthropic Claude 调用 |
| `cryptography` | `.vsk` skill package signing and Ed25519 signature verification | Skill 包签名与验签 |
| `httpx` | FastAPI test client support | FastAPI 测试客户端支持 |
| `pytest` | Local test runner | 本地测试运行器 |

## Optional local tools / 可选本地工具

| Tool | Used for | 用途 |
| --- | --- | --- |
| `git` | Preferred checkpoint backend for Unity projects already using Git | 已使用 Git 的 Unity 工程优先 checkpoint 后端 |

Git is optional at runtime. Git worktrees use git-backed checkpoints; non-git projects use a compressed local baseline and restore only the incremental file diff. Both strategies save open Unity scenes/assets before capture and reload them after restore.

## Unity / Unity 侧

Import the `v1.4.0` release artifact `VRCForge.unitypackage` into a supported
VRChat Avatar project. It installs the VRCForge-owned project-scoped MCP 2.0
Core, lifecycle bootstrap, and the release's 64 Unity tools under
`Assets/VRCForge`. This release does not require a separate MCP server/package,
Unity manifest edit, Python/`uvx` command, MCP token copy, or manual Core
configuration. The App and package use protocol revision `2026-07-28`.

Remove the Unity integration from `VRCForge > Uninstall VRCForge...`. That
explicit command stops the bundled Core, deletes only its versioned
auto-connect preference, and removes the product-owned `Assets/VRCForge` root.

将 `v1.4.0` 的 `VRCForge.unitypackage` 导入受支持的 VRChat Avatar 工程后，
它会把 VRCForge 自有的项目级 MCP 2.0 Core、生命周期引导程序和该版本的 64 个 Unity
工具安装到 `Assets/VRCForge`。此版本不需要额外 MCP 服务或包、Unity manifest 修改、
Python/`uvx` 命令、MCP token 复制或手工配置 Core，并使用协议版本 `2026-07-28`。

| Package | How to install | Required | 用途 |
| --- | --- | --- | --- |
| VRChat SDK - Avatars | Install through VRChat Creator Companion | Yes | Provides Avatar Descriptor, Expression Parameters, Expression Menu, VRChat avatar APIs, and the supported project baseline |
| Unity Newtonsoft Json (`com.unity.nuget.newtonsoft-json`) | Already present in the supported VCC Avatar project baseline; the verified `v1.4.0` clean-import path does not fetch or edit it | Baseline | JSON parsing inside Unity editor tools |

The `v1.4.0` release acceptance import was performed with network package
resolution disabled and verified that `Packages/manifest.json` plus
`packages-lock.json` stayed byte-identical. Reimporting that same integration
does not add a UPM package or require a dependency install. This is not an
overwrite-upgrade guarantee: migration from `1.3.6` follows the fresh-install
boundary in [README.md](README.md).

Before every release build, all bundled third-party components must pass
`packaging/check_third_party_licenses.ps1`. Any new bundled dependency must be
listed in `packaging/THIRD_PARTY_LICENSES.json` before it can be shipped.

Windows x64 release payloads may also bundle the official uv runtime for
backend-managed support tooling. It is not used to start or install the Unity
MCP Core. uv is licensed `MIT OR Apache-2.0`; release builds copy `LICENSE-MIT`,
`LICENSE-APACHE`, and VRCForge distribution notes into the payload `licenses/`
folder.

## VRCForge Unity Files / VRCForge Unity 文件

Copy or install this repository folder into the Unity project:
将本仓库中的以下目录复制或安装到 Unity 工程：

```text
Assets/VRCForge/
```

The source/debug helper script installs the same self-contained VRCForge tree:
源码/调试辅助脚本会安装相同的自包含 VRCForge 文件树：

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-unity-project.ps1 -ProjectPath "PATH_TO_UNITY_PROJECT"
```

## External Agent Gateway / 外部 Agent Gateway

The backend includes a local MCP + REST Agent Gateway for external MCP-capable
agent clients. It is served by the VRCForge-owned MCP 2.0 (`2026-07-28`) router
and is disabled by default until enabled in desktop settings.

外部 Agent Gateway 由 VRCForge 自有的 MCP 2.0（`2026-07-28`）路由提供，默认关闭。启用后，
外部 agent 只能通过 VRCForge 的受监督工具层读取、预览、请求写入和等待用户 approval，
不能绕过 VRCForge 直接调用 Unity MCP。approval token 由 VRCForge 内部保存，不包含在
复制给外部 agent 的 MCP 配置中。

## Execution Model / 执行模型

VRCForge ships predefined Unity tools for normal avatar reads and writes. Dry-run previews show the MCP tool payload that will be sent to Unity, not generated executable code.

Roslyn is preserved only as Advanced Power Mode. It is disabled by default and is not part of the normal app workflow. Snippets are compiled in-memory: the primary backend is Roslyn (only 4 DLLs: Microsoft.CodeAnalysis, Microsoft.CodeAnalysis.CSharp, System.Collections.Immutable, System.Reflection.Metadata), with a zero-install CodeDom fallback when those DLLs are absent. To install the Roslyn backend, define `VRCFORGE_ENABLE_ROSLYN` in Unity scripting define symbols and run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-roslyn-support.ps1 -ProjectPath "PATH_TO_UNITY_PROJECT"
```

Every Roslyn call must pass `confirmAdvancedPowerMode=true`. The desktop app records the first full-permission risk confirmation permanently and synchronizes it to Unity, so normal app calls do not repeat the modal. Direct calls that bypass the app retain a one-time Unity warning fallback; cancelling it prevents execution.

Use the read-only Unity tool `vrc_get_compile_errors` (gateway name `vrcforge_get_compile_errors`) to read the last Unity compile errors after a failed project compile. It combines `CompilationPipeline` capture with a Unity Console fallback so agent repair loops can see compiler diagnostics.

Use the read-only Unity tool `vrc_check_roslyn_status` to verify the installed DLLs, `VRCFORGE_ENABLE_ROSLYN` flag, and runtime type loading before requesting execution. For CI or local Unity batch checks, run:

```powershell
Unity.exe -batchmode -quit -projectPath "PATH_TO_UNITY_PROJECT" -executeMethod VRCForge.Editor.RoslynStatusTool.BatchStatusSmoke -logFile roslyn-status-smoke.log
```

To prove the snippet pipeline can dynamically compile and execute inside Unity, run the fixed safe execution smoke. It compiles a hardcoded C# snippet through the same in-memory compilation path and expects `result=42` (the log includes which compiler backend was used):

```powershell
Unity.exe -batchmode -quit -projectPath "PATH_TO_UNITY_PROJECT" -executeMethod VRCForge.Editor.RoslynStatusTool.BatchExecutionSmoke -logFile roslyn-execution-smoke.log
```
