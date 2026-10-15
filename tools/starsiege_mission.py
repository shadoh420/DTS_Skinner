"""Starsiege binary missions (.mis): DarkStar persistent blocks read into the node tree tools/import_t1_map.py's
parse_mission builds from a Tribes text mission, so the T1 map importer can place their terrain, sky and objects.

A block is a FOURCC (or a numeric class tag) and a size whose top bit asks for dword padding (Core/BLKSTRM.H). Sets
(SimGroup SIMG, drop point groups ESDP, shape groups Ssgp) hold a version, a child count, the children and then one
u8-length name per child (Sim/code/simBase.cpp SimSet::read, SimGroup::read). Most objects start with the version and
SimNetObject's id, flags and address: 24 bytes. Fields after that follow each class's read() (Sim/code/simTerrain.cpp,
SimObjects/Code/simSky.cpp, simPlanet.cpp, simStarField.cpp, simVolume.cpp). Starsiege's own placed shapes and
interiors use numeric tags 0x100 and 0x10c, measured on the install: position and Euler rotation at fixed offsets, the
model file as an int32-length name. Resource tags (sky material lists, planet bitmaps) resolve through the
`ID... = number, "file";` lines of scripts.vol mission.strings.cs.

Campaign units (HERC, TANK, FLYR) are DarkStar LZH-packed after their version word; the expanded record (measured on
all 755 in the install) holds the net header (20 bytes), a 6, the vehicle number, a count n and n u16 component ids,
three flag bytes, a u32, a u8-length name, a u8 count and that many bytes, then position and heading (4 floats). The
number is the mission editor's (scripts.vol datvehicle.cs build*(number, ..., "datHerc_tr_mino.cs"), whose script
names the shape). Turrets (TRRT) lay out like placed shapes. Both are drawn as NPCs.
"""
import math
import re
import struct

SHAPE, INTERIOR = 0x100, 0x10c
UNITS = (b'HERC', b'TANK', b'FLYR')
HEADER = 24  # Version, SimNetObject id, net flags, address and one more word.


def read_tags(text):
    """Resource tag number -> file name from a DarkStar strings script."""
    return {int(number): name for number, name in re.findall(r'^\s*ID\w+\s*=\s*(\d+)\s*,\s*"([^"]*)"', text, re.M)}


def read_vehicles(scripts):
    """Vehicle number -> shape file, from datvehicle.cs and the vehicle scripts it names ({lower-case name: text})."""
    found = {}
    for number, script in re.findall(r'build(?:Herc|Tank|Drone|Flyer)\(\s*(\d+)\s*,[^;]*?"([^"]+\.cs)"', scripts.get('datvehicle.cs', '')):
        shape = re.search(r'Base\([^;]*?"([^"]+\.dts)"', scripts.get(script.lower(), ''))
        if shape:
            found[int(number)] = shape[1]
    return found


def _expand(body):
    """A unit's LZH record after its version word, as long as the block holds (no length is stored)."""
    from tools.import_t1_map import lzh_expand  # The importer's LZH; imported here, as it imports this module's users.
    low, high = 0, 4096
    while low < high:  # The longest length that expands without running out of input.
        middle = (low + high + 1) // 2
        try:
            lzh_expand(body, 4, middle)
            low = middle
        except (ValueError, IndexError):
            high = middle - 1
    return lzh_expand(body, 4, low)[0]


def _unit(body, name, vehicles):
    record = _expand(body)
    at = 23 + 2 * record[22] + 3 + 4  # Component ids, three flags, a u32.
    at += 1 + record[at]  # Name.
    at += 1 + record[at]  # Counted bytes.
    x, y, z, heading = struct.unpack_from('<4f', record, at)
    shape = vehicles.get(record[21])
    if not shape or not all(math.isfinite(value) for value in (x, y, z, heading)):
        return None
    node = _node('staticshape', name, shapefile=shape, position='%g %g %g' % (x, y, z), rotation='0 0 %g' % heading)
    node['npc'] = True
    return node


def _string(body, offset):
    length = body[offset]
    return body[offset + 1:offset + 1 + length].decode('latin1')


def _floats(body, offset, count=3):
    values = struct.unpack_from('<%df' % count, body, offset)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Non-finite value in mission object')
    return ' '.join('%g' % value for value in values)


def _model_name(body, suffix):
    """The int32-length, NUL-ended model file name a Starsiege shape or interior carries."""
    for match in re.finditer(rb'([\w\-]+\.%s)\0' % suffix, body, re.I):
        start = match.start()
        if start >= 4 and struct.unpack_from('<i', body, start - 4)[0] == len(match[1]) + 1:
            return match[1].decode('latin1')
    return None


def _euler(rows):
    """DarkStar Euler angles (radians) whose import_t1_map.rotation_rows are these rows."""
    sx = max(-1.0, min(1.0, rows[1][2]))
    x, cx = math.asin(sx), math.sqrt(max(0.0, 1 - sx * sx))
    if cx < 1e-6:  # Looking straight up or down: put the turn in z.
        return '%g 0 %g' % (x, math.atan2(rows[0][1], rows[0][0]))
    return '%g %g %g' % (x, math.atan2(-rows[0][2], rows[2][2]), math.atan2(-rows[1][0], rows[1][1]))


def _children(data, start, end, count):
    """Child blocks of a set, or None when they do not fit (the block is no set)."""
    blocks, position = [], start
    for _ in range(count):
        if position + 8 > end:
            return None
        tag, size = struct.unpack_from('<II', data, position)
        length = size & 0x7fffffff
        following = position + 8 + ((length + 3) & ~3 if size & 0x80000000 else (length + 1) & ~1)
        if position + 8 + length > end:
            return None
        blocks.append((data[position:position + 4], tag, data[position + 8:position + 8 + length], position + 8))
        position = following
    return blocks, position


def _node(cls, name='', **fields):
    return {'class': cls, 'name': name, 'fields': fields, 'children': []}


def _read(data, raw, tag, body, offset, name, tags, vehicles):
    """One block as a node, or None for classes the maps do not draw (triggers, nav markers, sounds)."""
    if len(body) >= 16:
        count = struct.unpack_from('<i', body, 4)[0]
        found = 0 < count < 100000 and _children(data, offset + 8, offset + len(body), count)
        if found:
            blocks, position = found
            names = []
            for _ in range(count):
                length = data[position] if position < offset + len(body) else 0
                names.append(data[position + 1:position + 1 + length].decode('latin1'))
                position += 1 + length
            group = _node('simgroup', name)
            for (child_raw, child_tag, child_body, child_offset), child_name in zip(blocks, names):
                try:
                    child = _read(data, child_raw, child_tag, child_body, child_offset, child_name, tags, vehicles)
                except (ValueError, struct.error, IndexError):  # One unreadable object; the rest of the mission stays.
                    child = None
                if child:
                    group['children'].append(child)
            return group
    if raw == b'SVol':
        return _node('simvolume', name, filename=_string(body, HEADER))
    if raw == b'ESpt':
        return _node('simpalette', name, filename=_string(body, HEADER))
    if raw == b'STER':  # Context position and Euler, visibility (visible, haze, vertical min, max), detail, ted file.
        visible, haze = struct.unpack_from('<2f', body, HEADER + 24)
        return _node('simterrain', name, position=_floats(body, HEADER), rotation=_floats(body, HEADER + 12),
                     visibledistance='%g' % visible, hazedistance='%g' % haze, tedfilename=_string(body, HEADER + 48))
    if raw == b'SKYT':  # Sky colour, material list tag, feature position, size, distance, sixteen panel slots.
        tag_number, feature, size = struct.unpack_from('<Iff', body, HEADER + 12)
        fields = {'skycolor': _floats(body, HEADER), 'featureposition': '%g' % feature, 'size': '%g' % size,
                  'distance': '%g' % struct.unpack_from('<f', body, HEADER + 24)[0],
                  **{'textures[%d]' % slot: str(struct.unpack_from('<b', body, HEADER + 28 + slot)[0]) for slot in range(16)}}
        if tag_number in tags:
            fields['dmlname'] = tags[tag_number]
        return _node('sky', name, **fields)
    if raw == b'SKPL':  # Texture tag, azimuth, incidence, cast shadows, lens flare, intensity, ambient, size, distance.
        tag_number, azimuth, incidence, shadows, flare = struct.unpack_from('<Iff??', body, HEADER)
        size, distance = struct.unpack_from('<2f', body, HEADER + 38)
        fields = {'azimuth': '%g' % azimuth, 'incidence': '%g' % incidence, 'castshadows': 'true' if shadows else 'false',
                  'uselensflare': 'true' if flare else 'false', 'intensity': _floats(body, HEADER + 14), 'ambient': _floats(body, HEADER + 26),
                  'size': '%g' % size, 'distance': '%g' % distance}
        if tag_number in tags:
            fields['filename'] = tags[tag_number]
        return _node('planet', name, **fields)
    if raw == b'SKSF':  # In front of the sky (u8), then three RGB star colours.
        return _node('starfield', name, **{'colors[%d]' % shade: _floats(body, HEADER + 1 + shade * 12) for shade in range(3)})
    if raw == b'SNOW':  # Intensity, wind (its z is the fall speed), suspend rendering (u8), rain (u8): Snowfall::read.
        suspended, rain = body[HEADER + 16:HEADER + 18]
        return _node('snowfall', name, intensity='%g' % struct.unpack_from('<f', body, HEADER)[0], wind=_floats(body, HEADER + 4),
                     suspendrendering='true' if suspended else 'false', rain='true' if rain else 'false')
    if raw == b'DPNT':  # A spare word, then the transform's rotation rows and position.
        rows = [struct.unpack_from('<3f', body, HEADER + 4 + 12 * row) for row in range(3)]
        return _node('droppoint', name, position=_floats(body, HEADER + 40), rotation=_euler(rows))
    if raw in UNITS:
        return _unit(body, name, vehicles)
    if raw == b'TRRT' and len(body) > 72:
        model = _model_name(body, b'dts')
        if model:
            node = _node('staticshape', name, shapefile=model, position=_floats(body, 48), rotation=_floats(body, 60))
            node['npc'] = True
            return node
        return None
    if tag == SHAPE and len(body) > 72:
        model = _model_name(body, b'dts')
        return model and _node('staticshape', name, shapefile=model, position=_floats(body, 48), rotation=_floats(body, 60))
    if tag == INTERIOR and len(body) > 79:
        model = _model_name(body, b'dis')
        return model and _node('interiorshape', name, filename=model, position=_floats(body, 55), rotation=_floats(body, 67))
    return None


def read_mission(data, tags=None, vehicles=None):
    """Root node of a Starsiege .mis: {'class': 'root', 'children': [the mission's SimGroup]}; vehicles from read_vehicles."""
    if data[:4] != b'SIMG':
        raise ValueError('Not a Starsiege binary mission')
    size = struct.unpack_from('<I', data, 4)[0] & 0x7fffffff
    if 8 + size > len(data):
        raise ValueError('Truncated mission file')
    group = _read(data, b'SIMG', 0, data[8:8 + size], 8, '', tags or {}, vehicles or {})
    if not group or group['class'] != 'simgroup':
        raise ValueError('Mission has no object group')
    return {'class': 'root', 'name': '', 'fields': {}, 'children': [group]}
