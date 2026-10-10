"""Quake (1996) and classic mission-pack models, first pose and first skin, as game quake.

python tools/import_quake.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake"

PAK1 overrides PAK0. Writes catalog.json, model_json/*.json, textures/*.png and import-report.json;
existing PNG edits survive. BSP item boxes share the map decoder; sprites are listed but not decoded. No animation.
"""
import argparse
import json
import math
from pathlib import Path
import struct

from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from local_data import LOCAL_DATA

MONSTERS = {'boss', 'demon', 'dog', 'enforcer', 'fish', 'hknight', 'knight', 'ogre', 'oldone', 'shalrath',
            'shambler', 'soldier', 'tarbaby', 'wizard', 'zombie', 'armabody', 'armalegs', 'grem', 'scor',
            'spikmine', 'dragon', 'eel2', 'lavaman', 'morph_az', 'morph_eg', 'morph_gr', 'mummy', 's_wrath', 'wrath'}
EFFECTS = {'bolt', 'bolt2', 'bolt3', 'flame', 'flame2', 'grenade', 'k_spike', 'laser', 'lavaball', 'missile',
           's_light', 's_spike', 'spike', 'teleport', 'v_spike', 'w_spike', 'zom_gib', 'lasrspik', 'lavarock',
           'proxbomb', 'beam', 'fireball', 'lspike', 'plasma', 'sphere', 'w_ball', 'eelhead', 'hook'}
GAMES = {'quake': 'Quake (1996)', 'hipnotic': 'Scourge of Armagon', 'rogue': 'Dissolution of Eternity'}


def install_root(install):
    path = Path(install).expanduser()
    return path.parent if path.name.lower() in ('id1', 'hipnotic', 'rogue') else path


def available_games(install):
    root = install_root(install)
    return ['quake'] + [game for game in ('hipnotic', 'rogue') if (root / game).is_dir()]


def read_pak(data):
    """Directory entries as (lowercase name, bytes), retaining duplicates for accounting."""
    if len(data) < 12 or data[:4] != b'PACK':
        raise ValueError('Not a Quake PACK file')
    offset, size = struct.unpack_from('<ii', data, 4)
    if offset < 12 or size < 0 or size % 64 or offset + size > len(data):
        raise ValueError('Invalid PACK directory range')
    entries = []
    for at in range(offset, offset + size, 64):
        name, start, length = struct.unpack_from('<56sii', data, at)
        try:
            name = name.split(b'\0', 1)[0].decode('ascii').lower()
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid PACK entry name') from exc
        if not name or start < 12 or length < 0 or start + length > len(data):
            raise ValueError(f'Invalid PACK entry range: {name}')
        entries.append((name, data[start:start + length]))
    return entries


def read_install(install, game='quake'):
    """Effective layered files; records/counts cover only this campaign's own PAK directories."""
    if not isinstance(game, str) or game not in GAMES:
        raise ValueError('Expected quake, hipnotic or rogue')
    root = install_root(install)
    folder = root / ('id1' if game == 'quake' else game)
    if game == 'quake' and not folder.is_dir():
        folder = Path(install).expanduser()
    paths = {p.name.lower(): p for p in folder.iterdir() if p.is_file()} if folder.is_dir() else {}
    if 'pak0.pak' not in paths:
        raise ValueError(f'No classic Quake {folder.name}/PAK0.PAK in {install}')
    files = {} if game == 'quake' else read_install(install)[0]
    records, counts = [], {}
    for pak in ('pak0.pak', 'pak1.pak'):
        if pak not in paths:
            continue
        entries = read_pak(paths[pak].read_bytes())
        counts[pak] = dict(directory=len(entries), mdl=sum(n.startswith('progs/') and n.endswith('.mdl') for n, _ in entries),
                           spr=sum(n.startswith('progs/') and n.endswith('.spr') for n, _ in entries),
                           bsp=sum(n.endswith('.bsp') for n, _ in entries))
        for name, data in entries:
            if name in files and files[name][2]['game'] == game:
                files[name][2].update(status='overridden', reason=f'Overridden by {pak}:{name}')
            record = dict(game=game, pak=pak, source=name, status='pending')
            files[name] = data, pak, record
            records.append(record)
    palette = files.get('gfx/palette.lmp', (b'',))[0]
    if len(palette) != 768:
        raise ValueError('Missing or invalid gfx/palette.lmp (expected 256 RGB colours)')
    return files, records, counts


def palette_image(pixels, size, palette):
    image = Image.frombytes('P', size, pixels)
    image.putpalette(palette)
    return image.convert('RGB')  # Index 255 is opaque in classic MDLs and BSPs too.


def item_box(name):
    return name.startswith('maps/b_') and name.endswith('.bsp')


def read_mdl(data):
    """Decode IDPO v6. Keep only the first skin and pose; walk groups to validate their lengths."""
    at = 0

    def take(size):
        nonlocal at
        if size < 0 or at + size > len(data):
            raise ValueError('Truncated MDL')
        value, at = data[at:at + size], at + size
        return value

    def unpack(fmt):
        return struct.unpack('<' + fmt, take(struct.calcsize('<' + fmt)))

    head = unpack('4si3f3ff3f8if')
    if head[:2] != (b'IDPO', 6):
        raise ValueError('Expected IDPO version 6')
    scale, origin = head[2:5], head[5:8]
    skins, width, height, verts, tris, frames, sync, flags = head[12:20]
    if min(skins, width, height, verts, tris, frames) <= 0:
        raise ValueError('Invalid MDL dimensions or counts')
    if not all(math.isfinite(v) for v in head[2:12] + head[20:]):
        raise ValueError('Non-finite MDL header')
    if min(scale) <= 0:
        raise ValueError('Invalid MDL vertex scale')

    def group_count(kind, frame=False):
        if kind not in (0, 1):
            raise ValueError('Invalid MDL group type')
        if not kind:
            return 1
        count = unpack('i')[0]
        if count <= 0 or count > len(data) // 4:
            raise ValueError('Invalid MDL group count')
        if frame:
            take(8)  # Group bounding box.
        intervals = unpack(f'{count}f')
        if any(not math.isfinite(t) or t <= 0 for t in intervals):
            raise ValueError('Invalid MDL group intervals')
        return count

    skin = None
    for _ in range(skins):
        count = group_count(unpack('i')[0])
        pixels = take(width * height * count)
        if skin is None:
            skin = pixels[:width * height]
    st = list(struct.iter_unpack('<3i', take(verts * 12)))
    triangles = list(struct.iter_unpack('<4i', take(tris * 16)))
    if any(front not in (0, 1) or min(a, b, c) < 0 or max(a, b, c) >= verts
           for front, a, b, c in triangles):
        raise ValueError('Invalid MDL triangle')
    points, pose, pose_count = None, '', 0
    for _ in range(frames):
        count = group_count(unpack('i')[0], frame=True)
        pose_count += count
        for _ in range(count):
            take(8)  # Frame bounding box.
            name = take(16).split(b'\0', 1)[0].decode('ascii', errors='replace')
            packed = take(verts * 4)
            if points is None:
                points = [tuple(p[k] * scale[k] + origin[k] for k in range(3))
                          for p in struct.iter_unpack('<4B', packed)]
                pose = name
    return dict(points=points, st=st, triangles=triangles, skin=skin, width=width, height=height,
                frames=frames, poses=pose_count, skins=skins, pose=pose, flags=flags)


def build_model(mesh, texture):
    vertices, uvs, indices, corners = [], [], [], {}
    for front, a, b, c in mesh['triangles']:
        for point in (a, c, b):  # Quake's MDL fronts wind clockwise; the viewer uses anticlockwise.
            seam, s, t = mesh['st'][point]
            back = not front and bool(seam)
            key = point, back
            if key not in corners:
                corners[key] = len(vertices) // 3
                x, y, z = mesh['points'][point]
                # Quake: +x forward, +y left, +z up. A rotation (det +1), not a reflection.
                vertices.extend((y, z, x))
                uvs.extend(((s + (mesh['width'] // 2 if back else 0) + .5) / mesh['width'],
                            (t + .5) / mesh['height']))  # Pixel centres; v=0 is the PNG's top.
            indices.append(corners[key])
    return dict(game='quake', winding='ccw', vertices=vertices, uvs=uvs, indices=indices,
                groups=[dict(start=0, count=len(indices), materialIndex=0)], material_names=[texture],
                material_textures=[texture], material_settings=[{}],
                metadata=dict(pose=mesh['pose'], frames=mesh['frames'], poses=mesh['poses'], skins=mesh['skins'],
                              flags=mesh['flags'], fullbright_pixels=sum(p >= 224 for p in mesh['skin'])))


def category(name):
    if name in MONSTERS:
        return 'Monsters'
    if name in ('player', 'eyes', 'playham'):
        return 'Player'
    if name.startswith(('h_', 'gib', 'rubble', 's_wrtgb')) or 'gib' in name or name in EFFECTS:
        return 'Gibs/Effects'
    if name.startswith(('v_', 'g_')):
        return 'Weapons'
    return 'Items'


def import_catalog(install, output):
    """Import id1 and any adjacent classic packs into one catalog; preserve edited PNGs."""
    output = Path(output)
    base = read_install(install)[0]
    all_records, pak_counts, catalog, taken, base_models = [], {}, [], set(), {}
    for sub in ('model_json', 'textures'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    for game in available_games(install):
        files, records, counts = read_install(install, game)
        records = [r for r in records if (r['source'].startswith('progs/') and r['source'].endswith(('.mdl', '.spr', '.bsp')))
                   or item_box(r['source'])]
        all_records.extend(records)
        pak_counts.update({(pak if game == 'quake' else f'{game}/{pak}'): count for pak, count in counts.items()})
        palette = files['gfx/palette.lmp'][0]
        for record in records:
            if record['status'] == 'overridden':
                continue
            name = record['source']
            stem, suffix = name.split('/', 1)[1].rsplit('.', 1)
            model = safe(stem if suffix == 'mdl' else f'{stem}_{suffix}')
            if game != 'quake' and name in base and files[name][0] == base[name][0] and name in base_models:
                record.update(status='duplicate', model=base_models[name], reason='Byte-identical to id1; listed once in its category')
                continue
            renamed = game != 'quake' and name in base
            if game != 'quake':
                model = f'{game}_{model}'
            record.update(model=model, renamed=renamed)
            label = '' if game == 'quake' else GAMES[game] + ': '
            item = dict(model_name=model, display_name=label + name.split('/', 1)[1], texture_name='', game='quake',
                        campaign=game, category=label + category(stem), status='ready', source=f"{game}/{record['pak']}:{name}")
            try:
                if suffix == 'spr':
                    raise ValueError('Sprite (.spr), not an MDL; sprite decoding is not included')
                if suffix == 'bsp' and not item_box(name):
                    raise ValueError('Non-item BSP brush model; use the maps import')
                if model in taken:
                    raise ValueError('Output model name collision')
                taken.add(model)
                if suffix == 'bsp':
                    if __package__:
                        from .import_quake_map import build_item
                    else:
                        from import_quake_map import build_item
                    data, images = build_item(files[name][0], palette, model)
                    for png, image in images.items():
                        if not (output / 'textures' / png).is_file():
                            image.save(output / 'textures' / png)
                else:
                    mesh = read_mdl(files[name][0])
                    png = model + '.png'
                    data = build_model(mesh, png)
                    target = output / 'textures' / png
                    if not target.is_file():
                        palette_image(mesh['skin'], (mesh['width'], mesh['height']), palette).save(target)
                data['metadata']['source'] = item['source']
                (output / 'model_json' / f'{model}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
                item['texture_name'] = data['material_textures'][0]
                record['status'] = 'ready'
                if game == 'quake':
                    base_models[name] = model
            except ValueError as exc:
                record.update(status='skipped', reason=str(exc))
                item.update(status='skipped', reason=str(exc))
            catalog.append(item)
    records, counts = all_records, pak_counts
    report = dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog), pak_counts=counts,
                  mdl_entries=sum(c['mdl'] for c in counts.values()),
                  bsp_items=sum(item_box(r['source']) for r in records),
                  mdl_skipped=sum(r['status'] == 'skipped' and r['source'].endswith('.mdl') for r in records),
                  overridden=sum(r['status'] == 'overridden' for r in records),
                  duplicates=sum(r['status'] == 'duplicate' for r in records),
                  renamed=sum(r.get('renamed', False) and r['status'] == 'ready' for r in records), results=records)
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'quake'), indent=1))
