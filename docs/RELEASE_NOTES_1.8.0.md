# VRCForge 1.8.0

VRCForge 1.8.0 updates the shared Agent tools, exact-target editing and
recovery workflows on Windows x64. These notes describe implemented changes;
they do not claim that every possible avatar workflow has been validated.

The shared boundary targets MCP 2.0 (`2026-07-28`). Windows installers are
not code-signed; Windows may show an unverified-publisher warning. This does
not constitute permission to bypass a failed integrity or pairing check.

## Shared Agent capabilities

- Internal and external Agents use the same canonical Tool definitions and
  handlers. The existing lazy Tool Tree groups capabilities into six compact
  roots; legacy names resolve to canonical names instead of adding duplicates.
- Tool discovery carries usage, risk, approval, identity, result and provenance
  contracts. Lazy loading uses catalog generations and list-change notices.
  Planning exposure does not make write tools callable.
- Tools, Resources and Skill-backed Prompts share identity and provenance
  boundaries. Full workflow acceptance, including wardrobe dissolve animation,
  remains a separate real-Agent test and is not implied by protocol tests.

## Exact-target writes and recovery

- ExecutionTarget binds project, Editor process, Core instance, scene and
  applicable object identities. Hierarchy display paths are not write identity.
- Supervised writes carry one operation identifier through approval,
  checkpoint, apply and receipt. Unverified completion stays in recovery;
  it is not reported as a proven applied change.
- Renderer material-slot assignment changes one exact slot with a saved
  readback. Rollback only reports restoration after the saved scene and
  original slot state have been verified.
- Scene capture supports an explicit free camera. Visual acceptance still
  requires inspecting the resulting image, not just a successful Tool return.

## Tool correctness and diagnostics

- Avatar, component, animation and recovery selectors reject ambiguous or
  contradictory targets. Qualified paths are not silently reduced to leaf names.
  Parameter identity remains case-sensitive; parameter budgets count synced values.
- Scene, wardrobe, expression and material writers save only the relevant assets
  and scenes. Failed or pending operations preserve recovery information instead
  of reporting unverified completion.
- Material discovery supports bounded pages and exact IDs for subsequent reads.
  Generic shader capabilities are discovered from actual property types;
  unsupported operations and restricted properties remain explicitly rejected.
- External Agents can list directories, read text, find files and search text
  through the shared MCP handlers. These read tools do not expose arbitrary shell
  execution or source-file modification.
- External writes honor the selected confirmation mode and recheck permission
  changes before applying a prepared operation. Chat-storage repair uses its
  explicit supervised capability rather than a general permission bypass.
- Resource storage uses SQLite transactions for batches of related results.
  Pending observations, cancellations and capture retries retain their actual
  status; diagnostics expose the running Core and tool catalogue.

## Desktop and storage

- Selecting a project opens that project's new conversation; selecting a
  conversation child opens history, for both general and Unity projects.
- Manual checkpoint deletion is independent of the automatic keep-latest
  policy, with confirmation and protection for an active write's recovery point.
- Automatic checkpoints preserve the user's Git staging area and commit history.
  A clean scope can reference its existing commit; changed scopes use the existing
  archive checkpoint mechanism. Restore validates the selected record and contents
  and preserves recovery evidence when compensation is needed.
- Checkpoint archive usage above the configured budget triggers a warning;
  the warning does not silently delete user archives.
- Settings panels load independently of slow Agent notes, deduplicate in-flight
  requests and guard against stale responses while switching contexts.

## Verification boundary

Source regression, isolated Unity compilation, packaged smoke tests and real
desktop/Agent acceptance are recorded separately. Installer upgrade, desktop
latency and full avatar workflow claims require evidence from the exact package
being delivered. No public release or tag is created by these notes. Lightweight checkpoint storage
and a new plugin extension architecture are not included in this release.
