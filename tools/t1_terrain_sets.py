"""Terrain sets for the T1 Maps page's Terrain list: another world's or game's look on a map's terrain squares.

Each world's <world>.Terrain.dat (the Terrain Editor's table, LSMapper::createGridFile) lists its tiles in .dml order
with their four corner base types, in the order the name letters run: top left, top right, bottom right, bottom left
(lCCFF is grass above, dirt below). Worlds differ in tile count and types, so a set is moved by role (ROLES): each corner
type's role in the map's world, the source world's type for that role, then the source tile with those corners, turned
or mirrored when only that fits. Tribes 2 terrains are blended layers without tiles, so their tiles are blended here from
its material textures, corner by corner.

terrain-sets.json, beside the T1 pack's index: 'stock' maps each stock tile's stored PNG to its (world, tile), which
is how the page finds a map's tiles; each set holds per world one stored PNG or null (the map's own) per tile.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_t1_map import Install, bitmap_png  # noqa: E402
from local_data import LOCAL_DATA  # noqa: E402

# Each stock world's types in Terrain.dat order, by role. Pads and roads are man-made: the map keeps its own.
ROLES = {
    'lush': ['ground3', 'ground2', 'ground', 'rock', 'ground4', 'path', 'pad', 'road', 'rockmix'],
    'desert': ['ground', 'ground2', 'rock', 'ground3', 'rockmix', 'road', 'pad', 'pad'],
    'ice': ['ground', 'ground2', 'ground3', 'rock', 'rockmix', 'path'],
    'mars': ['ground', 'ground2', 'rockmix', 'rock', 'ground3', 'ground4'],
    'mud': ['ground', 'ground2', 'rock', 'ground3', 'rockmix'],
    'alien': ['ground', 'ground2', 'ground4', 'moss', 'pathmoss', 'path'],  # Its moss is its own: not other worlds' grass.
}
FALLBACK = {'ground4': 'ground3', 'ground3': 'ground2', 'ground2': 'ground', 'rockmix': 'rock', 'rock': 'ground3',
            'path': 'ground2', 'moss': 'ground3', 'pathmoss': 'path'}
# Tribes 2 worlds' terrain materials (textures/terrain/<name>.png) by role.
T2_WORLDS = {
    'Lush': {'ground': 'lushworld.grassmixed', 'ground2': 'lushworld.grasslight', 'ground3': 'lushworld.dirtmossy',
             'ground4': 'lushworld.lakebed', 'rock': 'lushworld.rocklight', 'rockmix': 'lushworld.rockmossy',
             'path': 'lushworld.lakesand'},
    'Desert': {'ground': 'desertworld.sand', 'ground2': 'desertworld.sanddark', 'ground3': 'desertworld.sandorange',
               'ground4': 'desertworld.sandburnt', 'rock': 'desertworld.rockfractured', 'rockmix': 'desertworld.rocksmooth',
               'path': 'desertworld.sandoxidized'},
    'Ice': {'ground': 'iceworld.snow', 'ground2': 'iceworld.snowice', 'ground3': 'iceworld.ice', 'rock': 'iceworld.rockblue',
            'rockmix': 'iceworld.snowrock'},
    'Badlands': {'ground': 'badlands.dirtyellow', 'ground2': 'badlands.dirtbumpy', 'ground3': 'badlands.dirtyellowcracked',
                 'ground4': 'badlands.dirtchipped', 'rock': 'badlands.rockbrown', 'rockmix': 'badlands.rockchipped'},
    'Lava': {'ground': 'lavaworld.muddyash', 'ground2': 'lavaworld.crust', 'ground3': 'lavaworld.lavarockhot',
             'rock': 'lavaworld.rockblack'},
}
T2_SIZE = 256  # Tribes 2's terrain textures; stock T1 tiles are 128. Its tiles are stored as JPEG.


def read_tile_table(data):
    """<world>.Terrain.dat: type names, then per tile its file name and corners (TL, TR, BR, BL type indices)."""
    count, tiles = struct.unpack_from('<ii', data)
    types = [data[8 + i * 32:40 + i * 32].split(b'\0')[0].decode('cp1252').strip() for i in range(count)]
    at = 8 + count * 32
    if len(data) < at + tiles * 276:
        raise ValueError('Short terrain table')
    records = [data[at + i * 276:at + (i + 1) * 276] for i in range(tiles)]
    return types, [(record[:256].split(b'\0')[0].decode('cp1252'), tuple(record[256:260])) for record in records]


def turned(corners, image, k):
    """The k-th of the eight turns and mirrors: k >= 4 mirrors left-right, then k % 4 quarter turns clockwise."""
    if k >= 4:
        corners, image = (corners[1], corners[0], corners[3], corners[2]), None if image is None else image[:, ::-1]
    for _ in range(k % 4):
        corners, image = (corners[3], corners[0], corners[1], corners[2]), None if image is None else np.rot90(image, -1)
    return corners, image


def role_type(role, roles):
    """The type index of a role in a world's ROLES list (or a role -> material dict), following FALLBACK."""
    if role in ('pad', 'road'):  # Man-made: the map keeps its own.
        return None
    while role and role not in roles:
        role = FALLBACK.get(role)
    return None if not role else roles.index(role) if isinstance(roles, list) else roles[role]


def wanted(target, corners, roles):
    """A target tile's corners as the source's types (or materials) by role; None where the map keeps its own."""
    return tuple(role_type(ROLES[target][t] if t < len(ROLES[target]) else None, roles) for t in corners)


def variants(tiles):
    """Each tile's ordinal among the tiles with its corners (lCCCC, lCCCC1...), so variants map to variants."""
    seen, out = {}, []
    for _, corners in tiles:
        out.append(seen.get(corners, 0))
        seen[corners] = out[-1] + 1
    return out


def match_t1(target, source, target_tiles, source_tiles):
    """Per target tile: (source tile, turn) or None (build_sets blends one). Unturned tiles before turned ones."""
    found = {}
    for k in range(8):
        for index, (_, corners) in enumerate(source_tiles):
            key = turned(corners, None, k)[0]
            if found.get(key, [(None, k)])[0][1] == k:
                found.setdefault(key, []).append((index, k))
    out = []
    for (_, corners), ordinal in zip(target_tiles, variants(target_tiles)):
        want = wanted(target, corners, ROLES[source])
        if None in want:
            out.append(None)
            continue
        out.append(found[want][ordinal % len(found[want])] if want in found else None)
    return out


def type_palettes(tiles, pngs):
    """Per type, four colours of its blurred solid tiles: a grass tile's own dirt patches are one of grass's colours."""
    solid = {}
    for (_, corners), png in zip(tiles, pngs):
        if png and len(set(corners)) == 1:
            with Image.open(io.BytesIO(png)) as image:
                solid.setdefault(corners[0], []).append(image.convert('RGB').filter(ImageFilter.GaussianBlur(image.width / 16)))
    palettes = {}
    for kind, images in solid.items():
        strip = Image.new('RGB', (sum(image.width for image in images), max(image.height for image in images)))
        for at, image in enumerate(images):
            strip.paste(image, (sum(other.width for other in images[:at]), 0))
        palettes[kind] = np.array(strip.quantize(4).getpalette()[:12], 'float32').reshape(-1, 3)
    return palettes


def blend_tile(types, materials, images, stock=None, palettes=None, size=T2_SIZE):
    """A transition tile from its corners' material textures (corner i: type types[i], material materials[i]).
    Inside, each pixel takes the type with a colour (type_palettes) nearest the blurred stock tile there, so paths and
    edges keep the stock tile's shapes; toward the tile's edges the corners' bilinear weights take over, so
    neighbouring tiles meet. Without the stock tile or a palette for each type, the bilinear weights alone."""
    u, v = np.linspace(0, 1, size)[None, :], np.linspace(0, 1, size)[:, None]
    edge = np.clip(np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v)) * 16, 0, 1)
    weights = {}
    for kind, weight in zip(types, [(1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v]):
        weights[kind] = weights.get(kind, 0) + weight
    if stock is not None and all(kind in palettes for kind in weights):
        blurred = np.asarray(Image.fromarray(stock).filter(ImageFilter.GaussianBlur(size / 16)), 'float32')
        distance = {kind: np.linalg.norm(blurred[..., None, :] - palettes[kind], axis=3).min(2) for kind in weights}
        nearest = np.min(list(distance.values()), axis=0)
        # Weighted by the corners too: a grass tile's own dirt patches, far from the path's corners, stay grass.
        near = {kind: np.exp((nearest - distance[kind]) / 8) * (weights[kind] + .05) ** 2 for kind in weights}
        total = sum(near.values())
        weights = {kind: (1 - edge) * weights[kind] + edge * near[kind] / total for kind in weights}
    by_material = {}
    for kind, material in dict(zip(types, materials)).items():
        by_material[material] = by_material.get(material, 0) + weights[kind]
    by_material = {material: weight ** 2 for material, weight in by_material.items()}
    total = sum(by_material.values())
    return sum(weight[..., None] * images[material] for material, weight in by_material.items()) / total[..., None]


def image_bytes(array, kind='PNG'):
    out = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(array).clip(0, 255).round().astype('uint8'), 'RGB').save(out, kind, quality=90)
    return out.getvalue()


def build_sets(install, output, t2_textures=None):
    """Write output/terrain-sets.json from the install's worlds and Tribes 2's terrain textures. Returns the set names."""
    output, everything = Path(output), install.everything()

    def store(data, suffix='.png'):
        stored = hashlib.sha1(data).hexdigest()[:20] + suffix
        (output / 'textures').mkdir(parents=True, exist_ok=True)
        if not (output / 'textures' / stored).exists():
            (output / 'textures' / stored).write_bytes(data)
        return stored

    def tile_png(read, name):
        for candidate in (Path(name).stem.lower() + '.png', Path(name).stem.lower() + '.bmp'):
            if candidate in read:
                try:
                    return bitmap_png(read[candidate](), {})
                except (ValueError, OSError, struct.error):
                    pass
        return None

    worlds = {}  # world -> (tiles, stock PNG bytes per tile)
    for world in ROLES:
        if world + '.terrain.dat' in everything:
            tiles = read_tile_table(everything[world + '.terrain.dat']())[1]
            worlds[world] = (tiles, [tile_png(everything, name) for name, _ in tiles])
    palettes = {world: type_palettes(tiles, pngs) for world, (tiles, pngs) in worlds.items()}
    stock = {}
    for world, (_, pngs) in worlds.items():
        for index, png in enumerate(pngs):
            if png:
                stock.setdefault(store(png), [world, index])

    def blended(target, index, want, images):
        """A tile blended from the set's ground (images, by want's keys) and, at man-made corners (None in want), the
        map's own tile, so a pad's edge keeps its pad. None when it would be all the map's own, or a ground lacks an image."""
        corners, png = worlds[target][0][index][1], worlds[target][1][index]
        if not png or all(w is None for w in want) or any(w is not None and w not in images for w in want):
            return None
        size = next(iter(images.values())).shape[0]
        own = np.asarray(Image.open(io.BytesIO(png)).convert('RGB').resize((size, size), Image.Resampling.BICUBIC))
        return blend_tile(corners, ['own' if w is None else w for w in want], {**images, 'own': own}, own, palettes[target], size)

    sets = []
    for source, (source_tiles, source_pngs) in worlds.items():
        solids = {}  # The source's ground by type, for tiles it has no match for.
        for (_, corners), png in zip(source_tiles, source_pngs):
            if png and len(set(corners)) == 1 and corners[0] not in solids:
                solids[corners[0]] = np.asarray(Image.open(io.BytesIO(png)).convert('RGB'), 'float32')
        tiles = {}
        for target, (target_tiles, _) in worlds.items():
            if target == source:
                continue
            row = []
            for index, found in enumerate(match_t1(target, source, target_tiles, source_tiles)):
                png = found and source_pngs[found[0]]
                if png and found[1]:
                    png = image_bytes(turned((0,) * 4, np.asarray(Image.open(io.BytesIO(png)).convert('RGB')), found[1])[1])
                elif not found:
                    image = blended(target, index, wanted(target, target_tiles[index][1], ROLES[source]), solids)
                    png = None if image is None else image_bytes(image)
                row.append(store(png) if png else None)
            tiles[target] = row
        sets.append(dict(id='t1-' + source, group='Tribes', name=source.capitalize(), tiles=tiles))
    folder = Path(t2_textures) if t2_textures else None
    for name, roles in T2_WORLDS.items() if folder and folder.is_dir() else ():
        images = {}
        for material in set(roles.values()):
            if (folder / (material + '.png')).is_file():
                with Image.open(folder / (material + '.png')) as image:
                    images[material] = np.asarray(image.convert('RGB').resize((T2_SIZE, T2_SIZE), Image.Resampling.LANCZOS), 'float32')
        roles = {role: material for role, material in roles.items() if material in images}
        if 'ground' not in roles:
            continue
        made, tiles = {}, {}
        for target, (target_tiles, _) in worlds.items():
            row = []
            for index, ((_, corners), ordinal) in enumerate(zip(target_tiles, variants(target_tiles))):
                want = wanted(target, corners, roles)
                # Solid tiles share a material texture, shifted a quarter per variant; a transition follows its stock tile.
                solid = len(set(want)) == 1 and None not in want
                key = want + (ordinal % 4,) if solid else (target, index)
                if key not in made:
                    image = np.roll(images[want[0]], (ordinal % 4) * T2_SIZE // 4, (0, 1)) if solid else blended(target, index, want, images)
                    made[key] = None if image is None else store(image_bytes(image, 'JPEG'), '.jpg')  # A fifth of the PNG's size.
                row.append(made[key])
            tiles[target] = row
        sets.append(dict(id='t2-' + name.lower(), group='Tribes 2', name=name, tiles=tiles))
    if not worlds:
        return []
    (output / 'terrain-sets.json').write_text(json.dumps({'stock': stock, 'sets': sets}, separators=(',', ':')), encoding='utf-8')
    return [entry['group'] + ' · ' + entry['name'] for entry in sets]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('game', help='Tribes folder (its base/ holds the worlds)')
    parser.add_argument('--output', default=str(LOCAL_DATA / 't1-maps'), help='T1 Maps pack folder')
    parser.add_argument('--t2', default=str(LOCAL_DATA / 't2-maps/base/textures/terrain'), help="Tribes 2's textures/terrain")
    args = parser.parse_args()
    for line in build_sets(Install(args.game), args.output, args.t2):
        print(line)


if __name__ == '__main__':
    main()
