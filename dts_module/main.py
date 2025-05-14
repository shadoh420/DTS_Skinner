# You will have to pip install opencv-python and numpy
import volumer
import terrain
import dml as tribes_dml
import cv2 as cv
import numpy as np
import palette
import dts

if __name__ == '__main__':
    pal = palette.palette_list()
    pal.load_file("example_files\\lush.day.ppl")

    model = dts.dts()
    model.load_file("example_files\\fedmonster.dts")

    model.dump_obj_test(".\\output\\mesh")

    exit(1)

    # Opening as an example
    vol = volumer.volumer()
    vol.load_file("example_files\\rpgmap5.vol")

    # Loading terrain file
    ter = terrain.terrain()
    ter.load_file("example_files\\rpgbutch5.ted")

    # Loading DML, DML's are not complete...haven't bothered figuring out the file structure other than the name to index
    dml = tribes_dml.dml()
    dml.load_file(".\\terrain_files\\lush.dml")
    dml.export_dictionary(".\\output\\mat_indicies.json")
    dml.eliminate_transitions()

    # Save the heightmap, Tribes you need to flip the axis
    height_map = ter.get_heightmap_image()
    cv.imwrite('.\\output\\heightmap.png', height_map)

    raw_hm = ter.get_heightmap_0_65535(4,4)
    cv.imwrite('.\\output\\heightmap_raw.png', raw_hm)
    raw_hm.astype('int16').tofile(".\\output\\heightmap.raw")

    # Each material is 128 x 128, but that means the image would be GB in size for a single portion.  So reducing
    # each material map to an final_mat_size by final_mat_size square.  You can set it to 128 if you want a 2 GB file with compression
    final_mat_size = 2

    # Create a greyscale image of all the material indices
    material_map = ter.get_material_map_image(3,3)
    cv.imwrite('.\\output\\matmap.bmp', material_map)

    # Already flipped
    material_map_flags = ter.get_material_flags_image(3,3)
    cv.imwrite('.\\output\\matmap_flags.bmp', material_map_flags)


    img = ter.render_material_image(dml, final_mat_size)

    # Create a shadow map to apply on image
    shadow_map = ter.get_shadow_mask_image(height_map)
    shadow_map = cv.resize(shadow_map, (ter.size_y * final_mat_size, ter.size_x * final_mat_size),
                           interpolation=cv.INTER_LINEAR)
    shadow_map = np.stack((shadow_map,) * 3, axis=-1)

    # Convert height map to a water mask
    height_map = cv.resize(height_map, (ter.size_y * final_mat_size, ter.size_x * final_mat_size), interpolation=cv.INTER_LINEAR)
    water_image = np.ones((ter.size_y * final_mat_size, ter.size_x * final_mat_size, 3))
    target_water_height = ter.get_height_0_65535(41.4)
    water_mask = (height_map < target_water_height)
    water_image[water_mask] = (0.9, 0.7, 0.7)
    water_image = cv.blur(water_image, (final_mat_size,final_mat_size))

    cv.imwrite('.\\output\\terrain_flat.png', img)
    # Combine the shadow mask and water mask
    img = img * water_image
    img = img * shadow_map

    cv.imwrite('.\\output\\terrain_shadow.png', img)

