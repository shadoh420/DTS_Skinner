# Reflex map viewer and brush editor

Opens Reflex Arena maps from Skinner to walk through, and edits their
brushes: the map's brushes coloured by their faces' colours and materials and
textured where their material is, prefabs placed where the map places them,
and its entities marked. **0** (or Tab) switches between playing and editing,
as the game's editor does. Playing, a player box walks, jumps and climbs
stairs through the map as Quake 3 moves one, and teleporters and jump pads
work. While editing, brushes of the map are selected, made, carved, hollowed,
merged, split, clipped, mirrored, moved, turned, copied and deleted, their
textures chosen from the game's materials or any of Skinner's texture
libraries, moved, scaled and turned, prefabs made, broken, updated and edited
in place, with undo, and the map is saved back as a Reflex `.map` file. No
weapons, items, gameplay, audio or baked lighting. Q3, T1 and T2 maps have their own pages, see
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

Faces of see-through materials are drawn faintly, after everything else and
from both sides: those whose shader is a light beam (`alphaFresnel`, as
`internal/effects/lights/fx_light_beam`), glass, race start and finish,
pickup and powerup glows, and water. Their own effects (fresnel, clouds,
refraction) are not drawn. The import keeps every material it finds with its
shader for this, also those it finds no colour for.

### Textures

The game keeps its textures in `.textureset` files and `.dds` files in the
same archives, and a material names them by bare name: `dev_grey128` has
`textureAlbedoSpec dev_grid16_albedospec`, `textureMeta dev_grid16_meta` and
`textureNormals dev_grid16_normals`, which are images of
`structural/dev/dev_grid16.textureset`; ivy's `textureDiffuse ivy_leaf_1_c` is
`environment/veg/ivy/ivy_leaf_1_c.dds`. So the game finds a texture by that
name wherever it lies, and the import does the same
(`tools/reflex_textures.py`).

A `.textureset` (little-endian): `20 00 0f d0`, its name (128 bytes), the
image count, a zero, then a table of 16 entries of 156 bytes from offset 140:
the image's name (128 bytes), the offsets of up to three copies of it
(`0xffffffff` for none), a mip count, a format number, width and height. The
copies are the same picture compressed for different quality settings:
`dev_grid16_albedoSpec` is there as BC1 sRGB and as BC7 sRGB. Each copy starts
with eleven 32-bit words (width, height, mip count, 1, its DXGI format, 1, 0,
1, bytes per 4 × 4 block, 0, 0) and its mips follow, largest first. Every one
of the 129 material thumbnails of `thumbs_material.pak` is one 256 × 256 BC1
image of the material on a sphere. The import decodes the best copy Pillow can
read (BC7, BC3, BC2, then BC1; sRGB formats Pillow does not name are the same
blocks under the plain name).

Most world materials of the stock packs are not textured at all: concrete,
gunmetal and the rest of `common` are a shader with an albedo, metallic and
roughness (`deferredPbrStylized`). The textured ones in the packs checked are
the 72 dev materials of `structural` (one grey albedo tinted by the
material's colour, with the grid in the meta texture), ivy, leaves and the
vegetation atlas; 69 materials in all, of which the 18 stock maps use 7, all
dev materials, on 221 faces. The theme packs (`ancient_japan`, `gothic`, `industrial`,
…) were not available to check and may hold more.

For each textured material the import writes one picture into
`local-data/reflex-maps/textures`: the albedo, darkened by the meta texture's
blue channel where there is one. In the dev grids that channel is 1 with 0.6
along the grid lines (and the normal map bevels them), so it is taken as
ambient occlusion; what the game's shader does with it is not known. A diffuse
texture keeps its alpha (ivy is alpha-keyed); an albedoSpec's alpha is its
specular level and is left out. Thumbnails go to `thumbs`, 128 pixels. The
page draws a textured face in its texture times its colour (the material's
tint, or the face's own colour), repeating every 128 units at scale 1 for the
game's textures: the dev grid's eight cells across are then 16 units, as
`dev_grid16` says. That scale, like the projection of the texture onto the
face (see the texture keys below), is a guess.

**Skinner's texture libraries.** The textures the import decodes are a
fourth library beside the T1, T2 and Q3 ones: on the model page, *Reflex
textures* reskins any model with them, and its exports include them. The
other way round, a brush face can take any texture of any library: the
Reflex page's material browser (Materials, or `me_activematerial`) lists the
game's materials with their thumbnails and each library's textures, and a
face that takes one gets the material `skinner/<library>/<file>`
(`skinner/t1/alientree`). The page draws it at its size in pixels over two
units, as Quake 3's default scale of 0.5 has it.

The game does not know `skinner/t1/alientree`. **Put the map's library
textures into the game** (in the material browser, when the map uses some)
writes, for each, `base/skinner/<library>/<file>.material` and the texture
beside it, into the Reflex Arena folder named under Import maps, and nowhere
else. The material is a stock dev material's: its shader
(`deferredPbr_TEXTUREALBEDOSPEC_TEXTUREMETA_TEXTURENORMALS_TINTED`, flags
`0x11b`), its flat normals and meta (`dev_nogrid_normals`, `dev_nogrid_meta`),
a white tint, and the library picture as its albedo, named
`skinner_<library>_<file>_c` because the game finds textures by bare name. The
texture is a `.dds` as the game's own are (BC1, or BC3 where the picture has
see-through pixels), scaled to powers of two up to 1024, with its mips. The
material writer gives back 446 of the 456 stock material files byte for byte.
**Not confirmed:** whether the game reads materials and textures from loose
files under `base` (its `myskins` folder holds loose `.dds` weapon skins, so
it reads some), and whether such a material then draws as intended. Try one
in the game before relying on it.

Faces of the editor's clip materials (`internal/editor/textures/editor_clip`,
`editor_fullclip`, `editor_weaponclip`) are not drawn while flying, as the game
does not draw them; while editing they show as purple glass.

## Controls

Playing (the page opens a map so): click the scene to capture the mouse or
drag to look, WASD walks, Space jumps, Escape releases the mouse, and keys
1–9 put the player at the map's spawn points. The player starts where the
camera is (as the game's play mode starts where the editor's camera was), or
at the first spawn point when a map opens, and comes back there after falling
out of the map. **F** flies instead (WASD, Space or E up, Shift or Q down, the
wheel for speed, and 1–9 also the camera the map ends on, the Target its
WorldSpawn names as `targetGameOverCamera`); F again walks. FOV (horizontal,
as in the game), invert and Reset view are above the scene.

**Movement** (`static/reflex-maps/movement.js`) is Quake 3's, not Reflex's
(which is CPMA's, with air control and more): a box 30 wide and 56 tall with
the eye 26 above its middle, 320 units a second, ground acceleration 10 and
friction 6, air acceleration 1, gravity 800, a jump of 270 units a second,
steps up to 18 units and slopes up to about 45 degrees, run 125 times a
second. Collision is Quake 3's box trace: each brush is its face planes,
pushed out by the box along each normal, plus a plane square to each axis
where it has none (its axial bevels; the edge bevels Quake 3's compiler adds
are not, so a box can catch slightly on the outside of a slanted edge), and
the player slides along what it meets. A brush is passed through when it is a
volume, has a liquid face (`liquids/`) or is all weapon clip; player clip and
full clip stop the player, as in the game. A teleporter puts the player at its
Target, facing the Target's yaw, at the speed it had; a jump pad throws it so
it lands on its Target at the top of its arc (Quake 3's `AimAtTarget`). On
Hieratic, the largest stock map, a step of movement takes about 0.05 ms and
the collision grid about 60 ms to build, which happens when play starts after
an edit.

Editing (**0**, as in the game, or Tab) follows the game's editor binds, read
from `game_default.cfg` of the install (its `bind me …` lines): the same
first-person view, everything done at the mouse, with keys held to change what
a drag does rather than separate tools.

| Do | Mouse / key | Game's command (key) |
| --- | --- | --- |
| Switch flying / editing | 0 (or Tab) | `toggleeditor` (0), `cl_playerstate 1` (0) |
| Select a brush or an entity's marker | Click | `+editorprimary` (Mouse1) |
| Add or remove one | Ctrl+click | `+editormultiselect` (Ctrl) |
| Look | Hold the right button | `+editorcameradrag` (Mouse2) |
| Move the selection on the grid, at the height it was taken | Drag a selected brush | `+editorprimary` |
| Move it up and down | Alt+drag | `+editorvertical` (Alt) |
| Push or pull one face (resize) | Shift+drag a face of a selected brush | `+editorfacemode` (Shift) |
| Clone | G | `editorclone` (G) |
| Delete | Backspace | `editordelete` (Backspace) |
| Undo / redo | Z / X (or Ctrl+Z, Ctrl+Y) | `editorundo` (Z), `editorredo` (X) |
| Choose what a click makes: brush, teleporter, jump pad, target, effect, pickup, point light, player spawn | 1–8 (again, or Escape, to stop) | `me_createtype` (1–8) |
| Make a brush, or a teleporter's or jump pad's volume | Drag a rectangle on a surface (or on the ground, y = 0) | `+editorprimary` |
| Place a target, effect, pickup, point light or player spawn | Click a surface | `+editorprimary` |
| Show the corners of the selected brushes; drag one (Alt: up and down) | V | `editortogglevertexmode` (V) |
| Pick a face (for the texture keys, Shift+M and the bridge); add or remove one | Shift+click; Ctrl+Shift+click | `+editorfacemode` (Shift) |
| Bridge the picked face to the face aimed at; steps; make it | B, then the wheel, then click | `me_startbridge` (B), `me_segments_inc/dec` (wheel) |
| Properties of the selection, or of the map | N | `me_showproperties 1` (N) |
| Turn the selection about the vertical through its middle, by the angle step | Numpad + / − | `me_rotate_inc` / `me_rotate_dec` (numpad + / −), `me_snapangle` |
| Move the texture of the face under the cursor (or the face Shift-click picked) a grid step | Arrows | `me_texcoords_inc/dec_offset_x/y` (arrows) |
| Scale it up / down along u; along v | Home / Insert; End / Delete | `me_texcoords_inc/dec_scale_x/y` |
| Flip it along u; along v | PgUp; PgDn | `me_texcoords_flip_scale_x/y` |
| Turn it by the angle step | `,` / `.` | `me_texcoords_dec/inc_rotation` |
| Clip mode: click two or three points of a plane; Enter clips, Shift+Enter splits, the wheel (or Ctrl+Enter) flips the side kept | C | `editortoggleclipmode` (C) |
| The console, for commands with no key (prefabs and the rest below) | ` | the game's console |
| Pick up the material and colour under the cursor | K | `me_getmaterial` (K) |
| Put them on the selection; on the picked faces, or else the face under the cursor | M; Shift+M | `me_setmaterial` (M) |
| Edit a placed prefab in place | Double click it | |
| Leave a mode, or clear the selection | Escape | |
| Fly | WASD, Q / E down and up | |
| Play | 0 | `cl_playerstate 1` (0) |

The toolbar shows the material K picked (or that of the face clicked last)
and holds what the game's editor did not have:

| Tool | Key | What it does |
| --- | --- | --- |
| Grid | | Snap and step size, 1 to 64 units, 16 to start with as the game's `me_snapdistance`; drawn on every surface while editing, with a heavier line every 8 steps |
| Angle | | The step numpad + and − turn the selection by and `,` and `.` turn a texture by (the game's `me_snapangle`): 1 to 90 degrees, 45 to start with (the game's own default is not known; 45 is the step most angles of the stock maps are on) |
| ⟲ ⟳ | Numpad − + | Turn the selection |
| Mirror | | Mirrors the selection left to right as the camera sees it (across x or z, whichever is nearer the view's right), about its middle; Shift+click, upside down; `me_mirror x\|y\|z` |
| New box | | A box 8 grid steps wide, 256 units in front of the camera, on the grid, with the picked material (1 and a drag on a surface makes one where wanted) |
| Properties | N | Opens the property panel |
| Subtract | Ctrl+Shift+S | Carves the selected brushes out of every other brush of the map they overlap; the selection stays |
| Hollow | H | Turns the selected brush into walls one grid step thick that do not overlap |
| Merge | Ctrl+M | Joins the selected brushes into one, when together they make a convex brush |
| Split | | Cuts the selected brushes on the grid plane through the point clicked last, across the axis the camera faces most |
| Clip | C | Clip mode, as below |
| Materials | | The material browser: the game's materials (with their thumbnails) and the textures of Skinner's libraries, with a search; a click chooses what M puts on, a double click also puts it on the selection |
| Prefabs | | The map's prefabs, with how many placements each has: make one from the selection, break the selected placements, edit one in place, update a prefab from the selection, place one or select its placements |
| Console | ` | Runs editor commands, below |
| Nudge | Shift+arrows, Shift+PgUp/PgDn | Moves the selection one grid step along the horizontal axis nearest the view, or up and down (without Shift, as in the game, these keys move a face's texture) |
| Save .map | Ctrl+S | Downloads the map as a Reflex map file |

Every edit is one step of undo (the last 100), and undoing brings back the
selection with it. While dragging, the yellow outline shows where the
selection goes; releasing makes the edit. A face pulled through its brush
stops at the last shape that is one.

**Creating.** A dragged brush or volume stands on the rectangle drawn, on the
grid, and grows out of the surface by four grid steps; Shift-drag its faces to
size it. A new brush takes the material K picked (concrete to start with). A
teleporter or jump pad is an entity followed by the brush that is its volume,
with no material, as the stock maps have them; it is made without a target,
which links it to a Target named the same: set it in the property panel. Entities clicked onto a floor stand where clicked; onto a wall or
ceiling, one grid step out from it. Each gets the properties every one of its
type has in the 18 stock maps, with their most common values: a pickup
`pickupType 40`, a point light colour `ffffc400`, attenuation 32 to 160 and
intensity 1.5, an effect `common/meshes/concrete/concrete_tile_64x64`, a
target a name of its own (`target1`, …), and a player spawn and a target the
camera's yaw to the nearest 45°.

**The bridge tool**, as the game's (0.48): Shift-click a face of a brush of the
map (it is outlined in light blue), press B and aim at another face; a
wireframe shows the brushes that would join them, the wheel sets how many
steps (1 to 32, 4 to start with), and a click makes them, as brushes of the
world, selected. Escape stops. Each corner of the first face runs to its
corner of the second along a curve that leaves the first face along its normal
and comes into the second against its own, so two faces looking at each other
give a straight run (convex, filling the gap exactly) and faces at an angle an
arch or a curved ramp. Which corner meets which is found by turning the second
face as the run turns. The faces must have as many corners; the steps are not
on the grid, and the sides of a curved step may bend slightly, which the game
allows (the status says how many do). The bridge takes the material and colour
of the first face. The game's own tool may shape its curve otherwise; only its
controls are known.

**The property panel** (N, or Properties) shows the selected entity's
properties, or with nothing selected the map's WorldSpawn (title, owner,
modes, fog, sky). A selected volume shows its teleporter's or jump pad's.
Each property is edited as its type: three numbers for a `Vector3`, a tick
for `Bool8`, a colour picker and its eight hex digits for `ColourXRGB32` and
`ColourARGB32`, text for the strings, with the map's Target names offered for
`target`, `nameNext` and `targetGameOverCamera` and the map's effects for
`effectName`; `pickupType` is a list named from reflex-map's converter (20,
70, 71 and 80, which the stock maps use and it does not name, are marked as
guesses). A change is made on Enter or leaving the field, as one step of
undo; × removes a property, and a string left empty is removed rather than
written empty. Add offers the properties every entity of the type has in the
stock maps, and any other the map's entities of that type have; a string
added starts as the first suggestion (a Target's name for `target`).

The mode (create, vertex, bridge) and the status stand over the scene, and
the footer keeps one line, so the scene never changes size under the mouse
while working.

**Turning** (numpad + and −, or ⟲ and ⟳) turns the selection about the
vertical through the middle of its box, by the angle step; + turns +z toward
+x, as a positive yaw turns a prefab. Brushes turn, entities move round with
them and their yaw turns too (an Effect, Prefab, PlayerSpawn, Target or Pickup
without angles is given them), and a volume turns with its entity. A quarter
turn of a selection whose corner is on the grid can leave it half a step off
(a box 64 by 16 about its middle); it then moves the rest of the way, so it
stays on the grid. Other angles leave corners off the grid, as the game's do.

**Texture keys**, as the game binds them: on the face under the cursor, or on
the faces Shift-click picked while there are some (outlined in light blue;
Ctrl+Shift-click adds and removes faces), the arrows
move the texture a grid step (left and right along u, up and down along v),
Home and Insert scale it up and down along u by a quarter, End and Delete
along v, PgUp and PgDn flip it along u and v (the scale's sign), and `,` and
`.` turn it by the angle step. Each press is a step of undo, for all the faces
at once. A textured face shows the change in its texture; one whose material
has none shows its texture coordinates as a pattern: tiles of 64 units shaded
red along u and green along v, chequered every 16. Those coordinates are a
guess at the game's mapping (`texcoords` in `brush.js`):
Quake 3's, projecting the face on the axis plane it faces most, turning by
the rotation, dividing by the scale and adding the offset, which is what
[Q3ToReflex](https://github.com/chronokun/Q3ToReflex) assumes when it
converts Quake 3 faces (with its scale doubled, and a note that offsets,
scales and rotations may not come out right). The values written are what
the keys set either way; only the preview may differ from the game. The
steps (a grid step, a quarter, the angle step) are the page's choice: the
stock maps' offsets are mostly multiples of 16, their scales other than 1
mostly quarters and their rotations mostly multiples of 45.

**The clipper** (C, as the game's `editortoggleclipmode`, which was bound but
listed as unimplemented in 2015, so this is Radiant's clipper in the game's
key): select brushes, press C and click two or three points on surfaces
(on the grid across them, red dots; a click near a dot drags it, a fourth
click starts again). Three points make the plane through them; two make the
upright plane through them, or, when they are one above the other, the plane
through them along the view. The parts of the selected brushes it would keep
are outlined green and those it would cut away red: the side kept is the one
away from the camera, and the wheel or Ctrl+Enter flips it. Enter clips
(a brush wholly on the side cut away goes), Shift+Enter splits, keeping both
parts; the points stay for another cut, and Escape clears them, then leaves
clip mode. The cut faces take the picked material, as new brushes do (a
volume's, its own). The Split button still cuts on a grid plane at the last
click.

**Prefabs.** A Prefab entity places a prefab; clicking anything it places
selects it, as one thing, outlined whole, to move, turn, clone and delete as
an entity. The console (`) takes the game's commands, and the Prefabs panel
has a button for each:

| Command | What it does |
| --- | --- |
| `me_createprefab <name>` | The selection becomes the prefab `name` (letters, digits, `_`, `.` and `-`), placed where it stood by a new Prefab entity: its origin the middle of the selection across and its bottom, on the grid. A selected volume takes its entity with it and the other way round |
| `me_breakprefab` | Each selected placement becomes copies of what its prefab holds, where it placed them: brushes of the world, entities (a nested Prefab stays a Prefab), selected. The prefab stays for its other placements |
| `me_updateprefab [name]` | The selection becomes what the prefab holds, in every placement of it, and is replaced by a placement where it stood. Without a name, the prefab the selection was broken from (an edited, cloned or carved piece remembers it); it goes back by the inverse of that placement's position and turn, so every other placement shows the change in its own place. A prefab named but not broken from takes the selection about a new origin, as `me_createprefab` would |
| `me_listprefabs` | Opens the Prefabs panel: each prefab with how many Prefab entities name it (in the map and in other prefabs, as the game's `meGetPrefabList` gives `refCount`), Place (the next click on a surface places one, turned to the view, as `me_createtype prefab <name>`) and Select (its placements in the map) |

**Editing a prefab in place**: double click one of its placements (or select
it and `me_editprefab`, or Edit selected in place in the Prefabs panel). The
placement is broken into the map as `me_breakprefab` breaks it, and while it
is open every edit of its pieces is also written into the prefab, so its other
placements change as it is edited; anything made meanwhile (a brush, a bridge,
an entity) goes into the prefab too. Escape with nothing selected (or
`me_closeprefab`) closes it: the pieces become its placement again where they
were. The mode line says which prefab is open. By hand, the same is: click a
placement, `me_breakprefab`, change the pieces, select them,
`me_updateprefab`. Undo undoes a prefab change as one
step, with the map's. New prefabs are written before `global`, each starting
with a WorldSpawn, as every prefab of the stock maps does. Breaking every
placement in the 18 stock maps gives exactly the brushes the page draws for
it, and putting each back gives the prefab's own coordinates (within 0.001).
What the game's own `me_updateprefab` takes, a name or the selection's
prefab, is not known; the page takes either. A placement's pitch and roll do
not carry to the angles of the entities inside when it is broken; no stock
map tilts a prefab that holds entities with angles.

The console also takes `me_rotate_inc`, `me_rotate_dec`, `me_snapangle <n>`,
`me_snapdistance <n>`, `me_createtype <type> [prefab name]`, `me_mirror
[x|y|z]`, `me_activematerial [name]`, `me_editprefab`, `me_closeprefab`,
`me_showproperties`, `editortoggleclipmode`, `editortogglevertexmode` and
`help`.

**Entities** are selected by their markers (within 12 pixels; volumes, clips
and glass do not hide them), moved, cloned and deleted as brushes are.
Teleporters and jump pads have no position, so they are selected by their
volume; deleting the last brush of a volume deletes its entity, and cloning a
volume makes a new entity with its brush.

**Vertex mode** (V) shows the corners of the selected brushes as dots; the one
under the mouse is white. Dragging a corner moves it on the grid across, or up
and down with Alt. Faces may bend, as in the game (33 brushes of the stock
maps have bent faces or are not convex; the CSG tools leave such brushes
alone). A corner dropped on another corner of its brush becomes it, and faces
left with fewer than three corners go.

A brush that is not convex or has a bent face (the game's editor can
make one by moving a vertex; 33 brushes of the stock maps are such) is drawn,
and the CSG tools and face dragging leave it alone.

## How the game's editor worked

What the page copies and what it does not yet, from the install's
`game_default.cfg` and from what could be found of the game's editor
documentation (the Reflex wiki at wiki.reflexfiles.com is gone, so its pages
were seen only as search summaries; players' configs on GitHub, such as
[Limegrass/configs](https://github.com/Limegrass/configs/blob/HEAD/games/reflex/game.cfg),
and a [console reference](https://github.com/TonikwithaK/tarp-wiki/blob/HEAD/content/games/reflex/console.md)
transcribed from AEon's command document were read as they are):

- One key switches the live map between playing and editing, with no compile
  step and the camera where it was. Several players could edit one map on a
  server at once.
- Everything is done in the one first-person view at the crosshair: click
  selects, drag moves, Alt drags vertically, Shift works on the face under the
  cursor, Ctrl adds to the selection. `editortogglevertexmode` (V) shows the
  corners of a brush to drag one at a time; `editortoggleclipmode` (C) is
  bound but was listed as unimplemented in 2015. Both are on the page, the
  clipper as Radiant's.
- New things are made by choosing a type, `me_createtype` (keys 1–8:
  worldspawn (a brush), teleporter, jumppad, target, effect, pickup,
  pointlight, playerspawn; others by name), and dragging it out in the world,
  as on the page; B on the page also makes a fixed box.
- Snapping: `me_snapdistance` (16) and `me_snapangle`, with `me_rotate_inc` and
  `me_rotate_dec` (numpad + and −) turning the selection. On the page.
- Faces: `me_activematerial` and `me_activealbedo` set the material and colour
  to put on; arrows, Home/End/Insert/Delete, PgUp/PgDn and `,`/`.` move, scale,
  flip and turn a face's texture (`me_texcoords_*`). On the page, with the
  bound keys; the steps the game takes are not known.
- The bridge tool (B, `me_startbridge`, added in 0.48): Shift-select a face,
  press B, aim at another face, and brushes joining the two are shown; the
  wheel (`me_segments_inc`/`_dec`) sets how many; a click makes them. On the
  page, as above.
- Prefabs (0.40): `me_createprefab <name>` from the selection,
  `me_breakprefab`, `me_updateprefab`, `me_listprefabs`; placed as Prefab
  entities that can nest. The game's Lua API has `meGetPrefabList()`, giving
  each prefab's name and `refCount`. On the page, from its console.
- `me_showproperties 1` (N) opens the selection's properties; teleporters and
  jump pads name their Target there. On the page, as above.
- Light is baked with `r_lm_build` (F4) into light probes on a 64-unit grid
  (the map's `.light` file); until then edited geometry is drawn fullbright.
  `savemap` saves; the stock MapAutoSave widget saves numbered copies.
- No subtract, hollow, merge or mirror was found in the game's editor; clip
  brushes and `editor_nolight` did some of that work.

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
  itself. Each holds entities and brushes as one list, and **a brush belongs
  to the entity before it**: the WorldSpawn's run of brushes is the world
  (Aerowalk's global: its WorldSpawn, 1,596 brushes, 20 entities, 3 brushes,
  …), and a `Teleporter`, `JumpPad`, `RaceStart`, `RaceFinish` or
  `TriggerVolume` is followed by the brush that is its volume, mostly without
  a material. No other type owns brushes in the 18 stock maps (34,788 world
  brushes; 106 jump pad, 51 teleporter, 49 trigger, 7 race start and 2 race
  finish volumes). Teleporters and jump pads have no position of their own.
  So the page keeps the list in order, draws volumes only while editing (light
  blue), and puts a new world brush at the end of the WorldSpawn's run, not at
  the end of the list where it would be the last entity's. Version 6 files
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

- **Textures**: lit by one fixed sun and the sky, with the editor's grid
  while editing; normal maps, specular and the game's own shaders are not
  drawn. How the game maps a face's offset, scale and rotation to texture
  coordinates, and how many units one repeat of a texture covers, have not
  been checked against the game: the page uses Quake 3's projection and 128
  units. Whether library textures put into the game work there is not
  confirmed.
- **Effects** (the game's models, by `effectName`) are marked as small grey
  points while editing, not drawn. Sky, fog and the baked light are not drawn;
  the background is the WorldSpawn's horizon colour.
- **Play mode** moves as Quake 3 does, not as Reflex (CPMA) does; no weapons,
  pickups, damage, race timing or crouching. Mirroring a prefab placement
  moves and turns it but cannot mirror what it places.
- Viewpoint pitch is taken to look down when positive; no map checked uses
  one.

## Checks

- `node --test tests/reflex_maps.test.cjs`: brush CSG, the map file reader
  and writer, texture coordinates, prefabs, mirroring and movement (landing,
  walking speed, walls, sliding, jump height, steps). Set `REFLEX_MAPS` to a folder of `.map` files to also read each,
  write it back unchanged and place its prefabs, and to break every placement
  of its prefabs and put each back.
- `python -m unittest tests.test_reflex_maps`: the import (with textures and
  thumbnails from made-up `.textureset` files), the material writer, putting
  library textures into a game folder, the Reflex texture library's routes,
  the routes, and the Node tests when Node is installed. Set `REFLEX_GAME_BASE` to a Reflex Arena
  folder to import that install and run the Node tests over its maps.
- `node tools/check_reflex_maps.cjs http://127.0.0.1:5000 build/reflex-review`:
  in a hidden browser, with the keyboard and mouse: makes boxes in a new map,
  carves, deletes, undoes, redoes and hollows them, saves and reads the
  download back; drags, lifts and pulls a face, clones, picks up and puts on a
  material; drags out a brush and a teleporter, places a pickup, a player spawn
  and a target, moves the pickup by its marker, deletes the teleporter by its
  volume; drags and welds corners in vertex mode; sets the map's title, a
  pickup's type and a teleporter's target in the property panel; bridges a
  face to another at a right angle in six steps; turns a box and a spawn a
  quarter onto the grid with numpad + and back with −; moves, scales, flips
  and turns a face's texture with the bound keys and undoes each; nudges with
  Shift; clips a box with two points, splits it and clips the other side;
  makes a prefab from the console, selects it by clicking it, clones and
  drags it, breaks the clone, pulls its top up and updates the prefab, which
  changes both placements, undoes that, and places a third from the Prefabs
  list; picks two faces and moves both textures, chooses a Tribes 1 texture in
  the material browser and puts it on them; mirrors a wedge left to right and
  upside down; opens a prefab placement with a double click, pulls its top up
  and sees the other placement follow, and closes it with Escape; walks on a
  floor in play mode, through a teleporter to its Target, and is thrown by a
  jump pad onto its Target; then draws the first imported map and checks it is
  not blank. Run six times in a row it passed every time. (Where it leaves
  edit mode and comes back only to refresh the view, it does so with no frame
  between, as play mode would otherwise move the camera.)

Checked on 2026-10-02 with the 18 maps of a stock install (Steam folder
`reflexfps`): AbandonedShelter, Aerowalk, Ashur, empty, forge, furnace,
Fusion, Hieratic, ironguard, Phobos, Ruin, SkyTemples, TheCatalyst and the
five training stages. Every one reads and writes back identical and places
every prefab it names (Hieratic, the largest: 8,539 brushes, 4,192 of them
from prefabs, drawn in about a second in headless Chromium; breaking and
updating one of its prefabs takes under a second, and undoing both gives back
the file byte for byte). The import ran against a stand-in install with those
maps and the stock `common.pak`, `environment.pak`, `structural.pak` and the
material files of `internal.pak`, which gave a colour to 50 of the 55
materials the maps name.
The other five have none to give: the three clip materials (a texture) and
race start and finish (an effect shader). Not
checked: any map against the game side by side.
