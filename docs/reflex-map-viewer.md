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
  they are, named by content, with each map's baked `.light`),
  `materials.json` (each material's albedo, metallic and roughness, shader and
  archive), `effects.json` and `models/` (the models and lights the maps
  place). All 18 maps of a stock install
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
taken as the sRGB bytes of a colour picker. The dev materials are the
exception: their shader (`…_TINTED`, which no other stock material uses) takes
the material's tint, and in the game a face colour on them changes nothing, so
the page leaves it off too. A face with colour `0x00000000`,
or none, is drawn in its material's albedo. The game raises both to the power
2.2, as its shaders do (`deferredPbrStylized` takes albedo and vertex colour
to 2.2): measured on plates in the game (read with a 2.2 decode; at those
levels the sRGB curve differs by about 3 %), a white face draws the same on
concrete, gunmetal and stone (the face's colour replaces the material's),
concrete's own 0.37 draws 0.107 of a white face (0.37^2.2 is 0.112), stone's
(0.25, 0.3, 0.35) a bluish dark grey, and `dev_grey128`'s tint of 0.5 times
its 0.9 texture 0.167. Shading is linear and the frame is encoded with the
sRGB curve: the game's last pass raises it to 2.2 / `r_gamma` (1 at the
default: no tone mapping, no exposure) into a swap chain `reflex.exe` makes
`R8G8B8A8_UNORM_SRGB`, so the hardware's sRGB curve does the encoding (darker
than a 2.2 gamma below about 0.15 linear; the page used 2.2 before, which
made its dark corners less saturated than the game's). Textures are decoded
with the same curve (their DDS are sRGB); the light is the map's (Light,
below). `gunmetal`,
whose albedo is almost black (0.015, 0.02, 0.02, roughness 0.4) and which most
of Aerowalk is, shows only what it reflects. Where the import found no albedo
for a material the page guesses a colour from the name and lists those
materials under Preview notes. Glowing materials
(`common/materials/effects/glow*`, shader `standard_ALBEDOCOLOUR_ALBEDOINTENSITY`)
are not lit: they shine (colour × `albedoIntensity`)^2.2, as that shader has
it; so do lava and slime ((colour × 3.3)^2.2, `fluid`) and the other forward
shaders the game draws solid (their flags lack 0x200).

Faces of see-through materials are drawn after everything else: glass and
water are added to the frame (The frame, below), and so are light beams
(`alphaFresnel`, as `internal/effects/lights/fx_light_beam` on Phobos), shaded
as on models (The frame); race starts and finishes, and pickup and powerup
glows (Aerowalk and TheCatalyst have such faces) stay faint, a third over
what is behind them, as their own shaders are not drawn. Refraction is not drawn. The import keeps every material it finds with
its shader and flags for this, also those it finds no colour for.

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
`local-data/reflex-maps/textures`: the albedo, divided by the meta texture's
blue channel where there is one. Only the dev grid's meta has that channel
below 1 (0.6 along the grid lines), and in the game those lines are lighter
than the face, almost white; the dev albedo is a flat light grey, so the
page's lines are lighter but clip at white. (Imports from before this change
darkened the lines; import again with "Re-import existing maps" ticked to
bake them anew.) A diffuse
texture keeps its alpha (ivy is alpha-keyed); an albedoSpec's alpha is its
specular level and is left out. Thumbnails go to `thumbs`, 128 pixels. The
page draws a textured face in its texture times its colour (the material's
tint, or the face's own colour), repeating every 128 units at scale 1 for the
game's textures, as in the game: the dev grid's eight cells across are 16
units, as `dev_grid16` says.

**Skinner's texture libraries.** The textures the import decodes are a
fourth library beside the T1, T2 and Q3 ones: on the model page, *Reflex
textures* reskins any model with them, and its exports include them. The
other way round, a brush face can take any texture of any library: the
Reflex page's material browser (Materials, or `me_activematerial`) lists the
game's materials with their thumbnails and each library's textures, and a
face that takes one gets the material `skinner/<library>/<file>`
(`skinner/t1/alientree`). The page draws it at its size in pixels over two
units, as Quake 3's default scale of 0.5 has it.

The game does not know `skinner/t1/alientree`: such a face draws in Reflex as
its grey grid fallback, and only Skinner draws the library texture. Skinner
does not write anything into the game folder. An earlier version of the page
put a material and a `.dds` for each library texture under `base/skinner`. The
game reads every loose file under `base` when it starts, and it refuses any
`.material` there that is not one of its own, even a byte-for-byte copy of a
stock one. It then stops with "Failed to load asset … from disk" and will not
start until the file is removed. The shipped game compiles custom content only
for weapon skins (`.skintxt` and `.texturesettxt` sources under
`assets/base/myskins`).

Faces of the editor's clip materials (`internal/editor/textures/editor_clip`,
`editor_fullclip`, `editor_weaponclip`) are not drawn while flying, as the game
does not draw them; while editing they show as purple glass.

### Light

Reflex lights a map in play from what the editor's Build Lighting writes
beside it (`maps/Aerowalk.light`), from its lights and from the sun; it has no
lightmaps for brushes. The page does the same, following the game's lighting
shaders (`internal/shaders/gbuffer_light_*.shad` in `internal.pak`, DirectX
bytecode, read with the Windows shader disassembler). The import copies each
map's `.light` beside it into the pack (`light.js` reads it):

- **Light probes**, one every 64 units over the map: the light arriving from
  every direction as order-2 spherical harmonics, seven half-float planes
  (`probes_cAr` … `probes_cC`). A surface takes the probes around it (blended)
  for its normal, times its albedo, times `lightmapGain` (`cbEngine`, offset
  616), which is 2: fitted as game = g × diffuse probe light + the rest over
  8 × 8 blocks lit by the probes only in the 14 game views (October 2026),
  the views' medians 2.00 to 2.46, nine of them 2.00 to 2.04; Ashur's, half
  reflection, still gives 2.02, so the reflections take no gain.
- **Reflection probes**: per ReflectionProbe entity (in their order) a 64 × 64
  cube map, BC1, five mips, decoded with the sRGB curve (the game's cube
  array is `BC1_UNORM_SRGB`); each probe cell names the one it reflects. A
  surface reflects it, blurred by its roughness, weighted by the split-sum
  lookup (here Karis' fit of the game's table), and its diffuse light is less
  what it reflects (1.54 % for anything not metal).
- Layout (little-endian): u16 2, u16 0x1323, f32 64, u32 nx, ny, nz, u32 a
  hash, f32 origin, f32 scale and f32 offset (a point's place in the grid is
  point × scale + offset); seven planes of nx × ny × nz × four float16, x
  fastest; one byte per probe (its reflection probe, from 1); then u16 2, u16
  0x1324, u32 count, u32 64, u32 5, and per probe its position, a byte and six
  faces (+x, −x, +y, −y, +z, −z), each with its mips. Every stock file is
  exactly that long. The game uses a copy of a map's `.light` under the copy's
  name. Without one (a map opened from a file, a test map) the game fills
  its probes with one light, the same in every map whatever its sky and
  surroundings (`reflex.exe` 0x140127fa0): order-2 SH of an even light
  (0.667, 0.784, 1) × 0.05 and two directional ones, (1, 0.941, 0.784) × 0.3
  from (0.433, 0.866, 0.25), above, and (0.627, 0.706, 1) × 0.15 from
  (−0.433, −0.5, −0.75), below. The page draws that light (the lights'
  shape 1/4 + t/2 + 5/16 P2(t) of t = n · direction) with the even part
  3.59 and the directional ones 1.74 times the exe's numbers, fitted to grey
  cubes in the game under the default sky, SkyTemples' sky, a black room and
  a grey room (October 2026; the four alike): on a grey face 0.57 up, 0.33
  toward +x, 0.24 toward −z, 0.20 down and 0.17 toward −x (light units; +z was not
  seen, 0.25 by the fit),
  bluer away from the floor; the page's faces come within 10 % of the
  game's each way (floor 0.117 against 0.119, ceiling 0.043 against 0.043).
  Its reflections take the same light (no reflection probes).

**Lights.** PointLight entities and the lights inside effects (point and
spot) light what they reach as `gbuffer_light_point` and `gbuffer_light_spot`
do: (1 − (distance − near) / (far − near))², clamped, Lambert diffuse and a
GGX highlight, and across a spot's edge ((cos − cos outer) / (cos inner − cos
outer))². Measured in the game with six PointLights over a grey floor: the
light is (colour × intensity)² × 0.87 (grey 0x80 gives a quarter, intensity 2
four times); without them near is 16, far 128 and intensity 1 (±4). An
effect's light hangs from a bone of its mesh (`b_light`) and a spot shines
along the bone's x axis; an Effect entity overrides them with
`pointLight…`/`spotLight…` properties where `…Overridden` is set. Measured
in the game with braziers and a torch in a closed room: a brazier given Ruin's
override (near 40, far 84) lights a pool as small as the page's, a plain one
the effect's own (far 160), and a torch with `pointLightColor ff000000` but no
`pointLightOverridden` lights as if it had none. An effect's scale
(`effectScale`, times its prefab's) scales its lights' near and far as well as
where they hang: Ruin's braziers are placed at 1.5, and only so does the page
light the walls and floor around them as the game does (within about 30 %,
where they were 5 to 30 times too dark). Measured in the game with three
braziers in a closed room, plain, at 1.5 and at 1.5 with Ruin's override (near
40, far 84): both scaled pools reach 1.5 times as far, and the page's floor
matches the game's within 3 % across all three. The same holds for a
teleporter portal with Ashur's override (near 32, far 128) at 1.5: on a wall
96 units behind it its glow falls off as a PointLight's with near 48, far 192
at the bone's height (90) does: within 2 % where bright, 10 % in the faint
tail (game run, October 2026). Not measured, and taken so because Aerowalk then matches: a spot's
angles are half the cone. The page
keeps the lights in a grid of 128-unit cells and sums, per point, only those
of its cell (ironguard has 362 lights).

**Light through paper and cloth.** Materials drawn with the `…_SSS` shaders
(paper, whose `sss` is 0.8, which the import keeps) let light through, as
`gbuffer_light_point`, `_spot` and `_directional` do: with s the material's
`sss` times its albedo's red (linear), 2 s (t + s (w − t)) of the diffuse
light whichever way the face is turned, t = sat(v · −l)⁴ (toward the viewer)
and w = sat(0.6 (−n · l) + 0.4). The paper's shader takes no vertex colour (nor
does the dev `_TINTED` one: their vertex shaders read none); with it the
page's lanterns came out orange with pale ribs. Measured in the game with a
lantern (`lantern_hanging_01`) in a black room and on an open floor under
SkyTemples' sky at 17:00 (October 2026), the paper's middle (red clipped in
all, linear): without the sun the game gives green 0.56 and blue 0.19 in both,
the page 0.53 and 0.18 (0.72 and 0.23 while it lit such maps evenly); the sun adds 0.215 blue in the game whether the lantern is 80 or 200
away or 4 times as big (so the lantern does not shade its own paper), and 0.21
on the page now that the sun's light follows `sky.sunColor` (see The sun; it
added 0.47 before). SkyTemples' lanterns in its sun: page blue 0.48 near and
0.40 to 0.45 far, game 0.53 and 0.39 to 0.42.

**The sun** rises at +z turned by `sky.skyAngle` at 6:00 (`sky.timeOfDay`), is
overhead at 12:00 and sets on the far side at 18:00, 15° an hour; a map that
gives neither is at 14:00, 30°. Its light at full incidence is
`sky.sunColor` (its bytes / 255, no 2.2) times 2.94: (2.94, 2.65, 2.13) under
the default `ffffe6b9` at noon, and SkyTemples' `b89a59` at 17:00 on a grey
floor gives 2.98 times it in all three channels (October 2026), so the hour
dims it no further. All measured in the game with a pole and a tower on an
open floor (at 6, 9, 12, 15 and 18, at 9 and 12 turned 90°, and with neither
given); the page's shadows match the game's there. The page shades it with two 4096²
shadow maps of the brushes and models: one over the whole map and a finer one
over the 2048 units around the viewer, drawn again each time the viewer has
moved 256 (the game has four cascades of 1024², split 92, 256, 512 and
4096 from the viewer: reflex.exe 0x140121821), each compare weighted bilinearly so
edges ramp; clip brushes cast no shadow (measured). Models cast with their
mesh's shadow mesh (the positions-only block after its levels of detail,
which the import writes as part 255 of the model): a shrub or tree by its
solid hull, not its leaf cards; a mesh from a pack imported before that casts
with what it shows. A textured brush face casts only where its texture is
kept (alpha ½ or more). Checked on SkyTemples, where the game's floor by the
pool is sunlit (the page's lay in the shadow of the shrubs' leaf cards), and
ironguard, whose door frame keeps the game's shadow.

**The sky** follows the game's compiled sky shaders (`sky2`, `clouds`): a dome
from `sky.skyBottomColor` through `sky.skyHorizonColor` to `sky.skyTopColor`
(each times its intensity), a halo around `sky.horizonLine` in
`sky.horizonColor`, the sun's disc and halo above the line, and the game's
cloud dome (`internal/world/skies/sky_clouds1`, which the import brings with
its texture) in `sky.cloudsColor`. What a map leaves out takes the defaults
the game's WorldSpawn starts with, read from `reflex.exe` (blue top 1a6bd4,
horizon 599dff, bottom b1d4f2, each at 0.5; sun ffe6b9 × 16, sharpness 32;
halo ffb644 × 0.2, exponents 3 and 4, line −0.1; clouds b2b2b2, coverage 0.8,
multiplier 16, bias 0.1, roughness 0.1, density and thickness 0.8; and the
sun's 14:00 and 30° measured above). Colours are taken as bytes / 255 without
2.2 (the default sky then matches the game's on an empty map); the cloud
texture is decoded from sRGB. Its red channel is read twice, the second time
half a texture along (the shader's swizzle; not red and green), and both
drift with time × `sky.cloudsSpeed` (default 1 and 0.01). Stars are the sky
material's `sky_stars_c` (the import brings it) seen along the three axes of
the view direction, each weighted by that axis to the sixth, times
`sky.starsIntensity` (default 0), fading out toward the horizon line.

### The frame

The page draws the frame as the game does (its compiled shaders and
`reflex.exe`): the scene in linear light into a half-float target, then
bloom, fog and the swap chain's sRGB curve; the editor's volumes, markers and outlines are
drawn after that, against the scene's depth, as before.

- **Bloom** (`bloomHighPass`, `bloomBlur`, `upsample`, `bloomAddToScene`): of
  what is brighter than 2.2 in luma, half the excess (`c × sat((luma − 2.2) ×
  0.5)`); five targets of half the size each, each blurred across and down
  (9 taps); each added into the next larger through a 9-tap tent of radius
  0.02, 0.0125, 0.006, 0.003 and 0.0015 of the frame's width, weighted 1, 1,
  1.5, 0.5 and 0.2 into the scene. Threshold, radii and weights are
  `reflex.exe`'s defaults; its bloom intensity is 1 in play.
- **Fog** (`postEffects_FOG`), where the scene has depth (not the sky): f =
  max(h², d²), d the way through `fogDistanceStart` … `fogDistanceEnd` by
  distance, h the way down `fogHeightTop` … `fogHeightBottom` (the top waving
  4 up and down, 4 sin(x/48 + 0.8 t + z/48)), toward `fogColor` (bytes / 255);
  the game keeps the start below the end and the bottom below the top. What
  a map leaves out takes WorldSpawn's defaults in `reflex.exe` (its default
  entity in `.data`, after the sky's): colour b4e1ff, start 512, end 8192, top
  0, bottom −8192, so a map without fog settings has a faint haze far off and
  below 0. Measured with Ruin copies: one with only a red colour and an end of
  1000 (clear up to 512, then red), one with only an end of 400 (pale blue);
  the page matches both within a level.
- **Forward shaders** add in linear light: the `standard_…` shaders (the
  teleporter ring, light strips, glass), water and environment slime (`fluid`,
  unlit, (colour × 3.3)^2.2, no vertex colours; its vertex shader has just
  that, its pixel shader passes it on), also where an entity puts them on a
  model (Ashur's teleporter portals: a bright cyan sheet, within 3 % of the
  game's green and blue; environment slime is added by its flags, not
  measured, and a model slot's colour where the entity sets none, as for
  other materials, is not checked for fluids: SkyTemples' sign_carnage), and
  pickup holograms and glows. Their colours are raised to 2.2 as
  their shaders do, and vertex colours count only where the shader's name says
  `VERTEXCOLOUR`. A material's flags (the u32 after its shader name, which the
  import keeps) set its render state; reflex.exe's material loader
  (0x140124f30) reads them so: bits 0 and 1 depth test and depth write, bits
  3 and 4 the cull mode (1 none, 3 back), bits 5 to 7 the blend (0 none, 1
  alpha, 2 added One + One, 3 premultiplied, 4 multiplied); 0x200 is set on
  every see-through one. Every see-through material the maps use adds, except
  the editor's volumes, smoke and clouds (alpha), and only water writes depth.
  Glass, holograms, the teleporter ribbons, light beams, pad glows and race
  starts and finishes cull nothing, so the page draws both their sides; water
  and ammo glows show their front faces only. Solid forward shaders, lava and
  the common slime (`fluid` with blend 0) shine their colour unlit.
- **Light beams and pads' glows** (`alphaFresnel`, on models and brush faces): sat(sat(n ·
  v)^pow × mul) of the material's `diffuseColour` × `intensityMul` × the
  mesh's vertex colour (2.2), added, both sides drawn with n turned to the
  viewer (`fresnelMulPow` and `intensityMul`, which the import keeps). A pad's
  glow cylinder fades up its height by its vertex colours.
- **Glass** (`standard_ALBEDOCOLOUR_GLASS_REFLECTION_VERTEXCOLOUR`: glass,
  frosted glass, ice; the team glasses' `…_TEAM_…`) lights nothing in the
  game: a lit sphere (`internal/effects/litspheres/glass_c`) looked up at
  the world normal's x and y × 0.5 + 0.5, plus a fixed cube map
  (`internal/debug/un_Old_Industrial_Hall_cube`, the same on every map, not
  its probes) along the reflected view × `reflectionIntensity` (0.25, ice
  0.6) at mip `reflectionBlur` rounded (0.5, ice 1.5, frosted 4, of its 512;
  its sampler takes the nearest mip), plus a colour term, added, both sides
  drawn with the normal as it is. The colour term is the material's
  albedo^2.2 × the face's vertex colour^2.2: a brush face's colour, or
  (0, 0, 0, 0) where it has none. Team glass lerps from its team's colour
  to that term's luma × the team's colour by the vertex alpha, so a face
  with no colour shows the team's colour whole; the page takes the
  WorldSpawn's `colorTeamA` or `colorTeamB` (red and blue where it gives
  none: reflex.exe .data 0x1409868d0, before the fog defaults), as with the
  game's absolute colours (`cl_colors_relative 0`, its default; with
  relative colours team 0 shows white and team 1 green). The lit sphere is
  sampled with the sRGB curve, the cube as its values are (reflex.exe's DDS
  loader, 0x14001b4f0, makes DXT1 BC1_UNORM; its one MakeSRGB, 0x140018be0,
  is called with force-sRGB 0, so the lit sphere is loaded by another
  path). The import keeps the two numbers and the team, and decodes both
  textures into the pack (the cube's faces stacked, 256 each:
  `tools/reflex_textures.py` decode_dds_cube); the page draws it so, the
  lit sphere as an sRGB texture, decoded before it is filtered. Measured
  with test maps in the game (build/reflex-sweep/game-run-glass and
  game-run-glass2: a black room, panes with glass on one face and on both,
  facing along x and along z, and single faces of glass_simple coloured and
  not, ice coloured white and both team glasses): one glass face adds 0.177
  where the sphere's rim is looked up (0.44 stored) and 0.05 at its centre
  (0.235 stored); both faces add. Ice and coloured glass_simple match the
  game within 0.004 (means); the glass panes come out 0.013 brighter on the
  page than in the game, whether one face or two (6 % at the rim, 20 % of
  the small value at the centre).
- **glass_simple** (`standard_ALBEDOCOLOUR_VERTEXCOLOUR`, flags 0xa49) adds
  its colour term alone, unlit, both sides: an uncoloured face adds nothing.
  The page draws a forward shader as see-through by its flags (0x200), so
  these faces are no longer drawn as solid lit brushes (Phobos has 219 of
  them, ironguard 16, Hieratic 9, TheCatalyst 6, Ruin 1).
- **Holograms** (`hologram`) and **ammo glows** (`glowPickup`) follow their
  shaders without their noise and movement: a hologram is brightest facing
  the viewer (its colour plus its scan lines, times a gradient up the model
  over the material's `vSize_gradMul`, plus a band, squared, then (1.5 x)^1.5
  × 1.5), a glow at its rim. What moves with the game's clock is drawn at its
  average over time: the scan lines add 0.09; the band running down the model
  is sat(0.5 − phase) (the clock is far larger than the model's height, so
  the phase runs negative), lit half the time, averaging 0.05; a glow's band
  s the same way (s 1/6, s² 1/18, s³ 1/48 on average). Drawn at the band's
  brightest instead, they came out as white blobs. Both bloom. The shaders
  were checked against the game's line by line (the gradient runs up the
  mesh's own height, `diffuseColour`'s alpha is 1). On Ruin from spawn 0 the
  plasma rifle's hologram has 993 white pixels on the page and 1177 in the
  game (light summed over it within 10 %); the game turns pickups, so a
  still shows them at another angle (the yellow armour is front-on in the
  game, side-on on the page: 346 white pixels against 1084).
- The game also grades colour through a 16³ lookup (`ColourGradingLUT_g`,
  which is the identity: nothing to draw) and darkens the frame's corners
  (`r_postfx_vignette`, on by default): max(0, 1 − 4 r⁵) of the distance r
  from the frame's centre in frame units (0.5 at the sides' middles), on the
  clamped colour before the sRGB curve. The page draws it so; on a flat floor
  the game's frame over the page's follows that curve within 2 % before
  (0.87 against 0.86 near a corner), and is flat within 3 % with it.

Measured on Aerowalk from a spawn (the camera fitted from the geometry: eye
58 above the spawn point, r_fov 110 on a 4:3 frame): with every effect of the
game off, the page's light matches the game's within 3 % overall
(game/page 0.97); with everything on, 1.04. Switching the game's effects off
one at a time there: dynamic lights −44 % of the brightness, sun 0, SSAO 1 %,
bloom 5 %, fog 0.

### Models

Effect entities place the game's models and lights by `effectName`; pickups,
teleporters and (in the editor) reflection probes place them too. The import
reads the `.effect` files the maps name, the pickups' and their pads', and the
`.mesh` files those name, and writes `effects.json` and one small binary per
mesh into the pack (`tools/reflex_models.py`, where both formats are set out:
materials and colours per mesh slot, lights on bones, levels of detail, rest
poses of skinned meshes). An `.effect` holds its records three times, for
`r_effect_quality` 0, 1 and 2; the import reads the second, the game's
default (a .data int of 1 behind the cvar in reflex.exe; read the same way,
`r_texture_quality` and `r_mesh_quality` are 1 too, which game.cfg saves as
the 2 they were set to). The first often
leaves out lights and particles: a pickup pad's yellow point and spot light
and its smoke, ammo's light, the quad's lights and flames, and fuseboxes'
lights are in the second only (35 of the stock effects the maps use). The
second and third differ only in three ion cannon effects. Checked on the
page with Ruin (pads, ammo), TheCatalyst (fuseboxes) and Ashur (the quad
glows orange in its ring and lights its alcove; no game shot shows one).
The page merges every placement into the map's
geometry, in the map's coordinates (position, angles, `effectScale` and any
prefab placement around them), and lights it as the brushes are. Angles are
yaw, pitch and roll, turned roll first, then pitch, then yaw, each about the
thing's own axes (seen in the game: Ruin's pads at 180 90 0 lie flat as at
0 90 0).

- A mesh slot's material is the entity's (`material0Name` …), else the
  effect's, else the mesh's (often a placeholder, `MaterialA`); its colour the
  entity's (`material0Albedo`), else the effect's (`p_metal` is a paint, green
  until given one), else the material's, all to 2.2, times the mesh's vertex
  colours. A part given a clip material is not drawn (Furnace hides most of a
  tree that way). God rays and light beams are left out (the game fades them
  with the view).
- Pickups stand on their pads (health, armour, powerups, weapons) and float
  30 units above them (measured on a Furnace health; taken for the others too;
  the game bobs and turns them). Holograms and glows are drawn as their
  shaders shade them (The frame, above). A
  skinned mesh (the teleporter's portal) is posed by its bones. A Teleporter
  shows nothing of its own (seen in the game); the stock maps place their
  portals as Effects.
- Mesh vertex colours are BGRA (the editor's target, red in the game, holds
  (0, 28, 180)).
- While editing, entities show the game editor's models (effects.json's
  `editor` list, seen in its editor): a spawn, a target (red flag), a point
  light (blue diamond, whatever its colour), a screenshot camera, a nav link's
  start and end (`isStart` 0), and a red "!" for an Effect with no model of its
  own. A reflection probe is a mirror sphere 16 in radius showing its probe.
  Volumes are drawn as the game's editor draws them: a teleporter's, jump
  pad's and trigger's in their editor texture (`editor_teleport`, …), unlit,
  41 % opaque (blended in sRGB, so the light stripes come out about 10 %
  darker than the game's, which blends in linear light), a tile every 16
  units at the face's scale 1, offset and turned with the face, upright and
  reading left to right on every wall (`texcoords(…, true)`; floors and
  ceilings not measured); race starts and finishes, which the game's editor
  does not draw, by a faint outline of their edges (they are clicked as the
  others). Measured with test maps in the game's editor.

### Particles

An effect's particle emitters (record 2 of the `.effect`) come with the
import as reflex.exe's `EffectParticleEmitter` reads them (0x1400eb820 sets
one up, 0x1400eaae0 moves its particles, 0x1400eba60 draws them; the record's
fields are set out in `tools/reflex_models.py`): how many it keeps at most,
the seconds between births (0: it fills up at once and they die together),
each particle's life (one, not a range), a velocity along the emitter's axes
and how far each of its three strays either way, an acceleration in the world
(along the emitter's axes for one that moves with it), half the width and
height at birth and at death, the point it hangs from, colour at birth and at
death, rotation and turn, and flags. The emitter's axes are its bone's pose
turned a quarter about y with x mirrored (0x1400f56c0), made unit length
(0x1400e6240): the pose's z, y and x rows, so a pad's smoke, thrown (0, 0, 7),
rises along the pad bone's x. What was read before as particles a second (+301)
is the acceleration's y: 0 for the pads' smoke, which is born once a second.
Their materials come with their texture, flags, `albedoIntensity`, flipbook
rows, columns, speed and first frame, and `particleDiffuseProperties`,
`fadeRoughness` and `intensityMultiplier`.

The page draws each emitter as it stands at one moment once running: its
particles born `interval` apart (taken in the middle of each gap), as many as
live, no more than it keeps (which spreads them over their life); each at its
velocity × age plus half its acceleration × age², with a fixed random stray
per particle, its size and colour taken between birth and death by its age,
the colour clamped to 0–1 (the game writes it as bytes). An emitter that fills
up at once is drawn at half its life (a moment picked: the game shows them in
step at every age). Each particle is a quad facing the camera, twice its half
sizes, hung from its point (flames 0.5, 0.75: a quarter of it below the bone,
the way up the brazier shots show), turned by its rotation, and shaded as its
material's shader:

- flipbooks (`particleFlipbook…`: flames): the frame at (age ÷ life +
  `flipbookOffset` + the red) × `flipbookSpeed`, the next blended in by the
  fraction, × colour^2.2 × `albedoIntensity`, added (one and one: the blend
  table at 0x140125340);
- smoke and steam (`particle_TEXTUREDIFFUSE…`): x, the texture's green carved
  by its blue and its red (`particleDiffuseProperties` x and y); smoke is x ×
  colour^2.2 over what is behind at alpha sat(x × alpha)^`fadeRoughness`,
  steam adds min(x^((`fadeRoughness` + 1)(1 − sat(alpha − 0.1)) + 0.001), 1)
  × `intensityMultiplier` × colour^2.2 (alpha is how hard its edge is);
- `standard_` ones (sparks): texture × `diffuseColour`^2.2, their colour left
  out (their vertex shader does not read it; `…_VERTEXCOLOUR` ones do).

The textures (`_c`) are decoded with the sRGB curve before they are filtered,
as the glass's lit sphere (not checked for smoke's channels). Measured: the 25
health pad's smoke on Ruin (game camera ruin_2) comes out within 10 % of the
game's (0.153, 0.124 against 0.146, 0.113 in red and green, a box over the
smoke; 0.088, 0.054 before, with none drawn); the braziers of the effect-scale
test map (build/reflex-sweep/game-run-effectscale) seen from above within
10 % (means over each brazier) with tongues of the same size; from the side
the page's flames reach higher above the rim than in the game's frame (2 to 4
times the light above it); wall torches (Ruin), the shards' smoke and the
steam (SkyTemples, Ashur) look as in the game's shots. Not drawn: their motion, the soft edge where a soft
particle meets a surface (`…_PARTICLESOFT`), the red's scroll, colour ramps,
sparks stretched along their velocity (they are squares), collisions;
`instanceColours[0]` is taken as white. A flipbook whose frame does not run by
age (the record's flag at +519) takes the view's x in the game; the page picks
a frame at random.

## Controls

Playing (the page opens a map so): click the scene to capture the mouse or
drag to look, WASD walks, Space jumps, Escape releases the mouse, and keys
1–9 put the player at the map's spawn points. The player starts where the
camera is (as the game's play mode starts where the editor's camera was), or
at the first spawn point when a map opens, and comes back there after falling
out of the map. **F** flies instead (WASD, Space or E up, Shift or Q down, the
wheel for speed, and 1–9 also the camera the map ends on, the Target its
WorldSpawn names as `targetGameOverCamera`); F again walks. FOV (as the game's
`r_fov`: horizontal on a 4:3 frame, so 110 shows 124° across at 16:9), invert
and Reset view are above the scene.

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
move the texture a grid step (left and right along u, up and down along v;
the offset changes by 16 times the grid, as offsets are 1/16 unit),
Home and Insert scale it up and down along u by a quarter, End and Delete
along v, PgUp and PgDn flip it along u and v (the scale's sign), and `,` and
`.` turn it by the angle step. Each press is a step of undo, for all the faces
at once. A textured face shows the change in its texture; one whose material
has none shows its texture coordinates as a pattern: tiles of 64 units shaded
red along u and green along v, chequered every 16.

Those coordinates follow the game's mapping as measured on test maps in the
game (`texcoords` in `brush.js`): u = (p · U + offset u / 16) / scale u in
world units, the same for v, so the texture is anchored to the world, one
repeat is 128 units at scale 1 and an offset of 16 moves it one unit toward
−U whatever the scale (64 moved it 4 units at scales 2, 0.5 and −1). U and V
come from the face's outward normal n (in the map file's coordinates):

| Face looks along | u grows toward | v grows toward |
| --- | --- | --- |
| −z | +y | +x |
| +z | +x | +y |
| +x | +y | +z |
| −x | −y | +z |
| +y (a floor) | −x | +z |
| −y (a ceiling) | +x | +z |

In general U and V come from the axis n is nearest: U = (0, 0, 1) × n and
V = n × U, except when n is nearest ±z, where U = (1, 0, 0) × n looking
along −z and (0, 1, 0) × n along +z. So slopes keep square cells, and u flips
where the nearest axis changes: a face turned 44° from −z toward −x has u
growing up, at 46° down. That was checked in the game on 30 tilted faces
(walls turned up to 46°, slopes of 25 to 60°, floors and ceilings tilted
toward each side, both sides of each 45° boundary), by the way an offset moved
the texture across the seam with a plain twin. A rotation turns U and V
right-handed about n (+90 on a face looking along −z turns u from +y to +x). The
arrows step as the game's do (Right adds 256 with a Snap Distance of 16). Two
things differ on purpose: the game's `,` and `.` turn by 90 whatever the
Angle, and its keys change every face of the selected brush; the page keeps
the angle step and the face under the cursor or picked.
The stock maps' scales other than 1 are mostly quarters and their rotations
mostly multiples of 45.

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

- **Textures**: normal maps and the game's special shaders (refraction, the
  holograms' noise and scan lines) are not drawn. The texture mapping was
  measured in the game; on slopes the direction of v is taken from u × v = n,
  as on the axis faces, not measured by itself. Library textures show in
  Skinner only.
- **Light**: spot lights cast no shadows (the game's do: Aerowalk's right wall
  is about 30 % too bright); the sun has two shadow maps where the game has
  four cascades, and alpha-cut leaves shadow as solid; reflection probes are
  taken from the point's cell only, where the game blends the eight around
  it; SSAO is not drawn.
- **Open from the 2026-10-03 comparison** (14 spawn views of AbandonedShelter,
  Ruin, SkyTemples, ironguard, TheCatalyst, Hieratic and Ashur, game against
  page):
  - Done since: bloom, fog, holograms, ammo glows, the teleporter ring
    (orange, as in the game), water (SkyTemples' floor is a 4-unit pool: blue
    now, its shaded parts matching the game's within a few levels), stars and
    cloud drift. The grey veil over the top of Ruin's first spawn (taken for
    sky above the pillar) is gone in the new frame; its cause was not found.
  - Ruin's warmth near the braziers was their scale (1.5), which the game
    applies to their lights' reach (see Lights); overrides, measured since,
    were right. Where only the probes light Ruin's floor it looked about a
    third less red than the game's: the game's screenshot had been read with
    a 2.2 gamma, but its frame is encoded with the sRGB curve (see Light,
    the swap chain). Read with that curve, and drawn with it, the floor's
    colour matches: (1, 0.333, 0.078) in the game, (1, 0.355, 0.089) on the
    page. It was dimmer on the page, by 1.5 to 1.7 times on the floor and
    about 1.6 over everything the probes light there, less where more of a
    pixel is reflection: `lightmapGain`, which multiplies the probes'
    diffuse light only, is 2 in the game (fitted, see Light; where
    `reflex.exe` sets it was not found) and the page took 1. With 2 the 14
    views' mean difference from the game fell from 0.0364 to 0.0265 (top
    corners 0.0229 to 0.0166), none worse.
  - Pickup holograms (Ruin's weapons and armour) came out as white blobs
    where the game shows their shape: the band that runs down them was drawn
    at its brightest, not at its average over time (see The frame:
    Holograms). Their blend (added) and depth state match the game's; the
    game draws their back faces too, which the page now does. What differs
    now is mostly the angle, as the game turns them.
  - A map without a `.light` (a test map) was lit evenly on the page, where
    the game lights its walls and ceiling darker and bluer than its floors.
    The game does not capture the room for it (`probes_convolve_sh_FLATBAKE`
    is the editor's bake): it fills the probes with one fixed light of three
    parts, the same in a black room as under open sky, which the page now
    draws (see Light, the layout bullet).
  - SkyTemples' floor under the water had no sun on the page: the shrubs'
    leaf cards shadowed it whole. Models now cast with their shadow meshes
    (see The sun) and the floor is sunlit as in the game. Its paper lanterns glow now (see
    Lights: light through paper), as bright as the game's since the sun
    follows `sky.sunColor` and the paper takes no vertex colour.
  - Light beams and the pickup pads' glows (`alphaFresnel`) are drawn on
    models and on brush faces (Phobos' beams; with mul 0.025 and pow 8 they
    are as faint as their shader makes them). On Phobos they are only the
    8-unit edges of the doors' window panes, seen nearly edge-on; what
    looked like a missing beam glow there is the panes' glass (next item). Their vertex shader also multiplies by the object's
    `instanceColours[0]`, which the page takes as white. What was brighter
    at the foot of Ruin's 25 health pad in the game was the pad's light and
    smoke, which the import missed (it read the effect's first list, see
    Models); the light is drawn now (next to the pad the game is 1.18 times
    the page, against 1.35 before and 1.5 out of its reach: the gap of
    probe-lit faces above). The smoke, a yellow wisp up the glow, is drawn
    now (Particles).
  - Glass was nearly black on the page, lit as a surface of its black
    albedo, and glass_simple solid; the game's glass is milky white
    (Phobos' door panes). Both are drawn with the game's shaders now
    (Forward shaders, above). On Phobos the panes come out at 0.46 and 0.42
    against the game's 0.52 and 0.46 (means, from the game camera of
    build/reflex-sweep/game-run-phobos): about 10 % short, where the test
    maps match; the rest is what lies behind the panes and the gap of
    probe-lit faces above. That whole shot is
    yellow-olive in the game and grey on the page; not looked into.
    Phobos' WorldSpawn names no colour grading; it has an orange fog
    (d67117, end 7234, the default heights: top 0, bottom −8192) and a
    yellow sun (faad19), where Ruin's fog is blue; candidates.
  - In the Phobos game run `cl_show_hud 0` did not hide the HUD (it was
    typed after the map had loaded; the same steps hid it on Ruin, and on
    the glass test maps, typed 4 s later).
  - Bright things (lights, the ring, holograms) are drawn brighter on the
    page than in the game: pixels the page shows at 96 and over come out at
    0.65 to 0.85 of that light in the game (Ruin and SkyTemples, spawn
    shots; the sRGB and 2.2 curves agree up there).
  - Sky colours other than the defaults were checked on AbandonedShelter and
    SkyTemples only. The cloud colour is not dimmed by the time of day: an
    overcast sky (coverage 1) at 17:00, 12:00 and 8:00 is `sky.cloudsColor`
    (bytes / 255) in the game and on the page alike, within 0.1 %. Why
    SkyTemples' clouds look darker in the game is not known (not checked).
  - Ashur's teal ceiling over the teleporter (game camera ashur_0) was
    never the page's: it shows only in headless renders (Chromium with
    SwiftShader), where the big brick face over the camera is clipped
    wrongly, hides the beams under it and takes one corner's position
    across the screen, inside the portal light's reach. On a GPU the page
    draws the beams and the ceiling as the game does. Check a headless-only
    oddity on a GPU before chasing it.
  - Ashur's Reflex logo behind its teleporters (`powerup_resist`) shows
    through the portal's sheet on the page as a pale ring; in the game it is
    barely there (8 % over the sheet in red, 2 to 3 % in green and blue).
  - Fire, sparks, smoke and steam are drawn at one moment (Particles): from
    the side the page's brazier flames reach higher above the rim than in
    the game's frame (2 to 4 times the light above it) while from above they
    are within 10 %; not known why (their physics are the game's; the game's
    frame is one moment too).
  - The rotation order (roll, pitch, yaw about the thing's own axes) was
    checked on pitched props (Ruin's pads, AbandonedShelter's floor lights);
    a prop with all three angles was not checked on its own.
- **Models**: animations are not played (fans, items' bob and turn); a skinned
  mesh shows its rest pose. Particles are drawn at one moment and do not move
  (see Particles). Entities the game's editor has no model for (a camera
  path) show as coloured markers while editing.
- **Sky**: packs imported before the cloud dome and the star texture were
  imported have neither until imported again; packs imported before material
  flags draw every forward shader added and holograms with a guessed
  gradient. Packs imported before shadow meshes, particles and `sss` cast
  model shadows with what the models show (leaf cards too), draw no flames
  and leave paper unlit from behind until imported again; packs imported
  before the emitters' fields (October 2026) draw no particles until then.
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
  thumbnails from made-up `.textureset` files), the Reflex texture library's
  routes, the routes, and the Node tests when Node is installed. Set `REFLEX_GAME_BASE` to a Reflex Arena
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
