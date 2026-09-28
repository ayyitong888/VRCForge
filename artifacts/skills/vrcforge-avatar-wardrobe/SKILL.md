---
name: vrcforge-avatar-wardrobe
title: VRChat 衣柜制作
description: Inspect or author avatar clothing behavior using actual project evidence and the user-approved design. Select only relevant tools; do not infer effects from names or impose a parameter, topology, activation pattern or menu layout.
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

# 衣柜检查与制作

## 什么时候使用

用户要求检查、创建、扩展或修复 Avatar 的换装行为时使用。已有结构使用证据驱动分支；明确从零新建时，使用参考中的默认制作方案。新建方案不是已有项目的标准答案。

## 什么时候不使用

与换装无关的编辑不使用。仅询问功能或讨论方案不触发工具；只读请求不得升级为制作、运行控制或修复。未经授权不得提取资产、改动作者组件或用户物理配置。

## 项目事实与用户意图

每次领域调用携带用户选择工程的绝对 `projectPath`，保持同一场景、Avatar、ExecutionTarget 和 provenance。复用绑定入口返回的完整身份，不手抄 hash；遵循实际接口的身份和 readiness 契约。

识别现有结构时，名称、菜单标签、参数类型或扫描命中都只是线索。Bool、Float、BlendTree 等结构按实际证据解释，不得隐式迁移成 Int。AnyState 只是扫描线索，不是必须存在的结构。

对已有衣柜增删改查时，参数、对象开关、拓扑、时间线和菜单均以实际证据及用户要求为准，不套用新建默认值。缺少会影响修改结果的事实时先询问，不用模板补齐。

只有用户明确要求从零制作，且确认目标范围不存在要保留的已有衣柜时，才采用 [从零新建默认方案](references/workflow.md#从零新建默认方案)。新建分支提供默认设计，不预设模型读取既有项目后应得出的答案。用户特别要求优先，执行前仍需具体 preview 和批准。目标范围不明时先确认；工具返回空、读取失败或尚未找到衣柜不等于不存在。

## 按问题取证

通过目录查看工具完整说明，加载当前所需的具名工具。工具名称和参数由实际目录提供，不凭空构造。以下为能力索引，不是固定调用顺序，也不要求每次调用全部工具：

- Avatar、Descriptor、对象及属性读取：定位实际引用与对象事实。
- 菜单和参数读取：获取控件、参数和值的配置关系。
- FX 与动画绑定读取：获取状态、条件、Motion、属性曲线与时间信息。
- 运行观察读取：获取指定会话已经采集的实际运行证据。
- 安装、菜单、参数、状态、曲线及对象修改：仅用于已批准且符合工具适用范围的变更。

选择能回答当前问题的证据，不把配置关系当作运行效果，不把局部样本当作完整结论。按返回的分页、范围和未读标记决定是否补读；只缺哪部分就读取哪部分，不要求无关对象、层或矩阵全量展开。报告证据范围和未决项，不用预设答案补足。已经回答的问题不重复综合扫描。

## 修改与验收

先读受影响范围并保留原始证据，再对用户批准的具体差异提出 preview。执行前必须满足当前权限策略、精确批准和 checkpoint；写后独立回读与批准内容比较。能工作的结构和正确曲线不因模板偏好被重建。

安装工具或高级衣柜工具可能有自身支持的参数、拓扑和默认行为；使用前核对其说明和 preview 是否符合本次需求。工具不支持某种设计时报告限制或使用合适的已暴露工具，不把用户设计改成工具的默认方案。特别是省略参数可能触发自动分配，不能把 `max+1` 当作批准过的映射。

形态键、对象适配和材质属性改动以受影响对象的实际证据与批准目标为准，不从固定名称推测数值，不扩大到未授权的身体、骨架或物理系统。曲线身份及 renderer-wide 属性遵循工具实际契约，不复制无关绑定。

运行控制需要另有明确批准的方案和有限 duration/frame 预算。记录原 Play 状态，保留观察 jobId；pending 或超时只回读原 job，不能重启同一观察。completed 只表示采集结束，必须核对采样范围、输入回执、状态身份与实际效果。结束或失败后恢复用户批准的 Play 状态并独立回读，不默认退出用户已有会话。

清理需要精确对象或资产、完整引用证据及单独批准；不得因未出现在某张表中就删除。回滚也需单独批准。只读任务回答后结束，不自动进入这些修改步骤。

工具契约与安全细节见 [参考](references/workflow.md) 和 [工作流描述](workflows/wardrobe-authoring.json)。
