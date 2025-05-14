import helper

class PaletteType:
    NoRemapPaletteType = 0
    ShadeHazePaletteType = 1
    TranslucentPaletteType = 2

    ColorQuantPaletteType = 3
    AlphaQuantPaletteType = 4

    AdditiveQuantPaletteType = 5
    AdditivePaletteType = 6

    SubtractiveQuantPaletteType = 7
    SubtractivePaletteType = 8


class palette_list:
    def __init__(self):
        self.num_palettes = 0
        self.shade_shift = 0
        self.haze_levels = 0
        self.haze_color = 0
        self.allowedColorMatches = []
        self.pal = [palette()] * 16

    def load_binary(self, data):
        if data[:4] != b'PL98':
            print("Invalid ppl file")
            return

        curr_index = 4
        self.num_palettes = helper.get_old_int(data, curr_index)
        curr_index += 4
        self.shade_shift = helper.get_old_int(data, curr_index)
        curr_index += 4
        self.haze_levels = helper.get_old_int(data, curr_index)
        curr_index += 4
        self.haze_color = helper.get_old_int(data, curr_index)
        curr_index += 4

        # Don't know what this is for
        for i in range(0, 8):
            self.allowedColorMatches.append(helper.get_old_int(data, curr_index))
            curr_index += 4

        shade_level = 1 << self.shade_shift
        remap_table_size = 0

        for pal_index in range(0, self.num_palettes):
            curr_index = self.pal[pal_index].load_binary(data, curr_index)

            self.pal[pal_index].shade_map = 0
            self.pal[pal_index].haze_map = 0
            self.pal[pal_index].trans_map = 0

            if self.pal[pal_index].pal_type == PaletteType.ShadeHazePaletteType:
                remap_table_size += 256 * shade_level * (self.haze_levels + 1)
                remap_table_size += 256
                remap_table_size += 4 * 4 * 256
            elif (self.pal[pal_index].pal_type == PaletteType.TranslucentPaletteType or
                    self.pal[pal_index].pal_type == PaletteType.AdditivePaletteType or
                    self.pal[pal_index].pal_type == PaletteType.SubtractivePaletteType):
                remap_table_size += 65536
                remap_table_size += 256
                remap_table_size += 4 * 4 * 256
            elif self.pal[pal_index].pal_type == PaletteType.NoRemapPaletteType:
                remap_table_size += 256
                remap_table_size += 4 * 4 * 256

        if remap_table_size > 0:
            todo = "Need to implement the rest of this...it's mainly stuff that software rendering uses so it might" \
                   "be handy later on"
        # self.print_stats()

    def load_file(self, file):
        with open(file, "rb") as file:
            data = file.read()
            return self.load_binary(data)

    def print_stats(self):
        print(f"Num Palettes: {self.num_palettes}")
        print(f"Shade Shift: {self.shade_shift}")
        print(f"Haze Levels: {self.haze_levels}")
        print(f"Haze Color: {self.haze_color}")
        print(f"Allowed Color Matches: {self.allowedColorMatches}")

class palette:
    def __init__(self):
        self.color = [0, 0, 0, 0]*256
        self.pal_index = 0
        self.pal_type = 0
        self.haze_map = 0
        self.shade_map = 0
        self.trans_map = 0

    def load_binary(self, data, curr_index):
        for i in range(0, 256):
            self.color[i] = [data[curr_index], data[curr_index+1], data[curr_index+2], data[curr_index+3]]
            curr_index += 4

        curr_index += 4
        self.pal_index = helper.get_old_int(data, curr_index)
        curr_index += 4
        self.pal_type = helper.get_old_int(data, curr_index)
        curr_index += 4
        return curr_index
