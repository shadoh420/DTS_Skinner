"""Read-only Source loose asset/search-path layer, shared by models and maps.

Uses the project's VMT/VTF and DDS readers; Troika archive/MDL code is not used.
"""
from collections import Counter
from pathlib import Path
import re

from .vtmb import Asset, asset_name, asset_reference, read_vmt, material_settings, material_texture
from .import_vtmb import key, texture_key


def game_directory(install):
    root = Path(install).expanduser().resolve()
    game = root if (root/'gameinfo.txt').is_file() else root/'EYE'
    if not (game/'gameinfo.txt').is_file():
        raise ValueError('Expected the EYE install or its gameinfo.txt folder')
    return game


def read_install(install):
    game = game_directory(install)
    text = re.sub(r'//[^\r\n]*', '', (game/'gameinfo.txt').read_text(encoding='utf-8-sig'))
    block = re.search(r'\bSearchPaths"?\s*\{([^{}]*)\}', text, re.I)
    if not block:
        raise ValueError('Missing gameinfo SearchPaths')
    roots = []
    for value in re.findall(r'\bGame"?\s+(?:"([^"]+)"|([^\s{}]+))', block[1], re.I):
        value = next(v for v in value if v)
        path = (game/value[len('|gameinfo_path|'):].lstrip('/\\') if value.lower().startswith('|gameinfo_path|')
                else game.parent/value).resolve()
        if path != game.parent and game.parent not in path.parents:
            raise ValueError('External Source search path requires explicit support: '+value)
        if path not in roots:
            roots.append(path)
    if not roots:
        raise ValueError('No supported Game search paths in gameinfo.txt')
    # Engine/editor defaults are the lowest-priority local fallback.
    if (game.parent/'platform').is_dir():
        roots.append(game.parent/'platform')
    files, collisions = {}, 0
    for root in roots:
        if not root.is_dir():
            raise ValueError('Missing Source search path: '+str(root))
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            name = asset_name(path.relative_to(root).as_posix())
            if name in files:
                collisions += 1
            else:
                files[name] = Asset(path, 0, path.stat().st_size, str(root))
    return files, dict(gameinfo=str(game/'gameinfo.txt'), search_paths=list(map(str, roots)),
                       precedence='first path wins; platform fallback last; no language override requested',
                       files=len(files), shadowed_files=collisions,
                       extensions=dict(Counter(Path(n).suffix for n in files)))


def resolve_material(files, name, directories=(), stack=()):
    name = asset_reference(name).removesuffix('.vmt')
    candidates = [asset_reference('materials/'+d.strip('/\\')+'/'+name) for d in directories if d.strip('/\\')]
    candidates.append(asset_reference(name if name.startswith('materials/') else 'materials/'+name))
    path = next((p+'.vmt' for p in candidates if p+'.vmt' in files), None)
    if path is None:
        raise ValueError('Missing VMT: '+', '.join(p+'.vmt' for p in candidates))
    if path in stack or len(stack) >= 16:
        raise ValueError('Cyclic/deep VMT patch include: '+path)
    vmt = read_vmt(files[path].read())
    if vmt['shader'].lower() == 'patch':
        params = vmt['params']; include = params.get('include')
        if not isinstance(include, str):
            raise ValueError('VMT patch has no include')
        base, _ = resolve_material(files, include, (), (*stack, path))
        vmt = dict(shader=base['shader'], params=dict(base['params']))
        for operation in ('insert', 'replace'):
            changes = params.get(operation, {})
            if not isinstance(changes, dict):
                raise ValueError('VMT patch operation must be a block')
            for k, v in changes.items():
                if (operation == 'insert') != (k in vmt['params']):
                    vmt['params'][k] = v
    return vmt, path


def prepare_texture(files, vmt, path):
    payload, info = material_texture(files, vmt, compressed=True)
    return key(texture_key(vmt, path))+('.dds' if isinstance(payload, bytes) else '.png'), payload, info
