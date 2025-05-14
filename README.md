# DTS_Skinner - Tribes 1 Model Skin Previewer

Real-time texture previewer for Tribes 1 DTS models. Load models, apply skins, and see live updates. (Only weapons supported for now.)

![image](https://github.com/user-attachments/assets/58498de5-e4c6-4abe-ac2b-2330734eef9f)

## Features

*   Live texture reloading.
*   Model selection via dropdown.
*   Interactive 3D view with rotation controls.
*   System tray icon: open app, access textures folder, quit.
*   Standalone executable.

## Usage (Executable)

1.  Run `DiscSkinnerApp.exe`.
2.  If the program doesn't automatically open, use tray icon or browser at `http://localhost:5000/`.
3.  Replace `.png` textures in `static/textures/`.


## Usage (Developer)

1.  Clone repo.
2.  `pip install Flask Flask-SocketIO watchdog pystray Pillow PyInstaller`.
3.  Place assets as above.
4.  Run `python app.py`.

## Tech

Python, Flask, Socket.IO, Watchdog, Three.js, pystray, PyInstaller, Bov's DTS parser.
