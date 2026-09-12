---
name: vrcforge-avatar-wardrobe
title: VRChat 衣柜制作
description: Recognize existing VRChat wardrobe logic through a bounded read/analyze/judge/exit loop without assuming AnyState, parameter names or types; preserve working topology, then author or repair the user-approved wardrobe with verified clips, morphs, menus and runtime evidence. Use for wardrobe recognition or whole wardrobe closure, not isolated menu/clip edits, unproven paid-asset extraction, FT2 hair migration or automatic physics rewrites.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_list_execution_targets
  - vrcforge_bind_execution_target
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_blendshapes
  - vrcforge_scan_wardrobe
  - vrcforge_get_runtime_observation
  - vrcforge_start_runtime_observation
  - vrcforge_set_play_mode
  - vrcforge_scan_modular_avatar
  - vrcforge_inspect_modular_avatar_component
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_inspect_skinned_mesh_deformation
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_preview_setup_outfit
  - vrcforge_setup_outfit
  - vrcforge_preview_add_outfit
  - vrcforge_add_outfit
  - vrcforge_preview_add_wardrobe_outfit
  - vrcforge_add_wardrobe_outfit
  - vrcforge_preview_manage_wardrobe
  - vrcforge_manage_wardrobe
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_preview_manage_expression_parameters
  - vrcforge_manage_expression_parameters
  - vrcforge_preview_ensure_animator_state
  - vrcforge_ensure_animator_state
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_set_property
  - vrcforge_set_gameobject_active
  - vrcforge_delete_gameobject
  - vrcforge_gesture_manager_status
  - vrcforge_gesture_manager_enter_play_mode
  - vrcforge_gesture_manager_set_parameter
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/wardrobe-authoring.json
  - references/workflow.md
---

# 完整衣柜制作

此 Skill 独立覆盖服装挂载、固定值衣柜参数、FX、完整互斥动画、证据驱动的形态补偿、菜单、替换清理与逐套动态验收。它不是“只新增一套”的快捷流程。

## 什么时候使用

用户明确要求识别现有衣柜逻辑，或创建、扩展、修复整个换装闭环时使用。开始后先固定用户选择的 Unity 工程、场景和 Avatar；**每一次领域工具调用都必须携带同一个绝对 `projectPath`**。从 Descriptor 回读真正绑定的 FX、Parameters 和 Menu，不以文件名或场景猜测替代证据。

## 什么时候不使用

只改一个菜单控件或一条 AnimationClip 曲线时不使用；不要迁移 FT2 头发内容、提取未授权资产、自动重做作者 PhysBone/constraint/collider，或修改用户已经调好的物理系统。当前 Manuka 项目中的 Marshmallow PB 2.x 是明确禁区。不要为了省调用跳过完整矩阵和逐套验收。

## 先识别现有结构，再决定是否 author

衣柜识别与“经用户批准的新建 Int 衣柜方案”是两个阶段。readiness 顺序固定为：先调用绑定入口，复用其 `structuredContent.result.executionTarget` 完整 JSON；再用 `resources.identityLockUri` 与 `operationReceiptUri` 读取身份锁和操作回执，并用 `prompts/get(identityLockUri, sessionContextUri)` 完成 prompt readiness。只有实际 `context.status == ready_for_planning` 才进入领域读取；不得手抄 hash，也不得丢弃 target 或 provenance。主入口先回读 Descriptor，再调用 `scan_fx_animator`；按实际 controller、layer、完整 state-machine 路径与条件/clip 建立有限待读队列。`scan_wardrobe` 只作为可选模式线索，不是识别入口或结论，逐层读取 Entry/Exit、AnyState、普通出边、AND 条件、子状态机、BlendTree、driver 与跨层依赖，再读取实际 clip 绑定和可见效果。每次读取绑定同一 `projectPath`、ExecutionTarget、候选路径和资源 hash；按分页返回并核对完整性。

循环明确经过 `enter → read → analyze → judge → exit → next`：进入候选时绑定身份与目标，窄读当前页，分析条件/依赖/clip/效果，输出 `confirmed`、`rejected` 或带原因的 `undetermined`，退出时保存局部证据，再取独立 page cursor 继续未访问候选。visited key 必须包含身份 scope、controller、layer、完整 state-machine 路径和资源 hash；分页 cursor 只表示读取位置，不是节点身份。遇到环、分页不完整或证据缺失时保留 `undetermined` 并继续其他候选；一次局部未识别不能推出全局没有衣柜。若某条读取路线被拒绝，改用允许的只读入口，同时保留同一 target 和 provenance。FX 返回缺 Entry/Exit、BlendTree 树或 driver 事实时，只标这些类别未决并继续其它层，不宣称所有读取工具不可用。预算或实际读取能力耗尽后保存队列与证据并报告 capability gap。AnyState 只是扫描线索，`scan_wardrobe` 的 loose-control 结果不能单独否定其他状态机结构。识别阶段只使用现有只读工具，不改变权限、provenance、拓扑或资产；只有识别完成且用户明确批准，才进入下面的新建/修复 authoring 契约。

## 不可放宽的衣柜契约

以下 authoring 契约仅适用于用户批准的制作或修复方案；纯识别任务输出证据和未决项后结束，不自动进入写入。

1. 仅在经批准的固定值新建或修复 authoring 方案中，选择器才约束为 saved、synced 的 `衣柜 : Int`、默认值 `0`；每个菜单服装按钮写固定值。固定值由用户批准的映射表决定，禁止省略 `value` 让工具自动 `max+1`。识别既有结构时保留已验证的 Bool、Float、BlendTree 或其他参数拓扑，不得隐式迁移成 Int。
2. 先分清三种情况：已经验收的现有拓扑逐字段保留回读的 source、destination、conditions、duration、exitTime、Write Defaults 与当前进度；新建基础衣柜才使用 AnyState、`衣柜 Equals N`、`hasExitTime=false`、`duration=0` 的瞬时默认；用户确认的动画序列可保留当前进度并制作可选反向序列。来源 FT2 中已验证可工作的无条件 AnyState 基线不是缺陷，不得为了理论规范化改成 Idle/Base。只修有回读和运行证据会冲突的额外层、状态或转换。
3. 先回读每个已批准值的稳定态和过渡态 clip；稳定态必须具备并验证完整互斥矩阵，已有正确曲线保留源时间线，只对有明确证据差异的 binding 做 preview/write 修补。仅新建基础衣柜的瞬时稳定态可用 `time=0` 写入矩阵：当前套装根开启、其他所有套装根关闭，并写该套设计的默认鞋、袜、内衣、领带、手环等。不能依赖 Write Defaults、场景默认状态或上一套状态补齐。过渡 clip 必须保留源时间线，允许旧新衣服在交融区同时存在；中途改选时从当前进度反向溶解旧套，再进入最新选择，不能套用新建基础衣柜的 `time=0` 互斥写入。材质/属性曲线按 renderer-wide property block 的 rendererPath、component、material slot 与 sharedMaterial 身份核对；同一 Renderer 的属性只写一条，不按每个 slot 重复生成。
4. 先检查每套衣服现有/候选动画是否已经带形态键，再扫描当前 Avatar 身体 Renderer、套装 Renderer 和全部相关形态键。以当前身体基线和该服装的适配能力为准，成对写入必要的 body/clothing `reset/apply` 曲线：支持当前基线的衣服维持或恢复基线；不支持的衣服只在该套中调整身体/衣服形态，切换到其他套时恢复。依据套装风格、鞋跟高度、作者说明与实际穿模证据定值，不硬编码 100 或固定名称。`Breast_big`、`Breast_big_PLUS`、`Foot_heel`、`Foot_heel_high` 仅是 Manuka 项目示例。
5. 先检测套件中依附头部、脸部或颈部的对象。只有发生接头/换头、当前头骨或头表面与套件原适配目标不一致，或视觉证据显示偏移/穿模时，才针对当前实际头部骨架和表面重新定位并验证动态继承；正常完整模型和已正确适配的衣服保留原配置。需要调整时只动导入对象或安装容器，不缩放身体。Sapphy Head 仅是当前目标示例。
6. 菜单遵循用户批准的现有根结构。当前目标根仅保留 `面捕` 与 `原模型菜单`，换装放到 `原模型菜单` 下并按 `衣服 / 头发 / 配饰` 组织；保留原模型内容，不迁移 FT2 头发。
7. 一次只处理一套。该套的菜单、参数、FX、动画、形态键、静态和动态回读全部通过，才允许进入下一套。运行观察必须保留完整 frame/step 身份、stateSetHash、状态行计数和 Resource page 链；按需选层时还必须回读 selected identity 与 source/selected counts，不能把省 token 当成省状态。

运行控制属于执行步骤：先取得具体运行方案的批准、记录原Play状态，并用Gesture Manager状态确认真实连接就绪。用`vrcforge_start_runtime_observation`按有限duration/frame预算启动一次，保留完整目标身份与返回jobId；pending时只用`vrcforge_get_runtime_observation`读取原job，不能因等待重启。completed只代表观察结束，仍需核对采样覆盖、参数输入、状态/图像和实际效果。完成或失败后按用户批准的目标状态调用`vrcforge_set_play_mode`并独立回读；用户已有的Play会话不能被默认退出。仅识别衣柜或读取历史job时，不进入此执行步骤。

## 替换与清理

定点复用值（例如当前目标的 `value=3`）时，先预览并移除旧值的菜单/FX 绑定但保留旧对象与资产，再用 `vrcforge_add_wardrobe_outfit` 明确传入同一固定值；不要走会自动分配 `max+1` 的准备式 Add Outfit 衣柜分支。旧 FX 层按精确层名逐层预览、删除、全量回读。旧场景实例先扫描完整且未截断的入向引用闭包，禁用并完成新套动态验收后，才可在单独批准下按精确对象路径删除；任何引用未闭合都停止。

完整调用顺序、阻断条件与逐套验收见 [衣柜制作说明](references/workflow.md) 和受签名保护的 [衣柜工作流契约](workflows/wardrobe-authoring.json)。所有写入仍经过当前权限策略、精确 preview、checkpoint、写后回读；回滚需要单独确认。
