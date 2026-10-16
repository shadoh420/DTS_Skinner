"""UE3 material inputs and MIC overrides; no filename-based texture selection."""
from collections import defaultdict
import hashlib
import io
import re

import numpy as np
from PIL import Image

from tools.tas_package import Reader
from tools.texture_storage import dds_bytes, open_texture, write_texture


def texture_payload(props, mips):
    fmt = props.get('Format')
    w, h, raw = mips[0]
    info = dict(format=fmt, width=w, height=h, mips=len(mips))
    if fmt in ('PF_DXT1', 'PF_DXT3', 'PF_DXT5'):
        for level, (mw, mh, _) in enumerate(mips):
            if (mw, mh) != (max(1,w >> level), max(1,h >> level)):
                raise ValueError('Non-contiguous DXT mip chain')
        return '.dds', dds_bytes(w, h, fmt[3:], [m[2] for m in mips]), info
    if fmt == 'PF_A8R8G8B8' and len(raw) == w*h*4:
        image = Image.frombytes('RGBA', (w,h), raw, 'raw', 'BGRA')
    elif fmt == 'PF_G8' and len(raw) == w*h:
        image = Image.frombytes('L', (w,h), raw)
    elif fmt == 'PF_G16' and len(raw) == w*h*2:
        image = Image.frombytes('I;16', (w,h), raw)
    elif fmt == 'PF_V8U8' and len(raw) == w*h*2:
        xy = np.clip(np.frombuffer(raw, np.int8).reshape(h,w,2).astype(np.float32)/127, -1, 1)
        z = np.sqrt(np.maximum(0, 1-(xy*xy).sum(axis=2)))
        image = Image.fromarray(np.rint((np.dstack((xy,z))+1)*127.5).astype(np.uint8))
        info['normal_encoding'] = 'signed XY, reconstructed positive Z, RGB PNG'
    else:
        raise ValueError('Unsupported texture format/length: '+str(fmt))
    stream = io.BytesIO(); image.save(stream, format='PNG')
    return '.png', stream.getvalue(), info


def texture_filename(source, digest, ext):
    """Readable object name plus a content hash, like the Vengeance library."""
    name = re.sub('[^a-z0-9_-]+', '_', source.rsplit('.', 1)[-1].lower()).strip('_')[:80] or 'texture'
    return f'{name}_{digest[:12]}{ext}'


class Materials:
    def __init__(self, library, directory):
        self.library, self.directory = library, directory
        self.textures, self.materials, self.definitions = {}, {}, {}
        self.failures, self.by_digest = [], {}
        self.missing = 'missing_'+getattr(library, 'game', 'tas')+'.png'
        image = Image.new('RGBA', (2,2)); image.putdata([(255,0,255,255),(32,32,32,255)]*2)
        write_texture(directory, self.missing, image)

    def texture(self, source):
        if source.lower() in self.textures:
            return self.textures[source.lower()]
        row = dict(source=source, texture=self.missing, status='unresolved')
        self.textures[source.lower()] = row
        try:
            p, ref = self.library.object(source, {'Texture2D'})
            row['selected'] = dict(file=p.path.relative_to(self.library.install).as_posix(), export=ref)
            props, mips = p.texture(ref, self.library.cache_files)
            ext, payload, info = texture_payload(props, mips)
            digest = hashlib.sha256(payload).hexdigest()
            filename = self.by_digest.setdefault(digest, texture_filename(source, digest, ext))  # Identical bytes share one file.
            write_texture(self.directory, filename, payload)
            row.update(info, texture=filename, sha256=digest, status='ready')
        except (ValueError, OSError) as exc:
            row['reason'] = str(exc)
        return row

    def definition(self, source, trail=()):
        k = source.lower()
        if k in trail or len(trail) > 64:
            raise ValueError('Material parent/redirect cycle: '+source)
        if k in self.definitions:
            return self.definitions[k]
        p, ref = self.library.object(source, {'Material','MaterialInstanceConstant','MaterialInstanceTimeVarying','ObjectRedirector'})
        props, r = p.properties(ref); cls = p.entry(ref)['class']
        if cls == 'ObjectRedirector':
            dest = p.qualified(r.i32())
            return self.definition(dest, trail+(k,))
        parameters = {'textures':{}, 'vectors':{}, 'scalars':{}, 'switches':{}}
        for field, dest in [('TextureParameterValues','textures'),('VectorParameterValues','vectors'),
                            ('ScalarParameterValues','scalars')]:
            for item in Reader(props.get(field, b'\0'*4), p.names).struct_array():
                name = item.get('ParameterName')
                value = item.get('ParameterValue')
                if name:
                    parameters[dest][name.lower()] = p.qualified(value) if dest == 'textures' else value
        # Input structs store object references in the containing package's index space.
        inputs = {name: self.input_source(p, props.get(name)) for name in
                  ('DiffuseColor','Opacity','OpacityMask','Normal','SpecularColor','EmissiveColor')}
        input_values = {name:{k:v for k,v in props[name].items() if k != 'Expression'}
                        for name in inputs if isinstance(props.get(name),dict)}
        parent = p.qualified(props.get('Parent', 0))
        preview = props.get('PreviewMesh')
        own = dict(source=source, cls=cls, parent=parent, root=source, inputs=inputs, input_values=input_values,
                   selected=dict(file=p.path.relative_to(self.library.install).as_posix(),export=ref),
                   parameters=parameters, preview=preview, chain=[source],
                   settings={k:props[k] for k in ('BlendMode','TwoSided','OpacityMaskClipValue','LightingModel') if k in props})
        own['time_varying'] = cls == 'MaterialInstanceTimeVarying'
        if parent:
            base = self.definition(parent, trail+(k,))
            own['root'] = base['root']; own['chain'] += base['chain']
            own['inputs'] = {**base['inputs'], **{k:v for k,v in inputs.items() if v is not None}}
            own['input_values'] = {**base['input_values'], **input_values}
            own['settings'] = {**base['settings'], **own['settings']}
            own['parameters'] = {k:{**base['parameters'][k], **parameters[k]} for k in parameters}
            own['time_varying'] |= base['time_varying']
        self.definitions[k] = own
        return own

    @staticmethod
    def input_source(p, value):
        if isinstance(value, dict) and value.get('Expression'):
            return p.qualified(value['Expression'])
        return None

    @staticmethod
    def base_texture(graph):
        """Rank graph samples; object names are only the last tie breaker."""
        candidates=graph['samples']
        def role(name,parameter=False):
            name=name.lower()
            # Tex_Mask can supply RGB albedo and an alpha mask: the parameter name
            # alone must not demote its colour output (Katabatic's rock master).
            negative=('detail','overlay','noise') if parameter else ('detail','overlay','noise','mask','normal')
            if any(s in name for s in negative):return -1
            if not parameter and re.search(r'_(?:d|diff|dif|albedo)(?:_\d+)?$',name):return 1
            return int(any(s in name for s in ('diff','dif','base','albedo','color','colour','sky')))
        remaining=list(candidates)
        for reason,score in [
            ('2D colour sample',lambda c:-c.get('dimension',2)),
            ('colour input',lambda c:-c.get('control',0)),
            ('texture parameter',lambda c:role(c['parameter'],True)),
            ('coordinate scale',lambda c:-max(c['uv_scale']) if c['uv_scale'] is not None else -1),
            ('texture name tie breaker',lambda c:role(c['texture'].rsplit('.',1)[-1]))]:
            best=max(map(score,remaining));remaining=[c for c in remaining if score(c)==best]
            if len({c['texture'] for c in remaining})==1:return remaining[0]['texture'],reason
        return remaining[0]['texture'],'first input fallback'

    def expression(self, source, parameters, trail=(), memo=None):
        """Collect only textures reachable from this material input, in graph order."""
        if not source:
            return dict(textures=[], samples=[], operations=[], constant=None, masks=[], uv_scale=None, pan=None, spin=None, tint=None)
        if source.lower() in trail or len(trail) > 64:
            raise ValueError('Material expression cycle: '+source)
        if memo is None:
            memo = {}
        if source.lower() in memo:
            return memo[source.lower()]
        p, ref = self.library.object(source)
        cls = p.entry(ref)['class']; props, _ = p.properties(ref)
        empty=dict(textures=[], samples=[], operations=[cls], constant=None, masks=[], uv_scale=None, pan=None, spin=None, tint=None)
        if cls.startswith('MaterialExpressionTextureSample') or cls == 'MaterialExpressionTextureObjectParameter':
            name = str(props.get('ParameterName','')).lower()
            texture = parameters['textures'].get(name) or p.qualified(props.get('Texture',0))
            if not texture:
                raise ValueError('Texture expression without a texture: '+source)
            coords=self.input_source(p,props.get('Coordinates'))
            c=self.expression(coords,parameters,trail+(source.lower(),),memo) if coords else {}
            uv=c.get('uv_scale') if coords else [1.,1.]
            return dict(empty,textures=[texture],samples=[dict(texture=texture,parameter=name,uv_scale=uv,expression=source,dimension=3 if cls.endswith('Cube') else 2,
                                                                pan=c.get('pan'),spin=c.get('spin'))])
        if cls=='MaterialExpressionTextureCoordinate':
            return dict(empty,uv_scale=[abs(props.get('UTiling',1)),abs(props.get('VTiling',1))])
        if cls=='MaterialExpressionConstant2Vector':
            return dict(empty,constant=[props.get('R',0),props.get('G',0)])
        if cls in ('MaterialExpressionConstant','MaterialExpressionScalarParameter',
                   'MaterialExpressionConstant3Vector','MaterialExpressionConstant4Vector','MaterialExpressionVectorParameter'):
            name = str(props.get('ParameterName','')).lower()
            value = parameters['vectors'].get(name, parameters['scalars'].get(name,
                    props.get('DefaultValue',props.get('Constant',props.get('R',0)))))
            return dict(empty,constant=value)
        inputs = [(k, self.input_source(p, v)) for k,v in props.items() if isinstance(v,dict) and v.get('Expression')]
        if cls == 'MaterialExpressionStaticSwitchParameter':
            # Static permutation binary overrides are not inferred from a name.
            chosen = 'A' if props.get('DefaultValue',False) else 'B'
            inputs = [(k,v) for k,v in inputs if k == chosen]
        result = empty;children={}
        if cls == 'MaterialExpressionComponentMask':
            result['masks'].append([c for c in ('R','G','B','A') if props.get(c)])
        for field, child in inputs:
            item = self.expression(child, parameters, trail+(source.lower(),), memo)
            children[field]=item
            result['textures'].extend(item['textures']); result['operations'].extend(item['operations'])
            result['masks'].extend(item['masks'])
            result['samples'].extend(dict(c,control=c.get('control',0)+int(field in ('Alpha','Percent','Exponent'))) for c in item['samples'])
        # Only evaluate simple coordinate scaling, not arbitrary shader arithmetic.
        uv=next((v['uv_scale'] for v in children.values() if v['uv_scale'] is not None),None)
        motion=next((v for v in children.values() if v.get('pan') or v.get('spin')),{})
        if cls in ('MaterialExpressionPanner','MaterialExpressionRotator'):
            result['uv_scale']=uv or [1.,1.]
            # UDK Materials Compendium: Panner adds Time * (SpeedX, SpeedY) to Coordinate; Rotator turns Coordinate about
            # (CenterX, CenterY) by Time * Speed. Time is game seconds unless wired; a constant Time scale is folded in,
            # anything else is left static (pan/spin None).
            time=children.get('Time')
            scale=1. if time is None else time['constant'] if isinstance(time.get('constant'),(int,float)) else 1. if time['operations']==['MaterialExpressionTime'] else None
            if scale is not None and cls.endswith('Panner'):result['pan']=[props.get('SpeedX',0)*scale,props.get('SpeedY',0)*scale]
            if scale is not None and cls.endswith('Rotator'):result['spin']=dict(speed=props.get('Speed',.25)*scale,center=[props.get('CenterX',.5),props.get('CenterY',.5)])
        elif cls=='MaterialExpressionComponentMask' and children:
            child=next(iter(children.values()));result['uv_scale']=uv;result['constant']=child['constant'];result.update(pan=motion.get('pan'),spin=motion.get('spin'))
            if isinstance(result['constant'],(tuple,list)):
                result['constant']=[v for c,v in zip(('R','G','B','A'),result['constant']) if props.get(c)]
        elif cls in ('MaterialExpressionMultiply','MaterialExpressionDivide','MaterialExpressionAdd','MaterialExpressionSubtract'):
            a=children.get('A',{}).get('constant',props.get('ConstA'))
            b=children.get('B',{}).get('constant',props.get('ConstB'))
            if uv is not None:
                factor=b if children.get('A',{}).get('uv_scale') is not None else a
                if cls.endswith(('Add','Subtract')):result['uv_scale']=uv;result.update(pan=motion.get('pan'),spin=motion.get('spin'))
                elif factor is not None:
                    factor=np.resize(np.asarray(factor,dtype=float),2)
                    if cls.endswith('Multiply'):
                        result['uv_scale']=(np.abs(factor)*uv).tolist()
                        if motion.get('pan'):result['pan']=(factor*motion['pan']).tolist()
                    elif children.get('A',{}).get('uv_scale') is not None and np.all(factor):result['uv_scale']=(np.asarray(uv)/np.abs(factor)).tolist()
            # A texture times a constant (an emissive tint): kept beside the texture list, not baked.
            if cls=='MaterialExpressionMultiply' and uv is None and (a is None)!=(b is None):
                side=children.get('A' if a is None else 'B',{})
                if side.get('textures'):result['tint']=(np.asarray(side.get('tint') or 1.,dtype=float)*np.asarray(a if b is None else b,dtype=float)).tolist()
            if a is not None and b is not None:
                try:
                    op={'MaterialExpressionMultiply':np.multiply,'MaterialExpressionDivide':np.divide,'MaterialExpressionAdd':np.add,'MaterialExpressionSubtract':np.subtract}[cls]
                    with np.errstate(all='ignore'):value=op(a,b)
                    if np.isfinite(value).all():result['constant']=value.tolist()
                except ValueError:pass
        result['textures'] = list(dict.fromkeys(result['textures']))
        result['operations'] = list(dict.fromkeys(result['operations']))
        memo[source.lower()] = result
        return result

    def material(self, source):
        if not source:
            return dict(source=None, texture=self.missing, settings={}, status='unresolved', reason='Empty material slot')
        if source.lower() in self.materials:
            return self.materials[source.lower()]
        row = dict(source=source, texture=self.missing, settings={}, status='unresolved', notes=[])
        self.materials[source.lower()] = row
        try:
            definition = self.definition(source)
            row.update(parent_chain=definition['chain'], parameters=definition['parameters'],selected=definition['selected'])
            inputs = {k:self.expression(v, definition['parameters']) for k,v in definition['inputs'].items()}
            inputs, preview_note = self.preview_inputs(definition, inputs)
            if preview_note:
                row['notes'].append(preview_note)
            for name, value in definition['input_values'].items():
                if value.get('UseConstant'):
                    inputs[name]['constant'] = value.get('Constant')
                mask = [c for c in ('R','G','B','A') if value.get('Mask'+c)]
                if value.get('Mask') and mask:
                    inputs[name]['masks'].insert(0, mask)
            row['inputs'] = inputs
            row['native_settings'] = definition['settings']
            if definition['time_varying']:
                row['notes'].append('Time-varying instance: authored ParameterValue preview; curves are not animated')
            settings = row['settings']; native = definition['settings']
            mode = native.get('BlendMode', 'BLEND_Opaque')
            if native.get('TwoSided'):
                settings['cull'] = 'none'
            if mode == 'BLEND_Masked':
                settings['alphaTest'] = native.get('OpacityMaskClipValue', .333333)
            elif mode in ('BLEND_Translucent','BLEND_AlphaComposite'):
                settings.update(blend=['gl_src_alpha','gl_one_minus_src_alpha'], depthWrite=False)
            elif mode == 'BLEND_Additive':
                settings.update(blend=['gl_src_alpha','gl_one'], depthWrite=False)
            elif mode == 'BLEND_Modulate':
                settings.update(blend=['gl_dst_color','gl_zero'], depthWrite=False)
            diffuse = inputs['DiffuseColor']
            emissive=inputs['EmissiveColor']
            if (native.get('LightingModel')=='MLM_Unlit' or not diffuse['textures'] and diffuse['constant'] is None) and (emissive['textures'] or emissive['constant'] is not None):
                diffuse = inputs['EmissiveColor']; settings['unlit'] = True
                row['notes'].append('Unlit material or absent diffuse: emissive input used for preview')
            referenced = {s for value in inputs.values() for s in value['textures']}
            tex = {s:self.texture(s) for s in sorted(referenced)}
            row['recorded_maps'] = {k:[dict(source=s, texture=tex[s]['texture'], status=tex[s]['status'])
                                       for s in inputs[k]['textures']] for k in ('Normal','SpecularColor','EmissiveColor')}
            if diffuse['textures']:
                selected,reason=self.base_texture(diffuse)
                row['diffuse_selection']=dict(method=reason,source=selected,candidates=diffuse['samples'])
                row.update(texture=tex[selected]['texture'], status=tex[selected]['status'], diffuse_source=selected)
                if tex[selected]['status'] != 'ready':
                    row['reason'] = tex[selected]['reason']
                if len(diffuse['textures']) > 1:
                    row['notes'].append('Multiple diffuse graph textures: '+reason+'; reachable textures/operators recorded')
            elif diffuse['constant'] is not None:
                value = diffuse['constant']
                value = list(value) if isinstance(value,(tuple,list)) else [value]*3
                rgba = tuple(round(min(1,max(0,v))*255) for v in (value+[1]*4)[:4])
                filename = 'constant_'+'_'.join(map(str,rgba))+'.png'
                write_texture(self.directory, filename, Image.new('RGBA',(1,1),rgba))
                row.update(texture=filename,status='ready')
            else:
                raise ValueError('No supported texture/constant connected to diffuse or emissive input')
            opacity = inputs['OpacityMask'] if mode == 'BLEND_Masked' else inputs['Opacity']
            if opacity['textures'] and row['status'] == 'ready':
                same = opacity['textures'] == [row.get('diffuse_source')]
                row['opacity'] = opacity
                masks = opacity['masks']
                if not (same and masks == [['A']]):
                    if len(opacity['textures']) == 1 and masks and len(masks[0]) == 1:
                        source_texture = tex[opacity['textures'][0]]
                        if source_texture['status'] != 'ready':
                            raise ValueError('Unresolved opacity texture: '+opacity['textures'][0])
                        with open_texture(self.directory/row['texture']) as base_image:
                            preview = base_image.convert('RGBA')
                        with open_texture(self.directory/source_texture['texture']) as alpha_image:
                            alpha = alpha_image.convert('RGBA').getchannel(masks[0][0]).resize(preview.size)
                        preview.putalpha(alpha)
                        stream = io.BytesIO(); preview.save(stream,format='PNG'); payload = stream.getvalue()
                        row['original_diffuse_texture'] = row['texture']
                        row['texture'] = texture_filename(row.get('diffuse_source','constant')+'_masked', hashlib.sha256(payload).hexdigest(), '.png')
                        write_texture(self.directory,row['texture'],payload)
                        row['notes'].append('Opacity-channel preview PNG; original compressed textures retained')
                    else:
                        row['notes'].append('Complex opacity expression recorded; preview uses diffuse alpha')
            elif opacity['constant'] is not None and isinstance(opacity['constant'],(int,float)):
                settings['opacity'] = min(1,max(0,opacity['constant']))
            sample=next((c for c in diffuse['samples'] if c['texture']==row.get('diffuse_source') and (c.get('pan') or c.get('spin'))),None)
            if sample:self.motion(settings,sample)
            if not settings.get('unlit') and (emissive['textures'] or emissive['constant'] is not None):
                # UE3 adds EmissiveColor to the lit result (BasePassPixelShader.usf); constants clamp to the page's 0..1.
                glow={'mode':'add'}
                value=emissive['constant'] if not emissive['textures'] else emissive.get('tint')
                if value is not None:
                    value=list(value) if isinstance(value,(tuple,list)) else [value]*3
                    glow['color']=[round(min(1,max(0,v)),4) for v in (value+value[-1:]*3)[:3]]
                if emissive['textures']:
                    selected,_=self.base_texture(emissive)
                    if tex[selected]['status']=='ready':
                        glow['texture']=tex[selected]['texture']
                        sample=next((c for c in emissive['samples'] if c['texture']==selected and (c.get('pan') or c.get('spin'))),None)
                        if sample:self.motion(glow,sample)
                    else:glow=None
                if glow and (glow.get('texture') or any(glow.get('color',[0]))):settings['emissive']=glow
            if any('StaticSwitchParameter' in op for value in inputs.values() for op in value['operations']):
                row['notes'].append('Static switch uses serialized expression default; permutation overrides deferred')
            row['notes'].append('Normal/specular and vector/scalar parameters recorded; no animated shader evaluation')
            if any(op not in ('MaterialExpressionTextureSample','MaterialExpressionTextureSampleParameter2D') for op in diffuse['operations']):
                row['notes'].append('Diffuse graph texture preview; shader arithmetic is not baked')
        except (ValueError,OSError) as exc:
            row.update(status='unresolved',reason=str(exc))
        return row

    @staticmethod
    def motion(settings, sample):
        """Panner/Rotator on a texture's coordinates as page settings: pan u/s, v/s; spin rad/s about uvCenter."""
        if sample.get('pan'):settings['pan']=sample['pan']
        if sample.get('spin'):
            # Hypothesis: uv' = R(Time * Speed)(uv - center) + center, as UE2's TexRotator matrix; Three turns the other way.
            settings.update(spin=-sample['spin']['speed'],uvCenter=sample['spin']['center'])

    def preview_inputs(self, definition, inputs):
        return inputs, None

    def variants(self, selected):
        """MIC PreviewMesh links, plus siblings within a character/weapon asset package.

        Generic environment masters do not assign every descendant MIC to every prop.
        """
        by_root, by_preview = defaultdict(list), defaultdict(list)
        for (name, cls), rows in self.library.rows.items():
            if cls not in ('MaterialInstanceConstant', 'MaterialInstanceTimeVarying') or 'default__' in name:
                continue
            source = rows[0]['source']
            # Only relevant asset namespaces; all others can still resolve on demand.
            if source.split('.')[0].lower() not in selected:
                continue
            try:
                d = self.definition(source)
                by_root[d['root'].lower()].append(source)
                if d['preview']:
                    by_preview[d['preview'].lower()].append(source)
            except (ValueError,OSError) as exc:
                self.failures.append(dict(source=source,reason=str(exc)))
        return by_root, by_preview
