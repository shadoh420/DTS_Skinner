# tools/export_interior.py

import sys
import pathlib
import json
import math
import argparse
import os
from PIL import Image # For getting texture dimensions

# Add project root to sys.path to find the interior_module
project_root = pathlib.Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

tools_dir = project_root / "tools"
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

try:
    from interior_module import interiorshape
    from interior_module import dml as interior_dml # Alias to avoid conflict if there's another dml
    # BitStream and huffman are used by interiorshape internally
except ImportError as e:
    print(f"CRITICAL ERROR in export_interior.py: Failed to import from 'interior_module': {e}")
    print(f"Ensure 'interior_module' directory is in {tools_dir} and has an __init__.py.")
    raise

# --- Helper Functions ---
def scale_offset_uv(point, scale, offset):
    return (offset[0] + (point[0] * scale[0]),
            offset[1] + (point[1] * scale[1]))

def transform_vertex_by_matrix(matrix, vertex):
    x,y,z=vertex
    res_x = matrix[0][0]*x + matrix[0][1]*y + matrix[0][2]*z + matrix[0][3]
    res_y = matrix[1][0]*x + matrix[1][1]*y + matrix[1][2]*z + matrix[1][3]
    res_z = matrix[2][0]*x + matrix[2][1]*y + matrix[2][2]*z + matrix[2][3]
    return (res_x,res_y,res_z)

def get_matrix_from_rotation_x(angle_degrees):
    rad = math.radians(angle_degrees)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return [
        [1, 0, 0, 0],
        [0, cos_a, -sin_a, 0],
        [0, sin_a, cos_a, 0],
        [0, 0, 0, 1]
    ]

# --- Main Exporter Function ---
def main(dis_file_path_str, output_json_dir_str, interior_source_dir_str, texture_source_dir_str):
    dis_file_path = pathlib.Path(dis_file_path_str)
    output_json_dir = pathlib.Path(output_json_dir_str)
    interior_source_dir = pathlib.Path(interior_source_dir_str)
    texture_source_dir = pathlib.Path(texture_source_dir_str)

    if not dis_file_path.exists():
        raise FileNotFoundError(f"DIS file not found at {dis_file_path}")

    output_json_path = output_json_dir / (dis_file_path.stem + ".json")

    print(f"Attempting to load DIS: {dis_file_path}")
    
    dis_obj = interiorshape.interiorshape()
    dis_obj.load_file(str(dis_file_path))

    if not dis_obj.get_dml_list():
        raise ValueError(f"DIS file {dis_file_path.name} does not reference a DML file.")
    
    dml_name_bytes = dis_obj.get_dml_list()[0]
    dml_name = dml_name_bytes.decode('utf-8', 'ignore')
    dml_file_path = interior_source_dir / dml_name
    
    if not dml_file_path.exists():
        raise FileNotFoundError(f"DML file '{dml_name}' not found at {dml_file_path} (referenced by {dis_file_path.name})")

    print(f"Loading DML: {dml_file_path}")
    dml_obj = interior_dml.dml()
    dml_obj.load_file(str(dml_file_path))

    json_material_textures = []
    texture_dimensions_map = {}

    for mat_idx_enum, mat_entry in enumerate(dml_obj.materials):
        original_bmp_name = mat_entry.name
        slot_identifier = mat_entry.index if hasattr(mat_entry, 'index') else mat_idx_enum

        if not original_bmp_name or not original_bmp_name.strip():
            placeholder_name = f"[Slot {slot_identifier}: No Texture Specified in DML]"
            json_material_textures.append(placeholder_name)
            print(f"Warning: DML material slot (index {slot_identifier}) has no texture name.")
            continue

        base_name, ext = os.path.splitext(original_bmp_name)
        if not base_name and ext: 
            placeholder_name = f"[Slot {slot_identifier}: Invalid Filename '{original_bmp_name}']"
            json_material_textures.append(placeholder_name)
            print(f"Warning: DML material slot {slot_identifier} has invalid texture file '{original_bmp_name}'. Using placeholder: '{placeholder_name}'")
            continue
        
        png_name = base_name + ".png"
        json_material_textures.append(png_name)
        
        texture_path = texture_source_dir / png_name
        if texture_path.exists():
            try:
                with Image.open(texture_path) as img:
                    texture_dimensions_map[png_name] = img.size
            except Exception as e:
                print(f"Warning: Could not load/read dimensions for texture {texture_path}: {e}")
                texture_dimensions_map[png_name] = (256, 256)
        else:
            print(f"Warning: Texture file {png_name} not found at {texture_path} (referenced by DML). Using default dimensions.")
            texture_dimensions_map[png_name] = (256, 256)

    # --- LOD SELECTION LOGIC ---
    selected_dig_filename_bytes = None
    if not dis_obj.lods:
        print(f"Warning: DIS file {dis_file_path.name} has no LOD information. Attempting to use first DIG in list if available.")
        dig_list_from_dis = dis_obj.get_dig_list() # Renamed to avoid conflict
        if dig_list_from_dis:
            selected_dig_filename_bytes = dig_list_from_dis[0]
    else:
        best_lod = None
        max_min_pixels = -1 # For Torque, larger min_pixels means higher detail (shown when object is larger on screen)
        
        # It seems is_lod.geometry_file_offset is an offset into the name_buffer.
        # The name_buffer is already split into dis_obj.name_list by null terminators.
        # The challenge is mapping geometry_file_offset to an index in dis_obj.name_list
        # or directly extracting the string.
        # A simpler approach if the .dis always lists DIGs in order of LOD might be to pick one from get_dig_list().
        # However, using the explicit LOD structure is more robust if available.

        # Let's try to find the DIG filename using the offset from the LOD entry.
        # This assumes geometry_file_offset is a byte offset into the raw name_buffer.
        for lod_entry in dis_obj.lods:
            lod_entry: interiorshape.is_lod
            if lod_entry.min_pixels > max_min_pixels: # Assuming higher min_pixels is better LOD
                max_min_pixels = lod_entry.min_pixels
                
                # Extract filename from name_buffer using the offset
                start_offset = lod_entry.geometry_file_offset
                # Find the null terminator from this start_offset
                try:
                    end_offset = dis_obj.name_buffer.index(b'\x00', start_offset)
                    potential_filename_bytes = dis_obj.name_buffer[start_offset:end_offset]
                    # Check if it's actually a DIG file
                    if potential_filename_bytes.lower().endswith(b'.dig'):
                        best_lod = lod_entry # Store the lod_entry itself
                        selected_dig_filename_bytes = potential_filename_bytes
                except ValueError: # Null terminator not found after offset (should not happen in well-formed file)
                    print(f"Warning: Could not find null terminator for LOD geometry filename at offset {start_offset} in {dis_file_path.name}")
                    continue 
        
        if selected_dig_filename_bytes and best_lod:
            print(f"Selected LOD based on min_pixels={best_lod.min_pixels}, DIG offset={best_lod.geometry_file_offset}, filename: {selected_dig_filename_bytes.decode('utf-8', 'ignore')}")
        else: 
            print(f"Warning: Could not determine best LOD for {dis_file_path.name} from LOD entries. Attempting to use first DIG in list.")
            dig_list_from_dis = dis_obj.get_dig_list()
            if dig_list_from_dis:
                selected_dig_filename_bytes = dig_list_from_dis[0]

    if not selected_dig_filename_bytes:
        # If still no DIG file selected, try the get_dig_list as a final fallback
        dig_list_from_dis = dis_obj.get_dig_list()
        if dig_list_from_dis:
            print(f"Final fallback: Using first DIG from get_dig_list(): {dig_list_from_dis[0].decode('utf-8', 'ignore')}")
            selected_dig_filename_bytes = dig_list_from_dis[0]
        else:
            raise ValueError(f"Could not find any DIG file to process for DIS {dis_file_path.name}.")
    # --- END LOD SELECTION LOGIC ---

    all_vertices_flat = []
    all_uvs_flat = []
    all_indices_flat = []
    material_groups = []
    current_vertex_offset = 0
    root_transform_matrix = get_matrix_from_rotation_x(-90)
    
    dig_name = selected_dig_filename_bytes.decode('utf-8', 'ignore')
    dig_file_path = interior_source_dir / dig_name
    
    if not dig_file_path.exists():
        raise FileNotFoundError(f"Selected DIG file '{dig_name}' not found at {dig_file_path} for DIS {dis_file_path.name}.")
        
    print(f"Processing selected DIG: {dig_file_path}")
    dig_obj = interiorshape.dig()
    dig_obj.load_file(str(dig_file_path))

    for surface in dig_obj.surfaces:
        material_idx = surface.mats
        if material_idx == 255: material_idx = 0 
        
        if not (0 <= material_idx < len(json_material_textures)):
            print(f"Warning: Surface in {dig_name} has invalid material index {material_idx}. Using material 0.")
            material_idx = 0
        
        surface_texture_name = json_material_textures[material_idx]
        is_placeholder_texture = surface_texture_name.startswith("[Slot")
        
        if is_placeholder_texture:
            tex_width, tex_height = (256, 256)
        else:
            tex_width, tex_height = texture_dimensions_map.get(surface_texture_name, (256,256))

        uv_scale_from_dis_loader = (-(surface.tsx + 1.0) / tex_width, 
                                    -(surface.tsy + 1.0) / tex_height)
        uv_offset_from_dis_loader = (-(surface.tox + 1.0) / tex_width, 
                                     -(surface.toy + 1.0) / tex_height)

        group_start_index_ptr = len(all_indices_flat)
        num_triangles_in_surface = 0
        temp_vertices_for_surface = []
        temp_uvs_for_surface = []
        
        surface_vertex_indices_in_dig_verts = list(range(surface.vert_id, surface.vert_id + surface.num_verts))

        for local_idx_in_surface, vert_list_idx in enumerate(surface_vertex_indices_in_dig_verts):
            point_idx, tex_coord_idx = dig_obj.verts[vert_list_idx]
            
            raw_vertex = dig_obj.points3f[point_idx]
            transformed_vertex = transform_vertex_by_matrix(root_transform_matrix, raw_vertex)
            temp_vertices_for_surface.append(transformed_vertex)

            raw_u, raw_v = dig_obj.points2f[tex_coord_idx]
            
            final_u = uv_offset_from_dis_loader[0] + (raw_u * uv_scale_from_dis_loader[0])
            final_v = uv_offset_from_dis_loader[1] + (raw_v * uv_scale_from_dis_loader[1])
            
            temp_uvs_for_surface.append((final_u, final_v))

        if surface.num_verts >= 3:
            for i in range(1, surface.num_verts - 1):
                all_indices_flat.append(current_vertex_offset + 0)
                all_indices_flat.append(current_vertex_offset + i)
                all_indices_flat.append(current_vertex_offset + i + 1)
                num_triangles_in_surface += 1
        
        if num_triangles_in_surface > 0:
            for v_tuple in temp_vertices_for_surface:
                all_vertices_flat.extend(v_tuple)
            for uv_tuple in temp_uvs_for_surface:
                all_uvs_flat.extend(uv_tuple)
            
            material_groups.append({
                "start": group_start_index_ptr,
                "count": num_triangles_in_surface * 3,
                "materialIndex": material_idx
            })
            current_vertex_offset += len(temp_vertices_for_surface)

    if not all_vertices_flat:
        print(f"INFO: No geometry processed for {dis_file_path.name}. Output JSON will be minimal.")
        json_data = {"vertices": [], "uvs": [], "indices": [], "material_textures": json_material_textures, "groups": []}
    else:
        json_data = {
            "vertices": all_vertices_flat,
            "uvs": all_uvs_flat,
            "indices": all_indices_flat,
            "material_textures": json_material_textures,
            "groups": material_groups
        }

    with open(output_json_path, "w") as fp:
        json.dump(json_data, fp)
    
    if all_vertices_flat:
        num_total_verts = len(all_vertices_flat) // 3
        num_total_tris = len(all_indices_flat) // 3
        print(f"SUCCESS: Wrote {output_json_path} (verts={num_total_verts}, tris={num_total_tris}) with {len(material_groups)} material groups.")
    else:
        print(f"INFO: Wrote empty/minimal JSON to {output_json_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert DIS interior file to JSON for web viewing.")
    parser.add_argument("dis_file", help="Path to the input .dis file")
    parser.add_argument("output_dir", help="Directory to save the output .json file")
    parser.add_argument("interior_source_dir", help="Directory containing the .dis, .dml, and .dig files")
    parser.add_argument("texture_source_dir", help="Directory containing the .png texture files")
    
    args = parser.parse_args()
    
    try:
        main(args.dis_file, args.output_dir, args.interior_source_dir, args.texture_source_dir)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred in export_interior.py: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)