# Offline T1 map viewer

Opens the stock Starsiege: Tribes Raindance CTF mission from Skinner: terrain with
its stock textures and lightmap, buildings, rocks, the bridge and placed base
objects (flags, stations, generators, turrets, sensors), in free-flight.

Unlike the T2 viewer this page needs no build step. It is plain Three.js
(`static/t1-maps/`) drawing the T1 model JSON the workshop already ships, placed by
a scene file that `tools/import_t1_map.py` writes from your own game install.

## Setup

```powershell
python tools/import_t1_map.py --game-base C:/path/to/Tribes/base
python app.py
```

Then click **T1 Maps · Raindance** in the model sidebar. The pack is written to
`local-data/t1-maps` (ignored by Git; next to the executable in a packaged build).
Import refuses to overwrite an existing pack; delete the folder or pass `--output`.
The install is only read. Resources come from the volumes the mission itself
mounts, and `SOURCES.json` records their hashes.

The importer reads installs whose volumes are zip files with PNG textures (the
layout it was built and checked against). Original `.vol` volumes with palettised
BMP textures are not read yet.

## Controls

Same as the T2 map page: click the scene to capture the mouse or drag to look,
WASD moves, Space rises, Shift descends, the wheel changes speed, Escape releases
the mouse and keys 1–7 jump to the mission's observer viewpoints. FOV, Fog,
**Invert horizontal** / **Invert vertical** and Reset view are above the scene;
settings persist in their own browser-storage namespace. Fog on uses the
mission's haze and visible distance (200 / 450); turn it off to see the whole map.

## How it is put together

- `Raindance.MIS` gives the object list, terrain placement, sun and fog distances.
- `Raindance.ted` holds the terrain index and one 256×256 height block, repeated
  3×3 as in game. Heights, per-square texture/orientation and the lightmap are
  LZH-compressed; the decoder and block layout follow ArenaPrototype's Tribes
  compatibility code, as do the DarkStar rotation matrix and square UV table.
- Import checks itself: every compressed block must land exactly on the next
  block's declared size, and decoded heights must match the block's height range.
- Buildings resolve `name.N.dis` to the catalog model `name`; other objects map
  their datablock to its stock shape. Unknown shapes show as magenta boxes and are
  listed under Preview notes.
- The six lush rocks (`lrock1`–`lrock6`) were added to the T1 model catalog for
  this map, with their five textures.

Tests: `python -m unittest tests.test_t1_maps`. Set `T1_GAME_BASE` to a Tribes
`base` folder to also run the full Raindance import check (all 56 objects resolve,
spawn points stand on the decoded terrain).

## Limits

Raindance only. No collision, gameplay, audio or editing. The sky dome and rain
are not drawn (the background is the sky texture's haze colour). Buildings are lit
by the mission sun rather than their mission lightmaps, so interiors are evenly
lit. Shapes use the workshop's static pose. The sun direction is approximate.
