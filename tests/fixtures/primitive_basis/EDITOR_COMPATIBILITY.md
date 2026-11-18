# Editor file preconditions

Use the local `6000.0.67f1` editor and an isolated project with the matching VRChat test SDK to exercise the announced VRChat upgrade. Repeat with `2022.3.22f1` and its current SDK for comparison. VRCForge runtime support and release qualification are separate from these two probes. [VRChat Developer Update, 5 November 2026](https://ask.vrchat.com/t/developer-update-5-november-2026/49298)

Run from the repository root in PowerShell. Set `$unityExe` to the editor under test and `$packageProject` to a disposable fixture project whose dependencies have already been resolved with that editor and SDK. The commands copy its project configuration and package cache into a fresh temp directory, then copy the current VRCForge source and the two probes.

```powershell
$unityExe = '<path to the chosen Unity.exe>'
$packageProject = '<path to a resolved disposable Unity project>'
$scratch = Join-Path $env:TEMP ("vrcforge-editor-smoke-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path "$scratch/Assets/Editor", "$scratch/Library" | Out-Null
Copy-Item -Recurse -LiteralPath "$packageProject/Packages" -Destination "$scratch/Packages"
Copy-Item -Recurse -LiteralPath "$packageProject/ProjectSettings" -Destination "$scratch/ProjectSettings"
Copy-Item -Recurse -LiteralPath "$packageProject/Library/PackageCache" -Destination "$scratch/Library/PackageCache"
Copy-Item -Recurse -LiteralPath './Assets/VRCForge' -Destination "$scratch/Assets/VRCForge"
$fixtures = './tests/fixtures/primitive_basis'
Copy-Item -LiteralPath "$fixtures/texture_import_settings/TextureImportSettingsFixtureProbe.cs" -Destination "$scratch/Assets/Editor"
Copy-Item -LiteralPath "$fixtures/animation_curve_write/AnimationCurveWriteFixtureProbe.cs" -Destination "$scratch/Assets/Editor"

$probes = [ordered]@{
    'TextureImportSettingsFixtureProbe.Run' = 'VRCFORGE_TEXTURE_IMPORT_SETTINGS_PROBE_OK'
    'AnimationCurveWriteFixtureProbe.Run' = 'VRCFORGE_ANIMATION_CURVE_WRITE_PROBE_OK'
}
$failed = $false
foreach ($method in $probes.Keys) {
    $log = Join-Path $scratch "$method.log"
    $launchArgs = @('-batchmode', '-nographics', '-quit', '-projectPath', ('"{0}"' -f $scratch),
        '-executeMethod', $method, '-logFile', ('"{0}"' -f $log))
    $process = Start-Process -FilePath $unityExe -ArgumentList $launchArgs -Wait -PassThru -WindowStyle Hidden
    $passed = ($process.ExitCode -eq 0) -and (Select-String -LiteralPath $log -SimpleMatch -Quiet $probes[$method])
    "${method} passed=$passed exit=$($process.ExitCode)"
    Select-String -LiteralPath $log -Pattern 'VRCFORGE_COMPATIBILITY_UNITY|VRCFORGE_COMPATIBILITY_SDK|VRCFORGE_ANIMATION_CURVE_ENTRY_OK|error CS' |
        ForEach-Object { $_.Line }
    $failed = $failed -or -not $passed
}
"Logs in $scratch"
if ($failed) { throw 'One or more editor probes failed. See the logs above.' }
```

`VRCFORGE_COMPATIBILITY_UNITY` is the actual `Application.unityVersion`. The animation probe also reports the registered VRChat Base and Avatars SDK versions as `VRCFORGE_COMPATIBILITY_SDK`. The texture probe checks preview, apply, no-op, timestamp-only edits and source or metadata changes. The animation probe checks persisted curves and the flat, single-clip and multi-clip input forms.
