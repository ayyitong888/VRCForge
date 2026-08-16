# VRCForge 1.6.1

VRCForge 1.6.1 is a corrective replacement for VRCForge 1.6.0. It contains no
new features.

## Correction

- Restored the Unity package hotfix to the tagged source: the approved-object
  receipt is now guarded as Unity Editor-only code, with a regression test that
  prevents `UnityEditor` references from leaking into non-Editor compilation.
- Synchronized the package, runtime, localized About text, and desktop window
  title version at `1.6.1`.

Use VRCForge 1.6.1 instead of VRCForge 1.6.0. The v1.6.0 tag and its historical
release assets remain unchanged.

## Windows binary SHA-256

`VRCForge.exe`

`f4ec47b9d4921f8e0f28aa2ce84fd6fc3a5b698e047827aa25ce332b7d4c1ca4`

The Windows installers are not code-signed. Download only assets attached to
the official VRCForge Release and verify the published hashes.
