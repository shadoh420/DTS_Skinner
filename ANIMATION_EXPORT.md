# Orientation, Quake 3 and animated GLB export

The September 8 workshop adds visible X/Y/Z quarter-turn controls and reset.
Preview orientation is saved per game/model in browser storage, survives reloads
and model changes, and is independent of the turntable. Like previous releases,
orientation affects the preview; exports retain source coordinates.

## Export workflow

Use **Download GLB + animations** to export embedded PNG textures and named
geometry animation clips. OBJ/MTL ZIP remains available. Applied material-slot
edits affect both formats. Import the GLB into an application supporting glTF 2.0
morph-target animation and select an animation clip. The Skinner viewport remains
a static preview.

GLB stores baked vertex motion, including source node/cel motion and supported
object visibility, rather than an editable skeleton. Large clip collections use
separate per-clip mesh nodes sharing their geometry buffers to avoid common GPU
morph-target array limits. These are independently playable clips; simultaneous
skeletal blending is not represented. Animations can produce large GLBs.

The download status distinguishes a static model from an animated export and from
an unsupported-source static fallback. Source details and limitations are embedded
in GLB extras. Material effects, animated UV/IFL textures, sound/event triggers,
gameplay movement and editable rigs are not retained as animation.

| Source | Delivered animation data | Known limits |
| --- | --- | --- |
| T1 | 390 local caches; 144 animated models; 460 clips | `marmor` / `Fall controlled` has invalid source timestamps and is excluded; other 37 clips remain. DIS interiors are static. |
| T2 | 255 compact native caches; 822 sampled clips | `borg3` and `chaingun_shot` have legacy object-state offsets the reader does not decode; their GLBs explicitly fall back to static geometry. `medium_male` has an unavailable empty `dieslump` animation. |
| Q3 | MD3 vertex frames, lower/upper/head tags, animation.cfg clip names, .skin variants | Native partial loops receive a separate `_LOOP` tail clip. Models without animation.cfg use a labeled `All frames` clip at 10 FPS when multiple frames exist; source timing is unknown. |

T2's compatible hidden objects are included where the preview already has their
material slots. Additional-material effects such as Jetfire remain omitted;
partial opacity fades are not reproduced. Source ground displacement remains
in-place. Blend sequences are sampled over the default pose. T1 discrete
visibility/cel changes retain transition timestamps with sub-frame sampling.

## Quake 3 import

Select **Quake 3 Arena**, expand **Import Quake 3 models**, and enter a local game
folder, `baseq3`, or one PK3 file. Folder import mounts `pak*.pk3` in filename order,
then loose `models`, `scripts` and `textures` files. This intentionally does not
mount unrelated custom map archives. Select a mod PK3 explicitly to import it.

The local reviewed corpus contains 468 entries: 458 geometry previews/exports,
including 96 assembled player/skin combinations; 10 tag-only hand attachments
remain visible with an explanation. Q3 names and textures cannot collide with
either Tribes game. Shader materials use a static base texture approximation with
explicit warnings. Missing images stay visible as missing materials.

Q3 source frame data and imported models are local to `local-data/q3`; editable
PNGs are in `local-data/q3/textures`. Reimport preserves existing edited PNGs.
Catalog/source publication is atomic via `current.json`; older import generations
remain under `imports` so active exports never read half-replaced source files.
No Q3 game files or generated retail assets are committed to the repository or
embedded in the executable.

## Rebuilding the local animation data

Keep `local-data` beside the executable when moving the application. The delivered
folder has T1/T2 animation caches and the user's local Q3 import. The original
game installations and T2 reader kit are not needed for cached exports.

From the repository, using the existing virtual environment:

```powershell
.venv/Scripts/python.exe -m tools.animate_t1 --all --source C:/DiscSkinner/tools
.venv/Scripts/python.exe -m tools.animate_t2 --bake
.venv/Scripts/python.exe -m tools.import_q3 'C:/Program Files (x86)/Steam/steamapps/common/Quake 3 Arena/baseq3'
.venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath dist/animated-workshop DiscSkinnerApp.spec
Copy-Item -LiteralPath local-data -Destination dist/animated-workshop/local-data -Recurse
```

T1 source override: `SKINNER_T1_SOURCE`. T2 overrides: `SKINNER_T2_GAME_DATA` and
`SKINNER_T2_KIT`; exact original reader/source provenance remains in
[T2_IMPORT.md](T2_IMPORT.md). Generated caches and retail assets are local build
inputs, excluded from Git. Run the copy command for a fresh build directory.

## Validation

September 8, 2026: 40 regression tests passed, followed by the final eight focused
GLB/Q3 checks after integration. The packaged executable passed all 1,807 available
previews and OBJ/texture exports: T1 390, T2 959, Q3 458. Hidden Edge checked normal
window layouts, visible rotation controls, saved orientation across model changes
and page reloads, all three material workflows, and Q3 import through the UI.

Exported T1 `larmor` (45 clips), T2 `light_male` (42), and Q3 Sarge (25) passed
Khronos validation with zero errors/warnings and rendered visibly moving in an
independent Three.js r149 WebGL2 GLTFLoader. The packaged exports were byte-identical
to those reviewed files while original game/toolkit paths were disabled. Applied
T1/T2/Q3 skins retained their animation clips; legacy static fallback was explicitly
labeled. Full-catalog visual acceptance and native tray interaction remain unverified.

Focused tests cover source motion, tag assembly, skin mapping, malformed files,
export structure, clip interpolation, texture embedding, source failures and large
animation collections. `tools/check_browser.cjs` exercises rotation persistence,
all three catalogs, material edits and real downloads in hidden Edge.
`tools/check_packaged.py` checks complete catalog geometry and OBJ/texture exports.
`tools/check_glb.cjs` loads actual exported GLBs with Three.js GLTFLoader, validates
them with Khronos glTF Validator, and compares rendered animation frames.

Format references: [id Software MD3 definitions](https://github.com/id-Software/Quake-III-Arena/blob/master/code/qcommon/qfiles.h),
[MD3 surface normalization](https://github.com/id-Software/Quake-III-Arena/blob/master/code/renderer/tr_model.c),
[player tags and animation configuration](https://github.com/id-Software/Quake-III-Arena/blob/master/code/cgame/cg_players.c),
and [glTF 2.0](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html).
