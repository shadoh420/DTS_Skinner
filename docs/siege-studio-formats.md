# siege-studio formats vs DTS Skinner

Research note, 2026-10-08. The reference is open-siege/siege-studio `master` at `53e7efb4` (2026-08-16). That is also GitHub HEAD, and a local copy is at `C:\tmp\open-siege-pin`. Paths below are relative to that repo:

- `C3` = `siege-modules/content/siege-content-3space/`
- `P` = `siege-modules/presentation/`

siege-studio has no wiki; its docs are `docs/` plus per-format `.md` files next to the readers. **Verified** means I read that source line myself. The other rows come from a read-only source survey and were not re-read line by line.

Three levels matter, because siege-studio registers many extensions that it cannot open:

- **reg**: listed in a presentation DLL's `get_supported_extensions`.
- **reader**: a working parser.
- **UI**: something the user can actually do with the file.

## 1. siege-studio formats

| Game(s) | Ext. | Holds | reg / reader / UI | siege-studio can | Source | Unclear |
|---|---|---|---|---|---|---|
| Darkstar 3Space 3.0: **Starsiege, Starsiege: Tribes**, FPS Ski Racing, Trophy Bass 3D/4, Driver's Ed | `.dts` (PERS `TS::Shape`) | model, animations | ✔ / ✔ / ✔ | View untextured with detail and sequence pickers; export OBJ (no MTL, one per detail level via `siege-tools/dts-convert`) | `C3src/dts/darkstar.cpp:299-344` (verified: versions 2, 3, 5, 6, 7, 8; v4 and >8 throw); structs `C3include/siege/content/dts/darkstar_structures.hpp`; view `P/siege-presentation-3d/src/views/dts_shared.cpp` | Which game uses which shape version is not documented. A writer exists (`darkstar.cpp:362-450`) but nothing calls it. |
| same | `.dml` | material list (texture names) | ✔ / ✔ / list only | Show the texture names | `C3src/dts/darkstar.cpp:215-253` (v2-4); `P/.../dml_shared.cpp` | No export |
| 3Space 2.0: **Earthsiege 1/2**, Aces of the Deep, Battledrome | `.dts`, `.dcs` | model | ✔ / ✔ (parse) / ✘ | **Nothing is drawn**: the whole render path in `C3src/dts/3space_renderable_shape.cpp:102-337` is commented out (verified) | `C3src/dts/3space.cpp:19-45, 306-345`; `docs/game-support.md:174-196` ("DTS support in progress"; the `feature/earthsiege-files` branch no longer exists) | The grid-shape tag is defined but not detected |
| 3Space 2.5: Silent Thunder, **Red Baron 2/3D**, Pro Pilot | `.dt2`, `.dts` | model | ✘ / ✔ / ✘ | Nothing (the reader is not wired into the UI) | `C3src/dts/3space_v2.cpp:18-31` | Not reachable in the app. Verified 2026-10-08: its reader map is empty, so it reads nothing; the 0x65 shapes Red Baron 3D uses have a struct but no reader, and the structs give int32 fields where the files hold floats |
| Torque: **Tribes 2**, Trophy Hunting 4/5 | `.dts`, `.dsq` | model | ✘ / ✘ / ✘ | Nothing; structs only | `C3include/.../torque_structures.hpp`; `docs/game-support.md:448` (verified: "No support") | |
| Darkstar (Starsiege, Tribes) | `.vol` (` VOL`, `PVOL`) | archive | ✔ / partial / ✔ | List; extract uncompressed entries | `C3src/darkstar_resource.cpp:22-31`, `:456`, `:510-544` (verified: compressed RLE/LZ/LZH entries are passed to an external `extract.exe` via `std::system`) | Which game uses ` VOL` and which `PVOL` is not documented |
| 3Space 2.0 / 1.5 / DGDS: Earthsiege `VOLN`, RMF+`.001`, DYN | `.vol`, `.rmf`, `.dyn` | archive | ✔ / ✔ / ✔ | List; extract | `C3src/three_space_resource.cpp:19-36`; extraction `:538-553` | Compressed VOLN entries (type 9, LZSS-Huffman) are copied out raw, **not** decompressed (verified). Earthsiege 1 and 2 store every entry uncompressed (type 2), so this doesn't matter for them. |
| Dynamix 2D: **CyberStorm 1/2**, Hunter Hunted, Trophy Bass, 3-D Ultra | `.rbx`, `.tbv` | archive | ✔ / ✔ / ✔ | List; extract | `C3src/trophy_bass_resource.cpp:15-25` | The files inside (BMX, PLX, ANX, WAX, FLX) have **no reader** |
| Earthsiege 1/2 bitmaps | `.dbm`, `.dba`, `.dci` | texture, texture arrays | partial / ✔ / ✔ | View; export to BMP, PNG, JPG and others | `C3src/bmp/bitmap.cpp:28-55`; `P/siege-presentation-2d/src/views/bmp_shared.cpp` | Registered as `.dmb` (typo); `.dbm` and `.dba` themselves are not registered |
| Phoenix (Starsiege, Tribes, Red Baron 2+) | `.bmp` (PBMP), `.pba` (PBMA) | texture, mips, texture arrays | ✔ / ✔ / ✔ | View with a palette picker; export (writing back to Phoenix is commented out) | `C3src/bmp/bitmap.cpp:12-27` | |
| Palettes | `.pal` (RIFF, old DGDS `PAL:`), `.dpl` (Earthsiege), `.ppl` (PL98) | palette | ✔ / ✔ / ✔ | View swatches; feed the bitmap view | `C3src/pal/palette.cpp:7-249` | `.ipl` is registered but has no reader |
| Darkstar binary missions (Starsiege) | `.mis` (SIMG...) | mission object tree | ✘ / ✔ / ✘ | Nothing (not in the `resource_maker.cpp` dispatch) | `C3src/mis/mission.cpp:12-24` | Tribes' text missions are not parsed. Terrain (`.ter`, `.dtf`, `.dtb`) has docs only. |
| Audio | `.sfx`, `.wav`, `.ogg`, `.flac` | sound | ✔ / ✔ / ✔ | Play; export WAV | `P/siege-presentation-audio/src/views/sfx_shared.cpp` | |
| Scripts | `.cs`, `.cfg`, `.ini` | script | ✔ / partial / ✔ | Key-value view | `P/siege-presentation-scripting/src/views/cfg_shared.cpp` | `CS.md` is only a heading |
| Outpost 2 | `.clm`, `.vol` | audio archive | ✔ / ✔ / ✔ | Extract | `C3src/clm_resource.cpp` | |
| MechWarrior 2 | `.wtb`, `.bwd`, `.prj` | model, world, archive | ✔ / ✔ / ✔ | View untextured; OBJ export; extract | `siege-content-mw2/src/*` | |
| MechWarrior 4 | `.mw4`, `.erf` | archive, model | ✔ / archive only / extract | Extract | `siege-content-mw4/src/*` | `.erf` has no detection |
| Colony Wars (PS1) | `.bnd`, `.tmd`, `.tim`, ... | archive, model, texture | ✔ / partial / extract and TIM view | Extract; view TIM | `siege-content-colony-wars/src/*` | Models are parsed, then thrown away |
| id Tech (Quake family) | `.pak`, `.pk3`, `.wad`, `.mdl`, `.md2` | archive, model | ✔ / ✔ / extract only | Extract | `siege-content-id-tech/src/*` | Models are parsed, then thrown away |
| Uprising, Die by the Sword | `.cln`, `.atd` | archive | ✔ / ✔ / ✔ | Extract | `siege-content-cyclone`, `siege-content-sword` | |
| Generic | `.zip`, `.vl2`, `.iso`, `.cab`, `.exe`/`.dll` | archive, resources | ✔ / ✔ / ✔ | Extract | `siege-content-common/src/*` | `.7z`/`.rar` are registered but not dispatched |

Never mentioned anywhere in the tree: `.dif`, `.dig`/`.dil`/`.dis` (doc only), `.ter`, `.md3`, `.3ds`, `.3di`/`.3dz`.

Beyond formats, siege-studio also has:

- disc installers for Earthsiege, Earthsiege 2, Starsiege, Tribes and Tribes 2 (`siege-modules/installation/`);
- about 40 id-Tech executable fix-ups (launcher and controller patches, no formats).

## 2. Skinner today, and what is installed

Skinner's reading is often deeper than siege-studio's:

- **VOL**: Skinner decodes PVOL LZH itself (`tools/import_t1_map.py:40`); siege-studio shells out to `extract.exe`.
- **DTS**: Skinner reads Darkstar DTS v7/v8 with textures, animations, and GLB/OBJ export.
- **Formats siege-studio lacks entirely**: Torque DTS/DIF, T1 maps (DIS/DIL/DTF/TED), and MD3/BSP.

| Area | Skinner has | Skinner lacks (siege-studio has) |
|---|---|---|
| Darkstar DTS | v7, v8 (`dts_module/dts.py`); the `< 7` branch at `:246` has never seen a real file | v2-v6, if Starsiege or older Darkstar games use them |
| DML | v1-4 | |
| PBMP | ✔ (`dts_module/dml.py`) | **PBA** (PBMA arrays) |
| Palettes | PL98 (`dts_module/palette.py:28`); Earthsiege `.dpl` with its shade ramps (`tools/import_earthsiege.py`); Red Baron's PPAL (`tools/import_redbaron.py`) | RIFF `.pal`, old DGDS `PAL:` |
| VOL | `PVOL` with LZH (`tools/import_t1_map.py:167`); Starsiege ` VOL`; Earthsiege `VOLN` (stored entries); Red Baron `VOL ` with LZH (`tools/import_redbaron.py`) | RMF/DYN (3Space 1.5/DGDS); RBX/TBV |
| 3Space 2.0 DTS, DBM/DBA/DCI | DTS (finest root, rest pose, textured) and DBA (`tools/import_earthsiege.py`) | DCI |
| 3Space 2.5 DTS, DML | Red Baron 3D's 0x65 shapes (finest detail and cockpits, textured, aircraft in a squadron's paint) and DML (`tools/import_redbaron.py`) | the 17 older 0x64 (fixed-point) shapes |
| ZIP/VL2, PK3 | ✔ | |

Games siege-studio covers, and where you have them:

| Game | siege-studio level | You have it? |
|---|---|---|
| Starsiege: Tribes | full | **installed** (`TRIBES11`, `realstocktribe`, ...): Skinner already covers it |
| Tribes 2 | none | installed (`C:\Dynamix\Tribes2`): Skinner already covers it |
| **Starsiege** | full (VOL, DTS, DML, PAL, PPL, BMP, PBA) | **not installed**: `StarsiegeDisk1.iso` and `StarsiegeDisk2.iso` are in Downloads; also in the Exiled library |
| **Earthsiege 1 / 2** | VOL ✔ (list/extract); DTS parsed but not drawn; DBM/DBA/DPL ✔ | downloaded 2026-10-08 from the Exiled library to `Downloads\siege-games\` (ES1 zip, ES2 ISO); data is in loose VOLN files, no install needed |
| CyberStorm 2 | RBX extract only | **installed** (`C:\SIERRA\Storm`; also a `.bin`/`.cue` in Downloads). Its RBX archives hold only BMX, PLX, ANX, WAX and FLX (sprites, palettes, animations, sounds, video), which siege-studio cannot read. **No 3D models.** |
| MissionForce: CyberStorm, Hunter Hunted | RBX/TBV extract only | in the library |
| Stellar 7, Nova 9 | none / RMF extract | in the library |
| **Red Baron 3D** | reader exists, not wired (and reads nothing) | **installed** 2026-10-08 from Steam's Red Baron Pack (`steamapps\common\Red Baron\Red Baron 3D`) |
| Red Baron 2, Silent Thunder | reader exists, not wired | not local, not in the library |
| Aces of the Deep/Pacific, Battledrome, MW2, MW4, Colony Wars, Outpost 2, Uprising, Die by the Sword, Trophy Bass, King's Quest MoE | archive extract (+ MW2 model view) | not local |
| Quake 1/2, Anachronox, Jedi Outcast/Academy, RTCW, ... | PAK/PK3 extract only (models are discarded) | several are installed via Steam |
| Tribes Ascend, Tribes Vengeance | none | Ascend on the Desktop; Vengeance as `Downloads\TribesVengeance.zip` (skip both) |

## 3. Proposed order

1. **Starsiege**, for most value and the most shared code. It reuses Skinner's Darkstar stack (PVOL/LZH, PL98, PBMP, DTS, DML) and adds:
   - the ` VOL` header;
   - PBA texture arrays;
   - RIFF `.pal`;
   - DTS v2-v6 if its shapes use them (structs: `darkstar_structures.hpp`).

   This becomes a "Starsiege" game in the model browser (HERCs, tanks, flyers, buildings), with its textures as a library. First I need to find out which shape and VOL versions the discs actually hold.
2. **Earthsiege 1 + 2**, one 3Space 2.0 group, all new code:
   - VOLN archives;
   - 3Space 2.0 DTS (structs: `C3include/.../3space.hpp`);
   - DBM/DBA/DCI bitmaps;
   - DPL palettes.

   siege-studio draws none of these models, so even untextured models in Skinner go beyond it.

   **Probe results (2026-10-08, scratch scripts, not in the repo):**
   - ES2 `SIMVOL0.VOL` holds 55 DTS models (all the HERCs plus debris and effects), 126 DBA, 45 HBA and 32 DPL.
   - Geometry, the part hierarchy and the default pose decode correctly: the Apocalypse renders as itself.
   - DBA bitmaps and DPL palettes decode correctly. DBM objects pad to an even size, and pixels start 21 bytes into each object.
   - ~~Open: which bitmap and which DBA each textured polygon uses.~~ **Solved** (2026-10-08), from a disassembly of Earthsiege 1's `DBSIM.EXE` and checked against Kevin Foley's Herculan docs for Earthsiege 2 (github.com/kevinfoley/Herculan, `docs/retail/formats/dts-texture-binding.md`, MIT):
     - The poly's colour field is a dword index into the group's surface table (front fill, front line, back fill, back line); the probe's `colors[color // 4]` was the wrong stride.
     - A texture poly's value is a frame of the shape's texture bank, mapped corner to corner in vertex order (ES1 inset 3 texels).
     - The bank comes from the u16 at offset 0x94 of the shape's `.DAT`: ES1 light, medium, heavy, enemy, apoco; ES2 adds apocatex, razortex, newhercs.
     - Shaded and Gouraud polys name a shade ramp, which the `.DPL` stores after its colours; solid polys name a palette index.
   - Imported by `tools/import_earthsiege.py` as games `es1` (32 of 35 shapes) and `es2` (50 of 55); the rest are effects with no polygons.
   - Weapons (2026-10-08): the ES2 player HERCs carry their shell stock fit (`SHELL0 GAM\INI_*.DAT`). Each `.GL` hardpoint with a drawn mounting code takes the weapon its fit slot (`+0x17`) names, drawn as the `MECHWPNS.DTS` root that the sim `WEAPONS.DAT` template gives for that code, at the hardpoint bone moved by the mount point. Checked against Herculan running TRAIN8 (Samson) and TRAIN1 (Outlaw). The Cybrids (and the other non-player machines) take the fit their missions give them most: row 12 of every `.MSN`, the mech roster, has the `MECHS.NAM` type at 0x30 and ten weapon ids at 0x32. ES1 has no stock-fit files, so every ES1 machine takes its missions' favourite. Its `.MSN` files also say revision 5 but have 11 rows, read from its shell `GO.EXE` (parser at 0x30120; X-32 image, address = file offset - 0x491D): records of 14, 22, 22, 10 bytes, waypoint lists, 12, 30, 106 (the mech roster, the same type and weapon fields as ES2's), 64, 24 and 88 bytes. 112 of its 114 missions read exactly to their end (only those count; ES2: 61 of 62). The empty weapon is the template with no hit spheres: ES2 id 0, ES1 id 17.
3. **Red Baron 3D** (3Space 2.5 DTS), done 2026-10-08 as game `rb3d` (`tools/import_redbaron.py`; 128 of 173 shapes, the rest are sprite effects or the older 0x64 format). siege-studio's reader reads nothing, so everything here was decoded from the files and `Baron.exe`:
   - `VOL ` volumes: chunks with a u24 size and 0x80; 14-byte index entries; LZH (type 3) as in Tribes.
   - Shapes: tagged objects padded to 2 bytes; the `nu` ones carry a leading u32 1; all floats. Every part's transform is -1, so points are in shape space. A cell animation's tail is sequence, count and the child per frame; frame 0 is the intact state for wings and control surfaces.
   - Faces: four corners (a triangle repeats its third), four texture corners, a normal vertex and a material; the corners wind against the normal. Texture v 0 is the bitmap's top row (checked on the Camel's trailing-edge cut-out and the church's door).
   - Axes: x right, y forward, z up, 1.5 inches a unit. Checked on the S.E.5a, whose Vickers sits on the pilot's left as on the real aircraft, and on the E.III's and Camel's spans.
   - Paint: `FUS`, `UWT`... are paint parts (`Baron.exe` table at 0x188268); the bitmap is `03 PP NN SS .bmp`. Materials flagged 1 make palette index 0 clear; flagged 2 are `.pab` alpha maps for fire, oil and propeller blur, left out.
   - The viewer draws `rb3d` front faces only, as the game does: a wing's top and bottom are two faces in one plane.
4. **Skip, unless you want a generic "extract archive" feature**:
   - CyberStorm 1/2 and the RBX/TBV/RMF/DYN games: archives of 2D formats nobody reads;
   - MW2/MW4, Colony Wars, Outpost 2, Uprising, Die by the Sword;
   - id-Tech PAK/MDL: siege-studio only extracts these; Skinner's MD3/PK3 path already goes further for Q3.

## 4. Licence

- siege-studio code is **MIT** (root `LICENSE`, "Copyright (c) 2019-2024 Matthew Rindel"; no other licence files).
  - Porting or translating its readers into Python/JS is allowed.
  - We must keep the copyright and permission notice with the ported parts, for example a `THIRD_PARTY_NOTICES` file plus a header comment in each ported module.
- The README (line 71) puts everything "not present in code form" under **CC BY-SA 4.0**: UI designs, themes, images, logos.
  - The format `.md` write-ups arguably fall under it, so I would paraphrase them, not copy them.
  - Don't reuse the logo or the Besieged theme.
- Note: Skinner itself has no LICENSE file.

No code has been copied. Using siege-studio as a format reference (reading structs, writing our own readers) needs nothing; copying or translating code line by line needs the MIT notice and your OK.
