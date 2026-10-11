"""Azrael's Tear Rooms and Extras, using the shared CD/XAF/RES readers.

python tools/import_azrael_map.py --install "C:/Program Files (x86)/GOG Galaxy/Games/Azrael's Tear"
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image

try:
    from tools import import_azrael as az
    from tools.import_unreal_map import floor_below
    from tools.local_data import LOCAL_DATA
except ImportError:
    import import_azrael as az
    from import_unreal_map import floor_below
    from local_data import LOCAL_DATA

# Keep native units; no metres-per-unit claim. R.EXE 0x121ff / constant 0x140328.
EYE_HEIGHT = 45.
# 0x3a773 initializes front=1; 0x10f74 sets back=30000. SetField's .8
# (0x10f83 -> 0x3a8a0) is a half-plane extent, not radians/degrees.
CAMERA = dict(near=1., far=30000., fov=math.degrees(2 * math.atan(.8)), fov_axis='longer',
              field=.8, eye_height=EYE_HEIGHT,
              note='Square image aspect (0x281b7/0x281c3), longer-axis field (0x59404); compare original HUD viewport')


def read_zones(data):
    """AZONES.DAT: u16 count; name[60], object[12], three preserved bytes.

    Exact size and NUL termination are checked; padding after NUL is ignored.
    The tail is 00 00 00 or 00 01 00 in retail; its meaning is not assumed.
    """
    if len(data) < 2 or len(data) > 2 + 65535 * 75:
        raise ValueError('Invalid AZONES.DAT size')
    count = struct.unpack_from('<H', data)[0]
    if len(data) != 2 + count * 75:
        raise ValueError('AZONES.DAT record count/size mismatch')
    def string(raw):
        if b'\0' not in raw:
            raise ValueError('Unterminated AZONES.DAT string')
        return raw.split(b'\0')[0].decode('ascii').strip()
    return [dict(name=string(data[p:p+60]), object=string(data[p+60:p+72]), tail=list(data[p+72:p+75]))
            for p in range(2, len(data), 75)]


def plan_maps(install):
    """Account for all XAFs without decoding textures or writing imported data."""
    cd, sources, _, rooms = az.inventory(install)
    game, _ = az.read_install(install)
    zone_file = az.child(game, 'AZONES.DAT')
    if zone_file is None:
        raise ValueError('AZONES.DAT is required for room entry metadata')
    zones = read_zones(az.read_file(zone_file, 5 * 1024 * 1024))
    entries, by_source, identical = [], defaultdict(list), {}
    for number, room in enumerate(rooms['headers'], 1):
        ident = f'room-{number:03d}-' + az.safe(room['name'])
        entry = dict(id=ident, title=room['name'], source=room['xaf'], section='Rooms',
                     record=number, room=room, aliases=[], status='pending')
        key = (room['xaf'], tuple(room['zones']), tuple(room['objects']))
        if not room['xaf']:
            entry.update(status='not-importable', reason='Blank XAF in ROOMS.DAT')
        elif room['xaf'] not in cd.files:
            entry.update(status='not-importable', reason='XAF absent from CD: ' + room['xaf'])
        elif key in identical:
            original = identical[key]
            original['aliases'].append(room['name'])
            entry.update(status='alias', alias_of=original['id'], reason='Same scene, ordered zones and object list')
        else:
            identical[key] = entry
        entries.append(entry)
        by_source[room['xaf']].append(entry)
    xafs = []
    for source in sources:
        name = source['source']
        row = dict(source=name, entries=[])
        if name in by_source:
            row.update(role='room', entries=[e['id'] for e in by_source[name]])
        elif source['status'] == 'pending':
            row.update(role='model', status='excluded', reason='Selected by model importer: ' + source['category'])
        else:
            ident = 'extra-' + az.safe(Path(name).stem)
            entry = dict(id=ident, title=Path(name).stem.upper(), source=name, section='Extras', aliases=[], status='pending',
                         room=dict(name=Path(name).stem, zones=[], objects=[]))
            if source.get('reason') != 'Static scene or camera dummy; reserved for Maps/extras':
                entry.update(status='not-importable', reason=source.get('reason', 'Not a drawable scene'))
            entries.append(entry)
            row.update(role='extra', entries=[ident])
        xafs.append(row)
    return cd, entries, zones, xafs


def room_pose(scene):
    """New-room time 0: R.EXE 0x1e448..0x1e4a8 explicitly calls SetTime(0).

    0x1df56..0x1df72 then restores the per-room frame in 0x162bc8. Fresh state
    clears this table (0x1e0f7..0x1e10c); saves use RANIM%d.ANM. Preview does
    not execute the callback at 0x1e2a8 or room scripts.
    """
    frame = 0 if scene['animations'] else None
    pose = dict(frame=frame, rule='Fresh room animation time 0' if frame is not None else 'Stored matrices; no animation set',
                objects=[a.get('object_name') or a['name'] for a in scene['animations']], status='posed' if frame is not None else 'stored')
    try:
        return az.evaluate_world_matrices(scene, frame), pose
    except (ValueError, OverflowError) as exc:
        pose.update(status='stored-fallback', requested_frame=frame, frame=None, reason=str(exc))
        return az.evaluate_world_matrices(scene, None), pose


def visible_scene(scene):
    """Retain hierarchy/paths, omit trigger visuals and existing invisible faces.

    zx/zy are reserved trigger/arrival markers (AZONES cross-references). Their
    retail materials are audited separately; no blanket ZZ/dummy draw exclusion:
    R.EXE's ZZ/ZQ/dummy test at 0x2d6dd is collision baking, not rendering.
    """
    objects, faces, details = Counter(), Counter(), []
    def visit(nodes, parent=()):
        result = []
        for i, obj in enumerate(nodes):
            path = parent + (i,)
            rule = 'zx trigger' if obj['name'].lower().startswith('zx') else 'zy arrival' if obj['name'].lower().startswith('zy') else None
            omitted = Counter()
            visible = 0
            for mesh in obj['meshes']:
                for material in mesh['materials']:
                    mat = scene['materials'].get(material)
                    reason = rule or ('material-less' if mat is None else 'alpha-zero' if mat['colour'][3] == 0 else None)
                    if reason: omitted[reason] += 1
                    else: visible += 1
            faces.update(omitted)
            for reason in omitted: objects[reason] += 1
            if omitted:
                details.append(dict(path=list(path), name=obj['name'], hidden_faces=dict(omitted), visible_faces=visible))
            result.append(dict(obj, meshes=[] if rule else obj['meshes'], children=visit(obj['children'], path)))
        return result
    result = dict(scene, objects=visit(scene['objects']))
    return result, dict(objects_by_rule=dict(objects), faces_by_rule=dict(faces), details=details)


def markers(scene, worlds):
    result = []
    for path, obj, _ in az.iter_objects(scene):
        if not obj['name'].lower().startswith(('zx', 'zy')):
            continue
        matrix = np.array(worlds[path]).reshape(4, 4)
        vertices = [v for mesh in obj['meshes'] for v in mesh['vertices']]
        center = np.mean(vertices, axis=0) if vertices else np.zeros(3)
        point = np.append(center, 1) @ matrix
        result.append(dict(name=obj['name'], path=list(path), position=point[:3].tolist(), forward=matrix[2,:3].tolist()))
    return result


def entry_candidates(room, zones, points):
    """Select entry/arrival markers, preserving alternate arrivals as viewpoints.

    EntryCoord is the NAME of room 1, so EntryCoordSpaRoom is an exit. Prefer
    a zone ending Entry (R1Entry -> zxentry), then zy arrivals nearest the first
    listed portal, then other arrival/entry markers. Never start on an exit zx.
    """
    by_name = {p['name'].casefold(): p for p in points}
    by_zone = {z['name'].casefold(): z for z in zones}
    linked = [by_zone[n.casefold()] for n in room['zones'] if n.casefold() in by_zone]
    result = []
    for zone in linked:
        if zone['name'].lower().endswith('entry') or zone['object'].lower() == 'zxentry':
            point = by_name.get(zone['object'].casefold())
            if point: result.append(dict(point, source='entry-zone', zone=zone['name']))
    arrivals = [p for p in points if p['name'].lower().startswith('zy')]
    exits = [by_name[z['object'].casefold()] for z in linked if z['object'].casefold() in by_name and z['object'].lower().startswith('zx')]
    if exits:
        arrivals.sort(key=lambda p: math.dist(p['position'], exits[0]['position']))
    result.extend(dict(p, source='arrival-marker', reason='Arrival nearest first listed zone' if exits else 'First authored arrival marker') for p in arrivals)
    result.extend(dict(p, source='entry-marker', reason='Authored zxentry without linked entry zone') for p in points if p['name'].lower() == 'zxentry')
    unique = {}
    for point in result:
        unique.setdefault(tuple(point['path']), point)
    return list(unique.values())


def viewpoints(room, zones, points, triangles):
    # Native Y up -> existing floor helper's Z up, using visible geometry only.
    normal = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
    horizontal = np.abs(normal[:,1]) > np.linalg.norm(normal, axis=1) * .5
    floors = triangles[horizontal][:,:,[0,2,1]]
    candidates = entry_candidates(room, zones, points)
    if not candidates:
        if len(floors):
            # Prefer the lower half of horizontal geometry to avoid choosing a
            # large ceiling. A triangle centroid stays inside concave rooms.
            heights = floors[:,:,2].mean(axis=1)
            lower = floors[heights <= (heights.min() + heights.max()) / 2]
            areas = np.abs(np.cross(lower[:,1]-lower[:,0], lower[:,2]-lower[:,0])[:,2])
            point = lower[int(np.argmax(areas))].mean(axis=0)[[0,2,1]]
            reason = 'Centroid of largest visible horizontal triangle in lower height half'
        else:
            point = np.median(triangles.reshape(-1,3), axis=0)
            reason = 'Median visible vertex position; no horizontal triangles'
        candidates = [dict(position=point.tolist(), forward=[0,0,1], source='fallback', reason=reason)]
    views = []
    for candidate in candidates:
        point = np.array(candidate['position'])
        # Entry trigger volumes can straddle the floor (CORICORI's centroid is
        # five units below it). The engine admits surfaces below eye - 22 at
        # 0x12185..0x121a0; use that clearance with the settled 45-unit eye.
        floor = floor_below(floors, point[0], point[2], point[1] + EYE_HEIGHT - 22.)
        eye = point.copy(); eye[1] = (floor if floor is not None else point[1]) + EYE_HEIGHT
        direction = np.array(candidate['forward'], dtype=float)
        length = math.hypot(direction[0],direction[2])
        facing = ('Default level +Z' if candidate['source'] == 'fallback' else
                  'Marker local +Z; forward-axis semantics need game comparison')
        if length < np.linalg.norm(direction) * .25 or length < 1e-8:
            direction = np.median(triangles.reshape(-1,3), axis=0) - point
            direction[1] = 0
            length = math.hypot(direction[0],direction[2])
            facing = 'No horizontal marker facing; level view toward median visible geometry'
        yaw = math.degrees(math.atan2(-direction[0], -direction[2])) if length > 1e-8 else 180.
        pitch = math.degrees(math.atan2(direction[1], length)) if length > 1e-8 else 0.
        views.append(dict(candidate, origin=(eye * [1,1,-1]).tolist(), native_origin=eye.tolist(),
                          yaw=yaw, pitch=pitch, floor_y=floor,
                          floor_rule='Highest visible surface below marker + 23' if floor is not None else 'No floor hit; marker height + eye height',
                          facing=facing))
    return views


def build_map(cd, entry, zones):
    source = entry['source']; scene = az.parse_xaf(cd.read(source), allow_identical_materials=True)
    worlds, pose = room_pose(scene)
    visible, hidden = visible_scene(scene)
    res_source = source[:-4] + '.res'
    if res_source not in cd.files: raise ValueError('Missing RES bundle: ' + res_source)
    records = list(az.res_records(cd.read(res_source)))
    if len(records) != len(scene['textures']): raise ValueError('XAF texture/RES record count mismatch')
    images, textures, texture_rows = {}, {}, []
    for texture, record in zip(scene['textures'], records):
        # Each entry owns its palette and PNG, even if another room uses the name.
        png = f"{entry['id']}_{record['index']}_{az.safe(Path(texture).stem)}.png"
        images[png] = az.decode_res(record, texture); textures[texture] = png
        texture_rows.append(dict(name=texture, source=res_source, record=record['index'], png=png))
    slots = {}
    for name, material in scene['materials'].items():
        if material['texture']:
            slots[name] = textures[material['texture']]
        else:
            rgba = tuple(round(v*255) for v in material['colour'])
            png = entry['id'] + '_colour_' + bytes(rgba).hex() + '.png'
            images[png] = Image.new('RGBA',(1,1),rgba); slots[name] = png
    model = az.build_model(visible, slots, worlds)
    vertices = np.array(model['vertices'], dtype='<f4').reshape(-1,3)
    indices = np.array(model['indices'], dtype='<u4')
    native_triangles = vertices[indices].reshape(-1,3,3) * [1,1,-1]
    points = markers(scene,worlds)
    views = viewpoints(entry['room'],zones,points,native_triangles)
    groups = []
    for group in model['groups']:
        slot = group['materialIndex']; name = model['material_names'][slot]; mat = scene['materials'][name]
        groups.append(dict(start=group['start'],count=group['count'],texture=slots[name],flags=0,unlit=True,
                           material=name, masked=bool(mat['texture'] and '#' in mat['texture']),
                           alpha=mat['colour'][3] if mat['texture'] else 1., transparent=mat['colour'][3] < 1,
                           colour=mat['colour'], specular=mat['specular']))
    room_zones = {n.casefold() for n in entry['room']['zones']}
    linked = [z for z in zones if z['name'].casefold() in room_zones]
    metadata = dict(name=entry['id'],title=entry['title'],source=source,game='azrael',format='azrael-xaf',
                    vertices=len(vertices),indices=len(indices),groups=groups,viewpoints=views,camera=dict(CAMERA),
                    bounds=[vertices.min(axis=0).tolist(),vertices.max(axis=0).tolist()],
                    aliases=entry['aliases'],section=entry['section'],room=entry['room'],pose=pose,hidden=hidden,
                    textures=texture_rows,markers=points,zones=linked,characters=[],
                    gaps=['Fresh room frame 0; scripts, saved state and moving machinery are held still',
                          'Additional characters omitted: initial WORLD.DAT/script placement not verified',
                          'Arrival selection and facing need game comparison; alternate arrivals use number keys',
                          'Unlit, no fog; original filter/lighting state and marker hiding need game comparison'])
    blob = vertices.tobytes() + np.asarray(model['uvs'], '<f4').tobytes() + indices.tobytes()
    return metadata, blob, images


def import_maps(install, output, replace=False, map_filter=None):
    cd, entries, zones, xafs = plan_maps(install)
    output = Path(output); (output/'textures').mkdir(parents=True,exist_ok=True)
    report = dict(imported=[],skipped=[],failed={},results=entries,xaf_results=xafs,zone_records=len(zones))
    index = []
    for entry in entries:
        if entry['status'] == 'alias': continue
        if entry['status'] == 'not-importable':
            report['failed'][entry['id']] = entry['reason']
        elif map_filter and map_filter.casefold() not in (entry['title']+' '+entry['source']).casefold():
            entry.update(status='filtered',reason='Excluded by requested filter'); report['skipped'].append(entry['id'])
        else:
            folder = output/'maps'/entry['id']
            try:
                if not replace and all((folder/f).is_file() for f in ('scene.json','geometry.bin')):
                    scene = json.loads(az.read_file(folder/'scene.json').decode('utf-8'))
                    entry.update(status='cached',reason='Already imported; use --replace');report['skipped'].append(entry['id'])
                else:
                    scene, blob, images = build_map(cd,entry,zones)
                    for png,image in images.items():
                        target = output/'textures'/png
                        if replace or not target.is_file(): image.save(target)
                    folder.mkdir(parents=True,exist_ok=True)
                    (folder/'geometry.bin').write_bytes(blob)
                    (folder/'scene.json').write_text(json.dumps(scene,separators=(',',':'),allow_nan=False),encoding='utf-8')
                    entry['status'] = 'imported'; report['imported'].append(entry['id'])
                entry.update(pose=scene['pose'],hidden=scene['hidden'],start_view=scene['viewpoints'][0],viewpoints=scene['viewpoints'],
                             texture_count=len(scene['textures']))
            except (ValueError,OSError,KeyError,IndexError,struct.error) as exc:
                entry.update(status='failed',reason=str(exc));report['failed'][entry['id']] = str(exc)
        if entry['section']=='Rooms' or entry['status'] in ('imported','cached'):
            index.append(dict(id=entry['id'],name=entry['title'],title=entry['title'],source=entry['source'],
                              group=entry['section'],aliases=entry['aliases'],status='ready' if entry['status'] in ('imported','cached') else 'unavailable',
                              reason=entry.get('reason','')))
    statuses = {e['id']:e for e in entries}
    for row in xafs:
        if row['role']!='model':
            row['results'] = [dict(id=i,status=statuses[i]['status'],reason=statuses[i].get('reason','')) for i in row['entries']]
    report['counts'] = dict(xafs=len(xafs),room_records=sum(e['section']=='Rooms' for e in entries),
                            extras=sum(e['section']=='Extras' for e in entries),statuses=dict(Counter(e['status'] for e in entries)))
    (output/'index.json').write_text(json.dumps(index,indent=1),encoding='utf-8')
    (output/'import-report.json').write_text(json.dumps(report,indent=1,allow_nan=False),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=LOCAL_DATA/'azrael-maps')
    parser.add_argument('--replace',action='store_true')
    parser.add_argument('--filter')
    args=parser.parse_args()
    print(json.dumps(import_maps(args.install,args.output,args.replace,args.filter),indent=1))
