# VRCForge Handoff

## 1. Current Project State

VRCForge is currently at a local MVP stage with the dashboard, FastAPI backend, Unity MCP tools, face tuning loop, Shader / Material Tuning MVP, and Phase 2 Unity tool layer expansion implemented.

Working and covered by automated tests:

- Windows x64 installer/launcher packaging path:
  - `VRCForge.exe` WinForms/WebView2 launcher source.
  - NSIS web/offline installer scripts.
  - release build/publish scripts with git cleanliness, unpushed commit, version, and CoplayDev license gates.
  - generated `VRCForge.unitypackage` fallback package.
  - pinned CoplayDev Unity MCP package copied into release payload after MIT license gate.
- Portable backend mode for installer payloads:
  - `VRCFORGE_APP_DIR`
  - `VRCFORGE_USER_DATA_DIR`
  - `VRCFORGE_CONFIG_DIR`
  - `VRCFORGE_LOG_DIR`
  - `VRCFORGE_ARTIFACTS_DIR`
  - `VRCFORGE_DASHBOARD_DIR`
  - `VRCFORGE_SETTINGS_PATH`
- Structured `/api/health` diagnostics for Launcher:
  - backend
  - dashboard files
  - config read/write
  - logs write
  - artifacts write
  - selected Unity project
  - Unity plugin installed
  - MCP package configured
  - Unity MCP bridge reachable
  - provider config present
- FastAPI dashboard backend and static dashboard routes.
- Provider/model configuration and model-list loading.
- Avatar discovery, connection status, and Unity MCP request plumbing.
- Facial Blendshape inventory loading, plan generation, plan review, safe apply, restore, history, presets, preset replay, and locked Blendshape filtering.
- Optional source/current images and target/reference images for AI-assisted face tuning.
- Shader / Material Tuning MVP backend flow:
  - material inventory scan
  - lilToon / Poiyomi semantic adapter validation
  - material tuning plan parsing
  - unsupported shader skip
  - lock handling
  - clamping
  - shader preset replay with saved after values
  - rejection of arbitrary real shader property names
- Roslyn fallback is optional and isolated behind `VRCFORGE_ENABLE_ROSLYN`; core workflows use dedicated Unity MCP tools.
- Phase 2 predefined Unity Editor tools:
  - `vrc_scan_avatar_items`
  - `vrc_scan_fx_animator`
  - `vrc_scan_animation_bindings`
  - `vrc_create_safe_backup`
  - `vrc_restore_safe_backup`
- Unity screenshot capture:
  - `vrc_capture_scene_view` keeps the original static Scene View capture outside Play Mode.
  - When Unity is in Play Mode, the same tool captures the current Game View for more accurate shader, lighting, and Gesture Manager preview.
  - The Vision Review dashboard panel now checks Play Mode / Gesture Manager state before capture and shows a reminder without blocking capture.
- Unity-side public branding has been renamed from the old `Assets/VRCAutoRig` path to `Assets/VRCForge`; install scripts migrate the legacy folder when found.
- Local planning/research materials from `D:\Codex\新建文件夹` were moved into ignored local folder `docs/research/` for safekeeping. They are not part of the pushed GitHub source tree unless explicitly force-added later.

Latest known automated validation:

- `python -m py_compile dashboard_server.py vrchat_blendshape_agent.py`
- `node --check dashboard/app.js`
- `python -m pytest -q` passed with 82 tests and 4 existing FastAPI deprecation warnings.
- PowerShell parser check passed for updated install/packaging scripts.
- `quickstart/setup-and-run.ps1 -SkipUnityInstall -CheckOnly -NoDashboard -NoBrowser -BindPort 8761` passed.
- Temp Unity project install test passed for `.vrcforge/backups` legacy migration, local MCP copy, and manifest file dependency.
- `dotnet build launcher/VRCForge.Launcher/VRCForge.Launcher.csproj -c Release -p:Platform=x64 --no-restore` passed with a WebView2 WindowsBase warning.
- Dirty verification release build produced installer/payload artifacts and the packaged backend `/api/health` responded with `portableMode=true`.
- `git diff --check`

Known live-validation status:

- Installer UI has not been manually clicked through after installation into `%ProgramFiles%`.
- Face tuning has been exercised through the dashboard and Unity workflow, but final quality still depends on real avatar content and selected model output.
- Shader / Material Tuning MVP has automated coverage for backend validation and dashboard syntax, but still needs a real Unity project pass for C# clean compile, material scan, apply, restore, preset replay, and Vision review.
- Gesture Manager / Play Mode screenshot routing has automated source and dashboard coverage, but still needs a real Unity play session pass to confirm Game View capture framing on a live avatar.
- Phase 2 tool source registration is covered by pytest. A direct `unity-mcp tool list` probe from this shell returned HTTP 503, so live Unity MCP list verification and Unity C# compile still need the open Unity project to be reachable.
- The ignored local research folder `docs/research/` should stay as background planning context only; do not treat those early Roslyn / external MCP research files as the default implementation route.

## 2. Known TODO Items

Priority P0:

- After this checkpoint is pushed, rebuild release artifacts without `-AllowDirty` / `-AllowUnpushed` and upload `VRCForge_Web_Installer_x64.exe`, `VRCForge_Offline_Installer_x64.exe`, and `VRCForge_Windows_x64_0.3.1-alpha.zip` to GitHub Release `v0.3.1-alpha`.
- Manually smoke-test the installed Launcher wizard from `%ProgramFiles%\VRCForge` when a clean Windows VM is available.
- Run Unity clean compile in a real VRChat Avatar project after the Phase 2 tool layer expansion.
- Confirm these five tools appear in the Unity MCP tool list:
  - `vrc_scan_avatar_items`
  - `vrc_scan_fx_animator`
  - `vrc_scan_animation_bindings`
  - `vrc_create_safe_backup`
  - `vrc_restore_safe_backup`
- Confirm `vrc_scan_avatar_materials` appears in MCP for Unity and returns renderer, mesh, slot, material, shader family, category, and supported semantic properties.
- Confirm `vrc_apply_material_tuning` safely applies and restores lilToon and Poiyomi material values without touching forbidden shader, texture, mesh, render queue, stencil, culling, blend mode, or shader assignment data.
- Enter Play Mode with Gesture Manager active, adjust Game View to the avatar front face, and confirm Vision Review captures Game View rather than Scene View.
- Keep `VRCFORGE_ENABLE_ROSLYN` disabled by default and confirm Unity compile does not require Roslyn DLLs.

Priority P1:

- Complete manual shader acceptance testing with at least one lilToon material and one Poiyomi material.
- Verify face tuning history, presets, restore, locked Blendshapes, and partial reroll behavior against a live avatar after this handoff.
- Improve dashboard error messages for Unity-side missing renderer/material/property cases if live testing exposes unclear warnings.
- Confirm uploaded/pasted source and target images are included in provider requests for models that support images, and errors are clear for models that do not.

Priority P2:

- Improve visual before/after comparison for both face and material tuning.
- Refine material category override persistence and UI affordances.
- Add more Unity-side diagnostics for unsupported shaders and missing material properties.
- Expand documentation only after live Unity behavior is verified.

## 3. Architecture Summary

VRCForge has four main layers.

FastAPI backend:

- `dashboard_server.py` serves the dashboard, exposes local REST endpoints, stores runtime JSON data, calls Unity MCP tools, validates AI plans, and coordinates apply/restore/preset flows.
- Runtime data is stored under ignored `artifacts/dashboard/` JSON files for history, presets, locks, screenshots, and generated plans.

Provider and planning layer:

- `vrchat_blendshape_agent.py` contains provider adapters, image plumbing, prompt construction, JSON parsing, and plan generation helpers for face tuning, material tuning, and Vision review.
- Providers are normalized so the dashboard can use Gemini, OpenAI-compatible APIs, Anthropic, Ollama-compatible endpoints, Google Vertex AI, DeepSeek, OpenRouter, and custom endpoints where configured.

Dashboard frontend:

- `dashboard/index.html`, `dashboard/app.js`, and `dashboard/styles.css` implement the local UI.
- The dashboard keeps plans reviewable before writes and separates generate, apply, restore, save preset, reapply, and lock actions.
- Frontend logging is intentionally minimal; detailed runtime logs belong in local backend logs and artifacts.

Unity MCP and adapters:

- Unity Editor tools live under `Assets/VRCForge/Editor/`.
- Existing tools cover avatar scanning, Blendshape export/apply, screenshot capture, parameter/menu scans, material inventory, and material apply.
- Shader adapters expose semantic material properties only. lilToon and Poiyomi map semantic properties to real material aliases internally and always check `Material.HasProperty`.
- Unsupported shaders remain visible in inventory but are skipped during writes.

## 4. Phase 2 Tool Expansion Status

Phase 2 added five focused Unity Editor tools without redesigning the current MVP.

1. `vrc_scan_avatar_items`
   - Read-only avatar hierarchy and item inventory.
   - Return object path, active state, renderer count, mesh/material summary, likely category, and whether the item appears wardrobe-related.
   - Implemented in `Assets/VRCForge/Editor/GameObjectTools.cs`.

2. `vrc_scan_fx_animator`
   - Read-only FX controller inventory.
   - Return layers, states, transitions, parameters used, animation clips referenced, and likely toggle groups.
   - Implemented in `Assets/VRCForge/Editor/ComponentTools.cs`.

3. `vrc_scan_animation_bindings`
   - Read-only animation clip binding scan.
   - Return animated paths/properties, material/property bindings, object active toggles, Blendshape bindings, and unsafe/unsupported binding warnings.
   - Implemented in `Assets/VRCForge/Editor/AssetTools.cs`.

4. `vrc_create_safe_backup`
   - Write a local Unity-side backup snapshot before asset-writing actions.
   - Include selected assets, generated metadata, timestamp, and restore hints. Do not replace version control.
   - Implemented in `Assets/VRCForge/Editor/ConsoleTools.cs`.
   - Defaults to `Library/VRCForge/Backups` so snapshots stay local to the Unity project.

5. `vrc_restore_safe_backup`
   - Restore from a VRCForge-created backup snapshot.
   - Validate target project identity and show warnings before restoring. It must not silently overwrite unrelated assets.
   - Implemented in `Assets/VRCForge/Editor/PrefabTools.cs`.
   - Defaults to preview mode until `confirmRestore=true` is supplied.

The goal is to support safer wardrobe, FX, and asset-writing workflows after face and material tuning are stable. The source-level work is complete; live Unity compile and MCP list verification are still pending because the local MCP probe returned HTTP 503.

## 5. Constraints For The Next Session

- Continue from the current MVP. Do not restart the project or redesign the architecture.
- Keep face tuning behavior working while adding Phase 2 tools.
- Keep Roslyn optional and isolated. New tools must not depend on Roslyn.
- Prefer dedicated Unity MCP tools over dynamic code execution.
- Do not let AI directly modify Unity objects. AI may produce structured plans only; Unity-side tools must validate and apply.
- Keep write operations reviewable and restorable.
- Never write shader source, texture files, mesh data, render queue, stencil, culling, blend mode, or shader assignment in the Shader / Material Tuning MVP.
- Use semantic properties and adapter validation for material writes.
- Ignore invalid, missing, locked, or unsupported targets safely and report warnings instead of crashing.
- Keep runtime stores and local configuration ignored by git.
- Preserve public documentation quality: professional tone, no private paths, no API keys, no internal-only testing language.
- Before ending a session, update `PROJECT_STATUS.md`, update `AGENTS.md`, run available validation, and commit intentional changes with a clear message.
