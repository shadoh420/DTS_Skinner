"""Source BSP20, game lumps and baked lighting. Layouts: public Source SDK headers.

Independent reader; VHV storage/ordering checked against noclip's Studio.ts.
No game execution, extraction to the install, or SDK implementation copied.
"""
import io
import re
import struct
import zipfile
import numpy as np
from . import vtmb
from .import_vtmb_map import rows, indexed, face_geometry, EmbeddedAsset

USED = {0:'entities',1:'planes',2:'texdata',3:'vertices',5:'nodes',6:'texinfo',7:'faces',
        10:'leaves',12:'edges',13:'surfedges',14:'models',26:'displacements',33:'dispverts',
        35:'game',40:'pakfile',43:'texture strings',44:'texture offsets',48:'disptris',
        51:'HDR ambient indices',53:'HDR lightmaps',55:'HDR ambient samples',58:'HDR faces'}
LUMP_NAMES = ('entities planes texdata vertices visibility nodes texinfo faces lighting occlusion leaves faceids '
              'edges surfedges models worldlights leaffaces leafbrushes brushes brushsides areas areaportals '
              'unused22 unused23 unused24 unused25 dispinfo originalfaces physdisp physcollide vertnormals '
              'vertnormalindices disp_lightmap_alphas dispverts disp_lightmap_sample_positions game_lump '
              'leafwaterdata primitives primverts primindices pakfile clipportalverts cubemaps texdata_strings '
              'texdata_offsets overlays leafmindisttowater face_macro_texture_info disptris physcollidesurface '
              'wateroverlays leaf_ambient_index_hdr leaf_ambient_index lighting_hdr worldlights_hdr '
              'leaf_ambient_lighting_hdr leaf_ambient_lighting xzippakfile faces_hdr map_flags overlay_fades '
              'unused61 unused62 unused63').split()


def entities(data):
    """Ordered pairs retain repeated Source entity outputs, including escaped quotes."""
    text=data.rstrip(b'\0').decode('cp1252'); result=[]; current=None; key=None; at=0
    token=re.compile(r'\s+|//[^\r\n\0]*|"(?P<value>(?:\\[^\0]|[^"\\\0])*)"|(?P<brace>[{}])')
    while at<len(text):
        m=token.match(text,at)
        if not m: raise ValueError(f'Invalid entity token at byte {at}')
        at=m.end(); value,brace=m.group('value','brace')
        if brace=='{':
            if current is not None: raise ValueError('Nested entity block')
            current=[]
        elif brace=='}':
            if current is None or key is not None: raise ValueError('Incomplete entity')
            result.append(current);current=None
        elif value is not None:
            if current is None: raise ValueError('Entity value outside block')
            value=re.sub(r'\\(["\\n])',lambda m:'\n' if m[1]=='n' else m[1],value)
            if key is None: key=value
            else: current.append((key,value));key=None
    if current is not None: raise ValueError('Unclosed entity')
    return result


def read_bsp(raw):
    vtmb.span(raw,0,1036)
    if raw[:8]!=b'VBSP\x14\0\0\0': raise ValueError('Expected Source VBSP20')
    lumps=[];audit=[]
    for i in range(64):
        at,size,version,code=vtmb.unpack(raw,'4i',8+i*16);vtmb.span(raw,at,size)
        if size and (at<1036 or code): raise ValueError(f'Compressed/overlapping lump {i} unsupported')
        lumps.append(raw[at:at+size]);audit.append(dict(id=i,offset=at,size=size,version=version,
            name=LUMP_NAMES[i],used=i in USED,reason='Read for preview' if i in USED else 'Collision/visibility/runtime effects not evaluated'))
    if lumps[58]:audit[7].update(used=False,reason='HDR faces 58 take precedence')
    b=dict(raw=raw,lumps=lumps,audit=audit,revision=vtmb.unpack(raw,'i',1032)[0],entities=entities(lumps[0]))
    for name,i,fmt in [('texdata',2,'3f5i'),('points',3,'3f'),('texinfo',6,'16f2i'),('edges',12,'2H'),
                        ('models',14,'9f3i'),('dispverts',33,'5f'),('nodes',5,'3i6h2H2h')]:
        b[name]=rows(lumps[i],fmt)
        if b[name] and not np.isfinite(b[name]).all(): raise ValueError('Nonfinite BSP '+name)
    b['source_planes']=rows(lumps[1],'4fi')
    # The shared tessellator expects an already oriented plane (BSP17 convention).
    b['planes']=[tuple(np.asarray(p[:3])*sign)+p[3:] for p in b['source_planes'] for sign in (1,-1)]
    b['surfedges']=[r[0] for r in rows(lumps[13],'i')];b['disptris']=[r[0] for r in rows(lumps[48],'H')]
    b['names']=[vtmb.string(lumps[43],r[0]) for r in rows(lumps[44],'i')]
    b['faces']=[]
    for (r,) in rows(lumps[58] or lumps[7],'56s'):
        plane,side,node,first,count,tex,disp,fog=vtmb.unpack(r,'HBBihhhh')
        if side not in (0,1):raise ValueError('Invalid face plane side')
        light,area,u,v,w,h=vtmb.unpack(r,'if4i',20)
        b['faces'].append(dict(plane=plane*2+side,side=side,first=first,count=count,texinfo=tex,disp=disp,
                                styles=list(r[16:20]),light=light,mins=[u,v],size=[w+1,h+1]))
    b['displacements']=[]
    for (r,) in rows(lumps[26],'176s'):
        vert,tri,power=vtmb.unpack(r,'3i',12)
        b['displacements'].append(dict(start=vtmb.unpack(r,'3f'),vertex=vert,triangle=tri,power=power,face=vtmb.unpack(r,'H',36)[0]))
    if not b['models']: raise ValueError('No BSP world model')
    return b


def pak_files(bsp,files):
    """BSP pak wins locally, never enters another map's index."""
    result=dict(files);audit=[];total=0
    if bsp['lumps'][40]:
        with zipfile.ZipFile(io.BytesIO(bsp['lumps'][40])) as z:
            for info in z.infolist():
                if info.is_dir(): continue
                name=vtmb.asset_name(info.filename);total+=info.file_size
                if total>512*1024*1024: raise ValueError('Pakfile exceeds 512 MiB')
                result[name]=EmbeddedAsset(z.read(info),'BSP pakfile')
                audit.append(dict(name=name,overrides=name in files,size=info.file_size))
    return result,audit


def game_lumps(bsp):
    data=bsp['lumps'][35];found={};audit=[]
    if not data:return found,audit
    count,=vtmb.unpack(data,'i');vtmb.span(data,4,count,16)
    for i in range(count):
        tag,flags,v,at,size=vtmb.unpack(data,'4sHHii',4+i*16);name=tag[::-1].decode('ascii')
        vtmb.span(bsp['raw'],at,size)
        if flags: raise ValueError('Compressed game lump '+name)
        found[name]=(v,bsp['raw'][at:at+size])
        audit.append(dict(name=name,version=v,size=size,used=name in ('sprp','dprp','dplh')))
    return found,audit


def dictionary(data):
    n,=vtmb.unpack(data,'i');vtmb.span(data,4,n,128)
    return [vtmb.model_reference(data[4+i*128:4+(i+1)*128].split(b'\0')[0].decode('cp1252')) for i in range(n)],4+n*128


def props(lumps):
    result=[];omitted=[]
    if 'sprp' in lumps:
        version,b=lumps['sprp']
        if version!=6:raise ValueError(f'Unsupported sprp {version}')
        names,at=dictionary(b);n,=vtmb.unpack(b,'i',at);at+=4
        leaves=vtmb.unpack(b,f'{n}H',at);at+=2*n;n,=vtmb.unpack(b,'i',at);at+=4;vtmb.span(b,at,n,64)
        if at+n*64!=len(b):raise ValueError('Invalid sprp6 size')
        for i in range(n):
            r=vtmb.unpack(b,'6f3H2Bi6f2H',at+i*64);model,first,count=r[6:9]
            if model>=len(names) or first+count>len(leaves):raise ValueError('Invalid sprp model/leaf')
            result.append(dict(entity=f'sprp:{i}',classname='prop_static',model=names[model],origin=list(r[:3]),angles=list(r[3:6]),
                skin=r[11],solid=r[9],flags=r[10],leaves=list(leaves[first:first+count]),fade=list(r[12:14]),
                lighting_origin=list(r[14:17]) if r[10]&2 else None,forced_fade_scale=r[17],dx_levels=list(r[18:20]),vhv=f'sp_hdr_{i}.vhv',npc=False))
    if 'dprp' in lumps:
        version,b=lumps['dprp']
        if version!=4:raise ValueError(f'Unsupported dprp {version}')
        names,at=dictionary(b);n,=vtmb.unpack(b,'i',at);at+=4;vtmb.span(b,at,n,32);at+=n*32
        n,=vtmb.unpack(b,'i',at);at+=4;vtmb.span(b,at,n,52)
        if at+n*52!=len(b):raise ValueError('Invalid dprp4 size')
        for i in range(n):
            p=at+i*52;kind=b[p+44];model,leaf=vtmb.unpack(b,'2H',p+24)
            if kind!=0:
                omitted.append(dict(entity=f'dprp:{i}',reason='Detail sprite/shape not rendered',type=kind));continue
            lighting=b[p+28:p+32];style,count=vtmb.unpack(b,'IB',p+32)
            if count and 'dplh' in lumps:
                hdr=lumps['dplh'][1];nh,=vtmb.unpack(hdr,'i')
                vtmb.span(hdr,4,nh,5)
                if style+count>nh:raise ValueError('Invalid detail HDR lighting index')
                lighting=hdr[4+style*5:8+style*5]
            result.append(dict(entity=f'dprp:{i}',classname='prop_detail',model=indexed(names,model,'detail model'),
                origin=list(vtmb.unpack(b,'3f',p)),angles=list(vtmb.unpack(b,'3f',p+12)),skin=0,npc=False,
                leaf=leaf,detail_light=rgbexp(lighting).ravel().tolist(),orientation=b[p+40],scale=1,
                authored_sprite_scale=vtmb.unpack(b,'f',p+48)[0]))
    return result,omitted


def rgbexp(raw,divisor=255):
    v=np.frombuffer(raw,np.uint8).reshape(-1,4)
    return v[:,:3].astype(float)*np.exp2(v[:,3].view(np.int8).astype(float))[:,None]/divisor


def light_tile(bsp,face,flags,exposure):
    if face['light']<0 or flags&(6|1024):return np.full((1,1,3),255,np.uint8)
    w,h=face['size']
    if min(w,h)<=0 or w*h>4_000_000:raise ValueError('Invalid HDR lightmap dimensions')
    b=bsp['lumps'][53];at=face['light'];stride=w*h*4*(4 if flags&2048 else 1);total=np.zeros((w*h,3))
    for style in face['styles']:
        if style==255:break
        vtmb.span(b,at,stride)
        if style not in bsp.get('dark_styles',()):total+=rgbexp(b[at:at+w*h*4])
        at+=stride
    return np.rint(np.clip(total*exposure,0,1)**(1/2.2)*255).astype(np.uint8).reshape(h,w,3)


class Ambient:
    def __init__(self,bsp):
        self.bsp=bsp
    def cube(self,origin):
        b=self.bsp;node=b['models'][0][9];seen=set()
        while node>=0:
            if node in seen:raise ValueError('Cyclic BSP nodes')
            seen.add(node);r=indexed(b['nodes'],node,'node');p=indexed(b['source_planes'],r[0],'plane')
            node=r[1 if np.dot(origin,p[:3])>=p[3] else 2]
        leaf=-node-1;version=b['audit'][10]['version'];stride=56 if version==0 else 32
        if version not in (0,1):raise ValueError(f'Unsupported leaf version {version}')
        raw=b['lumps'][10];vtmb.span(raw,leaf*stride,stride);r=raw[leaf*stride:(leaf+1)*stride]
        mins=np.array(vtmb.unpack(r,'3h',8));maxs=np.array(vtmb.unpack(r,'3h',14))
        if version==0:return rgbexp(r[30:54],1),leaf
        indices=b['lumps'][51];samples=b['lumps'][55]
        if not indices:
            if not samples:return np.full((6,3),.25),leaf
            vtmb.span(samples,leaf*24,24);return rgbexp(samples[leaf*24:leaf*24+24],1),leaf
        count,first=vtmb.unpack(indices,'2H',leaf*4)
        if not count:return np.full((6,3),.25),leaf
        vtmb.span(samples,first*28,count,28)
        positions=[];cubes=[]
        for i in range(first,first+count):
            cubes.append(rgbexp(samples[i*28:i*28+24],1));positions.append(mins+(maxs-mins)*np.frombuffer(samples[i*28+24:i*28+27],np.uint8)/255)
        nearest=int(np.argmin(np.sum((np.asarray(positions)-origin)**2,axis=1)))
        return cubes[nearest],leaf


def vhv(data,checksum):
    vtmb.span(data,0,40);version,check,flags,stride,count,n=vtmb.unpack(data,'6I')
    if check!=checksum:raise ValueError(f'VHV checksum mismatch: {check} != model {checksum}')
    if version!=2 or (flags,stride) not in ((4,4),(2,12)):
        raise ValueError(f'Unsupported VHV version/format: {version}/{flags}/{stride}')
    vtmb.span(data,40,n,28);result=[];total=0
    for i in range(n):
        lod,vertices,at=vtmb.unpack(data,'3I',40+i*28);vtmb.span(data,at,vertices,stride);total+=vertices
        if lod:continue
        colors=np.frombuffer(data,np.uint8,vertices*stride,at).reshape(vertices,stride)
        # VHV stores gamma/overbright BGRA; directional form has three BGRA bases.
        linear=(colors.reshape(vertices,-1,4)[:,:,[2,1,0]]/255*2)**2.2
        result.append(linear.mean(axis=1))
    if total!=count:raise ValueError('VHV vertex count mismatch')
    return result


def vtx_maps(data):
    """VHV follows hardware VTX strip-group vertices, not VVD order."""
    v,=vtmb.unpack(data,'i');nb,bo=vtmb.unpack(data,'2i',28)
    if v not in (6,7):raise ValueError('Unsupported lighting VTX')
    vtmb.span(data,bo,nb,8)
    result=[]
    for body in range(nb):
        bp=bo+body*8;nm,mo=vtmb.unpack(data,'2i',bp)
        vtmb.span(data,bp+mo,nm,8)
        if not nm:continue
        m=bp+mo;nl,lo=vtmb.unpack(data,'2i',m);lod=m+lo
        vtmb.span(data,lod,nl,12)
        if not nl:raise ValueError('No VTX LOD for prop lighting')
        n,me=vtmb.unpack(data,'2i',lod)
        vtmb.span(data,lod+me,n,9)
        for mesh in range(n):
            at=lod+me+mesh*9;ng,go=vtmb.unpack(data,'2i',at);original=[]
            vtmb.span(data,at+go,ng,25)
            for j in range(ng):
                sg=at+go+j*25;nv,vo=vtmb.unpack(data,'2i',sg);stride,offset=(9,4) if v==7 else (15,12)
                vtmb.span(data,sg+vo,nv,stride)
                original.extend(vtmb.unpack(data,'H',sg+vo+k*stride+offset)[0] for k in range(nv))
            if original:result.append(((body,0,mesh),np.asarray(original)))
    return result
