# Diabotical map viewer

Opens Diabotical maps from Skinner for a free-flight look at their blocks,
drawn in the textures of their materials. A first version: no props, entities,
water, decals or baked lighting, one fixed sun, and no editing. Reflex, Q3, T1
and T2 maps have their own pages, see [reflex-map-viewer.md](reflex-map-viewer.md),
[q3-map-viewer.md](q3-map-viewer.md), [t1-map-viewer.md](t1-map-viewer.md) and
[t2-map-viewer.md](t2-map-viewer.md).

## Getting maps

**Import maps** on the page (or `tools/import_diabotical_map.py --game-base
"C:/Program Files/Epic Games/Diabotical"`) reads the game's maps from
`packs/maps.dbp` and the maps made in the game's editor from
`%APPDATA%/Diabotical/Maps`, into `local-data/diabotical-maps` (ignored by
Git). The game folder is only read. A whole install takes about half a minute:
174 maps, about 225 MB of blocks and 40 MB of textures (295). Maps already imported
are skipped unless their file changed or **Re-import existing maps** is
ticked. The five oldest maps (the menu maps and `temple_islands`, version 21)
are not read yet and are listed as failed.

## What is drawn

A Diabotical map is a grid of blocks, each 40 units wide and deep and 20 tall,
with a shape, a quarter turn and a material on each of its six faces. The
import keeps the blocks that can be seen: a cube's face against another cube
is left out, and so are cubes closed on every side. The page meshes what is
left (`static/diabotical-maps/blocks.js`).

- **Shapes.** 1 is a cube, 3 a half cube cut along a vertical diagonal, its
  missing corner set by the turn. Shapes 2, 4, 5 and 6 draw nothing in the
  game's own OBJ export (probably invisible blocks: clips, triggers…), so the
  page leaves them out. Cubes are drawn without the game's 4-unit bevelled
  edges.
- **Materials.** A face's material is an asset (`scripts/*.assets`) naming a
  material in a `.shader` file; its first `map` is its colour texture (BC7
  `.dds` in the packs, decoded with Pillow and stored as PNG, 512 pixels at
  most) and `uv_scale` how often it repeats: once every 40 / `uv_scale` units.
  A name with a variant (`metalwall_heat01:3`) is drawn as its material. A
  name defined more than once takes its first definition in pack-name order
  whose texture is found, looking also beside the shader file under the
  texture's file name (as `black` and `white` need: the game draws them with
  `models_theme.dbp`'s scale 0.5, not `scripts.dbp`'s 1). 13 material names of
  the stock maps are not in the game files and are drawn in a flat colour of
  their own, listed in the map's Preview notes.
- **Half blocks.** The sloped side takes the material and texture axes of the
  stored face numbered by the turn (face 0 for turn 0, …), so its texture is
  stretched across the slope by √2, as in the game.

The file formats are written out in the import's module docstring
(`tools/import_diabotical_map.py`).

## How it was worked out

Measured on 2026-10-04 by running the game unattended offline
(`diabotical.exe --noepicstore`, the user's approval per run), loading test maps
made by script into its editor (`/edit NAME`) and reading back what it wrote:

- The game loads a map built from its own empty map with the block list
  replaced, and its `/save` writes every block back byte for byte. The header's
  hash is not checked.
- Its `/export NAME` writes the map as an OBJ (into the install folder's
  `maps\`, which it creates). The geometry of each shape and turn, the block
  size, the axes (the export mirrors z) and the face order came from exports of
  test maps: cubes floating in the air with one face per cube in a different
  material show which stored face is which by where the texture scale changes.
  Texture axes and scales came from the export's texture coordinates.
- Record layouts of versions 24 and 25 came from maps the game ships in two
  versions (`gr_titans_crossing` and `tt_gr_titans_crossing`; the editor's empty
  map, saved as version 25 and 27).

- Not mirrored: an "F" of pillars seen from straight above in the game
  (`/phy_fly 1`, `/goto -40 600 -40`, the mouse turned 4000 counts down) reads
  the same, the same way up, as the page drawing that map from the same point,
  so the game's x and z are the export's. Yaw 0 looks toward the export's -z.
- The sloped side: four half blocks, one per turn, each face in a material of
  a different texture scale; in the export the slope's scale is that of the
  face numbered by the turn, divided by √2. The same export confirmed the page's
  reading of six materials' `uv_scale` and showed `black`'s 0.5.

## Not done yet

Entities and props (models placed by the map), water and other volumes,
decals, lighting (the maps' large baked light data), the per-face flag byte and
the six small per-face values (probably texture offset and turn), what the
invisible shapes are, bevelled edges, version 21 maps, and editing.
