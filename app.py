# app.py

from flask import Flask, send_from_directory, render_template, abort, jsonify, request
from flask_socketio import SocketIO
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pathlib
import threading
import os
import sys
import webbrowser # To open the web browser

# --- System Tray Imports ---
try:
    from pystray import MenuItem as item, Icon as icon, Menu as menu
    from PIL import Image
    HAS_PYSTRAY = True
except ImportError:
    print("WARNING: pystray or Pillow not installed. System tray icon will not be available.")
    print("         Install with: pip install pystray Pillow")
    HAS_PYSTRAY = False

# --- Path Setup ---
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    root = pathlib.Path(sys._MEIPASS)
else:
    root = pathlib.Path(__file__).resolve().parent
print(f"Application root determined as: {root}")

static_dir = root / "static"
templates_dir = root / "templates"
textures_dir = static_dir / "textures"
model_json_dir = static_dir / "model_json"
dts_source_dir = root / "tools" / "dts_files"
exporter_script_module_dir = root / "tools"

# --- Import Exporter ---
if str(exporter_script_module_dir) not in sys.path:
    sys.path.insert(0, str(exporter_script_module_dir))
run_exporter = None # Initialize
try:
    from export_model import main as run_exporter_func
    run_exporter = run_exporter_func # Assign to the global variable
    print("Successfully imported run_exporter from export_model.")
except ImportError as e:
    print(f"ERROR: Could not import 'main' as 'run_exporter' from 'export_model.py': {e}")
except Exception as e:
    print(f"An unexpected error occurred importing exporter: {e}")

# --- Flask App Setup ---
app = Flask(__name__, static_folder=str(static_dir), template_folder=str(templates_dir))
socketio = SocketIO(app, cors_allowed_origins="*") # No async_mode specified, defaults to threading

# --- Global variable to control Flask server thread ---
flask_server_thread = None
stop_event = threading.Event() # Used to signal the Flask server to shut down

# --- Flask Routes (mostly unchanged) ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/list_models")
def list_models():
    effective_model_json_dir = model_json_dir 
    if not effective_model_json_dir.exists():
        print(f"Model JSON directory not found: {effective_model_json_dir}")
        return jsonify([])
    
    models = []
    for f_path in effective_model_json_dir.glob("*.json"):
        model_name_stem = f_path.stem # Get filename without .json extension (e.g., "chaingun")
        
        # Check for hardcoded mapping first
        if model_name_stem in TEXTURE_MAPPINGS:
            guessed_texture_name = TEXTURE_MAPPINGS[model_name_stem]
        else:
            # Default guessing logic
            guessed_texture_name = model_name_stem + ".png"
        
        # Optional: Could add a check here to see if guessed_texture_name actually exists
        # in textures_dir and provide a fallback or flag if it doesn't.
        # For simplicity, we'll let the client try to load it.
        # if not (textures_dir / guessed_texture_name).exists():
        #     print(f"Warning: Guessed/mapped texture '{guessed_texture_name}' not found for model '{model_name_stem}'.")
            # Could set guessed_texture_name to None or a placeholder here if desired.

        models.append({"model_name": model_name_stem, "texture_name": guessed_texture_name})
    
    models.sort(key=lambda x: x["model_name"])
    return jsonify(models)

@app.route("/model_json/<model_name>")
def get_model_json(model_name):
    if ".." in model_name or "/" in model_name or "\\" in model_name: abort(400)
    json_filename = model_name + ".json"
    json_path = model_json_dir / json_filename
    if not json_path.exists():
        print(f"JSON for {model_name} not found. Exporting...")
        dts_path = dts_source_dir / (model_name + ".dts")
        if dts_path.exists():
            if run_exporter is None:
                print("ERROR: Exporter not available."); abort(500)
            try:
                model_json_dir.mkdir(parents=True, exist_ok=True)
                run_exporter(str(dts_path), str(model_json_dir))
                if not json_path.exists(): abort(500, "Export failed - JSON not created.")
            except Exception as e:
                print(f"Error exporting {model_name}: {e}"); import traceback; traceback.print_exc(); abort(500)
        else: abort(404, f"Source DTS for {model_name} not found.")
    return send_from_directory(str(model_json_dir), json_filename)

@app.route("/texture/<texture_filename>")
def get_texture(texture_filename):
    if ".." in texture_filename or "/" in texture_filename or "\\" in texture_filename: abort(400)
    return send_from_directory(str(textures_dir), texture_filename)

# --- File Watcher (unchanged) ---
class TextureWatcher(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory: return
        src_path = pathlib.Path(event.src_path)
        if src_path.parent == textures_dir and src_path.suffix.lower() == '.png':
            print(f"Texture modified: {src_path.name}")
            socketio.emit("texture_updated", {"filename": src_path.name})

texture_observer = None # Global observer instance
def start_watcher():
    global texture_observer
    if not textures_dir.exists():
        print(f"❌ Texture directory {textures_dir} not found. Watcher not started.")
        return
    if texture_observer is None: # Start only if not already running
        texture_observer = Observer()
        texture_observer.schedule(TextureWatcher(), str(textures_dir))
        texture_observer.start()
        print(f"✓ Watching for texture changes in {textures_dir}")

def stop_watcher():
    global texture_observer
    if texture_observer and texture_observer.is_alive():
        texture_observer.stop()
        texture_observer.join() # Wait for the thread to finish
        texture_observer = None
        print("Texture watcher stopped.")

# --- Flask Server Thread Function ---
def run_flask_app():
    print(f"Flask server thread started. PID: {os.getpid()}, Thread: {threading.get_ident()}")
    try:
        # When using socketio.run, it has its own shutdown mechanism.
        # We need a way to signal it. A common way is a shutdown route.
        socketio.run(app, host="0.0.0.0", port=5000, 
                     use_reloader=False, debug=False, 
                     allow_unsafe_werkzeug=True)
        print("Flask server has shut down.")
    except OSError as e:
        print(f"ERROR starting Flask server in thread: {e}")
        # Signal main thread that server failed to start if needed
    except Exception as e:
        print(f"UNEXPECTED ERROR in Flask server thread: {e}")
    finally:
        stop_event.set() # Signal that the server thread is done

# --- System Tray Functions ---
tray_icon_instance = None

def open_textures_folder(icon=None, item=None):
    global textures_dir # Use the globally defined textures_dir
    folder_path = str(textures_dir.resolve()) # Ensure it's an absolute path string
    print(f"Opening textures folder: {folder_path}")
    try:
        if sys.platform == "win32":
            os.startfile(folder_path) # Works well on Windows
        elif sys.platform == "darwin": # macOS
            subprocess.Popen(["open", folder_path])
        else: # Linux and other UNIX-like
            subprocess.Popen(["xdg-open", folder_path])
    except Exception as e:
        print(f"Error opening textures folder '{folder_path}': {e}")
        # Optionally, show an error to the user if you have a GUI error mechanism

def open_browser(icon=None, item=None):
    print("Opening browser to http://localhost:5000/")
    webbrowser.open_new_tab("http://localhost:5000/")

def quit_application(icon=None, item=None):
    # ... (quit_application function remains the same as before) ...
    global tray_icon_instance, flask_server_thread
    print("Quit application called.")
    stop_event.set() 
    if HAS_PYSTRAY and tray_icon_instance:
        print("Stopping tray icon...")
        tray_icon_instance.stop()
    stop_watcher()
    print("Attempting to shut down Flask server...")
    print("Exiting application...")
    os._exit(0)


def setup_tray_icon():
    global tray_icon_instance
    if not HAS_PYSTRAY:
        print("System tray icon disabled (pystray/Pillow not found).")
        return

    try:
        icon_path = root / "static" / "tray_icon.png" 
        if not icon_path.exists():
            print(f"WARNING: Tray icon image not found at {icon_path}. Using default.")
            image = None 
        else:
            image = Image.open(str(icon_path))
        
        tray_menu = menu(
            item('Open Skinner', open_browser, default=True),
            item('Open Textures Folder', open_textures_folder), # <-- NEW MENU ITEM
            menu.SEPARATOR,
            item('Quit Skinner', quit_application)
        )
        tray_icon_instance = icon("Skinner", image, "Skinner", tray_menu)
        
        # This will be called from __main__ now
        # print("Starting tray icon...")
        # tray_icon_instance.run() 
    except Exception as e:
        print(f"Error setting up tray icon: {e}")
        import traceback
        traceback.print_exc()

# --- Hardcoded DTS to PNG Mappings ---
# Keys are the DTS file stems (without .dts)
# Values are the corresponding PNG filenames
TEXTURE_MAPPINGS = {
    "ammo1": "ammo.png",
    "grenadel": "grenade.png",
    "mine": "r_mine1.png"
    # Add any other known mappings here
}

# --- Main Application Logic  ---
if __name__ == "__main__":
    if run_exporter is None:
         print("CRITICAL WARNING: Exporter function could not be imported. On-demand export WILL FAIL.")
    
    (root / "tools").mkdir(parents=True, exist_ok=True)
    dts_source_dir.mkdir(parents=True, exist_ok=True)
    textures_dir.mkdir(parents=True, exist_ok=True) # Ensure textures_dir exists
    model_json_dir.mkdir(parents=True, exist_ok=True)

    flask_server_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_server_thread.start()
    print(f"Flask server thread initiated (ID: {flask_server_thread.ident}).")

    start_watcher()

    if HAS_PYSTRAY:
        setup_tray_icon() # Setup the icon and menu
        if tray_icon_instance: # Check if setup was successful
            print("Starting tray icon. Main thread will block here.")
            # Open browser after a short delay to give server time to start
            threading.Timer(1.0, open_browser).start() 
            try:
                tray_icon_instance.run() # This is the blocking call for pystray
            except KeyboardInterrupt: # Allow Ctrl+C to quit if run from console
                print("Keyboard interrupt received, quitting.")
                quit_application()
            except Exception as e: # Catch other errors during tray run
                print(f"Error running tray icon: {e}")
                quit_application()
            finally:
                print("Main thread (tray icon loop) finished.")
                if not stop_event.is_set():
                    quit_application()
        else: # pystray setup failed for some reason
            print("Failed to setup tray icon. Running without tray.")
            # Fallback behavior if tray setup fails but pystray was found
            print("Open http://localhost:5000/ in your browser.")
            print("Press Ctrl+C in this console to quit.")
            try:
                while not stop_event.is_set(): stop_event.wait(timeout=1.0)
            except KeyboardInterrupt: print("Ctrl+C received, quitting.")
            finally: quit_application()
    else:
        # No pystray, run as before
        print("No system tray. Flask server running in a daemon thread.")
        print("Open http://localhost:5000/ in your browser.")
        print("Press Ctrl+C in this console to quit.")
        try:
            while not stop_event.is_set(): stop_event.wait(timeout=1.0)
        except KeyboardInterrupt: print("Ctrl+C received, quitting.")
        finally: quit_application()
