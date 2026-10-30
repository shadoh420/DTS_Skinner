# T1 interior UV correction

DIS/DIG interiors previously had both texture axes negated, an extra pixel
added to each surface offset, and omitted highest-mip scaling. This could
mirror or shift markings and change their scale. For either axis, with bitmap
dimension D, the shared exporter now uses:

```text
old: -(raw_uv * (size_minus_one + 1) + offset + 1) / D
new:  (raw_uv * ((size_minus_one + 1) << highest_mip) + offset) / D
```

This was checked against DarkStar's surface-size expansion in
`Interior/code/itrRender.cpp`, positive scale/offset in `Dgfx/code/gOGLFn.cpp`,
and the local TribesRebuild renderer. Independent point and UV corner indices
were already correct; a regression fixture deliberately permutes them.
Geometry, winding, material slots, selected textures and PNG pixels are unchanged.

## Catalog and exports

All **391 catalog interiors** matched original DIG positions and legacy UVs
within 1e-5, with identical indices and material groups. Only `uvs` and
`interior_uv_version: 2` changed. The other **198 models** are byte-identical;
all 589 models remain.

Eight interiors need mip scaling: convertor, cpu, cpucorner, spawn and W64elevplat
use mip 1; iflagroom, iobservation and Underground8 use mip 2. The other 383 use
mip 0. New imports use the corrected shared exporter.

GLB export bypasses legacy animation caches for versioned interiors so cached
UVs cannot overwrite the correction. These interiors use static export without
a DTS lookup. Animated DTS models retain their existing behavior.

[`repair_t1_interior_uvs.py`](../tools/repair_t1_interior_uvs.py) is a dry-run-first
catalog repair. It searches competing source versions and writes nothing if
matches remain unresolved or ambiguous. See `--help` for source/write options.

## Existing map caches

Maps referencing the catalog receive corrected models directly. Independently
stored interiors need their own repair and new content keys because shared
map meshes use immutable browser caching.

[`refresh_t1_map_uvs.py`](../tools/refresh_t1_map_uvs.py) stages a targeted repair
using the same DIG matcher and exporter. It preserves geometry, materials,
texture choices, placements, lightmaps, shelter references and other scene
metadata. Terrain, map index, source receipts, textures, collision and gameplay
data are not rewritten. Source installs stay read-only.

```sh
python tools/refresh_t1_map_uvs.py --cache path/to/t1-maps --stage build/t1-uv-refresh --source path/to/Tribes
```

Repeat `--source` for mod/mission folders. Inspect staged `inventory.json` for
original/replacement keys, exact source archive/member matches, affected maps,
skipped models and missing inputs. Unreferenced legacy meshes remain untouched.
Resolve referenced missing or ambiguous interiors before activation.

Stop the application before publishing:

```sh
python tools/refresh_t1_map_uvs.py --cache path/to/t1-maps --stage build/t1-uv-refresh --activate
```

Activation preflights original scenes and content keys, refuses to overwrite
scenes edited since staging, and restores earlier scene writes if a later write
fails. Restart the application and reload viewers after the transaction.

For rollback, stop the application and use `--rollback` instead. Keep
`inventory.json`, `before`, `after` and staged `models` together; original
content-named meshes are retained. Rollback restores exact scene bytes and
refuses to overwrite subsequent edits. It does not revert catalog/importer code.

## Validation

Set `SKINNER_DATA_DIR` to a test directory for local application tests:

```sh
python -m unittest tests.test_refresh_t1_map_uvs tests.test_add_t1_buildings tests.test_glb_export tests.test_t1_maps
```

The clean publication branch passed **37 tests, with two install-dependent
skips**. Existing Windows CI runs the full suite. Tests cover native UVs and
corner indices, static exports, matching, missing inputs/textures, scene
metadata, stale activation refusal, partial-write recovery and rollback.

Local qualification also checked all 589 catalog preview/OBJ routes and four
affected real GLBs against float32 preview UVs, including a stale-cache negative
control. A separate local library refreshed 460 meshes across 99 maps; all 905
maps' model references resolved afterward.

Same-camera ArcticWolf and FleetCommand captures showed corrected markings;
DuelMap had zero changed canvas pixels. Visible spot-checks also covered
Xtremeski, RMRPG's Rin Vale masonry, RPG map 6's town facades, and Borderline's
base panels without browser errors. RPG map 6 retains its existing missing
bridge-lightmap warning. These are targeted checks, not inspection of every map
or native-game acceptance. Local maps, captures and multiplayer work are not
included in this change.
