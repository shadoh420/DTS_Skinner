# Diabotical map viewer

Opens Diabotical maps from Skinner for a free-flight look at their blocks,
props, heightmap terrain and decals, drawn in the textures of their materials, with markers for spawns and
pickups, and liquids as glowing see-through surfaces. No lights or baked lighting,
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
  page leaves them out; run 32 showed a roof of each casts no sun shadow
  either (nor do props, with or without `__shadow`). Cubes are drawn without the game's 4-unit bevelled
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
  An entity's `material` X is first looked up as the shader `MODEL_X`
  (bioplant's door frames: `frame_red` on `corridor_path_..._tile_x` is
  `corridor_path_..._tile_x_frame_red`, a tinted variant), else as X.
  A dynamic prop (trims, pipes, walls: about half of all props) is a row, column
  or block of 40-unit cells from the entity (its corner), its scale the size in cells; its asset's
  `dynamic_rule` blocks pick each cell's model by the cell's offsets from the
  prop's ends (the last rule that holds wins, per channel). The game picks among
  a rule's variants at random; the page picks the same way every time.
  `PATH_flipx` is `PATH` mirrored. Props marked `no_show` (clip boxes and other
  invisible ones) and materials marked `visible false` are left out; foliage
  (shadow shader `shadow_at_…` or `culling off`) is cut out by its texture's
  alpha and two-sided.
- **Colour tints.** A material drawn by the game's `tilemask.ps` (886 of
  the materials the maps use, 207 of them on blocks) has a colour mask, its fifth
  map, and up to three accent colours of its own (`pixel_shader_param
  accent1 444444`); a prop's `color`, `color2` and `color3` replace them,
  as a hex colour or as `accentN`, the map's palette in its `global`
  entity. Where the mask is red the texture turns toward accent 1 times the
  texel's brightness (the mean of its red, green and blue), then green
  toward accent 2, then blue toward accent 3 (read from the compiled
  shader). The game takes the hex as linear light (808080 is half of
  ffffff's light), so the page, which mixes display values, raises it to
  the power 1 / 2.2 first. A colour with no accent set leaves the texture
  as it is. Blocks take their material's own accents.
- **Terrain.** A map with a `terrain` entity has a heightmap beside it
  (`NAME-h.png`, 512 × 512, the height in red) and a dirt mask (`NAME-b.png`).
  Each pixel is a vertex 40 units from the next, pixel 256 at x = z = 0, its
  height the entity's `offset_y` + 8 × red; the entity's own position is not
  used. The dirt mask's pixels are cells instead (pixel 256 covers 0 to 40),
  so each vertex takes the mean of the four around it. It is drawn as the
  game's terrain shader (`tileter.ps`) draws it: the material's first map on
  flat ground, repeating every 128 units, mixed with its sixth by the dirt
  mask, and its fourth, four times larger, where the slope is steeper than
  about 37° (normal y below 0.8, blended up to 0.85). The material is the
  entity's `material` or `shader`, else `core_ter` (grass, rock cliffs).
- **Decals.** A `decal_…` entity paints its material's texture (drawn by the
  game's `tiledecal.ps`, with alpha) on the surfaces inside a box: the unit
  cube centred on the entity, under its rotation and scale. Only surfaces
  facing the box's local -z take it (the shader drops those whose normal is
  more than about 84° off). The page clips each block, terrain and prop
  triangle to the boxes near it (`static/diabotical-maps/decals.js`) and draws
  the pieces lit as the surface, times the decal's `color` (RRGGBB, or
  AARRGGBB with alpha first, as sRGB), in `order`. The texture's top is the
  world's up as seen along the box, turned by the decal's roll (rotation z):
  that is the box's own local y while cos(rotation x) > 0, and the texture is
  turned round where the box faces exactly up or down (floors: no up to see)
  or is turned past 90° about x. The game then cuts the decal to a second
  box of the same size and place, turned by the yaw the other way and not by
  the roll: a decal at a yaw that is not a right angle shows as a
  parallelogram, unless its box is square. A `v3` decal's boxes take |R| s
  as their scale (the rotation's entries as absolute values, times the
  scale); `v1` and `v2` decals take it as given. `mirrored` changes nothing
  seen in the game, nor does a box's depth.
- **Lights.** Lit as the game's lighting shader (`tile.cs`) does, with the
  numbers measured in it: the picture is the texture's bytes times the light,
  then the game's last pass, which looks the colour up in a 16-step LUT (the
  identity `textures/lut.png` unless the map sets `lut`) without the
  half-texel fix and so makes x into (16x − 0.5) / 15 (runs 21 to 23); the
  page does the same to every pixel. Point lights (`point`, `diffuse`, or no
  `type`) give colour (hex as linear) × `intensity` (default 4) / π × N·L, whole out to
  `falloff` × `radius` (falloff default 0.33) and fading to nothing at `radius`
  as ((radius − d) / (radius − inner))^2.2; spots light a cone of their whole
  `angle`, softened over `softness` (in cosines); a capsule is the segment
  from its position along its local +z for `length`. None casts a shadow. The
  sun travels along its local +z, colour × intensity / π × N·L, and casts
  the only shadow, in which it is multiplied by the `global` entity's
  `shadow_color` (by default the bluish 0.325, 0.469, 0.519 measured in run
  20) and the shadow ambient stands for the ambient. The ambient is its hex,
  linear. Ambient nodes replace the ambient where they reach (a 3D grid built
  from them, as the game builds one when a map loads): a sphere by a share
  of 0.86 − d (d the distance over the radius), a cubic node wholly to 0.86
  and not at all past 1.11 (its largest axis distance), the node's colour
  (hex, linear) weighted by the share squared. Where nodes meet, each adds
  a weight (a sphere's 1 − d) and the share is their sum less 0.14, the
  colour their weighted mean times the share squared, so two nodes between
  them take more of the ambient than either would leave the other (run
  26). Their `intensity` and `falloff` change nothing, and a node whose
  centre lies outside the blocks' bounds does nothing at all (run 23). The
  game's own grid shows its nodes about 27 units toward +x and +z of where
  they stand (probably sampled half a cell off); the page does not.
- **Specular light and reflections** (from `tile.cs`, not yet checked in the
  game). A material's specular map (its map 2) holds gloss in red and
  strength in green; its material id is its map 3's red where that is not
  black, else its `material_id`, else 40 (`textures/metal.png`, most blocks' id
  map, is 40 too; run 17's `default`, with neither, is a mirror).
  Point, spot, capsule and sun lights (not `diffuse` ones) add GGX specular
  light: roughness 1 − gloss × the map's `gloss` (default 1), Schlick
  visibility with k = (roughness + 1)² / 8, the distribution raised to
  1 / 2.2, Fresnel on N·L from F0 = albedo × the id's share (0.5 for 40 to
  49, 1 for metals, 51 to 79, else 0), times strength; the sun's is shaded as
  its diffuse light. Materials of share above 0 also reflect the map's
  `envmap` (a cube in `packs/textures_cubemaps.dbp`, `default_envmap`, a
  cathedral, where the map names none) along the reflection, at mip
  roughness^(1 / 2.2) × 10, times strength and the ambient light's hue (its
  colour over its largest channel); metals reflect it grey (51: by the
  albedo) and take no ambient, and id 46 takes 1.4 times the ambient.
- **Markers and liquids.** Spawns, health, armour, weapons, ammo, jump pads,
  teleporters, flags and power-ups (named by the entity's name) are coloured
  shapes. A `liquid_*` entity is a box of its scale centred on it, its top
  the surface: drawn unlit (lava and acid glow their colour, as in the game)
  in its `color` (AARRGGBB: the alpha is the surface's opacity); one with
  no colour is clear water, as bioplant's pools are in game; the ocean (`shader ocean`, `core_ocean`)
  is dark blue and mirrors the envmap. The game's own surfaces (water.ps,
  panel_water.ps) add moving noise, foam where they meet the ground and fog
  over distance; the page leaves those out, and its liquids light nothing
  around them (whether the game's do is not measured).

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
  slots, repeats and slope blend. Then a test map with a heightmap and dirt
  mask made by script, seen in the game (run 11): a column of blocks black and
  white every 20 units meets ground predicted at y -80 exactly on a band's
  edge, and ground predicted at the floor's top is level with it; a one-pixel
  ridge along a column and one along a row, seen from straight above, have
  their crests on the lines of x = 600 and z = 600 (to about 5 units), as
  blocks placed there show; a painted stripe of the dirt mask turns the ground
  to dirt between blocks at its predicted edges; and the grass texture
  repeats every 128 units (205 pixels in a shot from above where the floor's
  80-unit plates are 128).
- Tints: from `tilemask.ps`'s disassembly. The colour space from run 12:
  six sport trims (mask all red) side by side, coloured ffffff, c0c0c0,
  808080, 404040, 000000 and ff8000 (each entity needs a name of its own:
  the game keeps only the first of a name), seen from above and from the
  front. Taken back to linear light less the black one's, the top faces
  of c0, 80 and 40 come out 0.84, 0.52 and 0.26 of ffffff's: the hex is
  used as linear (decoded from sRGB it would be 0.54, 0.22, 0.05). Fitting
  both shots with an sRGB output, a filmic (ACES) curve and the hex as
  linear leaves 4 of 255 off on average; with the hex decoded, 15. The
  page then draws the top faces' ratios 0.90, 0.77, 0.59 against the
  game's 0.92, 0.75, 0.56 (the front faces, darker, differ more: the page
  has no such curve). The orange one's green comes out darker than the
  grey one's in the game: its picture is also more saturated, as a whole.
  (Run 17 later showed the game's picture is its shader's value with no sRGB
  step or curve, so the page's accent encoding, hex^(1/2.2), stands as a
  measured fit rather than an inverse curve.)

- Decals: the box from 2,700 flat stock decals (the surface under one lies at
  its centre and faces its local -z, whatever its version) and the decal
  shader's disassembly; the rest from run 13: fourteen `center_room` decals
  (text and arrows, so turns and mirrors show) on the template's floor and one
  on a pillar, seen from above and from the side and drawn by the page from
  the same points. v1 and v2 came out the same, a mirrored one the same as
  its twin, boxes raised 15 units the same as those on the floor; ff000080 dark
  blue (alpha first); 808080 half the value of ffffff in the picture (so sRGB,
  unlike an accent). A v3 decal 120 × 60 × 40 showed its texture 120 × 40
  flat and 60 × 40 turned a quarter (by roll or by yaw), which |R| s fits
  (and run 14's 40 × 40 rolled 45° on a wall, drawn about 57 × 57). The floor decals' texture
  runs against the box's x and y, the pillar's along them; no single rotation
  convention fits both. Run 14 (fifteen more): on a wall, rolls 0, 90, 180
  and 45 turn the texture counter-clockwise as seen facing it, along the
  box's x and y; a decal facing the other way along x the same; floor decals
  tilted 60°, 80° and even 89° read the wall way, only the flat one is turned
  round. That is a camera's view along the box with the world's up, then
  the roll: given the game's rotation (yaw, then pitch, then roll), the box's
  own x and y while cos(pitch) > 0, turned round past it. Of the stock decals
  13,852 have cos(pitch) > 0, 6,581 face straight up or down and 2,127 are
  past 90° (such as a "5" number decal at 180°, upright this way). Run 14's
  floor decals at a 45° yaw also showed only part of their text: run 15 put
  `dignitas_banner` decals (an orange rectangle filling nearly all its
  texture) flat on the floor at yaws 0, 30, 45, 60 and 90, one turned by roll
  45° instead, one at yaw 45° in a square box and one v3: yaws 30 to 60 come
  out as parallelograms with edges at +yaw and -yaw, the rolled one with two
  edges level, the square one whole. Simulated against the shot, a cutting
  box turned by -yaw and no roll fits all eight (overlap 0.77 to 0.92, as
  good as the uncut yaw 0, against 0.30 to 0.65 uncut), and explains run 14's
  wall decals rolled 90° in 40 × 20 boxes showing only about 20 × 20. A
  `center_room` decal on a ceiling (rotation x -90°) is turned round, as a
  floor's.
- Lights: the game's lighting compute shader (`tile.cs`, disassembled) gave
  the model (ambient grid, light list, sun shadow, falloff curve, cone); its
  post shaders have no curve. Run 16's floor used the `default` material,
  which turned out a mirror of the environment map; runs 17 and 18 used
  `gray` (50 % grey, black specular map) on an 80 × 80 block floor, the
  ambient and sun off or split by colour channel, one light per spot seen
  from 400 units straight above. Pixel values divided by the floor's
  albedo: 808080 gives half of ffffff, intensity 4 twice 2, a light with no
  type the same as `diffuse`, falloff 0.5 a profile flat to half the radius
  then the 2.2 curve (falloff 1 flat to the radius, 0 the curve from the
  centre, none fits 0.33), a spot of `angle` 60 lit to 30° (softness 0.1 full
  inside 15°, 0.5 a quarter at its centre), capsules along their +z (one
  turned by yaw 45°), each to within the floor texture's noise. The sun at
  rotation (60°, 30°) cast a pillar's shadow along that direction. Run 18:
  a red sun, green ambient and blue shadow ambient kept apart (black
  `shadow_color` takes the sun out of the shadow entirely); blue ambient
  nodes at intensity 1, 2 and 4 and at falloff 0 came out the same, 000040
  a fifth of 000080, a cubic node a flat square; their footprints set the
  page's fade. Run 19 compared the page with the game on `duel_bioplant`
  from five `/printcamera` points: the light falls in the same places (block
  brightness correlation 0.72 to 0.94) in the same colours, but the game is
  about 1.45 times brighter (up to 1.8 on glossy floors; a dim corner
  matched). The specular light and reflections (read from `tile.cs` after
  that) lift the page only 5 to 10 % against run 19's shots (median
  game / page block ratios 1.46, 1.29, 1.39, 1.71, 1.71 became 1.38, 1.23,
  1.27, 1.55, 1.60; the dim spot 0.96 became 0.90): most of the gap is
  elsewhere. The map has no `shadow_color`, so the page took the sun out of
  its shadow entirely, while the game's default is bluish.
- Run 20 (test maps on the grey floor, then `duel_bioplant` again from run
  19's points at yaw 90°): with no `shadow_color`, a pillar's shadow over a
  floor lit by a white sun alone (0.259, as run 18's) read 0.062, 0.104,
  0.118, so the default shadow colour is 0.238, 0.401, 0.457. On bioplant
  the game / page medians went from 1.66, 1.24, 1.09, 1.35, 2.59, 0.96
  (lights only) to 1.45, 1.19, 1.06, 1.28, 1.95, 0.90 with the specular
  light and reflections, and to 1.16, 0.85, 0.89, 1.00, 1.43, 0.89 with the
  shadow colour too (correlation 0.59 to 0.87); the fifth point's floors
  are still 1.7 times brighter in the game, beside a glowing liquid and
  intensity 4 ambient nodes. Maps with no sun get a strong light of their
  own: the floor read 0.435 with no ambient and no sun and 0.439, 0.607,
  0.740, 0.937 with ambient 101010, 606060, a0a0a0, ffffff; ambient nodes
  made no difference there, so their intensity is still unmeasured. The
  `default` mirror patch saturated (255) under ambient 404040, and two shots
  (a node and the `point` light over specular plates) came out a flat blue
  frame.
- Run 21 (sun in every map, black ambient): the `default` patch is a mirror
  of the cathedral envmap, and the page from the same four cameras (looking
  down, and at yaw 0°, 90°, 270°) shows the same vaults, pulpit and banners
  in the same places, 1.03 to 1.18 times darker. That comparison found the
  page's eye direction wrong (three gives Lambert materials no
  `cameraPosition`; it now comes from the view-space position), which had
  shown the envmap's floor in place of its ceiling. Bioplant against run 20
  after the fix: 1.18, 0.86, 0.86, 1.01, 1.53, 0.88. Ambient nodes (cubic
  at intensity 0.1, 1 and 20, spheres at 1 and 4, bright colours) changed
  nothing on the floor in this map either, though the log shows the grid
  built; run 18's nodes, in a map with a non-black ambient, did.
- Run 22 (the same nodes over ambient 404040, with and without a sun) changed
  nothing either. Run 23 found why: runs 17 and 18 had a pillar to y 600,
  runs 20 to 22 only the floor (top y 400) under nodes at y 450. With the
  pillar, or with a column as tall in a far corner, the nodes show; so the
  game drops nodes outside the blocks' bounds (centre outside; whether the
  whole sphere must be inside is not told apart; up to 3 % of the stock maps'
  nodes are outside). Intensity again changed nothing (cubic 0.1, 1, 20 and
  spheres 1, 4 alike). Runs 21 to 23 also give the floor under a sun of
  intensity 0.5, 1 (run 18) and 2: 0.039, 0.113, 0.259, linear with an
  offset, and ambient 101010, 404040, a0a0a0 with that sun: 0.075, 0.173,
  0.373, linear in the hex with the albedo as slope. All of it fits the
  LUT step above (the default `lut.png` is the identity, and (16x − 0.5) / 15
  is 1.067x − 0.033): through it the sun is colour × intensity / π (0.314,
  0.318, 0.318 at the three intensities), the ambient its hex, run 20's
  default shadow colour 0.325, 0.469, 0.519, a cubic node's colour its hex,
  and its weight on the floor the share squared; run 20's maps with no sun
  have 0.413 more light on the floor whatever their ambient (0.83 × the
  albedo). The page now gives every test floor of runs 21 to 23 within
  0.004. Bioplant against run 20 got further off where it is enclosed (1.23,
  0.65, 0.79, 0.89, 1.30, 0.78): the game's ambient occlusion is on (run 20's
  l13 floor darkens over about 27 units beside a 20-unit step; `tile.cs`
  multiplies the ambient by 1 − (1 − ao) × strength, except for id 47), and
  the page has none; the old, too low ambient fit stood in for it. Spot 2's
  floor is red with the lava's glow in the game and pale on the page.
- Run 24 (ridges 20, 60 and 200 high under a white ambient): the ambient
  occlusion is real but narrow, darkening the floor within about 19 units
  of a ridge to 0.59 beside the 20-unit one, 0.87 beside the 60-unit one
  and not at all beside the 200-unit one; too little to explain bioplant.
  Run 25: walls facing −x and −z, a ceiling and the floor under it read the
  same as the open floor, so the ambient does not depend on the surface's
  direction. The textures are BC7 without sRGB, and `tile.ps` writes the
  texel as it is.
- Run 26: point lights match the page within 1 to 4 levels (falloff 0,
  0.33, 0.8, radius 400, intensities 1 to 4, a grey light, two overlapping),
  so their fade needs no change. Overlapping nodes did not: the game takes
  all the ambient between two blue nodes that would each take 0.3; adding
  their weights fits (the page now does). Bioplant after it: 1.04, 0.68,
  0.76, 0.74, 0.96, 0.74. Dim areas are too dark on the page and bright
  ones too bright, and switching parts off on the page shows the sun
  carries the excess: without it the page is 1.04 to 1.41 of the game, and
  at spot 2 (under a roof) the game's floor shows no sun at all while the
  page's shadowed sun, times the default shadow colour, lights it yellow.
  The shader multiplies the sun by the shadow colour in shadow (with a
  fade past 6000 units toward `mHorizonShadeColor`), so either bioplant's
  default shadow colour differs from the test map's or it is not relative
  to the sun; next: bioplant's own sun, ambient and lack of shadow_color on
  a test map with a roof.

## Not done yet

Decals' cutting box tilted about x (only flat ones and walls measured), decals
with no texture in the game files (drawn not at all), props marked `no_decals` (they take decals), billboards, particles, the glowing crystals' colours
(`efferv.ps`, its own `color1` to `color3`), team colours on tinted
materials (a flag in the material picks the team's colour over an accent),
whether a
terrain material's `uv_scale` changes its repeat (`terrain_snow_blend` has
0.125; only `core_ter` was measured), the terrain's normal maps and the fields
of one map only (`tt_boost`: `offset_x`, `scale_y`, read as a shift and a
height scale untested; `mirrored`, not read), the game's choice among a
dynamic rule's variants and the rule conditions not understood (`/`, `when`,
neighbour tests such as `left empty`), skinned and ASCII FBX models, about 250
prop materials not found (flat colours; fewer since a prop's `material` X
is read as its shader's X variant), the liquids' moving noise, foam and fog; of the lighting: the lights' specular highlights are checked only
through bioplant's overall brightness, the reflections leave out the per-pixel
material id (the commonest
value of a material's id map is used), the spot lights' extra specular factor
(the shader's `mMapMax.w`, taken as 1), `video_specularity_factor`,
`reflectivity` and `skybox_rotation` (whether they turn or scale the
reflection), the reflections of the envmap's two finest mips (the page keeps
256 × 256 faces), the flat ambients of bots and pickups (ids 25, 44, 45, 48,
49, 65, 120 to 139), decals' own specular maps (a decal dims the reflection
under it) and the game's ambient occlusion; the ambient's fade toward the
shadow ambient on faces turned from the sun (the shader ramps it over N·L
below 0.1), fog and vertical fog, light volumes
(beams), flickering lights (drawn steady), colour grading (the 11 maps that
set `lut`; the page applies the identity's step), the strong light of maps
with no sun (measured in run 20, not drawn), the screen-space ambient
occlusion (narrow; not drawn), the sun's light in shadow on maps like
bioplant (the page lets far more through than the game), the game grid's
half-cell shift, liquids' light on what is around them, and
bloom, how nodes overlap and the grid's own size; the
per-face flag byte and
the six small per-face values (probably texture offset and turn), bevelled edges, version 21 maps, and editing.
