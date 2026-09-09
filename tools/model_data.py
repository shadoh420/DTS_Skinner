"""Read both generations of Skinner JSON for preview and OBJ export."""

import json
import math
import pathlib
import re

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


def material_texture_refs(data, material_overrides=None):
    """Validate slot references while keeping filenames compatible with old clients."""
    game = data.get('game', 't1')
    if game not in ('t1', 't2', 'q3'):
        raise ValueError('Unknown model game')
    names = data['material_textures']
    if not isinstance(names, list) or not names:
        raise ValueError('Material textures must be a nonempty list')
    names = list(names)
    games = data.get('material_texture_games', [game] * len(names))
    if not isinstance(games, list) or len(games) != len(names):
        raise ValueError('Texture games must match material slots')
    games = list(games)
    if material_overrides is not None and not isinstance(material_overrides, dict):
        raise ValueError('Material overrides must be a slot-to-texture object')
    for slot, reference in (material_overrides or {}).items():
        if not isinstance(slot, str) or not slot.isascii() or not slot.isdecimal() or not 0 <= int(slot) < len(names):
            raise ValueError('Material slot outside model material array')
        if isinstance(reference, dict):
            if set(reference) != {'game', 'filename'}:
                raise ValueError('Texture reference must contain game and filename')
            source_game, filename = reference['game'], reference['filename']
        else:
            source_game, filename = game, reference
        names[int(slot)], games[int(slot)] = filename, source_game
    for name, source_game in zip(names, games):
        if source_game not in ('t1', 't2', 'q3'):
            raise ValueError('Unknown texture game')
        # Imported untextured slots are labels, never filesystem lookups.
        placeholder = isinstance(name, str) and re.fullmatch(r'\[Slot [0-9]+: [A-Za-z0-9 _.-]+\]', name)
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or name.endswith((' ', '.')) or any(c in name for c in '/\\<>"|?*')
                or (':' in name and not placeholder)
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError('Texture names must be local filenames')
    return names, games


def material_texture_paths(data, textures_dir, texture_dirs=None):
    names, games = material_texture_refs(data)
    directories = texture_dirs if texture_dirs is not None else {data.get('game', 't1'): textures_dir}
    if any(game not in directories for game in games):
        raise ValueError('Texture source game directory is unavailable')
    return [pathlib.Path(directories[game]) / name for name, game in zip(names, games)]


def load_model_data(json_path, fallback_texture=None, material_overrides=None):
    path = pathlib.Path(json_path)
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if "vertices" not in data and "v" in data:
        data = dict(data, vertices=data["v"], uvs=data["uv"], indices=data["tri"])
        for key in ("v", "uv", "tri"):
            del data[key]
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
    data['material_textures'], data['material_texture_games'] = material_texture_refs(data, material_overrides)
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
    return data
