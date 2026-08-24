# Part transplant workflow

## Outcome

Copy one bounded, user-authorized source part into a target Avatar while leaving
the source unchanged. The target copy must have the intended geometry,
materials, live weighted bones, PhysBones/colliders, probe anchors,
constraints, animation/controller consumers, and attachment motion—without a
second Avatar Descriptor or unrelated donor hierarchy.

VRCForge 1.7.9 is the hard runtime floor. True named `Bottom` capture for an
underside-dependent attachment, the causal result contract, and deformation
diagnostics are acceptance requirements; an older runtime is `ready=false`
with the cause preserved.

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
| `independent_skeleton_physbone` | Cat ears, tails, or another accessory with its own bounded sway chain | Copy the whole accessory and complete root-to-tip chain; attach a whole-prefab fit container to Head/the exact target bone; keep the actual sway-chain root scale `(1,1,1)` and internal TRS; explicitly rebind PhysBone Root Transform and consumed Collider/probe references; prove renderer used-bone closure | Setup Outfit, Merge Armature, partial chain copy, donor-only Collider references, or changing the sway-chain root scale to hide a fit error |
| `same_skeleton_smr` | Clothing or close-fitting weighted accessories already compatible with the target skeleton | Author/prove a target-compatible armature merge or explicit used-bone remap; prove weights/bones/rootBone/bindposes and run deformation acceptance | Treat as a rigid accessory |
| `foreign_skeleton_smr` | Clothing or close-fitting/donor-body-weighted accessories on an incompatible skeleton | Author an armature merge or map every used bone explicitly; stop for DCC work when target rest/bindposes cannot be reconciled | Guess by matching names or copy an entire donor armature |
| `animator_menu` | Optional behavior layered after one attachment route | Add only the exact required consumers and prove their behavior | Use behavior assets to choose the geometry/skeleton route |

Module Creator is an optional external/manual preprocessing step for exporting a
donor branch as a reusable Prefab. VRCForge has no internal Module Creator
export atom. If that exact export is required, report
`capabilityGap=true`, `ready=false`, and
`failureCause=module_creator_export_atom_unavailable`; do not fabricate an
export result. VRCForge can attach an already available Prefab afterward.

For cross-model size mismatch, first fit the whole imported Prefab/container
with uniform scale and position in Unity. For an independent dynamic accessory,
the actual sway-chain root still remains local scale `(1,1,1)` and its internal
TRS remains unchanged. Escalate to Blender/DCC only when the real mesh aperture,
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
4. Keep target body and clothing scale unchanged. Fit the copied whole
   Prefab/container with uniform local scale and position. For a dynamic
   accessory, keep its actual bounded sway-chain root local scale at 1.

## 3. Rebind target-side dependencies

Use separate preview-bound approvals for the smallest coherent batches:

- for `rigid`, direct-parent or preview/add MA Bone Proxy to the exact target
  bone, then read back the proxy target and local TRS; do not call Setup Outfit
  or add Merge Armature;
- for `independent_skeleton_physbone`, keep the complete bounded sway chain,
  attach the whole-prefab fit container to Head/the exact target bone, keep the
  actual sway-chain root scale `(1,1,1)`, explicitly rebind PhysBone Root
  Transform plus every live Collider/probe consumer, and prove closure for every
  non-zero renderer used-bone slot;
- only for the two clothing/weighted-SMR routes, author/prove the target armature
  merge or map every non-zero weighted source bone to the intended target bone;
  verify renderer `rootBone`, bindposes, and index/count integrity afterward;
- map PhysBone roots, colliders, probe anchors, constraints, and contacts to
  target objects;
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

For cat ears and other `independent_skeleton_physbone` parts, capture the same
close-root camera at Rest and at AFK, head-down, left-turn, and right-turn across
Front, both Sides, Back, and Bottom. The actual sway-chain root scale must remain
`(1,1,1)`, the attachment must not shift relative to Head/the target bone, the
full chain must sway, every non-zero renderer used bone must remain resolved,
and every active PhysBone Collider must resolve in the target hierarchy. Record
`vrcforge_inspect_skinned_mesh_deformation` finite-vertex, Rest/world AABB,
distance-percentile, and reconstructed skin-matrix metrics before and after
each pose. This is a diagnostic gate, not a matrix-repair step. Fail on root
drift, a gap/seam whose size changes between poses, sudden AABB inflation/
collapse/translation, non-finite vertices, or unexplained skin-matrix scale/
translation jumps outside the declared Rest/source baseline.
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
attachment adds complete bounded chain → root scale 1 → Root/Collider closure →
used-bone closure → same-camera Rest/AFK/head-down/head-turn motion/collision
sweep. Only same/foreign-skeleton clothing/weighted-SMR routes use armature
merge or used-bone/rootBone/bindpose remap. Resolve size mismatch by measurement
and a whole-Prefab/container uniform Unity fit; use DCC only for real mesh
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

For attachment geometry, measure source/target ring center, normal, radius,
pivot, and bone roll in the target rest frame before local fitting. Do not
scale the target body/armature, use occlusion as a fix, or bulk-remesh a part
whose BlendShapes must survive. Preserve target bindposes, use minimal local
weight transfer, and keep Basis/BlendShape order and exact ARKit names.
PhysBone A/B isolation must record, temporarily disable, capture, restore, and
read back. Any unresolved geometry, bindpose, weight, Shape Key, or restore
condition requires a concrete `failureCause`, `needsDccRerig=true`, and
`ready=false`.
