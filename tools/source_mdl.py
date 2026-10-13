"""Static Source MDL43/44/48 + VVD4 + VTX6/7 reader.

Independent Python implementation. Layout reference: Valve Source SDK 2013
studio.h / optimize.h (Source 1 SDK license); no SDK code is incorporated.
VTX6's 15-byte vertex layout is checked against EYE's metalgibs companion.
"""
import math
import re

import numpy as np

from .vtmb import span, unpack, string, _matrix, model_reference


VERTEX = np.dtype([('weights', '<f4', 3), ('bones', 'u1', 3), ('count', 'u1'),
                   ('position', '<f4', 3), ('normal', '<f4', 3), ('uv', '<f4', 2)])
VERTEX43 = np.dtype([('weights', '<f4', 4), ('bones', '<u2', 4), ('count', '<u4'),
                     ('padding', 'u1', 4), ('position', '<f4', 3), ('normal', '<f4', 3), ('uv', '<f4', 2)])


def load_model(files, name):
    """Map-friendly lookup, default bodygroups/LOD0, with audited VTX fallback."""
    name = model_reference(name)
    if name not in files:
        raise ValueError('Missing MDL: '+name)
    mdl = files[name].read()
    if unpack(mdl, 'i', 232)[0] == 0:
        raise ValueError('Animation library: no bodyparts / render geometry')
    base = name[:-4]
    if base+'.vvd' not in files:
        raise ValueError('Missing VVD companion: '+base+'.vvd')
    vvd = files[base+'.vvd'].read(); failures = []
    for extension in ('.dx90.vtx', '.dx80.vtx', '.sw.vtx', '.dx7_2bone.vtx'):
        companion = base+extension
        if companion not in files:
            continue
        try:
            model = read_model(mdl, vvd, files[companion].read())
            model['metadata'].update(source=name, vtx=companion, companion_failures=failures)
            return model
        except ValueError as exc:
            failures.append(dict(source=companion, reason=str(exc)))
    raise ValueError('No readable VTX: '+('; '.join(r['source']+': '+r['reason'] for r in failures) or 'missing companions'))


def read_vvd(data, checksum, mdl_version=48):
    span(data, 0, 64)
    version, check, lods = unpack(data, 'iIi', 4)
    counts = unpack(data, '8i', 16)
    nf, fo, vo, tangent = unpack(data, '4i', 48)
    if data[:4] != b'IDSV' or version != 4 or check != checksum:
        raise ValueError('Unsupported/mismatched VVD signature, version or checksum')
    if not 1 <= lods <= 8 or counts[0] < 0 or any(not 0 <= c <= counts[0] for c in counts[:lods]):
        raise ValueError('Invalid VVD LOD counts')
    dtype = VERTEX43 if mdl_version == 43 else VERTEX
    span(data, vo, counts[0], dtype.itemsize); span(data, fo, nf, 12)
    if tangent:
        span(data, tangent, counts[0], 16)
    vertices = np.frombuffer(data, dtype, counts[0], vo)
    if nf:
        order = []
        for i in range(nf):
            lod, first, count = unpack(data, '3i', fo+i*12)
            if not 0 <= lod < lods or first < 0 or count < 0 or first+count > len(vertices):
                raise ValueError('Invalid VVD fixup range')
            order.extend(range(first, first+count))  # all fixups survive at LOD0
        if len(order) != counts[0] or len(set(order)) != len(order):
            raise ValueError('VVD fixups do not cover LOD0 exactly once')
        vertices = vertices[order]
    for field in ('weights', 'position', 'normal', 'uv'):
        if not np.isfinite(vertices[field]).all():
            raise ValueError('Nonfinite VVD ' + field)
    return vertices, dict(version=version, lods=lods, fixups=nf, vertex_stride=dtype.itemsize)


def _quat_euler(angles):
    x, y, z = np.asarray(angles)/2
    sx, sy, sz = np.sin([x, y, z]); cx, cy, cz = np.cos([x, y, z])
    return [sx*cy*cz-cx*sy*sz, cx*sy*cz+sx*cy*sz,
            cx*cy*sz-sx*sy*cz, cx*cy*cz+sx*sy*sz]


def _quat_packed(data, at, bits):
    span(data, at, bits//8)
    v = int.from_bytes(data[at:at+bits//8], 'little')
    if bits == 48:
        xyz = [(v & 65535)/32768-1, ((v >> 16) & 65535)/32768-1,
               ((v >> 32) & 32767)/16384-1]
    else:
        xyz = [((v >> (21*i)) & 2097151)/1048576-1 for i in range(3)]
    w = math.sqrt(max(0, 1-sum(c*c for c in xyz)))
    return [*xyz, -w if v >> (bits-1) else w]


def pose_matrices(data):
    """First local idle/reference frame; unsupported blends/libraries use bind.

    VVD positions already contain the bind pose. Selected bone transforms are
    multiplied by poseToBone, never applied to model-space vertices twice.
    """
    count, at = unpack(data, '2i', 156)
    span(data, at, count, 216)
    bind = [np.eye(4) for _ in range(count)]
    result = dict(pose='bind', reason='No local idle/reference sequence', frame=0,
                  limitations=['No IK, procedural bones, flexes, animation layers or clips'])
    ns, seq_at = unpack(data, '2i', 188)
    span(data, seq_at, ns, 212)
    candidates = []
    for i in range(ns):
        s = seq_at+i*212
        label = string(data, s+unpack(data, 'i', s+4)[0])
        if re.search(r'idle|(^|[_ ])ref(erence)?($|[_ ])|bind', label, re.I):
            candidates.append((0 if 'idle' in label.lower() else 1, i, s, label))
    if not candidates:
        result['included_models'] = unpack(data, 'i', 344)[0]
        return bind, result
    failures = []
    for _, index, s, label in sorted(candidates):
        try:
            flags, = unpack(data, 'i', s+12)
            blends, refs = unpack(data, '2i', s+56)
            layers, = unpack(data, 'i', s+148)
            if blends != 1 or flags & 0x04 or layers:
                raise ValueError('Blended/delta/layered sequence requires runtime evaluation')
            animation, = unpack(data, 'h', s+refs)
            na, aa = unpack(data, '2i', 180)
            if not 0 <= animation < na:
                raise ValueError('Sequence refers to an unavailable animation')
            span(data, aa, na, 100)
            a = aa+animation*100
            af, frames = unpack(data, '2i', a+12)
            block, offset = unpack(data, '2i', a+52)
            section, sectionframes = unpack(data, '2i', a+80)
            if sectionframes:
                if unpack(data, 'i', 4)[0] < 46:
                    raise ValueError('Pre-46 sectioned animation is incompatible; using reference pose')
                block, offset = unpack(data, '2i', a+section)
            if block or af & 0x04 or frames < 1:
                raise ValueError('External or delta animation data; using reference pose')
            positions = [list(unpack(data, '3f', at+i*216+32)) for i in range(count)]
            quats = [list(unpack(data, '4f', at+i*216+44)) for i in range(count)]
            seen = set(); cursor = a+offset if offset else 0
            while cursor:
                bone, flags, next_offset = unpack(data, 'BBh', cursor)
                if bone == 255:
                    break
                if bone >= count or bone in seen or flags & ~0x3f or flags & 0x10:
                    raise ValueError('Invalid/unsupported animation bone or flags')
                seen.add(bone); b = at+bone*216; payload = cursor+4
                if flags & 2:
                    quats[bone] = _quat_packed(data, payload, 48)
                if flags & 32:
                    quats[bone] = _quat_packed(data, payload, 64)
                if flags & 1:
                    positions[bone] = list(unpack(data, '3e', payload+6*bool(flags & 2)+8*bool(flags & 32)))
                for flag, dest, ptr, default, scale in (
                        (8, quats, payload, b+60, b+84),
                        (4, positions, payload+6*bool(flags & 8), b+32, b+72)):
                    if not flags & flag:
                        continue
                    values = list(unpack(data, '3f', default))
                    scales = unpack(data, '3f', scale)
                    for axis, relative in enumerate(unpack(data, '3h', ptr)):
                        if relative:
                            valid, total, value = unpack(data, 'BBh', ptr+relative)
                            if not 1 <= valid <= total:
                                raise ValueError('Invalid animation RLE run')
                            values[axis] += value*scales[axis]
                    dest[bone] = _quat_euler(values) if flag == 8 else values
                if next_offset < 0:
                    raise ValueError('Invalid animation next offset')
                cursor = cursor+next_offset if next_offset else 0
            worlds = []
            for i in range(count):
                parent, = unpack(data, 'i', at+i*216+4)
                if parent < -1 or parent >= i:
                    raise ValueError('Invalid bone hierarchy')
                local = _matrix(positions[i], quats[i])
                worlds.append(worlds[parent] @ local if parent >= 0 else local)
            matrices = []
            for i, world in enumerate(worlds):
                inverse = np.eye(4); inverse[:3] = np.array(unpack(data, '12f', at+i*216+96)).reshape(3, 4)
                matrices.append(world @ inverse)
            if not np.isfinite(matrices).all():
                raise ValueError('Nonfinite pose matrix')
            return matrices, dict(result, pose=label, sequence=index, animation=animation,
                                  reason='First readable local idle, then reference, first frame', fallbacks=failures)
        except ValueError as exc:
            failures.append(dict(sequence=label, reason=str(exc)))
    return bind, dict(result, reason='No supported local idle/reference; VVD bind pose', fallbacks=failures)


def read_model(mdl, vvd, vtx):
    span(mdl, 0, 408)
    version, checksum = unpack(mdl, 'iI', 4)
    if mdl[:4] != b'IDST' or version not in (43, 44, 48):
        raise ValueError('Unsupported Source MDL version/signature')
    if unpack(mdl, 'i', 76)[0] != len(mdl):
        raise ValueError('MDL declared length mismatch')
    nt, textures, nd, directories, refs, families, skins, nb, bodies = unpack(mdl, '9i', 204)
    if not nb:
        raise ValueError('Animation library: no bodyparts / render geometry')
    for off, n, stride in ((textures, nt, 64), (directories, nd, 4), (skins, refs*families, 2), (bodies, nb, 16)):
        span(mdl, off, n, stride)
    names = [string(mdl, textures+i*64+unpack(mdl, 'i', textures+i*64)[0]) for i in range(nt)]
    dirs = [string(mdl, unpack(mdl, 'i', directories+i*4)[0]) for i in range(nd)]
    table = [list(unpack(mdl, f'{refs}H', skins+i*refs*2)) for i in range(families)] or [list(range(nt))]
    if any(value >= nt for row in table for value in row):
        raise ValueError('Invalid MDL skin material')
    vertices, vvd_info = read_vvd(vvd, checksum, version)
    vertex_stride = vvd_info['vertex_stride']
    span(vtx, 0, 36)
    vv, = unpack(vtx, 'i')
    vc, nl, replacements, vb, bo = unpack(vtx, 'I4i', 16)
    if vv not in (6, 7) or vc != checksum or not 1 <= nl <= 8 or vb != nb:
        raise ValueError('Unsupported/mismatched VTX version, checksum, LODs or bodyparts')
    span(vtx, bo, nb, 8)
    material_replacements = []
    if replacements:
        span(vtx, replacements, nl, 8)
        count, relative = unpack(vtx, '2i', replacements)  # LOD0 only
        span(vtx, replacements+relative, count, 6)
        for i in range(count):
            at = replacements+relative+i*6
            slot, relative_name = unpack(vtx, 'hi', at)
            if not 0 <= slot < nt:
                raise ValueError('Invalid VTX material replacement')
            name = string(vtx, at+relative_name)
            material_replacements.append(dict(slot=slot, original=names[slot], replacement=name))
            names[slot] = name
    matrices, pose = pose_matrices(mdl)
    points, normals, uvs, indices, groups, skin_refs, defaults = [], [], [], [], [], [], []
    for body in range(nb):
        bp = bodies+body*16; _, nm, _, mo = unpack(mdl, '4i', bp)
        vp = bo+body*8; vm, vmo = unpack(vtx, '2i', vp)
        if nm < 1 or nm != vm:
            raise ValueError('MDL/VTX bodygroup mismatch')
        span(mdl, bp+mo, nm, 148); span(vtx, vp+vmo, vm, 8)
        m = bp+mo
        meshes, mesh_at, nv, vertex_at = unpack(mdl, '4i', m+72)
        if vertex_at < 0 or vertex_at % vertex_stride or nv < 0 or vertex_at//vertex_stride+nv > len(vertices):
            raise ValueError('Invalid MDL model VVD range')
        span(mdl, m+mesh_at, meshes, 116)
        defaults.append(dict(bodypart=body, model=0, name=string(mdl, m), alternatives=nm))
        model = vp+vmo; lodcount, lo = unpack(vtx, '2i', model)
        if not 1 <= lodcount <= nl:
            raise ValueError('Invalid VTX model LOD count')
        span(vtx, model+lo, lodcount, 12)
        lod = model+lo; nmesh, mesh_off = unpack(vtx, '2i', lod)
        if nmesh != meshes:
            raise ValueError('MDL/VTX mesh count mismatch')
        span(vtx, lod+mesh_off, meshes, 9)
        start = len(points)//3
        for row in vertices[vertex_at//vertex_stride:vertex_at//vertex_stride+nv]:
            count = int(row['count'])
            if not 0 <= count <= len(row['weights']):
                raise ValueError('Invalid VVD bone count')
            transform = np.eye(4)
            if count:
                weights, bones = row['weights'][:count], row['bones'][:count]
                if (weights < 0).any() or abs(float(weights.sum())-1) > .01 or any(b >= len(matrices) for b in bones):
                    raise ValueError('Invalid VVD vertex weights/bones')
                transform = sum(float(w)*matrices[int(b)] for w, b in zip(weights, bones))
            point = transform @ [*row['position'], 1]
            normal = transform[:3, :3] @ row['normal']; length = np.linalg.norm(normal)
            if length:
                normal /= length
            points.extend(float(point[i]) for i in (1, 2, 0))
            normals.extend(float(normal[i]) for i in (1, 2, 0)); uvs.extend(map(float, row['uv']))
        for mesh in range(meshes):
            mat, _, count, first = unpack(mdl, '4i', m+mesh_at+mesh*116)
            if not 0 <= mat < len(table[0]) or first < 0 or count < 0 or first+count > nv:
                raise ValueError('Invalid MDL mesh vertex/material range')
            mp = lod+mesh_off+mesh*9; ng, go = unpack(vtx, '2i', mp)
            span(vtx, mp+go, ng, 25); begin = len(indices)
            for g in range(ng):
                sg = mp+go+g*25
                nverts, vo, ni, io, ns, so, flags = unpack(vtx, '6iB', sg)
                stride, original = (9, 4) if vv == 7 else (15, 12)
                span(vtx, sg+vo, nverts, stride); span(vtx, sg+io, ni, 2); span(vtx, sg+so, ns, 27)
                remap = [unpack(vtx, 'H', sg+vo+i*stride+original)[0] for i in range(nverts)]
                if any(v >= count for v in remap):
                    raise ValueError('Invalid VTX original mesh vertex')
                ix = unpack(vtx, f'{ni}H', sg+io)
                if any(v >= nverts for v in ix):
                    raise ValueError('Invalid VTX vertex index')
                for strip in range(ns):
                    st = sg+so+strip*27
                    sn, si, sv, sf, bones, flag, nc, co = unpack(vtx, '4ihB2i', st)
                    if min(sn, si, sv, sf) < 0 or si+sn > ni or sf+sv > nverts:
                        raise ValueError('Invalid VTX strip range')
                    span(vtx, st+co, nc, 8)
                    seq = ix[si:si+sn]
                    if flag == 1 and sn % 3 == 0:
                        triangles = [seq[i:i+3] for i in range(0, sn, 3)]
                    elif flag == 2:
                        triangles = [(seq[i+1], seq[i], seq[i+2]) if i % 2 else seq[i:i+3] for i in range(sn-2)]
                    else:
                        raise ValueError('Unsupported VTX strip flags/count')
                    for tri in triangles:
                        if len(set(tri)) == 3:
                            indices.extend(start+first+remap[v] for v in (tri[0], tri[2], tri[1]))
            if len(indices) > begin:
                groups.append(dict(start=begin, count=len(indices)-begin, materialIndex=table[0][mat],
                                   source_bodypart=body, source_model=0, source_mesh=mesh,
                                   vertex_start=start+first, vertex_count=count, vvd_first=vertex_at//vertex_stride+first))
                skin_refs.append(mat)
    if not indices:
        raise ValueError('No triangles in default bodygroups / LOD0')
    return dict(game='eye', winding='ccw', vertices=points, normals=normals, uvs=uvs, indices=indices,
                groups=groups, material_names=names, skin_table=table, skin_refs=skin_refs,
                metadata=dict(version=version, checksum=checksum, vtx_version=vv, vvd=vvd_info,
                              lod=0, bodygroups=defaults, material_directories=dirs, pose=pose,
                              material_replacements=material_replacements,
                              coordinate_system='Source units; (x,y,z) -> (y,z,x), no recenter/scale'))
