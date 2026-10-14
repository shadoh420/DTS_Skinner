"""Static IronSphere preview data. Never evaluate RPG scripts or overlay stock missions."""
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

MISSION = 'T2RPG_WorldMap'
MISSION_PATH = 'missions/t2rpg_worldmap.mis'
PREFIX = 'mods/ironsphere/'


def decode(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252')
    return text.replace('\r\r\n', '\n').replace('\r\n', '\n')


def uncomment(text):
    # Preserve strings (including URLs) and source offsets for audit IDs.
    return re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else re.sub(r'[^\n]', ' ', m[0]), text)


def entity_id(source, text, offset):
    return f'{source}:{text[:offset].count(chr(10)) + 1}:{offset - text.rfind(chr(10), 0, offset)}'


def fields(text):
    return {m[1].lower(): m[2] if m[2] is not None else m[3].strip()
            for m in re.finditer(r'(\w+)\s*=\s*(?:"([^"\n]*)"|([^;{}]+))\s*;', text)}


def datablocks(scripts):
    definitions = {}
    for text in scripts.values():
        for m in re.finditer(r'datablock\s+\w+\s*\(\s*(\w+)\s*(?::\s*(\w+))?\s*\)\s*(?::\s*(\w+))?\s*\{([^{}]*)\}', uncomment(text), re.I):
            definitions[m[1].lower()] = (m[2] or m[3], fields(m[4]))

    def resolve(name, seen=()):
        key = name.lower()
        if key not in definitions or key in seen:
            return {}
        parent, own = definitions[key]
        return {**(resolve(parent, (*seen, key)) if parent else {}), **own}
    return resolve


def npc_entries(mission, scripts):
    """Literal placed actors and DefineTownBots calls only; dynamic populations are omitted."""
    resolve = datablocks({**scripts, MISSION_PATH: mission})
    result, notes = [], []

    def add(entity, classname, db, position, rotation, scale='1 1 1', skin='base', name=''):
        shape = resolve(db).get('shapefile')
        try:
            pos, rot, size = ([float(n) for n in v.split()] for v in (position, rotation, scale))
            if len(pos) != 3 or len(rot) != 4 or len(size) != 3 or not all(math.isfinite(n) for n in pos + rot + size):
                raise ValueError()
            if not shape or any(c in shape for c in '%$@'):
                raise ValueError()
        except ValueError:
            notes.append('Unresolved NPC: ' + entity)
            return
        result.append(dict(npc=True, entity=entity, classname=classname, datablock=db,
                           name=name, shape=shape, skin=skin, position=pos, rotation=rot,
                           scale=size, pose='root'))

    text = uncomment(mission)
    for m in re.finditer(r'new\s+(AIPlayer|Player)\s*\(([^)]*)\)\s*\{([^{}]*)\}', text, re.I):
        f = fields(m[3])
        add(entity_id(MISSION_PATH, text, m.start()), m[1], f.get('datablock', ''),
            f.get('position', ''), f.get('rotation', '0 0 1 0'), f.get('scale', '1 1 1'),
            f.get('skinname', resolve(f.get('datablock', '')).get('skinname', 'base')), m[2])

    source = 'missions/t2rpg_worldmap.cs'
    text = uncomment(scripts.get(source, ''))
    # This function is a literal list, not a general Torque interpreter. Reject control flow
    # instead of accidentally importing calls from an unexecuted branch or loop.
    body = re.search(r'function\s+DefineTownBots\s*\(\s*\)\s*\{([^{}]*)\}', text, re.I)
    if body and re.search(r'\b(?:if|for|while|switch)\s*\(', re.sub(r'"[^"\n]*"', '', body[1]), re.I):
        body = None
    if body:
        for m in re.finditer(r'\b(spawnTownBot|spawnquestbot)\s*\(([^;]*)\)\s*;', body[1], re.I):
            args = re.findall(r'"([^"\n]*)"|([^,\s]+)', m[2])
            args = [a or b for a, b in args]
            entity = entity_id(source, text, body.start(1) + m.start())
            if len(args) != 6 or any(c in ''.join(args) for c in '%$@'):
                notes.append('Nonliteral town NPC omitted: ' + entity)
                continue
            transform = args[0].split()
            if len(transform) != 7:
                notes.append('Invalid town NPC transform: ' + entity)
                continue
            # rpgspawns.cs accepts only these two armors (Goblin aliases Orc).
            race = args[3] if m[1].lower() == 'spawntownbot' else 'MaleHuman'
            db = 'MaleOrcArmor' if race.lower() in ('maleorc', 'goblin') else 'MaleHumanArmor'
            try:
                rotation = ' '.join(transform[3:6]) + ' ' + str(math.degrees(float(transform[6])))
            except ValueError:
                notes.append('Invalid town NPC rotation: ' + entity)
                continue
            add(entity, 'Player', db, ' '.join(transform[:3]), rotation,
                name=args[5] if m[1].lower() == 'spawntownbot' else args[4])
    elif re.search(r'\bDefineTownBots\s*\(', text, re.I):
        notes.append('DefineTownBots has nonliteral control flow; town NPCs omitted')
    spawners = sum(len(re.findall(r'\bcreateAISpawn\s*\(', uncomment(t), re.I))
                   for p, t in scripts.items() if p.startswith('missions/'))
    if spawners:
        notes.append(f'{spawners} AI spawn sites omitted: proximity, random selection, respawn and quest state require gameplay')
    return result, notes


def add_ironsphere(staging, resources, missions, archive_path, loose_root=None):
    """Add one isolated resource namespace; return manifest overlay and provenance."""
    archive_path = Path(archive_path)
    files, scripts, provenance = {}, {}, {'archive': str(archive_path), 'files': []}
    if loose_root:
        root = Path(loose_root)
        if not root.is_dir():
            raise ValueError('IronSphere folder not found: ' + str(root))
        provenance['root'] = str(root)
        # Visual assets only. Scripts are read statically, never published into the VM's
        # scripts/ search path. No loose file is made available to a base mission.
        for folder in ('shapes', 'textures', 'interiors', 'characters', 'missions', 'scripts'):
            for file in sorted((root / folder).rglob('*')):
                if not file.is_file():
                    continue
                if not file.resolve().is_relative_to(root.resolve()):
                    raise ValueError('IronSphere input escapes folder: ' + str(file))
                path = file.relative_to(root).as_posix().lower()
                ext = file.suffix.lower()
                if ext == '.cs':
                    data = file.read_bytes()
                    scripts[path] = decode(data)
                elif folder in ('shapes', 'textures', 'interiors', 'characters') and ext in {'.dts', '.dsq', '.dif', '.png', '.jpg', '.jpeg', '.bmp', '.ifl', '.dml'}:
                    data = file.read_bytes()
                    files[path] = data
                else:
                    continue
                provenance['files'].append({'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
    with archive_path.open('rb') as stream:
        provenance['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename.replace('\\', '/').lower())
            if path.is_absolute() or '..' in path.parts or ':' in str(path):
                raise ValueError('Unsafe IronSphere archive path: ' + entry.filename)
            if not entry.is_dir() and (str(path) == MISSION_PATH or path.parts[0] in ('terrains', 'textures', 'shapes', 'interiors')):
                files[str(path)] = archive.read(entry)
    if MISSION_PATH not in files:
        raise ValueError('IronSphere archive has no ' + MISSION_PATH)
    mission = decode(files[MISSION_PATH])
    npcs, notes = npc_entries(mission, scripts)
    if not loose_root:
        notes.append('IronSphere scripts and custom assets not supplied; fixed town NPCs unavailable')
    resolve = datablocks(scripts)
    # Preserve the original mission, adding only visual datablocks needed by its scenery.
    declared = set(n.lower() for n in re.findall(r'datablock\s+\w+\s*\(\s*(\w+)', mission, re.I))
    for name in sorted(set(re.findall(r'\bdataBlock\s*=\s*"?(\w+)"?\s*;', uncomment(mission), re.I))):
        shape = resolve(name).get('shapefile')
        if shape and not any(c in shape for c in '%$@') and name.lower() not in declared:
            mission = f'datablock StaticShapeData({name}) {{ shapeFile = "{shape}"; }};\n' + mission
    files[MISSION_PATH] = mission.encode('utf-8')
    overlay = {'resources': {}, 'npcs': [], 'notes': notes}
    for path, data in sorted(files.items()):
        target = staging / 'base' / PREFIX / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        overlay['resources'][path] = [PREFIX + path, ['']]
    available = {**resources, **overlay['resources']}
    for npc in npcs:
        shape = npc['shape'].lower().removeprefix('shapes/')
        npc['shape'] = shape
        if 'shapes/' + shape not in available:
            notes.append('Missing NPC shape: ' + npc['shape'] + ' (' + npc['entity'] + ')')
            continue
        # ShapeLoader discovers stem_*.dsq. Alias the constructor's root sequence only,
        # preserving the stock root pose rather than rendering the DTS bind pose.
        constructor = scripts.get('shapes/' + shape.removesuffix('.dts') + '.cs', '')
        root = re.search(r'sequence\d+\s*=\s*"([^" ]+\.dsq)\s+root"', uncomment(constructor), re.I)
        if root:
            sequence = 'shapes/' + root[1].lower().removeprefix('shapes/')
            if sequence in available:
                overlay['resources']['shapes/' + shape.removesuffix('.dts') + '_root.dsq'] = available[sequence]
            else:
                notes.append('Missing NPC standing sequence: ' + sequence)
                continue
        overlay['npcs'].append(npc)
    display = re.search(r'^//\s*DisplayName\s*=\s*(.+)$', mission, re.M | re.I)
    missions[MISSION] = dict(resourcePath=MISSION_PATH, displayName=display[1].strip() if display else None,
                             missionTypes=['T2RPG'], source='ironsphere.vl2')
    provenance['policy'] = 'Only T2RPG_WorldMap uses these resources. Visual files and literal town/quest placements; RPG scripts are not executed.'
    return overlay, provenance
