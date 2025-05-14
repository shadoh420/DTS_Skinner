import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from dts_module import dts

disc_path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "disc.dts"
shape     = dts()
shape.load_file(disc_path)

# list anything that looks like it might hold transforms
candidates = [a for a in dir(shape) if any(x in a.lower() for x in ("node","transform","matrix"))]
print("Possible transform APIs on shape:\n", "\n ".join(candidates))
