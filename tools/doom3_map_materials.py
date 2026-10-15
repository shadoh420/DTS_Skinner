"""Small, data-only Doom material expression/stage compiler for the Maps renderer."""
import re
from collections import Counter
try:
    from tools.doom3_assets import compact, declarations, image_expression, lex, path_name, PROGRAMS
except ImportError:
    from doom3_assets import compact, declarations, image_expression, lex, path_name, PROGRAMS


def expression(source,policy=None):
    if policy:source=re.sub(r"\b(?:glslPrograms|fragmentPrograms|vertexPrograms)\b","0",source,flags=re.I)
    tokens=re.findall(r'\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+|[a-zA-Z_]\w*|==|!=|<=|>=|&&|\|\||[-+*/%<>()\[\]!]',source)
    if ''.join(tokens)!=re.sub(r'\s+','',source): raise ValueError('Unsupported expression: '+source)
    at=0
    precedence={'||':1,'&&':2,'==':3,'!=':3,'<':4,'>':4,'<=':4,'>=':4,'+':5,'-':5,'*':6,'/':6,'%':6}
    def parse(level=0):
        nonlocal at
        if at>=len(tokens): raise ValueError('Incomplete expression: '+source)
        token=tokens[at].lower();at+=1
        if token in ('-','+','!'): left=[token+'u',parse(7)]
        elif token=='(':
            left=parse()
            if at>=len(tokens) or tokens[at]!=')': raise ValueError('Unclosed expression')
            at+=1
        elif re.fullmatch(r'(?:\d|\.).*',token): left=float(token)
        elif token in ('time','sound') or re.fullmatch(r'(?:parm|global)\d+',token): left=token
        elif at<len(tokens) and tokens[at]=='[':
            at+=1;arg=parse()
            if at>=len(tokens) or tokens[at]!=']':raise ValueError('Unclosed table expression')
            at+=1;left=['table',token.lower(),arg]
        else: raise ValueError('Unsupported expression variable: '+token)
        while at<len(tokens) and precedence.get(tokens[at],-1)>=level:
            op=tokens[at];at+=1;left=[op,left,parse(precedence[op]+1)]
        return left
    result=parse()
    if at!=len(tokens):raise ValueError('Trailing expression tokens')
    return result


def tables(files):
    result={}
    for name,asset in sorted(files.items(),key=lambda item:(item[1].campaign=='d3xp',item[0])):
        if not name.endswith('.mtr'):continue
        for head,body in declarations(lex(asset.read().decode('latin1'))):
            if head and head[0].lower()=='table':
                result[head[-1].lower()]={'clamp':'clamp' in body,'snap':'snap' in body,
                    'values':[float(t) for t in body if re.fullmatch(r'-?(?:\d+(?:\.\d*)?|\.\d+)',t)]}
    return result


COMMANDS=set(('name map clampmap diffusemap bumpmap specularmap blend if alphatest rgb rgba red green blue alpha color colored '
              'vertexcolor inversevertexcolor scroll translate scale centerscale rotate texgen cubemap cameracubemap '
              'program vertexprogram fragmentprogram fragmentmap vertexparm videomap soundmap maskcolor maskalpha maskdepth '
              'forcehighquality highquality uncompressed zeroclamp alphazeroclamp clamp nearest linear nopicmip '
              'polygonoffset privatepolygonoffset ignorealphatest').split())
RAVEN_COMMANDS=set('shaderparm shadertexture glslprogram fp20program nomips shear mirrorrendermap remoterendermap noclamp'.split())
BLENDS={'add':['gl_one','gl_one'],'filter':['gl_dst_color','gl_zero'],'modulate':['gl_dst_color','gl_zero'],
        'blend':['gl_src_alpha','gl_one_minus_src_alpha'],'none':['gl_zero','gl_one']}
# Material.h materialSort_t; other words go through atof like ParseSort.
SORTS={'subview':-3,'gui':-2,'opaque':0,'portalsky':1,'decal':2,'far':3,'medium':4,'close':5,
       'almostnearest':6,'nearest':7,'postprocess':100}


def sort_value(token):
    if token.lower() in SORTS:return SORTS[token.lower()]
    try:return float(token)
    except ValueError:return 0


def split_args(tokens):
    args=[[]];depth=0
    for token in tokens:
        if token==',' and depth==0:args.append([])
        else:args[-1].append(token)
        depth+=token.count('(')+token.count('[')-token.count(')')-token.count(']')
    return [' '.join(a) for a in args]


def compile_stage(tokens,textures):
    policy=getattr(textures,'policy',None)
    commands=COMMANDS | RAVEN_COMMANDS if policy else COMMANDS
    expr=lambda value:expression(value,policy)
    t=compact(tokens);stage=dict(role='ambient',color=[1,1,1,1],condition=1,uv=[],vertexColor=0,blend=[],alphaTest=0,clamp=False)
    notes=[];i=0
    while i<len(t):
        key=t[i].lower();i+=1
        if key=='blend' and i<len(t) and t[i].lower() in ('diffusemap','bumpmap','specularmap'):
            stage['role']=t[i].lower().removesuffix('map');i+=1;continue
        if key=='blend' and i<len(t) and t[i].lower() in BLENDS:
            stage['blend']=BLENDS[t[i].lower()];i+=1;continue
        if key in ('map','clampmap','diffusemap','bumpmap','specularmap') and i<len(t):
            value,i=image_expression(t,i,PROGRAMS | policy.programs if policy else PROGRAMS);stage['image']=textures.resolve(value)
            if key not in ('map','clampmap'):stage['role']=key.removesuffix('map')
            if key=='clampmap':stage['clamp']=True
            continue
        if key in ('cubemap','cameracubemap') and i<len(t):
            name=t[i];i+=1
            stage['cube']=dict(camera=key=='cameracubemap',expression=name)
            continue
        start=i;depth=0
        while i<len(t):
            if depth==0 and t[i].lower() in commands:break
            depth+=t[i].count('(')+t[i].count('[')-t[i].count(')')-t[i].count(']');i+=1
        args=t[start:i]
        try:
            if key=='blend' and args:
                mode=args[0].lower()
                if mode in ('diffusemap','bumpmap','specularmap'):stage['role']=mode.removesuffix('map')
                elif mode in BLENDS:stage['blend']=BLENDS[mode]
                elif mode.startswith('gl_'):stage['blend']=[x.lower() for x in args if x!=',']
            elif key in ('rgb','rgba','red','green','blue','alpha','color'):
                values=[expr(x) for x in split_args(args)]
                if key=='color':stage['color']=(values+[1]*4)[:4]
                else:
                    for k in {'rgb':(0,1,2),'rgba':(0,1,2,3),'red':(0,),'green':(1,),'blue':(2,),'alpha':(3,)}[key]:stage['color'][k]=values[0]
            elif key=='colored':stage['color']=['parm0','parm1','parm2','parm3']
            elif key=='if':stage['condition']=expr(' '.join(args))
            elif key=='alphatest':stage['alphaTest']=expr(' '.join(args))
            elif key in ('scroll','translate','scale','centerscale','rotate'):
                stage['uv'].append([key,*[expr(x) for x in split_args(args)]])
            elif key in ('vertexcolor','inversevertexcolor'):stage['vertexColor']=1 if key=='vertexcolor' else -1
            elif key in ('maskcolor','maskalpha'):stage[key]=True
            elif key in ('clamp','zeroclamp','alphazeroclamp'):stage['clamp']=True
            elif key in ('name','forcehighquality','highquality','uncompressed','nearest','linear','nopicmip','nomips','noclamp'):pass
            elif key in ('privatepolygonoffset','polygonoffset'):stage['polygonOffset']=True
            elif key=='texgen' and args:
                stage['texgen']=args[0].lower()
                if stage['texgen']=='wobblesky':
                    values=split_args(args[1:]) if ',' in args else args[1:]
                    if len(values)!=3:raise ValueError('Expected three wobbleSky expressions')
                    stage['wobble']=[expr(v) for v in values]
            else:notes.append(key)
        except (ValueError,IndexError) as exc:notes.append(str(exc))
    if stage.get('cube') and stage.get('texgen') in ('skybox','wobblesky'):
        cube=stage['cube']
        suffixes=('forward','back','left','right','up','down') if cube['camera'] else ('px','nx','py','ny','pz','nz')
        cube['faces']=[textures.resolve(cube['expression']+'_'+face) for face in suffixes]
    return stage,notes


def compile_material(name,defs,materials,textures):
    record=materials.resolve(name);body=defs.materials.get(record['name'],[])
    global_tokens=[];stages=[];i=0
    while i<len(body):
        if body[i]=='{':
            start=i+1;depth=1;i+=1
            while i<len(body) and depth:depth+=(body[i]=='{')-(body[i]=='}');i+=1
            stages.append(body[start:i-1])
        else:global_tokens.append(body[i]);i+=1
    notes=[];parsed=[];shortcuts=[];reflection=False
    for stage in stages:
        s,n=compile_stage(stage,textures);notes.extend(n)
        reflection|=s.get('texgen')=='reflect'
        if s.get('cube') and s.get('texgen') not in ('skybox','wobblesky'):
            notes.append('unsupported cube texgen: '+s.get('texgen','normal'))
        elif s.get('image') or s.get('cube'):parsed.append(s)
    t=compact(global_tokens);lower=[x.lower() for x in t];i=0;falloff=None
    while i<len(t):
        key=t[i].lower();i+=1
        if key in ('diffusemap','bumpmap','specularmap','lightfalloffimage') and i<len(t):
            value,i=image_expression(t,i,PROGRAMS | defs.policy.programs if defs.policy else PROGRAMS)
            if key=='lightfalloffimage':falloff=textures.resolve(value)
            else:
                s,_=compile_stage([key,value],textures);shortcuts.append(s)
    parsed=shortcuts+parsed
    if not body:
        s,_=compile_stage(['diffusemap',record['diffuse']],textures);parsed.append(s)
    roles={s['role'] for s in parsed}
    if roles & {'bump','diffuse','specular'}:
        if 'bump' not in roles:
            s,_=compile_stage(['bumpmap','_flat'],textures);parsed.append(s)
        # Material.cpp AddImplicitStages: no white diffuse when a reflection stage exists.
        if not roles & {'diffuse','specular'} and not reflection:
            s,_=compile_stage(['diffusemap','_white'],textures);parsed.append(s)
    # Material.cpp SortInteractionStages; retain separate bump groups.
    order={'ambient':0,'bump':1,'diffuse':2,'specular':3};i=0
    while i<len(parsed):
        j=i+1
        while j<len(parsed):
            if parsed[j]['role']=='bump' and parsed[i]['role']=='bump':break
            j+=1
        parsed[i:j]=sorted(parsed[i:j],key=lambda s:order[s['role']]);i=j
    translucent=record['translucent']
    if 'forceopaque' in lower or 'mirror' in lower:translucent=False
    elif not any(s['alphaTest']!=0 for s in parsed) and not roles & {'bump','diffuse','specular'}:
        blend=parsed[0]['blend'] if parsed else []
        translucent=translucent or not parsed or bool(blend and (blend[1]!='gl_zero' or blend[0] in ('gl_dst_color','gl_one_minus_dst_color','gl_dst_alpha','gl_one_minus_dst_alpha')))
    if defs.policy and 'deform' in lower and lower[lower.index('deform')+1:lower.index('deform')+2] in (['particle'],['particle2']):
        record=dict(record,visible=False);notes.append('particle deform skipped')
    spectrum=int(t[lower.index('spectrum')+1]) if 'spectrum' in lower else 0
    # Material.cpp: DECAL_MACRO = polygonOffset + discrete + sort decal + noShadows.
    decal='decal_macro' in lower
    sort=sort_value(t[lower.index('sort')+1]) if 'sort' in lower[:-1] else SORTS['decal'] if decal else None
    if sort is None:sort=SORTS['medium'] if translucent else SORTS['opaque']
    portal_sky='portalsky' in lower
    for feature in ('deform','guisurf','foglight','blendlight','mirror'):
        if feature in lower:notes.append(feature)
    for s in parsed:
        for image in s['cube']['faces'] if s.get('cube') else [s['image']]:
            if image['status']!='ready' and not (portal_sky and image['expression']=='_currentrender'):
                notes.append('missing image: '+image['expression'])
        if any(x in lower for x in ('clamp','zeroclamp','alphazeroclamp')):s['clamp']=True
        if 'polygonoffset' in lower or decal:s['polygonOffset']=True
    def sound_reactive(value):
        return value=='sound' if isinstance(value,str) else any(sound_reactive(x) for x in value) if isinstance(value,list) else False
    return dict(name=record['name'],visible=record['visible'],twoSided=record['twoSided'],translucent=translucent,
                soundReactive=any(sound_reactive([s['color'],s['condition'],s['uv'],s['alphaTest']]) for s in parsed),
                unsmoothedTangents='unsmoothedtangents' in lower,ambientLight='ambientlight' in lower,fogLight='foglight' in lower,blendLight='blendlight' in lower,
                stages=parsed,falloff=falloff,spectrum=spectrum,sort=sort,portalSky=portal_sky,approximations=sorted(set(notes)))
