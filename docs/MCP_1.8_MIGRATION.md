# MCP client migration for VRCForge 1.8

This describes the 1.8 candidate contract. A version string or successful
discovery is not proof that a particular Unity project is ready for writes.
Check the running Core and exact execution target before beginning a workflow.

## Discovery and tool names

VRCForge exposes native Tools, Resources and Prompts. The STDIO adapter supports
the standard MCP `2025-11-25` profile and VRCForge's `2026-07-28` profile. Preserve
the negotiated profile for the connection. The HTTP endpoint advertises its own
loading instructions; do not assume STDIO activation tools also exist there.

Keep the existing lazy Tool Tree. Follow each block's advertised `loadWith`
instruction. When `notifications/tools/list_changed` arrives, request a fresh
`tools/list` and replace the previous tool snapshot. Check `catalogGeneration`;
a successful block-load response alone does not prove the host refreshed its
available tools. Unloading follows the same notification and re-list sequence.

Use the `name` advertised in `tools/list` when calling a tool. `canonicalName`
is its stable logical identity. For example, `vrcforge_scene_save` is the wire
name for `vrcforge.scene.save`; `vrcforge_save_current_scene` is an accepted
legacy alias, not a second independently exposed tool. Responses report
`canonicalToolName` and `legacyAliasUsed`.

Planning exposes read tools and workflow guidance. Loading a block or selecting
a Prompt never authorizes a write. Execution exposure, approvals, checkpoints
and the tool's declared preconditions remain authoritative.

## Bind context before using a workflow

1. Discover the project and execution-target candidates through the public
   bootstrap tools. Bind one exact candidate with
   `vrcforge_bind_execution_target` and retain the returned `executionTarget`.
2. Read the returned `resources.identityLockUri` and
   `resources.operationReceiptUri`. Keep their explicit revisions. A catalog
   Resource describes tools; it cannot substitute for captured session state.
3. Select a Skill from `prompts/list`, then call `prompts/get` with the identity
   URI and a matching captured context URI. A missing, unbound, stale or
   mismatched context must not be treated as ready for execution.
4. Follow the retrieved instructions. Supply the exact target wherever the
   tool schema requires it. Display paths and same-named objects are not stable
   identities. Rebind after identity changes; do not guess a replacement.

The current identity Resource must belong to the current Gateway binding.
Matching a pair of arbitrary historical Resources is insufficient. Old immutable
Resource revisions remain readable as historical evidence, while a Prompt's
current identity lock must use its latest revision. `resources/read` retrieves
captured data and never silently scans or modifies Unity.

## Prompt provenance and support files

`prompts/list` returns discovery metadata without reading support-file bodies.
Its `contentHash` covers Prompt guidance and support-file declarations; the
`hashScope` field states that boundary. It is not a hash of unread file bytes.

`prompts/get` returns the declared support files through the existing bounded
UTF-8 loader. Each entry includes its path, exact text and SHA-256 of its UTF-8
bytes. The returned provenance also includes `supportContentHash`, binding that
set of files. Keep the complete provenance from `prompts/get` when supplying
`promptSkillProvenance` to a Tool. Do not reconstruct it from list metadata or
drop the support-content digest. Changed or missing support content invalidates
the prior provenance and requires another `prompts/get`.

JSON-array Prompt arguments, including `referenceSources` and
`acceptanceCriteria`, may be supplied as serialized JSON string arrays. Scope
and protected-state arguments are JSON objects. Incorrect argument types are
rejected instead of being converted into apparent facts.

## Results, handoff and acceptance

Keep the `operationId`, exact target, Resource handles and terminal-state fields
together. Distinguish `operationStatus`, `commitState`, `persistenceState` and
`readbackState`; HTTP success or an accepted asynchronous job is not a saved,
verified change. Read the operation receipt independently. A missing before,
after or diff handle means that evidence is unavailable, not that it may be
inferred from the requested input.

A user-adjustment handoff ends the foreground Agent turn and its tool work.
Resume only after new user instruction and a fresh read of the relevant state.
Gesture Manager evidence must exercise the actual menu and parameter paths and
remain distinct from VRChat in-game, Build & Test and upload acceptance.

For the protocol's message formats, see the official
[Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
and [Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)
specifications. VRCForge's identity, provenance and receipt requirements are
application contracts layered on those messages.
