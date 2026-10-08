# ChatGPT Integration Boundary

This is a provider-specific integration, not part of the shared agent loop.
Importing the package alone does not initialize OAuth or open a listener.

## Ownership

- `storage.py`: protected account persistence.
- `service.py`: login, callback lifetime, refresh, account catalog and logout.
- `routes.py`: App-authenticated HTTP registration and service shutdown.
- `adapter.py`: provider-specific Responses transport and message translation.
- `src/features/chatgpt-oauth/`: account UI and its private API client.
- `src-tauri/src/chatgpt_oauth.rs`: authenticated desktop bridge and browser opening.

The storage location, schema and refresh behavior are independent of these source
paths. Moving or hiding the UI must not clear credentials. Shared provider
configuration stores an account identifier, not OAuth tokens.

## Host Connections

- `dashboard_server.py`: register the authenticated router.
- `provider_model_catalog_service.py`: account-backed model discovery.
- `provider_test_integration_service.py`, `provider_vision_service.py`: transport selection.
- `vrchat_blendshape_agent.py`: provider defaults, protocol guards and transport selection.
- `provider_configuration_service.py`: provider-specific configuration contract.
- `src/components/settings/settings-workspace.tsx`: mount the account UI.
- `src/components/settings/provider-settings.tsx`: provider picker entries.
- `src/hooks/use-provider-settings.ts`, `src/lib/provider-ui.ts`: provider metadata and API-key distinction.
- `src-tauri/src/main.rs`: register the desktop module and its commands.
- `src/i18n/`: display strings only.

## Hiding or Removing Later

Hiding is a UI change: remove the picker entries and account-panel mount without
deleting saved accounts or changing an already selected provider silently.

Complete removal requires removing the host connections above as well as the
owned modules and their dedicated tests. First define what happens to an active
`chatgpt` configuration; do not silently substitute another provider. Credential
deletion or revocation is a separate explicit user operation.

This is not a hot-unload plugin: deleting the directory alone leaves imports
unresolved. No compatibility aliases or generic plugin framework are included.
Shared Responses parsing, native history, approvals and Unity code remain owned
by their existing modules and must not be deleted with this integration.

## Verification

Run `tests/test_chatgpt_*.py` with isolated profiles and synthetic credentials,
then `npm run build`. The tests cover service persistence and refresh, route
authentication, Responses transport, provider integration and the native loop.
Real account authorization is a separate acceptance step.
