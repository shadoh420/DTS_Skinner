from dts_module import helper
import struct
from dts_module import dts_mesh
import numpy as np

class dts_material_param:
    def __init__(self, data, curr_data_index, material_block_version):
        self.flags = helper.get_int(data, curr_data_index) # Kaitai: s4, assuming helper.get_int is u4, might need get_sint
        self.alpha = helper.get_float(data, curr_data_index)
        self.internal_index = helper.get_int(data, curr_data_index) # Kaitai: s4
        
        self.rgb_r = helper.get_int8(data, curr_data_index)
        self.rgb_g = helper.get_int8(data, curr_data_index)
        self.rgb_b = helper.get_int8(data, curr_data_index)
        self.rgb_flags_byte = helper.get_int8(data, curr_data_index)

        map_file_bytes = b''
        if material_block_version == 1:
            map_file_bytes = data[curr_data_index[0]:curr_data_index[0] + 16]
            curr_data_index[0] += 16
        elif material_block_version >= 2:
            map_file_bytes = data[curr_data_index[0]:curr_data_index[0] + 32]
            curr_data_index[0] += 32
        
        self.map_file = map_file_bytes.split(b'\x00')[0].decode('utf-8', 'ignore')

        if material_block_version >= 3:
            self.type = helper.get_int(data, curr_data_index) # Kaitai: s4
            self.elasticity = helper.get_float(data, curr_data_index)
            self.friction = helper.get_float(data, curr_data_index)
        else:
            self.type = -1 
            self.elasticity = 0.0
            self.friction = 0.0

        if material_block_version >= 4:
            self.use_default_props = helper.get_int(data, curr_data_index) # Kaitai: u4
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
        self.material_list = []
        self.dts_version_from_material_list_pers = -1
        return

    def load_binary(self, data):
        curr_data_index = [0] # Initialize at the very beginning, pointing to the start of the data

        if curr_data_index[0] + 4 > len(data) or data[curr_data_index[0]:curr_data_index[0] + 4] != b"PERS": # Check from offset 0
            print(f"ERROR: Wrong PERS header at start of file. Index: {curr_data_index[0]}. Data len: {len(data)}")
            return False # Indicate failure

        curr_data_index[0] = 4 # Now advance past "PERS"
        
        _ = helper.get_int(data, curr_data_index) # chunk_size of the PERS block, unused by us but needs to be read
        
        classname_len = helper.get_uint16(data, curr_data_index)
        actual_classname_len_to_read = (classname_len + 1) & (~1) # Padded to even boundary
        classname_bytes = data[curr_data_index[0]:curr_data_index[0] + classname_len]
        curr_data_index[0] += actual_classname_len_to_read
        
        if classname_bytes != b'TS::Shape':
            print(f"ERROR: Not a TS::Shape. Found: {classname_bytes.decode('utf-8','ignore')}")
            return
        
        self.version = helper.get_int(data, curr_data_index) # u4
        print(f"DEBUG: DTS File Version: {self.version}")

        self.num_nodes = helper.get_int(data, curr_data_index)
        self.num_seq = helper.get_int(data, curr_data_index)
        self.num_sub_seq = helper.get_int(data, curr_data_index)
        self.num_keyframes = helper.get_int(data, curr_data_index)
        self.num_transforms = helper.get_int(data, curr_data_index)
        self.num_names = helper.get_int(data, curr_data_index)
        self.num_objects = helper.get_int(data, curr_data_index)
        self.num_details = helper.get_int(data, curr_data_index)
        self.num_meshes = helper.get_int(data, curr_data_index)

        # num_transitions and num_frame_triggers are read differently based on version in Kaitai
        # Kaitai: num_transitions (u4) if version >= 2
        # Kaitai: num_frametriggers (u4) if version >= 4
        # Your code reads them sequentially if version conditions met. This seems okay.
        if self.version >= 2:
            self.num_transitions = helper.get_int(data, curr_data_index)
        if self.version >= 4:
            self.num_frame_triggers = helper.get_int(data, curr_data_index)

        self.radius = helper.get_float(data, curr_data_index)
        self.center = helper.get_float3d(data, curr_data_index)

        if self.version >= 8: # Kaitai: bounds (box3f) if version >= 8
            self.min_bounds = helper.get_float3d(data, curr_data_index)
            self.max_bounds = helper.get_float3d(data, curr_data_index)
        else: # For v <= 7, bounds are not explicitly stored here or derived differently.
              # Your original logic for v <= 7:
            self.min_bounds = list(self.center) # Make mutable
            self.max_bounds = list(self.center) # Make mutable
            # This was likely a placeholder. DTS files usually have bounds.
            # Kaitai implies bounds are only explicitly here for v8+.
            # For older versions, bounds might be implicit or part of the "bounds node" transform.
            # Let's keep your original derivation for now if not v8+
            self.min_bounds[0] -= self.radius; self.min_bounds[1] -= self.radius; self.min_bounds[2] -= self.radius;
            self.max_bounds[0] += self.radius; self.max_bounds[1] += self.radius; self.max_bounds[2] += self.radius;


        print(f"DEBUG: Before nodes. Version: {self.version}. Num_nodes: {self.num_nodes}. curr_data_index: {curr_data_index[0]}")
        self.nodes = []
        if self.version == 7: # Kaitai: nodev7 (u4, s4, u4, u4, u4)
            for _ in range(self.num_nodes):
                name_idx = helper.get_int(data, curr_data_index)          # u4
                parent_node_idx = helper.get_sint(data, curr_data_index)  # s4
                num_ss = helper.get_int(data, curr_data_index)            # u4
                first_ss = helper.get_int(data, curr_data_index)          # u4
                default_tf = helper.get_int(data, curr_data_index)        # u4
                self.nodes.append(dts_node(name_idx, parent_node_idx, num_ss, first_ss, default_tf))
        elif self.version >= 8: # Kaitai: node (u2, s2, u2, u2, u2)
            for _ in range(self.num_nodes):
                name_idx = helper.get_uint16(data, curr_data_index)       # u2
                parent_node_idx = helper.get_int16(data, curr_data_index) # s2
                num_ss = helper.get_uint16(data, curr_data_index)         # u2
                first_ss = helper.get_uint16(data, curr_data_index)       # u2
                default_tf = helper.get_uint16(data, curr_data_index)     # u2
                self.nodes.append(dts_node(name_idx, parent_node_idx, num_ss, first_ss, default_tf))
        else: # Versions < 7 (e.g. v2-v6, assuming they use 20 bytes like your original code)
              # This path might need more specific version checks if formats differ significantly.
            node_struct = 'iiiii' # 5x s4 (name, parent, num_sub_seq, first_sub_seq, default_transform)
            node_bytesize = 20
            for _ in range(self.num_nodes):
                [name, parent_node, num_sub_seq, first_sub_seq, default_transform_idx] = \
                    struct.unpack(node_struct, data[curr_data_index[0]:curr_data_index[0] + node_bytesize])
                curr_data_index[0] += node_bytesize
                self.nodes.append(dts_node(name, parent_node, num_sub_seq, first_sub_seq, default_transform_idx))
        print(f"DEBUG: After nodes. Read {len(self.nodes)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before sequences. Num_seq: {self.num_seq}. curr_data_index: {curr_data_index[0]}")
        self.sequences = []
        # Kaitai: vector_sequence (u4, u4, f4, u4, u4, u4, u4, u4) - consistent across versions where it exists
        # Your original logic for versions seems fine here.
        for _ in range(0, self.num_seq):
            name_idx = helper.get_int(data, curr_data_index)
            cyclic = helper.get_int(data, curr_data_index) # u4, but often 0 or 1, so int is fine
            duration = helper.get_float(data, curr_data_index)
            priority = helper.get_int(data, curr_data_index)
            first_trigger = 0; num_triggers = 0; num_ifl = 0; first_ifl = 0
            if self.version >= 4: # Fields for v4+
                first_trigger = helper.get_int(data, curr_data_index)
                num_triggers = helper.get_int(data, curr_data_index)
            if self.version >= 5: # Fields for v5+
                num_ifl = helper.get_int(data, curr_data_index)
                first_ifl = helper.get_int(data, curr_data_index)
            self.sequences.append(dts_sequence(name_idx, cyclic, duration, priority, first_trigger, num_triggers, num_ifl, first_ifl))
        print(f"DEBUG: After sequences. Read {len(self.sequences)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before sub_sequences. Num_sub_seq: {self.num_sub_seq}. curr_data_index: {curr_data_index[0]}")
        self.sub_sequences = []
        if self.version == 7: # Kaitai: subsequencev7 (u4, u4, u4)
            for _ in range(self.num_sub_seq):
                seq_idx = helper.get_int(data, curr_data_index)    # u4
                num_kf = helper.get_int(data, curr_data_index)     # u4
                first_kf = helper.get_int(data, curr_data_index)   # u4
                self.sub_sequences.append(dts_sub_sequence(seq_idx, num_kf, first_kf))
        elif self.version >= 8: # Kaitai: subsequence (u2, u2, u2)
            for _ in range(self.num_sub_seq):
                seq_idx = helper.get_uint16(data, curr_data_index) # u2
                num_kf = helper.get_uint16(data, curr_data_index)  # u2
                first_kf = helper.get_uint16(data, curr_data_index)# u2
                self.sub_sequences.append(dts_sub_sequence(seq_idx, num_kf, first_kf))
        else: # Versions < 7 (assuming 12 bytes like your original code for <=7)
            sub_seq_struct = 'iii' # 3x s4 (sequence_idx, num_key_frames, first_key_frame)
            ss_bytesize = 12
            for _ in range(self.num_sub_seq):
                [sequence_idx, num_key_frames, first_key_frame] = \
                    struct.unpack(sub_seq_struct, data[curr_data_index[0]:curr_data_index[0] + ss_bytesize])
                curr_data_index[0] += ss_bytesize
                self.sub_sequences.append(dts_sub_sequence(sequence_idx, num_key_frames, first_key_frame))
        print(f"DEBUG: After sub_sequences. Read {len(self.sub_sequences)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before keyframes. Num_keyframes: {self.num_keyframes}. curr_data_index: {curr_data_index[0]}")
        self.keyframes = []
        if self.version == 7: # Kaitai: keyframev7 (f4, u4, u4)
            for _ in range(self.num_keyframes):
                pos = helper.get_float(data, curr_data_index)   # f4
                kv = helper.get_int(data, curr_data_index)      # u4
                mat_idx = helper.get_int(data, curr_data_index) # u4
                self.keyframes.append(dts_key_frames(pos, kv, mat_idx))
        elif self.version >= 8: # Kaitai: keyframe (f4, u2, u2)
            for _ in range(self.num_keyframes):
                pos = helper.get_float(data, curr_data_index)      # f4
                kv = helper.get_uint16(data, curr_data_index)     # u2
                mat_idx = helper.get_uint16(data, curr_data_index)# u2
                self.keyframes.append(dts_key_frames(pos, kv, mat_idx))
        elif self.version < 3: # Your original logic
            kf_struct = 'fi' # f4, s4 (position, key_value)
            kf_bytesize = 8
            for _ in range(0, self.num_keyframes):
                [position, key_value] = struct.unpack(kf_struct, data[curr_data_index[0]:curr_data_index[0] + kf_bytesize])
                curr_data_index[0] += kf_bytesize
                self.keyframes.append(dts_key_frames(position, key_value, 0)) # mat_index is 0
        else: # Versions 3 to 6 (assuming 12 bytes like your original code for <=7)
            kf_struct = 'fii' # f4, s4, s4 (position, key_value, mat_index)
            kf_bytesize = 12
            for _ in range(0, self.num_keyframes):
                [position, key_value, mat_index_val] = struct.unpack(kf_struct, data[curr_data_index[0]:curr_data_index[0] + kf_bytesize])
                curr_data_index[0] += kf_bytesize
                self.keyframes.append(dts_key_frames(position, key_value, mat_index_val))
        print(f"DEBUG: After keyframes. Read {len(self.keyframes)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before transforms. Num_transforms: {self.num_transforms}. curr_data_index: {curr_data_index[0]}")
        self.transforms = []
        for _ in range(self.num_transforms):
            # Quat16: x(s2), y(s2), z(s2), w(s2)
            qx = helper.get_int16(data, curr_data_index)
            qy = helper.get_int16(data, curr_data_index)
            qz = helper.get_int16(data, curr_data_index)
            qw = helper.get_int16(data, curr_data_index)
            # Pass raw s2 values to dts_quat, or convert here.
            # For now, dts_quat expects floats, so let's pass them as floats (though they are s2 ranges)
            quat = dts_quat(float(qx), float(qy), float(qz), float(qw))
            
            translate = helper.get_float3d(data, curr_data_index) # Point3f
            
            scale = (1.0, 1.0, 1.0) # Default for v8+
            if self.version <= 7: # Kaitai: transformv7 has scale (Point3f)
                scale = helper.get_float3d(data, curr_data_index)
            self.transforms.append(dts_transform(quat, translate, scale))
        print(f"DEBUG: After transforms. Read {len(self.transforms)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before names. Num_names: {self.num_names}. curr_data_index: {curr_data_index[0]}")
        self.names = []
        for _ in range(self.num_names):
            self.names.append(data[curr_data_index[0]:curr_data_index[0] + 24]) # 24 bytes per name string
            curr_data_index[0] += 24
        print(f"DEBUG: After names. Read {len(self.names)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before objects. Num_objects: {self.num_objects}. curr_data_index: {curr_data_index[0]}")
        self.objects = []
        for _ in range(self.num_objects):
            offset_flags_val = None; offset_rot_val = None; offset_val = None # Init for clarity
            if self.version == 7: # Kaitai: objectv7
                name_idx = helper.get_uint16(data, curr_data_index)       # u2
                flags_val = helper.get_uint16(data, curr_data_index)      # u2
                mesh_idx = helper.get_int(data, curr_data_index)          # u4
                node_idx = helper.get_int(data, curr_data_index)          # u4
                offset_rot_val = dts_mat3f().read(data, curr_data_index)  # tmat3f
                num_ss = helper.get_int(data, curr_data_index)            # u4
                first_ss = helper.get_int(data, curr_data_index)          # u4
            elif self.version >= 8: # Kaitai: objectv8
                name_idx = helper.get_int16(data, curr_data_index)        # s2
                flags_val = helper.get_int16(data, curr_data_index)       # s2
                mesh_idx = helper.get_int(data, curr_data_index)          # s4 (Kaitai: s4)
                node_idx = helper.get_int16(data, curr_data_index)        # s2
                _ = helper.get_uint16(data, curr_data_index)              # dummy u2
                offset_val = helper.get_float3d(data, curr_data_index)    # point3f
                num_ss = helper.get_int16(data, curr_data_index)          # s2
                first_ss = helper.get_int16(data, curr_data_index)        # s2
            else: # Versions < 7 (Your original logic for <=7)
                name_idx = helper.get_int16(data, curr_data_index)
                flags_val = helper.get_int16(data, curr_data_index)
                mesh_idx = helper.get_int(data, curr_data_index)
                node_idx = helper.get_int(data, curr_data_index) # Your original was s4
                offset_rot_val = dts_mat3f().read(data, curr_data_index)
                num_ss = helper.get_int16(data, curr_data_index) # Your original was s2
                first_ss = helper.get_int16(data, curr_data_index) # Your original was s2
            self.objects.append(dts_object(name_idx, flags_val, mesh_idx, node_idx, 
                                           offset_flags_val, offset_rot_val, offset_val, 
                                           num_ss, first_ss))
        print(f"DEBUG: After objects. Read {len(self.objects)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before details. Num_details: {self.num_details}. curr_data_index: {curr_data_index[0]}")
        self.details = []
        # Kaitai: detail (u4, f4) - consistent across versions
        for _ in range(self.num_details):
            root_node_idx = helper.get_int(data, curr_data_index) # u4
            size_val = helper.get_float(data, curr_data_index)    # f4
            self.details.append(dts_details(root_node_idx, size_val))
        print(f"DEBUG: After details. Read {len(self.details)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before transitions. Num_transitions: {self.num_transitions}. curr_data_index: {curr_data_index[0]}")
        self.transitions = []
        if self.num_transitions > 0: # Only read if num_transitions > 0
            if self.version == 7: # Kaitai: transitionv7 (u4, u4, f4, f4, transformv7)
                                  # transformv7: quat16, point3f (translate), point3f (scale)
                for _ in range(self.num_transitions):
                    start_seq = helper.get_int(data, curr_data_index)
                    end_seq = helper.get_int(data, curr_data_index)
                    start_pos = helper.get_float(data, curr_data_index)
                    end_pos = helper.get_float(data, curr_data_index)
                    # transformv7 part
                    qx = helper.get_int16(data, curr_data_index)
                    qy = helper.get_int16(data, curr_data_index)
                    qz = helper.get_int16(data, curr_data_index)
                    qw = helper.get_int16(data, curr_data_index)
                    quat = dts_quat(float(qx), float(qy), float(qz), float(qw))
                    trans_pos = helper.get_float3d(data, curr_data_index)
                    trans_scale = helper.get_float3d(data, curr_data_index)
                    # dts_transition expects duration, but v7 doesn't have it here. Pass 0.
                    self.transitions.append(dts_transition(start_seq, end_seq, start_pos, end_pos, 0.0, 
                                                           quat, trans_pos, trans_scale))
            elif self.version >= 8: # Kaitai: transition (u4, u4, f4, f4, f4, transform)
                                   # transform: quat16, point3f (translate)
                for _ in range(self.num_transitions):
                    start_seq = helper.get_int(data, curr_data_index)
                    end_seq = helper.get_int(data, curr_data_index)
                    start_pos = helper.get_float(data, curr_data_index)
                    end_pos = helper.get_float(data, curr_data_index)
                    duration = helper.get_float(data, curr_data_index)
                    # transform part
                    qx = helper.get_int16(data, curr_data_index)
                    qy = helper.get_int16(data, curr_data_index)
                    qz = helper.get_int16(data, curr_data_index)
                    qw = helper.get_int16(data, curr_data_index)
                    quat = dts_quat(float(qx), float(qy), float(qz), float(qw))
                    trans_pos = helper.get_float3d(data, curr_data_index)
                    trans_scale = (1.0, 1.0, 1.0) # No scale in v8 transform
                    self.transitions.append(dts_transition(start_seq, end_seq, start_pos, end_pos, duration,
                                                           quat, trans_pos, trans_scale))
            else: # Versions < 7 (Your original logic)
                tran_struct = "iifffffffffffff" # 15 * f4
                tran_bytesize = 15 * 4
                for _ in range(self.num_transitions):
                    # This unpacks too many floats if trying to match Kaitai's older transform (quat16, p3f, p3f)
                    # This part is hard to reconcile without knowing the exact v<7 format.
                    # For now, keeping your original logic for v<7.
                    [ss, es, sp, ep, dur, rx,ry,rz,rw, px,py,pz, sx,sy,sz] = \
                        struct.unpack(tran_struct, data[curr_data_index[0]:curr_data_index[0] + tran_bytesize])
                    curr_data_index[0] += tran_bytesize
                    quat = dts_quat(rx, ry, rz, rw) # Assuming these are already float-like
                    t_pos = (px,py,pz); t_scale = (sx,sy,sz)
                    self.transitions.append(dts_transition(ss,es,sp,ep,dur,quat,t_pos,t_scale))
        print(f"DEBUG: After transitions. Read {len(self.transitions)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before frame_triggers. Num_frame_triggers: {self.num_frame_triggers}. curr_data_index: {curr_data_index[0]}")
        self.frame_trigger = []
        if self.num_frame_triggers > 0 and self.version >= 4: # Kaitai: frame_trigger (f4, u4)
            for _ in range(self.num_frame_triggers):
                pos = helper.get_float(data, curr_data_index)
                value = helper.get_int(data, curr_data_index) # u4
                self.frame_trigger.append(dts_frame_trigger(pos, value))
        print(f"DEBUG: After frame_triggers. Read {len(self.frame_trigger)}. curr_data_index: {curr_data_index[0]}")


        if self.version >= 5: # Kaitai: default_material (u4)
            self.default_materials = helper.get_int(data, curr_data_index)
        else: self.default_materials = 0
        print(f"DEBUG: Default materials: {self.default_materials}. curr_data_index: {curr_data_index[0]}")

        if self.version >= 6: # Kaitai: always_animate (s4)
            self.always_node = helper.get_sint(data, curr_data_index)
        else: self.always_node = -1
        print(f"DEBUG: Always node: {self.always_node}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before meshes. Num_meshes: {self.num_meshes}. curr_data_index: {curr_data_index[0]}")
        self.meshes = []
        for i in range(self.num_meshes):
            print(f"DEBUG: Reading mesh {i + 1}/{self.num_meshes}. curr_data_index before mesh: {curr_data_index[0]}")
            # Check for PERS header of the mesh itself
            if curr_data_index[0] + 4 > len(data) or data[curr_data_index[0]:curr_data_index[0] + 4] != b"PERS":
                print(f"ERROR: Expected PERS header for mesh {i+1} at offset {curr_data_index[0]}, but not found or EOS.")
                # Fill with None or break, depending on how you want to handle partial loads
                self.meshes.append(None) # Or some placeholder
                continue # Try to parse next mesh if any, or just break
            
            mesh_instance = dts_mesh.mesh(data, curr_data_index)
            # dts_mesh.mesh advances curr_data_index internally
            if not hasattr(mesh_instance, 'faces'): # Basic check if mesh init failed PERS check or other critical parts
                print(f"ERROR: Mesh instance {i + 1} seems uninitialized or failed its own PERS/CelAnimMesh check.")
            self.meshes.append(mesh_instance)
            print(f"DEBUG: Reading mesh {i + 1}/{self.num_meshes}. curr_data_index after mesh: {curr_data_index[0]}")
        print(f"DEBUG: After meshes. Read {len(self.meshes)}. curr_data_index: {curr_data_index[0]}")


        print(f"DEBUG: Before material list. curr_data_index: {curr_data_index[0]}")
        has_materials_flag_value = helper.get_int(data, curr_data_index) # s4 in Kaitai, u4 in your helper
        print(f"DEBUG: has_materials_flag_value: {has_materials_flag_value}")

        if has_materials_flag_value == 1:
            if curr_data_index[0] + 4 <= len(data) and data[curr_data_index[0]:curr_data_index[0] + 4] == b"PERS":
                curr_data_index[0] += 4 
                _ = helper.get_int(data, curr_data_index) # block_size

                mat_classname_len = helper.get_uint16(data, curr_data_index) # u2
                mat_actual_classname_len_to_read = (mat_classname_len + 1) & (~1)
                mat_classname_bytes = data[curr_data_index[0]:curr_data_index[0] + mat_classname_len]
                curr_data_index[0] += mat_actual_classname_len_to_read
                
                if mat_classname_bytes == b'TS::MaterialList':
                    self.dts_version_from_material_list_pers = helper.get_int(data, curr_data_index) # u4
                    print(f"DEBUG: MaterialList version: {self.dts_version_from_material_list_pers}")
                    
                    _num_details_in_matlist = helper.get_int(data, curr_data_index) # u4
                    num_actual_materials = helper.get_int(data, curr_data_index)   # u4
                    print(f"DEBUG: Num materials in list: {num_actual_materials}, num_details_in_matlist: {_num_details_in_matlist}")

                    for _ in range(num_actual_materials): # Kaitai: repeat-expr: num_materials (which is num_actual_materials here)
                        mat_param = dts_material_param(data, curr_data_index, self.dts_version_from_material_list_pers)
                        self.material_list.append(mat_param)
                    print(f"DEBUG: Parsed {len(self.material_list)} materials.")
                else:
                    print(f"Warning: Expected 'TS::MaterialList' PERS block, but found '{mat_classname_bytes.decode('utf-8','ignore')}'")
            else:
                print("Warning: has_materials_flag is 1, but no 'PERS' block found for materials where expected.")
        print(f"DEBUG: After material list. curr_data_index: {curr_data_index[0]}. EOF: {curr_data_index[0] >= len(data)}")
        
        self.print_stats()
        return True # Indicate success

    def load_file(self, file_name):
        with open(file_name, "rb") as file:
            data = file.read()
            return self.load_binary(data)

    def dump_obj_test(self, folder_name):
        # ... (This method seems fine, no changes needed based on current issues) ...
        pass

    def print_stats(self):
        print(f"--- DTS Stats ---")
        print(f"Version: {self.version}")
        print(f"Num Nodes: {self.num_nodes} (Parsed: {len(self.nodes) if self.nodes else 0})")
        print(f"Num Sequences: {self.num_seq} (Parsed: {len(self.sequences) if self.sequences else 0})")
        # ... add more stats for other lists ...
        print(f"Num Meshes: {self.num_meshes} (Parsed: {len(self.meshes) if self.meshes else 0})")
        print(f"Num Materials in List: {len(self.material_list) if self.material_list else 0}")
        # ...
        # split_arg = b"\\x00" # Corrected: use bytes for split
        # for i, node_obj in enumerate(self.nodes or []):
        #     node_name = "Unknown"
        #     if self.names and 0 <= node_obj.name_index < len(self.names):
        #         try:
        #             node_name = self.names[node_obj.name_index].split(split_arg)[0].decode('utf-8', 'ignore')
        #         except: pass
        #     # print(f"Node {i}: Name='{node_name}', Parent={node_obj.parent_node}")


class dts_node:
    def __init__(self, name_idx, parent_node_idx, num_sub_seq, first_sub_seq, default_transform_idx):
        self.name_index = name_idx
        self.parent_node = parent_node_idx
        self.num_sub_seq = num_sub_seq
        self.first_sub_seq = first_sub_seq
        self.transform_index = default_transform_idx

class dts_sequence:
    def __init__(self, name_idx, cyclic, duration, priority, first_trigger_frame, num_trigger_frames, num_ifl_subsequences,
                 first_ifl_subsequence):
        self.name_index = name_idx
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
    def __init__(self, rotate_quat, translate_vec, scale_vec): # Changed params for clarity
        self.rotate = rotate_quat # Should be a dts_quat object
        self.translate = translate_vec # Should be a (x,y,z) tuple
        self.scale = scale_vec # Should be a (x,y,z) tuple

class dts_object:
    def __init__(self, name_idx, flags_val, mesh_idx, node_idx, offset_flags_val, offset_rot_val, offset_val, num_sub_seq, first_sub_seq):
        self.name = name_idx
        self.flags = flags_val
        self.mesh_index = mesh_idx
        self.node_index = node_idx
        self.offset_flags = offset_flags_val # This was not used in original, keeping for potential future use
        self.offset_rot = offset_rot_val     # This is a TMat3F for v<=7
        self.offset = offset_val             # This is a Point3F for v8+
        self.num_sub_seq = num_sub_seq
        self.first_sub_seq = first_sub_seq

    # ... (get_translate_rotation method - no change needed for this fix) ...

class dts_details:
    def __init__(self, root_node_idx, size_val):
        self.root_node = root_node_idx
        self.size = size_val

class dts_transition:
    def __init__(self, start_seq, end_seq, start_pos, end_pos, duration, 
                 transform_rot_quat, transform_pos_vec, transform_scale_vec):
        self.start_seq = start_seq
        self.end_seq = end_seq
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.duration = duration
        self.transform_rot = transform_rot_quat     # dts_quat object
        self.transform_pos = transform_pos_vec     # (x,y,z) tuple
        self.transform_scale = transform_scale_vec # (x,y,z) tuple

class dts_frame_trigger:
    def __init__(self, pos, value):
        self.pos = pos
        self.value = value

class dts_quat:
    def __init__(self, x, y, z, w): # Expects float inputs (raw s2 values should be passed as floats)
        self.x = x
        self.y = y
        self.z = z
        self.w = w

    # ... (get_quatwxyz, get_numpy_rotational_matrix methods - no change needed) ...
    def get_quatwxyz(self):
        return (self.w, self.x, self.y, self.z)

    def get_numpy_rotational_matrix(self):
        # This conversion is for when x,y,z,w are already normalized quaternion components
        # If they are raw s2 values, they need to be divided by 32767.0 first
        # For now, assume they are passed as floats that might be raw s2 values
        # The actual normalization and conversion to matrix happens in export_model.py
        rotational_matrix = np.zeros((3,3), np.single)
        
        # If x,y,z,w are raw s2, they need normalization first.
        # This function assumes they are components of a (potentially unnormalized) quaternion.
        # Let's assume the values are small (like raw s2) and need to be treated as such.
        # The export_model.py handles the /32767.0 conversion.
        
        xs = self.x * 2.0; ys = self.y * 2.0; zs = self.z * 2.0
        wx = self.w * xs; wy = self.w * ys; wz = self.w * zs
        xx = self.x * xs; xy = self.x * ys; xz = self.x * zs
        yy = self.y * ys; yz = self.y * zs; zz = self.z * zs

        rotational_matrix[0,0] = 1.0 - (yy + zz)
        rotational_matrix[0,1] = xy - wz
        rotational_matrix[0,2] = xz + wy
        rotational_matrix[1,0] = xy + wz
        rotational_matrix[1,1] = 1.0 - (xx + zz)
        rotational_matrix[1,2] = yz - wx
        rotational_matrix[2,0] = xz - wy
        rotational_matrix[2,1] = yz + wx
        rotational_matrix[2,2] = 1.0 - (xx + yy)
        return rotational_matrix


class dts_mat3f:
    def __init__(self):
        self.flags = None
        self.arr_3_3 = None # 3x3 matrix as a flat list of 9 floats
        self.point = None   # (x,y,z) translation

    def read(self, data, offset_arr): # Changed to offset_arr
        self.flags = helper.get_int(data, offset_arr) # u4
        self.arr_3_3 = helper.get_float_array(data, 9, offset_arr)
        self.point = helper.get_float3d(data, offset_arr)
        return self