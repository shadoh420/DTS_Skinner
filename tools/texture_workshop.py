"""Non-destructive texture transforms, cached color metadata and local user tags."""
import colorsys
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import tempfile
import threading

from PIL import Image


def normalize_transform(value=None):
    if value is None:
        return {'rotation': 0, 'flip_x': False, 'flip_y': False}
    if not isinstance(value, dict) or set(value) - {'rotation', 'flip_x', 'flip_y'}:
        raise ValueError('Invalid texture transform')
    result = dict(rotation=0, flip_x=False, flip_y=False)
    result.update(value)
    if type(result['rotation']) is not int or result['rotation'] not in (0, 90, 180, 270):
        raise ValueError('Rotation must be 0, 90, 180 or 270 degrees')
    if any(type(result[key]) is not bool for key in ('flip_x', 'flip_y')):
        raise ValueError('Texture flips must be booleans')
    return result


def transform_image(image, transform=None):
    """Clockwise quarter turns, then horizontal/vertical flips in image space."""
    transform = normalize_transform(transform)
    image = image.convert('RGBA')
    rotation = transform['rotation']
    if rotation:
        image = image.transpose({90: Image.Transpose.ROTATE_270,
                                 180: Image.Transpose.ROTATE_180,
                                 270: Image.Transpose.ROTATE_90}[rotation])
    if transform['flip_x']:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if transform['flip_y']:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return image


def transformed_name(filename, transform=None):
    transform = normalize_transform(transform)
    suffix = (f"_r{transform['rotation']}" if transform['rotation'] else '')
    suffix += '_flipx' if transform['flip_x'] else ''
    suffix += '_flipy' if transform['flip_y'] else ''
    return Path(filename).stem + (suffix or '_copy') + '.png'


@lru_cache(maxsize=16384)
def _image_metadata(path, fingerprint, ignore_alpha):
    with Image.open(path) as source:
        width, height = source.size
        image = source.convert('RGBA')
        # ponytail: sample at most 4096 pixels; use histograms if perceptual matching is needed.
        image.thumbnail((64, 64), Image.Resampling.NEAREST)
        x = y = weight = 0.0
        for red, green, blue, alpha in image.getdata():
            chroma = (max(red, green, blue) - min(red, green, blue)) / 255
            amount = chroma * (1 if ignore_alpha else alpha / 255)
            hue = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)[0] * math.tau
            x += math.cos(hue) * amount
            y += math.sin(hue) * amount
            weight += amount
        # Achromatic and cancelling hue mixtures have no meaningful mean hue.
        hue = math.degrees(math.atan2(y, x)) % 360 if weight and math.hypot(x, y) / weight > .05 else None
        return {'width': width, 'height': height, 'hue': hue}


def texture_metadata(path, game):
    path = Path(path)
    stat = path.stat()
    return dict(_image_metadata(str(path.resolve()), (stat.st_mtime_ns, stat.st_size, stat.st_ino), game == 't2'))


def validate_tags(tags):
    if not isinstance(tags, list) or len(tags) > 32:
        raise ValueError('Use at most 32 tags per texture')
    result = []
    for tag in tags:
        if not isinstance(tag, str) or not 1 <= len(tag.strip().casefold()) <= 48 or any(ord(c) < 32 or ord(c) == 127 for c in tag):
            raise ValueError('Each tag must contain 1–48 printable characters')
        tag = tag.strip().casefold()
        if tag not in result:
            result.append(tag)
    return result


_tags_lock = threading.Lock()


def read_tags(path):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(game not in ('t1', 't2', 'q3') or not isinstance(entries, dict)
                                          for game, entries in data.items()):
        raise ValueError('Invalid texture tags file; existing data was preserved')
    for entries in data.values():
        for tags in entries.values():
            validate_tags(tags)
    return data


def save_tags(path, game, filename, tags):
    if game not in ('t1', 't2', 'q3'):
        raise ValueError('Unknown texture game')
    tags = validate_tags(tags)
    path = Path(path)
    with _tags_lock:
        data = read_tags(path)
        entries = data.setdefault(game, {})
        if tags:
            entries[filename] = tags
        else:
            entries.pop(filename, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return tags
