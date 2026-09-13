# Resource storage v2 — breaking update

The development update replaces the MCP resource JSON index with a SQLite
database. Publishing a resource now writes the changed records in one database
transaction instead of rewriting all captured history.

This is a **breaking change to local captured-resource history and references**:

- The old `mcp-resources/registry.json` file is preserved untouched as offline
  evidence. The application does not read or import it into the new store.
- Newly captured resources use `registry-v2.sqlite3`. Immutable resource URIs carry
  both a revision and a store identifier. Clients must use the returned URI
  verbatim, including its query parameters.
- Old immutable URIs cannot resolve to newly captured records. Recapture the
  needed state with a read tool and bind the current target again. Plans or
  approvals that depend on retired resource references must be prepared again;
  an old approval does not authorize newly captured evidence.
- Resource generation and revision counters start again in the new store.
  Revisions are meaningful only within their store identifier.
- Saved Unity assets, scenes, materials, and animations are unaffected.

There is no runtime reader for the old JSON format and no automatic rollback or
deletion of historical evidence. Retain the old file if its captured history is
needed for offline investigation. This document describes a development change,
not a published release.
