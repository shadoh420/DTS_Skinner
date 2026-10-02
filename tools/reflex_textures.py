"""Reflex Arena textures for the Reflex Maps page and the texture libraries: the game's .textureset files and its
.dds files, decoded to images with Pillow.

A material names its textures by bare name (textureAlbedoSpec dev_grid16_albedospec, textureDiffuse ivy_leaf_1_c),
and the game finds them by that name, without its folder, among the images of its .textureset files and its .dds
files (environment/veg/ivy/ivy_leaf_1_c.dds).

.textureset file (version 0x20, magic 0xd00f; little-endian): u16 version, u16 magic, its name (128 bytes), u32
image count, u32 0, then a table of 16 images of 156 bytes from offset 140: the image's name (128 bytes,
structural/dev/dev_grid16_albedoSpec), the offsets of up to three copies of it (0xffffffff where there is none),
the mip count, a format number, width and height. The copies are the same picture compressed for different
quality settings (dev_grid16_albedoSpec: BC1 sRGB and BC7 sRGB). Each copy starts with eleven u32 (width, height,
mip count, 1, DXGI format, 1, 0, 1, bytes per 4 x 4 block, 0, 0) and its mips follow, largest first. Read from the
stock structural.pak and thumbs_material.pak (every material's 256 x 256 thumbnail, BC1 sRGB).
"""
import io
import struct

TEXTURESET_MAGIC = b'\x20\x00\x0f\xd0'
NONE = 0xffffffff
# Pillow's DDS reader knows these DXGI formats; the sRGB ones it does not are the same blocks under the plain name.
SRGB_AS_PLAIN = {72: 71, 75: 74, 78: 77, 99: 98}
BLOCK_BYTES = {71: 8, 74: 16, 77: 16, 80: 8, 83: 16, 95: 16, 98: 16}
# Copies are tried best first: BC7, then BC3, BC2, BC1; BC4 to BC6 hold other kinds of data.
PREFERENCE = (98, 77, 74, 71)


def textureset_images(raw):
    """{name: [(DXGI format, width, height, offset of the top mip), ...]} of a .textureset file."""
    if len(raw) < 140 or raw[:4] != TEXTURESET_MAGIC:
        raise ValueError('not a Reflex textureset')
    count = struct.unpack_from('<I', raw, 132)[0]
    if count > 16:
        raise ValueError('textureset holds more images than its table')
    images = {}
    for index in range(count):
        at = 140 + index * 156
        name = raw[at:at + 128].split(b'\0', 1)[0].decode('latin-1')
        copies = []
        for offset in struct.unpack_from('<3I', raw, at + 128):
            if offset == NONE or offset + 44 > len(raw):
                continue
            width, height, _, _, fmt = struct.unpack_from('<5I', raw, offset)
            copies.append((fmt, width, height, offset + 44))
        images[name] = copies
    return images


def _dds(fmt, width, height, data):
    """A DDS file of the top mip of BCn data, with the DX10 header that names its DXGI format."""
    fmt = SRGB_AS_PLAIN.get(fmt, fmt)
    if fmt not in BLOCK_BYTES:
        raise ValueError(f'DXGI format {fmt} is not read')
    size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * BLOCK_BYTES[fmt]
    if len(data) < size:
        raise ValueError('texture data is cut short')
    header = struct.pack('<4sII5I44s', b'DDS ', 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, height, width, size, 0, 1, b'\0' * 44)
    pixel_format = struct.pack('<II4s5I', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    return header + pixel_format + struct.pack('<5I', 0x1000, 0, 0, 0, 0) + struct.pack('<5I', fmt, 3, 0, 1, 0) + data[:size]


def decode_textureset_image(raw, copies):
    """The best copy of a textureset image Pillow can read, as an RGBA image."""
    from PIL import Image
    ranked = sorted(copies, key=lambda copy: PREFERENCE.index(SRGB_AS_PLAIN.get(copy[0], copy[0])) if SRGB_AS_PLAIN.get(copy[0], copy[0]) in PREFERENCE else len(PREFERENCE))
    errors = []
    for fmt, width, height, offset in ranked:
        try:
            with Image.open(io.BytesIO(_dds(fmt, width, height, raw[offset:]))) as image:
                return image.convert('RGBA')
        except (OSError, ValueError, NotImplementedError) as exc:
            errors.append(f'{fmt}: {exc}')
    raise ValueError('no copy of the image could be read (' + '; '.join(errors) + ')')


def decode_dds(raw):
    """A .dds file as an RGBA image."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return image.convert('RGBA')
    except NotImplementedError as exc:
        raise ValueError(str(exc)) from exc


def bake(albedo, meta=None, size=1024, alpha=False):
    """The colour of a surface as the page draws it: the albedo's colour (its alpha, the specular level of an
    albedoSpec texture, left out unless `alpha`: a diffuse texture's alpha is how see-through it is), darkened by the
    meta texture's blue channel where there is one. In the dev grid materials that channel is 1 with 0.6 along the
    grid lines, so it is taken as ambient occlusion; what the game's shader does with it is not known. Scaled down
    to `size` at most."""
    from PIL import Image, ImageChops
    colour = albedo.convert('RGB')
    if meta is not None:
        shade = meta.getchannel('B').resize(colour.size, Image.Resampling.BILINEAR)
        colour = ImageChops.multiply(colour, Image.merge('RGB', (shade, shade, shade)))
    if max(colour.size) > size:
        scale = size / max(colour.size)
        colour = colour.resize((max(1, round(colour.width * scale)), max(1, round(colour.height * scale))), Image.Resampling.LANCZOS)
    if alpha and albedo.getchannel('A').getextrema()[0] < 255:
        colour.putalpha(albedo.getchannel('A').resize(colour.size, Image.Resampling.BILINEAR))
    return colour
