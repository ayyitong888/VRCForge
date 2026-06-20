# Windows x64 Release Packaging

VRCForge keeps `start.cmd`, PowerShell scripts, and `quickstart/` as debug paths. The release path is Windows x64 only:

- `VRCForge_Web_Installer_x64.exe`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge.exe`

Program files install to `%ProgramFiles%\VRCForge`. User data lives under `%LOCALAPPDATA%\VRCForge` and contains `config/`, `logs/`, `artifacts/`, and `backups/`.

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
  -PayloadDownloadUrl https://github.com/ayyitong888/VRCForge/releases/download/v0.5.2-beta/VRCForge_Windows_x64_0.5.2-beta.zip

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\publish_release.ps1
```

Publishing uploads the two installer executables to the GitHub Release matching `VERSION`.
The web installer also requires the payload zip on the same release:

```text
VRCForge_Windows_x64_<VERSION>.zip
```

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
