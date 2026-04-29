# VRChat Avatar Blendshape Gemini Automation

这套框架把 Unity 自定义工具、Gemini 语义匹配和 Roslyn 动态执行串成一条链路，用自然语言直接调整 VRChat SDK3 Avatar 的 blendshape 权重。

## 目录结构

- `Assets/VRCAutoRig/Editor/BlendshapeExporter.cs`
- `Assets/VRCAutoRig/Editor/RoslynExecutor.cs`
- `vrchat_blendshape_agent.py`
- `.gemini/settings.json`
- `requirements.txt`

## 功能概览

- Unity 自定义工具 `vrc_export_blendshapes`
  - 扫描当前打开场景中的 `SkinnedMeshRenderer`
  - 识别 VRChat Avatar 根节点
  - 导出真实 blendshape 名字、路径、当前权重到 JSON
- Unity 自定义工具 `vrc_execute_roslyn`
  - 接收 C# 代码字符串
  - 使用 Roslyn 在 Unity 主线程执行
  - 自动注入 Unity / VRChat SDK 程序集引用
  - 编译失败时输出清晰诊断
  - 统一把 Animator State 的 `Write Defaults` 打开
  - 2 秒安全超时保护
- Python 代理
  - 先调用 Unity MCP 导出 blendshape
  - 使用 Gemini 根据自然语言做语义匹配
  - 生成 C# 赋值代码
  - 再通过 Unity MCP 发送回 Unity 执行
  - MCP 调用失败自动重试

## 前置条件

### 1. Unity 工程

- Unity 2021.3 LTS 或更高
- 已导入 VRChat SDK3 Avatar
- 已安装 [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp)

推荐从 Package Manager 用 Git URL 安装：

```text
https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main
```

### 2. 启用 unity-mcp 服务

按照官方 README，在 Unity 中：

1. 打开 `Window > MCP for Unity`
2. 点击 `Start Server`
3. 确认本地服务已连通
4. 等待 Unity 编译完 `Assets/VRCAutoRig/Editor/` 下的自定义工具

`unity-mcp` 官方文档说明了：

- 自定义工具必须放在 `Editor/` 目录下才会被发现
- 可以通过 `unity-mcp editor custom-tool "<tool_name>" --params '{...}'` 调用工具

参考：

- [CoplayDev/unity-mcp README](https://github.com/CoplayDev/unity-mcp)
- [Custom Tools 文档](https://github.com/CoplayDev/unity-mcp/blob/beta/docs/reference/CUSTOM_TOOLS.md)

### 3. 安装 Roslyn

在 Unity 中：

1. 打开 `Window > MCP For Unity`
2. 找到 Runtime Code Execution / Roslyn 区域
3. 点击安装 Roslyn DLL
4. 在 `Player Settings > Scripting Define Symbols` 中加入 `USE_ROSLYN`
5. 重启 Unity

如果没有这一步，`vrc_execute_roslyn` 会直接返回清晰错误，不会静默失败。

### 4. Python 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Gemini API Key

把 API Key 放到环境变量里：

```powershell
$env:GEMINI_API_KEY="你的 Key"
```

`.gemini/settings.json` 默认读取这个环境变量，并默认使用：

```json
"model": "gemini-3.1-pro-preview"
```

如果 Google 后续调整预览模型 ID，只需要改这个配置文件，不用改业务代码。

## 运行步骤

确认 Unity 已打开目标 Avatar 场景，并且 `MCP for Unity` 服务已经启动后，执行：

```bash
python vrchat_blendshape_agent.py "把眼睛睁大，嘴角上扬"
```

脚本执行流程：

1. 调用 `vrc_export_blendshapes`
2. 读取 `Assets/VRCAutoRig/blendshapes_export.json`
3. 请求 Gemini 生成调参计划 JSON
4. 生成 `RoslynExecutor.SetBlendshapeWeight(...)` 代码
5. 调用 `vrc_execute_roslyn`
6. Unity 主线程实际修改 blendshape 权重并保存

## 常用调试方式

### 只看 Gemini 计划

```bash
python vrchat_blendshape_agent.py "做一个轻微微笑" --print-plan --dry-run
```

### 只看生成的 C# 代码

```bash
python vrchat_blendshape_agent.py "眉毛抬高一点" --dry-run
```

## 输出 JSON 说明

导出的 `blendshapes_export.json` 里，核心字段包括：

- `avatars[].avatarPath`
- `avatars[].renderers[].rendererPath`
- `avatars[].renderers[].blendshapes[].name`

Python 侧只允许 Gemini 选择这些真实存在的值，这样可以尽量避免“模型自己编造 blendshape 名”。

## 后续替换成 DeepSeek 的位置

后续如果要换模型，不需要重写整套流程，主要改这里：

- `vrchat_blendshape_agent.py` 里的 `create_blendshape_plan`

现在 Gemini 调用已经被单独封装，替换成 DeepSeek 时只要保留输入输出 JSON 结构一致即可。

## 已知限制

- 2 秒超时是安全护栏，不是强制中断不安全代码的沙箱
- `Write Defaults` 统一 ON 会修改当前场景里挂载的 `AnimatorController`
- 如果场景里有多个名字非常接近的 blendshape，建议先用 `--print-plan` 看 Gemini 的匹配结果
- 这套脚本默认修改当前场景对象的 blendshape 当前权重，不会自动创建动画文件

## 验证建议

最小验证链路：

1. 打开 Unity 和目标 Avatar 场景
2. 启动 MCP for Unity
3. 手动从 Unity 菜单执行 `VRCAutoRig/Export Blendshapes`
4. 确认 `Assets/VRCAutoRig/blendshapes_export.json` 生成
5. 运行：

```bash
python vrchat_blendshape_agent.py "把眼睛睁大，嘴角上扬"
```

6. 回到 Unity 检查对应 `SkinnedMeshRenderer` 的 blendshape 权重是否变化
