# NPCs on Maps pages

Every Maps page has an **NPCs** checkbox. It defaults on, remembers the choice per viewer in
`localStorage` (`skinner.maps.<game>.npcs`, with `t2` for `/maps/`), and shows the loaded map's
NPC count. With no supported NPCs it is disabled and says **No NPCs on this map**; switching
through an empty map does not reset the saved choice. Storage being unavailable is harmless.

Doom 3 and Quake 4 work with existing imports. Their surfaces name their owning entity;
`static/doom3-maps/npcs.js` classifies its classname/model declaration, including curated map
aliases. The count is unique rendered entities, not surfaces, material passes or currently
visible actors. The toggle combines with portal/frustum culling on every frame and sky pass.
T1, Unreal/UT/Na Pali, SiN, Daikatana, Anachronox, Deus Ex, Bloodlines, Quake and Quake II
exports carry NPC tags; older packs need `--replace`. E.Y.E., Tribes: Vengeance, Tribes: Ascend and Nosgoth already export tags.
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

Step 2 tagged existing imports without adding missing models or executing scripts. The table below
records that step's scope; step 3 additions follow below. Each actor keeps its ID across material groups.

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

## Step 2 re-import handoff

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

## Step 3: placed NPC models

| Game | Import and edge-case decision |
|---|---|
| Deus Ex | Admit Pawn/ScriptedPawn ancestry; resolve inherited Mesh/MultiSkins through the existing package owners. Use the model catalog's standing pose selector even when a Pawn names another animation. DeusExPlayer descendants are excluded (`excluded_players`); carcasses, pickups and decorations keep their existing path. Hidden actors remain omitted. |
| Bloodlines | Admit `npc_*` entities with explicit models, skin family and the MDL reader's first idle/reference pose. Entity animation requests remain in the audit. Remove the blanket character-model path veto: character props remain untagged scenery. Exclude `npc_vcamera`, `npc_maker`, `npc_template_maker`, `npc_enemyfinder`, `npc_bullseye`, and model-less NPC declarations. StartHidden/renderamt-zero filtering remains. |
| Quake | Curated spawn-class MDLs, embedded skin and standing/idle frame, first pose fallback. Normal SP excludes spawnflag 512; NOT_DEATHMATCH (2048) stays in SP. Threewave has no monsters. Armagon body/legs share one ID, body offset +64 Z. Rogue morph flags select their authored model; statue knights retain stone skin and count as actors. Decoys/unknown classes are audited, never guessed. |
| Quake II | Curated base/Xatrix/Rogue MD2s and variant skins; standing frames include the native wait/ambient/swim/search names. Medium SP excludes 512, retains 2048; CTF has no monsters. Tanks, bosses and functional turrets count. Jorg/rider and wall turret/base share IDs. Authored trigger-spawn actors are previews; no target_spawner offspring, summoned reinforcements or commander corpses are added. |
| Daikatana | AIData VSC/CSV resolves monster, Mikiko/Superfly and cinematic actor models and scale; prefer DKM amba/stand/idle sequences. Existing ambient creatures remain tagged, including sprite draw groups. Medium-skill exclusion remains; unknown classes/spawners do not become guessed models. |

Quake mappings were checked against the installed `progs.dat` spawn functions and models.
Quake II model/skin/attachment rules follow the [original game sources](https://github.com/id-Software/quake2-rerelease-dll/tree/main/original).
Model readers retain first-frame defaults for catalog callers. Bloodlines/Daikatana audit rows retain
model, skin and pose; Quake/Q2 also retain frame and part records. DX groups retain actor identity and
resolved textures. Failed/unresolved draws do not count.
All five pages use the existing shared viewer hide/count path; only Quake's material adapter changes,
to light indexed NPC skins evenly without sampling the brush light atlas.

Scratch exports, 2026-10-13 (`build/npc-step3/samples`), compared with read-only library files:

| Game/map | NPCs | Map growth, MiB | New referenced NPC textures, KiB |
|---|---:|---:|---:|
| DX 01_NYC_UNATCOIsland | 58 | 1.36 | 0 |
| DX 02_NYC_BatteryPark | 41 | 0.95 | 0 |
| Bloodlines sm_hub_1 | 31 | 5.11 | 0 |
| Bloodlines sm_pier_1 | 10 | 2.09 | 0 |
| Quake e1m1 | 23 | 0.28 | 31 |
| Quake e2m2 | 39 | 0.37 | 70 |
| Quake II base1 | 17 | 0.25 | 108 |
| Quake II base2 | 41 | 0.61 | 188 |
| Daikatana e1m1a | 14 | 0.42 | 266 |
| Daikatana e2m1a | 27 | 0.66 | 203 |

Map growth covers scene.json + geometry.bin + lightmap.png. Texture figures are referenced files absent
from the corresponding library texture directory; shared files can overlap between maps, so do not sum
them as library-wide growth. All ten samples have zero NPC model failures. Counts are authored previews,
not gameplay population totals: hidden/script-created inhabitants and later script state are absent.

Focused checks: `python -m unittest discover -s tests -p test_npc_step3.py`, the adjusted DX/Bloodlines/
step-2 tests, selected model-reader/scenery tests, `node tests/test_npc_step2.js`, and
`node tests/test_npc_toggle.js`. Tests use synthetic assets and plain tempfile. Local sandbox ACL
workarounds and sample exporters are confined to ignored build helpers.

Claude: re-import from the step-3 checkout under the host lock (prepared, not run here):

```powershell
$py = 'C:\tmp\DTS_Skinner\.venv\Scripts\python.exe'
$lib = Join-Path $env:LOCALAPPDATA 'DTS-Skinner\local-data'
$steam = 'C:\Program Files (x86)\Steam\steamapps\common'
& $py tools/import_unreal_map.py --install "$steam\Deus Ex" --game dx --output "$lib\dx-maps" --replace
& $py tools/import_vtmb_map.py --install 'C:\GOG Games\VtMB' --output "$lib\vtmb-maps" --replace
& $py tools/import_quake_map.py --install "$steam\Quake" --game all --output "$lib\quake-maps" --replace
& $py tools/import_quake2_map.py --install "$steam\Quake 2" --game all --output "$lib\quake2-maps" --replace
& $py tools/import_daikatana_map.py --install "$steam\Daikatana" --output "$lib\daikatana-maps" --replace
```

Browser checks: confirm these counts, switch NPCs off while moving/reloading/changing maps, and verify
same-material scenery, corpses and pickups stay visible. Check skin/standing poses, Quake palette lighting,
Q2 soldier variants, multipart bosses/turrets and DK companions. Check an empty/CTF map and independent
saved preferences. Watch frame rate and memory in the hub: groups are per actor, geometry is baked, and
hiding does not unload it. Quake/Q2 use even preview lighting and authored origins, without floor settling;
all readers hold one pose. Full-suite, browser and gameplay visual acceptance remain host-side checks.
