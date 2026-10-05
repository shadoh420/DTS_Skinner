"""Local Diabotical model import for the model browser: what the game's Editpad lists. Retail data stays outside
source and builds.

python tools/import_diabotical_models.py --game-base "C:/Program Files/Epic Games/Diabotical"

The Editpad lists every asset of the packs' .assets files that is not `listed false` (nor a weapon skin or avatar): props
(a model: the asset's `model`, else the asset's name is the model path), dynamic props (cells of pieces its
dynamic_rule blocks pick, see import_diabotical_map.placements), pickups (`pickup_size`), entities (`type entity`, a
`command` that adds one: a pickup, a model, or something the game draws by itself), utility boxes and sounds (`listed
true`: invisible boxes with models of their own, and sound spheres, whose model is only compiled: markers), decals, sprays (decals under textures/customization)
and surface materials. The models come out as the map import draws them (its placements, convert_models and
material rules), a dynamic prop at its `dynamic_default_size` and each piece its rules can pick listed under it; an
entity with no model is a marker (cone, disc or box). The materials of all of them, decals, sprays and surface
materials included, make the `diabotical` texture library, each as NAME.png with its default accent colours baked in
(tilemask.ps, see import_diabotical_map). The Editpad's thumbnail of an asset is ui/html/asset_thumbnails/NAME.png (or
.png.dds; NAME the asset's name, / as _).

Written (under local-data/diabotical): catalog.json, model_json/MODEL.json per entry (its material slots and a range
of corners per slot in a file of models/, read by tools/model_data.load_model_data), models/ (the map import's model
files, 8 float32 a corner: position, normal, uv, and models.json; c-HASH.bin for models put together from pieces),
textures/NAME.png (kept when there: edits survive a new import) and thumbnails/MODEL.png.
"""
import argparse
import functools
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image

try:
    from tools.import_diabotical_map import Pack, convert_models, material_textures, pickup_kinds, piece_shaders, placements, prop_key, read_assets, read_materials
    from tools.local_data import LOCAL_DATA
    from tools.model_data import model_sort_key
    from tools.reflex_textures import decode_dds
except ImportError:  # Run as a script from tools/.
    from import_diabotical_map import Pack, convert_models, material_textures, pickup_kinds, piece_shaders, placements, prop_key, read_assets, read_materials
    from local_data import LOCAL_DATA
    from model_data import model_sort_key
    from reflex_textures import decode_dds

S = '\\'
# Families by theme, from the asset's .assets folder and name (the first theme with a word in either).
THEMES = [('medina', 'medina mdn'), ('medieval', 'medieval med_ castle'), ('industrial', 'industr ind_ static_industrial dynamic_industrial'),
          ('temple', 'temple redstone'), ('snow & ice', 'snow ice'), ('japan', 'japan jpn'), ('offshore', 'offshore ofs'),
          ('steampunk', 'steampunk stmpnk'), ('sport', 'sport'), ('colliery', 'colliery ct_'), ('holidays', 'holiday halloween pumpkin christmas'),
          ('macguffin', 'macguffin'), ('desert', 'desert sand'), ('nature', 'nature grass foliage tree flower ivy vine palm rock'),
          ('production', 'production'), ('sound', 'sound ambience'), ('invisible', 'invisible')]
KINDS = {'prop': 'Props', 'dynprop': 'Dynprops', 'pickup': 'Pickups', 'entity': 'Entities', 'utility': 'Utility'}
# Markers for entities the game draws by itself, as the map page draws them: (pattern on the kind, colour, shape).
MARKERS = [(r'spawn|bot', 0x3fd06a, 'cone'), (r'flag', 0xff4040, 'cone'), (r'jumppad|jpdir|jp$', 0x30d0f0, 'disc'),
           (r'teleport|tpexit', 0x4060ff, 'disc'), (r'light', 0xffe040, 'box'), (r'trigger|base_team', 0xf0a020, 'box'),
           (r'sound|ambience', 0x60c0a0, 'drum')]


def safe(name):
    """A name as a file name, model name and slot label: lower case letters, digits, _ . and -."""
    return re.sub(r'[^a-z0-9_.-]+', '_', name.lower()).strip('._') or '_'


def category(name, fields, source):
    """The Editpad's list an asset is in (prop, dynprop, pickup, entity, utility, decal, spray, surface_material), or None."""
    if fields.get('listed') == 'false' or fields.get('type') in ('weapon_skin', 'avatar'):
        return None
    if fields.get('dynamic') == 'true':
        return 'dynprop'
    if fields.get('type') == 'decal':
        return 'spray' if source.startswith('textures' + S + 'customization') else 'decal'
    if fields.get('type') in ('prop', 'surface_material', 'entity'):
        return fields['type']
    if 'pickup_size' in fields:
        return 'pickup'
    return 'utility' if fields.get('listed') == 'true' else None  # Else a piece of a dynamic prop or another helper.


def theme(source, name):
    parts = source.lower().split(S)[:-1]
    text = ' '.join(parts[1:] if parts[:1] in (['models'], ['textures']) else parts) + ' ' + name.lower()
    return next((title for title, words in THEMES if any(word in text for word in words.split())), 'other')


def marker(kind, size):
    """A marker's triangles (n, 3, 8: position, normal, uv in page axes) and colour: a cone, disc or box (`size`, the
    command's /resize, in game axes; 20 by default) standing on y 0."""
    colour, shape = next(((c, s) for pattern, c, s in MARKERS if re.match(pattern, kind)), (0x8090a0, 'box'))
    sides, bottom, top, height = {'cone': (12, 12, 0, 40), 'disc': (20, 30, 30, 4), 'drum': (12, 15, 15, 30), 'box': (4, 2 ** -.5, 2 ** -.5, 1)}[shape]
    turn = np.pi / 4 if shape == 'box' else 0
    ring = lambda r, y: [(r * np.cos(turn + 2 * np.pi * i / sides), y, r * np.sin(turn + 2 * np.pi * i / sides)) for i in range(sides)]
    low, high = ring(bottom, 0), ring(top, height)
    triangles = [t for i in range(sides) for j in [(i + 1) % sides] for t in ((low[i], low[j], high[j]), (low[i], high[j], high[i]))]
    triangles += [((0, 0, 0), low[j], low[i]) for i in range(sides) for j in [(i + 1) % sides]]
    triangles += [((0, height, 0), high[i], high[j]) for i in range(sides) for j in [(i + 1) % sides]] if top else []
    corners = np.array(triangles, float)
    if shape == 'box':
        corners *= size if size else 20
    middle = corners.reshape(-1, 3).mean(0)
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    inward = np.einsum('ij,ij->i', normal, corners.mean(1) - middle) < 0
    corners[inward] = corners[inward][:, ::-1]  # Each triangle counter-clockwise seen from outside.
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    out = np.zeros((len(corners), 3, 8), np.float32)
    out[..., :3], out[..., 3:6] = corners, normal[:, None]
    return out, colour


def bake_accents(image, mask, accents):
    """The game's tilemask.ps on a texture: toward accent x the texel's mean by the mask's red, green and blue for the
    accents 1, 2, 3 (each hex as linear light, mixed in display values as the map page does)."""
    rgb = np.asarray(image.convert('RGB'), float) / 255
    weight = np.asarray(mask.convert('RGB').resize(image.size), float) / 255
    grey = rgb.mean(2, keepdims=True)
    for channel, value in enumerate(accents):
        if value is not None:
            accent = (np.array([value >> 16 & 255, value >> 8 & 255, value & 255]) / 255) ** (1 / 2.2)
            rgb += (accent * grey - rgb) * weight[..., channel:channel + 1]
    out = Image.fromarray(np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    if image.mode == 'RGBA':
        out.putalpha(image.getchannel('A'))
    return out


def import_catalog(game, output, replace=False):
    """Import the Editpad's models, materials and thumbnails of the Diabotical folder `game` into `output`. Returns
    counts of entries and previews, the models with no readable FBX and the materials with no texture."""
    game, output = Path(game).expanduser(), Path(output)
    if not (game / 'packs' / 'scripts.dbp').is_file():
        raise ValueError('No packs/scripts.dbp here. Enter the Diabotical folder.')
    packs = [Pack(path) for path in sorted((game / 'packs').glob('*.dbp')) if not path.name.startswith('audio')]
    assets, found = read_assets(packs), read_materials(packs)
    sources = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith('.assets'):
                for asset in re.findall(r'\basset\s+(\S+)\s*\{', re.sub(r'//[^\n]*', '', pack.read(name).decode('latin-1'))):
                    sources.setdefault(asset.lower(), name)
    kinds = {name: category(name, fields, sources.get(name, '')) for name, fields in assets.items()}
    fbx = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith('.fbx'):
                fbx.setdefault(name, pack)
    readable = lambda model: (file := 'models\\' + model.replace('/', '\\') + '.fbx') in fbx and (
        fbx[file].read(file)[:18] == b'Kaydara FBX Binary' or fbx[file].read(file).lstrip()[:5] == b'; FBX')
    pickups = pickup_kinds(assets, readable)
    # Utility boxes are `no_show` in maps; the browser shows them.
    view = {name: {k: v for k, v in fields.items() if k != 'entity_property'} if kinds[name] == 'utility' else fields for name, fields in assets.items()}

    # Each entry: name, display, family, its pieces [(key "model|material|flags", page matrix rows)] and the
    # thumbnail's asset name. A marker's pieces are ('marker:N', ...).
    entries, markers = [], []
    added = {}  # The entity that adds each kind: a pickup's thumbnail is that entity's.
    taken = set()

    def unique(name):  # Names that differ only in characters safe() replaces.
        out = next(f'{name}-{n}' if n else name for n in range(len(taken) + 1) if (f'{name}-{n}' if n else name) not in taken)
        taken.add(out)
        return out
    for name in sorted(kinds, key=model_sort_key):
        kind = kinds[name]
        if kind not in KINDS:
            continue
        fields, family = assets[name], KINDS[kind] + ('' if kind in ('pickup', 'entity', 'utility') else f' / {theme(sources.get(name, ""), name)}')
        entry = dict(name=unique(f'{kind}.{safe(name)}'), display=name, family=family, thumbnail=name)
        if kind in ('prop', 'dynprop', 'utility'):
            size = [float(v) for v in fields.get('dynamic_default_size', '1 1 1').split()[:3]] if kind == 'dynprop' else [1, 1, 1]
            props = placements([('prop', (0, 0, 0), (0, 0, 0), (size + [1, 1, 1])[:3], {'model': name})], view, found)[0]
            entry['pieces'] = [(key, row) for key, rows in props.items() for row in rows]
        elif kind == 'pickup':
            entry['pieces'] = [(f'pickup/{name}||', np.eye(4)[:3].ravel())] if name in pickups else []
        else:
            command = fields.get('command', '')
            add = (re.search(r'\badd\s+(\w+)', command) or [None, ''])[1].lower()
            added.setdefault(add, name)
            model = re.search(r'\bset\s+\S+\s+model\s+([^\s;]+)', command)
            if add in pickups:
                entry['pieces'] = [(f'pickup/{add}||', np.eye(4)[:3].ravel())]
            elif model:
                key, matrix = prop_key(model.group(1).lower(), np.eye(4), {}, {}, assets, found)
                entry['pieces'] = [(key, matrix[:3].ravel())]
            else:
                resize = re.search(r'resize\s+!last\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', command)
                markers.append(marker(add or safe(name), [float(v) for v in resize.groups()] if resize else None))
                entry['pieces'] = [(f'marker:{len(markers) - 1}', None)]
        entries.append(entry)
        if kind == 'dynprop':
            # Each piece its rules can pick, as it stands on its own (its flips kept), listed under the prop.
            choices = list(dict.fromkeys(choice for _, _, picked in fields['rules'] for choice in picked))
            for choice in choices:
                piece = prop_key(choice, np.eye(4), {}, fields, assets, found)
                if piece:
                    entries.append(dict(name=unique(f'{entry["name"]}.{safe(choice)}'), display=f'{name} \u203a {choice}', family=family,
                                        pieces=[(piece[0], piece[1][:3].ravel())]))
    for entry in entries:
        if entry['name'].startswith('pickup.') and entry['display'] in added:
            entry['thumbnail'] = added[entry['display']]

    (output / 'models').mkdir(parents=True, exist_ok=True)
    models_path = output / 'models' / 'models.json'
    previous = json.loads(models_path.read_text(encoding='utf-8')) if models_path.is_file() else {}
    paths = {key.split('|')[0] for entry in entries for key, _ in entry['pieces'] if not key.startswith('marker:')}
    models, unconverted = convert_models(packs, paths, found, output, replace, previous, piece_shaders(assets, found),
                                         {f'pickup/{kind}': value for kind, value in pickups.items()})
    models_path.write_text(json.dumps(models, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    # Sound spheres' model (sound/sphere) is only a compiled .dbm: a marker instead.
    for entry in entries:
        if entry['name'].startswith('utility.') and not any(key.split('|')[0] in models for key, _ in entry['pieces']):
            markers.append(marker(entry['display'], None))
            entry['pieces'] = [(f'marker:{len(markers) - 1}', None)]

    # The material a piece's group draws with, as the map page picks it: the prop's material X as the group's shader's
    # variant OWN_X where there is one, else X; else the group's own.
    def material(own, override):
        return (f'{own}_{override}' if f'{own}_{override}' in found else override) if override else own
    flags = lambda name: next((definition[3] for definition in found.get(name.split(':')[0], [])), {})
    used = {material(own, key.split('|')[1]) for entry in entries for key, _ in entry['pieces'] if key.split('|')[0] in models
            for own, _ in models[key.split('|')[0]]['groups']}
    used |= {assets[name].get('material', '').lower() for name, kind in kinds.items() if kind in ('decal', 'spray', 'surface_material')} - {''}
    library = output / 'textures'
    library.mkdir(exist_ok=True)
    files = {name: safe(name) + '.png' for name in used if not flags(name).get('hidden') and not flags(name).get('glass')}
    wanted = {name for name, file in files.items() if replace or not (library / file).is_file()}
    with tempfile.TemporaryDirectory(dir=output) as work:
        decoded, _ = material_textures(packs, wanted | {name + '#4' for name in wanted if name + '#4' in found}, found, Path(work))
        for name in wanted:
            if (decoded.get(name) or {}).get('texture'):
                with Image.open(Path(work) / 'textures' / decoded[name]['texture']) as image:
                    image.load()
                mask = (decoded.get(name + '#4') or {}).get('texture')
                if mask and decoded[name].get('accents'):
                    with Image.open(Path(work) / 'textures' / mask) as weights:
                        image = bake_accents(image, weights, decoded[name]['accents'])
                image.save(library / files[name])
    untextured = {name for name in used if name in files and not (library / files[name]).is_file()}
    for number, (_, colour) in enumerate(markers):
        target = library / f'marker_{colour:06x}.png'
        if not target.is_file():
            Image.new('RGB', (4, 4), f'#{colour:06x}').save(target)

    thumbs = game / 'ui' / 'html' / 'asset_thumbnails'
    (output / 'thumbnails').mkdir(exist_ok=True)
    (output / 'model_json').mkdir(exist_ok=True)
    catalog = []

    @functools.lru_cache(maxsize=256)
    def corners(file):
        return np.fromfile(output / 'models' / file, '<f4').reshape(-1, 8)

    for entry in entries:
        item = dict(model_name=entry['name'], display_name=entry['display'], game='diabotical', category=entry['family'], status='ready', warnings=[])
        slots, groups, parts, at, direct = [], [], [], 0, None
        for key, row in entry['pieces']:
            if key.startswith('marker:'):
                triangles, colour = markers[int(key[7:])]
                slots.append((f'marker_{colour:06x}', f'marker_{colour:06x}.png', {}))
                parts.append(triangles.reshape(-1, 8))
                groups.append(dict(start=at, count=len(parts[-1]), materialIndex=len(slots) - 1))
                at += len(parts[-1])
                continue
            model, override, mark = key.split('|')
            if model not in models:
                item['warnings'].append(f'No readable model file for {model}.')
                continue
            matrix = np.vstack([np.asarray(row, float).reshape(3, 4), [0, 0, 0, 1]])
            # A single model as it stands is drawn from its own file; others are put together into one.
            if len(entry['pieces']) == 1 and np.allclose(matrix, np.eye(4)):
                direct = models[model]['file']
            start = 0
            for own, count in models[model]['groups']:
                name = material(own, override)
                start += count
                if flags(name).get('hidden'):
                    continue
                if name not in [slot[0] for slot in slots]:
                    setting = {'alphaFunc': 'GE128', 'cull': 'none'} if flags(name).get('cutout') else {
                        'blend': ['gl_src_alpha', 'gl_one_minus_src_alpha'], 'depthWrite': False} if flags(name).get('blend') else {}
                    slots.append((name, files[name] if name in files and name not in untextured else f'[Slot {len(slots)}: {safe(name)}]', setting))
                index = [slot[0] for slot in slots].index(name)
                if direct:
                    groups.append(dict(start=start - count, count=count, materialIndex=index))
                    continue
                piece = corners(models[model]['file'])[start - count:start].astype(float)
                piece[:, :3] = piece[:, :3] @ matrix[:3, :3].T + matrix[:3, 3]
                normals = piece[:, 3:6] @ np.linalg.inv(matrix[:3, :3])
                piece[:, 3:6] = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
                if mark.startswith('m'):  # Mirrored: each triangle turned the other way round keeps facing out.
                    piece = piece.reshape(-1, 3, 8)[:, ::-1].reshape(-1, 8)
                parts.append(piece.astype(np.float32))
                groups.append(dict(start=at, count=count, materialIndex=index))
                at += count
        if not groups:
            item.update(status='unsupported', warnings=item['warnings'] or ['Nothing of this asset is drawn.'])
            catalog.append(item)
            continue
        if not direct:
            raw = np.concatenate(parts).tobytes()
            direct = f'c-{hashlib.sha256(raw).hexdigest()[:16]}.bin'
            if not (output / 'models' / direct).is_file():
                (output / 'models' / direct).write_bytes(raw)
        data = dict(game='diabotical', winding='ccw', geometry=direct, groups=groups, material_names=[s[0] for s in slots],
                    material_textures=[s[1] for s in slots], material_settings=[s[2] for s in slots],
                    metadata=dict(asset=entry['display'], pieces=len(entry['pieces'])))
        (output / 'model_json' / f'{entry["name"]}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        thumb = entry.get('thumbnail', '').replace('/', '_')
        target = output / 'thumbnails' / f'{entry["name"]}.png'
        for source in (thumbs / f'{thumb}.png', thumbs / f'{thumb}.png.dds') if thumb else ():
            if target.is_file() or not source.is_file():
                continue
            try:
                image = decode_dds(source.read_bytes()) if source.suffix == '.dds' else Image.open(source)
                image.save(target)
            except (OSError, ValueError):
                continue
        item['thumbnail'] = target.is_file()
        catalog.append(item)
    names = {item['model_name'] for item in catalog}
    for path in (output / 'model_json').glob('*.json'):
        if path.stem not in names:
            path.unlink()
    temporary = output / 'catalog.tmp'
    temporary.write_text(json.dumps(catalog, separators=(',', ':')), encoding='utf-8')
    temporary.replace(output / 'catalog.json')
    return dict(entries=len(catalog), ready=sum(item['status'] == 'ready' for item in catalog), unconverted=unconverted, untextured=sorted(untextured))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'diabotical')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_catalog(args.game_base, args.output, args.replace)
    print(f"{done['entries']} entries, {done['ready']} previews; {len(done['unconverted'])} models unread, {len(done['untextured'])} materials untextured")
