# Unreal map viewer

A free-flight preview of the maps of your Unreal install (Unreal Gold, OldUnreal 227: 102 `.unr` files under
`Maps`, 43 of them Return to Na Pali's in `Maps\UPak`). Open **Unreal Maps** from Skinner's home page. Unreal
Tournament's maps (same format) come later on the same page.

This first stage draws each level's BSP surfaces with their textures, evenly lit. Not drawn yet: lighting
(lightmaps, zone ambient light, fog), the sky seen through fake-backdrop surfaces, translucent and modulated blending,
placed actors (decorations, pickups, monsters) and movers. The page is plain Three.js, like the Q3 one; nothing of
the game is compiled in.

## Importing maps

Open **Import maps** on the page, enter your Unreal folder (`C:\Unreal` when left empty) and press **Import**. Every
map under `Maps` is read with the textures it uses; the game folder is only read. A full import takes under a minute
and writes about 140 MB to `local-data/unreal-maps/unreal`:

- `index.json`: one entry per map (id, file name, the LevelInfo's title, group).
- `maps/ID/scene.json`: texture groups, viewpoints, counts; `maps/ID/geometry.bin`: float32 positions (x, y, z),
  float32 texture coordinates (u, v), uint32 triangle indices, one after the other.
- `textures/*.png`: the textures, shared by every map and named `package.group.name`, kept when already there.

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

## Checks

- `tests/test_unreal_map.py`: a 1998 map (Vortex2, version 61) and a Return to Na Pali one (Abyss, version 68) read
  exactly, wind their polygons as above and resolve every texture; the page's routes and the import guard.
- Browser sweep (2026-10-09, scratch data): all 102 maps load on the page; 98 show surfaces at their first
  viewpoint, and the 4 that did not (End, Intro1, Intro2, UGCredits: cutscene and credits maps whose PlayerStart
  sees nothing or a black room) now open at their cutscene camera, except UGCredits, whose credits are movers.
- In the game (OldUnreal 227, windowed 1024 × 768, FOV 90, scratch INI; the install unchanged by file manifest,
  2026-10-09): the spawn views of NyLeve and Vortex2 match the page's first viewpoint in layout and texture placement
  to within 1–2 % of the frame, once the viewpoint stands on the floor (the first try, at the PlayerStart's own
  height plus the eye, was 30 units too high on NyLeve). Inter5's starmap screens and DmDeck16's view from
  PlayerStart0 read the same way round as on the page. Brightness differs: the game is lit, the page is not yet.
