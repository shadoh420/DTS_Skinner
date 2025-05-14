# tools/export_disc.py

import sys, pathlib, json, math

# 1) Ensure we can import your DTS loader
project_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))
from dts_module import dts # This will require dts_module to be in the path

# 2) Paths: adjust disc_dts if yours lives elsewhere
disc_dts = project_root / "tools" / "disc.dts"
out_json = project_root / "static" / "disc.json"

# 3) Load the .dts
shape = dts()
if not disc_dts.exists():
    sys.exit(f"DTS file not found at {disc_dts}. Please ensure it's there.")
shape.load_file(str(disc_dts))

# --- Helper Functions for LOD and Transforms ---

def get_all_descendant_nodes(shape_nodes, root_node_idx_param):
    nodes_in_lod_set = set()
    if root_node_idx_param < 0 or root_node_idx_param >= len(shape_nodes):
        # print(f"Warning: Invalid root_node_idx {root_node_idx_param} for get_all_descendant_nodes.")
        return nodes_in_lod_set
    
    queue = [root_node_idx_param]
    nodes_in_lod_set.add(root_node_idx_param)
    head_ptr = 0
    while head_ptr < len(queue):
        current_parent_idx = queue[head_ptr]
        head_ptr += 1
        # Iterate through all nodes to find children of current_parent_idx
        for i, node_obj in enumerate(shape_nodes):
            if node_obj.parent_node == current_parent_idx and i != current_parent_idx: # Avoid self-parent loops adding indefinitely
                if i not in nodes_in_lod_set:
                    nodes_in_lod_set.add(i)
                    queue.append(i)
    return nodes_in_lod_set

def get_matrix_from_quat_trans(q_tuple, t_tuple):
    qx, qy, qz, qw = q_tuple
    tx, ty, tz = t_tuple

    nn = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    if nn == 0: qx, qy, qz, qw = 0.0, 0.0, 0.0, 1.0 # Default to identity quaternion
    else: qx /= nn; qy /= nn; qz /= nn; qw /= nn

    x2, y2, z2 = qx+qx, qy+qy, qz+qz
    xx, xy, xz = qx*x2, qx*y2, qx*z2
    yy, yz, zz = qy*y2, qy*z2, qz*z2
    wx, wy, wz = qw*x2, qw*y2, qw*z2

    return [
        [1-(yy+zz), xy-wz,     xz+wy,     tx],
        [xy+wz,     1-(xx+zz), yz-wx,     ty],
        [xz-wy,     yz+wx,     1-(xx+yy), tz],
        [0,         0,         0,         1 ]
    ]

def multiply_matrices(mat_a, mat_b):
    c = [[0,0,0,0] for _ in range(4)]
    for i in range(4):
        for j in range(4):
            for k_val in range(4):
                c[i][j] += mat_a[i][k_val] * mat_b[k_val][j]
    return c

def transform_vertex_by_matrix(matrix, vertex):
    x, y, z = vertex
    # Assume w = 1 for position vectors
    res_x = matrix[0][0]*x + matrix[0][1]*y + matrix[0][2]*z + matrix[0][3]
    res_y = matrix[1][0]*x + matrix[1][1]*y + matrix[1][2]*z + matrix[1][3]
    res_z = matrix[2][0]*x + matrix[2][1]*y + matrix[2][2]*z + matrix[2][3]
    # w_res = matrix[3][0]*x + matrix[3][1]*y + matrix[3][2]*z + matrix[3][3] # If w needed
    return (res_x, res_y, res_z)

node_world_transforms_cache = {}

def get_world_transform_for_node(node_idx_param, shape_obj):
    if node_idx_param in node_world_transforms_cache:
        return node_world_transforms_cache[node_idx_param]

    if node_idx_param < 0 or node_idx_param >= shape_obj.num_nodes:
        # print(f"Warning: Invalid node_idx {node_idx_param} for get_world_transform_for_node. Returning identity.")
        return [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]] # Identity matrix

    current_node = shape_obj.nodes[node_idx_param]
    
    if current_node.transform_index < 0 or current_node.transform_index >= shape_obj.num_transforms:
        # print(f"Warning: Node {node_idx_param} has invalid transform_index {current_node.transform_index}. Using identity local transform.")
        local_q_data = (0,0,0,1)
        local_t_data = (0,0,0)
    else:
        node_transform_data = shape_obj.transforms[current_node.transform_index]
        local_q_data = (node_transform_data.rotate.x, node_transform_data.rotate.y, node_transform_data.rotate.z, node_transform_data.rotate.w)
        local_t_data = node_transform_data.translate
    
    local_node_matrix = get_matrix_from_quat_trans(local_q_data, local_t_data)

    if current_node.parent_node == -1 or current_node.parent_node == node_idx_param: # Is root or self-parented
        final_world_matrix = local_node_matrix
    else:
        parent_world_matrix = get_world_transform_for_node(current_node.parent_node, shape_obj)
        final_world_matrix = multiply_matrices(parent_world_matrix, local_node_matrix)
    
    node_world_transforms_cache[node_idx_param] = final_world_matrix
    return final_world_matrix

# --- LOD Selection ---
selected_lod_nodes = set()
if shape.details and shape.num_details > 0:
    # Assuming details are ordered by importance or pick the one with largest size
    # For simplicity, let's try the first detail level. A more robust way is to find max shape.details[i].size
    # target_lod = shape.details[0] 
    
    # Find detail with largest size (often highest detail)
    target_lod = shape.details[0]
    if shape.num_details > 1:
        for i in range(1, shape.num_details):
            if hasattr(shape.details[i], 'size') and hasattr(target_lod, 'size'): # Check attributes exist
                 if shape.details[i].size > target_lod.size:
                    target_lod = shape.details[i]
            elif hasattr(shape.details[i], 'size'): # if target_lod was missing size, but current has it
                target_lod = shape.details[i]


    print(f"Selected LOD: Detail with size {getattr(target_lod, 'size', 'N/A')}, root_node {getattr(target_lod, 'root_node', 'N/A')}")
    
    root_node_for_lod = getattr(target_lod, 'root_node', -1)
    if root_node_for_lod != -1 and root_node_for_lod < shape.num_nodes :
         selected_lod_nodes = get_all_descendant_nodes(shape.nodes, root_node_for_lod)
         if not selected_lod_nodes : # If root node has no children and is valid, it's the only node.
             selected_lod_nodes.add(root_node_for_lod)
    else:
         print(f"LOD root_node is {root_node_for_lod}. Considering all nodes or this indicates an issue.")
         # Fallback: if LOD selection is problematic, consider all nodes to get some output
         for i in range(shape.num_nodes): selected_lod_nodes.add(i)
else:
    print("No LODs (shape.details) found or num_details is 0. Processing all objects by considering all nodes.")
    for i in range(shape.num_nodes): selected_lod_nodes.add(i)

if not selected_lod_nodes and shape.num_nodes > 0 : # If no nodes selected but nodes exist, default to all
    print("Warning: No nodes selected for LOD. Defaulting to all nodes.")
    for i in range(shape.num_nodes): selected_lod_nodes.add(i)
elif not selected_lod_nodes and shape.num_nodes == 0:
    sys.exit("Error: No nodes in shape and no LOD nodes selected. Cannot proceed.")


# 4) Prepare accumulators
final_model_vertices = []
final_model_uvs = []
meshes_processed_in_lod = 0

# 5) Iterate through objects, selecting those belonging to the chosen LOD
for obj_i, current_obj in enumerate(shape.objects):
    if current_obj.node_index not in selected_lod_nodes:
        continue # Skip object if its node is not in the selected LOD's node set
        
    if current_obj.mesh_index < 0 or current_obj.mesh_index >= shape.num_meshes:
        # print(f"Warning: Object {obj_i} has invalid mesh_index {current_obj.mesh_index}. Skipping.")
        continue
        
    mesh_to_process = shape.meshes[current_obj.mesh_index]
    meshes_processed_in_lod +=1

    # Basic mesh validation
    if not mesh_to_process.faces: continue
    if not mesh_to_process.verts: continue
    if not mesh_to_process.text_verts: continue
    if not mesh_to_process.frames or not hasattr(mesh_to_process.frames[0], 'scale') or not hasattr(mesh_to_process.frames[0], 'origin'):
        continue
    
    mesh_frame = mesh_to_process.frames[0] # Assuming first frame for static model

    obj_world_transform = get_world_transform_for_node(current_obj.node_index, shape)

    # Transform vertices: first by mesh frame, then by object's world transform
    transformed_verts_for_mesh = []
    for vert_obj in mesh_to_process.verts:
        if not hasattr(vert_obj, 'get_unpacked_vert'): break
        # Applies mesh frame's scale and origin
        vertex_in_mesh_space = vert_obj.get_unpacked_vert(mesh_frame.scale, mesh_frame.origin)
        # Then apply the object's full world transform
        transformed_verts_for_mesh.append(transform_vertex_by_matrix(obj_world_transform, vertex_in_mesh_space))
    else: # Only if inner loop (vert_obj in mesh_to_process.verts) completed without break
        # Unroll vertices and UVs for each face
        for face_i, face_data in enumerate(mesh_to_process.faces):
            indices_valid = True
            vert_uv_pairs = [
                (face_data.vert_index0, face_data.tex_index0),
                (face_data.vert_index1, face_data.tex_index1),
                (face_data.vert_index2, face_data.tex_index2)
            ]
            for v_idx, uv_idx in vert_uv_pairs:
                if not (0 <= v_idx < len(transformed_verts_for_mesh) and 0 <= uv_idx < len(mesh_to_process.text_verts)):
                    # print(f"Warning: Face {face_i} in mesh {current_obj.mesh_index} (obj {obj_i}) has out-of-bounds index. Skipping face.")
                    indices_valid = False
                    break
            if not indices_valid: continue

            for v_idx, uv_idx in vert_uv_pairs:
                final_model_vertices.extend(transformed_verts_for_mesh[v_idx])
                final_model_uvs.extend(mesh_to_process.text_verts[uv_idx])

if meshes_processed_in_lod == 0:
    print("Warning: No meshes were processed for the selected LOD. Output JSON might be empty or incorrect.")

# 6) Sanity check
if not final_model_vertices:
    sys.exit(f"No vertex data generated – is {disc_dts} correct, or LOD selection resulted in no meshes?")

# Generate triangle indices: a simple sequence [0, 1, 2, ..., N-1]
num_total_final_vertices = len(final_model_vertices) // 3
final_model_triangles = list(range(num_total_final_vertices))

# 7) Dump merged JSON
out_json.parent.mkdir(parents=True, exist_ok=True)
with open(out_json, "w") as fp:
    json.dump({
      "v":   final_model_vertices,
      "uv":  final_model_uvs,
      "tri": final_model_triangles
    }, fp)

print(f"✓ wrote {out_json}  (verts={num_total_final_vertices}, tris={len(final_model_triangles)//3}) from {meshes_processed_in_lod} meshes in LOD.")

# Dummy dts_module structure for static analysis if the real module isn't present
# In actual execution, the real dts_module must be in Python's path.
# Example:
# if "dts_module" not in sys.modules:
#     print("Creating dummy dts_module for static analysis.")
#     class DummyQuat: x=0; y=0; z=0; w=1
#     class DummyTransform: rotate=DummyQuat(); translate=(0,0,0)
#     class DummyVert: def get_unpacked_vert(self,s,o): return (0,0,0)
#     class DummyFace: vert_index0=0; tex_index0=0; vert_index1=0; tex_index1=0; vert_index2=0; tex_index2=0
#     class DummyFrame: scale=(1,1,1); origin=(0,0,0)
#     class DummyMesh: faces=[]; verts=[]; text_verts=[]; frames=[DummyFrame()]
#     class DummyNode: parent_node=-1; transform_index=0
#     class DummyDetail: size=0; root_node=0
#     class DummyObject: node_index=0; mesh_index=0
#     class dts:
#         meshes=[DummyMesh()]; transforms=[DummyTransform()]; nodes=[DummyNode()]; details=[DummyDetail()]; objects=[DummyObject()]
#         num_meshes=1; num_transforms=1; num_nodes=1; num_details=1; num_objects=1
#         def load_file(self,p): pass
#     # Make dts_module.dts point to this dummy dts class
#     import types
#     dts_module_dummy = types.ModuleType('dts_module')
#     dts_module_dummy.dts = dts
#     sys.modules['dts_module'] = dts_module_dummy
