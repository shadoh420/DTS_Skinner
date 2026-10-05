"""Local Quake 3 map import for the Q3 Maps page. Retail data stays outside source and builds.

python tools/import_q3_map.py --game-base "C:/Games/Quake 3 Arena" [--replace] [--extra folder]

Reads every maps/*.bsp of baseq3 and of the mod folders beside it (pk3 archives and loose files) and writes
a pack the viewer draws: the map's draw lumps, the shader stages each of its surfaces uses, and their
textures. Format: IBSP version 46 (id Software qfiles.h). The stage model is ArenaPrototype's
Quake3StaticMaterial and stage defaults are the game's (tr_shader.c ParseShader, ParseStage, FinishShader,
R_FindShader). Where engines differ this follows CNQ3, which the install it was written against runs: search
order (files.cpp FS_AddGameDirectory), which of two shader scripts with one name wins (tr_shader.cpp
ScanAndLoadShaderFiles; ioquake3 picks the other one) and the default shader for what is missing. Pickups get
the models of ArenaPrototype's Quake3ItemCatalog.

What a map names and the game folder lacks can be supplied from an extras folder laid out like baseq3
(textures/..., scripts/..., or whole pk3 archives): local-data/q3-extra beside the pack unless --extra names
another. It is searched after everything of the game and only fills gaps; what came from it is listed per map.
What is still missing after that is guessed from a shader or image of the same file name in another folder,
and listed per map as a guess.
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
    from tools.import_q3 import key_name, read_md3, read_text
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script.
    from import_q3 import key_name, read_md3, read_text
    from local_data import LOCAL_DATA

LUMPS = 17
KEPT_LUMPS = (0, 1, 7, 10, 11, 13, 14)  # Entities, shaders, models, vertices, indices, faces, lightmaps: all the viewer reads.
SURF_NODRAW = 0x80
EXTRA = 'extras: '  # Starts the label of every source that is not the game's own.
IMAGES = ('.tga', '.jpg', '.jpeg', '.png')
SORT = dict(portal=1, sky=2, opaque=3, decal=4, seethrough=5, banner=6, underwater=8, additive=10, nearest=16)
BLEND = dict(add=['gl_one', 'gl_one'], filter=['gl_dst_color', 'gl_zero'], blend=['gl_src_alpha', 'gl_one_minus_src_alpha'])
SKY_SIDES = ('rt', 'bk', 'lf', 'ft', 'up', 'dn')
# Pickups by class name: kind, then the models the game shows for it (under models/, without .md3). Health and
# powerups show both of theirs; the kind decides how the game turns and sizes them (cg_ents.c CG_Item).
ITEMS = {row.split()[0]: (kind, row.split()[1:]) for kind, rows in dict(
    health='''item_health_small powerups/health/small_cross powerups/health/small_sphere
        item_health powerups/health/medium_cross powerups/health/medium_sphere
        item_health_large powerups/health/large_cross powerups/health/large_sphere
        item_health_mega powerups/health/mega_cross powerups/health/mega_sphere''',
    armor='''item_armor_shard powerups/armor/shard
        item_armor_combat powerups/armor/armor_yel
        item_armor_body powerups/armor/armor_red
        item_armor_jacket powerups/armor/armor_gre''',
    weapon='''weapon_gauntlet weapons2/gauntlet/gauntlet
        weapon_shotgun weapons2/shotgun/shotgun
        weapon_machinegun weapons2/machinegun/machinegun
        weapon_grenadelauncher weapons2/grenadel/grenadel
        weapon_rocketlauncher weapons2/rocketl/rocketl
        weapon_lightning weapons2/lightning/lightning
        weapon_railgun weapons2/railgun/railgun
        weapon_plasmagun weapons2/plasma/plasma
        weapon_bfg weapons2/bfg/bfg
        weapon_grapplinghook weapons2/grapple/grapple
        weapon_nailgun weapons/nailgun/nailgun
        weapon_prox_launcher weapons/proxmine/proxmine
        weapon_chaingun weapons/vulcan/vulcan''',
    powerup='''item_quad powerups/instant/quad powerups/instant/quad_ring
        item_enviro powerups/instant/enviro powerups/instant/enviro_ring
        item_haste powerups/instant/haste powerups/instant/haste_ring
        item_invis powerups/instant/invis powerups/instant/invis_ring
        item_regen powerups/instant/regen powerups/instant/regen_ring
        item_flight powerups/instant/flight powerups/instant/flight_ring''',
    other='''ammo_shells powerups/ammo/shotgunam
        ammo_bullets powerups/ammo/machinegunam
        ammo_grenades powerups/ammo/grenadeam
        ammo_cells powerups/ammo/plasmaam
        ammo_lightning powerups/ammo/lightningam
        ammo_rockets powerups/ammo/rocketam
        ammo_slugs powerups/ammo/railgunam
        ammo_bfg powerups/ammo/bfgam
        ammo_nails powerups/ammo/nailgunam
        ammo_mines powerups/ammo/proxmineam
        ammo_belt powerups/ammo/chaingunam
        holdable_teleporter powerups/holdable/teleporter
        holdable_medkit powerups/holdable/medkit
        holdable_kamikaze powerups/kamikazi
        holdable_portal powerups/holdable/porter
        holdable_invulnerability powerups/holdable/invulnerability
        item_scout powerups/scout
        item_guard powerups/guard
        item_doubler powerups/doubler
        item_ammoregen powerups/ammo
        team_ctf_redflag flags/r_flag
        team_ctf_blueflag flags/b_flag
        team_ctf_neutralflag flags/n_flag''').items() for row in rows.splitlines()}
# Classes the base game does not have (Team Arena's, and the green armour that mods add): it places none of
# them, so a map is not short of their models where the game folder has none.
NOT_IN_BASE = ('item_armor_jacket', 'weapon_nailgun', 'weapon_prox_launcher', 'weapon_chaingun', 'ammo_nails', 'ammo_mines', 'ammo_belt', 'holdable_kamikaze',
              'holdable_portal', 'holdable_invulnerability', 'item_scout', 'item_guard', 'item_doubler', 'item_ammoregen', 'team_ctf_neutralflag')
SPAWNS = ('info_player_deathmatch', 'info_player_start', 'team_ctf_redplayer', 'team_ctf_blueplayer', 'team_ctf_redspawn', 'team_ctf_bluespawn')


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


class Game:
    """One game folder over the ones it builds on, searched as CNQ3 and the original game search: archives from
    the last name to the first, then loose files, then the same for the folder beneath (baseq3 under a mod).
    ioquake3 looks at loose files first. Extras folders come after all of them and never replace what the game has."""

    def __init__(self, folders, extras=()):
        self.archives, self.sources = [], []  # Sources: (label, {key: read}), first found wins.
        self.own = 0  # How many of them are the first folder's.
        for number, folder in enumerate([*folders, *extras]):
            self.own = self.own or len(self.sources)
            prefix = EXTRA if number >= len(folders) else ''
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
                self.sources.append((prefix + path.name, files))
            loose = {key_name(path.relative_to(folder)): path.read_bytes for path in sorted(folder.rglob('*'))
                     if path.is_file() and path.suffix.lower() != '.pk3'}
            self.sources.append((prefix + folder.name + ' folder', loose))
        self.files = {}
        for label, files in reversed(self.sources):
            self.files.update({key: (label, read) for key, read in files.items()})
        # Of script files with one name only the first found is read. CNQ3 joins the scripts in the order found
        # and takes the first definition of a name, so of two scripts defining one shader the higher source wins.
        # (ioquake3 and the original game join them last to first, so there the lower source wins.)
        script = lambda key: re.fullmatch(r'scripts/[^/]+\.shader', key)
        listed = list(dict.fromkeys(key for label, files in self.sources if not label.startswith(EXTRA) for key in files if script(key)))
        self.shaders, self.shader_label = {}, {}  # And the source each definition came from.
        for key in listed:
            for name, body in parse_shaders(read_text(self.files[key][1]())).items():
                if name not in self.shaders:
                    self.shaders[name], self.shader_label[name] = body, self.files[key][0]
        # Scripts of the extras define only shaders the game has none for.
        self.outside = set()  # Shader names and file keys that were found in the extras.
        for label, files in self.sources:
            for key in files if label.startswith(EXTRA) else ():
                for name, body in parse_shaders(read_text(files[key]())).items() if script(key) else ():
                    if name not in self.shaders:
                        self.shaders[name], self.shader_label[name] = body, label
                        self.outside.add(name)
        self.arenas = {}
        for key in self.files:
            if re.fullmatch(r'scripts/([^/]+\.arena|arenas\.txt)', key):
                for block in re.findall(r'\{([^{}]*)\}', read_text(self.files[key][1]())):
                    pairs = {(quoted or bare).lower(): value for quoted, bare, value in re.findall(r'(?:"([^"]*)"|([^\s"]+))\s+"([^"]*)"', block)}
                    if pairs.get('map'):
                        self.arenas.setdefault(pairs['map'].lower(), pairs.get('longname', ''))
        self.described, self.images, self.models, self.same_name = {}, {}, {}, None

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


def describe(game, name, textures, source=''):
    """What the viewer needs of one shader, as (shader, unresolved, limits); the lists say what is missing and
    what is not reproduced. A shader the game cannot build is guessed where `guess` finds something, which can
    depend on `source`, the archive of the map that asks."""
    for key in ((name, source), name):
        if key in game.described:
            return game.described[key]
    result, key = build(game, name, textures), name
    if result[0].get('default'):
        guessed = guess(game, name, result, textures, source)
        if guessed:
            result, key = guessed, (name, source)
    game.described[key] = result
    return result


def guess(game, name, result, textures, source):
    """For a shader the game cannot build: stand-ins of the same file name from another folder under the same top
    folder, a script before an image, the map's own archive first, then the stock paks, then any other. Returns
    the result with what was guessed listed in the shader's `guessed`, or None. Nothing says the stand-in is the
    same thing, which is why it is always reported as a guess."""
    if game.same_name is None:
        game.same_name = {}
        for other, label in game.shader_label.items():
            game.same_name.setdefault(other.rsplit('/', 1)[-1], []).append(('shader', other, label))
        for key, (label, _) in game.files.items():
            if key.endswith(IMAGES):
                game.same_name.setdefault(re.sub(r'\.[^/.]*$', '', key).rsplit('/', 1)[-1], []).append(('image', key, label))

    def candidates(path, kinds):
        stem = re.sub(r'\.[^/.]*$', '', key_name(path).lstrip('/'))
        found = [(kind, other, label) for kind, other, label in game.same_name.get(stem.rsplit('/', 1)[-1], ())
                 if kind in kinds and other.split('/')[0] == stem.split('/')[0] and re.sub(r'\.[^/.]*$', '', other) != stem]
        return sorted(found, key=lambda item: (0 if item[2] == source else 1 if re.fullmatch(r'pak\d\.pk3', item[2]) else 2, item[0] != 'shader', item[1]))

    if name not in game.shaders:
        for kind, other, _ in candidates(name, ('shader', 'image')):
            found = build(game, other, textures) if kind == 'shader' else build(game, name, textures, {name: other})
            if not found[0].get('default'):
                return dict(found[0], name=name, guessed=[f'shader {name} drawn as {other}']), [], found[2]
        return None
    swap, notes = {}, []
    for item in result[1]:  # 'texture <path> (shader <name>)'
        path = item[len('texture '):].rsplit(' (shader', 1)[0]
        found = candidates(path, ('image',))
        if found:
            swap[key_name(path).lstrip('/')] = found[0][1]
            notes.append(f'texture {path} taken from {found[0][1]}')
    found = build(game, name, textures, swap) if swap else result
    return None if found[0].get('default') else (dict(found[0], guessed=notes), found[1], found[2])


def build(game, name, textures, swap=None):
    """One shader for the viewer: stages in ArenaPrototype's terms, with the game's defaults. `swap` names
    images to use in place of ones the shader asks for."""
    unresolved, limits, outside = [], set(), ['shader script ' + name] if name in game.outside else []
    # What the game draws for a shader it cannot build, one with no script or image or with a stage whose image
    # is missing: its built-in default shader (R_FindShader, ShaderForShaderNum).
    default = dict(name=name, default=True, cull='front', sort=SORT['opaque'], outside=outside)

    def image(path):
        used = (swap or {}).get(key_name(path).lstrip('/'), path)
        file = textures.resolve(game, used)
        if not file:
            default['broken'] = True
            unresolved.append(f'texture {path} (shader {name})')
        elif key_name(used).lstrip('/') in game.outside:
            outside.append(f'texture {used}')
        return file

    if name not in game.shaders:
        # No script: the game draws the image of that name under the lightmap, or with the vertex colours.
        file = image(name)
        if not file:
            unresolved[:] = [f'shader {name} (no script and no texture of that name)']
        default.pop('broken', None)
        return (dict(name=name, implicit=True, map=file, cull='front', sort=SORT['opaque'], outside=outside) if file else default), unresolved, []
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
                outside += [f'texture {row[1]}_{side}.tga' for side in SKY_SIDES if key_name(f'{row[1]}_{side}.tga') in game.outside]
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
            continue  # A stage that names no image is left out by the game too.
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
    shader['outside'] = outside
    return (default, unresolved, []) if default.pop('broken', False) else (shader, unresolved, sorted(limits))


def read_tga(raw):
    """A TGA as the game reads it (tr_image_tga.c): plain or run-length colour, or plain grey, 8, 24 or 32 bits.
    For the files Pillow refuses: a run that crosses the end of a row, or an id field that makes the file look
    like another format."""
    if len(raw) < 18:
        raise ValueError('TGA header cut short')
    id_length, colour_map, kind, _, _, _, _, _, width, height, bits, attributes = struct.unpack_from('<BBBHHBHHHHBB', raw)
    if kind not in (2, 3, 10) or colour_map or bits not in (8, 24, 32) or not (0 < width <= 8192 and 0 < height <= 8192):
        raise ValueError('TGA of a kind the game does not read')
    size, at, wanted = bits // 8, 18 + id_length, width * height * (bits // 8)
    if kind == 10:
        data = bytearray()
        while len(data) < wanted and at < len(raw):
            run = (raw[at] & 127) + 1
            if raw[at] & 128:
                data += raw[at + 1:at + 1 + size] * run
                at += 1 + size
            else:
                data += raw[at + 1:at + 1 + size * run]
                at += 1 + size * run
        data = bytes(data[:wanted])
    else:
        data = raw[at:at + wanted]
    if len(data) < wanted:
        raise ValueError('TGA data cut short')
    return Image.frombytes({1: 'L', 3: 'RGB', 4: 'RGBA'}[size], (width, height), data, 'raw', {1: 'L', 3: 'BGR', 4: 'BGRA'}[size], 0,
                           1 if attributes & 0x20 else -1)  # Rows run bottom to top unless the header says otherwise.


class Textures:
    """Images named by content under textures/, so maps that share one store it once."""

    def __init__(self, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)

    def resolve(self, game, path):
        key = key_name(path).lstrip('/')  # The game's file system drops a leading slash, which some maps' shader names have.
        if key in game.images:
            return game.images[key]
        stem = re.sub(r'\.[^/.]*$', '', key)
        named = [candidate for candidate in dict.fromkeys([key] + [stem + ext for ext in IMAGES]) if candidate in game.files]
        # The game goes on to the next extension when a file will not load: Team Arena's pak0 holds empty .tga files
        # over images the base game has as .jpg. Every name is tried among the game's own files before the extras.
        for found in sorted(named, key=lambda candidate: game.files[candidate][0].startswith(EXTRA)):
            raw = game.files[found][1]()
            jpeg = raw[:2] == b'\xff\xd8'
            file = hashlib.sha256(raw).hexdigest()[:20] + ('.jpg' if jpeg else '.png')
            if not (self.output / file).exists():
                try:
                    if jpeg:
                        Image.open(io.BytesIO(raw)).verify()
                        (self.output / file).write_bytes(raw)
                    else:
                        try:
                            source = Image.open(io.BytesIO(raw))
                            source.load()
                        except (OSError, ValueError, SyntaxError):
                            source = read_tga(raw)
                        source.convert('RGBA' if source.mode in ('RGBA', 'LA', 'P', 'PA') or 'transparency' in source.info else 'RGB').save(self.output / file)
                except (OSError, ValueError, SyntaxError, struct.error):
                    continue
            if game.files[found][0].startswith(EXTRA): game.outside.add(key)
            game.images[key] = file
            return file
        game.images[key] = None  # Reported as unresolved by the caller.
        return None


def item_model(game, path, textures, output):
    """One pickup model for the viewer: its first frame under models/, named by content, the shader of each
    surface, and the middle of its bounds. Returns (model, unresolved), or (None, []) if the game has no such file."""
    if path in game.models:
        return game.models[path]
    result = None, []
    if path in game.files:
        raw = game.files[path][1]()
        try:
            surfaces = read_md3(raw)['surfaces']
        except (ValueError, struct.error):
            surfaces = []
        if surfaces:
            file = 'models/' + hashlib.sha256(raw).hexdigest()[:20] + '.json'
            if not (output / file).exists():
                short = lambda values, places: [round(value, places) for value in values]
                (output / file).write_text(json.dumps([dict(vertices=short(surface['vertices'], 3), normals=short(surface['normals'], 3),
                                                            uvs=short(surface['uvs'], 5), indices=surface['indices']) for surface in surfaces], separators=(',', ':')), encoding='utf-8')
            shaders, unresolved = [], []
            for surface in surfaces:
                shader, missing, _ = describe(game, shader_name(next(iter(surface['shaders']), '')), textures)
                shaders.append(shader)
                unresolved += [item for item in missing if item not in unresolved]
            points = [value for surface in surfaces for value in surface['vertices']]
            middle = [(min(points[axis::3]) + max(points[axis::3])) / 2 for axis in range(3)]
            result = dict(file=file, shaders=shaders, middle=middle), unresolved
    game.models[path] = result
    return result


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
        shader, missing, effects = describe(game, names[index][0], textures, source)
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
    items, item_models = [], {}
    for thing in things:
        classname = thing.get('classname', '').lower()
        if classname not in ITEMS: continue
        kind, paths = ITEMS[classname]
        paths = [f'models/{path}.md3' for path in paths]
        for path in paths:
            model, missing = item_model(game, path, textures, output)
            if model: item_models[path] = model
            elif classname not in NOT_IN_BASE: missing = [f'model {path} ({classname})']
            unresolved += [item for item in missing if item not in unresolved]
        if paths[0] in item_models:
            # Spawnflag 1 hangs the item where it is; any other is dropped to the floor (g_items.c FinishSpawningItem).
            items.append(dict(kind=kind, origin=vector(thing.get('origin', '')), models=[path for path in paths if path in item_models],
                              suspended=bool(int(numbers([thing.get('spawnflags', '0')], 1)[0]) & 1)))
    file = 'bsp/' + hashlib.sha256(raw).hexdigest()[:20] + '.bsp'
    if not (output / file).exists():
        write_bsp(output / file, lumps)
    folder = output / 'maps' / ident
    folder.mkdir(parents=True, exist_ok=True)
    scene = dict(name=name, longname=game.arenas.get(name.lower(), ''), source=source, bsp=file, shaders=shaders, viewpoints=viewpoints,
                 items=items, itemModels=item_models,
                 **{kind: list(dict.fromkeys(item for shader in [*shaders, *(shader for model in item_models.values() for shader in model['shaders'])]
                                             if shader for item in shader.get(kind, []))) for kind in ('outside', 'guessed')},
                 models=[dict(model=int(thing['model'][1:]), origin=vector(thing['origin'])) for thing in things
                         if re.fullmatch(r'\*\d+', thing.get('model', '')) and 'origin' in thing],
                 unresolved=unresolved, limits=notes)
    (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
    return scene


def import_maps(game, output, replace=False, extra=None):
    """Import every map of the Quake 3 folder `game`. Returns imported, skipped and failed maps and, for
    each imported map with any, what it names that the game files do not hold and what the extras filled in."""
    game, output = Path(game).expanduser(), Path(output)
    extra = Path(extra) if extra else output.parent / 'q3-extra'
    extras = [extra] if extra.is_dir() else []
    if not game.is_dir():
        raise ValueError('Quake 3 folder does not exist')
    if game.name.lower() == 'baseq3':
        game = game.parent
    base = next((folder for folder in game.iterdir() if folder.is_dir() and folder.name.lower() == 'baseq3'), None)
    if not base or not any(base.glob('pak*.pk3')):
        raise ValueError('No baseq3 folder with pak0.pk3 here. Enter the Quake 3 Arena folder.')
    for folder in ('bsp', 'models'):
        (output / folder).mkdir(parents=True, exist_ok=True)
    textures = Textures(output / 'textures')
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={}, unresolved={}, outside={}, guessed={})
    folders = [base] + sorted(folder for folder in game.iterdir() if folder.is_dir() and folder != base and any(folder.glob('*.pk3')))
    for folder in folders:
        mounted = Game([folder] if folder == base else [folder, base], extras)
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
                if scene['outside']: result['outside'][name] = scene['outside']
                if scene['guessed']: result['guessed'][name] = scene['guessed']
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
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'q3-maps')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--extra', type=Path, help='folder of files the game lacks (default: q3-extra beside the pack)')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace, args.extra)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    for name, items in done['unresolved'].items(): print('UNRESOLVED', name, '; '.join(items))
    for name, items in done['outside'].items(): print('FROM EXTRAS', name, '; '.join(items))
    for name, items in done['guessed'].items(): print('GUESSED', name, '; '.join(items))
