"""Embedded glTF 2.0 export of the shared, Y-up Skinner model representation.

Animation is baked vertex motion (morph targets), not a reconstructed skeleton.
Sparse deltas and one-hot weights keep repeated poses and animation clips compact.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import struct

from PIL import Image

from .obj_exporter import compute_smooth_normals, q3_material_settings


def _floats(values, count, label):
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise ValueError(f"{label} must contain {count} numbers")
    if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 3.402823466e38 for x in values):
        raise ValueError(f"{label} must contain finite float32 numbers")
    return values


def model_to_glb(data, textures_dir):
    """Return an embedded GLB; optional animation_clips contain name/fps/frames/loop.

    Frames contain flat vertices and optional normals, in the base model's order.
    Optional times (seconds) override fps; duration can extend the final key, and
    interpolation can be LINEAR or STEP. Loop intent is retained in clip extras.
    """
    vertices = data.get('vertices', [])
    if not vertices or len(vertices) % 3:
        raise ValueError('Model must contain complete vertices')
    vertices = _floats(vertices, len(vertices), 'Vertices')
    vertex_count = len(vertices) // 3
    uvs = _floats(data.get('uvs'), vertex_count * 2, 'UVs')
    indices = data.get('indices', [])
    if not indices or len(indices) % 3 or any(type(i) is not int or not 0 <= i < vertex_count for i in indices):
        raise ValueError('Model must contain complete triangles with valid indices')
    names = data.get('material_textures') or ['[Unassigned material]']
    for name in names:
        if not isinstance(name, str) or not name or name in ('.', '..') or any(c in name for c in '/\\:\r\n'):
            raise ValueError('Texture names must be local filenames')
    groups = data.get('groups') or [{'start': 0, 'count': len(indices), 'materialIndex': 0}]
    end = 0
    for group in groups:
        start, count, material = (group.get(k) for k in ('start', 'count', 'materialIndex'))
        if (any(type(x) is not int for x in (start, count, material)) or start != end or
                count <= 0 or count % 3 or not 0 <= material < len(names)):
            raise ValueError('Invalid material group')
        end += count
    if end != len(indices):
        raise ValueError('Material groups must cover all triangles exactly once')
    flags = data.get('material_flags') or [0] * len(names)
    settings = q3_material_settings(dict(data, material_textures=names))
    if len(flags) != len(names) or any(type(flag) is not int or flag < 0 for flag in flags):
        raise ValueError('Invalid material flags')
    reverse = data.get('winding') != 'ccw'
    triangles = [i for offset in range(0, len(indices), 3)
                 for i in (indices[offset], indices[offset + (2 if reverse else 1)], indices[offset + (1 if reverse else 2)])]

    def normals_for(frame):
        if frame.get('normals') is not None:
            source = _floats(frame['normals'], len(vertices), 'Normals')
            # This is the same legacy normal/winding correction as OBJ export.
            result = []
            for offset in range(0, len(source), 3):
                vector = source[offset:offset + 3]
                length = math.hypot(*vector)
                result.extend([(-1 if reverse else 1) * x / length for x in vector] if length else [0.0, 1.0, 0.0])
            return result
        return [x for normal in compute_smooth_normals(frame['vertices'], triangles) for x in normal]

    normals = normals_for(data)
    binary = bytearray()
    gltf = {'asset': {'version': '2.0', 'generator': 'Skinner'},
            'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
            'meshes': [{'primitives': []}], 'buffers': [], 'bufferViews': [],
            'accessors': [], 'materials': [], 'textures': [], 'images': [], 'samplers': [],
            'extensionsUsed': ['KHR_materials_unlit'],
            'extras': {'sourceMetadata': data.get('metadata', {}),
                       'animationRepresentation': 'Baked vertex motion; no skeleton or rig.',
                       'missingTextures': [], 'warnings': list(data.get('warnings') or []) + list(data.get('animation_notes') or [])}}

    def view(raw, target=None):
        binary.extend(b'\0' * (-len(binary) % 4))
        entry = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(raw)}
        if target:
            entry['target'] = target
        binary.extend(raw)
        gltf['bufferViews'].append(entry)
        return len(gltf['bufferViews']) - 1

    def accessor(values, width, component=5126, target=None, bounds=False, sparse=False, count=None):
        count = len(values) // width if count is None else count
        entry = {'componentType': component, 'count': count,
                 'type': {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3'}[width]}
        if bounds:
            entry['min'] = [min(values[axis::width]) for axis in range(width)]
            entry['max'] = [max(values[axis::width]) for axis in range(width)]
        nonzero = None
        if sparse:
            nonzero = [(i // width, values[i:i + width]) for i in range(0, len(values), width)
                       if any(values[i:i + width])]
        if sparse and len(nonzero) * (width * 4 + 4) < len(values) * 4:
            set_sparse(entry, nonzero)
        else:
            code = 'f' if component == 5126 else 'I'
            entry['bufferView'] = view(struct.pack('<' + str(len(values)) + code, *values), target)
        gltf['accessors'].append(entry)
        return len(gltf['accessors']) - 1

    def set_sparse(entry, nonzero):
        if not nonzero:
            return  # An accessor without a bufferView is initialized to zero.
        component, code = (5123, 'H') if nonzero[-1][0] < 65536 else (5125, 'I')
        sparse_indices = [index for index, _ in nonzero]
        sparse_values = [x for _, values in nonzero for x in values]
        entry['sparse'] = {'count': len(nonzero),
                           'indices': {'bufferView': view(struct.pack('<' + str(len(sparse_indices)) + code, *sparse_indices)),
                                       'componentType': component},
                           'values': {'bufferView': view(struct.pack('<' + str(len(sparse_values)) + 'f', *sparse_values))}}

    attributes = {'POSITION': accessor(vertices, 3, target=34962, bounds=True),
                  'NORMAL': accessor(normals, 3, target=34962),
                  # glTF and the viewer both use an upper-left texture origin.
                  'TEXCOORD_0': accessor(uvs, 2, target=34962)}
    embedded_images = {}
    for slot, name in enumerate(names):
        flag = flags[slot] if data.get('game') == 't2' else 0
        setting = settings[slot]
        alpha_func, blend = setting.get('alphaFunc', ''), setting.get('blend', [])
        material = {'name': name, 'doubleSided': True,
                    'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 1},
                    'extensions': {'KHR_materials_unlit': {}},
                    'extras': {'sourceTexture': name, 'sourceFlags': flags[slot]}}
        if flag & (4 | 8 | 16):
            material['alphaMode'] = 'BLEND'
        if flag & (8 | 16):
            gltf['extras']['warnings'].append(f'{name}: additive/subtractive blending is approximated by alpha blending in glTF.')
        if data.get('game') == 'q3':
            material['extras']['sourceShaderSettings'] = setting
            material['doubleSided'] = not data.get('material_settings') or setting.get('cull', 'back') != 'back'
            if setting.get('cull') == 'front':
                gltf['extras']['warnings'].append(f'{name}: front-face culling is approximated by a double-sided material in glTF.')
            if alpha_func:
                material.update(alphaMode='MASK', alphaCutoff=0.5 / 255 if alpha_func == 'GT0' else .5)
                if blend:
                    gltf['extras']['warnings'].append(f'{name}: combined alpha test/blending uses MASK without partial-alpha blending in glTF.')
            elif blend:
                material['alphaMode'] = 'BLEND'
            if blend and blend != ['gl_src_alpha', 'gl_one_minus_src_alpha']:
                gltf['extras']['warnings'].append(f'{name}: nonstandard blending is approximated by alpha blending in glTF.')
            if setting.get('tcGen', 'base') != 'base':
                gltf['extras']['warnings'].append(f'{name}: generated environment coordinates use the authored UVs in glTF.')
            if not setting.get('depthWrite', True):
                gltf['extras']['warnings'].append(f'{name}: explicit depth-write state is metadata only in glTF.')
        source = Path(textures_dir) / name
        image_key = (name, alpha_func == 'LT128')
        if not name.startswith('[') and source.is_file():
            if image_key not in embedded_images:
                with Image.open(source) as texture:
                    texture.load()
                    stream = io.BytesIO()
                    texture = texture.convert('RGBA')
                    if alpha_func == 'LT128':
                        texture.putalpha(texture.getchannel('A').point(lambda value: 255 - value))
                    texture.save(stream, format='PNG')
                embedded_images[image_key] = len(gltf['images'])
                gltf['images'].append({'name': name, 'mimeType': 'image/png', 'bufferView': view(stream.getvalue())})
            sampler = {'magFilter': 9729 if data.get('game') == 't2' else 9728,
                       'minFilter': (9729 if flag & 128 else 9987) if data.get('game') == 't2' else 9728,
                       'wrapS': 33071 if data.get('game') == 't2' and not flag & 1 else 10497,
                       'wrapT': 33071 if data.get('game') == 't2' and not flag & 2 else 10497}
            if data.get('game') == 'q3':
                sampler = {'magFilter': 9729, 'minFilter': 9987,
                           'wrapS': 33071 if setting.get('clamp') else 10497,
                           'wrapT': 33071 if setting.get('clamp') else 10497}
            if sampler not in gltf['samplers']:
                gltf['samplers'].append(sampler)
            texture_index = len(gltf['textures'])
            gltf['textures'].append({'source': embedded_images[image_key], 'sampler': gltf['samplers'].index(sampler)})
            material['pbrMetallicRoughness']['baseColorTexture'] = {'index': texture_index}
        else:
            material['extras']['missingTexture'] = True
            if name not in gltf['extras']['missingTextures']:
                gltf['extras']['missingTextures'].append(name)
        gltf['materials'].append(material)

    targets, pose_targets, clips = [], {}, []
    def pose_key(position, normal):
        return hashlib.sha256(struct.pack('<' + str(len(position) + len(normal)) + 'f', *(list(position) + normal))).digest()

    base_key = pose_key(vertices, normals)
    pose_targets[base_key] = -1
    for clip in data.get('animation_clips') or []:
        if not isinstance(clip, dict):
            raise ValueError('Animation clips must be objects')
        frames = clip.get('frames')
        if not isinstance(frames, list) or not frames:
            raise ValueError('Animation clips must contain frames')
        fps = clip.get('fps', 30)
        if type(fps) not in (float, int) or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Animation fps must be positive and finite')
        times = list(clip.get('times', [i / fps for i in range(len(frames))]))
        _floats(times, len(frames), 'Animation times')
        times = list(struct.unpack('<' + str(len(times)) + 'f', struct.pack('<' + str(len(times)) + 'f', *times)))
        if times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError('Animation times must be nonnegative and strictly increasing')
        interpolation = clip.get('interpolation', 'LINEAR')
        if interpolation not in ('LINEAR', 'STEP'):
            raise ValueError('Animation interpolation must be LINEAR or STEP')
        frame_targets = []
        for frame in frames:
            if not isinstance(frame, dict):
                raise ValueError('Animation frames must be objects')
            position = _floats(frame.get('vertices'), len(vertices), 'Animation vertices')
            frame_normals = normals_for(frame)
            key = pose_key(position, frame_normals)
            if key not in pose_targets:
                pose_targets[key] = len(targets)
                deltas = [a - b for a, b in zip(position, vertices)]
                normal_deltas = [a - b for a, b in zip(frame_normals, normals)]
                _floats(deltas, len(vertices), 'Animation position deltas')
                target = {'POSITION': accessor(deltas, 3, target=34962, bounds=True, sparse=True)}
                if any(normal_deltas):
                    target['NORMAL'] = accessor(normal_deltas, 3, target=34962, sparse=True)
                targets.append(target)
            frame_targets.append(pose_targets[key])
        duration = clip.get('duration', times[-1] + (1 / fps if clip.get('loop') else 0))
        _floats([duration], 1, 'Animation duration')
        duration = struct.unpack('<f', struct.pack('<f', duration))[0]
        if duration < times[-1]:
            raise ValueError('Animation duration cannot end before its final frame')
        if duration > times[-1]:
            times.append(duration)
            frame_targets.append(frame_targets[0] if clip.get('loop') else frame_targets[-1])
        clips.append((clip, times, frame_targets, interpolation))

    # A constant animation still has a named, playable clip with a zero target.
    if clips and not targets:
        targets.append({'POSITION': accessor([0.0] * len(vertices), 3, bounds=True, sparse=True)})
    # Large all-animation sets exceed common GPU texture-array limits. Each clip
    # gets a hidden mesh using only its own targets, sharing all binary payloads.
    separate_clips = len(targets) > 1024
    if separate_clips:
        gltf['extras']['warnings'].append('Large animation set uses one mesh per clip. Play one clip at a time; cross-fading independent clip meshes is not supported.')
        scale_time = accessor([0.0], 1, bounds=True)
        hidden_scale = accessor([0.0, 0.0, 0.0], 3)
        shown_scale = accessor([1.0, 1.0, 1.0], 3)
    for group in groups:
        start, count = group['start'], group['count']
        primitive = {'attributes': attributes,
                     'indices': accessor(triangles[start:start + count], 1, 5125, 34963),
                     'material': group['materialIndex'], 'mode': 4}
        if targets and not separate_clips:
            primitive['targets'] = targets
        gltf['meshes'][0]['primitives'].append(primitive)
    if targets and not separate_clips:
        gltf['meshes'][0]['weights'] = [0.0] * len(targets)
    if clips:
        gltf['animations'] = []
    for clip, times, frame_targets, interpolation in clips:
        node_index, target_count = 0, len(targets)
        if separate_clips:
            selected = list(dict.fromkeys(target for target in frame_targets if target >= 0)) or [0]
            remap = {target: index for index, target in enumerate(selected)}
            frame_targets = [remap[target] if target >= 0 else -1 for target in frame_targets]
            target_count = len(selected)
            clip_targets = [targets[target] for target in selected]
            node_index = len(gltf['nodes'])
            gltf['nodes'].append({'name': str(clip.get('name', 'Animation')), 'mesh': len(gltf['meshes']), 'scale': [0.0, 0.0, 0.0]})
            gltf['scenes'][0]['nodes'].append(node_index)
            clip_primitives = []
            for primitive in gltf['meshes'][0]['primitives']:
                # Older GLTFLoader caches geometry by accessor IDs, ignoring
                # morph targets. Alias the index accessor, sharing its bytes.
                index_alias = len(gltf['accessors'])
                gltf['accessors'].append(dict(gltf['accessors'][primitive['indices']]))
                clip_primitives.append(dict(primitive, indices=index_alias, targets=clip_targets))
            gltf['meshes'].append({'primitives': clip_primitives, 'weights': [0.0] * target_count})
        time_accessor = accessor(times, 1, bounds=True)
        weights = {'componentType': 5126, 'count': len(times) * target_count, 'type': 'SCALAR'}
        set_sparse(weights, [(frame * target_count + target, [1.0])
                             for frame, target in enumerate(frame_targets) if target >= 0])
        gltf['accessors'].append(weights)
        gltf['animations'].append({'name': str(clip.get('name', 'Animation')),
                                   'samplers': [{'input': time_accessor, 'output': len(gltf['accessors']) - 1,
                                                 'interpolation': interpolation}],
                                   'channels': [{'sampler': 0, 'target': {'node': node_index, 'path': 'weights'}}],
                                   'extras': {'loop': bool(clip.get('loop')), 'sourceFps': clip.get('fps', 30)}})
        if separate_clips:
            animation = gltf['animations'][-1]
            animation['samplers'].extend([{'input': scale_time, 'output': hidden_scale, 'interpolation': 'STEP'},
                                           {'input': scale_time, 'output': shown_scale, 'interpolation': 'STEP'}])
            animation['channels'].extend([{'sampler': 1, 'target': {'node': 0, 'path': 'scale'}},
                                           {'sampler': 2, 'target': {'node': node_index, 'path': 'scale'}}])
    # Empty glTF arrays are invalid; omit optional unused collections.
    gltf = {key: value for key, value in gltf.items() if value != []}
    gltf['buffers'] = [{'byteLength': len(binary)}]
    encoded = json.dumps(gltf, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    size = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack('<4sII', b'glTF', 2, size) + struct.pack('<I4s', len(encoded), b'JSON') + encoded +
            struct.pack('<I4s', len(binary), b'BIN\0') + binary)
