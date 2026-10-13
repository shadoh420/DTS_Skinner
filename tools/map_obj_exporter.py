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
    if len(blob) not in (count * 28 + faces * 4, count * 32 + faces * 4):
        raise ValueError('geometry.bin does not match scene.json vertex and index counts')
    positions = np.frombuffer(blob, '<f4', count * 3).reshape(count, 3)
    uvs = np.frombuffer(blob, '<f4', count * 2, count * 12).reshape(count, 2)
    indices = np.frombuffer(blob, '<u4', faces, len(blob) - faces * 4)
    if faces % 3 or (faces and indices.max() >= count):
        raise ValueError('Invalid triangle indices')
    return scene, positions, uvs, indices


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


def map_to_obj_zip(folder, texture_dirs, page, output):
    """Write the ZIP to `output` (path or file object); returns a summary dict."""
    scene, positions, uvs, indices = read_pack(folder)
    name = scene['name']
    names = group_names(scene)
    materials, missing, skipped = {}, [], 0
    for group, label in zip(scene['groups'], names):
        role = group_role(group, page)
        if role is None:
            skipped += 1
            continue
        key = (role, group['texture'])  # One material per texture file and role; the first group's name labels it.
        if key not in materials:
            base = ('water_' if role == 'water' else '') + label
            final, n = base, 2
            while any(m['name'] == final for m in materials.values()):
                final, n = f'{base}_{n}', n + 1
            materials[key] = dict(name=final, texture=group['texture'], role=role, alpha=float(group.get('alpha', 1) or 1), faces=[])
        materials[key]['faces'].append(indices[group['start']:group['start'] + group['count']])

    obj = io.StringIO()
    obj.write(f'# Exported from DTS Skinner: {page} map {name}\n# Pack units and axes (Y up); no normals, lightmaps or vertex colours\n'
              f'mtllib {name}.mtl\n')
    np.savetxt(obj, positions, fmt='v %.6f %.6f %.6f')
    np.savetxt(obj, np.column_stack((uvs[:, 0], 1 - uvs[:, 1])), fmt='vt %.6f %.6f')
    triangles = 0
    for material in materials.values():
        faces = np.concatenate(material['faces']).reshape(-1, 3).astype(np.int64) + 1
        triangles += len(faces)
        obj.write(f"o {material['name']}\nusemtl {material['name']}\n")
        np.savetxt(obj, np.repeat(faces, 2, axis=1), fmt='f %d/%d %d/%d %d/%d')

    palette = load_colormap(texture_dirs)
    mtl, images = io.StringIO(), {}
    for material in materials.values():
        image = load_texture(material['texture'], texture_dirs, palette)
        mtl.write(f"newmtl {material['name']}\nKa 1.000 1.000 1.000\nKd 1.000 1.000 1.000\nKs 0.000 0.000 0.000\n"
                  f"d {0.5 if material['role'] == 'water' else min(material['alpha'], 1.0):.3f}\nillum 1\n")
        if image is None:
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
              f'Materials: {len(materials)}, triangles: {triangles}, groups left out: {skipped}.\n')
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
