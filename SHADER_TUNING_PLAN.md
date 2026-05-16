# VRCForge Shader / Material Tuning MVP Plan

## Goal

Shader / Material Tuning adds an AI-assisted, reviewable, user-controlled material parameter workflow to VRCForge. The MVP supports lilToon and Poiyomi first. Unsupported shaders remain visible in the inventory but are skipped during writes.

The workflow is:

1. Unity scans avatar renderers, meshes, material slots, and shader families.
2. VRCForge builds a safe material inventory snapshot.
3. The user provides a natural-language instruction and optional screenshots or reference images.
4. AI returns a structured JSON material tuning plan.
5. VRCForge validates the plan against shader adapters and safety rules.
6. The user reviews, applies, restores, saves presets, and reapplies saved results.

AI does not directly access Unity objects and does not edit shader code, texture files, meshes, render queue, stencil, culling, blend mode, or shader assignment.

## Supported MVP Scope

- Shader families: lilToon, Poiyomi.
- Modes: inventory-only planning and optional Vision-assisted planning.
- Writes: whitelisted semantic material properties only.
- Persistence: local JSON history, presets, and locks.
- Restore: revert modified semantic material values from pre-apply backups.
- Preset replay: apply saved after values, not repeated deltas.

Generic shader fallback, shader replacement, texture editing, mesh editing, and automatic second-round tuning are out of scope for the MVP.

## Material Inventory

Unity scans the selected avatar root and its child renderers. Each inventory item includes:

- avatar name and root path
- renderer id, name, and hierarchy path
- mesh name
- material id
- material slot index
- material name
- shader name
- shader family
- material category
- supported semantic properties and current values

Material ids are stable for the current hierarchy and are derived from normalized renderer path, slot index, material name, and shader name. Unity instance ids are not used as preset identifiers.

Automatic categories are inferred from object, renderer, mesh, and material names:

- `face`, `skin`, `body` -> skin
- `eye`, `iris`, `pupil` -> eyes
- `hair` -> hair
- `cloth`, `clothes`, `hoodie`, `shirt`, `skirt`, `dress`, `pants`, `shoe` -> clothes
- `accessory`, `ring`, `glasses`, `hat` -> accessory
- otherwise unknown

The dashboard allows manual category overrides.

## Shader Adapters

Shader adapters map safe semantic properties to real material properties. Each adapter checks `Material.HasProperty` before reading or writing and omits unsupported properties from the inventory.

MVP semantic properties:

- `base_color`
- `shade_color`
- `shadow_strength`
- `shadow_softness`
- `smoothness`
- `specular_strength`
- `rim_color`
- `rim_strength`
- `emission_color`
- `emission_strength`
- `matcap_strength`
- `outline_color`
- `outline_width`
- `normal_strength`

Adapters:

- `LilToonShaderAdapter`
- `PoiyomiShaderAdapter`

Unsupported shaders are scanned but not writable.

## AI Plan Contract

AI receives only:

- safe material inventory snapshot
- supported semantic properties
- user instruction
- safety rules
- optional selected categories or materials
- optional current screenshots
- optional target or reference images

AI returns JSON only:

- `material_tuning_plan`
- `vision_assisted_material_tuning_plan`

Each change must reference a known material id and semantic property. Arbitrary shader property names are rejected.

## Validation And Safety

Before apply, VRCForge validates every change:

- material id exists
- material still exists in the current Unity scene
- shader adapter exists
- semantic property is supported
- underlying real material property exists
- value type is valid
- value is clamped to adapter-safe range
- material, category, or property is not locked
- unsupported shaders and missing properties are skipped safely

Users should back up Unity and VRChat avatar projects before using asset-writing features.

## History, Presets, And Locks

Shader tuning history stores generated plans, user instructions, provider/model info, warnings, visual analysis, apply status, before values, after values, and optional screenshot references.

Shader presets store named reusable material tuning results. Reapply uses saved after values by default so repeated preset use does not stack deltas.

Locks can block selected materials, categories, or semantic properties from being modified by apply or preset replay.

## Vision Review

If screenshots are available, VRCForge can run an optional post-apply Vision review. The review compares before and after screenshots against the user goal and returns advisory feedback only. It never auto-applies another tuning round.

## Manual Acceptance Test

1. Select avatar root.
2. Scan materials.
3. Confirm renderer, mesh, slot, material, and shader family are shown.
4. Confirm lilToon and Poiyomi detection.
5. Set material categories.
6. Generate an inventory-only shader plan.
7. Confirm the plan uses semantic properties only.
8. Apply the plan.
9. Restore the previous values.
10. Save a shader preset.
11. Reload the dashboard or project.
12. Reapply the preset.
13. Try an unsupported shader and confirm it is skipped safely.
14. Try a missing property and confirm the app does not crash.
15. Confirm shader source, textures, mesh data, render queue, stencil, culling, blend mode, and shader assignment are unchanged.
16. Generate a Vision-assisted plan with screenshots or reference images.
17. Apply the plan.
18. Capture an after screenshot.
19. Run Vision review.
20. Confirm the review is advisory only.
