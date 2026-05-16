# Shader / Material Tuning Checkpoints

This file records implementation checkpoints so another agent can continue without rereading the full session.

## Checkpoint 1: Read-only material scan and inventory

Status: completed locally on `feature/shader-material-tuning-mvp`.

Implemented:

- Added Unity MCP tool `vrc_scan_avatar_materials`.
- Scans avatar child `Renderer` and `SkinnedMeshRenderer` components through the selected avatar root.
- Captures renderer path/name, mesh name, material slot, material name, shader name, shader family, category, shared material key, and stable material id.
- Stable ids use normalized renderer path, slot index, material name, and shader name, not Unity instance ids.
- Detects shader family as `lilToon`, `Poiyomi`, or `Unsupported`.
- Adds heuristic categories: skin, eyes, hair, clothes, accessory, unknown.
- Added dashboard endpoint `POST /api/shader/materials/scan`.
- Added fixed-height dashboard inventory table and category dropdown overrides.
- This checkpoint performs no material writes.

Changed files:

- `Assets/VRCAutoRig/Editor/ShaderMaterialScanner.cs`
- `dashboard_server.py`
- `dashboard/index.html`
- `dashboard/app.js`
- `dashboard/styles.css`
- `docs/SHADER_TUNING_CHECKPOINTS.md`

Validation:

- `python -m py_compile dashboard_server.py vrchat_blendshape_agent.py`
- `node --check dashboard/app.js`
- `git diff --check`

Next:

- Checkpoint 2 should add `ShaderAdapterRegistry`, `LilToonShaderAdapter`, and `PoiyomiShaderAdapter`.
- Scanner should then populate `supported_properties` by reading adapter-supported semantic properties.

## Checkpoint 2: Shader adapter system

Status: completed locally on `feature/shader-material-tuning-mvp`.

Implemented:

- Added `ShaderAdapterRegistry` and `IShaderMaterialAdapter`.
- Added `LilToonShaderAdapter` and `PoiyomiShaderAdapter`.
- Adapters expose only semantic properties and keep real shader property aliases internal.
- Adapter reads always call `Material.HasProperty` before reading a real material property.
- Adapter write helpers validate semantic property support, value type, and clamp ranges, but are not wired to any write endpoint yet.
- Updated `vrc_scan_avatar_materials` so each supported lilToon/Poiyomi material includes `supported_properties`.
- Unsupported shaders remain visible with an empty `supported_properties` object.

Changed files:

- `Assets/VRCAutoRig/Editor/ShaderMaterialAdapters.cs`
- `Assets/VRCAutoRig/Editor/ShaderMaterialScanner.cs`
- `docs/SHADER_TUNING_CHECKPOINTS.md`

Validation:

- `git diff --check`

Next:

- Checkpoint 3 should add backend AI material plan generation and validation.
- The validation layer should reject arbitrary shader property names and only accept adapter semantic properties.

## Checkpoint 3: AI shader plan generation and validation

Status: completed locally on `feature/shader-material-tuning-mvp`.

Implemented:

- Added `create_material_tuning_plan(...)` in the Python agent layer.
- Reused the existing provider/model request path for Google AI Studio, OpenAI-compatible providers, Anthropic, Ollama-compatible endpoints, and Google Vertex AI.
- Reused the existing source/target reference image pipeline for Vision-assisted material planning.
- Added material tuning prompt rules requiring JSON-only output and semantic properties only.
- Added dashboard endpoint `POST /api/shader/plan`.
- Added backend validation for:
  - known `material_id`
  - supported shader family
  - semantic property whitelist
  - real property availability through `supported_properties`
  - locked material/property placeholders
  - color and numeric value type validation
  - safe numeric clamping
  - rejection of arbitrary real shader property names
- Added dashboard shader instruction input, Generate Shader Plan button, and reviewable plan preview.
- This checkpoint still performs no material writes.

Changed files:

- `vrchat_blendshape_agent.py`
- `dashboard_server.py`
- `dashboard/index.html`
- `dashboard/app.js`
- `docs/SHADER_TUNING_CHECKPOINTS.md`

Validation:

- `python -m py_compile dashboard_server.py vrchat_blendshape_agent.py`
- `node --check dashboard/app.js`
- `git diff --check`

Next:

- Checkpoint 4 should add safe material apply and restore using adapter validation.
- Apply must save pre-apply values and must not touch shader source, textures, render queue, stencil, culling, blend mode, mesh data, or shader assignment.

## Checkpoint 4: Apply, restore, and backup

Status: completed locally on `feature/shader-material-tuning-mvp`.

Implemented:

- Added Unity MCP tool `vrc_apply_material_tuning`.
- The tool resolves current scene materials by stable material id before each write.
- The tool applies only adapter-validated semantic material properties.
- Missing material ids, unsupported shader families, missing properties, and invalid values are skipped with warnings.
- The backend revalidates requested changes before calling Unity.
- The backend stores pre-apply values in an in-memory undo stack.
- Added `POST /api/shader/apply`.
- Added `POST /api/shader/restore`.
- Restore calls the same Unity apply tool with saved previous values.
- Dashboard now has Apply Shader Plan and Restore Shader buttons.

Safety boundary:

- No shader source editing.
- No texture editing.
- No mesh editing.
- No shader replacement.
- No render queue, stencil, culling, or blend mode changes.

Changed files:

- `Assets/VRCAutoRig/Editor/MaterialTuningApplier.cs`
- `dashboard_server.py`
- `dashboard/index.html`
- `dashboard/app.js`
- `docs/SHADER_TUNING_CHECKPOINTS.md`

Validation:

- `python -m py_compile dashboard_server.py vrchat_blendshape_agent.py`
- `node --check dashboard/app.js`
- `git diff --check`

Next:

- Checkpoint 5 should persist shader history, presets, and locks.
- Preset replay must apply saved after values, not deltas.

## Checkpoint 5: Shader history, presets, and locks

Status: completed locally on `feature/shader-material-tuning-mvp`.

Implemented:

- Added ignored runtime stores:
  - `artifacts/dashboard/shader_tuning_history.json`
  - `artifacts/dashboard/shader_tuning_presets.json`
  - `artifacts/dashboard/shader_tuning_locks.json`
- Shader plan generation now saves a history record automatically.
- History records include instruction, provider/model, reference image count, changes, warnings, visual analysis, apply status, and lock context.
- Added shader history reapply endpoint.
- Added shader preset create, apply, rename, duplicate, and delete endpoints.
- Preset/history replay applies saved `after` values, not repeated deltas.
- Added shader lock store and lock endpoint.
- Dashboard can open shader history, open shader presets, save the current shader plan as a preset, reapply history, apply presets, and lock materials from the inventory table.

Changed files:

- `dashboard_server.py`
- `dashboard/index.html`
- `dashboard/app.js`
- `docs/SHADER_TUNING_CHECKPOINTS.md`

Validation:

- `python -m py_compile dashboard_server.py vrchat_blendshape_agent.py`
- `node --check dashboard/app.js`
- `git diff --check`

Next:

- Checkpoint 6 should add optional post-apply Vision review.
- Vision review must compare screenshots and remain advisory only.
