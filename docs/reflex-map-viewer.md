# Reflex map viewer and brush editor

Opens Reflex Arena maps from Skinner in free-flight, and edits their brushes:
the map's brushes coloured by their faces' colours and materials, prefabs
placed where the map places them, and its entities marked. **Tab** switches
between flying and editing, as the game's editor switches between playing and
editing a map. While editing, brushes of the map are selected, made, carved,
hollowed, merged, split, moved, copied and deleted, with undo, and the map is
saved back as a Reflex `.map` file. No collision, movement physics, gameplay,
audio or baked lighting. Q3, T1 and T2 maps have their own pages, see
[q3-map-viewer.md](q3-map-viewer.md), [t1-map-viewer.md](t1-map-viewer.md)
and [t2-map-viewer.md](t2-map-viewer.md).

Unlike Quake 3, Reflex has no compile step: the game draws the map file as it
is, every brush stored as its own vertices and faces. So the page reads the
`.map` text itself (`static/reflex-maps/mapfile.js`), and the brushes it reads
are the ones the editor changes (`static/reflex-maps/brush.js`). No build
step, plain Three.js. Nothing of the game, of its editor or of the converters
below is compiled in.

## Getting maps

- **Open .map file** on the page reads a map from disk. Nothing is imported or
  kept; the map is gone when the page reloads.
- **Import maps** copies the maps of your install into
  `local-data/reflex-maps` (ignored by Git; next to the executable in a
  packaged build) so the dropdown lists them. Enter the Reflex Arena folder.
  (`steamapps/common/reflexfps` in a Steam install). A file is taken as a map
  when its first line is `reflex map version N`, wherever it lies in that
  folder (the stock maps are in `maps`); Steam Workshop maps are read from
  `steamapps/workshop/content/328070` beside the install when there is one.
  From a checkout:

  ```powershell
  python tools/import_reflex_map.py --game-base "C:/Program Files (x86)/Steam/steamapps/common/Reflex Arena" [--replace]
  ```

  The game folder is only read. The pack holds `index.json` (name, title and
  author from the map's WorldSpawn, group, source), `maps/` (the map files as
  they are, named by content) and `materials.json` (each material's albedo,
  metallic and roughness, shader and archive). All 18 maps of a stock install
  import in under a second.
- With no map chosen the page opens a new, empty map in edit mode.

### Materials and colours

The game's content is in zip archives named `.pak` in its `base` folder, one
per top folder of the names inside: `common.pak` holds
`common/materials/stone/concrete.material`, `structural.pak` the `structural/`
materials, `internal.pak` the editor's and effects'. The import reads the
material files the maps name from them (a loose `.material` under `base`
first) and keeps each one's shader, albedo, metallic and roughness. A stock
material is mostly constants, not images: `concrete` is shader
`internal/shaders/deferredPbrStylized` with albedo (0.37, 0.38, 0.35),
metallic 0 and roughness 0.8.

Material file, little-endian: `14 00 0e d0`, the shader name (128 bytes), a
32-bit flags word, the parameter count, a zero word, then 260 bytes per
parameter: its type (32 bits), name (128 bytes) and value (128 bytes). Types 0
to 3 are one to four floats (`roughness`; `uvScale`; `tintColor`; `albedo`,
`diffuseColour`), 4 a texture path (`textureAlbedoSpec`). Every stock material
file in `common`, `environment` and `structural` is 144 + 260 × count bytes.
A material's colour is its `albedo`, else its `diffuseColour` (ivy), else its
`tintColor`: the dev materials of `structural` are a grid texture tinted by it
(`dev_grey128` 0.5 grey, `dev_nogrid_red` red), so the tint stands in for the
textured colour.

A face whose colour has alpha above zero is drawn in that colour, which is
what most faces of stock maps carry (Furnace: 18 colours over 13 materials),
taken as the sRGB bytes of a colour picker. A face with colour `0x00000000`,
or none, is drawn in its material's albedo, which is linear, as the game's
physically based materials are. Shading is linear and the frame is encoded as
sRGB: diffuse light from one fixed sun and the sky, and a grey surrounding
reflected at 4 % (or in the albedo's colour for a metallic material, which
then has no diffuse colour). That is what keeps `gunmetal`, whose albedo is
almost black (0.015, 0.02, 0.02, not metallic, roughness 0.4) and which most
of Aerowalk is, a dark grey as in the game. Where the import found no albedo
for a material the page guesses a colour from the name and lists those
materials under Preview notes.

Faces of the editor's clip materials (`internal/editor/textures/editor_clip`,
`editor_fullclip`, `editor_weaponclip`) are not drawn while flying, as the game
does not draw them; while editing they show as purple glass.

## Controls

Flying: click the scene to capture the mouse or drag to look, WASD moves,
Space or E rises, Shift or Q descends, the wheel changes speed, Escape releases
the mouse and keys 1–9 jump between viewpoints: the camera the map ends on
(the Target its WorldSpawn names as `targetGameOverCamera`) first, then the
spawn points at eye height. FOV (horizontal, as in the game), invert and Reset
view are above the scene.

Editing (**Tab**): a click selects a brush of the map, Shift-click adds or
removes one, Escape clears the selection; the right button looks, WASD/QE
move. The toolbar and keys:

| Tool | Key | What it does |
| --- | --- | --- |
| Grid | | Snap and step size, 1 to 64 units; drawn on every surface while editing, with a heavier line every 8 steps |
| New box | B | A box 8 grid steps wide, 256 units in front of the camera, on the grid, with the faces of the brush selected last |
| Subtract | Ctrl+Shift+S | Carves the selected brushes out of every other brush of the map they overlap; the selection stays |
| Hollow | H | Turns the selected brush into walls one grid step thick that do not overlap |
| Merge | Ctrl+M | Joins the selected brushes into one, when together they make a convex brush |
| Split | C | Cuts the selected brushes on the grid plane through the point clicked last, across the axis the camera faces most |
| Move | Arrows, PgUp/PgDn | Moves the selection one grid step along the horizontal axis nearest the view, or up and down |
| Duplicate | Ctrl+D | Copies the selection one grid step along x |
| Delete | Delete | |
| Undo / Redo | Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y | The last 100 edits |
| Save .map | Ctrl+S | Downloads the map as a Reflex map file |

Brushes placed by a prefab are drawn but not selected: clicking one names its
prefab. A brush that is not convex or has a bent face (the game's editor can
make one by moving a vertex; 33 brushes of the stock maps are such) is drawn,
and the CSG tools leave it alone.

## The map file

Checked against Aerowalk and Furnace (stock, version 8), fourier's
[reflex-map](https://github.com/fourier/reflex-map) (its `examples/cube.map`
and parser), chronokun's
[ReflexToQ3](https://github.com/chronokun/ReflexToQ3) parser and a
[PEG.js grammar of version 6](https://gist.github.com/c89c79df2e3471220745).

Text, lines ending CR LF, blocks by tab indentation:

```
reflex map version 8
prefab mini_spot
	entity
		type WorldSpawn
	brush
		vertices
			-16.000000 -2.000000 8.000000
			…
		faces
			0.000000 0.000000 1.000000 1.000000 0.000000 0 1 2 3 0xff332805 common/materials/wood/bare
			…
	entity
		type Effect
		Vector3 position -8.000000 -2.000000 0.000000
		String64 effectName industrial/lights/light_spot_sml
global
	entity
		type WorldSpawn
		…
```

- **Groups.** `prefab <name>` blocks come first, then `global`, the map
  itself. Each holds entities and brushes in the order they were made,
  interleaved (Aerowalk's global: 1 entity, 1,596 brushes, 20 entities, 3
  brushes, …), so the page keeps them as one list per group. Version 6 files
  have no group lines: entities and brushes stand at the top level.
- **Entities**: `type <Type>`, then typed properties, `<Type> <name> <value>`:
  `Vector3` (three numbers), `Float`, `UInt8`, `UInt32`, `Bool8`,
  `ColourXRGB32` / `ColourARGB32` (eight hex digits, no `0x`), and `String32`
  / `String64` / `String256`, whose value is the rest of the line and may hold
  spaces (`ownerString TurboPixel + Hubster + Preacher`). Types in the two
  stock maps: WorldSpawn (one per group; sky, fog, title, owner, modes),
  Prefab, Effect (a model or light of the game by `effectName`, with
  `effectScale` and per-material albedo overrides), Pickup (`pickupType`),
  PlayerSpawn, Teleporter and Target (`target` / `name`), JumpPad, PointLight,
  ReflectionProbe, NavLink, CameraPath, WorkshopScreenshot.
- **Brushes**: `vertices`, one `x y z` per line, then `faces`, one per line:
  offset u, offset v, scale u, scale v, rotation (degrees), the face's vertex
  indices (3 to 8 in the stock maps, mostly 4), a colour `0xAARRGGBB` and the
  material. The material may be empty: the line then ends in a space after the
  colour. Version 6 has no colour.
- **Numbers** are written with six decimals, a negative zero with its sign
  (`-0.000000` appears in most maps), and from 10¹⁷ up with 17 significant
  digits and then zeros, as the game's C runtime writes them (AbandonedShelter
  has a face turned by `1602806319568810500000000.000000` degrees). The page
  writes what it read back byte for byte: all 18 stock maps come back
  identical.
- **Axes**: y is up. The converters to Quake swap y and z and the result is not
  mirrored, so the game's axes are left-handed; the page shows them under a root
  mirrored on z. Faces are wound counter-clockwise seen from outside, taking the
  right-hand rule on the numbers as written (outward normal
  `(b − a) × (c − b)`), which is the winding the page writes new faces in.
- **Prefabs** are placed by a Prefab entity's `prefabName`, `position` and
  `angles`, recursively. Names are matched without regard to case, as the
  game matches them: SkyTemples defines `tower_1` and places `Tower_1`, and
  Hieratic six more like it. Angles are degrees, yaw first, about y. The sign of
  yaw was settled on Aerowalk: with yaw turning +z toward +x its `panel_tall`
  copies at −90° meet their wall at 66 of 160 vertices and none sink into it,
  against 18 and 8 the other way. Pitch and roll are applied as reflex-map's
  `export-prefab` applies them (yaw, then roll, then pitch), which is
  unconfirmed: Ashur, Fusion and Hieratic tilt 30 placements, but scoring all
  six orders and four sign pairs by how their 3,818 vertices meet the map's
  brushes separates none, as most of them are rocks and props sunk into the
  ground whichever way they turn.
- 33 brushes of the 18 stock maps are not convex or have bent faces (30 on
  Ironguard, 2 on AbandonedShelter, 1 on Aerowalk, the rest none), and 3 on
  Ironguard have a face with no area.

Beside each map the game keeps `<map>.light` (version 2; a grid of 64-unit
cells over the map's bounds, 42 × 16 × 27 on Aerowalk with about 85 bytes per
cell, which looks like baked light probes), `<map>.nav` (bot navigation,
`VAND` chunks) and `<map>.preview` (name, author, then the picture). The page
reads none of them yet.

## How the CSG works

`brush.js` keeps a brush as the map stores it and works through its planes, as
Quake's and Radiant's brush CSG does: a convex brush is where every face plane
has it behind, and a brush is rebuilt from planes by clipping a large square
on each plane by all the others (`BaseWindingForPlane`, `ChopWinding`), so a
face's other fields (material, colour, texture offset, scale, rotation) carry
through every operation. Subtract cuts a brush by each plane of the cutter in
turn, keeping what is outside and carrying on with what is inside, which ends
up dropped (Quake's `SubtractBrush`); the faces of the hole take the cutter's
fields. Hollow is the brush less itself shrunk by the wall thickness, so the
walls do not overlap. Merge takes the convex hull of both and keeps it only
when its volume is the volume of the union. Node tests check each, and that
subtract conserves volume (the pieces and the overlap add up to the brush) for
random convex brushes.

## Limits and what comes next

- **Textures**: faces are drawn flat in their colour, lit by one fixed sun and
  the sky, with the editor's grid while editing. How the game maps the face's
  offset, scale and rotation to texture coordinates has not been checked; the
  page keeps those values and writes them back unchanged.
- **Effects** (the game's models, by `effectName`) are marked as small grey
  points while editing, not drawn. Sky, fog and the baked light are not drawn;
  the background is the WorldSpawn's horizon colour.
- **Editing** works on the brushes of the map (`global`), not on prefabs or
  entities. There is no face selection, texture tool, vertex editing or
  clipper of three points yet, and Split cuts only on grid planes square to
  the axes. `brush.js` already has the arbitrary-plane clip and a ray test the
  page can build a three-point clipper and face picking on.
- **Play mode**: flying stands in for the game's play mode. Movement with
  collision against the brushes (the planes `brush.js` keeps are what a
  Quake-style player trace needs) is the next step toward it.
- Viewpoint pitch is taken to look down when positive; no map checked uses
  one.

## Checks

- `node --test tests/reflex_maps.test.cjs`: brush CSG and the map file reader
  and writer. Set `REFLEX_MAPS` to a folder of `.map` files to also read each,
  write it back unchanged and place its prefabs.
- `python -m unittest tests.test_reflex_maps`: the import, the routes, and the
  Node tests when Node is installed. Set `REFLEX_GAME_BASE` to a Reflex Arena
  folder to import that install and run the Node tests over its maps.
- `node tools/check_reflex_maps.cjs http://127.0.0.1:5000 build/reflex-review`:
  in a hidden browser, makes boxes in a new map, carves, deletes, undoes,
  redoes and hollows with the keyboard and mouse, saves and reads the download
  back; then draws the first imported map and checks it is not blank.

Checked on 2026-10-02 with the 18 maps of a stock install (Steam folder
`reflexfps`): AbandonedShelter, Aerowalk, Ashur, empty, forge, furnace,
Fusion, Hieratic, ironguard, Phobos, Ruin, SkyTemples, TheCatalyst and the
five training stages. Every one reads and writes back identical and places
every prefab it names (Hieratic, the largest: 8,539 brushes, 4,192 of them
from prefabs, drawn in about a second in headless Chromium). The import ran
against a stand-in install with those maps and the stock `common.pak`, which
and `common.pak`, `environment.pak` and `structural.pak`, which gave 46 of the
55 materials the maps name; the other 9 are in `internal.pak`, which was not
at hand. Not
checked: any map against the game side by side.
