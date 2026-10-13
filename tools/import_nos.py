"""Import installed Nosgoth models; the header key comes from your own Nosgoth.exe.

python -m tools.import_nos --install PATH --output build/nos-data/nos
Repeat --model Package.Object (quoted wildcards accepted) for a small sample.
"""
import argparse
import json
from pathlib import Path

from tools.import_tas import import_catalog as import_ue3, skin_families as preview_skins
from tools.local_data import LOCAL_DATA
from tools.nos_package import Library
from tools.nos_materials import Materials


def category(row):
    name = row['source'].lower()
    classes = {'asshuman':'Hunter', 'sneakhuman':'Scout', 'bombhuman':'Alchemist',
               'supporthuman':'Prophet', 'shieldhuman':'Vanguard', 'beasthuman':'Beastmaster',
               'fastvampire':'Reaver', 'bigvampire':'Tyrant', 'wingedvampire':'Sentinel',
               'mindvampire':'Deceiver', 'summonvampire':'Summoner', 'rahabvampire':'Drowned'}
    if name.startswith('weapon'):
        kind = 'Weapons'
    elif name.startswith(('ability', 'fx_')):
        kind = 'Abilities and effects meshes'
    elif any(x in name for x in ('ghoul', 'summoned', 'summon_creature', 'bot.', '_pet')):
        kind = 'Bots and summoned creatures'
    elif name.startswith(('human_', 'vampire_', 'exotic_vampires', 'armor_recolor')):
        role = next((v for k,v in classes.items() if k in name), None)
        if role is None:
            role = next((v for v in classes.values() if v.lower() in name), 'Other')
        faction = 'Humans' if name.startswith('human_') or role in list(classes.values())[:6] else 'Vampires'
        kind = faction+' / '+role
    elif 'sky' in name:
        kind = 'Sky'
    else:
        kind = 'Environment props'
    return row['section']+' / '+kind


def skin_families(source, refs, mats, by_root, by_preview):
    # A shared character shader does NOT establish UV compatibility: different
    # armour meshes in one package often have different atlases. Only accept
    # explicit PreviewMesh assignments or descendants of the slot's own MIC.
    result = preview_skins(source, refs, mats, {}, by_preview)
    seen = {tuple(v['refs']) for v in result}
    for slot, material in enumerate(refs):
        if not material:
            continue
        try:
            definition = mats.definition(material)
        except (ValueError, OSError):
            continue
        if definition['cls'] == 'Material':
            continue
        for candidate in by_root.get(definition['root'].lower(), []):
            chain = mats.definition(candidate)['chain'][1:]
            if material.lower() not in [c.lower() for c in chain]:
                continue
            values = list(refs); values[slot] = candidate
            if tuple(values) not in seen:
                seen.add(tuple(values))
                result.append(dict(label=candidate.rsplit('.',1)[-1], refs=values,
                                   assignment=dict(slot=slot, material=candidate, evidence='Descendant of authored mesh-slot instance')))
    for look in mats.library.mesh_looks(source):
        values = list(refs)
        if len(look['materials']) > len(values):
            continue
        for slot, material in enumerate(look['materials']):
            if material:
                values[slot] = material
        if tuple(values) not in seen:
            seen.add(tuple(values))
            result.append(dict(label=look['source'], refs=values, assignment=look))
    return result


def import_catalog(install, output, model_paths=None):
    return import_ue3(install, output, model_paths, game='nos', library_factory=Library,
                      category_fn=category, skins_fn=skin_families, materials_factory=Materials)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA/'nos')
    parser.add_argument('--model', action='append', help='Package-qualified name/wildcard; repeat for samples')
    args = parser.parse_args()
    report = import_catalog(args.install, args.output, args.model)
    print(json.dumps({k:report[k] for k in ('ready','entries','skipped','skipped_reasons','unresolved_materials',
                                          'unresolved_textures','elapsed_seconds','report')}, indent=2))
