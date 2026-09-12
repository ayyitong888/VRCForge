# VRChat 衣柜制作：固定值、完整互斥矩阵与逐套验收

这是一个单独安装即可完成衣柜闭环的 Skill。所有示例中的 `projectPath` 都必须替换为用户选择工程的**绝对路径**，且每次调用都要显式携带；本流程不允许工具自行猜工程。

## 0. 先冻结事实，不先写

1. `vrcforge_list_avatars` 固定 `projectPath`、场景、Avatar；`vrcforge_read_avatar_descriptor` 回读真正绑定的 FX、Parameters、Menu。
2. readiness 顺序固定为：绑定后复用 `structuredContent.result.executionTarget` 完整 JSON；读取 `resources.identityLockUri`、`operationReceiptUri`，再调用 `prompts/get(identityLockUri, sessionContextUri)`。只有实际 `context.status == ready_for_planning` 才进入领域读取；不得手抄 hash、丢弃 target 或 provenance。先回读 Descriptor，再调用 `scan_fx_animator`，按实际 controller/layer/state-machine path 与条件/clip 建队列；`scan_wardrobe` 只是可选模式线索。随后只针对当前候选缺口窄读相关资源，累计全局完整性，不要求无关范围全量调用。记录每个已批准值对应的菜单、状态、clip、套装根、默认鞋/袜/内衣/领带/手环和全部相关形态键。
3. 先检查每套衣服现有动画和候选动画是否已经包含形态键曲线，再补缺口。盘点必须覆盖当前 Avatar 的身体 Renderer、当前套装 Renderer、现有动画绑定和候选动画绑定。依据套装风格、鞋跟高度、作者说明、已有动画与实际穿模证据，确定该套需要写的身体和衣服形态键及数值。名称相似不是证据。
4. `Breast_big`、`Breast_big_PLUS`、`Foot_heel`、`Foot_heel_high` 仅是当前 Manuka 目标中可能出现的示例，不是任何其他 Avatar 的必填字段。
5. 调用 `vrcforge_scan_modular_avatar`、`vrcforge_inspect_skinned_mesh_bone_usage` 检查服装 prefab、Humanoid Hips、SMR、rootBone 和作者组件。保留作者 PhysBone、collider、constraint 和所有用户已调好的物理配置；当前 Manuka 项目中的 Marshmallow PB 2.x 明确禁改。
6. 得到用户确认的固定值矩阵后才写。任一对象路径、clip、形态键值或菜单归属不明确，先沿允许的只读路径继续识别并保留 `undetermined`；只有可用读取路径、真实工具能力或本次预算耗尽后仍不足，才返回 `capabilityGap=true` 并停止进入写入。

## 0.1 现有结构识别循环（只读）

先完成身份 Resource、`sessionContext` 与 `prompts/get` readiness，再把“识别现有结构”和“经批准的新建衣柜”分开。维护一个有界候选队列，元素是身份 scope、controller、layer、完整 state-machine 路径、资源 hash；分页游标单独保存为每个读取来源的 next cursor。循环按 `enter → read → analyze → judge → exit → next`：进入时绑定同一绝对 `projectPath`、ExecutionTarget、target 和 provenance；读取当前窄页；分析参数、菜单、Entry/Exit/AnyState/普通 transition 的完整条件（包括 AND）、子状态机、BlendTree、driver、跨层依赖、clip binding 与可见效果；判断并输出 `confirmed`、`rejected` 或带原因的 `undetermined`；前两者必须有实际 FX transition 与 clip effect 证据，非服装 Int 多路控制命中旧扫描不得直接确认为衣柜；退出时保存局部证据、完整性和已读集合；再以独立游标继续下一个未访问候选或页面。visited key 必须是身份 scope + controller + layer + 完整路径 + resource hash，不能把 page cursor 当节点身份。

若某个读取入口拒绝请求，使用另一个允许的只读入口并保留同一 target/provenance；FX 缺 Entry/Exit、BlendTree 树或 driver 事实时只标对应类别未决并继续其它层；遇到环、分页不完整或证据缺失，将候选标为 `undetermined`，记录原因、当前页和 next cursor，继续其他 controller/layer。一次候选没有 AnyState 或一次局部未识别不得结束全局识别，也不得把 `scan_wardrobe` 的 `loose_control` 当成全局否定。只有允许的只读路径、实际工具能力或本次预算耗尽后仍无法取得完整证据，才返回已读证据、未读队列和 `capabilityGap=true`，停止进入写入阶段。此循环不放宽权限/provenance、不删除结构、不重建稳定拓扑。

## 1. 建立唯一参数并保留已验证 FX 拓扑

1. 仅在经批准的固定值新建或修复 authoring 方案中，选择器才约束为 saved、synced、默认值 0 的 `衣柜 : Int`。识别既有结构时保留已验证的 Bool、Float、BlendTree 或其他参数拓扑，不得隐式迁移成 Int。使用 `vrcforge_preview_ensure_expression_parameter` → 批准 → `vrcforge_ensure_expression_parameter`；不要创建每套 Bool。
2. 先用 `vrcforge_scan_fx_animator` 和实际 Play Mode 切换证明现有状态机拓扑。已有且已验收的拓扑逐字段保留 source、destination、conditions、duration、exitTime、Write Defaults 与当前进度；不要因为理论偏好把它改成 Idle/Base，也不要重排能工作的状态。
3. 新建基础衣柜才使用瞬时默认：`AnyState → Outfit_N`、`衣柜 Equals N`、`hasExitTime=false`、`exitTime=0`、`duration=0`、`canTransitionToSelf=false`。用户确认的动画序列可保留当前进度并制作可选反向序列；只对回读和运行证据已证明会冲突的额外层、状态或转换做精确 preview/修改。

## 2. 一次只安装一套

1. 普通完整服装用 `vrcforge_preview_setup_outfit` → 批准 → `vrcforge_setup_outfit`，回读 `ModularAvatarMergeArmature`。未实例化 prefab 可用 Add Outfit，但若需要固定值，衣柜管理必须关闭，随后单独调用衣柜原子。
2. 所有套装写入都显式传 `value=N`。禁止省略 `value`；准备式 Add Outfit 的衣柜分支会自动 `max+1`，不能用于复用 `value=3` 或其他固定槽位。
3. 定点替换值 3 时：先 `vrcforge_preview_manage_wardrobe(action=remove_outfit,targetValue=3,deleteObjects=false,deactivateObjects=true,deleteGeneratedAssets=false)`；批准并执行后全量回读，确认 3 的旧菜单/FX 绑定释放，再 `vrcforge_preview_add_wardrobe_outfit(...,value=3)` → 批准 → `vrcforge_add_wardrobe_outfit(...,value=3)`。

## 3. 验证全部动画，只修补已确认的差异

`vrcforge_add_wardrobe_outfit` 只新增一套，不会改写旧 clip。因此新增/替换后必须对每个已批准值逐个处理：

1. 先逐个回读已批准 clip 的套装根、部件开关、形态键和属性曲线。已有正确稳定态或过渡态都保留源时间线；只有回读与已批准矩阵存在明确差异时，才用 `vrcforge_preview_write_animation_curve` / `vrcforge_write_animation_curve` 修补对应 binding。
2. 仅新建基础衣柜的瞬时稳定态使用 `time=0`：当前套装根 `m_IsActive=1`，其他所有套装根 `m_IsActive=0`，并显式写该套设计的默认鞋、袜、内衣、领带、手环等开关。不要把“保持不变”当成“默认正确”；矩阵未列出的旧开关曲线先预览，再删除陈旧 binding。
3. 过渡 clip 单独遵循已确认的动画时间线：保留旧套和新套的当前进度，允许两者在交融区同时存在；中途改选时从当前进度反向溶解旧套，再进入最新选择。不得把新建基础衣柜的 `time=0` 互斥矩阵强写到已有过渡或用户动画，否则会截断交融或造成裸露窗口。
4. 以当前已验收身体形态为基线，并先复用衣服动画中已经正确存在的形态键曲线。适配当前基线的衣服要维持或恢复基线；不适配的衣服只在该套 clip 内把必要的身体形态调到可穿范围，并在其他套 clip 中恢复。衣服自身形态键也按相同方式成对写 `apply/reset`。不能硬编码数值 100、不能从固定名称猜测，也不能要求所有衣服把身体推到最大。
5. 每个稳定态 clip 写完立即用 `vrcforge_scan_animation_bindings` 与 `vrcforge_scan_blendshapes` 回读：当前根必须 ON、所有其他根必须 OFF、默认部件和形态键必须与冻结矩阵完全一致。过渡 clip 另需回读时间线、旧新并存区间和反向进度。材质/属性曲线以 renderer-wide property block 的 rendererPath、component、slot 和 sharedMaterial 身份核对，同一 Renderer 属性不得按 slot 重复。Write Defaults 或场景默认值不能代替这项检查。
6. Play Mode 中实际驱动形态键并观察身体与衣服：胸、腰、臀、腿、脚和鞋跟等相关区域不得穿模，鞋跟/脚部姿态必须与套装一致；过渡期间遵守用户已确认的端点遮挡规则，过渡不得额外产生端点没有的暴露。只验证曲线存在不算视觉通过。

## 4. 头部、脸部和颈部对象按需适配

先检测依附头部、脸部或颈部的套件对象，但不要默认重调：

1. 只有发生接头/换头、当前头骨或头表面与套件原适配目标不一致，或正交/透视证据显示偏移、悬空或穿模时，才进入重新定位流程。正常完整模型和已正确适配的衣服保留作者原配置。
2. 需要重新适配时，读取当前实际头部的父级、骨架、表面、局部 TRS、constraint/PhysBone 引用；只调整导入对象或安装容器，不缩放身体与 Armature。Sapphy Head 只是当前目标示例，不是通用目标名。
3. 通过 `vrcforge_set_property` 做精确、可回读的局部位置/旋转/统一缩放。不得自动重建作者组件或改用户物理配置。
4. 只有发生重新适配时，才强制验收 Front、Left 90、Right 90、Back、Bottom 和 Persp 近景；Play Mode 下转头和 AFK 动作时对象必须继承正确且不穿模/漂移。

## 5. 菜单只放到用户批准层级

1. 通用规则是保留用户批准的现有根结构。当前目标根只保留 `面捕` 与 `原模型菜单`，不要在根新建 `换装`、`R18` 或衣柜分页。
2. 当前目标在 `原模型菜单` 下组织 `衣服 / 头发 / 配饰`；服装按钮放 `衣服`，写固定 `衣柜=N`。头发和配饰仅整理原模型已有或本任务明确授权内容，不迁移 FT2 头发。
3. 每页最多 8 个控制项，`下一页` 自身占一个槽。每次先 preview 精确菜单路径，写后回读根与子菜单；禁止工具回退到根菜单。

## 6. 每套闭环后才继续

对当前 N 依次回读：菜单按钮 → `衣柜=N` → 条件式 AnyState 或保留的现有序列 → State/Motion → 完整 clip 矩阵 → 场景对象/默认部件/形态键。运行控制需取得具体方案批准并进入执行曝光层：记录原Play状态，用 Gesture Manager 进入并确认module连接及pending结束，再以`vrcforge_start_runtime_observation`提交有限duration/frame预算和获准参数序列，至少覆盖`0 → N → 其他值 → N`。保留返回jobId；pending或观察超时不代表失败，不重启，继续以`vrcforge_get_runtime_observation`回读原job的完整 frame/step/state Resource 链。completed不等于效果通过：核对实际采样覆盖、每个输入回执、source state counts、stateSetHash、frame identity 和 selected identity/counts，再做正面、左右 90、背面、底部并检查所有已改变形态键对应区域的身体/衣服穿模。完成或失败后，用`vrcforge_set_play_mode`恢复用户批准的Play目标状态并独立回读，不默认退出用户原本已有的Play会话。只有本套触发了头部重新适配，才增加 Persp 近景与转头动作。`vrcforge_run_validation_report` 和 `vrcforge_build_test_readiness` 通过后，才可处理下一套。仅识别或读已有观察job时，不启动或改变运行状态。

## 7. 已证明旧内容的清理

1. 旧 FX 层只能按 `vrcforge_scan_fx_animator` 返回的精确 `layerName`，逐层 preview `delete_layer`、批准、执行、全量回读；不得用模糊名批量删除。
2. 旧场景实例先 `vrcforge_scan_inbound_reference_closure`。结果必须完整、未截断且所有消费者已解绑；先 `vrcforge_set_gameobject_active(false)` 并完成替换套装的动态验收。
3. 只有用户对精确对象路径另行批准后，才调用 `vrcforge_delete_gameobject`。删除后回读对象不存在、FX/Menu/Parameters 无残留；来源 prefab/资产和来源 Avatar 永不删除。

## 8. 最短安全调用序列

所有项目内调用均附同一个绝对 `projectPath`：回读现有扫描与动画证据，按 scope、变更范围和证据 freshness 复用；仅对陈旧或受影响范围补扫 → 先读衣服已有动画 → 冻结固定值、对象、部件和成对形态键矩阵 → 确保 `衣柜` 并保留/复现已确认 FX 拓扑 → 预览/安装一套 → 必要时释放并显式复用固定值 → 回读并验证全部已批准 clip 的完整矩阵，保留已有正确稳定态与过渡时间线，只对回读与已批准矩阵存在的证据差异做 preview/修补；仅新建基础衣柜的瞬时稳定态写新建的完整互斥矩阵，过渡和用户动画保留源时间线并验证 → 核对衣柜选择转换且只修已证明冲突 → 必要时按证据适配头部对象 → 定点菜单 → 当前套完整矩阵、静态/动态/穿模验收 → 下一套。全部套装闭环后，再逐层清已证明过时的 FX；最后做旧实例引用闭包、禁用、动态复验与单独批准删除。
