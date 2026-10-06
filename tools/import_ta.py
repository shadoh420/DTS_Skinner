"""TE-Krogoth's Tribes Ascend conversion pack (Tribes 1.40 DTS models, one folder each) into the model browser."""
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import tempfile

from tools.export_model import main as export_dts
from tools.import_diabotical_models import safe


def import_catalog(pack, output, t1_textures):
    """Every folder of `pack` holding a .dts becomes a model named after the folder. Its PNGs go into output/textures
    (kept when already there, so edits survive); a PNG name two folders give different pictures is prefixed with the
    model name. Slots the pack has no PNG for take the stock Tribes 1 texture of that name when there is one."""
    pack, output = pathlib.Path(pack), pathlib.Path(output)
    folders = sorted(p for p in pack.iterdir() if p.is_dir() and any(f.suffix.lower() == '.dts' for f in p.iterdir())) \
        if pack.is_dir() else []
    if not folders:
        raise ValueError(f'No model folders with a .dts file in {pack}')
    pictures = {}
    for folder in folders:
        for png in folder.glob('*.png'):
            pictures.setdefault(png.name.lower(), set()).add(hashlib.sha256(png.read_bytes()).digest())
    stock = {p.name.lower(): p.name for p in pathlib.Path(t1_textures).glob('*.png')}
    for sub in ('model_json', 'textures', 'dts'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    entries, missing = [], set()
    for folder in folders:
        name = safe(folder.name)
        dts = sorted(f for f in folder.iterdir() if f.suffix.lower() == '.dts')[0]
        own = {}
        for png in folder.glob('*.png'):
            stored = f'{name}.{png.name}' if len(pictures[png.name.lower()]) > 1 else png.name
            own[png.name.lower()] = stored
            if not (output / 'textures' / stored).exists():
                shutil.copyfile(png, output / 'textures' / stored)
        # The exporter keys player-armor handling and its output name off the DTS file's own name.
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            export_dts(dts, temp)
            data = json.loads((pathlib.Path(temp) / (dts.stem + '.json')).read_text(encoding='utf-8'))
        slots, games = [], []
        for slot in data['material_textures'] or [f'{dts.stem}.png']:
            if slot.lower() in own:
                slots.append(own[slot.lower()]); games.append('ta')
            elif slot.lower() in stock:
                slots.append(stock[slot.lower()]); games.append('t1')
            else:
                slots.append(slot); games.append('ta')
                if not slot.startswith('['):
                    missing.add(slot)
        data.update(game='ta', material_textures=slots, material_texture_games=games, source=f'{folder.name}/{dts.name}')
        (output / 'model_json' / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')
        (output / 'dts' / name).mkdir(exist_ok=True)
        shutil.copyfile(dts, output / 'dts' / name / dts.name)
        entries.append(dict(model_name=name, display_name=folder.name, texture_name=slots[0], game='ta',
                            category='TA conversions', status='ready' if data['vertices'] else 'no visible geometry'))
    (output / 'catalog.json').write_text(json.dumps(entries, indent=1), encoding='utf-8')
    return dict(entries=len(entries), ready=sum(e['status'] == 'ready' for e in entries), missing=sorted(missing))


def dts_source(output, name):
    """The DTS a model was imported from, for animation export."""
    return next((pathlib.Path(output) / 'dts' / name).iterdir())
