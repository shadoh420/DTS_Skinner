"""UE 4.22 cooked StaticMesh and SkeletalMesh LOD0 render buffers (reference pose)."""
import numpy as np

from tools.tvg_package import geometry

UE4_SCALE_NOTE = 'Unreal 4 (x,y,z) cm -> viewer (-y,z,x), units /52.5 like the UE1-3 games (UE4 meshes read ~2x Ascend size)'


def strip_flags(r):
    global_flags, class_flags = r.unpack('BB')
    return global_flags, class_flags


def positions(r):
    stride, count = r.unpack('ii')
    size, n, raw = r.bulk_array()
    if size != 12 or n != count or stride != 12:
        raise ValueError('Unexpected position buffer layout')
    return np.frombuffer(raw, '<f4').reshape(-1, 3)


def tangents_uvs(r, count):
    strip_flags(r)
    uv_sets, n = r.unpack('ii'); full_uv = r.bool32(); high = r.bool32()
    if n != count or not 1 <= uv_sets <= 8:
        raise ValueError('Static vertex buffer disagrees with positions')
    size, items, raw = r.bulk_array()
    if items != n or size != (16 if high else 8):
        raise ValueError('Unexpected tangent stream')
    if high:
        normal = np.frombuffer(raw, '<i2').reshape(n, 8)[:, 4:7].astype(np.float32)/32767
    else:
        normal = np.frombuffer(raw, np.int8).reshape(n, 8)[:, 4:7].astype(np.float32)/127
    size, items, raw = r.bulk_array()
    if items != n*uv_sets or size != (8 if full_uv else 4):
        raise ValueError('Unexpected texcoord stream')
    uv = np.frombuffer(raw, '<f4' if full_uv else '<f2').reshape(n, uv_sets, 2)[:, 0].astype(np.float32)
    return normal, uv


def colors(r):
    strip_flags(r)
    stride, n = r.unpack('ii')
    if n:
        r.bulk_array()
    return n


def index_buffer(r):
    wide = r.bool32()
    size, n, raw = r.bulk_array()
    if size != 1:
        raise ValueError('Unexpected index buffer element size')
    return np.frombuffer(raw, '<u4' if wide else '<u2').astype(np.int64)


def section_geometry(points, uv, normal, indices, sections, materials, kind):
    groups, used = [], []
    for material, first, faces in sections:
        if min(material, first, faces) < 0 or first+faces*3 > len(indices):
            raise ValueError('Mesh section outside index buffer')
        if faces:
            groups.append(dict(start=len(used), count=faces*3, materialIndex=min(material, max(0, len(materials)-1))))
            used.extend(indices[first:first+faces*3].tolist())
    if not materials:
        materials = [None]
    result = geometry(points, uv, used, groups, materials, normals=normal)
    result['metadata'].update(engine='UE4.22', geometry_source=kind+' LOD0 render vertex/index buffers',
                              coordinate_system=UE4_SCALE_NOTE, animation_playback=False)
    return result


def static_mesh(p, ref):
    props, r = p.properties(ref)
    strip_flags(r)
    if not r.bool32():
        raise ValueError('StaticMesh is not cooked')
    r.take(8)  # body setup, nav collision
    r.take(16)  # lighting guid
    r.take(r.count(4)*4)  # sockets
    lods = r.count(4, 16)
    if not lods:
        raise ValueError('StaticMesh has no render LOD')
    strip_flags(r)
    sections = []
    for _ in range(r.count(28, 4096)):
        material, first, faces, low, high = r.unpack('5i')
        r.take(8)  # collision, shadow (no force-opaque field in this 4.22 build)
        sections.append((material, first, faces))
    r.take(4)  # max deviation
    points = positions(r)
    normal, uv = tangents_uvs(r, len(points))
    colored = colors(r)
    indices = index_buffer(r)
    materials = [m.get('MaterialInterface') if isinstance(m, dict) else None for m in props.get('StaticMaterials') or []]
    result = section_geometry(points, uv, normal, indices, sections, materials, 'StaticMesh')
    result['metadata'].update(available_lods=lods, vertex_colors='present; lighting deferred' if colored else None)
    return result


def mesh(p, ref):
    cls = p.entry(ref)['class']
    if cls == 'StaticMesh':
        return static_mesh(p, ref)
    if cls == 'SkeletalMesh':
        return skeletal_mesh(p, ref)
    raise ValueError('Unsupported mesh class '+cls)


def _skip_array(r, item):
    r.take(r.count(item)*item)


def skeletal_mesh(p, ref, mask_byte=False):
    props, r = p.properties(ref)
    strip_flags(r)
    r.take(28)  # bounds
    materials = []
    for _ in range(r.count(36, 4096)):
        material = r.i32(); r.name()
        if r.bool32():
            r.name()
        r.take(24)  # UV channel data
        materials.append(p.qualified(material) if material else None)
    bones = []
    for _ in range(r.count(12, 65536)):
        bones.append(dict(name=r.name(), parent=r.i32()))
    poses = r.count(40, 65536)
    for bone in bones[:poses]:
        q = r.unpack('4f'); t = r.unpack('3f'); s = r.unpack('3f')
        bone.update(rotation=list(q), translation=list(t), scale=list(s))
    r.take(r.count(12, 65536)*12)  # name -> index map
    if not r.bool32():
        raise ValueError('SkeletalMesh is not cooked')
    lods = r.count(4, 16)
    if not lods:
        raise ValueError('SkeletalMesh has no render LOD')
    strip_flags(r)
    sections = []
    for _ in range(r.count(20, 4096)):
        strip_flags(r)
        material, base, faces = r.unpack('hii')
        r.bool32()  # recompute tangent
        if mask_byte:
            r.u8()
        r.bool32()  # cast shadow
        r.u32()  # base vertex index
        _skip_array(r, 64)  # cloth mapping
        _skip_array(r, 2)  # bone map
        r.unpack('ii')  # vertices, max influences
        r.take(2+20)  # cloth asset index, clothing section data
        _skip_array(r, 4); _skip_array(r, 8)  # duplicated vertices (pre-4.23)
        disabled = r.bool32()
        sections.append((material, base, 0 if disabled else faces))
    width = r.u8()
    if width not in (2, 4):
        raise ValueError('Unexpected skeletal index width')
    size, n, raw = r.bulk_array()
    indices = np.frombuffer(raw, '<u'+str(width)).astype(np.int64)
    _skip_array(r, 2); _skip_array(r, 2)  # active and required bones
    points = positions(r)
    normal, uv = tangents_uvs(r, len(points))
    result = section_geometry(points, uv, normal, indices, sections, materials, 'SkeletalMesh')
    result['metadata'].update(pose='reference', bones=bones, available_lods=lods,
                              skinning='bind-pose positions; no animation or runtime skinning')
    return result
