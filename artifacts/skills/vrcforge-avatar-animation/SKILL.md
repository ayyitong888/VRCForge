---
name: vrcforge-avatar-animation
title: VRChat 动画与体型补偿
description: Author or repair one proven VRChat outfit, hair or accessory AnimationClip and its FX state, object-activation curves and body/heel BlendShape compensation without replacing unrelated controllers or assuming an OFF clip.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_blendshapes
  - vrcforge_get_gameobject
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_preview_ensure_animator_state
  - vrcforge_ensure_animator_state
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/animation.json
  - references/workflow.md
---

# VRChat 动画与体型补偿

## 什么时候使用

用户独立要求制作或修复服装/发型/饰品 AnimationClip、对象启停、胸型/袜子/尾巴/鞋跟等已证实的体型补偿时使用。

## 什么时候不使用

完整衣柜制作不需要另外安装本 Skill；不要凭猜测写 BlendShape、替换 controller、修改无关 menu、生成不存在的 OFF clip 或改变第三方物理。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/animation.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
