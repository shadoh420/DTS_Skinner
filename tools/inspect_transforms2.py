# tools/inspect_transforms2.py
import sys, pathlib, pprint
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from dts_module import dts

# load the shape
disc_path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "disc.dts"
shape     = dts()
shape.load_file(disc_path)

print("\n=== Nodes ({} total) ===".format(shape.num_nodes))
for i, nm in enumerate(shape.nodes):
    print(f"  [{i:2d}] {nm}")

print("\n=== Transforms ({} total) ===".format(shape.num_transforms))
# show the first 8 entries
for i, tr in enumerate(shape.transforms[:8]):
    print(f"\n--- transforms[{i}]  (type: {type(tr)}) ---")
    # if it's a simple tuple/list, just pprint it
    if isinstance(tr, (list, tuple)):
        pprint.pprint(tr)
    else:
        # otherwise dump its __dict__ or its attributes
        attrs = getattr(tr, "__dict__", None)
        if attrs:
            pprint.pprint(attrs)
        else:
            print(tr)
