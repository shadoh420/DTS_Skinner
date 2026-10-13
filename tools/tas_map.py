"""UE3 map object state and native level/BSP/terrain layouts, read-only.

Map-specific actor/component preambles live here; job-1 formats stay unchanged.
"""
from functools import lru_cache
import struct

import numpy as np

from tools.tas_package import Library, Reader
from tools.tvg_map import BASIS, viewer, viewpoint
from tools.tvg_package import rotation_matrix


def object_properties(p, ref):
    e=p.entry(ref); r=p.object_reader(ref); r.resolve=p.qualified
    if e['class'] in ('DominantDirectionalLightComponent','DominantSpotLightComponent') and not e['flags'] & 0x200:
        r.take(r.count(2)*2)  # native dominant shadow samples precede UComponent
    if e['flags'] & 0x0200000000000000:
        node,state=r.unpack('2i');r.take(10)  # probe mask and latent action
        if node:r.take(4)  # script offset; no state-stack array in 805
    elif e['class'].endswith('Component') and not e['flags'] & 0x200:
        r.take(4)  # component template owner class
        outer=e['outer'];seen=set()
        while outer>0:
            if outer in seen:raise ValueError('Component outer cycle')
            seen.add(outer);parent=p.entry(outer)
            if parent['flags'] & 0x200:
                r.name()  # template name only inside a class default object
                break
            outer=parent['outer']
    r.take(4)  # NetIndex
    props=r.properties()
    for name,value in list(props.items()):
        if not isinstance(value,bytes):continue
        a=Reader(value,p.names,resolve=p.qualified)
        if name in ('Components','AllComponents','TerrainComponents','StreamingLevels') or name=='Materials' and e['class'].endswith('Component'):
            props[name]=[p.qualified(x[0]) for x in a.array('i')]
        elif name in ('Layers','DecoLayers') or name=='Materials' and e['class']=='TerrainLayerSetup':
            props[name]=a.struct_array()
            if a.at!=len(value):raise ValueError('Trailing '+name+' struct data')
            if name=='DecoLayers':
                for layer in props[name]:
                    layer['Decorations']=Reader(layer.get('Decorations',b'\0'*4),p.names,resolve=p.qualified).struct_array()
                    for deco in layer['Decorations']:
                        deco['Instances']=Reader(deco.get('Instances',b'\0'*4),p.names,resolve=p.qualified).struct_array()
    return props,r


class MapLibrary(Library):
    def __init__(self,install):
        self.map_objects={};self.classes={};self.registered=set()
        super().__init__(install)

    def package(self,path):
        p=super().package(path)
        if p.path not in self.registered:
            self.registered.add(p.path)
            for ref,e in enumerate(p.exports,1):
                # Keep table locations, not actor payloads or decoded geometry.
                self.map_objects.setdefault(e['source'].lower(),(str(p.path),ref))
                if e['class']=='Class':self.classes[e['source'].lower()]=p.qualified(e['base'])
        return p

    def locate(self,source):
        if not source or source.lower() not in self.map_objects:raise ValueError('Missing map object: '+str(source))
        path,ref=self.map_objects[source.lower()];return self.package(path),ref

    @lru_cache(maxsize=4096)
    def ancestry(self,classname):
        result=[];current=classname
        while current:
            if current.lower() in result:raise ValueError('Class inheritance cycle')
            result.append(current.lower());current=self.classes.get(current.lower())
        return result

    @lru_cache(maxsize=4096)
    def state(self,source,depth=0):
        if depth>40:raise ValueError('Archetype/default chain too deep')
        p,ref=self.locate(source);e=p.entry(ref)
        props,_=object_properties(p,ref)
        archetype=p.qualified(e['archetype'])
        classname=p.qualified(e['cls'])
        default=classname.rsplit('.',1)[0]+'.Default__'+classname.rsplit('.',1)[-1] if classname else None
        base={}
        parent=archetype or (default if default and default.lower()!=source.lower() else None)
        if parent and parent.lower() in self.map_objects:base=self.state(parent,depth+1)[0]
        return dict(base,**props),classname

    def close(self):
        self.state.cache_clear();self.ancestry.cache_clear();super().close()


def level(p,ref):
    _,r=object_properties(p,ref)
    owner=r.i32()
    if owner!=ref:raise ValueError('Level owner does not match export')
    actors=[x[0] for x in r.array('i')]
    if any(x<0 or x>len(p.exports) for x in actors):raise ValueError('Invalid level actor reference')
    url=[r.text() for _ in range(4)];options=[r.text() for _ in range(r.count(4))]
    r.take(8);model=r.i32()
    if model and p.entry(model)['class']!='Model':raise ValueError('Invalid level Model reference')
    return dict(actors=actors,model=model,url=url,options=options)


def transform(actor,component=None):
    """Compose native actor pivot/scale with component local transform, then change basis."""
    component=component or {}
    def native(props,local=False):
        scale=np.asarray(props.get('Scale3D' if local else 'DrawScale3D',(1,1,1)),dtype=float)*props.get('Scale' if local else 'DrawScale',1)
        linear=rotation_matrix(*props.get('Rotation',(0,0,0)))@np.diag(scale)
        location=np.asarray(props.get('Translation' if local else 'Location',(0,0,0)),dtype=float)-linear@np.asarray(props.get('PrePivot',(0,0,0)))
        m=np.eye(4);m[:3,:3]=linear;m[:3,3]=location;return m
    a=native(actor);c=native(component,True)
    # Absolute component channels are authored exceptions to actor inheritance.
    if component.get('AbsoluteTranslation'):a[:3,3]=0
    if component.get('AbsoluteRotation') or component.get('AbsoluteScale'):
        scale=np.linalg.norm(a[:3,:3],axis=0)
        a[:3,:3]=np.diag(scale) if component.get('AbsoluteRotation') else a[:3,:3]/np.maximum(scale,1e-30)
        if component.get('AbsoluteRotation') and component.get('AbsoluteScale'):a[:3,:3]=np.eye(3)
    m=a@c;m[:3,:3]=BASIS@m[:3,:3]@BASIS.T;m[:3,3]=viewer(m[:3,3])
    if not np.isfinite(m).all():raise ValueError('Non-finite placement transform')
    return m.T.reshape(-1).tolist()


def is_npc(ancestry):
    names={n.rsplit('.',1)[-1].lower() for n in ancestry}
    return bool(names & {'trdeployable_turret','trturretpawn','trplayerpawn'})


def terrain_native(p,ref):
    props,r=object_properties(p,ref)
    w,h=props.get('NumVerticesX',0),props.get('NumVerticesY',0)
    if not 2<=w<=8192 or not 2<=h<=8192:raise ValueError('Invalid terrain dimensions')
    n=r.count(2)
    if n!=w*h:raise ValueError('Terrain height dimensions mismatch')
    heights=r.take(n*2)
    if r.count()!=n:raise ValueError('Terrain info dimensions mismatch')
    info=r.take(n);alphas=[]
    for _ in range(r.count(4,256)):
        if r.count()!=n:raise ValueError('Terrain alpha dimensions mismatch')
        alphas.append(r.take(n))
    return props,heights,info,alphas


def bsp(p,ref):
    _,r=object_properties(p,ref);r.take(28)
    _,raw=r.bulk_array(12);vectors=np.frombuffer(raw,'<f4').reshape(-1,3)
    _,raw=r.bulk_array(12);points=np.frombuffer(raw,'<f4').reshape(-1,3)
    count,raw=r.bulk_array(64)
    nodes=[dict(pool=struct.unpack_from('<i',raw,i*64+16)[0],surface=struct.unpack_from('<i',raw,i*64+20)[0],count=raw[i*64+54]) for i in range(count)]
    r.take(4)  # owning UModel
    surfaces=[]
    for _ in range(r.count(60)):
        material,flags,base,normal,u,v,brush,actor=r.unpack('iI6i');r.take(28)
        surfaces.append(dict(material=p.qualified(material),flags=flags,base=base,normal=normal,u=u,v=v,actor=p.qualified(actor)))
    count,raw=r.bulk_array(24);verts=np.ndarray((count,),'<i4',buffer=raw,strides=(24,)).copy()
    for node in nodes:
        if not 0<=node['surface']<len(surfaces) or not 0<=node['pool']<=len(verts)-node['count']:raise ValueError('BSP node outside pools')
        if any(not 0<=i<len(points) for i in verts[node['pool']:node['pool']+node['count']]):raise ValueError('BSP vertex outside points')
    return dict(nodes=nodes,surfaces=surfaces,points=points,vectors=vectors,verts=verts)
