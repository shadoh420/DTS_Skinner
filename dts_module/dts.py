from dts_module import helper
import struct
from dts_module import dts_mesh
import numpy as np

class dts_material_param:
    def __init__(self, data, curr_data_index, material_block_version):
        self.flags = helper.get_int(data, curr_data_index)
        self.alpha = helper.get_float(data, curr_data_index)
        self.internal_index = helper.get_int(data, curr_data_index) # Kaitai calls this 'index'
        
        self.rgb_r = helper.get_int8(data, curr_data_index)
        self.rgb_g = helper.get_int8(data, curr_data_index)
        self.rgb_b = helper.get_int8(data, curr_data_index)
        self.rgb_flags_byte = helper.get_int8(data, curr_data_index) # Often unused padding

        map_file_bytes = b''
        # Note: material_block_version is the version from the TS::MaterialList PERS block
        if material_block_version == 1: # map_file_old (16 bytes)
            map_file_bytes = data[curr_data_index[0]:curr_data_index[0] + 16]
            curr_data_index[0] += 16
        elif material_block_version >= 2: # map_file (32 bytes)
            map_file_bytes = data[curr_data_index[0]:curr_data_index[0] + 32]
            curr_data_index[0] += 32
        
        self.map_file = map_file_bytes.split(b'\x00')[0].decode('utf-8', 'ignore')

        if material_block_version >= 3:
            self.type = helper.get_int(data, curr_data_index)
            self.elasticity = helper.get_float(data, curr_data_index)
            self.friction = helper.get_float(data, curr_data_index)
        else:
            self.type = -1 
            self.elasticity = 0.0
            self.friction = 0.0

        if material_block_version >= 4:
            self.use_default_props = helper.get_int(data, curr_data_index)
        else:
            self.use_default_props = 0

class dts:
    def __init__(self):
        self.meshes = None
        self.always_node = None
        self.default_materials = None
        self.frame_trigger = None
        self.transitions = None
        self.objects = None
        self.details = None
        self.num_transitions = 0
        self.names = None
        self.transforms = None
        self.keyframes = None
        self.sub_sequences = None
        self.sequences = None
        self.nodes = None
        self.max_bounds = None
        self.min_bounds = None
        self.center = None
        self.radius = None
        self.num_frame_triggers = 0
        self.num_meshes = 0
        self.num_details = 0
        self.num_objects = 0
        self.num_names = 0
        self.num_transforms = 0
        self.num_keyframes = 0
        self.num_sub_seq = 0
        self.num_seq = 0
        self.version = None
        self.num_nodes = 0
        self.material_list = [] # To store parsed dts_material_param objects
        self.dts_version_from_material_list_pers = -1 # Store version from TS::MaterialList
        return

    def load_binary(self, data):
        if data[:4] != b"PERS":
            print("Wrong PERS header")
            return

        curr_data_index = [4]
        chunk_size = helper.get_int(data, curr_data_index)
        curr_data_index[0] += 2 # Flags?  Don't know...skipping for now

        if data[curr_data_index[0]:curr_data_index[0] + 9] != b'TS::Shape':
            print("Not a TS::Shape")
            return
        curr_data_index[0] += 10

        self.version = helper.get_int(data, curr_data_index)
        self.num_nodes = helper.get_int(data, curr_data_index)
        self.num_seq = helper.get_int(data, curr_data_index)
        self.num_sub_seq = helper.get_int(data, curr_data_index)
        self.num_keyframes = helper.get_int(data, curr_data_index)
        self.num_transforms = helper.get_int(data, curr_data_index)
        self.num_names = helper.get_int(data, curr_data_index)
        self.num_objects = helper.get_int(data, curr_data_index)
        self.num_details = helper.get_int(data, curr_data_index)
        self.num_meshes = helper.get_int(data, curr_data_index)

        if self.version >= 2:
            self.num_transitions = helper.get_int(data, curr_data_index)
        if self.version >= 4:
            self.num_frame_triggers = helper.get_int(data, curr_data_index)

        self.radius = helper.get_float(data, curr_data_index)

        self.center = helper.get_float3d(data, curr_data_index)

        if self.version > 7:
            self.min_bounds = helper.get_float3d(data, curr_data_index)
            self.max_bounds = helper.get_float3d(data, curr_data_index)
        else:
            self.min_bounds = self.center
            self.max_bounds = self.center
            self.min_bounds += self.min_bounds
            self.max_bounds += self.min_bounds
        self.nodes = []
        if self.version < 7:
            #  Reading 20 bytes into int
            node_struct = 'iiiii'
            node_bytesize = 20
        else:
            #  Reading 10 bytes into int16
            node_struct = 'hhhhh'
            node_bytesize = 10
        for i in range(0, self.num_nodes):
            [name, parent_node, num_sub_seq, first_sub_seq, default_transform] = struct.unpack(node_struct, data[curr_data_index[0]:curr_data_index[0] + node_bytesize])
            curr_data_index[0] += node_bytesize
            node = dts_node(name, parent_node, num_sub_seq, first_sub_seq, default_transform)
            self.nodes.append(node)

        name = cyclic = duration = priority = first_trigger_frame =\
            num_trigger_frames = num_ifl_sub_seq = first_ifl_sub_seq = None
        self.sequences = []
        for i in range(0, self.num_seq):
            if self.version >= 5:
                seq_struct = "iifiiiii"
                seq_bytesize = 8 * 4
                [name, cyclic, duration, priority, first_trigger_frame, num_trigger_frames, num_ifl_sub_seq, first_ifl_sub_seq] =\
                    struct.unpack(seq_struct, data[curr_data_index[0]:curr_data_index[0] + seq_bytesize])
            elif self.version >= 4:
                seq_struct = "iifiii"
                seq_bytesize = 6 * 4
                [name, cyclic, duration, priority, first_trigger_frame, num_trigger_frames] =\
                    struct.unpack(seq_struct, data[curr_data_index[0]:curr_data_index[0] + seq_bytesize])
            else:
                seq_struct = "iifi"
                seq_bytesize = 4 * 4
                [name, cyclic, duration, priority] =\
                    struct.unpack(seq_struct, data[curr_data_index[0]:curr_data_index[0] + seq_bytesize])

            self.sequences.append(dts_sequence(name, cyclic, duration, priority, first_trigger_frame, num_trigger_frames,
                                               num_ifl_sub_seq, first_ifl_sub_seq))
            curr_data_index[0] += seq_bytesize

        self.sub_sequences = []
        if self.version <= 7:
            sub_seq_struct = 'iii'
            ss_bytesize = 12
        else:
            sub_seq_struct = 'hhh'
            ss_bytesize = 6

        for i in range(0, self.num_sub_seq):
            [sequence_idx, num_key_frames, first_key_frame] = struct.unpack(sub_seq_struct, data[curr_data_index[0]:curr_data_index[0] + ss_bytesize])
            curr_data_index[0] += ss_bytesize
            self.sub_sequences.append(dts_sub_sequence(sequence_idx, num_key_frames, first_key_frame))

        self.keyframes = []
        if self.version < 3:
            kf_struct = 'fi'
            kf_bytesize = 8
            for i in range(0, self.num_keyframes):
                [position, key_value] = struct.unpack(kf_struct, data[curr_data_index[0]:curr_data_index[0] + kf_bytesize])
                curr_data_index[0] += kf_bytesize
                self.keyframes.append(dts_key_frames(position, key_value, 0))
        elif self.version <= 7:
            kf_struct = 'fii'
            kf_bytesize = 12
            for i in range(0, self.num_keyframes):
                [position, key_value, mat_index] = struct.unpack(kf_struct, data[curr_data_index[0]:curr_data_index[0] + kf_bytesize])
                curr_data_index[0] += kf_bytesize
                self.keyframes.append(dts_key_frames(position, key_value, mat_index))
        else:
            kf_struct = 'fhh'
            kf_bytesize = 8
            for i in range(0, self.num_keyframes):
                [position, key_value, mat_index] = struct.unpack(kf_struct, data[curr_data_index[0]:curr_data_index[0] + kf_bytesize])
                curr_data_index[0] += kf_bytesize
                self.keyframes.append(dts_key_frames(position, key_value, mat_index))

        self.transforms = []
        if self.version < 7:
            transform_struct = 'ffff'
            transform_bytesize = 12
        else:
            transform_struct = 'hhhh'
            transform_bytesize = 8

        for i in range(0, self.num_transforms):
            [x, y, z, w] = struct.unpack(transform_struct, data[curr_data_index[0]:curr_data_index[0] + transform_bytesize])
            curr_data_index[0] += transform_bytesize
            quat = dts_quat(x, y, z, w)
            translate = helper.get_float3d(data, curr_data_index)
            if self.version > 7: # ie. 8 has no scale
                scale = 1
            else:
                scale = helper.get_float3d(data, curr_data_index)
            self.transforms.append(dts_transform(quat, translate, scale))

        self.names = []
        for i in range(0, self.num_names):
            self.names.append(data[curr_data_index[0]:curr_data_index[0] + 24])
            curr_data_index[0] += 24

        self.objects = []
        for i in range(0, self.num_objects):
            if self.version <= 7:
                name = helper.get_int16(data, curr_data_index)
                flags = helper.get_int16(data, curr_data_index)
                mesh_index = helper.get_int(data, curr_data_index)
                node_index = helper.get_int(data, curr_data_index)
                offset_rot = dts_mat3f().read(data, curr_data_index)
                offset = None #  point offset is stored in the mat3F
                num_sub_seq = helper.get_int16(data, curr_data_index)
                first_sub_seq = helper.get_int16(data, curr_data_index)
            else:
                name = helper.get_int16(data, curr_data_index)
                flags = helper.get_int16(data, curr_data_index)
                mesh_index = helper.get_int(data, curr_data_index)
                node_index = helper.get_int16(data, curr_data_index)
                # Because c++ compiles the struct to have a structure here
                burning_variable = helper.get_int16(data, curr_data_index)
                offset_flags = None
                offset_rot = None
                offset = helper.get_float3d(data, curr_data_index)
                num_sub_seq = helper.get_int16(data, curr_data_index)
                first_sub_seq = helper.get_int16(data, curr_data_index)
            self.objects.append(dts_object(name, flags, mesh_index, node_index, offset_flags,
                                           offset_rot, offset, num_sub_seq, first_sub_seq))

        self.details = []
        for i in range(0, self.num_details):
            root_node = helper.get_int(data, curr_data_index)
            size = helper.get_float(data, curr_data_index)
            self.details.append(dts_details(root_node, size))

        self.transitions = []
        if self.version > 2:
            if self.version < 7:
                tran_struct = "iifffffffffffff"
                tran_bytesize = 15 * 4
                tran_increment = tran_bytesize
                max_value = 1
            elif self.version == 7:
                tran_struct = "iifffhhhhffffff"
                tran_bytesize = 13 * 4
                tran_increment = tran_bytesize
                max_value = 32767
            else:
                # Last float isn't real just need to read it into transform_scale to make the code cleaner
                # There will already be an additional 3 bytes anyways
                tran_struct = "iifffhhhhffffff"
                tran_bytesize = 13 * 4
                tran_increment = 10 * 4
                max_value = 32767

            for i in range(0, self.num_transitions):
                [start_seq, end_seq, start_pos, end_pos, duration, rot_x, rot_y, rot_z, rot_w, pos_x, pos_y, pos_z, scale_x, scale_y, scale_z] = \
                    struct.unpack(tran_struct, data[curr_data_index[0]:curr_data_index[0] + tran_bytesize])
                curr_data_index[0] += tran_increment

                quat = dts_quat(rot_x/max_value, rot_y/max_value, rot_z/max_value, rot_w/max_value)
                transform_pos = (pos_x, pos_y, pos_z)
                transform_scale = (scale_x, scale_y, scale_z)
                self.transitions.append(dts_transition(start_seq, end_seq, start_pos, end_pos, duration, quat,
                                                       transform_pos, transform_scale))

        self.frame_trigger = []
        if self.version >= 4:
            for i in range(0, self.num_frame_triggers):
                pos = helper.get_float(data, curr_data_index)
                value = helper.get_int(data, curr_data_index)
                self.frame_trigger.append(dts_frame_trigger(pos, value))

        if self.version >= 5:
            self.default_materials = helper.get_int(data, curr_data_index)
        else:
            self.default_materials = 0

        if self.version >= 6:
            self.always_node = helper.get_int(data, curr_data_index)
        else:
            self.always_node = -1

        self.meshes = []
        for _ in range(0, self.num_meshes):
            self.meshes.append(dts_mesh.mesh(data, curr_data_index))

        # --- MODIFIED MATERIAL PARSING ---
        # The material list is its own PERS block, usually following the mesh data.
        # The `has_materials` flag (if it's just a 0/1 int) might indicate if this block exists.
        
        # Find the start of the material list by looking for its PERS block
        # This is a simplified search; a more robust parser might track block offsets.
        # For now, let's assume it follows meshes or is findable.
        # The Kaitai structure implies `has_materials` (an int flag) is read,
        # then if 1, the `materials` (a PERS section) is read.

        # Let's assume curr_data_index is now at the `has_materials` flag (s4)
        has_materials_flag_value = helper.get_int(data, curr_data_index)

        if has_materials_flag_value == 1:
            # Expect a "PERS" block for "TS::MaterialList"
            if curr_data_index[0] + 4 <= len(data) and data[curr_data_index[0]:curr_data_index[0] + 4] == b"PERS":
                curr_data_index[0] += 4 # Skip 'PERS'
                _ = helper.get_int(data, curr_data_index) # block_size

                classname_len = helper.get_int16(data, curr_data_index) # u2 in Kaitai
                # Classname string is null-terminated and padded to an even boundary
                actual_classname_len_to_read = (classname_len + 1) & (~1)
                
                classname_bytes = data[curr_data_index[0]:curr_data_index[0] + classname_len]
                curr_data_index[0] += actual_classname_len_to_read
                
                if classname_bytes == b'TS::MaterialList':
                    self.dts_version_from_material_list_pers = helper.get_int(data, curr_data_index) # Version of this material block
                    
                    # According to dts.ksy for ts_mat_list:
                    # - num_details (u4)
                    # - num_materials (u4)
                    # The 'num_details' here is a bit unusual for a material list.
                    # For sensor_small.DTS, hex dump suggests:
                    # Version (e.g., 0F 00 00 00 = 15)
                    # num_details (e.g., 01 00 00 00 = 1)
                    # num_materials (e.g., 05 00 00 00 = 5)
                    _num_details_in_matlist = helper.get_int(data, curr_data_index) # Read and potentially ignore
                    num_actual_materials = helper.get_int(data, curr_data_index)

                    for _ in range(num_actual_materials):
                        mat_param = dts_material_param(data, curr_data_index, self.dts_version_from_material_list_pers)
                        self.material_list.append(mat_param)
                else:
                    print(f"Warning: Expected 'TS::MaterialList' PERS block, but found '{classname_bytes.decode('utf-8','ignore')}'")
            else:
                print("Warning: has_materials_flag is 1, but no 'PERS' block found for materials where expected.")
        # --- END OF MODIFIED MATERIAL PARSING ---
        
        self.print_stats() # You might want to update print_stats to show loaded materials
        return

    def load_file(self, file_name):
        with open(file_name, "rb") as file:
            data = file.read()
            return self.load_binary(data)

    def dump_obj_test(self, folder_name):
        for i in range(0, self.num_objects):
            name = str(self.names[self.objects[i].name]).split('\\x00')[0]

            mesh = self.meshes[self.objects[i].mesh_index]
            file_name = f"{folder_name}\\{name}.obj{i}.obj"
            with open(file_name, "w") as file:
                file.write(f"g\n")
                scale = mesh.frames[0].scale
                origin = mesh.frames[0].origin
                for vert in mesh.verts:
                    (x, y, z) = vert.get_unpacked_vert(scale, origin)
                    (nx, ny, nz) = vert.get_normal()
                    file.write(f"v {x} {z} {y}\n")
                    file.write(f"vn {nx} {nz} {ny}\n")

                for face in mesh.faces:
                    file.write(f"f {face.vert_index0+1} {face.vert_index2+1} {face.vert_index1+1}\n")

    def print_stats(self):
        print(f"Version: {self.version}")
        print(f"num nodes: {self.num_nodes}")
        print(f"num seq: {self.num_seq}")
        print(f"num sub_seq: {self.num_sub_seq}")
        print(f"num keyframes: {self.num_keyframes}")
        print(f"num transforms: {self.num_transforms}")
        print(f"num names: {self.num_names}")
        print(f"num objects: {self.num_objects}")
        print(f"num details: {self.num_details}")
        print(f"num meshes: {self.num_meshes}")

        print("Node Names")
        split_arg = "\\x00"
        node_tree = []
        for node_index in range(0, self.num_nodes):
            curr_node = self.nodes[node_index]
            if node_index == curr_node.parent_node or curr_node.parent_node == -1:
                print(f"parent_node {self.names[curr_node.name_index]}")

        for i in range(0, self.num_nodes):
            if self.nodes[i].parent_node >= i:
                print(i)

        #for nodes in self.nodes:
        #    print(str(self.names[nodes.name_index]).split(split_arg)[0])
        #    print(f"{nodes.parent_node} {str(self.names[self.nodes[nodes.parent_node].name_index]).split(split_arg)[0]}" )

        #print("sequence")
        #for seq in self.sequences:
        #   print(str(self.names[seq.name_index]).split("\\x00")[0])

class dts_node:
    def __init__(self, name, parent_node, num_sub_seq, first_sub_seq, default_transform):
        self.name_index = name
        self.parent_node = parent_node
        self.num_sub_seq = num_sub_seq
        self.first_sub_seq = first_sub_seq
        self.transform_index = default_transform

class dts_sequence:
    def __init__(self, name, cyclic, duration, priority, first_trigger_frame, num_trigger_frames, num_ifl_subsequences,
                 first_ifl_subsequence):
        self.name_index = name
        self.cyclic = cyclic
        self.duration = duration
        self.priority = priority
        self.first_trigger_frame = first_trigger_frame
        self.num_trigger_frames = num_trigger_frames
        self.num_ifl_subsequences = num_ifl_subsequences
        self.first_ifl_subsequence = first_ifl_subsequence

class dts_sub_sequence:
    def __init__(self, sequence_idx, num_key_frames, first_key_frame):
        self.sequence_idx = sequence_idx
        self.num_key_frames = num_key_frames
        self.first_key_frame = first_key_frame

class dts_key_frames:
    def __init__(self, position, key_value, mat_index):
        self.position = position
        self.key_value = key_value
        self.mat_index = mat_index

class dts_transform:
    def __init__(self, rotate, translate, scale):
        self.rotate = rotate
        self.translate = translate
        self.scale = scale

class dts_object:
    def __init__(self, name, flags, mesh_index, node_index, offset_flags, offset_rot, offset, num_sub_seq, first_sub_seq):
        self.name = name
        self.flags = flags
        self.mesh_index = mesh_index
        self.node_index = node_index
        self.offset_flags = offset_flags
        self.offset_rot = offset_rot
        self.offset = offset
        self.num_sub_seq = num_sub_seq
        self.first_sub_seq = first_sub_seq

    def get_translate_rotation(self, model: dts):
        node_ind = self.node_index
        translate = np.zeros(3)
        while node_ind > -1:
            node : dts_node = model.nodes[node_ind]
            transform : dts_transform = model.transforms[node.transform_index]

            translate += (transform.transform.translate * transform.rotate.get_numpy_rotational_matrix())
            node_ind = node.parent_node

        return translate

class dts_details:
    def __init__(self, root_node, size):
        self.root_node = root_node
        self.size = size

class dts_transition:
    def __init__(self, start_seq, end_seq, start_pos, end_pos, duration, transform_rot, transform_pos, transform_scale):
        self.start_seq = start_seq
        self.end_seq = end_seq
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.duration = duration
        self.transform_rot = transform_rot
        self.transform_pos = transform_pos
        self.transform_scale = transform_scale

class dts_frame_trigger:
    def __init__(self, pos, value):
        self.pos = pos
        self.value = value

class dts_quat:
    def __init__(self, x, y, z, w):
        self.x = x
        self.y = y
        self.z = z
        self.w = w

    def get_quatwxyz(self):
        return (self.w, self.x, self.y, self.z)

    def get_numpy_rotational_matrix(self):
        rotational_matrix = np.zeros((3,3), np.single)
        xs = self.x * 2.0
        ys = self.y * 2.0
        zs = self.z * 2.0
        wx = self.w * xs
        wy = self.w * ys
        wz = self.w * zs
        xx = self.x * xs
        xy = self.x * ys
        xz = self.x * zs
        yy = self.y * ys
        yz = self.y * zs
        zz = self.z * zs

        rotational_matrix[0,0] = 1 - (yy + zz)
        rotational_matrix[0,1] = xy - wz
        rotational_matrix[0,2] = xz + wy
        rotational_matrix[1,0] = xy + wz
        rotational_matrix[1,1] = 1 - (xx + zz)
        rotational_matrix[1,2] = yz - wx
        rotational_matrix[2,0] = xz - wy
        rotational_matrix[2,1] = yz + wx
        rotational_matrix[2,2] = 1 - (xx + yy)

        return rotational_matrix

class dts_mat3f:
    def __init__(self):
        self.flags = None
        self.arr_3_3 = None
        self.point = None

    def read(self, data, offset):
        self.flags = helper.get_int(data, offset)
        self.arr_3_3 = helper.get_float_array(data, 9, offset)
        self.point = helper.get_float3d(data, offset)
        return self
