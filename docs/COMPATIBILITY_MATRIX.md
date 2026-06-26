# VRCForge Compatibility Matrix

This matrix is the public compatibility checklist for the 1.0.0 public stable
release, the 1.0.1 Avatar Encryption / Anti-Rip addon preview, and future
stable refreshes. It
does not claim that every avatar, outfit, or shader stack is supported. It
records the components VRCForge must detect, report, and gate before stable
release work can be accepted or refreshed.

## Stable Compatibility Targets

| Area | Current target | Release evidence | 1.0 stable expectation |
| --- | --- | --- | --- |
| Windows | Windows x64 installer and portable payload | Strict x64 release build and packaged smokes passed | Install, update, uninstall, and portable launch remain boring |
| Unity | Unity 2022.3 LTS VRChat avatar projects | Golden Path Matrix and Unity-package import smokes use Unity project roots | Doctor reports Unity version and project validity clearly |
| VRChat SDK | VRChat SDK3 Avatar package | Validation report and Build/Test readiness detect SDK state | Missing SDK is a clear blocker, not a generic scan failure |
| Modular Avatar | Optional package, read/write only through VRCForge approval paths | MA scan and rollback coverage audit metadata exist | MA-heavy writes require checkpoint, validation, and rollback proof |
| NDMF | Optional dependency for optimizer/plugin ecosystems | Rollback coverage audit records NDMF package baseline metadata | NDMF generated residue is detected or explicitly marked not present |
| VRCFury | Read-only stable; risky writes experimental | Compatibility report and blocked request surfaces exist | VRCFury Parameter Compressor remains Advanced/Experimental until proof |
| AAO | Conservative Trace And Optimize delegated apply | 0.8 proof plus 0.9 request guard evidence | Hidden body cut and PhysBone cleanup require manual/visual proof |
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
list, set `VRCFORGE_AVATAR_ALIAS_PATH` to a JSON file that adds or extends
aliases. The file may be either flat (`{ "canonicalName": ["alias", ...] }`) or
wrapped (`{ "avatars": { "canonicalName": ["alias", ...] } }`). The override is
merged on top of the builtin defaults, so it only adds coverage and never
removes it; a missing or malformed file is ignored without error.

## Known Conflicts

| Conflict | Expected behavior |
| --- | --- |
| Already-installed shader support package appears inside an imported outfit package | Skip or report the dependency instead of blindly importing duplicate support packages |
| Outfit material imports with a missing or InternalError shader (magenta / pink render) | Post-import validation raises a blocking `Error` listing the magenta materials and renderers; import the required shader support package before the outfit prefab, then re-import |
| Unity compile errors before apply | Block write-heavy workflows until compile status is understood |
| Missing VRChat SDK performance type | Report a degraded validation source such as `missing_sdk_type` instead of hiding the reason |
| External MCP client requests direct executor targets | Keep direct apply hidden; require named request tools and VRCForge approval |
| Non-admin installer session | Record a blocked installer smoke artifact; rerun from Administrator shell or VM for full install/uninstall evidence |

## Known Safe Profiles

| Profile | Stable meaning |
| --- | --- |
| PC Conservative | Prefer reversible, low-risk changes and one optimizer step at a time |
| PC Medium | Allow more optimization only after validation deltas are reviewed |
| Quest Medium | Treat as a planning target unless project-specific visual and upload gates pass |
| Event Light | Prefer lower-risk reductions and clear skipped/rejected items |
| PC Upload Pass | Focus on hard upload blockers before performance-rank polish |
| Quest Upload Pass | Focus on Android download/uncompressed size and shader/material constraints |

## Privacy Boundary

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

## 1.0 Evidence Rule

Before a future stable release or stable refresh is published, every stable row
above needs either fresh evidence in the Golden Path Matrix / proof matrix or
an explicit not-run/blocked reason in release evidence. Experimental rows must
stay labeled as Experimental or Advanced and must not become default one-click
behavior.
