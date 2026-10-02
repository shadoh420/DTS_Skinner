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

### Filling in what the game folder lacks

A map can name a texture or shader that is in no archive of the install: a
custom map shipped without it, or a stock gap. Such files can be supplied from
an extras folder, `local-data/q3-extra` beside the pack (or `--extra folder`
on the command line), laid out like `baseq3`: `textures/...`, `scripts/...`,
or whole pk3 archives. It is searched after everything of the game and only
fills gaps: a file the game has is never replaced, and a script there defines
only shaders the game has none for. Re-import (tick **Re-import existing
maps**) after adding to it. What a map took from it is listed under that map's
Preview notes as filled in from the extras folder, and in the import's output,
so it stays clear what is the game's and what is not.

What is still missing after that is guessed, where there is something to guess
from: a shader the game cannot build is drawn as the shader, or else the
image, of the same file name in another folder under the same top folder
(`textures/steven/flame2` as `textures/sfx/flame2`), and a stage's missing
image is taken from an image of the same file name. Of several the map's own
archive is preferred, then the stock paks. Nothing says such a stand-in is the
same thing, so each is listed under that map's Preview notes as a guess, and
as `GUESSED` in the import's output. The game makes no such guess: it draws
its default image there.

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
files do not hold (a shader with neither script nor image, or a stage's
texture; either way the surface is drawn with the game's dark default image,
as the game draws it), what was filled in from the extras folder, what was
guessed from a same-named file, what is not drawn or is simplified, and
anything that failed while loading (a texture, a shader the graphics driver
rejected, a map file with no surfaces).

## How a map is resolved

References: ArenaPrototype's Quake 3 code first (`Quake3BspReader`,
`Quake3StaticWorldScene`, `Quake3StaticMaterial` and its
`Quake3StaticStage.shader`), and ioquake3 at `67e4fa9` for rules it does not
state. Where engines differ, CNQ3 (at `ef32da8`) decides, since that is the
engine the install checked here runs. All were read for behaviour; none of
their code is in the app.

- Files are searched as CNQ3 and the original game search them: a folder's
  archives from the last name to the first, then its loose files, then the
  same for `baseq3` under a mod folder (`FS_AddGameDirectory`). ioquake3 looks
  at loose files first.
- Of script files with one name only the first found is read. Of two scripts
  that define one shader the one from the higher source wins, and inside a
  file the first definition (CNQ3's `ScanAndLoadShaderFiles`, which joins the
  scripts in the order found). ioquake3 and the original game join them last
  to first, so there the lower source wins and a custom pk3 cannot replace a
  `pak0` shader. On this install the two rules give 25 shaders a different
  definition, on 47 maps.
- A shader the game cannot build gets its default shader: one with neither a
  script nor an image of its name, and one whose script names a stage image
  that is missing, whatever its other stages (`R_FindShader`,
  `ShaderForShaderNum`). The default image drawn is CNQ3's: 16 × 16, dark
  grey, with a red line along one edge, a green one along the other and a
  yellow diagonal. From any distance it is a dark surface.
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
- The game subdivides a patch by its curvature (`r_subdivisions`) and drops
  detail with distance; here every span is 4 × 4.
- All lightmaps of a map are packed into one texture, so a shader is one draw
  per stage whatever lightmaps its surfaces use. Coordinates are kept half a
  texel inside each lightmap, which is what clamping did when each was a
  texture of its own.
- The sky is drawn on the map's sky surfaces where they stand; the game draws
  it at the far limit of depth, so there geometry beyond a sky surface can
  show in front of the sky.
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
1.9 GB: 306 map files (978 MB), 9,244 images and 58 pickup models (0.8 MB).

All 306 import. In the browser, each on a fresh page:

- All 306 reach "Map ready" (half of them within 0.3 to 0.4 seconds, the
  slowest in 2.2 to 3.0 over three sweeps, most of that dropping a hundred or
  more pickups to the floor), every shader compiles and none fails to draw.
  `radianttest02`, one of the loose files, holds no surfaces and says so.
- 15,107 pickups are placed on 285 maps; every model the base game's item
  classes name is in the game files.
- Drawn at two times, 267 maps differ between them (scrolling, pulsing and
  animated shaders, and turning pickups); before pickups were added it was
  221. A map drawn twice at one time gives the same pixels.
- On `q3dm1` from its intermission camera every pixel is covered; with the
  culling reversed 42.5 % is, which is how the winding was settled. The
  doubled frame's mean is twice the undoubled one's (66.4 against 33.2).
- All 36 stock Quake III Arena maps and all 21 Team Arena maps list nothing
  unresolved. From the game folder alone, 23 maps name things that are in no
  archive of it under that name, or that the game cannot reach there:

| Map | Not in the game files | Filled from extras |
| --- | --- | --- |
| `firstrebirth`, `hektik`, `ospdm6`, `pukka3tourney4`, `q3wcp6`, `ump3ctf7` | shader `textures/radiant/notex` (the editor's "no texture") | yes |
| `q3wcp2`, `q3wcp3` | shader `textures/ssctf/s_scan`, `textures/ssctf2/s_scan` | yes |
| `spikedm9` | shader `models/mapobjects/spikedm9/cake_plate` | yes |
| `quarantine` | four shaders (`skies/meth_clouds4`, `sfx/white`, two walls) | yes |
| `absmidair` | four `textures/proto2/…` shaders and `textures/cos1/cretebase5` | all but `cretebase5` |
| `painless` | six shaders (`jk_tourney1`, `ad_content`, `mine`, `liquids`) | three; `mine/glass_mio` and two `liquids/protolava_mio…` are left |
| `q3ctfchnu01` | three shaders, among them `textures/__tb_empty` | `__tb_empty`; `ctfchnu/small_light` and `ctfchnu/porctrim8seamless-_blue` are left |
| `reactor` | 41 shaders, 38 of them `textures/bus_ca4/…` | `notex` and two Quake Live shaders; the 38 are left |
| `b0_beta6` | texture `textures/b0_final/b0_beam_blue.tga` | no |
| `bones_fkd_b1` | shader `textures/bones/pipe_test` | no |
| `caustic` | texture `textures/sfx/powerupshit.tga` (the teleporter shader) | no |
| `cpm32_b1` | shader `textures/terblend_soc/rock_grey2` | no |
| `ghosttown2` | eight `textures/steven/…` shaders | no |
| `pukka3tourney2` | shader `textures/pukka3tourney2/yellowtech1_l1_8a` | no |
| `ql_harvest` | shader `textures/null` | no |
| `runtfest` | shader `textures/nh/nh_floorplank` | no |
| `xcm_ctf4` | three `models/mapobjects/dm_statue/…` shaders | no |

  The full lists are in each map's Preview notes and in the import's output.

Two of these are conflicts inside the install, which the game has too:
`q3wpak0.pk3` and `q3wpak1.pk3` each hold a `scripts/q3wcp2.shader` and a
`scripts/q3wcp3.shader`, and five archives hold a `scripts/cake.shader`. Of
script files with one name only the last archive's is read, so `s_scan` and
`spikedm9`'s cake shaders are defined in a file the game never opens.

For the 79 names missing from the game folder the drive was searched for a
file of exactly that path, loose or inside a pk3 archive anywhere outside the
install, and for a script defining the shader. 17 were found and copied into
`local-data/q3-extra` (its `SOURCES.json` says where each came from: Quake
Live's `pak00.pk3`, ArenaPrototype's extracted map scripts, TrenchBroom's and
NetRadiant's editor textures): five images, and twelve shader definitions
with the one stage texture they needed. With them 14 maps take something from
the extras and 13 still name something unresolved: 62 names found nowhere on
the drive under their own path.

26 of those 62 have a shader or image of the same file name in another folder
and are drawn as that, as guesses, on five maps: all eight `textures/steven/…`
shaders of `ghosttown2` (as stock shaders of `liquids`, `sfx`, `common` and
others), fifteen `textures/bus_ca4/…` shaders of `reactor` (fourteen as
`textures/bus_ca1/…`), `rock_grey2` of `cpm32_b1` and the beam texture of
`b0_beta6` (each from another folder of the map's own archive), and
`small_light` of `q3ctfchnu01`. That leaves 10 maps naming 35 shaders and one
texture that nothing stands in for: `absmidair`, `bones_fkd_b1`, `caustic`,
`painless`, `pukka3tourney2`, `q3ctfchnu01`, `ql_harvest`, `reactor` (23),
`runtfest` and `xcm_ctf4`. None of the 35 shaders is defined in any script of
the install, read or hidden.

`pukka3tourney2` and `b0_beta6` were looked at closely, being maps that play
without any visible fault. Both do lack a file. `pukka3tourney2.pk3` has
`yellowtech1_l1_5a`, `_l3_4a` and `_l3_c1a` but nothing named
`yellowtech1_l1_8a`, in an image or a script, and nor has any archive on the
drive; 24 faces use it, strips 4 units high around the ceiling lights, which
under the default image are as dark as the trim beside them. In
`b0_beta6.pk3` the script's `textures/b0_final/b0_beam_blue.tga` is in the
archive as `textures/sfx/b0_beam_blue.jpg`, so the shader of five upward
faces fails and the game gives them its default image. CNQ3 handles both cases
as the other engines do; what differed was the viewer, which drew magenta for
the first and nothing for the second. With the guess above the beams are drawn
blue from the `sfx` image.

Three faults in the importer were found by this and fixed, which is what took
the count from 31 maps to 23 before any extras:

- A file that will not load is passed over for the next extension, as in the
  game. Team Arena's `pak0` holds empty `.tga` files over images the base game
  has as `.jpg` (`base_trim/pewter`, `base_light/light2`); five Team Arena
  maps listed those as missing.
- Pillow refuses some TGA files the game reads: a run-length run that crosses
  the end of a row (`ospctf1`, `q3wcp15`) and an id field ten bytes long, which
  makes the file look like a PCX (`q3wcp20`). Those are now read as
  `tr_image_tga.c` reads them.
- A shader name with a leading slash (`/textures/cpm32_b1/cthulu_v2`) finds
  its image, as the game's file system drops the slash.

What the Preview notes list as not drawn or simplified, by number of maps:
sprites that turn to the camera 103, fog volumes 100, light flares 88,
wobbling normals 69, portals and mirrors 29, noise waves 16, model lighting 3,
entity colours 1. 88 maps list nothing at all.

Looked at in the browser: `q3dm1` (also its shards and rocket launcher),
`q3ctf1` (the red flag at its base), `q3tourney2`, `cpm22`, `q3dm7` (arches and
other patches),
`q3dm15` (cloud sky, lava), `q3dm17` (no sky surfaces around it, so black
beyond the platforms), `qc_bloodrun` and `13castle` (custom; sky box, whose sides and top
meet without a seam at two opposite corners), Team Arena's `mpteam1`, and the
loose `egypttower01` (unlit, drawn by its vertex colours).

A packaged build (PyInstaller, the repository's spec, run from its own
folder) served the page, imported the install through its own import route in
42 seconds and drew `q3dm7`; its pack was the same as the source run's, file
for file. That was before pickups were added, which have not been tried in a
packaged build.

Not checked: any map against the game side by side, and every map by eye.

Tests: `python -m unittest tests.test_q3_maps` covers the script reader, the
search order and which script wins, stage defaults, the default shader for
what cannot be built and the guesses for it, the import (draw lumps,
scenes, pickups and their models, unresolved content, the extras folder,
skipping, a map file of the wrong version), the TGA and empty-file cases above
and the local routes. Set `Q3_GAME_BASE` to a Quake 3 folder to also import that
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
