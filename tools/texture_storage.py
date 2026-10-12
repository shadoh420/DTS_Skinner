"""Lossless DXT containers and non-destructive shared texture output."""
from pathlib import Path
import io
import struct

from PIL import Image


def open_texture(path):
    """Pillow preview/export adapter for RXGB; the stored DDS is never modified."""
    if Path(path).suffix.lower() == '.dds':
        with open(path, 'rb') as stream:
            header = stream.read(128)
            if len(header) == 128 and header[:4] == b'DDS ' and header[84:88] == b'RXGB':
                payload = header[:84] + b'DXT5' + header[88:] + stream.read()
                with Image.open(io.BytesIO(payload)) as source:
                    _, green, blue, x = source.convert('RGBA').split()
                    return Image.merge('RGBA', (x, green, blue, Image.new('L', source.size, 255)))
    return Image.open(path)


def game_texture_dir(data_root, game):
    return Path(data_root) / game / 'textures'


def dds_bytes(width, height, fourcc, mipmaps):
    """Wrap existing top-to-bottom DXT blocks, largest mip first; no re-encoding."""
    if fourcc not in ('DXT1', 'DXT3', 'DXT5') or not mipmaps:
        raise ValueError('Expected DXT mip chain')
    if min(width, height) < 1 or len(mipmaps) > max(width, height).bit_length():
        raise ValueError('Invalid DDS dimensions/mips')
    for level, payload in enumerate(mipmaps):
        w, h = max(1, width >> level), max(1, height >> level)
        expected = ((w+3)//4)*((h+3)//4)*(8 if fourcc == 'DXT1' else 16)
        if len(payload) != expected:
            raise ValueError('Invalid DXT mip size')
    header = bytearray(128); header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x81007 | (0x20000 if len(mipmaps)>1 else 0),
                     height, width, len(mipmaps[0]), 0, len(mipmaps))
    struct.pack_into('<II4s', header, 76, 32, 4, fourcc.encode('ascii'))
    struct.pack_into('<I', header, 108, 0x1000 | (0x400008 if len(mipmaps)>1 else 0))
    return bytes(header) + b''.join(mipmaps)


def write_texture(directory, filename, payload):
    """Imports never delete or overwrite the shared library or user PNG edits."""
    target = Path(directory) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            payload.save(target)
