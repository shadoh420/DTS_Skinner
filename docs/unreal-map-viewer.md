# Unreal map viewer

A free-flight preview of the maps of your Unreal install (Unreal Gold, OldUnreal 227: 102 `.unr` files under
`Maps`, 43 of them Return to Na Pali's in `Maps\UPak`). Open **Unreal Maps** from Skinner's home page. Unreal
Tournament's maps (same format) come later on the same page.

The page draws each level's BSP surfaces with their textures and the map's own lighting (lightmaps rebuilt from its
lights and shadow bits, zone ambient light, the game's default display brightness): masked, translucent, modulated
and two-sided as the game draws them, auto-panning where the game pans them, and the sky zone seen through the sky
surfaces. Not drawn yet: fog, animated and wavy textures, mirrors, placed actors (decorations, pickups, monsters) and
movers. The page is plain Three.js, like the Q3 one; nothing of the game
is compiled in.

## Importing maps

Open **Import maps** on the page, enter your Unreal folder (`C:\Unreal` when left empty) and press **Import**. Every
map under `Maps` is read with the textures it uses; the game folder is only read. A full import takes two to three
minutes (most of it rebuilding the lightmaps) and writes about 180 MB to `local-data/unreal-maps/unreal`:

- `index.json`: one entry per map (id, file name, the LevelInfo's title, group).
- `maps/ID/scene.json`: texture groups, viewpoints, counts, the lightmap atlas's size; `maps/ID/geometry.bin`:
  float32 positions (x, y, z), float32 texture coordinates (u, v), float32 lightmap coordinates (u, v), uint32
  triangle indices, one after the other; `maps/ID/lightmap.png`: the map's lightmaps in one atlas. A pack from before
  the lighting (no `lightmap` in its scene) has no lightmap coordinates and is drawn evenly lit.
- `textures/*.png`: the textures, shared by every map and named `package.group.name`, kept when already there
  (a re-import rewrites them).

Maps already imported are skipped unless **Re-import existing maps** is ticked. From a shell:
`python tools/import_unreal_map.py --install C:/Unreal [--replace]`.

## Controls

Click the view to capture the mouse (or drag to look), **WASD** to move, **Space** up, **Shift** down, the wheel
changes speed (8 m/s at first, about the game's running speed), **Esc** releases the mouse, **1–9** jump to the
map's viewpoints: its cutscene cameras (Intro1, Intro2 and End only), its PlayerStarts at the eye of a player standing
on the floor below them (39 + 23 units over it, as the game spawns one; a start more than 200 units over the level's
floor, standing on something not drawn yet, keeps its own height), and where each of its camera paths starts.

## How a map is read

`tools/import_unreal_map.py` reads the map with the package reader of the model importer (`tools/import_unreal.py`,
which also resolves the textures across the install's packages). SurrealEngine (github.com/dpjudas/SurrealEngine)
was read as the format reference; no code was copied.

- The **Level** export lists the placed actors and names the **Model**, the level's BSP. Up to package version 61
  (Unreal's 1998 maps, 54 of them) the Model's points, vectors, nodes, surfaces and vertex pool are objects of their
  own (`Vectors`, `BspNodes`, `BspSurfs`, `Verts`); from version 62 they are arrays inside the Model. The import
  checks that every read ends exactly at its object's end: all 102 maps of the install do, and all 96 of UT's.
- A **node** with three or more corners is a convex polygon on one **surface**; its corners are
  `Points[Verts[VertPool + n].Vertex]`. Every such node is drawn, as the game draws them, except where its surface
  (or its texture's own `PolyFlags`, which the game adds to the surface's) is invisible, or a zone portal.
- **Texture coordinates**: `u = ((P - Base) · TextureU + PanU) / (USize × DrawScale)`, and v likewise; row 0 is the
  top of the image. A surface without a texture draws the LevelInfo's `DefaultTexture`, or Engine's.
- **Placement** is the model importer's, so placed meshes will line up later: Unreal's (x forward, y right, z up)
  becomes the page's (-y, z, x) at 52.5 units to a metre. Unreal stores a polygon's corners clockwise seen from its
  front; the page reverses them. Each polygon is checked against its surface's normal: of the install's 601,972 all but
  89 (semisolid faces on 18 maps, stored the other way round) need reversing, and those are wound to face
  their surface's normal too.
- **Procedural textures** (fire, water, wet, wave and ice) are drawn by the game as it runs, so their files hold
  no pixels: fire gets a still from its sparks (as for models), wet and ice textures show their source texture
  undistorted, water and wave textures calm water. One 227 texture format is not decoded (19, `UWindow.BlackTexture`
  on DmRetrospective) and is drawn flat in its stored average colour.
- **Surface flags** follow the game's OpenGL drawing (as SurrealEngine reads it): masked surfaces cut out palette
  index 0; translucent ones add their texture over what is behind them (one, one minus source colour) and modulated
  ones multiply it by twice their texture (destination colour, source colour), neither hiding what is behind them
  from later surfaces; translucent drops masked; two-sided surfaces show both faces. A texture marked `bMasked`
  masks its surfaces even where they are not flagged masked (765 opaque polygons: cobwebs, grates; Glathriel2's
  cobwebs are see-through in the game), and a surface flagged masked cuts out index 0 of a texture that is not
  `bMasked` (NyLeve's sky panorama of mountains, `SkyBox.lnd_1..4`, cut out in the game), so the pack writes every
  palette texture with index 0 clear; opaque surfaces ignore it.
- **Auto-panning** surfaces move 64 texels a second times their zone's `TexUPanSpeed` / `TexVPanSpeed` (the zone on
  the surface's front side; the LevelInfo for zone 0; 1 where the zone does not set it: ZoneInfo's default, read from
  Engine.u). 83 maps have some.
- **The sky**: a fake-backdrop surface is a window onto the sky zone, a small room elsewhere in the map seen from its
  `SkyZoneInfo` with the view turned by that actor's `Rotation`. Every zone links to the same one, as 227's
  `ZoneInfo.LinkToSkybox` picks it: the last SkyZoneInfo in the map, or the last high-detail one (most maps have a
  low- and a high-detail sky; the game runs in high detail). The page draws the level from the sky zone first, then
  the level over it, the backdrop surfaces drawing nothing but hiding what is behind them. 68 maps show a sky; Inter3,
  Inter4 and Inter14 have backdrops but no sky zone, and the game draws those as ordinary surfaces, as the page does.
  227's per-zone `SkyZoneInfoTag` is set nowhere in the install, so one sky per map.
  Checked in the game: the sky through NyLeve's backdrop is the sky zone's own view, unmoved by the viewer and
  turned with the view; NaliC2's sky, turned 180°, shows the same part of its panorama as the game. Whether a 90° turn
  (Eldora, DmDeathFan) goes the game's way round is not checked: a 180° turn reads the same either way.
- **Class defaults**: a placed actor stores only what differs from its class's defaults (a plain `Light` stores no
  brightness), so actors and zones are read over their class's defaults and its parents'. A class with script of its
  own (Actor, ZoneInfo, LevelInfo) keeps its defaults after its bytecode, which the reader does not decode: it tries
  each offset after the bytecode's start for the class tail (state and class header, dependencies that are classes,
  package imports, defaults ending exactly at the export's end) and takes the first. On every class checked, in both
  installs, that was the defaults (Actor's `DrawType` 1, ZoneInfo's `TexUPanSpeed` 1.0, `AmbientSaturation` 255).
  227 moved UnrealI's light decorations (Lantern, TriggerLight...) into UnrealShare; a class its package does not
  hold is taken from the code package that does.

## Lighting

The game keeps each surface's light as a **lightmap entry** (an offset into the light bits, a pan, a size in texels
and a texel size in units, the first of its lights in the Model's light list) and one **shadow bitmap** per light that
reaches it (one bit a texel, rows of `(width + 7) / 8` bytes, the low bit first; the lights' bitmaps follow each
other). It builds the lightmaps from these as it runs; the import does the same once, with the rules as SurrealEngine
reads the game (no code copied):

- Texel `(i, j)` is the point of the surface's plane where `(P - Base) · TextureU = PanX + i × UScale`, and likewise
  for v; on screen texel i's centre is at `s = ((P - Base) · TextureU - PanX) / UScale + 0.5`.
- Each light's shadow bits are blurred 3 × 3 (weights 0.5 in the middle, 0.25 at the sides, 0.125 at the corners: a
  fully lit texel is 2). Its light there is shadow × falloff × incidence × colour, at most 1 a channel, where the
  falloff is `min((1 + 2v³ - 3v²) / v, 1)` for `v` = distance / radius below 1, the radius `(LightRadius + 1) × 25`
  units, and the incidence `|cos|` of the angle to the surface's normal. Spot lights narrow it to their cone
  (`LightCone`), non-incidence lights drop the angle, cylinder lights (3,412 of the install's) fall off with the
  distance across, not up; the waving and turning effects light as plain ones.
- **Colour**: value `6.512735 × √LightBrightness` of 255, so 0.408 at most; `LightSaturation` 255 is white, lower is
  more of the `LightHue` (three 85-wide sectors, red, green, blue).
- The zone's ambient light (`AmbientBrightness`, `AmbientHue`, `AmbientSaturation`, the zone on the surface's front
  side) starts every texel; the lights add up from it, and the lightmap stops at 1.
- Lights that change as the game runs are drawn at their average: pulse 0.65 of their brightness, subtle pulse 0.8,
  blink, strobe and flicker 0.5 (on half the time). TriggerLights that start off (`bInitiallyOn`, false by default:
  300 of the install's) give no light; lights of type none or brightness 0 neither.
- The page draws texture × lightmap × 2 (the game's OpenGL device without one-x blending). Unlit surfaces and those
  without a lightmap draw their texture as it is. Every map's lightmaps go into one atlas (`lightmap.png`; NyLeve's
  1024 × 386), each with a one-texel border copied from its edge, so filtering stays inside it.
- **Display brightness**: the game's OpenGL device ramps the finished screen, and its screenshots, by
  `pow(c, 1 / (2.5 × Brightness))`, Brightness 0.5 by default. The page draws the frame into a target and ramps it
  onto the screen the same way, after blending. This is fitted, not read from 227's code: aligned 227 shots of
  NyLeve's and Vortex2's starts match the page within 2–18 % per brightness band only with it.
- **Fog** is not drawn. 41 maps have fog zones (volumetric fog: spheres of fog around lights, drawn as seen from the
  camera, so nothing to bake), one zone uses 227's distance fog (DmRetrospective).

Checked against 227 shots (block means of the whole frame, HUD rows left out; game / page): NyLeve's start 0.87,
NyLeve's sky zone 0.93, Vortex2's start 0.95, NaliC2 from its first BlockMonsters 1.04 (its sky room now lit red and
purple as in the game). Glathriel2's start comes out 1.65 times too bright (0.60): most of its light comes from dim
lights of very wide radius (brightness 8 to 80 over 1,000 to 2,000 units), where the falloff's flat top (full light
out to half the radius) is the likeliest cause; not settled without a game run that measures one such light alone.

## Checks

- Lighting (2026-10-09): the comparisons above, from the earlier runs' 227 screenshots. Not checked in the game:
  modulated surfaces lightmapped (as SurrealEngine draws them), the cylinder and spot light shapes, the averages for
  changing lights.
- Browser sweep with lighting (2026-10-09, scratch data): all 102 maps load without console errors; at their first
  viewpoint a median 95 % of the view is drawn. Two first views are almost black and plausibly so in the game too
  (not checked): Abyss's start faces a dirt wall lit only at a grazing angle, in a fog zone; NaliBoat's looks across
  open water at night, mostly shadowed from its one light. UGCredits stays black (its credits are movers).

- `tests/test_unreal_map.py`: a 1998 map (Vortex2, version 61) and a Return to Na Pali one (Abyss, version 68) read
  exactly, wind their polygons as above and resolve every texture; translucent drops masked, panning rates, Vortex2's
  bMasked grates masked, Abyss's sky; the page's routes and the import guard.
- Browser sweep with surface flags and skies (2026-10-09): all 102 maps load without console errors; NyLeve's sky
  room (mountain panorama, sun, clouds) shows through its sky surfaces, Vortex2's grates are see-through.
- Second game run (227, same guarded setup, install unchanged by manifest, 2026-10-09): Glathriel2's start, NyLeve
  from its first AlarmPoint and from its sky zone, NaliC2 from its first BlockMonsters (`viewclass` then
  `behindview 0`) match the page in layout; the first try showed a black band where the page drew the sky
  panorama's index 0 opaque, fixed as above. Colours still differ: the game lights the sky rooms (NaliC2's red).
- Browser sweep (2026-10-09, scratch data): all 102 maps load on the page; 98 show surfaces at their first
  viewpoint, and the 4 that did not (End, Intro1, Intro2, UGCredits: cutscene and credits maps whose PlayerStart
  sees nothing or a black room) now open at their cutscene camera, except UGCredits, whose credits are movers.
- In the game (OldUnreal 227, windowed 1024 × 768, FOV 90, scratch INI; the install unchanged by file manifest,
  2026-10-09): the spawn views of NyLeve and Vortex2 match the page's first viewpoint in layout and texture placement
  to within 1–2 % of the frame, once the viewpoint stands on the floor (the first try, at the PlayerStart's own
  height plus the eye, was 30 units too high on NyLeve). Inter5's starmap screens and DmDeck16's view from
  PlayerStart0 read the same way round as on the page. Brightness differs: the game is lit, the page is not yet.
