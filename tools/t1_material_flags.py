"""Add `material_flags` (what tools/export_model.py now writes) to the shipped Tribes 1 catalog models in static/model_json,
read from an install's shapes; only shapes with unshaded, transparent or translucent slots change. An additive bitmap
(PBMP attribute 16) sets the extra bit 0x10000, as tools/import_t1_map.py does for pack models; a 1.40 PNG install
cannot tell additive bitmaps apart, so use a classic .vol install (TRIBES11).

    python tools/t1_material_flags.py "C:\\Program Files (x86)\\Dynamix\\TRIBES11\\base"
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dts_module.dts import dts as DTS  # noqa: E402
from tools.import_t1_map import Install, bitmap_attributes  # noqa: E402

DRAWN = 0x100 | 0x1000 | 0x2000  # ShadingNone, TextureTransparent, TextureTranslucent.


def main(game_base, catalog=ROOT / 'static' / 'model_json'):
    install = Install(Path(game_base))
    files = {}
    for volume in sorted(install.base.glob('*.vol')):
        for name, reader in install.volume(volume).items():
            files.setdefault(name.lower(), reader)
    changed = []
    for path in sorted(catalog.glob('*.json')):
        reader = files.get(path.stem.lower() + '.dts')
        if not reader:
            continue
        shape = DTS()
        with contextlib.redirect_stdout(io.StringIO()):
            shape.load_binary(reader())
        text = path.read_text(encoding='utf-8')
        model = json.loads(text)
        if 'material_flags' in model or len(shape.material_list) != len(model.get('material_textures', [])):
            continue
        flags = []
        for material in shape.material_list:
            bitmap = files.get(Path(material.map_file).stem.lower() + '.bmp')
            additive = bool(bitmap and bitmap_attributes(bitmap()) & 16)
            flags.append(material.flags & 0xffffffff | (0x10000 if additive else 0))
        if not any(flag & DRAWN for flag in flags):
            continue
        # The key goes in before the closing brace, so the file's own formatting stays as it was.
        path.write_text(text.rstrip()[:-1] + ', "material_flags": %s}' % json.dumps(flags), encoding='utf-8')
        changed.append(path.stem)
    print('Added material_flags to %d catalog models' % len(changed))
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('game_base', help='Tribes 1 base folder with .vol volumes')
    main(parser.parse_args().game_base)
