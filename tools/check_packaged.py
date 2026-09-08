"""Exercise a running --no-browser --no-tray build using only the standard library.

Usage: python tools/check_packaged.py http://127.0.0.1:5068
The server must be the packaged candidate. This check does not start/stop apps.
"""
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, quote
from urllib.request import urlopen
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(base):
    root = Path(__file__).resolve().parents[1]
    results = {}
    def get(path):
        with urlopen(base + path, timeout=90) as response:
            return response.read()
    for game, inventory in [('t1', 'model_catalog.txt'), ('t2', 't2_catalog.txt'), ('q3', None)]:
        catalog = json.loads(get('/list_models?' + urlencode({'game': game})))
        if inventory:
            expected = (root/inventory).read_text(encoding='utf-8').splitlines()
        else:
            from tools.import_q3 import current_import
            expected = [entry['model_name'] for entry in json.loads((current_import(root/'local-data/q3')/'catalog.json').read_text(encoding='utf-8'))]
        assert [m['model_name'] for m in catalog] == expected, (game, 'catalog mismatch')
        ready = [m for m in catalog if m.get('status', 'ready') == 'ready']
        missing = {}
        for entry in ready:
            name = entry['model_name']
            query = '?' + urlencode({'game': game})
            data = json.loads(get('/model_json/' + quote(name) + query))
            with zipfile.ZipFile(io.BytesIO(get('/export_obj/' + quote(name) + query))) as archive:
                obj = archive.read(name + '.obj').decode()
                assert sum(line.startswith('f ') for line in obj.splitlines()) == len(data['indices'])//3, name
                assert sum(line.startswith('v ') for line in obj.splitlines()) == len(data['vertices'])//3, name
                absent = []
                for texture in set(data['material_textures']):
                    if texture.startswith('[Slot'):
                        continue
                    if texture in archive.namelist():
                        assert archive.read(texture) == get('/texture/' + quote(texture) + query), (name, texture)
                    else:
                        assert texture in archive.read('README.txt').decode(), (name, texture, 'unreported missing texture')
                        absent.append(texture)
                if absent:
                    missing[name] = sorted(absent)
                if game in ('t2','q3'):
                    assert 'metadata.json' in archive.namelist(), name
        results[game] = {'catalog': len(catalog), 'preview_and_export': len(ready),
                         'unsupported': [m['model_name'] for m in catalog if m not in ready],
                         'missing_export_textures': missing}
        print(game, len(catalog), 'catalog;', len(ready), 'previews/exports verified', flush=True)
    (root/'build/packaged-validation.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    return results


if __name__ == '__main__':
    check(sys.argv[1])
