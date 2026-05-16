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
