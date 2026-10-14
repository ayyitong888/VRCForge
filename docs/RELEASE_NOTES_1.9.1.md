# VRCForge 1.9.1

This release improves desktop chat-history recovery, provider safety, and agent-turn feedback.

The release uses the VRCForge-owned MCP 2.0 (`2026-07-28`) Core and bridge.

## Chat history

- Display sent image attachments in their messages, including thumbnails, full-size previews, and restoration after refresh.
- Keep public progress replies interleaved with tool calls. Expand the elapsed-time row to inspect the process, or collapse it to keep the model's final answer visible.
- Preserve message phases, tool records, and final-answer boundaries when saving and reopening chats. Show an explicit incomplete state when no final answer was returned.
- Preserve local edits when the same chat changes concurrently in another storage writer.
- Keep the conflict and recovery notice visible instead of replacing it with a generic save error.
- Merge changes to different chats without overwriting unrelated conversations.

## Agent turns and provider failures

- Fix switching a session to a saved provider account and an explicitly selected model failing with an empty endpoint.
- Fix approval completion returning a conflict when handing control back for manual adjustment; stop resources owned by the original task without cancelling unrelated turns.
- Prepare optional provider SDK resources before the planner worker starts, while keeping missing or broken optional resources visible in diagnostics.
- Classify plain HTTP 503 responses as request failures with their server status and apply the existing bounded retry policy without replaying a stream that has already started.
- Keep commentary and progress entries ordered and show the correct elapsed duration when a turn supplies one.
- Keep provider configuration controls locked while an agent turn is active, then restore them when the turn reaches a terminal state.
- Show the final terminal phase and failure classification in the turn status so a completed, cancelled, or failed run remains distinguishable after refresh.

## Upgrade

Use the App and `VRCForge.unitypackage` from the same release. Back up the Unity
project before importing the matching Core, wait for compilation to finish, then
refresh the App connection.

The release includes an Offline Installer, a Web Installer, a Windows x64 ZIP,
the Unity package and a hash manifest. Windows binaries are not code-signed.
