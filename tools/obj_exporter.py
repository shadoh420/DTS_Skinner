# tools/obj_exporter.py

"""
OBJ Exporter for DTS Skinner
Converts JSON model data to Wavefront OBJ format with materials and textures.
"""

import json
import pathlib
import zipfile
import math
import tempfile
import shutil
from typing import List, Tuple, Dict, Optional


def compute_smooth_normals(vertices: List[float], indices: List[int]) -> List[Tuple[float, float, float]]:
    """
    Compute smooth vertex normals by averaging adjacent face normals.
    
    Args:
        vertices: Flat list [x,y,z, x,y,z, ...] of vertex positions
        indices: Flat list of triangle indices (groups of 3)
    
    Returns:
        List of normalized normals per vertex [(nx,ny,nz), ...]
    """
    num_vertices = len(vertices) // 3
    
    # Initialize normals accumulator for each vertex
    normals_accum = [[0.0, 0.0, 0.0] for _ in range(num_vertices)]
    
    # Process each triangle
    for i in range(0, len(indices), 3):
        idx0, idx1, idx2 = indices[i], indices[i+1], indices[i+2]
        
        # Get vertex positions
        v0 = (vertices[idx0*3], vertices[idx0*3+1], vertices[idx0*3+2])
        v1 = (vertices[idx1*3], vertices[idx1*3+1], vertices[idx1*3+2])
        v2 = (vertices[idx2*3], vertices[idx2*3+1], vertices[idx2*3+2])
        
        # Compute edges
        edge1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
        edge2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
        
        # Compute face normal via cross product
        normal = (
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0]
        )
        
        # Accumulate to each vertex of the triangle
        for idx in [idx0, idx1, idx2]:
            normals_accum[idx][0] += normal[0]
            normals_accum[idx][1] += normal[1]
            normals_accum[idx][2] += normal[2]
    
    # Normalize accumulated normals
    normals = []
    for accum in normals_accum:
        length = math.sqrt(accum[0]**2 + accum[1]**2 + accum[2]**2)
        if length < 1e-6:
            # Degenerate case: use up vector
            normals.append((0.0, 1.0, 0.0))
        else:
            normals.append((accum[0]/length, accum[1]/length, accum[2]/length))
    
    return normals


def generate_obj_content(
    vertices: List[float],
    uvs: List[float],
    indices: List[int],
    normals: List[Tuple[float, float, float]],
    material_textures: List[str],
    groups: List[Dict],
    model_name: str,
    scale_factor: float = 1.0
) -> str:
    """
    Generate OBJ file content from parsed JSON data.
    
    Args:
        vertices: Flat list of vertex positions [x,y,z, ...]
        uvs: Flat list of UV coordinates [u,v, ...]
        indices: Flat list of triangle indices
        normals: List of computed normals per vertex
        material_textures: List of material texture filenames
        groups: List of material group definitions
        model_name: Base name for the model
        scale_factor: Scale factor to apply (default 1.0 keeps original scale)
    
    Returns:
        String containing OBJ file content
    """
    lines = []
    
    # Header
    lines.append("# Exported from DTS Skinner")
    lines.append(f"# Model: {model_name}")
    lines.append("# Right-handed, Y-up coordinate system")
    lines.append(f"# Scale factor: {scale_factor} applied")
    lines.append(f"mtllib {model_name}.mtl")
    lines.append("")
    
    # Vertices (scale factor applied)
    lines.append(f"# Vertices: {len(vertices) // 3}")
    for i in range(0, len(vertices), 3):
        x, y, z = vertices[i] * scale_factor, vertices[i+1] * scale_factor, vertices[i+2] * scale_factor
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    lines.append("")
    
    # Texture coordinates
    lines.append(f"# Texture coordinates: {len(uvs) // 2}")
    for i in range(0, len(uvs), 2):
        u, v = uvs[i], uvs[i+1]
        # Flip V coordinate for OBJ format (V is inverted compared to most formats)
        v_flipped = 1.0 - v
        lines.append(f"vt {u:.6f} {v_flipped:.6f}")
    lines.append("")
    
    # Normals
    lines.append(f"# Normals: {len(normals)}")
    for nx, ny, nz in normals:
        lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")
    lines.append("")
    
    # Faces grouped by material
    lines.append("# Faces")
    
    if groups and len(groups) > 0:
        # Process material groups
        for group in groups:
            mat_idx = group.get('materialIndex', 0)
            start = group.get('start', 0)
            count = group.get('count', 0)
            
            # Validate material index
            if mat_idx < 0 or mat_idx >= len(material_textures):
                mat_idx = 0
            
            mat_name = f"material_{mat_idx}"
            lines.append(f"usemtl {mat_name}")
            
            # Write faces for this group
            for i in range(start, start + count, 3):
                if i + 2 >= len(indices):
                    break
                
                # OBJ uses 1-based indexing
                idx0, idx1, idx2 = indices[i] + 1, indices[i+1] + 1, indices[i+2] + 1
                
                # Format: f v/vt/vn v/vt/vn v/vt/vn
                # Reverse winding order to fix inside-out faces
                lines.append(f"f {idx0}/{idx0}/{idx0} {idx2}/{idx2}/{idx2} {idx1}/{idx1}/{idx1}")
            
            lines.append("")
    else:
        # No groups defined, treat all as single material
        lines.append("usemtl material_0")
        for i in range(0, len(indices), 3):
            if i + 2 >= len(indices):
                break
            
            idx0, idx1, idx2 = indices[i] + 1, indices[i+1] + 1, indices[i+2] + 1
            # Reverse winding order to fix inside-out faces
            lines.append(f"f {idx0}/{idx0}/{idx0} {idx2}/{idx2}/{idx2} {idx1}/{idx1}/{idx1}")
    
    return '\n'.join(lines)


def generate_mtl_content(material_textures: List[str], model_name: str) -> str:
    """
    Generate MTL (material library) file content.
    
    Args:
        material_textures: List of texture filenames referenced by materials
        model_name: Base name for the model
    
    Returns:
        String containing MTL file content
    """
    lines = []
    
    lines.append(f"# Material library for {model_name}")
    lines.append("# Generated by DTS Skinner")
    lines.append("")
    
    for idx, tex_name in enumerate(material_textures):
        mat_name = f"material_{idx}"
        
        lines.append(f"newmtl {mat_name}")
        lines.append("Ka 1.000 1.000 1.000")  # Ambient color (white)
        lines.append("Kd 1.000 1.000 1.000")  # Diffuse color (white)
        lines.append("Ks 0.000 0.000 0.000")  # Specular color (black)
        lines.append("Ns 0.000")               # Specular exponent
        lines.append("d 1.0")                  # Dissolve (opacity)
        lines.append("illum 1")                # Illumination model (diffuse)
        
        # Only add texture map if it's a valid texture (not a placeholder)
        if tex_name and not tex_name.startswith('[Slot'):
            lines.append(f"map_Kd {tex_name}")
        
        lines.append("")
    
    return '\n'.join(lines)


def generate_readme_content(model_name: str, scale_factor: float) -> str:
    """
    Generate README file with OBJ import instructions.
    
    Args:
        model_name: Base name for the model
        scale_factor: Scale factor applied during export
    
    Returns:
        String containing README content
    """
    return f"""DTS Skinner - OBJ Export
========================================

Model: {model_name}
Export Date: Auto-generated
Scale Factor: {scale_factor} (pre-applied)

Contents:
---------
- {model_name}.obj   (3D geometry)
- {model_name}.mtl   (material definitions)
- *.png             (textures)

Import Instructions:
--------------------
1. Extract all files to your project folder
2. Import the .obj file into your 3D application
3. Materials should auto-link via the .mtl file
4. If textures don't appear, manually assign them to materials

Coordinate System:
------------------
- Right-handed, Y-up coordinate system
- Standard OBJ format
- UV coordinates: Bottom-left origin

Troubleshooting:
----------------
- Model too large/small: Adjust scale in your 3D application
- Textures not loading: Check texture files are in same folder as .obj
- Materials appear grey: Manually assign textures to material slots
- Wrong orientation: May need rotation adjustment depending on target application

Notes:
------
- Original format: Tribes 1 DTS/DIS
- Processed by DTS Skinner
- GitHub: https://github.com/shadoh420/DTS_Skinner

For issues or questions, please open an issue on GitHub.
"""


def json_to_obj_zip(
    json_path: pathlib.Path,
    textures_dir: pathlib.Path,
    output_zip_path: pathlib.Path,
    model_name: str,
    scale_factor: float = 1.0
) -> pathlib.Path:
    """
    Convert JSON model data to OBJ/MTL and bundle with textures in a ZIP archive.
    
    Args:
        json_path: Path to the model JSON file
        textures_dir: Directory containing PNG texture files
        output_zip_path: Path for the output ZIP file
        model_name: Base name for OBJ/MTL files (without extension)
        scale_factor: Scale factor to apply to geometry (default 1.0 keeps original scale)
    
    Returns:
        Path to the created ZIP file
    
    Raises:
        FileNotFoundError: If JSON file doesn't exist
        ValueError: If JSON data is invalid or missing required fields
    """
    # Validate inputs
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")
    
    if not textures_dir.exists():
        print(f"Warning: Textures directory not found: {textures_dir}")
    
    # Load JSON data
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Support both old and new JSON formats
    # Old format: {'v': [...], 'uv': [...], 'tri': [...]}
    # New format: {'vertices': [...], 'uvs': [...], 'indices': [...], 'material_textures': [...], 'groups': [...]}
    
    if 'v' in data:
        # Old format (from disc.json, chaingun.json, etc.)
        vertices = data['v']
        uvs = data['uv']
        indices = data['tri']
        material_textures = []
        groups = []
    elif 'vertices' in data:
        # New format (from export_model.py, export_interior.py)
        vertices = data['vertices']
        uvs = data['uvs']
        indices = data['indices']
        material_textures = data.get('material_textures', [])
        groups = data.get('groups', [])
    else:
        raise ValueError("JSON format not recognized. Expected 'v' or 'vertices' key.")
    
    # Validate we have data
    if not vertices or not uvs or not indices:
        raise ValueError("JSON contains empty geometry data")
    
    if len(vertices) == 0:
        raise ValueError("No vertex data in model")
    
    # Compute normals
    print(f"Computing normals for {len(vertices)//3} vertices...")
    normals = compute_smooth_normals(vertices, indices)
    
    # Generate OBJ content
    print(f"Generating OBJ content (scale factor: {scale_factor})...")
    obj_content = generate_obj_content(
        vertices, uvs, indices, normals,
        material_textures, groups, model_name, scale_factor
    )
    
    # Generate MTL content
    print(f"Generating MTL content for {len(material_textures)} materials...")
    mtl_content = generate_mtl_content(material_textures, model_name)
    
    # Generate README
    readme_content = generate_readme_content(model_name, scale_factor)
    
    # Create temporary directory for staging files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        
        # Write OBJ file
        obj_file = temp_path / f"{model_name}.obj"
        with open(obj_file, 'w') as f:
            f.write(obj_content)
        print(f"Created {obj_file.name}")
        
        # Write MTL file
        mtl_file = temp_path / f"{model_name}.mtl"
        with open(mtl_file, 'w') as f:
            f.write(mtl_content)
        print(f"Created {mtl_file.name}")
        
        # Write README
        readme_file = temp_path / "README.txt"
        with open(readme_file, 'w') as f:
            f.write(readme_content)
        print(f"Created README.txt")
        
        # Copy texture files
        textures_copied = []
        if material_textures:
            for tex_name in material_textures:
                # Skip placeholder materials
                if not tex_name or tex_name.startswith('[Slot'):
                    continue
                
                tex_src = textures_dir / tex_name
                if tex_src.exists():
                    tex_dst = temp_path / tex_name
                    shutil.copy2(tex_src, tex_dst)
                    textures_copied.append(tex_name)
                else:
                    print(f"Warning: Texture not found: {tex_name}")
        
        print(f"Copied {len(textures_copied)} texture(s)")
        
        # Create ZIP archive
        print(f"Creating ZIP archive: {output_zip_path.name}")
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add OBJ file
            zipf.write(obj_file, obj_file.name)
            
            # Add MTL file
            zipf.write(mtl_file, mtl_file.name)
            
            # Add README
            zipf.write(readme_file, readme_file.name)
            
            # Add textures
            for tex_name in textures_copied:
                tex_file = temp_path / tex_name
                zipf.write(tex_file, tex_name)
        
        print(f"Export complete: {output_zip_path}")
    
    return output_zip_path


if __name__ == "__main__":
    # Test/example usage
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python obj_exporter.py <json_file> <textures_dir> <output_zip>")
        print("Example: python obj_exporter.py disc.json ../static/textures disc_export.zip")
        sys.exit(1)
    
    json_file = pathlib.Path(sys.argv[1])
    tex_dir = pathlib.Path(sys.argv[2])
    output_zip = pathlib.Path(sys.argv[3])
    model_name = json_file.stem
    
    try:
        json_to_obj_zip(json_file, tex_dir, output_zip, model_name)
        print(f"\n✓ Success! Export saved to: {output_zip}")
    except Exception as e:
        print(f"\n✗ Export failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
