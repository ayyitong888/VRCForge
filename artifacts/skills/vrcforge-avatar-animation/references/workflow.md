# VRChat 动画与体型补偿：社区实际做法与固定步骤

用户独立要求制作或修复服装/发型/饰品 AnimationClip、对象启停、胸型/袜子/尾巴/鞋跟等已证实的体型补偿时使用。

## 适用范围与禁止事项

完整衣柜制作不需要另外安装本 Skill；不要凭猜测写 BlendShape、替换 controller、修改无关 menu、生成不存在的 OFF clip 或改变第三方物理。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

读取当前 Descriptor 绑定的 FX controller，并扫描现有 FX state、transition、motion、AnimationClip binding、对象层级、Body renderer 和真实 BlendShape 名称。

## 2. 真实结构和社区规则

列出目标服装/发型/饰品精确的启用、关闭、保持不变对象；分清原服装、内衣、袜子、兽尾、胸型和高跟，未证明的补偿一律不写。

## 3. 精确原子与适用条件

需要 state 时使用 `vrcforge_preview_ensure_animator_state` / `vrcforge_ensure_animator_state`；仅在明确验证条件时精确调整现有 FX controller。

## 4. 执行边界与读取结果

每个属性调用 `vrcforge_preview_write_animation_curve` / `vrcforge_write_animation_curve`，关键帧保持 `time=0`，一次只处理一个精确绑定并沿用当前权限策略和检查点。

## 5. 完整验收和停止条件

保留原有空 OFF Motion、相邻服装的开关逻辑与其他参数域；写后回读 clip 路径、state motion、curve 值、对象/BlendShape 结果并验证 Avatar。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

