---
name: vrcforge-avatar-wardrobe
title: VRChat 衣柜制作
description: Author or repair one complete VRChat Int wardrobe with fixed outfit values, preserved proven FX topology, full per-outfit mutual-exclusion clips, evidence-derived paired morph reset/apply curves, exact menu placement, optional replacement cleanup, and per-outfit runtime acceptance; use for whole wardrobe closure, not isolated menu/clip edits, unproven paid-asset extraction, FT2 hair migration, or automatic physics rewrites.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_blendshapes
  - vrcforge_scan_wardrobe
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

用户明确要求创建、扩展或修复整个换装闭环时使用。开始后先固定用户选择的 Unity 工程、场景和 Avatar；**每一次调用都必须携带同一个绝对 `projectPath`**。从 Descriptor 回读真正绑定的 FX、Parameters 和 Menu，不以文件名或场景猜测替代证据。

## 什么时候不使用

只改一个菜单控件或一条 AnimationClip 曲线时不使用；不要迁移 FT2 头发内容、提取未授权资产、自动重做作者 PhysBone/constraint/collider，或修改用户已经调好的物理系统。当前 Manuka 项目中的 Marshmallow PB 2.x 是明确禁区。不要为了省调用跳过完整矩阵和逐套验收。

## 不可放宽的衣柜契约

1. 唯一选择器是 saved、synced 的 `衣柜 : Int`，默认值 `0`；每个菜单服装按钮写固定值。固定值由用户批准的映射表决定，禁止省略 `value` 让工具自动 `max+1`。
2. 保留已经通过实际切换与动态验收的现有状态机拓扑；来源 FT2 中已验证可工作的无条件 AnyState 基线不是缺陷，不得为了理论规范化改成 Idle/Base。只修有回读和运行证据会冲突的额外层、状态或转换。新建时复现用户确认的参考结构；每套选择转换仍使用 `衣柜 Equals N`、`hasExitTime=false`、`duration=0`。
3. 每个已批准值的 clip 都要显式写完整互斥矩阵：当前套装根开启、其他所有套装根关闭；再按该套设计显式写默认鞋、袜、内衣、领带、手环等。不能依赖 Write Defaults、场景默认状态或上一套状态补齐。
4. 先检查每套衣服现有/候选动画是否已经带形态键，再扫描当前 Avatar 身体 Renderer、套装 Renderer 和全部相关形态键。以当前身体基线和该服装的适配能力为准，成对写入必要的 body/clothing `reset/apply` 曲线：支持当前基线的衣服维持或恢复基线；不支持的衣服只在该套中调整身体/衣服形态，切换到其他套时恢复。依据套装风格、鞋跟高度、作者说明与实际穿模证据定值，不硬编码 100 或固定名称。`Breast_big`、`Breast_big_PLUS`、`Foot_heel`、`Foot_heel_high` 仅是 Manuka 项目示例。
5. 先检测套件中依附头部、脸部或颈部的对象。只有发生接头/换头、当前头骨或头表面与套件原适配目标不一致，或视觉证据显示偏移/穿模时，才针对当前实际头部骨架和表面重新定位并验证动态继承；正常完整模型和已正确适配的衣服保留原配置。需要调整时只动导入对象或安装容器，不缩放身体。Sapphy Head 仅是当前目标示例。
6. 菜单遵循用户批准的现有根结构。当前目标根仅保留 `面捕` 与 `原模型菜单`，换装放到 `原模型菜单` 下并按 `衣服 / 头发 / 配饰` 组织；保留原模型内容，不迁移 FT2 头发。
7. 一次只处理一套。该套的菜单、参数、FX、动画、形态键、静态和动态回读全部通过，才允许进入下一套。

## 替换与清理

定点复用值（例如当前目标的 `value=3`）时，先预览并移除旧值的菜单/FX 绑定但保留旧对象与资产，再用 `vrcforge_add_wardrobe_outfit` 明确传入同一固定值；不要走会自动分配 `max+1` 的准备式 Add Outfit 衣柜分支。旧 FX 层按精确层名逐层预览、删除、全量回读。旧场景实例先扫描完整且未截断的入向引用闭包，禁用并完成新套动态验收后，才可在单独批准下按精确对象路径删除；任何引用未闭合都停止。

完整调用顺序、阻断条件与逐套验收见 [衣柜制作说明](references/workflow.md) 和受签名保护的 [衣柜工作流契约](workflows/wardrobe-authoring.json)。所有写入仍经过当前权限策略、精确 preview、checkpoint、写后回读；回滚需要单独确认。
