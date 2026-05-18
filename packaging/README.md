# Windows x64 Release Packaging

VRCForge keeps `start.cmd`, PowerShell scripts, and `quickstart/` as debug paths. The release path is Windows x64 only:

- `VRCForge_Web_Installer_x64.exe`
- `VRCForge_Offline_Installer_x64.exe`
- `VRCForge.exe`

Program files install to `%ProgramFiles%\VRCForge`. User data lives under `%LOCALAPPDATA%\VRCForge` and contains `config/`, `logs/`, `artifacts/`, and `backups/`.

## Build Gates

`packaging/build_release.ps1` refuses to package when:

- `git status --short` is not clean
- `git log origin/main..HEAD --oneline` has unpushed commits
- local `VERSION` differs from `origin/main:VERSION`
- CoplayDev Unity MCP license / notice gate fails
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

## Commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
  -PayloadDownloadUrl https://github.com/ayyitong888/VRCForge/releases/download/v0.3.1-alpha/VRCForge_Windows_x64_0.3.1-alpha.zip

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\publish_release.ps1
```

Publishing uploads the two installer executables to the GitHub Release matching `VERSION`.
The web installer also requires the payload zip on the same release:

```text
VRCForge_Windows_x64_<VERSION>.zip
```
