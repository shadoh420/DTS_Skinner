"""TE-Krogoth's Tribes Vengeance conversion pack, HudBot edition (Tribes 1.40 DTS) into the model browser."""
import json
import pathlib

from PIL import Image

from tools.import_ta import add_model


def import_catalog(pack, output, t1_textures):
    """Every .dts in `pack`/base is a model named after the file; every one in `pack`/armors/<team> is one named
    <team>_<file>. Skins are the HudBot replacements (`pack`/replacements/*.tga), stored as PNGs in output/textures
    (kept when already there, so edits survive): the pack's .bmp files are PBMPs without a palette (the game supplies
    it), which HudBot swaps for these by CRC. Slots without a replacement take the stock Tribes 1 texture of that name when there is one."""
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
    stock = {p.name.lower(): p.name for p in pathlib.Path(t1_textures).glob('*.png')}
    entries, missing = [], set()
    for name, display_name, dts in models:
        entry, lost = add_model(dts, name, display_name, own, stock, output, 'tv', 'TV conversions')
        entries.append(entry); missing |= lost
    (output / 'catalog.json').write_text(json.dumps(entries, indent=1), encoding='utf-8')
    return dict(entries=len(entries), ready=sum(e['status'] == 'ready' for e in entries), missing=sorted(missing))
