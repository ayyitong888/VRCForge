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
- CoplayDev Unity MCP license / notice gate fails
- uv runtime license / notice gate fails
- .NET SDK 8.0+ or NSIS is missing
- the web installer download URL is not provided

The CoplayDev Unity MCP package must be pinned locally before packaging:

```text
third_party/com.coplaydev.unity-mcp/
```

The release payload copies it into:

```text
unity_plugin/Packages/com.coplaydev.unity-mcp/
```

and writes the Unity manifest dependency as:

```json
"com.coplaydev.unity-mcp": "file:Packages/com.coplaydev.unity-mcp"
```

The pinned package is MIT licensed. The release build must preserve:

- `unity_plugin/Packages/com.coplaydev.unity-mcp/LICENSE`
- `unity_plugin/Packages/com.coplaydev.unity-mcp/VRCFORGE_DISTRIBUTION_NOTES.txt`
- `licenses/VRCForge-NOTICE.txt`
- `licenses/CoplayDev-Unity-MCP-LICENSE.txt`
- `licenses/CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt`

Every release build runs `packaging/check_third_party_licenses.ps1` before
packaging. The manifest is `packaging/THIRD_PARTY_LICENSES.json`; add any new
bundled third-party component there before shipping it. A release must stop if a
bundled component lacks a recognized redistributable license, required license
text, or required notice/distribution notes.

Windows x64 payloads bundle the official uv runtime under `tools/uv/` so the
desktop app/backend can use `uvx --from mcpforunityserver unity-mcp` when Python
and uv are not installed system-wide. uv is licensed `MIT OR Apache-2.0`; preserve:

- `licenses/uv-LICENSE-MIT.txt`
- `licenses/uv-LICENSE-APACHE-2.0.txt`
- `licenses/uv-DISTRIBUTION-NOTES.txt`

`packaging/check_coplaydev_mcp_license.ps1` also refuses to build if the pinned
CoplayDev package is missing the expected CoplayDev MIT LICENSE text or VRCForge
distribution notes.

## Commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
  -Version 1.0.0 `
  -PayloadDownloadUrl https://github.com/ayyitong888/VRCForge/releases/download/v1.0.0/VRCForge_Windows_x64_1.0.0.zip

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\publish_release.ps1 `
  -Version 1.0.0
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

Do not upload artifacts built from a newer commit into an older existing tag.
If release contents change after `v<VERSION>` was already created, bump
`VERSION`, push that version commit, build with the matching
`-PayloadDownloadUrl`, then publish the new tag/release. The web installer
downloads exactly the URL passed at build time, so that URL must point to the
payload zip generated from the same commit.

Release smoke should also verify first-run resilience: optional failures in
user-data `AGENTS.md` creation, project scanning, Unity/MCP discovery, skill
loading, or external-agent MCP startup must not prevent the backend and ordinary
agent chat from opening.

The installer install/uninstall smoke is reusable on any Windows x64 machine.
Run it from an elevated shell, or start it with UAC from a non-elevated shell.
Use a disposable install directory for smoke so an existing user install is not
overwritten:

```powershell
python scripts\smoke_installer_install_uninstall.py `
  --installer dist\release\VRCForge_Offline_Installer_x64.exe `
  --install-dir "$env:ProgramFiles\VRCForge-Smoke" `
  --backend-port 8791
```

The script defaults to `%ProgramFiles%\VRCForge`,
`%LOCALAPPDATA%\VRCForge\agentic-app`, and
`artifacts\installer-smoke`, but all three can be overridden with
`--install-dir`, `--user-data-root`, and `--artifacts-dir`.

Manual Unity package fallback smoke should import `VRCForge.unitypackage` into a
fresh Unity project and verify `Assets/VRCForge/Editor` plus a representative
editor script exist. Folder entries in the `.unitypackage` must contain only
folder metadata and no empty `asset` payload, otherwise Unity can fail with
`Failed to copy package file to Assets/VRCForge/Editor` on first import.

External-agent release smoke must verify both config generation and the
supervised write/rollback path. The preflight smoke temporarily enables the
gateway and restores previous gateway/permission state; the live smoke also
writes to Unity and rolls back:

```powershell
npm run smoke:external-agent
npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject
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
python scripts\smoke_stable_readiness_gate.py --version 1.0.0
```

This gate checks current-version public docs, the public golden-path wording,
the privacy boundary, `docs/COMPATIBILITY_MATRIX.md`, and local evidence
pointers when they exist in the checkout.
