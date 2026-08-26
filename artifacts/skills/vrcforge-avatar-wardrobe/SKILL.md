---
name: vrcforge-avatar-wardrobe
title: VRChat 衣柜制作
description: Create or extend one complete VRChat outfit wardrobe, including Modular Avatar Setup Outfit, wardrobe expression menu, saved/synced Int parameters, FX states, outfit animation and body compensation; use for outfit or wardrobe authoring, not isolated menu-only changes, unrelated avatar cleanup, paid-asset extraction, or automatic migration.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_blendshapes
  - vrcforge_scan_wardrobe
  - vrcforge_scan_modular_avatar
  - vrcforge_inspect_modular_avatar_component
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_preview_setup_outfit
  - vrcforge_setup_outfit
  - vrcforge_preview_add_outfit
  - vrcforge_add_outfit
  - vrcforge_preview_create_wardrobe
  - vrcforge_create_wardrobe
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

此 Skill 独立覆盖 **服装挂载 → 衣柜参数 → FX 状态 → 动画曲线 → 衣柜菜单 → 实际换装验收**，不要求另外安装菜单制作或动画制作 Skill。

## 什么时候使用

用户明确要求新增服装、创建衣柜、接入现有 Int 衣柜，或者修复其菜单、参数、FX 与动画之间已经查明的断链时使用。先固定用户选择的 Unity 工程和 Avatar，读取 Descriptor 当前真正绑定的 FX、Parameters 和 Menu。

## 什么时候不使用

只要求独立改菜单或独立改 AnimationClip 时不使用；不要重建根菜单、替换面捕、删除服装、迁移不相关参数、提取未获授权的付费资产，或凭推测创建作者 prefab 内的 PhysBone、constraint。

## 社区做法与不确定性

骨骼、Modular Avatar Setup Outfit、衣柜 Int、AnyState、AnimationClip、分页、参数同步或作者 prefab 行为拿不准时，先查成熟 VRChat / Modular Avatar 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止，返回 `capabilityGap=true`，不猜测、不虚构工具。

完整顺序与阻断条件见 [衣柜制作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/wardrobe-authoring.json)；Agent 根据当前 Avatar、菜单、骨骼和衣柜状态选择适用原子。每项写入经过当前用户配置的权限策略、精确预览、检查点和现有监督通道；回滚仍须单独确认。
