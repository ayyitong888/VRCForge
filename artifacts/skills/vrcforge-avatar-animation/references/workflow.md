# VRChat 动画与体型补偿：按证据执行的步骤

用户独立要求制作或修复服装/发型/饰品 AnimationClip、对象启停、胸型/袜子/尾巴/鞋跟等已证实的体型补偿时使用。

## 适用范围与禁止事项

完整衣柜制作不需要另外安装本 Skill；不要凭猜测写 BlendShape、替换 controller、修改无关 menu，或改变第三方物理。不得一概强制保留既有空 OFF motion，也不得自动补建；按当前 Write Defaults、状态过渡、恢复曲线及现场证据判断，只能修改用户明确批准的目标。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

<a id="existing-clip-targeted-read"></a>

## 1. 目标绑定与现状盘点

绑定用户指定的 Avatar 与实际 FX controller。读取相关 state、transition、motion、clip 及其真实 binding、目标对象和 Body renderer/BlendShape；只深入足以判定用户目标的路径，不把全项目或无关 controller 扫描当成前提。需要补证时使用现有只读扫描工具，并遵循其分页/精确选择器。

已有换衣动画只需改少量曲线时，先从衣柜状态定位现有 clip，再用 `vrcforge_scan_animation_bindings` 的 `clipPaths` 和 `bindingSelectors` 精读目标属性；先确认 index 中的精确绑定，再读所需 details/key 页。不要为局部体型补偿分页遍历完整 FX、面捕或无关 clip。调用前以当前目录和已加载工具 block 返回的 `selectedTools` 为准，使用其中的精确 Agent 工具名；若因跨 block 而拒绝，返回目录选择正确 block，不猜别名或重试相近名称。

## 2. 真实结构和社区规则

按用户要求列出目标服装/发型/饰品精确的启用、关闭和保持不变对象；仅在本 Avatar/资产证据支持时区分原服装、内衣、袜子、兽尾、胸型和高跟。未证明的补偿一律不写。

不同衣服可能需要不同的身体形态适配。按衣服真实需求与用户授权，在该衣服现有换装 clip 中同时加入精确绑定的身体 BlendShape，让衣服与身体适配同步生效；只处理相关的胸型、袜口／大腿、脚／鞋或局部遮体 Shape，不清空所有形态键，也不把局部胸型换成全身 Shrink。各套服装适配保持独立，不默认重写无关服装；核对已有默认／退出恢复机制，只有证据确认切换恢复缺失时才提出最小补充。并检查 Modular Avatar 同步与动画是否重复控制同一属性。

## 3. 精确原子与适用条件

需要 state 时先用 `vrcforge_preview_ensure_animator_state`，仅在用户批准 preview 后调用 `vrcforge_ensure_animator_state`。只有目标行为和条件由证据确定且用户批准时，才用 `vrcforge_preview_manage_fx_animator` / `vrcforge_manage_fx_animator` 精确调整现有 controller；不替换无关层或参数域。

## 4. 执行边界与读取结果

既有 clip 按读到的真实 timeline 保留时间点与未获批准的曲线；不要将时间点归一化到 `0`。只有用户批准的新设计明确为静态值时，新建曲线才使用 `time=0`。每个精确 binding 先 preview，用户批准后再以 `vrcforge_write_animation_curve` 写入；目标状态、曲线恢复行为或 OFF motion 不确定时停止并询问，不推测。

## 5. 完整验收和停止条件

### 替换同一功能

仅在用户批准替换本功能时使用：列出旧/新驱动的 controller/层/MA 组件、目标 bindings、enable/default 参数和 neutral 状态。若须临时共存，先写清验证方法、结束条件与待退役的精确项；新组件已挂载不等于替换完成，共存也不能单独证明运行冲突或某张截图的异常原因。

新链路验证后，按批准的 preview/checkpoint 精确停用或移除旧驱动及不再需要的 bindings，不改无关层、参数域或源资产；删除资产仍需单独批准。回读实际绑定和启用状态，再核对 neutral、开/关及受影响的手势/表情，确认没有重复控制和恢复残留。运行控制仍需批准、有限预算与原 Play 状态恢复；缺运行证据就保留未验证项，不以新组件或静态校验成功结束替换。

同一 controller 的多个差异仅在实际 schema 明确支持这些 action 时合批；核对 preview 和原子失败语义。`edits` 支持状态/转换编辑及按唯一 `layerName` 的 `delete_layer`；多个删层使用一个有序批次，先删除同步依赖层再删除其目标层。剩余同步索引由工具校正，共享 state machine、motion、clip 等子资产保留。任一项缺失、重名或依赖不合法则整批失败；重新扫描并修正计划，不拆成并发写入绕过检查。不支持该 action 的旧版工具仍报告范围与往返成本，不虚构能力。

写后重新扫描受影响的 FX state/transition/motion 与 AnimationClip binding/key 值，并读取目标对象/BlendShape；对照批准的精确 delta 和相邻行为。`vrcforge_run_validation_report` 与 `vrcforge_build_test_readiness` 仅提供验证/就绪证据，不会替代 Unity/VRChat 运行时测试或实际 SDK/NDMF 构建；需要这些结果时明确标记未验证，不能宣称已构建或运行验收。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

## 参考资料

- [VRC School：Write Defaults](https://vrc.school/docs/Unity-Animations/Write-Defaults/)
- [VRC School：Dissolve Toggles](https://vrc.school/docs/Avatars/Dissolve-Toggles/)
