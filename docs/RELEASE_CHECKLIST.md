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
* [ ] Do not remove GPL-3.0 notices from redistributed or modified versions.
