from dts_module import helper
from dts_module import tribes_normal


class mesh:
    def __init__(self, data, data_index):
        if data[data_index[0]:data_index[0] + 4] != b"PERS":
            print("Wrong PERS header")
            return

        data_index[0] += 4 # Flags?  Don't know...skipping for now
        chunk_size = helper.get_int(data, data_index)
        data_index[0] += 2 # Flags?  Don't know...skipping for now

        if data[data_index[0]:data_index[0] + 15] != b'TS::CelAnimMesh':
            print("Not a TS::CelAnimMesh")
            return

        data_index[0] += 16
        version = helper.get_int(data, data_index)
        self.num_verts = helper.get_int(data, data_index)
        self.verts_per_frame = helper.get_int(data, data_index)
        self.num_texture_verts = helper.get_int(data, data_index)
        self.num_faces = helper.get_int(data, data_index)
        self.num_frames = helper.get_int(data, data_index)

        if version >= 2:
            self.texture_verts_per_frame = helper.get_int(data, data_index)
        else:
            self.texture_verts_per_frame = self.num_texture_verts

        if version < 3:
            self.v2_scale = helper.get_float3d(data, data_index)
            self.v2_origin = helper.get_float3d(data, data_index)

        self.radius = helper.get_float(data, data_index)

        self.verts = []
        for _ in range(0, self.num_verts):
            self.verts.append(dts_vert(data, data_index))

        self.text_verts = []
        for _ in range(0, self.num_texture_verts):
            self.text_verts.append(helper.get_float2d(data, data_index))

        self.faces = []
        for _ in range(0, self.num_faces):
            self.faces.append(dts_mesh_face(data, data_index))

        self.frames = []
        for _ in range(0, self.num_frames):
            self.frames.append(dts_frame(version, data, data_index))


class dts_vert:
    def __init__(self, data, data_index):
        self.packed_x = helper.get_int8(data, data_index)
        self.packed_y = helper.get_int8(data, data_index)
        self.packed_z = helper.get_int8(data, data_index)
        self.normal_index = helper.get_int8(data, data_index)
        self.normal = tribes_normal.tribes_normal_table[self.normal_index]

    def get_unpacked_vert(self, scale, origin):
        return (self.packed_x * scale[0] + origin[0],
                self.packed_y * scale[1] + origin[1],
                self.packed_z * scale[2] + origin[2])

    def get_normal(self):
        return self.normal

class dts_mesh_face:
    def __init__(self, data, data_index):
        self.vert_index0 = helper.get_int(data, data_index)
        self.tex_index0 = helper.get_int(data, data_index)
        self.vert_index1 = helper.get_int(data, data_index)
        self.tex_index1 = helper.get_int(data, data_index)
        self.vert_index2 = helper.get_int(data, data_index)
        self.tex_index2 = helper.get_int(data, data_index)
        self.mat_index = helper.get_int(data, data_index)

class dts_frame:
    def __init__(self, version, data, data_index):
        self.first_vert = helper.get_int(data, data_index)
        if version >= 3:
            self.scale = helper.get_float3d(data, data_index)
            self.origin = helper.get_float3d(data, data_index)
