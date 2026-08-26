---
name: vrcforge-avatar-accessory-switch
title: VRChat 饰品安装与开关
description: Install or configure one independent VRChat accessory with its proven Bone Proxy or existing constraint attachment, dedicated Bool, FX state, activation animation and expression-menu toggle; preserve the exact accessory subtree and existing sources.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_scan_modular_avatar
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_avatar_controls
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_preview_ensure_animator_state
  - vrcforge_ensure_animator_state
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_preview_constraint_sources
  - vrcforge_set_constraint_sources
  - vrcforge_preview_unity_constraint_conversion
  - vrcforge_convert_unity_constraint
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/accessory-switch.json
  - references/workflow.md
---

# VRChat 饰品安装与开关

## 什么时候使用

给目标 Avatar 安装或调整一个用户明确指定的饰品，并制作该饰品独立开关；与跨 Avatar 提取/移植现有配件不同。

## 什么时候不使用

整套服装 Setup Outfit、盲目替换已经有效的 constraint、未经授权的外部模型资源、自动暴露隐藏参数或跨 Avatar 批量复制。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/accessory-switch.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
