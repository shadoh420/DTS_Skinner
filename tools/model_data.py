"""Read both generations of Skinner JSON for preview and OBJ export."""

import json
import math
import pathlib

TEXTURE_MAPPINGS = {
    "ammo1": "ammo.png",
    "grenadel": "grenade.png",
    "sensor_small": "sensor_rmt.png",
    "mine": "r_mine1.png",
}


def default_texture(model_name):
    return TEXTURE_MAPPINGS.get(model_name.lower(), model_name + ".png")


def load_model_data(json_path, fallback_texture=None):
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
    for name in data["material_textures"]:
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid texture name")
        if not name.startswith("[Slot") and (name in (".", "..") or any(c in name for c in '/\\:\r\n')):
            raise ValueError("Texture names must be local filenames")
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
    return data
