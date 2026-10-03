"""Local Reflex Arena map import for the Reflex Maps page. Retail data stays outside source and builds.

python tools/import_reflex_map.py --game-base "C:/Program Files (x86)/Steam/steamapps/common/Reflex Arena" [--replace]

Reflex draws its map files as they are (no compile step), so the pack holds the .map files themselves, read in
the browser by static/reflex-maps/mapfile.js. A file is taken as a map when its first line is "reflex map
version N", wherever it lies in the game folder; Steam Workshop maps are read from the workshop folder beside the
install (steamapps/workshop/content/328070), when there is one.

Material colours: the game's materials are in the zip archives of its base folder (base/common.pak holds
common/materials/stone/concrete.material). A material file names its shader and holds typed parameters; the
colour of each material the maps name is its albedo parameter (diffuseColour in some, and for a textured
material its tintColor, which tints the texture: structural/dev/dev_grey128 is a grid texture tinted 0.5 grey),
written to materials.json with its shader. A face with no colour of its own is drawn in it. Materials without
one keep the page's guess from their name.

Material file (version 0x14, magic 0xd00e; little-endian): u16 version, u16 magic, shader name (128 bytes), u32
flags, u32 parameter count, u32 0, then per parameter 260 bytes: u32 type, name (128 bytes), value (128 bytes).
Types: 0 to 3 are one to four floats (roughness; uvScale; tintColor; albedo), 4 a texture path.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

try:
    from tools.reflex_models import EDITOR_EFFECTS, ENTITY_EFFECTS, PICKUP_EFFECTS, SKY_MESHES, VOLUME_MATERIALS, export_models, pickup_pad
    from tools.reflex_textures import bake, decode_dds, decode_textureset_image, textureset_images
except ImportError:  # Run as a script from tools/.
    from reflex_models import EDITOR_EFFECTS, ENTITY_EFFECTS, PICKUP_EFFECTS, SKY_MESHES, VOLUME_MATERIALS, export_models, pickup_pad
    from reflex_textures import bake, decode_dds, decode_textureset_image, textureset_images

WORKSHOP_APP = '328070'
# The game's sky material: the page takes its star texture (textureStars) from it.
SKY_MATERIAL = 'internal/world/skies/sky2'
HEADER = re.compile(rb'reflex map version (\d+)\s*$')
EFFECT_NAME = re.compile(r'^	+String64 effectName (\S+)', re.M)
# Materials an Effect entity puts on its model in place of the mesh's own (material0Name …).
MATERIAL_NAME = re.compile(r'^	+String256 material\dName (\S+)', re.M)
MATERIAL_MAGIC = b'\x14\x00\x0e\xd0'
PARAMETER = 260


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


def read_header(path):
    """The map file version, or None when the file is not a Reflex map."""
    try:
        with open(path, 'rb') as handle:
            match = HEADER.match(handle.readline(64))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def describe(text):
    """Title, author and the materials a map's faces name, read from its text."""
    title = author = ''
    group = None
    materials = set()
    for line in text.splitlines():
        if line and not line.startswith('\t'):
            group = line.split(' ', 1)[0]
        elif group == 'global' and line.startswith('\t\tString256 title ') and not title:
            title = line.split(' ', 2)[2].strip()
        elif group == 'global' and line.startswith('\t\tString256 ownerString ') and not author:
            author = line.split(' ', 2)[2].strip()
        if line.startswith('\t\t\t'):
            words = line.split()
            # A face: five numbers, vertex indices, a colour, then the material (empty or absent).
            at = 5
            while at < len(words) and words[at].isdigit():
                at += 1
            if at < len(words) and words[at].startswith('0x'):
                at += 1
            if 5 < at < len(words) and len(words) > 5 and re.fullmatch(r'-?\d+\.\d+', words[0]):
                materials.add(' '.join(words[at:]))
    return title, author, materials


def workshop_folder(game):
    """steamapps/workshop/content/328070 beside steamapps/common/<game>, when it exists."""
    for parent in game.parents:
        if parent.name.lower() == 'steamapps':
            folder = parent / 'workshop' / 'content' / WORKSHOP_APP
            return folder if folder.is_dir() else None
    return None


def find_maps(game):
    """(path, group) for every Reflex map of the install and of the Workshop folder beside it."""
    found = []
    for path in sorted(game.rglob('*.map')):
        if read_header(path) is not None:
            found.append((path, 'Reflex Arena'))
    workshop = workshop_folder(game)
    if workshop:
        for path in sorted(workshop.rglob('*.map')):
            if read_header(path) is not None:
                found.append((path, 'Steam Workshop'))
    return found


def read_material(raw):
    """A material file's shader and parameters: {name: a float, a list of 2 to 4 floats, or a texture path}."""
    if len(raw) < 144 or raw[:4] != MATERIAL_MAGIC:
        raise ValueError('not a Reflex material file')
    text = lambda chunk: chunk.split(b'\0', 1)[0].decode('latin-1')
    count = struct.unpack_from('<I', raw, 136)[0]
    if len(raw) < 144 + count * PARAMETER:
        raise ValueError('material file is cut short')
    parameters = {}
    for index in range(count):
        at = 144 + index * PARAMETER
        kind = struct.unpack_from('<I', raw, at)[0]
        name, value = text(raw[at + 4:at + 132]), raw[at + 132:at + 260]
        parameters[name] = (struct.unpack_from('<f', value)[0] if kind == 0 else list(struct.unpack_from(f'<{kind + 1}f', value)) if kind < 4
                            else text(value) if kind == 4 else value.hex())
    return text(raw[4:132]), parameters


def game_files(game, suffix):
    """Readers of every file of the game folder with this suffix by lower-case name without it
    (common/materials/stone/concrete for .material): loose files under base first, then those in its .pak archives."""
    base = next((folder for folder in game.iterdir() if folder.is_dir() and folder.name.lower() == 'base'), game)
    found = {}
    for path in sorted(base.rglob('*' + suffix)):
        found.setdefault(path.relative_to(base).with_suffix('').as_posix().lower(), (path.read_bytes, str(path.relative_to(game))))
    for pak in sorted(base.glob('*.pak')):
        try:
            archive = zipfile.ZipFile(pak)
        except (OSError, zipfile.BadZipFile):
            continue
        for entry in archive.namelist():
            if entry.lower().endswith(suffix):
                found.setdefault(entry[:-len(suffix)].lower(), (lambda archive=archive, entry=entry: archive.read(entry), pak.name))
    return found


def material_files(game):
    return game_files(game, '.material')


def material_colours(game, names, files=None):
    """The shader, and the colour where there is one, of each named material the game folder has; and the names
    with no colour."""
    files, colours = files if files is not None else material_files(game), {}
    for name in sorted(name for name in names if name):
        if name.lower() not in files:
            continue
        read, source = files[name.lower()]
        try:
            raw = read()
            shader, parameters = read_material(raw)
        except (OSError, ValueError, struct.error, zipfile.BadZipFile):
            continue
        # Every material found is kept with its shader, which tells the page what is see-through; a colour where it has one.
        # Its flags (the u32 after the shader name) say how the game blends a forward shader: 0x200 see-through, else solid.
        entry = colours[name] = dict(shader=shader, source=source, flags=struct.unpack_from('<I', raw, 132)[0],
                                     **{key: round(parameters[key], 4) for key in ('metallic', 'roughness', 'sss') if isinstance(parameters.get(key), float)})
        # Holograms and pickup glows: the height their shading runs over and how strong its gradient is.
        if isinstance(parameters.get('vSize_gradMul'), list):
            entry['gradient'] = [round(value, 4) for value in parameters['vSize_gradMul'][:2]]
        # Glowing materials (standard_ALBEDOCOLOUR_ALBEDOINTENSITY) shine their colour times this.
        if isinstance(parameters.get('albedoIntensity'), float):
            entry['intensity'] = round(parameters['albedoIntensity'], 4)
        key = next((key for key in ('albedo', 'diffuseColour', 'tintColor') if isinstance(parameters.get(key), list) and len(parameters[key]) >= 3), None)
        if key:
            entry['colour'] = [round(channel, 4) for channel in parameters[key][:3]]
            if key == 'tintColor': entry['tints'] = parameters.get('textureAlbedoSpec') or parameters.get('textureDiffuse') or ''
        # The textures a surface of it shows: its albedo (or diffuse) texture and the meta texture that darkens it.
        # Light beams' and pads' glows (alphaFresnel): how the glow falls off away from facing the viewer, and its strength.
        if isinstance(parameters.get('fresnelMulPow'), list) and isinstance(parameters.get('intensityMul'), float):
            entry['fresnel'] = [round(value, 4) for value in parameters['fresnelMulPow'][:2]] + [round(parameters['intensityMul'], 4)]
        # Particles' flipbooks: rows and columns of frames in the texture.
        if isinstance(parameters.get('flipbookRows'), float) and isinstance(parameters.get('flipbookCols'), float):
            entry['flipbook'] = [parameters['flipbookRows'], parameters['flipbookCols']]
        if shader.startswith(('internal/shaders/deferredPbr', 'internal/shaders/standard_', 'internal/shaders/clouds', 'internal/shaders/sky2', 'internal/shaders/particle')):
            albedo = parameters.get('textureAlbedoSpec') or parameters.get('textureDiffuse') or parameters.get('textureStars')
            if isinstance(albedo, str) and albedo:
                entry['textures'] = [albedo] + ([parameters['textureMeta']] if isinstance(parameters.get('textureMeta'), str) and parameters['textureMeta'] else [])
                # A diffuse texture's alpha is how see-through it is (ivy leaves); an albedoSpec's is its specular level.
                entry['seeThrough'] = not isinstance(parameters.get('textureAlbedoSpec'), str)
    return colours, sorted(name for name in names if name and 'colour' not in colours.get(name, {}))


class Textures:
    """The game's textures by the bare names materials give them, decoded on demand. A name is an image of a
    .textureset (dev_grid16_albedospec is structural/dev/dev_grid16_albedoSpec in structural/dev/dev_grid16.textureset)
    or a .dds file (ivy_leaf_1_c is environment/veg/ivy/ivy_leaf_1_c.dds)."""

    def __init__(self, game):
        self.dds = {key.rsplit('/', 1)[-1]: reader for key, reader in game_files(game, '.dds').items()}
        self.sets = {}
        for key, reader in game_files(game, '.textureset').items():
            if not key.startswith(('thumbs_material/', 'thumbs_mesh/', 'internal_skins/', 'myskins/')):
                self.sets.setdefault(key.rsplit('/', 1)[-1], reader)
        self.thumbs = {key[len('thumbs_material/'):]: reader for key, reader in game_files(game, '.textureset').items() if key.startswith('thumbs_material/')}
        self._read = {}

    def _textureset(self, key):
        if key not in self._read:
            raw = self.sets[key][0]()
            self._read[key] = (raw, {name.rsplit('/', 1)[-1].lower(): copies for name, copies in textureset_images(raw).items()})
        return self._read[key]

    def image(self, name):
        """The texture `name` as an RGBA image; KeyError when the game has none of that name. Some materials name it
        with its folders (internal/effects/ribbons/ribbon_strokes_c); the game finds it by its bare name all the same."""
        name = name.lower().rsplit('/', 1)[-1]
        if name in self.dds:
            return decode_dds(self.dds[name][0]())
        # Its textureset is named as it is less a suffix: dev_grid16 for dev_grid16_albedospec.
        parts = name.split('_')
        for cut in range(len(parts) - 1, 0, -1):
            key = '_'.join(parts[:cut])
            if key in self.sets:
                raw, images = self._textureset(key)
                if name in images:
                    return decode_textureset_image(raw, images[name])
        raise KeyError(name)

    def thumbnail(self, material):
        raw = self.thumbs[material.lower()][0]()
        images = textureset_images(raw)
        return decode_textureset_image(raw, next(iter(images.values())))


def texture_file(textures):
    """The file name a material's baked texture is kept under: its albedo's name, with its meta's after two
    underscores."""
    return '__'.join(name.lower().rsplit('/', 1)[-1] for name in textures) + '.png'


def material_textures(game, colours, output, replace=False):
    """Decodes the textures and thumbnails of the materials in `colours` into output/textures and output/thumbs,
    noting each one's files in its entry. Returns how many of each it has."""
    textures, done, counts = Textures(game), {}, dict(textures=0, thumbs=0)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    (output / 'thumbs').mkdir(parents=True, exist_ok=True)
    for name, entry in colours.items():
        wanted, alpha = entry.pop('textures', None), entry.pop('seeThrough', False)
        if wanted:
            file = texture_file(wanted)
            if file not in done:
                done[file] = file if (output / 'textures' / file).is_file() and not replace else None
                if done[file] is None:
                    try:
                        images = [textures.image(texture) for texture in wanted]
                        bake(images[0], images[1] if len(images) > 1 else None, alpha=alpha).save(output / 'textures' / file)
                        done[file] = file
                    except (KeyError, OSError, ValueError, struct.error, zipfile.BadZipFile) as exc:
                        done[file] = None
                        entry['textureError'] = f'{exc.__class__.__name__}: {exc}'
            if done[file]:
                entry['texture'] = file
                counts['textures'] += 1
        if name.lower() in textures.thumbs:
            thumb = re.sub(r'[^a-z0-9_.-]', '~', name.lower()) + '.png'
            if not (output / 'thumbs' / thumb).is_file() or replace:
                try:
                    textures.thumbnail(name).convert('RGB').resize((128, 128)).save(output / 'thumbs' / thumb)
                except (OSError, ValueError, StopIteration, struct.error, zipfile.BadZipFile):
                    continue
            entry['thumb'] = thumb
            counts['thumbs'] += 1
    return counts


def import_maps(game, output, replace=False):
    """Import every map of the Reflex Arena folder `game` (and its Workshop maps). Returns imported, skipped and
    failed maps, how many material colours were read and which materials have none."""
    game, output = Path(game).expanduser(), Path(output)
    if not game.is_dir():
        raise ValueError('Reflex Arena folder does not exist')
    maps = find_maps(game)
    if not maps:
        raise ValueError('No Reflex map files here. Enter the Reflex Arena folder.')
    (output / 'maps').mkdir(parents=True, exist_ok=True)
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={}, materials=0, uncoloured=[])
    used, effects = set(), {*PICKUP_EFFECTS.values(), *ENTITY_EFFECTS.values(), *EDITOR_EFFECTS.values(), *SKY_MESHES.values()}
    for path, group in maps:
        name = path.stem
        ident = map_id(name if group == 'Reflex Arena' else f'workshop__{path.parent.name}__{name}')
        # Packs made before the import took the baked light take it now.
        light_missing = path.with_suffix('.light').is_file() and not (output / 'maps' / index.get(ident, {}).get('light', '-')).is_file()
        if ident in index and (output / 'maps' / index[ident]['file']).is_file() and not replace and not light_missing:
            result['skipped'].append(name)
            used.update(index[ident].get('materials', []))
            kept = (output / 'maps' / index[ident]['file']).read_text(encoding='utf-8', errors='replace')
            effects.update(EFFECT_NAME.findall(kept))
            used.update(MATERIAL_NAME.findall(kept))
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            result['failed'][name] = str(exc)
            continue
        title, author, materials = describe(text)
        file = f'{ident}-{hashlib.sha256(raw).hexdigest()[:12]}.map'
        old = index.get(ident, {}).get('file')
        if old and old != file and (output / 'maps' / old).is_file():
            (output / 'maps' / old).unlink()
        (output / 'maps' / file).write_bytes(raw)
        # The map's baked light (Build Lighting writes it beside the map), which the page lights the map with.
        light = None
        old_light = index.get(ident, {}).get('light')
        try:
            light_raw = path.with_suffix('.light').read_bytes()
            light = f'{ident}-{hashlib.sha256(light_raw).hexdigest()[:12]}.light'
            (output / 'maps' / light).write_bytes(light_raw)
        except OSError:
            pass
        if old_light and old_light != light and (output / 'maps' / old_light).is_file():
            (output / 'maps' / old_light).unlink()
        index[ident] = dict(id=ident, name=name, title=title, author=author, group=group, file=file, version=read_header(path),
                            source=str(path.relative_to(game) if path.is_relative_to(game) else path.name), materials=sorted(materials))
        if light:
            index[ident]['light'] = light
        result['imported'].append(name)
        used.update(materials)
        effects.update(EFFECT_NAME.findall(text))
        used.update(MATERIAL_NAME.findall(text))
    # The models the maps place (Effect entities and pickups), with the materials they use.
    found = {}
    for suffix in ('.effect', '.mesh'):
        found.update({key + suffix: reader for key, (reader, _) in game_files(game, suffix).items()})
    # The pad a pickup stands on (health_25_pad, rocketlauncher_pad), where the game has one.
    effects.update(pad for pad in map(pickup_pad, PICKUP_EFFECTS.values()) if pad + '.effect' in found)
    files = material_files(game)
    bare = {}
    for name in files:
        bare.setdefault(name.rsplit('/', 1)[-1], name)
    model_materials, result['models_failed'] = export_models(lambda path: found[path.lower()]() if path.lower() in found else None, effects, output,
                                                             lambda name: name if '/' in name or name.lower() in files else bare.get(name.lower(), name))
    used |= model_materials
    # Every material of the game for the page's material browser (the editor's and effects' only when a map names
    # them, or the editor draws volumes with them), with its colour, texture and thumbnail.
    catalogue = used | set(VOLUME_MATERIALS.values()) | {SKY_MATERIAL} | {name for name in files if not name.startswith('internal/')}
    colours, uncoloured = material_colours(game, catalogue, files)
    result['uncoloured'] = [name for name in uncoloured if name in used]
    result['materials'] = sum('colour' in entry for name, entry in colours.items() if name in used)
    result.update(material_textures(game, colours, output, replace))
    (output / 'materials.json').write_text(json.dumps(colours, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    temporary = index_path.with_suffix('.tmp')
    rank = {'Reflex Arena': 0, 'Steam Workshop': 1}
    listed = sorted(index.values(), key=lambda item: (rank.get(item['group'], 2), item['name'].lower()))
    temporary.write_text(json.dumps(listed, separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'local-data/reflex-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}; "
          f"{done['materials']} material colours read, {done['textures']} materials textured, {done['thumbs']} thumbnails")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    if done['uncoloured']: print('NO ALBEDO FOR', ', '.join(done['uncoloured']))
