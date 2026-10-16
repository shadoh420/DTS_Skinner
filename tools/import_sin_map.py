"""SiN RBSP1 maps, static spawn models and brush entities.

python -m tools.import_sin_map --install "C:/Program Files (x86)/Steam/steamapps/common/SiN 1" --game all --replace
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

import numpy as np
from PIL import Image

from tools import import_sin as sin, import_quake2_map as q2
from tools.import_anachronox_map import checker, image_name, number
from tools.import_daikatana_map import hull_floor, light_point
from tools.import_quake_map import SCALE, floor_below, rotation, vector
from tools.local_data import LOCAL_DATA

LUMPS = ('entities', 'planes', 'vertices', 'visibility', 'nodes', 'texinfo', 'faces', 'lighting',
         'leaves', 'leaffaces', 'leafbrushes', 'edges', 'surfedges', 'models', 'brushes',
         'brushsides', 'pop', 'areas', 'areaportals', 'lightinfo')
FORMATS = {1: '4fi', 2: '3f', 4: '3i6h2H', 5: '8fi64sifii8f32s', 6: 'Hhihh16Bii',
           8: 'ihh6h4H', 9: 'H', 10: 'H', 11: '2H', 12: 'i', 13: '9f3i',
           14: '3i', 15: 'Hhi', 17: '2i', 18: '2i', 19: 'i6f32s'}
MASKED, SKY, WARP, NONLIT, NOFILTER, CONVEYOR, NODRAW, WAVY = 2, 4, 8, 16, 32, 64, 128, 0x400
TRANSLATE, USECOLOR, ADD, ANIMATE = 0x2000000, 0x8000, 0x100000, 0x800000  # qbsp3.exe surface-flag table: translate, usecolor, add, animate
VIEWHEIGHT = 66  # gamex86 1009565D / 10098FB1: Player viewheight = 0x42.


def lighting_profile(install=None, files=None, game='sin'):
    """Fresh true-colour OpenGL profile; parse numeric cvars, never execute config."""
    values = dict(vid_gamma=1., intensity=1., gl_modulate=1.)
    sources, warnings = [], []
    if install is not None:
        root = sin.base_folder(install)
        roots = [root] if game == 'sin' else [root, root.parent / game]
        # FS player default is blade. CL_WriteConfiguration uses gamedir/players/player/config.cfg.
        for name in ('default.cfg', 'players/blade/config.cfg', 'autoexec.cfg'):
            raw, source = None, ''
            if files and name in files:
                raw, source = sin.read_asset(files[name], max_bytes=1024 * 1024), files[name]['pak'] + ':' + name
            archive_game = files[name]['game'] if files and name in files else 'sin'
            for folder in roots:
                if archive_game != 'sin' and folder == root:
                    continue  # Base loose files cannot override an expansion archive.
                path = folder / name
                if path.is_file() and path.resolve().is_relative_to(folder.resolve()):
                    if path.stat().st_size > 1024 * 1024:
                        raise ValueError('Config exceeds text limit')
                    raw, source = path.read_bytes(), str(path)
            if raw is None:
                continue
            sources.append(source)
            for line in raw.decode('latin1').splitlines():
                words = re.findall(r'"[^"]*"|[^\s;]+', line.split('//', 1)[0])
                words = [w.strip('"') for w in words]
                if words and words[0].lower() in ('set', 'seta'):
                    words = words[1:]
                if len(words) >= 2 and words[0].lower() in values:
                    value = float(words[1])
                    if not math.isfinite(value) or not 0 < value <= 16:
                        raise ValueError('Invalid lighting cvar: ' + ' '.join(words))
                    values[words[0].lower()] = value
                elif words and words[0].lower() in ('exec', 'player'):
                    warnings.append('Additional config/profile command not executed: ' + ' '.join(words))
    values['intensity'] = max(1., values['intensity'])
    return dict(values, renderer='SiN ref_gl true-colour OpenGL', display_gamma=1, normal_style=1,
                luxel_scale=16, light_channels=3, light_styles=16, overbright=1,
                palette_mode='SWL local palette; no shared-palette extension quantization',
                config_sources=sources, warnings=warnings)


def upload_image(image, profile, mipmapped=True):
    # ref_gl 10008E61/10008EF0: gamma[intensity[c]] for mipmapped images.
    pixels = np.array(image.convert('RGBA'))
    values = np.arange(256, dtype=np.float64)
    gamma = profile['vid_gamma']
    table = values.astype(np.uint8) if gamma == 1 else np.clip(
        255 * ((values + .5) / 255.5) ** gamma + .5, 0, 255).astype(np.uint8)
    intensity = np.minimum((values * profile['intensity']).astype(int), 255) if mipmapped else np.arange(256)
    pixels[:, :, :3] = table[intensity[pixels[:, :, :3]]]
    return Image.fromarray(pixels)


def texture(name, files, profile):
    base = name if name.startswith(('textures/', 'env/', 'models/')) else 'textures/' + name
    candidates = [base] if base.endswith(('.swl', '.tga')) else [base + '.swl', base + '.tga']
    if candidates[0].endswith('.swl'):
        candidates += [candidates[0][:-4] + '.tga']
    source = next((p for p in candidates if p in files), '')
    reason = ''
    try:
        if not source:
            raise ValueError('Missing texture: ' + ', '.join(dict.fromkeys(candidates)))
        image = upload_image(sin.read_texture(source, files), profile, not base.startswith('env/'))
    except (ValueError, OSError) as exc:
        image, reason = checker(), str(exc)
    return dict(name=name, source=source, image=image, pixels=image.tobytes(), width=image.width,
                height=image.height, missing=bool(reason), reason=reason)


def validate_collision(bsp):
    nodes, leaves, planes = bsp['nodes'], bsp['leaves'], bsp['planes']
    for row in nodes:
        if not 0 <= row[0] < len(planes) or any(n >= len(nodes) or -n - 1 >= len(leaves) for n in row[1:3]):
            raise ValueError('Invalid RBSP node reference')
        if row[9] + row[10] > len(bsp['faces']):
            raise ValueError('Invalid RBSP node faces')
    visited, active = set(), set()
    for root in range(len(nodes)):
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                active.remove(node); visited.add(node)
            elif node >= 0 and node not in visited:
                if node in active:
                    raise ValueError('Cyclic RBSP nodes')
                active.add(node); stack.append((node, True))
                stack.extend((child, False) for child in nodes[node][1:3])
    if nodes and any(m[9] >= len(nodes) or -m[9] - 1 >= len(leaves) for m in bsp['models']):
        raise ValueError('Invalid RBSP model headnode')
    for row in leaves:
        if row[11] + row[12] > len(bsp['leafbrushes']) or row[9] + row[10] > len(bsp['leaffaces']):
            raise ValueError('Invalid RBSP leaf range')
    if any(i >= len(bsp['brushes']) for i in bsp['leafbrushes']) or any(i >= len(bsp['faces']) for i in bsp['leaffaces']):
        raise ValueError('Invalid RBSP leaf index')
    for first, count, _ in bsp['brushes']:
        if min(first, count) < 0 or first + count > len(bsp['brushsides']):
            raise ValueError('Invalid RBSP brush range')
    if any(p >= len(planes) or t < -1 or t >= len(bsp['texinfo']) for p, t in bsp['brushsides']):
        raise ValueError('Invalid RBSP brush side')


def read_bsp(raw, files, profile=None):
    profile = profile or lighting_profile()
    if len(raw) < 168 or raw[:8] != b'RBSP\x01\x00\x00\x00':
        raise ValueError('Expected RBSP version 1')
    ranges = list(struct.iter_unpack('<ii', raw[8:168]))
    if any(o < 168 or n < 0 or o + n > len(raw) for o, n in ranges):
        raise ValueError('Invalid RBSP lump range')
    occupied = sorted((o, o + n) for o, n in ranges if n)
    if any(b > c for (_, b), (c, _) in zip(occupied, occupied[1:])):
        raise ValueError('Overlapping RBSP lumps')
    rows = {}
    for i, fmt in FORMATS.items():
        o, n = ranges[i]
        if n % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete RBSP {LUMPS[i]} lump')
        rows[i] = list(struct.iter_unpack('<' + fmt, raw[o:o + n]))
    bsp = q2.read_bsp(raw, files, texture_loader=lambda n: texture(n, files, profile),
                      header=(b'RBSP', 1, 20), face_format=FORMATS[6],
                      texinfo_format=FORMATS[5], texinfo_fields=(9, 10))
    for key, i in (('nodes', 4), ('leaves', 8), ('brushes', 14), ('lightinfo', 19)):
        bsp[key] = rows[i]
    bsp.update(leaffaces=[r[0] for r in rows[9]], leafbrushes=[r[0] for r in rows[10]],
               brushsides=[r[:2] for r in rows[15]], brushside_lightinfo=[r[2] for r in rows[15]],
               surfaces=rows[5], lump_sizes=dict(zip(LUMPS, (n for _, n in ranges))),
               info=next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {}),
               profile=profile, rerelease=True)  # Shared helper's exact float32 texture-space arithmetic.
    for row in rows[5]:
        if not all(math.isfinite(v) for v in (row[11], *row[14:22])):
            raise ValueError('Non-finite RBSP surface properties')
    validate_collision(bsp)
    return bsp


def face_data(bsp, index):
    face = q2.face_data(bsp, index)
    ti = bsp['faces'][index][4]
    row, flags = bsp['surfaces'][ti], bsp['texinfo'][ti][9]
    translucence = row[16]  # byte offset 124, csurface_t +0x58.
    kind = 'sky' if flags & SKY else 'hidden' if flags & NODRAW else 'turbulent' if flags & (WARP | WAVY) else 'normal'
    face.update(kind=kind, unlit=bool(flags & (SKY | WARP | WAVY | NONLIT | ADD) or translucence > 0),
                # ref_gl supplies 1-translucence to glColor4f; GL clamps its alpha to [0,1].
                alpha=1 if flags & ADD else max(0., min(1., 1 - translucence)), styles=bsp['faces'][index][5:21], lightofs=bsp['faces'][index][21],
                material_key=ti, settings=dict(masked=bool(flags & MASKED and not flags & ADD), nofilter=bool(flags & NOFILTER),
                    nonlit=1 if flags & ADD else row[15] if flags & NONLIT else 1 / bsp['profile']['intensity'],
                    blend='add' if flags & ADD else '', flowing=bool(flags & TRANSLATE),
                    trans=[row[11], row[12]], color=list(row[19:22]) if flags & ADD or flags & USECOLOR and flags & NONLIT else [1, 1, 1]))
    return face


def frame_rate(bsp, instance, info):
    """Texinfo row: vecs, flags, texture, nexttexinfo, trans_mag 11, trans_angle 12, base_angle 13, animtime 14,
    nonlit, translucence, friction, restitution, color, group (qbsp3.exe field table; animtime is 0.2 on 8166 of 8308
    chained texinfos). Only ANIMATE chains run by themselves (SDK docs/node/surfacecom.htm `frame`: unchecking animate
    stops it); unflagged chains are damage and script frames. Hypothesis: one frame every animtime seconds, from the
    face's own texinfo, for world and brush entities alike."""
    animtime = bsp['surfaces'][info][14]
    return (1 / animtime, 0) if bsp['texinfo'][info][9] & ANIMATE and animtime > 0 else None


def lightmap(face, lighting, dark, modulate=1):
    if modulate == 1:
        return q2.lightmap_rgb(face, lighting, dark)
    # Sum before modulation/normalization; do not modulate an already-clipped tile.
    if face['unlit'] or face['lightofs'] == -1 or not lighting:
        return q2.lightmap_rgb(face, lighting, dark)
    w, h = map(int, face['size'])
    if min(w, h) <= 0 or w * h > 1_000_000:
        raise ValueError('Invalid SiN lightmap extent')
    at, total = face['lightofs'], np.zeros((h, w, 3), np.float32)
    for style in face['styles']:
        if style == 255: break
        size = w * h * 3
        if at < 0 or at + size > len(lighting):
            raise ValueError('SiN RGB lightmap exceeds lighting lump')
        if style not in dark:
            total += np.frombuffer(lighting, np.uint8, size, at).reshape(h, w, 3) * np.float32(modulate)
        at += size
    total = np.maximum(total, 0).astype(int).astype(np.float32)
    maximum = total.max(axis=2)
    bright = maximum > 255
    total[bright] *= (255 / maximum[bright])[:, None]
    return total.astype(np.uint8)


def deathmatch(bsp, game, files=None, name=''):
    # Retail DM maps also have SP starts. Their authored map-info menus identify the mode.
    menu = f'maps/{name}.mnu'
    if files and menu in files:
        text = sin.read_asset(files[menu], max_bytes=64 * 1024).decode('latin1')
        if re.search(r'^\s*menulevel\s+dm_mapinfo', text, re.I | re.M):
            return True
    return game == 'ctf' or (not any(e.get('classname', '').lower() == 'info_player_start' for e in bsp['entities'])
                            and any(e.get('classname', '').lower() == 'info_player_deathmatch' for e in bsp['entities']))


def excluded(entity, dm=False):
    flags = int(number(entity, 'spawnflags'))
    # base gamex86 100783AE..10078463: medium SP, coop off, developer off, high detail.
    return bool(flags & (0x800 if dm else 0x200 | 0x2000))


def light_style(entity):
    # Light constructor 1008241F..10082445 uses atoi, not a float or pattern parser.
    match = re.match(r'\s*([+-]?[0-9]+)', entity.get('style', ''))
    return int(match[1]) if match else 0


def startup_visibility(text, entities):
    """Read only visibility before each startup thread's first yield, never run game commands.

    Literal targets and numeric setup loops suffice for common map initializers. Stop a
    thread at unknown control flow; runtime triggers, external scripts and motion stay static.
    """
    clean = re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\r\n]*',
                   lambda m: m[0] if m[0].startswith('"') else '\n' * m[0].count('\n'), text)
    lines = [(i + 1, [t.strip('"') for t in re.findall(r'"(?:\\.|[^"\\])*"|[^\s]+', line)])
             for i, line in enumerate(clean.splitlines()) if line.strip()]
    labels = {words[0][:-1]: i + 1 for i, (_, words) in enumerate(lines) if words[0].endswith(':')}
    visible, shared, limits = {}, {}, set()
    budget = 4096

    def run(at, depth=0):
        nonlocal budget
        local = {}

        def value(token):
            if token.startswith(('local.', 'parm.', 'level.', 'game.')):
                return (local if token.startswith('local.') else shared).get(token)
            try: return float(token)
            except ValueError: return token

        while at < len(lines):
            line, words = lines[at]; at += 1; budget -= 1
            if budget < 0 or depth > 16:
                limits.add('Startup visibility instruction/depth limit'); return
            command = words[0].lower()
            if command.endswith(':'): continue
            if len(words) > 1 and words[1].lower().startswith('if'):
                op = words[1].lower()
                a, b = value(words[0]), value(words[2]) if len(words) > 2 else None
                if op not in ('ifequal', 'ifnotequal', 'ifgreater', 'ifless') or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                    limits.add(f'Unresolved startup condition at line {line}'); return
                condition = {'ifequal': a == b, 'ifnotequal': a != b, 'ifgreater': a > b, 'ifless': a < b}[op]
                if not condition: continue
                words = words[3:]
                if not words: return
                command = words[0].lower()
            if command.startswith('wait') or command in ('pause', 'end'):
                return
            if command in ('goto', 'thread'):
                label = words[1] if len(words) == 2 else ''
                if label not in labels:
                    limits.add(f'Unresolved startup {command} at line {line}')
                    if command == 'goto': return
                elif command == 'goto': at = labels[label]
                else: run(labels[label], depth + 1)
            elif len(words) == 2 and words[1].lower() in ('hide', 'show', 'remove'):
                target = value(words[0])
                if not isinstance(target, str) or not target.startswith('$'):
                    limits.add(f'Unresolved visibility target at line {line}'); continue
                for i, e in enumerate(entities):
                    if target == '$' + e.get('targetname', '') and visible.get(i, ('', 0))[0] != 'remove':
                        visible[i] = (words[1].lower(), line)
            elif len(words) >= 2 and words[0].startswith(('local.', 'parm.', 'level.', 'game.')):
                variables = local if words[0].startswith('local.') else shared
                op = words[1].lower(); rhs = value(words[2]) if len(words) == 3 else None
                if op == '=': variables[words[0]] = rhs
                elif op in ('+=', '-=') and isinstance(rhs, (int, float)) and isinstance(value(words[0]), (int, float)):
                    variables[words[0]] = value(words[0]) + rhs * (1 if op == '+=' else -1)
                else: variables.pop(words[0], None)
    run(0)
    return {i: f'Startup script {op} at line {line}' for i, (op, line) in visible.items() if op != 'show'}, sorted(limits)


def script_visibility(bsp, name, files):
    source = sin.checked_name(bsp['info'].get('script') or f'maps/{name}.scr')
    if source not in files: return {}, dict(source=source, found=False)
    hidden, limits = startup_visibility(sin.read_asset(files[source], max_bytes=1024 * 1024).decode('latin1'), bsp['entities'])
    return hidden, dict(source=source, found=True, hidden=hidden, limits=limits)


def instances(bsp, dm=False):
    result = [dict(model=0, entity=0, classname='worldspawn', offset=[0., 0., 0.], angles=[0., 0., 0.], state='world', guess=False)]
    omitted = []
    for i, e in enumerate(bsp['entities']):
        model, cls = e.get('model', ''), e.get('classname', '').lower()
        if not model.startswith('*'): continue
        row = dict(entity=i, classname=e.get('classname', ''), model=model)
        reason = bsp.get('script_hidden', {}).get(i, '') or ('Excluded by deathmatch/medium single-player spawnflags' if excluded(e, dm) else '')
        if cls.startswith('trigger_') or cls in ('func_areaportal', 'func_scriptorigin', 'func_clipbox', 'func_remove'):
            reason = reason or 'Invisible control/collision brush'
        if reason:
            omitted.append(dict(row, reason=reason)); continue
        ident = int(model[1:])
        if not 0 < ident < len(bsp['models']):
            omitted.append(dict(row, reason='Invalid inline model index')); continue
        origin, turn = vector(e), q2.angles(e)
        state, guess = 'Authored spawn transform; scripts and movers not advanced', cls.startswith('func_script')
        if cls == 'func_door':
            turn = np.zeros(3)  # angle is movement direction, not brush rotation.
            state = 'Sliding door closed endpoint'
            if int(number(e, 'spawnflags')) & 1:
                size = np.array(bsp['models'][ident][3:6]) - np.array(bsp['models'][ident][:3]) + 2
                direction = q2.movedir(e)
                origin += direction * (abs(direction @ size) - number(e, 'lip', 8))
                state = 'START_OPEN sliding endpoint; constructor schedules initial open event'
        elif cls == 'func_rotatingdoor':
            state = 'Rotating door closed angles'
            if int(number(e, 'spawnflags')) & 1:
                turn[1] += number(e, 'openangle', 90) * (1 if int(number(e, 'spawnflags')) & 2 else -1)
                state = 'START_OPEN yaw endpoint; no-activator direction, reverse flag honored'
        result.append(dict(row, model=ident, offset=origin.tolist(), angles=turn.tolist(), state=state, guess=guess))
    return result, omitted


def model_path(named):
    name = sin.checked_name(named)
    return name if name.startswith('models/') else 'models/' + name


def scenery(bsp, dm=False):
    drawn, omitted = [], []
    for i, e in enumerate(bsp['entities']):
        cls, model = e.get('classname', ''), e.get('model', '')
        if cls.lower() == 'worldspawn' or model.startswith('*'): continue
        row = dict(entity=i, classname=cls)
        reason = bsp.get('script_hidden', {}).get(i, '') or ('Excluded by deathmatch/medium single-player spawnflags' if excluded(e, dm) else '')
        if cls.lower().startswith(('trigger_', 'info_', 'light', 'path_', 'fx_', 'func_spawn', 'func_remove')) or cls.lower() in (
                'waypoint', 'lensflare', 'func_camera', 'func_scriptorigin', 'func_beam', 'func_exploder', 'func_multi_exploder'):
            reason = reason or 'Control, delayed spawn, light, particle or camera entity; no initial polygon model'
        elif not model:
            reason = reason or 'No explicit spawn model; class constructor model is not inferred'
        if reason:
            omitted.append(dict(row, reason=reason)); continue
        try:
            path = model_path(model)
            if not path.endswith(('.def', '.sbm')):
                raise ValueError('Unsupported spawn representation (not DEF/SBM)')
            scale = number(e, 'scale', 1)
            if scale <= 0: raise ValueError('Invalid entity scale')
            drawn.append(dict(row, path=path, origin=vector(e).tolist(), angles=q2.angles(e).tolist(), scale=scale,
                              alpha=max(0, min(1, number(e, 'alpha', 1))), anim=e.get('anim', ''),
                              state='First pose at authored spawn spot; no gravity, AI or script execution'))
        except (ValueError, IndexError) as exc:
            omitted.append(dict(row, reason=str(exc)))
    return drawn, omitted


def model_is_npc(item, definition):
    """DEF spawn class, with placed-class fallback for raw SBMs; never infer from a skin."""
    cls, path = item['classname'].lower(), item['path'].lower()
    spawn = definition.get('classname', '').lower()
    # Lamprey/Manero's Monster_HelicopterBoss is a DEF vehicle, not an Actor.
    # Reconah is an Actor warrior, not a helicopter. Functional turrets are actors;
    # driver-operated CTF turrets remain vehicles. Explicit corpses/gibs stay scenery.
    if (spawn in ('vehicle', 'drivablevehicle', 'ctfdrivableturret', 'hoverbike')
            or cls.startswith('vehicles_') or cls == 'monster_helicopterboss'):
        return False
    if re.search(r'(?:^|[/_])(?:dead\w*|corpse\w*|gib\w*|mauled|chop\d*)(?:[/_.]|$)', cls + '/' + path):
        return False
    return (spawn in {'actor', 'bacrodai', 'bat', 'fish', 'rat', 'eonandpeon', 'peon', 'thrallmaster',
                      'bob', 'mortician', 'crawler', 'goliath', 'maneroboss', 'splitter', 'ctfturret'}
            or cls.startswith(('monster_', 'animals_', 'boss_', 'hero_', 'neutral_', 'innocent_'))
            or cls in ('trap_securityturret', 'trap_rocketturret', 'ctf_turret'))


def append_models(data, bsp, files, placements):
    images, cache, failures = {}, {}, []
    points, uvs, uv2 = [data['points']], [data['uvs']], [data['uv2']]
    count = len(data['points'])
    for item in placements:
        try:
            key = item['path'], item['anim']
            if key not in cache:
                path = item['path']
                if path not in files: raise ValueError('Spawn model absent: ' + path)
                definition = sin.read_def(sin.read_asset(files[path]), path) if path.endswith('.def') else dict(
                    basemodel=path, skins=[], animations=[], surfaces={}, scale=1., origin=[0, 0, 0],
                    warnings=['Raw SBM has no authored DEF defaults'])
                requested = [a for a in definition['animations'] if a['name'].lower() == item['anim'].lower()]
                if requested:
                    definition = dict(definition, animations=requested)
                base = definition['basemodel']
                if base not in files: raise ValueError('Missing SBM: ' + base)
                mesh = sin.read_sbm(sin.read_asset(files[base]))
                pose, animation, _ = sin.choose_animation(definition, mesh, files)
                mesh.update(pose)
                skins = sin.resolve_skins(definition, mesh, files)
                for skin in skins:
                    image, errors, resolved = checker(), [], ''
                    for source in skin['candidates']:
                        if source not in files: continue
                        try:
                            image = upload_image(sin.read_texture(source, files), bsp['profile'])
                            resolved = source; break
                        except (ValueError, OSError) as exc:
                            errors.append(dict(source=source, reason=str(exc)))
                    png = image_name(image, 'model_'); images[png] = image
                    skin.update(texture=png, source=resolved, missing=not resolved, decode_failures=errors)
                cache[key] = sin.build_model(mesh, definition, skins), skins, animation, definition
            model, skins, animation, definition = cache[key]
            item['npc'] = model_is_npc(item, definition)
            local = np.asarray(model['vertices']).reshape(-1, 3)[:, [2, 0, 1]]
            placed = local * item['scale'] @ rotation(item['angles']).T + item['origin']
            light = light_point(bsp, np.array(item['origin']), data['dark'], face_reader=face_data) if bsp['nodes'] else np.ones(3)
            light = np.clip(light * bsp['profile']['gl_modulate'], .1, 1)
            for group in model['groups']:
                slot = group['materialIndex']; png = model['material_textures'][slot]
                part = model['indices'][group['start']:group['start'] + group['count']]
                settings = dict(model['material_settings'][slot])
                data['groups'].append(dict(texture=png, kind='model', flags=0, size=list(images[png].size), unlit=True,
                    npc=item['npc'], entity=item['entity'], classname=item['classname'],
                    alpha=item['alpha'] * settings.get('opacity', 1), start=len(data['indices']), count=len(part),
                    settings=settings, model_light=light.tolist()))
                data['indices'].extend(count + i for i in part)
            points.append(placed); uvs.append(np.asarray(model['uvs']).reshape(-1, 2)); uv2.append(np.zeros((len(local), 2)))
            count += len(local)
            item.update(status='drawn', skins=skins, animation=animation, vertices=len(local), warnings=model['metadata']['warnings'])
        except (ValueError, KeyError, OSError, IndexError, struct.error) as exc:
            item.update(status='omitted', reason=str(exc)); failures.append(dict(item))
    data.update(points=np.concatenate(points), uvs=np.concatenate(uvs), uv2=np.concatenate(uv2))
    return images, failures


def viewpoints(bsp, floors, dm=False):
    order = (['info_player_hardcorps', 'info_player_sintek', 'info_player_deathmatch', 'info_player_start'] if dm else
             ['info_player_start', 'info_player_deathmatch', 'info_player_hardcorps', 'info_player_sintek'])
    order += ['info_player_coop', 'info_player_intermission', 'func_camera']
    starts = [e for e in bsp['entities'] if e.get('classname', '').lower() in order and not excluded(e, dm)]
    starts.sort(key=lambda e: (order.index(e['classname'].lower()), bool(e.get('targetname'))))
    views = []
    for e in starts:
        start, turn = vector(e), q2.angles(e); eye = start.copy(); floor = None
        camera = e['classname'].lower() in ('info_player_intermission', 'func_camera')
        if not camera:
            floor = hull_floor(bsp, start, mins=(-16, -16, 0), maxs=(16, 16, 72))
            if floor is None: floor = floor_below(floors, *start)
            eye[2] = (min(start[2], floor) if floor is not None else start[2]) + VIEWHEIGHT
        views.append(dict(origin=(eye[[1, 2, 0]] * SCALE).tolist(), native_origin=eye.tolist(), authored_origin=start.tolist(),
            yaw=-float(turn[1]), pitch=-float(turn[0]), roll=float(turn[2]), floor_z=floor, classname=e['classname']))
    if not views:
        eye = floors[0].mean(axis=0) + [0, 0, VIEWHEIGHT] if len(floors) else np.array(bsp['models'][0][:6]).reshape(2, 3).mean(axis=0)
        views.append(dict(origin=(eye[[1, 2, 0]] * SCALE).tolist(), native_origin=eye.tolist(), yaw=0, pitch=0,
                          classname='fallback_camera', reason='No start; first floor triangle or world bounds center'))
    return views


def water_volume(bsp):
    """Collapse the world BSP to a small wet/dry tree for camera point contents."""
    if not bsp['nodes']:
        return None
    root, nodes, mapped = bsp['models'][0][9], [], {}
    stack = [(root, False)]
    while stack:
        i, ready = stack.pop()
        if i in mapped: continue
        if i < 0:
            contents = bsp['leaves'][-1 - i][0]
            mapped[i] = -2 if contents & 32 and not contents & 9 else -1
        elif not ready:
            stack.extend([(i, True), (bsp['nodes'][i][1], False), (bsp['nodes'][i][2], False)])
        else:
            plane, front, back = bsp['nodes'][i][:3]
            front, back = mapped[front], mapped[back]
            mapped[i] = front if front == back else len(nodes)
            if front != back: nodes.append([*bsp['planes'][plane][:4], front, back])
    if mapped[root] == -1: return None
    # World defaults and contents selection: gamex86 100DF50B..5AB / 1009CBC4..CC07.
    info = bsp['info']
    return dict(root=mapped[root], nodes=nodes,
                color=vector(info, 'watercolor').tolist() if 'watercolor' in info else [0, 0, 1],
                alpha=max(0., min(1., number(info, 'wateralpha', .1))))


def skybox(info, files, profile):
    name = info.get('sky') or 'sky_'
    faces, images, missing = {}, {}, []
    for suffix in ('rt', 'lf', 'bk', 'ft', 'up', 'dn'):
        tex = texture(sin.checked_name(f'env/{name}{suffix}.tga'), files, profile)
        png = image_name(tex['image'], 'sky_'); images[png] = tex['image']
        faces[suffix] = dict(texture=png, source=tex['source'])
        if tex['missing']: missing.append(dict(source=tex['name'], reason=tex['reason']))
    return dict(name=name, rotate=number(info, 'skyrotate'), axis=vector(info, 'skyaxis').tolist() if 'skyaxis' in info else [0, 0, 1],
                faces=faces, missing=missing), images


def build_map(raw, name, files, game='sin', profile=None):
    bsp = read_bsp(raw, files, profile)
    bsp['script_hidden'], script = script_visibility(bsp, name, files)
    dm = deathmatch(bsp, game, files, name)
    placed, skipped = instances(bsp, dm)
    dark = [light_style(e) for e in bsp['entities'] if e.get('classname', '').lower() == 'light'
            and light_style(e) >= 32 and int(number(e, 'spawnflags')) & 1 and not excluded(e, dm)] if not dm else []
    data = q2.geometry(bsp, placed, face_reader=face_data, dark_styles=dark, frames=frame_rate,
                       lightmap_reader=lambda f, l, d: lightmap(f, l, d, bsp['profile']['gl_modulate']))
    views = viewpoints(bsp, data['floors'], dm)
    models, omitted = scenery(bsp, dm)
    images, failures = append_models(data, bsp, files, models)
    sky, skies = skybox(bsp['info'], files, bsp['profile']); images.update(skies)
    images.update({png: tex['image'] for png, tex in data['textures'].items()})
    points = data['points'][:, [1, 2, 0]] * SCALE
    missing = [dict(source=t['name'], reason=t['reason']) for t in bsp['textures'] if t['missing']] + sky['missing']
    missing += [dict(source=s['named'] or m['path'], reason=s['reason'], decode_failures=s['decode_failures'])
                for m in models for s in m.get('skins', []) if s['missing'] or s['decode_failures']]
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(), data['uv2'].astype('<f4').tobytes(),
                     np.asarray(data['indices'], '<u4').tobytes()))
    palette = f'maps/{name}.pal'
    # Styles 1-11 come from global/global0.scr's `lightstyle` lines; this lightmap path multiplies by gl_modulate.
    # Hypothesis: the Quake II 10 Hz step. Underwater tint keeps its own `water` path (sinMaps.viewBlend).
    styles = q2.scene_extras(bsp, data, blends=None, scale=bsp['profile']['gl_modulate'] / 12)
    scene = dict(name=name, title=bsp['info'].get('message', name), game=game, format='sin-rbsp1', **styles,
        vertices=len(points), indices=len(data['indices']), lightmap=list(data['atlas'].size), groups=data['groups'], viewpoints=views,
        bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(), instances=placed, skipped_entities=skipped,
        models=models, omitted_models=omitted, model_failures=failures, faces=data['audit'], skybox=sky, water=water_volume(bsp), script=script,
        lump_sizes=bsp['lump_sizes'], entity_classes=dict(Counter(e.get('classname', '') for e in bsp['entities'])),
        dark_styles=dark, missing=sorted({r['source'] for r in missing}), missing_reasons=missing, lighting=bsp['profile'],
        palette=dict(source=palette if palette in files else 'pics/default.pal', applied=False,
                     reason='Renderer palette tables serve indexed/shared-palette paths; true-colour SWL keeps its own RGB'),
        assumptions=['Deathmatch filtering' if dm else 'Medium single player; developer and coop off, high detail',
                     'ANIMATE texture chains at 1/animtime, TRANSLATE scrolling, sky rotation and light styles 1-11 (one per face, others at m) '
                     'run on page time (rates and directions partly hypotheses)', 'Startup script visibility only; no save-state or runtime scripts'],
        gaps=['Particles, sprites, lens flares, dynamic lights and scripted spawns are omitted',
              'Model lighting is a uniform lightmap sample; skeletal attachments and runtime skins are omitted',
              'Startup visibility stops at waits and unresolved conditions; external scripts and computed targets are not evaluated',
              'Underwater tint uses world water contents; moving liquid brushes and underwater distortion are omitted',
              'Script movers hold authored transforms; later activator-dependent door motion is omitted',
              'Mirrors, environment maps, consoles and animated surface damage are ordinary first-frame surfaces',
              'No world fog path found in the inspected renderer; wavy vertex displacement is approximated by Quake II warp UVs',
              'Script-set light styles (map .scr lightstyle commands) and styles beyond 11 stay at m'])
    return scene, blob, images, data['atlas']


def import_maps(install, output, replace=False, game='sin', map_filter=None):
    files, records, counts = sin.read_install(install, maps=True, game=game)
    output = Path(output) / game
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    report = dict(game=game, imported=[], skipped=[], failed={}, bsp_entries=len(records), archive_counts=counts,
                  results=records, entity_classes={}, brush_classes={}, model_classes={})
    profile, index = lighting_profile(install, files, game), []
    for row in records:
        if row['status'] != 'pending': continue
        name = row['source']
        if not name.startswith('maps/') or map_filter and map_filter.lower() not in name:
            row.update(status='skipped', reason='Outside maps/ or excluded by requested filter'); report['skipped'].append(name); continue
        ident = sin.safe(name.removeprefix('maps/').removesuffix('.bsp'))
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                row.update(status='skipped', reason='Already imported; use --replace'); report['skipped'].append(name)
            else:
                scene, blob, images, atlas = build_map(sin.read_asset(files[name]), ident, files, game, profile)
                for png, image in images.items():
                    target = output / 'textures' / png
                    if replace or not target.is_file(): image.save(target)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'geometry.bin').write_bytes(blob); atlas.save(folder / 'lightmap.png')
                (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':'), allow_nan=False), encoding='utf-8')
                row['status'] = 'imported'; report['imported'].append(ident)
            row.update(warnings=scene['missing_reasons'], model_failures=scene['model_failures'])
            for cls, count in scene['entity_classes'].items():
                report['entity_classes'][cls] = report['entity_classes'].get(cls, 0) + count
            for key, items in (('brush_classes', scene['instances'] + scene['skipped_entities']),
                               ('model_classes', scene['models'] + scene['omitted_models'])):
                for item in items:
                    report[key].setdefault(item['classname'], Counter())[item.get('reason') or item.get('status', 'drawn')] += 1
            index.append(dict(id=ident, name=ident, title=scene['title'], group=sin.GAMES[game]))
        except (ValueError, OSError, KeyError, IndexError, struct.error) as exc:
            row.update(status='failed', reason=str(exc)); report['failed'][name] = str(exc)
    (output / 'index.json').write_text(json.dumps(sorted(index, key=lambda r: r['id']), indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'sin-maps')
    parser.add_argument('--game', choices=['all', *sin.GAMES], default='all')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--filter')
    args = parser.parse_args()
    games = sin.available_games(args.install) if args.game == 'all' else [args.game]
    print(json.dumps({g: import_maps(args.install, args.output, args.replace, g, args.filter) for g in games}, indent=1))
