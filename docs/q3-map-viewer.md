# Offline Q3 map viewer

Opens the Quake 3 Arena maps of a local install from Skinner in free-flight:
the map's surfaces with their lightmaps, curved patches, and the shader
scripts' stages (blending, alpha test, scrolling, rotating, stretching and
turbulent textures, pulsing colours, animated textures, environment mapping,
waving and bulging surfaces), sky boxes and cloud layers, and the map's
pickups (weapons, ammo, armour, health, powerups, holdables and flags) turning
and bobbing where the game puts them. No collision, gameplay, audio or map
editing. T1 and T2 maps have their own pages, see
[t1-map-viewer.md](t1-map-viewer.md) and [t2-map-viewer.md](t2-map-viewer.md).

Like the T1 page this needs no build step: plain Three.js
(`static/q3-maps/`) drawing the compiled `.bsp` files, from a pack that
`tools/import_q3_map.py` writes from your own game install. Nothing from
q3edit, a Radiant or the game's source is compiled in.

## Importing maps

In the app: **Q3 Maps** in the model sidebar, then **Import maps**. Enter your
Quake 3 Arena folder (or its `baseq3` folder). Every `maps/*.bsp` is imported:
those of `baseq3` and of each mod folder beside it that has pk3 archives
(`missionpack`, `defrag`, …), from the archives and from loose files. Maps
already imported are skipped unless **Re-import existing maps** is ticked. From
a checkout the same import runs as:

```powershell
python tools/import_q3_map.py --game-base "C:/Games/Quake 3 Arena" [--replace]
```

The game folder is only read. Output goes to `local-data/q3-maps` (ignored by
Git; next to the executable in a packaged build):

- `index.json` lists the maps (name, long name from the `.arena` scripts,
  group, the archive each came from, how many things it names that are missing).
- `maps/<id>/scene.json` holds, for each shader a drawn surface of the map
  uses, its stages; the map's viewpoints and pickups; brush models an entity
  moves; and the two lists shown under Preview notes.
- `models/` holds the first frame of each pickup model the maps place.
- `bsp/` holds each map file cut down to the lumps the viewer reads (entities,
  shaders, models, vertices, indices, faces, lightmaps), still a valid map
  file, and `textures/` the images, JPEG as they are and TGA as PNG. Both are
  named by content, as the models are, so maps that share a file store it
  once and the browser keeps them.

The dropdown groups the maps: Quake III Arena (`pak0`–`pak8`), Team Arena and
other mod folders, Custom maps (any other pk3 in `baseq3`), and Loose files in
maps folder (a `.bsp` lying in `baseq3/maps`, which is anything you compiled
there, a level or not).

## Controls

Pick a map from the dropdown; the choice is kept in the address as `?map=id`
and loads a fresh page, as the other two viewers do. Click the scene to capture
the mouse or drag to look, WASD moves, Space rises, Shift descends, the wheel
changes speed, Escape releases the mouse and keys 1–9 jump between the map's
viewpoints: its intermission camera first, then spawn points. FOV, **Animate
shaders** (off holds scrolling, pulsing and animated textures still),
**Invert horizontal** / **Invert vertical** and Reset view are above the
scene. While a map loads, each surface is a green wireframe until its textures
have arrived, as the T1 terrain is.

**Preview notes** lists, for the map in view: what it names that the game
files do not hold (a shader with neither script nor image, drawn magenta; a
stage's texture, that stage left out), what is not drawn or is simplified, and
anything that failed while loading (a texture, a shader the graphics driver
rejected, a map file with no surfaces).

## How a map is resolved

References: ArenaPrototype's Quake 3 code first (`Quake3BspReader`,
`Quake3StaticWorldScene`, `Quake3StaticMaterial` and its
`Quake3StaticStage.shader`), and ioquake3 at `67e4fa9` for rules it does not
state. Both were read for behaviour; none of their code is in the app.

- Files are searched as ioquake3 searches them: loose files of a folder, then
  its archives from the last name to the first, then the same for `baseq3`
  under a mod folder (`FS_AddGameDirectory`).
- Of two scripts that define one shader, the one from the lower source wins,
  unless the two script files share a name; inside a file the first definition
  wins (`ScanAndLoadShaderFiles`, `FindShaderInShaderText`). A custom pk3
  therefore cannot replace a `pak0` shader from a script of another name.
- A surface whose shader has no script gets the image of that name: under the
  lightmap, or coloured by its vertices where the surface has no lightmap
  (`R_FindShader`). Surfaces of a shader flagged no-draw in the map file are
  skipped, as in `tr_bsp.c`.
- Stage defaults are the game's (`ParseStage`, `FinishShader`): colour is
  identity lighting unless the stage blends with something other than
  `GL_ONE` or `GL_SRC_ALPHA`; a blended stage does not write depth unless it
  says `depthWrite`; sort comes from `sort`, sky, `polygonOffset` or the first
  stage's blend. Shaders draw in sort order, each one's stages in sequence.
- Images are found under the name given, then as `.tga`, `.jpg`, `.jpeg`,
  `.png` (ArenaPrototype's order).
- Patches are grids of quadratic Bézier spans, each drawn as 4 × 4 quads with
  position, normal, texture and lightmap coordinates and colour interpolated
  (ArenaPrototype's default tessellation).
- Brightness follows the convention ArenaPrototype uses (the game's defaults
  `r_mapOverBrightBits 2`, `r_overBrightBits 1`): lightmaps and vertex colours
  are doubled, a channel that overflows scaling the colour down to keep its
  hue (`R_ColorShiftLightingBytes`); identity lighting is one half; and the
  finished frame is shown doubled, which is what the game's gamma table does.
- Stages are drawn into a frame that keeps alpha, because 586 stages in this
  install blend with `GL_ONE_MINUS_DST_ALPHA`.
- Texture coordinates, waves, vertex deformation, the specular highlight
  (with the game's one fixed light position) and the portal fade follow
  `tr_shade_calc.c`. A sky's cloud layers use the game's dome mapping
  (ArenaPrototype's `NativeCloudTexCoord`), and the six sides of a sky box are
  placed as `MakeSkyVec` places them.
- Coordinates stay the game's (x, y, z up) under one rotation to the viewer's
  axes; front faces wind clockwise, as in the game.
- Pickups are the entities whose class is in the game's item list, with the
  models ArenaPrototype's `Quake3ItemCatalog` gives each. They are shown as
  `cg_ents.c` `CG_Item` shows them: one turn in 2.048 seconds (health items
  twice as fast), bobbing 4 ± 4 units, weapons half as large again and turning
  about the middle of their bounds, and the second model of a health item or
  powerup (its sphere or ring) turning the other way, a powerup's 12 units up.
  An item not marked suspended is dropped to the floor (`g_items.c`
  `FinishSpawningItem`). Classes the base game does not have (Team Arena's,
  and the green armour mods add) are placed where the game folder has their
  models and passed over where it has not, as the base game does not spawn
  them.

## Where this departs from the references

- ArenaPrototype refuses a map with a shader directive it does not support or
  a missing resource. Here the map is drawn with what resolves and the rest is
  listed under Preview notes.
- The game gives up on a whole shader when one stage's image is missing and
  draws its default image; here the other stages are still drawn. A shader
  with neither script nor image is magenta, not the game's grey default.
- The game subdivides a patch by its curvature (`r_subdivisions`) and drops
  detail with distance; here every span is 4 × 4.
- All lightmaps of a map are packed into one texture, so a shader is one draw
  per stage whatever lightmaps its surfaces use. Coordinates are kept half a
  texel inside each lightmap, which is what clamping did when each was a
  texture of its own.
- The sky is drawn on the map's sky surfaces where they stand; the game draws
  it at the far limit of depth, so there geometry beyond a sky surface can
  show in front of the sky.
- Loose files are found before archives, ioquake3's order. The original 1.32
  game looks in archives first.
- The whole map is drawn, culled only by the view per shader; the game also
  culls by its visibility data. Lightmap and texture are drawn as two passes,
  as the game does without multitexture.
- Brush models (doors, lifts) stand where the map places them and do not move.
- The game drops an item's 30-unit box until it rests on something solid.
  Here one line from the item's centre down to the nearest opaque drawn
  surface stands in for that, since the pack keeps no collision data. On
  `q3dm1` 17 of the 18 items come to rest within 3 units of where the map put
  them and one 9 units lower.
- Every pickup is placed, whatever game type its entity is limited to
  (`notfree`, `notteam`, `gametype`). Pickup models are lit evenly, not from
  the map's light grid as in the game, and a weapon's separate barrel model is
  not added.

## Coverage

Checked on 2026-10-02 against the install at
`C:\Program Files (x86)\Steam\steamapps\common\Quake 3 Arena`: 306 maps (36 in
`pak0`–`pak8`, 239 in other archives of `baseq3`, 8 loose `.bsp` files, 21 in
`missionpack`, 2 in `defrag`). The import takes 43 seconds and the pack is
1.9 GB: 306 map files (978 MB), 9,100 images (898 MB) and 58 pickup models
(0.8 MB).

All 306 import. In the browser, each on a fresh page:

- All 306 reach "Map ready" (half of them within 0.39 seconds, three take
  over 2 and the slowest 3.0, most of that dropping a hundred or more pickups
  to the floor), every shader compiles and none fails to draw.
  `radianttest02`, one of the loose files, holds no surfaces and says so.
- 15,107 pickups are placed on 285 maps; every model the base game's item
  classes name is in the game files.
- Drawn at two times, 267 maps differ between them (scrolling, pulsing and
  animated shaders, and turning pickups); before pickups were added it was
  221. A map drawn twice at one time gives the same pixels.
- On `q3dm1` from its intermission camera every pixel is covered; with the
  culling reversed 42.5 % is, which is how the winding was settled. The
  doubled frame's mean is twice the undoubled one's (66.4 against 33.2).
- All 36 stock Quake III Arena maps list nothing unresolved. 31 maps name
  things that are in no archive of the install under that name:

| Map | Not in the game files |
| --- | --- |
| Team Arena `mpq3tourney6`, `mpteam2`, `mpteam5`, `mpteam7` | shader `textures/base_trim/pewter` |
| Team Arena `mpq3ctf4`, `mpteam7` | texture `textures/base_light/light2.tga` (three light shaders) |
| `firstrebirth`, `hektik`, `ospdm6`, `pukka3tourney4`, `q3wcp6`, `reactor`, `ump3ctf7` | shader `textures/radiant/notex` (the editor's "no texture") |
| `absmidair` | five `textures/proto2/…` and `cos1` shaders |
| `b0_beta6` | texture `textures/b0_final/b0_beam_blue.tga` |
| `bones_fkd_b1` | shader `textures/bones/pipe_test` |
| `caustic` | texture `textures/sfx/powerupshit.tga` (the teleporter shader) |
| `cpm32_b1` | shaders `/textures/cpm32_b1/cthulu_v2`, `textures/terblend_soc/rock_grey2` |
| `ghosttown2` | eight `textures/steven/…` shaders |
| `ospctf1` | `textures/wnoise/…` textures of several shaders |
| `painless` | six shaders (`jk_tourney1`, `mine`, `ad_content`, `liquids`) |
| `pukka3tourney2` | shader `textures/pukka3tourney2/yellowtech1_l1_8a` |
| `q3ctfchnu01` | three shaders, among them `textures/__tb_empty` |
| `q3wcp15` | `textures/revolution_ctf/…` textures of several shaders |
| `q3wcp2`, `q3wcp3` | shader `textures/ssctf/s_scan`, `textures/ssctf2/s_scan` |
| `q3wcp20`, `spikedm9`, `xcm_ctf4` | shaders of map models (`o3-coffin`, `cake_plate`, `dm_statue`) |
| `ql_harvest` | shader `textures/null` |
| `quarantine` | four shaders (`skies/meth_clouds4`, `sfx/white`, two walls) |
| `runtfest` | shader `textures/nh/nh_floorplank` |

  The full lists are in each map's Preview notes and in the import's output.

What the Preview notes list as not drawn or simplified, by number of maps:
sprites that turn to the camera 103, fog volumes 100, light flares 88,
wobbling normals 69, portals and mirrors 29, noise waves 16, model lighting 3,
entity colours 1. 88 maps list nothing at all.

Looked at in the browser: `q3dm1` (also its shards and rocket launcher),
`q3ctf1` (the red flag at its base), `q3tourney2`, `cpm22`, `q3dm7` (arches and
other patches),
`q3dm15` (cloud sky, lava), `q3dm17` (no sky surfaces around it, black as in
the game), `qc_bloodrun` and `13castle` (custom; sky box, whose sides and top
meet without a seam at two opposite corners), Team Arena's `mpteam1`, and the
loose `egypttower01` (unlit, drawn by its vertex colours).

A packaged build (PyInstaller, the repository's spec, run from its own
folder) served the page, imported the install through its own import route in
42 seconds and drew `q3dm7`; its pack was the same as the source run's, file
for file. That was before pickups were added, which have not been tried in a
packaged build.

Not checked: any map against the game side by side, and every map by eye.

Tests: `python -m unittest tests.test_q3_maps` covers the script reader, the
search order and which script wins, stage defaults, the import (draw lumps,
scenes, pickups and their models, unresolved content, skipping, a map file of
the wrong version) and the local routes. Set `Q3_GAME_BASE` to a Quake 3 folder to also import that
install and require its stock maps to resolve completely.

## Limits

Free-flight only. Not drawn: fog volumes, light flares, portal and mirror
views (the surface is drawn plain), video textures, inner sky boxes, and
player models. Map objects other than pickups that the game adds while it runs
(Team Arena's obelisks and skulls, for one) are not placed. Simplified: `deformVertexes`
autosprite, normal, text and projection shadow are left out, a `noise` wave
is drawn as a sine, texture coordinates from vectors and entity colours are
left at their defaults. A stage that takes lightmap coordinates for an image
of its own on a surface that also has a lightmap reads the packed lightmap's
coordinates. Brightness is the game's default setting, not adjustable.
