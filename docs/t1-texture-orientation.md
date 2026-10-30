# T1 interior UV correction — 2026-10-29

Completed in the isolated `codex/t1-texture-orientation` branch, based on
`e8cdb57` (v30). No merge, deployment, or changes to either active checkout.
The review server used port 5110 and this worktree's own `local-data`.

## Cause and correction

The four examples are DIS/DIG interiors, not animated DTS meshes:

| Model | Identified graphics |
| --- | --- |
| aw_sub | subchart1 (slot 25); console tiles include radarscr, radarscr2, jaxscr, cam1 and bluescrdeath |
| base75 | base75_icaution (slot 35) |
| fc_fireblade | carpet_cap (slot 7), carpet_strait (slot 6) |
| fcomm | carpet_cap (slot 7), carpet_strait (slot 22) |

The shared interior exporter negated both texture axes, added an extra pixel
to each surface offset, and omitted `highest_mip` from the surface size.
For either axis, with bitmap dimension D, the correction is:

```text
old: -(raw_uv * (size_minus_one + 1) + offset + 1) / D
new:  (raw_uv * ((size_minus_one + 1) << highest_mip) + offset) / D
```

This matches original DarkStar's surface-size expansion in
[itrRender.cpp](C:/tmp/tribes-rebirth-source-1105fd/darkstar/Interior/code/itrRender.cpp:335)
and positive scale/offset in
[gOGLFn.cpp](C:/tmp/tribes-rebirth-source-1105fd/darkstar/Dgfx/code/gOGLFn.cpp:886).
The user's [TribesRebuild renderer](C:/TribesRebuild/engine/Interior/code/itrrender.cpp:527)
also confirms the surface rectangle calculation. That fork additionally has
reference-dimension support for high-resolution replacement art; this change
preserves the catalog's selected textures and their existing dimensions.

Independent DIG point and UV corner indices were already correct; a regression
fixture deliberately permutes them. Geometry, triangle winding, material slots,
texture selection, PNGs and viewer texture orientation are unchanged. Original
PBMP/palette decoding matched the existing PNG pixels for subchart1,
display_defense, display_weapons, carpet_cap and carpet_strait. There is no
renderer-wide texture rotation or per-model rotation override.

## Scope and automatic detection

All **391 catalog interiors** were matched to original DIG data before repair:
positions and old UVs within 1e-5, identical triangle indices and material groups.
Only `uvs` and the new `interior_uv_version: 2` marker changed. The other
**198 models are byte-identical** to the baseline. All 589 catalog models remain.

Eight interiors also needed omitted mip scaling: convertor, cpu, cpucorner,
spawn and W64elevplat (mip 1); iflagroom, iobservation and Underground8 (mip 2).
The other 383 use mip 0. New imports use the corrected shared exporter.
GLB export bypasses old static-interior animation caches so they cannot restore
legacy UVs; versioned interiors take the static export path without a DTS lookup.

`tools/repair_t1_interior_uvs.py` is a bounded, dry-run-first repair using the
existing install/archive readers and model validator. It searches competing
source versions, rejects nonmatching or ambiguous results, and writes no models
if the scan has unresolved matches. It preserves material/geometry data and is
idempotent. It does not reimport textures or rebuild the catalog.

Sources were found locally under C:/realstocktribe, C:/Dynamix/TribesRPG,
C:/Users/c/T1OpenGLMods/megaversion/T1OpenGLMods/bovidi/Maps and
C:/Program Files (x86)/Dynamix/TRIBES11/base. No missing client download is needed.
The [repair report](../build/repair-report.json) lists the exact archive and DIG
member for every repaired model. This can identify this known conversion bug
automatically; it cannot infer whether arbitrary source artwork was intentionally
rotated or whether unrelated replacement art has the intended scale.

## Validation and captures

The baseline was reproduced using the unchanged v30 UV arrays in the isolated
viewer. Before/after captures use identical camera, target, FOV and section
settings. Browser capture records confirm visible, focused, fully loaded pages;
all seven models produced zero page errors. Browser jobs used the coordination
runner's game lease; source repair/import jobs used CPU leases.

[Open the before/after gallery](../build/uv-review/index.html).
All captures and camera records are in `build/uv-review` in this worktree.
The aw_sub debug capture isolates the material-bearing surface; fc_fireblade
uses a roof section to expose the carpet. base75's existing replacement-art crop
is retained. These are source-validated viewer comparisons, not captures from a
running native game. Native-game and owner visual acceptance remain unclaimed.

| Check | Result |
| --- | --- |
| Source-match repair and real output validation | 391 matched, 0 unresolved |
| Catalog delta against e8cdb57 | 391 UV-only repairs plus marker; 198 unchanged |
| Independent source UV calculation, four screenshot models | Exact match |
| Fresh real imports, four screenshot models | Vertices, triangles, groups, UVs and version match repaired catalog |
| All bundled T1 models through real preview and OBJ routes | 589 passed |
| Focused building/import, texture-workshop and map tests | 35 run, 2 opt-in skips, no failures |
| Building/import tests after GLB cache guard | 12 run, 1 opt-in skip, no failures |
| GLB tests; storage tests | 7 passed each |
| Real GLB export, four screenshot models | HTTP 200; TEXCOORD values match previews as float32 |
| Same-camera affected views | aw_sub, base75, fc_fireblade, fcomm and iblock inspected |
| Unaffected same-camera controls | larmor and disc: zero changed pixels |

Existing passing checks were reused rather than rerunning a full local suite.
Logs/results: [focused tests](../build/focused-tests.log),
[final building tests](../build/interior-regressions.log),
[catalog validation](../build/catalog-validation.log),
[catalog delta](../build/catalog-delta.json),
[real imports](../build/real-import-check.json),
[real GLBs](../build/real-glb-check.log),
[bitmap decoding](../build/bitmap-check.json).
Evidence under build is local and ignored by Git.

Earlier investigation failures are resolved: the first candidate for drock was
a different source variant; a broader source search found its exact match.
An initial repair scan lacked box/ras; the TRIBES11 installation supplied them.
One malformed SPAWN archive was skipped. An early test invocation referenced a
nonexistent test module and lacked the tests import path for storage; the actual
relevant modules subsequently passed with the correct path.
No GPU timings were taken because rendering cost is unchanged.

## Integration

1. Review/cherry-pick this branch's fix commit into the intended integration
   branch when the other session is ready. Include code and all 391 generated
   catalog JSON changes together. No full catalog reimport is necessary.
2. Run the existing Windows CI and inspect the linked same-camera comparisons.
   Restart the intended application only as part of that coordinated integration.
3. Existing external custom-map model caches were deliberately not touched.
   Catalog-backed map props receive the fixed catalog; independently stored
   legacy map models need a targeted refresh through the corrected map importer.
   New map imports already use the corrected conversion.

The active port-5100 app, its tabs, both source checkouts, and their writable
data were not modified by this task. The completed work and local evidence stay
in this worktree for later integration.
