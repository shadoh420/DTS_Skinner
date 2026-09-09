# DTS_Skinner - Tribes 1, Tribes 2 and Quake 3 Model Workshop

Browse models and interiors, inspect materials, apply editable PNG skins, and export
OBJ/MTL/texture ZIPs from one Windows application. T1 and T2 have separate model
and texture namespaces. The entire existing T1 inventory and repaired armor pose
are retained.

| Catalog | Entries | Static previews / exports |
| --- | ---: | ---: |
| Tribes 1 DTS / DIS | 390 | 390 |
| Tribes 2 DTS | 257 | 255 |
| Tribes 2 DIF interiors | 704 | 704 |

All 961 T2 entries remain browsable, including two explicit unsupported shapes.
The installed catalog includes Team Rabbit 2, Classic, community map packs and
installed HD texture overrides. It includes 1,921 editable T2 PNGs, covering 1,102
skin variants. See [T2 import coverage](T2_IMPORT.md) for exact source versions,
archive policy, material gaps, animation dependencies and licensing/provenance.
The executable needs neither the game installation nor the porting kit at runtime.

Current Windows build: `dist/controls-workshop/SkinnerApp.exe`.
Keep its adjacent `local-data` folder for animated exports and local Q3 models.
Visible orientation controls and **Download GLB + animations** are now available;
Quake 3 imports appear as a third game. See [animation/export coverage and usage](ANIMATION_EXPORT.md).
The previous `dist/combined-workshop` build is preserved.
Older builds, including `dist/complete-catalog`, are preserved.

![Combined workshop with a T2 Blood Eagle skin](docs/workshop.png)

## Features

*   Game selection, searchable natural-sorted catalog, family filter, previous/next.
*   Simultaneous 3D viewport and material inspector with per-slot skin selection.
*   Offline texture reloading, editable PNG selection/reset and PNG download.
*   Frame model, named camera views, lighting, wireframe, background and turntable.
*   **Export**: static OBJ/MTL ZIP or GLB with embedded textures and animation clips.
*   Visible X/Y/Z orientation controls, remembered per game/model in the preview.
*   Walk/fly mouse-look navigation (Shift + backtick), speed control and free interior exploration.
*   Fullscreen viewport with an Exit fullscreen button (or Escape).
*   Exact world-axis X/Y/Z position fields and adjustable step buttons; Y moves up/down.
*   Cross-game per-slot textures in preview, OBJ and animated GLB; source games stay explicit.
*   Reset all restores the current model's materials, transforms, camera and preview settings.
*   Local Quake 3 MD3/PK3 import, assembled players and skin variants.
*   System tray icon: open app, access textures folder, quit.
*   Standalone executable.

## Usage (Executable)

1.  Run `SkinnerApp.exe`.
2.  If the program doesn't automatically open, use tray icon or browser at `http://localhost:5000/`.
3.  Choose a game and select a model; search and family filters narrow the list.
4.  Choose a material slot, a **Texture library** (T1, T2 or imported Q3), and an editable PNG, then **Apply to slot**. **Reset slot** restores its authored material. Texture selection preserves the target material's UVs, transparency and shader settings.
5.  Edit PNGs in `static/textures/` for T1 or `static/textures/t2/` for T2 beside the executable. Changes reload automatically; **Reload textures** also refreshes manually.
6.  **Download OBJ + textures ZIP** and **Download GLB + animations** export the applied skin selections. Cross-game OBJ ZIPs use `textures/<game>/` folders to avoid filename collisions. **Save PNG** downloads the selected source texture including alpha. Orientation and position controls affect the preview only.
7.  Right click the system tray icon to fully close the program when you're done (save your skins first).

### Navigation, transforms and recovery

Click **Walk / Fly** or press **Shift + backtick** outside a text field to capture
the mouse. WASD / arrows move, E / Q rise and descend, Shift moves faster and Alt
moves slower. The wheel adjusts speed; the Navigation panel also accepts an exact
speed in model units per second. Walk stays level; Fly follows the view direction.
This is free navigation without gravity or collision. Escape or Enter releases the
mouse and keeps the camera where you left it. Frame returns to the whole model.
**Fullscreen** expands the viewport; **Exit fullscreen** or Escape restores the
workshop. While walking, Escape first releases the mouse.

Position fields apply immediately in world axes, independent of model rotation.
Set **Step** and use X/Y/Z plus/minus for exact nudges. Y is always vertical.
Rotation and position are remembered separately for each game/model in the browser.

**Reset orientation** clears model rotation and reframes from the default camera
angle. **Reset position** clears translation. **Reset all** restores the current
model's authored textures, clears its saved rotation/position, exits walk/fly,
reframes the camera, and resets wireframe, lighting, turntable, background,
navigation speed and movement step. Edited PNG files are kept.

Keep the entire `dist/controls-workshop` folder together: `SkinnerApp.exe`, editable
`static/textures`, and `local-data` for animation caches and imported Q3 assets.
Previous builds remain in their existing folders.

September 9, 2026 controls build validation: 46 Python regressions passed. Hidden
Edge against the packaged executable passed world-axis translation/persistence,
orientation and full reset, real pointer-lock walk/fly/look/speed/exit, fullscreen
entry/exit, all nine source/target texture combinations, source-library auto-reload,
OBJ/animated GLB downloads, and normal-window layouts. Existing catalog/skin browser
checks and six rendered Q3 shader samples also passed without browser/WebGL errors.
The unchanged packaged backend passed all 1,807 available previews and OBJ/texture
exports (T1 390, T2 959, Q3 458), with unavailable entries and existing texture gaps
still explicit.
A T1 Disc GLB with a T2 texture retained activation animation, passed Khronos
validation with zero errors/warnings, and rendered moving in the independent GLB
viewer; the packaged export was byte-identical. Direct user navigation/feel review
remains separate. Reproduce with `tools/check_controls.cjs <url> <output-directory>`;
local evidence is in `build/controls-package-review` and related controls folders.


## OBJ Export

The export feature creates a ZIP archive containing:
- `.obj` file (3D geometry with scale factor applied)
- `.mtl` file (material definitions)
- `.png` textures (all referenced textures)
- `README.txt` (import instructions)
- `metadata.json` for T2 (source, material flags, animation descriptors and import limitations)

**Import Steps**:
1. Extract the ZIP file
2. Import the `.obj` file into your 3D application
3. Textures should auto-link via the `.mtl` file
4. Adjust rotation or scale as needed for your target application

## Usage (Developer)

1.  Clone repo.
    Run `git lfs pull` to retrieve the imported T2 texture catalog.
2.  `pip install -r requirements.txt`.
3.  Place assets as above.
4.  Run `python app.py`.

## Tech

Python, Flask, Socket.IO, Watchdog, Three.js, pystray, PyInstaller, Bov's DTS parser, Krogoth/Kaitai TribesToBlender.

## Combined workshop delivery

The UI was informed by [exogen's T2 Model Skinner](https://github.com/exogen/t2-model-skinner)
at `49680aa4b112bdfc042e7a3c9ab766bd7956b1a2` (MIT, copyright 2022 Brian Beck).
Its live application and implementation were inspected: grouped browsing,
simultaneous model/material inspection, and contextual skin/export actions were
adapted into the existing Flask/Three.js application. No framework or editor code
was copied. The reference's curated model subset is not used as our inventory.

T2 preview and export retain authored static poses and the finest nonempty visible
detail. Collision meshes are excluded. Native normals, UVs, material assignment,
wrapping and transparency flags are carried through the adapter. The viewer handles
T2 reflectivity stored in alpha without darkening opaque RGB; its inspector can show
RGB while saved PNGs retain alpha. OBJ/MTL cannot reproduce all Torque rendering
features, so flags and limitations accompany the export.

In-app animation playback and rigged exports remain a separate tranche. Animated
GLB export is now implemented; see [current coverage](ANIMATION_EXPORT.md). Sequence and
external DSQ descriptors are retained, with original samples in the read-only
installation. DIF interiors currently use base textures; baked lightmaps, alarm
states and resource movers are deferred. The two unavailable T2 shapes, ten models
with missing materials and four partial DIF resource tails are detailed in
[T2_IMPORT.md](T2_IMPORT.md). The six original T1 texture gaps below remain explicit.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist\combined-workshop DiscSkinnerApp.spec
# Start only the candidate build, on an unused port:
.\dist\combined-workshop\SkinnerApp.exe --no-browser --no-tray --port 5068
.\.venv\Scripts\python.exe tools\check_packaged.py http://127.0.0.1:5068
```

The optional `tools/check_browser.cjs` uses Playwright/Edge in headless mode to
check offline browsing, normal-window layout, applied-skin downloads and rendered
representatives. Playwright is a development check, not a runtime dependency.

Validation on September 7, 2026: all 21 regression tests pass. Packaged checks
covered all 390 T1 and 959 available T2 previews and OBJ ZIP exports, including
geometry counts, material textures and explicit missing-file reports. Hidden Edge
checks passed offline at 1280x800 and 1024x768; rendered armor, weapon, vehicle,
effect and interior examples were inspected, including Blood Eagle skin selection.
Both games' applied skins were checked in downloaded ZIPs, and unapplied fallback
text cannot change an export. Final packaged checks also verified automatic atomic
PNG replacement with unchanged timestamp/size and edit persistence across restart.
Original packaged textures were restored after the checks. The final reload-only
correction leaves all 5,310 bundled UI/model/texture assets byte-identical to the
fully export-checked candidate. Native tray interaction and full-catalog visual
acceptance remain unverified.

Next step: direct review of the combined workshop, then animation playback and
the explicit DIF rendering gaps. Automated inventory and export checks do not
establish visual correctness across every model. ArenaPrototype integration is
future work.

## September 2026 compatibility repair

Preview and OBJ export now share support for the bundled `v`/`uv`/`tri` JSON
and newer `vertices`/`uvs`/`indices` files. Legacy models use the selected fallback
PNG in both preview and export; modern JSON keeps its authored material groups.
Weapon geometry is preserved. The stale light-armor snapshot was regenerated
with the existing corrected DTS exporter; its node rotation convention now
matches the working heavy-armor exporter, instead of the old twisted pose.

Exported normals now agree with face winding, download errors are reported,
temporary ZIPs no longer accumulate, and texture reloads bypass caching and
recognize atomic file replacement. Missing textures are listed in the exported
README. Light armor uses its authored `base.larmor.png` material. Disc defaults
to `stock_disc.png`; the previous custom `disc.png` is still available by entering
that filename in the fallback field and loading the model again.
Paintgun is included through its complete `paintgun.json`; the old `.nopng`
duplicate is not another selectable model.

Executable builds keep editable skins in `static/textures` beside `SkinnerApp.exe`.
Bundled PNGs are copied only when absent, preserving edits across restarts.
Keep the executable in a writable folder. Source runs use the repository's
`static/textures` folder.

- Tests: `python -m unittest discover -s tests -v`
- Build: `pip install PyInstaller`, then `pyinstaller DiscSkinnerApp.spec`
- Without desktop UI: `python app.py --no-browser --no-tray --port 5057`
  (the executable accepts the same flags). The server binds to localhost.
- The combined workshop uses local texture-change polling and has no CDN dependency.

### Related tools

[Jobo's toolkit](https://github.com/jcmolnar/Starsiege-Tribes-Blender-Toolkit)
was inspected at `21f63ea11b95aa229e62cfa6ae0cf6db680120a0`. Its DTS parsing,
animation/visibility tracks, materials/palettes, and sequence-preserving GLB export
are useful follow-up candidates. No toolkit source was copied for this fix.
[OBJ to DIS](https://github.com/jcmolnar/Tribes-OBJ-to-DIS-Converter) and
[DIS to OBJ](https://github.com/jcmolnar/Tribes-DIS-to-OBJ) handle interiors,
separately from weapon DTS models.

Direct DTS loading, animation playback, multi-material regeneration, and
ArenaPrototype integration remain separate work. Browser tests of bundled
snapshots do not establish native-game pose or material parity.

The stock disc and base light-armor PNGs came from the local Tribes
`Entities.zip` assets (256x256). The base light-armor texture was also compared
with the older working Skinner's material. The custom texture is not overwritten.

Validation: nine regression tests pass, including preview data and OBJ/texture
ZIP export for all 390 models. All 390 also load in a headless Edge browser
without JavaScript errors. The complete-catalog Windows executable also passed
all 390 catalog entries and OBJ ZIP exports. Representative female-armor and large-interior renders
were inspected. Earlier download-error, CDN-unavailable preview, and executable
skin-persistence checks remain applicable. Full visual acceptance and native tray
interaction remain unverified.

The light-armor snapshot is checked against fresh DTS conversion to prevent
shipping obsolete geometry again. Fresh heavy-armor conversion exactly matched
the older working Skinner snapshot. Disc/light-armor renders are review candidates;
the user's visual acceptance remains open.

## Consolidated catalog and sorting

`model_catalog.txt` records every selectable model in the consolidated release.
Catalog tests compare against this inventory instead of accepting whichever files
happen to remain in the folder. All names from the older Skinner catalog are
retained, including `lfemale`, `mfemale`, `marmor`, `harmor`, vehicles, props,
effects, and interiors. Existing stock-disc and corrected light-armor assets are
preserved. The older folder's PNGs and alternate skins are included without
overwriting the repaired build's existing textures.

Model names sort without case jumps and with numbers in numeric order:
`Base1`, `base2`, `Base3`, ... `base10`; `bunker2` precedes `bunker10`.
The displayed texture list is sorted independently of material slot assignments.
Camera clipping/framing adapts to both small effects and very large interiors.

The empty `microex` snapshot was regenerated after correcting V6 float quaternion
and object-subsequence reads and pre-V3 mesh scale/origin handling, using the
format layout documented in Jobo's toolkit. This is still a static effect snapshot,
not animation playback. Three missing terrain textures were recovered from the
local Tribes `mudDML.zip` assets.

Six models still reference eight unavailable textures in their displayed geometry:

| Model | Missing texture files |
| --- | --- |
| grenadetrail | gtrl00.png |
| microex | mic00.png |
| plant1 | plant@.png |
| pulse | pulserifle.png |
| shotsprk | shot00.png |
| teleporter | telept1.png, telptlt1.png, telita1.png |

These models remain selectable/exportable with visible missing-texture indicators;
no unrelated replacement skins are assigned. Some models also reference absent
textures in unused animation/material slots, which the ZIP README reports.
