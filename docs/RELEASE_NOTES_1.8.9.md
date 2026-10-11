# VRCForge 1.8.9

Release candidate. Publication requires the release validation gates to pass.

## Sign-in and Connection Status

- Ordinary ChatGPT sign-in ignores a selected account that is no longer present, so a retry can start a fresh account login.
- Signing out removes the selected local account and clears its active provider connection. Other accounts remain available; previously signed-out local accounts can be removed from the account list.
- Allow five seconds of clock skew during identity-token validation, while retaining signature, issuer, audience, expiry and nonce checks. Show a specific safe validation error when sign-in fails.
- Distinguish internal model connections from authenticated external Agent sessions. Saved internal configuration stays yellow until its connection test succeeds; changing the tested configuration invalidates that result.
- Keep Unity connection readiness independent from model-provider connection status.
- Keep the ChatGPT model selector visible and reload the selected account's catalog when reopening settings. Group account and model actions so controls remain aligned at narrow widths.

## Unity Project Guidance

- Check the selected project's VRChat SDK and Unity Newtonsoft Json package metadata when Core cannot connect. Show the missing prerequisite before directing users to a menu that compilation errors may have prevented from loading.
- Recognize dependencies resolved through Unity's package lock file, so SDK-provided Newtonsoft is not incorrectly reported as missing.
- Preserve a working Core connection even when package metadata is unavailable. Unreadable metadata is reported as unknown.
- Importing into an empty Unity project now keeps a dependency setup menu available without compiling SDK-dependent tools. Install the VRChat Avatar SDK and its dependencies through VCC, ALCOM or vrc-get to enable the complete Core automatically. VRCForge does not install these dependencies itself.
- Require the Avatar SDK specifically: a project with only VRChat Base no longer passes the Avatar dependency check.

## Recovery

- Reject checkpoint archive restoration before writing project files if its required scene is unavailable.

## Protocol

App and Core use MCP 2.0 (`2026-07-28`) with newline JSON-RPC transport.

## Upgrade

Use the App and `VRCForge.unitypackage` from the same release. Back up the Unity
project before importing the matching Core, wait for compilation to finish, then
refresh the App connection. Check `release-manifest.json` against downloaded files.

The release includes an Offline Installer, a Web Installer, a Windows x64 ZIP,
the Unity package and the release manifest. Windows binaries are not code-signed.
