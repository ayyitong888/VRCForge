# AI Face Tuning Acceptance Test

Use this checklist to validate the v0.1 alpha face tuning loop against a real Unity / VRChat avatar project.

## Steps

1. Start Unity, open the avatar scene, and start MCP for Unity.
2. Start VRCForge and confirm Unity shows as connected.
3. Select the target Avatar.
4. Load facial Blendshapes.
5. Add an optional current/original avatar image.
6. Add one or more optional target reference images.
7. Enter a natural-language face tuning instruction.
8. Click `Generate Plan`.
9. Confirm the UI shows Blendshape name, before value, after value, and delta for each generated change.
10. Click `Apply Plan`.
11. Confirm the Blendshape values change in Unity or in the dashboard list.
12. Click `Restore` / undo and confirm the previous values return.
13. Generate a second Plan.
14. Save the useful result as a named preset.
15. Reload the dashboard or project and confirm the preset still appears.
16. Apply the saved preset and confirm it uses saved after values.
17. Lock one Blendshape and generate another Plan.
18. Confirm the locked Blendshape is not included in the new Plan and is not modified during apply.
19. Try applying a preset or history entry with a missing Blendshape target.
20. Confirm VRCForge skips the invalid target and does not crash.

## Expected Result

The user can generate multiple AI-assisted face tuning candidates, review each Blendshape change, apply a candidate, restore if needed, save a good result as a preset, reapply it later, and lock selected Blendshapes before rerolling only the remaining editable parts.

## Safety Notes

Back up the Unity / VRChat avatar project before using asset-writing features. Review generated changes before applying them to important projects.
