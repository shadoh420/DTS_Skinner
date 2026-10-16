# Red Baron 3D maps

Research and implementation note, 2026-10-11. The reference is the Steam Sierra/Dynamix Red Baron 3D install and its `Baron.exe` (SHA-256 `e5538ef3a521c63710054ecd654dc553feb6cc02961b6fa619054e1a2cd065f3`). Addresses below are virtual addresses in that executable. These are static disassembly and bounded file-probe findings, not a comparison against a running game.

## What the page reads

**Red Baron 3D Maps** uses the T1 map viewer's camera, input, settings and import controls. An optional RB3D adapter supplies terrain streaming. T1 is the closer fit: it already combines a heightfield and placed shapes with free flight; the Unreal viewer's BSP paths add nothing needed here. T1's existing defaults and render path remain unchanged when the adapter is absent.

Import from the page or run:

```powershell
python tools/import_redbaron_map.py --install "C:\Games\Red Baron 3D"
```

Output is `<LOCAL_DATA>/rb3d-maps/`. `--output` overrides that folder. `--replace` writes a new generation and switches the index after the manifests and report are written; old generations remain for open pages. The install is only read. No retail data ships with Skinner. Model volumes, LZH, shapes, DMLs and bitmaps use the existing RB3D/T1 readers; the RB3D model catalog and edited model textures are not touched.

`Western Front` contains a region selector and landmark jump points. Each region keeps the game's own coordinate system; the four front regions are **not stitched into one continuous mesh**. The smaller multiplayer terrain is also selectable. Chunks follow top-level TED squares (about 10 km each), retain the finest authored squares, and stream around the camera. The adapter permits two simultaneous chunk loads and at most 25 resident terrain chunks, disposing evicted GPU resources. Mission aircraft/brigades are separate static instances.

Every `Single/*.MIS` is attempted, with its companion DAT and NUL-terminated DSC briefing. Failed missions remain in the map list with their reason. Shared asset failures also produce a row for every TED and MIS. Placements outside root terrain cells are counted and retained as `outsideObjects` in the region manifest, rather than silently discarded. Later flights are listed in scene/report metadata rather than spawned before their authored time. Campaign missions are skipped: this importer does not run their generator.

This is an **incomplete preview**, not a faithful reproduction of the requested game start. The report sets `complete: false`. Authored terrain, material colours, landmarks, brigade layouts, aircraft types and formation offsets are read. Terrain normals, sun direction and distance haze now follow the recovered Glide equations described below. Native sky/weather animation, time-of-day palette recolouring, procedural clutter, damage appearance, campaign/date movement and simulation warm-up remain unresolved. Claude supplied game/browser comparisons; no game or browser was run for this implementation.

## Terrain volumes

The five files are areas, not alternate detail levels. Each contains its own multi-level terrain tree. Geographic names below describe the covered front, rather than claiming a recovered UI label.

| TED | Area | Bytes | Live VOL entries | Native extent | Geographic origin (lat, lon) | Landmark rows |
|---|---|---:|---:|---|---|---:|
| BARON0 | Marne / central front | 13,306,716 | 516 | 4,194,304 square | 48.424999237, 2.516666889 | 226 |
| BARON1 | Flanders / northern front | 26,164,972 | 708 | 4,194,304 square | 49.863445282, 1.819324970 | 410 |
| BARON2 | Verdun / Lorraine | 12,824,480 | 516 | 4,194,304 square | 48.335098267, 4.545241833 | 211 |
| BARON3 | Alsace / southern front | 12,198,812 | 520 | 4,194,304 square | 47.705776215, 5.856603146 | 238 |
| BARON4 | Multiplayer | 664,096 | 36 | 1,048,576 square | same as BARON0 | 51 |

Native units are the model importer's 1.5 inches (`0.0381 m`). Front terrains are about 159.8 km square; BARON4 is about 39.95 km. Its generic `Aero 1a`, `Village 1a`, etc. belong to a separate area. Do not overlay it on BARON0. The earlier inference that it was training terrain was wrong: its materials require the multiplayer resource bank; the shipped tutorial missions use BARON0–3.

| Structure | Measured layout | Executable evidence |
|---|---|---|
| VOL chunks | Tag + 24-bit size + flag byte; `volh`, `vols`, `voli`, `VBLK`. Numeric index records `<IIIBB>`: ID, VBLK offset, expanded size, compression, named flag | shared VOL reader and `0049C230` |
| `vols` | 6,404 bytes in sampled files; **used name length is zero**. The remaining bytes are unused storage, not the terrain table | numeric IDs / named flag zero |
| ID 100 | 52 bytes, version 100; scale exponent +8; four signed bounds +20; height range +44/+48 | `0049C230` |
| ID 200 | version/count then 48-byte records; ID is the second u32. After all records, row fields 0 and 10 conditionally introduce length-prefixed name and material strings. Pointer-shaped fields are saved process addresses, not file offsets | `00543570` |
| ID 300, 500… | Nine u32: version, ID, selected level, secondary selection, base level, top level, low, high, shared-height flag. If flag zero, 256 signed heights follow. Two final u32: inherit-material flag and level count. Binding a directory DML clears the runtime inherit-material flag | `0049EC10`, `0049EBC0` |
| ID `block OR 0x40000` | version; 24-byte descriptors for every level: saved pointer, node count, log2 row width, log2 row height, regular flag, stride; then `count * 32` node bytes for each level | `0049ED30`, `0049EE56` |
| Node | Four 8-byte squares, quadrants x + 2*y. Square `<H6B>`: tree reference, four height indices, **material byte**, flags. Material 255 inherits the ancestor's DML, slot and texture footprint | `004F9E5E`, `004F9D71`, `004FA901`–`004FAA0A` |
| Square type | high two bits of reference: 0 surface; 1 next finer node; 2 another terrain block; 3 hole. Low 14 bits are the node index/block ID for types 1/2; ignored for type 0 | `004F9E07`–`004F9E30` |
| Cell scale | `2 ** (worldScale + blockBase + level)` native units. Root base 7 / world scale 11; finest child level commonly 2,048 units (78.03 m) | traversal plus stored descriptors |

Corner order is `(0,0), (0,1), (1,1), (1,0)`, checked against shared neighbour heights. Shared height tables inherit from the immediate parent block (`0049B910`). Coarse vertices survive subdivision. Height `0x7fffffff` means an interpolated midpoint: average its endpoints, or the lower diagonal sum for the centre, with signed division truncated toward zero (`004C4BF7`–`004C4C0E`, `004C4F30`). These are resolved before export. Square flag histograms are saved per chunk. Triangle diagonals, material mirror/rotation direction and cracks at differing leaf sizes still need rendered comparison.

The DML name resolver replaces an initial `s`, `f` or `w` with the season letter, or prepends the letter (`0043F39B`–`0043F41A`). Thus BARON4's `multi2.dml` requests `smulti2.dml`; it is present in `Data/Multi/rbmmulti.vol`, not a generated or aliased material. BARON4 needs that bank's 252-slot `sbaron.dml` and `scoast.dml` too, whereas single-player uses 248/250 slots. `Baronmmp.exe` mounts `rb.vol` in slot 6, `rbmulti.vol` in 5 and `rbmmulti.vol` in 4 (`00480F32`–`00480F5A`), with first-match lookup from slot zero (`0043A174`–`0043A1B7`) and the 3D patch in slot 2 (`0045E5F9`). The map importer applies this bank only to BARON4. The model importer and the other four regions keep their existing mounts.

Rivers, roads, devastated ground and forests appear in the terrain DML bitmap families (`River*`, etc.). Their authored material assignments are exported as terrain, without inventing separate water or trench meshes. This does not establish that every front-line feature is terrain: brigade and procedural-object paths also exist.

## Placements and missions

| File/record | Layout and interpretation | Evidence |
|---|---|---|
| DAT table | 100-byte header, u16 row count/stride at +20/+22 | loaders and exact file sizes |
| `LNDMRKS0..4.DAT` | 63-byte rows: i32 ID; u8 kind, variant, side; float latitude, longitude, altitude feet, heading degrees; 40-byte NUL-terminated name | `005047D0` |
| `BRIGADE0..3.DAT` | Same geographic rows, military layouts. Sample BRIGADE1: 33 rows; ID 21074 is `11th B` | DAT probe, `004C9680` state lookup |
| `LO02kkvv.DAT` / `LO01kkvv.DAT` | Landmark / brigade composites. 20-byte rows `<BBB4fB>`: class/index/variant, x/y/z feet, heading, flags. Nested layouts use `LOcc iivv`; leaves use shape `ccii80vv.dts` | `004C2660`, `004C2700`–`004C27F6` |
| Geographic conversion | x = `(lon-originLon)*cos(lat*0.017453292522222223)*2915860.391`; y = `(lat-originLat)*2915860.391`; altitude feet *8. Viewer axes are `(-x,z,y)` times SCALE | `00524190`; origins `0058A510/0058A528`; constants `0056C8D8/0056C8E0` |
| `OBJDAT.DAT` | 194 rows of 100 bytes: object properties, not a world placement list | bounded probe |
| `EXCEPT.DAT` | 4,361 pairs of numeric resource IDs. Fill absent shape/material/texture names through explicit aliases; preserve actual mounted overrides | loader `004F7528`–`004F75C0` |
| MIS header | 17 bytes: 8 reserved zero bytes, u32 player flight ID, u8 player slot, u32 flight count | `0043DB50` |
| Flight | 216-byte record: ID +0, squadron +4, kind +20, start ms +24, count +28, waypoint count +192; if pointer flag +204 is nonzero, read extra i32 linked-flight ID | `0043DDC0` |
| Waypoint | 46 bytes: ID +0, saved links +4/+8, native x/y +12/+16, ground height +20, time ms +24, **flight altitude +28**; formation +34, near formation +40, action +41, target ID +42 | `0043DE31`; altitude proved by `0052F81D`–`0052F838`, `0042B4E6` |
| Flight tail | selected/objective waypoint ID; **12 aircraft-type i32**, 12 pilot-reference i32, then 12 signed flags, each nonnegative flag followed by exactly three state bytes | `0043DF3D`/`0043DF90`, `0043E011`, `004D3460`; aircraft use `0042B547`–`0042B54D` |
| Companion DAT | saved-state byte, Julian day u32, time-ms u32, variable pilot record, region u32, weather byte; when saved-state is set: brigade count and states; extra pilot count and records. Loader stops after declared records | `004EFAF0`, `004EFB44`, `004C8C00`, `004E49D0` |
| Pilot | 79 bytes, two u32, 39 bytes, squadron u32, five bytes, two count-prefixed 16-byte history lists, 29 bytes | `004E69B0`/`004E6D70`/`004E7140` |
| Brigade state | ID u32, 3 flags, day u32. Active: layout-present byte; if present, one record per immediate layout child: two u32, blob-present byte, optional u32 byte count + blob | `004C9680`, `004C2170`, `004EF710`, `004CF090` |
| `FORMATNS.DAT` | 17 rows of 147 bytes: min/max/preferred/count-parity flag, then eleven `<B3f>` parent/bearing/elevation/distance records. Parent is one-based. Offsets accumulate through parents | `004D1860`–`004D1A4F`, `004D17A0`/`004D17E0` |
| Formation scale | Initial spacing index 2 selects multiplier 2 from [.75,1,2,3,4]. Spherical offsets are feet, then rotate by heading and multiply by 8 | `0052F7D2`, `00585CD0`, `00523A00`, `0042B896` |
| DSC | First NUL-terminated text; trailing fields are not briefing text | sampled MISSION1.DSC, 152 bytes |

The ACE1 MIS is 1,027 bytes with two one-plane flights, aircraft types 36 and 10. Their first authored altitudes are 40,000 native units (1,524 m). ACE1 DAT is 242 bytes: its state contains brigade 21074 and four empty child states. The implemented reader consumes both to their exact end. Native MIS loading stops after the declared flights, resolves linked IDs and closes the stream (`0043E0E5`–`0043E1F7`); there is no EOF requirement or additional tail record. MISSION9/C/D contain respectively 230/9/6 unused bytes at offsets `0x7b7`/`0x406`/`0x431`. The importer records their lengths and hashes. DAT similarly stops after declared state/pilot records; MISSION1/3/C/D/E have 516/19/344/172/344 unused bytes. Neither suffix becomes a live flight, pilot or object.

The game does not simply open at DAT noon: `0052F350` warms up earlier flights in five-second steps, then advances in one-second steps until the player spawns, and `0052F720` creates each flight at its first waypoint. The preview uses the authored player's start time, interpolates earlier flights along timed waypoints, and applies the stored formations. It does not reproduce warm-up ticks, special takeoff placement, linked flights, near-target formation switching or first-tick heading exactly. Damage state is parsed and retained, not applied to meshes. These are acceptance gaps, not just unrun tests.

## Drawables, landmark views and texture sampling (Follow-up 2)

The layout stores class/type/variant bytes, not a literal DTS filename. In particular, class **08/type 0a** is not a missing trench model: the factory selects the class-08 constructor (`004EF317`–`004EF34D`, `004FCB60`), which clears its drawable pointer at `004FCBB0`. Initialisation compares type against `0a` at `004FCDA1` and jumps over drawable construction at `004FCDA3`. Other types create the drawable at `004FCDDA`. Thus the importer's synthesized `080a8000.dts` name is never requested by this native path. This establishes absence of a static mesh, not absence of simulation or transient effects. The actual slit-trench shape is class 08/type 04 (`PNAMES.DAT` entry 804).

These placements remain in the scene metadata with `nativeNoDrawable: true`, a report warning naming the shape and per-TED/per-mission `nonDrawableObjects` counts. The viewer omits null models instead of drawing diagnostic boxes; other genuinely missing shapes still appear in `missingShapes`. Flanders has 760 such brigade placements, matching ACE1. All other geographic/brigade shape names resolved in the parse-only census.

Landmark jumps frame the placed layout's centre and extent from above and behind, with a downward view and a 100 m building margin. They are preview navigation, not native game cameras. Type labels follow the shipped layouts: 0 airfield, 1 town, 2 military depot, 3 rail yard, 4 bridge, 5 factory, 7/8 village. These descriptive names are inferred from layout contents and landmark names, not a recovered UI enum. Toulis is type 0/variant 7, `lo020007.dat`: 62 objects using 15 models, all present in Claude's exported `regions/0/232` chunk. Its old jump was directly over the origin looking horizontally. The new view contains all 2,718 exported instance vertices within a 60-degree square camera frustum by a read-only numeric check. New camera matrices require reimporting the pack; type labels and omission of null models also work on older packs.

The common RB3D texture loader now uses nearest minification/magnification, repeat wrapping, no generated mipmaps and anisotropy 1, for terrain and shapes. Wrapping and single-level uploads are measured in the executable. **Nearest filtering follows Claude's observed game capture, not the executable's static default:**

| Setting | Native evidence |
|---|---|
| Filter | `_grTexFilterMode@12` resolves to pointer `00588120` at `004EC186`. `004EEE92`–`004EEEA1` sends the same min/mag enum, 1 if global `00588064` equals 1, otherwise 0. Its file initialiser is 1 (Glide bilinear); the observed blocky in-game texels therefore conflict with that static default. No runtime value was measured here. |
| Wrap | `_grTexClampMode@12`, pointer `00588184`, is called with both clamp enums 0 (wrap) at `004EEEA7`–`004EEEAC`. |
| Mips | `004ECCCA`/`004ECCCF` fill the small and large LOD with the same texture-entry value, then `004ECD00` calls `grTexDownloadMipMap`; `004ED570`–`004ED5AB` binds a single LOD. No `grTexMipMapMode` binding was found. This does not justify browser-generated mip chains. |

The filter setup is called by texture-state branches at `004EEFC2`, `004EEFFB` and `004EF07A`; it is not merely an unused import. The remaining discrepancy needs a runtime Glide-state capture or an nGlide/settings comparison by the game reviewer. Ground colours, palette lookup, sky/haze colour and fog distances are unchanged in this follow-up. Claude accepted the existing ground and horizon colours in ACE1; that does not establish the remaining lighting/time/weather paths below.

## Lighting and seasons

Summer mounts `rb.vol`, then `3dpatch.vol`, exactly as the model importer does. The season mount at `004171D0`–`004171FA` selects fall or winter; ground/cloud/effect SPL banks are loaded at `00417495` onward. Probed seasonal volumes contain:

| Volume | Members | Non-bitmap members |
|---|---:|---|
| 3dfall.vol | 489 (486 BMP) | BAT, `rb3dgnd.pal`, `rb3dgnd.spl` |
| 3dwinter.vol | 487 (484 BMP) | BAT, `rb3dgnd.pal`, `rb3dgnd.spl` |

BAT members are never executed.

**Seasons.** `004EFB60` takes a mission's season from its DAT date: 20 September to 20 December is fall, 21 December to 19 March winter, the rest summer. The importer writes one pack per season, each a whole Western Front (`western_front`, `western_front_fall`, `western_front_winter`), and exports each mission into the pack of its date's season. A season pack mounts its volume over `rb.vol` + `3dpatch.vol`, which replaces the terrain bitmaps and the ground palette and SPL bank (so its haze and sky colours), and the terrain takes that season's DML lists (`fbaron.dml`, `wbaron.dml`) where they exist; BARON4's multiplayer bank has only summer lists and keeps them. An install without a season volume imports summer only and records a warning. `--season` limits the import to the named seasons.

Fog colour is palette index **47** for all five banks (`005830A4`, installed by `00444BB7`; consumed at `004ED33D`; Glide fog colour call `004EEE6F`). The sampled summer/ground palette yields RGB **159,183,195**. The preview reads this colour from the mounted ground SPL, rather than hardcoding it.

### Terrain light and haze (Follow-up 3)

Glide terrain uses computed normals, not a shade byte stored in each TED square. `004C42D0`–`004C4428` takes half the difference between the two neighbouring heights on each axis, uses the grid spacing for the vertical component, and normalizes. A missing neighbour uses the current height. The on-disk DML shading mode is at material record +16 (runtime +1c): 0 unlit, 1 flat, 2 smooth. Although the sampled terrain DMLs store 1, the **Glide mode-4 override** at `00440033`–`00440050`, `00440162`–`00440177` selects 2. `00522063` identifies renderer 4 by its Glide surface constructor. The importer exports smooth height-field normals with neighbouring-cell queries and a nine-cell cache. Samples use the finest world grid; native camera-dependent LOD and its coarser normal sampling are not reproduced. `005437D0` selects the lower diagonal of a saddle; chunk triangles and height interpolation now follow it.

The scene initializes ambient RGB to `0.5 * 65536` (`00532DD8`, `00532F17`–`00532F49`). The directional evaluator adds `max(0, -dot(normal, lightDirection)) * intensity * 65535` (`0051B6E0`–`0051B7DA`). The result is rounded and capped at 65535 (`004D3900`, `00547EB0`); Glide receives RGB divided by 256 (`004ED100`–`004ED17E`, flat-face equivalent `004ED190`). The adapter applies the equivalent multiplier, capped at white. Neutral terms therefore leave the accepted texture/palette colours unchanged. **The `vertex +4 >> 7` term at `004ED3BA` is fog alpha, not terrain light.** The RGB shade terms are +8/+c/+10. The Glide RGB path does not need the software renderer's SPL shade-table lookup.

Sun direction comes from DAT Julian day, regional latitude/longitude, and the authored player's start time. The importer ports the game's simplified celestial calculation (`004AAF90`, `004D0A10`, `004ABAC0`, `00523AD0`), including the single-precision epoch store. It does not substitute a modern astronomical library. Elevation-dependent sun/moon brightness is evaluated by `0049E9C0`; the scene intensity is `min(1, sun + .2 * moon + .05)` (`00409CF0`). The light elevation is clamped to at least 11.25 degrees (`0049EADC`). Mission warm-up remains approximated by the authored birth time. Free flight uses the existing sample day at noon, recomputed for each region.

Terrain haze uses **radial camera distance per vertex** (`004C5081`), a 0–65535 ramp (`004C508D`–`004C50C6`, `0048C350`), then `>>7` and saturation at 255 (`004ED3BA`–`004ED408`). Full Glide haze thus occurs about halfway through the nominal ramp. The vertex shader computes that value; RGB and haze use screen-linear interpolation while texture UVs retain perspective correction. The accepted SPL index-47 haze colour is unchanged.

`0043EF20`–`0043F2DB` computes the range. With world-grid exponent `s`, visibility slider `v` in 0–1, and camera altitude `h` in native units:

```
baseCells = trunc(.35 * 52800 * 8) >> s
cells = trunc(trunc(baseCells * (.65 + .35*v)) * (1 + max(0, h*.000005)))
view = cells << s
start = trunc(.7 * weatherStart * view)
end = trunc(weatherEnd * view)
```

Weather codes 3/4/5 use start/end multipliers `.75/.9`, `.5/.75`, `.25/.5`; other codes use `1/1`. Distances are converted with SCALE. The adapter uses the binary's default maximum visibility (`00580858 = 100`, `00411B30`) and updates the range as the camera climbs. At sea level on the shipped exponent-11 terrain, nominal start/end are **3932.644/5618.074 m**, with full Glide haze at about **4772 m**. These replace the 12–18 km preview. The haze checkbox still works. The import does not read the user's graphics preferences, so a changed native visibility slider must be accounted for when comparing.

Shapes retain their own DML mode: unlit parts stay unlit; flat parts use their authored face normals, and smooth parts use authored vertex normals (`0051D112`–`0051D153`, `0051D170`–`0051D23F`). The shared model reader gains an optional smooth-normal callback; existing model-catalog callers and output are unchanged. Only the maps importer opts in. Both shape and terrain materials use the same scene sun and haze shader. Palette recolouring at dusk/night, transient global haze overlays (`004447A0`) and moving cloud shadows remain outside this implementation; the sky's cloud band and sheets are below.

### Sky (Glide)

`[0x58a434]` is the render mode; 4 is Glide (`00522039` opens `glide2x.dll`), the path the 3D patch and nGlide use. The sky manager (`004B5270`) keeps the cloud type in `+0x18`, set from the weather (`004B4C61` → `004A6880`). In Glide it draws:

- **The horizon cloud band** (`004B40F0`, `004B41E0`, `004B4440`). Sixteen upright panels sit π/8 apart on a ring of radius 2500 × 8 = 20000 native units (762 m) round the eye.
  - Size and height: each panel is centred 2500 units above the eye (`0x451c4000`). It is one chord plus 2% wide (`sin(π/16) · R · 1.02 · 8` in the sprite's 1/8 units, centred by `00527510`). It is 2500 units tall, or 5000 in weathers 3–5.
  - Texture: panel *n* shows tile *n* mod 8 of `hz-cld<weather>` (`004B4380`): tiles 0.25 × 0.5, four across and two down, from v 0.01. The 3D patch's `hz-cld0-5.bmp` carry their own cloud palette and `.aph` alpha. A missing number falls back to `hz-cld0`.
  - Placement: the game draws the five panels round the view heading. The page draws all sixteen, which looks the same. The ring moves with the eye and never turns.
- **Cloud sheets** (`004BDA46` by weather, `004BD710` per layer, `004BDFD0`).
  - Size and texture: flat `csheet<weather>` squares 80000 units (3 km) across, with a random yaw.
  - Layers by weather: weather 1 has one layer at 160000 units (6.1 km); weather 2 has 120000 and 64000; weathers 3–5 have 120000, 80000 and 56000. Weather 0 has none.
  - Grid: a 4 × 4 grid of 131072-unit cells, starting three cells below the eye's cell.
  - Each cell runs its own `004BD5A0` stream. That is three congruential sequences (×179 mod 32771, ×183 mod 32779, ×182 mod 32783) summed mod 65536. The cell is empty when the first number is at most 0.25 (weathers below 3). Otherwise it has one, two or three sheets (next number below 0.75, below 0.95, else). The first sheet sits at the cell corner; each further one moves ±65536 across and ±20000 up from the last.
  - **Approximation:** the game seeds each cell from a field sampled at the cell (`0043FD10`, not decoded). The page seeds from the cell and layer, so the layout has the same density and sizes but is not the game's copy.
  - The sheets take the scene's haze. At low altitude a 6 km layer lies past the haze's end, as in the game.
- The software renderer's tiled high deck (`tl-cld<n>`, 200000 units up, `004B30F0`) is not created in Glide (`004B46EE`), and the page does not draw it.

- **The sun** (`004D05B0`, drawn by `004AB700`): `Sun.bmp` with its alpha, 32 pixels of the game's 480-line screen at the sun's projected point, behind everything, while its elevation + 3 is above −2 degrees.
- **Sky colour.** The clear colour behind the band is index **39** of the mounted ground SPL bank, measured in game rather than traced (the Glide clear call `004ED4B0` takes a palette index through the driver table). TUTOR3 at noon reads 197,213,221 against the summer 199,215,223; ACE5 in fall reads 177,191,198 against the fall bank's 170,186,188. Fog keeps index 47.
- **Dusk and night.** Missions scale both the sky and fog colours by the scene light intensity (`00409CF0`, above), as the live palette does (`004099A0`–`00409BE0` rewrites palette ranges by light). ACE5 at sun 6.8 degrees keeps full intensity through the moon term, and matches. **Night is approximated:** MISSIONE at sun −12 degrees (intensity 0.21) reads 53,61,63 in game, 27% of index 39, where this gives 42,46,47 (21%). The game's other palette branch (`00409A67`, chosen by `[0x5924a0]`: it blends entries `0xf2`–`0xf5` by `(x − 0.5) · 2` and passes −1 to the haze factor `00444CB0`) is not decoded.

Not drawn yet: the moon sprite and stars (MISSIONE shows a few), scud layers in weathers 3–5 (`004B4CA1`), and cloud shadows on the ground (the light's 8-bit shadow map at +0xa8..+0xbc, read at `0051B6E0`; its setter is not found).

Refresh the pack to obtain normals and lighting metadata. New terrain records are eight little-endian floats (`xyz, uv, normal`), identified by `stride: 8`; old five-float packs still load with their old unlit appearance. Source measurements and numeric probes establish the implemented equations, **not rendered parity**. In particular, most vertices near the TUTOR3 birth point still saturate at white under the recovered ambient/sun light; stronger shade occurs on steeper Alsace slopes. Claude must compare the new haze and lighting against the supplied banked capture rather than treating synthetic tests as visual acceptance.

## Verification

`tests/test_redbaron_maps.py` builds tiny VOL/TED/MIS/DAT fixtures in plain `tempfile` directories. Targeted tests cover numeric trees, malformed input, coordinates, aircraft fields, formations, companion state, briefing boundaries, read-only input, report accounting, failed-refresh preservation and local import routes. Optional install probes are disabled unless `RB3D_INSTALL_TESTS=1`, and skipped when the install is absent. They inspect one root cell and one MIS, never import the install.

Follow-up 1 adds a read-only parse census of all five TEDs and all 27 MIS/DAT pairs, including effective material ranges, texture-name existence, height sentinels, brigade states and start-time formations. No retail texture or geometry export was run. Python 3.14's sandbox `tempfile` permission issue required a helper under ignored `build/rb3d/`; no workaround is present in committed tests. Full import, browser responsiveness, rendered seams, game lighting and mission-start comparison belong to Claude's next validation pass. The ignored `build/rb3d/HANDOFF.md` records per-file results and exact next checks.

Follow-up 2 adds synthetic checks for non-drawable placement accounting and landmark framing. `node tests/test_redbaron_maps_stream.cjs` exercises the real adapter and Three.js math with synthetic I/O: null-model omission, visible placed meshes, labels and pitched jumps, both texture paths, timer yields with rAF unavailable, bounded streaming and disposal. It does not launch a browser. The all-TED/all-mission parse census still passes; the separate placement census decodes no shapes or bitmaps. Rendered acceptance of the new view and sampling remains with Claude.
