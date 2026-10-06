"""TE-Krogoth's Tribes Vengeance conversion pack, HudBot edition (Tribes 1.40 DTS) into the model browser."""
import json
import pathlib

from PIL import Image

from tools.import_t1_map import bitmap_png
from tools.import_ta import add_model

# Palette 1136 of every Tribes 1.11 world palette set (identical in all 12): it turns the pack's PBMPs into its own
# TGAs to within a few levels, so it decodes the ones with no TGA.
PALETTE = (list((pathlib.Path(__file__).parent / 't1_shape_palette_1136.rgb').read_bytes()), [255] * 256)


def import_catalog(pack, output, t1_textures):
    """Every .dts in `pack`/base is a model named after the file; every one in `pack`/armors/<team> is one named
    <team>_<file>. Skins are the HudBot replacements (`pack`/replacements/*.tga), stored as PNGs in output/textures
    (kept when already there, so edits survive). A `pack`/base .bmp with no replacement (a palettised PBMP) is decoded
    with PALETTE. Other slots take the stock Tribes 1 texture of that name when there is one."""
    pack, output = pathlib.Path(pack), pathlib.Path(output)
    models = sorted((f.stem.lower(), f.stem, f) for f in (pack / 'base').glob('*') if f.suffix.lower() == '.dts') + \
        sorted((f'{f.parent.name}_{f.stem}'.lower(), f'{f.parent.name} {f.stem}', f)
               for f in (pack / 'armors').glob('*/*') if f.suffix.lower() == '.dts')
    if not models:
        raise ValueError(f'No base or armors .dts files in {pack}')
    for sub in ('model_json', 'textures', 'dts'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    own = {}
    for tga in sorted((pack / 'replacements').glob('*.tga')):
        stored = tga.stem + '.png'
        own[stored.lower()] = stored
        if not (output / 'textures' / stored).exists():
            with Image.open(tga) as image:
                image.save(output / 'textures' / stored)
    for bmp in sorted((pack / 'base').glob('*.bmp')):
        stored = bmp.stem + '.png'
        if stored.lower() not in own:
            own[stored.lower()] = stored
            if not (output / 'textures' / stored).exists():
                (output / 'textures' / stored).write_bytes(bitmap_png(bmp.read_bytes(), {0: PALETTE}))
    stock = {p.name.lower(): p.name for p in pathlib.Path(t1_textures).glob('*.png')}
    entries, missing = [], set()
    for name, display_name, dts in models:
        entry, lost = add_model(dts, name, display_name, own, stock, output, 'tv', 'TV conversions')
        entries.append(entry); missing |= lost
    (output / 'catalog.json').write_text(json.dumps(entries, indent=1), encoding='utf-8')
    return dict(entries=len(entries), ready=sum(e['status'] == 'ready' for e in entries), missing=sorted(missing))
