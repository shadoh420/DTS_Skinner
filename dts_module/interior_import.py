from interior_shape_module import interiorshape_blender
import os

texture_directory = "D:/Programming/Other/Python/TribesVolumer/example_files/interiorshapes/textures"
shape_directory = "D:/Programming/Other/Python/TribesVolumer/example_files/interiorshapes"

for subdir, dir, files in os.walk(shape_directory):
    for file_name in files:
        if file_name[-3:] == "dis":
            try:
                interiorshape_blender.load_dis(file_name, subdir, texture_directory)
            except Exception as error:
                print(f"Error {file_name}:", error)
                pass
            
def print(data):
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'CONSOLE':
                override = {'window': window, 'screen': screen, 'area': area}
                bpy.ops.console.scrollback_append(override, text=str(data), type="OUTPUT")