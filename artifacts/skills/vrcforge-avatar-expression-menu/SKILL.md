---
name: vrcforge-avatar-expression-menu
title: VRChat 菜单制作
description: Inspect or author an exact standalone VRChat expression-menu category, submenu, pagination or control while preserving existing parameter domains, FacialTracking, icons, aliases, original labels and the eight-control limit.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/expression-menu.json
  - references/workflow.md
---

# VRChat 菜单制作

## 什么时候使用

用户单独要求检查、创建或调整一个 expression menu 分类、子菜单、翻页或控件时使用。

## 什么时候不使用

要求完整制作衣柜时不单独安装本 Skill；不要重建根菜单、替换 FT、修改 R18 域、批量重排、暴露隐藏参数或顺手创建动画。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/expression-menu.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
