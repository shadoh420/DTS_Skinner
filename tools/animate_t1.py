"""Bake actual T1 DTS sequences into the shared morph-animation contract.

Read-only sources: an extracted DTS/DIS, or a directory containing them.
Sequence positions, node tracks and stepped cel/visibility tracks follow
Darkstar Ts3 ts_shape.cpp / ts_shapeInst.cpp. Geometry uses Skinner's existing
LOD, bounds and corrected armor coordinate conventions.
"""
import contextlib
import copy
import io
import math
import os
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from dts_module import dts
from tools.export_model import (PLAYER_MODEL_STEMS, get_all_descendant_nodes,
                                get_matrix_from_quat_trans,
                                transpose_rotation_in_4x4)


def _source(name, source_path):
    root = Path(source_path or os.environ.get('SKINNER_T1_SOURCE', 'C:/DiscSkinner/tools'))
    if root.is_file() and root.stem.casefold() == name.casefold():
        return root
    if root.is_dir():
        matches = sorted(p for p in root.rglob('*') if p.is_file()
                         and p.suffix.casefold() in ('.dts', '.dis')
                         and p.stem.casefold() == name.casefold())
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f'Ambiguous T1 animation source for {name}')
    raise FileNotFoundError(f'T1 animation source missing for {name}: {root}')


def _track(shape, owner, sequence):
    for sub in shape.sub_sequences[owner.first_sub_seq:
                                   owner.first_sub_seq + owner.num_sub_seq]:
        if sub.sequence_idx == sequence:
            keys = shape.keyframes[sub.first_key_frame:
                                   sub.first_key_frame + sub.num_key_frames]
            if any(not math.isfinite(k.position) or not 0 <= k.position <= 1
                   for k in keys):
                raise ValueError('Invalid DTS animation key time')
            return sorted(keys, key=lambda key: key.position)
    return []


def _keys(keys, position, loop):
    """Previous/next transform keys, with native cyclic wrap and clamping."""
    before = [k for k in keys if k.position <= position]
    after = [k for k in keys if k.position > position]
    a = before[-1] if before else (keys[-1] if loop else keys[0])
    b = after[0] if after else (keys[0] if loop else keys[-1])
    ta, tb = a.position, b.position
    if loop and not before:
        ta -= 1
    if loop and not after:
        tb += 1
    weight = (position - ta) / (tb - ta) if tb > ta else 0
    return a, b, min(1., max(0., weight))


def _quaternion(transform):
    q = transform.rotate
    value = np.array([q.x, q.y, q.z, q.w], dtype=float)
    norm = np.linalg.norm(value)
    return value / norm if norm > 1e-8 else np.array([0., 0., 0., 1.])


def _matrix(a, b=None, weight=0., armor=False):
    b = a if b is None else b
    qa, qb = _quaternion(a), _quaternion(b)
    dot = float(qa @ qb)
    if dot < 0:
        qb, dot = -qb, -dot
    if dot > .9995:
        q = qa + weight * (qb - qa)
    else:
        angle = math.acos(min(1., dot))
        q = (math.sin((1 - weight) * angle) * qa +
             math.sin(weight * angle) * qb) / math.sin(angle)
    q /= np.linalg.norm(q)
    translation = np.array(a.translate) * (1 - weight) + np.array(b.translate) * weight
    scale = np.array(a.scale) * (1 - weight) + np.array(b.scale) * weight
    matrix = get_matrix_from_quat_trans(q * 32767, translation, scale)
    if armor:
        matrix = transpose_rotation_in_4x4(matrix)
    return np.array(matrix)


def load_animated_model(name, source_path, preview_data):
    """Return normalized geometry and named, timed baked source sequences."""
    path = _source(name, source_path)
    result = copy.deepcopy(preview_data)
    metadata = result.setdefault('metadata', {})
    metadata.update(game='t1', animation_source=path.name,
                    animation_export='baked morph targets; static UV/material binding')
    result['animation_clips'] = []
    if path.suffix.casefold() == '.dis':
        metadata['animation_status'] = 'Static DIS interior; no DTS model sequences'
        return result
    shape = dts()
    with contextlib.redirect_stdout(io.StringIO()):
        loaded = shape.load_file(str(path))
    if not loaded or not shape.nodes or not shape.meshes:
        raise ValueError(f'Invalid T1 DTS animation source: {path.name}')
    if not shape.sequences:
        metadata['animation_status'] = 'Source DTS has no sequences'
        return result

    armor = name.casefold() in PLAYER_MODEL_STEMS
    selected = set(range(len(shape.nodes)))
    if shape.details:
        detail = max(shape.details, key=lambda item: item.size)
        selected = get_all_descendant_nodes(shape.nodes, detail.root_node)
    if shape.always_node is not None and shape.always_node >= 0:
        selected |= get_all_descendant_nodes(shape.nodes, shape.always_node)
    objects = [obj for obj in shape.objects if obj.node_index in selected
               and 0 <= obj.mesh_index < len(shape.meshes)]
    # Each material group keeps a stable vertex/UV pair mapping in every pose.
    bindings, indices, uvs, groups = [], [], [], []
    materials = result['material_textures']
    for obj in objects:
        mesh = shape.meshes[obj.mesh_index]
        by_material = {}
        for face in mesh.faces:
            by_material.setdefault(face.mat_index, []).append(face)
        for material, faces in by_material.items():
            pairs, start = {}, len(indices)
            for face in faces:
                for vertex, uv in ((face.vert_index0, face.tex_index0),
                                   (face.vert_index1, face.tex_index1),
                                   (face.vert_index2, face.tex_index2)):
                    if not 0 <= vertex < mesh.verts_per_frame or not 0 <= uv < len(mesh.text_verts):
                        raise ValueError('DTS animation mesh vertex/UV index outside frame')
                    pair = vertex, uv
                    if pair not in pairs:
                        pairs[pair] = len(bindings)
                        bindings.append((obj, vertex))
                        uvs.extend(mesh.text_verts[uv])
                    indices.append(pairs[pair])
            if len(indices) > start:
                groups.append(dict(start=start, count=len(indices)-start,
                                   materialIndex=material if material < len(materials) else 0))
    if not bindings:
        raise ValueError('T1 animation source has no selected render geometry')
    bounds = _matrix(shape.transforms[shape.nodes[0].transform_index])
    conversion = np.array([[1, 0, 0, 0], [0, 0, 1, 0],
                           [0, -1, 0, 0], [0, 0, 0, 1]]) @ np.linalg.inv(bounds)
    limitations = ['Baked geometry animation; editable skeleton, triggers, transition blending and IFL/UV animation are not exported.']
    if any(s.num_ifl_subsequences for s in shape.sequences):
        limitations.append('Source has IFL material animation; exported textures retain the preview binding.')
    if any(k.mat_index & 0x2000 for obj in objects
           for sub in shape.sub_sequences[obj.first_sub_seq:obj.first_sub_seq+obj.num_sub_seq]
           for k in shape.keyframes[sub.first_key_frame:sub.first_key_frame+sub.num_key_frames]):
        limitations.append('Source has material/UV frame tracks; geometry frames are baked but UVs remain static.')

    def sample(node_tracks, object_tracks, position, loop):
        worlds, visible, visiting = {}, {}, set()

        def world(index):
            if index in worlds:
                return worlds[index]
            if index in visiting:
                raise ValueError('Cyclic DTS node hierarchy')
            visiting.add(index)
            node = shape.nodes[index]
            keys = node_tracks[index]
            shown = True
            if keys:
                a, b, weight = _keys(keys, position, loop)
                local = _matrix(shape.transforms[a.key_value],
                                shape.transforms[b.key_value], weight, armor)
                shown = bool(a.mat_index & 0x8000) if shape.version >= 7 else True
            else:
                local = _matrix(shape.transforms[node.transform_index], armor=armor)
            parent = node.parent_node
            if parent >= 0 and parent != index:
                local = world(parent) @ local
                shown = shown and visible[parent]
            worlds[index], visible[index] = local, shown
            visiting.remove(index)
            return local

        transformed = {}
        for obj in objects:
            transform = conversion @ world(obj.node_index)
            offset = obj.offset if shape.version >= 8 else obj.offset_rot.point
            transform = transform.copy()
            transform[:3, 3] += transform[:3, :3] @ np.array(offset)
            mesh = shape.meshes[obj.mesh_index]
            frame, shown = 0, not (obj.flags & 1)
            keys = object_tracks[id(obj)]
            if keys:
                key = _keys(keys, position, loop)[0]
                if key.mat_index & 0x1000 or shape.version < 7:
                    frame = key.key_value
                if key.mat_index & 0x4000:
                    shown = bool(key.mat_index & 0x8000)
            if not 0 <= frame < len(mesh.frames):
                raise ValueError('DTS cel animation frame outside mesh')
            source_frame = mesh.frames[frame]
            points = [v.get_unpacked_vert(source_frame.scale, source_frame.origin)
                      for v in mesh.verts[source_frame.first_vert:
                                          source_frame.first_vert+mesh.verts_per_frame]]
            points = np.array(points) @ transform[:3, :3].T + transform[:3, 3]
            if not shown or not visible[obj.node_index]:
                points[:] = transform[:3, 3]  # Degenerate triangles preserve visibility in a morph mesh.
            transformed[id(obj)] = points
        return {'vertices': [float(value) for obj, index in bindings
                             for value in transformed[id(obj)][index]]}

    total_samples = 0
    unsupported = []
    for sequence_index, sequence in enumerate(shape.sequences):
        title = shape.names[sequence.name_index].split(b'\0')[0].decode('cp1252')
        duration = float(sequence.duration)
        if not math.isfinite(duration) or not 0 <= duration <= 600:
            unsupported.append(dict(name=title, reason='Invalid DTS sequence duration'))
            continue
        try:
            node_tracks = [_track(shape, node, sequence_index) for node in shape.nodes]
            object_tracks = {id(obj): _track(shape, obj, sequence_index) for obj in objects}
        except ValueError as error:
            unsupported.append(dict(name=title, reason=str(error)))
            continue
        count = max(1, math.ceil(duration * 30))
        positions = {i/count for i in range(count+1)} if duration else {0.}
        for keys in node_tracks + list(object_tracks.values()):
            positions.update(k.position for k in keys)
        # Preserve discrete cel/visibility switches, without interpolation across
        # an entire sample interval. The 0.1 ms guard is below a rendered frame.
        for keys in object_tracks.values():
            positions.update(max(0., k.position - 1e-4 / max(duration, 1e-4)) for k in keys if k.position)
        # glTF animation inputs are float32. Merge times that quantize together.
        times = sorted({float(np.float32(p * duration)) for p in positions}) if duration else [0.]
        positions = [t / duration for t in times] if duration else [0.]
        total_samples += len(positions) * len(bindings)
        if total_samples > 12_000_000:
            raise ValueError('T1 animation exceeds 12 million baked vertex samples')
        frames = [sample(node_tracks, object_tracks, pos, bool(sequence.cyclic)) for pos in positions]
        result['animation_clips'].append(dict(name=title, fps=30., frames=frames,
                                             times=times, duration=duration,
                                             loop=bool(sequence.cyclic), interpolation='LINEAR'))
    if result['animation_clips']:
        result.update(vertices=result['animation_clips'][0]['frames'][0]['vertices'],
                      uvs=uvs, indices=indices, groups=groups)
        result.pop('normals', None)
    metadata.update(animation_status='Source DTS sequences baked',
                    animation_limits=limitations, source_sequence_count=len(shape.sequences),
                    sample_rate=30, source_version=shape.version)
    if unsupported:
        metadata.update(animation_status='Partial: malformed source sequences excluded',
                        unsupported_sequences=unsupported)
    return result


def main():
    """Bake local caches without making the installed game a runtime dependency."""
    import argparse
    import gzip
    import json
    from tools.model_data import load_model_data

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--bake', metavar='MODEL')
    action.add_argument('--all', action='store_true')
    parser.add_argument('--source', type=Path, default=None)
    parser.add_argument('--output', type=Path, default=root / 'local-data/animations/t1')
    args = parser.parse_args()
    names = ((root / 'model_catalog.txt').read_text().splitlines() if args.all else [args.bake])
    args.output.mkdir(parents=True, exist_ok=True)
    summary = dict(models=0, animated_models=0, clips=0, frames=0, bytes=0, errors=[])
    for name in names:
        try:
            preview = load_model_data(root / 'static/model_json' / (name + '.json'))
            model = load_animated_model(name, args.source, preview)
            payload = gzip.compress(json.dumps(model, separators=(',', ':'), allow_nan=False).encode(), compresslevel=4, mtime=0)
            target = args.output / (name + '.json.gz')
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(payload)
            temporary.replace(target)
            clips = model['animation_clips']
            summary['models'] += 1
            summary['animated_models'] += bool(clips)
            summary['clips'] += len(clips)
            summary['frames'] += sum(len(c['frames']) for c in clips)
            summary['bytes'] += len(payload)
        except (ValueError, OSError, IndexError) as error:
            summary['errors'].append(dict(model=name, error=str(error)))
            print(f'{name}: {error}', file=sys.stderr)
    print(json.dumps(summary))
    if summary['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
