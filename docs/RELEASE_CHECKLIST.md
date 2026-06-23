# Release Checklist

Before publishing a release package:

* [ ] Include LICENSE in the release package.
* [ ] Include NOTICE in the release package.
* [ ] Include README.md or a link to the official repository.
* [ ] Include source code archive or clear source code access link.
* [ ] Mark the version number clearly.
* [ ] Mark whether this is an official release or a modified build.
* [ ] Ensure third-party dependencies and their licenses are documented.
* [ ] Run `packaging/check_third_party_licenses.ps1` and stop the release if any
      bundled component fails its license gate.
* [ ] Add every bundled third-party component to
      `packaging/THIRD_PARTY_LICENSES.json` before publishing it.
* [ ] For bundled CoplayDev Unity MCP, include the upstream MIT LICENSE in both
      the package root and release `licenses/` folder.
* [ ] For bundled CoplayDev Unity MCP, include VRCForge distribution notes that
      state the upstream project, pinned commit, license, and local changes.
* [ ] Add a warning that users should back up Unity / VRChat avatar projects before writing assets.
* [ ] Add changelog notes for major behavior changes.
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
      direct import smoke and confirm folder entries do not contain empty
      `asset` payloads.
* [ ] Do not remove GPL-3.0 notices from redistributed or modified versions.
