---
name: vrcforge-avatar-animation
title: VRChat 动画与体型补偿
description: Author or repair one evidence-backed VRChat outfit, hair or accessory animation while preserving authored timelines and changing only the exact user-approved FX, object or body/heel BlendShape behavior.
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

完整衣柜制作不需要另外安装本 Skill；不要凭猜测写 BlendShape、替换 controller、修改无关 menu、强制保留或创建空 OFF motion，或改变第三方物理。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

保留现有 AnimationClip 的真实关键帧时间线；仅当用户批准的新设计明确为静态值时，才可将新曲线设在 `time=0`。OFF motion 不作统一预设：结合 Write Defaults、现有恢复曲线与过渡证据判断，只按用户明确批准的改动执行。写后精确回读受影响的 state、clip、binding 与值。Build & Test readiness 是就绪检查而非实际构建或运行时证明。

替换同一功能时，先读 `references/workflow.md` 的替换分支，列旧/新驱动、目标 bindings、参数及临时共存结束条件；新组件存在不代表替换完成，也不证明重复驱动已造成运行冲突。新链路验证后按批准差异精确退役旧驱动，并核对 neutral、开/关及受影响的手势/表情。仅对同一 controller 且实际 schema 支持的 action 合批，不构造未支持的 batch。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [工作流指引](workflows/animation.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
