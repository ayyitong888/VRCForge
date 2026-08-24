# Head transplant workflow

## Outcome

Produce one target Avatar whose retained donor head, target body, body controls,
wardrobe, gestures, and optional face tracking behave as one system. Do not
move or delete the donor source. Do not remove the old target head until the
new head passes reference, static-pixel, dynamic-Gesture-Manager, and local
Build & Test gates.

VRCForge 1.7.9 is the hard runtime floor. True named `Bottom` capture and the
causal result contract are acceptance requirements, not optional enhancement;
an older runtime is `ready=false` with the missing capability reported.

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
4. Preserve and inspect:
   `success`, `status`, `ready`, `blockingReasons`, `failureLayer`,
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
| Side left | pitch 10, yaw +90, roll 0 | jaw-to-neck contour and shoulder junction |
| Side right | pitch 10, yaw -90, roll 0 | jaw-to-neck contour and shoulder junction |
| Back | pitch 10, yaw 180, roll 0 | full rear neck ring and hairline |
| Bottom | pitch -90 | true under-chin view with the whole neck opening and shoulder boundary |

A named angle is invalid evidence if its returned rotation differs or the
attachment is cropped. At idle and in motion, fail on any open rim, hollow or
internal face, backface, overlap, floating strip, hard geometric step, normal,
shading, or material-color discontinuity.

Run at least:

- locomotion (`VelocityZ` non-zero), turning (`AngularY` non-zero), and return
  parameters to their prior values;
- left and right gestures with face tracking disabled/fallback active;
- for the face-tracked branch, representative eye, lip/viseme, tongue, and
  expression controls;
- target body sizing and default wardrobe controls.

The head, body, hair, ears, and other retained parts must stay connected and
deform through the intended single chain. Only after these pixel and behavior
gates pass may local Build & Test run. Build success never cures a visible
seam and is not remote upload proof.
