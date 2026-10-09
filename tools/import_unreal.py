"""Unreal (1998) and Return to Na Pali, or Unreal Tournament (UT99): an install's meshes into the model browser as
game unreal, or ut.

python tools/import_unreal.py --install C:/Unreal
python tools/import_unreal.py --game ut --install C:/UnrealTournament

Read from the install's packages (System/*.u for the meshes, Textures/*.utx and the .u files for their skins). A
package starts with a u32 0x9E2A83C1, u16 version (61 for 1998's files, 69 for OldUnreal 227's), u16 licensee, u32
flags, then count/offset pairs for the name, export and import tables. Numbers in tables and objects are compact
indexes (sign 0x80 and 6 bits in the first byte, 7 bits in each next byte while 0x40/0x80 says more follow). An
object reference is a compact index: n > 0 is export n-1, n < 0 import -n-1, 0 none.

- Objects start with tagged properties (name, info byte: type low nibble, size code bits 4-6, array flag 0x80, which
  for a bool is its value) up to the name None.
- LodMesh (every stock mesh): UPrimitive bounds, then Mesh: lazy arrays (from version 62 each led by a u32 skip
  offset) of packed vertices (x 11 bits, y 11, z 10, signed) and old triangles, animation sequences, connects, bounds,
  vertex links, the Textures list, frame bounds, FrameVerts, AnimFrames, flags, Scale, Origin, RotOrigin, two u32,
  texture LODs; then LodMesh: collapse lists, faces (u16 wedge[3], u16 material), wedges (u16 vertex, u8 u, u8 v),
  materials (u32 poly flags, i32 Textures slot), special faces (the weapon triangle, not drawn), ModelVerts,
  SpecialVerts... A face corner's point is Verts[SpecialVerts + wedge vertex] of the frame; u, v are /255 of the
  texture.
- Texture: properties (Palette, Format, bMasked...), u8 mip count, each mip (from version 63 a u32 skip offset)
  compact size, data, u32 width, height, u8 bits. Format 0 is 8-bit, coloured from the Palette object (compact
  count of RGBA); bMasked makes index 0 clear. A FireTexture's mips are empty (the game draws it as it runs): after
  them come its sparks (compact count, 8 bytes each), from which fire_pixels draws a still. Formats 3/6/7 are
  DXT1/3/5, 5 BGRA.
- Empty slots: the game fills a mesh's empty Textures slot from the actor's MultiSkins[slot], Skin, then Texture.
  Those are class defaults: a class export (no properties) is UField super and next, UStruct script text,
  children, friendly name, line, text position, script size and bytecode, UState masks, label table and flags,
  UClass flags, GUID, dependencies, package imports, within, config name, then its default properties. Classes
  with bytecode of their own (29 of 850 in the stock packages) are skipped. Actors placed in the maps (Maps/**.unr,
  their properties over their class's) vote too: the Panel's glass is set only there. The pick most classes and
  placed actors showing the mesh agree on wins; otherwise the mesh's nearest filled slot below (UT's Bin2 faces use
  its empty slot 2 and the game draws slot 1's recycling bin, checked in 469 on DM-Pressure, 2026-10-09; with no
  slot below, 227 draws DefaultTexture); then the same slot of a same-named mesh in another package (UnrealI
  repeats some of UnrealShare's); then, as the game does, Engine's DefaultTexture. A mesh's own texture
  stays even where a class reskins it (Brute2, the Skaarj colours): those are variants.
- UT's players get their skins at run time from the class's DefaultSkinName "Package.Base" (a str property: compact
  length, text, NUL): slot n is the skin package's texture Base{n+1} (SoldierSkins.blkt1..blkt4, the face
  included), or Base itself for the bonus pack's models (TCowMeshSkins.WarCow). The player menu's and the ladder
  trophy's meshes (SelectionMesh, SpecialMesh, named as text) wear the same; a weapon's MuzzleFlashMesh wears its
  MuzzleFlashTexture.
- Placement: the mesh's own Scale, Origin and RotOrigin (#exec MESH ORIGIN); Unreal is x forward, y right, z up.
  Corners wind clockwise seen from the front. Checked against the game (227, 2026-10-09): the UPak drop box's
  "FIELD LOGISTICS / UMS" reads the same way, and players hold their weapon in the left hand, where Male1's weapon
  triangle is.
- OldUnreal 227's packages: an export flagged 0x100 carries 4 more bytes in the export table.
- SkeletalMesh (UT's SkeletalChars: Xan Mark II, WarBoss): a LodMesh (no packed vertices), then MeshScaleMax and
  LOD settings (f f f u32 f f), remapped animation vertices, u32, float wedges (none), the reference pose's points
  (f x, y, z; placed like packed vertices), bones, weights, local points, depth, animation, weapon bone and adjust.
  Drawn in the reference pose, standing with the arms a little out; the right hand bone lands on the right.
- UT ships UnrealShare and UnrealI: game ut leaves their meshes to game unreal (the same models: 251 of 344 byte
  for byte, the rest re-imported by OldUnreal 227).

SurrealEngine (github.com/dpjudas/SurrealEngine) was read as a format reference; no code was copied.

Written (under local-data/unreal, or local-data/ut): catalog.json, model_json/MODEL.json and textures/*.png (kept
when already there, so edits survive).
"""
import argparse
import json
import math
from pathlib import Path
import random
import struct

import numpy as np
from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from local_data import LOCAL_DATA

SCALE = 1 / 52.5  # Unreal units to metres: the community's usual 52.5 to a metre.
CATEGORY = {'unrealshare': 'Unreal', 'unreali': 'Unreal', 'upak': 'Return to Na Pali', 'botpack': 'Unreal Tournament',
            'relics': 'Relics', 'epiccustommodels': 'Bonus Pack', 'skeletalchars': 'Skeletal Characters'}
GAMES = {'unreal': 'UnrealShare', 'ut': 'Botpack'}  # Game id: the package its install must have.
# UT ships Unreal's packages; their meshes are the unreal game's (the same models; OldUnreal 227's are re-imports of
# some), so ut lists only its own. They are still read for their skins and same-named meshes.
SKIP = {'ut': ('unrealshare', 'unreali')}
# Skins only a class's script puts on (no class default holds them): UT's skeletal players, from the no-team branch
# of WarBoss's and XanMk2's SetMultiSkin (their source ships in SkeletalChars.u).
RUNTIME_SKINS = {('skeletalchars', 'warmachineboss'): ('SkeletalChars', 'Skins', 'WarBlue'),
                 ('skeletalchars', 'newxan'): ('SkeletalChars', 'Skins', 'XanTitanium')}
# Mesh poly flags: 0x02 masked (palette index 0 clear), 0x04 translucent, 0x10 environment mapped, 0x40 modulated,
# 0x100 two-sided; 0x01 invisible draws nothing.
INVISIBLE, MASKED, TRANSLUCENT, ENVIRONMENT, MODULATED, TWO_SIDED = 0x01, 0x02, 0x04, 0x10, 0x40, 0x100
# The sequence whose first frame is shown, first found; otherwise frame 0 (often mid-stride in a pawn's All).
MESH_PROPERTIES = ('Mesh', 'PlayerViewMesh', 'PickupViewMesh', 'ThirdPersonMesh')
POSES = ('still', 'breath', 'breath1', 'idle', 'breath2', 'stand', 'look')


class Package:
    """An Unreal package's name, import and export tables, and its objects' bytes on demand."""

    def __init__(self, path):
        self.path, self.paths = Path(path), None
        self.data = data = self.path.read_bytes()
        magic, self.version, self.licensee, _flags, *counts = struct.unpack_from('<IHHI6I', data)
        if magic != 0x9E2A83C1:
            raise ValueError(f'{self.path.name} is not an Unreal package')
        names, name_at, exports, export_at, imports, import_at = counts
        self.at = name_at
        self.names = []
        for _ in range(names):
            if self.version < 64:  # NUL-terminated
                end = data.index(b'\0', self.at)
                text = data[self.at:end]
                self.at = end + 1
            else:
                length = self.index()
                text = data[self.at:self.at + length].rstrip(b'\0')
                self.at += length
            self.names.append(text.decode('latin-1'))
            self.at += 4  # flags
        self.at = import_at
        self.imports = []
        for _ in range(imports):
            _package, class_name = self.index(), self.index()
            outer = self.i32()
            self.imports.append(dict(class_name=self.names[class_name], outer=outer, name=self.names[self.index()]))
        self.at = export_at
        self.exports = []
        for _ in range(exports):
            cls, _base = self.index(), self.index()
            outer = self.i32()
            name, flags, size = self.index(), self.i32(), self.index()
            if flags & 0x100:
                # ponytail: seen once (227's UnrealShare BoxRigidBody); the table then ends at the file's end.
                self.at += 4
            offset = self.index() if size > 0 else 0
            self.exports.append(dict(cls=cls, outer=outer, name=self.names[name], flags=flags, size=size,
                                     offset=offset))

    def index(self):
        """A compact index at self.at."""
        data, at = self.data, self.at
        b = data[at]
        value, shift, more = b & 0x3f, 6, b & 0x40
        sign = b & 0x80
        at += 1
        while more:
            b = data[at]
            at += 1
            value |= (b & 0x7f) << shift
            shift += 7
            more = b & 0x80
        self.at = at
        return -value if sign else value

    def i32(self):
        self.at += 4
        return struct.unpack_from('<i', self.data, self.at - 4)[0]

    def unpack(self, fmt):
        values = struct.unpack_from('<' + fmt, self.data, self.at)
        self.at += struct.calcsize('<' + fmt)
        return values

    def ref_name(self, ref):
        """The bare name of object reference `ref`."""
        if ref > 0:
            return self.exports[ref - 1]['name']
        if ref < 0:
            return self.imports[-ref - 1]['name']
        return 'None'

    def ref_path(self, ref):
        """(package, group..., name) of object reference `ref`; an import's outermost is its package."""
        parts, export = [], ref > 0
        while ref:
            entry = self.exports[ref - 1] if ref > 0 else self.imports[-ref - 1]
            parts.append(entry['name'])
            ref = entry['outer']
        if export:
            parts.append(self.path.stem)
        return tuple(reversed(parts))

    def export_by_path(self, path, cls):
        """The export of class `cls` whose ref_path is `path` (in any case), or None. A fire texture and its palette
        share a path."""
        if self.paths is None:
            self.paths = {(tuple(p.lower() for p in self.ref_path(n)), self.ref_name(e['cls']).lower()): n
                          for n, e in enumerate(self.exports, 1)}
        return self.paths.get((tuple(p.lower() for p in path), cls.lower()))

    def class_defaults(self, ref):
        """(super class reference, default properties) of class export `ref`, or None for a class with its own
        bytecode (the class's code, not its functions'), which is read token by token and not decoded here."""
        export = self.exports[ref - 1]
        self.at = export['offset']
        parent = self.index()
        for _ in range(4):  # next, script text, children, friendly name
            self.index()
        _line, _text, script = self.unpack('III')
        if script:
            return None
        self.at += 22 + 4 * (self.version <= 61) + 20  # state masks, label table, flags; class flags and GUID
        for _ in range(self.index()):  # dependencies
            self.index()
            self.at += 8
        for _ in range(self.index()):  # package imports
            self.index()
        if self.version >= 62:
            self.index()  # within
            self.index()  # config name
        return parent, self.tagged()

    def find(self, cls_names):
        """Export numbers (1-based references) whose class name is in `cls_names`."""
        return [n + 1 for n, e in enumerate(self.exports) if self.ref_name(e['cls']) in cls_names]

    def properties(self, ref):
        """Tagged properties of export `ref` as {name: value or raw bytes}; leaves self.at after them."""
        export = self.exports[ref - 1]
        self.at = export['offset']
        if export['flags'] & 0x02000000:  # RF_HasStack
            node = self.index()
            self.index()
            self.at += 12
            if node:
                self.index()
        return self.tagged()

    def tagged(self):
        """Tagged properties at self.at, up to the name None."""
        props = {}
        while True:
            name = self.names[self.index()]
            if name == 'None':
                return props
            info = self.data[self.at]
            self.at += 1
            kind, size_code = info & 15, info >> 4 & 7
            if kind == 10:  # struct
                self.index()
            size = (1, 2, 4, 12, 16)[size_code] if size_code < 5 else None
            if size is None:
                size = struct.unpack_from(('<B', '<H', '<I')[size_code - 5], self.data, self.at)[0]
                self.at += (1, 2, 4)[size_code - 5]
            element = 0
            if info & 0x80 and kind != 3:
                b = self.data[self.at]
                if b & 0x80 == 0:
                    element, self.at = b, self.at + 1
                elif b & 0xc0 == 0x80:
                    element, self.at = (b & 0x7f) << 8 | self.data[self.at + 1], self.at + 2
                else:
                    element, self.at = (struct.unpack_from('>I', self.data, self.at)[0]) & 0x3fffffff, self.at + 4
            start = self.at
            if kind == 3:
                value = bool(info & 0x80)
            elif kind == 1:
                value = self.data[start]
            elif kind == 2:
                value = struct.unpack_from('<i', self.data, start)[0]
            elif kind == 4:
                value = struct.unpack_from('<f', self.data, start)[0]
            elif kind in (5, 6):  # object, name
                value = self.index()
                if kind == 6:
                    value = self.names[value]
            elif kind == 13:  # str: (from version 64) a compact length, then the characters and a NUL
                if self.version >= 64:
                    self.index()
                value = self.data[self.at:start + size].split(b'\0')[0].decode('latin-1')
            else:
                value = self.data[start:start + size]
            self.at = start + size
            props[name if not element else f'{name}[{element}]'] = value

    def lazy(self, item):
        """A lazy array: (from version 62) u32 skip offset, compact count, then `item` read count times."""
        if self.version > 61:
            self.at += 4
        return [item() for _ in range(self.index())]

    def mesh(self, ref):
        """Mesh or LodMesh export `ref`: dict of its vertices, faces, textures and placement."""
        cls = self.ref_name(self.exports[ref - 1]['cls'])
        self.properties(ref)
        sphere = 'ffff' if self.version > 61 else 'fff'
        self.unpack('6fB' + sphere)  # UPrimitive bounds
        verts = self.lazy(lambda: self.unpack('i')[0])
        tris = self.lazy(lambda: self.unpack('3H6BIi'))
        seqs = []
        for _ in range(self.index()):
            name = self.names[self.index()]
            self.index()  # group
            start, frames = self.unpack('ii')
            for _ in range(self.index()):
                self.unpack('f')
                self.index()
            seqs.append(dict(name=name, start=start, frames=frames, rate=self.unpack('f')[0]))
        self.lazy(lambda: self.unpack('iI'))  # connects
        self.unpack('6fB' + sphere)
        self.lazy(lambda: self.unpack('i'))  # vertex links
        textures = [self.index() for _ in range(self.index())]
        for _ in range(self.index()):
            self.unpack('6fB')
        for _ in range(self.index()):
            self.unpack(sphere)
        frame_verts, frames, _and, _or = self.unpack('iiII')
        scale, origin, rot = self.unpack('3f'), self.unpack('3f'), self.unpack('3i')
        self.unpack('II')
        if self.version == 65:
            self.unpack('f')
        elif self.version >= 66:
            for _ in range(self.index()):
                self.unpack('f')
        mesh = dict(cls=cls, verts=verts, tris=tris, seqs=seqs, textures=textures, frame_verts=frame_verts,
                    frames=frames, scale=scale, origin=origin, rot=rot, special_verts=0)
        if cls in ('LodMesh', 'SkeletalMesh'):
            for _ in range(2):  # collapse points, face levels
                count = self.index()
                self.at += 2 * count
            mesh['faces'] = [self.unpack('4H') for _ in range(self.index())]
            count = self.index()  # collapse wedges
            self.at += 2 * count
            mesh['wedges'] = [self.unpack('H2B') for _ in range(self.index())]
            mesh['materials'] = [self.unpack('Ii') for _ in range(self.index())]
            count = self.index()  # special faces
            self.at += 8 * count
            _model, mesh['special_verts'] = self.unpack('II')
        if cls == 'SkeletalMesh':  # UT's: the reference pose's points as floats (bones and weights not read)
            self.unpack('fffIff')  # LOD settings
            count = self.index()  # remapped animation vertices
            self.at += 2 * count
            self.unpack('I')
            count = self.index()  # float wedges (none in UT's)
            self.at += 12 * count
            mesh['points'] = [self.unpack('3f') for _ in range(self.index())]
            mesh['frame_verts'], mesh['frames'] = len(mesh['points']), 1
        return mesh


class Library:
    """The install's packages by name, opened on first use, and their textures as images."""

    def __init__(self, install):
        self.maps = sorted((install / 'Maps').rglob('*.unr')) if (install / 'Maps').is_dir() else []
        self.files = {path.stem.lower(): path for folder in ('System', 'Textures') if (install / folder).is_dir()
                      for path in (install / folder).iterdir() if path.suffix.lower() in ('.u', '.utx')}
        self.packages, self.textures = {}, {}

    def package(self, name):
        name = name.lower()
        if name not in self.packages:
            self.packages[name] = Package(self.files[name]) if name in self.files else None
        return self.packages[name]

    def resolve(self, package, ref):
        """(package, export) that object reference `ref` in `package` names, or None when no package holds it."""
        if ref > 0:
            return package, ref
        if ref == 0:
            return None
        path = package.ref_path(ref)
        target = self.package(path[0])
        found = target and target.export_by_path(path, package.imports[-ref - 1]['class_name'])
        return (target, found) if found else None

    def class_skins(self):
        """{mesh path in lower case: [(Skin, Texture, {slot: MultiSkins}) of each class whose defaults show the mesh,
        and of each actor of such a class placed in a map]}, each texture a (package, reference) pair; a class's
        defaults over its parents', a placed actor's properties over its class's."""
        classes = {}
        for name in sorted(self.files):
            package = self.package(name) if self.files[name].suffix.lower() == '.u' else None
            for ref, export in enumerate(package.exports if package else (), 1):
                if export['cls'] == 0 and export['size'] > 0:
                    try:
                        found = package.class_defaults(ref)
                    except (struct.error, IndexError):
                        found = None
                    if found:
                        key = tuple(p.lower() for p in package.ref_path(ref))
                        parent = found[0] and tuple(p.lower() for p in package.ref_path(found[0]))
                        classes[key] = parent, {k: (package, v) for k, v in found[1].items()}
        merged_classes = {}
        for key in classes:
            chain, merged = [], {}
            while key in classes and key not in chain:
                chain.append(key)
                key = classes[key][0]
            for link in reversed(chain):
                merged.update(classes[link][1])
            merged_classes[chain[0]] = merged
        skins = {}

        def vote(merged):
            def texture(name):
                found = merged.get(name)
                # Engine's textures are editor icons.
                return found if found and found[1] and found[0].ref_path(found[1])[0].lower() != 'engine' else None

            multi = {int(k[11:-1]) if '[' in k else 0: texture(k) for k in merged if k.startswith('MultiSkins')}
            skin = texture('Skin')
            # UT's players take their skins at run time from DefaultSkinName "Package.Base": slot n is Base{n+1}
            # (SoldierSkins.blkt1..blkt4); a bonus pack model's Base is its Skin (TCowMeshSkins.WarCow).
            package_name, _, base = str(merged.get('DefaultSkinName', (None, ''))[1]).partition('.')
            skin_package = base and self.package(package_name)
            if skin_package:
                for slot in range(8):
                    found = skin_package.export_by_path((package_name, f'{base}{slot + 1}'), 'Texture')
                    if found and not multi.get(slot):
                        multi[slot] = skin_package, found
                found = skin_package.export_by_path((package_name, base), 'Texture')
                skin = skin or found and (skin_package, found)
            shown = [tuple(p.lower() for p in package.ref_path(ref)) for package, ref in
                     (merged.get(prop, (None, 0)) for prop in MESH_PROPERTIES) if ref and isinstance(ref, int)]
            # The player menu and the ladder's trophy show the player's skins on these, named as text.
            shown += [tuple(merged[prop][1].lower().split('.')) for prop in ('SelectionMesh', 'SpecialMesh')
                      if isinstance(merged.get(prop, (None, 0))[1], str)]
            for mesh in shown:
                skins.setdefault(mesh, []).append((skin, texture('Texture'), multi))
            package, ref = merged.get('MuzzleFlashMesh', (None, 0))  # UT weapons draw it with MuzzleFlashTexture.
            if ref and isinstance(ref, int):
                skins.setdefault(tuple(p.lower() for p in package.ref_path(ref)), []).append(
                    (texture('MuzzleFlashTexture'), None, {}))

        for merged in merged_classes.values():
            vote(merged)
        shown = {key for key, merged in merged_classes.items() if any(prop in merged for prop in MESH_PROPERTIES)}
        for path in self.maps:
            try:
                level = Package(path)
            except (OSError, ValueError, struct.error, IndexError):
                continue
            for ref, export in enumerate(level.exports, 1):
                if export['cls'] < 0 and export['size'] > 0:
                    key = tuple(p.lower() for p in level.ref_path(export['cls']))
                    if key in shown:
                        try:
                            placed = level.properties(ref)
                        except (struct.error, IndexError):
                            continue
                        vote(dict(merged_classes[key], **{k: (level, v) for k, v in placed.items()}))
        return skins

    def texture(self, package, ref):
        """(package.group.name, RGBA image) of texture reference `ref`, or None when it is missing or unreadable."""
        found = self.resolve(package, ref)
        if not found:
            return None
        package, ref = found
        key = package.ref_path(ref)
        if key not in self.textures:
            try:
                image = decode_texture(self, package, ref)
            except (struct.error, ValueError, KeyError, IndexError, OSError):
                image = None
            self.textures[key] = image and ('.'.join(key), image)
        return self.textures[key]


def decode_texture(library, package, ref):
    """The first mip of texture export `ref` as an RGBA image; a masked texture's palette index 0 is clear."""
    props = package.properties(ref)
    mips = []
    for _ in range(package.unpack('B')[0]):
        if package.version >= 63:
            package.at += 4  # skip offset
        size = package.index()
        data = package.data[package.at:package.at + size]
        package.at += size
        width, height, _u, _v = package.unpack('IIBB')
        mips.append((data, width, height))
    if not mips:
        raise ValueError('no mips')
    data, width, height = mips[0]
    kind = props.get('Format', 0)
    if kind == 0 and len(data) < width * height and package.ref_name(package.exports[ref - 1]['cls']) == 'FireTexture':
        sparks = [package.unpack('8B') for _ in range(package.index())]  # type, heat, x, y, four by type
        data = fire_pixels(props, sparks, props.get('UClamp', width), props.get('VClamp', height))
        width, height = props.get('UClamp', width), props.get('VClamp', height)
    if kind == 0:
        found = library.resolve(package, props.get('Palette', 0))
        if not found:
            raise ValueError('no palette')
        palette, at = found
        palette.properties(at)
        count = palette.index()
        colours = bytearray(palette.data[palette.at:palette.at + 4 * count])
        colours[3::4] = b'\xff' * count
        if props.get('bMasked'):
            colours[3] = 0
        image = Image.frombytes('P', (width, height), data)
        image.putpalette(bytes(colours), 'RGBA')
        return image.convert('RGBA')
    if kind in (3, 6, 7):  # DXT1, DXT3, DXT5
        return Image.frombytes('RGBA', (width, height), data, 'bcn', {3: 1, 6: 2, 7: 3}[kind])
    if kind == 5:
        return Image.frombytes('RGBA', (width, height), data, 'raw', 'BGRA')
    raise ValueError(f'texture format {kind}')


FIRE_BLUR = 12
DRIFTS = (4, 5, 6, 13, 14)  # spark kinds that send out drifting particles: blaze, oz, cone, emit, fountain


def fire_pixels(props, sparks, width, height, seed=1):
    """A still of a fire texture, which the game draws as it runs: each frame its sparks heat pixels (drifting
    particles for the blaze and emit kinds, points jittered over their A x B area for the rest), then every pixel
    takes the average of its row's three and the next row's one, less a loss set by RenderHeat, the picture moving
    up a row when bRising. After two heights of frames it has settled; the brightest of the last FIRE_BLUR frames
    is kept, as the eye sees the moving sparks. SurrealEngine's fire was the reference for
    the rules; this is a smaller approximation of the same idea, not the game's exact output."""
    rng = random.Random(seed)
    heat = np.zeros((height, width), np.int32)
    loss = 1 - (255 - props.get('RenderHeat', 0)) / 16
    drifts, seen = [], np.zeros((height, width))  # drifts: x, y, speed x, speed y, heat, decay
    for frame in range(2 * height):
        for kind, value, x, y, a, b, c, d in sparks:
            if kind == 0:  # burn
                heat[y % height, x % width] = rng.randrange(256)
            elif kind in DRIFTS and rng.random() < .25 and len(drifts) < 1024:
                if kind == 13:
                    speed = ((a ^ 128) - 128) / 128, ((b ^ 128) - 128) / 128
                else:
                    speed = rng.uniform(-1, 1), (rng.uniform(-1, 1) if kind == 4 else -.5)
                drifts.append([x + .5, y + .5, *speed, value, d if kind == 13 else 5])
            elif kind not in DRIFTS:
                heat[(y + (rng.randrange(256) * b + 128) // 256) % height,
                     (x + (rng.randrange(256) * a + 128) // 256) % width] = value
        for drift in drifts:
            drift[4] -= drift[5]
            if drift[4] > 0:
                heat[int(drift[1]) % height, int(drift[0]) % width] = drift[4]
                drift[0] += drift[2]
                drift[1] += drift[3]
        drifts = [drift for drift in drifts if drift[4] > 0]
        source = np.roll(heat, -1, axis=0) if props.get('bRising') else heat
        total = np.roll(source, 1, axis=1) + source + np.roll(source, -1, axis=1) + np.roll(source, -1, axis=0)
        heat = np.clip(total / 4 + loss, 0, 255).astype(np.int32)
        if frame >= 2 * height - FIRE_BLUR:
            seen = np.maximum(seen, heat)
    return seen.astype(np.uint8).tobytes()


def rotation(pitch, yaw, roll):
    """Matrix rows of an Unreal rotator (65536 to a turn): roll about x, then pitch about y, then yaw about z."""
    p, y, r = (a * math.pi / 32768 for a in (pitch, yaw, roll))
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    columns = ((cp * cy, cp * sy, sp), (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp),
               (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp))
    return [[column[i] for column in columns] for i in range(3)]


def unpack_vertex(packed):
    """x, y, z of a packed mesh vertex: 11, 11 and 10 signed bits from the low end."""
    x, y, z = packed & 0x7ff, packed >> 11 & 0x7ff, packed >> 22 & 0x3ff
    return x - (x & 0x400) * 2, y - (y & 0x400) * 2, z - (z & 0x200) * 2


def material_settings(flags):
    """The viewer's material settings (as for Quake 3 shaders) for mesh poly flags."""
    settings = {'cull': 'none'} if flags & TWO_SIDED else {}
    if flags & TRANSLUCENT:
        settings.update(blend=['gl_one', 'gl_one_minus_src_color'], depthWrite=False)
    elif flags & MODULATED:
        settings.update(blend=['gl_dst_color', 'gl_src_color'], depthWrite=False)
    elif flags & MASKED:
        settings['alphaFunc'] = 'GE128'
    if flags & ENVIRONMENT:
        settings['tcGen'] = 'environment'
    return settings


def build_model(mesh, slot_of, frame=0, game='unreal'):
    """Geometry of `mesh` at animation frame `frame`; slot_of(texture slot, poly flags) gives a PNG name, or None (not
    drawn). Faces with the same PNG and flags share a material."""
    rows = rotation(*mesh['rot'])
    base = frame * mesh['frame_verts']
    points = []
    packed = mesh['verts'][base:base + mesh['frame_verts']]
    for point in mesh['points'] if 'points' in mesh else map(unpack_vertex, packed):
        v = [(c - o) * s for c, o, s in zip(point, mesh['origin'], mesh['scale'])]
        x, y, z = (sum(row[k] * v[k] for k in range(3)) for row in rows)
        points.append((-y * SCALE, z * SCALE, x * SCALE))  # x forward, y right, z up -> y up, +z forward.
    if 'faces' in mesh:
        wedges, special = mesh['wedges'], mesh['special_verts']
        faces = [[(special + wedges[w][0], wedges[w][1], wedges[w][2]) for w in face[:3]] + [mesh['materials'][face[3]]]
                 for face in mesh['faces']]
    else:
        faces = [[(t[i], t[3 + 2 * i], t[4 + 2 * i]) for i in range(3)] + [(t[9], t[10])] for t in mesh['tris']]
    vertices, uvs, slots = [], [], {}
    for a, b, c, (flags, texture) in faces:
        png = slot_of(texture, flags)
        if png is None or max(a[0], b[0], c[0]) >= len(points):
            continue
        triangles = slots.setdefault((png, flags & (MASKED | TRANSLUCENT | ENVIRONMENT | MODULATED | TWO_SIDED)), [])
        for point, u, v in (a, c, b):  # Unreal's corners wind clockwise seen from the front.
            triangles.append(len(vertices))
            vertices.append(points[point])
            uvs.extend((u / 255, v / 255))
    keys = sorted(slots)
    indices, groups = [], []
    for number, key in enumerate(keys):
        groups.append(dict(start=len(indices), count=len(slots[key]), materialIndex=number))
        indices.extend(slots[key])
    return dict(game=game, winding='ccw', vertices=[round(c, 4) for v in vertices for c in v],
                uvs=[round(v, 5) for v in uvs], indices=indices, groups=groups, material_names=[k[0] for k in keys],
                material_textures=[k[0] for k in keys], material_settings=[material_settings(k[1]) for k in keys])


def slot_texture(library, skins, meshes, package, ref, mesh, index):
    """(package, texture reference) for slot `index` of mesh export `ref`: the mesh's own; for an empty slot, the
    texture most of the classes showing the mesh give it (MultiSkins, Skin, then Texture, as the game picks); then
    the mesh's nearest filled slot below it; then the same slot of a same-named mesh in another package. None when
    nothing fills it."""
    own = mesh['textures'][index] if index < len(mesh['textures']) else 0
    if own:
        return package, own
    runtime = RUNTIME_SKINS.get(tuple(p.lower() for p in package.ref_path(ref)))
    runtime = runtime and package.export_by_path(runtime, 'Texture')
    if runtime:
        return package, runtime
    votes = {}
    for skin, texture, multi in skins.get(tuple(p.lower() for p in package.ref_path(ref)), ()):
        pick = multi.get(index) or skin or texture
        if pick:
            votes.setdefault(pick[0].ref_path(pick[1]), []).append(pick)
    if votes:
        return max(sorted(votes.items()), key=lambda item: len(item[1]))[1][0]
    lower = next((t for t in reversed(mesh['textures'][:index]) if t), 0)
    if lower:
        return package, lower
    for other, other_mesh in meshes.get(package.exports[ref - 1]['name'].lower(), ()):
        textures = other_mesh['textures']
        if other is not package and index < len(textures) and textures[index]:
            return other, textures[index]
    return None


def import_catalog(install, output, game='unreal'):
    """Import every mesh in the System packages of the Unreal (game unreal) or Unreal Tournament (ut) install
    `install` into `output`."""
    install, output = Path(install).expanduser(), Path(output)
    library = Library(install)
    if not library.package(GAMES[game]):
        title = 'Unreal Tournament' if game == 'ut' else 'Unreal'
        raise ValueError(f'No {title} install (System/{GAMES[game]}.u) in {install}')
    for sub in ('model_json', 'textures'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    found, meshes = [], {}
    for source in sorted(name for name, path in library.files.items() if path.suffix.lower() == '.u'):
        package = library.package(source)
        for ref in package.find({'LodMesh', 'Mesh', 'SkeletalMesh'}):
            try:
                mesh = package.mesh(ref)
            except (struct.error, ValueError, IndexError, KeyError) as exc:
                mesh = exc
            if source not in SKIP.get(game, ()):
                found.append((source, package, ref, mesh))
            if isinstance(mesh, dict):
                meshes.setdefault(package.exports[ref - 1]['name'].lower(), []).append((package, mesh))
    skins = library.class_skins()
    # A slot nothing fills is drawn with Engine's DefaultTexture: the Eightball's first-person hand and the candle
    # flames show it in game (227, checked 2026-10-09).
    engine = library.package('engine')
    default = engine and engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')
    default = default and (engine, default)
    catalog, textures, taken = [], {}, set()
    for source, package, ref, mesh in found:
        mesh_name = package.exports[ref - 1]['name']
        model = safe(mesh_name)
        if model in taken:
            model = safe(f'{package.path.stem}_{mesh_name}')
        taken.add(model)
        item = dict(model_name=model, display_name=mesh_name, texture_name='', game=game,
                    category=CATEGORY.get(source, package.path.stem), status='ready')
        if not isinstance(mesh, dict):
            catalog.append(dict(item, status=f'failed: {mesh}'))
            continue

        def slot_of(index, flags):
            if flags & INVISIBLE:
                return None
            pick = slot_texture(library, skins, meshes, package, ref, mesh, index) or default
            texture = pick and library.texture(*pick)
            if not texture:
                textures['missing.png'] = None
                return 'missing.png'
            png = safe(texture[0]) + '.png'
            textures[png] = texture[1]
            return png

        names = {seq['name'].lower(): seq for seq in mesh['seqs']}
        pose = next((names[name] for name in POSES if name in names), None)
        try:
            data = build_model(mesh, slot_of, pose['start'] if pose and pose['start'] < mesh['frames'] else 0, game)
        except (struct.error, ValueError, IndexError, KeyError) as exc:
            catalog.append(dict(item, status=f'failed: {exc}'))
            continue
        if not data['indices']:
            catalog.append(dict(item, status='no visible geometry'))
            continue
        data['metadata'] = dict(source=f'{package.path.name}:{mesh_name}', frames=mesh['frames'],
                                pose=pose and pose['name'], sequences=[s['name'] for s in mesh['seqs']])
        (output / 'model_json' / f'{model}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        catalog.append(dict(item, texture_name=data['material_textures'][0]))
    for png, image in textures.items():
        target = output / 'textures' / png
        if not target.is_file():
            (image or Image.new('RGB', (8, 8), '#808080')).save(target)
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    return dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--game', choices=GAMES, default='unreal')
    args = parser.parse_args()
    print(import_catalog(args.install, args.output or LOCAL_DATA / args.game, args.game))
