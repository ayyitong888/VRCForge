# VRChat Avatar Blendshape Gemini Automation

这套项目的目标是：把 Unity 自定义工具、Gemini 语义匹配和 Roslyn 动态执行串成一条链路，用自然语言调整 VRChat SDK3 Avatar 的 blendshape。

当前仓库已经优先整理出一条可先演示、可先验收的 MVP 路线。
项目现状、差距和后续建议见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 目录结构

- `Assets/VRCAutoRig/Editor/BlendshapeExporter.cs`
- `Assets/VRCAutoRig/Editor/RoslynExecutor.cs`
- `vrchat_blendshape_agent.py`
- `.gemini/settings.json`
- `examples/mvp_blendshapes_export.json`
- `examples/mvp_plan_smile.json`
- `tests/test_vrchat_blendshape_agent.py`
- `PROJECT_STATUS.md`
- `tools/unity-mcp-cli.ps1`
- `tools/install-unity-project.ps1`

## 现在能做什么

### MVP 路线

MVP 已经支持两种跑法：

1. 纯本地样例 MVP
2. Gemini 规划 + 本地 mock 执行 MVP

这两条都不依赖真实 Unity 在线执行，因此适合先演示主流程。

### 完整路线

完整路线仍然保留：

- Unity 导出真实 blendshape
- Gemini 生成计划
- Roslyn 生成并执行 C# 代码
- 通过 `unity-mcp` 把执行结果送回 Unity

当前仓库已经改成优先通过仓库内 wrapper 调用 `unity-mcp` CLI，不再强依赖全局 PATH。
如果本机已经装了 `mcpforunityserver` 或 `uv`，脚本会优先复用它们。

## MVP 路线

### 路线 A：纯本地样例 MVP

这条路线不需要 Gemini Key，也不需要 Unity。

它会：

- 读取 `examples/mvp_blendshapes_export.json`
- 读取 `examples/mvp_plan_smile.json`
- 校验计划是否合法
- 生成 Roslyn C# 代码
- 返回 mock 执行结果

运行命令：

```bash
python vrchat_blendshape_agent.py --mvp --plan-json examples/mvp_plan_smile.json --save-plan artifacts/mvp/plan.json --save-csharp artifacts/mvp/apply.cs --save-result artifacts/mvp/result.json
```

这是当前仓库最小、最稳、最容易先演示的一条链路。

### 路线 B：Gemini 规划 + 本地 mock 执行 MVP

这条路线需要 Gemini API Key，但仍然不依赖真实 Unity 执行。

先设置 Key：

```powershell
$env:GEMINI_API_KEY="你的 Key"
```

再运行：

```bash
python vrchat_blendshape_agent.py --mvp "把眼睛睁大，嘴角上扬" --print-plan --save-plan artifacts/mvp/plan.json --save-csharp artifacts/mvp/apply.cs --save-result artifacts/mvp/result.json
```

这条路线会：

- 读取 `examples/mvp_blendshapes_export.json`
- 调用 Gemini 生成计划
- 做本地严格校验
- 生成 C# 代码
- 返回 mock 执行结果

## 完整 Unity 路线

### 接现有 Unity 工程

如果你已经有现成 Unity 工程，先把本仓库的插件和 `unity-mcp` 包依赖接进去：

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-unity-project.ps1 `
  -ProjectPath "E:\unity\Projects\Karin FT Rework" `
  -UnityEditorPath "E:\unity\Unity 2022.3.22f1\Editor\Unity.exe" `
  -LaunchUnity
```

这个脚本会：

- 把 `Assets/VRCAutoRig` 复制到目标 Unity 工程
- 确保 `Packages/manifest.json` 里有 `com.coplaydev.unity-mcp`
- 可选直接启动对应 Unity Editor

然后在 Unity 里继续完成：

- 等待 Package Manager 拉取 `MCP for Unity`
- 安装 Roslyn 运行时支持
- 确认启用 `USE_ROSLYN`
- 启动 Unity MCP Server

### 前置条件

- Unity 2021.3 LTS 或更高
- 已导入 VRChat SDK3 Avatar
- 已安装或可通过 wrapper 自动找到 [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp) CLI
- 已安装 Roslyn DLL，并启用 `USE_ROSLYN`

推荐 Unity Package Manager Git URL：

```text
https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main
```

### Unity 侧工具

当前仓库已经有两个自定义工具：

- `vrc_export_blendshapes`
- `vrc_execute_roslyn`

它们分别对应：

- 导出当前场景 Avatar 的 blendshape 数据
- 在 Unity 主线程执行 Roslyn C# 代码

### 完整命令

Unity 启动并连上 MCP 后，建议先做连接诊断：

```bash
python vrchat_blendshape_agent.py --unity-status
python vrchat_blendshape_agent.py --list-unity-instances
```

如果 `--unity-status` 正常，再运行完整路线命令：

```bash
python vrchat_blendshape_agent.py --avatar "YourAvatarRootPath" "把眼睛睁大，嘴角上扬"
```

多 Avatar 场景里建议先列出：

```bash
python vrchat_blendshape_agent.py --list-avatars
```

## CLI 说明

### 输入来源

- 默认：调用 Unity 导出
- `--export-json path`：直接读取本地导出 JSON
- `--skip-export`：跳过导出，读取 `.gemini/settings.json` 里配置的导出路径
- `--mvp`：如果没传 `--export-json`，默认读取 `examples/mvp_blendshapes_export.json`

### 计划来源

- 默认：调用 Gemini
- `--plan-json path`：跳过 Gemini，直接读取本地计划 JSON
- `--model name`：临时覆盖 Gemini 模型，例如 `gemini-2.5-flash`

### 执行方式

- 默认：真实调用 Unity MCP 执行
- `--mock-execute`：只返回 mock 成功结果，不连接 Unity
- `--mvp`：自动启用 mock 执行

### Unity 诊断

- `--unity-status`：输出当前 `unity-mcp` 连接状态
- `--list-unity-instances`：列出当前可见的 Unity 实例

### 安全相关

- `--avatar`：多 Avatar 场景显式指定目标
- `--min-confidence`：本地置信度阈值
- `--allow-low-confidence`：允许低于阈值的计划继续执行

### 结果落盘

- `--save-plan`
- `--save-csharp`
- `--save-result`

## 配置文件

`.gemini/settings.json` 当前包含：

```json
{
  "gemini": {
    "api_key_env": "GEMINI_API_KEY",
    "model": "gemini-2.5-flash",
    "thinking_level": ""
  },
  "unity_mcp": {
    "command": [
      "powershell",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      "tools/unity-mcp-cli.ps1"
    ],
    "host": "127.0.0.1",
    "port": 8080,
    "instance": "",
    "retries": 3
  },
  "paths": {
    "blendshape_export": "Assets/VRCAutoRig/blendshapes_export.json"
  },
  "planning": {
    "min_confidence": 0.65
  }
}
```

说明：

- 当前模板默认模型改成了 `gemini-2.5-flash`，优先保证本地 MVP 更容易跑通
- 当前模板默认关闭了 `thinking_level`，因为部分 `flash` 模型不支持这个参数
- 当前模板默认通过 `tools/unity-mcp-cli.ps1` 调用官方 `unity-mcp` CLI
- 如果后续要锁定某个 Unity 实例，可以把 `unity_mcp.instance` 填成 `unity-mcp instances` 返回的 `Name@hash`
- 如果你后续有 `gemini-3.1-pro-preview` 配额，可以直接在配置文件里改回去，或运行时加 `--model gemini-3.1-pro-preview`

## 已实现的关键保护

- 多 Avatar 场景下不指定 `--avatar` 会拒绝执行
- 只把目标 Avatar 的导出数据发给 Gemini
- 本地再次校验 avatar / renderer / blendshape 是否真实存在
- 本地拦截低置信度调整
- 重复 adjustment 会自动去重
- `Write Defaults` 已经收敛到目标 Avatar 路径范围

## 本地测试

运行：

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖：

- Avatar 选择
- 目标 Avatar 过滤
- 低置信度拦截
- 重复 adjustment 去重
- 本地导出 JSON 读取
- 本地计划 JSON 读取
- mock 执行结果
- unity-mcp 命令拼装

## 已知限制

- 2 秒超时仍是软保护，不是强制沙箱
- 如果多个 Avatar 共享同一个 `AnimatorController`，`Write Defaults` 仍可能一起生效
- 当前机器上已经确认 `unity-mcp` CLI 可通过 wrapper 启动，但 Unity MCP 服务器尚未从 Unity Editor 侧真正启动
- 当前 MVP 的纯本地路线是“样例导出 + 样例计划 + mock 执行”，目的是先演示主流程，不是替代真实 Unity 验收

## 下一步建议

建议先这样推进：

1. 先跑路线 A，确认本地 MVP 产物都能落出来。
2. 再跑路线 B，确认 Gemini 生成结果能通过本地校验。
3. 在 Unity 里启动 MCP Server 后，先跑 `--unity-status` 和 `--list-avatars`。
4. 最后再跑真实 Unity 执行链路。
