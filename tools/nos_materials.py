"""Nosgoth cooked material preview. Full graphs use the shared TA ranking.

The cooker removes many Expression references, but retains parameter samples
and compiled-resource texture references. These support an explicitly labelled
approximation, not reconstruction of the missing graph.
"""
import re
from tools.tas_materials import Materials as UE3Materials


class Materials(UE3Materials):
    def __init__(self, library, directory):
        super().__init__(library, directory)
        self.resources = {}

    def resource(self, source):
        if source.lower() in self.resources:
            return self.resources[source.lower()]
        p, ref = self.library.object(source, {'Material'})
        _, r = p.properties(ref)
        r.take(4)  # >=858 compiled material resource mask
        for _ in range(r.count(4)):
            r.text()  # compilation errors
        r.array('2i')  # texture dependency lengths
        r.take(24)  # maximum dependency, GUID, coordinate count
        textures = [p.qualified(x[0]) for x in r.array('i') if x[0]]
        samples = []
        for expr, e in enumerate(p.exports, 1):
            if e['outer'] != ref or not e['class'].startswith('MaterialExpressionTextureSample'):
                continue
            props, _ = p.properties(expr)
            texture = p.qualified(props.get('Texture', 0))
            if texture:
                samples.append(dict(texture=texture, parameter=str(props.get('ParameterName','')).lower(),
                                    expression=p.qualified(expr), dimension=3 if e['class'].endswith('Cube') else 2))
        self.resources[source.lower()] = textures, samples
        return textures, samples

    @staticmethod
    def channel(sample):
        name = sample['parameter'].lower()
        if name:
            if any(s in name for s in ('normal', 'bump')): return 'Normal'
            if any(s in name for s in ('specular', 'spec', 'rough')): return 'SpecularColor'
            if any(s in name for s in ('emiss', 'glow')): return 'EmissiveColor'
            if 'mask' in name: return 'OpacityMask'
            return 'DiffuseColor'
        # Cooked native references have no input wiring. This last-resort role
        # inference is reported; never attach a guessed mask as alpha-test data.
        name = sample['texture'].rsplit('.',1)[-1].lower()
        if re.search(r'(_n|_nrm|_normal|_norm)(?:_\d+)?$',name): return 'Normal'
        if re.search(r'(_s|_spec|_specular)(?:_\d+)?$',name): return 'SpecularColor'
        if re.search(r'(_e|_em|_emissive)(?:_\d+)?$',name): return 'EmissiveColor'
        if re.search(r'(_m|_mask)(?:_\d+)?$',name): return 'OpacityMask'
        return 'DiffuseColor'

    def preview_inputs(self, definition, inputs):
        if any(inputs[k]['textures'] or inputs[k]['constant'] is not None for k in ('DiffuseColor','EmissiveColor')):
            return inputs, None
        textures, original = self.resource(definition['root'])
        samples = []
        defaults = {s['texture'].lower() for s in original}
        used_parameters = set()
        for item in original:
            sample = dict(item, uv_scale=None)
            sample['texture'] = definition['parameters']['textures'].get(sample['parameter']) or sample['texture']
            used_parameters.add(sample['parameter']); samples.append(sample)
        for name, texture in definition['parameters']['textures'].items():
            if texture and name not in used_parameters:
                samples.append(dict(texture=texture, parameter=name, uv_scale=None, dimension=2, expression=None))
        for texture in textures:
            if texture.lower() not in defaults:
                try:
                    p, ref = self.library.object(texture)
                    if p.entry(ref)['class'] != 'Texture2D': continue
                except ValueError:
                    continue
                samples.append(dict(texture=texture, parameter='', uv_scale=None, dimension=2, expression=None))
        result = {k:dict(v, textures=list(v['textures']), samples=list(v['samples'])) for k,v in inputs.items()}
        for sample in samples:
            if sample['dimension'] != 2: continue
            channel = self.channel(sample)
            result[channel]['samples'].append(sample)
            if sample['texture'] not in result[channel]['textures']:
                result[channel]['textures'].append(sample['texture'])
        return result, ('Cooked graph links stripped: preview from surviving texture parameters and compiled-resource '
                        'references; input roles without parameters inferred from names; opacity wiring unavailable')
