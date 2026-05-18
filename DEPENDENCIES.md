# Dependencies / 依赖说明

This project has two sides: the local Python dashboard and the Unity avatar project.
本项目分为两部分：本地 Python dashboard，以及 Unity Avatar 工程。

## Python / Python 侧

Install the Python packages from the repository root:
在仓库根目录安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

| Package | Used for | 用途 |
| --- | --- | --- |
| `fastapi` | Local dashboard API and browser UI backend | 本地 dashboard API 与浏览器 UI 后端 |
| `uvicorn` | Runs the local dashboard server | 启动本地 dashboard 服务 |
| `pydantic` | Request, response, and plan validation | 请求、响应与调整计划校验 |
| `openai` | OpenAI-compatible providers, including OpenAI, DeepSeek, OpenRouter, Ollama-compatible HTTP endpoints | OpenAI-compatible 接口，包括 OpenAI、DeepSeek、OpenRouter、Ollama 兼容 HTTP 接口 |
| `google-genai` | Google AI Studio and Google Vertex AI Gemini calls | Google AI Studio 与 Google Vertex AI Gemini 调用 |
| `anthropic` | Anthropic Claude calls | Anthropic Claude 调用 |
| `mcpforunityserver` | Python side of the Unity MCP connection | Unity MCP 连接的 Python 侧组件 |
| `httpx` | FastAPI test client support | FastAPI 测试客户端支持 |
| `pytest` | Local test runner | 本地测试运行器 |

## Unity / Unity 侧

VRCForge does not require one single `.unitypackage` file. Use a normal VRChat Avatar project and add the packages below.
VRCForge 当前不依赖一个单独的 `.unitypackage` 文件。请使用普通 VRChat Avatar 工程，并加入下面的包。

| Package | How to install | Required | 用途 |
| --- | --- | --- | --- |
| VRChat SDK - Avatars | Install through VRChat Creator Companion | Yes | Provides Avatar Descriptor, Expression Parameters, Expression Menu, and VRChat avatar APIs |
| MCP for Unity (`com.coplaydev.unity-mcp`) | Add through Unity Package Manager, or let `tools/install-unity-project.ps1` add it to `Packages/manifest.json` | Yes | Lets the local dashboard call Unity Editor tools |
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

## Execution Model / ????

VRCForge ships predefined Unity tools for avatar reads and writes. It does not ship a VRCForge arbitrary C# execution fallback. Dry-run previews show the MCP tool payload that will be sent to Unity, not generated executable code.
