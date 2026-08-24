# Part transplant workflow

## Outcome

Copy one bounded, user-authorized source part into a target Avatar while leaving
the source unchanged. The target copy must have the intended geometry,
materials, live weighted bones, PhysBones/colliders, probe anchors,
constraints, animation/controller consumers, and attachment motion—without a
second Avatar Descriptor or unrelated donor hierarchy.

VRCForge 1.7.9 is the hard runtime floor. True named `Bottom` capture for an
underside-dependent attachment and the causal result contract are acceptance
requirements; an older runtime is `ready=false` with the cause preserved.

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

## 2. Stage the copy

1. Create the pre-copy checkpoint.
2. Preview and duplicate the minimal bounded branch. Never move/delete the
   donor and never copy its Avatar Descriptor, Pipeline Manager, whole-body
   Animator, unrelated body, or entire armature.
3. Create the target copy inactive. Reparent it to the intended target bone,
   preserve or deliberately fit world pose, and read local/world transforms
   back after the operation. A `preserveWorldTransform` receipt is not enough.
4. Keep target body and clothing scale unchanged. Fit the copied branch or its
   bounded attachment root locally.

## 3. Rebind target-side dependencies

Use separate preview-bound approvals for the smallest coherent batches:

- map every non-zero weighted source bone to the intended target bone; verify
  renderer `rootBone` and index/count integrity afterward;
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
4. Preserve and inspect:
   `success`, `status`, `ready`, `blockingReasons`, `failureLayer`,
   `failurePhase`, `failureCause`, `rootCause`, `causeChain`, `observed`,
   `expected`, `delta`, `mutationStarted`, `committed`, `commitState`,
   `sceneSaved`, `persistedReadback`, `evidence`, `recovery`, and `nextAction`
   when present.
5. `success=true/status=ok` can mean the inspection succeeded while
   `ready=false`; report the domain blocker instead of calling the transplant
   ready. A timeout or proposal is not commit proof.
6. If commit state is unknown, do not retry. Read back the exact target first.
   If the tool omits the cause, stop and report the missing causal contract.

Rollback requires a new approval bound to the exact checkpoint and reason. It
must never be automatic. A failed rollback preserves the current state and is
reported with its own cause.

## 5. Static and Gesture Manager acceptance

Enable the staged copy only after dependency readback. Use Unity Scene view,
not the Game camera. Capture views appropriate to the attachment, and verify
the returned rotation/scope and that the actual root is in frame:

- head/face accessory or ears: Front (0,0,0), Side Left (10,+90,0),
  Side Right (10,-90,0), Back (10,180,0), plus close-ups of each root;
- tail or rear accessory: back, both sides, and a tail/root close-up;
- chest/hip/limb accessory: front/back/sides that show the whole attachment
  boundary and adjacent deforming body;
- underside-dependent attachment: true Bottom at pitch -90, never a mislabeled
  front/top view.

Fail static acceptance on floating roots, gaps, hard overlap, internal or
backface geometry, wrong scale, material discontinuity, or clipping. A capture
that crops the attachment is no evidence.

In Gesture Manager, exercise locomotion (`VelocityZ`), turning (`AngularY`),
and any relevant gesture, wardrobe, body-shape, face, or accessory control.
Return runtime parameters to their prior values. The part must deform with the
intended target chain and show no detached/frozen mesh, root separation,
collider jump, clipping, or unexpected target-system regression.

After acceptance, prove:

- the source remains unchanged;
- exactly one intended target copy exists;
- every live weighted bone and target-side component/reference resolves;
- no duplicate Avatar/Animator hierarchy was introduced;
- unrelated target face, gestures, body controls, and wardrobe still work.

Only then may local Build & Test run. It does not prove remote upload. Removing
or cleaning the donor/old target part is a separate extraction workflow with a
fresh complete closure scan and separate approval.
