"""Characters exported from an Unreal Engine 5 project (Fab assets) by tools/ue5_export.py, in their reference pose.

python -m tools.import_fab --export C:/tmp/fab-export

Each folder of the export is one character kit: its skeletal meshes (FBX, Z up, centimetres) become models in page
axes (Y up), one material slot per mesh slot. Unreal builds these materials' colours in the graph, not in a texture:
each base colour is a chain of lerps between colour parameters by mask channels. The import bakes it per material
instance into one PNG (base colour times ambient occlusion, plus emissive), evaluating the graph as Unreal does
(sRGB textures decoded, linear colours encoded for display). A lerp whose mask the import cannot evaluate (an engine
material function) keeps its lower layer and is listed in the report. Every set of sibling instances that fills
the mesh's slots is a skin: one instance per slot from one folder (SciFiGirl's Type_A..F), or else each instance of
the slot's parent (SciFiSoldier's MI_SciFiSoldier_2..5). Animations are listed, not played.
"""
import argparse
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image

from tools.fbx_mesh import fbx_mesh
from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.texture_workshop import write_json_atomic


class Unsupported(ValueError):
    pass


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def evaluate(node, params, sample, skipped):
    """A material expression tree (tools/ue5_export.graph) as a float32 array broadcasting to (h, w, channels), or
    None where nothing is connected. `sample(texture, pin)` reads a texture's output; the expressions of lerp masks
    left out are added to `skipped`."""
    if node is None:
        return None
    kind, inputs = node['cls'].removeprefix('MaterialExpression'), node['inputs']

    def arg(i, const=None):
        found = evaluate(inputs[i], params, sample, skipped) if i < len(inputs) else None
        return np.float32(const) if found is None else found
    if kind == 'Reroute':
        return arg(0)
    if kind in ('VectorParameter', 'ScalarParameter'):
        value = params.get(node['parameter_name'], node['default_value'])
        return np.asarray(value[:3] if isinstance(value, list) else value, np.float32)
    if kind == 'Constant':
        return np.float32(node['r'])
    if kind == 'TextureSample':
        return sample(node['texture'], node.get('pin') or 'RGB')
    if kind == 'LinearInterpolate':
        a, b = arg(0, node.get('const_a')), arg(1, node.get('const_b'))
        try:
            alpha = arg(2, node.get('const_alpha'))
        except Unsupported as exc:
            skipped.add(str(exc))
            return a  # ponytail: an unevaluable mask keeps the lower layer; port the function if one matters.
        return a + (b - a) * alpha
    if kind in ('Multiply', 'Add'):
        a, b = arg(0, node.get('const_a')), arg(1, node.get('const_b'))
        return a * b if kind == 'Multiply' else a + b
    if kind == 'OneMinus':
        return 1 - arg(0, 0)
    raise Unsupported(kind + (' ' + node['material_function'].rsplit('.', 1)[-1] if node.get('material_function') else ''))


BAKED = ('MP_BASE_COLOR', 'MP_AMBIENT_OCCLUSION', 'MP_EMISSIVE_COLOR')
CHANNELS = {'R': [0], 'G': [1], 'B': [2], 'A': [3], 'RGB': [0, 1, 2], 'RGBA': [0, 1, 2, 3]}


def sampler(folder, rows, graph):
    """`sample(texture, pin)` for one bake: the export's textures as linear float32, all at the largest size the
    baked outputs read, and that size (w, h)."""
    rows = {r['path']: r for r in rows}
    paths = set(re.findall(r'"texture": "([^"]+)"', json.dumps([graph.get(name) for name in BAKED])))
    missing = paths - set(rows)
    if missing:
        raise ValueError('Textures not exported: ' + ', '.join(sorted(missing)))
    size = max((Image.open(folder / rows[p]['file']).size for p in paths), default=(64, 64))
    cache = {}

    def sample(path, pin):
        if path not in cache:
            image = Image.open(folder / rows[path]['file']).convert('RGBA')
            cache[path] = np.asarray(image.resize(size, Image.BILINEAR) if image.size != size else image, np.float32) / 255
            if rows[path]['srgb']:
                cache[path][..., :3] = srgb_to_linear(cache[path][..., :3])
        return cache[path][..., CHANNELS.get(pin, [0, 1, 2])]
    return sample, size


def bake(params, base, folder, texture_rows, unsupported):
    """A material's display colour as an RGB image: base colour x ambient occlusion + emissive."""
    graph = base['graph']
    sample, size = sampler(folder, texture_rows, graph)

    def output(name, default):
        skipped = set()
        try:
            found = evaluate(graph.get(name), params, sample, skipped)
        except Unsupported as exc:
            skipped.add(str(exc))
            found = None
        unsupported.update(f"{base['name']} {name}: {what}" for what in skipped)
        return np.float32(default) if found is None else found
    occlusion = output('MP_AMBIENT_OCCLUSION', 1)
    occlusion = occlusion[..., :1] if np.ndim(occlusion) == 3 else occlusion  # A sampled texture's red.
    colour = output('MP_BASE_COLOR', 0.5) * occlusion + output('MP_EMISSIVE_COLOR', 0)
    colour = np.broadcast_to(colour, (size[1], size[0], 3))
    return Image.fromarray(np.round(linear_to_srgb(colour) * 255).astype(np.uint8), 'RGB')


def skins(slots, materials):
    """[(label, [material path per slot])]: the mesh's own first, then sibling instance sets (see the docstring)."""
    by_path = {m['path']: m for m in materials}
    parents = [by_path.get(path, {}).get('parent') for path in slots]
    folder = lambda m: m['path'].rsplit('/', 1)[0]
    candidates = [[m for m in materials if m.get('parent') and m['parent'] == parent] for parent in parents]
    sets = {}
    for slot, found in enumerate(candidates):
        for m in found:
            per_folder = [[c for c in others if folder(c) == folder(m)] for others in candidates]
            key = folder(m).rsplit('/', 1)[-1] if all(len(c) == 1 for c in per_folder) else m['name']
            sets.setdefault(key, list(slots))[slot] = m['path']
    out = [('Default', list(slots))]
    for label, chosen in sorted(sets.items()):
        if chosen not in [c for _, c in out]:
            out.append((label, chosen))
        elif chosen == slots:
            out[0] = (label, chosen)
    return out


def safe(name):
    return re.sub(r'[^a-z0-9_.-]+', '_', name.lower()).strip('._') or '_'


def mesh(path, slot_names):
    """Indexed page-axes geometry with one group per slot, from the FBX's triangles per material name."""
    groups = fbx_mesh(Path(path).read_bytes())
    corners, out_groups = [], []
    for slot, name in enumerate(slot_names):
        if name not in groups:
            continue
        positions, normals, uvs = groups[name]
        n = len(positions) * 3
        out_groups.append(dict(start=sum(len(c) for c in corners), count=n, materialIndex=slot))
        rows = np.concatenate([positions.reshape(-1, 3), normals.reshape(-1, 3), uvs.reshape(-1, 2)], 1)
        corners.append(rows)
    if not corners or set(groups) - set(slot_names):
        raise ValueError(f'{path}: FBX materials {sorted(groups)} do not match the slots {slot_names}')
    rows = np.concatenate(corners).astype(np.float64)
    rows[:, [0, 1, 2]] = rows[:, [0, 2, 1]] * [1, 1, -1]  # Z up to Y up: (x, y, z) -> (x, z, -y), a rotation.
    rows[:, [3, 4, 5]] = rows[:, [3, 5, 4]] * [1, 1, -1]
    rows[:, 7] = 1 - rows[:, 7]  # The FBX writer flips Unreal's V.
    unique, indices = np.unique(np.round(rows, 5), axis=0, return_inverse=True)
    return dict(vertices=unique[:, :3].ravel(), normals=unique[:, 3:6].ravel(), uvs=unique[:, 6:].ravel(),
                indices=indices.ravel().astype(np.int64), groups=out_groups)


def import_catalog(export, output):
    export, output = Path(export), Path(output)
    catalog, report = [], dict(characters=[], unsupported=[], skipped=[])
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    for kit in sorted(p.parent for p in export.glob('*/materials.json')):
        info = json.loads((kit / 'materials.json').read_text(encoding='utf-8'))
        materials = {m['path']: m for m in info['materials']}
        unsupported, baked = set(), {}

        def resolve(path):
            """The base material under an instance chain, and the parameters the chain sets (nearest wins)."""
            params, m = {}, materials[path]
            while 'graph' not in m:
                params = {**m['scalars'], **m['colours'], **params}
                m = materials[m['parent']]
            return m, params
        for entry in info['meshes']:
            slots = [s['material'] for s in entry['slots']]
            try:
                data = mesh(kit / entry['fbx'], [p.rsplit('.', 1)[-1] for p in slots])
            except ValueError as exc:
                report['skipped'].append(str(exc))
                continue
            for label, chosen in skins(slots, info['materials']):
                names = []
                for path in chosen:
                    if path not in baked:
                        base, params = resolve(path)
                        filename = safe(kit.name + '_' + materials[path]['name']) + '.png'
                        bake(params, base, kit, info['textures'], unsupported).save(output / 'textures' / filename)
                        baked[path] = filename
                    names.append(baked[path])
                ident = safe(kit.name + '_' + entry['name'] + ('' if label == 'Default' else '_' + label))
                write_model_data(output / 'model_json' / (ident + '.json'), dict(
                    data, game='fab', material_textures=names, winding='ccw',
                    metadata=dict(source=f'{kit.name}/{entry["fbx"]}', skin=label, materials=chosen, pose='reference',
                                  units='centimetres', animations=[a['name'] for a in info['animations']])))
                catalog.append(dict(game='fab', model_name=ident, display_name=f'{entry["name"]} / {label}', source=entry['fbx'],
                                    category=kit.name, status='ready', texture_name=names[0], warnings=[]))
        report['characters'].append(dict(kit=kit.name, meshes=len(info['meshes']), animations=len(info['animations']),
                                         materials_baked=len(baked)))
        report['unsupported'] += sorted(unsupported)
    if not catalog:
        raise ValueError(f'No exported characters in {export} (folders with materials.json from tools/ue5_export.py)')
    report.update(entries=len(catalog), report=str(output / 'import-report.json'))
    write_json_atomic(output / 'catalog.json', catalog)
    write_json_atomic(output / 'import-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--export', required=True, type=Path, help='FAB_EXPORT_OUT of tools/ue5_export.py')
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'fab')
    print(json.dumps(import_catalog(**vars(parser.parse_args())), indent=2))
