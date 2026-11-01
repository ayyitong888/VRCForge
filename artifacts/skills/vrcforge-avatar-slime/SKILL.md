---
name: vrcforge-avatar-slime
title: 半透明 Slime Avatar 制作
description: Make or repair a translucent Slime avatar, lightweight water ripples and a smooth normal/Slime toggle using observed materials, actual animation bindings and multi-angle runtime evidence.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_bind_execution_target
  - vrcforge_find_assets
  - vrcforge_get_asset_info
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_set_property
  - vrcforge_scan_materials
  - vrcforge_inspect_material_properties
  - vrcforge_preview_project_asset_duplicate
  - vrcforge_duplicate_project_asset
  - vrcforge_preview_material_shader_assignment
  - vrcforge_set_material_shader
  - vrcforge_preview_renderer_material_slot
  - vrcforge_set_renderer_material_slot
  - vrcforge_plan_shader_tuning
  - vrcforge_preview_shader_apply
  - vrcforge_apply_shader_tuning
  - vrcforge_preview_material_texture_assignment
  - vrcforge_set_material_texture
  - vrcforge_preview_texture_import_settings
  - vrcforge_set_texture_import_settings
  - vrcforge_texture_patch
  - vrcforge_scan_modular_avatar
  - vrcforge_inspect_modular_avatar_component
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_manage_expression_parameters
  - vrcforge_manage_expression_parameters
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_scene_object_duplicate
  - vrcforge_duplicate_scene_object
  - vrcforge_set_gameobject_active
  - vrcforge_preview_save_asset
  - vrcforge_save_asset
  - vrcforge_scene_save
  - vrcforge_capture_screenshot
  - vrcforge_capture_multi_screenshot
  - vrcforge_gesture_manager_status
  - vrcforge_gesture_manager_enter_play_mode
  - vrcforge_gesture_manager_set_parameter
  - vrcforge_start_runtime_observation
  - vrcforge_get_runtime_observation
  - vrcforge_set_play_mode
  - vrcforge_get_compile_errors
  - vrcforge_get_ndmf_errors
  - vrcforge_read_vrchat_sdk_builder_alerts
  - vrcforge_preview_diagnostic_fix
  - vrcforge_apply_diagnostic_fix
  - vrcforge_run_validation_report
  - vrcforge_scan_avatar_performance
support-files:
  - workflows/slime.json
  - references/workflow.md
  - references/surface-and-transition.md
  - references/parameter-tuning.md
---

# 半透明 Slime Avatar 制作

## 什么时候使用
用户要求制作或修复半透明胶体、Slime 娘、水波光泽、内部物体可见效果，或正常外观与 Slime 外观的平滑切换时使用。这是 VRCForge 原生 agentic Skill，由内部 Agent 和外部 MCP Agent 使用；不是 Codex Skill，也不是自动执行脚本。

## 什么时候不使用
仅讨论美术概念、用户只要求只读盘点、任务与胶体外观无关时，不执行写入。不要将水波等同于折射，不把穿透视觉当成同步物理交互，不为 Slime 擅自重做衣柜、体型、面捕或第三方物理。

## 执行原则
1. 先按当前用户目标确定部位、颜色、透明度、正常状态、菜单位置、同步与保存偏好；沿用已明确约定，不重复确认。默认复用原有正常外观。不要从本 Skill 自动套用蓝色、白毛、红眼、某个菜单层级、参数名或 Avatar 路径。
2. 先读当前工具目录/schema，再选最窄 MCP 读写。使用 find_assets/get_asset_info 的结构化事实与 includeText/textSearch，inspect_material_properties 的属性与渲染状态，scan_animation_bindings 的精确 clip/binding 选择。不要转去 Shell、手改 Unity YAML/meta 或动态 C#；缺少实际原子能力时报告具体缺口，仅暂停依赖它的动作。
3. 按 [逐步操作说明](references/workflow.md) 选择当前需要的步骤；每步列出工具、先读数据、修改、检查与纠错。调数值时用 [参数起点与调整方向](references/parameter-tuning.md)，遇到对应现象时读 [表面、过渡与问题处理](references/surface-and-transition.md)。数值是带条件的美术试调起点，不是模型无关的正确答案。已有研究仍匹配配置时复用；陌生功能、用户纠正或根因不明时先读官方文档、作者说明和成熟源码。联网/图片读取使用当前宿主实际具备的工具，不把链接、文件名或搜索摘要说成已读。
4. 用独立工作材质保留源资产及正常外观；从真实 Renderer/slot、Shader 属性和依赖确定修改范围。头、身体、发束、耳/角和尾巴按实际构造处理，不凭同名材质批量覆盖眼睛、口腔或备用表情。
5. 低开销胶体优先现有透明着色器的光泽、Fresnel/边缘反射和少量平铺法线 UV 动画；真正需要折射时再核对性能。头部可比身体更不透明，但仍按用户目标保留可见的透过效果；不要靠全头不透明或关闭面捕掩盖内部表情穿出。
6. 正常与 Slime 端点、过渡中途、反向切换都要实际检查。Color/Vector 属性在每个受影响状态明确保留完整 RGBA/XYZW，不能只写 alpha 后用静态材质读回代替运行态颜色证明。
7. Slime 只处理本次授权的表面和切换驱动，保留已有服装、体型、物理与面捕。仅核对 Slime 开关是否影响它们；不在本 Skill 中整理衣柜、修体型或配置物理插件，发现独立问题另报。
8. 现有授权内必要步骤自主完成，沿用产品批准和检查点机制，不新增哈希、版本握手、保护门禁或重复确认。只保存本次涉及的精确资产/场景，不全局 SaveAssets。未知结果先读已提交操作/目标，不重放副作用；回滚遵循原有单独授权。
9. 写入成功只代表存档成功。实际运行、材质视觉、性能和 VRChat 客户端验证分别报告；普通检查由 Agent 完成，按用户约定统一交付可见图片，不把“已查看 N 张图像”当成给用户发图。

