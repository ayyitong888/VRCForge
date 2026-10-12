# VRCForge 1.9.0

Telegram direct chat and session controls are available in this release.

## Telegram

- Start a direct Telegram chat without choosing a Unity project first.
- Create, switch, and browse sessions across general chats and authorized projects.
- View session history and summaries, choose the current session provider/model, compact context, stop a run, steer an active run, and use the 16 Telegram commands.
- Receive progress updates, question and approval cards, previews, and compact results.
- Restrict Telegram access to the owner’s private chat and authorized project scopes.
- Configure a dedicated Telegram proxy without changing model-provider network settings.

## App and Unity

- The desktop App and Unity Core remain available while Telegram is connected.
- Use the setup guidance and tutorial in Settings for connection and feature configuration.
- Full Access ordinary operations do not require one confirmation per operation.
- Choose Core’s manual Chinese or English language explicitly, with guidance when required items are missing.

## Providers and accounts

- Prefer email when displaying account identity where the provider supplies it.
- Provider `auto` settings negotiate the transport protocol while preserving the user-selected model.

## Context compaction

- Telegram can compact the same durable conversation state as the desktop session controls.
- Conversations with attachments use the desktop compaction path until the Telegram attachment-preservation flow is available.

## Scope

- Keep this computer and VRCForge running to receive Telegram requests. Keep the selected Unity project open and connected for avatar editing.

## Upgrade

Use the App and `VRCForge.unitypackage` from the same release. Back up the Unity
project before importing the matching Core, wait for compilation to finish, then
refresh the App connection.

The release includes an Offline Installer, a Web Installer, a Windows x64 ZIP,
the Unity package and a hash manifest. Windows binaries are not code-signed.
