# tools/inspect_dts.py

import sys, pathlib, argparse
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))  # project root
from dts_module import dts

def get_transform_details_str(transform_idx, shape_obj):
    if 0 <= transform_idx < shape_obj.num_transforms:
        t = shape_obj.transforms[transform_idx]
        q_raw = (int(t.rotate.x), int(t.rotate.y), int(t.rotate.z), int(t.rotate.w))
        
        # Decode Quat16 for display
        qx_f = float(q_raw[0]) / 32767.0
        qy_f = float(q_raw[1]) / 32767.0
        qz_f = float(q_raw[2]) / 32767.0
        qw_f = float(q_raw[3]) / 32767.0
        
        # Normalize for display (optional, but gives a sense of the rotation)
        nn = math.sqrt(qx_f*qx_f + qy_f*qy_f + qz_f*qz_f + qw_f*qw_f)
        if nn > 1e-6: qx_f/=nn; qy_f/=nn; qz_f/=nn; qw_f/=nn
        else: qx_f,qy_f,qz_f,qw_f = 0,0,0,1 # Identity if zero length

        q_disp = f"Q({qx_f:.2f},{qy_f:.2f},{qz_f:.2f},{qw_f:.2f})"
        pos_disp = f"T({t.translate[0]:.3f},{t.translate[1]:.3f},{t.translate[2]:.3f})"
        
        s_disp = "S(1,1,1)"
        if hasattr(t, 'scale'):
            if isinstance(t.scale, (list,tuple)) and len(t.scale)==3:
                s_disp = f"S({t.scale[0]:.2f},{t.scale[1]:.2f},{t.scale[2]:.2f})"
            elif t.scale != 1:
                 s_disp = f"S({float(t.scale):.2f})"
        return f"{q_disp} {pos_disp} {s_disp}"
    return "Invalid Transform Index"

def inspect_dts_file(dts_file_path_str):
    dts_path = pathlib.Path(dts_file_path_str)
    if not dts_path.exists():
        print(f"Error: DTS file not found at {dts_path}")
        return

    print(f"Inspecting DTS file: {dts_path.name}\n" + "="*30)

    shape = dts()
    try:
        shape.load_file(str(dts_path))
    except Exception as e:
        print(f"Error loading DTS file {dts_path.name}: {e}")
        return

    print(f"\n--- General Info ---")
    print(f"  Version: {getattr(shape, 'version', 'N/A')}")
    print(f"  Num Nodes: {shape.num_nodes}, Seqs: {shape.num_seq}, SubSeqs: {shape.num_sub_seq}, Keyframes: {shape.num_keyframes}")
    print(f"  Num Transforms: {shape.num_transforms}, Names: {shape.num_names}, Objects: {shape.num_objects}")
    print(f"  Num Details: {shape.num_details}, Meshes: {shape.num_meshes}")

    # Find 'root' sequence index (assuming it's sequence 0 if not found by name)
    root_sequence_idx = 0 # Default to 0
    found_root_by_name = False
    if hasattr(shape, 'sequences') and shape.sequences and hasattr(shape, 'names') and shape.names:
        for i, seq in enumerate(shape.sequences):
            if 0 <= seq.name_index < shape.num_names:
                try:
                    seq_name_bytes = shape.names[seq.name_index]
                    seq_name = seq_name_bytes.split(b'\x00')[0].decode('utf-8', 'ignore').lower().strip()
                    if seq_name == "root":
                        root_sequence_idx = i
                        found_root_by_name = True
                        print(f"  Identified 'root' sequence at index: {root_sequence_idx}")
                        break
                except: pass
        if not found_root_by_name and shape.num_seq > 0:
             print(f"  No sequence named 'root' found. Will use Sequence 0 for 'root' pose check.")
        elif not hasattr(shape, 'sequences') or not shape.sequences:
            print("  No sequences found in model.")
            root_sequence_idx = -1 # Indicate no sequences


    if hasattr(shape, 'nodes') and shape.nodes and hasattr(shape, 'names') and shape.names:
        print(f"\n--- Nodes ({shape.num_nodes} total) ---")
        # Create a mapping of node index to its name for easier parent lookup
        node_idx_to_name_map = {}
        for i, node in enumerate(shape.nodes):
            node_name = f"UnnamedNode{i}"
            if 0 <= node.name_index < shape.num_names:
                try:
                    node_name_bytes = shape.names[node.name_index]
                    node_name = node_name_bytes.split(b'\x00')[0].decode('utf-8', 'ignore').strip()
                except: pass
            node_idx_to_name_map[i] = node_name
        
        for i, node in enumerate(shape.nodes):
            node_name = node_idx_to_name_map.get(i, f"UnnamedNode{i}")
            parent_name = "N/A (Root)"
            if node.parent_node != -1:
                parent_name = node_idx_to_name_map.get(node.parent_node, f"UnknownParentIdx{node.parent_node}")
            
            print(f"\n  Node[{i:3d}]: '{node_name}' (NameIdx: {node.name_index})")
            print(f"    Parent: Node[{node.parent_node}] ('{parent_name}')")
            print(f"    Default Transform Idx: {node.transform_index} -> {get_transform_details_str(node.transform_index, shape)}")
            
            has_root_anim = False
            if root_sequence_idx != -1: # Only check if sequences exist
                for ss_offset in range(node.num_sub_seq):
                    sub_seq_idx_abs = node.first_sub_seq + ss_offset
                    if 0 <= sub_seq_idx_abs < shape.num_sub_seq:
                        sub_seq = shape.sub_sequences[sub_seq_idx_abs]
                        if sub_seq.sequence_idx == root_sequence_idx:
                            has_root_anim = True
                            if sub_seq.num_key_frames > 0:
                                # We care about the first keyframe for the 'root' pose
                                key_frame_idx_abs = sub_seq.first_key_frame 
                                if 0 <= key_frame_idx_abs < shape.num_keyframes:
                                    key_frame = shape.keyframes[key_frame_idx_abs]
                                    anim_transform_idx = key_frame.key_value
                                    print(f"    'root' Seq (idx {root_sequence_idx}) Keyframe[0] Transform Idx: {anim_transform_idx} -> {get_transform_details_str(anim_transform_idx, shape)}")
                                else:
                                    print(f"    'root' Seq SubSeq has invalid first_key_frame index: {key_frame_idx_abs}")
                            else:
                                print(f"    'root' Seq SubSeq has 0 keyframes.")
                            break # Found the relevant subsequence for this node and root animation
            if not has_root_anim and root_sequence_idx != -1:
                print(f"    No animation track in 'root' sequence (idx {root_sequence_idx}). Uses Default Transform for this pose.")
            elif root_sequence_idx == -1:
                 print(f"    No sequences in model. Uses Default Transform.")

if __name__ == "__main__":
    # Need to import math for get_transform_details_str if it's not already
    import math 
    parser = argparse.ArgumentParser(description="Inspect a DTS model file, focusing on skeletal hierarchy for 'root' pose.")
    parser.add_argument("dts_file", help="Path to the .dts file to inspect (e.g., tools/dts_files/larmor.dts)")
    args = parser.parse_args()

    inspect_dts_file(args.dts_file)