"""Read both generations of Skinner JSON for preview and OBJ export."""

import json
import math
import pathlib
import re
if __package__:
    from .texture_workshop import TEXTURE_GAMES, normalize_transform
else:
    from texture_workshop import normalize_transform

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


MODEL_GAMES = ('t1', 't2', 'q3', 'diabotical', 'reflex', 'ta', 'tv', 'trpg', 'sw', 'rm', 't2rpg', 'ss', 'es1', 'es2', 'rb3d', 'unreal', 'ut', 'quake', 'quake2', 'daikatana')


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
