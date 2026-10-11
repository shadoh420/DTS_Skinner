"""Local RTCW static models. No retail data or engine source is bundled.

Game, read_model and Textures also serve placed models in a future map importer.
Format rules: iortcw qfiles.h, tr_model.c, tr_animation.c and cg_players.c.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile

try:
    from tools import import_q3 as q3
    from tools.import_q3_map import parse_shaders
except ImportError:
    import import_q3 as q3
    from import_q3_map import parse_shaders

SECTIONS = {'sp': 'Single player', 'mp': 'Multiplayer'}
MAX_BYTES = 256 * 1024 * 1024
MODEL_EXTS = ('.md3', '.mdc', '.mds')


def checked_name(name):
    name = q3.key_name(name)
    if not name or name.startswith('/') or ':' in name or '\0' in name or any(p in ('..', '.') for p in name.split('/')):
        raise ValueError('Invalid RTCW asset path: ' + name)
    return name


def read_asset(path, entry=None, max_bytes=MAX_BYTES):
    if entry is None:
        if path.stat().st_size > max_bytes:
            raise ValueError('Loose asset exceeds read limit')
        with path.open('rb') as stream:
            raw = stream.read(max_bytes + 1)
    else:
        if entry.file_size > max_bytes or entry.flag_bits & 1:
            raise ValueError('Oversized or encrypted PK3 member')
        with zipfile.ZipFile(path) as archive, archive.open(entry) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) != entry.file_size:
            raise ValueError('Truncated PK3 member')
    if len(raw) > max_bytes:
        raise ValueError('Asset exceeds read limit')
    return raw


class Game:
    """Lazy Main mounts, highest priority first; no open archives retained.

    FS_AddGameDirectory: SP packs precede loose; MP loose precedes packs.
    Both include pak0; opposite-mode prefixes are excluded. Own prefix sorts
    as zz_, then descending lexical order. All asset paths remain virtual.
    """
    def __init__(self, install, section='sp'):
        if section not in SECTIONS:
            raise ValueError('Unknown RTCW section')
        self.section = section
        root = Path(install).expanduser()
        root = root / 'Main' if (root / 'Main').is_dir() else root
        if not root.is_dir():
            raise ValueError('RTCW Main folder not found')
        self.assets, self.sources, self.issues = {}, [], []
        opposite = 'mp_' if section == 'sp' else 'sp_'
        packs = [p for p in root.iterdir() if p.suffix.lower() == '.pk3' and not p.name.lower().startswith(opposite)]
        if len(packs) > 1024:
            raise ValueError('Too many RTCW archives')
        def order(p):
            n = p.name.lower()
            return 'zz_' + n[3:] if n.startswith(section + '_') else n
        loose = {}
        for folder in ('models', 'textures', 'scripts', 'maps'):
            for path in sorted((root / folder).rglob('*')):
                if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                    name = checked_name(path.relative_to(root).as_posix())
                    loose[name] = dict(read=lambda max_bytes=MAX_BYTES, p=path: read_asset(p,max_bytes=max_bytes), source='loose')
        for path in sorted(packs, key=order, reverse=True):
            files = {}
            try:
                with zipfile.ZipFile(path) as archive:
                    if len(archive.infolist()) > 131072:
                        raise ValueError('Too many PK3 members')
                    for entry in archive.infolist():
                        if entry.is_dir():
                            continue
                        try:
                            name = checked_name(entry.filename)
                        except ValueError as exc:
                            self.issues.append(dict(source=path.name, asset=entry.filename, reason=str(exc)))
                            continue
                        # FS_LoadZipFile prepends hash entries: last duplicate wins.
                        files[name] = dict(read=lambda max_bytes=MAX_BYTES, p=path, e=entry: read_asset(p,e,max_bytes), source=path.name)
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                self.issues.append(dict(source=path.name, reason=str(exc)))
            self.sources.append((path.name, files))
        self.sources.insert(0 if section == 'mp' else len(self.sources), ('loose', loose))
        for _, files in self.sources:
            for name, entry in files.items():
                self.assets.setdefault(name, entry)
        if not self.assets:
            raise ValueError('No RTCW assets found')
        self.search_order = [label for label, _ in self.sources]

    def read(self, name):
        name = checked_name(name)
        if name not in self.assets:
            raise ValueError('Missing asset: ' + name)
        limit = 1024*1024 if name.endswith(('.skin','.cfg','.script')) else 8*1024*1024 if name.endswith('.shader') else MAX_BYTES
        return self.assets[name]['read'](max_bytes=limit)

    def model_path(self, name):
        name = checked_name(name)
        # Stock r_compressModels=0 asks for MDC first, then MD3.
        candidates = [name]
        if name.endswith(('.md3', '.mdc')):
            candidates = [name[:-4] + '.mdc', name[:-4] + '.md3']
        return next((n for n in candidates if n in self.assets), name)


def span(offset, count, stride, end, minimum=0):
    if offset < minimum or count < 0 or stride < 0 or offset + count * stride > end:
        raise ValueError('Model array outside declared bounds')


def string(raw):
    return raw.split(b'\0')[0].decode('latin1').lower()


def finite(values):
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Non-finite model value')
    return values


def axis(angles):
    """Pitch/yaw/roll in radians, returned as native basis rows."""
    p, y, r = angles
    cp, cy, cr = math.cos(p), math.cos(y), math.cos(r)
    sp, sy, sr = math.sin(p), math.sin(y), math.sin(r)
    return ((cp*cy, cp*sy, -sp), (sr*sp*cy-cr*sy, sr*sp*sy+cr*cy, sr*cp),
            (cr*sp*cy+sr*sy, cr*sp*sy-sr*cy, cr*cp))


def packed_normal(n):
    # RB_SurfaceMDC/MD3 use the 1024-entry renderer sine lookup (R_Init).
    a, b = (n >> 8)*4, (n & 255)*4
    sine = lambda i: math.sin((i & 1023)*math.tau/1023)
    return sine(a+256)*sine(b), sine(a)*sine(b), sine(b+256)


def mdc_normal(index):
    # Generate the 256 spherical samples mathematically; no copied normal table.
    for ring in [0, *range(-1, -8, -1), *range(1, 8)]:
        count = 32 - 4 * abs(ring)
        if index < count:
            latitude, longitude = ring * math.pi / 16, index * math.tau / count
            return math.cos(latitude)*math.cos(longitude), math.cos(latitude)*math.sin(longitude), math.sin(latitude)
        index -= count
    raise ValueError('Invalid MDC normal index')


def read_mdc(raw, frame=0):
    if len(raw) < 112 or raw[:4] != b'IDPC' or struct.unpack_from('<i', raw, 4)[0] != 2:
        raise ValueError('Expected MDC IDPC version 2')
    _, nf, nt, ns, _, of, on, ot, os, end = struct.unpack_from('<10i', raw, 72)
    if not (0 < nf <= 65536 and 0 <= nt <= 256 and 0 <= ns <= 256 and 112 <= end <= len(raw) and 0 <= frame < nf):
        raise ValueError('Invalid MDC header/frame')
    for off, count, size in ((of,nf,56), (on,nt,64), (ot,nf*nt,12)):
        span(off,count,size,end,112)
    tags = {}
    for i in range(nt):
        xyz = struct.unpack_from('<6h',raw,ot+(frame*nt+i)*12)
        tags[string(raw[on+i*64:on+(i+1)*64])] = (*[v/64 for v in xyz[:3]], *sum(axis([v*math.tau/32700 for v in xyz[3:]]), ()))
    surfaces = []
    offset = os
    for _ in range(ns):
        span(offset,1,124,end,112)
        name = string(raw[offset+4:offset+68])
        name = re.sub(r'_\d$', '', name)
        _, nc, nb, nsh, nv, ntri, tri, sh, uv, xyz, comp, bases, comps, size = struct.unpack_from('<14i',raw,offset+68)
        if not (0 <= nc <= nf and 0 < nb <= nf and 0 <= nsh <= 4096 and 0 < nv <= 65536 and 0 < ntri <= 131072 and size >= 124):
            raise ValueError('Invalid MDC surface counts')
        span(offset,1,size,end,112)
        for off,count,stride in ((tri,ntri,12),(sh,nsh,68),(uv,nv,8),(xyz,nv*nb,8),(comp,nv*nc,4),(bases,nf,2),(comps,nf,2)):
            span(off,count,stride,size,124)
        bf = struct.unpack_from('<h',raw,offset+bases+frame*2)[0]
        cf = struct.unpack_from('<h',raw,offset+comps+frame*2)[0]
        if not (0 <= bf < nb and -1 <= cf < nc):
            raise ValueError('MDC frame map outside surface')
        vertices, normals = [], []
        for i in range(nv):
            x,y,z,n = struct.unpack_from('<3hH',raw,offset+xyz+(bf*nv+i)*8)
            point, normal = [x/64,y/64,z/64], packed_normal(n)
            if cf >= 0:
                v = struct.unpack_from('<I',raw,offset+comp+(cf*nv+i)*4)[0]
                point = [p + (((v >> (j*8)) & 255)-127)*.05 for j,p in enumerate(point)]
                normal = mdc_normal(v >> 24)
            vertices.extend(point); normals.extend(normal)
        indices = list(struct.unpack_from('<'+'i'*(ntri*3),raw,offset+tri))
        if any(i < 0 or i >= nv for i in indices):
            raise ValueError('MDC triangle index outside surface')
        surfaces.append(dict(name=name,vertices=vertices,normals=normals,
            uvs=list(finite(struct.unpack_from('<'+'f'*(nv*2),raw,offset+uv))),indices=indices,
            shaders=[string(raw[offset+sh+i*68:offset+sh+i*68+64]) for i in range(nsh)]))
        offset += size
    return dict(surfaces=surfaces,tags=tags,frame_count=nf)


def read_mds(raw, frame=0):
    if len(raw) < 120 or raw[:4] != b'MDSW' or struct.unpack_from('<i',raw,4)[0] != 4:
        raise ValueError('Expected MDS MDSW version 4')
    nf, nb, of, ob, torso, ns, os, nt, ot, end = struct.unpack_from('<10i',raw,80)
    if not (0 < nf <= 65536 and 0 < nb <= 128 and 0 <= ns <= 32 and 0 <= nt <= 128 and 120 <= end <= len(raw) and 0 <= frame < nf and 0 <= torso < nb):
        raise ValueError('Invalid MDS header/frame')
    for off,count,size in ((of,nf,52+nb*12),(ob,nb,80),(ot,nt,72)):
        span(off,count,size,end,120)
    start = of + frame*(52+nb*12)
    root = finite(struct.unpack_from('<3f',raw,start+40))
    bones, active = {}, set()
    def bone(i):
        if i in bones: return bones[i]
        if i in active: raise ValueError('Cyclic MDS bone hierarchy')
        active.add(i)
        parent, weight, distance, flags = struct.unpack_from('<iffi',raw,ob+i*80+64)
        finite((weight,distance))
        if parent < -1 or parent >= nb or parent == i or distance < 0:
            raise ValueError('Invalid MDS parent bone')
        angles = struct.unpack_from('<6h',raw,start+52+i*12)
        mat = axis([a*math.tau/65536 for a in angles[:3]])
        pos = root
        if parent >= 0:
            parent_pos = bone(parent)[1]
            direction = axis([angles[4]*math.tau/65536,angles[5]*math.tau/65536,0])[0]
            pos = tuple(p+distance*d for p,d in zip(parent_pos,direction))
        bones[i] = mat,pos
        active.remove(i)
        return bones[i]
    for i in range(nb): bone(i)
    tags = {}
    for i in range(nt):
        at = ot+i*72
        bi = struct.unpack_from('<i',raw,at+68)[0]
        if not 0 <= bi < nb: raise ValueError('MDS tag bone outside skeleton')
        mat,pos = bone(bi)
        tags[string(raw[at:at+64])] = (*pos,*sum(mat,()))
    surfaces, offset = [], os
    for _ in range(ns):
        span(offset,1,176,end,120)
        name, shader = string(raw[offset+4:offset+68]),string(raw[offset+68:offset+132])
        _, _, back, nv, ov, ntri, tri, collapse, nr, refs, size = struct.unpack_from('<11i',raw,offset+132)
        if not (0 < nv <= 6000 and 0 < ntri <= 8192 and 0 <= nr <= nb and size >= 176 and back == -offset):
            raise ValueError('Invalid MDS surface counts/header offset')
        span(offset,1,size,end,120)
        for off,count,stride in ((tri,ntri,12),(collapse,nv,4),(refs,nr,4),(ov,nv,32)):
            span(off,count,stride,size,176)
        referenced = struct.unpack_from('<'+'i'*nr,raw,offset+refs)
        if any(b < 0 or b >= nb for b in referenced): raise ValueError('Invalid MDS bone reference')
        vertices, normals, uvs = [], [], []
        at = ov
        for _ in range(nv):
            span(at,1,32,size,176)
            normal = finite(struct.unpack_from('<3f',raw,offset+at))
            uvs.extend(finite(struct.unpack_from('<2f',raw,offset+at+12)))
            nw = struct.unpack_from('<i',raw,offset+at+20)[0]
            if not 0 < nw <= nb: raise ValueError('Invalid MDS weight count')
            span(at+32,nw,20,size,176)
            point, total = [0.,0.,0.], 0.
            for j in range(nw):
                bi,w,x,y,z = struct.unpack_from('<if3f',raw,offset+at+32+j*20)
                finite((w,x,y,z))
                if not 0 <= bi < nb or not 0 <= w <= 1: raise ValueError('Invalid MDS weight')
                mat,pos = bone(bi)
                for k in range(3): point[k] += w*(sum(a*b for a,b in zip(mat[k],(x,y,z)))+pos[k])
                if j == 0: normals.extend(sum(a*b for a,b in zip(row,normal)) for row in mat)
                total += w
            if abs(total-1) > .02: raise ValueError('MDS weights do not sum to one')
            vertices.extend(point)
            at += 32+nw*20
        indices = list(struct.unpack_from('<'+'i'*(ntri*3),raw,offset+tri))
        if any(i < 0 or i >= nv for i in indices): raise ValueError('MDS triangle index outside surface')
        surfaces.append(dict(name=name,shaders=[shader],vertices=vertices,normals=normals,uvs=uvs,indices=indices))
        offset += size
    return dict(surfaces=surfaces,tags=tags,frame_count=nf)


def read_model(raw, frame=0):
    if raw[:4] == b'IDP3':
        return q3.read_md3(raw,frame,normal_decoder=packed_normal)
    reader = {b'IDPC':read_mdc,b'MDSW':read_mds}.get(raw[:4])
    if not reader: raise ValueError('Unknown RTCW model magic')
    return reader(raw,frame)


def read_skin(raw):
    values = q3.skin_map(raw)
    if not values:
        raise ValueError('Skin contains no surface or model declarations')
    scale = float(values.pop('playerscale', '1'))
    if not math.isfinite(scale) or scale <= 0 or scale > 100:
        raise ValueError('Invalid skin playerscale')
    return dict(surfaces={k:v for k,v in values.items() if not k.startswith('md3_')},
                models={k:v for k,v in values.items() if k.startswith('md3_')}, scale=scale)


def shader_maps(game):
    # Original RTCW concatenates the FS script listing in reverse order and
    # accepts the first definition. Same-name files still use the winning mount.
    listed = list(dict.fromkeys(n for _, files in game.sources for n in files
                               if re.fullmatch(r'scripts/[^/]+\.shader', n)))
    result = {}
    for name in reversed(listed):
        try:
            definitions = parse_shaders(q3.read_text(game.read(name)))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            game.issues.append(dict(source=name, reason='Unreadable shader script: '+str(exc)))
            continue
        for shader, (body, stages) in definitions.items():
            if shader in result: continue
            # Reuse the Q3 first-stage material interpreter after RTCW precedence.
            text = shader+'\n{\n'+'\n'.join(' '.join(r) for r in body)+'\n'
            text += '\n'.join('{\n'+'\n'.join(' '.join(r) for r in s)+'\n}' for s in stages)+'\n}'
            parsed = q3.shader_maps({'one.shader': {'read':lambda t=text:t.encode()}})
            implicit = next((r for r in body if r[0] in ('implicitmap','implicitmask','implicitblend')), None)
            if shader in parsed:
                result[shader] = parsed[shader]
            else:
                result[shader] = dict(target=shader, settings=dict(shader=shader,alphaFunc='',blend=[],cull='back',depthWrite=True,clamp=False,tcGen='base'),limitations=[])
                if not implicit:
                    result[shader]['limitations'].append('shader has no usable image stage; shader-name fallback')
            if implicit and shader not in parsed:
                entry = result[shader]
                entry['target'] = implicit[1].lower() if len(implicit)>1 and implicit[1] != '-' else shader
                if implicit[0] == 'implicitmask': entry['settings'].update(alphaFunc='GE128',cull='none')
                if implicit[0] == 'implicitblend': entry['settings'].update(blend=['gl_src_alpha','gl_one_minus_src_alpha'],depthWrite=False,cull='none')
                entry['limitations'].append('implicit directive compatibility extension (absent from supplied iortcw renderer)')
    return result


class Textures(q3.Textures):
    def __init__(self, game, output):
        super().__init__(game.assets, Path(output), shaders=shader_maps(game), prefix=game.section+'_',
                         image_extensions=('.tga','.jpg','.jpeg','.png','.pcx','.bmp'))
        self.results = {}

    def resolve(self, shader):
        result = super().resolve(shader)
        filename, warnings, _ = result
        self.results[shader] = dict(shader=shader,texture=filename,
            status='resolved' if (self.output/filename).is_file() else 'unresolved',warnings=warnings)
        return result


def idle_pose(game, folder):
    name = folder+'/wolfanim.cfg'
    if name not in game.assets:
        return 0, 'Frame 0: no wolfanim.cfg'
    text = re.sub(r'/\*.*?\*/|//[^\r\n]*','',q3.read_text(game.read(name)),flags=re.S)
    rows = re.findall(r'^\s*([\w]+)\s+(\d+)\s+(-?\d+)\s+\d+\s+[\d.]+',text,re.M)
    frames = {n.lower():int(f) for n,f,_ in rows}
    script = folder+'/wolfanim.script'
    if script in game.assets:
        text = re.sub(r'/\*.*?\*/|//[^\r\n]*','',q3.read_text(game.read(script)),flags=re.S).lower()
        def block(text, label):
            match = re.search(r'\b'+label+r'\s*\{',text)
            if not match: return ''
            depth, start = 1, match.end()
            for i in range(start,len(text)):
                depth += (text[i]=='{') - (text[i]=='}')
                if depth == 0: return text[start:i]
            return ''
        idle = block(block(text,r'state\s+relaxed'),'idle')
        choice = block(idle,r'weapons\s+none') or block(idle,'default')
        match = re.search(r'\bboth\s+(\w+)',choice)
        if match and match[1] in frames:
            n = match[1]
            return frames[n], f'{script}: relaxed idle, unarmed/default both {n}; {name} first frame {frames[n]}'
    candidates = [(n,int(f)) for n,f,_ in rows if re.search(r'(^|_)(idle|stand)(_|$)',n,re.I)]
    candidates.sort(key=lambda r:(r[0].lower() not in ('idle','stand'),not r[0].lower().startswith('relaxed')))
    if candidates:
        n,f = candidates[0]
        return f, f'{name}: named idle/stand fallback {n}, first frame {f}; no unambiguous whole-body script default'
    return 0, 'Frame 0: no named idle/stand in wolfanim.cfg'


ACCESSORIES = {'md3_beltl':'tag_bleft','md3_beltr':'tag_bright','md3_belt':'tag_ubelt',
               'md3_back':'tag_back','md3_weapon':'tag_weapon','md3_weapon2':'tag_weapon2',
               'md3_hat':'tag_mouth','md3_hat2':'tag_mouth','md3_hat3':'tag_mouth'}


def character_parts(game, folder, body_skin, head_skin):
    parts, warnings, used = [], [], []
    def skin(name):
        if not name: return dict(surfaces={},models={},scale=1.)
        used.append(name)
        return read_skin(game.read(name))
    bs, hs = skin(body_skin), skin(head_skin)
    frame, pose = idle_pose(game,folder)
    def part(path, sk, tags=(), at=0):
        path = game.model_path(path)
        raw = game.read(path)
        model = read_model(raw,at)
        if sk['surfaces']:
            for surface in model['surfaces']:
                if surface['name'] not in sk['surfaces']:
                    warnings.append(f'{path}: skin has no {surface["name"]} mapping; embedded-shader fallback')
        used.append(path)
        item = dict(name=path,model=model,skin=sk['surfaces'],tags=tags,
                    digest=hashlib.sha256(raw).hexdigest(),frame=at)
        parts.append(item)
        return model
    body = part(folder+'/'+bs['models'].get('md3_part','body.mds'),bs,at=frame)
    head = None
    head_chain = ()
    if head_skin or game.model_path(folder+'/head.md3') in game.assets:
        try:
            if 'tag_head' not in body['tags']: raise ValueError('Body has no tag_head')
            head_chain = (body['tags']['tag_head'],)
            head = part(folder+'/'+hs['models'].get('md3_part','head.md3'),hs,head_chain)
            if not head_skin: warnings.append('Missing head skin: using embedded shaders')
        except (ValueError,OSError,zipfile.BadZipFile) as exc:
            warnings.append('Head: '+str(exc))
    for sk,parent,chain in ((bs,body,()),(hs,head,head_chain)):
        for key,value in sk['models'].items():
            if key == 'md3_part': continue
            if key not in ACCESSORIES:
                warnings.append(f'{key}: conditional animation-script part not shown ({value})')
                continue
            try:
                tag = ACCESSORIES[key]
                if parent is None or tag not in parent['tags']: raise ValueError('Missing attachment tag '+tag)
                accskin = dict(surfaces={},models={},scale=1.)
                path = folder+'/'+value
                if not value.endswith(MODEL_EXTS):
                    skpath = path+'.skin'
                    if skpath in game.assets:
                        accskin = skin(skpath)
                        path = folder+'/acc/'+accskin['models']['md3_part'] if 'md3_part' in accskin['models'] else path+'.md3'
                    else:
                        warnings.append('Missing accessory skin '+skpath+'; trying embedded shaders')
                        path += '.md3'
                part(path,accskin,(parent['tags'][tag],*chain))
            except (ValueError,OSError,zipfile.BadZipFile) as exc:
                warnings.append(f'{key} {value}: {exc}')
    if not body_skin: warnings.append('Missing body skin: using embedded shaders')
    return parts, dict(pose=pose,frame=frame,scale=bs['scale'],warnings=warnings,used=used)


def category(name):
    n = name.lower()
    if '/gibs/' in n or '/gib' in n: return 'Gibs'
    if '/players/' in n: return 'Character parts'
    if '/shards/' in n: return 'Debris'
    if '/weaphits/' in n: return 'Weapon effects'
    if '/multiplayer/' in n:
        folder = n.split('/multiplayer/',1)[1].split('/')[0]
        if folder in ('uboat','transport'): return 'Vehicles'
        if folder in ('gold','treasure','secretdocs','medpack','ammopack'): return 'Items and powerups'
        if folder == 'flagpole': return 'Map objects'
        return 'Weapons and equipment'
    if '/weapons' in n or '/ammo/' in n: return 'Weapons and ammunition'
    if '/powerups/' in n or '/items/' in n or '/flags/' in n or '/mapobjects/health' in n: return 'Items and powerups'
    if any(s in n for s in ('/vehicles/','/tank','/truck','/plane','/airplane','/jeep')): return 'Vehicles'
    if '/mapobjects/' in n or '/furniture/' in n: return 'Map objects'
    return 'Other models'


def model_specs(game):
    """One character for each body/head suffix, pairing default for absent mates.

    Head and body skins can be chosen independently in game; avoid inventing a
    Cartesian product. Raw parts remain available and carry their own provenance.
    """
    names = sorted(n for n in game.assets if n.endswith(MODEL_EXTS))
    specs = []
    for name in names:
        if name.endswith('/body.mds') and name.startswith('models/players/'):
            folder = name.rsplit('/',1)[0]
            skins = {n for n in game.assets if n.startswith(folder+'/') and n.endswith('.skin')}
            suffixes = sorted({PurePosixPath(n).stem.split('_',1)[1] for n in skins
                               if re.fullmatch(re.escape(folder)+r'/(body|head)_[^/]+\.skin',n)}) or ['default']
            for suffix in suffixes:
                chosen, warnings = [], []
                for kind in ('body','head'):
                    wanted = folder+'/'+kind+'_'+suffix+'.skin'
                    fallback = folder+'/'+kind+'_default.skin'
                    found = wanted if wanted in skins else fallback if fallback in skins else ''
                    if wanted != found: warnings.append(f'{wanted} absent; '+(f'using {found}' if found else 'embedded-shader fallback'))
                    chosen.append(found)
                specs.append(dict(source=name,variant=suffix,folder=folder,body_skin=chosen[0],head_skin=chosen[1],warnings=warnings,category='Characters'))
        else:
            specs.append(dict(source=name,variant='',category=category(name),warnings=[]))
    return specs


def import_catalog(install, output, model_filter=None):
    output = Path(output)
    (output/'model_json').mkdir(parents=True,exist_ok=True)
    catalog, results, sections, skin_results, texture_results = [], [], {}, [], []
    for section,label in SECTIONS.items():
        game = Game(install,section)
        resolver = Textures(game,output/'textures')
        seen = {}
        for spec in model_specs(game):
            name = spec['source']
            if model_filter and model_filter.lower() not in name: continue
            ident = section+'_'+q3.asset_name(name+'#'+spec['variant'])
            row = dict(section=section,source=name,variant=spec['variant'],model=ident)
            display = PurePosixPath(spec.get('folder',name)).name
            item = dict(game='rtcw',model_name=ident,display_name=label+': '+display+(' / '+spec['variant'] if spec['variant'] else ''),
                        category=label+': '+spec['category'],source=game.assets[name]['source']+':'+name,texture_name='',status='ready')
            try:
                if 'folder' in spec:
                    parts, metadata = character_parts(game,spec['folder'],spec['body_skin'],spec['head_skin'])
                else:
                    raw = game.read(name)
                    parts = [dict(name=name,model=read_model(raw),digest=hashlib.sha256(raw).hexdigest(),frame=0)]
                    adjacent = name[:-4]+'.skin'
                    if adjacent in game.assets: parts[0]['skin'] = read_skin(game.read(adjacent))['surfaces']
                    metadata = dict(pose='Frame 0',frame=0,scale=1,warnings=[],used=[name])
                data = q3.combine(parts,resolver)
                scale = metadata['scale']
                data['vertices'] = [x*scale for x in data['vertices']]
                data['game'] = 'rtcw'
                data['warnings'] = sorted(set(data['warnings']+metadata['warnings']+spec['warnings']))
                metadata.update(section=section,source=item['source'],warnings=data['warnings'])
                data['metadata'] = metadata
                identity = json.dumps([[p['digest'],p['frame']] for p in parts])+json.dumps(
                    [data['vertices'],data['material_textures'],data['material_settings']],sort_keys=True)
                digest = hashlib.sha256(identity.encode()).hexdigest()
                if digest in seen:
                    row.update(status='duplicate',model=seen[digest],reason='Byte-identical parts and identical pose/materials')
                    results.append(row)
                    continue
                seen[digest] = ident
                q3.json_write(output/'model_json'/(ident+'.json'),data)
                item.update(texture_name=data['material_textures'][0],warnings=data['warnings'])
                row.update(status='ready',metadata=metadata,triangles=len(data['indices'])//3)
            except (OSError,ValueError,zipfile.BadZipFile,struct.error) as exc:
                row.update(status='skipped',reason=str(exc))
                item.update(status='skipped',reason=str(exc))
            results.append(row); catalog.append(item)
        # Audit every skin, even ones whose geometry failed or has no body pair.
        for name in sorted(n for n in game.assets if n.endswith('.skin')):
            if model_filter and model_filter.lower() not in name: continue
            row = dict(section=section,source=name)
            try:
                sk = read_skin(game.read(name))
                refs = []
                for surface,shader in sk['surfaces'].items():
                    if shader in ('off','*off','nodraw'):
                        refs.append(dict(surface=surface,shader=shader,status='hidden'))
                    else:
                        resolver.resolve(shader)
                        refs.append(dict(surface=surface,**resolver.results[shader]))
                row.update(status='read',surfaces=refs,models=sk['models'])
            except (ValueError,OSError,zipfile.BadZipFile) as exc:
                row.update(status='unreadable',reason=str(exc))
            skin_results.append(row)
        texture_results.extend(dict(section=section,**r) for r in resolver.results.values())
        sections[section] = dict(search_order=game.search_order,issues=game.issues,
            models=dict(Counter(category(n) for n in game.assets if n.endswith(MODEL_EXTS))))
    report = dict(entries=len(catalog),ready=sum(r['status']=='ready' for r in results),
        skipped=sum(r['status']=='skipped' for r in results),duplicates=sum(r['status']=='duplicate' for r in results),
        textures=sum(r['status']=='resolved' for r in texture_results),results=results,sections=sections,
        skin_results=skin_results,texture_results=texture_results,
        skin_resolution=dict(Counter(r['status'] for s in skin_results for r in s.get('surfaces',[]))),
        skin_file_status=dict(Counter(r['status'] for r in skin_results)),
        unresolved_skin_reasons=dict(Counter(w for r in texture_results if r['status']=='unresolved' for w in r['warnings'])))
    q3.json_write(output/'catalog.json',catalog)
    q3.json_write(output/'import-report.json',report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True,help='RTCW install or Main folder')
    parser.add_argument('--output',type=Path,default=q3.LOCAL_DATA/'rtcw')
    parser.add_argument('--filter',help='Optional model/skin path substring')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install,args.output,args.filter),indent=1))
