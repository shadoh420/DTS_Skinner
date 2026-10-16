"""Vengeance map serialization; separate from the UE1 and model readers.

Native layouts are checked against the installed 130/27 packages. Unknown native
revisions fail explicitly instead of guessing offsets. No game code is executed.
"""
import numpy as np

from tools.tvg_package import Package, rotation_matrix, SCALE

BASIS = np.array(((0., -1., 0.), (0., 0., 1.), (1., 0., 0.)))


def viewer(points):
    return np.asarray(points, dtype=np.float64) @ BASIS.T * SCALE


def transform(props):
    scale = np.asarray(props.get('DrawScale3D', (1, 1, 1))) * props.get('DrawScale', 1)
    linear = rotation_matrix(*props.get('Rotation', (0, 0, 0))) @ np.diag(scale)
    location = np.asarray(props.get('Location', (0, 0, 0))) - linear @ np.asarray(props.get('PrePivot', (0, 0, 0)))
    matrix = np.eye(4)
    matrix[:3, :3] = BASIS @ linear @ BASIS.T
    matrix[:3, 3] = viewer(location)
    if not np.isfinite(matrix).all():
        raise ValueError('Non-finite actor transform')
    return matrix.T.reshape(-1).tolist()  # Three.js column-major; library geometry already uses viewer units.


def viewpoint(props):
    pitch, yaw, _ = props.get('Rotation', (0, 0, 0))
    return dict(origin=viewer(np.asarray(props.get('Location', (0,0,0)))+[0,0,64]).tolist(),
                yaw=yaw % 65536 * 360/65536, pitch=(pitch+32768) % 65536 * 360/65536-180)


def is_npc(ancestry):
    names = {name.rsplit('.', 1)[-1].lower() for name in ancestry}
    # Character derives from Ragdoll! Ragdoll alone is not evidence of a corpse
    # or a character. Functional turret ancestry includes deployed turrets.
    return 'character' in names or 'turret' in names


class MapPackage(Package):
    def properties(self, ref):
        e = self.begin(ref)
        if e['flags'] & 0x02000000:
            if self.licensee < 25:
                raise ValueError('Pre-25 actor stack layout not verified')
            # Native Actor and Rook each serialize a header before FStateFrame.
            headers = 0
            while self.data[self.at:self.at+4] == b'\3\0\0\0':
                if self.header(25) != (0, 1) or headers >= 4:
                    raise ValueError('Unknown actor stack header')
                headers += 1
            if not headers:
                raise ValueError('Missing actor stack header')
            self.take(25)  # state-frame probe mask, latent state and native stack metadata
            node, state, owner = self.index(), self.index(), self.index()
            self.qualified(node)
            self.qualified(state)
            if owner != ref:
                raise ValueError('Actor stack owner mismatch')
            self.take(10)
        return self.tagged()

    def structure(self, raw):
        saved = self.data, self.at, self.limit
        try:
            self.data, self.at, self.limit = raw, 0, len(raw)
            result = self.tagged()
            if self.at != self.limit:
                raise ValueError('Trailing struct bytes')
            return result
        finally:
            self.data, self.at, self.limit = saved

    def level(self, ref):
        self.properties(ref)
        count, capacity = self.unpack('ii')
        if capacity < count:
            raise ValueError('Invalid actor capacity')
        actors = [self.index() for _ in range(self.count(count))]
        for actor in actors:
            if actor < 0 or actor > len(self.exports):
                raise ValueError('Invalid level actor reference')
        url = [self.text() for _ in range(4)]
        options = [self.text() for _ in range(self.count())]
        self.take(8)  # port, valid
        if self.header(25) != (8, 6):
            raise ValueError('Unknown ULevel native revision')
        self.take(4)
        model = self.index()
        if model and self.ref_name(self.exports[model-1]['cls']) != 'Model':
            raise ValueError('Level model reference is not UModel')
        return dict(actors=actors, model=model, url=url, options=options)

    def model(self, ref):
        self.properties(ref)
        self.take(41)
        if self.header(25) != (0, 5):
            raise ValueError('Unknown UModel native revision')
        vectors, points = self.array('3f'), self.array('3f')
        nodes = []
        for _ in range(self.count()):
            if self.header(25) != (2, 2):
                raise ValueError('Unknown BSP node revision')
            plane = self.unpack('4f')
            self.take(9)  # zone mask and node flags
            pool, surface, back, front, coplanar, collision, render = [self.index() for _ in range(7)]
            self.take(32)  # render bounds and native visibility metadata
            zone0, zone1, count = self.unpack('3B')
            self.take(21)  # leaves, native visibility metadata, terminal byte
            nodes.append(dict(pool=pool, surface=surface, count=count, plane=plane, zones=(zone0, zone1), back=back, front=front))
        surfaces = []
        for _ in range(self.count()):
            if self.header(25) != (1, 1):
                raise ValueError('Unknown BSP surface revision')
            material, flags = self.index(), self.unpack('I')[0]
            base, normal, u, v, brush_poly, actor = [self.index() for _ in range(6)]
            plane = self.unpack('4f')
            light_scale = self.unpack('f')[0]
            surfaces.append(dict(material=material, flags=flags, base=base, normal=normal,
                                 u=u, v=v, actor=actor, plane=plane, light_scale=light_scale))
        verts = [(self.index(), self.index()) for _ in range(self.count())]
        for node in nodes:
            if not 0 <= node['surface'] < len(surfaces) or not 0 <= node['pool'] <= len(verts)-node['count']:
                raise ValueError('BSP node outside surface/vertex pools')
        # The editor retains unused vertex-pool entries from earlier rebuilds.
        if any(not 0 <= verts[i][0] < len(points) for node in nodes
               for i in range(node['pool'], node['pool'] + node['count'])):
            raise ValueError('BSP vertex outside point pool')
        self.take(4)  # shared sides
        zones = []
        for _ in range(self.count(self.i32())):
            zones.append(self.index())
            self.take(20)
        polys = self.index()
        return dict(vectors=vectors, points=points, nodes=nodes, surfaces=surfaces,
                    verts=verts, zones=zones, polys=polys)

    def polys(self, ref):
        self.properties(ref)
        count, capacity = self.unpack('ii')
        if capacity < count:
            raise ValueError('Invalid polygon capacity')
        result = []
        for _ in range(self.count(count)):
            corners = self.count()
            base, normal, u, v = [self.unpack('3f') for _ in range(4)]
            points = [self.unpack('3f') for _ in range(corners)]
            flags = self.unpack('I')[0]
            actor, material, item, link, brush_poly = [self.index() for _ in range(5)]
            self.take(4)  # light-map scale; UE2 does not store UE1's integer pans
            result.append(dict(points=points, base=base, normal=normal, u=u, v=v,
                               flags=flags, material=material, actor=actor))
        return result

    def terrain_native(self):
        if self.header(25) != (4, 3):
            raise ValueError('Unknown TerrainInfo native revision')
        sectors = self.refs()
        sectors_xy = self.unpack('2i')
        to_world, to_height, to_macro = [self.unpack('12f') for _ in range(3)]
        width, height = self.unpack('2i')
        count = self.count()
        colors = self.take(count*4)
        if count != width*height:
            raise ValueError('Terrain vertex color dimensions disagree')
        return dict(width=width, height=height, to_world=to_world, to_macro=to_macro, colors=colors)

    def vertex_colors(self, ref):
        self.properties(ref)
        if self.header(25) != (2, 5):
            raise ValueError('Unknown StaticMeshInstance native revision')
        return self.take(self.count()*4)  # native FColor BGRA
