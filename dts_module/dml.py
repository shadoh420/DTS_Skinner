import helper
import json

class dml:
    def __init__(self):
        self.materials = []

    def load_binary(self, data):
        if data[:4] != b"PERS":
            print("Wrong GBLK header")
            return
        curr_data_index = 4

        block_size = helper.get_old_int(data, curr_data_index)
        curr_data_index += 4

        unknown = helper.get_old_int(data, curr_data_index)
        curr_data_index = 54 # A bit of cheating here.  Will work out later what everything is
        # Tribes seems to load the bmp files in order for the index

        index = 0
        max_len = len(data)
        while curr_data_index < max_len:
            self.materials.append(data[curr_data_index:curr_data_index + 64].split(b'\x00')[0].decode("utf-8").lower())
            curr_data_index += 64

        return True

    def load_file(self, file):
        if file[-3:] != 'dml':
            print("Haven't implemented anything other than dml")
            return False

        with open(file, "rb") as file:
            data = file.read()
            return self.load_binary(data)

    def eliminate_transitions(self):
        for item_index in range(0, len(self.materials)):
            r = s = f = n = g = c = h = 0
            for char in self.materials[item_index][1:5]:
                if char == 'r':
                    r += 1
                elif char == 's':
                    s += 1
                elif char == 'f':
                    f += 1
                elif char == 'n':
                    n += 1
                elif char == 'g':
                    g += 1
                elif char == 'c':
                    c += 1
                elif char == 'h':
                    h += 1

            if r >= 4 or s >= 4 or f >= 4 or n >= 4 or g >= 4 or c >= 4:
                continue

            if r >= 3:
                self.materials[item_index] = 'lrrrr.bmp'
            if s >= 3:
                self.materials[item_index] = 'lssss.bmp'
            if f >= 3:
                self.materials[item_index] = 'lffff.bmp'
            if n >= 3:
                self.materials[item_index] = 'lnnnn.bmp'
            if g >= 3:
                self.materials[item_index] = 'lgggg.bmp'
            if c >= 3:
                self.materials[item_index] = 'lcccc.bmp'

            if r >= 2:
                self.materials[item_index] = 'lrrrr.bmp'
            if s >= 2:
                self.materials[item_index] = 'lssss.bmp'
            if f >= 2:
                self.materials[item_index] = 'lffff.bmp'
            if n >= 2:
                self.materials[item_index] = 'lnnnn.bmp'
            if g >= 2:
                self.materials[item_index] = 'lgggg.bmp'
            if c >= 2:
                self.materials[item_index] = 'lcccc.bmp'

            if r >= 1:
                self.materials[item_index] = 'lrrrr.bmp'
            if s >= 1:
                self.materials[item_index] = 'lssss.bmp'
            if f >= 1:
                self.materials[item_index] = 'lffff.bmp'
            if n >= 1:
                self.materials[item_index] = 'lnnnn.bmp'
            if g >= 1:
                self.materials[item_index] = 'lgggg.bmp'
            if c >= 1:
                self.materials[item_index] = 'lcccc.bmp'

        return

    def export_dictionary(self, file):
        mat_dict = {}
        for i in range(0, len(self.materials)):
            mat_dict[i] = self.materials[i]

        json_object = json.dumps(mat_dict, indent=4)

        # Writing to sample.json
        with open(file, "w") as outfile:
            outfile.write(json_object)
