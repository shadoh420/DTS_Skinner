"""Adapt externally parsed DIF render surfaces to Skinner's static mesh format.

The caller supplies the read-only kit module; no kit code is bundled here.
Collision/null surfaces, portals and lower LODs are not preview geometry.
"""
import math


def geometry(raw_bytes, reader):
    interior_file = reader.parse_dif(raw_bytes)
    candidates = sorted(enumerate(interior_file.details), key=lambda item: item[1].detail_level)
    if not candidates:
        raise ValueError('DIF contains no interior details')
    selected = next(((i, detail) for i, detail in candidates if detail.surfaces), None)
    if selected is None:
        raise ValueError('DIF contains no render surfaces')
    detail_index, detail = selected
    data = {'vertices': [], 'normals': [], 'uvs': [], 'indices': [], 'groups': [],
            'material_names': detail.materials, 'material_flags': [3] * len(detail.materials),
            'winding': 'ccw'}
    lookup, triangles, skipped = {}, {}, 0
    for surface in detail.surfaces:
        slot = surface['textureIndex']
        if not 0 <= slot < len(detail.materials):
            raise ValueError('DIF render surface references an invalid material slot')
        plane = detail.planes[surface['planeIndex']]
        normal = detail.normals[plane[0]]
        length = math.sqrt(sum(x * x for x in normal))
        if not math.isfinite(length) or length == 0:
            raise ValueError('DIF render surface has an invalid plane normal')
        normal = tuple(x / length * (-1 if surface['planeFlipped'] else 1) for x in normal)
        start, count = surface['windingStart'], surface['windingCount']
        winding = detail.windings[start:start + count]
        if len(winding) != count:
            raise ValueError('DIF surface winding extends outside its index buffer')
        for offset in range(count - 2):
            indices = winding[offset:offset + 3]
            points = [detail.points[index] for index in indices]
            a = [points[1][j] - points[0][j] for j in range(3)]
            b = [points[2][j] - points[0][j] for j in range(3)]
            cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
            facing = sum(cross[j] * normal[j] for j in range(3))
            if abs(facing) < 1e-12:
                skipped += 1
                continue
            if facing < 0:
                indices[1], indices[2] = indices[2], indices[1]
            group = triangles.setdefault(slot, [])
            for index in indices:
                point = detail.points[index]
                uv = detail.surface_uv(surface['texGenIndex'], point)
                key = (index, normal, uv)
                if key not in lookup:
                    lookup[key] = len(data['vertices']) // 3
                    # Proper rotation from Torque Z-up to the existing Y-up viewer.
                    data['vertices'].extend((point[0], point[2], -point[1]))
                    data['normals'].extend((normal[0], normal[2], -normal[1]))
                    data['uvs'].extend(uv)
                group.append(lookup[key])
    for slot, indices in sorted(triangles.items()):
        data['groups'].append({'start': len(data['indices']), 'count': len(indices), 'materialIndex': slot})
        data['indices'].extend(indices)
    if not data['indices']:
        raise ValueError('DIF render surfaces contain no nondegenerate triangles')
    metadata = {
        'format': 'DIF', 'file_version': interior_file.file_version, 'selected_detail': detail_index,
        'details': [{'version': d.version, 'detail_level': d.detail_level, 'min_pixels': d.min_pixels,
                     'surface_count': len(d.surfaces)} for d in interior_file.details],
        'null_surfaces_excluded': detail.null_surface_count, 'degenerate_triangles_skipped': skipped,
        'lightmap_count': detail.lightmap_count,
        'render_boundary': 'Static authored render surfaces with base textures. Baked lightmaps, alarm states, animated lights and resource movers are not applied; collision hulls and null surfaces are excluded.',
        'resource_tail_error': interior_file.tail_error, 'resource_tail_bytes': len(interior_file.tail),
    }
    return data, metadata
