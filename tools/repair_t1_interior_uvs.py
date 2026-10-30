"""Repair only UVs in legacy T1 catalog interiors, matching their original DIG first.

Dry-run by default. Pass --write to update this checkout's catalog, never the
source installs. Shapes, materials, geometry, textures and receipts stay intact.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import zipfile

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.export_interior import surface_uv
from tools.import_t1_map import Install, interiorshape
from tools.model_data import load_model_data, t1_default_families
from tools.texture_workshop import write_json_atomic


def corrected_uvs(model, dig, sizes):
    """Return corrected UVs only for the same geometry, corner order and old mapping."""
    vertices, legacy, fixed, indices, groups = [], [], [], [], []
    for surface in dig.surfaces:
        if surface.num_verts < 3:
            continue
        slot = surface.mats if surface.mats < len(sizes) else 0
        width, height = sizes[slot]
        first, start = len(vertices) // 3, len(indices)
        for pi, ti in dig.verts[surface.vert_id:surface.vert_id + surface.num_verts]:
            x, y, z = dig.points3f[pi]
            u, v = dig.points2f[ti]
            vertices.extend((x, z, -y))
            legacy.extend((-(u * (surface.tsx + 1) + surface.tox + 1) / width,
                           -(v * (surface.tsy + 1) + surface.toy + 1) / height))
            fixed.extend(surface_uv((u, v), surface, dig.highest_mip, (width, height)))
        for i in range(1, surface.num_verts - 1):
            indices.extend((first, first + i, first + i + 1))
        groups.append(dict(start=start, count=len(indices) - start, materialIndex=slot))
    same = lambda a, b: len(a) == len(b) and all(abs(x - y) <= 1e-5 for x, y in zip(a, b))
    if (not same(vertices, model['vertices']) or not same(legacy, model['uvs'])
            or indices != model['indices'] or groups != model['groups']):
        return None
    return fixed


def repair(catalog, sources, write=False):
    catalog = Path(catalog)
    # Keep competing versions: the first file with a matching name may be another model.
    resources = defaultdict(list)
    for folder in map(Path, sources):
        install = Install(folder)
        for path in sorted(folder.rglob('*')):
            if path.suffix.lower() in ('.vol', '.zip'):
                try:
                    members = install.volume(path)
                except (ValueError, OSError, zipfile.BadZipFile) as error:
                    print(f'Skipping unreadable archive {path}: {error}', file=sys.stderr)
                    continue
                for name, read in members.items():
                    if name.endswith(('.dis', '.dig')):
                        resources[name].append((str(path), read))
            elif path.suffix.lower() in ('.dis', '.dig'):
                resources[path.name.lower()].append((str(path), path.read_bytes))
    aliases = {}
    for path in (catalog.parent / 't1-buildings').glob('*.json'):
        aliases.update({name: item['source'] for name, item in json.loads(path.read_text())['models'].items()})
    families = t1_default_families(catalog)
    report, pending = [], []
    for path in sorted(catalog.glob('*.json')):
        model = json.loads(path.read_text(encoding='utf-8'))
        if model.get('interior_uv_version') == 2:
            continue
        if (model.get('metadata', {}).get('source_format') != 'dis'
                and families.get(path.stem) not in ('InteriorShapes', 'Dropships')):
            continue
        sizes = []
        for name in model['material_textures']:
            if name.startswith('[Slot'):
                sizes.append((256, 256))
            else:
                with Image.open(catalog.parent / 'textures' / name) as image:
                    sizes.append(image.size)
        stem, candidates = aliases.get(path.stem, path.stem).lower(), set()
        for member in (stem + '.dis', stem + '.0.dis'):
            for _, read in resources[member]:
                shape = interiorshape.interiorshape()
                shape.load_binary(read())
                lod = max(shape.lods, key=lambda item: item.min_pixels)
                candidates.add(shape.name_buffer[lod.geometry_file_offset:].split(b'\0')[0].decode('cp1252').lower())
        matches = []
        for name in sorted(candidates):
            for source, read in resources[name]:
                dig = interiorshape.dig()
                dig.load_binary(read())
                fixed = corrected_uvs(model, dig, sizes)
                if fixed is not None:
                    matches.append((fixed, source, name, dig.highest_mip))
        if not matches or any(item[0] != matches[0][0] for item in matches[1:]):
            report.append(dict(model=path.stem, error='No unique source geometry/UV match',
                               candidates=[dict(source=m[1], member=m[2], highest_mip=m[3],
                                                delta=max(abs(a-b) for a,b in zip(m[0],matches[0][0]))) for m in matches]))
            continue
        fixed, source, member, mip = matches[0]
        model.update(uvs=fixed, interior_uv_version=2)
        pending.append((path, model))
        report.append(dict(model=path.stem, source=source, member=member, highest_mip=mip,
                           vertices=len(fixed) // 2))
    # A failed scan writes nothing. The report names exactly what needs another source.
    if write and not any('error' in row for row in report):
        for path, model in pending:
            write_json_atomic(path, model, indent=2 if '\n' in path.read_text(encoding='utf-8').rstrip() else None)
            load_model_data(path)  # Existing geometry/material validation on the real output.
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[1] / 'static/model_json')
    parser.add_argument('--source', action='append', required=True, help='Read-only install or resource folder (recursive)')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    rows = repair(args.catalog, args.source, args.write)
    write_json_atomic(args.report, rows)
    failures = [row for row in rows if 'error' in row]
    print(f'{len(rows) - len(failures)} matched interiors; {len(failures)} unresolved; write={args.write and not failures}')
    for row in failures:
        print(row)
    sys.exit(bool(failures))
