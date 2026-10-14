"""OBJ/MTL export of an imported map pack (the layout tools/import_unreal_map.py and the Quake-family importers write).

Reads maps/ID/scene.json + geometry.bin and the pack's textures; writes a ZIP with ID.obj, ID.mtl, textures/*.png and a
README. Positions stay in the pack's units and axes (Three's Y-up frame; metres where the importer scaled, Azrael native).
Sky, sky holes and sprite billboards are left out; water surfaces become half-transparent water_* materials so the volume
shows; lightmaps and vertex colours are dropped. Quake's index textures are coloured through the pack's colormap.
"""
import io
import json
from pathlib import Path
import re
import zipfile

import numpy as np
from PIL import Image

SKY_HOLE = 0x80  # Unreal PF_FakeBackdrop: a hole onto the sky zone.
COLORMAP_ROW = 32  # The colormap row equal to the palette (0 is twice as bright, 63 black); checked against gfx/palette.lmp.


def read_pack(folder):
    """(scene, positions (n, 3), uvs (n, 2), indices) of maps/ID; the RGBA block, when present, is skipped."""
    folder = Path(folder)
    scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
    blob = (folder / 'geometry.bin').read_bytes()
    count, faces = scene['vertices'], scene['indices']
    if len(blob) not in (count * 20 + faces * 4, count * 28 + faces * 4, count * 32 + faces * 4):
        raise ValueError('geometry.bin does not match scene.json vertex and index counts')
    positions = np.frombuffer(blob, '<f4', count * 3).reshape(count, 3)
    uvs = np.frombuffer(blob, '<f4', count * 2, count * 12).reshape(count, 2)
    indices = np.frombuffer(blob, '<u4', faces, len(blob) - faces * 4)
    if faces % 3 or (faces and indices.max() >= count):
        raise ValueError('Invalid triangle indices')
    return scene, positions, uvs, indices


def placed_meshes(folder, scene, stores):
    """(positions, uvs, [(group, indices)]) per placed mesh and terrain of an instanced pack (Vengeance, Ascend, Nosgoth),
    as static/tvg-maps/materials.js builds them: library buffers through each instance's column-major matrix, terrain
    heights through to_world. NPCs and sky meshes are left out; so is decoration scatter."""
    library, buffers = scene.get('model_library') or {}, {}
    for item in scene.get('instances') or []:
        model = library.get(item.get('look'))
        if not model or item.get('npc') or model.get('sky'):
            continue
        geometry = model['geometry']
        if not re.fullmatch(r'[a-f0-9]{64}\.bin', geometry['file']):
            raise ValueError('Invalid model buffer name')
        key = (model.get('store'), geometry['file'])
        if key not in buffers:
            blob = (stores['map-models' if model.get('store') == 'map-models' else 'models'] / geometry['file']).read_bytes()
            buffers[key] = {name: np.frombuffer(blob, array['dtype'], array['count'], array['offset'])
                            for name, array in geometry['arrays'].items() if array['dtype'] in ('<f4', '<u4')}
        arrays, matrix = buffers[key], np.array(item['matrix'], float).reshape(4, 4)
        positions = arrays['vertices'].reshape(-1, 3) @ matrix[:3, :3] + matrix[3, :3]
        yield positions, arrays['uvs'].reshape(-1, 2), [(group, arrays['indices'][group['start']:group['start'] + group['count']])
                                                        for group in model['groups']]
    terrain = scene.get('terrain') or []
    blob = (Path(folder) / 'terrain.bin').read_bytes() if terrain else b''
    for grid in terrain:
        width, height, offset, c = grid['width'], grid['height'], grid['offset'], grid['to_world']
        count = width * height
        if grid['bytes'] != count * 2 or offset + grid['bytes'] + grid['visibility_bytes'] + grid['edge_bytes'] > len(blob):
            raise ValueError('Invalid terrain grid')
        heights = np.frombuffer(blob, '<u2', count, offset).astype(float)
        visible = np.frombuffer(blob, np.uint8, grid['visibility_bytes'], offset + grid['bytes'])
        edges = np.frombuffer(blob, np.uint8, grid['edge_bytes'], offset + grid['bytes'] + grid['visibility_bytes'])
        y, x = np.divmod(np.arange(count), width)
        a, b, d = x - c[0], y - c[1], heights - c[2]
        positions = np.column_stack((-(a * c[6] + b * c[7] + d * c[8]), a * c[9] + b * c[10] + d * c[11], a * c[3] + b * c[4] + d * c[5])) / 52.5
        uvs = np.column_stack(((x + .5) / width, (y + .5) / height))
        cell = (y * width + x)[(x < width - 1) & (y < height - 1)]
        bit = lambda bits, default: (bits[cell >> 3] >> (cell & 7) & 1).astype(bool) if len(bits) else np.full(len(cell), default)
        cell = cell[bit(visible, True)]
        split = bit(edges, False)
        p, q, r, s = cell, cell + 1, cell + width, cell + width + 1
        faces = np.where(split[:, None], np.column_stack((p, s, q, p, r, s)), np.column_stack((p, r, q, q, r, s))).reshape(-1, 3)
        if grid.get('inverted'):
            faces = faces[:, [0, 2, 1]]
        textures = grid.get('textures') or {}
        macro = textures.get('RenderMacroTexture') or textures.get('FinalisedMacroTexture') or textures.get('MacroTexture')
        yield positions, uvs, [({'texture': macro, 'name': 'terrain'}, faces.reshape(-1).astype(np.uint32))]


def group_role(group, page):
    """'solid', 'water' or None (not exported)."""
    kind = group.get('kind')
    if kind in ('sky', 'sprite', 'sprite_oriented') or (page in ('unreal', 'dx') and group.get('flags', 0) & SKY_HOLE):
        return None
    if kind == 'turbulent' or (group.get('settings') or {}).get('shader', '').lower() == 'water':
        return 'water'
    return 'solid'


def group_names(scene):
    """Readable texture names per group: the group's own name/material, else recovered from the Quake face audit
    (first-appearance order of (texture, kind) matches the groups table when textures and names are one to one),
    else the texture file's stem."""
    groups = scene['groups']
    # Source packs name per-face patched materials (maps/ID/wood/x_-120_244_44); the base texture is the readable name.
    names = [(g.get('settings') or {}).get('parameters', {}).get('$basetexture') or g.get('name') or g.get('material') for g in groups]
    audit = scene.get('faces') or []
    if not all(names) and audit and all('texture' in f and 'kind' in f for f in audit):
        seen = list(dict.fromkeys((f['texture'], f['kind']) for f in audit))
        if len(seen) == len(groups) and all(k == g.get('kind') for (_, k), g in zip(seen, groups)):
            names = [n or t for n, (t, _) in zip(names, seen)]
    return [re.sub(r'[^A-Za-z0-9_.-]+', '_', n or Path(g['texture']).stem).strip('_') or 'material'
            for n, g in zip(names, groups)]


def load_colormap(texture_dirs):
    for folder in texture_dirs:
        path = Path(folder) / 'colormap.png'
        if path.is_file():
            with Image.open(path) as image:
                return np.asarray(image.convert('RGB'))[COLORMAP_ROW]
    return None


def load_texture(file, texture_dirs, palette):
    """RGB/RGBA image of the pack texture `file`, or None when missing or unreadable."""
    for folder in texture_dirs:
        path = Path(folder) / file
        if path.is_file():
            try:
                with Image.open(path) as image:
                    if image.mode == 'L' and palette is not None:
                        return Image.fromarray(palette[np.asarray(image)])
                    return image.convert('RGBA' if 'A' in image.getbands() or image.mode == 'P' else 'RGB')
            except Exception:  # Unreadable DDS variants and the like land in the README's missing list.
                return None
    return None


def map_to_obj_zip(folder, texture_dirs, page, output, stores=None):
    """Write the ZIP to `output` (path or file object); returns a summary dict. `stores` ({'models': folder,
    'map-models': folder}) adds an instanced pack's placed meshes and terrain."""
    scene, positions, uvs, indices = read_pack(folder)
    name = scene['name']
    materials, missing, skipped = {}, [], 0

    def add(group, label, faces):
        nonlocal skipped
        role = group_role(group, page)
        if role is None:
            skipped += 1
            return
        key = (role, group['texture'])  # One material per texture file and role; the first group's name labels it.
        if key not in materials:
            base = ('water_' if role == 'water' else '') + label
            final, n = base, 2
            while any(m['name'] == final for m in materials.values()):
                final, n = f'{base}_{n}', n + 1
            materials[key] = dict(name=final, texture=group['texture'], role=role, alpha=float(group.get('alpha', 1) or 1), faces=[])
        materials[key]['faces'].append(faces)

    for group, label in zip(scene['groups'], group_names(scene)):
        add(group, label, indices[group['start']:group['start'] + group['count']])
    blocks, start = [(positions, uvs)], len(positions)
    for placed, placed_uvs, groups in placed_meshes(folder, scene, stores) if stores else ():
        for group, faces in groups:
            label = group.get('name') or group.get('material') or Path(group['texture'] or 'material').stem
            add(group, re.sub(r'[^A-Za-z0-9_.-]+', '_', label).strip('_') or 'material', faces.astype(np.int64) + start)
        blocks.append((placed, placed_uvs))
        start += len(placed)

    obj = io.StringIO()
    obj.write(f'# Exported from DTS Skinner: {page} map {name}\n# Pack units and axes (Y up); no normals, lightmaps or vertex colours\n'
              f'mtllib {name}.mtl\n')
    for block, _ in blocks:
        np.savetxt(obj, block, fmt='v %.6f %.6f %.6f')
    for _, block in blocks:
        np.savetxt(obj, np.column_stack((block[:, 0], 1 - block[:, 1])), fmt='vt %.6f %.6f')
    triangles = 0
    for material in materials.values():
        faces = np.concatenate(material['faces']).reshape(-1, 3).astype(np.int64) + 1
        triangles += len(faces)
        obj.write(f"o {material['name']}\nusemtl {material['name']}\n")
        np.savetxt(obj, np.repeat(faces, 2, axis=1), fmt='f %d/%d %d/%d %d/%d')

    palette = load_colormap(texture_dirs)
    mtl, images = io.StringIO(), {}
    for material in materials.values():
        image = material['texture'] and load_texture(material['texture'], texture_dirs, palette)
        mtl.write(f"newmtl {material['name']}\nKa 1.000 1.000 1.000\nKd 1.000 1.000 1.000\nKs 0.000 0.000 0.000\n"
                  f"d {0.5 if material['role'] == 'water' else min(material['alpha'], 1.0):.3f}\nillum 1\n")
        if not image:
            if material['texture']:  # A terrain without a macro texture is plain on purpose.
                missing.append(material['texture'])
        else:
            png = f"textures/{material['name']}.png"
            images[png] = image
            mtl.write(f'map_Kd {png}\n')
        mtl.write('\n')

    readme = (f'DTS Skinner - map OBJ export\n\nMap: {name} ({page})\n\n'
              f"{name}.obj and {name}.mtl with textures/*.png. Positions are the pack's: Three.js Y-up axes, in the units the "
              'importer wrote (metres where it scaled the game, native otherwise); scale in your 3D application.\n'
              'No normals are written; importers recompute them. Lightmaps and vertex colours are not exported.\n'
              'Sky surfaces, sky holes and sprite billboards are left out. Water surfaces are exported as water_* materials '
              "at half opacity so the volume shows; the look is not the game's.\n"
              + ('Placed meshes and terrain are included (terrain with its macro texture, plain where it has none); NPCs, '
                 'sky meshes and terrain decoration scatter (grass, flowers, small rocks) are left out.\n' if stores else '')
              + f'Materials: {len(materials)}, triangles: {triangles}, groups left out: {skipped}.\n')
    if missing:
        readme += '\nMissing textures (not included):\n' + '\n'.join(sorted(set(missing))) + '\n'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{name}.obj', obj.getvalue())
        archive.writestr(f'{name}.mtl', mtl.getvalue())
        archive.writestr('README.txt', readme)
        for png, image in images.items():
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            archive.writestr(png, buffer.getvalue())
    return dict(materials=len(materials), triangles=triangles, skipped=skipped, missing=sorted(set(missing)))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Export an imported map pack folder (maps/ID) as OBJ/MTL in a ZIP.')
    parser.add_argument('folder')
    parser.add_argument('output')
    parser.add_argument('--page', default='quake', help='Maps page the pack belongs to (quake, quake2, unreal, dx, ...)')
    parser.add_argument('--textures', nargs='*', default=[], help='Texture folders, searched in order')
    args = parser.parse_args()
    print(map_to_obj_zip(args.folder, args.textures or [Path(args.folder).parents[1] / 'textures'], args.page, args.output))
