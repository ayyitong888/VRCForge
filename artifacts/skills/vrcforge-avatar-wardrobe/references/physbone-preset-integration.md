# PhysBone / Marshmallow preset integration

Use this reference only when the requested outfit or avatar workflow depends on author-provided PhysBone, Modular Avatar, NDMF, or Marshmallow PB setup. It records verified author guidance; it is not a universal component recipe.

## Verified author guidance

- Marshmallow PB's official documentation describes avatar presets under `marshmallow_PB/Setup/Preset`. The preset stores the avatar-relative breast/collider path, `breast_L_position`, `breast_L_scale`, and parameter values for blendshape endpoints. The right-side position is derived by inverting the left-side coordinate in the preset workflow.
- The official installation flow uses the `wataameya → MarshmallowPB` menu entry and a matching avatar preset. A prefab or already-generated `marshmallow_PB_Setup` may already contain the author components; do not add another copy merely because a generic component scan does not recognize it.
- The author's advanced guidance identifies generated objects such as `marshmallow_PB/PhysBone_L,R` and `marshmallow_PB/Inertia_L,R` for later tuning. The preset guide refers to both `marshmallow_PB_L` and `marshmallow_PB_R`; these are examples from the author's package, not paths to impose on another avatar.
- The unsupported-avatar branch explicitly names `For Unsupported Avatar/marshmallow_PB_L`; the preset documentation names `marshmallow_PB_L` and derives the right side from it. Current Milltina/author prefab evidence also contains hierarchy names `Breast_L` and `Breast_R`, but those are bone-name examples, not a universal path. The public author pages do not expose a C# component namespace, so this reference does not invent one.
- The owned Marshmallow PB 2.0.3 package was also read in the isolated Unity fixture. Its concrete component type is `wataameya.marshmallow_PB.ndmf.marshmallow_PB_MA`, with serialized references `_avatar`, `_Breast_L`, and `_Breast_R`. The author menu instantiated this component on `marshmallow_PB_Setup`, and the fixture read back the left/right object references and committed them. This is a product-specific compatibility fact, not a universal adapter namespace or bone-path rule.
- Modular Avatar's official documentation distinguishes a compatible outfit from a preset outfit. A preset may include Modular Avatar components such as Merge Armature, Blendshape Sync, Object Toggle, or Menu Item; end users should run the author's intended setup/build path and preserve those components when present.

## Required agent order

1. Read the relevant BOOTH/product page and local README or setup notes when asset installation or adaptation depends on them. Record title, author, target model/version, dependencies, and whether the delivered prefab already includes setup components.
2. Read the current target's actual hierarchy and component identities. Treat names such as `marshmallow_PB`, `_avatar`, `Breast_L`, and `Breast_R` as search clues only; do not hard-code bone paths or build an adapter map.
3. Run the author's normal initialization or preset selection first, then read back the generated objects and PhysBone fields. Do not fine-tune values before initialization, because the author's Inspector can copy the selected preset when its index/previous-index state differs and overwrite PB fields.
4. If the initialized result is mismatched, preview only the smallest author-supported correction using the actual resolved paths and component identities. Preserve vendor PhysBones, colliders, Modular Avatar components, skeletons, and animation references unless the author documentation and approved target explicitly require a change.
5. Treat the author menu-created component, serialized references, NDMF build-time wiring, runtime observation, and visual acceptance as separate evidence. A successful setup tool or static readback does not prove NDMF build/runtime behavior; the isolated fixture only proves component creation and serialized reference roundtrip.

## Explicit non-rules

- Do not require every product to receive a manually added component; author prefabs can legally contain the required component already. 不要添加另一个副本来替代实际组件核对。
- Do not treat a generic missing-name scan as proof that a component is absent.
- Do not apply a fixed `_avatar`, `Breast_L`, or `Breast_R` path to an unrelated avatar; `_avatar` is only a serialized field clue when returned by the actual component/prefab.
- Do not delete or replace author PhysBones, colliders, presets, or Modular Avatar components to make a scan match a template.

## Sources read

- Marshmallow PB author documentation: https://wataame89.github.io/documents-marshmallowPB/en/addition/
- Marshmallow PB author installation: https://wataame89.github.io/documents-wataameya/en/marshmallowPB/howtouse/install/
- Marshmallow PB author troubleshooting: https://wataame89.github.io/documents-marshmallowPB/en/trouble/
- Modular Avatar official outfit creator guidance: https://modular-avatar.nadena.dev/docs/distributing-prefabs/for-outfit-creators
- Modular Avatar official Blendshape Sync reference: https://modular-avatar.nadena.dev/docs/reference/blendshape-sync

## Local owned-package evidence

- `local-review/marshmallow-generic-control-20261027/result.json`
- package SHA-256: `4A0AB2945F54312B2DAB47F1736CCD4407B85DCA8A5706C47E241E13DF40BF0C`
- isolated menu result: `GameObject/wataameya/MarshmallowPB` created `marshmallow_PB_Setup` with component type `wataameya.marshmallow_PB.ndmf.marshmallow_PB_MA`; `_Breast_L` and `_Breast_R` persisted with hierarchy identities.
