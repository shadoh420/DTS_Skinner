# Offline T2 map viewer

Opens the Tribes 2 missions of a local install from Skinner in free-flight:
terrain, interiors, placed shapes, sky, water and force fields, drawn by a
compiled build of [exogen/t2-mapper](https://github.com/exogen/t2-mapper) that
also runs the game's own mission scripts. No collision walking, gameplay, audio
or map editing. T1 maps have their own page, see [t1-map-viewer.md](t1-map-viewer.md).

Upstream: https://github.com/exogen/t2-mapper
Pinned source: e9b6332aaa48bc79535655d644bacc7e22b6ac79.
Source checkout: C:/tmp/t2-mapper-skinner-reference (sparse source, no game assets).

## Importing maps

In the app: **T2 Maps** in the model sidebar, then **Import maps**. Enter your
Tribes 2 folder (`GameData` or its `base` folder). Every mission in the archives
below is imported in one go, about 370 MB and a few seconds on a local disk. A
pack that already exists is kept unless **Re-import existing maps** is ticked.
From a checkout the same import runs as:

```powershell
python tools/import_t2_map.py --game-base C:/Dynamix/Tribes2/GameData [--replace]
```

The game folder is only read. Output goes to `local-data/t2-maps` (in `%LOCALAPPDATA%/DTS-Skinner`,
shared by every build and checkout). Only these archives are read,
in this order, later ones winning; loose files and other archives are not:

`base`, `scripts`, `missions`, `shapes`, `interiors`, `textures`, `skins`,
`badlands`, `desert`, `ice`, `lava`, `lush` (all required), then
`Classic_maps_v1`, `TR2final105-client` and `TR2final105-server` where present.

Of the archives' files only those the viewer reads are copied (scripts,
missions, terrains, shapes, interiors, PNG, JPEG and BMP textures and their
lists). The game's paletted `.bm8` copy of nearly every PNG, 126 MB in all, is
left out: upstream never reads one, and the 130 kB of textures that exist only
as `.bm8` could not be drawn either way.

The TR2 server archive holds no maps: it has `TR2Game.cs` and the datablocks the
eight Team Rabbit 2 missions run. `T2csri`, `zz_Classic_client_v1` (client and
login scripts), `TR2final093-extras`, `audio` and `voice` are left out.

Unlike the T1 pack, which has a folder per map, this is one shared pack, as
upstream's is: the missions share nearly all their scripts, shapes, interiors
and textures. `manifest.json` lists every resource and the missions (name,
display name, mission types, source archive); `SOURCES.json` records the
archive hashes. Replacing a pack builds the new one beside it and swaps them;
a folder that is not a map pack is never replaced.

## Controls

Pick a map from the dropdown (grouped Official, Classic, Team Rabbit 2) and,
for a mission with several game types, the type beside it; the type decides
which of the mission's objects are placed. The choice is kept in the address as
`?mission=Name~Type`, upstream's form. Click the scene to capture the mouse or
drag to look, WASD moves, Space rises, Shift descends, the wheel changes speed,
Escape releases the mouse and keys 1–9 select the mission's observer
viewpoints. FOV, Fog, **Invert horizontal** / **Invert vertical** and Reset view
are above the scene; settings persist in their own browser-storage namespace.
Every mission of the install has observer cameras; a map opens at its first.
A mission whose display name is not its file name is listed with both
("Training1 · Newblood").

**Preview notes** lists what the renderer could not resolve on the map in
view, in plain words taken from upstream's log: files a mission, shape or
material list names that are not in the game files (upstream draws a
fallback), with the stock game's own known gaps marked as such, shapes or
shaders that failed, lines a mission's scripts print through `error()`, and a
mission whose scripts have not reported ready after 90 seconds.

## Where this departs from upstream

- The pack is flattened to one lowercase tree with later archives overwriting
  earlier ones, where upstream keeps a folder per archive and the game's order
  (by archive name). On the install checked the two orders pick identical
  content for every path, and the three added archives override nothing.
- Mission names and types are read at import in Python, and mount points by the
  viewer when it first opens a pack (stored as `mounts.json`), with upstream's
  own `parseDTS` and `extractMountTransforms`; upstream does both in its Node
  manifest build. This is what lets a packaged build import without Node.
- A plain dropdown replaces upstream's search box, and choosing a map loads a
  fresh page, as the T1 viewer does. Upstream switches in place, but its caches
  keep every map's textures (1.6 GB of script memory after opening all 84) and
  report an unresolved file only the first time it is asked for.
- One shader line is patched at build time. Upstream's reflection lookup for
  shapes reads `transformedNormal`, which Three declares for an unlit material
  only when it is skinned, so the instanced glowing parts of shapes whose
  datablock has an environment map did not compile and were not drawn. Upstream's
  main branch has the same line.
- A water block whose environment map is not in the game files (the five maps
  in the table below with a water entry) had upstream's white fallback texture
  added to its water. It now adds nothing, the rule upstream's sky already
  follows for a missing environment map. Patched at build time as well.
- As before: audio off, settings and skins kept local, mouse-look inversion,
  no live-server, demo or relay interfaces. CSP blocks external requests.

## Coverage

Checked on 2026-10-02 against the install at `C:\Dynamix\Tribes2\GameData`:
84 missions (52 in `missions.vl2`, 24 in `Classic_maps_v1.vl2`, 8 in
`TR2final105-client.vl2`), 4,450 resources, 371 MB, mount points on 89 shapes.
The 79 shapes of the earlier Katabatic pack get the same mount points as the
Node build step gave them; the other ten are TR2 armors and weapons.

All 84 import, with a terrain for each. Counting every mission type there are
133 combinations, and each was loaded twice:

- Headless, by `check-pack.ts` (below): all 133 become ready, with every
  terrain, interior, sky material list and shape file they place in the pack,
  and the importer's names and types equal to upstream's parser.
- In the browser, each on a fresh page: all 133 reach "Map ready", the slowest
  in 13 seconds. 53 missions list nothing under Preview notes; 30 list one or
  more of the following, all of them files the stock archives name but do not
  hold (upstream draws its white fallback):

| Unresolved | Missions |
| --- | --- |
| `textures/skins/axe` (named by five turret and barrel shapes) | Archipelago, DeathBirdsFly, DesertofDeath_nef, Hillside, IceBound, IceRidge_nef, JacobsLadder, Katabatic, Lakefront, Magmatic, Overreach, Quagmire, Rollercoaster_nef, Sandstorm (CTF, DnD), Starfallen, Stonehenge_nef, Surreal (CTF), Titan, Training3, WhiteDwarf |
| `textures/desert/skies/ice_blue_emap` (sky environment map, the file is under `ice/skies`; shapes get no reflection) | IceBound, IceRidge_nef, Katabatic, Rimehold, SubZero, ThinIce, Whiteout |
| `textures/ice/skies/icebound_emap_cloudsground` (water environment map, the file is under `liquidTiles`) | IceBound, ShockRidge, ThinIce, WhiteDwarf |
| `textures/lava/skies/volcanic_starrynite_v5_dn` (the sky box's bottom face, below the horizon; the file is `starrynite_v5_DN`) | FrozenFury, GodsRift, SkinnyDip, SolsDescent |
| `textures/lava/tlite1t` (a building texture) | Surreal |
| `textures/special/lush_env` (the default water environment map) | Raindance_nef |

None of these files is anywhere in the install under that name, so the game
itself lacks them. Training2 and Training3 also list a line each that their
own scripts print through `error()` ("Running Mission 2 Script", "Effective
Turret Range is: 150"); `check-pack.ts` lists the same two as script output.

Looked at in the browser: Katabatic (ice), Riverdance (lush, both types),
Desiccator (desert), Recalescence (lava), Minotaur (badlands), Raindance
(Classic), Crater 71 and Treasure Island (Team Rabbit 2) and Training1. Before
the shader patch above, 13 or more maps, the Hunters maps among them, logged
the compile error; none does now.

The in-app import was checked from an empty state in a source run: the page
imported the install, reloaded, read the mount points and drew Katabatic, and
the pack was identical, file for file, to one imported from the command line.
The same was then done in a packaged build (PyInstaller, the repository's
spec, run from its own folder): same result, same pack.

Not checked: every map by eye, the look of each map against the game, and
anything that needs the scripts to run on (see Limits).

## Build

The checked-in `static/t2-maps` bundle works without Node at runtime. To rebuild:

```powershell
git clone --depth 1 --filter=blob:none --sparse https://github.com/exogen/t2-mapper C:/tmp/t2-mapper-skinner-reference
git -C C:/tmp/t2-mapper-skinner-reference fetch origin e9b6332aaa48bc79535655d644bacc7e22b6ac79
git -C C:/tmp/t2-mapper-skinner-reference checkout e9b6332aaa48bc79535655d644bacc7e22b6ac79
git -C C:/tmp/t2-mapper-skinner-reference sparse-checkout set src generated public scripts relay
python tools/build_t2_maps.py --source C:/tmp/t2-mapper-skinner-reference
```

Build requires Node/npm, installs the pinned lockfile when dependencies are
absent, validates the upstream revision and that its tracked inputs are
unchanged, copies `map-client/` into the checkout as `skinner/` and replaces
only the generated `static/t2-maps` bundle. The build does not touch the map
pack. `UPSTREAM.json` and `THIRD-PARTY-NOTICES.txt` ship with the bundle.
Upstream `package.json` declares MIT and this revision has no root LICENSE.

## Checks

`python -m unittest tests.test_t2_maps` covers the importer (every mission,
encoding, precedence, replace, traversal, missing archives) and the local
routes. Two checks run from the upstream checkout after a build:

```powershell
$env:LOG_LEVEL = 'error'; $env:TSX_TSCONFIG_PATH = 'tsconfig.node.json'
node --import=tsx/esm skinner/check-pack.ts C:/path/to/local-data/t2-maps
node --import=tsx/esm skinner/check-mouse-look.ts
```

`check-pack.ts` reads the mount points itself when the viewer has not yet
opened the pack and written `mounts.json`. It loads every mission in each of its types through upstream's script runtime, as
upstream's own mission-load test does for one map, and reports missions that
fail or do not become ready, terrain, interior, sky and shape files the pack
lacks, runtime errors, and any mission whose name or types the importer read
differently from upstream's parser. It does not draw anything.

## Limits

Free-flight only. The script runtime does not simulate gameplay: upstream skips
the AI, admin and single-player dialogue scripts and logs the engine functions
it does not implement, so scripted events (Training missions, Siege gates,
TR2 play) do not happen. A rendered check is not a claim of full native map
fidelity. Embedded-browser mouse capture is unavailable; drag-to-look is the
fallback there.
