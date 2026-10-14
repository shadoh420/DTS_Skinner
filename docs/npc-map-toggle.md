# NPCs on Maps pages

Every Maps page has an **NPCs** checkbox. It defaults on, remembers the choice per viewer in
`localStorage` (`skinner.maps.<game>.npcs`, with `t2` for `/maps/`), and shows the loaded map's
NPC count. With no supported NPCs it is disabled and says **No NPCs on this map**; switching
through an empty map does not reset the saved choice. Storage being unavailable is harmless.

Doom 3 and Quake 4 work with existing imports. Their surfaces name their owning entity;
`static/doom3-maps/npcs.js` classifies its classname/model declaration, including curated map
aliases. The count is unique rendered entities, not surfaces, material passes or currently
visible actors. The toggle combines with portal/frustum culling on every frame and sky pass.
T1, Unreal/UT/Na Pali, SiN, Daikatana and Anachronox exports now carry NPC tags; older packs
need `--replace`. E.Y.E., Tribes: Vengeance, Tribes: Ascend and Nosgoth already export tags.
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

Tribes: Vengeance uses serialized class ancestry: `Character` descendants and functional
`Turret` descendants are NPCs. Its `Character -> Ragdoll -> Rook -> Pawn` chain does **not**
mean the character is a corpse. Vehicles, stations, generators, sensors, pickups and flags
remain visible. Hidden controls and talking heads are omitted and listed by the importer.

Step 2 tagging follows the existing import visibility policy; it does not add missing models
or execute scripts. Each rendered actor keeps its entity ID and classname across material groups.

| Importer | NPC decision |
|---|---|
| T1 | RPG TownBot marker previews are `staticshape` objects; keep that class, their `*TownBot` datablock, and a stable scene entity index. Also tag placed `Player`/`AIPlayer` objects that the existing shape resolver draws. Shared model materials stay visible; toggle individual meshes. Missing-model boxes follow the preference but do not count. |
| Unreal / UT / Na Pali | Preserve `Pawn` ancestry before batching, with separate groups per actor ID. `Vehicle`/`Carcass` ancestry is excluded. Keep the existing hidden-actor filter; runtime bots and creature factories are not spawned. DX's NPC exclusion is unchanged. |
| SiN / Wages / CTF | Use the DEF server spawn class and placed monster/worker/animal/boss classes (also for raw SBM). Functional turrets count; driver-operated turrets and vehicles do not. Explicit dead/mauled/gib models remain scenery. Script models with actor DEFs count; delayed spawns and startup-hidden models remain omitted. |
| Daikatana | Tag the existing `e_seagull`, `e_goldfish`, `e_greyfish`/`e_grayfish`, `e_guppy`, `e_guppy2` models. Monsters remain outside this importer's current scope. |
| Anachronox | Tag entity.dat types `char`, `charfly`, `playerchar`; retain hidden/triggered/conditional-spawn exclusions. Tags apply on first import when no pack exists. |

SiN helicopter review: `Monster_HelicopterBoss` uses `models/lamprey_man.def`, which declares
`server classname vehicle` and `undrivable`; the `nuke.scr` helicopter movement is scripted.
It remains visible with ordinary Lamprey vehicles. `models/reconah.def` declares `actor` and
describes a Reconah warrior, so it counts. Helicopter weapons/rotors and gibs do not count.

Stock T1 training missions `2_Weapons`, `4_Commander_TargetLaser`, `5_CTF`, `6_Towers`,
`7_Retrieval`, `8_Destroy` contain AI marker groups; the exporter skips markers and does not
run `base/scripts/ai.cs` (`AI::spawn`). Their scripted bots are not drawn or counted by this step.

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

Step 2: `python -m unittest discover -s tests -p test_npc_step2.py` and
`node tests/test_npc_step2.js`. Synthetic scenes test same-material scenery/NPC separation,
multiple groups per actor, distinct actors sharing a model, preserved audit fields, exclusions,
DX compatibility, and the actual shared/T1 viewer hide paths using vendored Three objects.

Read-only retail inputs, scratch outputs only (`build/npc-step2/samples`, 2026-10-13):

| Game | Sample unique rendered NPC counts |
|---|---|
| T1, Modern Tribes V30 RPG | rpgmap1 **15**, rpgmap5 **27** |
| Unreal / Na Pali | NyLeve **52**, Dig **46**, UPak/Glathriel2 **52** |
| UT | DM-Deck16][ **0**, CityIntro **0** (runtime/scripted bots are not placed visible Pawns) |
| SiN / Wages / CTF | bank **48**, abandon **41**; Wages nuke **20**; CTF ctf_brafish **1**, ctf_dig **0** |
| Daikatana | e2m1a **3**, e2m1c **2** |
| Anachronox | baltown **42**, bricksc **13** |

The older `C:/Dynamix/TribesRPG/RPG/missions` samples omitted TownBots because their datablocks
were unavailable to the current resolver. Use the complete Modern V30 RPG scripts beside its
missions. No real-library re-import or browser acceptance was performed for these counts.

## Re-import handoff

Run from the checkout containing step 2, under the host's heavy-job lock. These commands were
prepared, **not executed** against the real library:

```powershell
$py = 'C:\tmp\DTS_Skinner\.venv\Scripts\python.exe'
$lib = Join-Path $env:LOCALAPPDATA 'DTS-Skinner\local-data'
$t1 = 'C:\Users\c\Downloads\Modern Tribes V30'
$steam = 'C:\Program Files (x86)\Steam\steamapps\common'
& $py tools/import_t1_map.py --game-base $t1 --mission "$t1\base\missions" --mission "$t1\Mods\RPG\MISSIONS" --output "$lib\t1-maps" --replace
& $py tools/import_unreal_map.py --install C:\Unreal --game unreal --output "$lib\unreal-maps\unreal" --replace
& $py tools/import_unreal_map.py --install C:\UnrealTournament --game ut --output "$lib\unreal-maps\ut" --replace
& $py tools/import_sin_map.py --install "$steam\SiN 1" --game all --output "$lib\sin-maps" --replace
& $py tools/import_daikatana_map.py --install "$steam\Daikatana" --output "$lib\daikatana-maps" --replace
& $py tools/import_anachronox_map.py --install "$steam\Anachronox" --output "$lib\anachronox-maps" --replace
```

Unreal includes the install's Na Pali maps. T1's command covers Modern V30 base + RPG only;
repeat with original `--mission` sources for any other custom packs. The sandbox cannot read
the existing T1 map library to reconstruct that source list. Do not re-import DX for this step.
