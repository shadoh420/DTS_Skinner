"""Add mission-placed DIS interiors to the shipped T1 catalog, without overwriting files.

python tools/add_t1_buildings.py --game-base C:/Tribes/base --mission C:/maps

Mission folders are searched recursively. Sources are read-only. A content-named
receipt in static/t1-buildings records sources, palettes, team decisions and textures;
it also supplies the browser family. Unplaced archive interiors are reported, not
guessed a palette. Existing catalog models and previously recorded aliases are skipped.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import re
import struct
import sys

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.import_t1_map import (Install, bitmap_png, export_mounted_interior,
                                 mission_resources, parse_mission, read_palettes, walk)
from tools.model_data import t1_catalog_names
from tools.texture_workshop import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
TOLERANCE = 1e-5  # Absolute exported-coordinate noise; no translation, rotation or scale alignment.


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filename(value):
    """Reject paths before any source-controlled name is used as an output filename."""
    # The same rule as the app's material_texture_refs: a local file name (DECK1+.bmp is one), no path or device.
    if (not value or value in ('.', '..') or value.endswith((' ', '.')) or any(c in value for c in '/\\<>"|?*:')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('Expected a plain resource filename: ' + value)
    return value.lower()


def interior_stem(name):
    return re.sub(r'(\.\d+)?\.dis$', '', filename(name), flags=re.I)


def write_new(path, data):
    """Exclusive creation, or byte-identical reuse; never replace even on a rerun."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('Refusing to overwrite: ' + str(path)) from None


def pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert('RGBA')
        return image.size, image.tobytes()


class Textures:
    """Plan PNG writes, comparing decoded pixels rather than encoding or metadata."""
    def __init__(self, folder):
        self.folder = folder
        self.existing = {p.name.lower(): p for p in folder.iterdir()} if folder.exists() else {}
        self.pending, self.events = {}, []

    def add(self, stem, name, data):
        name = filename(Path(name).stem + '.png')
        expected = pixels(data)
        for attempt in itertools.count():
            candidate = name if attempt == 0 else f'{stem}_{name}' if attempt == 1 else f'{stem}_{attempt}_{name}'
            old = self.existing.get(candidate)
            previous = old.read_bytes() if old else self.pending.get(candidate)
            if previous is not None and pixels(previous) != expected:
                continue
            stored = old.name if old else candidate
            if previous is None:
                self.pending[candidate] = data
            self.events.append(dict(building=stem, requested=name, stored=stored,
                                    reused=previous is not None, collision=attempt > 0,
                                    pixel_sha256=digest(expected[1])))
            return stored


def team_pairs(names):
    """Explicit irregular team names plus any two stems that differ by one BE/DS swap (be_rig, hilde_be, storkbe,
    dxberad/dxdsrad, berc/dsrc); only identical geometry is merged, so a false match just keeps both."""
    names = set(names)
    pairs = {('ccbeaglelz', 'ccdswordlz', 'cclz')}
    for name in names:
        for i in range(len(name) - 1):
            if name[i:i + 2] == 'be':
                neutral = re.sub(r'_+', '_', name[:i] + name[i + 2:]).strip('_')
                if neutral:
                    pairs.add((name, name[:i] + 'ds' + name[i + 2:], neutral))
    return sorted(pair for pair in pairs if pair[0] in names and pair[1] in names)


def triangle_keys(model, vertices):
    indices = model['indices']
    if len(indices) % 3:
        raise ValueError('Incomplete triangle')
    keys = []
    for at in range(0, len(indices), 3):
        triangle = tuple(vertices[i] for i in indices[at:at + 3])
        # Cyclic permutations keep winding; reversed faces are different geometry.
        keys.append(min(triangle, triangle[1:] + triangle[:1], triangle[2:] + triangle[:2]))
    return keys


def compare_geometry(a, b):
    """Compare triangle multisets, ignoring vertex/surface ordering and float noise.

    Map both meshes to the first mesh's spatially indexed positions. Multiplicity
    and winding are retained; materials and UVs are examined separately for reskins.
    """
    buckets, positions = defaultdict(list), []
    maximum_delta = 0.0

    def ids(model, create):
        nonlocal maximum_delta
        result = []
        xyz = model['vertices']
        if len(xyz) % 3 or not all(math.isfinite(v) for v in xyz):
            raise ValueError('Invalid interior vertices')
        for at in range(0, len(xyz), 3):
            point = tuple(xyz[at:at + 3])
            cell = tuple(math.floor(v / TOLERANCE) for v in point)
            matches = [(max(abs(x - y) for x, y in zip(point, positions[i])), i)
                       for offset in itertools.product((-1, 0, 1), repeat=3)
                       for i in buckets.get(tuple(x + y for x, y in zip(cell, offset)), ())]
            distance, found = min(matches, default=(math.inf, None))
            if distance > TOLERANCE:
                if not create:
                    return None
                found = len(positions)
                positions.append(point)
                buckets[cell].append(found)
            elif not create:
                maximum_delta = max(maximum_delta, distance)
            result.append(found)
        return result

    av, bv = ids(a, True), ids(b, False)
    ak = triangle_keys(a, av)
    bk = triangle_keys(b, bv) if bv is not None else []
    same = bool(ak) and Counter(ak) == Counter(bk)
    evidence = dict(equal=same, tolerance=TOLERANCE, triangles=[len(ak), len(b['indices']) // 3],
                    max_matched_coordinate_delta=maximum_delta,
                    geometry_sha256=[digest(encoded({k: m[k] for k in ('vertices', 'indices')})) for m in (a, b)])
    if same:
        def slots(model, keys):
            result = defaultdict(set)
            for group in model['groups']:
                for at in range(group['start'], group['start'] + group['count'], 3):
                    result[keys[at // 3]].add(group['materialIndex'])
            return result
        left, right = slots(a, ak), slots(b, bk)
        mapping = defaultdict(set)
        for key, source_slots in left.items():
            for slot in source_slots:
                mapping[slot].update(right[key])
        evidence['slots'] = [dict(slot=slot, texture=a['material_textures'][slot],
                                  alternate_slots=sorted(other),
                                  alternate_textures=[b['material_textures'][i] for i in sorted(other)],
                                  differs=any(a['material_textures'][slot] != b['material_textures'][i] for i in other))
                             for slot, other in sorted(mapping.items())]
        evidence['uv_arrays_equal'] = (len(a['uvs']) == len(b['uvs']) and
                                      all(abs(x - y) <= TOLERANCE for x, y in zip(a['uvs'], b['uvs'])))
    return evidence


def mission_paths(items):
    paths = []
    for item in map(Path, items):
        found = sorted(p for p in item.rglob('*') if p.suffix.lower() == '.mis') if item.is_dir() else [item]
        if not found or any(not p.is_file() or p.suffix.lower() != '.mis' for p in found):
            raise ValueError('No mission files found: ' + str(item))
        paths.extend(found)
    # Folder argument order is the explicit pack preference; lexical order within each.
    return list(dict.fromkeys(paths))


def export_building(install, mission, nodes, stem, instance):
    _, _, volumes, warnings, read = mission_resources(install, mission, nodes)
    resources = {}

    def tracked(name):
        data = read(name)
        resources[name] = digest(data)
        return data

    palette = next((n['fields'].get('filename', '') for n, _ in nodes if n['class'] == 'simpalette'), '')
    palettes, palette_error = {}, None
    try:
        palettes, _ = read_palettes(tracked(palette))
    except (ValueError, KeyError, OSError, struct.error) as error:
        palette_error = str(error)
    textures, missing = {}, []

    def texture(name):
        key = filename(Path(name).stem + '.png')
        if key not in textures:
            textures[key] = None
            for candidate in (key, Path(key).stem + '.bmp'):
                try:
                    raw = tracked(candidate)
                    # Do not silently colour indexed bytes with the first unrelated palette.
                    if raw[:4] == b'PBMP' and not palettes:
                        raise ValueError('PBMP requires mission palette: ' + palette)
                    textures[key] = bitmap_png(raw, palettes)
                    break
                except (ValueError, KeyError, OSError, struct.error):
                    continue
            else:
                # As in map imports: a texture the files lack (e.g. the editor's AAATRIGGER) leaves its slot empty.
                missing.append(f'{name} (palette {palette}{": " + palette_error if palette_error else ""})')
        return textures[key]

    try:
        member, source = stem + '.dis', tracked(stem + '.dis')
    except ValueError:
        member, source = instance, tracked(instance)
    model = export_mounted_interior(stem, source, tracked, texture)
    for name in model['material_textures']:
        if not name.startswith('['):
            texture(name)
    model['metadata'] = dict(game='t1', source_format='dis')
    return model, textures, dict(mission=str(mission), mission_sha256=digest(mission.read_bytes()),
                                member=member, palette=palette, palette_error=palette_error,
                                resources=resources, volumes=volumes, warnings=warnings, missing_textures=missing)


def add_buildings(game_base, missions, static=ROOT / 'static', resources=(), also_skip=(), game='t1'):
    """Stage all conversions before creating files. Returns the additive family receipt.

    also_skip: other catalogs' model names (the shipped T1 catalog when writing a local game's folder)."""
    install, static = Install(game_base, resources), Path(static)
    model_dir = static / 'model_json'
    existing = {name.lower() for name in t1_catalog_names(model_dir)} if model_dir.exists() else set()
    existing |= {name.lower() for name in also_skip}
    known = set(existing)
    for path in (static / 't1-buildings').glob('*.json'):
        receipt = json.loads(path.read_text(encoding='utf-8'))
        for stem, source in receipt['sources'].items():
            if source['model'] in existing:
                known.add(stem)
    paths = mission_paths(missions)
    placed, archived = defaultdict(list), defaultdict(list)
    for path in paths:
        nodes = list(walk(parse_mission(path.read_text(encoding='cp1252', errors='replace'))))
        for node, _ in nodes:
            name = node['fields'].get('filename', '')
            if node['class'] == 'interiorshape' and name.lower().endswith('.dis'):
                name = filename(name)
                stem = interior_stem(name)
                if not any(p == path for p, _, _ in placed[stem]):
                    placed[stem].append((path, nodes, name))
    # Inventory unplaced interiors too, so an absent .mis cannot silently erase a scan entry.
    roots = [Path(item) if Path(item).is_dir() else Path(item).parent for item in missions]
    archives = sorted({p for root in roots for p in root.rglob('*') if p.suffix.lower() in ('.vol', '.zip')})
    for path in archives:
        for name in install.volume(path):
            if name.endswith('.dis'):
                archived[interior_stem(name)].append(str(path))

    report = dict(models={}, sources={}, pairs=[], textures=[], skipped=sorted(known & placed.keys()), failed={},
                  unplaced={k: sorted(set(v)) for k, v in sorted(archived.items()) if k not in placed and k not in known})
    converted, pngs = {}, Textures(static / 'textures')
    for stem in sorted(placed.keys() - known):
        failures = []
        incomplete = None  # A mission lacking some textures is used only if no placing mission has them all.
        for path, nodes, instance in placed[stem]:
            try:
                model, textures, provenance = export_building(install, path, nodes, stem, instance)
                if game != 't1':  # A local game's models name it, so their textures come from its own library.
                    model['game'] = model['metadata']['game'] = game
            except (ValueError, KeyError, IndexError, OSError, struct.error) as error:
                failures.append(dict(mission=str(path), error=str(error)))
                continue
            if not provenance['missing_textures']:
                break
            failures.append(dict(mission=str(path), error='missing textures: ' + ', '.join(provenance['missing_textures'])))
            incomplete = incomplete or (model, textures, provenance)
        else:
            if not incomplete:  # No placing mission can read it (e.g. its .dis is absent): reported, the rest go on.
                report['failed'][stem] = failures
                continue
            model, textures, provenance = incomplete
        provenance['earlier_candidates_failed'] = failures
        # A texture the files lack becomes an untextured-slot label, as the stock catalog writes them.
        model['material_textures'] = [name if name.startswith('[') else
                                      pngs.add(stem, name, textures[filename(name)]) if textures.get(filename(name)) else
                                      '[Slot %d: %s]' % (slot, re.sub(r'[^A-Za-z0-9 _.-]', '_', Path(name).stem))
                                      for slot, name in enumerate(model['material_textures'])]
        converted[stem] = model
        report['sources'][stem] = provenance

    output_names = {stem: stem for stem in converted}
    for left, right, neutral in team_pairs(converted):
        evidence = compare_geometry(converted[left], converted[right])
        evidence.update(left=left, right=right, neutral=neutral)
        taken = neutral in existing or neutral in converted or neutral in output_names.values()
        merged = output_names[left] != left or output_names[right] != right  # already in another pair
        if evidence['equal'] and not taken and not merged:
            output_names[left] = output_names[right] = neutral
            evidence['decision'] = 'one neutral model; both texture sets'
        else:
            evidence['decision'] = ('keep both geometries' if not evidence['equal'] else
                                    f'same geometry, kept both: {neutral} is taken' if taken else 'same geometry, kept both: already merged')
        report['pairs'].append(evidence)
    output = {}
    for stem, model in converted.items():
        name = output_names[stem]
        output.setdefault(name, encoded(model))  # Sorted BE source wins a collapsed pair.
        report['sources'][stem]['model'] = name
        report['models'].setdefault(name, dict(source=stem, sha256=digest(output[name])))
    report['textures'] = pngs.events
    # Check every model destination before any write; PNG decisions already considered existing files.
    for name, data in output.items():
        path = model_dir / (name + '.json')
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Refusing to overwrite: ' + str(path))
    for name, data in pngs.pending.items():
        write_new(static / 'textures' / name, data)
    for name, data in output.items():
        write_new(model_dir / (name + '.json'), data)
    if report['sources']:
        data = encoded(report)
        write_new(static / 't1-buildings' / (digest(data)[:20] + '.json'), data)
    return report


def handoff(report):
    """Human-readable companion to the exact, machine-readable conversion receipt."""
    lines = ['# T1 building conversion result', '',
             f"Added {len(report['models'])} models from {len(report['sources'])} placed interiors.", '',
             '## Source missions and palettes', '',
             '| Interior | Catalog model | Placing mission | Palette requested | Palette read |',
             '| --- | --- | --- | --- | --- |']
    for stem, source in report['sources'].items():
        lines.append(f"| {stem} | {source['model']} | {source['mission']} | {source['palette']} | "
                     + ('unavailable; textures must decode independently' if source['palette_error'] else 'yes') + ' |')
    lines += ['', 'The JSON receipt records mission/member/palette/DML/DIG/bitmap hashes, mounted volumes, '
              'mount warnings and any earlier candidate failures.', '', '## Team comparisons', '']
    for pair in report['pairs']:
        lines += [f"### {pair['left']} / {pair['right']}", '',
                  f"Decision: {pair['decision']}. Triangles: {pair['triangles']}; tolerance {pair['tolerance']}; "
                  f"maximum matched coordinate delta {pair['max_matched_coordinate_delta']}.",
                  f"Exported geometry hashes: `{pair['geometry_sha256'][0]}`, `{pair['geometry_sha256'][1]}`.", '']
        if pair['equal']:
            lines += [f"Catalog name: `{pair['neutral']}`. Slot numbers are zero-based.", '',
                      '| Slot | Default texture | Other team slot(s) | Other team texture(s) | Changed |',
                      '| --- | --- | --- | --- | --- |']
            for slot in pair['slots']:
                lines.append(f"| {slot['slot']} | {slot['texture']} | {slot['alternate_slots']} | "
                             f"{', '.join(slot['alternate_textures'])} | {slot['differs']} |")
            lines += ['', f"UV arrays equal in exported order: {pair['uv_arrays_equal']}. "
                      'Reordered/different UVs or multiple alternate slots need visual reskin review.', '']
    lines += ['## Texture collisions', '', '| Building | Requested | Stored | Reused pixels |', '| --- | --- | --- | --- |']
    for texture in report['textures']:
        if texture['collision']:
            lines.append(f"| {texture['building']} | {texture['requested']} | {texture['stored']} | {texture['reused']} |")
    lines += ['', '## Unplaced interiors', '']
    lines += [f"- `{stem}`: {', '.join(archives)}. No placing mission/palette: not exported."
              for stem, archives in report['unplaced'].items()]
    lines += ['', 'Review rendered geometry, all team reskins and the receipt before committing the model JSONs, '
              'PNGs and static/t1-buildings receipt. Conversion is not visual acceptance.', '']
    return '\n'.join(lines).encode('utf-8')


def write_catalog(static, game):
    """A local game's catalog.json (local-data/<game>): every converted model, its family the mission that places it."""
    static, families = Path(static), {}
    for path in (static / 't1-buildings').glob('*.json'):
        for source in json.loads(path.read_text(encoding='utf-8'))['sources'].values():
            families.setdefault(source['model'], Path(source['mission']).stem)
    entries = []
    for name in t1_catalog_names(static / 'model_json'):
        textures = json.loads((static / 'model_json' / (name + '.json')).read_text(encoding='utf-8'))['material_textures']
        entries.append(dict(model_name=name, display_name=name, game=game, category=families.get(name, 'Other'),
                            texture_name=next((t for t in textures if t and not t.startswith('[')), ''), status='ready'))
    write_json_atomic(static / 'catalog.json', entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-base', required=True, type=Path, help='read-only Tribes install or base folder')
    parser.add_argument('--mission', required=True, type=Path, action='append', help='mission file or folder; repeat in preferred pack order')
    parser.add_argument('--static', type=Path, default=ROOT / 'static', help='catalog destination (default: repository static/)')
    parser.add_argument('--report', type=Path, help='also write a JSON receipt and sibling .md handoff; never overwrite either')
    parser.add_argument('--resources', type=Path, action='append', help='another mod folder the missions mount volumes from (e.g. dox, lt); repeatable')
    parser.add_argument('--catalog', metavar='GAME', help="also write --static/catalog.json for a local game (e.g. ge, with --static "
                                                          "<local-data>/ge); each model's family is its placing mission")
    args = parser.parse_args()
    if args.report and args.report.suffix.lower() != '.json':
        parser.error('--report must end in .json (its companion handoff ends in .md)')
    try:
        shipped = ROOT / 'static'
        # A local game's folder holds only what the shipped T1 catalog lacks.
        also_skip = [*t1_catalog_names(shipped / 'model_json'),  # and the stems shipped under another name (be_rig -> rig)
                     *(stem for path in (shipped / 't1-buildings').glob('*.json')
                       for stem in json.loads(path.read_text(encoding='utf-8'))['sources'])] if args.static.resolve() != shipped.resolve() else ()
        report = add_buildings(args.game_base, args.mission, args.static, args.resources or (), also_skip, args.catalog or 't1')
        if args.report:
            write_new(args.report, encoded(report))
            write_new(args.report.with_suffix('.md'), handoff(report))
        if args.catalog:
            write_catalog(args.static, args.catalog)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(f"Added {len(report['models'])} models from {len(report['sources'])} interiors; skipped {len(report['skipped'])} existing.")
    for stem, source in report['sources'].items():
        print(f"{stem} -> {source['model']}: {source['mission']} / {source['palette']}")
    for pair in report['pairs']:
        print(pair['left'], '/', pair['right'], ':', pair['decision'])
    for stem, archives in report['unplaced'].items():
        print(f'UNPLACED: {stem}: {archives}; no mission palette chosen')
    for stem, failures in report['failed'].items():
        print(f'FAILED: {stem}: {failures}')


if __name__ == '__main__':
    main()
