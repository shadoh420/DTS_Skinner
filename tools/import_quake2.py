"""Quake II and installed xatrix/rogue/ctf MD2 models, as game quake2.

python tools/import_quake2.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake 2/baseq2"

Numbered PAKs layer in numeric order, then loose model/skin files override them.
Writes catalog.json, model_json, textures and import-report.json; existing PNG edits survive.
Player body defaults: male/grunt, female/athena, cyborg/oni911. Other PCXs remain in the library.
Missing skins try the named basename beside the model, then skin.pcx (body models may use
another non-icon body PCX). Base player weapons may resolve missing named skins from the
installed packs; crakhor's absent shotgun skin uses the stock male weapon atlas. Other
missing skins use a checker. Packs independently overlay baseq2. No animation or sprites.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import posixpath
import re
import struct

from PIL import Image

try:
    from tools.import_quake import read_pak
    from tools.import_diabotical_models import safe
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake import read_pak
    from import_diabotical_models import safe
    from local_data import LOCAL_DATA

PLAYER_SKINS = {'male': 'grunt', 'female': 'athena', 'cyborg': 'oni911'}
GAMES = {'quake2': 'Quake II', 'xatrix': 'The Reckoning', 'rogue': 'Ground Zero', 'ctf': 'CTF'}


def base_folder(install):
    root = Path(install).expanduser()
    return root / 'baseq2' if (root / 'baseq2').is_dir() else root


def available_games(install):
    root = base_folder(install)
    return ['quake2'] + [g for g in GAMES if g != 'quake2' and (root.parent / g / 'pak0.pak').is_file()]


def asset_name(name):
    """Normalize archive references only; never use them as source filesystem paths."""
    return posixpath.normpath(name.replace('\\', '/')).lower()


def read_install(install, maps=False, game='quake2', base=None):
    """Return layered assets, but inventory only this campaign's own directory entries."""
    if game not in GAMES:
        raise ValueError(f'Unknown Quake II campaign: {game}')
    root = base_folder(install)
    inherited = {} if game == 'quake2' else (base if base is not None else read_install(install, maps)[0])
    if game != 'quake2':
        root = root.parent / game
    paks = sorted((p for p in root.iterdir() if p.is_file() and re.fullmatch(r'pak\d+\.pak', p.name.lower())),
                  key=lambda p: int(p.stem[3:])) if root.is_dir() else []
    if not any(p.name.lower() == 'pak0.pak' for p in paks):
        raise ValueError(f'No Quake II {game} pak0.pak in {root}')
    files, records, counts = dict(inherited), [], {}

    def layer(entries, origin):
        counts[origin] = dict(directory=len(entries), md2=sum(n.endswith('.md2') for n, _ in entries),
                              sp2=sum(n.endswith('.sp2') for n, _ in entries),
                              pcx=sum(n.endswith('.pcx') for n, _ in entries),
                              bsp=sum(n.endswith('.bsp') for n, _ in entries))
        for name, data in entries:
            name = asset_name(name)
            record = dict(game=game, pak=origin, source=name, status='pending')
            if name in files and files[name][2]['game'] == game:
                previous = files[name]
                identical = previous[0] == data
                previous[2].update(status='duplicate' if identical else 'overridden',
                                   reason=f'{"Byte-identical to" if identical else "Overridden by"} {origin}:{name}')
            files[name] = data, origin, record
            records.append(record)

    for pak in paks:
        layer(read_pak(pak.read_bytes()), pak.name.lower())
    # Only read relevant loose assets; never traverse sibling campaigns.
    directories = ('models', 'players', 'sprites', 'pics') + (('maps', 'textures', 'env') if maps else ())
    suffixes = ('.md2', '.pcx', '.sp2') + (('.bsp', '.wal', '.tga') if maps else ())
    loose = sorted(p for directory in directories
                   for p in (root / directory).rglob('*')
                   if p.is_file() and p.suffix.lower() in suffixes)
    layer([(p.relative_to(root).as_posix().lower(), p.read_bytes()) for p in loose], 'loose')
    return files, [r for r in records if r['source'].endswith('.bsp' if maps else ('.md2', '.sp2'))], counts


def read_md2(data):
    if len(data) < 68:
        raise ValueError('Truncated MD2 header')
    magic, version, width, height, frame_size, skins, verts, sts, tris, commands, frames, skin_at, st_at, tri_at, frame_at, cmd_at, end = struct.unpack_from('<17i', data)
    if magic != int.from_bytes(b'IDP2', 'little') or version != 8:
        raise ValueError('Expected IDP2 version 8')
    if min(width, height, verts, sts, tris, frames) <= 0 or min(skins, commands) < 0 or frame_size < 40 + verts * 4:
        raise ValueError('Invalid MD2 dimensions or counts')
    if end < 68 or end > len(data):
        raise ValueError('Invalid MD2 end offset')
    previous = 68
    for offset, length in ((skin_at, skins * 64), (st_at, sts * 4), (tri_at, tris * 12),
                           (frame_at, frames * frame_size), (cmd_at, commands * 4)):
        if offset < previous or offset + length > end:
            raise ValueError('Invalid MD2 section range')
        previous = offset + length
    names = [asset_name(data[skin_at + i * 64:skin_at + (i + 1) * 64].split(b'\0')[0].decode('ascii', errors='replace'))
             for i in range(skins)]
    st = list(struct.iter_unpack('<2h', data[st_at:st_at + sts * 4]))
    triangles = list(struct.iter_unpack('<6H', data[tri_at:tri_at + tris * 12]))
    if any(max(t[:3]) >= verts or max(t[3:]) >= sts for t in triangles):
        raise ValueError('Invalid MD2 triangle index')
    points, pose = None, ''
    for frame in range(frames):
        at = frame_at + frame * frame_size
        values = struct.unpack_from('<6f', data, at)
        if not all(math.isfinite(v) for v in values) or min(values[:3]) < 0:
            raise ValueError('Invalid MD2 frame scale/translate')
        if frame == 0:
            pose = data[at + 24:at + 40].split(b'\0')[0].decode('ascii', errors='replace')
            points = [tuple(p[k] * values[k] + values[k + 3] for k in range(3))
                      for p in struct.iter_unpack('<4B', data[at + 40:at + 40 + verts * 4])]
    return dict(points=points, st=st, triangles=triangles, skin_names=names,
                width=width, height=height, frames=frames, pose=pose)


def build_model(mesh, texture):
    vertices, uvs, indices, corners = [], [], [], {}
    for triangle in mesh['triangles']:
        for corner in (0, 2, 1):  # MD2 fronts, like MDL, are clockwise.
            point, texcoord = triangle[corner], triangle[corner + 3]
            key = point, texcoord  # MD2 has independent ST indices, no MDL onseam flag.
            if key not in corners:
                corners[key] = len(vertices) // 3
                x, y, z = mesh['points'][point]
                vertices.extend((y, z, x))  # Proper rotation, det +1, same as Quake 1.
                s, t = mesh['st'][texcoord]
                uvs.extend(((s + .5) / mesh['width'], (t + .5) / mesh['height']))
            indices.append(corners[key])
    return dict(game='quake2', winding='ccw', vertices=vertices, uvs=uvs, indices=indices,
                groups=[dict(start=0, count=len(indices), materialIndex=0)], material_names=[texture],
                material_textures=[texture], material_settings=[{}],
                metadata=dict(pose=mesh['pose'], frames=mesh['frames'], skin_names=mesh['skin_names']))


def read_pcx(data):
    """Pillow handles PCX RLE and scanline padding; require the skin's own 256-colour palette."""
    if len(data) < 897 or data[:4] != b'\x0a\x05\x01\x08' or data[65] != 1 or data[-769] != 12:
        raise ValueError('Expected 8-bit paletted PCX with trailing palette')
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image.putpalette(data[-768:])
            return image.convert('RGB')
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid PCX: {exc}') from exc


def category(name):
    if name.startswith('players/'):
        return f'Players/{name.split("/")[1]}'
    if name.startswith('models/deadbods/') or '/gibs/' in name:
        return 'Deadbodies/Gibs'
    if name.startswith('models/monsters/'):
        return 'Monsters'
    if name.startswith('models/weapons/v_'):
        return 'Weapons/View'
    if name.startswith('models/weapons/g_'):
        return 'Weapons/World'
    if name.startswith('models/items/'):
        return 'Items'
    return 'Objects/Effects'


def choose_skin(name, mesh, files):
    folder = str(PurePosixPath(name).parent)
    body = name.startswith('players/') and name.endswith('/tris.md2')
    named = mesh['skin_names'][0] if mesh['skin_names'] else ''
    default = f'{folder}/{PLAYER_SKINS.get(folder.split("/")[-1], "skin")}.pcx' if body else ''
    if default in files:
        return default, f'Player body preview default: {default}'
    if named in files:
        return named, ''
    candidates = [f'{folder}/{PurePosixPath(named).name}', f'{folder}/skin.pcx'] if named else [f'{folder}/skin.pcx']
    if body:
        candidates += sorted(n for n in files if str(PurePosixPath(n).parent) == folder and n.endswith('.pcx')
                             and not n.endswith('_i.pcx') and not PurePosixPath(n).name.startswith('weapon'))
    for candidate in candidates:
        if candidate in files:
            return candidate, f'Missing named skin {named or "(none)"}; same-folder PCX fallback'
    if name == 'players/crakhor/w_shotgun.md2' and 'players/male/weapon.pcx' in files:
        return 'players/male/weapon.pcx', 'Missing crakhor weapon.pcx; stock male shotgun atlas fallback (136x60), approximate skin'
    return '', f'Missing named skin {named or "(none)"}; missing-skin checker fallback'


def import_catalog(install, output):
    output = Path(output)
    base, base_records, base_counts = read_install(install)
    campaigns = {'quake2': (base, base_records, base_counts)}
    for game in available_games(install)[1:]:
        campaigns[game] = read_install(install, game=game, base=base)
    # Supplemental PCXs only for unresolved base skins; never leak sibling overrides into a pack.
    supplemented = dict(base)
    for assets, _, _ in list(campaigns.values())[1:]:
        for name, entry in assets.items():
            if name.endswith('.pcx'):
                supplemented.setdefault(name, entry)
    campaigns['quake2'] = supplemented, base_records, base_counts
    records = [r for _, rows, _ in campaigns.values() for r in rows]
    counts = {f'{game}/{pak}': count for game, (_, _, inventory) in campaigns.items() for pak, count in inventory.items()}
    for sub in ('model_json', 'textures'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    catalog, seen, path_seen, taken, texture_records = [], {}, {}, set(), {}

    def texture(name):
        source_game = files[name][2]['game']
        key = source_game, name
        if key in texture_records:
            record = texture_records[key]
            if record['status'] == 'skipped':
                raise ValueError(record['reason'])
            return record['texture']
        prefix = '' if source_game == 'quake2' else source_game + '_'
        png = prefix + safe(name.removesuffix('.pcx')) + '.png'
        if any(r.get('texture') == png for r in texture_records.values()):
            png = prefix + safe(name.removesuffix('.pcx')) + '_' + hashlib.sha256(name.encode()).hexdigest()[:12] + '.png'
        record = dict(game=source_game, source=name, texture=png)
        texture_records[key] = record
        try:
            image = read_pcx(files[name][0])
            target = output / 'textures' / png
            if not target.is_file():
                image.save(target)
            record.update(status='ready', pak=files[name][1])
        except ValueError as exc:
            record.update(status='skipped', reason=str(exc))
            raise
        return png

    for record in records:
        if record['status'] != 'pending':
            continue
        name = record['source']
        game = record['game']
        files = campaigns[game][0]
        prefix = '' if game == 'quake2' else game + '_'
        model = prefix + safe(name.rsplit('.', 1)[0])
        record['model'] = model
        item = dict(model_name=model, display_name=(GAMES[game] + ': ' if prefix else '') + name, texture_name='', game='quake2',
                    category=(GAMES[game] + ': ' if prefix else '') + category(name), status='ready', source=f"{game}/{record['pak']}:{name}")
        try:
            if name.endswith('.sp2'):
                raise ValueError('Sprite (.sp2), not an MD2; sprite decoding is not included')
            raw = files[name][0]
            mesh = read_md2(raw)
            skin, reason = choose_skin(name, mesh, files)
            skin_game = files[skin][2]['game'] if skin else ''
            if skin and game == 'quake2' and skin_game != game:
                reason = f'Named skin supplied by installed {skin_game}/{files[skin][1]}'
            record.update(skin=skin, skin_reason=reason, skin_game=skin_game)
            # Include every named skin and adjacent PCX, including player alternatives and portraits.
            folder = str(PurePosixPath(name).parent)
            related = set(n for n in mesh['skin_names'] if n in files and n.endswith('.pcx'))
            related.update(n for n in files if n.endswith('.pcx') and str(PurePosixPath(n).parent) == folder)
            for alternative in sorted(related):
                try:
                    texture(alternative)
                except ValueError:
                    pass  # Exact texture failure is retained in the report.
            if skin:
                try:
                    png = texture(skin)
                except ValueError as exc:
                    reason = f'Invalid skin {skin}: {exc}; missing-skin checker fallback'
                    record.update(skin_reason=reason)
                    skin = ''
            if not skin:
                png = 'missing_skin.png'
                target = output / 'textures' / png
                if not target.is_file():
                    image = Image.new('RGB', (2, 2))
                    image.putdata([(255, 0, 255), (32, 32, 32), (32, 32, 32), (255, 0, 255)])
                    image.save(target)
            digest = hashlib.sha256(raw).hexdigest()
            retained = path_seen.get((name, digest)) or seen.get((game, digest))
            if retained:
                record.update(status='duplicate', model=retained, reason='Byte-identical MD2; listed once')
                path_seen[name, digest] = retained
                continue
            if model in taken:
                model += '_' + hashlib.sha256(name.encode()).hexdigest()[:12]
                record['model'] = item['model_name'] = model
            taken.add(model)
            data = build_model(mesh, png)
            data['metadata'].update(source=item['source'], skin=record['skin'], skin_reason=reason, skin_game=skin_game)
            (output / 'model_json' / f'{model}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
            item['texture_name'] = png
            record['status'] = 'ready'
            seen[game, digest] = model
            path_seen[name, digest] = model
        except ValueError as exc:
            record.update(status='skipped', reason=str(exc))
            item.update(status='skipped', reason=str(exc))
        catalog.append(item)
    report = dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog), pak_counts=counts,
                  md2_entries=sum(c['md2'] for c in counts.values()),
                  md2_skipped=sum(r['status'] == 'skipped' and r['source'].endswith('.md2') for r in records),
                  overridden=sum(r['status'] == 'overridden' for r in records),
                  duplicates=sum(r['status'] == 'duplicate' for r in records),
                  textures=sum(r['status'] == 'ready' for r in texture_records.values()),
                  texture_results=list(texture_records.values()), results=records)
    report['campaigns'] = {game: dict(pak_counts=inventory,
        md2_entries=sum(c['md2'] for c in inventory.values()),
        ready=sum(r['status'] == 'ready' for r in rows),
        duplicates=sum(r['status'] == 'duplicate' for r in rows),
        skipped=sum(r['status'] == 'skipped' for r in rows),
        overridden=sum(r['status'] == 'overridden' for r in rows))
        for game, (_, rows, inventory) in campaigns.items()}
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'quake2'), indent=1))
