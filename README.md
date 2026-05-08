# VRCFaceForge

VRCFaceForge 是这个项目准备推到 GitHub 时使用的名字。它是一套面向 VRChat Avatar 的本地捏脸、Blendshape、衣柜 FX、参数优化和识图分析控制台。

这套项目的目标是把 Unity 自定义工具、LLM 语义规划、Roslyn 动态执行、Unity MCP 通信和本地控制台串成一条完整链路，用自然语言或手动滑块去调整 VRChat SDK3 Avatar 的 Blendshape，并逐步扩展到衣柜 FX、参数优化和视觉质检。

项目现状已经不只是骨架：
- 命令行 MVP 可运行
- LLM provider 可热切换，支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 和自定义 OpenAI-compatible endpoint
- Unity MCP 已接入
- 零编译 dashboard 已经成型
- 一键启动脚本已可用
- 衣柜 FX 资产写入已接到 dashboard
- 参数优化 diff 预览与应用已接到 dashboard
- 多视角截图与多图识图分析已接到 dashboard

更详细的完成度、差距和后续建议见 [PROJECT_STATUS.md](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/PROJECT_STATUS.md)。

给非技术测试人员看的操作说明见 [NON_TECHNICAL_TEST_GUIDE.md](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/NON_TECHNICAL_TEST_GUIDE.md)。这份文档说明了工具往 Unity 工程里写了什么、每块功能有什么用、哪些按钮会真正改工程、以及出问题时该反馈哪些信息。
按功能区阅读的正式使用手册见 [USER_MANUAL.md](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/USER_MANUAL.md)。这份文档按“功能、作用、怎么用、注意事项”说明 dashboard 每个区域。

## 目录结构

- `Assets/VRCAutoRig/Editor/BlendshapeExporter.cs`
- `Assets/VRCAutoRig/Editor/RoslynExecutor.cs`
- `vrchat_blendshape_agent.py`
- `dashboard_server.py`
- `dashboard/index.html`
- `dashboard/styles.css`
- `dashboard/app.js`
- `tools/unity-mcp-cli.ps1`
- `tools/install-unity-project.ps1`
- `tools/start-dashboard.ps1`
- `start_dashboard.cmd`
- `.gemini/settings.json`
- `examples/mvp_blendshapes_export.json`
- `examples/mvp_plan_smile.json`
- `tests/test_vrchat_blendshape_agent.py`
- `tests/test_dashboard_server.py`
- `NON_TECHNICAL_TEST_GUIDE.md`
- `USER_MANUAL.md`

## 当前能力

### 1. Unity 侧工具

`BlendshapeExporter.cs`
- 读取场景中的 `SkinnedMeshRenderer`
- 识别 Avatar 根节点
- 导出 Blendshape 名称、路径、当前权重到 JSON

`RoslynExecutor.cs`
- 接收 C# 代码字符串
- 在 Unity 主线程执行
- 自动注入 Unity / VRChat 相关程序集引用
- 支持 2 秒超时保护
- 支持按目标 Avatar 范围处理 `Write Defaults`

### 2. Python 代理

[vrchat_blendshape_agent.py](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/vrchat_blendshape_agent.py) 现在支持：
- Unity 实时导出
- 本地导出 JSON 输入
- 本地计划 JSON 输入
- LLM 实时规划
- 多 Avatar 选择
- Unity MCP 状态诊断
- `host / port / instance` 定向连接
- 计划校验、低置信度拦截、重复调整去重
- 生成 Roslyn C# 代码
- mock 执行和真实 Unity 执行

### 3. Dashboard

[dashboard_server.py](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/dashboard_server.py) + [dashboard/index.html](D:/Codex/vrchat-avatar-blendshape-gemini-api-unity/dashboard/index.html) 组成一套零编译控制台，特点是：
- FastAPI + WebSocket
- 前端无 React / Vite / 构建链
- 深色中文 UI
- 实时状态推送，不靠前端轮询
- 一键启动

当前 dashboard 已包含这些模块：

#### 状态栏
- Unity MCP 连接状态红绿灯
- 当前 provider / model
- 当前加载 Avatar 名称
- Socket 在线状态

#### 工程管理
- Unity 工程列表下拉选择
- 一键打开工程
- 刷新场景 Avatar 列表
- 加载当前 Avatar 的 Blendshape 列表

#### 捏脸模块
- 自然语言指令输入
- Blendshape 列表展示
- 本地搜索
- 滑块实时预览
- 手动应用
- 撤销上一步
- AI 规划并执行

#### 衣柜 FX 模块
- 扫描场景衣服对象
- 显示开关状态
- 单件开关切换
- 生成 FX blueprint
- `Dry-run` 生成 C# 预览
- 写入 FX 资产到 Unity

说明：
当前后端已经支持通过 `/api/clothes/apply-fx` 生成并执行 C#，写入：
- `AnimationClip` 开关动画
- FX Layer 状态机结构
- `VRCExpressionParameters` Bool 参数
- `VRCExpressionsMenu` Toggle 菜单项

默认 `dry_run=true`，建议先预览生成代码，再决定是否真实写入。

#### 参数优化模块
- 扫描 Bool / Int / Float 用量
- 显示参数列表
- 生成 Int -> Bool 的启发式优化建议
- `Dry-run` 生成 diff + C# 预览
- 应用建议到 `VRCExpressionParameters`

说明：
当前后端已经支持通过 `/api/parameters/apply-optimization` 把选中的 Int 参数改写成 Bool，并返回变更 diff 预览。
默认 `dry_run=true`，建议先确认 diff 再执行。

#### 视觉质检模块
- SceneView 截图
- 截图预览
- 识图分析
- 返回 `pass / clipping` 结果
- 多视角截图：`front / side_left / side_right / back`
- 多图聚合分析

说明：
识图分析当前要求 dashboard 的 provider 设为 `Google AI Studio`。

#### 操作日志
- WebSocket 实时滚动 log
- 时间戳
- scope / level 颜色区分
- 错误红色高亮
- 一键清空前端日志

## Dashboard 启动

### 一键启动

双击：

```text
start_dashboard.cmd
```

或：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start-dashboard.ps1
```

默认地址：

```text
http://127.0.0.1:8757
```

### 自检

```powershell
powershell -ExecutionPolicy Bypass -File tools/start-dashboard.ps1 -CheckOnly
```

这个脚本会：
- 查找 Python
- 检查 `fastapi / uvicorn / google-genai / openai / anthropic / pydantic`
- 缺依赖时自动 `pip install -r requirements.txt`

## Dashboard 使用流程

### 基础流程

1. 启动 dashboard
2. 在“工程管理”里选择 Unity 工程
3. 点击“打开工程”
4. 在 Unity 中启动 MCP Server
5. 回到 dashboard，看 Unity MCP 状态变成已连接
6. 点击“刷新 Avatar 列表”
7. 选择目标 Avatar
8. 点击“加载 Blendshape”
9. 选择手动滑块调整，或直接输入自然语言后点击“AI 生成并执行”

### 高级模块流程

衣柜 FX：
1. 先扫描衣服对象
2. 检查开关状态和生成结果
3. 保持 `Dry-run` 打开，先看 C# 预览
4. 确认后再点击“写入 FX 资产”

参数优化：
1. 先扫描参数占用
2. 查看 Int -> Bool 建议
3. 保持 `Dry-run` 打开，先看 diff 和 C# 预览
4. 确认后再点击“应用建议”

视觉质检：
1. 可以先做单图截图和单图识图分析
2. 需要更稳的检查时，使用“多视角截图”
3. 截图完成后，使用“多图聚合分析”查看整体结论

### Avatar 刷新逻辑

“刷新 Avatar 列表”不是前端假数据。

当前逻辑是：
- dashboard 调用后端接口 `/api/scene/avatars`
- 后端通过 Unity MCP 调用 Roslyn 执行代码
- Unity 中查找 `FindObjectsOfType<VRCAvatarDescriptor>()`
- 返回场景内 Avatar 名称、路径、场景名
- 前端把结果显示到下拉选择器

## Provider 配置

### 保存位置

dashboard 的 API 配置保存到仓库根目录本地文件：

```text
config.json
```

这个文件已加入 `.gitignore`，不会默认提交到仓库。

### 支持的 Provider

- `Google AI Studio`
- `DeepSeek`
- `OpenAI`
- `OpenRouter`
- `Anthropic`
- `Ollama`
- `Google Vertex AI`
- `自定义`

### 路由规则

- `Google AI Studio`
  - 使用 `google-genai` 官方 SDK
  - 使用 API Key
  - 不需要 Base URL

- `Anthropic`
  - 使用 Anthropic SDK
  - 走官方端点
  - 使用 `x-api-key`

- `Google Vertex AI`
  - 使用 `google-genai` 的 Vertex AI 模式
  - 使用本机 Google ADC / gcloud 认证
  - Project/Location 可在 dashboard 配置栏填写

- `Ollama`
  - 使用 OpenAI-compatible `/v1` 接口
  - 默认 `http://127.0.0.1:11434/v1`
  - API Key 可留空

- OpenAI-compatible provider
  - 统一走 OpenAI 兼容接口
  - 使用 `Base URL + Bearer token`

### 热更新

dashboard 里保存 provider 配置后：
- 会写入本地 `config.json`
- 会立即更新后端内存配置
- 会通过 WebSocket 推送给前端
- 不需要重启服务

## CLI 路线

### 本地 MVP

纯本地样例：

```bash
python vrchat_blendshape_agent.py --mvp --plan-json examples/mvp_plan_smile.json --save-plan artifacts/mvp/plan.json --save-csharp artifacts/mvp/apply.cs --save-result artifacts/mvp/result.json
```

LLM + mock：

```bash
python vrchat_blendshape_agent.py --mvp "把眼睛睁大，嘴角上扬" --print-plan --save-plan artifacts/mvp/plan.json --save-csharp artifacts/mvp/apply.cs --save-result artifacts/mvp/result.json
```

### Unity 诊断

```bash
python vrchat_blendshape_agent.py --unity-status
python vrchat_blendshape_agent.py --list-unity-instances
python vrchat_blendshape_agent.py --list-avatars
```

### 真实执行

```bash
python vrchat_blendshape_agent.py --avatar "YourAvatarRootPath" "把眼睛睁大，嘴角上扬"
```

## Unity 工程接入

如果你已经有 Unity 工程，可以直接安装：

```powershell
powershell -ExecutionPolicy Bypass -File tools/install-unity-project.ps1 `
  -ProjectPath "E:\unity\Projects\Karin FT Rework" `
  -UnityEditorPath "E:\unity\Unity 2022.3.22f1\Editor\Unity.exe" `
  -LaunchUnity
```

脚本会：
- 复制 `Assets/VRCAutoRig`
- 确保 `Packages/manifest.json` 包含 `com.coplaydev.unity-mcp`
- 可选直接打开 Unity

推荐 Unity Package Manager Git URL：

```text
https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main
```

## 配置文件

### `.gemini/settings.json`

这个文件负责：
- Unity MCP 命令与连接参数
- Blendshape 导出路径
- 最低置信度
- dashboard 工程根目录和 Unity Editor 路径

当前模板大致如下：

```json
{
  "llm": {
    "provider": "gemini",
    "api_key_env": "GEMINI_API_KEY",
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
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
  },
  "dashboard": {
    "project_roots": [
      "E:/unity/Projects"
    ],
    "unity_editor_path": "E:/unity/Unity 2022.3.22f1/Editor/Unity.exe",
    "status_push_interval_seconds": 2.5
  }
}
```

## 当前已知边界

- 真实 Unity 在线执行仍然依赖你在 Unity Editor 里启动 MCP Server
- 衣柜 FX 已支持写入资产，但当前生成逻辑仍带启发式，建议始终先 `Dry-run`
- 参数优化已支持改写 `VRCExpressionParameters`，但当前还没有回滚机制
- 参数优化当前主要覆盖参数资产本身，还没有做 Animator / Menu 的全量联动修复
- 识图分析目前使用 Google AI Studio provider
- dashboard 的截图抓取依赖当前可用的 SceneView
- `build_clothes_fx_apply_code` 当前依赖 `Newtonsoft.Json.Linq.JObject` 解析衣服数据；如果 Unity 工程里的 Newtonsoft 组件不完整，Roslyn 编译可能失败

## 验证

最近已经验证通过的内容：
- `python -m unittest discover -s tests -v` → 最近一次为 `33 tests OK`
- `powershell -ExecutionPolicy Bypass -File tools/start-dashboard.ps1 -CheckOnly`
- `node --check dashboard/app.js`

## 下一步建议

最自然的下一步有三条：

1. 在真实 Unity 工程里跑通 Avatar 刷新、Blendshape 加载和滑块应用
2. 在真实 Unity 工程里验收“写入 FX 资产”和“应用建议”两条写回链路
3. 给参数优化补上回滚机制，给视觉质检补上穿模位置可视标注
