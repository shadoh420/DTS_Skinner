"""Validated visibility metadata shared by IBSP46/47 and Raven RBSP1 previews.

Original implementation of the on-disk rules: renderer R_LoadNodesAndLeafs,
R_LoadVisibility, R_PointInLeaf and R_MarkLeaves. No engine source is bundled.
"""
import base64
import math
import struct


def read_visibility(lumps, *, surface_stride=104):
    def records(index, fmt):
        size=struct.calcsize(fmt)
        if len(lumps[index])%size:raise ValueError(f'PVS lump {index}: partial record')
        if len(lumps[index])//size>4000000:raise ValueError(f'PVS lump {index}: record limit')
        return list(struct.iter_unpack(fmt,lumps[index]))
    def bounds(values):
        if not all(math.isfinite(v) for v in values) or any(values[i]>values[i+3] for i in range(3)):
            raise ValueError('Invalid BSP cull bounds')
        return list(values)
    def span(first,size,total):
        if min(first,size)<0 or first+size>total:raise ValueError('Invalid BSP cull range')
    planes=records(2,'<4f');nodes=records(3,'<9i');leaves=records(4,'<12i')
    refs=[r[0] for r in records(5,'<i')];models=records(7,'<6f4i')
    if len(lumps[13])%surface_stride:raise ValueError('PVS partial surface record')
    count=len(lumps[13])//surface_stride
    if len(nodes)+len(leaves)>1000000:raise ValueError('BSP cull tree exceeds limit')
    for plane in planes:
        if not all(math.isfinite(v) for v in plane) or not any(plane[:3]):raise ValueError('Invalid PVS plane')
    for node in nodes:
        bounds(node[3:9])
        if not 0<=node[0]<len(planes):raise ValueError('Invalid PVS node plane')
        for child in node[1:3]:
            if child>=len(nodes) or child<0 and -1-child>=len(leaves):raise ValueError('Invalid BSP cull child')
    seen=set();stack=[0] if nodes else []
    while stack:
        index=stack.pop()
        if index<0:continue
        if index in seen:raise ValueError('Invalid BSP cull tree cycle/shared node')
        seen.add(index);stack.extend(nodes[index][1:3])
    for leaf in leaves:
        # Solid, empty leaves may have inverted compiler sentinel bounds.
        if leaf[9]:bounds(leaf[2:8])
        span(leaf[8],leaf[9],len(refs))
    if any(n<0 or n>=count for n in refs):raise ValueError('Invalid BSP cull surface')
    for model in models:
        bounds(model[:6]);span(model[6],model[7],count)
    vis=lumps[16];clusters=width=0;bits=''
    if vis:
        if len(vis)<8:raise ValueError('Truncated PVS header')
        clusters,width=struct.unpack_from('<2i',vis)
        if clusters<0 or width<0 or width<(clusters+7)//8 or clusters*width!=len(vis)-8 or len(vis)>64*1024*1024:
            raise ValueError('Invalid PVS dimensions/size')
        if clusters and any(l[0]>=clusters for l in leaves):raise ValueError('Invalid PVS leaf cluster')
        bits=base64.b64encode(vis[8:]).decode('ascii')
    return dict(nodes=[list(n[3:9])+list(n[1:3])+[n[0]] for n in nodes],
                leaves=[list(l[2:8])+list(l[8:10])+[l[0],l[1]] for l in leaves],
                planes=[list(p) for p in planes],surfaces=refs,
                world=[models[0][6],models[0][7]] if models else [0,count],
                bounds=list(models[0][:6]) if models else None,surfaceCount=count,inline=[],
                pvs=dict(numClusters=clusters,clusterBytes=width,bits=bits))
