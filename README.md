# DTS_Skinner - Tribes 1 Model Skin Previewer

Real-time texture previewer for Tribes 1 DTS models. Load models, apply skins, and see live updates. All Tribes models should work now.

![image](https://github.com/user-attachments/assets/58498de5-e4c6-4abe-ac2b-2330734eef9f)

## Features

*   Live texture reloading.
*   Model selection via dropdown.
*   Interactive 3D view with rotation controls.
*   **Export to OBJ**: Export models as OBJ with textures.
*   System tray icon: open app, access textures folder, quit.
*   Standalone executable.

## Usage (Executable)

1.  Run `SkinnerApp.exe`.
2.  If the program doesn't automatically open, use tray icon or browser at `http://localhost:5000/`.
3.  Select a model from the dropdown and click "Load/Refresh Model".
4.  Replace `.png` textures in `static/textures/` to test skins.
5.  Click **"Export to OBJ"** to save model with custom textures.
6.  Use the mouse to control the view, use the buttons for upside-down models.
7.  Right click the system tray icon to fully close the program when you're done (save your skins first).


## OBJ Export

The export feature creates a ZIP archive containing:
- `.obj` file (3D geometry with scale factor applied)
- `.mtl` file (material definitions)
- `.png` textures (all referenced textures)
- `README.txt` (import instructions)

**Import Steps**:
1. Extract the ZIP file
2. Import the `.obj` file into your 3D application
3. Textures should auto-link via the `.mtl` file
4. Adjust rotation or scale as needed for your target application

## Usage (Developer)

1.  Clone repo.
2.  `pip install -r requirements.txt`.
3.  Place assets as above.
4.  Run `python app.py`.

## Tech

Python, Flask, Socket.IO, Watchdog, Three.js, pystray, PyInstaller, Bov's DTS parser, Krogoth/Kaitai TribesToBlender.

## September 2026 compatibility repair

Preview and OBJ export now share support for the bundled `v`/`uv`/`tri` JSON
and newer `vertices`/`uvs`/`indices` files. Legacy models use the selected fallback
PNG in both preview and export; modern JSON keeps its authored material groups.
Existing geometry and poses are preserved, without regenerating model files.

Exported normals now agree with face winding, download errors are reported,
temporary ZIPs no longer accumulate, and texture reloads bypass caching and
recognize atomic file replacement. Missing textures are listed in the exported
README. `larmor.png` is absent: supply an armor skin via the fallback field.
`paintgun.json.nopng` remains excluded from the model list.

Executable builds keep editable skins in `static/textures` beside `SkinnerApp.exe`.
Bundled PNGs are copied only when absent, preserving edits across restarts.
Keep the executable in a writable folder. Source runs use the repository's
`static/textures` folder.

- Tests: `python -m unittest discover -s tests -v`
- Build: `pip install PyInstaller`, then `pyinstaller DiscSkinnerApp.spec`
- Without desktop UI: `python app.py --no-browser --no-tray --port 5057`
  (the executable accepts the same flags). The server binds to localhost.
- If the Socket.IO CDN is unavailable, preview/export still work; automatic
  texture updates require that script.

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

Validation: five regression tests pass, all ten bundled models load in a headless
Edge browser, and all ten export from the Windows executable. Browser download,
export-error reporting, CDN-unavailable preview, and skin persistence across an
executable restart were checked. Native tray interaction and user visual review
remain unverified.
