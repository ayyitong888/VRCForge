# Windows x64 Release Packaging

VRCForge keeps `start.cmd`, PowerShell scripts, and `quickstart/` as debug paths. The release path is Windows x64 only:

- `VRCForge_Web_Installer_x64.exe`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge.exe`

Program files install to `%ProgramFiles%\VRCForge`. User data lives under `%LOCALAPPDATA%\VRCForge\agentic-app` and contains `config/`, `logs/`, `artifacts/`, and `backups/`.

The payload root `VRCForge.exe` is the Tauri desktop app. Legacy launcher and
`start_dashboard.cmd` paths remain debug/compatibility surfaces only; they are
not the primary release entry point.

## Build Gates

`packaging/build_release.ps1` refuses to package when:

- `git status --short` is not clean
- `git log origin/main..HEAD --oneline` has unpushed commits
- local `VERSION` differs from `origin/main:VERSION`
- the VRCForge-owned Unity package or paired desktop/backend trust contract fails
- uv runtime license / notice gate fails
- .NET SDK 8.0+ or NSIS is missing
- the web installer download URL is not the exact official version-bound HTTPS asset

For an unpublished next-version acceptance build, `packaging/build_local.ps1`
may package an unpushed `VERSION` that differs from `origin/main`. That wrapper
passes both local-only gates and labels the output unpublished. It does not
relax `build_release.ps1` or `publish_release.ps1`: releasable artifacts still
require a clean, pushed HEAD whose `VERSION` matches `origin/main`.
The generated manifest records `buildPolicy.mode=local-acceptance` and
`releaseEligible=false`; neither the stable gate nor the publisher accepts it,
even if the same commit is pushed later. Rebuild strictly after pushing.

The release builds `VRCForge.unitypackage` directly from the staged
`Assets/VRCForge` tree. The package contains the VRCForge-owned project-scoped
MCP Core, the fixed 64-tool registry, Editor lifecycle bootstrap, and the
generated desktop/backend trust binding. Users import this one package; no
separate MCP server, Unity package dependency, manifest edit, Python command,
or token copy is required.

The App and Unity package use only protocol `2026-07-28` with newline JSON-RPC
transport. Release acceptance must reject old protocol strings, legacy
transport branches, external Unity MCP package paths, a tool count other than
64, or trusted desktop/backend hashes that differ from the packaged binaries.

Every release build runs `packaging/check_third_party_licenses.ps1` before
packaging. The manifest is `packaging/THIRD_PARTY_LICENSES.json`; add any new
bundled third-party component there before shipping it. A release must stop if a
bundled component lacks a recognized redistributable license, required license
text, or required notice/distribution notes.

The release build also scans the actual source inputs immediately before
packaging and all four generated artifacts afterward for high-confidence
private-key, credential, token, credential-URL, and local-machine-path markers.
Any finding stops the build. Diagnostics report only the member path, line, and
rule; the matched value is never printed.

Windows x64 payloads bundle the official uv runtime under `tools/uv/` for
backend-managed support tooling without requiring a system Python or uv
installation. uv is licensed `MIT OR Apache-2.0`; preserve:

- `licenses/uv-LICENSE-MIT.txt`
- `licenses/uv-LICENSE-APACHE-2.0.txt`
- `licenses/uv-DISTRIBUTION-NOTES.txt`

## Commands

The current source and latest published stable package is `1.7.7` (`v1.7.7`).
Its tag, manifest, source, and asset evidence were published together; the
v1.7.6 tag and Release page remain available and unchanged. It also supersedes
the unpublished `v1.7.0` Draft. Check the GitHub Releases page before preparing
any later build. The Avatar
Encryption / Anti-Rip addon remains a connector preview and is not bundled with
the `v1.7.7` package. The public repo must not contain encryption
implementation files; it may only expose connector/request interfaces for a
separately installed private addon module. Profile docs must list Lite,
Standard, and Paranoid, with Standard as the default recommendation and
Paranoid blocked until additional proof exists.
All three profiles are Windows PC-only. Quest/Android requests must remain
blocked for this feature.
Do not describe implementation details in release notes; say only that the
release includes Avatar Encryption / Anti-Rip connector request interfaces
with approval, checkpoint, rollback, and proof gates.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
  -Version 1.7.7 `
  -PayloadDownloadUrl https://github.com/ayyitong888/VRCForge/releases/download/v1.7.7/VRCForge_Windows_x64_1.7.7.zip `
  -UvDownloadSha256 ebc76197bf3e1a58f9dac6f70f49b0ebd3e6907ab35289ce228bce5ba8a3f201

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\publish_release.ps1 `
  -Version 1.7.7
```

Publishing uploads the Unity package, Windows payload zip, offline installer,
and web installer to the GitHub Release matching `VERSION`. The web installer
also requires the payload zip on the same release:

```text
VRCForge.unitypackage
VRCForge_Windows_x64_<VERSION>.zip
VRCForge_Offline_Installer_x64.exe
VRCForge_Web_Installer_x64.exe
```

Before publishing a version, fill the release evidence and proof matrix with
real artifact paths, sizes, SHA-256 hashes, and acceptance notes. Placeholder
rows are allowed in docs before that handoff, but release notes must not imply
unverified artifact/hash evidence.

Normally, do not replace artifacts for an existing tag; bump `VERSION`, build
from the matching pushed commit, and publish a new tag/release. If the release
owner explicitly authorizes a signed refresh of an existing version, rebuild
from the authorized target, replace all affected assets together, and re-verify
the published hashes and release evidence before closing the refresh. The build
derives and enforces the official version-bound HTTPS asset URL; alternate
hosts, paths, query strings, fragments, tag names, and payload filenames fail
the release gate.

Release smoke should also verify first-run resilience: optional failures in
user-data `AGENTS.md` creation, project scanning, Unity/MCP discovery, skill
loading, or external-agent MCP startup must not prevent the backend and ordinary
agent chat from opening.

The installer install/uninstall smoke is reusable on any Windows x64 machine.
Run it from an elevated shell, or start it with UAC from a non-elevated shell.
Use only a compiler-scoped smoke flavor with a disposable install directory so
an existing user install is not overwritten. The smoke installer must be built
with `VRCFORGE_SMOKE_BUILD` and the same validated `VRCFORGE_NSIS_SMOKE_ID`
shown below. Never point this command at `dist\release`: changing `/D` does not
change a production installer's registry, shortcut, or uninstall identity.

```powershell
$smokeId = [guid]::NewGuid().ToString("N")
python scripts\smoke_installer_install_uninstall.py `
  --installer "artifacts\installer-smoke-build\$smokeId\VRCForge_Offline_Installer_x64.exe" `
  --smoke-id $smokeId `
  --install-dir "$env:ProgramFiles\VRCForge-Smoke-$smokeId" `
  --user-data-root "$env:LOCALAPPDATA\VRCForge\installer-smoke\$smokeId" `
  --backend-port 8791
```

The smoke requires the exact generated identity in both the disposable
Program Files leaf and the isolated user-data root. This prevents the test from
overwriting or uninstalling a real VRCForge installation. Reports default to
`artifacts\installer-smoke`; use `--artifacts-dir` only to relocate evidence.

The isolated smoke proves installer behavior but cannot satisfy the stable
artifact hash gate because its compiler-scoped registry, shortcut, uninstall,
and user-data identity intentionally changes the executable bytes. Final stable
evidence must run the exact strict offline installer in a disposable clean
Windows VM (or an equivalent throwaway machine) with no existing VRCForge
install, user data, shortcuts, or registry identity. The runner checks those
conditions before mutation and refuses to run without the exact confirmation:

```powershell
python scripts\smoke_installer_install_uninstall.py `
  --scope production-clean `
  --production-clean-confirmation I-OWN-THIS-DISPOSABLE-WINDOWS-ENVIRONMENT `
  --upgrade-installer "<downloaded official v1.6.0 offline installer>" `
  --installer "dist\release\VRCForge_Offline_Installer_x64.exe" `
  --backend-port 8791
```

Do not run `production-clean` on a normal workstation or an environment that
contains user data. Do not pass `--smoke-id`, `--install-dir`, or
`--user-data-root` in this mode. For 1.6.2 stable evidence the report must show a
successful production-identity 1.6.0-to-1.6.2 upgrade, health check, uninstall, and
user-data preservation, and its current-installer SHA-256 must exactly match the
strict release manifest.

Manual Unity package fallback smoke should import `VRCForge.unitypackage` into a
fresh supported VCC VRChat Avatar project on Unity 2022.3 and verify zero compiler errors, protocol
`2026-07-28`, all 64 VRCForge tools, automatic App connection to that exact
project, a UTF-8 read, an approved checkpointed write/readback/restore, and
automatic reconnect after Unity domain reload and App restart. Folder entries
in the `.unitypackage` must contain only folder metadata and no empty `asset`
payload, otherwise Unity can fail with `Failed to copy package file to
Assets/VRCForge/Editor` on first import.
The same disposable-project pass must exercise
`VRCForge > Uninstall VRCForge...`, restart the Editor, and verify that
`Assets/VRCForge`, VRCForge menus, the Core listener, and the versioned
auto-connect EditorPrefs key are absent.

External-agent release smoke must verify both config generation and the
supervised write/rollback path. The preflight smoke temporarily enables the
gateway and restores previous gateway/permission state; the live smoke also
writes to Unity and rolls back:

```powershell
npm run smoke:external-agent
npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject --parent-path Avatar
```

The live report must show `vrcforge_request_apply` advertised, direct apply
tools hidden, a checkpoint id, validation report generation, rollback applied,
no temporary GameObject residue, Unity compile errors at zero, and cleanup that
restores the previous gateway and permission settings. If rollback fails, fix
rollback before publishing.

For packaged builds, Agent Connector stdio config should point at
`backend/vrcforge_backend.exe --agent-mcp-stdio --no-start` instead of requiring
a system Python installation. Generated client config should not let Codex or
other MCP clients launch the desktop app implicitly; VRCForge should already be
running.

Stable public-support smoke should also verify that Doctor can export a
support bundle and that the GitHub issue template asks users to upload or
paste that artifact manually. The bundle must not be auto-attached to issues.

Before publishing or refreshing a stable release, run the stable-readiness gate:

```powershell
python scripts\smoke_stable_readiness_gate.py `
  --version 1.6.2 `
  --latest-stable 1.6.0 `
  --installer-smoke "<production-clean installer report>" `
  --upgrade-from-installer-sha256 853dfce74830e73098cc55240abf1e23162d66e579225d16f5b13d44089ca2d4 `
  --max-artifact-age-hours 24 `
  --require-live-writes
```

For the owner-approved 1.6.2 corrective publication, fresh warm-start,
Golden Path, packaged Skill, optimizer, external-Agent, and clean-Windows
install/upgrade/uninstall inputs are explicitly deferred until after
publication. The user-attested live 1.6.2 UI/UX comparison is not deferred and
does not require a screenshot artifact. Do not
relabel a blocked or incomplete readiness report as a pass, and do not claim
deferred probes as pre-release evidence. All non-deferred UI, build, policy,
provenance, document, manifest and artifact checks remain authoritative; the
deferred probes must later be refreshed against the exact published hashes.

This gate checks current target-version public docs, the public golden-path wording,
the privacy boundary, `docs/COMPATIBILITY_MATRIX.md`, and local evidence
pointers when they exist in the checkout. For the current `1.7.7` target release,
the gate also checks that public docs distinguish source/target from the latest published release,
direct avatar-encryption writers are not exposed, and the public surface is
only the private-addon connector request interface with explicit approval,
checkpoint, and rollback.
For `1.3.0` and newer it also requires a manifest-bound packaged Skill
Ecosystem report (`--skill-ecosystem-smoke`) and a non-skipped `.vsk` Golden
Path lifecycle. Local-only acceptance reports do not satisfy that strict
release binding.
