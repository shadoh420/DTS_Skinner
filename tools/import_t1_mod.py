"""A Tribes 1 mod's models and skins (the RPG mod, the Star Wars mods, RedMoon RPG) into the model browser as a game."""
import collections
import hashlib
import json
import pathlib
import tempfile
import zipfile

from tools.import_diabotical_models import safe
from tools.import_t1_map import SHARED_PALETTES, bitmap_png
from tools.import_ta import add_model

IMAGES = ('.png', '.bmp')


def import_catalog(pack, output, t1_textures, game, category):
    """Every .dts under `pack` is a model named after the file; a .dts only inside one of its .zip volumes counts too
    (a later volume, in name order, over an earlier one). Two loose models of one name with different contents keep
    the folder's name in front of the later one's. Skins are the mod's .png and .bmp files, loose or in a volume
    holding no interiors (.dis): loose over volume, a shallower folder over a deeper one, a .png over a .bmp of the
    same name; a .bmp is coloured from the game palette it names (SHARED_PALETTES). Those a model's slot names, and the
    other skins of an armor slot (<skin>.<armor>.png for a slot base.<armor>.png), are stored as PNGs in
    output/textures (kept when already there, so edits survive); the mod's intro screens and interior textures are
    not. Other slots take the stock Tribes 1 texture of that name."""
    pack, output = pathlib.Path(pack), pathlib.Path(output)
    if not pack.is_dir():
        raise ValueError(f'No mod folder at {pack}')
    loose = sorted((p for p in pack.rglob('*') if p.is_file()), key=lambda p: (len(p.relative_to(pack).parts), str(p).lower()))
    volumes = sorted((p for p in loose if p.suffix.lower() == '.zip'), key=lambda p: str(p).lower())
    images, zipped = {}, {}  # Skin name (lower case) -> (rank, file name, read); zipped .dts name -> (volume, member).
    for volume in reversed(volumes):  # The later volume wins: its entries go in first.
        try:
            members = zipfile.ZipFile(volume).namelist()
        except (zipfile.BadZipFile, OSError):
            continue
        interiors = any(m.lower().endswith('.dis') for m in members)
        for member in members:
            file_name, suffix = member.rsplit('/', 1)[-1], pathlib.PurePath(member).suffix.lower()
            if suffix == '.dts':
                zipped.setdefault(file_name.lower(), (volume, member))
            elif suffix in IMAGES and not interiors:
                images.setdefault(file_name.lower(), (1, volume, member))
    for path in loose:
        if path.suffix.lower() in IMAGES:
            name = path.name.lower()
            if images.get(name, (1,))[0]:
                images[name] = (0, path, None)
    stems = collections.defaultdict(dict)
    for name, (_, *source) in images.items():
        stems[pathlib.PurePath(name).stem][pathlib.PurePath(name).suffix] = source
    for sub in ('model_json', 'textures', 'dts'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    own, skins = {}, {}
    for stem, found in stems.items():
        suffix = '.png' if '.png' in found else '.bmp'
        source, member = found[suffix]
        own[stem + '.png'] = (source.name if member is None else member.rsplit('/', 1)[-1])[:-4] + '.png'
        skins[stem + '.png'] = suffix, source, member
    stock = {p.name.lower(): p.name for p in pathlib.Path(t1_textures).glob('*.png')}
    entries, missing, seen, used = [], set(), {}, set()
    with tempfile.TemporaryDirectory() as temp:
        models = [(p, p.parent.name) for p in loose if p.suffix.lower() == '.dts']
        names = {p.name.lower() for p, _ in models}
        for file_name, (volume, member) in sorted(zipped.items()):
            if file_name not in names:
                path = pathlib.Path(temp, volume.stem, file_name)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(zipfile.ZipFile(volume).read(member))
                models.append((path, volume.stem))
        for dts, folder in models:
            digest = hashlib.sha256(dts.read_bytes()).digest()
            name = safe(dts.stem)
            if name in seen:
                if seen[name] == digest:
                    continue
                name = safe(f'{folder}_{dts.stem}')
            seen[name] = digest
            try:
                entry, lost = add_model(dts, name, dts.stem, own, stock, output, game, category)
                data = json.loads((output / 'model_json' / f'{name}.json').read_text(encoding='utf-8'))
                used |= {slot.lower() for slot, source in zip(data['material_textures'], data['material_texture_games']) if source == game}
            except Exception as exc:  # A mod's broken or unusual shape is listed, not fatal to the import.
                entry, lost = dict(model_name=name, display_name=dts.stem, texture_name='', game=game, category=category,
                                   status=f'failed: {exc}'), set()
            entries.append(entry); missing |= lost
    armors = {slot.split('.', 1)[1] for slot in used if slot.count('.') > 1}
    unreadable = []
    for name, (suffix, source, member) in sorted(skins.items()):
        stored = own[name]
        if (name in used or name.count('.') > 1 and name.split('.', 1)[1] in armors) and not (output / 'textures' / stored).exists():
            data = source.read_bytes() if member is None else zipfile.ZipFile(source).read(member)
            try:
                (output / 'textures' / stored).write_bytes(data if suffix == '.png' else bitmap_png(data, SHARED_PALETTES))
            except (OSError, ValueError, SyntaxError) as exc:  # PIL raises SyntaxError for a malformed bitmap.
                unreadable.append(f'{stored}: {exc}')
    entries.sort(key=lambda e: e['model_name'])
    (output / 'catalog.json').write_text(json.dumps(entries, indent=1), encoding='utf-8')
    return dict(entries=len(entries), ready=sum(e['status'] == 'ready' for e in entries), missing=sorted(missing),
                unreadable=unreadable)
