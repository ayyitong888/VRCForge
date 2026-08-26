---
name: vrcforge-avatar-audit
title: VRChat Avatar 检查与验收
description: Perform a strictly read-only VRChat avatar inventory and final acceptance audit across Descriptor assets, hierarchy, weighted bones, controls, parameters, FX, animation bindings, wardrobe, compile diagnostics and validation readiness.
permission-mode: read_only
risk-level: low
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_scan_avatar_items
  - vrcforge_get_gameobject
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_get_compile_errors
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/audit.json
  - references/workflow.md
---

# VRChat Avatar 检查与验收

## 什么时候使用

用户要求只读盘点现有 Avatar，或者对已完成的改模做 Descriptor、骨骼、菜单、参数、FX、动画和构建就绪验收时使用。

## 什么时候不使用

不用于写资产、自动修复、切换运行参数、进入播放模式、重排层级、替换面捕、截图保存或上传模型。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/audit.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
本 Skill 严格只读：不创建、修改、删除、播放、上传或恢复任何资产。
