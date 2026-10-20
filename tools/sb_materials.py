"""Spellbreak (UE 4.22) materials: base-colour texture through the MIC parent chain.

Cooked UE4 materials keep no expression graph, so a slot's diffuse is the first
base-colour texture parameter on the instance chain, else the base material's
own imported textures (normal, packed-mask and noise maps excluded by name).
"""
import hashlib
import io
import re
import struct

import numpy as np
from PIL import Image

from tools.texture_storage import dds_bytes, write_texture

DIFFUSE_PARAMETERS = ('diffusetexture', 'albedo', 'basecolor', 'base color', 'primary basecolor', 'basecolour',
                      'diffuse', 'basetexture', 'base texture', 'color', 'colour', 'rgb texture', 'texture', 'texture 1',
                      'maintexture', 'main texture', 'tex')
NOT_DIFFUSE = re.compile(r'normal|_n$|_nm$|_nrm|rod|rso|orm|mask|noise|erosion|erode|gradient|displace|ramp|rough|'
                         r'_ao|aomap|height|flow|distort|packed|detail|grunge|opacity|alpha|lut|_mra|metal|spec', re.I)
DIFFUSE_NAME = re.compile(r'albedo|base ?colou?r|diffuse', re.I)
SECONDARY_LAYER = re.compile(r'far|normal|mask|detail|moss|dust|grunge|snow|ramp', re.I)
BLOCK = {'PF_DXT1': ('DXT1', 8), 'PF_DXT5': ('DXT5', 16), 'PF_DXT3': ('DXT3', 16)}
DX10 = {'PF_BC4': (80, 8), 'PF_BC5': (83, 16), 'PF_BC7': (98, 16)}  # DXGI formats Pillow decodes
MAX_PNG = 2048


def texture_mips(p, ref):
    """Cooked FTexturePlatformData: (props, format, [(w, h, bytes)]) with resident mips only."""
    props, r = p.properties(ref)
    r.take(4)  # UTexture and UTexture2D strip flags
    if not r.bool32():
        raise ValueError('Texture is not cooked')
    if r.name() == 'None':
        raise ValueError('Texture has no platform data')
    r.take(8)  # skip offset
    width, height, slices = r.unpack('iii'); fmt = r.text(); r.take(4)
    mips = []
    for _ in range(r.count(20, 32)):
        r.take(4)
        raw = p.bulk(r)
        w, h, _ = r.unpack('iii')
        if raw is not None:
            mips.append((w, h, raw))
    if not mips:
        raise ValueError('No resident texture mips')
    return props, fmt, mips


def dx10_image(fmt, w, h, raw):
    code, block = DX10[fmt]
    if len(raw) < ((w+3)//4)*((h+3)//4)*block:
        raise ValueError('Short block-compressed mip')
    header = bytearray(148); header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x81007, h, w, len(raw), 0, 1)
    struct.pack_into('<II4s', header, 76, 32, 4, b'DX10')
    struct.pack_into('<I', header, 108, 0x1000)
    struct.pack_into('<5I', header, 128, code, 3, 0, 1, 0)
    image = Image.open(io.BytesIO(bytes(header)+raw)); image.load()
    return image


def texture_payload(fmt, mips):
    w, h, raw = mips[0]
    info = dict(format=fmt, width=w, height=h, mips=len(mips))
    if fmt in BLOCK:
        fourcc, block = BLOCK[fmt]
        chain = []
        for level, (mw, mh, data) in enumerate(mips):
            if (mw, mh) != (max(1, w >> level), max(1, h >> level)):
                break
            chain.append(data[:((mw+3)//4)*((mh+3)//4)*block])
        return '.dds', dds_bytes(w, h, fourcc, chain), info
    level = next((i for i, m in enumerate(mips) if max(m[0], m[1]) <= MAX_PNG), len(mips)-1)
    w, h, raw = mips[level]
    if fmt in DX10:
        image = dx10_image(fmt, w, h, raw)
    elif fmt == 'PF_B8G8R8A8' and len(raw) >= w*h*4:
        image = Image.frombytes('RGBA', (w, h), raw[:w*h*4], 'raw', 'BGRA')
    elif fmt == 'PF_G8' and len(raw) >= w*h:
        image = Image.frombytes('L', (w, h), raw[:w*h])
    elif fmt == 'PF_FloatRGBA' and len(raw) >= w*h*8:
        rgba = np.frombuffer(raw[:w*h*8], '<f2').reshape(h, w, 4).astype(np.float32)
        image = Image.fromarray(np.rint(np.clip(rgba, 0, 1)**(1/2.2)*255).astype(np.uint8), 'RGBA')
    else:
        raise ValueError(f'Unsupported texture format {fmt}')
    info.update(width=w, height=h, png_level=level)
    stream = io.BytesIO(); image.save(stream, format='PNG')
    return '.png', stream.getvalue(), info


class Materials:
    def __init__(self, library, directory):
        self.library, self.directory = library, directory
        self.textures, self.materials, self.failures = {}, {}, []
        self.missing = 'missing_sb.png'
        image = Image.new('RGBA', (2, 2)); image.putdata([(255, 0, 255, 255), (32, 32, 32, 255)]*2)
        write_texture(directory, self.missing, image)

    def texture(self, source):
        key = source.lower()
        if key in self.textures:
            return self.textures[key]
        row = dict(source=source, texture=self.missing, status='unresolved')
        self.textures[key] = row
        try:
            p, ref = self.library.object(source, {'Texture2D'})
            props, fmt, mips = texture_mips(p, ref)
            ext, payload, info = texture_payload(fmt, mips)
            digest = hashlib.sha256(payload).hexdigest()
            name = re.sub('[^a-z0-9_-]+', '_', source.rsplit('.', 1)[-1].lower()).strip('_')[:80] or 'texture'
            row.update(texture=f'{name}_{digest[:12]}{ext}', status='ready', **info)
            write_texture(self.directory, row['texture'], payload)
        except (ValueError, OSError, struct.error) as exc:
            row['reason'] = str(exc); self.failures.append(dict(source=source, reason=str(exc)))
        return row

    def chain(self, source):
        """Instance chain from the slot material to its base UMaterial: [(source, class, props)]."""
        result = []
        while source and len(result) < 16:
            p, ref = self.library.object(source)
            props, _ = p.properties(ref)
            cls = p.entry(ref)['class']
            result.append((source, cls, props, p))
            source = props.get('Parent') if cls.startswith('MaterialInstance') else None
        return result

    def material(self, source):
        if not source:
            return dict(source=None, texture=self.missing, settings={}, status='unresolved', reason='Empty material slot')
        key = source.lower()
        if key in self.materials:
            return self.materials[key]
        row = dict(source=source, texture=self.missing, settings={}, status='unresolved')
        self.materials[key] = row
        try:
            chain = self.chain(source)
            diffuse = None
            for _, _, props, _ in chain:
                params = {((v.get('ParameterInfo') or {}).get('Name') or '').lower(): v.get('ParameterValue')
                          for v in props.get('TextureParameterValues') or [] if isinstance(v, dict)}
                diffuse = next((params[n] for n in DIFFUSE_PARAMETERS if params.get(n)), None)
                if not diffuse:  # 'VillageTilingPristine Basecolor', 'Primary Basecolor': a layer's own colour map
                    named = sorted((n for n in params if params[n] and DIFFUSE_NAME.search(n) and not SECONDARY_LAYER.search(n)),
                                   key=lambda n: ('primary' not in n and 'pristine' not in n, n))
                    diffuse = params[named[0]] if named else None
                if diffuse:
                    row['parameter'] = 'instance'
                    break
            base_source, base_cls, base, base_package = chain[-1]
            if not diffuse:
                imports = [base_package.qualified(-i) for i, e in enumerate(base_package.imports, 1) if e['class_name'] == 'Texture2D']
                candidates = [t for t in imports if not NOT_DIFFUSE.search(t.rsplit('.', 1)[-1])]
                diffuse = candidates[0] if candidates else None
                row['parameter'] = 'base material texture'
            settings = row['settings']
            overrides = {}
            for _, _, props, _ in reversed(chain):
                overrides.update({k: v for k, v in (props.get('BasePropertyOverrides') or {}).items()})
            blend = overrides.get('BlendMode') if overrides.get('bOverride_BlendMode') else base.get('BlendMode')
            two_sided = overrides.get('TwoSided') if overrides.get('bOverride_TwoSided') else base.get('TwoSided')
            if two_sided:
                settings['cull'] = 'none'
            if blend == 'BLEND_Masked':
                clip = overrides.get('OpacityMaskClipValue') if overrides.get('bOverride_OpacityMaskClipValue') else base.get('OpacityMaskClipValue')
                settings['alphaTest'] = .3333 if clip is None else clip
            elif blend == 'BLEND_Translucent':
                settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'], depthWrite=False)
            elif blend == 'BLEND_Additive':
                settings.update(blend=['gl_src_alpha', 'gl_one'], depthWrite=False)
            elif blend == 'BLEND_Modulate':
                settings.update(blend=['gl_dst_color', 'gl_zero'], depthWrite=False)
            if (base.get('ShadingModel') or '') == 'MSM_Unlit':
                settings['unlit'] = True
            row['base_material'] = base_source
            if diffuse:
                texture = self.texture(diffuse)
                row.update(texture=texture['texture'], texture_source=diffuse, status=texture['status'])
                if texture['status'] != 'ready':
                    row['reason'] = texture.get('reason')
            else:
                row['reason'] = 'No base-colour texture on the instance chain or base material'
        except (ValueError, OSError, struct.error) as exc:
            row['reason'] = str(exc); self.failures.append(dict(source=source, reason=str(exc)))
        return row
