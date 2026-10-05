"""Local Reflex Arena model import for the model browser: the editor's props and the game's pickups. Retail data stays
outside source and builds.

python tools/import_reflex_models.py --game-base "C:/Program Files (x86)/Steam/steamapps/common/reflexfps"

The editor's prop list is every .effect outside internal/ (common/meshes/crates/crate_32, gothic/pillars/...); the
pickups are the effects pickups place (reflex_models.PICKUP_EFFECTS) and the pads they stand on. Each effect's meshes
are read as the Reflex Maps page reads them (reflex_models.read_effect and read_mesh: the finest level of detail,
skinned meshes in their pose, at the effect's scale); effects with no mesh (lights, particles only) are left out.

A slot's material and colour are the page's: the effect's material for the slot, else the mesh's; the effect's
colour for the slot, else the material's. Most Reflex materials are only a colour (deferredPbrStylized), shaded by
the mesh's vertex colours, so a slot's texture is its material's texture (decoded as the map import does, into the
same `reflex` library: local-data/reflex-maps/textures) times its colour (NAME__RRGGBB.png when the colour is not
white), or a flat swatch colour_RRGGBB.png; and the vertex colours go with the model (`colors`, display values) where
the material's shader takes them. Holograms, pickup glows and the game's forward (standard_) shaders add to what is
behind them, as on the page. Kept PNGs are not written again: edits survive a new import.

Axes: the game's are left-handed, y up; z is turned round (positions and normals) and each triangle with it, so
the browser's right-handed y-up view shows the model as the game does.

Written (under local-data/reflex-models): catalog.json, model_json/MODEL.json and thumbnails/MODEL.png (the editor's
thumbnail of the effect, thumbs_mesh/EFFECT.textureset).
"""
import argparse
import json
from pathlib import Path
import re
import struct
import zipfile

import numpy as np
from PIL import Image

try:
    from tools.import_reflex_map import game_files, material_colours, material_textures
    from tools.import_diabotical_models import safe
    from tools.local_data import LOCAL_DATA
    from tools.model_data import model_sort_key
    from tools.reflex_models import PICKUP_EFFECTS, SHADOW_PART, pickup_pad, read_effect, read_mesh
    from tools.reflex_textures import decode_textureset_image, textureset_images
except ImportError:  # Run as a script from tools/.
    from import_reflex_map import game_files, material_colours, material_textures
    from import_diabotical_models import safe
    from local_data import LOCAL_DATA
    from model_data import model_sort_key
    from reflex_models import PICKUP_EFFECTS, SHADOW_PART, pickup_pad, read_effect, read_mesh
    from reflex_textures import decode_textureset_image, textureset_images

ADD = {'blend': ['gl_one', 'gl_one'], 'depthWrite': False, 'cull': 'none'}


def hex_colour(colour):
    return ''.join(f'{round(min(max(c, 0), 1) * 255):02x}' for c in colour[:3])


def look(read):
    """What the page does with a material (viewer.js): (settings, whether the mesh's vertex colours shade it)."""
    shader = read.get('shader', '')
    kind = re.search(r'hologram|glowPickup|alphaFresnel', shader)
    forward = shader.startswith('internal/shaders/standard_')
    fluid = shader.startswith('internal/shaders/fluid')
    glow = 'standard_ALBEDOCOLOUR_ALBEDOINTENSITY' in shader
    added = fluid and (read['flags'] >> 5 & 7) == 2 if 'flags' in read else False
    shines = (not glow and bool(kind or forward or re.search(r'alphaFresnel|GLASS|raceStartFinish|glowPickup|powerup', shader))) or added
    vertex = not kind and not fluid and not re.search(r'_SSS|_TINTED', shader) and not (forward and 'VERTEXCOLOUR' not in shader)
    if shines:
        return dict(ADD), vertex
    if read.get('seeThrough') and read.get('textures'):  # Leaf cards: a diffuse texture's alpha cuts them out.
        return {'alphaFunc': 'GE128', 'cull': 'none'}, vertex
    return ({'cull': 'none'} if 'flags' in read and (read['flags'] >> 3 & 3) == 1 else {}), vertex


def import_catalog(game, output, library, replace=False):
    """Import the props and pickups of the Reflex Arena folder `game` into `output`, their textures into the `reflex`
    texture library folder `library` (local-data/reflex-maps, as the map import writes it). Returns counts of
    entries, previews, swatches and the materials the game has no file for."""
    game, output, library = Path(game).expanduser(), Path(output), Path(library)
    if not game.is_dir() or not any(folder.name.lower() == 'base' for folder in game.iterdir() if folder.is_dir()):
        raise ValueError('No base folder here. Enter the Reflex Arena folder (reflexfps).')
    effects, meshes, files = game_files(game, '.effect'), game_files(game, '.mesh'), game_files(game, '.material')
    thumbs = {key[len('thumbs_mesh/'):]: reader for key, (reader, _) in game_files(game, '.textureset').items() if key.startswith('thumbs_mesh/')}
    bare = {}
    for name in files:
        bare.setdefault(name.rsplit('/', 1)[-1], name)
    resolve = lambda name: name if '/' in name or name.lower() in files else bare.get(name.lower(), name)
    pickups = {*PICKUP_EFFECTS.values(), *map(pickup_pad, PICKUP_EFFECTS.values())}
    names = sorted({name for name in effects if not name.startswith('internal/')} | (pickups & set(effects)), key=model_sort_key)

    entries, read_meshes = [], {}
    for name in names:
        try:
            effect = read_effect(effects[name][0]())
        except (ValueError, struct.error, OSError, zipfile.BadZipFile):
            continue
        if not effect['meshes']:
            continue  # Lights and particles only: nothing to draw.
        parts, warnings = [], []
        for record in effect['meshes']:
            key = record['mesh'].lower()
            if key not in read_meshes:
                try:
                    read_meshes[key] = read_mesh(meshes[key][0]()) if key in meshes else None
                except (ValueError, struct.error, OSError, zipfile.BadZipFile):
                    read_meshes[key] = None
            mesh = read_meshes[key]
            if mesh is None:
                warnings.append(f'No readable mesh {record["mesh"]}.')
                continue
            for part in mesh['parts']:
                slot = part['material']
                if slot == SHADOW_PART:
                    continue
                material = resolve((record['materials'][slot] if slot < 8 else '') or mesh['materials'][slot])
                colour = record['colours'][slot][:3] if slot < 8 and record['colours'][slot] else None
                parts.append((part, record['scale'], material, colour))
        family = 'Weapons' if name.startswith('internal/weapons/') else 'Items' if name.startswith('internal/') else ' / '.join(
            word.replace('_', ' ').capitalize() if i == 0 else word.replace('_', ' ') for i, word in enumerate(name.split('/')[:2]))
        entries.append(dict(name=name, model=safe(name.replace('/', '.')), family=family, parts=parts, warnings=warnings))

    used = {material for entry in entries for _, _, material, _ in entry['parts']}
    colours, _ = material_colours(game, used, files)
    looks = {material: look(read) for material, read in colours.items()}
    material_textures(game, colours, library, replace)
    textures = library / 'textures'
    swatches = set()

    def slot_texture(material, colour):
        """The slot's PNG in the library: its texture times its colour, or a swatch of its colour (white for a material
        the game has no file for, MaterialA and the like, as the page draws it)."""
        read = colours.get(material, {})
        colour = colour or read.get('colour') or [1, 1, 1]
        shade = hex_colour(colour)
        if read.get('texture'):
            if shade == 'ffffff':
                return read['texture']
            file = f'{read["texture"][:-4]}__{shade}.png'
            if replace or not (textures / file).is_file():
                with Image.open(textures / read['texture']) as image:
                    mode, rgba = image.mode, np.asarray(image.convert('RGBA'), float)
                rgba[..., :3] *= np.array([int(shade[i:i + 2], 16) for i in (0, 2, 4)]) / 255
                Image.fromarray(np.round(rgba).astype(np.uint8), 'RGBA').convert('RGBA' if mode == 'RGBA' else 'RGB').save(textures / file)
            return file
        file = f'colour_{shade}.png'
        swatches.add(file)
        if not (textures / file).is_file():
            Image.new('RGB', (4, 4), '#' + shade).save(textures / file)
        return file

    for folder in ('model_json', 'thumbnails'):
        (output / folder).mkdir(parents=True, exist_ok=True)
    catalog, missing = [], set()
    for entry in entries:
        item = dict(model_name=entry['model'], display_name=entry['name'], game='reflex', category=entry['family'], status='ready', warnings=entry['warnings'])
        slots, by_slot = [], {}
        vertices, normals, uvs, tints = [], [], [], []
        for part, scale, material, colour in entry['parts']:
            read = colours.get(material, {})
            if re.match(r'internal/editor/textures/editor_.*clip', material) or re.search(r'godrays|powerup_resist', read.get('shader', '')):
                continue  # Not drawn by the game (clip) or left out by the page.
            file = slot_texture(material, colour)
            if material not in colours:
                missing.add(material)
            key = (material, file)
            if key not in by_slot:
                label = material + (f' #{hex_colour(colour)}' if colour else '')
                settings, _ = looks.get(material, ({}, True))
                by_slot[key] = len(slots)
                slots.append(dict(name=label, texture=file, settings=settings, triangles=[]))
            at = sum(len(v) for v in vertices)
            positions = np.reshape(part['positions'], (-1, 3)) * scale * (1, 1, -1)
            vertices.append(positions)
            normals.append(np.reshape(part['normals'], (-1, 3)) * (1, 1, -1))
            uvs.append(np.reshape(part['uvs'], (-1, 2)))
            bgra = np.frombuffer(part['colours'], np.uint8).reshape(-1, 4)
            tints.append(bgra[:, 2::-1] / 255 if looks.get(material, ({}, True))[1] else np.ones((len(positions), 3)))
            # z turned round: each triangle the other way round keeps facing out.
            slots[by_slot[key]]['triangles'].append(np.reshape(part['indices'], (-1, 3))[:, ::-1] + at)
        if not slots:
            item.update(status='unsupported', warnings=item['warnings'] or ['Nothing of this effect is drawn.'])
            catalog.append(item)
            continue
        indices, groups = [], []
        for number, slot in enumerate(slots):
            triangles = np.concatenate(slot['triangles']).ravel()
            groups.append(dict(start=sum(len(i) for i in indices), count=len(triangles), materialIndex=number))
            indices.append(triangles)
        tint = np.concatenate(tints)
        data = dict(game='reflex', winding='ccw', vertices=np.round(np.concatenate(vertices), 4).ravel().tolist(),
                    normals=np.round(np.concatenate(normals), 4).ravel().tolist(), uvs=np.round(np.concatenate(uvs), 5).ravel().tolist(),
                    indices=np.concatenate(indices).astype(int).tolist(), groups=groups,
                    material_names=[slot['name'] for slot in slots], material_textures=[slot['texture'] for slot in slots],
                    material_settings=[slot['settings'] for slot in slots], metadata=dict(effect=entry['name']))
        if (tint < 1).any():
            data['colors'] = np.round(tint, 3).ravel().tolist()
        (output / 'model_json' / f'{entry["model"]}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        target = output / 'thumbnails' / f'{entry["model"]}.png'
        if entry['name'] in thumbs and (replace or not target.is_file()):
            try:
                raw = thumbs[entry['name']]()
                decode_textureset_image(raw, next(iter(textureset_images(raw).values()))).convert('RGB').save(target)
            except (OSError, ValueError, StopIteration, struct.error, zipfile.BadZipFile):
                pass
        item['thumbnail'] = target.is_file()
        catalog.append(item)
    kept = {item['model_name'] for item in catalog}
    for path in (output / 'model_json').glob('*.json'):
        if path.stem not in kept:
            path.unlink()
    temporary = output / 'catalog.tmp'
    temporary.write_text(json.dumps(catalog, separators=(',', ':')), encoding='utf-8')
    temporary.replace(output / 'catalog.json')
    return dict(entries=len(catalog), ready=sum(item['status'] == 'ready' for item in catalog), swatches=len(swatches),
                thumbnails=sum(bool(item.get('thumbnail')) for item in catalog), missing=sorted(missing))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'reflex-models')
    parser.add_argument('--library', type=Path, default=LOCAL_DATA / 'reflex-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_catalog(args.game_base, args.output, args.library, args.replace)
    print(f"{done['entries']} entries, {done['ready']} previews, {done['thumbnails']} thumbnails, {done['swatches']} colour swatches; "
          f"no material file for {len(done['missing'])}")
