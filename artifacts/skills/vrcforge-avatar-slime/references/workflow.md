# Slime 逐步操作说明

这是制作 Slime 的操作指引，不会自动执行。只选择当前目标需要的步骤，复用已读且仍有效的数据。每个写入沿用已有授权、预览和检查点；不要为阅读本页增加确认、哈希或检查流程。工具名称是现有公开入口，调用参数以本次加载的 schema 为准；示意占位值必须替换成现场返回的值。

## 1. 确定范围，读正常状态

- **工具与先读数据**：用 `vrcforge_list_avatars`、`vrcforge_bind_execution_target` 确定当前项目/场景/头像；已有有效绑定继续用。用 `vrcforge_scan_materials` 先取目录，再按实际 material IDs 取所需详情；`vrcforge_get_gameobject` 确认 Renderer、材质槽、对象显隐和共享引用。保存正常状态的材质、Shader、颜色/alpha、纹理变换、有效队列及用户已有外观。
- **怎样做**：从实际网格分出身体、头部皮肤、眼睫/口腔/备用表情、头发、耳和尾巴；只给用户要 Slime 化的表面建立正常/Slime 对应关系。不把服装、镜片或整个面部图集一起染色。
- **起点与方向**：原正常状态就是恢复端点。透明度、颜色、部位与持续时间沿用已确认意图；缺少美术数值时可用 [参数试调表](parameter-tuning.md) 的有条件起点，不把某次头像路径或参数名当默认。
- **完成怎么看**：每个要变的部位都能定位到具体 Renderer/slot；正常外观有完整恢复值，眼睛和面捕不在不明批量覆盖范围内。
- **异常纠正**：同名材质不等于同一用途；缺部位时回到真实槽位与引用，不继续扩大扫描或覆盖所有材质。Editor 重载、对象重建等实际变化才按产品机制重新绑定。

## 2. 读取 Shader 能力，选准写入口

- **工具与先读数据**：`vrcforge_inspect_material_properties` 传 `projectPath`、`materialAssetPath`，用 `search`、`offset/limit` 取需要的属性页；它给静态序列化值，不是运行 GPU/PropertyBlock 读回。必要时用 `vrcforge_get_asset_info` 的 `includeText/textSearch` 读当前 Shader/preset 的来源、Range、关键字和 pass。
- **怎样做**：已知具体改动不必再调语义模型。声明的 Float/Range/Int 用 `vrcforge_preview_material_shader_assignment` → `vrcforge_set_material_shader` 的 `propertyChanges:[{propertyName,value}]`；有效队列可随这一模式的 `renderQueue` 修改。此入口不收 Color/Vector，也不与换 Shader/keyword 模式混用。
- **颜色入口**：inventory 的 `supported_properties` 已有对应语义键时，用 `vrcforge_preview_shader_apply` → `vrcforge_apply_shader_tuning`，`changes` 每项填扫描返回的 `material_id`、实际 `semantic_property` 和数值/色值 `after`。只有目标仍需语义规划时才用 `vrcforge_plan_shader_tuning`。
- **完成怎么看**：预览只影响选定材质/属性，类型和范围正确；写后保留原回执并读具体变化。原始 queue=-1 表示 Shader 默认，排序用有效值。
- **异常纠正**：不把原始 `_Color2nd` 当语义键、不往 scalar 接口塞 RGBA；无通用静态颜色/向量入口时报告这个精确限制。动画端点按第6步写，不伪装成静态材质写入。纯颜色微调不需要重查无关商品说明。

## 3. 准备 Slime 工作材质和纹理

- **工具与先读数据**：从第1步映射及源资产确认目标；`vrcforge_find_assets` 查已授权纹理，`vrcforge_get_asset_info` 读用途与导入状态。作者提供的特殊纹理先读随附说明/对应商品页并查看真实图像，区分底图、overlay、alpha、mask、normal。
- **怎样做**：`vrcforge_preview_project_asset_duplicate` → `vrcforge_duplicate_project_asset`，用精确 `sourceAssetPath/destinationAssetPath` 复制独立工作材质；目标遵守当前 schema 的生成目录，存在则不覆盖。换 Shader 用 shader-assignment 预览/写入口；`vrcforge_preview_renderer_material_slot` → `vrcforge_set_renderer_material_slot` 只改指定 slot，或留给第6步的开关驱动。
- **纹理分配**：`vrcforge_preview_material_texture_assignment` → `vrcforge_set_material_texture`，填实际 `materialAssetPath/propertyName/textureAssetPath`；可带 `textureScale:{x,y}`、`textureOffset:{x,y}`，不填就保留。多项可用 schema 的 `assignments`，不混单项和批次参数。
- **导入设置范围**：小平铺法线可先选已正确导入的 256–512 资源，近看再按实际缺陷调整，不缩小原头像大图。纹理压缩/Mip Streaming 可走 `vrcforge_preview_texture_import_settings` → `vrcforge_set_texture_import_settings`；平台、尺寸、格式、压缩、crunch、quality 必填项从当前状态保留，仅改本次相关项。该工具没有 NormalMap 类型、Wrap、sRGB 或生成 Mip 的写字段，不能编造这些参数；缺的按当前真实工具能力报告。
- **完成怎么看**：工作材质独立、正常材质还在，法线没有误当颜色底图；尾巴/发束透明轮廓未被白底纹理抹掉。材质引用成功后仍要看外观。
- **异常纠正**：部分透明补充贴图不能直接代替完整面/身体底图。`vrcforge_texture_patch` 只做 schema 支持的 PNG 局部补丁；新波纹生成由宿主实际图像能力承担，再用现有正式导入路径，不用 Shell 写 Unity 资产。导入结果未知时读原任务/目标，不重放导入。

## 4. 先调静止胶体，再调部位协调

- **工具与先读数据**：复用第2步实际属性和第3步工作材质；读 alpha 最终来源、反射/法线功能开关、每层贴图颜色，不只看 `_Color`。
- **怎样调**：按 [参数表](parameter-tuning.md) 依次调颜色/alpha → smoothness/reflectance → 法线/边缘；每次只调与当前现象相关的一组。用户要更透明时降低最终不透明度；用普通透明和可见内部测试物判断，先不增加折射。
- **部位差异**：身体先保留轮廓和透过；头部起点可更不透明，再检查内部表情；头发保留发束轮廓和成块高光；耳/尾与身体色调协调但保留局部 alpha。真实眼睛/睫毛保持可读，独立镜片不跟身体共用透明参数。
- **完成怎么看**：同光照下观察正面/斜侧，皮肤全覆盖且不变金属片；眼睛、唇和表情不随底色污染，尾尖/发丝不变实心矩形。光泽清楚但不白成一片。
- **异常纠正**：alpha 无效先查 Alpha Mask 替换/乘算和第二层，不盲降颜色值；黑脸先查完整颜色/运行覆盖；肉色残留按实际层定位。内层、假阴影、镜片的专门处理见 [问题处理](surface-and-transition.md)，不要全头改不透明或禁用面捕。

## 5. 水波：分离 UV，保留完整纹理变换

- **工具与先读数据**：读 Shader 实现和纹理变换；`vrcforge_scan_animation_bindings` 用真实 `clipPaths/bindingSelectors` 找写入路径；已分配的法线 Offset/Tiling 用纹理分配工具，动态曲线用 `vrcforge_preview_write_animation_curve` → `vrcforge_write_animation_curve`。
- **怎样调**：只有当前 Shader 确实支持时才用第二法线分离底图。保留原第一法线；为波纹选择正确 UV，设置非零 Tiling。独立 Offset 循环从周期等价位置回到起点，不滚动面部底图；试调密度、强度和周期按参数表。
- **绑定规则**：`animatorRootPath` 是实际 Animator 根；`bindingPath` 相对它且不含根名，`componentType/propertyName` 来自扫描/预览。场景效果不使用 `assetOnly`。Vector 的 XYZW 都明确保持，尤其纹理变换的 xy 不能因只写 zw 而归零。
- **完成怎么看**：进入 Slime 后多时刻 Offset 改变、Tiling 始终非零、两周期接缝无跳变；五官、底图和 alpha mask 留在原位，退出后恢复原值。单张 PNG 不能证明“在动”。
- **异常纠正**：偏移在动但没有波纹，检查法线强度、功能开关、光泽响应及 Tiling 是否清零；纹理在抖/脸在滑，先修 UV 作用范围，别继续加速度或强度。Shader 不支持独立变换时报告限制，不造不存在的 Scroll 字段。

## 6. 正常/Slime 开关与平滑过渡

- **工具与先读数据**：`vrcforge_scan_modular_avatar`、`vrcforge_inspect_modular_avatar_component` 读材质选择和序列化字段；`vrcforge_scan_fx_animator`、`vrcforge_scan_parameters`、绑定扫描读真实写入方、默认状态、目标 clip、过渡和参数类型。修改源 MA/FX，不编辑构建合并产物。
- **材质与菜单**：MA 引用配置用 `vrcforge_preview_add_modular_avatar_component` → `vrcforge_add_modular_avatar_component`；已有组件用 `action:update`、读回的 `componentIndex/expectedComponentGlobalObjectId`。确认是 MaterialSetter 的现场结构后，`fields.Objects` 各项用 `Object:{referencePath:<目标>}`、零基 `MaterialIndex` 和实际 `Material` 资产路径；这是整表替换，保留的原条目都要带上，不用通用 set_property 改这个嵌套表。Slime 参数用 `vrcforge_preview_manage_expression_parameters` → `vrcforge_manage_expression_parameters`，控制项用 `vrcforge_preview_manage_expression_menu` → `vrcforge_manage_expression_menu`，只改本开关。名称/位置/default/saved/synced 沿用约定，无约定的新开关以正常状态为默认；跨用户可见目标需要同步参数。
- **动画**：`vrcforge_preview_manage_fx_animator` → `vrcforge_manage_fx_animator` 配实际状态/过渡；曲线入口写连续颜色、alpha、法线强度等。缺层时用单项 `action:ensure_layer`，`edits` 批次只操作已有 controller/层，不能在批次里建层。空层第一个状态成为默认，先加入正常状态并在预览检查 defaultState。批次 `ensure_transition` 明确 `sourceStateName/destinationStateName`；省略 source 会变成 Any State。当前批次 duration 使用固定秒数，不另传 schema 不支持的 hasFixedDuration 字段；`hasExitTime:false` 用于需要即时响应开关的过渡，`canTransitionToSelf:false` 避免稳定状态反复重入。正反条件分开定义；中断设置从当前支持枚举选。材质引用和 Shader 选择是离散切换；入场/退场要与连续曲线匹配端点，不能只换引用称为平滑。
- **起点与方向**：已有时长优先；无要求的可见渐变可从约1秒试看，缩短更利落、加长更舒缓。入退都显式保留完整 RGBA/XYZW 与正常恢复值；曲线值按参数表。多条修改可用当前 schema 的 `curves`/已有 clip 的 `clips` 批次，新 clip 不塞进仅收已有 clip 的模式。
- **完成怎么看**：身体/头/发/尾同一开关同步，关→开→关都有连续效果；半途反向不闪黑、不跳到错误固定起点，正常材质/颜色/UV 完整恢复。Slime 之外的既有系统保持其原本行为。
- **异常纠正**：引用瞬换先检查离散交接时点；RGB 黑或 ST 缩放0要补完整分量；参数改了但没效果查 Animator 条件/MA引用与竞争写入方，不另复制整套身体规避。开关反复触发查自过渡及中断条件，依据实际 schema 修现有状态机。

## 7. 透明叠层与内部可见性

- **工具与先读数据**：材质 inspect、资产文本和绑定扫描读发生冲突各层的有效队列、ZWrite/ZTest/Cull、Stencil、alpha 和显隐来源；截图工具定位正面/侧面/背面疑点。
- **怎样调**：先沿作者正常配置确定相对绘制关系。刘海/FakeShadow 检查脸写标记、头发清除与投影读取；镜片区分反射、折射和遮挡；头内备用片先查真实显隐，再按局部 alpha、深度覆盖或 Stencil 选必要方案。详细因果和调整方向见参数表与问题处理，不设通用队列/Stencil ID。
- **内部测试**：用已授权的正常比较对象穿入胶体，选择能看出前后关系的姿势；`vrcforge_preview_scene_object_duplicate` → `vrcforge_duplicate_scene_object` 只在确需比较对象时使用。复制入口没有 position 参数；摆放用 `vrcforge_get_property` 先读副本 Transform 的 `localPosition`，再 `vrcforge_set_property`，指定 `gameObjectPath`、`componentType:UnityEngine.Transform`、实际 componentIndex、`propertyPath:localPosition`、`value:{x,y,z}`。根据当前比例/父物体坐标作小偏移；不固定照搬某个头像的坐标。此写工具内部有现成预览/审批，没有公开 preview_set_property，不编造该工具。只移动测试副本，不手改 Scene；已有测试物直接复用。
- **完成怎么看**：内部物体轮廓可见、外壳有体积；脸部 idle/眨眼/张嘴/目标表情依然正确，备用内层不泄露；后脑修正不能牺牲正面或让穿入物全消失。
- **异常纠正**：深度预写挡掉内部测试物就不能算通过；先收窄到头部/局部设计，不把同一 ZWrite 配置套全身。Unity 与客户端不一致时分别保留结论，不从一张背面图关闭正面问题。

## 8. 运行观察、保存与相关诊断

- **工具与先读数据**：`vrcforge_gesture_manager_status` 确认实际模型与运行状态；获授权后用 `vrcforge_gesture_manager_enter_play_mode`、`vrcforge_gesture_manager_set_parameter` 触发 Slime。多时刻采样用 `vrcforge_start_runtime_observation` → `vrcforge_get_runtime_observation`，只声明该任务实际返回的值/画面，不把静态材质值当 MPB 结果。
- **采样参数**：start 传当前 `projectPath/avatarPath`、Slime `parameterName/value`，以及 `durationSeconds/frameCount`、实际相机 `cameraPosition/targetPosition/upVector/fieldOfView`、`width/height`；用 `parameterSteps:[{timeSeconds,parameterName,value}]` 在同一任务反向。现有范围为0.1–10秒、2–32帧、图像边长128–512；约1秒变身可先观察3–4秒、16帧，在中途反向一次。不要先把开关打到终点再开始观察入场。长于10秒的水波周期分段观察，不能声称一个任务覆盖了整周期。
- **运行值读取**：`rendererProbes` 每项指定当前完整 `rendererPath`、零基 `materialIndex`、本次所需 `propertyNames`（最多8 probes、每项8属性）；这里是完整场景路径，不是 clip 的相对 bindingPath。start 返回 `jobId`，get 带同项目/头像/jobId，按需要用 `stateDetail:inline` 读帧。核对 status、sampledFrameCount、undersampled/coverageStatus、每帧 actualElapsedSeconds；Color/Vector读完整4分量并看 `valueSource`，区分 MPB、Renderer block、纹理变换及静态材质回退，缺 probe 不当0值，也不把有限采样说成全时段连续通过。
- **怎样检查**：观察入场、稳定至少一个水波周期、退场、半途反向；采样若因短时窗错过过渡，仅重采需要的区间。用 `vrcforge_capture_screenshot`/`vrcforge_capture_multi_screenshot` 留能看清脸、皮肤接缝、发尾与内部测试物的视角，不重复已有效的图片。
- **保存**：回到约定编辑状态后，`vrcforge_preview_save_asset` → `vrcforge_save_asset` 保存本次确切资产；`vrcforge_scene_save` 保存本场景。不全局保存无关 dirty 资产；Play 退出用 `vrcforge_set_play_mode`。保留原始编辑/开关状态，不自动删未获授权的测试对象或回滚。
- **相关报告**：Console 用 `vrcforge_get_compile_errors`，NDMF 用 `vrcforge_get_ndmf_errors`，SDK 用 `vrcforge_read_vrchat_sdk_builder_alerts`。只处理与本次 Slime 资产/运行直接有关的问题；有原生修复 action 时读取当前 source/index/message，`vrcforge_preview_diagnostic_fix` 后再 `vrcforge_apply_diagnostic_fix`，保留要求的原文及 SDK 目标头像。不自动修所有警告。
- **异常纠正**：no_report/panel_not_open/历史缓存不是本轮0错误；不支持的自定义 NDMF UI 不伪装可调用。回调返回不等于目标警告消失，读相关报告与资产实际状态；无关上传/插件错误另报，不扩写这个 Skill。

## 9. 性能、客户端边界与交付

- **工具与先读数据**：`vrcforge_scan_avatar_performance`（必要时现有 `vrcforge_run_validation_report`）比较正常/Slime 的新增材质、Pass 和纹理，实际有帧时数据才报耗时。客户端画面、好友手臂、镜子/相机及同步只能按真实测试报告。
- **怎样取舍**：不必要的整身透明重叠层、折射/模糊先去掉；单张平铺法线可按近景需要取舍，降低分辨率减少显存但不等于解决透明 overdraw。保留水波与内部可见的用户目标，不为性能指标擅自取消效果。
- **完成怎么看**：交付用户可见图片/短视频，以及正常/Slime 两种状态、过渡、水波、头内层、性能与客户端各自结论。已有 Unity 多帧结果可说明 Unity 验证；未做客户端检查就明确待验，不能声称正式游戏通过。
- **异常纠正**：Performance Rank 不覆盖全部 Shader 开销；managed latest 图片可能被下一次捕获覆盖，用当前宿主的附件机制留交付副本，不把“已查看 N 张图像”当用户收到图。模型上传、工具修复和独立衣柜/体型/物理改造不属于本 Skill。

## 参考与适用范围

Shader 参数语义、Range 与本版本字段先查现场；以下是机制资料，不是任意模型通用预设。
- [lilToon 属性声明](https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/Shader/lts.shader)、[法线采样实现](https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/Shader/Includes/lil_common_frag.hlsl)
- [lilToon 基本设置](https://lilxyzw.github.io/lilToon/ja_JP/base/base.html)、[法线](https://lilxyzw.github.io/lilToon/ja_JP/reflections/normal.html)、[光泽](https://lilxyzw.github.io/lilToon/ja_JP/reflections/reflection.html)
- [FakeShadow](https://lilxyzw.github.io/lilToon/ja_JP/base/fakeshadow.html)、[Stencil](https://lilxyzw.github.io/lilToon/ja_JP/advanced/stencil.html)、[Alpha Mask](https://lilxyzw.github.io/lilToon/ja_JP/color/alphamask.html)
- [MA Material Setter](https://modular-avatar.nadena.dev/docs/reference/reaction/material-setter)、[Unity MaterialPropertyBlock](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/MaterialPropertyBlock.html)
- [VRChat 优化建议](https://creators.vrchat.com/avatars/avatar-optimizing-tips/)、[Performance Rank 范围](https://creators.vrchat.com/avatars/avatar-performance-ranking-system/)

