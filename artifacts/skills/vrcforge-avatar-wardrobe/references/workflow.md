# VRChat 衣柜制作：固定值、完整互斥矩阵与逐套验收

这是一个单独安装即可完成衣柜闭环的 Skill。所有示例中的 `projectPath` 都必须替换为用户选择工程的**绝对路径**，且每次调用都要显式携带；本流程不允许工具自行猜工程。

## 0. 先冻结事实，不先写

1. `vrcforge_list_avatars` 固定 `projectPath`、场景、Avatar；`vrcforge_read_avatar_descriptor` 回读真正绑定的 FX、Parameters、Menu。
2. 全量调用 `vrcforge_scan_wardrobe`、`vrcforge_scan_parameters`、`vrcforge_scan_fx_animator`、`vrcforge_scan_avatar_controls`、`vrcforge_scan_animation_bindings`、`vrcforge_scan_blendshapes`。记录每个已批准值对应的菜单、状态、clip、套装根、默认鞋/袜/内衣/领带/手环和全部相关形态键。
3. 先检查每套衣服现有动画和候选动画是否已经包含形态键曲线，再补缺口。盘点必须覆盖当前 Avatar 的身体 Renderer、当前套装 Renderer、现有动画绑定和候选动画绑定。依据套装风格、鞋跟高度、作者说明、已有动画与实际穿模证据，确定该套需要写的身体和衣服形态键及数值。名称相似不是证据。
4. `Breast_big`、`Breast_big_PLUS`、`Foot_heel`、`Foot_heel_high` 仅是当前 Manuka 目标中可能出现的示例，不是任何其他 Avatar 的必填字段。
5. 调用 `vrcforge_scan_modular_avatar`、`vrcforge_inspect_skinned_mesh_bone_usage` 检查服装 prefab、Humanoid Hips、SMR、rootBone 和作者组件。保留作者 PhysBone、collider、constraint 和所有用户已调好的物理配置；当前 Manuka 项目中的 Marshmallow PB 2.x 明确禁改。
6. 得到用户确认的固定值矩阵后才写。任一对象路径、clip、形态键值或菜单归属不明确，返回 `capabilityGap=true` 并停止。

## 1. 建立唯一参数并保留已验证 FX 拓扑

1. 唯一选择器必须是 saved、synced、默认值 0 的 `衣柜 : Int`。使用 `vrcforge_preview_ensure_expression_parameter` → 批准 → `vrcforge_ensure_expression_parameter`；不要创建每套 Bool。
2. 先用 `vrcforge_scan_fx_animator` 和实际 Play Mode 切换证明现有状态机拓扑。来源 FT2 中已经正常运行的无条件 AnyState 基线必须保留；不要因为理论偏好把它改成 Idle/Base，也不要重排能工作的状态。
3. 只对回读和运行证据已证明会冲突的额外层、状态或转换做精确 preview/修改。新建状态机时复现用户确认的参考结构，不自创规范。每套选择转换使用 `AnyState → Outfit_N`、`衣柜 Equals N`、`hasExitTime=false`、`exitTime=0`、`duration=0`、`canTransitionToSelf=false`。

## 2. 一次只安装一套

1. 普通完整服装用 `vrcforge_preview_setup_outfit` → 批准 → `vrcforge_setup_outfit`，回读 `ModularAvatarMergeArmature`。未实例化 prefab 可用 Add Outfit，但若需要固定值，衣柜管理必须关闭，随后单独调用衣柜原子。
2. 所有套装写入都显式传 `value=N`。禁止省略 `value`；准备式 Add Outfit 的衣柜分支会自动 `max+1`，不能用于复用 `value=3` 或其他固定槽位。
3. 定点替换值 3 时：先 `vrcforge_preview_manage_wardrobe(action=remove_outfit,targetValue=3,deleteObjects=false,deactivateObjects=true,deleteGeneratedAssets=false)`；批准并执行后全量回读，确认 3 的旧菜单/FX 绑定释放，再 `vrcforge_preview_add_wardrobe_outfit(...,value=3)` → 批准 → `vrcforge_add_wardrobe_outfit(...,value=3)`。

## 3. 完整重写所有已批准动画矩阵

`vrcforge_add_wardrobe_outfit` 只新增一套，不会改写旧 clip。因此新增/替换后必须对每个已批准值逐个处理：

1. 对该值的 clip，用 `vrcforge_preview_write_animation_curve` / `vrcforge_write_animation_curve` 在 `time=0` 明确写当前套装根 `m_IsActive=1`，其他所有套装根 `m_IsActive=0`。
2. 同一个 clip 明确写该套设计的默认鞋、袜、内衣、领带、手环等开关。不要把“保持不变”当成“默认正确”；矩阵未列出的旧开关曲线先预览，再删除陈旧 binding。
3. 以当前已验收身体形态为基线，并先复用衣服动画中已经正确存在的形态键曲线。适配当前基线的衣服要维持或恢复基线；不适配的衣服只在该套 clip 内把必要的身体形态调到可穿范围，并在其他套 clip 中恢复。衣服自身形态键也按相同方式成对写 `apply/reset`。不能硬编码数值 100、不能从固定名称猜测，也不能要求所有衣服把身体推到最大。
4. 每个 clip 写完立即用 `vrcforge_scan_animation_bindings` 与 `vrcforge_scan_blendshapes` 回读：当前根必须 ON、所有其他根必须 OFF、默认部件和形态键必须与冻结矩阵完全一致。Write Defaults 或场景默认值不能代替这项检查。
5. Play Mode 中实际驱动形态键并观察身体与衣服：胸、腰、臀、腿、脚和鞋跟等相关区域不得穿模，鞋跟/脚部姿态必须与套装一致。只验证曲线存在不算视觉通过。

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

对当前 N 依次回读：菜单按钮 → `衣柜=N` → 条件式 AnyState → State/Motion → 完整 clip 矩阵 → 场景对象/默认部件/形态键。然后用 Gesture Manager 进入 Play Mode，至少切换 `0 → N → 其他值 → N`，做正面、左右 90、背面、底部并检查所有已改变形态键对应区域的身体/衣服穿模。只有本套触发了头部重新适配，才增加 Persp 近景与转头动作。`vrcforge_run_validation_report` 和 `vrcforge_build_test_readiness` 通过后，才可处理下一套。

## 7. 已证明旧内容的清理

1. 旧 FX 层只能按 `vrcforge_scan_fx_animator` 返回的精确 `layerName`，逐层 preview `delete_layer`、批准、执行、全量回读；不得用模糊名批量删除。
2. 旧场景实例先 `vrcforge_scan_inbound_reference_closure`。结果必须完整、未截断且所有消费者已解绑；先 `vrcforge_set_gameobject_active(false)` 并完成替换套装的动态验收。
3. 只有用户对精确对象路径另行批准后，才调用 `vrcforge_delete_gameobject`。删除后回读对象不存在、FX/Menu/Parameters 无残留；来源 prefab/资产和来源 Avatar 永不删除。

## 8. 最短安全调用序列

所有项目内调用均附同一个绝对 `projectPath`：全量扫描 → 先读衣服已有动画 → 冻结固定值、对象、部件和成对形态键矩阵 → 确保 `衣柜` 并保留/复现已确认 FX 拓扑 → 预览/安装一套 → 必要时释放并显式复用固定值 → 全量重写所有已批准 clip 矩阵 → 核对衣柜选择转换且只修已证明冲突 → 必要时按证据适配头部对象 → 定点菜单 → 当前套静态/动态/穿模验收 → 下一套。全部套装闭环后，再逐层清已证明过时的 FX；最后做旧实例引用闭包、禁用、动态复验与单独批准删除。
