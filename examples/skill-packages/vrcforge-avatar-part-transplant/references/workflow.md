# Part transplant workflow

## Outcome

Copy one bounded, user-authorized and properly licensed source part into a
target Avatar while leaving
the source unchanged. The target copy must have the intended geometry,
materials, live weighted bones, PhysBones/colliders, probe anchors,
constraints, animation/controller consumers, and attachment motion—without a
second Avatar Descriptor or unrelated donor hierarchy.

VRCForge 1.7.9 is the hard runtime floor. True named `Bottom` capture for an
underside-dependent attachment, the causal result contract, and deformation
diagnostics are acceptance requirements; an older runtime is `ready=false`
with the cause preserved.

## Uncertainty rule

Whenever skeletons, neck seams, materials, accessories, PhysBones, Modular
Avatar, or VRCFury behavior is uncertain, consult a mature community guide,
the asset author's instructions, and relevant official documentation before
choosing a route. Use only evidence-supported actions; if evidence or an
exact atom is missing, stop with `capabilityGap=true` and report the gap. Do
not guess or invent tools.

## Unity 直接提取和移植配件：社区操作清单

默认直接使用 Unity 和 Modular Avatar，不要求先导出 Prefab 或经过
Blender。只有拥有合法源文件并且许可允许改作时才可提取；不得从他人
已上传 Avatar、抓取文件或无授权素材中取得配件。

### 1. 确定来源对象和目标安装点

1. 在同一个 Unity 场景放入来源 Avatar 与目标 Avatar，保持来源完整。
2. 展开来源 Hierarchy，选择真正可见的眼镜、耳朵、尾巴、头发、衣服
   或其他配件 mesh，不仅看 Transform 骨骼图标。
3. 使用 `vrcforge_get_gameobject`、`vrcforge_get_property`、
   `vrcforge_inspect_skinned_mesh_bone_usage` 与
   `vrcforge_scan_inbound_reference_closure` 清点实际 renderer、材质、
   非零权重骨、PhysBone、Collider、Constraint、Contact 和动画引用。
4. 分类：无蒙皮配件为 `rigid`；有独立耳朵/尾巴摇摆链为
   `independent_skeleton_physbone`；使用目标兼容身体骨架的服装为
   `same_skeleton_smr`；保留其他模型身体权重的服装为
   `foreign_skeleton_smr`；开关和菜单属于后置 `animator_menu`。
5. 骨名长得一样或对象刚好在正确位置，不代表它已经引用目标骨架。

### 2. 眼镜、帽子和其他刚性物件

1. 预览后审批复制唯一配件分支；不要复制来源 Avatar Descriptor、
   Pipeline Manager、完整 Animator 或整套 Hips/Spine 骨架。
2. 用 `vrcforge_reparent_gameobject` 直接挂到目标 Head、Hand 或 Chest，
   或预览并添加 `MA Bone Proxy`，将 `Target` 指向准确目标骨。
3. 若配件已经被用户手工摆到正确位置，Bone Proxy 使用
   `As child keep world pose`，避免安装后跳回骨头原点；新通用配件
   需要对齐骨原点时才使用 `As child at root`。
4. 只调整配件本地位置、旋转和统一比例；至少从正面和侧面验证。
   不要对刚性物件使用 Setup Outfit 或 Merge Armature。

### 3. 耳朵、尾巴和独立 PhysBone 配件

1. 复制配件 mesh 与整条真实摇摆骨链，不能只拖 renderer 或耳尖骨。
2. 建立安装容器 `installContainer` 和独立摇摆根 `swayChainRoot`。
   猫耳目标为 `Head`；尾巴通常为 `Hips`；用户指定其他合法骨位时以
   明确目标为准。
3. 使用 direct parent 或 `MA Bone Proxy` 挂载安装容器。整体位置、大小
   和左右对齐只改 `installContainer`；摇摆根和每个内部骨维持其正确
   Rest local scale，当前标准结构为 `(1,1,1)`。
4. 如果用户希望手动拖动猫耳，先保持当前骨链状态和局部位姿证据；只在
   用户批准后临时解除影响操作的连接，用户调整完成后恢复正式左右耳骨
   与 PhysBone 关系，同时读回用户实际调好的 mesh 位置和外轮廓。
5. 重新绑定 `VRCPhysBone.Root Transform` 到复制后的自有摇摆根；把
   Collider、Constraint Source、Contact、Probe Anchor 等引用全部改到
   最终 Avatar 或已复制的配件分支。不能继续引用来源 Avatar。
6. 如一个骨或末端链无法摆动，检查 PhysBone `Endpoint Position`、
   Ignore Transforms、Limit 与 collider；同一对象不同时被相互冲突的
   Constraint 和 PhysBone 驱动。
7. 头发开启时看整体美观，关闭时检查耳根是否真正贴入头皮；必要时只调
   耳朵中上段外观，不能为了埋耳根把耳尖整体压到错误高度。
8. 在 Gesture Manager 复查 Rest、AFK、低头、左右转头和移动：耳根不
   浮空、不瞬间放大、无冻结、无来源骨引用，尾根不穿裙且跟随 Hips。

### 4. 兼容衣服和异体衣服

1. 已适配目标 Avatar 的服装：把服装 Prefab 放到目标根下，右键
   `[ModularAvatar] > Setup Outfit`；VRCForge 用
   `vrcforge_preview_setup_outfit` 和审批后的 `vrcforge_setup_outfit`。
2. 确认 `MA Merge Armature` 指向正确目标骨架；必要时执行
   `Adjust bone names to match target`。共享骨应复用目标 Avatar，独有
   裙摆或饰品骨才合并新增。
3. 使用服装作者提供的 body hide / breast size / body shape 等
   BlendShape 解决穿模；需要同步体型时添加 `MA Blendshape Sync`。
4. 若是异体服装，先尝试 Unity 中容器、服装骨位置和作者提供的对应
   BlendShape；只有实际 mesh 形状或原身体权重无法适配，才使用已有
   MochiFitter 转换配置或 Blender 外部重新适配，再回到 Setup Outfit。
5. 没有匹配源/目标 profile 时不要声称 MochiFitter 可用；没有目标权重
   转移原子时不要用 C# 临时脚本冒充 VRCForge 原子能力。

### 5. 可选开关和最终验收

1. 如果配件需要衣柜开关，依次预览并批准 Expression Parameter、
   Animator State 和 Menu Control 原子；只有三者引用一致才算完成。
2. MA Merge Animator、MA Menu Item 或 VRCFury Toggle 已在工程中并经
   用户选择时，可使用作者标准配置；缺少对应精确原子时由用户在 Unity
   手动完成，不得伪造组件写入。
3. 检查 On/Off 视觉变化、Play Mode 骨架运动、PhysBone 摆动、用户
   调过的位置、来源对象未变化、唯一最终 Descriptor 和全部引用闭合。
4. 每个写入均需要预览、用户审批、checkpoint 与持久化读回；失败不能
   自动 rollback，更不能删除来源模型。通过 SDK Alerts 和本地 Build &
   Test 后仍然不得替用户上传。

### 社区与作者文档

- Modular Avatar 官方服装教程：<https://modular-avatar.nadena.dev/docs/tutorials/clothing>
- Merge Armature 适用范围：<https://modular-avatar.nadena.dev/docs/reference/merge-armature>
- Bone Proxy 及保留世界位置：<https://modular-avatar.nadena.dev/docs/reference/bone-proxy>
- Merge Animator：<https://modular-avatar.nadena.dev/docs/reference/merge-animator>
- VRChat 官方 PhysBones：<https://creators.vrchat.com/common-components/physbones/>
- VRChat 用户内容及版权：<https://hello.vrchat.com/legal>

## 1. Bound the part and its dependencies

1. Identify one source Avatar, one target Avatar, one exact part root, and the
   intended target parent bone. Reject ambiguous paths and whole-avatar roots.
2. Read every renderer, material, component, child Transform, and current
   local/world transform in the part branch.
3. Inspect each SkinnedMeshRenderer's non-zero bone indices, `bones`,
   `rootBone`, and bindpose/bone counts. Use live weights to derive the minimal
   chain; do not copy every serialized bone slot.
4. Read every PhysBone root, collider list, probe anchor, constraint source,
   contact, and other component reference. A collider is retained only when a
   copied or target component actually consumes it.
5. Scan animation bindings, FX, Menu, Parameters, and framework path/object
   references. Distinguish source-project assets that mention the donor from
   consumers that must exist on the final target.

If closure is truncated, unavailable, or ambiguous, stop. A zero result from a
failed/truncated scan is not proof. Hidden or inactive is not unused, and
visible is not self-contained.

### Route before choosing tools

Keep the existing route ids and classify before any write:

| Route | Use for | Required operation | Never do |
| --- | --- | --- | --- |
| `rigid` | Glasses, earrings, hair clips, weapons, rigid wings, or another part with no donor-body skin | Attach to one exact target bone by direct parent or MA Bone Proxy; fit and read back local position, rotation, and scale; optionally prove toggle on/off | Setup Outfit, Merge Armature, or copying the donor armature |
| `independent_skeleton_physbone` | Cat ears, tails, or another accessory with its own bounded sway chain | Copy the whole accessory and complete root-to-tip chain; create distinct `installContainer` and `swayChainRoot`; for cat ears set MA Bone Proxy target=`Head` or direct-parent `installContainer` to target `Head`; fit only `installContainer` uniformly; keep `swayChainRoot` and every descendant bone at Rest scale `(1,1,1)`; close PhysBone root/collider/probe, constraint, contact, and every external reference; prove renderer used-bone closure | Setup Outfit, Merge Armature, partial chain copy, donor-only references, or changing the sway-chain root scale to hide a fit error |
| `same_skeleton_smr` | Clothing or close-fitting weighted accessories already compatible with the target skeleton | Author/prove a target-compatible armature merge or explicit used-bone remap; prove weights/bones/rootBone/bindposes and run deformation acceptance | Treat as a rigid accessory |
| `foreign_skeleton_smr` | Clothing or close-fitting/donor-body-weighted accessories on an incompatible skeleton | Author an armature merge or map every used bone explicitly; stop for DCC work when target rest/bindposes cannot be reconciled | Guess by matching names or copy an entire donor armature |
| `animator_menu` | Optional behavior layered after one attachment route | Add only the exact required consumers and prove their behavior | Use behavior assets to choose the geometry/skeleton route |

### Community route details

- Compatible clothing: import/place the clothing Prefab under the Avatar, run
  MA Setup Outfit, then use the clothing author's documented body BlendShapes
  for penetration.
- Non-compatible clothing: prefer external MochiFitter only when source and
  target conversion profiles both exist. Otherwise use external Blender to
  scale/position the clothing armature, transfer target-body weights, export
  FBX, reimport, and run MA Setup Outfit. Unity C# weight transfer or vertex
  realignment is forbidden.
- Rigid accessories: MA Bone Proxy to the exact target bone, fit local TRS,
  optionally add MA Toggle/Menu Installer, and prove position from at least two
  visible angles. No matrix analysis, weight transfer, or armature merge.
- Independent PhysBone accessories remain accessories, not head swaps. MA Bone
  Proxy attaches `installContainer`; uniform fit stays on that node while
  `swayChainRoot` and descendants remain Rest scale 1. Rebind Root Transform and
  the complete Collider/Constraint/Contact/external-reference closure, then
  prove natural Play-mode swing without distortion or frozen bones.

Default acceptance uses verified free-camera pixels at roughly 1–2 m normal
VRChat viewing distance. Matrix, determinant, bindpose, transform-guessing,
and sub-millimeter analysis is prohibited unless separately
authorized and never counts as visual acceptance. If one step reaches 20 minutes
without a visible candidate, stop, capture/report the current state, and ask for
direction.

Module Creator is an optional external/manual preprocessing step for exporting a
donor branch as a reusable Prefab. VRCForge has no internal Module Creator
export atom. If that exact export is required, report
`capabilityGap=true`, `ready=false`, and
`failureCause=module_creator_export_atom_unavailable`; do not fabricate an
export result. VRCForge can attach an already available Prefab afterward.

For cross-model size mismatch, first fit the distinct imported
`installContainer` with uniform scale and position in Unity. For an independent
dynamic accessory, the separate `swayChainRoot` and every descendant bone still
remain Rest local scale `(1,1,1)`. Escalate to Blender/DCC only
when the real mesh aperture,
shape, or mesh-level proportions must change, or when a close-fitting part
retains donor-body-specific weights. Direct target bone/Bone Proxy attachment,
PhysBone Root/Collider rebinding, and uniform container fitting are Unity-side
operations and are not DCC reasons. Bindpose/skin-matrix metrics are diagnostic;
raw matrix editing is not a routine user step.
For an independent PhysBone accessory, preserve its internal mesh-to-sway-chain
binding. Do not run the target-armature single-slot remap merely because its
renderer is skinned to that local chain; reclassify only if live weights depend
on the donor body outside the bounded accessory branch.

## 2. Stage the copy

1. Create the pre-copy checkpoint.
2. Preview and duplicate the minimal bounded branch. Never move/delete the
   donor and never copy its Avatar Descriptor, Pipeline Manager, whole-body
   Animator, unrelated body, or entire armature.
3. Create the target copy inactive. Reparent it to the intended target bone,
   preserve or deliberately fit world pose, and read local/world transforms
   back after the operation. A `preserveWorldTransform` receipt is not enough.
4. Keep target body and clothing scale unchanged. Fit only the copied
   `installContainer` with uniform local scale and position. For a dynamic
   accessory, keep its distinct bounded `swayChainRoot` and every descendant
   bone at Rest local scale 1.

## 3. Rebind target-side dependencies

Use separate preview-bound approvals for the smallest coherent batches:

- for `rigid`, direct-parent or preview/add MA Bone Proxy to the exact target
  bone, then read back the proxy target and local TRS; do not call Setup Outfit
  or add Merge Armature;
- for `independent_skeleton_physbone`, keep the complete bounded sway chain and
  distinct `installContainer`/`swayChainRoot`; for cat ears hard-code MA Bone
  Proxy target=`Head` or direct-parent `installContainer` to target `Head`;
  scale only `installContainer`, keep `swayChainRoot` and every descendant bone
  at Rest scale `(1,1,1)`, and
  explicitly close PhysBone Root, Collider, probe, constraint-source, contact,
  and every other external object reference before proving every non-zero
  renderer used-bone slot;
- only for the two clothing/weighted-SMR routes, author/prove the target armature
  merge or map every non-zero weighted source bone to the intended target bone;
  verify renderer `rootBone`, bindposes, and index/count integrity afterward;
- map PhysBone roots, colliders, probe anchors, constraint sources, contacts,
  and every other external object reference to the copied branch or target
  hierarchy; require zero donor/unresolved references and a complete,
  non-truncated closure scan;
- remap exact animation paths or controller references only when the copied
  part actually needs them;
- add only required Menu/Parameter/FX consumers without replacing target body,
  wardrobe, locomotion, face, or gesture systems wholesale.

Read the target fields and closure again before enabling the copy. Duplicate
names or path coincidences do not prove that a binding resolves to the intended
object.

## 4. Supervised write and causal-result contract

For each write:

1. Preserve the accepted preview, exact target, arguments, and checkpoint.
2. Explain expected effect and failure/rollback boundary.
3. After approval, let the runtime supervision layer invoke the corresponding
   exact write atom; the workflow never calls an approval-helper tool itself.
4. Preserve and inspect identically on the internal Agent loop and external MCP
   Agent:
   `success`, `status`, `error`, `failedStep`, `diagnostics`, `ready`,
   `blockingReasons`, `failureLayer`,
   `failurePhase`, `failureCause`, `rootCause`, `causeChain`, `observed`,
   `expected`, `delta`, `mutationStarted`, `committed`, `commitState`,
   `sceneSaved`, `persistedReadback`, `evidence`, `recovery`, and `nextAction`
   when present.
5. `success=true/status=ok` can mean the inspection succeeded while
   `ready=false`; report the domain blocker instead of calling the transplant
   ready. A timeout or proposal is not commit proof.
6. If commit state is unknown, do not retry. Read back the exact target first.
   If the tool omits the cause, stop and report the missing causal contract.

For a composed sequence, preserve each atom's `step`, `tool`, nested `result`,
`success`, `status`, `ready`, `error`, `failureCause`, `rootCause`, `causeChain`,
`failedStep`, and `diagnostics`. Stop at the first failure; never collapse a
tool error to `false`, drop handler diagnostics, replace it with a generic
failure, or continue after a failed write.

Rollback requires a new approval bound to the exact checkpoint and reason. It
must never be automatic. A failed rollback preserves the current state and is
reported with its own cause.

## 5. Static and Gesture Manager acceptance

Enable the staged copy only after dependency readback. Use Unity Scene view,
not the Game camera. Capture views appropriate to the attachment, and verify
the returned rotation/scope and that the actual root is in frame:

- head/face accessory or ears: orthogonal Front (0,0,0), Side Left (0,+90,0),
  Side Right (0,-90,0), Back (0,180,0), plus close-ups of each root;
- tail or rear accessory: back, both sides, and a tail/root close-up;
- chest/hip/limb accessory: front/back/sides that show the whole attachment
  boundary and adjacent deforming body;
  - underside-dependent attachment: true Bottom at pitch -90, never a mislabeled
  front/top view.

Fail static acceptance on floating roots, gaps, hard overlap, internal or
backface geometry, wrong scale, material discontinuity, or clipping. A capture
that crops the attachment is no evidence.

Named acceptance views use an orthogonal basis: Side Left/Right are pitch 0
with yaw +90/-90, Back is pitch 0/yaw 180, and Bottom is pitch -90/yaw 0.
Oblique pitch is auxiliary only and never counts toward the five required
views; validate returned rotation and camera evidence.

In Gesture Manager, exercise locomotion (`VelocityZ`), turning (`AngularY`),
and any relevant gesture, wardrobe, body-shape, face, or accessory control.
Return runtime parameters to their prior values. The part must deform with the
intended target chain and show no detached/frozen mesh, root separation,
collider jump, clipping, or unexpected target-system regression.

For cat ears and other `independent_skeleton_physbone` parts, capture Rest and
AFK with the exact same camera position, target, projection, crop, and close-root
framing across Front, both Sides, Back, and Bottom; pair head-down, left-turn,
and right-turn to that Rest framing too. Cat ears must remain attached to the target
`Head` through MA Bone Proxy target=`Head` or the exact direct-parent equivalent.
The `installContainer` may retain its accepted uniform fit while the distinct
`swayChainRoot` and every descendant bone remain Rest scale `(1,1,1)`; the
attachment must not shift, the
full chain must sway, every non-zero renderer used bone must remain resolved,
and every active PhysBone Collider must resolve in the target hierarchy. Record
When separately authorized, record `vrcforge_inspect_skinned_mesh_deformation` finite-vertex, Rest/world AABB,
distance-percentile, and reconstructed skin-matrix metrics before and after
each pose. This is an authorized diagnostic only, not a matrix-repair or visual
acceptance gate. Report root drift, pose-dependent seam size, sudden AABB
inflation/collapse/translation, non-finite vertices, or unexplained skin-matrix
jumps as diagnostic anomalies; only verified pixels and behavior determine
visual acceptance.
For a requested optional toggle, capture/read back both off and on states.

After acceptance, prove:

- the source remains unchanged;
- exactly one intended target copy exists;
- every live weighted bone and target-side component/reference resolves;
- no duplicate Avatar/Animator hierarchy was introduced;
- unrelated target face, gestures, body controls, and wardrobe still work.

Only then may local Build & Test run. It does not prove remote upload. Removing
or cleaning the donor/old target part is a separate extraction workflow with a
fresh complete closure scan and separate approval.

## 6. 5.6-sol structural routes and failure matrix

Classify the part as `rigid`, `same_skeleton_smr`, `foreign_skeleton_smr`,
`independent_skeleton_physbone`, or `animator_menu`. Rigid attachment is target
bone/Bone Proxy → local TRS → readback → optional toggle. Independent PhysBone
attachment adds distinct install/sway nodes → complete bounded chain → cat-ear
Head target → sway root scale 1 → all-external-reference closure →
used-bone closure → same-camera Rest/AFK/head-down/head-turn motion/collision
sweep. Only same/foreign-skeleton clothing/weighted-SMR routes use armature
merge or used-bone/rootBone/bindpose remap. Resolve size mismatch by measurement
and a whole-Prefab `installContainer` uniform Unity fit; use DCC only for real mesh
aperture/shape edits or retained donor-body weights, and never scale the target
body/armature or the dynamic accessory's internal sway-chain root.

Mandatory fields are `bones`, `rootBone`, `bindposes`, non-zero weight indices,
bone/index count parity, and mixed-chain closure. Missing/truncated capability is
`capabilityGap` + `ready=false`, not an empty closure. The free-camera contract
may reference `angle`, `rotation`, `scope`, `returnedRotation`, `unobstructed`,
and `evidence` and must be checked.

Retain `success`, `status`, `error`, `failedStep`, `diagnostics`,
`failureLayer`, `failurePhase`, `failureCause`, `rootCause`, `causeChain`,
`observed`, `expected`, `delta`, `blockingReasons`, `capabilityGap`,
`needsDccRerig`, mutation/commit/save/readback fields, `evidence`, `recovery`,
and `nextAction`. Diagnose wrong parent/bindpose, donor PhysBone closure,
path/menu contract, or unavailable scans explicitly; do not hide them behind
Build success. Disable the staged copy or restore its exact checkpoint, then
rerun unobstructed Front/Side Left/Side Right/Back/Bottom over Rest, AFK,
Upright, VelocityZ, AngularY, body-size extremes, and face-tracking extremes.

Only outside the community route and after separate diagnostic authorization
may attachment analysis measure ring center, normal, radius, pivot, and bone
roll. Do not scale the target body/armature, use camera occlusion or unverified
cover as a fix, or bulk-remesh a part
whose BlendShapes must survive. Preserve target bindposes, use minimal local
weight transfer, and keep Basis/BlendShape order and exact ARKit names.
PhysBone A/B isolation must record, temporarily disable, capture, restore, and
read back. Any unresolved geometry, bindpose, weight, Shape Key, or restore
condition requires a concrete `failureCause`, `needsDccRerig=true`, and
`ready=false`.
