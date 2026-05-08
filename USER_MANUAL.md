# VRCAutoRig 功能作用与使用手册

这份手册写给实际使用 dashboard 的人。它不要求你会写代码，只需要你知道 Unity、VRChat Avatar、Blendshape、FX、参数这些基础概念。

核心目标很简单：让你知道每个区域有什么功能、它的作用是什么、什么时候该用、用的时候要注意什么。

## 一句话说明

VRCAutoRig 是一套本地 VRChat Avatar 自动化控制台。它把 Unity 工程、Unity MCP、LLM API、Blendshape 调整、衣柜 FX、参数优化和视觉质检集中到浏览器里的一个 dashboard 页面中。

默认 dashboard 地址：

```text
http://127.0.0.1:8757
```

默认 Unity MCP 地址：

```text
127.0.0.1:8080
```

## 系统由哪些部分组成

| 部分 | 作用 | 普通用户需要做什么 |
| --- | --- | --- |
| Unity Editor | 打开真实 VRChat Avatar 工程，承载模型和资产 | 保持工程打开，确认没有红色编译错误 |
| MCP server | 让 dashboard 能和 Unity 通信 | 确认服务已启动，dashboard 顶部显示已连接 |
| Dashboard | 浏览器里的中文控制台 | 主要操作都在这里完成 |
| Provider API | 让 AI 根据文字生成调整计划，或做视觉审核 | 填 API Key，读取模型列表，保存配置 |
| Unity 插件脚本 | 在 Unity 内导出数据、执行 C#、连接 MCP | 不需要手动改代码 |
| Artifacts | 保存计划、截图、结果、快照等产物 | 出问题时可发给开发者排查 |

## 使用前检查

开始点功能前，先确认这几件事：

1. Unity 工程已经打开。
2. Unity 没有停在 Safe Mode。
3. Unity Console 没有红色编译错误。
4. dashboard 页面能打开。
5. dashboard 顶部 `Unity MCP` 显示已连接。
6. 当前工程、当前 Avatar、当前 Provider 和 Model 显示正确。

如果上面任何一项不对，先不要点写入类功能。

## 功能总览

| 功能区 | 它的作用 | 常见用途 | 风险等级 |
| --- | --- | --- | --- |
| 状态栏 | 显示 Unity、Provider、Avatar、Socket 的当前状态 | 判断系统是否连通 | 只读 |
| 工程管理 | 选择、打开、同步 Unity 工程 | 切换测试工程，检查 MCP 连接 | 低 |
| API 配置 | 配置 AI provider、API Key、模型 | 让 AI 规划和视觉审核可用 | 中 |
| 数据源与执行模式 | 选择从 Unity 实时导出、样例数据或本地 JSON 运行 | 切换真测或离线测试 | 中 |
| 自然语言 + Blendshape | 手动或 AI 调整 Avatar Blendshape | 捏脸、调表情、验证写入 | 中 |
| 衣柜 FX | 扫描衣服对象，生成衣柜开关 FX | 自动创建衣服开关动画和菜单 | 高 |
| 参数优化 | 扫描表达参数并给出优化建议 | 节省 Avatar 参数占用 | 高 |
| 视觉质检 | Unity 截图并交给 Gemini Vision 审核 | 检查穿模和外观问题 | 中 |
| 操作日志 | 记录每次操作和错误 | 报错排查、复现问题 | 只读 |

## 状态栏

状态栏在页面顶部，用来回答一个问题：现在系统能不能工作。

你会看到：

- `Unity MCP`：是否已经连上 Unity。
- `当前 Provider`：现在使用哪个 AI 服务，例如 Gemini、DeepSeek、OpenAI。
- `当前 Model`：现在实际使用的模型名称。
- `当前 Avatar`：当前选中的 Avatar。
- `Socket`：浏览器与 dashboard 后端是否在线。

正常状态：

- `Unity MCP` 显示已连接。
- `Socket` 显示 Live。
- Provider 和 Model 不为空。
- 加载 Avatar 后，当前 Avatar 不再显示未加载。

如果 `Unity MCP` 未连接，Avatar 扫描、Blendshape 加载、截图、写入 Unity 等功能大概率不可用。

## 工程管理

工程管理负责选择和打开 Unity 工程。

主要控件：

| 控件 | 作用 |
| --- | --- |
| `Unity 工程` 下拉框 | 从本机可识别的 Unity 工程里选择一个 |
| `刷新工程列表` | 重新扫描本机工程列表 |
| `一键打开工程` | 使用配置里的 Unity Editor 打开选中的工程 |
| `场景 Avatar 列表` | 显示当前 Unity 场景里的 Avatar |
| `刷新 Avatar 列表` | 让 Unity 扫描场景里的 `VRCAvatarDescriptor` |
| `加载 Blendshape` | 读取选中 Avatar 的 Blendshape 数据 |
| `检查连接` | 检查 dashboard 能否连上 MCP server 和 Unity |
| `工具列表` | 查看当前 Unity MCP 暴露了哪些工具 |

推荐流程：

1. 选择工程，例如 `manuka FT2` 或 `Sapphy R18`。
2. 点 `一键打开工程`。
3. 等 Unity 打开并编译完成。
4. 确认 MCP 已连接。
5. 点 `刷新 Avatar 列表`。
6. 选中目标 Avatar。
7. 点 `加载 Blendshape`。

如果 Avatar 列表为空，通常是 Unity 还没编译完、场景里没有 Avatar、Avatar 没有 `VRCAvatarDescriptor`，或者 MCP 没连上。

## API 配置

API 配置决定 AI 功能使用哪个服务、哪个模型。

主要控件：

| 控件 | 作用 |
| --- | --- |
| `Provider` | 选择 AI 服务，例如 Gemini、DeepSeek、OpenAI、OpenRouter、Anthropic、自定义 |
| `Model 名称` | 从 API 读取到的模型列表里选择模型 |
| `读取模型列表` | 用当前 API Key 和 Base URL 请求 provider，读取可用模型 |
| `手动 Model 名称` | 读取失败或需要列表外模型时手动填写 |
| `API Key` | 填 provider 的密钥 |
| `Base URL` | 填 OpenAI-compatible 接口地址；Anthropic 不显示 |
| `恢复默认值` | 把当前 provider 的 Base URL 和默认模型恢复成内置值 |
| `保存并生效` | 写入本地 `config.json`，并立即热更新后端配置 |

推荐流程：

1. 选择 Provider。
2. 填 API Key。
3. 确认 Base URL。
4. 点 `读取模型列表`。
5. 在 `Model 名称` 下拉框里选择模型。
6. 点 `保存并生效`。

说明：

- `config.json` 是本地配置文件，已经被 `.gitignore` 忽略，不应该提交真实密钥。
- Gemini、DeepSeek、OpenAI、OpenRouter、自定义 provider 走 OpenAI-compatible 接口。
- Anthropic 走官方 SDK，不使用 Base URL。
- 如果模型列表读取失败，页面会切换到手动填写模式。

## 数据源与执行模式

这个区域决定数据从哪里来、执行到哪里去。

主要选项：

| 选项 | 作用 | 适合什么时候用 |
| --- | --- | --- |
| `Unity 实时导出` | 从当前 Unity 工程直接读取 Avatar 数据 | 真实工程测试 |
| `配置文件导出路径` | 使用配置中指定的导出 JSON | 半离线测试 |
| `自定义导出 JSON` | 手动填一个导出 JSON 文件路径 | 排查特定数据 |
| `MVP 示例` | 使用仓库里的样例数据 | 不打开 Unity 也能演示 |

其他控件：

| 控件 | 作用 |
| --- | --- |
| `Local Plan JSON` | 手动指定一份已有计划，不走 AI 规划 |
| `Min Confidence` | AI 计划最低置信度，低于该值会被拦截 |
| `Mock 模式` | 只模拟执行，不真正写 Unity |
| `允许低置信度` | 允许低置信度 AI 计划继续执行 |
| `保存产物` | 保存计划、C#、执行结果等文件到 `artifacts/` |

建议：

- 真测 Unity 时使用 `Unity 实时导出`。
- 不确定 AI 输出是否稳定时，先开 `Mock 模式`。
- 不要随便打开 `允许低置信度`，除非你正在调试。

## 自然语言 + Blendshape 手调

这是核心捏脸区域，负责读取、显示、调整和应用 Blendshape。

主要功能：

| 功能 | 作用 |
| --- | --- |
| 自然语言指令 | 输入你希望 Avatar 变成什么样 |
| 参考图片 | 上传本地图片、填写图片路径，或使用最新截图作为捏脸参考 |
| AI 生成并执行 | 让 AI 根据文字生成 Blendshape 调整计划并应用 |
| 应用滑块改动 | 把手动滑块改动写到 Unity 当前对象 |
| 撤销上一步 | 回退上一次手动或自动应用 |
| Blendshape 搜索 | 按名字、Renderer、Mesh 搜索 Blendshape |
| Blendshape 滑块 | 手动调整单个 Blendshape 权重 |
| LLM 改动明细 | 显示 AI 计划具体改了哪些 Blendshape、从多少改到多少、为什么改 |

手动调整流程：

1. 先刷新 Avatar 列表。
2. 选择 Avatar。
3. 加载 Blendshape。
4. 搜索一个安全的 Blendshape，例如 smile、eye、mouth。
5. 小幅拖动滑块。
6. 点 `应用滑块改动`。
7. 回 Unity 看模型是否变化。
8. 点 `撤销上一步` 验证回退是否正常。

AI 执行流程：

1. 确认 Provider、API Key、Model 已保存。
2. 确认已经加载 Blendshape。
3. 输入自然语言，例如：

```text
轻微微笑，嘴角上扬一点，眼睛稍微睁大
```

4. 如果想参考某张脸，可以在参考图片处上传图片，或先截图再点 `用最新截图`。
5. 点 `AI 生成并执行`。
6. 看 summary、LLM 改动明细、日志和 Unity 模型变化。
7. 不满意就撤销；想继续微调时，在上一轮结果基础上继续输入新的自然语言。

建议第一次只使用“轻微”“一点”“稍微”这类保守描述。
参考图片功能目前优先给 Gemini 使用；图片会转成捏脸方向提示，再和自然语言一起送进 Blendshape 规划。

## 衣柜 FX

衣柜 FX 用来扫描场景里的衣服对象，并生成 VRChat 衣柜开关相关资产。

它可能生成或修改：

- `_ON.anim` / `_OFF.anim` 动画。
- FX Layer。
- AnyState 跳转。
- Bool 参数。
- 表情菜单 Toggle。
- `Assets/VRCAutoRig/Generated/FX/` 下的生成资产。

主要控件：

| 控件 | 作用 |
| --- | --- |
| `扫描场景衣服` | 找出可能是衣服、配件、部件的 GameObject |
| `生成 FX Blueprint` | 生成衣柜开关方案预览 |
| `写入 FX 资产` | 把方案写入 Unity 资产 |
| `Dry-run` | 只预览 C# 和方案，不真正写资产 |

推荐流程：

1. 点 `扫描场景衣服`。
2. 检查识别出的对象是否真的是衣服或配件。
3. 点 `生成 FX Blueprint`。
4. 保持 `Dry-run` 打开。
5. 点 `写入 FX 资产` 看 C# 预览。
6. 确认没问题后，再关闭 `Dry-run` 做真实写入。

风险提示：

- 这是高风险功能，因为它会写 Unity 资产。
- 第一次测试时建议只 dry-run。
- 写入前最好确认工程有备份或版本管理。

## 参数优化

参数优化用来查看 Avatar 的表达参数占用，并给出减少参数成本的建议。

当前主要能力是把部分 `Int` 参数建议改成 `Bool`，用于节省表达参数容量。

主要控件：

| 控件 | 作用 |
| --- | --- |
| `扫描参数用量` | 读取 Bool、Int、Float 参数数量和占用 |
| `一键优化建议` | 根据规则生成优化建议 |
| `应用建议` | 把选中的优化写回参数资产 |
| `回滚参数` | 从快照恢复参数 |
| `Dry-run` | 只看 diff 和 C#，不真正写回 |

推荐流程：

1. 点 `扫描参数用量`。
2. 点 `一键优化建议`。
3. 查看建议是否合理。
4. 保持 `Dry-run` 打开。
5. 点 `应用建议` 查看 diff 和 C#。
6. 确认后再关闭 `Dry-run` 真正写回。
7. 写回后如果不对，使用 `回滚参数`。

风险提示：

- 这是高风险功能，因为它会修改 `VRCExpressionParameters`。
- 参数类型变化可能影响 Animator 条件、菜单和默认值。
- 真正写回前必须先看 dry-run diff。

## 视觉质检

视觉质检用于让 Unity 截图，再让 Gemini Vision 判断外观是否有问题，例如穿模。

主要控件：

| 控件 | 作用 |
| --- | --- |
| `捕获截图` | 从 Unity SceneView 获取单张截图 |
| `Gemini Vision 审核` | 对当前截图做 AI 视觉审核 |
| `多视角截图` | 从正面、侧面、背面等角度截图 |
| `多图聚合审核` | 对多张图一起审核，得到整体结论 |
| 视角 Tab | 切换查看单视角、正面、左侧、右侧、背面 |

推荐流程：

1. 确认 Provider 是 Gemini。
2. 确认 API Key 和模型配置可用。
3. 点 `捕获截图`。
4. 看截图是否正常显示。
5. 点 `Gemini Vision 审核`。
6. 如果需要更稳，再点 `多视角截图` 和 `多图聚合审核`。

结果会显示：

- `通过` 或 `穿模风险`。
- 问题摘要。
- 可能的风险区域标注。
- 保存到 `artifacts/dashboard/latest/` 的截图和 JSON。

风险提示：

- 截图本身不会改 Unity 工程。
- 审核依赖 Gemini provider。
- 如果 SceneView 角度不对，审核结果也可能不准。

## 操作日志

操作日志记录 dashboard 最近做了什么。

它的作用：

- 看每一步有没有成功。
- 查 provider 请求失败原因。
- 查 Unity MCP 连接失败原因。
- 查 Roslyn 编译或执行错误。
- 给开发者复现问题。

报错时优先看：

- 红色错误日志。
- `scope` 是 config、unity、pipeline、vision 还是 dashboard。
- 错误里的 data JSON。

## 哪些操作会真的改 Unity 工程

| 操作 | 会不会改工程 | 建议 |
| --- | --- | --- |
| 刷新工程列表 | 不会 | 可以放心 |
| 检查连接 | 不会 | 可以放心 |
| 刷新 Avatar 列表 | 不会 | 可以放心 |
| 加载 Blendshape | 通常不会 | 可以放心 |
| 手动应用 Blendshape | 会改当前场景对象状态 | 小幅测试，确认撤销 |
| AI 生成并执行 | 会改当前场景对象状态 | 第一次保守描述，必要时开 Mock |
| 衣柜 FX dry-run | 不会写资产 | 推荐先用 |
| 衣柜 FX 真实写入 | 会写资产 | 高风险，确认后再做 |
| 参数优化 dry-run | 不会写资产 | 推荐先用 |
| 参数优化真实写入 | 会改参数资产 | 高风险，确认后再做 |
| 回滚参数 | 会改参数资产 | 只在需要恢复时使用 |
| 截图 | 不会 | 可以放心 |
| 视觉审核 | 不会 | 依赖 API |

## 推荐的第一次完整测试流程

这条流程尽量安全，适合第一次验证系统是否跑通。

1. 打开 dashboard。
2. 选择当前 Unity 工程。
3. 确认 Unity MCP 已连接。
4. 刷新 Avatar 列表。
5. 选择目标 Avatar。
6. 加载 Blendshape。
7. 搜索一个安全 Blendshape。
8. 小幅拖动滑块。
9. 应用滑块改动。
10. 在 Unity 里确认模型变化。
11. 撤销上一步。
12. 再测试一次 AI 轻微调整。
13. 捕获一张截图。
14. 检查操作日志里是否有错误。

这条流程跑通后，说明核心链路基本正常：dashboard、Unity、MCP、Avatar 扫描、Blendshape 读取、执行和撤销都可用。

## 常见问题

### dashboard 打不开

确认地址是：

```text
http://127.0.0.1:8757
```

如果打不开，需要重新启动 dashboard。

### Unity MCP 未连接

检查：

- Unity 工程是否打开。
- Unity 是否编译完成。
- Unity Console 是否有红色错误。
- MCP server 是否运行在 `127.0.0.1:8080`。
- Unity 内 MCP Bridge 是否启动。

### 刷新不到 Avatar

常见原因：

- 当前场景没有 Avatar。
- Avatar 没有 `VRCAvatarDescriptor`。
- dashboard 连到了错误的 Unity 实例。
- Unity 正在编译或有红色错误。

### 加载不到 Blendshape

常见原因：

- 没有先选择 Avatar。
- Avatar 的 mesh 没有 Blendshape。
- Blendshape 名称不是你搜索的英文。
- Unity 导出失败。

### AI 执行失败

常见原因：

- API Key 没填。
- Provider 或 Base URL 不对。
- 模型名称不对。
- 模型列表读取失败后没有手动填写 model。
- 低置信度被拦截。

### 截图或视觉审核失败

常见原因：

- Unity SceneView 不可用。
- Provider 不是 Gemini。
- Gemini API Key 或模型不可用。
- 截图路径不存在。

## 报错反馈模板

出问题时，把下面信息发给开发者最有用：

```text
工程：
Unity 状态：
dashboard 顶部 Unity MCP 状态：
当前 Provider / Model：
当前 Avatar：
我点了哪个按钮：
我输入了什么：
dashboard 错误提示：
操作日志里的红色错误：
Unity Console 红色错误：
预期结果：
实际结果：
是否开了 dry-run：
是否能复现：
```

如果可以，再附两张截图：

- dashboard 整页截图。
- Unity Console 截图。

## 使用原则

- 先只读，再临时调整，最后才写资产。
- 第一次测试永远小幅度。
- 不确定时保持 `Dry-run`。
- 看到 Unity 红色错误时暂停操作。
- Provider 配置改完要保存。
- 出错时不要连续乱点，先看操作日志。
