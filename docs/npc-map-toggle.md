# NPCs on Maps pages

Every Maps page has an **NPCs** checkbox. It defaults on, remembers the choice per viewer in
`localStorage` (`skinner.maps.<game>.npcs`, with `t2` for `/maps/`), and shows the loaded map's
NPC count. With no supported NPCs it is disabled and says **No NPCs on this map**; switching
through an empty map does not reset the saved choice. Storage being unavailable is harmless.

Doom 3 and Quake 4 work with existing imports. Their surfaces name their owning entity;
`static/doom3-maps/npcs.js` classifies its classname/model declaration, including curated map
aliases. The count is unique rendered entities, not surfaces, material passes or currently
visible actors. The toggle combines with portal/frustum culling on every frame and sky pass.
Other pages show the disabled control until their adapter supplies NPC data.

## Scope

- Include monsters, characters, cinematic player doubles, actor turrets/sentries and tank-like
  monsters. Hidden/triggered actors and spawner previews already present in an export count too.
- Leave vehicles, scenery turrets, corpses/ragdolls, gibs, props, pickups and weapons visible.
  A monster that resembles a tank is an actor; `vehicle_*` is excluded. A ragdoll model wins over
  a character-looking classname. Wounded/cinematic characters remain actors.
- This is a visibility option: it does not run AI, spawn missing characters, change the imported
  pose, or infer later script state. Old exports lack inheritance, so unknown custom aliases may
  need another explicit rule. Do not infer NPCs from texture names or arbitrary model substrings.

## Contract for importers and new viewers

Export `npc: true` on each NPC **object or draw group**, retaining `classname` and a stable
`entity` ID for audit. All draw groups/attachments belonging to one actor share that ID and tag.
Keep NPC and non-NPC geometry in separate draw groups even when materials match. `npc: false`
explicitly means non-NPC; missing tags on older packs mean unclassified, not proof of no NPCs.
Count unique drawable entity IDs. Do not count control entities or skipped/unresolved models.

```json
{"entity": 42, "classname": "npc_example", "npc": true, "start": 120, "count": 36}
```

The app injects `static/npc-toggle.js` before viewers initialize. A viewer reads
`window.skinnerNPCToggle.enabled`, assigns `onChange = enabled => ...`, and calls `setCount(n)`
after loading. Reset the count to zero when unloading or failing to load. Never clear the saved
preference for an empty map. The helper mounts one control in the header, including T2's delayed
React header; checkbox keys do not move the camera.

For E.Y.E. and later Bloodlines/Unreal adapters, emit these tags during import and hide only tagged
groups in `static/unreal-maps/viewer.js`. Preserve entity identity before material batching. Do not
reuse Doom classname rules for other games. A viewer that rewrites visibility every frame must
combine the toggle with its other visibility conditions. Hiding does not free loaded GPU assets.

## Focused checks

`node tests/test_npc_toggle.js`, `node tests/test_doom3_npcs.js`, and
`python -m unittest tests.test_npc_maps` use synthetic data/temporary directories. Browser review:
toggle Doom/Q4 actors while moving and changing viewpoints, reload an OFF preference, check that
the games remember independently, and visit a zero-NPC map and an unimplemented Maps page.
