"""Sample native T2 DTS/DSQ poses; bake caches without redistributing the kit.

SKINNER_T2_GAME_DATA and SKINNER_T2_KIT override the documented local inputs.
Run python -m tools.animate_t2 --bake light_male weapon_disc to make portable caches.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys

import numpy as np

from tools import import_t2


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME_DATA = Path('C:/Dynamix/Tribes2/GameData')
DEFAULT_KIT = Path('C:/Users/c/ArenaPrototypeContainer/TOOLS/t2port-kit/tools')


def retain_animation_arrays(name, source):
    if name != 't2_dts_read':
        return source
    old = '''        a.f32(nUni)
        for _ in range(nAli):
            a.f32(3)
        for _ in range(nArb):
            a.f32(3)
        for _ in range(nArb):
            a.g16(4)'''
    new = '''        s["uniformScales"] = a.f32(nUni)
        s["alignedScales"] = [a.f32(3) for _ in range(nAli)]
        s["arbScaleFactors"] = [a.f32(3) for _ in range(nArb)]
        s["arbScaleRots"] = [a.g16(4) for _ in range(nArb)]'''
    if source.count(old) != 1:
        raise ValueError('T2 kit reader changed: inspect animation scale retention')
    return source.replace(old, new)


def frame_worlds(shape, data, sequence, frame, mapping=None):
    """Torque track addressing, blend matrices and parent order at authored keys."""
    count = sequence['numKeyframes']
    blend = bool(sequence.get('flags', 0) & 8)
    mapping = list(range(len(shape['nodes']))) if mapping is None else mapping
    rotations = [(0, 0, 0, 32767)] * len(shape['nodes']) if blend else list(shape['defaultRotations'])
    translations = [(0, 0, 0)] * len(shape['nodes']) if blend else list(shape['defaultTranslations'])
    for field, values, source_key, base in (
        ('rotationMatters', rotations, 'rotations', 'baseRotation'),
        ('translationMatters', translations, 'translations', 'baseTranslation'),
    ):
        for rank, source_node in enumerate(sequence.get(field, [])):
            node = mapping[source_node]
            if node >= 0:
                values[node] = data[source_key][sequence[base] + rank * count + frame]
    local_shape = dict(shape, nodes=[(n[0], -1) for n in shape['nodes']],
                       defaultRotations=rotations, defaultTranslations=translations)
    local = import_t2.node_matrices(local_shape)
    for rank, source_node in enumerate(sequence.get('scaleMatters', [])):
        node = mapping[source_node]
        if node < 0:
            continue
        index = sequence['baseScale'] + rank * count + frame
        flags = sequence['flags']
        if flags & 1:
            scale = np.eye(3) * data['uniformScales'][index]
        elif flags & 2:
            scale = np.diag(data['alignedScales'][index])
        elif flags & 4:
            rotation = import_t2.node_matrices({'nodes': [(0, -1)],
                'defaultRotations': [data['arbScaleRots'][index]],
                'defaultTranslations': [(0, 0, 0)]})[0][:3, :3]
            scale = rotation @ np.diag(data['arbScaleFactors'][index]) @ rotation.T
        else:
            raise ValueError('Scale track has no scale encoding flag')
        local[node][:3, :3] = local[node][:3, :3] @ scale
    if blend:
        rest = import_t2.node_matrices(dict(shape, nodes=local_shape['nodes']))
        local = [a @ b for a, b in zip(rest, local)]
    worlds, visiting = {}, set()

    def world(node):
        if node in visiting:
            raise ValueError('Cyclic node hierarchy')
        if node not in worlds:
            visiting.add(node)
            parent = shape['nodes'][node][1]
            worlds[node] = world(parent) @ local[node] if parent >= 0 else local[node]
            visiting.remove(node)
        return worlds[node]

    return [world(i) for i in range(len(local))]


def _layout(shape, detail_index, allowed_materials=None):
    """Keep the preview's triangle order, material slots and default visibility."""
    detail = shape['details'][detail_index]
    sub, lod = detail['subShapeNum'], detail['objectDetailNum']
    first = shape['subShapeFirstObject'][sub]
    layout = []
    for oi in range(first, first + shape['subShapeNumObjects'][sub]):
        obj = shape['objects'][oi]
        state = shape['objectStates'][oi] if oi < len(shape['objectStates']) else (1065353216, 0, 0)
        hidden = import_t2.struct.unpack('<f', import_t2.struct.pack('<I', state[0] & 0xffffffff))[0] <= 0
        if (hidden and allowed_materials is None) or lod >= obj[1]:
            continue
        mesh = shape['meshes'][obj[2] + lod]
        if mesh['type'] in (2, 4):
            continue
        if hidden and any(material not in allowed_materials for _, _, _, material in import_t2.triangles(mesh)):
            continue
        source, seen = mesh, set()
        while not source.get('verts') and source.get('parent', -1) >= 0:
            parent = source['parent']
            if parent in seen:
                raise ValueError('Cyclic mesh parent')
            seen.add(parent)
            source = shape['meshes'][parent]
        if source.get('verts'):
            indices = np.array([v for a, b, c, _ in import_t2.triangles(mesh) for v in (a, b, c)], dtype=np.int64)
            if len(indices):
                layout.append((oi, obj[3], mesh, source, indices, state))
    return layout


def sample_clip(shape, data, sequence, layout, name, mapping=None):
    count = sequence['numKeyframes']
    if count < 1 or sequence['duration'] <= 0:
        raise ValueError('Sequence has no positive key count/duration: ' + name)
    object_members = sorted(set(sequence.get('visMatters', [])) |
                            set(sequence.get('frameMatters', [])) |
                            set(sequence.get('matFrameMatters', [])))
    if mapping is None and object_members and shape.get('version', 25) < 18:
        raise ValueError('DTS v15/v16 animated object-state offsets are not decoded by the external reader; only static geometry can currently be exported')
    frames = []
    for frame in range(count):
        worlds = frame_worlds(shape, data, sequence, frame, mapping)
        vertices, normals = [], []
        for oi, node, mesh, source, indices, default_state in layout:
            state = default_state
            vertex_frame = default_state[1]
            if mapping is None and oi in object_members:
                state_index = sequence['baseObjectState'] + object_members.index(oi) * count + frame
                animated_state = shape['objectStates'][state_index]
                state = (animated_state[0] if oi in sequence.get('visMatters', []) else state[0],
                         animated_state[1] if oi in sequence.get('frameMatters', []) else state[1], state[2])
                vertex_frame = state[1]
            points, norms = import_t2.posed_vertices(source, node, worlds)
            offset = vertex_frame * (mesh.get('vertsPerFrame') or len(points))
            selected = points[indices + offset]
            visibility = import_t2.struct.unpack('<f', import_t2.struct.pack('<I', state[0] & 0xffffffff))[0]
            if visibility <= 0:
                selected = np.tile(selected.mean(axis=0), (len(selected), 1))
            vertices.extend(selected.ravel().tolist())
            normals.extend(norms[indices + offset].ravel().tolist())
        frames.append({'vertices': vertices, 'normals': normals})
    loop = bool(sequence.get('flags', 0) & 16)
    if loop and count > 1:
        frames.append(frames[0])
    return {'name': name, 'fps': max(1, len(frames) - 1) / sequence['duration'],
            'duration': sequence['duration'], 'loop': loop, 'frames': frames,
            'blend_baked_on_default_pose': bool(sequence.get('flags', 0) & 8)}


def sample_cached_model(preview_data, cached):
    """Expand portable native arrays using this sampler, without the external kit."""
    shape = cached['shape']
    base, detail, _, _ = import_t2.geometry(shape)
    if len(base['vertices']) != len(preview_data['vertices']) or not np.allclose(base['vertices'], preview_data['vertices'], atol=1e-5):
        raise ValueError('T2 source pose does not match preview geometry; rebuild the catalog')
    material_map = {item['original_slot']: i for i, item in enumerate(preview_data['metadata'].get('material_resolution', []))}
    layout = _layout(shape, detail, allowed_materials=material_map)
    embedded = dict(shape, rotations=shape.get('seqRotations', []), translations=shape.get('seqTranslations', []))
    result = dict(preview_data, animation_clips=[])
    notes = list(cached['animation_notes'])
    if sum(len(item[4]) for item in layout) * 3 != len(base['vertices']):
        default = sample_clip(shape, {}, {'numKeyframes': 1, 'duration': 1}, layout, 'default')['frames'][0]
        result.update(default, indices=[], uvs=[], groups=[])
        for _, _, mesh, mesh_source, indices, state in layout:
            frame_size = mesh.get('vertsPerFrame') or len(mesh_source['verts']) // 3
            uv = np.asarray(mesh_source['tverts']).reshape(-1, 2)
            result['uvs'].extend(uv[indices + state[2] * frame_size].ravel().tolist())
            for _, _, _, material in import_t2.triangles(mesh):
                slot = material_map[material]
                if not result['groups'] or result['groups'][-1]['materialIndex'] != slot:
                    result['groups'].append({'start': len(result['indices']), 'count': 0, 'materialIndex': slot})
                result['indices'].extend(range(len(result['indices']), len(result['indices']) + 3))
                result['groups'][-1]['count'] += 3
    included = {shape['names'][shape['objects'][item[0]][0]] for item in layout}
    omitted = set(preview_data['metadata'].get('hidden_by_default', [])) - included
    if omitted:
        notes.append('Hidden objects needing additional material slots remain omitted: ' + ', '.join(sorted(omitted)))
    used = set()
    for item in cached['sequences']:
        data, seq, clip_name, mapping = item
        data = embedded if data is None else data
        original, suffix = clip_name, 2
        while clip_name in used:
            clip_name = original + '_' + str(suffix)
            suffix += 1
        try:
            result['animation_clips'].append(sample_clip(shape, data, seq, layout, clip_name, mapping))
            used.add(clip_name)
        except Exception as exc:
            notes.append('Animation unavailable: ' + clip_name + ': ' + str(exc))
    if cached['sequences'] and not result['animation_clips']:
        result = dict(preview_data, animation_clips=[], animation_status='static-fallback')
        notes.append('Static GLB only: native clips could not be retained; see the animation-unavailable details above.')
    result['animation_notes'] = notes
    return result


def load_animated_model(name, source_path=None, preview_data=None, *, kit=None, use_cache=True, context=None, native_cache=False):
    if preview_data is None:
        preview_data = json.loads((ROOT / 'static/t2/model_json' / (name + '.json')).read_text())
    result = dict(preview_data, animation_clips=[])
    metadata = preview_data.get('metadata', {})
    source = metadata.get('source', {})
    if source.get('format') == 'DIF' or name.startswith('interior_'):
        result['animation_notes'] = ['DIF interiors are static geometry; no character animation tracks.']
        return result
    cache_root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else ROOT
    cache = cache_root / 'local-data/animations/t2' / (name + '.json.gz')
    if use_cache and cache.is_file():
        with gzip.open(cache, 'rt', encoding='utf-8') as stream:
            cached = json.load(stream)
        if cached.get('source_sha256') == source.get('source_sha256'):
            if cached.get('schema') == 't2-native-animation-v1':
                return sample_cached_model(preview_data, cached)
            result.update({k: cached[k] for k in ('animation_clips', 'animation_notes')})
            return result
    root = Path(source_path or os.environ.get('SKINNER_T2_GAME_DATA', DEFAULT_GAME_DATA))
    kit = Path(kit or os.environ.get('SKINNER_T2_KIT', DEFAULT_KIT))
    if context is None:
        if not root.is_dir() or not (kit / 't2_dts_read.py').is_file():
            raise ValueError('T2 animated export needs its animation cache or original game data and reader kit. '
                             'Set SKINNER_T2_GAME_DATA and SKINNER_T2_KIT; see T2_IMPORT.md.')
        assets, _ = import_t2.inventory(root)
        reader, dsq_reader, _ = import_t2.load_readers(kit, assets, source_transform=retain_animation_arrays)
    else:
        assets, reader, dsq_reader = context
    key = source.get('source_path', 'shapes/' + name + '.dts')
    if key not in assets:
        raise ValueError('Original T2 source missing: ' + key)
    if source.get('source_sha256') and import_t2.digest(assets[key]['read']()) != source['source_sha256']:
        raise ValueError('T2 source differs from preview; rebuild the catalog before animated export: ' + key)
    shape = reader.read(key)
    if shape.get('seqError'):
        raise ValueError('Cannot read embedded T2 animations: ' + shape['seqError'])
    notes = ['Geometry animation sampled at every authored keyframe; interpolation between keys uses morph targets.',
             'Binary visibility is baked as collapsed triangles at hidden keys; partial alpha fades, UV/material frames, IFL textures and event triggers are not represented.',
             'Ground-path displacement is gameplay movement and is not applied to these in-place clips. Blend clips are baked over the default pose.']
    pending = [(None, seq, shape['names'][seq['nameIndex']], None) for seq in shape.get('sequences', [])]
    aliases = {entry['path']: entry['alias'] for entry in metadata.get('script_sequence_bindings', [])}
    for path in sorted(set(metadata.get('external_sequences', [])) | set(aliases)):
        if path not in assets:
            notes.append('Missing external animation: ' + path)
            continue
        try:
            dsq = dsq_reader.read(path)
            mapping = dsq_reader.bind(dsq, [shape['names'][n[0]] for n in shape['nodes']])
            if not mapping['matched']:
                raise ValueError('No matching shape nodes')
            if mapping['unmatched']:
                notes.append(path + ': unbound nodes: ' + ', '.join(mapping['unmatched']))
            for seq in dsq['sequences']:
                clip_name = aliases.get(path, seq['name'])
                pending.append((dsq, seq, clip_name, mapping['nodeMap']))
        except Exception as exc:
            notes.append('External animation unavailable: ' + path + ': ' + str(exc))
    cached = {'schema': 't2-native-animation-v1', 'source_sha256': source.get('source_sha256'),
              'shape': {k: v for k, v in shape.items() if not k.startswith('_')},
              'sequences': pending, 'animation_notes': notes}
    return cached if native_cache else sample_cached_model(preview_data, cached)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bake', nargs='*', required=True, help='Model names; omit names for every ready DTS')
    parser.add_argument('--game-data', type=Path, default=Path(os.environ.get('SKINNER_T2_GAME_DATA', DEFAULT_GAME_DATA)))
    parser.add_argument('--kit', type=Path, default=Path(os.environ.get('SKINNER_T2_KIT', DEFAULT_KIT)))
    parser.add_argument('--output', type=Path, default=ROOT / 'local-data/animations/t2')
    args = parser.parse_args()
    assets, _ = import_t2.inventory(args.game_data)
    reader, dsq_reader, _ = import_t2.load_readers(args.kit, assets, source_transform=retain_animation_arrays)
    names = args.bake or [p.stem for p in sorted((ROOT / 'static/t2/model_json').glob('*.json')) if not p.stem.startswith('interior_')]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    for name in names:
        cache = load_animated_model(name, args.game_data, use_cache=False, context=(assets, reader, dsq_reader), native_cache=True)
        with gzip.open(output / (name + '.json.gz'), 'wt', encoding='utf-8', compresslevel=6) as stream:
            json.dump(cache, stream, separators=(',', ':'), allow_nan=False)
        print(name, len(cache['sequences']), 'clips', flush=True)
