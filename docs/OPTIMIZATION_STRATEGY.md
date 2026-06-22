# VRCForge Model Optimization Strategy

## Calling third-party tools vs first-class VRCForge capabilities

1. VRCForge owns safety, UI, orchestration, validation, rollback, audit, MCP projection, and user-facing workflows.
2. Third-party optimizers own specialized transformations.
3. External agents can plan and request; VRCForge executes.
4. Direct integration means first-class UX and schema, not copying third-party algorithms.
5. Every optimizer starts detect-only, then read-only, then plan-only, then delegated apply.
6. No optimizer becomes stable until it passes sample matrix validation and rollback proof.
7. Never enable all optimizers at once.
8. Scan, validate, and rollback must work without any LLM or API key.
9. Paid avatar and Booth assets stay local by default.
10. Public Stable favors conservative, predictable optimization over aggressive automation.

## Tool integration roles

VRCForge integrates third-party optimizers through package detection, dependency cards, plan schemas, approval-gated write requests, checkpoint creation, validation deltas, rollback proof, and support-bundle audit records.

VRCForge does not copy optimizer source code, silently install packages, direct-write optimizer components from external agents, or run every optimizer as a single batch. Specialized transforms stay in their owning packages:

- AAO / Avatar Optimizer: non-destructive avatar optimization, conservative Trace And Optimize, merge planning, cleanup transforms.
- Avatar Compressor / LAC: NDMF texture compression profiles.
- TexTransTool: atlas and texture transform components.
- Meshia Mesh Simplification: mesh simplification and preview/apply components.
- VRCFury: VRCFury-specific controller, parameter, Direct Tree, and build-hook transforms.
- MA2BT-Pro: Modular Avatar responsive layer to BlendTree conversion.
- VRC Avatar Performance Tools: optional editor-side performance and VRAM reference checks.

## Stable callable matrix

The avatar optimization skill group exposes stable read/plan tools plus stable `*-apply-request` tools only. A request tool creates an approval record; it does not bypass Desktop approval, checkpoint, validation, or rollback.

The internal executor targets `vrcforge_configure_optimizer_component` and `vrcforge_install_vpm_package` are wrapper-only. They are not listed as external write targets, and generic `vrcforge_request_apply` calls cannot target them directly; callers must use the named optimizer or package install request tools.

Stable request tools:

- `optimization.lac.apply-request`: adds/configures `dev.limitex.avatar.compressor.TextureCompressor` for conservative/balanced profiles.
- `optimization.aao.trace-apply-request`: adds the public `Anatawa12.AvatarOptimizer.TraceAndOptimize` marker component. AAO documents the component type as the public API, so VRCForge does not script-configure internal AAO fields.
- `optimization.ttt.atlas-apply-request`: adds/configures `net.rs64.TexTransTool.TextureAtlas.AtlasTexture` only when the request includes user-confirmed `Assets/...` material asset paths for `AtlasTargetMaterials`.
- `optimization.ma2bt.convert-apply-request`: adds/configures `zhuozhi.MA2BTPro.MAToBlendTreePro` with conservative public settings.
- `optimization.meshia.simplify-apply-request`: adds/configures `Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier` on one user-selected renderer path only. Stable requests are limited to conservative relative vertex counts from `0.75` to `1.0`.
- `optimization.vrcfury.parameter-compressor-apply-request`: stable request name, but currently returns a blocked preview because inspected VRCFury parameter-compressor feature models are internal and do not provide a validated public writer path for VRCForge.
- `optimization.vrcfury.direct-tree-apply-request`: stable request name, but currently returns a blocked preview by default because Direct Tree remains experimental and must not be enabled by external agents.

Stable read-only plugin calls:

- `optimization.performance-tools.report`: reports the VRC Avatar Performance Tools integration surface.
- `vrcforge_scan_thry_avatar_performance`: calls Thry's read-only VRAM/mesh calculator helpers when the package is installed. VRCForge does not invoke Thry UI actions that change texture import settings.

Still blocked or experimental until further sample-matrix validation:

- TTT automatic group selection, atlas execution without user-confirmed materials, and material-slot reduction that requires mesh/material coordination.
- Meshia aggressive simplification, body/face/eyes/mouth/hands simplification, and auto-selected renderer writes.
- VRCFury Parameter Compressor and Direct Tree writes until a public, validated writer path and rollback proof exist.
- One-click multi-optimizer execution.

Package dependency installs use the same safety model. VRCForge detects ALCOM/VCC for UI handoff, prefers a supervised VCC `vpm` or `vrc-get` CLI command for non-interactive installs, and falls back to an agent-managed package-manager download plan only when no supported manager is available. Direct manifest editing is not a default install path.

## Roadmap

### 0.7.2-beta - Model Optimization Planner

P0:

- Optimization Dashboard
- Baseline scan
- Target profiles
- Dependency doctor for AAO, LAC, TexTransTool, Meshia, MA2BT-Pro, VRCFury, VRC Avatar Performance Tools, NDMF, Modular Avatar, and VRChat SDK
- Texture VRAM audit
- Material slot audit
- Parameter budget audit
- Mesh triangle audit
- AAO plan
- LAC profile plan
- TTT atlas plan
- MA2BT convertibility plan
- VRCFury compatibility report
- Meshia simplification plan
- MCP read/plan exposure
- No direct apply

Done when:

- The user can see top performance offenders without modifying the project.
- The user can get a step-by-step optimization plan.
- Codex and Claude Code can request the same read-only plan through MCP.
- VRCForge recommends one optimization step at a time.

### 0.8.0-beta - Delegated Optimizer Skill Pack v1

P0:

- LAC conservative/balanced apply
- AAO conservative Trace And Optimize apply
- TTT atlas apply with user-confirmed groups
- MA2BT-Pro apply for MA Responsive layers
- Stable request names for Meshia and VRCFury that block unsafe writes with actionable reasons
- Read-only Thry performance/VRAM calculator bridge
- Before/after validation delta
- Before/after screenshot comparison
- Rollback proof after each step

Rules:

- One optimizer step at a time
- Every apply uses approval, checkpoint, validation, and rollback
- No external agent direct apply
- No one-click all optimizers

### 0.8.1-beta - Advanced Optimization

P0/P1:

- Hidden body cut plan/apply with manual visual confirmation
- Meshia simplify preview/apply for low-risk accessories and clothing
- VRCFury Parameter Compressor plan/apply as Experimental
- VRCFury Direct Tree plan-only by default
- MA2BT skipped-reason diagnostics

Experimental:

- Aggressive Meshia simplification
- Automatic hidden body cut
- VRCFury Direct Tree apply
- Aggressive Quest profile

### 0.9.0-beta - End-to-end Optimization Workflow

P0:

- PC Conservative / PC Medium / Quest Medium / Event Light profiles
- Before/after performance delta
- Before/after screenshots
- Validation delta report
- Batch scan
- External-agent optimization workflow
- Rollback proof

### 0.9.5-rc - Optimization Freeze

Allowed:

- Compatibility fixes
- False positive and false negative fixes
- Rollback fixes
- Validation fixes
- NDMF order fixes
- Docs and UI copy fixes

Not allowed:

- New optimizer integrations
- New default apply paths
- New direct external-agent writers
- New aggressive optimization defaults

### 1.0 Public Stable

Stable:

- Baseline scan
- Target profile
- Dependency doctor
- VRAM/material/mesh/parameter/animator audits
- LAC conservative/balanced delegated apply
- AAO conservative delegated apply
- TTT user-confirmed atlas delegated apply
- MA2BT-Pro delegated apply for MA-heavy avatars
- Before/after validation
- Rollback proof
- External-agent read/plan/write-request flow

Experimental at 1.0:

- Meshia aggressive simplification
- Hidden body cut auto-apply
- VRCFury Parameter Compressor apply
- VRCFury Direct Tree apply
- One-click aggressive Quest optimization
