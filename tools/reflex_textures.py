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


def decode_dds_cube(raw, size=256):
    """A cube map .dds (DXT1, DXT3 or DXT5; faces +x, -x, +y, -y, +z, -z, each with its mips) as one RGB image of
    its faces stacked top to bottom, each scaled to `size`. Each face's first level is decoded as a .dds of its own."""
    from PIL import Image
    width, mips, block = struct.unpack_from('<I', raw, 16)[0], struct.unpack_from('<I', raw, 28)[0] or 1, {b'DXT1': 8, b'DXT3': 16, b'DXT5': 16}.get(raw[84:88])
    if block is None or struct.unpack_from('<I', raw, 112)[0] & 0xfe00 != 0xfe00:
        raise ValueError('not a DXT cube map')
    level = lambda m: max(1, ((width >> m) + 3) // 4) ** 2 * block
    header = bytearray(raw[:128])
    struct.pack_into('<I', header, 8, struct.unpack_from('<I', raw, 8)[0] & ~0x20000)  # no mip count
    struct.pack_into('<I', header, 28, 1)
    struct.pack_into('<2I', header, 108, 0x1000, 0)  # a plain texture
    strip = Image.new('RGB', (size, size * 6))
    for face in range(6):
        at = 128 + face * sum(level(m) for m in range(mips))
        strip.paste(decode_dds(bytes(header) + raw[at:at + level(0)]).convert('RGB').resize((size, size), Image.Resampling.LANCZOS), (0, face * size))
    return strip


def bake(albedo, meta=None, size=1024, alpha=False):
    """The colour of a surface as the page draws it: the albedo's colour (its alpha, the specular level of an
    albedoSpec texture, left out unless `alpha`: a diffuse texture's alpha is how see-through it is), divided by the
    meta texture's blue channel where there is one. Only the dev grid's meta has that channel below 1 (0.6 along the
    grid lines), and the game draws those lines about 1/0.6 times lighter than the face (in linear light); the dev
    albedo is a flat 0.9, so here they clip at white. Scaled down to `size` at most."""
    import numpy as np
    from PIL import Image
    colour = albedo.convert('RGB')
    if meta is not None:
        shade = np.asarray(meta.getchannel('B').resize(colour.size, Image.Resampling.BILINEAR), dtype=np.float32) / 255
        lit = np.asarray(colour, dtype=np.float32) / np.maximum(shade, 1 / 255)[..., None] ** (1 / 2.2)
        colour = Image.fromarray(np.clip(np.rint(lit), 0, 255).astype(np.uint8), 'RGB')
    if max(colour.size) > size:
        scale = size / max(colour.size)
        colour = colour.resize((max(1, round(colour.width * scale)), max(1, round(colour.height * scale))), Image.Resampling.LANCZOS)
    if alpha and albedo.getchannel('A').getextrema()[0] < 255:
        colour.putalpha(albedo.getchannel('A').resize(colour.size, Image.Resampling.BILINEAR))
    return colour
