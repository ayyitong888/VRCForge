# VRCForge Compatibility Matrix

This matrix is the public compatibility checklist for the corrective 1.6.2
stable release and future stable refreshes. Version 1.6.2 replaces 1.6.0 and
supersedes the unpublished v1.6.1 tag. It
does not claim that every avatar, outfit, or shader stack is supported. It
records the components VRCForge must detect, report, and gate before stable
release work can be accepted or refreshed.

The 1.6.2 corrective release carries forward the reviewed 1.6.x product
behavior and closes the Unity-package/source evidence gap. Automated source,
package-construction and no-regression gates do not replace the release-paired
manual checks: the final `VRCForge.unitypackage` still requires a disposable
fresh-project Import All, Editor and Player compilation, VRChat Build & Test,
and the official upgrade-path review. Any exact-version probe not performed on
the final artifact remains unverified and must not be represented as passed
release evidence.

## Stable Compatibility Targets

| Area | Current target | Release evidence | Stable expectation |
| --- | --- | --- | --- |
| Windows | Windows x64 installer and portable payload | Single clean `v1.6.2` build plus manifest/hash checks; exact-version clean-environment install/upgrade/uninstall results must be recorded when run | Use the release notes for the tested installation, migration, uninstall, and portable-launch boundaries |
| Unity | Unity 2022.3 LTS VRChat avatar projects | Static/package-construction gates plus release-paired fresh-project Import All, Editor/Player compile, VRChat Build & Test, and upgrade review | Doctor reports Unity version and project validity clearly; unrun final-artifact checks remain unverified |
| VRCForge MCP 2.0 Core | Self-contained Unity package, MCP 2.0 (`2026-07-28`), fixed 82-tool contract in v1.7.10 | Clean import, direct App connection, `tools/list`, approval/checkpoint/write/readback/restore/reconnect evidence | No external Unity MCP runtime is required; the Unity Core remains `2026-07-28` only |
| External-Agent MCP edge | App-side MCP 2026 preferred; pinned supported standard MCP 1.x selected only from the first valid initialize frame | Profile selection/freeze and client-compatibility tests; any unrun fresh external-Agent 1.6.2 smoke remains unverified | No mid-connection switch, silent catalogue downgrade, or direct external Unity write |
| VRChat SDK | VRChat SDK3 Avatar package | Validation report and Build/Test readiness detect SDK state | Missing SDK is a clear blocker, not a generic scan failure |
| Modular Avatar | Optional package, read/write only through VRCForge approval paths | MA scan and rollback coverage audit metadata exist | MA-heavy writes require checkpoint, validation, and rollback proof |
| NDMF | Optional dependency for optimizer/plugin ecosystems | Rollback coverage audit records NDMF package baseline metadata | NDMF generated residue is detected or explicitly marked not present |
| VRCFury | Read-only stable; risky writes experimental | Compatibility report and blocked request surfaces exist | VRCFury Parameter Compressor remains Advanced/Experimental until proof |
| AAO | Planning and guarded apply-request surface; dedicated writer and live proof remain deferred | Planner/request-guard evidence only | Preview or manual proof is required; do not treat hidden-body-cut or PhysBone cleanup as a one-click stable write |
| LAC | Conservative/balanced delegated apply | 0.8 proof plus packaged request guard evidence | Stable profile names remain conservative and one-step |
| TTT | User-confirmed AtlasTexture material group | TTT rollback proof with explicit material path | No automatic material-group guessing as a stable default |
| Meshia | Low-risk explicit renderer only | Low-risk accessory/clothing renderer proof with screenshots | Aggressive/body/face simplification stays experimental |
| MA2BT-Pro | MA-heavy responsive layer conversion request | 0.8 proof and skipped-reason diagnostics | Skipped layers are explainable before conversion |
| Thry tools | Read-only avatar performance report | Read-only bridge and performance-tool diagnostics | Performance data is advisory and never a direct write |
| lilToon | First-class shader adapter | Carry-forward semantic material proof and package-preflight rules | Safe semantic properties only; raw property mutation stays blocked |
| Poiyomi | First-class shader adapter | Poiyomi package/shader/tuning rollback proof | Package install/tuning/rollback remains checkpointed |
| Generic semantic shader | Conservative fallback | Generic semantic fallback exists | Only safe common properties; unsupported shader report otherwise |
| Avatar Encryption addon | 1.0.1 connector preview for lilToon and Poiyomi first | Research/scan/plan/preview plus private-addon connector request interfaces; Lite/Standard/Paranoid profiles; Standard is the default; public repo contains no encryption implementation | Windows PC-only; Quest/Android is blocked for this feature; private addon module is required for execution |
| Face/shader adjustment timeline | 1.0.1 source-line A/B checkpoints for high-frequency tuning | API and desktop Checkpoints UI support CRUD, overwrite, A/B selection, preview, and restore-approval apply | Applies must stay on the normal checkpoint/approval/rollback chain |

## Avatar Compatibility Aliases

VRCForge ships a builtin alias table that maps common base-avatar names and
their nicknames so outfit/compatibility detection recognizes them. The builtin
defaults are not exhaustive. To recognize an avatar that is not in the default
list, developers may optionally set `VRCFORGE_AVATAR_ALIAS_PATH` to a JSON file
that adds or extends aliases. The file may be either flat
(`{ "canonicalName": ["alias", ...] }`) or
wrapped (`{ "avatars": { "canonicalName": ["alias", ...] } }`). The override is
not required for normal package import or App connection. It is merged on top
of the builtin defaults and cannot remove a builtin entry; a missing or
malformed file is ignored.

## Known Conflicts

| Conflict | Expected behavior |
| --- | --- |
| Already-installed shader support package appears inside an imported outfit package | Skip or report the dependency instead of blindly importing duplicate support packages |
| Outfit material imports with a missing or InternalError shader (magenta / pink render) | Post-import validation raises a blocking `Error` listing the magenta materials and renderers; import the required shader support package before the outfit prefab, then re-import |
| Unity compile errors before apply | Block write-heavy workflows until compile status is understood |
| Missing VRChat SDK performance type | Report a degraded validation source such as `missing_sdk_type` instead of hiding the reason |
| External MCP client requests direct executor targets | Keep direct apply hidden; require named request tools and VRCForge approval |
| Client connects directly to the Unity Core with a protocol other than `2026-07-28` | Reject it before tool dispatch with an update-client error; do not negotiate or fall back |
| Standard MCP client initializes through the App-side external-Agent edge | Select one pinned supported MCP 1.x profile from the first valid frame and freeze it for that connection; otherwise fail closed |
| Known third-party Unity MCP package is present | Log a conflict warning at Unity startup; do not remove the package automatically |
| Non-admin installer session | Record a blocked installer smoke artifact; rerun from Administrator shell or VM for full install/uninstall evidence |

## Known Safe Profiles (conservative planning labels, not guarantees)

| Profile | Stable meaning |
| --- | --- |
| PC Conservative | Prefer reversible, low-risk changes and one optimizer step at a time |
| PC Medium | Allow more optimization only after validation deltas are reviewed |
| Quest Medium | Treat as a planning target unless project-specific visual and upload gates pass |
| Event Light | Prefer lower-risk reductions and clear skipped/rejected items |
| PC Upload Pass | Focus on hard upload blockers before performance-rank polish |
| Quest Upload Pass | Focus on Android download/uncompressed size and shader/material constraints |

## Privacy Boundary

This table describes normal product-controlled paths. It is not a guarantee
against content the user deliberately includes in a prompt, hands to a
third-party client, accesses through raw Unity tooling, or exposes through an
operating-system compromise.

| Data category | Desktop UI | Support bundle | Model context | External agent | .vsk export |
| --- | --- | --- | --- | --- | --- |
| API key | Local config only | No | No | No | No |
| Gateway token | Local config only | No | No | No plaintext copied config | No |
| Full local path | Visible by user action | Redacted where possible | Avoid by default | Redacted where possible | No private absolute paths |
| Unity logs | User controlled | Redacted excerpt | Opt-in only | Redacted where possible | No |
| Screenshots | User controlled | Opt-in | Opt-in only | Opt-in only | No |
| FBX, textures, materials | Local only | No paid asset payloads | No | No | No |
| Booth package contents | Local only | No paid asset payloads | No | No | No |
| Validation metadata | Yes | Yes, redacted | Redacted summary | Redacted summary | Schema and variables only |

## Stable Evidence Rule

Before a future stable release or stable refresh is published, every stable row
above needs either fresh evidence in the Golden Path Matrix / proof matrix or
an explicit not-run/blocked reason in release evidence. Experimental rows must
stay labeled as Experimental or Advanced and must not become default one-click
behavior.
