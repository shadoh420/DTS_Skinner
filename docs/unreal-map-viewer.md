# Unreal map viewer

A free-flight preview of the maps of your Unreal install (Unreal Gold, OldUnreal 227: 102 `.unr` files under
`Maps`, 43 of them Return to Na Pali's in `Maps\UPak`). Open **Unreal Maps** from Skinner's home page. Unreal
Tournament's maps (same format) come later on the same page.

The page draws each level's BSP surfaces with their textures and the map's own lighting (lightmaps rebuilt from its
lights and shadow bits, zone ambient light, the game's default display brightness): masked, translucent, modulated
and two-sided as the game draws them, auto-panning where the game pans them, and the sky zone seen through the sky
surfaces, and the placed actors where the game starts them: decorations, pickups and monsters as meshes, movers
(doors, lifts) at their first position. Not drawn: fog, sprites, coronas and particle effects, animated and wavy
textures, mirrors. The page is plain Three.js, like the Q3 one; nothing of the game
is compiled in.

## Importing maps

Open **Import maps** on the page, enter your Unreal folder (`C:\Unreal` when left empty) and press **Import**. Every
map under `Maps` is read with the textures it uses; the game folder is only read. A full import takes about three
minutes (most of it rebuilding the lightmaps) and writes about 260 MB to `local-data/unreal-maps/unreal`:

- `index.json`: one entry per map (id, file name, the LevelInfo's title, group).
- `maps/ID/scene.json`: texture groups, viewpoints, counts, the lightmap atlas's size; `maps/ID/geometry.bin`:
  float32 positions (x, y, z), float32 texture coordinates (u, v), float32 lightmap coordinates (u, v), uint8 RGBA
  vertex colours (the meshes' light; white elsewhere), uint32 triangle indices, one after the other; `maps/ID/lightmap.png`: the map's lightmaps in one atlas. A pack from before
  the lighting (no `lightmap` in its scene) has no lightmap coordinates and is drawn evenly lit.
- `textures/*.png`: the textures, shared by every map and named `package.group.name`, kept when already there
  (a re-import rewrites them).

Maps already imported are skipped unless **Re-import existing maps** is ticked. From a shell:
`python tools/import_unreal_map.py --install C:/Unreal [--replace]`.

## Controls

Click the view to capture the mouse (or drag to look), **WASD** to move, **Space** up, **Shift** down, the wheel
changes speed (8 m/s at first, about the game's running speed), **Esc** releases the mouse, **1–9** jump to the
map's viewpoints: its cutscene cameras (Intro1, Intro2 and End only), its PlayerStarts at the eye of a player standing
on the floor below them (39 + 23 units over it, as the game spawns one; invisible floors count, as the game collides
with them; a start more than 200 units over any floor keeps its own height: DmRetrospective's stands on a mesh, and
Dark's, Endgame's, Abyss's and Glathriel1's first starts are 380 to 990 units up, the player dropping from there), and
where each of its camera paths starts.

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

## Placed actors

Every actor the map places, unless it is hidden (`bHidden`), is read over its class's defaults:

- **Movers** (any class that is a Mover) draw their brush's polygons (the brush Model's `Polys`: corners, base, normal,
  U, V and pan in the brush's own space) where the mover starts: `BasePos + KeyPos[KeyNum]` and `BaseRot +
  KeyRot[KeyNum]`, as Mover's BeginPlay places it, then `Location + Rotation × MainScale × (point − PrePivot)`. Some
  are mirrored through `MainScale` (a −1); each polygon is wound to face its turned normal. Their lightmaps are the
  brush Model's own (one entry per brush poly, its own light list and shadow bits), rebuilt as the level's, at the pose
  the editor raytraced them (`BrushRaytraceKey`). Their upward faces are floors for the viewpoints (SkyCaves' starts
  stand on one). A `TriggerToggle` mover whose tag a proximity Trigger fires as a player lands below a PlayerStart is
  drawn at its last key: it opens in the first second and stays open (SpireVillage's arrival force field, checked in the
  game; Nalic2's and VeloraEnd's arrival doors, the same pattern). Movers that close again (`TriggerControl`,
  `TriggerOpenTimed`) keep their saved pose.
- **Meshes** (DrawType mesh): the mesh at its `AnimSequence` frame (`AnimFrame` into it; with none, the first of
  still, breath, idle..., as the model browser poses them), at `Location + PrePivot + Rotation × DrawScale × point`
  (SurrealEngine's reading). Each texture slot as the game picks it: `MultiSkins[i]`, then `Skin` (slot 0, or a slot
  the mesh leaves empty), the mesh's own, `Texture` (not Engine's editor icons), the last `MultiSkins` set, then
  Engine's default texture. `Style` adds masked, translucent or modulated. Monsters stand where they are placed, in
  that pose; their weapons are not drawn.
- **Mesh light** is worked out at each vertex as the lightmaps are, without shadows: the zone's ambient light and the
  actor's `AmbientGlow` (255 pulses: 0.3, its average), plus `ScaleGlow ×` each light's colour × 2 × falloff × the
  incidence on the vertex's normal (both sides where a face is two-sided). Unlit meshes and faces (`bUnlit`, unlit
  faces, outside every zone) are drawn at `ScaleGlow / 2 + AmbientGlow` of the texture: SpireVillage's Plant14 (bUnlit)
  read 0.58 of the page's full texture on screen in 227, 0.5 before the display's gamma, and 1.00 after the change
  (the palm beside it 0.89 before, 1.12 after). Unlit flames (TorchFlame, Flame) follow the same rule, not checked in
  the game. A leaf's back face lit only on its front (`max(cos, 0)`) changed nothing there: the leaf is unlit. The page
  carries it in vertex colours and points the meshes at a white lightmap texel.
- **Zones**: the saved `Region` is often stale (zone 0), so an actor's zone is found as the game does when it starts:
  down the BSP from the root to the leaf its location falls in.

Over the 102 maps: 9,476 meshes and 2,874 movers drawn; 9 actors not read (227's StaticMesh, on DmRiot, its movers
among them; a format of 227's own, left unread). A mesh corner shared by triangles with the same texture is stored
once. Not drawn: sprites, decals, the monsters' weapons, and 227's emitters (Emitter, WeatherEmitter, MeshEmitter,
SpriteEmitter: 77) and coronas (DynamicCorona: 526 on DmRetrospective and EntryII). The sprites a
player would see are lens-flare glows: 8 on Unreal, one each on Intro1, Intro2 and End, 2 on Endgame, 52 ScaledSprites
on DmRetrospective. TriggerLights (436), ExplodingWalls (94) and BreakingGlass (120) are sprites their defaults leave
unhidden, but the game does not draw them (a TriggerLight 87 units in front of a 227 camera on Glathriel2 shows
nothing), and neither does the page.

The mesh light is a proxy where it matters most: lit with every light in range, MarineBox6 on Glathriel2 took half its
light from wide fill lights behind walls and came out about three times too bright. The game lights an actor only with
lights that reach it; the import takes the lights the editor listed (raytraced) for the floor surface beneath the
actor, or every light in range with no floor below. Against the Glathriel2 run's shots (on screen, game / page): the
box 0.76, the big plant 0.91, a Skaarj 0.69, a torch sconce 1.23, a hanging lantern 0.82; the level's own surfaces in
the same views 0.75–0.97.

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
- **Colour**: the brightest channel is `LightBrightness / 255 / 2`, so a white light of 255 lights a fully lit texel
  to exactly 1; `LightSaturation` 255 is white, lower is more of the `LightHue` (three 85-wide sectors, red, green,
  blue). Linear in brightness, measured in the game (below): SurrealEngine's `6.512735 × √LightBrightness` lit dim
  lights 2.7 times too strongly.
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
  NyLeve's and Vortex2's starts match the page within about 20 % per brightness band only with it (the darkest,
  near-black bands excepted, which the game lifts further).
- **Fog** is not drawn. 41 maps have fog zones (volumetric fog: spheres of fog around lights, drawn as seen from the
  camera, so nothing to bake), one zone uses 227's distance fog (DmRetrospective).

Checked against 227 shots (block means of the whole frame, HUD rows left out; game / page, on screen): NyLeve's
start 1.14, NyLeve's sky zone 1.25, Vortex2's start 1.11, NaliC2 from its first BlockMonsters 1.16 (its sky room lit
red and purple as in the game), Glathriel2 from its start and nine actors 0.82–1.19. With SurrealEngine's square-root
brightness these were 0.86, 0.93, 0.96, 1.04 and 0.40–1.27.

How the brightness rule was found (third game run, 2026-10-09): Glathriel2 seen from its start and from nine actors
whose class occurs once in the map (`viewclass X`, `behindview 0`). The page's lighting there was split into parts by
which lights give it, each part rendered alone from the same views, and the game's frame (gamma undone) fitted as a sum
of the parts. Lights brighter than 80 came out right (0.93), dimmer ones 2.7 times too strong (0.37), whatever their
radius (wide 0.36, narrow 0.39), saturation (0.34, 0.42) or distance; nor did keeping each surface's four strongest
lights explain it (0.75 and 0.33). Linear brightness, scaled so a full white light lights 1, brings every view within
the range above; a falloff without SurrealEngine's flat top made the views 1.3 to 2 times too dark.

## Checks

- Placed actors (2026-10-09): `tests/test_unreal_map.py` checks Vortex2's and Abyss's meshes and movers (none failing,
  meshes lit). Browser sweep with actors: all 102 maps load without errors or console warnings, a median 92 % of the
  first view drawn; SkyCaves' and IsvKran32's starts now stand on movers (49 % and 40 % drawn before, 100 % and 98 %
  now). DmRadikus' starts all stand on a drawn floor (an earlier note said two floated; a raycast down from each eye
  finds the floor 62 units under it). UGCredits' two UGoldCredits textures are found: the package saved
  `Logos.Legend` and `Wall.ugoldcredits12` without their groups, and the import takes the only export of that name and
  class (the only two such imports in the install; not checked in the game).
- Game run 4 (227, same guarded setup, install unchanged by file manifest; cameras by `viewclass X` repeated K times,
  K counted over the map's actors of class X, only classes whose every actor is static or never deleted):
  SpireVillage's start stood inside Mover0's purple force field on the page; in the game a Trigger under the start
  lowers it 272 units as the player lands (now drawn so: block correlation 0.94, game/page 1.05). From two path nodes
  and a plant: walls and ground 0.96–1.11, the Titan 1.06; a plant leaf around the camera (the camera inside Plant14)
  0.58, too bright on the page: Plant14 is bUnlit, now drawn at half the texture (1.00 after; see Mesh light). Vortex2:
  start 1.03 (correlation 0.92), a hall from PlayerStart1 0.92; the view of the door Mover1 is in a fog zone (fog not drawn), its layout matches.

- Lighting (2026-10-09): the comparisons above, from the earlier runs' 227 screenshots and a third run on Glathriel2
  (same guarded setup; the install unchanged by file manifest). Not checked in the game:
  modulated surfaces lightmapped (as SurrealEngine draws them), the cylinder and spot light shapes, the averages for
  changing lights.
- Browser sweep with lighting (2026-10-09, scratch data): all 102 maps load without console errors; at their first
  viewpoint a median 91 % of the view is drawn. Four first views are almost black and plausibly so in the game too
  (not checked): Abyss's start faces a dirt wall lit only at a grazing angle, in a fog zone; NaliBoat's looks across
  open water at night, mostly shadowed from its one light; DKNightOp and ExtremeDark are dark by design. UGCredits
  stays black (its credits are movers).

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
