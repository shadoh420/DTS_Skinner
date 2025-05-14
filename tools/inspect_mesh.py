# tools/inspect_mesh.py
import sys, pathlib, pprint
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from dts_module import dts

# load your DTS
disc_path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "disc.dts"
shape     = dts()
shape.load_file(disc_path)

# pick the first non-empty mesh
mesh = next(m for m in shape.meshes if len(m.faces) > 0)

print("\n--- mesh attributes ---")
pprint.pprint(mesh.__dict__)

print("\n--- mesh dir() ---")
pprint.pprint([a for a in dir(mesh) if not a.startswith("__")])
