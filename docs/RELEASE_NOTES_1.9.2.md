# VRCForge 1.9.2

This release improves tool discovery, result navigation, execution identity, and verified Unity edits.

## Agent tools and results

- Keep tool groups stable within a session, show valid groups when a selection is invalid, and find exact leaf tools with keyword searches.
- Match complete keywords, handle long searches with an explicit truncation notice, and reuse tool definitions already loaded in the session.
- Keep Unity execution-target binding available without a separate discovery step. Public target references remain scoped to the current project and session.
- Return complete small scan results and bounded write-verification summaries directly; retain large results for explicit reading.
- Read material pages from one retained scan with freshness checks instead of repeating the source scan for every page.
- Calculate continuation hints from rows actually delivered to the model. If the first page was hidden, reading starts at zero rather than skipping unseen rows.
- Record per-request tools, fixed audit phases, and input, cached-input, output, and reasoning usage in the request ledger.

## Unity and recovery

- Return authoritative FX state before/after values and require the original speed and state digest when applying an edit.
- Capture the dependencies actually used by material inventory reads, avoiding unrelated component data that could invalidate the scan.
- Retry a blocked local history-file replacement within the existing bounded policy without replaying a Unity tool or provider request.

Identity checks, preview, approval, checkpoints, validation, and rollback requirements remain in place.

## Validation and known limitations

- A fresh supported Unity project with the VRChat Avatar SDK compiled and loaded Core 1.9.2 with zero compilation errors.
- The material benchmark completed a 76-row index and the exact target material details. The FX benchmark completed the approved speed change and persisted readback.
- Two native-continuation regression fixtures have an outdated mock method signature and fail before reaching their behavior assertions. Replaying the unchanged assertions with an in-memory signature adapter passed both cases. The checked-in fixtures remain a test-maintenance limitation; these failures did not demonstrate a runtime continuation defect.
- One existing owner-surface structural assertion remains a baseline failure. The targeted result-reader suite introduced no additional failures; no assertions were weakened or skipped.
- Fresh imports can log notices that non-empty or modified directories were preserved by legacy cleanup. The tested import compiled successfully; preserved files are not removed automatically.

## Upgrade

Use the App and `VRCForge.unitypackage` from this same release. Back up the Unity project before importing the matching Core and wait for compilation to finish before reconnecting the App.

The release includes an Offline Installer, a Web Installer, a Windows x64 ZIP, the Unity package, and a hash manifest. Windows binaries are not code-signed.
