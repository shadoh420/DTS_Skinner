"""Doom 3 static mesh readers. Coordinates returned here are native Doom units.

Format behavior follows id Software's neo/renderer/Model_{md5,ma,ase}.cpp
and Model.cpp. Maya script nodes are data only; no MEL is ever executed.
"""
import math
import re
import struct
from collections import defaultdict

import numpy as np

if __package__:
    from . import import_quake_md5 as md5
else:
    import import_quake_md5 as md5


def doom_quaternion(xyz):
    # idQuat::CalcW is sqrt(abs(1 - xyz.xyz)), NOT clamp or normalization.
    # Negate W for the Hamilton convention of the shared MD5 reader.
    return (*xyz, -math.sqrt(abs(1 - sum(x*x for x in xyz))))


def doom_rotate(q, p):
    x, y, z, w = q
    return (p[0]*(1-2*y*y-2*z*z) + p[1]*(2*x*y-2*z*w) + p[2]*(2*x*z+2*y*w),
            p[0]*(2*x*y+2*z*w) + p[1]*(1-2*x*x-2*z*z) + p[2]*(2*y*z-2*x*w),
            p[0]*(2*x*z-2*y*w) + p[1]*(2*y*z+2*x*w) + p[2]*(1-2*x*x-2*y*y))


def unit(v):
    v = np.asarray(v, dtype=float)
    length = np.linalg.norm(v)
    return v / length if length else v


def triangulate(points):
    """Ear clipping preserves concave polygon boundaries and corner attributes."""
    if len(points) < 3:
        return []
    if len(points) == 3:
        return [(0, 1, 2)]
    p = np.asarray(points)
    normal = sum((np.cross(p[i], p[(i+1) % len(p)]) for i in range(len(p))), np.zeros(3))
    p = np.delete(p, np.argmax(np.abs(normal)), axis=1)
    def cross(a, b, c):
        u, v = b-a, c-a
        return u[0]*v[1]-u[1]*v[0]
    direction = 1 if sum(cross(np.zeros(2), p[i], p[(i+1) % len(p)]) for i in range(len(p))) >= 0 else -1
    remaining, result = list(range(len(p))), []
    while len(remaining) > 3:
        for k, b in enumerate(remaining):
            a, c = remaining[k-1], remaining[(k+1) % len(remaining)]
            if direction*cross(p[a], p[b], p[c]) <= 1e-10:
                continue
            if any(all(direction*cross(p[x], p[y], p[j]) >= -1e-10 for x, y in ((a,b),(b,c),(c,a)))
                   for j in remaining if j not in (a,b,c)):
                continue
            result.append((a,b,c)); remaining.pop(k)
            break
        else:
            raise ValueError('Degenerate or self-intersecting polygon')
    result.append(tuple(remaining))
    return result


def surface(name):
    return dict(shader=name, vertices=[], uvs=[], normals=[], colors=[], indices=[])


def polygon(out, positions, uvs=None, normals=None, colors=None, reverse=False):
    if len(positions) < 3:
        return
    triangles = triangulate(positions)
    if reverse:
        triangles = [(a,c,b) for a,b,c in triangles]
    if normals is None:
        a,b,c = triangles[0]
        normal = unit(np.cross(np.subtract(positions[b], positions[a]), np.subtract(positions[c], positions[a])))
        normals = [normal]*len(positions)
    offset = len(out['vertices'])
    out['vertices'].extend(positions)
    out['uvs'].extend(uvs if uvs is not None else [(0,0)]*len(positions))
    out['normals'].extend(normals)
    out['colors'].extend(colors if colors is not None else [(1,1,1,1)]*len(positions))
    out['indices'].extend(offset+i for tri in triangles for i in tri)


def joint_matrices(joints):
    result=[]
    for joint in joints:
        matrix=np.eye(4)
        matrix[:3,:3]=np.array([doom_rotate(joint['orientation'],axis) for axis in np.eye(3)]).T
        matrix[:3,3]=joint['position']; result.append(matrix)
    return result


def copy_head_joints(head, body, spawnargs, head_origin):
    """Actor::CopyJointsFromBodyToHead, evaluated once for the resting pose."""
    body_world,head_world=joint_matrices(body),joint_matrices(head)
    def local(joints,world,i):
        parent=joints[i]['parent']
        return np.linalg.inv(world[parent]) @ world[i] if parent>=0 else world[i]
    locals_=[local(head,head_world,i) for i in range(len(head))]
    overrides={}
    body_names={j['name'].lower():i for i,j in enumerate(body)}
    head_names={j['name'].lower():i for i,j in enumerate(head)}
    for key,target in spawnargs.items():
        if not key.startswith(('copy_joint ','copy_joint_world ')): continue
        kind,source=key.split(' ',1)
        if source.lower() not in body_names or target.lower() not in head_names: continue
        src,dst=body_names[source.lower()],head_names[target.lower()]
        if kind=='copy_joint': locals_[dst]=local(body,body_world,src)
        else:
            overrides[dst]=body_world[src].copy(); overrides[dst][:3,3]-=head_origin
    result={}
    def resolve(i,stack=()):
        if i in result: return result[i]
        if i in stack: raise ValueError('Cyclic head skeleton')
        parent=head[i]['parent']
        result[i]=overrides[i] if i in overrides else resolve(parent,(*stack,i)) @ locals_[i] if parent>=0 else locals_[i]
        return result[i]
    return [resolve(i) for i in range(len(head))]


def read_md5(data, animation=None, pose_callback=None):
    model = md5.read_mesh(data, quaternion_fn=doom_quaternion)
    pose = md5.read_anim(animation, model['joints'], quaternion_fn=doom_quaternion,
                         rotate_fn=doom_rotate) if animation else None
    joints = pose['joints'] if pose else model['joints']
    matrices=pose_callback(joints) if pose_callback else None
    parts = []
    for part in model['meshes']:
        out = surface(part['shader'])
        if matrices is None:
            out['vertices'] = md5.skin_points(part, joints, rotate_fn=doom_rotate)
        else:
            out['vertices'] = [sum((bias*(matrices[j] @ np.r_[point,1])[:3] for j,bias,point in part['weights'][start:start+count]),np.zeros(3))
                               for _,start,count in part['verts']]
        out['uvs'] = [v[0] for v in part['verts']]
        out['indices'] = [i for a,b,c in part['triangles'] for i in (a,c,b)]
        normals = np.zeros((len(out['vertices']),3))
        for a,b,c in np.asarray(out['indices']).reshape(-1,3):
            n = np.cross(np.subtract(out['vertices'][b], out['vertices'][a]),
                         np.subtract(out['vertices'][c], out['vertices'][a]))
            for i in (a,b,c): normals[i] += n
        out['normals'] = [unit(n) for n in normals]
        out['colors'] = [(1,1,1,1)]*len(normals)
        parts.append(out)
    return parts, joints


def chunks(data, start=0, short=False):
    header = 6 if short else 8
    while start < len(data):
        if start+header > len(data): raise ValueError('Truncated LWO chunk')
        name = data[start:start+4]
        size = struct.unpack_from('>H' if short else '>I', data, start+4)[0]
        end = start+header+size
        if end > len(data): raise ValueError('Truncated LWO payload')
        yield name, data[start+header:end]
        start = end+(size & 1)


def s0(data, at):
    end = data.index(b'\0', at)
    return data[at:end].decode('latin1'), end+1+((end-at+1) & 1)


def vx(data, at):
    if data[at] == 255:
        return int.from_bytes(data[at+1:at+4], 'big'), at+4
    return struct.unpack_from('>H', data, at)[0], at+2


def read_lwo(data):
    if data[:4] != b'FORM' or data[8:12] != b'LWO2': raise ValueError('Expected LWO2')
    if struct.unpack_from('>I', data, 4)[0]+8 != len(data): raise ValueError('Invalid LWO FORM size')
    tags, layers, surfaces = [], [], {}
    layer = None
    for kind, body in chunks(data, 12):
        if kind == b'TAGS':
            at = 0
            while at < len(body):
                name, at = s0(body, at); tags.append(name)
        elif kind == b'SURF':
            name, at = s0(body, 0); _, at = s0(body, at)
            properties = dict(smooth=0., uv=None)
            for sub, value in chunks(body, at, True):
                if sub == b'SMAN': properties['smooth'] = struct.unpack_from('>f', value)[0]
                if sub == b'BLOK':
                    # VMAP is a direct BLOK subchunk, following the IMAP header.
                    for bk, bv in chunks(value, short=True):
                        if bk == b'VMAP': properties['uv'] = s0(bv,0)[0]
            surfaces[name] = properties
        elif kind == b'LAYR':
            layer = dict(points=[], polys=[], tags={}, maps={}, seams={})
            layers.append(layer)
        elif kind in (b'PNTS', b'POLS', b'PTAG', b'VMAP', b'VMAD'):
            if layer is None:
                layer = dict(points=[], polys=[], tags={}, maps={}, seams={}); layers.append(layer)
            if kind == b'PNTS':
                layer['points'] = [(x,z,y) for x,y,z in struct.iter_unpack('>fff',body)]
            elif kind == b'POLS':
                if body[:4] not in (b'FACE', b'PTCH'): continue
                at = 4
                while at < len(body):
                    count = struct.unpack_from('>H', body, at)[0] & 1023; at += 2
                    poly = []
                    for _ in range(count):
                        v,at = vx(body,at); poly.append(v)
                    layer['polys'].append(poly)
            elif kind == b'PTAG' and body[:4] == b'SURF':
                at = 4
                while at < len(body):
                    p,at = vx(body,at); layer['tags'][p] = struct.unpack_from('>H',body,at)[0]; at += 2
            elif kind in (b'VMAP',b'VMAD'):
                typ, size = body[:4], struct.unpack_from('>H',body,4)[0]
                name,at = s0(body,6)
                target = layer['seams' if kind == b'VMAD' else 'maps'].setdefault((typ,name),{})
                while at < len(body):
                    v,at = vx(body,at)
                    if kind == b'VMAD':
                        p,at = vx(body,at); v = (p,v)
                    target[v] = struct.unpack_from('>'+('f'*size),body,at); at += size*4
    result = {}
    for layer in layers:
        points, polys = layer['points'], layer['polys']
        normals, adjacent = [], defaultdict(list)
        for p, ids in enumerate(polys):
            normal = -unit(sum((np.cross(points[ids[i]], points[ids[(i+1)%len(ids)]]) for i in range(len(ids))),np.zeros(3)))
            normals.append(normal)
            for v in ids: adjacent[v].append(p)
        for p, ids in enumerate(polys):
            if len(ids) < 3: continue
            name = tags[layer['tags'].get(p,0)] if tags else '_default'
            props = surfaces.get(name,{})
            keys = list(dict.fromkeys([*layer['maps'],*layer['seams']]))
            uvkey = next((k for k in keys if k[0] == b'TXUV' and k[1] == props.get('uv')),None)
            uvkey = uvkey or next((k for k in keys if k[0] == b'TXUV'),None)
            colkey = next((k for k in keys if k[0] in (b'RGBA',b'RGB ')),None)
            def attr(key, v, default):
                return layer['seams'].get(key,{}).get((p,v), layer['maps'].get(key,{}).get(v,default))
            uvs = [(u,1-v) for u,v in (attr(uvkey,v,(0,0))[:2] for v in ids)]
            colors = [tuple(attr(colkey,v,(1,1,1,1))) for v in ids]
            colors = [c if len(c)==4 else (*c,1) for c in colors]
            smooth = props.get('smooth',0.)
            ns = [unit(sum((normals[q] for q in adjacent[v] if np.dot(normals[p],normals[q]) >= math.cos(smooth)-1e-6),np.zeros(3)))
                  if smooth else normals[p] for v in ids]
            polygon(result.setdefault(name,surface(name)), [points[v] for v in ids],uvs,ns,colors,reverse=True)
    return list(result.values())


def asset_path(value):
    value = value.lower().replace('\\','/')
    match = re.search(r'(?:^|/)((?:textures|models|guis|lights)/.*)',value)
    value = match[1] if match else value
    return re.sub(r'\.(?:tga|dds|png)$','',value)


def brace_blocks(text, keyword):
    for m in re.finditer(r'\*'+keyword+r'\b([^{}]*)\{',text):
        at, depth = m.end(), 1
        for t in re.finditer(r'"[^"\n]*"|[{}]',text[at:]):
            if t[0] == '{': depth += 1
            elif t[0] == '}': depth -= 1
            if depth == 0:
                yield m[1].strip(),text[at:at+t.start()]; break
        else: raise ValueError('Unclosed ASE block')


def read_ase(data):
    text = data.decode('latin1')
    if not re.search(r'\*3DSMAX_ASCIIEXPORT\s+200\b',text): raise ValueError('Expected ASE 200')
    materials = {}
    for idx,body in brace_blocks(text,'MATERIAL'):
        children = list(brace_blocks(body,'SUBMATERIAL'))
        for sub,b in children or [('',body)]:
            bitmap = re.search(r'\*BITMAP\s+"([^"]+)"',b)
            label = re.search(r'\*MATERIAL_NAME\s+"([^"]+)"',b)
            name = asset_path(bitmap[1] if bitmap else label[1] if label else '_default')
            def value(k,default):
                m = re.search(r'\*UVW_'+k+r'\s+([-+\deE.]+)',b)
                return float(m[1]) if m else default
            materials[(int(idx),int(sub or 0))] = (name,[value('U_OFFSET',0),value('V_OFFSET',0),value('U_TILING',1),value('V_TILING',1),value('ANGLE',0)])
    result = {}
    for _,body in brace_blocks(text,'GEOMOBJECT'):
        def vectors(k):
            return {int(m[1]):tuple(map(float,m[2].split())) for m in re.finditer(r'\*'+k+r'\s+(\d+)\s+([^\r\n*{}]+)',body)}
        positions,uvs,colors = vectors('MESH_VERTEX'),vectors('MESH_TVERT'),vectors('MESH_VERTCOL')
        tf,cf = vectors('MESH_TFACE'),vectors('MESH_CFACE')
        ref = re.search(r'\*MATERIAL_REF\s+(\d+)',body); ref = int(ref[1]) if ref else 0
        # ASE positions are already in world space, but normals are object-local.
        transform = np.eye(3)
        for axis in range(3):
            tm = re.search(r'\*TM_ROW'+str(axis)+r'\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)',body)
            if tm: transform[axis] = list(map(float,tm.groups()))
        normals,face = defaultdict(dict),None
        for m in re.finditer(r'\*MESH_(FACENORMAL|VERTEXNORMAL)\s+(\d+)\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)',body):
            if m[1] == 'FACENORMAL': face = int(m[2])
            else: normals[face][int(m[2])] = tuple(unit(np.array(list(map(float,m.group(3,4,5)))) @ transform))
        for m in re.finditer(r'\*MESH_FACE\s+(\d+):\s*A:\s*(\d+)\s*B:\s*(\d+)\s*C:\s*(\d+)([^\r\n]*)',body):
            face = int(m[1]); ids = list(map(int,m.group(2,3,4)))
            sub = re.search(r'\*MESH_MTLID\s+(\d+)',m[5]); sub = int(sub[1]) if sub else 0
            name,transform = materials.get((ref,sub),materials.get((ref,0),('_default',[0,0,1,1,0])))
            uo,vo,ut,vt,angle = transform
            uv = []
            for i in tf.get(face,(0,0,0)):
                u,v = uvs.get(int(i),(0,0))[:2]; u,v = u*ut-uo,(1-v)*vt+vo
                uv.append((u*math.cos(angle)+v*math.sin(angle),-u*math.sin(angle)+v*math.cos(angle)))
            col = [(*colors.get(int(i),(1,1,1))[:3],1) for i in cf.get(face,(0,0,0))]
            # Doom reverses ASE faces to CW; keep the original order for Three CCW.
            polygon(result.setdefault(name,surface(name)),[positions[v] for v in ids],uv,
                    [normals[face][v] for v in ids] if all(v in normals[face] for v in ids) else None,col)
    return list(result.values())


def read_ma(data):
    text = re.sub(r'//[^\n]*','',data.decode('latin1'))
    nodes,connections,current = {},[],None
    for command in re.findall(r'(?:"(?:\\.|[^"\\])*"|[^;"])+;',text):
        tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s;]+',command)
        if not tokens: continue
        plain = [t[1:-1] if t.startswith('"') else t for t in tokens]
        if plain[0] == 'createNode':
            name = plain[plain.index('-n')+1]
            current = dict(kind=plain[1],parent=plain[plain.index('-p')+1] if '-p' in plain else None,attrs={})
            nodes[name] = current
        elif plain[0] == 'connectAttr':
            args = [t for t in plain[1:] if not t.startswith('-')]
            if len(args)>=2: connections.append(args[:2])
        elif plain[0] == 'setAttr' and current is not None:
            at = next((i for i,t in enumerate(plain) if t.startswith('.')),None)
            if at is None: continue
            values = plain[at+1:]
            if '-type' in values:
                k = values.index('-type'); values = values[:k]+values[k+2:]
            current['attrs'][plain[at]] = values
    def arrays(node,pattern,width):
        result = {}
        for key,values in node['attrs'].items():
            match = re.fullmatch(pattern+r'\[(\d+)(?::(\d+))?\]',key)
            if match:
                first,last = int(match[1]),int(match[2] or match[1])
                vals = list(map(float,values))
                if len(vals)!=(last-first+1)*width: raise ValueError('Invalid Maya array '+key)
                result.update((first+i,tuple(vals[i*width:(i+1)*width])) for i in range(last-first+1))
        return result
    def point(p): return np.array((p[0],-p[2],p[1]))
    def transform(node):
        total,seen = np.eye(4),set()
        name = node['parent']
        while name in nodes:
            if name in seen: raise ValueError('Cyclic Maya transform')
            seen.add(name); parent = nodes[name]; attrs = parent['attrs']
            t = point(list(map(float,attrs.get('.t',(0,0,0)))))
            r = list(map(float,attrs.get('.r',(0,0,0))))
            sc = list(map(float,attrs.get('.s',(1,1,1)))); sc = (sc[0],sc[2],sc[1])
            matrix = np.diag([*sc,1.])
            # Maya XYZ rotation, expressed in Doom's (x,-z,y) basis.
            for axis,degrees in ((0,r[0]),(2,r[1]),(1,-r[2])):
                c,s = math.cos(math.radians(degrees)),math.sin(math.radians(degrees))
                rot = np.eye(4); a,b = ((1,2),(2,0),(0,1))[axis]
                rot[a,a]=rot[b,b]=c; rot[a,b]=-s; rot[b,a]=s
                matrix = rot @ matrix
            matrix[:3,3] = t; total = matrix @ total; name = parent['parent']
        return total
    def shader(group):
        seen = set()
        while group in nodes and group not in seen:
            seen.add(group); node = nodes[group]
            if node['kind'] == 'file': return asset_path(' '.join(node['attrs'].get('.ftn',['_default'])))
            incoming = [src.split('.')[0] for src,dst in connections if dst.split('.')[0]==group and (dst.endswith('.ss') or dst.endswith('.c'))]
            if not incoming: break
            group = incoming[0]
        return '_default'
    result = {}
    for name,node in nodes.items():
        if node['kind']!='mesh': continue
        vertices = arrays(node,r'\.vt',3); tweaks = arrays(node,r'\.pt',3); edges = arrays(node,r'\.ed',3)
        uv = arrays(node,r'\.uvst\[0\]\.uvsp',2); colors = arrays(node,r'\.clr',4); normal = arrays(node,r'\.n',3)
        if not vertices: continue
        matrix = transform(node); nmatrix = np.linalg.inv(matrix[:3,:3]).T
        positions = {i:(matrix @ np.r_[point(np.add(v,tweaks.get(i,(0,0,0)))),1])[:3] for i,v in vertices.items()}
        bindings = [(src,dst.split('.')[0]) for src,dst in connections if src.split('.')[0]==name and '.iog' in src]
        default = shader(bindings[0][1]) if bindings else '_default'
        face_materials = {}
        for src,group in bindings:
            obj = re.search(r'\.og\[(\d+)\]',src)
            if not obj: continue
            key = '.iog[0].og['+obj[1]+'].gcl'
            for value in node['attrs'].get(key,[]):
                match = re.fullmatch(r'f\[(\d+)(?::(\d+))?\]',value)
                if match:
                    for f in range(int(match[1]),int(match[2] or match[1])+1): face_materials[f]=shader(group)
        faces = {}
        for key,values in node['attrs'].items():
            match = re.fullmatch(r'\.fc\[(\d+)(?::\d+)?\]',key)
            if not match: continue
            i,f = 0,int(match[1])-1
            while i<len(values):
                typ = values[i]; i+=1
                if typ=='mu': uvset=int(values[i]); i+=1
                count=int(values[i]); i+=1
                ids=list(map(int,values[i:i+count])); i+=count
                if typ=='f': f+=1; faces[f]={'edges':ids}
                elif typ in ('mf','mu') and (typ=='mf' or uvset==0): faces[f]['uv']=ids
                elif typ=='fc': faces[f]['color']=ids
                else: raise ValueError('Unsupported Maya face component '+typ)
        next_normal,shared = 0,{}
        for f,face in sorted(faces.items()):
            ids = [int(edges[-e-1][1] if e<0 else edges[e][0]) for e in face['edges']]
            ns=[]
            for v in ids:
                smooth=any(edges[abs(e)-1 if e<0 else e][2]==1 and v in edges[abs(e)-1 if e<0 else e][:2] for e in face['edges'])
                if normal:
                    if smooth and v in shared: n=shared[v]
                    else:
                        if next_normal not in normal: raise ValueError('Invalid Maya normal index')
                        n=unit(nmatrix @ point(normal[next_normal])); next_normal+=1
                    ns.append(n); shared[v]=n
            material=face_materials.get(f,default)
            polygon(result.setdefault(material,surface(material)),[positions[i] for i in ids],
                    [(uv[i][0],1-uv[i][1]) for i in face['uv']] if 'uv' in face else None,
                    ns or None,[colors[i] for i in face['color']] if 'color' in face else None)
    return list(result.values())
