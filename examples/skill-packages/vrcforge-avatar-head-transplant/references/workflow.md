# Head transplant workflow

## Outcome

Produce one target Avatar whose retained donor head, target body, body controls,
wardrobe, gestures, and optional face tracking behave as one system. Do not
move or delete the donor source. Do not remove the old target head until the
new head passes reference, static-pixel, dynamic-Gesture-Manager, and local
Build & Test gates.

VRCForge 1.7.9 is the hard runtime floor. True named `Bottom` capture, the
causal result contract, and deformation diagnostics are acceptance
requirements, not optional enhancements; an older runtime is `ready=false`
with the missing capability reported.

## Uncertainty rule

Whenever skeletons, neck seams, materials, accessories, PhysBones, Modular
Avatar, or VRCFury behavior is uncertain, consult a mature community guide,
the asset author's instructions, and relevant official documentation before
choosing a route. Use only evidence-supported actions; if evidence or an
exact atom is missing, stop with `capabilityGap=true` and report the gap. Do
not guess or invent tools.

## 1. Establish exact identities

1. Read the loaded Avatar list and both descriptors. Reject ambiguous paths,
   nested donor Avatar Descriptors, or more than one intended final Avatar.
2. Record the donor head mesh/renderers, donor Neck/Head chain, target
   Neck/Head chain, target body/wardrobe controls, current FX, Parameters, Menu,
   and every Modular Avatar or equivalent merge contributor.
3. Identify the one renderer intended to be active in the final Avatar. Read
   its exact path and active state, `Descriptor.VisemeSkinnedMesh`, lipsync mode,
   and every expected viseme, gesture, and expression animation binding. Record
   inactive/old/donor renderer paths separately; a binding count without exact
   path resolution is not closure.
4. Scan non-zero renderer bone usage. A renderer bone-array slot existing does
   not prove a live vertex dependency; a zero-weight slot also does not by
   itself make its Transform safe to delete.
5. Classify the donor head:

   - `face-tracked` only when the retained mesh BlendShapes, face FX/animation,
     Expression Parameters, and Expressions Menu are all proven and mutually
     referenced.
   - `gesture-only` when that four-part contract is absent or the user
     explicitly chooses not to retain it. Similar BlendShape names alone do
     not prove face tracking.

If any inventory result is truncated, unavailable, ambiguous, or lacks an
exact cause, stop before mutation and report that blocker.

### Community MA route and authority boundary

The user-supplied community sequence is authoritative for the default route:
match overall head/body scale; place the head Prefab under the target Avatar;
run MA Setup Outfit then Merge Armature, enabling name matching to the
integration target when required; use MA ReplaceObject so the retained head-side
Body reference resolves through the body-side Body path; then mark the original
target head/face `EditorOnly`. VRCForge supports supervised Setup Outfit and
Merge Armature atoms. MA ReplaceObject remains an exact manual/external step
when no dedicated atom is present; report `capabilityGap=true`, `ready=false`
instead of approximating it.

Fit the head in Unity first. Resolve the neck by classifying real visible
geometry, pose-dependent attachment, lighting/shader differences, and the
exact visible face-material slot before considering a localized head-texture
copy. Preserve the user's currently assigned body material and texture. An
overlap, MeshHoleShrinker, collar, or color-matched primitive is only an
explicitly user-accepted concealment fallback, never an actual seam fix.
Reconfigure EyeBone, viseme LipSync, Eyelids,
and ViewPoint and repair all FX paths changed by hierarchy/ReplaceObject work.
Do not write C# neck-vertex alignment, calculate a neck-ring diameter, or use
camera occlusion as acceptance.

The default community route performs no skin-matrix, determinant, bindpose,
transform-guessing, or sub-millimeter analysis. Such diagnostics
require separate explicit authorization and never pass visual acceptance. If a
single step reaches 20 minutes without a visible candidate, stop, capture the
current state, report it, and ask for direction.

## 2. Stage and fit the head

1. Create the pre-duplication checkpoint.
2. Preview and duplicate only the bounded donor head branch into the target.
   Do not duplicate a donor Avatar Descriptor, Pipeline Manager, whole-body
   Animator, unrelated body mesh, or source-only hierarchy.
3. Keep the copy inactive while fitting. Reparent it under the intended target
   Head chain and read back both local and world position, rotation, and scale.
   First fit the whole imported head with uniform local scale and position in
   Unity. If the actual neck aperture/mesh shape remains incompatible, stop for
   DCC mesh work rather than editing raw matrices or scaling the body.
4. Keep target body, armature, and clothing at their existing scale. Adjust the
   staged head/accessories locally; scaling the body to hide a head mismatch
   breaks clothing compatibility.
5. Inspect renderer `bones`, `rootBone`, bindpose/bone counts, non-zero bone
   indices, PhysBone roots/colliders, probe anchors, constraint sources,
   contacts, and every other external object reference before rebinding.

Static transform equality is not enough. A duplicate inner Neck/Head chain can
match at idle yet inherit target Head motion twice. During the rebinding gate,
prove that neck-weighted vertices follow the intended target Neck and that the
retained head/face vertices follow the intended Head chain. Preserve array
length/index stability unless a dedicated atom proves compaction safe.

For cat ears/tails, choose `independent_skeleton_physbone`: move the whole
accessory and its complete bounded local sway chain. Create a distinct
`installContainer` for uniform fitting and `swayChainRoot` for simulation. Cat
ears use MA Bone Proxy target=`Head`, or a direct parent of `installContainer`
to the target Avatar's `Head` as the exact equivalent; tails keep a separately
declared target such as `Hips`. Only `installContainer` may use uniform scale;
`swayChainRoot` and every descendant bone remain Rest local scale `(1,1,1)`.
Rebind PhysBone Root Transform, all Colliders, probe anchors, constraint
sources, contacts, and every other external object reference, with zero donor
or unresolved references, then prove closure for every non-zero renderer
used-bone slot. This is neither the rigid-accessory route nor the clothing
armature-merge route.

## 3A. Gesture-only branch

1. Scan donor left/right gesture conditions, clips, BlendShape paths, and the
   target body's FX/wardrobe controls.
2. Preview exact animation-path/reference changes and merge only the gesture
   layers needed by the retained head.
3. Preserve target body sizing, wardrobe, locomotion, and other target-authored
   controls. Do not replace the target FX or Parameters wholesale.
4. Do not copy any face-tracking parameter, menu control, face layer, or orphan
   animation. Scan the final state for residual face-tracking consumers.
5. Read back the final Descriptor and binding paths. The active renderer must be
   the viseme/lipsync target and receive every expected retained gesture and
   expression binding. No inactive/old/donor renderer may remain a Descriptor
   target, the sole Animator target, or the only path satisfying a binding.

## 3B. Face-tracked branch

Treat the following as one transactionally staged contract:

1. Mesh: the retained renderer exposes every required BlendShape, and every
   animation binding resolves to that exact renderer path.
2. FX/animations: face layers, smoothing/drivers, visemes, tongue/eye/lip logic,
   and ordinary left/right gesture fallback remain compatible.
3. Parameters: merge all contributors without name/type/default conflicts.
   Measure the final Modular Avatar/NDMF/Gesture Manager build result, not only
   the smaller Parameters asset directly assigned on the Descriptor.
4. Menu: every face control resolves to a final parameter and the body/wardrobe
   controls remain reachable.
5. Active-renderer closure: `Descriptor.VisemeSkinnedMesh` and lipsync resolve to
   the active renderer; every expected FX/controller binding resolves to an
   existing BlendShape on it; every retained Parameter/Menu control reaches that
   controller and renderer. Inactive/old/donor renderers have zero unique or
   sole face consumers in the final target Avatar.

Face tracking does not replace gesture fallback. If a runtime disable flag
blocks gestures, diagnose that FX condition instead of rewriting gesture
clips. Protect face protocol parameters from unused-parameter cleanup unless
all four consumers are proven absent. Final synced parameter cost must stay at
or below the VRChat limit.

## 4. Supervised write and causal-result contract

For each mutation batch:

1. Read the exact preview and checkpoint identity.
2. Explain the target, fields, expected effect, and rollback boundary.
3. After approval, let the runtime supervision layer invoke the exact matching
   write atom; the workflow never calls an approval-helper tool itself.
4. Preserve and inspect identically on the internal Agent loop and external MCP
   Agent:
   `success`, `status`, `error`, `failedStep`, `diagnostics`, `ready`,
   `blockingReasons`, `failureLayer`,
   `failurePhase`, `failureCause`, `rootCause`, `causeChain`, `observed`,
   `expected`, `delta`, `mutationStarted`, `committed`, `commitState`,
   `sceneSaved`, `persistedReadback`, `evidence`, `recovery`, and `nextAction`
   when present.
5. A successful call with `ready=false` is a successful inspection of a
   blocked domain state, not readiness. A proposal, timeout, or `ok` wrapper is
   not commit proof.
6. If commit state is unknown, do not retry. Read the exact target and scene
   state first. If the cause is still missing, stop and report the contract
   deficiency rather than inventing it.

Rollback is never automatic. Request it separately only after naming the exact
failure, checkpoint, current-state loss, and expected recovery. If rollback
fails, preserve the current state and report the rollback cause.

## 5. Static and dynamic acceptance

Use Unity Scene view through Gesture Manager, not the Game camera. Temporarily
hide hair, collars, or other occluders only when their before-state is recorded
and restored afterward.

Capture and verify the returned rotation/scope:

| View | Rotation | Required coverage |
|---|---:|---|
| Front | pitch 0, yaw 0, roll 0 | full face, neck ring, and both shoulders |
| Side left | pitch 0, yaw +90, roll 0 | jaw-to-neck contour and shoulder junction |
| Side right | pitch 0, yaw -90, roll 0 | jaw-to-neck contour and shoulder junction |
| Back | pitch 0, yaw 180, roll 0 | full rear neck ring and hairline |
| Bottom | pitch -90 | true under-chin view with the whole neck opening and shoulder boundary |

A named angle is invalid evidence if its returned rotation differs or the
attachment is cropped. At idle and in motion, fail on any open rim, hollow or
internal face, backface, overlap, floating strip, hard geometric step, normal,
shading, or material-color discontinuity.

Named acceptance views use an orthogonal basis: Side Left/Right are pitch 0
with yaw +90/-90, Back is pitch 0/yaw 180, and Bottom is pitch -90/yaw 0.
Any oblique pitch is auxiliary evidence only and never counts toward the five
required views; validate the returned rotation and camera evidence.

Run at least:

- locomotion (`VelocityZ` non-zero), turning (`AngularY` non-zero), and return
  parameters to their prior values;
- left and right gestures with face tracking disabled/fallback active;
- for the face-tracked branch, representative eye, lip/viseme, tongue, and
  expression controls;
- target body sizing and default wardrobe controls.

When cat ears are present, record
separately authorized `vrcforge_inspect_skinned_mesh_deformation` diagnostics at Rest and again at
AFK, head-down, left-turn, and right-turn. Rest and AFK must use the exact same
camera position, target, projection, crop, and close-root framing across Front,
both Sides, Back, and Bottom; pair later motion poses to that Rest framing. Read finite
vertices, Rest/world AABB, distance percentiles, and reconstructed skin-matrix
metrics; the tool diagnoses binding/deformation and is not a routine matrix
repair step. Report non-finite vertices, root drift, a seam/gap that changes
size between poses, sudden AABB inflation/collapse/translation, or unexplained
skin-matrix jumps as authorized diagnostic anomalies; only verified pixels and
behavior determine visual acceptance.

The head, body, hair, ears, and other retained parts must stay connected and
deform through the intended single chain. Only after these pixel and behavior
gates pass may local Build & Test run. Build success never cures a visible
seam and is not remote upload proof.

Before removing the old head, compare the complete expected binding set against
the final exact paths. Hard-fail when the active renderer is absent from viseme,
lipsync, gesture, or expression consumers; when any expected binding is missing;
or when an inactive/old/donor renderer remains the Descriptor target or the
Animator's only target. For face tracking, also hard-fail incomplete Parameter,
Menu, FX/controller, Descriptor, or active-renderer BlendShape closure.

The community visual minimum is same-framing Rest and AFK front/back/left/right
neck close-ups at roughly 1–2 m normal VRChat viewing distance. VRCForge retains
its stricter Bottom view when underside closure matters. Pass only when pixels
show no visible gap or floating geometry; on failure classify the visible
geometry, pose, lighting, material-slot ownership, or localized texture layer
and repair only that layer. Concealment needs separate explicit user acceptance.
Matrix or remote
screenshot values cannot substitute for verified free-camera pixels.

## 6. 5.6-sol structural routes and failure matrix

Select one route: `rigid`; `same_skeleton_smr` (weights, bones/rootBone,
bindposes, and index parity); `foreign_skeleton_smr` (explicit minimal weighted
chain); `independent_skeleton_physbone` (distinct fit/sway nodes plus complete
root/collider/probe/constraint/contact/external-reference closure); or
`animator_menu` (exact path/parameter remap retaining target
controls). Resolve size mismatch in this order: measure attachment space, first
fit the imported whole-head/accessory `installContainer` with uniform scale and
position in Unity, and keep the distinct `swayChainRoot` and every descendant
bone at Rest local scale 1. Only after separate diagnostic authorization may a
non-community route inspect bindpose/skin-matrix metrics; then set `needsDccRerig=true` only for
an unreconciled real mesh aperture/shape or donor-specific weights. Never resize
the target body or armature.

The mandatory skinning contract reads back `bones`, `rootBone`, `bindposes`,
non-zero weighted indices, bone/index count parity, and mixed-chain closure.
Missing or truncated atoms produce `capabilityGap` and `ready=false`, never a
pass. The free-camera owner may return `angle`, `rotation`, `scope`,
`returnedRotation`, `unobstructed`, and `evidence`; absent or mismatched fields
invalidate the view.

Failure reports retain `success`, `status`, `error`, `failedStep`,
`diagnostics`, `failureLayer`, `failurePhase`, `failureCause`,
`rootCause`, `causeChain`, `observed`, `expected`, `delta`, `blockingReasons`,
`capabilityGap`, `needsDccRerig`, mutation/commit/save/readback state,
`evidence`, `recovery`, and `nextAction`. Wrong parent/bindpose, duplicate
Neck/Head inheritance, donor PhysBone closure, partial Mesh/FX/Parameters/Menu,
and unavailable scans are diagnosed explicitly. Recovery disables the staged
copy or restores its checkpoint, then reruns unobstructed five-view evidence
across Rest, AFK, Upright, VelocityZ, AngularY, body-size, and face extremes.

Only outside the community route and after separate diagnostic authorization
may neck analysis measure ring center, outward normal, radius, pivot, and bone
roll. The default route uses Unity-first fitting and layered visible-interface
repair; overlap concealment is an explicitly user-selected fallback. Fit only the
imported head's local transform. Preserve target bindposes, transfer only the
minimal weighted chain, and preserve Basis/BlendShape order plus exact ARKit
names. Unweighted null slots are informational; unresolved used bones or
bindposes block. PhysBone A/B isolation records state, temporarily disables,
captures Rest/dynamic evidence, restores, and reads back. Any unreconciled
geometry, bindpose, weight, Shape Key, or restore state requires a concrete
`failureCause`, `needsDccRerig=true`, and `ready=false`.

## 7. Unity 直接换头：社区实际操作顺序

本节优先于前面的抽象诊断说明。默认路径是在 Unity 内通过 Modular
Avatar 完成，不要求先导出 FBX 或打开 Blender。仅使用本人有权使用且
授权允许改作的头像与配件；保留原 Prefab，在临时副本上操作。

### 7.1 清点并固定身体基线

1. 确认工程已经安装 VRChat Avatar SDK、Modular Avatar 及模型所需 shader。
2. 把身体侧和头部侧 Prefab 放入同一 Unity 场景；身体侧作为唯一最终
   Avatar，保留它的 Descriptor、骨架、衣柜、身体调节和原始身体材质。
3. 使用 `vrcforge_list_avatars`、`vrcforge_read_avatar_descriptor`、
   `vrcforge_get_gameobject` 和 `vrcforge_get_property` 读取两个对象。
4. 加载 `materials` 工具块，再用 `vrcforge_scan_materials` 记录当前
   身体 renderer、材质槽、shader、主贴图、遮罩和 Probe Anchor。
5. 身体基线是用户此时真正选择的材质与纹理，不是默认材质、旧截图、
   第三方皮肤或代理自行挑选的替代贴图。没有单独批准不得修改它。
6. 用 BlendShape、FX、参数、菜单和动画绑定扫描判断头部属于
   `face-tracked` 或 `gesture-only`。

### 7.2 在 Unity 中准备头部工作副本

1. `vrcforge_preview_scene_object_duplicate` 预览来源头部所需分支，
   经审批后 `vrcforge_duplicate_scene_object` 创建工作副本。
2. 只保留头、脸、所需头发、眼睛、舌头、面捕和必要骨链；不需要的来源
   身体、衣服和配件可以先隐藏或标记 `EditorOnly`。不要把仍需合并的
   Armature 骨链标记 `EditorOnly`，也不要更改来源 Prefab。
3. 来源头部副本不得保留第二个 Avatar Descriptor、Pipeline Manager 或
   整体 Avatar Animator。删除组件属于写操作，必须先审批并确认该组件
   不是面捕构建组件或仍被最终动画引用的必要对象。
4. 通过 `vrcforge_reparent_gameobject` 将副本放进目标 Avatar 层级，
   使用 `vrcforge_set_property` 仅调整头部容器或来源 Head 的位置、
   旋转及统一 XYZ 比例；不得为了合头缩放身体、原 Armature 或衣服。
5. 临时隐藏头发、衣领和遮挡手臂，从正面、两侧、后面和必要的下方检查
   下巴、颈口和头身比例。先处理真实开口；整脸肤色不同不是几何漏洞。

### 7.3 Modular Avatar 合骨与面部替换

1. 在 Unity 内对头部副本右键，选择 `[ModularAvatar] > Setup Outfit`；
   VRCForge 原子组合为 `vrcforge_preview_setup_outfit`，审批后调用
   `vrcforge_setup_outfit`。
2. 检查头部副本的 Armature 是否获得 `MA Merge Armature`，其
   `Merge Target` 应指向目标 Avatar 对应的 Armature/骨节点。需要增加
  组件时先调用 `vrcforge_preview_add_modular_avatar_component`，再审批
   `vrcforge_add_modular_avatar_component`。
3. 骨名不一致时使用 MA Inspector 的 `Adjust bone names to match target`
   或当前版本等价按钮。确认 Neck、Head、眼骨和必要舌骨在 Play Mode
   跟随目标骨架，来源特有骨只在确实需要时保留。
4. 如果旧脸与新脸需要保留既有表情/动画路径，在**新脸 renderer 对象**
   上添加 `MA Replace Object`，将 `Replacement Target` 精确设为
   **目标 Avatar 的旧脸 renderer**。若两边对象都叫 `Body`，先根据
   renderer、材质和 BlendShape 确认它是旧脸，不是必须保留的身体躯干。
5. `Replace Object` 能重写原对象的动画路径和同类型组件引用，但不会
   模糊匹配不同类型组件；同一旧对象也不能被多个组件重复替换。若
   VRCForge 没有对应的精确 MA 组件写原子，停下等待用户手动在 Unity
   Inspector 配置，不得用猜测性改名或 Bone Proxy 假装完成。
6. 来源的非脸部 mesh 或目标旧脸只在依赖扫描、替换关系和构建结果都
  确认后标记 `EditorOnly`；不要先删除旧脸再修引用。

### 7.4 Descriptor、面捕和手势

1. 读取最终 Avatar Descriptor，重新指向新头部的 `LeftEye`、
   `RightEye`、LipSync/Viseme Skinned Mesh、Eyelids Mesh 和 ViewPoint。
2. 有面捕时同时保留四组内容：脸部 Mesh/BlendShape、FX/手势与动画、
   Expression Parameters、Expressions Menu。任何一组断链都不是完成。
3. 用动画绑定和入站引用扫描逐项检查每条曲线确实指向最终**启用**的
   新脸 renderer，而不是 staging 分支、旧脸或已隐藏来源对象。
4. 保留身体原有衣服开关、胸部或体型调节、基础 FX 和左右手普通手势；
   不要为了导入面捕直接覆盖整个目标控制器或菜单。
5. 最终参数和菜单应以 Play Mode/MA/NDMF 的合并结果为准；检查参数位
   预算以及 face tracking 关闭时普通手势仍然有效。

### 7.5 接缝：先社区标准材质适配，再决定是否需要局部贴图

1. 先隐藏头发和衣领，水平观察实际露出的下巴前缘、颈口与肩颈交界。
   调整被头发遮住的内圈不等于修复可见接缝。
2. 按顺序区分问题：真实几何开口 → 转头才出现的骨权重断层 →
   shader/受光差 → 错误 renderer 材质槽 → 局部纹理颜色边界。
3. 社区常用的 Unity 方案是以身体兼容的材质设置作为头脸材质模板，但
   **保留头脸自己的主贴图**：预览并复制合适的身体/身体侧脸部材质，
   再把来源脸贴图放回复制材质的 Main Texture。眼睛、泪膜、表情覆盖层
   保留各自原有材质，不可把身体材质直接覆盖整个 renderer 全部槽位。
4. 材质模板复制使用 `vrcforge_preview_project_asset_duplicate` 和审批后
   的 `vrcforge_duplicate_project_asset`。已有贴图写入使用
   `vrcforge_preview_material_texture_assignment` 和审批后的
   `vrcforge_set_material_texture`；renderer 材质槽若没有精确原子，停在
  用户手动 Inspector 指派，不得伪造写入能力。
5. 对照头与身体的实际 lilToon shader、Probe Anchor、Lighting Min/Max、
   Shadow Color/Strength/Border/Blur、主色 tint 和纹理 HSVG；先统一
   同一 Probe Anchor 与受光，再用 `vrcforge_plan_shader_tuning` 生成
   最小计划，经审批后 `vrcforge_apply_shader_tuning`。
6. 若头身使用相同 shader 设置后仍有可见色界，先用同一相机和局部诊断
   确认真实 face renderer 与材质槽。嘴、鼻、额头、眼球可能是不同槽，
   不能凭名字把 `Expressions` 或眼球槽当作皮肤槽。
7. 只有确实需要时，用户可在外部绘图工具制作头脸主贴图副本：从当前
   身体**实际显示颜色**取样，补偿头部材质 tint，只对可见颈口/下颌
   对应 UV 岛做小范围柔边。原身体贴图、mouth/nose/eyes/异色瞳、alpha
   和 mask 外像素全部保持不变。
8. VRCForge 当前没有局部 UV 绘制、法线编辑或网格重拓扑原子。需要
   这些步骤时必须返回 `capabilityGap=true`、`ready=false` 和
   `failureCause=seam_authoring_atom_unavailable`；用户提供已完成贴图
   后才能通过已有材质赋值原子继续。
9. MeshHoleShrinker 是 Unity 外部插件路线：仅当用户允许安装并确实有
   网格颈洞时使用，从正下方对准洞口、再从侧面调整，确保舌头和脸部
   shape key 没被卷入。项圈、遮挡球或简单重叠只是用户明确选择的遮盖，
   不得描述为已经修复几何或色差。

### 7.6 最終验收与原子操作组合

1. 每个 Unity 写入均按 `读取/扫描 → 预览 → 用户批准 → checkpoint →
   精确原子写入 → 场景或资源持久化读回` 执行；rollback 永远需要另行
   说明原因并获得批准。
2. Gesture Manager 中记录并恢复参数基线；检查 Rest、AFK、低头、左右
   转头、前进、身体大小、衣服开关和面捕/普通手势。
3. 使用 `vrcforge_capture_status` 与 `vrcforge_capture_screenshot`，
   在同一相机位置拍正面、左侧、右侧、背面及必要的底部；分别检查亮灯
   和暗灯。无遮挡画面必须包含下巴、颈口和肩线。
4. 需要保持 4096 原图时，通过 `vrcforge_preview_texture_import_settings`
   和 `vrcforge_set_texture_import_settings` 检查/修正最大尺寸；若开启
   mipmap 并且 SDK 需要 Streaming Mip Maps，则一并设置。
5. 读取 `vrcforge_read_vrchat_sdk_builder_alerts` 与 Build readiness；
   仅在视觉、动作和真正的 SDK 阻断都通过后运行本地 Build & Test。
   验收必须保持 `uploadAttempted=false`、`published=false`。

### 社区与作者文档

- Unity 直接换头社区实操：<https://note.com/k_sitiya/n/nf8ea57b8b2a6>
- Unity + Modular Avatar 换头与正确 Replace Object：<https://note.com/kesera2_vrc/n/n7f9560916970>
- Unity 换头、MeshHoleShrinker 和身体材质模板：<https://daisuki-vrc.com/kimera>
- Modular Avatar Setup Outfit：<https://modular-avatar.nadena.dev/docs/tutorials/clothing>
- Modular Avatar Merge Armature：<https://modular-avatar.nadena.dev/docs/reference/merge-armature>
- Modular Avatar Replace Object：<https://modular-avatar.nadena.dev/docs/reference/replace-object>
- VRChat PhysBones：<https://creators.vrchat.com/common-components/physbones/>
