# Earthsiege Maps and the wider T1 sky list

2026-10-15, branch `claude/es-maps-skies`. Pages: `/maps/es1/` (Earthsiege) and `/maps/es2/` (Earthsiege 2), both the T1
Maps viewer over packs written by `tools/import_es_map.py`. The game folders are only read; no game content is in the
repository.

```powershell
python tools/import_es_map.py --game es2 --install "C:\Games\Earthsiege 2"
python tools/import_es_map.py --game es1 --install "C:\Games\Earthsiege"
```

Output goes to `<LOCAL_DATA>/es2-maps` / `es1-maps` (`--output` overrides, `--mission C1_01` imports one, `--replace`
re-imports). The pages' Import panel does the same.

## Earthsiege 2: what is read

Sources: Kevin Foley's [Herculan](https://github.com/kevinfoley/Herculan) documentation (MIT), named per rule in the
importer's docstring. Checked against Herculan's own engine screenshot of TRAIN1 (same ridge line, palette and sky).

| Part | Rule |
|---|---|
| Missions | `.MSN` rows read as VSHELL's generator does: condition gates, variants, repeated GUIDs (rows 3/6-9 replace, 10-16 overlay set fields). Campaign flags 1-2 = the mission's stage and number; every random draw is 0, so a mission shows its first authored alternative (stated in each map's notes). |
| Theatre | Campaign `Cn` → theatre n-1 (urban, bsnow, volcan, ice, moon); practice and instant-action missions fly theatre 1. Day variant. |
| Terrain | Zone from the header patch: `dat\zoneN` (cell shift, height scale) and `dba\zoneN` (8-bit heights, image row 0 north). Per-cell diagonal selector from corner heights. |
| Ground texture | `mat0` frames 0/1 tiled (v falls with y), material 1 on the retail sparse 30% roll of DBSIM's random stream; base groups that paint ground cover their tile with the formation's pad frame. Pad bays line up with the structures placed on them (checked on C1_01). |
| Levelling | Structure footprints (shape radius) and pad layout maps flood-filled to their average, forced east/north/north-east; structures re-settle afterwards. |
| Placement | Group point, else first order's route's first waypoint; heading from the group, else the route's first leg (atan2 − ¼ turn); MFORMS/BFORMS/FFORMS offsets rotated by the group heading, BFORMS per-slot turns, pad anchor snap. Machines and structures stand on the ground at the group point / their own spot; flyers at the spawn height (5000 when none). |
| Shapes | HERCs with their roster weapon fit (hardpoint placeholders dropped), SKIMMER, `BASES.DGS` / `BASES_AN.DTS` structures in BASETEX / VEHTEX, all exported into the pack under the theatre palette. |
| Look | Light = the mission sun's terrain curve min(255, 512 cos) as the ramp row's multiplier; sky = hzline palette bands up to the horizon colour; fog from half to all of the 14-cell detail range in the horizon colour. |

Not shown, and said so in each map's notes: groups that arrive later (deployment action), the player's own HERCs (viewpoint
1 stands at the squad's point), weapons' guidance/ammo, animation (radar dishes, turrets), damage states, the
tight-cockpit FOV. Terrain is drawn with linear filtering (the game is point-sampled) and per-vertex light (the game
flat-shades each triangle).

## Earthsiege 1: what is read

From the ES1 `DBSIM.EXE` disassembly (X-32 image, linear = file offset − 0x48FD): the zone loader (0x228d0) takes
the script header's first two words as theatre and zone; the world loader (0x280b8) picks palette
`world<6·theatre + 2·word9>` and world file `+word8`; `gndtex.cpp` (0x2c150) loads the world file's fourth name as a raw
256×256 `dba\<name>.64k` ground. A zone is a list of grid shapes from `dgs\<theatre>.dgs` (desert, temper, ice, urban,
waste, ruins) placed with an int32 position and a yaw on flat ground.

**Incomplete:** mission units (HERCs, structures, flyers) are not decoded; ES1's `.MSN` rows differ from ES2's and their
roster/group layout is unknown. Ground texture tiling (one repeat per 32768 units) and the draw/fog distance are
estimates. Header patches are read with the ES2 sentinel rules; GO.EXE may override the theatre or time of day (not
checked). B0_C1_20 and B0_C1_30 end inside their header rows and are reported as failed.

## T1 sky list

`/t1-map-skies` now lists skies from every imported pack in `SKY_PACKS` (app.py), grouped by game, with `renderer`
(`dome` or `q3`) and `base` (the pack's data route):

| Source | How it is drawn |
|---|---|
| Tribes, Starsiege, Earthsiege, Earthsiege 2 | the dome panels (`scene.sky`) |
| Quake III (+ Team Arena, mods), RTCW, ET, Jedi Outcast, Jedi Academy | the Q3 shader path (box + cloud stages) |
| Quake II, Daikatana, Anachronox | their six `env` faces as a Q3 box (same rt/bk/lf/ft/up/dn convention, gl_warp.c / tr_sky.c); rotation speed not applied |

Not offered: Source skyboxes (VTMB, E.Y.E.; DDS faces), Unreal sky zones (Deus Ex, Vengeance), Doom 3 / Quake 4
(portal skies / cubemaps), Quake 1 (scrolling layers), Nosgoth / Ascend (no sky in the pack). The mission's own sky
is the first entry, "(default)". A choice is stored in the page's local settings only; nothing is written to a pack.
The box sky now draws without mipmaps and with clamped edges (seams were visible at face edges). Fog under a swapped
sky keeps the mission's haze colour (as before).
