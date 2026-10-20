"""Read Skinner model metadata and expand stored geometry for preview and export."""

import json
import math
import pathlib
import re
import threading
if __package__:
    from .texture_workshop import TEXTURE_GAMES, normalize_transform, write_json_atomic
else:
    from texture_workshop import normalize_transform, write_json_atomic

TEXTURE_MAPPINGS = {
    "disc": "stock_disc.png",
    "larmor": "base.larmor.png",
    "ammo1": "ammo.png",
    "grenadel": "grenade.png",
    "sensor_small": "sensor_rmt.png",
    "mine": "r_mine1.png",
}


def default_texture(model_name):
    return TEXTURE_MAPPINGS.get(model_name.lower(), model_name + ".png")


def model_sort_key(name):
    """Alphabetize without case jumps, with numbered variants in numeric order."""
    parts = tuple(int(part) if part.isdecimal() else part.casefold()
                  for part in re.split(r'(\d+)', name))
    return parts, name.casefold(), name


def t1_catalog_names(model_dir):
    """The shipped JSON files are the T1 catalog; manifests live outside this folder."""
    return sorted((p.stem for p in pathlib.Path(model_dir).iterdir()
                   if p.is_file() and p.suffix.casefold() == '.json'), key=model_sort_key)


def t1_building_names(model_dir):
    """Additive conversion receipts also identify the bundled custom-building family."""
    return {name for path in (pathlib.Path(model_dir).parent / 't1-buildings').glob('*.json')
            for name in json.loads(path.read_text(encoding='utf-8'))['models']}


def t1_default_families(model_dir):
    """The shipped T1 families (tools/sort_t1_families.py --bundle): model name -> family, beside the catalog."""
    path = pathlib.Path(model_dir).parent / 't1-families.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


MODEL_GAMES = ('t1', 't2', 'q3', 'diabotical', 'reflex', 'ta', 'tv', 'trpg', 'sw', 'rm', 't2rpg', 'ss', 'es1', 'es2', 'rb3d', 'unreal', 'ut', 'dx', 'quake', 'quake2', 'daikatana', 'anachronox', 'sin', 'rtcw', 'et', 'jk2', 'ja', 'azrael', 'vtmb', 'eye', 'tvg', 'tas', 'nos', 'ge', 'doom3', 'quake4', 'fab')
_families_lock = threading.Lock()


def read_families(path):
    """The user's own families: {game: {model_name: family}}, laid over the imports' categories."""
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(game not in MODEL_GAMES or not isinstance(entries, dict)
                                          or not all(isinstance(k, str) and isinstance(v, str) for k, v in entries.items())
                                          for game, entries in data.items()):
        raise ValueError('Invalid model families file; existing data was preserved')
    return data


def save_families(path, game, models, family):
    """Move models into a family; an empty family puts them back in their imported one."""
    if game not in MODEL_GAMES:
        raise ValueError('Unknown model game')
    if not isinstance(models, list) or not models or not all(isinstance(m, str) and 0 < len(m) <= 200 for m in models):
        raise ValueError('Expected a list of model names')
    if not isinstance(family, str):
        raise ValueError('Expected a family name')
    family = ' '.join(family.split())
    if len(family) > 80:
        raise ValueError('Family names are at most 80 characters')
    with _families_lock:
        data = read_families(path)
        entries = data.setdefault(game, {})
        for name in models:
            if family:
                entries[name] = family
            else:
                entries.pop(name, None)
        write_json_atomic(path, data)
    return family


def rename_family(path, game, old, new):
    """Rename one of the user's families; an empty new name deletes it, putting its models back in their imported ones."""
    if game not in MODEL_GAMES:
        raise ValueError('Unknown model game')
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError('Expected family names')
    new = ' '.join(new.split())
    if len(new) > 80:
        raise ValueError('Family names are at most 80 characters')
    with _families_lock:
        data = read_families(path)
        entries = data.get(game, {})
        models = [name for name, family in entries.items() if family == old]
        if not models:
            raise ValueError(f'{old!r} is not one of your families; families from an import cannot be renamed or deleted')
        for name in models:
            if new:
                entries[name] = new
            else:
                del entries[name]
        write_json_atomic(path, data)
    return models


def material_texture_refs(data, material_overrides=None):
    """Validate slot references while keeping filenames compatible with old clients."""
    game = data.get('game', 't1')
    if game not in MODEL_GAMES:
        raise ValueError('Unknown model game')
    names = data['material_textures']
    if not isinstance(names, list) or not names:
        raise ValueError('Material textures must be a nonempty list')
    names = list(names)
    games = data.get('material_texture_games', [game] * len(names))
    if not isinstance(games, list) or len(games) != len(names):
        raise ValueError('Texture games must match material slots')
    games = list(games)
    transforms = data.get('material_texture_transforms', [None] * len(names))
    if not isinstance(transforms, list) or len(transforms) != len(names):
        raise ValueError('Texture transforms must match material slots')
    transforms = [normalize_transform(value) for value in transforms]
    if material_overrides is not None and not isinstance(material_overrides, dict):
        raise ValueError('Material overrides must be a slot-to-texture object')
    for slot, reference in (material_overrides or {}).items():
        if not isinstance(slot, str) or not slot.isascii() or not slot.isdecimal() or not 0 <= int(slot) < len(names):
            raise ValueError('Material slot outside model material array')
        if isinstance(reference, dict):
            if set(reference) not in ({'game', 'filename'}, {'game', 'filename', 'transform'}):
                raise ValueError('Texture reference must contain game, filename and optional transform')
            source_game, filename = reference['game'], reference['filename']
            transform = normalize_transform(reference.get('transform'))
        else:
            source_game, filename = game, reference
            transform = normalize_transform()
        names[int(slot)], games[int(slot)] = filename, source_game
        transforms[int(slot)] = transform
    for name, source_game in zip(names, games):
        if source_game not in TEXTURE_GAMES:
            raise ValueError('Unknown texture game')
        # Imported untextured slots are labels, never filesystem lookups.
        placeholder = isinstance(name, str) and re.fullmatch(r'\[Slot [0-9]+: [A-Za-z0-9 _.-]+\]', name)
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or name.endswith((' ', '.')) or any(c in name for c in '/\\<>"|?*')
                or (':' in name and not placeholder)
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError('Texture names must be local filenames')
    return names, games, transforms


def material_texture_paths(data, textures_dir, texture_dirs=None):
    names, games, _ = material_texture_refs(data)
    directories = texture_dirs if texture_dirs is not None else {data.get('game', 't1'): textures_dir}
    if any(game not in directories for game in games):
        raise ValueError('Texture source game directory is unavailable')
    return [pathlib.Path(directories[game]) / name for name, game in zip(names, games)]


def geometry(path, data):
    """A Diabotical model's (tools/import_diabotical_models.py) vertices: its groups are ranges of corners (8 float32:
    position, normal, uv with v up) in a file of models/ beside model_json/, read as shared, indexed vertices."""
    import numpy as np
    if not re.fullmatch(r'(c-)?[0-9a-f]{16}\.bin', str(data['geometry'])):
        raise ValueError('Invalid model geometry file')
    corners = np.fromfile(path.parent.parent / 'models' / data['geometry'], '<f4').reshape(-1, 8)
    if any(type(g.get(k)) is not int for g in data['groups'] for k in ('start', 'count')) or any(
            g['start'] < 0 or g['count'] <= 0 or g['count'] % 3 or g['start'] + g['count'] > len(corners) for g in data['groups']):
        raise ValueError('Invalid model geometry range')
    corners = np.concatenate([corners[g['start']:g['start'] + g['count']] for g in data['groups']]).astype(float)
    corners[:, 7] = 1 - corners[:, 7]
    rows, indices = np.unique(np.round(corners, 4), axis=0, return_inverse=True)
    groups, at = [], 0
    for group in data['groups']:
        groups.append(dict(group, start=at))
        at += group['count']
    return dict(vertices=rows[:, :3].ravel().tolist(), normals=rows[:, 3:6].ravel().tolist(), uvs=rows[:, 6:].ravel().tolist(),
                indices=indices.ravel().tolist(), groups=groups)


def write_model_data(path, data):
    """Indexed array storage; skin families share content-addressed bytes.

    Floats are float32, the precision the browser and GLB export use anyway. JSON
    carries byte offsets, scalar counts and little-endian types, not corners.
    """
    import hashlib
    import numpy as np
    path = pathlib.Path(path)
    result, arrays, chunks, offset = dict(data), {}, [], 0
    for name in ('vertices', 'normals', 'uvs', 'indices', 'colors'):
        if name not in data:
            continue
        values = np.asarray(data[name])
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError('Invalid model array: ' + name)
        if name == 'indices':
            if values.dtype.kind not in 'iu' or (values < 0).any() or (values > 0xffffffff).any():
                raise ValueError('Invalid model indices')
            dtype = '<u4'
        else:
            dtype = '<f4'
        raw = values.astype(dtype).tobytes()
        arrays[name] = dict(offset=offset, count=len(values), dtype=dtype)
        chunks.append(raw); offset += len(raw); del result[name]
    raw = b''.join(chunks)
    filename = hashlib.sha256(raw).hexdigest() + '.bin'
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.parent / filename
    if not target.exists() or target.stat().st_size != len(raw):
        target.write_bytes(raw)
    result['geometry_buffers'] = dict(file=filename, arrays=arrays)
    write_json_atomic(path, result)


def read_geometry_buffers(path, description):
    import numpy as np
    filename = description['file']
    if not isinstance(filename, str) or not re.fullmatch(r'[0-9a-f]{64}\.bin', filename):
        raise ValueError('Invalid indexed geometry file')
    raw = (pathlib.Path(path).parent / filename).read_bytes()
    result, end = {}, 0
    for name, entry in description['arrays'].items():
        if name not in ('vertices', 'normals', 'uvs', 'indices', 'colors'):
            raise ValueError('Unknown indexed geometry array')
        dtype, offset, count = entry['dtype'], entry['offset'], entry['count']
        if dtype not in (('<u4',) if name == 'indices' else ('<f4', '<f8')) or type(offset) is not int or type(count) is not int:
            raise ValueError('Invalid indexed geometry layout')
        size = np.dtype(dtype).itemsize * count
        if offset != end or count < 0 or offset+size > len(raw):
            raise ValueError('Invalid indexed geometry range')
        result[name] = np.frombuffer(raw, dtype, count=count, offset=offset).tolist()
        end += size
    if end != len(raw):
        raise ValueError('Unexpected indexed geometry bytes')
    return result


def load_model_data(json_path, fallback_texture=None, material_overrides=None):
    path = pathlib.Path(json_path)
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if "vertices" not in data and "v" in data:
        data = dict(data, vertices=data["v"], uvs=data["uv"], indices=data["tri"])
        for key in ("v", "uv", "tri"):
            del data[key]
    if "geometry" in data:
        data = dict(data, **geometry(path, data))
    if 'geometry_buffers' in data:
        data.update(read_geometry_buffers(path, data.pop('geometry_buffers')))
    vertices, uvs, indices = (data.get(key, []) for key in ("vertices", "uvs", "indices"))
    if not vertices or len(vertices) % 3 or not indices or len(indices) % 3:
        raise ValueError("Model must contain complete vertices and triangles")
    if len(uvs) != len(vertices) // 3 * 2:
        raise ValueError("Model must have one UV pair per vertex")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in vertices + uvs):
        raise ValueError("Model coordinates must be finite numbers")
    if any(type(i) is not int or i < 0 or i >= len(vertices) // 3 for i in indices):
        raise ValueError("Triangle index outside vertex array")
    if not data.get("material_textures"):
        data["material_textures"] = [fallback_texture or default_texture(path.stem)]
    data['material_textures'], data['material_texture_games'], data['material_texture_transforms'] = material_texture_refs(data, material_overrides)
    if not data.get("groups"):
        data["groups"] = [{"start": 0, "count": len(indices), "materialIndex": 0}]
    expected_start = 0
    for group in data["groups"]:
        start, count, material = (group.get(key) for key in ("start", "count", "materialIndex"))
        if any(type(x) is not int for x in (start, count, material)):
            raise ValueError("Invalid material group")
        if start != expected_start or count <= 0 or count % 3 or not 0 <= material < len(data["material_textures"]):
            raise ValueError("Invalid material group range or material")
        expected_start += count
    if expected_start != len(indices):
        raise ValueError("Material groups must cover all triangles exactly once")
    if "normals" in data:
        if len(data["normals"]) != len(vertices) or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in data["normals"]):
            raise ValueError("Model must have one finite normal per vertex")
    if "colors" in data:  # Reflex: vertex colours (display values, 0 to 1) that shade the slot's texture.
        if len(data["colors"]) != len(vertices) or any(not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in data["colors"]):
            raise ValueError("Model must have one colour between 0 and 1 per vertex")
    return data


def write_surface_data(path, surfaces):
    """Map prop surfaces in the same binary container as browser models."""
    data=dict(vertices=[],normals=[],uvs=[],indices=[],groups=[])
    for index,surface in enumerate(surfaces):
        first=len(data['vertices'])//3
        data['groups'].append(dict(start=len(data['indices']),count=len(surface['indices']),materialIndex=index,
                                   vertexStart=first,vertexCount=len(surface['vertices'])//3))
        for key in ('vertices','normals','uvs'):data[key].extend(surface[key])
        data['indices'].extend(first+i for i in surface['indices'])
    write_model_data(path,data)
