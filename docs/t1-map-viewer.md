# Offline T1 map viewer

Opens Starsiege: Tribes missions from Skinner in free-flight: terrain with its
stock textures and lightmap, buildings, rocks and placed objects (flags, stations,
generators, turrets, sensors, items). It covers the stock missions of an install
and any custom missions you point it at.

Unlike the T2 viewer this page needs no build step. It is plain Three.js
(`static/t1-maps/`) drawing the T1 model JSON the workshop already ships, placed by
scene files that `tools/import_t1_map.py` writes from your own game install.

## Importing maps

In the app: **T1 Maps** in the model sidebar, then **Import maps**. Enter your
Tribes folder to import every mission in its `base/missions`. To add custom
missions, also enter a mission file or a folder of missions; the Tribes folder is
still needed because custom missions reuse the game's terrain textures, buildings
and shapes. Maps already imported are skipped unless **Re-import existing maps**
is ticked.

From a checkout the same import runs as:

```powershell
python tools/import_t1_map.py --game-base C:/path/to/Tribes
python tools/import_t1_map.py --game-base C:/path/to/Tribes --mission C:/Maps/MyMap.mis
```

The game folder and mission files are only read. Output goes to
`local-data/t1-maps` (ignored by Git; next to the executable in a packaged build):
one folder per map under `maps/`, shared textures under `textures/` named by
content so identical bitmaps are stored once, buildings exported at import under
`models/`, and `index.json` listing the maps.

Both install layouts are read: newer installs with zip volumes and PNG textures,
and classic installs with `.vol` (PVOL) volumes and palettised PBMP bitmaps, which
are converted through the mission's palette. A mission that names `x.vol` finds
`x.zip` and the reverse.

## Controls

Pick a map from the dropdown. Click the scene to capture the mouse or drag to
look, WASD moves, Space rises, Shift descends, the wheel changes speed, Escape
releases the mouse and keys 1–9 jump to the mission's observer viewpoints. FOV,
Fog, **Invert horizontal** / **Invert vertical** and Reset view are above the
scene; settings persist in their own browser-storage namespace. Fog on uses the
mission's haze and visible distance; turn it off to see the whole map. Missions
without observer cameras (training and many custom maps) start from a generated
view above the placed objects.

## How a mission is resolved

- Resources come from the volumes the mission mounts (later ones win), then from
  any other volume in `base`, then from loose files beside the mission or in
  `base`. `SOURCES.json` in each map folder records the volumes and their hashes.
- Mission class and field names are matched case-insensitively, as the game does.
- A mission that does not mount its own terrain volume still gets `<terrain>.ted`
  from beside the mission or from the install.
- Terrain is one height block repeated 3×3 (64, 128 or 256 squares). Heights,
  per-square texture/orientation and the lightmap are LZH-compressed; the decoder
  and block layout follow ArenaPrototype's Tribes compatibility code, as do the
  DarkStar rotation matrix, square UV table and PVOL layout. PBMP reading also
  follows SurfaceLevel2's loader.
- Import checks itself: every compressed block must land exactly on the next
  block's declared size, and decoded heights must match the block's height range.
- Buildings resolve `name.N.dis` to the catalog model `name`. Buildings the catalog
  lacks, such as those custom maps ship in their own volumes, are exported at
  import with the existing interior exporter.
- Other objects map their datablock to a shape through the `shapeFile` values in
  the install's scripts (and scripts beside a custom mission).
- Anything still unresolved shows as a magenta box and is listed under Preview
  notes for that map and in the import output.

## Coverage

Checked against three installs: a 1.40-style install with zip volumes (46
missions) and two classic installs with `.vol` volumes (194 and 132 missions,
most of them custom). All 232 distinct maps import. 28 stock desert, ice and ruin
interiors were added to the T1 model catalog for this; custom buildings are
exported at import.

A mission can be imported against a different install than the one it sits in:
give its file as the custom mission and the install that has its volumes as the
Tribes folder. That is how maps whose extras volume is missing from one install
are completed from another.

Known gaps after scanning every archive in those installs (and others on the same
machine), including archives stored inside archives:

- `badmoon.vol`, BadMoon's lighting volume. The map loads with every object
  resolved, because all its buildings are stock; only its building lightmaps are
  absent.
- Stock interiors `dbridge`, `dcolumn` and `drock` have geometry but no material
  list (`.dml`) in the stock volumes, and no stock mission places them. Custom maps
  that use `dcolumn` or `drock` ship the material list in their own volume
  (`Desert_Rain.zip` and `Forsaken_Deserts.zip` in jcmolnar/Tribes-Repack do), so
  those buildings convert when such a map is imported. `dbridge.dml` was not found
  anywhere, including the archives of Tribes-Repack and Tribes-Asset-Store.

`BFstand.vol` (Bastard_Forge_Day) and `stand2.vol` (Runout) are also named by
their missions and absent, but nothing is lost: the buildings they held
(`bfstand`, `runout_stand`) are in `opencall2.vol`, which those missions also
mount, and their lit instances are in the missions' own volumes.

Tests: `python -m unittest tests.test_t1_maps`. Set `T1_GAME_BASE` to a Tribes
folder to also run the real Raindance import check (every object resolves, spawn
points stand on the decoded terrain).

## Limits

No collision, gameplay, audio or editing. The sky dome and weather are not drawn;
the background is the palette haze colour, or the sky's colour where the install
has no palette. Buildings are lit by the mission sun rather than their mission
lightmaps, so interiors are evenly lit. Shapes use the workshop's static pose and
elevators stay where the mission places them. The sun direction is approximate.
Custom shapes (`.dts`) that are not in the catalog are not converted; none occur
in the 198 maps checked. The in-app import has not been exercised in a packaged
build.
