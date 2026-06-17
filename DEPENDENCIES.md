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
| `mcpforunityserver` | Python side of the Unity MCP connection | Unity MCP 连接的 Python 侧组件 |
| `httpx` | FastAPI test client support | FastAPI 测试客户端支持 |
| `pytest` | Local test runner | 本地测试运行器 |

## Optional local tools / 可选本地工具

| Tool | Used for | 用途 |
| --- | --- | --- |
| `git` | Development-branch checkpoint timeline for Unity project writes | Unity 工程写入前 checkpoint 时间线 |

The checkpoint timeline only becomes restorable when the selected Unity project is a git worktree and `git` is available. If not, VRCForge records checkpointing as unavailable and continues to use the normal approval model.

## Unity / Unity 侧

VRCForge does not require one single `.unitypackage` file. Use a normal VRChat Avatar project and add the packages below.
VRCForge 当前不依赖一个单独的 `.unitypackage` 文件。请使用普通 VRChat Avatar 工程，并加入下面的包。

| Package | How to install | Required | 用途 |
| --- | --- | --- | --- |
| VRChat SDK - Avatars | Install through VRChat Creator Companion | Yes | Provides Avatar Descriptor, Expression Parameters, Expression Menu, and VRChat avatar APIs |
| MCP for Unity (`com.coplaydev.unity-mcp`) | Add through Unity Package Manager, or let VRCForge / `tools/install-unity-project.ps1` add it to `Packages/manifest.json` | Yes | Lets the local runtime call Unity Editor tools |
| Unity Newtonsoft Json (`com.unity.nuget.newtonsoft-json`) | Usually pulled in by SDK/packages; add from Unity Package Manager if Unity reports missing `Newtonsoft.Json` | Yes if missing | JSON parsing inside Unity editor tools |

MCP for Unity package URL used by the install script:
安装脚本使用的 MCP for Unity 包地址：

```json
"com.coplaydev.unity-mcp": "file:Packages/com.coplaydev.unity-mcp"
```

Windows x64 installer builds bundle a pinned copy of CoplayDev MCP under
`third_party/com.coplaydev.unity-mcp` after the license gate passes, then copy it
into the release payload at `unity_plugin/Packages/com.coplaydev.unity-mcp`.

The bundled CoplayDev Unity MCP package is MIT licensed. VRCForge must preserve
the upstream `LICENSE` file in the package and copy it into the release payload
as `licenses/CoplayDev-Unity-MCP-LICENSE.txt`. The build gate checks for the
expected CoplayDev MIT copyright and permission notice text before packaging.
Because VRCForge vendors a modified package copy, it also ships
`VRCFORGE_DISTRIBUTION_NOTES.txt` in the package root and copies it into release
payloads as `licenses/CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt`.

Before every release build, all bundled third-party components must pass
`packaging/check_third_party_licenses.ps1`. Any new bundled dependency must be
listed in `packaging/THIRD_PARTY_LICENSES.json` before it can be shipped.

Windows x64 release payloads may also bundle the official uv runtime so the
desktop app/backend can bootstrap `uvx --from mcpforunityserver unity-mcp` on
machines that do not have Python or uv installed. uv is licensed `MIT OR Apache-2.0`; release
builds copy `LICENSE-MIT`, `LICENSE-APACHE`, and VRCForge distribution notes
into the payload `licenses/` folder.

## VRCForge Unity Files / VRCForge Unity 文件

Copy or install this repository folder into the Unity project:
将本仓库中的以下目录复制或安装到 Unity 工程：

```text
Assets/VRCForge/
```

The helper script can copy it and add MCP for Unity:
辅助脚本可以复制该目录并添加 MCP for Unity：

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-unity-project.ps1 -ProjectPath "PATH_TO_UNITY_PROJECT"
```

## External Agent Gateway / 外部 Agent Gateway

The backend includes a local MCP + REST Agent Gateway for external MCP-capable agent clients. It uses the official Python MCP SDK through `mcp[cli]` and is disabled by default until enabled in desktop settings.

外部 Agent Gateway 使用官方 Python MCP SDK（`mcp[cli]`），默认关闭。启用后，外部 agent 只能通过 VRCForge 的受监督工具层读取、预览、请求写入和等待用户 approval，不能直接绕过 VRCForge 调 Unity MCP。approval token 由 VRCForge 内部保存，不包含在复制给外部 agent 的 MCP 配置中。

## Execution Model / 执行模型

VRCForge ships predefined Unity tools for normal avatar reads and writes. Dry-run previews show the MCP tool payload that will be sent to Unity, not generated executable code.

Roslyn is preserved only as Advanced Power Mode. It is disabled by default and is not part of the normal app workflow. Snippets are compiled in-memory: the primary backend is Roslyn (only 4 DLLs: Microsoft.CodeAnalysis, Microsoft.CodeAnalysis.CSharp, System.Collections.Immutable, System.Reflection.Metadata), with a zero-install CodeDom fallback when those DLLs are absent. To install the Roslyn backend, define `VRCFORGE_ENABLE_ROSLYN` in Unity scripting define symbols and run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-roslyn-support.ps1 -ProjectPath "PATH_TO_UNITY_PROJECT"
```

Every Roslyn call must pass `confirmAdvancedPowerMode=true`, and Unity shows a modal warning dialog before executing the snippet. If the user cancels the dialog, the tool does not run.

Use the read-only Unity tool `vrc_get_compile_errors` (gateway name `vrcforge_get_compile_errors`) to read the last Unity compile errors after a failed project compile. It combines `CompilationPipeline` capture with a Unity Console fallback so agent repair loops can see compiler diagnostics.

Use the read-only Unity tool `vrc_check_roslyn_status` to verify the installed DLLs, `VRCFORGE_ENABLE_ROSLYN` flag, and runtime type loading before requesting execution. For CI or local Unity batch checks, run:

```powershell
Unity.exe -batchmode -quit -projectPath "PATH_TO_UNITY_PROJECT" -executeMethod VRCForge.Editor.RoslynStatusTool.BatchStatusSmoke -logFile roslyn-status-smoke.log
```

To prove the snippet pipeline can dynamically compile and execute inside Unity, run the fixed safe execution smoke. It compiles a hardcoded C# snippet through the same in-memory compilation path and expects `result=42` (the log includes which compiler backend was used):

```powershell
Unity.exe -batchmode -quit -projectPath "PATH_TO_UNITY_PROJECT" -executeMethod VRCForge.Editor.RoslynStatusTool.BatchExecutionSmoke -logFile roslyn-execution-smoke.log
```
