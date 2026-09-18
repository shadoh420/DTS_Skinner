# DTS Skinner

Browse, reskin, and export models from **Starsiege: Tribes, Tribes 2, and Quake 3**. Runs on Windows with a 3D viewer in your browser.

**[Download the latest release](https://github.com/shadoh420/DTS_Skinner/releases/latest)**

## Features

- Browse models and interiors; inspect and replace individual materials.
- Expand the texture browser, resize thumbnails, and search filenames or your own tags.
- Rotate textures 90° left/right or flip horizontally/vertically using temporary copies; save a copy only when wanted.
- Filter pixel dimensions or find textures with similar size or overall hue, with adjustable tolerances.
- Undo/redo session edits, tags, material choices, model transforms, camera moves, and browsing actions.
- Export OBJ with textures or GLB with available animations.
- Explore in walk/fly mode and fullscreen; rotate and move models along X/Y/Z.
- Expand **Transforms** when needed; position/orientation controls start collapsed.
- Adjust vertical field of view from 20–110° under Navigation, using a slider or exact value. Undo/redo includes FOV; Reset all restores 45°.
- **Save view PNG** downloads the current rendered view, including the background and preview transforms, without interface controls.
- The expanded texture browser keeps **Apply to slot** visible while its settings and thumbnails scroll.
- Restore the default view and materials with **Reset all**.
- **T2 Maps · Katabatic** opens an experimental offline map viewer with terrain, buildings, placed objects, fog and free-flight. Requires the separate local map pack included in the preview package; see [map setup and limitations](docs/t2-map-viewer.md).

## Getting started

1. Download and extract the release ZIP into a writable folder.
2. Run `SkinnerApp.exe`. Keep `static` and `local-data` beside it.
3. Choose a model, select a material and texture, then click **Apply to slot**.

If the browser doesn't open, visit `http://localhost:5000/`. Quit through the app's system-tray menu.

**Controls:** drag to orbit, wheel to zoom, right-drag to pan. **Walk / Fly** (Shift + backtick) uses WASD and Q/E; Escape returns to orbit. Position and rotation changes are preview-only.

### Texture workshop

**Expand texture browser** opens a large gallery with adjustable thumbnail size. Selecting a thumbnail previews it without changing the model. Rotate or flip the selected texture, then **Apply to slot** to see the copy on the model. **Save rotated copy** downloads a separate PNG with alpha preserved; it does not add it to the library. Applied copies also appear in OBJ and GLB exports. Original PNGs are never overwritten. Copies are temporary for the current window and disappear on reload/close; place a saved PNG in the relevant texture folder and reload to keep it in the library.

Enter comma-separated **User tags** such as `walls, metal, concrete`, then **Save tags**. Tags are stored by game and filename in `local-data/texture-tags.json` beside the executable and remain available after restart. Filename/tag search matches all entered words.

Under **Size & hue search**, width and height limits are inclusive; leave either end blank for no limit. For example, Width min `100` and Width max `120` finds textures 100–120 pixels wide at any height. **Find similar size** uses the highlighted texture's original dimensions and a ±pixel tolerance for each dimension (zero means exact). **Find similar hue** compares a sampled, chroma-weighted circular average hue, with tolerance from 0 to 180 degrees. Neutral or color-balanced textures without a meaningful average match each other. Hue search is a color aid, not image/pattern recognition. Transparent pixels contribute less; T2 uses RGB because alpha can store reflectivity. Similarity buttons clear previous search filters; changing tolerance updates matches immediately.

**Undo / Redo** retain the latest 100 actions in the current window, including persisted tag edits. Use Ctrl+Z and Ctrl+Shift+Z / Ctrl+Y outside text fields (text fields keep native text undo). Reset all is undoable. Downloads and external PNG edits are not reversed. Importing a new Q3 catalog starts a new history; window reload/close also clears history. Existing PNGs and saved tags remain on disk.

Switching games reads texture dimensions without decoding every image. Color analysis starts on the first **Find similar hue** request and can take several seconds for large libraries; subsequent searches reuse cached results until a texture changes. Selecting a texture reuses the gallery thumbnails.

Developer checks: `python -B -m unittest discover -s tests -v`, then run `node tools/check_texture_workshop.cjs http://127.0.0.1:5000 build/workshop-review` with optional Playwright available. The browser check verifies real texture pixels/downloads, gallery layout, search, and history without adding image files to the library.

Viewport checks: `node tools/check_viewport_workshop.cjs http://127.0.0.1:5000 build/viewport-review` verifies visible gallery actions at three window sizes, collapsed transforms, FOV/reset/history, and nonblank PNG downloads for all three games. Run against the packaged executable with its complete texture/model libraries.

![DTS Skinner](docs/workshop.png)

See [animation and export details](ANIMATION_EXPORT.md) and [T2 coverage and limitations](T2_IMPORT.md).

Built with Python and Three.js. Inspired by [exogen's T2 Model Skinner](https://github.com/exogen/t2-model-skinner).
