---
name: vrcforge-avatar-breast-physics-audit
title: VRChat 胸部动态骨骼检查
description: Read-only audit of existing VRChat breast PhysBones, Marshmallow setup, left/right weighted bones, collider or constraint references and NDMF-generated evidence boundaries; never modify physics assets or claim unsupported generated output.
permission-mode: read_only
risk-level: low
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_avatar_items
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/breast-physics-audit.json
  - references/workflow.md
---

# VRChat 胸部动态骨骼检查

## 什么时候使用

用户要求只读检查、保护或解释已有胸部 PhysBone、Marshmallow setup、左右骨骼、collider、constraint 和构建期生成证据时使用。

## 什么时候不使用

没有现成专用 PhysBone / Marshmallow 写原子；禁止用通用字段工具冒充修复，禁止创建、删除或改动任何骨骼、物理、约束、prefab 或 Avatar。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/breast-physics-audit.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
本 Skill 严格只读：不创建、修改、删除、播放、上传或恢复任何资产。
