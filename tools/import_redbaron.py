"""Red Baron 3D (Dynamix 3Space 2.5, 1998): an install's shapes into the model browser as game rb3d.

python tools/import_redbaron.py --install "C:/Program Files (x86)/Steam/steamapps/common/Red Baron/Red Baron 3D"

Read from the install's VOL volumes, RB.VOL with 3DPATCH.VOL over it (the seasonal 3DFALL/3DWINTER and the shell's are
not read). A volume is chunks of a 4-byte tag and a u24 size with 0x80 in the fourth byte: 'VOL ', an empty 'volh',
'vols' (u32 used length, then NUL-separated names), 'voli' (14-byte entries: u32 name offset, u32 'VBLK' offset, u32
size, u8 compression (0 stored, 3 DarkStar LZH), u8 1) and the 'VBLK' blocks.

- Shapes (.DTS, named CCIILLVV in hex: class, index, level of detail 0x80 finest or 0x8f cockpit, variant): tagged
  objects, each u16 type, u16 0x14, u32 size, body padded to 2 bytes. The 'nu' objects (shape 0x65, BSP part 0x47,
  mesh 0x2b) start with a u32 1. A part is i32 transform (always -1: points are in shape space), i32 id, f32 radius,
  f32 centre[3]; a part list adds u32 count and its children; a cell animation part (0x51) then i32 sequence, i32
  count, i32 cell[count] and shows child cell[0]; null (0x78) and bitmap (sprite) parts draw nothing. A mesh is u32
  vertex, texture-vertex and face counts, vertices (f32 point[3], f32 normal[3]), texture vertices (f32 u, v) and
  faces (i32 vertex[4], i32 texture vertex[4], i32 normal vertex, i32 material, f32 plane distance, i32 0; a
  triangle repeats its third corner). The corners wind against the normal; texture v 0 is the bitmap's top row. Axes
  are x right, y forward, z up (the S.E.5a's Vickers is on the pilot's left). The 17 older 0x64 shapes (fixed-point)
  are skipped.
- Materials (.DML, CC II 90 00 for a shape's class and index): tag 0x1e, u32 count, u32 detail levels, then each
  material (tag 0x1f): u32 kind, then 1: palette index at body +16; 2: 0xBBGGRR at +20; 3: six i32 (the sixth its
  flags: 1 palette index 0 is clear, 2 a .pab alpha map for fire, oil or propeller blur, not drawn), u32 length, name.
- Textures: an aircraft's FUS, UWT, MWB... names a paint part (Baron.exe's code table at 0x188268, the paint shop's
  rmpaint.dat); its bitmap is 03 PP NN SS .bmp for plane PP, part NN and squadron SS, the plane's lowest squadron
  painted here. PBMP bitmaps are coloured from the sim palette (summer.pal, a PPAL's 'data' RGBA chunk); Windows
  bitmaps carry their own.
- Names: pnames.dat, a Dynamix table (u16 rows, u16 row width at 0x14; rows at the end: i32 id, then the name); an
  aircraft's id is its index, anything else's class * 100 + index.

Written (under local-data/rb3d): catalog.json, model_json/MODEL.json and textures/*.png (kept when already there, so
edits survive).
"""
import argparse
import io
import json
from pathlib import Path
import re
import struct

from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.import_t1_map import bitmap_png, lzh_expand
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from import_t1_map import bitmap_png, lzh_expand
    from local_data import LOCAL_DATA

VOLUMES = ('rb.vol', '3dpatch.vol')  # Later wins.
SCALE = 0.0381  # 1.5 inches: the Fokker E.III's span and length and the Camel's span come out within 1% of the real ones.
PARTS = {'fus': 0, 'uwt': 1, 'uwb': 2, 'mwt': 3, 'mwb': 4, 'lwt': 5, 'lwb': 6, 'rud': 7, 'wel': 8, 'elt': 9, 'elb': 10,
         'elb1': 10, 'elb2': 11, 'elt1': 9, 'elt2': 12, 'uwb-1': 13, 'uwb-2': 14}
CATEGORY = {3: 'Aircraft', 4: 'Soldiers', 6: 'Vehicles', 7: 'Guns', 8: 'Ground targets', 9: 'Buildings', 11: 'Projectiles',
            12: 'Explosions', 13: 'Effects', 18: 'Fire'}
LISTS = {0x65, 0x47, 0x51, 0x3c, 0x5a, 0x46, 0x64}
NU = {0x65, 0x47, 0x2b, 0x2a}


def read_volume(path):
    """{lowercase name: function returning its bytes} of one VOL volume."""
    data = Path(path).read_bytes()
    chunk = lambda at: (data[at:at + 4], int.from_bytes(data[at + 4:at + 7], 'little'))
    if chunk(0)[0] != b'VOL ' or chunk(8) != (b'volh', 0) or chunk(16)[0] != b'vols':
        raise ValueError(f'Not a Red Baron volume: {path}')
    names_at, names_size = 28, chunk(16)[1] - 4
    tag, index_size = chunk(names_at + names_size)
    if tag != b'voli':
        raise ValueError(f'No volume index: {path}')
    at, members = names_at + names_size + 8, {}
    for entry in range(index_size // 14):
        name_at, offset, size, compression, _ = struct.unpack_from('<IIIBB', data, at + 14 * entry)
        if not offset:
            break  # The index is preallocated; the used entries come first.
        name = data[names_at + name_at:data.index(b'\0', names_at + name_at)].decode('cp1252').lower()
        if data[offset:offset + 4] != b'VBLK' or compression not in (0, 3):
            raise ValueError(f'Unsupported volume entry {name}: {path}')
        members[name] = lambda o=offset + 8, s=size, c=compression: lzh_expand(data, o, s)[0] if c else data[o:o + s]
    return members


def read_volumes(install):
    found = {p.name.lower(): p for p in Path(install).rglob('*.vol') if p.is_file()} if Path(install).is_dir() else {}
    files = {}
    for name in VOLUMES:
        if name in found:
            files.update(read_volume(found[name]))
    return files


def table(data):
    """A Dynamix .dat table as {id: name}."""
    rows, width = struct.unpack_from('<HH', data, 0x14)
    start = len(data) - rows * width
    return {struct.unpack_from('<i', data, start + r * width)[0]:
            data[start + r * width + 4:start + (r + 1) * width].split(b'\0')[0].decode('cp1252').strip() for r in range(rows)}


def materials(data):
    """A .DML's first detail level as [(kind, value, flags)]: (1, palette index), (2, 0xBBGGRR) or (3, texture name);
    a texture's flags 1 make palette index 0 clear, 2 mark an effect (.pab alpha map: fire, oil, propeller blur)."""
    tag, _, _, count, _ = struct.unpack_from('<HHIII', data)
    if tag != 0x1e:
        raise ValueError('Not a material list')
    at, out = 16, []
    for _ in range(count):
        size = struct.unpack_from('<I', data, at + 4)[0]
        kind = struct.unpack_from('<I', data, at + 8)[0]
        if kind == 3:
            length = struct.unpack_from('<I', data, at + 36)[0]
            out.append((3, data[at + 40:at + 40 + length].split(b'\0')[0].decode('cp1252').lower(),
                        struct.unpack_from('<i', data, at + 32)[0]))
        else:
            out.append((kind, struct.unpack_from('<i', data, at + (24 if kind == 1 else 28))[0], 0))
        at += 8 + size + (size & 1)
    return out


def skip(data, at):
    size = struct.unpack_from('<I', data, at + 4)[0]
    return at + 8 + size + (size & 1)


def meshes(data):
    """Every drawn mesh of a 0x65 shape as (vertices, texture vertices, faces)."""
    if struct.unpack_from('<HH', data) != (0x65, 0x14):
        raise ValueError('Not a 3Space 2.5 shape')
    out, lists = [], [0]
    while lists:
        at = lists.pop()
        tag = struct.unpack_from('<H', data, at)[0]
        body = at + 8 + (4 if tag in NU else 0)
        children, child = [], body + 28
        for _ in range(struct.unpack_from('<I', data, body + 24)[0]):
            children.append(child)
            child = skip(data, child)
        if tag == 0x51:  # Cell animation: its first cell (an aircraft's intact part, a propeller's first frame).
            count, first = struct.unpack_from('<2i', data, child + 4)
            children = children[first:first + 1] if count and first >= 0 else []
        for child in children:
            kind, version = struct.unpack_from('<HH', data, child)
            if version != 0x14:
                raise ValueError(f'Unknown object at {child:#x}')
            if kind in LISTS:
                lists.append(child)
            elif kind == 0x2b:
                counts = struct.unpack_from('<3I', data, child + 36)
                p = child + 48
                vertices = list(struct.iter_unpack('<6f', data[p:p + 24 * counts[0]]))
                p += 24 * counts[0]
                uvs = list(struct.iter_unpack('<2f', data[p:p + 8 * counts[1]]))
                p += 8 * counts[1]
                out.append((vertices, uvs, list(struct.iter_unpack('<10ifi', data[p:p + 48 * counts[2]]))))
    return out


def build_model(data, slots_of):
    """Geometry, UVs and material slots of a shape; slots_of(material index) gives a PNG name or None (not drawn)."""
    vertices, normals, uvs, slots = [], [], [], {}
    for points, texture, faces in meshes(data):
        for face in faces:
            count = 3 if face[3] == face[2] else 4
            slot = slots_of(face[9])
            if slot is None or max(face[:count]) >= len(points) or face[8] >= len(points):
                continue
            normal = points[face[8]][3:]
            triangles = slots.setdefault(slot, [])
            # The corners wind against the normal: reversed, the front winds anticlockwise.
            ring = [(face[i], face[4 + i]) for i in reversed(range(count))]
            for k in range(1, count - 1):
                for point, corner in (ring[0], ring[k], ring[k + 1]):
                    x, y, z = points[point][:3]
                    u, v = texture[corner] if corner < len(texture) else (0.5, 0.5)
                    triangles.append(len(vertices) // 3)
                    vertices.extend((-x * SCALE, z * SCALE, y * SCALE))  # Z-up, +y forward -> y-up, +z forward.
                    normals.extend((-normal[0], normal[2], normal[1]))
                    uvs.extend((u, v))  # v 0 is the bitmap's top row, as in the viewer.
    names = sorted(slots)
    indices, groups = [], []
    for number, name in enumerate(names):
        groups.append(dict(start=len(indices), count=len(slots[name]), materialIndex=number))
        indices.extend(slots[name])
    return dict(game='rb3d', winding='ccw', vertices=[round(v, 4) for v in vertices], normals=[round(v, 4) for v in normals],
                uvs=[round(v, 5) for v in uvs], indices=indices, groups=groups, material_names=names,
                material_textures=names)


def import_catalog(install, output):
    """Import every finest-detail shape and cockpit of the Red Baron 3D install `install` into `output`."""
    install, output = Path(install).expanduser(), Path(output)
    files = read_volumes(install)
    shapes = sorted(n for n in files if re.fullmatch(r'[0-9a-f]{4}8[0f][0-9a-f]{2}\.dts', n))
    if not shapes or 'summer.pal' not in files:
        raise ValueError(f'No Red Baron volumes (rb.vol with shapes and summer.pal) in {install}')
    pal, at = files['summer.pal'](), 8
    while pal[at:at + 4] != b'data':
        at += 8 + struct.unpack_from('<I', pal, at + 4)[0]
    colours = [b for i, b in enumerate(pal[at + 8:at + 8 + 1024]) if i % 4 != 3]
    palettes = {'summer': (colours, [255] * 256)}  # A key no Windows bitmap names, so those keep their own colours.
    names = table(files['pnames.dat']()) if 'pnames.dat' in files else {}
    squadrons = {}
    for name in files:
        if re.fullmatch(r'03[0-9a-f]{2}00[0-9a-f]{2}\.bmp', name):
            squadrons.setdefault(name[2:4], []).append(name[6:8])
    for sub in ('model_json', 'textures'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    catalog, textures = [], {}
    for name in shapes:
        kind, index, detail, variant = (int(name[i:i + 2], 16) for i in (0, 2, 4, 6))
        title = names.get(index if kind == 3 else kind * 100 + index) or name[:-4]
        title += ' cockpit' if detail == 0x8f else f' {variant}' if variant else ''
        model = safe(name[:-4])
        item = dict(model_name=model, display_name=title, texture_name='', game='rb3d',
                    category='Cockpits' if detail == 0x8f else CATEGORY.get(kind, 'Objects'), status='ready')
        squadron = min(squadrons.get(name[2:4], ['00']))

        def slots_of(number):
            if not 0 <= number < len(material_table):
                return None
            what, value, flags = material_table[number]
            if what in (1, 2):
                rgb = colours[value * 3:value * 3 + 3] if what == 1 else (value & 255, value >> 8 & 255, value >> 16 & 255)
                slot = 'rgb_%02x%02x%02x.png' % tuple(rgb)
                textures.setdefault(slot, None)
                return slot
            stem = value.rsplit('.', 1)[0]
            source = f'03{name[2:4]}{PARTS[stem]:02x}{squadron}.bmp' if kind == 3 and stem in PARTS else value
            if flags & 2 or source not in files:
                return None  # Effects show only while burning or spinning; clbill1.bmp is in no volume.
            slot = safe(source.rsplit('.', 1)[0]) + '.png'
            textures[slot] = (source, textures.get(slot, (source, False))[1] or bool(flags & 1))
            return slot

        try:
            dml = files.get(f'{name[:4]}9000.dml')
            material_table = materials(dml()) if dml else []
            data = build_model(files[name](), slots_of)
        except (struct.error, ValueError, IndexError) as exc:
            catalog.append(dict(item, status=f'failed: {exc}'))
            continue
        if not data['indices']:
            catalog.append(dict(item, status='no visible geometry'))
            continue
        data['metadata'] = dict(source=name, squadron=squadron if kind == 3 else None)
        (output / 'model_json' / f'{model}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        catalog.append(dict(item, texture_name=data['material_textures'][0]))
    for slot, source in textures.items():
        target = output / 'textures' / slot
        if target.is_file():
            continue
        if source is None:
            Image.new('RGB', (8, 8), '#' + slot[4:10]).save(target)
            continue
        data = bytearray(files[source[0]]())
        if source[1] and data[:4] == b'PBMP' and data[8:12] == b'head':
            data[32:36] = struct.pack('<i', 1)  # Colour-keyed: palette index 0 clear.
        elif source[1] and data[:2] == b'BM':
            with Image.open(io.BytesIO(data)) as image:
                if image.mode == 'P':
                    clear = Image.frombytes('L', image.size, image.tobytes()).point([0] + [255] * 255)
                    image = image.convert('RGBA')
                    image.putalpha(clear)
                    image.save(target)
                    continue
        target.write_bytes(bitmap_png(bytes(data), palettes, alpha=source[1]))
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    return dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(import_catalog(args.install, args.output or LOCAL_DATA / 'rb3d'))
