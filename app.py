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
import subprocess # For opening folders on Linux/macOS

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
    root = pathlib.Path(sys._MEIPASS) # PyInstaller temporary path
else:
    root = pathlib.Path(__file__).resolve().parent
print(f"Application root determined as: {root}")

static_dir = root / "static"
templates_dir = root / "templates"
textures_dir = static_dir / "textures" # For PNGs
model_json_dir = static_dir / "model_json" # Where pre-processed JSONs are stored

# Source directories (can be used by list_models for discovery if desired, but not for on-demand export)
# dts_source_dir = root / "tools" / "dts_files"
# interior_source_dir = root / "tools" / "interior_files"

# Exporter imports are no longer needed here if we pre-process everything
# exporter_script_module_dir = root / "tools"
# if str(exporter_script_module_dir) not in sys.path:
#     sys.path.insert(0, str(exporter_script_module_dir))
# run_dts_exporter = None
# run_interior_exporter = None
# try:
#     # from export_model import main as run_dts_exporter_func
#     # run_dts_exporter = run_dts_exporter_func
#     # print("Successfully imported run_dts_exporter from export_model.")
#     # from export_interior import main as run_interior_exporter_func
#     # run_interior_exporter = run_interior_exporter_func
#     # print("Successfully imported run_interior_exporter from export_interior.")
# except ImportError as e:
#     print(f"INFO: Exporter functions not imported (expected for pre-processing workflow): {e}")
# except Exception as e:
#     print(f"An unexpected error occurred importing exporters (expected for pre-processing workflow): {e}")


# --- Flask App Setup ---
app = Flask(__name__, static_folder=str(static_dir), template_folder=str(templates_dir))
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Global variable to control Flask server thread ---
flask_server_thread = None
stop_event = threading.Event() # Used to signal the Flask server to shut down

# --- Flask Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/list_models")
def list_models():
    # List models based on existing .json files in static/model_json/
    models = []
    if model_json_dir.exists():
        for f_path in model_json_dir.glob("*.json"):
            model_name_stem = f_path.stem
            # Guessing texture name for DTS models can still be useful for the dropdown's default
            # For DIS, the JSON itself will list all textures.
            guessed_texture_name = TEXTURE_MAPPINGS.get(model_name_stem, model_name_stem + ".png")
            # 'type' is not strictly needed if all are JSON, but can be kept if UI uses it.
            models.append({"model_name": model_name_stem, "texture_name": guessed_texture_name})
    else:
        print(f"Model JSON directory not found: {model_json_dir}")
        
    models.sort(key=lambda x: x["model_name"])
    if not models:
        print(f"No pre-processed .json models found in {model_json_dir}. Please run batch export scripts.")
    return jsonify(models)

@app.route("/model_json/<model_name>")
def get_model_json(model_name):
    if ".." in model_name or "/" in model_name or "\\" in model_name: abort(400)
    
    json_filename = model_name + ".json"
    json_path = model_json_dir / json_filename
    
    if not json_path.exists():
        # With pre-processing, if JSON doesn't exist, it's a 404.
        print(f"ERROR: Pre-processed JSON for '{model_name}' not found at {json_path}.")
        abort(404, f"Model data for '{model_name}' not found. Please ensure it has been pre-processed by running the appropriate batch export script (e.g., batch_export_dts.py or batch_export_interiors.py).")
            
    return send_from_directory(str(model_json_dir), json_filename)

@app.route("/texture/<texture_filename>")
def get_texture(texture_filename):
    if ".." in texture_filename or "/" in texture_filename or "\\" in texture_filename: abort(400)
    return send_from_directory(str(textures_dir), texture_filename)

# --- File Watcher ---
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
        socketio.run(app, host="0.0.0.0", port=5000, 
                     use_reloader=False, debug=False, 
                     allow_unsafe_werkzeug=True) # allow_unsafe_werkzeug for programmatic shutdown
        print("Flask server has shut down.")
    except OSError as e:
        if e.errno == 98: # Address already in use
             print(f"ERROR starting Flask server: Port 5000 is already in use.")
        else:
            print(f"ERROR starting Flask server in thread: {e}")
    except Exception as e:
        print(f"UNEXPECTED ERROR in Flask server thread: {e}")
    finally:
        stop_event.set() # Signal that the server thread is done

# --- System Tray Functions ---
tray_icon_instance = None

def open_textures_folder(icon=None, item=None):
    global textures_dir
    folder_path = str(textures_dir.resolve())
    print(f"Opening textures folder: {folder_path}")
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_path])
        else: # Linux and other UNIX-like
            subprocess.Popen(["xdg-open", folder_path])
    except Exception as e:
        print(f"Error opening textures folder '{folder_path}': {e}")

def open_browser(icon=None, item=None):
    print("Opening browser to http://localhost:5000/")
    webbrowser.open_new_tab("http://localhost:5000/")

# Add a shutdown route for programmatic server stop
@app.route('/shutdown_server_please', methods=['GET','POST']) # Allow GET for easy browser call during dev
def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        print('Not running with the Werkzeug Server or shutdown not supported.')
        # For non-Werkzeug or if direct shutdown fails, rely on os._exit in quit_application
        return 'Server shutdown failed (not Werkzeug or not supported).'
    func()
    print("Server shutdown initiated via /shutdown_server_please route.")
    return 'Server shutting down...'

def quit_application(icon=None, item=None):
    global tray_icon_instance, flask_server_thread
    print("Quit application called.")
    
    if HAS_PYSTRAY and tray_icon_instance:
        print("Stopping tray icon...")
        tray_icon_instance.stop() # This should allow the tray_icon_instance.run() to unblock

    stop_watcher()
    
    print("Attempting to shut down Flask server via HTTP request...")
    try:
        # Make a request to the shutdown route
        import requests
        requests.get("http://localhost:5000/shutdown_server_please", timeout=2)
    except Exception as e:
        print(f"Could not reach shutdown route (server might be already down or unresponsive): {e}")

    stop_event.set() # Signal Flask thread to stop if it's in a loop (less relevant with socketio.run)

    if flask_server_thread and flask_server_thread.is_alive():
        print("Waiting for Flask server thread to join...")
        flask_server_thread.join(timeout=5) # Wait for the thread to finish
        if flask_server_thread.is_alive():
            print("Flask server thread did not join in time.")

    print("Exiting application with os._exit(0)...")
    os._exit(0) # Force exit if threads are stuck


def setup_tray_icon():
    global tray_icon_instance
    if not HAS_PYSTRAY:
        print("System tray icon disabled (pystray/Pillow not found).")
        return

    try:
        icon_path = root / "static" / "tray_icon.png" 
        image = None
        if icon_path.exists():
            image = Image.open(str(icon_path))
        else:
            print(f"WARNING: Tray icon image not found at {icon_path}. Using default system icon if available or no icon.")
        
        tray_menu = menu(
            item('Open Skinner', open_browser, default=True),
            item('Open Textures Folder', open_textures_folder),
            menu.SEPARATOR,
            item('Quit Skinner', quit_application)
        )
        tray_icon_instance = icon("DTS Skinner", image, "DTS Model Skinner", tray_menu)
        
    except Exception as e:
        print(f"Error setting up tray icon: {e}")
        import traceback
        traceback.print_exc()
        tray_icon_instance = None # Ensure it's None if setup fails

# --- Hardcoded DTS to PNG Mappings (for /list_models fallback texture guessing) ---
TEXTURE_MAPPINGS = {
    "ammo1": "ammo.png",
    "grenadel": "grenade.png",
    "sensor_small": "sensor_rmt.png",
    "mine": "r_mine1.png"
}

# --- Main Application Logic  ---
if __name__ == "__main__":
    # No longer need to check for exporter imports here if using pre-processing
    # if run_dts_exporter is None or run_interior_exporter is None:
    #      print("CRITICAL WARNING: One or more exporter functions could not be imported. On-demand export WILL FAIL.")
    
    # Ensure necessary directories exist
    # (root / "tools").mkdir(parents=True, exist_ok=True) # tools dir for batch scripts
    # dts_source_dir.mkdir(parents=True, exist_ok=True)    # if you still want to scan them
    # interior_source_dir.mkdir(parents=True, exist_ok=True) # if you still want to scan them
    textures_dir.mkdir(parents=True, exist_ok=True)
    model_json_dir.mkdir(parents=True, exist_ok=True) # Crucial for pre-processing

    flask_server_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_server_thread.start()
    print(f"Flask server thread initiated (ID: {flask_server_thread.ident}).")

    start_watcher()

    if HAS_PYSTRAY:
        setup_tray_icon()
        if tray_icon_instance:
            print("Starting tray icon. Main thread will block here.")
            threading.Timer(1.5, open_browser).start() # Slightly longer delay
            try:
                tray_icon_instance.run() # This is the blocking call for pystray
            except KeyboardInterrupt: # Allow Ctrl+C to quit if run from console
                print("Keyboard interrupt received, quitting application.")
                quit_application()
            except SystemExit: # pystray might raise this on icon.stop()
                print("SystemExit caught from pystray, application should be quitting.")
            except Exception as e: # Catch other errors during tray run
                print(f"Error running tray icon: {e}")
                quit_application()
            finally:
                print("Tray icon run() method has exited.")
                # If quit_application wasn't called or didn't fully exit, ensure it does.
                if not stop_event.is_set(): # Check if already signaled
                    quit_application() 
        else: 
            print("Failed to setup tray icon. Running without tray.")
            print("Open http://localhost:5000/ in your browser.")
            print("Press Ctrl+C in this console to quit.")
            try:
                while not stop_event.is_set(): stop_event.wait(timeout=1.0)
            except KeyboardInterrupt: print("Ctrl+C received, quitting.")
            finally: quit_application()
    else:
        print("No system tray. Flask server running in a daemon thread.")
        print("Open http://localhost:5000/ in your browser.")
        print("Press Ctrl+C in this console to quit.")
        try:
            while not stop_event.is_set(): stop_event.wait(timeout=1.0)
        except KeyboardInterrupt: print("Ctrl+C received, quitting.")
        finally: quit_application()