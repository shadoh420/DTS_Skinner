"""Starsiege (1999, DarkStar like Tribes 1): an install's shapes and their textures into the model browser as a game."""
import contextlib
import hashlib
import io
import json
import pathlib
import re
import struct
import tempfile

from PIL import Image

from dts_module.dts import dts as Shape
from tools.import_diabotical_models import safe
from tools.import_t1_map import bitmap_png, open_volume, read_palettes
from tools.import_ta import add_model

# Shape palettes (2, 3, 5, 9) are the same in every world's set; the per-world ones (6, 7) come from Temperate by day.
FIRST_PALETTES = ('temperate.d.ppl',)
# The vehicle data scripts (scripts.vol: datherc_*.cs and so on) name the shapes of each kind.
KINDS = {'datherc': 'HERCs', 'dattank': 'Tanks', 'datflyer': 'Flyers', 'datdrone': 'Drones', 'datpilot': 'Pilots'}
SLOT_PLACEHOLDER = re.compile(r'\[Slot (\d+): No Texture Specified\]')


def colour_slots(dts, model, textures):
    """Untextured slots of a flat-colour material (type 2 in the low flag bits) become an 8x8 swatch PNG of its colour,
    written to textures once."""
    shape = Shape()
    with contextlib.redirect_stdout(io.StringIO()):
        shape.load_file(str(dts))
    slots = model['material_textures']
    for index, slot in enumerate(slots):
        match = SLOT_PLACEHOLDER.fullmatch(slot)
        if not match or int(match[1]) >= len(shape.material_list or []):
            continue
        material = shape.material_list[int(match[1])]
        if material.flags & 0xf != 2:
            continue
        rgb = tuple(c & 255 for c in (material.rgb_r, material.rgb_g, material.rgb_b))
        slots[index] = 'rgb_%02x%02x%02x.png' % rgb
        if not (textures / slots[index]).exists():
            Image.new('RGB', (8, 8), rgb).save(textures / slots[index])
    return model


def first_frame(data):
    """A Phoenix bitmap array (PBMA) as its first PBMP frame; any other bitmap unchanged."""
    if data[:4] != b'PBMA':
        return data
    at = data.find(b'PBMP', 8)
    if at < 0:
        raise ValueError('Bitmap array holds no bitmap')
    return data[at:at + 8 + struct.unpack_from('<I', data, at + 4)[0]]


def import_catalog(install, output, _t1_textures=None):
    """Every shape (.dts) in the install's volumes is a model named after the file, in the category its vehicle data
    script gives it (HERCs, Tanks, Flyers, Drones, Pilots), else Editor (the editor's markers) or Objects; a name two
    shapes with different contents share keeps its category in front of the later one's. Flat-colour materials become
    swatch PNGs (rgb_rrggbb.png). The bitmaps
    (.bmp, .pba) a model's slots name are stored as PNGs in output/textures (kept when already there, so edits
    survive), coloured from the install's palette sets."""
    install, output = pathlib.Path(install), pathlib.Path(output)
    volumes = sorted(install.rglob('*.vol'), key=lambda p: str(p).lower()) if install.is_dir() else []
    if not volumes:
        raise ValueError(f'No Starsiege volumes (.vol) in {install}')
    shapes, bitmaps, palette_sets, kinds = [], {}, {}, {}
    for volume in volumes:
        try:
            members = open_volume(volume)
        except ValueError:
            continue
        for name, read in members.items():
            suffix = pathlib.PurePath(name).suffix
            if suffix == '.dts':
                shapes.append((volume, name, read))
            elif suffix in ('.bmp', '.pba'):
                bitmaps.setdefault(pathlib.PurePath(name).stem + '.png', read)
            elif suffix == '.ppl':
                palette_sets[name] = read
            elif suffix == '.cs' and name.split('_', 1)[0] in KINDS:
                for shape in re.findall(r'"([^"]+)\.dts"', read().decode('latin1'), re.IGNORECASE):
                    kinds.setdefault(shape.lower(), KINDS[name.split('_', 1)[0]])
    palettes = {}
    for name in sorted(palette_sets, key=lambda n: (n not in FIRST_PALETTES, n)):
        for number, colours in read_palettes(palette_sets[name]())[0].items():
            palettes.setdefault(number, colours)
    for sub in ('model_json', 'textures', 'dts'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    own = {name: name for name in bitmaps}
    entries, missing, seen, used = [], set(), {}, set()
    with tempfile.TemporaryDirectory() as temp:
        for volume, file_name, read in shapes:
            stem = pathlib.PurePath(file_name).stem
            category = kinds.get(stem, 'Editor' if volume.stem.lower() == 'editor' else 'Objects')
            data = read()
            digest = hashlib.sha256(data).digest()
            name = safe(pathlib.PurePath(file_name).stem)
            if name in seen:
                if seen[name] == digest:
                    continue
                name = safe(f'{category}_{name}')
            seen[name] = digest
            dts = pathlib.Path(temp, category, file_name)
            dts.parent.mkdir(exist_ok=True)
            dts.write_bytes(data)
            try:
                entry, lost = add_model(dts, name, pathlib.PurePath(file_name).stem, own, {}, output, 'ss', category)
                model_json = output / 'model_json' / f'{name}.json'
                model = colour_slots(dts, json.loads(model_json.read_text(encoding='utf-8')), output / 'textures')
                model_json.write_text(json.dumps(model), encoding='utf-8')
                entry['texture_name'] = model['material_textures'][0]
                used |= {slot.lower() for slot in model['material_textures']}
            except Exception as exc:  # An unusual shape is listed, not fatal to the import.
                entry, lost = dict(model_name=name, display_name=pathlib.PurePath(file_name).stem, texture_name='',
                                   game='ss', category=category, status=f'failed: {exc}'), set()
            entries.append(entry); missing |= lost
    unreadable = []
    for name in sorted(used & set(bitmaps)):
        if not (output / 'textures' / name).exists():
            try:
                (output / 'textures' / name).write_bytes(bitmap_png(first_frame(bitmaps[name]()), palettes))
            except (OSError, ValueError, KeyError, struct.error) as exc:
                unreadable.append(f'{name}: {exc}')
    entries.sort(key=lambda e: e['model_name'])
    (output / 'catalog.json').write_text(json.dumps(entries, indent=1), encoding='utf-8')
    return dict(entries=len(entries), ready=sum(e['status'] == 'ready' for e in entries), missing=sorted(missing),
                unreadable=unreadable)
