# Tribes 2 import coverage

The installation snapshot at `C:\Dynamix\Tribes2\GameData` contains 257 unique DTS
virtual paths and 704 unique DIF interior paths. All 961 are recorded in
`t2_catalog.txt` and `static/t2/catalog.json`: 959 have static preview/export
geometry, and two remain explicit unsupported records. This is inventory coverage,
not a claim that every rendered asset has passed direct visual review.

The inventory scans nested VL2 archives as well as loose files. It includes the
stock shapes, Team Rabbit 2, Classic, and nested community map packs. The kit's
three-archive/244-shape example would have omitted 13 additional DTS files.

| Source | Unique DTS |
| --- | ---: |
| shapes.vl2 (also 219 identical loose copies) | 219 |
| TR2final105-client.vl2 | 24 |
| Classic_maps_v1.vl2 | 1 |
| z_mappacks/zDMP-4.7.3DX.vl2 | 13 |

DTS versions are 15 (1), 16 (2), 18 (5), 19 (23), 21 (3), 22 (155), 23 (55),
24 (12), plus one empty file. There are 770 archive DIF entries resolving to 704
virtual paths. Every DIF resource file reports version 44. Archive texture entries
include PNG (5,889), JPG (83), BMP (17), and BM8 (20), with 73 IFL frame lists.
These archive-entry counts include overrides; they are not unique asset counts.

The build resolves 1,945 image paths into 1,921 image files, including all 1,102 effective PNG/JPG skin
variants under `textures/skins`. Material names and texture files stay separate:
sorting the displayed skins cannot rearrange a model's material assignments.

## Import and rendering policy

Run from the repository with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe tools\import_t2.py `
  --game-data C:\Dynamix\Tribes2\GameData `
  --kit C:\Users\c\ArenaPrototypeContainer\TOOLS\t2port-kit\tools
```

The installation and kit are read-only inputs. `--output` can select a separate
staging directory. Generated model JSON and metadata live in `static/t2`; editable
images live in `static/textures/t2`. Source-hashed image filenames avoid collisions;
regeneration preserves an already existing editable image of that name. To produce
a fresh image baseline, use a new staging output directory. Game identity supplies
the T1/T2 namespace; interior IDs also carry an `interior_` prefix.

The viewer's reproducible search policy is case-insensitive virtual paths, later
alphabetically sorted archive paths overriding earlier identical virtual paths,
then loose files overriding archives. For image formats the preference is PNG,
JPG, JPEG, BMP, TGA, DDS, BM8, IFL, DML; an explicitly supplied extension takes
priority. Exact authored paths (also relative to `textures` and `textures/skins`)
are tried across supported formats before any basename fallback.
IFL/DML lists bind frame zero and retain their list in the metadata.
Basename fallback candidates prefer `textures/skins`, then `textures`, then sorted paths;
ambiguous candidates and overridden sources are recorded. This is an explicit
offline viewer policy, **not verified native runtime mount precedence**. It selects
installed HD image overrides where they replace the same virtual path.

DTS preview geometry uses the finest nonempty authored visible detail. Zero-size
details are valid T2 rendering details; negative-size details and explicitly named
collision/LOS details are excluded. Default object visibility and vertex/UV frames
are respected. Torque conjugate quaternions, recursive parent transforms, authored
normals, UVs and material flags are retained. Clockwise faces become counterclockwise,
and coordinates rotate from Z-up to Y-up as `(x, z, -y)`. No T1.50 mount aliases,
first-person offsets or player contracts are applied. The current installation
contains no skin meshes: its armors consist of rigid node-bound pieces. The adapter
also has a numerical check for weighted inverse-bind deformation without applying
the object transform a second time.

DIF interiors use the first nonempty/highest-detail render surface set, signed plane
normals and authored texture-coordinate generators. Collision hulls and null surfaces
are excluded. Base textures are included; baked lightmaps, alarm states, animated
lights and resource movers are a future tranche. This boundary is included in each
interior's metadata and browsing warnings.

## Animation information

There are 331 embedded sequences across 123 DTS models and 428 external DSQ files.
Embedded descriptors retain names, flags, durations, key counts and track membership.
The global inventory retains external DSQ node-name binding tables and sequence
descriptors. Per-model metadata records exact `TSShapeConstructor` script declarations,
including TR2 armors that borrow base-game DSQ files. Prefix-related external DSQs
are also inventoried. Source hashes and locations are preserved.

Preview and OBJ export currently use authored static default poses. Animation
playback and animated textures are deferred; original animation samples remain in
the original DTS/DSQ installation files rather than being silently flattened out
of a converted source file. No sequence is renamed or dropped for T1.50 loader caps.
`medium_male_dieslump.dsq` is empty in the installation and explicitly reported.

## Known incomplete assets

- `xorg2`: zero-byte source DTS.
- `effect_plasma_explosion`: no geometry in an authored visible detail.
- Missing DTS textures: `borg3` / `SeaWeed`; `borg15` / `OldwoodBranch2`;
  `c_baselopro` / `C_BaseLoPro`; `deploy_ammo` and `pack_deploy_ammo` /
  `pack_deploy_inventory`; `gravemarker_1` / `gravemarker_ground`.
- Missing interior materials: `interior_flingrock01`, `interior_flingrockvent01`,
  `interior_flingtower02`, and `interior_siege`. Their exact missing material names
  are in the catalog and export metadata.
- Parsed render surfaces but unparsed resource-tail data: `interior_drock6`,
  `interior_prock7`, `interior_xrock7`, and `interior_xbunk2`. Their parser errors
  are retained; render-surface success is not full resource support.

Missing materials receive explicit errors, not invented substitute skins. Authored
no-material primitives instead use an explicit untextured slot.

## Provenance and validation

No kit source is redistributed. Its external reader modules are loaded only at
build time. No explicit license file was present in the supplied kit, so the
application does not vendor them. `static/t2/inventory.json` records the exact
SHA-256 of each reader and four checked in-memory retention adaptations: initial
skin normals, legacy detail-to-skin ranges and IFL material records. The kit's
GLB exporter is not used because its output applies T1.50-specific geometry,
sequence, hierarchy and visibility conventions.

Coordinate, skinning, material and zero-size LOD semantics were checked against
[Torque3D source at c669fd4](https://github.com/GarageGames/Torque3D/tree/c669fd4005890d68557103940883da555b295e97/Engine/source):
`ts/tsMesh.cpp`, `ts/tsMaterialList.h`, `ts/tsShape.cpp` and `math/mMath_C.cpp`.
Exogen's UI and Blender addon were studied as references; their parser/exporter
code is not copied into this import adapter. Game artwork retains its original
owners' rights; the kit's availability does not grant a new artwork license.

Focused tests cover conjugate quaternions and parent order, weighted inverse-bind
poses, indexed/unindexed strip/fan winding, zero-size visible LODs, DIF signed normals
and UVs, and exact catalog-to-inventory accounting. The main README records the
packaged build and rendered-review validation. Tests and source parsing do not
establish visual correctness across the entire catalog.
