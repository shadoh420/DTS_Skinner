"""Build the T2 catalog from a read-only install; the external kit is never bundled.

Run: python tools/import_t2.py --game-data C:/Dynamix/Tribes2/GameData --kit PATH
The generated viewer files do not depend on the kit or installed game at runtime.
"""
import argparse
import collections
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import types
import zipfile

import numpy as np
from PIL import Image


def digest(data):
    return hashlib.sha256(data).hexdigest()


def natural(name):
    return tuple(int(s) if s.isdigit() else s.casefold() for s in re.split(r'(\d+)', name)), name.casefold(), name


def safe_name(name):
    return re.sub(r'[^a-z0-9_.-]', '_', name.lower()).strip('.') or 'asset'


def load_readers(kit, assets, source_transform=None):
    """Load user-provided readers without writing into their original directory."""
    modules, provenance = {}, {}
    for name in ('t2_dts_legacy_read', 't2_dts_read', 't2_dsq_read', 't2_dif_read'):
        path = kit / (name + '.py')
        raw = path.read_bytes()
        source = raw.decode('utf-8-sig')
        adaptations = []
        if name == 't2_dts_read':
            # Preserve these fields discarded by the loader-specific kit reader.
            replacements = {
                'a.f32(3 * sz)                # initial norms': 'm["initialNormals"] = a.f32(3 * sz) # initial norms',
                'a.g32(nDetails)                  # detailFirstSkin': 's["detailFirstSkin"] = a.g32(nDetails)',
                'a.g32(nDetails)                  # detailNumSkins': 's["detailNumSkins"] = a.g32(nDetails)',
                'a.g32(nIfl * 5)': 's["iflMaterials"] = a.g32(nIfl * 5)',
            }
            for old, new in replacements.items():
                if source.count(old) != 1:
                    raise ValueError('Kit reader changed; inspect adaptation: ' + old)
                source = source.replace(old, new)
                adaptations.append(new)
        if source_transform is not None:
            source = source_transform(name, source)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.open = lambda key, mode='rb': io.BytesIO(assets[str(key)]['read']())
        sys.modules[name] = module
        exec(compile(source, str(path), 'exec'), module.__dict__)
        modules[name] = module
        provenance[name] = {'sha256': digest(raw), 'adaptations': adaptations}
    return modules['t2_dts_read'], modules['t2_dsq_read'], provenance


def inventory(root):
    """Explicit viewer policy: later archive path wins, then loose files win.

    This reproducible policy is not a claim about native engine mount order.
    Every competing virtual path is retained in provenance.
    """
    assets, archives, counts = {}, [], collections.Counter()
    wanted = {'.dts', '.dsq', '.dif', '.png', '.jpg', '.jpeg', '.bmp', '.bm8', '.dds', '.tga', '.ifl', '.dml', '.cs'}

    def add(key, record):
        key = key.replace('\\', '/').lower().lstrip('/')
        previous = assets.get(key)
        record['alternatives'] = ([] if previous is None else previous['alternatives'] + [previous['source']])
        assets[key] = record

    for path in sorted(root.rglob('*.vl2'), key=lambda p: str(p.relative_to(root)).casefold()):
        archive = zipfile.ZipFile(path)
        count = collections.Counter()
        for entry in archive.infolist():
            ext = Path(entry.filename).suffix.lower()
            counts[ext] += 1
            count[ext] += 1
            if ext not in wanted:
                continue
            key = entry.filename.replace('\\', '/').lower()
            add(key, {'source': str(path.relative_to(root)).replace('\\', '/') + ':' + entry.filename,
                      'size': entry.file_size, 'read': lambda z=archive, n=entry.filename: z.read(n)})
        archives.append({'path': str(path.relative_to(root)).replace('\\', '/'), 'counts': dict(count)})
    loose = collections.Counter()
    for path in sorted(root.rglob('*'), key=lambda p: str(p).casefold()):
        if not path.is_file() or path.suffix.lower() not in wanted:
            continue
        relative = path.relative_to(root)
        key = '/'.join(relative.parts[1:]) if relative.parts[0].lower() in ('base', 'classic') else relative.as_posix()
        add(key, {'source': 'loose:' + relative.as_posix(), 'size': path.stat().st_size, 'read': path.read_bytes})
        loose[path.suffix.lower()] += 1
    return assets, {'archives': archives, 'archive_entry_counts': dict(counts), 'loose_counts': dict(loose)}


def node_matrices(shape):
    result, visiting = {}, set()

    def visit(i):
        if i in result:
            return result[i]
        if i in visiting or not 0 <= i < len(shape['nodes']):
            raise ValueError('Invalid node hierarchy')
        visiting.add(i)
        q = np.array(shape['defaultRotations'][i], dtype=float) / 32767
        q[:3] *= -1  # Torque stores the conjugate of the mathematical quaternion.
        length = np.linalg.norm(q)
        if length < 1e-8:
            q = np.array([0., 0., 0., 1.])
        else:
            q /= length
        x, y, z, w = q
        m = np.eye(4)
        m[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
        m[:3, 3] = shape['defaultTranslations'][i]
        parent = shape['nodes'][i][1]
        if parent >= 0:
            m = visit(parent) @ m
        visiting.remove(i)
        result[i] = m
        return m

    return [visit(i) for i in range(len(shape['nodes']))]


def triangles(mesh):
    """Decode indexed/unindexed lists, strips and fans, changing CW to CCW."""
    for start, count, raw in mesh['prims']:
        flags = raw & 0xffffffff
        kind, material = flags & 0xc0000000, flags & 0xfffffff
        if flags & 0x10000000:  # NoMaterial: caller creates explicit untextured slot.
            material = -1
        sequence = mesh['indices'][start:start+count] if flags & 0x20000000 else list(range(start, start+count))
        if len(sequence) != count:
            raise ValueError('Primitive outside index buffer')
        if kind == 0:
            source = [sequence[i:i+3] for i in range(0, count-2, 3)]
        elif kind == 0x40000000:
            source = [(sequence[i+1], sequence[i], sequence[i+2]) if i % 2 else sequence[i:i+3] for i in range(count-2)]
        elif kind == 0x80000000:
            source = [(sequence[0], sequence[i], sequence[i+1]) for i in range(1, count-1)]
        else:
            raise ValueError('Unsupported primitive type')
        for a, b, c in source:
            if len({a, b, c}) == 3:
                yield (a, c, b, material)


def posed_vertices(source, node, worlds):
    vertices = np.array(source.get('initialVerts') or source['verts'], dtype=float).reshape(-1, 3)
    normals = np.array(source.get('initialNormals') or source.get('norms'), dtype=float).reshape(-1, 3)
    if len(vertices) != len(normals):
        raise ValueError('Missing authored normals')
    if source.get('type') == 1:
        # Skin initial transforms are row-major inverse bind matrices in Torque.
        matrices = np.array(source['initialTransforms']).reshape(-1, 4, 4)
        skin = [worlds[index] @ bind for index, bind in zip(source['nodeIndex'], matrices)]
        output, normout, total = np.zeros_like(vertices), np.zeros_like(normals), np.zeros(len(vertices))
        for v, bone, weight in zip(source['vertexIndex'], source['boneIndex'], source['weight']):
            output[v] += weight * (skin[bone] @ np.append(vertices[v], 1))[:3]
            normout[v] += weight * (np.linalg.inv(skin[bone][:3, :3]).T @ normals[v])
            total[v] += weight
        if np.any(np.abs(total - 1) > .005):
            raise ValueError('Skin has invalid weight sums')
        vertices, normals = output, normout
    else:
        transform = worlds[node] if node >= 0 else np.eye(4)
        vertices = vertices @ transform[:3, :3].T + transform[:3, 3]
        normals = normals @ np.linalg.inv(transform[:3, :3])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-12)
    # Right-handed Z-up -> Three.js right-handed Y-up; determinant remains +1.
    return vertices[:, [0, 2, 1]] * [1, 1, -1], normals[:, [0, 2, 1]] * [1, 1, -1]


def geometry(shape):
    worlds = node_matrices(shape)
    visible = sorted([(i, d) for i, d in enumerate(shape['details'])
                      if d['subShapeNum'] >= 0 and d['size'] >= 0
                      and not shape['names'][d['name']].lower().startswith(('collision', 'los'))],
                     key=lambda item: -item[1]['size'])
    hidden, empty = set(), []
    for detail_index, detail in visible:
        sub, lod = detail['subShapeNum'], detail['objectDetailNum']
        output = {'vertices': [], 'normals': [], 'uvs': [], 'indices': [], 'groups': [], 'winding': 'ccw'}
        first = shape['subShapeFirstObject'][sub]
        for oi in range(first, first + shape['subShapeNumObjects'][sub]):
            obj = shape['objects'][oi]
            name, count, start, node = obj[:4]
            state = shape.get('objectStates', [])[oi] if oi < len(shape.get('objectStates', [])) else (1065353216, 0, 0)
            visibility = struct.unpack('<f', struct.pack('<I', state[0] & 0xffffffff))[0]
            if visibility <= 0:
                hidden.add(shape['names'][name])
                continue
            if lod >= count:
                continue
            mesh = shape['meshes'][start + lod]
            if mesh['type'] in (2, 4):
                continue
            if mesh['type'] not in (0, 1, 3):
                raise ValueError('Unsupported mesh type ' + str(mesh['type']))
            source, seen = mesh, set()
            while not source.get('verts') and source.get('parent', -1) >= 0:
                parent = source['parent']
                if parent in seen:
                    raise ValueError('Cyclic mesh parent')
                seen.add(parent)
                source = shape['meshes'][parent]
            if not source.get('verts'):
                continue
            points, normals = posed_vertices(source, node, worlds)
            uv = np.array(source['tverts']).reshape(-1, 2)
            frame_size = mesh.get('vertsPerFrame') or len(points)
            vertex_offset = state[1] * frame_size
            uv_offset = state[2] * frame_size
            for a, b, c, material in triangles(mesh):
                if not output['groups'] or output['groups'][-1]['materialIndex'] != material:
                    output['groups'].append({'start': len(output['indices']), 'count': 0, 'materialIndex': material})
                for vertex in (a, b, c):
                    if vertex + vertex_offset >= len(points) or vertex + uv_offset >= len(uv):
                        raise ValueError('Vertex/UV frame outside source buffer')
                    output['vertices'].extend(points[vertex + vertex_offset].tolist())
                    output['normals'].extend(normals[vertex + vertex_offset].tolist())
                    output['uvs'].extend(uv[vertex + uv_offset].tolist())
                    output['indices'].append(len(output['indices']))
                output['groups'][-1]['count'] += 3
        if output['indices']:
            return output, detail_index, sorted(hidden), empty
        empty.append(detail_index)
    raise ValueError('No visible geometry in nonnegative-size authored details; collision/utility details excluded')


class Textures:
    def __init__(self, assets, output):
        self.assets, self.output = assets, output
        self.basenames = collections.defaultdict(list)
        for key in assets:
            self.basenames[Path(key).name].append(key)
        self.copied = {}

    def resolve(self, name, depth=0):
        if depth > 4:
            return None, {'reason': 'Texture frame-list cycle'}
        stem = name.replace('\\', '/').lower()
        extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.tga', '.dds', '.bm8', '.ifl', '.dml')
        explicit_ext = Path(stem).suffix
        if explicit_ext in extensions:
            stem = stem.rsplit('.', 1)[0]
            extensions = (explicit_ext,) + tuple(ext for ext in extensions if ext != explicit_ext)
        exact = next((key for ext in extensions for key in
                      (stem + ext, 'textures/' + stem + ext, 'textures/skins/' + stem + ext)
                      if key in self.assets), None)
        candidates = [exact] if exact else []
        if not candidates:
            for ext in extensions:
                candidates.extend(self.basenames.get(Path(stem + ext).name, []))
                if candidates:
                    break
        candidates = sorted(set(candidates), key=lambda k: (not k.startswith('textures/skins/'), not k.startswith('textures/'), k))
        if not candidates:
            return None, {'reason': 'No matching source image or frame list', 'material': name}
        key = candidates[0]
        record = self.assets[key]
        evidence = {'virtual_path': key, 'source': record['source'], 'alternatives': record['alternatives'], 'ambiguous_paths': candidates[1:]}
        if key.endswith(('.ifl', '.dml')):
            frames = [line.split('//')[0].strip() for line in record['read']().decode('latin1').splitlines()]
            frames = [line for line in frames if line]
            if not frames:
                return None, dict(evidence, reason='Empty texture frame list')
            filename, child = self.resolve(frames[0].split()[0].strip('"'), depth + 1)
            return filename, dict(evidence, frames=frames, frame_zero=child)
        if key in self.copied:
            return self.copied[key], evidence
        raw = record['read']()
        filename = safe_name(Path(key).stem) + '__' + digest(raw)[:10] + '.png'
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if not (self.output / filename).exists():
                    im.convert('RGBA').save(self.output / filename)
        except Exception as exc:
            return None, dict(evidence, reason='Unsupported image: ' + str(exc))
        self.copied[key] = filename
        return filename, dict(evidence, sha256=digest(raw))


def category(name):
    if any(word in name for word in ('male', 'female', 'bioderm', 'player')):
        return 'Players'
    if name.startswith(('weapon', 'tr2weapon', 'ammo', 'grenade', 'mine', 'disc', 'bomb')):
        return 'Weapons & ammunition'
    if name.startswith(('vehicle', 'turret', 'pack', 'deploy', 'station', 'sensor')):
        return 'Vehicles & equipment'
    if any(word in name for word in ('effect', 'explosion', 'bolt', 'shot', 'debris')):
        return 'Effects'
    return 'World & props'


def attach_materials(data, names, flags, record, metadata, resolver):
    slots = sorted({g['materialIndex'] for g in data['groups']})
    textures, used_flags, used_names = [], [], []
    metadata['material_resolution'] = []
    for slot in slots:
        material = names[slot] if 0 <= slot < len(names) else '[Untextured]'
        filename, resolution = resolver.resolve(material) if material != '[Untextured]' else (None, {'reason': 'Authored no-material primitive'})
        metadata['material_resolution'].append(dict(resolution, name=material, original_slot=slot))
        textures.append(filename or ('[Slot ' + str(len(textures)) + ': untextured]' if slot == -1 else safe_name(material) + '__missing.png'))
        used_flags.append(flags[slot] if 0 <= slot < len(flags) else 0)
        used_names.append(material)
        if filename is None and slot != -1:
            record['warnings'].append('Missing texture: ' + material + ' (' + resolution['reason'] + ')')
        if resolution.get('frames'):
            record['warnings'].append('Animated texture uses frame zero: ' + material)
        if resolution.get('ambiguous_paths'):
            record['warnings'].append('Multiple texture paths: ' + material + '; selected ' + resolution['virtual_path'])
    for group in data['groups']:
        group['materialIndex'] = slots.index(group['materialIndex'])
    data.update(material_textures=textures, material_flags=used_flags, material_names=used_names)
    record['materials'] = used_names


def build(root, kit, output):
    assets, report = inventory(root)
    reader, dsq_reader, provenance = load_readers(kit, assets)
    model_dir, texture_dir = output / 'static/t2/model_json', output / 'static/textures/t2'
    model_dir.mkdir(parents=True, exist_ok=True)
    texture_dir.mkdir(parents=True, exist_ok=True)
    texture_resolver = Textures(assets, texture_dir)
    skin_variants = []
    for key in sorted(assets):
        if '/skins/' in key and key.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.dds', '.tga', '.bm8')):
            filename, evidence = texture_resolver.resolve(key)
            skin_variants.append({'path': key, 'file': filename, 'resolution': evidence})
    source_models = sorted((key for key in assets if key.endswith('.dts')), key=natural)
    stems = collections.Counter(Path(key).stem for key in source_models)
    constructors = collections.defaultdict(list)
    for path, asset in assets.items():
        if not path.endswith('.cs'):
            continue
        text = re.sub(r'//[^\r\n]*', '', asset['read']().decode('latin1'))
        for block in re.findall(r'datablock\s+TSShapeConstructor\([^)]*\)\s*\{(.*?)\}', text, re.I | re.S):
            base = re.search(r'baseShape\s*=\s*"([^"]+)"', block, re.I)
            if base:
                for index, filename, alias in re.findall(r'sequence(\d+)\s*=\s*"([^"\s]+\.dsq)\s+([^"]+)"', block, re.I):
                    constructors[Path(base[1]).stem.lower()].append({'index': int(index), 'path': 'shapes/' + filename.lower(), 'alias': alias, 'script': path, 'source': asset['source']})
    external = {}
    for key in sorted(assets):
        if not key.endswith('.dsq'):
            continue
        raw = assets[key]['read']()
        entry = {'path': key, 'source': assets[key]['source'], 'sha256': digest(raw), 'bytes': len(raw)}
        try:
            parsed = dsq_reader.read(key)
            entry.update(version=parsed['version'], node_names=parsed['nodeNames'], sequences=parsed['sequences'])
        except Exception as exc:
            entry['error'] = str(exc)
        external[key] = entry
    catalog = []
    metadata_dir = output / 'static/t2/metadata'
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for key in source_models:
        raw = assets[key]['read']()
        stem = Path(key).stem
        name = safe_name(stem) + ('__' + digest(key.encode())[:8] if stems[stem] > 1 else '')
        record = {'model_name': name, 'game': 't2', 'category': category(stem), 'status': 'failed',
                  'warnings': [], 'format': 'DTS', 'source': assets[key]['source'], 'source_path': key,
                  'source_sha256': digest(raw), 'source_bytes': len(raw),
                  'version': struct.unpack_from('<I', raw)[0] & 255 if len(raw) >= 4 else None,
                  'alternatives': assets[key]['alternatives']}
        metadata = {'source': dict(record)}
        try:
            if not raw:
                record['status'] = 'unsupported'
                raise ValueError('Empty source DTS (zero bytes in installation)')
            shape = reader.read(key)
            ml = reader.find_material_list(shape) or {'names': [], 'flags': []}
            sequences = [dict(seq, name=shape['names'][seq['nameIndex']]) for seq in shape.get('sequences', [])]
            references = {path for path in external if Path(path).stem.startswith(stem + '_')}
            references.update(e['path'] for e in constructors[stem])
            linked = [external[path] for path in sorted(references) if path in external]
            for path in sorted(references):
                if path not in external:
                    record['warnings'].append('Missing external animation: ' + path)
                elif external[path].get('error'):
                    record['warnings'].append('Invalid external animation: ' + path)
            metadata = {'source': dict(record), 'pose': 'authored default node transforms / default object frame',
                        'nodes': [{'name': shape['names'][n[0]], 'parent': n[1], 'rotation_quat16': q, 'translation': t}
                                  for n, q, t in zip(shape['nodes'], shape['defaultRotations'], shape['defaultTranslations'])],
                        'details': [dict(d, name=shape['names'][d['name']]) for d in shape['details']],
                        'sequences': sequences, 'external_sequences': [e['path'] for e in linked],
                        'script_sequence_bindings': constructors[stem],
                        'animation': 'Playback deferred; embedded and external sequence descriptors retained in catalog metadata. Original DTS/DSQ remain in the read-only installation.',
                        'ifl_material_records': shape.get('iflMaterials', []), 'material_resolution': []}
            record['sequences'] = len(sequences)
            record['external_sequences'] = len(linked)
            if shape.get('seqError'):
                record['warnings'].append('Sequence parse: ' + shape['seqError'])
            if shape.get('skins'):
                raise ValueError('Legacy separately stored skin meshes require additional LOD association')
            try:
                data, detail, hidden, empty = geometry(shape)
            except ValueError as exc:
                if str(exc).startswith('No visible geometry'):
                    record['status'] = 'unsupported'
                raise
            metadata.update(selected_detail=detail, hidden_by_default=hidden, empty_finer_details=empty)
            attach_materials(data, ml['names'], ml['flags'], record, metadata, texture_resolver)
            if hidden:
                record['warnings'].append(str(len(hidden)) + ' authored hidden objects excluded from default pose')
            if empty:
                record['warnings'].append(str(len(empty)) + ' empty finer LODs skipped')
            record.update(status='ready', triangles=len(data['indices']) // 3,
                          selected_detail=metadata['details'][detail]['name'])
            metadata['source'] = dict(record)
            data.update(game='t2', metadata=metadata)
            (model_dir / (name + '.json')).write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        except Exception as exc:
            record['warnings'].append(str(exc))
        metadata['source'] = dict(record)
        (metadata_dir / (name + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        catalog.append(record)
    from tools.import_t2_dif import geometry as dif_geometry
    source_interiors = sorted((key for key in assets if key.endswith('.dif')), key=natural)
    interior_stems = collections.Counter(Path(key).stem for key in source_interiors)
    for key in source_interiors:
        raw, stem = assets[key]['read'](), Path(key).stem
        name = 'interior_' + safe_name(stem) + ('__' + digest(key.encode())[:8] if interior_stems[stem] > 1 else '')
        record = {'model_name': name, 'game': 't2', 'category': 'Interiors', 'format': 'DIF',
                  'status': 'failed', 'warnings': [], 'source': assets[key]['source'], 'source_path': key,
                  'source_sha256': digest(raw), 'source_bytes': len(raw), 'alternatives': assets[key]['alternatives']}
        metadata = {'source': dict(record)}
        try:
            data, metadata = dif_geometry(raw, sys.modules['t2_dif_read'])
            attach_materials(data, data['material_names'], data['material_flags'], record, metadata, texture_resolver)
            if metadata.get('resource_tail_error'):
                record['warnings'].append('Unparsed resource tail: ' + metadata['resource_tail_error'])
            record['warnings'].append('Base textures only; lightmaps, alarm states and resource movers deferred')
            record.update(status='ready', version=metadata['file_version'], triangles=len(data['indices']) // 3,
                          selected_detail=str(metadata['selected_detail']), sequences=0, external_sequences=0)
            metadata['source'] = dict(record)
            data.update(game='t2', metadata=metadata)
            (model_dir / (name + '.json')).write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        except Exception as exc:
            record['warnings'].append(str(exc))
        metadata['source'] = dict(record)
        (metadata_dir / (name + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        catalog.append(record)
    catalog.sort(key=lambda record: natural(record['model_name']))
    report.update(reader_provenance=provenance, source_root=str(root), source_model_count=len(source_models),
                  source_dif_count=len(source_interiors), unique_dif_count=len(source_interiors),
                  versions=dict(collections.Counter(str(r['version']) for r in catalog)),
                  statuses=dict(collections.Counter(r['status'] for r in catalog)),
                  external_sequence_files=list(external.values()),
                  selectable_skin_variants=skin_variants,
                  policy='Virtual paths are case-insensitive. Loose files override archives; later case-insensitive sorted archive paths override earlier identical virtual paths. Texture formats prefer PNG, JPG, JPEG, BMP, TGA, DDS, BM8, IFL, DML; basename ambiguity is reported. Native runtime search precedence is not asserted.',
                  animation_boundary='Default static poses only. Sequence descriptors, node bindings, flags, durations and source hashes retained; original animation samples remain available in the source installation. No T1.50-specific player contracts or sequence renaming.',
                  dif_boundary='DIF render surfaces use highest-detail base textures; lightmaps, alarm states, animated lights and resource movers deferred; null surfaces/collision hulls excluded.')
    (output / 'static/t2/catalog.json').write_text(json.dumps(catalog, indent=2), encoding='utf-8')
    (output / 'static/t2/inventory.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 't2_catalog.txt').write_text('\n'.join(r['model_name'] for r in catalog) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('source_model_count', 'versions', 'statuses', 'unique_dif_count')}, indent=2))
    print('Textures:', len(texture_resolver.copied), 'DSQ:', len(external))
    for record in catalog:
        if record['status'] != 'ready':
            print(record['model_name'], record['status'], '; '.join(record['warnings']))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    parser.add_argument('--kit', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    build(args.game_data, args.kit, args.output)
