---
name: vrcforge-avatar-hairstyle
title: VRChat 发型切换
description: Create one complete VRChat hairstyle switch including verified head attachment, the existing fewer-than-eight Bool or eight-or-more shared-Int convention, FX states, animation curves, expression-menu controls and separately authorized migration.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_scan_modular_avatar
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
  - vrcforge_preview_manage_expression_parameters
  - vrcforge_manage_expression_parameters
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_preview_ensure_animator_state
  - vrcforge_ensure_animator_state
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/hairstyle.json
  - references/workflow.md
---

# VRChat 发型切换

## 什么时候使用

新增、调整或检查一个独立发型切换，并根据包含默认发型的总数选择既有 Bool 或 Int 习惯。

## 什么时候不使用

整套服装、独立饰品、跨 Avatar 配件移植、面捕替换或未经单独允许的整组参数迁移。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/hairstyle.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
