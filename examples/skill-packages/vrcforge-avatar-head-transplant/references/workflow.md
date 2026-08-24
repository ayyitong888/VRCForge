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

## 1. Establish exact identities

1. Read the loaded Avatar list and both descriptors. Reject ambiguous paths,
   nested donor Avatar Descriptors, or more than one intended final Avatar.
2. Record the donor head mesh/renderers, donor Neck/Head chain, target
   Neck/Head chain, target body/wardrobe controls, current FX, Parameters, Menu,
   and every Modular Avatar or equivalent merge contributor.
3. Scan non-zero renderer bone usage. A renderer bone-array slot existing does
   not prove a live vertex dependency; a zero-weight slot also does not by
   itself make its Transform safe to delete.
4. Classify the donor head:

   - `face-tracked` only when the retained mesh BlendShapes, face FX/animation,
     Expression Parameters, and Expressions Menu are all proven and mutually
     referenced.
   - `gesture-only` when that four-part contract is absent or the user
     explicitly chooses not to retain it. Similar BlendShape names alone do
     not prove face tracking.

If any inventory result is truncated, unavailable, ambiguous, or lacks an
exact cause, stop before mutation and report that blocker.

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
   indices, PhysBone roots/colliders, and probe anchors before rebinding.

Static transform equality is not enough. A duplicate inner Neck/Head chain can
match at idle yet inherit target Head motion twice. During the rebinding gate,
prove that neck-weighted vertices follow the intended target Neck and that the
retained head/face vertices follow the intended Head chain. Preserve array
length/index stability unless a dedicated atom proves compaction safe.

For cat ears/tails, choose `independent_skeleton_physbone`: move the whole
accessory and its complete bounded local sway chain, attach a whole-prefab fit
container to Head/the requested target bone, and keep the actual sway-chain
root local scale `(1,1,1)` with internal TRS unchanged. Rebind PhysBone Root
Transform and every consumed Collider explicitly, then prove closure for every
non-zero renderer used-bone slot. This is neither the rigid-accessory route nor
the clothing armature-merge route.

## 3A. Gesture-only branch

1. Scan donor left/right gesture conditions, clips, BlendShape paths, and the
   target body's FX/wardrobe controls.
2. Preview exact animation-path/reference changes and merge only the gesture
   layers needed by the retained head.
3. Preserve target body sizing, wardrobe, locomotion, and other target-authored
   controls. Do not replace the target FX or Parameters wholesale.
4. Do not copy any face-tracking parameter, menu control, face layer, or orphan
   animation. Scan the final state for residual face-tracking consumers.

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
`vrcforge_inspect_skinned_mesh_deformation` diagnostics at Rest and again at
AFK, head-down, left-turn, and right-turn. Use the exact same close-root camera
at Rest and motion across Front, both Sides, Back, and Bottom. Read finite
vertices, Rest/world AABB, distance percentiles, and reconstructed skin-matrix
metrics; the tool diagnoses binding/deformation and is not a routine matrix
repair step. Fail on non-finite vertices, root drift, a seam/gap that changes
size between poses, sudden AABB inflation/collapse/translation, or unexplained
skin-matrix scale/translation jumps outside the declared Rest/source baseline.

The head, body, hair, ears, and other retained parts must stay connected and
deform through the intended single chain. Only after these pixel and behavior
gates pass may local Build & Test run. Build success never cures a visible
seam and is not remote upload proof.

## 6. 5.6-sol structural routes and failure matrix

Select one route: `rigid`; `same_skeleton_smr` (weights, bones/rootBone,
bindposes, and index parity); `foreign_skeleton_smr` (explicit minimal weighted
chain); `independent_skeleton_physbone` (bounded branch plus root/collider/
probe closure); or `animator_menu` (exact path/parameter remap retaining target
controls). Resolve size mismatch in this order: measure attachment space, first
fit the imported whole-head/accessory container with uniform scale and position
in Unity, keep independent sway-chain roots at local scale 1, diagnose used-bone
closure and bindpose/skin-matrix metrics, then set `needsDccRerig=true` only for
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

Before fitting a neck, measure source/target ring center, outward normal,
radius, pivot, and bone roll in the target Neck rest frame. Fit only the
imported head's local transform. Preserve target bindposes, transfer only the
minimal weighted chain, and preserve Basis/BlendShape order plus exact ARKit
names. Unweighted null slots are informational; unresolved used bones or
bindposes block. PhysBone A/B isolation records state, temporarily disables,
captures Rest/dynamic evidence, restores, and reads back. Any unreconciled
geometry, bindpose, weight, Shape Key, or restore state requires a concrete
`failureCause`, `needsDccRerig=true`, and `ready=false`.
