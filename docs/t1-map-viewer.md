# Offline T1 map viewer

Opens Starsiege: Tribes missions from Skinner in free-flight: terrain with its
stock textures and lightmap, buildings and rocks with their mission lightmaps,
placed objects (flags, stations, generators, turrets, sensors, items), the
mission's sky with its suns, moons, stars and lens flare, and its rain or snow. It
covers the stock missions of an install and
any custom missions you point it at.

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
`local-data/t1-maps` (in `%LOCALAPPDATA%/DTS-Skinner`, shared by every build):
one folder per map under `maps/`, shared files under `textures/` named by content
so identical data is stored once (bitmaps, building lightmap atlases, their `.uv`
coordinates, `.anim` light animations and `.bsp` weather shelters), buildings
exported at import under `models/`, and `index.json` listing the maps. Packs
imported before lightmaps, sky, animated lights or weather shelters were added
still open, without them (older lightmaps hold every light in its first state);
tick **Re-import existing maps** (or pass `--replace`) to add them.

Both install layouts are read: newer installs with zip volumes and PNG textures,
and classic installs with `.vol` (PVOL) volumes and palettised PBMP bitmaps, which
are converted through the mission's palette. A mission that names `x.vol` finds
`x.zip` and the reverse.

## Controls

Pick a map from the dropdown. Click the scene to capture the mouse or drag to
look, WASD moves, Space rises, Shift descends, the wheel changes speed, Escape
releases the mouse and keys 1–9 jump to the mission's observer viewpoints. FOV,
Fog, Weather, **Animate lights**, **Invert horizontal** / **Invert vertical** and
Reset view are above the scene; settings persist in their own browser-storage
namespace. Fog on uses the mission's haze and visible distance; turn it off to see
the whole map. Weather switches the mission's rain or snow and is greyed out on
maps that have none. Animate lights off holds the buildings' flickering and
pulsing lights where they are (in their first state if it was off when the map
loaded); it is greyed out on maps without such lights. Missions
without observer cameras (training and many custom maps) start from a generated
view above the placed objects. While a map loads, its terrain is in view at once
as a green wireframe and turns textured as each terrain texture arrives, the
look t2-mapper's terrain has while loading (unlit here); locally that is a
tenth to half a second, longer the first time a map is opened. Each placed
building and object is likewise an orange wireframe box, t2-mapper's loading
interior, until its model has loaded (0.3 to 0.9 seconds on the larger maps).
Because the files under `textures/` and `models/` are named by their content,
the browser keeps them: opening a map again, or another map that shares them,
asks the app for none of them (Raindance: ready in 0.37 seconds instead of
0.61).

## How a mission is resolved

- Resources come from the volumes the mission mounts (later ones win), then from
  any other volume in `base`, then from loose files beside the mission or in
  `base`. `SOURCES.json` in each map folder records the volumes and their hashes.
- Mission class and field names are matched case-insensitively, as the game does.
- A mission that does not mount its own terrain volume still gets `<terrain>.ted`
  from beside the mission or from the install.
- Terrain is one height block repeated 3×3 (64, 128 or 256 squares). Heights,
  per-square texture/orientation and the lightmap are LZH-compressed in the game's
  own blocks (version 5) and stored raw in the version 0 blocks newer editors
  write, whose lightmap is one 8-bit grey level per word rather than 4-bit RGB.
  The block's own size is used even where the index disagrees. The decoder and
  block layout follow ArenaPrototype's Tribes compatibility
  code, as do the DarkStar rotation matrix, square UV table and PVOL layout. PBMP
  reading also follows SurfaceLevel2's loader.
- Import checks itself: every compressed block must land exactly on the next
  block's declared size, and decoded heights must match the block's height range.
- Buildings resolve `name.N.dis` to the catalog model `name`. Buildings the catalog
  lacks, such as those custom maps ship in their own volumes, are exported at
  import with the existing interior exporter, and shapes (`.dts`) it lacks with
  the existing shape exporter. No mission in the four installs checked (922
  missions) places such a shape, so this is tested two ways: importing Raindance
  against an empty catalog, where everything is then exported from the install
  and matches the catalog's models, and importing a mission folder whose own
  script places the install's editor shapes, which the catalog lacks. The shape
  exporter reads no geometry from some of those (`cube8`, `pyrm8`); such a shape
  stays a placeholder and is listed like any other unresolved object.
- Building lightmaps come from the mission's lighting volume. Each placed
  `name.N.dis` is a lit instance whose `.dil` replaces the outside-facing maps of
  the building's own lighting with ones holding the mission sun and shadows; the
  building's own light sources (state 0 of each) are added on top. Maps are packed
  into one small atlas per placed building at import (Raindance: 32 buildings,
  66 KB) and drawn as texture × lightmap, following ArenaPrototype's
  TribesInteriorLighting and TribesUnityInteriorLightmaps.
- Building lights that flicker, pulse or chase are animated. ArenaPrototype stops
  at state 0 of every light, so this part follows the DarkStar source instead
  (`ITRInstance::stepLightTime`, `updateLight`, `updateSpecialLight` and `merge`,
  `InteriorShape::clientProcess`). Lights the building's lighting flags auto-start
  loop for ever in 67 ms steps: the colour runs from each state's to the next
  one's, or, for flicker lights, a state is drawn at random every flicker
  interval. Each state has its own intensity map per surface, so a light can also
  move. Other lights stay in state 0, baked into the atlas. At import the animated
  lights are kept out of the atlas and written to one `.anim` file per building
  (states, colours, intensity maps and where each lands in the atlas); the viewer
  adds them back with the game's 4-bit saturating arithmetic whenever a light's
  state or colour changes. Of 229 stock interiors, 56 have such lights.
- `lightParams` on an `InteriorShape` is not read, as in the game: the field's
  setter there is empty (`setDataTypeLightAnimParam`) and `InteriorShape::onAdd`
  rebuilds the values from the building's lighting. The values in mission files
  are that lighting's own duration and auto-start flag per light, written back
  out on save: the 5,183 placed buildings checked in two installs all carry
  exactly them.
- A building with no lit instance (lighting volume missing, or a building placed
  without relighting the mission) uses its own lighting plus the mission sun on
  the faces marked visible from outside, as the game does, so interiors are still
  lit but nothing casts a shadow on it. If that also fails, or the lighting does
  not match the preview model's geometry, the building keeps plain sun shading and
  is listed under Preview notes.
- The sky is the mission's `Sky` object: sixteen panels around the camera textured
  from its material list (`dmlName`, `textures[]`), caps above and below, sized by
  `size` and the terrain's visible distance as in ArenaPrototype's
  TribesUnityEnvironment. A sky without a material list is its plain `skyColor`.
  Fog is the palette haze colour, else the first sky texture's bottom-left texel.
- Sky objects follow ArenaPrototype's TribesUnityEnvironment, TribesStarFieldRenderer
  and TribesPlanetFlare. Each `Planet` with a bitmap is drawn at its azimuth and
  incidence, as wide as its `size` over `distance`, in front of the sky and behind
  the world; a planet without a bitmap is only the mission's light. A `StarField`
  draws the game's 3,000 generated stars above the horizon in its three colours.
  A planet with `useLensFlare` strings the six `lensflare.dml` bitmaps from the sun
  through the screen centre with a wash of the sun's colour, fading as the sun
  leaves the centre and hidden when terrain or an object is in the way.
- Planet and flare bitmaps keep their transparency: a PNG's own; for a classic
  bitmap flagged colour-keyed, palette index 0 is clear (as ArenaPrototype reads
  it); for one flagged translucent, alpha is the fourth byte of its palette
  entries. ArenaPrototype has no rule for the translucent ones; this one was
  checked against the 1.40 PNG lens flares, which it reproduces exactly.
- `Snowfall` objects with rendering enabled draw rain or snow around the camera.
  Buildings keep it out as in the game (`Snowfall::processQuery` and
  `InteriorShape::getWeatherDistance`, which ArenaPrototype's
  TribesWeatherInterior ports the same way): the test is on the camera, not on
  each drop. With the camera inside a building's bounding box, nothing falls
  unless the part of the building it is in can see out, and then only beyond the
  opening it looks toward. For this each placed building of a mission with
  weather gets a small `.bsp` file at import (its box, BSP tree and which faces
  of the box each leaf sees). The game's distance arithmetic is kept as it is,
  slips included.
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

A fourth, larger install (Modern Tribes V30, zip volumes, 550 missions) was
imported as a coverage check: all 550 import, with a lightmap on every placed
building (21,554 across the 549 checked in one pass; `CanyonRemixLT` was added
afterwards, once the block's own size was trusted over its index, which says 256
squares for a 128-square block). `Superbowl` and `Superbowl2` name a `STADIUM.vol`
that is absent, which leaves the `hlfpstd1` building of `Superbowl2` as the one
unresolved object.

Known gaps after scanning every archive in those installs (and others on the same
machine), including archives stored inside archives:

- `badmoon.vol`, BadMoon's lighting volume. The map loads with every object
  resolved, because all its buildings are stock; they are lit from their own
  lighting plus the sun, without the mission's cast shadows.
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

The in-app import was checked in a packaged build (PyInstaller, the repository's
spec): through the app's own import route it imported Raindance from the zip
install, a custom map with 79 buildings of its own from a `.vol` install, and a
mission folder placing uncatalogued shapes. The first two came out identical,
file for file, to an import run from source.

Raindance's `lrock61` rock is under the ground because the mission puts it there,
not because of how it is turned: its origin is 5.9 units below the terrain and no
part of the rock is further than 5.76 from its origin, so no rotation brings it
above. The placement matrix is the one in DarkStar's `RMat3F::set(EulerF)`, and
the map's other rocks turned on three axes sit as expected.

Tests: `python -m unittest tests.test_t1_maps`. Set `T1_GAME_BASE` to a Tribes
folder to also run the real Raindance import check (every object resolves, every
building has a lightmap matching its model, animated lights land inside their
atlas, the sky and rain are found, Blastside's
sun and flare bitmaps keep their transparency, spawn
points stand on the decoded terrain).

## Limits

No collision, gameplay, audio or editing. Building lights that only scripts switch
(`Interior::switchOnLight` and the like) stay in their first state. Placements
that share one lightmap animate in step, and animated lightmaps are blended on
the CPU for every building of the map, in view or not.
Stars follow the game's generator but not its exact constellation (the game seeds
it from shared state) and are not snapped to the palette on classic installs. A
planet bitmap stays upright on screen rather than keeping its top toward the
zenith, and only the first flared planet has a flare. Seen from outside, rain and
snow still fall through a building's roof, as in the game: only depth hides them.
Shapes (not buildings) are shaded by the mission sun without shadows, use the
workshop's static pose, and elevators stay where the mission places them. The sun
direction used for shapes and for buildings without a lit instance follows
ArenaPrototype's convention; the mission-baked lightmaps agree with it better than
with the alternatives tried, but only by a small margin.
