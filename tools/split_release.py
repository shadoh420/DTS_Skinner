"""Split a release folder into an app ZIP and a data ZIP, each with a .sha256.

python tools/split_release.py dist/v25-package/DTS-Skinner dist/v25 v25

The data ZIP holds what rarely changes and makes up most of the size: the stock Tribes 1 and 2 models and textures
(_internal/static/model_json, t2 and textures) and the Tribes 1 animation caches (local-data/animations). The app ZIP
holds the rest. Both unzip into the same folder (each starts with DTS-Skinner/).

The data ZIP is written reproducibly (sorted names, fixed timestamps), so its .sha256 equals the last data release's
when nothing in it changed: then publish only the app ZIP and link the data release. Otherwise publish the new data
ZIP as the next data release (data-2, data-3...).
"""
import argparse
import hashlib
from pathlib import Path
import zipfile

DATA = ('_internal/static/model_json/', '_internal/static/t2/', '_internal/static/textures/', 'local-data/animations/')


def write_zip(target, folder, files):
    """Zip `files` (paths under `folder`) as DTS-Skinner/..., same bytes for the same files; return the sha256."""
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            info = zipfile.ZipInfo('DTS-Skinner/' + path.relative_to(folder).as_posix(), (2020, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (target.parent / (target.name + '.sha256')).write_bytes(f'{digest}  {target.name}\n'.encode())  # LF for sha256sum
    return digest


def split(folder, output, tag):
    folder, output = Path(folder), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    files = sorted((p for p in folder.rglob('*') if p.is_file()), key=lambda p: p.relative_to(folder).as_posix())
    is_data = [p.relative_to(folder).as_posix().startswith(DATA) for p in files]
    data = [p for p, flag in zip(files, is_data) if flag]
    app = [p for p, flag in zip(files, is_data) if not flag]
    result = {}
    for name, part in ((f'DTS-Skinner-{tag}-Windows.zip', app), ('DTS-Skinner-data-Windows.zip', data)):
        target = output / name
        result[name] = dict(files=len(part), sha256=write_zip(target, folder, part), bytes=target.stat().st_size)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', type=Path, help='the release folder holding SkinnerApp.exe')
    parser.add_argument('output', type=Path)
    parser.add_argument('tag', help='the release tag in the app ZIP name, e.g. v25')
    args = parser.parse_args()
    for name, info in split(args.folder, args.output, args.tag).items():
        print(name, info)
