# Diabotical map viewer

Opens Diabotical maps from Skinner for a free-flight look at their blocks,
props and heightmap terrain, drawn in the textures of their materials, with markers for spawns and
pickups and liquids as see-through boxes. No decals, lights or baked lighting,
one fixed sun, and no editing. Reflex, Q3, T1
and T2 maps have their own pages, see [reflex-map-viewer.md](reflex-map-viewer.md),
[q3-map-viewer.md](q3-map-viewer.md), [t1-map-viewer.md](t1-map-viewer.md) and
[t2-map-viewer.md](t2-map-viewer.md).

## Getting maps

**Import maps** on the page (or `tools/import_diabotical_map.py --game-base
"C:/Program Files/Epic Games/Diabotical"`) reads the game's maps from
`packs/maps.dbp` and the maps made in the game's editor from
`%APPDATA%/Diabotical/Maps`, into `local-data/diabotical-maps` (ignored by
Git). The game folder is only read. A whole install takes about five minutes:
174 maps (about 340 MB of blocks and entities, 5 MB of terrain), the 6,000 models they place
(300 MB) and the textures of their materials (about 120 MB). Maps already imported
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

- **Props.** An entity whose `model` is a path under `models/` or an asset
  naming one is drawn as that model: its `.fbx` (read by `tools/fbx_mesh.py`;
  the game's compiled `.dbm` is the same model with z negated, and the bounding
  boxes of 7,720 of the 7,974 models shipped both ways match) placed by the
  entity's position, rotation and scale. Its material is the asset's or the
  entity's `material`, else a shader named after the model, else the one
  named most like it in the nearest `.shader` file at or above its folder.
  A dynamic prop (trims, pipes, walls: about half of all props) is a row, column
  or block of 40-unit cells from the entity (its corner), its scale the size in cells; its asset's
  `dynamic_rule` blocks pick each cell's model by the cell's offsets from the
  prop's ends (the last rule that holds wins, per channel). The game picks among
  a rule's variants at random; the page picks the same way every time.
  `PATH_flipx` is `PATH` mirrored. Props marked `no_show` (clip boxes and other
  invisible ones) and materials marked `visible false` are left out; foliage
  (shadow shader `shadow_at_…` or `culling off`) is cut out by its texture's
  alpha and two-sided.
- **Terrain.** A map with a `terrain` entity has a heightmap beside it
  (`NAME-h.png`, 512 × 512, the height in red) and a dirt mask (`NAME-b.png`).
  Each pixel is a vertex 40 units from the next, pixel 256 at x = z = 0, its
  height the entity's `offset_y` + 8 × red; the entity's own position is not
  used. It is drawn as the game's terrain shader (`tileter.ps`) draws it: the
  material's first map on flat ground, mixed with its sixth by the dirt mask,
  and its fourth, four times larger, where the slope is steeper than about 37°
  (normal y below 0.8, blended up to 0.85). The material is the entity's
  `material` or `shader`, else `core_ter` (grass, rock cliffs). The ground
  texture repeats every 100 units or so, judged by eye.
- **Markers and liquids.** Spawns, health, armour, weapons, ammo, jump pads,
  teleporters, flags and power-ups (named by the entity's name) are coloured
  shapes; `liquid_*` entities boxes of their scale, centred on them.

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
  the same, the same way up, as the page drawing that map from the same point
  (the point at (-40, -40) hid the axes: the game's `/goto` z is the export's
  -z, see the props below). Yaw 0 looks toward the export's -z.
- The sloped side: four half blocks, one per turn, each face in a material of
  a different texture scale; in the export the slope's scale is that of the
  face numbered by the turn, divided by √2. The same export confirmed the page's
  reading of six materials' `uv_scale` and showed `black`'s 0.5.
- Props: where entities stand came from the stock maps (jump pads stand
  exactly on the blocks under them, pickups 20 units above). A test map with
  seven chairs in seven rotations and three dynamic props (a wooden trim 4
  cells long, a pipe 3 cells high, a sport trim 3 cells long) beside 1-block
  pillars was seen in the game from above and from the side (`/goto` takes the
  entities' coordinates; `/printcamera` gives the eye, 26 units above) and
  drawn by the page from the same points: the chairs match (rotation: roll
  about z, then pitch about x, then yaw about y, in the game's axes) and so do
  the dynamic props once a prop's cells start at the entity (cell centre at
  40 × (i + ½) on each axis).
- Liquids: the same map without its `terrain` entity (which hides the water
  near the map) and with a column of blocks hanging from the floor's edge to
  y -900, black and white every 100 units: from 260 and 860 units away the
  water meets the column at y -270, the top of the template's ocean (y -520,
  500 tall) as a box centred on its entity.
- Terrain: the game builds the terrain's mesh itself (its vertex shader
  `tilestaticter.vs` reads no heightmap), so the mapping was fitted to the
  stock maps: the grass, flowers and bushes standing on the terrain in 64 maps
  (4,444 props, most of them on blocks). With height = `offset_y` + k × red,
  457 stand within 3 units of the surface at k = 8 against 100 to 230 at 7.9 or
  8.1; x and z map to column and row without a flip or a swap, centred on pixel
  256 (to within half a cell: the fit is flat there). The pixel shader
  (disassembled with the game's own `d3dcompiler_47.dll`) gave the texture
  slots, repeats and slope blend. In run 9's screenshot the floor's side shows
  less above the grass than the page draws, so the ground may be up to one
  step (8 units) higher in the game.

## Not done yet

Decals, lights, billboards, particles, props' `color` tints, the terrain's
exact texture repeat and height (one step), its normal maps and the fields
of one map only (`tt_boost`: `offset_x`, `scale_y`, read as a shift and a
height scale untested; `mirrored`, not read), the game's choice among a
dynamic rule's variants and the rule conditions not understood (`/`, `when`,
neighbour tests such as `left empty`), skinned and ASCII FBX models, about 250
prop materials not found (flat colours), water
surfaces, lighting (the maps' large baked light data), the per-face flag byte and
the six small per-face values (probably texture offset and turn), what the
invisible shapes are, bevelled edges, version 21 maps, and editing.
