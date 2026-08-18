# Release Checklist

Before publishing a release package:

* [ ] Run the 1.6.2 no-regression gates for General Cowork/right-rail order,
      conditional Goal placement and `/goal` plus Agent control, compact generic
      slash commands plus `/handoff`, sole right-rail collapse ownership,
      jump-to-bottom with no first wheel-arrival bounce, complete Provider/model
      identity, continuous context ring thresholds, startup-only silent App
      Update and the General-read
      `A -> B -> A` / third-no-progress boundary. Confirm General uses cached
      project metadata while only Unity discovery refreshes without blocking
      native window drag; empty groups have no fold control; zero-source General
      chats have no `Context`/`Sources` placeholder. Run one real ordinary Agent
      task and verify every non-CoT planner update, tool invocation and final
      answer remains visibly interleaved in authoritative sequence/timestamp
      order. Only adjacent same-kind events may be compacted; any intervening
      commentary, status, tool call or tool result must split the batch, with no
      single-event generic `Work segment` / `Used tools` wrapper.
      General Agent tool rows use plain capability names without `vrcforge_`;
      Unity-specific rows use the `unity_` namespace.
      Expand a result longer than 1000 characters and confirm its tail remains
      present inside the internally scrolling card; repeated action IDs must
      bind full stored results in occurrence order rather than reuse the last.
      Confirm App-global and bound-project `AGENTS.md` rules reach planning
      without entering tool arguments, logs or the visible timeline. Each changed UI contract also
      needs user-attested live manual acceptance at an equivalent state against
      the approved Codex/Claude Cowork references; no screenshot artifact is required.
      Confirm General Settings never renders the local identity-map alias list;
      enable Developer Options and confirm the diagnostic map is available only
      on the Developer page.
* [ ] Confirm Goal keeps both entry paths: users can `/goal <objective>`, view,
      pause, resume and clear; the Agent sees exactly `get_goal`, `create_goal`
      and `update_goal`. Agent create requires an explicit user request and
      conflicts with any unfinished Goal. Agent update accepts only
      evidence-backed completion or blocked evidence; the same reason must be
      observed on three distinct consecutive Goal turns, and user resume resets
      the audit. Runtime scope must override model-supplied chat/session/project/
      turn identifiers. Reject Agent pause/resume/clear/cancel, silent Goal
      replacement, token budgets, price lookup and cost-conversion UI.
* [ ] Confirm the profiled registry exposes Core + General in General Mode and
      Core + General + Unity in Unity Project Mode using the same handlers.
      Registered Unity roots remain readable by General tools but reject
      ordinary Edit/Write/Delete/Move/apply_patch and ordinary Shell cwd/direct
      path references; `unity_shell` is current-root-only, and external paths
      remain open. Reject any Shadow Workspace, OS sandbox/ACL, separate-user,
      filesystem-interception or adversarial-bypass scope expansion.
* [ ] Review `git diff --numstat <release-base> -- agent_gateway.py` as a
      diagnostic, not a zero-growth gate. Every net increase must have a
      written per-hunk reason showing that the code is Gateway-owned
      coordination or a trust-boundary seam, that moving it to an existing
      focused module would weaken ownership or duplicate behavior, and that a
      regression test binds the behavior. Reject unexplained growth and domain,
      presentation, registration-catalog or helper logic that belongs outside
      the Gateway; do not reject justified growth by line count alone.
* [ ] Confirm the user slash list contains only the approved Agent-generic
      commands (`/compact`, `/goal`, `/memory`, `/delegate`, `/handoff`, plus
      Developer-only `/desktop` when enabled); domain skills remain Agent-owned.
* [ ] Confirm App Update runs once in the startup background and only a
      successful newer version opens an in-App dialog. Current/offline/error
      paths must be silent; manual/periodic/system-notification paths are absent.
* [ ] Confirm Memory Settings shows only **Enable Memory** and **Remember and
      use Memory across conversations**. Dreaming must read only already-saved
      Memory, reuse the configured BYOK Provider/Model, keep all Memory intact
      through the proposal pass, and make a separate second model call over the
      same batch plus proposal. Commit only the second pass's final duplicate
      set after local scope/kind/ID/snapshot/removal checks; failed review leaves
      Memory unchanged and no Dreaming workflow/provider/budget UI is visible.
* [ ] Confirm theme customization exposes the seven approved multi-colour
      palettes, keeps the untouched default appearance unchanged, stores only a
      managed background path rather than Base64, accepts images above the old
      2 MiB boundary, exposes 0–100% visibility, and offers both center-only and
      continuous whole-App background coverage (including both sidebars).
      Custom mode must expose accent/background-base seeds through a palette,
      editable HEX/RGB/HSL values and a capability-gated screen eyedropper;
      verify automatic light/dark derivation and exactly three deduplicated
      recent colours retained across **Restore defaults**. Typed fields retain
      raw partial text while editing, commit only on valid blur/Enter, and never
      commit a composing IME Enter.
      Replacing, removing and
      **Restore defaults** must clear only VRCForge-managed background files;
      unrelated/user-named files remain intact.

* [ ] Include LICENSE in the release package.
* [ ] Include NOTICE in the release package.
* [ ] Include README.md or a link to the official repository.
* [ ] Include source code archive or clear source code access link.
* [ ] Mark the version number clearly.
* [ ] Mark whether this is an official release or a modified build.
* [ ] Ensure third-party dependencies and their licenses are documented.
* [ ] Run
      `python scripts\smoke_stable_readiness_gate.py --version <VERSION> --latest-stable <PUBLISHED_VERSION> --installer-smoke <production-clean-report> --upgrade-from-installer-sha256 <published-previous-offline-installer-sha256>`
      and resolve any public-doc or COMPATIBILITY_MATRIX blocker before
      publishing a stable release or stable refresh. This includes the Doctor
      support bundle flow and prevents target-version docs from mislabeling an
      unpublished build as the latest stable release.
      For an already-published stable refresh, pass `--stable-refresh` and set
      `--latest-stable` equal to `<VERSION>`; a new release must name a lower
      explicitly published stable version.
    - Stable installer evidence must use the exact strict production installer
      in the runner's `production-clean` mode on a disposable clean Windows
      environment. A compiler-scoped isolated smoke flavor is useful behavior
      evidence but can never satisfy the production installer hash gate.
* [ ] For `1.3.0` and newer, provide a fresh
      `--skill-ecosystem-smoke <report.json>` artifact from
      `scripts\diagnose_packaged_skill_ecosystem.mjs`. The strict gate must
      bind it to the release-manifest commit and portable ZIP SHA-256, and the
      `.vsk` Golden Path row must pass instead of skip.
* [ ] For a stable release or stable refresh, add the freshness/liveness guards
      so a stale or writes-skipped artifact cannot carry the gate:
      `--max-artifact-age-hours <N>` blocks any required smoke artifact older
      than `N` hours, and `--require-live-writes` rejects a Golden Path Matrix
      artifact that ran with `safeDefault=True` (writes skipped) instead of a
      real live write. Both flags are opt-in; capture a fresh Golden Path Matrix
      artifact with live writes before enabling `--require-live-writes`.
* [ ] Run `packaging/check_third_party_licenses.ps1` and stop the release if any
      bundled component fails its license gate.
* [ ] Add every bundled third-party component to
      `packaging/THIRD_PARTY_LICENSES.json` before publishing it.
* [ ] Confirm `VRCForge.unitypackage` contains only VRCForge-owned MCP Core and
      tool sources under `Assets/VRCForge`, with no external Unity MCP package.
* [ ] Extract
      `Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs` from the built
      package and confirm GUID `c03999e57815100961016fab067f9c2b`, first-line
      `#if UNITY_EDITOR`, final-line `#endif`, and guarded `EditorUtility` /
      `GlobalObjectId` references. Reject any package that can reproduce
      `CS0103` in `Assembly-CSharp.dll` or break Player/VRChat `Build & Test`.
* [ ] Confirm the package exposes exactly 64 tools and only protocol
      `2026-07-28`; old protocol/transport/fallback strings must be absent.
* [ ] Confirm the package-generated trusted desktop/backend SHA-256 values
      match the exact binaries in the paired Windows payload.
* [ ] Add a warning that users should back up Unity / VRChat avatar projects before writing assets.
* [ ] Add changelog notes for major behavior changes.
* [ ] Confirm the public compatibility matrix covers Unity, VRChat SDK,
      Modular Avatar, NDMF, VRCFury, AAO, LAC, TTT, Meshia, MA2BT-Pro, Thry
      tools, lilToon, Poiyomi, known conflicts, and known safe profiles.
* [ ] Confirm desktop WebView CORS preflight for authenticated app APIs returns
      200, not 401:
      `OPTIONS /api/app/bootstrap` with `Origin: tauri://localhost` and
      `Access-Control-Request-Headers: authorization`.
* [ ] Confirm startup/refresh failure UI points to Startup Doctor and Retry,
      and does not mislabel a runtime-offline state as a Unity project failure.
* [ ] Run external-agent preflight smoke: `npm run smoke:external-agent`.
* [ ] Run external-agent live write/rollback smoke against a real Unity project:
      `npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject`.
* [ ] Confirm external-agent smoke hides direct apply tools, creates a
      checkpoint, runs validation, restores the checkpoint, leaves no temporary
      GameObject residue, and keeps Unity compile errors at zero.
* [ ] If live smoke hits a timeout after creating a checkpoint, confirm the
      report contains `rollback.emergency` and
      `rollback.verify_no_residue_after_emergency` evidence before treating the
      Unity project as clean.
* [ ] If external-agent rollback fails, fix rollback before publishing.
* [ ] For optimizer releases, update the proof matrix with artifact paths for
      request guard, direct-apply exposure, validation delta, screenshots, and
      rollback proof.
* [ ] For releases that ship `VRCForge.unitypackage`, run a fresh-project
      direct import smoke; confirm folder entries do not contain empty `asset`
      payloads, Editor and Player compilation have zero errors and no unexpected
      warnings, and VRChat `Build & Test` reaches validation/build normally.
* [ ] Do not remove GPL-3.0 notices from redistributed or modified versions.
