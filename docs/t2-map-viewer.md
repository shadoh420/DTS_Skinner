# Offline T2 map viewer milestone

Goal: open one stock Katabatic CTF mission from Skinner, render terrain,
interiors and placed shapes, and verify free-flight in the packaged application.
No collision walking, map editing, T1/Q3 maps, demos, live servers or relay.

Upstream: https://github.com/exogen/t2-mapper
Pinned source: e9b6332aaa48bc79535655d644bacc7e22b6ac79.
Source checkout: C:/tmp/t2-mapper-skinner-reference (sparse source, no game assets).
Game input: C:/Dynamix/Tribes2/GameData/base, read-only stock archive allowlist.
Existing workshop checkpoint: a7987ac on codex/texture-workshop.

Approach: separate compiled map page served by Flask. Preserve the upstream
renderer and script runtime; a small local entry mounts only the map view.
Keep source assets in ignored local-data/t2-maps; do not commit game archives.
Keep build dependencies and upstream checkout outside the tracked source.
Retain exact source revision and attribution alongside the compiled viewer.

Current state: source build displays terrain, bases and placed shapes. All five
stock biome archives are included because building textures cross biome boundaries.
The local pack has 3736 resources. Authored DTS mounts are extracted with upstream's
helper (including vehicle_pad/mount0); the stock empty xorg2.dts is skipped.
The Windows package now renders both the snow-covered exterior and the lower
generator room, including lightmapped interior surfaces and placed generators.
Verified WASD movement, drag-to-look, observer camera selection, FOV, fog, reset,
and navigation to/from the existing rendered T1 model workshop. Browser inspection
found no map-page errors in the packaged smoke check. Embedded-browser mouse capture
is unavailable; its drag-to-look fallback is verified. Native browser pointer-lock
feel still needs user review.

Validation: 57 Python tests passed (including import scope, archive traversal,
encoding/precedence, no-overwrite, and confined local routes), production viewer
build and PyInstaller succeeded, Git LFS fsck passed. The packaged check used
local port 5088. No Unity files or game-install files were modified.

Launch: dist/texture-workshop/SkinnerApp.exe. Portable archive:
dist/DTS-Skinner-T2-Katabatic-preview-Windows.zip, with adjacent SHA-256 file.
Next expansion should begin with user review of this map, then a small stock-map
selection. T1/Q3 loaders and collision walking remain separate future work.

## Reproduce

The checked-in static/t2-maps bundle works without Node at runtime. Game assets
are intentionally excluded from Git. To prepare a new local pack and rebuild:

```powershell
git clone --depth 1 --filter=blob:none --sparse https://github.com/exogen/t2-mapper C:/tmp/t2-mapper-skinner-reference
git -C C:/tmp/t2-mapper-skinner-reference fetch origin e9b6332aaa48bc79535655d644bacc7e22b6ac79
git -C C:/tmp/t2-mapper-skinner-reference checkout e9b6332aaa48bc79535655d644bacc7e22b6ac79
git -C C:/tmp/t2-mapper-skinner-reference sparse-checkout set src generated public scripts relay
python tools/import_t2_map.py --game-base C:/Dynamix/Tribes2/GameData/base
python tools/build_t2_maps.py --source C:/tmp/t2-mapper-skinner-reference --map-data local-data/t2-maps
python app.py
```

Import refuses to overwrite an existing pack. The stock archive allowlist and
order are explicit; no loose files or mods override it. SOURCES.json records
the input archive hashes. Build requires Node/npm, installs the pinned lockfile
when dependencies are absent, validates upstream source revision/cleanliness,
and replaces only the generated static/t2-maps bundle. Keep the upstream source
checkout outside Skinner. UPSTREAM.json and THIRD-PARTY-NOTICES.txt ship with the
bundle. Upstream package.json declares MIT and this revision has no root LICENSE.

## Controls and limits

Click **T2 Maps · Katabatic** in the model sidebar. Click the scene to capture the
mouse, WASD moves, Space rises, Shift descends, wheel changes movement speed,
and Escape releases the mouse. Keys 1–9 select authored observer viewpoints.
Drag to look also works when an embedded browser cannot capture the mouse.
FOV, fog, and Reset view are available above the scene. Models returns to Skinner.
Map settings use a separate browser-storage namespace.

This first milestone is Katabatic CTF only: free-flight without collision,
gameplay, audio, map editing or material editing. T1 and Q3 maps are not implemented.
It runs through local Flask asset routes; CSP blocks external asset/network calls.
It does not mount upstream live-server, demo or relay interfaces.

Known source limitations: the stock sky_ice_blue.dml refers to an absent
desert/skies/ice_blue_emap (the file exists under ice/skies), and a stock shape
references the absent axe texture. Upstream's fallbacks remain; these are disclosed
in Preview notes. The script interpreter also logs unsupported gameplay functions;
this preview is not a gameplay simulator. A rendered smoke check is not a claim
of full native map fidelity.
