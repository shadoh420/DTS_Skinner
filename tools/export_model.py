# tools/export_model.py

import sys, pathlib, json, math, argparse

project_root = pathlib.Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path: # Add only if not already there
    sys.path.insert(0, str(project_root))

try:
    from dts_module import dts
except ImportError as e:
    # This error will be critical if the module cannot be found
    print(f"CRITICAL ERROR in export_model.py: Failed to import 'dts' from 'dts_module': {e}")
    print(f"       Ensure 'dts_module' directory is in {project_root} and has an __init__.py if needed.")
    # If this happens when imported by app.py, app.py's import will fail first.
    # If run directly and it fails, this provides a clear message.
    raise # Re-raise the import error to stop execution if run directly

# --- Helper Functions ---
def get_all_descendant_nodes(shape_nodes, root_node_idx_param):
    # ... (no changes needed) ...
    nodes_in_lod_set = set()
    if root_node_idx_param < 0 or root_node_idx_param >= len(shape_nodes): return nodes_in_lod_set
    queue = [root_node_idx_param]; nodes_in_lod_set.add(root_node_idx_param); head_ptr = 0
    while head_ptr < len(queue):
        current_parent_idx = queue[head_ptr]; head_ptr += 1
        for i, node_obj in enumerate(shape_nodes):
            if node_obj.parent_node == current_parent_idx and i != current_parent_idx:
                if i not in nodes_in_lod_set: nodes_in_lod_set.add(i); queue.append(i)
    return nodes_in_lod_set

def get_matrix_from_quat_trans(q_tuple_raw, t_tuple, s_tuple=(1.0,1.0,1.0)):
    raw_qx, raw_qy, raw_qz, raw_qw = q_tuple_raw 
    tx, ty, tz = t_tuple; sx, sy, sz = s_tuple
    qx = float(raw_qx) / 32767.0; qy = float(raw_qy) / 32767.0
    qz = float(raw_qz) / 32767.0; qw = float(raw_qw) / 32767.0
    nn = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    if nn < 1e-6: qx, qy, qz, qw = 0.0, 0.0, 0.0, 1.0
    else: qx /= nn; qy /= nn; qz /= nn; qw /= nn
    x2,y2,z2 = qx+qx,qy+qy,qz+qz; xx,xy,xz = qx*x2,qx*y2,qx*z2
    yy,yz,zz = qy*y2,qy*z2,qz*z2; wx,wy,wz = qw*x2,qw*y2,qw*z2
    r00,r01,r02 = 1-(yy+zz), xy-wz,     xz+wy
    r10,r11,r12 = xy+wz,     1-(xx+zz), yz-wx
    r20,r21,r22 = xz-wy,     yz+wx,     1-(xx+yy)
    final_mat = [[0.0]*4 for _ in range(4)]
    final_mat[0][0] = r00*sx; final_mat[0][1] = r01*sy; final_mat[0][2] = r02*sz;
    final_mat[1][0] = r10*sx; final_mat[1][1] = r11*sy; final_mat[1][2] = r12*sz;
    final_mat[2][0] = r20*sx; final_mat[2][1] = r21*sy; final_mat[2][2] = r22*sz;
    final_mat[0][3] = tx; final_mat[1][3] = ty; final_mat[2][3] = tz;
    final_mat[3][3] = 1.0
    return final_mat

def multiply_matrices(mat_a, mat_b):
    c = [[0.0]*4 for _ in range(4)];
    for i in range(4):
        for j in range(4):
            sum_val = 0.0
            for k_val in range(4): sum_val += mat_a[i][k_val] * mat_b[k_val][j]
            c[i][j] = sum_val
    return c

def transform_vertex_by_matrix(matrix, vertex):
    x,y,z=vertex
    res_x = matrix[0][0]*x + matrix[0][1]*y + matrix[0][2]*z + matrix[0][3]
    res_y = matrix[1][0]*x + matrix[1][1]*y + matrix[1][2]*z + matrix[1][3]
    res_z = matrix[2][0]*x + matrix[2][1]*y + matrix[2][2]*z + matrix[2][3]
    return (res_x,res_y,res_z)

def invert_affine_matrix(m): # Simplified inverse
    inv = [[0.0]*4 for _ in range(4)]
    inv[0][0],inv[1][0],inv[2][0] = m[0][0],m[0][1],m[0][2]
    inv[0][1],inv[1][1],inv[2][1] = m[1][0],m[1][1],m[1][2]
    inv[0][2],inv[1][2],inv[2][2] = m[2][0],m[2][1],m[2][2]
    tx,ty,tz = m[0][3],m[1][3],m[2][3]
    inv[0][3]=-(inv[0][0]*tx + inv[0][1]*ty + inv[0][2]*tz)
    inv[1][3]=-(inv[1][0]*tx + inv[1][1]*ty + inv[1][2]*tz)
    inv[2][3]=-(inv[2][0]*tx + inv[2][1]*ty + inv[2][2]*tz)
    inv[3][3]=1.0
    return inv

node_world_transforms_cache = {}
def get_world_transform_for_node(node_idx_param, shape_obj, target_anim_info):
    cache_key = (node_idx_param, target_anim_info)
    if cache_key in node_world_transforms_cache: return node_world_transforms_cache[cache_key]
    if node_idx_param < 0 or node_idx_param >= shape_obj.num_nodes: return [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]] 
    current_node = shape_obj.nodes[node_idx_param]
    q_tuple_raw, local_t_data, local_s_data = (0,0,0,32767), (0.0,0.0,0.0), (1.0,1.0,1.0)
    transform_source_is_animated = False
    if target_anim_info:
        target_anim_sequence_idx, use_last_keyframe = target_anim_info
        if 0 <= target_anim_sequence_idx < shape_obj.num_seq:
            for i in range(current_node.num_sub_seq):
                sub_seq_idx_abs = current_node.first_sub_seq + i
                if 0 <= sub_seq_idx_abs < shape_obj.num_sub_seq:
                    sub_seq = shape_obj.sub_sequences[sub_seq_idx_abs]
                    if sub_seq.sequence_idx == target_anim_sequence_idx and sub_seq.num_key_frames > 0:
                        key_frame_relative_idx = sub_seq.num_key_frames - 1 if use_last_keyframe else 0
                        key_frame_idx_abs = sub_seq.first_key_frame + key_frame_relative_idx
                        if 0 <= key_frame_idx_abs < shape_obj.num_keyframes:
                            key_frame = shape_obj.keyframes[key_frame_idx_abs]
                            if 0 <= key_frame.key_value < shape_obj.num_transforms:
                                anim_transform = shape_obj.transforms[key_frame.key_value]
                                q_tuple_raw = (int(anim_transform.rotate.x),int(anim_transform.rotate.y),int(anim_transform.rotate.z),int(anim_transform.rotate.w))
                                local_t_data = anim_transform.translate
                                if hasattr(anim_transform,'scale'):
                                    if isinstance(anim_transform.scale,(list,tuple)) and len(anim_transform.scale)==3: local_s_data = anim_transform.scale
                                    elif anim_transform.scale == 1: local_s_data = (1.0,1.0,1.0)
                                    else: 
                                        try: local_s_data = (float(anim_transform.scale),float(anim_transform.scale),float(anim_transform.scale))
                                        except: local_s_data = (1.0,1.0,1.0) 
                                else: local_s_data = (1.0,1.0,1.0)
                                transform_source_is_animated = True; break 
    if not transform_source_is_animated:
        if 0 <= current_node.transform_index < shape_obj.num_transforms:
            node_transform_data = shape_obj.transforms[current_node.transform_index]
            q_tuple_raw = (int(node_transform_data.rotate.x),int(node_transform_data.rotate.y),int(node_transform_data.rotate.z),int(node_transform_data.rotate.w))
            local_t_data = node_transform_data.translate
            if hasattr(node_transform_data,'scale'):
                if isinstance(node_transform_data.scale,(list,tuple)) and len(node_transform_data.scale)==3: local_s_data = node_transform_data.scale
                elif node_transform_data.scale == 1: local_s_data = (1.0,1.0,1.0)
                else: 
                    try: local_s_data = (float(node_transform_data.scale),float(node_transform_data.scale),float(node_transform_data.scale))
                    except: local_s_data = (1.0,1.0,1.0) 
            else: local_s_data = (1.0,1.0,1.0)
    local_node_matrix = get_matrix_from_quat_trans(q_tuple_raw, local_t_data, local_s_data) 
    if current_node.parent_node == -1 or current_node.parent_node == node_idx_param: final_world_matrix = local_node_matrix
    else:
        parent_world_matrix = get_world_transform_for_node(current_node.parent_node, shape_obj, target_anim_info)
        final_world_matrix = multiply_matrices(parent_world_matrix, local_node_matrix)
    node_world_transforms_cache[cache_key] = final_world_matrix
    return final_world_matrix

# --- Main Exporter Function ---
def main(dts_file_path_str, output_json_dir_str):
    global node_world_transforms_cache
    node_world_transforms_cache = {} 

    dts_file_path = pathlib.Path(dts_file_path_str)
    output_json_dir = pathlib.Path(output_json_dir_str)

    if not dts_file_path.exists():
        # sys.exit(f"DTS file not found at {dts_file_path}") # Old
        raise FileNotFoundError(f"DTS file not found at {dts_file_path}") # New

    # output_json_dir.mkdir(parents=True, exist_ok=True) # app.py will ensure this
    output_json_path = output_json_dir / (dts_file_path.stem + ".json")

    print(f"Attempting to load DTS: {dts_file_path}") # Added for clarity when called from app.py
    shape = dts()
    try:
        shape.load_file(str(dts_file_path))
    except Exception as e:
        # print(f"Error loading DTS file {dts_file_path.name} with dts_module: {e}") # Optional more detail
        raise RuntimeError(f"Error loading DTS file {dts_file_path.name} with dts_module: {e}") from e


    target_anim_for_pose_info = None 
    preferred_sequences_config = [("activation", True),("root", False), ("ambient", False), ("idle", False)]
    if shape.num_seq > 0:
        found_preferred = False; 
        for preferred_name, use_last_kf in preferred_sequences_config:
            for seq_idx, seq_obj in enumerate(shape.sequences):
                if 0 <= seq_obj.name_index < shape.num_names:
                    try:
                        seq_name_bytes = shape.names[seq_obj.name_index]
                        seq_name = seq_name_bytes.split(b'\x00')[0].decode('utf-8', 'ignore').lower().strip()
                        if seq_name == preferred_name:
                            target_anim_for_pose_info = (seq_idx, use_last_kf)
                            print(f"Found preferred sequence '{seq_name}' (idx {seq_idx}, use_last_kf={use_last_kf}) for base pose of {dts_file_path.name}.")
                            found_preferred = True; break 
                    except Exception: pass # Ignore errors decoding a specific name
            if found_preferred: break
        if not found_preferred and shape.num_seq > 0 : 
            target_anim_for_pose_info = (0, False) 
            print(f"No preferred sequence. Using first keyframe of seq 0 for {dts_file_path.name}.")
    else: print(f"No animation sequences in {dts_file_path.name}. Using default node transforms.")

    # Use the Z-up to Y-up root transform (RotX(-90))
    q_root_x = int(-0.70710678118 * 32767.0)
    q_root_w = int( 0.70710678118 * 32767.0)
    root_coord_transform_matrix = get_matrix_from_quat_trans(
        (q_root_x, 0, 0, q_root_w),
        (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    )
    print("Applied root Z-up to Y-up coordinate system transform.")

    inverse_bounds_matrix = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]] 
    if shape.num_nodes > 0 and hasattr(shape.nodes[0], 'transform_index') and \
       0 <= shape.nodes[0].transform_index < shape.num_transforms:
        bounds_node_transform_data = shape.transforms[shape.nodes[0].transform_index]
        bounds_q_raw = (int(bounds_node_transform_data.rotate.x), int(bounds_node_transform_data.rotate.y), int(bounds_node_transform_data.rotate.z), int(bounds_node_transform_data.rotate.w))
        bounds_t = bounds_node_transform_data.translate
        bounds_s_actual = (1.0,1.0,1.0)
        if hasattr(bounds_node_transform_data, 'scale'):
            if isinstance(bounds_node_transform_data.scale, (list,tuple)) and len(bounds_node_transform_data.scale)==3: bounds_s_actual = bounds_node_transform_data.scale
            elif bounds_node_transform_data.scale == 1: bounds_s_actual = (1.0,1.0,1.0)
            else: 
                try: bounds_s_actual = (float(bounds_node_transform_data.scale), float(bounds_node_transform_data.scale), float(bounds_node_transform_data.scale))
                except: pass 
        bounds_matrix = get_matrix_from_quat_trans(bounds_q_raw, bounds_t, bounds_s_actual)
        if bounds_s_actual != (1.0,1.0,1.0): print(f"WARNING: Bounds node for {dts_file_path.name} has non-identity scale {bounds_s_actual}. Simplified 'invert_affine_matrix' might be inaccurate.")
        inverse_bounds_matrix = invert_affine_matrix(bounds_matrix)
        print(f"Applied inverse transform of bounds node for {dts_file_path.name}.")
    else: print(f"INFO: Could not get bounds node transform for {dts_file_path.name}. Using identity for inverse_bounds_matrix.")
    
    selected_lod_nodes = set() 
    if shape.details and shape.num_details > 0:
        target_lod = shape.details[0]
        if shape.num_details > 1:
            for i in range(1, shape.num_details):
                current_detail_size = getattr(shape.details[i], 'size', -1)
                target_lod_size = getattr(target_lod, 'size', -1)
                if current_detail_size > target_lod_size: target_lod = shape.details[i]
        root_node_for_lod = getattr(target_lod, 'root_node', -1)
        if root_node_for_lod != -1 and root_node_for_lod < shape.num_nodes :
            selected_lod_nodes = get_all_descendant_nodes(shape.nodes, root_node_for_lod)
            if not selected_lod_nodes : selected_lod_nodes.add(root_node_for_lod)
        else:
            for i in range(shape.num_nodes): selected_lod_nodes.add(i)
    else:
        for i in range(shape.num_nodes): selected_lod_nodes.add(i)
    
    if not selected_lod_nodes and shape.num_nodes > 0 : # Fallback if LOD selection yields no nodes
        print(f"Warning: LOD selection resulted in no nodes for {dts_file_path.name}. Defaulting to all nodes.")
        for i in range(shape.num_nodes): selected_lod_nodes.add(i)
    elif not selected_lod_nodes and shape.num_nodes == 0: # Error if no nodes at all
        raise ValueError(f"Error: No nodes in shape {dts_file_path.name} and no LOD nodes selected.")


    final_model_vertices = []; final_model_uvs = []; meshes_processed_in_lod = 0
    for obj_i, current_obj in enumerate(shape.objects):
        if current_obj.node_index not in selected_lod_nodes: continue
        if current_obj.mesh_index < 0 or current_obj.mesh_index >= shape.num_meshes: continue
        OBJECT_IS_INITIALLY_INVISIBLE_FLAG = 0x1
        if hasattr(current_obj, 'flags') and (current_obj.flags & OBJECT_IS_INITIALLY_INVISIBLE_FLAG): continue
        mesh_to_process = shape.meshes[current_obj.mesh_index]
        if not (mesh_to_process.faces and mesh_to_process.verts and mesh_to_process.text_verts and \
                mesh_to_process.frames and hasattr(mesh_to_process.frames[0], 'scale') and \
                hasattr(mesh_to_process.frames[0], 'origin')): continue
        meshes_processed_in_lod +=1
        mesh_frame = mesh_to_process.frames[0]
        node_world_transform_model_space = get_world_transform_for_node(current_obj.node_index, shape, target_anim_for_pose_info)
        temp_transform = multiply_matrices(inverse_bounds_matrix, node_world_transform_model_space)
        node_final_world_transform = multiply_matrices(root_coord_transform_matrix, temp_transform)
        obj_offset_matrix = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]] 
        obj_offset_point = None
        if shape.version <= 7:
            if hasattr(current_obj, 'offset_rot') and current_obj.offset_rot and hasattr(current_obj.offset_rot, 'point') and current_obj.offset_rot.point:
                obj_offset_point = current_obj.offset_rot.point
        else: 
            if hasattr(current_obj, 'offset') and isinstance(current_obj.offset, (list, tuple)) and len(current_obj.offset) == 3:
                obj_offset_point = current_obj.offset
        if obj_offset_point:
            obj_offset_matrix[0][3] = obj_offset_point[0]; obj_offset_matrix[1][3] = obj_offset_point[1]; obj_offset_matrix[2][3] = obj_offset_point[2]
        effective_obj_transform = multiply_matrices(node_final_world_transform, obj_offset_matrix)
        transformed_verts_for_mesh = []
        for vert_obj in mesh_to_process.verts:
            if not hasattr(vert_obj, 'get_unpacked_vert'): break
            vertex_in_mesh_frame_space = vert_obj.get_unpacked_vert(mesh_frame.scale, mesh_frame.origin)
            transformed_verts_for_mesh.append(transform_vertex_by_matrix(effective_obj_transform, vertex_in_mesh_frame_space))
        else: 
            for face_data in mesh_to_process.faces:
                indices_valid = True; vert_uv_pairs = [(face_data.vert_index0, face_data.tex_index0), (face_data.vert_index1, face_data.tex_index1), (face_data.vert_index2, face_data.tex_index2)]
                for v_idx, uv_idx in vert_uv_pairs:
                    if not (0 <= v_idx < len(transformed_verts_for_mesh) and 0 <= uv_idx < len(mesh_to_process.text_verts)):
                        indices_valid = False; break
                if not indices_valid: continue
                for v_idx, uv_idx in vert_uv_pairs:
                    final_model_vertices.extend(transformed_verts_for_mesh[v_idx]) # No swizzle
                    final_model_uvs.extend(mesh_to_process.text_verts[uv_idx])

    if meshes_processed_in_lod == 0: 
        print(f"Warning: No meshes processed for LOD in {dts_file_path.name}.")
        # It's possible for a model to have no visible meshes in its highest LOD if it's an effects shape or similar
        # So, don't raise an error here, but the JSON will be empty of vertices.
    
    if not final_model_vertices:
        # sys.exit(f"No vertex data for {dts_file_path.name}") # Old
        # If no meshes were processed, this is expected. If meshes WERE processed but no verts, that's an error.
        if meshes_processed_in_lod > 0:
            raise ValueError(f"No vertex data generated for {dts_file_path.name} despite processing {meshes_processed_in_lod} meshes.")
        else: # No meshes processed, so no vertices is normal. Output an empty JSON.
            print(f"INFO: No visible meshes found for the selected LOD of {dts_file_path.name}. Output JSON will be empty.")


    num_total_final_vertices = len(final_model_vertices) // 3
    final_model_triangles = list(range(num_total_final_vertices))
    with open(output_json_path, "w") as fp:
        json.dump({"v": final_model_vertices, "uv": final_model_uvs, "tri": final_model_triangles}, fp)
    
    if final_model_vertices: # Only print success if there's actual data
        print(f"✓ wrote {output_json_path} (verts={num_total_final_vertices}, tris={len(final_model_triangles)//3}) from {meshes_processed_in_lod} meshes.")
    else:
        print(f"✓ wrote empty JSON to {output_json_path} as no visible mesh data was found/processed.")


# This block allows the script to be run from the command line
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert DTS model file to JSON for web viewing.")
    parser.add_argument("dts_file", help="Path to the input .dts file")
    parser.add_argument("output_dir", help="Directory to save the output .json file")
    args = parser.parse_args()
    
    try:
        main(args.dts_file, args.output_dir)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except ValueError as e: # Catch specific errors raised by main()
        print(e, file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e: # Catch errors from dts_module loading
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred in export_model.py: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)