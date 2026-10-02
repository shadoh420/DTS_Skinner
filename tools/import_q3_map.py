"""Local Quake 3 map import for the Q3 Maps page. Retail data stays outside source and builds.

python tools/import_q3_map.py --game-base "C:/Games/Quake 3 Arena" [--replace]

Reads every maps/*.bsp of baseq3 and of the mod folders beside it (pk3 archives and loose files) and writes
a pack the viewer draws: the map's draw lumps, the shader stages each of its surfaces uses, and their
textures. Format: IBSP version 46 (id Software qfiles.h). The stage model is ArenaPrototype's
Quake3StaticMaterial; search order, which of two shader scripts with one name wins, and stage defaults are
ioquake3's (files.c FS_AddGameDirectory, tr_shader.c ScanAndLoadShaderFiles, ParseShader, ParseStage,
FinishShader, R_FindShader).
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import zipfile

from PIL import Image

try:
    from tools.import_q3 import key_name, read_text
except ImportError:  # Run as a script.
    from import_q3 import key_name, read_text

LUMPS = 17
KEPT_LUMPS = (0, 1, 7, 10, 11, 13, 14)  # Entities, shaders, models, vertices, indices, faces, lightmaps: all the viewer reads.
SURF_NODRAW = 0x80
SORT = dict(portal=1, sky=2, opaque=3, decal=4, seethrough=5, banner=6, underwater=8, additive=10, nearest=16)
BLEND = dict(add=['gl_one', 'gl_one'], filter=['gl_dst_color', 'gl_zero'], blend=['gl_src_alpha', 'gl_one_minus_src_alpha'])
SKY_SIDES = ('rt', 'bk', 'lf', 'ft', 'up', 'dn')
PICKUPS = ('weapon_', 'ammo_', 'item_', 'holdable_', 'team_ctf_redflag', 'team_ctf_blueflag', 'team_ctf_neutralflag')
SPAWNS = ('info_player_deathmatch', 'info_player_start', 'team_ctf_redplayer', 'team_ctf_blueplayer', 'team_ctf_redspawn', 'team_ctf_bluespawn')


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


class Game:
    """One game folder over the ones it builds on, searched as the game does: loose files, then archives
    from the last name to the first, then the same for the folder beneath (baseq3 under a mod)."""

    def __init__(self, folders):
        self.archives, self.sources = [], []  # Sources: (label, {key: read}), first found wins.
        self.own = 0  # How many of them are the first folder's.
        for folder in folders:
            self.own = self.own or len(self.sources)
            loose = {key_name(path.relative_to(folder)): path.read_bytes for path in sorted(folder.rglob('*'))
                     if path.is_file() and path.suffix.lower() != '.pk3'}
            self.sources.append((folder.name + ' folder', loose))
            for path in sorted(folder.glob('*.pk3'), key=lambda p: p.name.lower().replace('\\', '/'), reverse=True):
                try:
                    archive = zipfile.ZipFile(path)
                except (OSError, zipfile.BadZipFile):
                    continue  # The game also passes over an archive it cannot open.
                self.archives.append(archive)
                files = {}
                for entry in archive.infolist():
                    if not entry.is_dir() and entry.file_size <= 256 * 1024 * 1024:
                        files.setdefault(key_name(entry.filename), lambda z=archive, n=entry.filename: z.read(n))
                self.sources.append((path.name, files))
        self.files = {}
        for label, files in reversed(self.sources):
            self.files.update({key: (label, read) for key, read in files.items()})
        # The game joins the scripts from the last listed to the first and takes the first definition of a
        # name, so of two scripts defining one shader the lower source wins unless the files share a name.
        listed = list(dict.fromkeys(key for _, files in self.sources for key in files if re.fullmatch(r'scripts/[^/]+\.shader', key)))
        self.shaders = {}
        for key in reversed(listed):
            for name, body in parse_shaders(read_text(self.files[key][1]())).items():
                self.shaders.setdefault(name, body)
        self.arenas = {}
        for key in self.files:
            if re.fullmatch(r'scripts/([^/]+\.arena|arenas\.txt)', key):
                for block in re.findall(r'\{([^{}]*)\}', read_text(self.files[key][1]())):
                    pairs = {(quoted or bare).lower(): value for quoted, bare, value in re.findall(r'(?:"([^"]*)"|([^\s"]+))\s+"([^"]*)"', block)}
                    if pairs.get('map'):
                        self.arenas.setdefault(pairs['map'].lower(), pairs.get('longname', ''))
        self.described, self.images = {}, {}

    def close(self):
        for archive in self.archives:
            archive.close()


def shader_lines(text):
    """Script lines as token lists, braces on lines of their own. The game reads a directive and its
    arguments from one line, so line ends matter."""
    text = re.sub(r'/\*.*?\*/', lambda match: '\n' * match.group().count('\n'), text, flags=re.S)
    for line in text.splitlines():
        row = []
        for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', line.split('//')[0]):
            token = quoted or bare
            if bare in ('{', '}'):
                if row: yield row
                yield [bare]
                row = []
            else:
                row.append(token)
        if row: yield row


def parse_shaders(text):
    """{name: (shader lines, [stage lines])}; the first definition of a name is kept, as in the game."""
    shaders, name, depth, body, stages = {}, None, 0, [], []
    for row in shader_lines(text):
        if row == ['{']:
            depth += 1
            if depth == 2: stages.append([])
        elif row == ['}']:
            depth = max(depth - 1, 0)
            if depth == 0 and name is not None:
                shaders.setdefault(shader_name(name), (body, stages))
                name = None
        elif depth == 0:
            name, body, stages = row[0], [], []
        elif depth == 1:
            body.append([row[0].lower()] + row[1:])
        elif depth == 2:
            stages[-1].append([row[0].lower()] + row[1:])
    return shaders


def shader_name(name):
    # R_FindShader strips the extension before it looks a name up.
    return re.sub(r'\.[^/.]*$', '', key_name(name))


def numbers(tokens, count, default=0.0):
    values = []
    for token in tokens:
        try: values.append(float(token))
        except ValueError: continue  # Brackets around vectors, and words where the game expects a number.
    return (values + [default] * count)[:count]


def wave(tokens):
    return [tokens[0].lower() if tokens else 'sin'] + numbers(tokens[1:], 4)


def describe(game, name, textures):
    """What the viewer needs of one shader: stages in ArenaPrototype's terms, with ioquake3's defaults.
    Returns (shader, unresolved, limits); the lists say what is missing and what is not reproduced."""
    if name in game.described:
        return game.described[name]
    unresolved, limits = [], set()

    def image(path):
        file = textures.resolve(game, path)
        if not file:
            unresolved.append(f'texture {path} (shader {name})')
        return file

    if name not in game.shaders:
        # No script: the game draws the image of that name under the lightmap, or with the vertex colours.
        file = textures.resolve(game, name)
        if not file:
            unresolved.append(f'shader {name} (no script and no texture of that name)')
        result = dict(name=name, implicit=True, map=file, cull='front', sort=SORT['opaque']), unresolved, []
        game.described[name] = result
        return result
    body, stage_lines = game.shaders[name]
    shader = dict(name=name, cull='front', stages=[])
    sort = 0
    for row in body:
        word, args = row[0], [arg.lower() for arg in row[1:]]
        if word == 'surfaceparm' and args:
            if args[0] == 'fog': shader['fog'] = True
        elif word == 'skyparms':
            sky = dict(cloudHeight=numbers(args[1:2], 1)[0] or 512)
            if args and args[0] != '-':
                # A box of which no side exists ("full", "half", "512" in many scripts) is not drawn by the game either.
                sides = [textures.resolve(game, f'{row[1]}_{side}.tga') for side in SKY_SIDES]
                if all(sides): sky['box'] = sides
                elif any(sides): unresolved.extend(f'texture {row[1]}_{side}.tga (shader {name})' for side, file in zip(SKY_SIDES, sides) if not file)
            if len(args) > 2 and args[2] != '-': limits.add('inner sky boxes')
            shader['sky'] = sky
        elif word == 'cull' and args:
            shader['cull'] = 'none' if args[0] in ('none', 'twosided', 'disable') else 'back' if args[0] in ('back', 'backside', 'backsided') else 'front'
        elif word == 'polygonoffset':
            shader['polygonOffset'] = True
        elif word == 'sort' and args:
            sort = SORT.get(args[0]) or numbers(args, 1)[0]
        elif word == 'portal':
            sort = SORT['portal']
            limits.add('portals and mirrors (drawn as plain surfaces)')
        elif word == 'deformvertexes' and args:
            if args[0] == 'wave':  # Spread is one over the first number; then a wave along the normal.
                shader.setdefault('deforms', []).append(['wave', 1 / (numbers(args[1:2], 1)[0] or 100)] + wave(args[2:]))
            elif args[0] == 'move':
                shader.setdefault('deforms', []).append(['move'] + numbers(args[1:4], 3) + wave(args[4:]))
            elif args[0] == 'bulge':
                shader.setdefault('deforms', []).append(['bulge'] + numbers(args[1:], 3))
            else:
                limits.add({'normal': 'wobbling normals', 'autosprite': 'sprites that turn to the camera', 'autosprite2': 'sprites that turn to the camera',
                            'projectionshadow': 'projected shadows'}.get(args[0], 'text sprites' if args[0].startswith('text') else 'vertex deformation ' + args[0]))
        elif word == 'fogparms':
            limits.add('fog volumes')
    for lines in stage_lines:
        stage = dict(rgbGen=None, alphaGen=['identity'], tcMods=[], depthWrite=True)
        explicit_depth = False
        for row in lines:
            word, args = row[0], [arg.lower() for arg in row[1:]]
            if word in ('map', 'clampmap') and args:
                stage['clamp'] = word == 'clampmap'
                stage['map'] = args[0] if args[0] in ('$lightmap', '$whiteimage') else '$whiteimage' if args[0] == '*white' else image(row[1])
            elif word == 'animmap' and len(args) > 1:
                frames = [image(path) for path in row[2:10]]
                stage.update(fps=numbers(args, 1)[0], frames=[frame for frame in frames if frame])
                stage['map'] = stage['frames'][0] if stage['frames'] else None
            elif word == 'videomap':
                limits.add('video textures')
            elif word == 'blendfunc' and args:
                blend = BLEND.get(args[0]) or [args[0], args[1] if len(args) > 1 else 'gl_one']
                stage['blend'] = None if blend == ['gl_one', 'gl_zero'] else blend
                if not explicit_depth: stage['depthWrite'] = blend == ['gl_one', 'gl_zero']
            elif word == 'alphafunc' and args:
                stage['alphaFunc'] = args[0]
            elif word == 'depthfunc' and args:
                stage['depthFunc'] = 'equal' if args[0] == 'equal' else 'lequal'
            elif word == 'depthwrite':
                stage['depthWrite'] = explicit_depth = True
            elif word == 'rgbgen' and args:
                stage['rgbGen'] = ['wave'] + wave(args[1:]) if args[0] == 'wave' else ['const'] + numbers(args[1:], 3) if args[0] == 'const' else [args[0]]
            elif word == 'alphagen' and args:
                stage['alphaGen'] = ['wave'] + wave(args[1:]) if args[0] == 'wave' else ['const'] + numbers(args[1:], 1) if args[0] == 'const' else \
                    ['portal', numbers(args[1:], 1)[0] or 256] if args[0] == 'portal' else [args[0]]
            elif word in ('tcgen', 'texgen') and args:
                stage['tcGen'] = 'base' if args[0] == 'texture' else args[0]
                if args[0] == 'vector': limits.add('texture coordinates from vectors')
            elif word == 'tcmod' and args:
                kind = args[0]
                if kind == 'stretch':
                    stage['tcMods'].append([kind] + wave(args[1:]))
                elif kind in ('turb', 'scale', 'scroll', 'transform', 'rotate'):
                    stage['tcMods'].append([kind] + numbers(args[1:], dict(turb=4, scale=2, scroll=2, transform=6, rotate=1)[kind]))
                else:
                    limits.add('texture movement ' + kind)
        if not stage.get('map'):
            continue  # Reported above. The game gives up on the whole shader here; the other stages are still drawn.
        blend = stage.get('blend')
        if stage['rgbGen'] is None:
            stage['rgbGen'] = ['identitylighting'] if not blend or blend[0] in ('gl_one', 'gl_src_alpha') else ['identity']
        if stage['map'] == '$lightmap': stage.setdefault('tcGen', 'lightmap')
        for kind, value in (('rgbGen', stage['rgbGen'][0]), ('alphaGen', stage['alphaGen'][0])):
            if value in ('entity', 'oneminusentity', 'lightingdiffuse'):
                limits.add('model lighting' if value == 'lightingdiffuse' else 'entity colours')
        if stage['rgbGen'][0] == 'wave' and stage['rgbGen'][1] == 'noise' or stage['alphaGen'][0] == 'wave' and stage['alphaGen'][1] == 'noise':
            limits.add('noise waves')
        shader['stages'].append({key: value for key, value in stage.items() if value not in (None, False, [])
                                 or key == 'depthWrite'})
    stages = shader['stages']
    if not sort:
        blended = [stage for stage in stages if stage.get('blend')]
        sort = SORT['sky'] if 'sky' in shader else SORT['decal'] if shader.get('polygonOffset') else (
            SORT['opaque'] if not blended or not stages[0].get('blend') else SORT['seethrough'] if blended[0]['depthWrite'] else 9)
    shader['sort'] = sort
    if shader.get('fog'): limits.add('fog volumes')
    result = shader, unresolved, sorted(limits)
    game.described[name] = result
    return result


class Textures:
    """Images named by content under textures/, so maps that share one store it once."""

    def __init__(self, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)

    def resolve(self, game, path):
        key = key_name(path)
        if key in game.images:
            return game.images[key]
        stem = re.sub(r'\.[^/.]*$', '', key)
        found = next((candidate for candidate in [key] + [stem + ext for ext in ('.tga', '.jpg', '.jpeg', '.png')] if candidate in game.files), None)
        file = None
        if found:
            raw = game.files[found][1]()
            jpeg = raw[:2] == b'\xff\xd8'
            file = hashlib.sha256(raw).hexdigest()[:20] + ('.jpg' if jpeg else '.png')
            if not (self.output / file).exists():
                try:
                    if jpeg:
                        Image.open(io.BytesIO(raw)).verify()
                        (self.output / file).write_bytes(raw)
                    else:
                        with Image.open(io.BytesIO(raw)) as source:
                            source.convert('RGBA' if source.mode in ('RGBA', 'LA', 'P', 'PA') or 'transparency' in source.info else 'RGB').save(self.output / file)
                except (OSError, ValueError, SyntaxError):
                    file = None  # Reported as unresolved by the caller.
        game.images[key] = file
        return file


def read_bsp(raw):
    """Lumps of an IBSP 46 file, each checked to lie inside it."""
    if len(raw) < 8 + LUMPS * 8 or raw[:4] != b'IBSP':
        raise ValueError('not a Quake 3 map (IBSP)')
    version, = struct.unpack_from('<i', raw, 4)
    if version != 46:
        raise ValueError(f'IBSP version {version}, not 46')
    lumps = []
    for index in range(LUMPS):
        offset, length = struct.unpack_from('<ii', raw, 8 + index * 8)
        if offset < 0 or length < 0 or offset + length > len(raw):
            raise ValueError(f'lump {index} lies outside the file')
        lumps.append(raw[offset:offset + length])
    return lumps


def write_bsp(path, lumps):
    """The lumps the viewer reads, as a valid map file of its own; the others are left empty."""
    header, body, at = b'IBSP' + struct.pack('<i', 46), b'', 8 + LUMPS * 8
    for index, lump in enumerate(lumps):
        kept = lump if index in KEPT_LUMPS else b''
        header += struct.pack('<ii', at + len(body), len(kept))
        body += kept + b'\0' * (-len(kept) % 4)
    path.write_bytes(header + body)


def entities(text):
    result = []
    for block in re.findall(r'\{([^{}]*)\}', text):
        result.append({key.lower(): value for key, value in re.findall(r'"([^"]*)"\s+"([^"]*)"', block)})
    return result


def import_map(game, name, ident, raw, source, output, textures):
    lumps = read_bsp(raw)
    names = [(shader_name(entry[0].split(b'\0')[0].decode('latin-1')), entry[1]) for entry in struct.iter_unpack('<64sii', lumps[1][:len(lumps[1]) // 72 * 72])]
    used, flares = set(), 0
    for at in range(0, len(lumps[13]) // 104 * 104, 104):
        shader, _, kind = struct.unpack_from('<3i', lumps[13], at)
        if kind == 4: flares += 1
        elif 0 <= shader < len(names) and not names[shader][1] & SURF_NODRAW: used.add(shader)  # tr_bsp.c skips these too.
    shaders, unresolved, limits = [None] * len(names), [], {}
    for index in sorted(used):
        shader, missing, effects = describe(game, names[index][0], textures)
        shaders[index] = shader
        unresolved += [item for item in missing if item not in unresolved]
        for effect in effects:
            limits.setdefault(effect, []).append(shader['name'])
    things = entities(lumps[0].decode('latin-1'))
    vector = lambda value, count=3: numbers(str(value).split(), count)
    by_target = {thing['targetname']: thing for thing in things if 'targetname' in thing}
    viewpoints = []
    for kind in ('info_player_intermission',) + SPAWNS:
        for thing in things:
            if thing.get('classname', '').lower() != kind: continue
            origin = vector(thing.get('origin', ''))
            angles = vector(thing['angles']) if 'angles' in thing else [0, numbers([thing.get('angle', '0')], 1)[0], 0]
            view = dict(origin=origin, pitch=angles[0], yaw=angles[1])
            if thing.get('target') in by_target: view['target'] = vector(by_target[thing['target']].get('origin', ''))
            if kind != 'info_player_intermission': view['origin'] = [origin[0], origin[1], origin[2] + 26]  # Eye height above a spawn point.
            viewpoints.append(view)
    notes = [f'{label}: {len(found)} shader{"s" if len(found) > 1 else ""} ({", ".join(found[:4])}{"…" if len(found) > 4 else ""})' for label, found in sorted(limits.items())]
    if flares: notes.append(f'{flares} light flares')
    pickups = sum(thing.get('classname', '').lower().startswith(PICKUPS) for thing in things)
    if pickups: notes.append(f'{pickups} pickups and flags (their models are not placed)')
    file = 'bsp/' + hashlib.sha256(raw).hexdigest()[:20] + '.bsp'
    if not (output / file).exists():
        write_bsp(output / file, lumps)
    folder = output / 'maps' / ident
    folder.mkdir(parents=True, exist_ok=True)
    scene = dict(name=name, longname=game.arenas.get(name.lower(), ''), source=source, bsp=file, shaders=shaders, viewpoints=viewpoints,
                 models=[dict(model=int(thing['model'][1:]), origin=vector(thing['origin'])) for thing in things
                         if re.fullmatch(r'\*\d+', thing.get('model', '')) and 'origin' in thing],
                 unresolved=unresolved, limits=notes)
    (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
    return scene


def import_maps(game, output, replace=False):
    """Import every map of the Quake 3 folder `game`. Returns imported, skipped and failed maps and, for
    each imported map with any, what it names that the game files do not hold."""
    game, output = Path(game).expanduser(), Path(output)
    if not game.is_dir():
        raise ValueError('Quake 3 folder does not exist')
    if game.name.lower() == 'baseq3':
        game = game.parent
    base = next((folder for folder in game.iterdir() if folder.is_dir() and folder.name.lower() == 'baseq3'), None)
    if not base or not any(base.glob('pak*.pk3')):
        raise ValueError('No baseq3 folder with pak0.pk3 here. Enter the Quake 3 Arena folder.')
    (output / 'bsp').mkdir(parents=True, exist_ok=True)
    textures = Textures(output / 'textures')
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={}, unresolved={})
    folders = [base] + sorted(folder for folder in game.iterdir() if folder.is_dir() and folder != base and any(folder.glob('*.pk3')))
    for folder in folders:
        mounted = Game([folder] if folder == base else [folder, base])
        try:
            maps = {}
            for label, files in mounted.sources[:mounted.own or len(mounted.sources)]:
                for key in files:
                    match = re.fullmatch(r'maps/([^/]+)\.bsp', key)
                    if match: maps.setdefault(match.group(1), label)
            for name, label in sorted(maps.items()):
                loose = label.endswith(' folder')
                stock = folder == base and re.fullmatch(r'pak\d\.pk3', label.lower())
                group = ('Quake III Arena' if stock else 'Loose files in maps folder' if loose else 'Custom maps') if folder == base else \
                    'Team Arena' if folder.name.lower() == 'missionpack' else folder.name
                ident = map_id(name if folder == base else folder.name + '__' + name)
                if ident in index and (output / 'maps' / ident / 'scene.json').is_file() and not replace:
                    result['skipped'].append(name)
                    continue
                try:
                    scene = import_map(mounted, name, ident, mounted.files['maps/' + name + '.bsp'][1](), label, output, textures)
                except (ValueError, OSError, struct.error, zipfile.BadZipFile, KeyError) as exc:
                    result['failed'][name] = str(exc)
                    continue
                index[ident] = dict(id=ident, name=name, longname=scene['longname'], group=group, source=label, unresolved=len(scene['unresolved']))
                result['imported'].append(name)
                if scene['unresolved']: result['unresolved'][name] = scene['unresolved']
        finally:
            mounted.close()
    temporary = index_path.with_suffix('.tmp')
    # The game's own maps first, then mods, then what was added to baseq3.
    rank = {'Quake III Arena': 0, 'Team Arena': 1, 'Custom maps': 3, 'Loose files in maps folder': 4}
    listed = sorted(index.values(), key=lambda item: (rank.get(item['group'], 2), item['group'], item['name'].lower()))
    temporary.write_text(json.dumps(listed, separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'local-data/q3-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    for name, items in done['unresolved'].items(): print('UNRESOLVED', name, '; '.join(items))
