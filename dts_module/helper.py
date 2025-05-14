import struct

def get_int(data, byte_offset_arr):
    res = int.from_bytes(bytes=data[byte_offset_arr[0]:byte_offset_arr[0] + 4], byteorder='little')
    byte_offset_arr[0] += 4
    return res

def get_int16(data, byte_offset_arr):
    [res] = struct.unpack('h', data[byte_offset_arr[0]:byte_offset_arr[0] + 2])
    byte_offset_arr[0] += 2
    return res

def get_int8(data, byte_offset_arr):
    [res] = struct.unpack('B', data[byte_offset_arr[0]:byte_offset_arr[0] + 1])
    byte_offset_arr[0] += 1
    return res

def get_float(data, byte_offset_arr):
    [res] = struct.unpack('f', data[byte_offset_arr[0]:byte_offset_arr[0] + 4])
    byte_offset_arr[0] += 4
    return res

def get_float_array(data, array_size, byte_offset_arr):
    array_res = []
    for i in range(0, array_size):
        [res] = struct.unpack('f', data[byte_offset_arr[0]:byte_offset_arr[0] + 4])
        byte_offset_arr[0] += 4
        array_res.append(res)
    return array_res

def get_float3d(data, byte_offset_arr):
    [x, y, z] = struct.unpack('fff', data[byte_offset_arr[0]:byte_offset_arr[0] + 12])
    byte_offset_arr[0] += 12
    return (x,y,z)

def get_float2d(data, byte_offset_arr):
    [x, y] = struct.unpack('ff', data[byte_offset_arr[0]:byte_offset_arr[0] + 8])
    byte_offset_arr[0] += 8
    return (x,y)

def get_old_int(data, byte_offset):
    return int.from_bytes(bytes=data[byte_offset:byte_offset+4], byteorder='little')

def get_old_int16(data, byte_offset):
    [res] = struct.unpack('h', data[byte_offset:byte_offset+2])
    return res

def get_old_float(data, byte_offset):
    [res] = struct.unpack('f', data[byte_offset:byte_offset+4])
    return res

def get_old_float3d(data, byte_offset):
    [x, y, z] = struct.unpack('fff', data[byte_offset:byte_offset+12])
    return (x,y,z)

