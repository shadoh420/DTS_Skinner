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

This is an **incomplete preview**, not a faithful reproduction of the requested game start. The report sets `complete: false`. Authored terrain, material colours, landmarks, brigade layouts, aircraft types and formation offsets are read. Native sky/weather, palette lighting and haze distance, procedural clutter, damage appearance, campaign/date movement and simulation warm-up remain unresolved. The game and browser have not been run for this work.

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

## Lighting and seasons

Summer mounts `rb.vol`, then `3dpatch.vol`, exactly as the model importer does. The season mount at `004171D0`–`004171FA` selects fall or winter; ground/cloud/effect SPL banks are loaded at `00417495` onward. Probed seasonal volumes contain:

| Volume | Members | Non-bitmap members |
|---|---:|---|
| 3dfall.vol | 489 (486 BMP) | BAT, `rb3dgnd.pal`, `rb3dgnd.spl` |
| 3dwinter.vol | 487 (484 BMP) | BAT, `rb3dgnd.pal`, `rb3dgnd.spl` |

They change palette/shading data too, so there is no season switch in this version. BAT members are never executed.

Fog colour is palette index **47** for all five banks (`005830A4`, installed by `00444BB7`; consumed at `004ED33D`; Glide fog colour call `004EEE6F`). The sampled summer/ground palette yields RGB **159,183,195**. The preview reads this colour from the mounted ground SPL, rather than hardcoding it.

The rest remains incomplete. The adapter draws palette-coloured textures unlit. Its linear 12–18 km haze and solid background are clearly labelled preview defaults, **not measured game fog or sky**. `004447A0` clamps a global palette haze value to 0–255; `004ED320` combines palette and per-vertex fog, and `004EEE59` enables Glide fog. Native shade ramps, time-of-day palette transformations, distance mapping and sky/cloud rendering are not yet reproduced. DAT day/time/weather remain in scene metadata for the next comparison.

## Verification

`tests/test_redbaron_maps.py` builds tiny VOL/TED/MIS/DAT fixtures in plain `tempfile` directories. Targeted tests cover numeric trees, malformed input, coordinates, aircraft fields, formations, companion state, briefing boundaries, read-only input, report accounting, failed-refresh preservation and local import routes. Optional install probes are disabled unless `RB3D_INSTALL_TESTS=1`, and skipped when the install is absent. They inspect one root cell and one MIS, never import the install.

Follow-up 1 adds a read-only parse census of all five TEDs and all 27 MIS/DAT pairs, including effective material ranges, texture-name existence, height sentinels, brigade states and start-time formations. No retail texture or geometry export was run. Python 3.14's sandbox `tempfile` permission issue required a helper under ignored `build/rb3d/`; no workaround is present in committed tests. Full import, browser responsiveness, rendered seams, game lighting and mission-start comparison belong to Claude's next validation pass. The ignored `build/rb3d/HANDOFF.md` records per-file results and exact next checks.
