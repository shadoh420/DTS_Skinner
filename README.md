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
2.  `pip install Flask Flask-SocketIO watchdog pystray Pillow PyInstaller`.
3.  Place assets as above.
4.  Run `python app.py`.

## Tech

Python, Flask, Socket.IO, Watchdog, Three.js, pystray, PyInstaller, Bov's DTS parser, Krogoth/Kaitai TribesToBlender.
