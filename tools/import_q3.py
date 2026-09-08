"""Local Quake 3 MD3/skin import. Retail data stays outside source and builds.

Format: id Software Quake-III-Arena/code/qcommon/qfiles.h, IDP3 version 15.
Folder policy: pak*.pk3 in baseq3, then loose files. A single mod PK3 can
be selected explicitly; unrelated map packs are not mounted automatically.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
import zipfile
import uuid
from functools import cache

from PIL import Image

ANIM_NAMES = ('BOTH_DEATH1 BOTH_DEAD1 BOTH_DEATH2 BOTH_DEAD2 BOTH_DEATH3 BOTH_DEAD3 '
              'TORSO_GESTURE TORSO_ATTACK TORSO_ATTACK2 TORSO_DROP TORSO_RAISE TORSO_STAND '
              'TORSO_STAND2 LEGS_WALKCR LEGS_WALK LEGS_RUN LEGS_BACK LEGS_SWIM LEGS_JUMP '
              'LEGS_LAND LEGS_JUMPB LEGS_LANDB LEGS_IDLE LEGS_IDLECR LEGS_TURN').split()


def key_name(path):
    return str(path).replace('\\', '/').lower()


def asset_name(path):
    key = key_name(path)
    return re.sub(r'[^a-z0-9_-]', '_', str(Path(key).with_suffix('')))[:100] + '__' + hashlib.sha256(key.encode()).hexdigest()[:12]


def read_text(raw):
    return raw.decode('utf-8-sig', errors='replace')


def skin_map(raw):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', read_text(raw), flags=re.S)
    result = {}
    for line in text.splitlines():
        if ',' in line:
            surface, shader = (x.strip().strip('"').lower() for x in line.split(',', 1))
            if surface and shader and not surface.startswith('tag_'):
                result[surface] = shader
    return result


def animations(raw):
    rows = []
    for line in read_text(raw).splitlines():
        match = re.match(r'\s*(-?\d+)\s+(-?\d+)\s+(\d+)\s+([\d.]+)', line)
        if match:
            first, count, loop, fps = match.groups()
            rows.append(dict(start=int(first), count=abs(int(count)), reverse=int(count)<0,
                             loop_frames=int(loop), fps=max(float(fps), 1)))
    result = {name: dict(row, name=name) for name, row in zip(ANIM_NAMES, rows)}
    if 'LEGS_WALKCR' in result and 'TORSO_GESTURE' in result:
        offset = result['LEGS_WALKCR']['start'] - result['TORSO_GESTURE']['start']
        for name, row in result.items():
            if name.startswith('LEGS_'):
                row['start'] -= offset
    return result


def read_md3(raw, frame=0):
    """Read one frame with bounded offsets; all surfaces share native topology."""
    if len(raw) < 108 or raw[:4] != b'IDP3':
        raise ValueError('Not an MD3 model')
    version, = struct.unpack_from('<i', raw, 4)
    flags, frame_count, tag_count, surface_count, skins, oframes, otags, osurfaces, end = struct.unpack_from('<9i', raw, 72)
    if version != 15 or not 0 < frame_count <= 1024 or not 0 <= tag_count <= 256 or not 0 <= surface_count <= 256 or not 108 <= end <= len(raw):
        raise ValueError('Invalid MD3 header or unsupported version/counts')
    if not 0 <= frame < frame_count:
        raise ValueError('MD3 animation frame outside model')

    def span(offset, count, size, limit=end, minimum=108):
        if offset < minimum or count < 0 or offset + count * size > limit:
            raise ValueError('MD3 array outside declared bounds')
    span(oframes, frame_count, 56)
    span(otags, frame_count * tag_count, 112)
    tags = {}
    for index in range(tag_count):
        offset = otags + (frame * tag_count + index) * 112
        name = raw[offset:offset+64].split(b'\0')[0].decode('ascii', errors='replace').lower()
        values = struct.unpack_from('<12f', raw, offset+64)
        if not all(math.isfinite(x) for x in values):
            raise ValueError('Non-finite MD3 tag')
        tags[name] = values
    surfaces = []
    offset = osurfaces
    for _ in range(surface_count):
        span(offset, 1, 108)
        if raw[offset:offset+4] != b'IDP3':
            raise ValueError('Invalid MD3 surface magic')
        name = raw[offset+4:offset+68].split(b'\0')[0].decode('ascii', errors='replace').lower()
        if len(name)>2 and name[-2]=='_':
            name = name[:-2]  # R_LoadMD3 strips LOD surface suffixes before .skin lookup.
        _, frames, shaders, verts, triangles, otri, oshader, ouv, overt, send = struct.unpack_from('<10i', raw, offset+68)
        if frames != frame_count or not 0 < verts <= 65536 or not 0 < triangles <= 131072 or not 0 <= shaders <= 4096 or send < 108:
            raise ValueError('Invalid MD3 surface counts')
        span(offset, 1, send)
        for start, count, size in ((otri, triangles, 12), (oshader, shaders, 68), (ouv, verts, 8), (overt, verts*frames, 8)):
            span(start, count, size, send)
        indices = list(struct.unpack_from('<' + 'i' * (triangles*3), raw, offset+otri))
        if any(i < 0 or i >= verts for i in indices):
            raise ValueError('MD3 triangle index outside surface')
        uvs = list(struct.unpack_from('<'+'f'*(verts*2), raw, offset+ouv))
        if not all(math.isfinite(x) for x in uvs):
            raise ValueError('Non-finite MD3 UV')
        positions, normals = [], []
        for index in range(verts):
            x, y, z, encoded = struct.unpack_from('<3hH', raw, offset+overt+(frame*verts+index)*8)
            positions.extend((x/64, y/64, z/64))
            lat, lng = (encoded >> 8) * 2*math.pi/255, (encoded & 255)*2*math.pi/255
            normals.extend((math.cos(lat)*math.sin(lng), math.sin(lat)*math.sin(lng), math.cos(lng)))
        shader_names = [raw[offset+oshader+i*68:offset+oshader+i*68+64].split(b'\0')[0].decode('ascii', errors='replace').lower() for i in range(shaders)]
        surfaces.append(dict(name=name, vertices=positions, normals=normals, uvs=uvs, indices=indices, shaders=shader_names))
        offset += send
    return dict(surfaces=surfaces, tags=tags, frame_count=frame_count)


def transform(values, tags=(), normal=False):
    output = []
    for i in range(0, len(values), 3):
        point = values[i:i+3]
        for tag in tags:
            point = [sum(tag[3+k*3+j]*point[k] for k in range(3)) + (0 if normal else tag[j]) for j in range(3)]
        x, y, z = point
        output.extend((y, z, x))  # Proper rotation: native +X faces viewer +Z, Z becomes up.
    return output


def shader_key(name):
    # R_FindShader strips the image extension before looking up a script.
    return re.sub(r'\.[^/.]*$', '', key_name(name))


def shader_maps(assets):
    result = {}
    for path, record in assets.items():
        if not path.endswith('.shader'):
            continue
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', read_text(record['read']()), flags=re.S)
        tokens = re.findall(r'[^\s{}]+|[{}]', text)
        index = 0
        while index+1 < len(tokens):
            name = tokens[index].strip('"').lower(); index += 1
            if tokens[index] != '{':
                continue
            depth, body, stages, stage = 1, [], [], []
            index += 1
            while index < len(tokens) and depth:
                token = tokens[index]; index += 1
                if token == '{':
                    depth += 1
                    if depth == 2: stage = []
                elif token == '}':
                    if depth == 2: stages.append(stage)
                    depth -= 1
                elif depth == 1: body.append(token.strip('"').lower())
                elif depth == 2: stage.append(token.strip('"').lower())
            def value(tokens, keyword, default=''):
                return tokens[tokens.index(keyword)+1] if keyword in tokens[:-1] else default
            for stage in stages:
                kind = next((x for x in stage if x in ('map','clampmap','animmap')), None)
                if not kind: continue
                at = stage.index(kind) + (2 if kind == 'animmap' else 1)
                if at >= len(stage): continue
                target = stage[at]
                if target == '$lightmap': continue
                blend = []
                if 'blendfunc' in stage[:-1]:
                    at = stage.index('blendfunc')+1
                    blend = {'add':['gl_one','gl_one'], 'blend':['gl_src_alpha','gl_one_minus_src_alpha'],
                             'filter':['gl_dst_color','gl_zero']}.get(stage[at], stage[at:at+2])
                    if blend == ['gl_one','gl_zero']: blend = []
                cull = value(body, 'cull', 'front')
                settings = dict(shader=shader_key(name), alphaFunc=value(stage,'alphafunc').upper(), blend=blend,
                    # Store the discarded CCW face, after native clockwise conversion.
                    cull='none' if cull in ('none','disable','twosided') else 'front' if cull == 'back' else 'back',
                    depthWrite='depthwrite' in stage or not blend, clamp=kind=='clampmap', tcGen=value(stage,'tcgen','base'))
                limitations = []
                if len(stages)>1: limitations.append('additional shader stages')
                if 'deformvertexes' in body: limitations.append('shader vertex deformation')
                if kind == 'animmap': limitations.append('animated textures (first frame shown)')
                if 'tcmod' in stage: limitations.append('texture coordinate modifiers')
                if value(stage,'rgbgen') not in ('','identity','identitylighting','lightingdiffuse'): limitations.append('dynamic color generation')
                if value(stage,'alphagen') not in ('','identity'): limitations.append('dynamic alpha generation')
                result[shader_key(name)] = dict(target=target, settings=settings, limitations=limitations)
                break
    return result


class Textures:
    def __init__(self, assets, output):
        self.assets, self.output, self.cache = assets, output, {}
        self.shaders = shader_maps(assets)
        output.mkdir(parents=True, exist_ok=True)

    def resolve(self, shader):
        if shader in self.cache:
            return self.cache[shader]
        definition = self.shaders.get(shader_key(shader), {})
        target = definition.get('target', key_name(shader))
        settings = definition.get('settings', dict(shader=shader_key(shader), alphaFunc='', blend=[], cull='back', depthWrite=True, clamp=False, tcGen='base'))
        warnings = [f'Shader {shader}: not reproduced: {", ".join(definition["limitations"])}.'] if definition.get('limitations') else []
        stem = Path(target).with_suffix('').as_posix() if Path(target).suffix.lower() in ('.png','.jpg','.jpeg','.tga') else target
        candidates = [target] + [stem+ext for ext in ('.tga','.jpg','.png','.jpeg')]
        key = next((key for key in candidates if key in self.assets), None)
        filename = asset_name(key or target or 'missing')+'.png'
        if target == '$whiteimage':
            if not (self.output/filename).exists(): Image.new('RGBA',(1,1),'white').save(self.output/filename)
        elif key:
            try:
                # Never overwrite a user's edited PNG when reimporting.
                if not (self.output/filename).exists():
                    with Image.open(io.BytesIO(self.assets[key]['read']())) as source:
                        source.convert('RGBA').save(self.output/filename)
            except (OSError, ValueError) as exc:
                warnings.append(f'Cannot decode {key}: {exc}')
        else:
            warnings.append(f'Missing texture: {target or "unassigned MD3 surface"}')
        self.cache[shader] = filename, warnings, settings
        return filename, warnings, settings


def combine(parts, resolver):
    data = dict(game='q3', winding='ccw', vertices=[], normals=[], uvs=[], indices=[], groups=[], material_textures=[], material_names=[], material_settings=[], warnings=[])
    for part in parts:
        md3, skin, tags = part['model'], part.get('skin', {}), part.get('tags', ())
        for surface in md3['surfaces']:
            shader = skin.get(surface['name'], next(iter(surface['shaders']), ''))
            if shader in ('*off', 'off', 'nodraw'):
                continue
            texture, warnings, settings = resolver.resolve(shader)
            base = len(data['vertices'])//3
            data['groups'].append(dict(start=len(data['indices']), count=len(surface['indices']), materialIndex=len(data['material_textures'])))
            data['material_textures'].append(texture)
            data['material_names'].append(part['name']+'/'+surface['name'])
            data['material_settings'].append(settings)
            data['warnings'].extend(warnings)
            data['vertices'].extend(transform(surface['vertices'], tags))
            data['normals'].extend(transform(surface['normals'], tags, True))
            data['uvs'].extend(surface['uvs'])
            # Native MD3 faces are clockwise relative to their outward normals.
            # Our proper axis rotation preserves handedness; glTF/Three use CCW.
            for at in range(0, len(surface['indices']), 3):
                a, b, c = surface['indices'][at:at+3]
                data['indices'].extend((base+a, base+c, base+b))
    data['warnings'] = sorted(set(data['warnings']))
    if not data['vertices']:
        raise ValueError('No visible MD3 surfaces (tag-only attachment model).')
    return data


def json_write(path, data):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data, separators=(',',':')), encoding='utf-8')
    temporary.replace(path)


def current_import(root):
    root = Path(root)
    pointer = root/'current.json'
    if not pointer.is_file():
        return root  # Earlier local imports remain readable.
    generation = json.loads(pointer.read_text(encoding='utf-8'))['generation']
    if not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}',generation):
        raise ValueError('Invalid local Q3 import generation')
    return root/'imports'/generation


def import_catalog(source, output):
    source, output = Path(source).expanduser().resolve(), Path(output).resolve()
    if not source.exists():
        raise ValueError('Game folder or PK3 file does not exist')
    if source.is_dir() and (source/'baseq3').is_dir():
        source = source/'baseq3'
    if source.is_file() and source.suffix.lower() != '.pk3':
        raise ValueError('Select a game folder or a PK3 file')
    archives, assets = [], {}
    wanted = {'.md3','.skin','.cfg','.shader','.tga','.jpg','.jpeg','.png'}
    try:
        paths = [source] if source.is_file() else sorted(source.glob('pak*.pk3'), key=lambda p:p.name.lower())
        for path in paths:
            archive = zipfile.ZipFile(path); archives.append(archive)
            for entry in archive.infolist():
                key = key_name(entry.filename)
                if Path(key).suffix in wanted and not entry.is_dir():
                    if entry.file_size > 128*1024*1024:
                        raise ValueError('Oversized archive asset: '+key)
                    assets[key] = dict(source=str(path)+':'+entry.filename, read=lambda z=archive,n=entry.filename:z.read(n))
        if source.is_dir():
            for folder in ('models','scripts','textures'):
                for path in sorted((source/folder).rglob('*')):
                    if path.is_file() and path.suffix.lower() in wanted:
                        assets[key_name(path.relative_to(source))] = dict(source=str(path), read=path.read_bytes)
        models = sorted(key for key in assets if key.endswith('.md3'))
        if not models:
            raise ValueError('No MD3 models found. Select baseq3, a PK3, or a folder containing models/.')
        texture_output = output/'textures'
        import_root = output
        generation = uuid.uuid4().hex
        # Publish one small pointer only after all source/catalog files are ready.
        # Previous generations preserve active exports and are never overwritten.
        output = import_root/'imports'/generation
        for folder in ('model_json','sources','textures'):
            (output/folder).mkdir(parents=True, exist_ok=True)
        manifest = {}
        for key, record in assets.items():
            if Path(key).suffix in ('.md3','.skin','.cfg'):
                filename = asset_name(key)+Path(key).suffix
                raw = record['read']()
                (output/'sources'/filename).write_bytes(raw)
                manifest[key] = dict(file=filename, source=record['source'], sha256=hashlib.sha256(raw).hexdigest())
        resolver, catalog = Textures(assets, texture_output), []

        def config(key):
            path = (Path(key).parent/'animation.cfg').as_posix()
            return animations(assets[path]['read']()) if path in assets else {}

        def add(name, category, specs, clips):
            label = Path(specs[0]['path']).with_suffix('').as_posix().removeprefix('models/')
            if category == 'Complete players':
                variant = Path(specs[0].get('skin') or 'lower_default.skin').stem.removeprefix('lower_')
                label = Path(specs[0]['path']).parent.name + ' / ' + variant
            entry = dict(model_name=name, display_name=label, game='q3', category=category, status='ready', warnings=[])
            try:
                parts = build_parts(specs, lambda key:assets[key]['read']())
                data = combine(parts, resolver)
                data['metadata'] = dict(source=[dict(virtual_path=s['path'], **manifest[s['path']]) for s in specs],
                    parts=specs, animations=clips, pose='Standing pose where animation.cfg is available; frame 0 otherwise.',
                    warnings=data['warnings'], material_version=2, limitations=['First shader stage with alpha test, blend, culling and wrapping; additional stages and shader animation are not reproduced.'])
                json_write(output/'model_json'/(name+'.json'), data)
                entry['warnings'] = data['warnings']
            except (ValueError, KeyError, OSError, struct.error) as exc:
                entry.update(status='unsupported', warnings=[str(exc)])
            catalog.append(entry)

        for key in models:
            stem, parent = re.sub(r'_[12]$', '', Path(key).stem), Path(key).parent
            cfg = config(key)
            frame = cfg.get('LEGS_IDLE' if stem == 'lower' else 'TORSO_STAND' if stem == 'upper' else '', {}).get('start',0)
            skin = (parent/(stem+'_default.skin')).as_posix()
            specs = [dict(path=key, name=stem, frame=frame, skin=skin if skin in assets else None)]
            add(asset_name(key), 'Player parts' if key.startswith('models/players/') else 'Weapons' if '/weapons' in key else 'Items & world models', specs, cfg)
        for lower in (key for key in models if key.startswith('models/players/') and Path(key).name == 'lower.md3'):
            parent = Path(lower).parent
            if any((parent/(part+'.md3')).as_posix() not in assets for part in ('upper','head')):
                continue
            cfg = config(lower)
            variants = sorted(Path(key).stem[6:] for key in assets if str(Path(key).parent) == str(parent) and Path(key).name.startswith('lower_') and key.endswith('.skin')) or ['default']
            for variant in variants:
                specs = []
                for part, idle in (('lower','LEGS_IDLE'),('upper','TORSO_STAND'),('head','')):
                    skin = (parent/(part+'_'+variant+'.skin')).as_posix()
                    if skin not in assets:
                        skin = (parent/(part+'_default.skin')).as_posix()
                    specs.append(dict(path=(parent/(part+'.md3')).as_posix(), name=part, frame=cfg.get(idle,{}).get('start',0), skin=skin if skin in assets else None))
                add('player_'+asset_name(str(parent)+'/'+variant), 'Complete players', specs, cfg)
        from tools.model_data import model_sort_key
        catalog.sort(key=lambda item:model_sort_key(item['model_name']))
        json_write(output/'sources.json', manifest)
        json_write(output/'inventory.json', dict(source=str(source), archives=[str(z.filename) for z in archives], source_model_count=len(models),
            policy='Retail pak*.pk3 in filename order, then loose files. Explicit single PK3 supported.', entries=len(catalog),
            ready=sum(x['status']=='ready' for x in catalog), limitations=['MD3 and standard lower/upper/head players supported; shader effects approximated with static base maps.']))
        json_write(output/'catalog.json', catalog)
        json_write(import_root/'current.json', dict(generation=generation))
        return dict(entries=len(catalog), ready=sum(x['status']=='ready' for x in catalog))
    finally:
        for archive in archives:
            archive.close()


def build_parts(specs, read):
    parts = []
    for spec in specs:
        part = dict(name=spec['name'], model=read_md3(read(spec['path']), spec['frame']),
                    skin=skin_map(read(spec['skin'])) if spec.get('skin') else {}, tags=[])
        if len(specs) == 3:
            if spec['name'] == 'upper':
                part['tags'] = [parts[0]['model']['tags']['tag_torso']]
            elif spec['name'] == 'head':
                part['tags'] = [parts[1]['model']['tags']['tag_head'], parts[0]['model']['tags']['tag_torso']]
        parts.append(part)
    return parts


def load_animated_model(name, source_path, preview_data):
    """Bake MD3 vertex frames and player attachment tags into genuine GLB clips."""
    output = current_import(source_path)
    manifest = json.loads((output/'sources.json').read_text(encoding='utf-8'))
    @cache
    def read(key):
        return (output/'sources'/manifest[key]['file']).read_bytes()
    specs = preview_data['metadata']['parts']
    definitions = preview_data['metadata']['animations']
    data = dict(preview_data, animation_clips=[])
    # Materials are already resolved/edited. Only bake geometry from source.
    class ExistingTextures:
        def resolve(self, shader):
            return 'unused.png', [], {}
    clips = list(definitions.values())
    if len(specs) == 1:
        part = specs[0]['name']
        clips = [clip for clip in clips if part in ('lower','upper') and (clip['name'].startswith('BOTH_') or clip['name'].startswith('LEGS_' if part=='lower' else 'TORSO_'))]
        count = read_md3(read(specs[0]['path']))['frame_count']
        if not clips and count > 1:
            clips = [dict(name='All frames', start=0, count=count, fps=10, loop_frames=0)]
    for clip in clips:
        affected_specs = [spec for spec in specs if len(specs)==1 or clip['name'].startswith('BOTH_') and spec['name'] in ('lower','upper') or clip['name'].startswith('TORSO_') and spec['name']=='upper' or clip['name'].startswith('LEGS_') and spec['name']=='lower']
        if (type(clip['start']) is not int or type(clip['count']) is not int or clip['start'] < 0 or clip['count'] < 1 or
                not math.isfinite(clip['fps']) or clip['fps'] <= 0 or not 0 <= clip.get('loop_frames',0) <= clip['count'] or
                any(clip['start']+clip['count'] > read_md3(read(spec['path']))['frame_count'] for spec in affected_specs)):
            raise ValueError('Animation range outside source MD3 frames: '+clip['name'])
        frames = []
        indices = list(range(clip['start'], clip['start']+clip['count']))
        if clip.get('reverse'):
            indices.reverse()
        for frame in indices:
            sample_specs = [dict(spec) for spec in specs]
            for spec in sample_specs:
                affected = len(specs)==1 or clip['name'].startswith('BOTH_') and spec['name'] in ('lower','upper') or clip['name'].startswith('TORSO_') and spec['name']=='upper' or clip['name'].startswith('LEGS_') and spec['name']=='lower'
                if affected:
                    spec['frame'] = frame
            geometry = combine(build_parts(sample_specs, read), ExistingTextures())
            frames.append(dict(vertices=geometry['vertices'], normals=geometry['normals']))
        if frames:
            loop_frames = clip.get('loop_frames',0)
            partial_loop = 0 < loop_frames < len(frames)
            data['animation_clips'].append(dict(name=clip['name'], fps=clip['fps'], frames=frames, loop=bool(loop_frames) and not partial_loop,
                                               source_loop_frames=loop_frames))
            if partial_loop:
                data['animation_clips'].append(dict(name=clip['name']+'_LOOP', fps=clip['fps'], frames=frames[-loop_frames:], loop=True,
                                                   source_loop_frames=loop_frames))
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'local-data/q3')
    args = parser.parse_args()
    print(import_catalog(args.source, args.output))
