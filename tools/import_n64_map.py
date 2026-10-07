"""Import N64 level conversions made for Blender: one folder per level holding its pieces as .obj files (each
vertex's RGBA colour after its position), one .mtl and the .png textures. Every folder of .obj files under the
chosen folder (or the folder itself) is copied to <output>/maps/<id>/ and listed in <output>/index.json, which the
N64 Maps page (static/n64-maps/viewer.js) reads. Other files (.blend, .rar) are left out."""
import json
from pathlib import Path
import re
import shutil

KEEP = ('.obj', '.mtl', '.png')


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


def import_maps(folder, output, replace=False):
    """Returns the imported, skipped (already imported) and failed maps."""
    folder, output = Path(folder).expanduser(), Path(output)
    if not folder.is_dir():
        raise ValueError('No folder here. Enter the folder you extracted the maps to.')
    sources = [path for path in (folder, *sorted(folder.iterdir())) if path.is_dir() and any(path.glob('*.obj'))]
    if not sources:
        raise ValueError('No map folders (folders of .obj files) here. Enter the folder you extracted the maps to.')
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={})
    for source in sources:
        ident, target = map_id(source.name), output / 'maps' / map_id(source.name)
        if not replace and ident in index and target.is_dir():
            result['skipped'].append(source.name)
            continue
        try:
            files = sorted(path.name for path in source.iterdir() if path.is_file() and path.suffix.lower() in KEEP)
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            for name in files:
                shutil.copyfile(source / name, target / name)
        except OSError as exc:
            result['failed'][source.name] = str(exc)
            index.pop(ident, None)
            continue
        index[ident] = dict(id=ident, name=source.name, objs=[name for name in files if name.lower().endswith('.obj')],
                            mtls=[name for name in files if name.lower().endswith('.mtl')])
        result['imported'].append(source.name)
    output.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(sorted(index.values(), key=lambda item: item['name'].lower()), indent=1), encoding='utf-8')
    return result
