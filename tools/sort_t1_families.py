"""Sort the T1 catalog into families the way the game's own files group them, as the user's families.

python tools/sort_t1_families.py [--game-base C:/realstocktribe/base] [--dry-run] [--bundle]

--bundle writes static/t1-families.json, the families the app ships for T1 (laid over the catalog as its imported
families); without it the result goes into the user's own families.

Rules, first match wins:
  1. a name with "drop" in it is a Dropship (they are interiors, so this beats rule 3);
  2. a shape a datablock in scripts/server/items names goes in that script's folder (armors -> Armors, misc -> Misc,
     ...); a script directly in items/ names the family after itself (moveable.cs -> Moveables). A shapeFile
     reference beats a secondary one (projectile, shield, explosion); a shape still claimed by several folders
     takes the first in FOLDER_ORDER and is listed in the report;
  3. a .dis in the install's volumes is an InteriorShape, the mission files' word for it.
Custom map buildings keep their own family; models the user already moved are left alone. Everything else stays
in the import's family, listed in the report. Sources are read-only; only local-data/model-families.json is written.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.import_t1_map import Install
from tools.local_data import LOCAL_DATA
from tools.model_data import read_families, save_families, t1_building_names, t1_catalog_names

ROOT = Path(__file__).resolve().parents[1]
FOLDER_ORDER = ('armors', 'weapons', 'vehicles', 'turrets', 'stations', 'misc', 'sensors', 'packs')
TOP_LEVEL = {'debris': 'Debris', 'marker': 'Markers', 'moveable': 'Moveables', 'staticshape': 'StaticShapes',
             'shockwave': 'Shockwaves', 'item': 'Items'}


def script_families(items_dir, names):
    """Catalog name -> (family, every family the scripts put it in), from the datablocks under items_dir."""
    lower = {name.lower(): name for name in names}
    claims = defaultdict(lambda: ([], []))  # name -> (shapeFile folders, other-field folders)
    for path in sorted(Path(items_dir).rglob('*.cs')):
        folder = path.parent.name.lower() if path.parent != Path(items_dir) else path.stem.lower()
        for block in re.finditer(r'^[ \t]*\w+Data[ \t]+\w+\s*\{(.*?)\};', path.read_text(encoding='cp1252', errors='replace'), re.S | re.M):
            for field, value in re.findall(r'(\w+)\s*=\s*"?([\w.]+?)(?:\.dts)?"?\s*;', block[1]):
                if value.lower() in lower:
                    claims[lower[value.lower()]][field.lower() != 'shapefile'].append(folder)
    order = lambda folder: (FOLDER_ORDER.index(folder) if folder in FOLDER_ORDER else len(FOLDER_ORDER), folder)
    family = lambda folder: TOP_LEVEL.get(folder, folder.capitalize())
    return {name: (family(min(primary or secondary, key=order)), sorted({family(f) for f in primary or secondary}))
            for name, (primary, secondary) in claims.items()}


def sort_families(names, items_dir, interiors, customs=()):
    """Catalog name -> family for every model a rule places, and name -> candidate families for shared shapes."""
    scripted = script_families(items_dir, names)
    families, shared = {}, {}
    for name in names:
        if name in customs:
            continue
        if 'drop' in name.lower():
            families[name] = 'Dropships'
        elif name in scripted:
            families[name], candidates = scripted[name]
            if len(candidates) > 1:
                shared[name] = candidates
        elif name.lower() in interiors:
            families[name] = 'InteriorShapes'
    return families, shared


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--game-base', default=r'C:\realstocktribe\base', help='Tribes base folder with loose scripts/server/items')
    parser.add_argument('--dry-run', action='store_true', help='print the report without saving families')
    parser.add_argument('--bundle', action='store_true', help='write the shipped defaults (static/t1-families.json) instead of your families')
    args = parser.parse_args()
    install = Install(args.game_base)
    items = install.base / 'scripts/server/items'
    if not items.is_dir():
        raise SystemExit(f'No loose item scripts in {items}')
    interiors = {Path(name).name.split('.')[0] for name in install.everything() if name.lower().endswith('.dis')}
    model_dir = ROOT / 'static/model_json'
    names, customs = t1_catalog_names(model_dir), t1_building_names(model_dir)
    families, shared = sort_families(names, items, interiors, customs)
    if args.bundle:
        # The app lays these over T1's catalog as its imported families; users' own families still win.
        bundle = model_dir.parent / 't1-families.json'
        bundle.write_text(json.dumps(dict(sorted(families.items(), key=lambda item: item[0].lower())), indent=1) + '\n', encoding='utf-8')
        print(f'{len(families)} models in {len(set(families.values()))} families written to {bundle}')
        return
    family_file = LOCAL_DATA / 'model-families.json'
    mine = read_families(family_file).get('t1', {})
    kept = sorted(set(families) & set(mine), key=str.lower)
    by_family = defaultdict(list)
    for name, family in families.items():
        if name not in mine:
            by_family[family].append(name)
    for family, models in sorted(by_family.items()):
        print(f'{family}: {len(models)}')
        if not args.dry_run:
            save_families(family_file, 't1', models, family)
    print(f'\nShared shapes (placed in the first family): ' + '; '.join(f'{n} -> {families[n]} of {", ".join(c)}' for n, c in sorted(shared.items())))
    print(f'Already in your own families, left alone ({len(kept)}): {" ".join(kept)}')
    print(f'Custom map buildings, left in their family: {len(customs)}')
    rest = [name for name in names if name not in families and name not in customs and name not in mine]
    print(f'Not placed by any rule ({len(rest)}): {" ".join(rest)}')
    print('\n(dry run: nothing saved)' if args.dry_run else f'\nSaved to {family_file}')


if __name__ == '__main__':
    main()
