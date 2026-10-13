"""Import installed Tribes: Vengeance models (static/reference pose, no game execution).

python -m tools.import_tvg --install "C:/Program Files (x86)/VUGames/Tribes Vengeance" --output build/tvg-data/tvg
Repeat --model Package.Object (shell-quoted wildcards accepted) for a bounded sample.
Output is a game folder; its textures are shared by the models and future maps.
"""
import argparse
from collections import Counter, defaultdict
import fnmatch
import hashlib
import json
from pathlib import Path
import re

if not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.texture_storage import dds_bytes, game_texture_dir, open_texture, write_texture
from tools.texture_workshop import write_json_atomic
from tools.tvg_package import Library


FORMATS = {0: 'P8', 3: 'DXT1', 5: 'RGBA8', 7: 'DXT3', 8: 'DXT5', 10: 'G16', 12: 'CxV8U8', 13: 'DXT5N'}
MATERIAL_CLASSES = {'Texture', 'Shader', 'Combiner', 'FinalBlend', 'TexPanner', 'TexScaler', 'TexRotator',
                    'TexOscillator', 'TexEnvMap', 'MaterialSwitch', 'MaterialSequence', 'ConstantColor',
                    'ColorModifier', 'OpacityModifier', 'Cubemap', 'FadeColor'}
# Explicit stock package identities. Unknown additions are sectioned separately;
# being installed in Art/Environments is not proof of stock provenance.
STOCK = set(('BaseObjects CivilianMale CliffTest Creatures CS_LoadScreens Deployables Destroyable Editor_res '
             'Engine_res EntrySky FX Glorax GUIContent GUITribes gui_Tex HeavyArmour HUD '
             'IGEffectsSystem_EditorTextures JuliaJnr LightArmour LightArmourFem LoadingScreens MediumArmour '
             'MPGameObjects Packs PhysicsObjects Placeholders PrefabEnvironments PrefabSkyboxes TribesFonts '
             'UDebugMenu_res UWindowFonts UWindow_res Vehicles Water Weather Alpine ArenaObjects BloodEagleBase '
             'Broadside CityInfrastructure ImperialBase ImperialPalace Lush Meadows PhoenixBase Rocky ShipInt Urban '
             'WasteLand Yacht Weapons').lower().split())


def key(name):
    clean = re.sub(r'[^a-z0-9_-]+', '_', name.lower()).strip('_')[:130]
    return clean + '_' + hashlib.sha256(name.lower().encode()).hexdigest()[:10]


def category(row):
    package = row['package']
    file = row['file'].replace('\\', '/').lower()
    stock = package in STOCK or file.startswith('content/cutscenes/art/cs_') or package.startswith('skybox')
    if file.endswith('.tvm'):
        stock = package.startswith(('sp-', 'cs-', 'prefabmap')) or package in ('entry', 'autoplay')
    section = 'Stock' if stock else 'Community / unclassified'
    if 'armour' in package:
        kind = 'Armour'
    elif package == 'vehicles' or 'atv' in package:
        kind = 'Vehicles'
    elif package == 'weapons' or package == 'cs_weapons':
        kind = 'Weapons'
    elif package in ('baseobjects', 'deployables', 'mpgameobjects'):
        kind = 'Base and turrets'
    elif package in ('glorax', 'creatures', 'civilianmale', 'juliajnr') or 'cutscenes/art/cs_' in file:
        kind = 'Characters and cutscenes'
    else:
        kind = 'Environment props'
    return f'{section} / {kind} / {row["source"].rsplit(".", 1)[0]}'


def exclusion(row):
    if row['cls'] == 'HkMeshProxy':
        return 'Havok collision proxy (not render geometry)'
    if row['package'] in ('editor_res', 'igeffectssystem_editortextures', 'placeholders', 'udebugmenu_res'):
        return 'Editor/helper package'
    # Explicit collision labels only; creatures/turrets are never excluded by shape.
    name = row['source'].split('.')[-1].lower()
    if 'collision' in name or name.startswith(('ucx_', 'ubx_', 'usp_', 'hkproxy')):
        return 'Collision-only mesh name'
    return None


def texture_payload(fmt, mips, palette=None):
    """Keep DXT blocks; PNG for uncompressed formats. No input mutation or re-encoding DXT."""
    if fmt not in FORMATS:
        raise ValueError(f'Unsupported texture format {fmt}')
    w, h, raw = mips[0]
    info = dict(format=FORMATS[fmt], width=w, height=h, mips=len(mips))
    if fmt in (3, 7, 8, 13):
        fourcc = {3: 'DXT1', 7: 'DXT3', 8: 'DXT5', 13: 'DXT5'}[fmt]
        for level, (mw, mh, _) in enumerate(mips):
            if (mw, mh) != (max(1, w >> level), max(1, h >> level)):
                raise ValueError('Non-contiguous mip dimensions')
        if fmt == 13:
            info['normal_encoding'] = 'DXT5N: X=alpha, Y=green, reconstruct positive Z'
        return '.dds', dds_bytes(w, h, fourcc, [m[2] for m in mips]), info
    if fmt == 0:
        if palette is None or len(raw) != w*h or len(palette) != 1024:
            raise ValueError('Invalid P8 pixels/palette')
        colors = np.frombuffer(palette, np.uint8).reshape(256, 4).copy()
        colors[:, 3] = 255
        colors[0, 3] = 0  # Shared image also works for masked materials; opaque slots ignore alpha.
        rgba = colors[np.frombuffer(raw, np.uint8)]
        return '.png', Image.fromarray(rgba.reshape(h, w, 4)), info
    if fmt == 5:
        if len(raw) != w*h*4:
            raise ValueError('Invalid RGBA8 byte count')
        return '.png', Image.frombytes('RGBA', (w, h), raw, 'raw', 'BGRA'), info
    if fmt == 10:
        if len(raw) != w*h*2:
            raise ValueError('Invalid G16 byte count')
        return '.png', Image.frombytes('I;16', (w, h), raw), info
    if len(raw) != w*h*2:
        raise ValueError('Invalid CxV8U8 byte count')
    xy = np.frombuffer(raw, np.int8).reshape(h, w, 2).astype(np.float32)/127
    xy = np.clip(xy, -1, 1)
    z = np.sqrt(np.maximum(0, 1-(xy*xy).sum(axis=2)))
    rgb = np.dstack((xy, z))
    info['normal_encoding'] = 'decoded CxV8U8: signed XY, reconstructed positive Z, RGB PNG'
    return '.png', Image.fromarray(np.rint((rgb+1)*127.5).astype(np.uint8), 'RGB'), info


class Materials:
    def __init__(self, library, texture_dir):
        self.library, self.directory = library, texture_dir
        self.textures, self.materials = {}, {}
        self.missing = 'missing_tvg.png'
        image = Image.new('RGBA', (2, 2))
        image.putdata([(255, 0, 255, 255), (32, 32, 32, 255)]*2)
        write_texture(texture_dir, self.missing, image)

    def texture(self, source):
        k = source.lower()
        if k in self.textures:
            return self.textures[k]
        row = dict(source=source, status='unresolved')
        self.textures[k] = row
        try:
            p, ref = self.library.object(source, {'Texture'})
            props, mips = p.texture(ref)
            fmt = props.get('Format', 0)
            palette_source = p.qualified(props.get('Palette', 0))
            palette = None
            if fmt == 0 and palette_source:
                pp, pr = self.library.object(palette_source, {'Palette'})
                pp.properties(pr)
                count = pp.count()
                palette = pp.take(count*4)
            ext, payload, info = texture_payload(fmt, mips, palette)
            filename = key(source)+ext
            write_texture(self.directory, filename, payload)
            row.update(info, texture=filename, status='ready')
        except (ValueError, OSError) as exc:
            row.update(reason=str(exc), texture=self.missing)
        return row

    def image(self, row):
        with open_texture(self.directory/row['texture']) as image:
            return image.convert('RGBA')

    def derived(self, source, image, purpose):
        filename = key(source+'_'+purpose)+'.png'
        write_texture(self.directory, filename, image)
        return filename

    def material(self, source, trail=()):
        if not source:
            return dict(source=None, texture=self.missing, settings={}, status='unresolved', reason='Empty material slot')
        k = source.lower()
        if k in trail:
            raise ValueError('Material graph cycle: ' + source)
        if k in self.materials:
            return self.materials[k]
        row = dict(source=source, texture=self.missing, settings={}, status='unresolved', notes=[])
        self.materials[k] = row
        try:
            p, ref = self.library.object(source, MATERIAL_CLASSES)
            cls = p.ref_name(p.exports[ref-1]['cls'])
            props = p.properties(ref)
            row['class'] = cls
            refs = {n: p.qualified(props[n]) for n in ('Diffuse', 'Opacity', 'NormalMap', 'Specular', 'SpecularityMask',
                    'SelfIllumination', 'Material', 'Material1', 'Material2', 'Mask', 'PerPixelReflection')
                    if props.get(n)}
            row['references'] = refs
            settings = row['settings']
            if cls == 'Texture':
                tex = self.texture(source)
                row.update(texture=tex['texture'], status=tex['status'])
                if tex['status'] != 'ready':
                    row['reason'] = tex['reason']
                if props.get('bMasked'):
                    settings['alphaTest'] = .5
                settings['clamp'] = bool(props.get('UClampMode') or props.get('VClampMode'))
                return row
            if cls == 'Combiner':
                a = self.material(refs.get('Material1'), trail+(k,))
                b = self.material(refs.get('Material2'), trail+(k,))
                mask = self.material(refs.get('Mask'), trail+(k,)) if refs.get('Mask') else a
                op = props.get('CombineOperation', 0)
                if op in range(8):
                    images = [self.image(x) for x in (a, b, mask)]
                    size = tuple(max(image.size[d] for image in images) for d in (0, 1))
                    aa, bb, mm = [np.asarray(image.resize(size), dtype=np.float32)/255 for image in images]
                    alpha = mm[:, :, 3:4]
                    if props.get('InvertMask'):
                        alpha = 1-alpha
                    color = {0: lambda: aa[:, :, :3], 1: lambda: bb[:, :, :3],
                             2: lambda: aa[:, :, :3]*bb[:, :, :3], 3: lambda: aa[:, :, :3]+bb[:, :, :3],
                             4: lambda: aa[:, :, :3]-bb[:, :, :3],
                             5: lambda: aa[:, :, :3]*alpha+bb[:, :, :3]*(1-alpha),
                             6: lambda: aa[:, :, :3]+bb[:, :, :3]*alpha, 7: lambda: mm[:, :, :3]}
                    if op not in color:
                        raise ValueError(f'Unsupported combiner operation {op}')
                    rgb = color[op]() * (4 if props.get('Modulate4X') else 2 if props.get('Modulate2X') else 1)
                    ao = props.get('AlphaOperation', 0)
                    alpha = {0: alpha, 1: aa[:, :, 3:4]*bb[:, :, 3:4], 2: aa[:, :, 3:4]+bb[:, :, 3:4],
                             3: aa[:, :, 3:4], 4: bb[:, :, 3:4]}.get(ao, alpha)
                    img = Image.fromarray(np.rint(np.clip(np.dstack((rgb, alpha)), 0, 1)*255).astype(np.uint8))
                    used = [a] if op == 0 else [b] if op == 1 else [mask] if op == 7 else [a, b]
                    used += [mask] if ao == 0 else [a] if ao == 3 else [b] if ao == 4 else [a, b]
                    if op in (5, 6):
                        used.append(mask)
                    row.update(texture=self.derived(source, img, 'combiner'),
                               status='ready' if all(x['status']=='ready' for x in used) else 'unresolved')
                    if row['status'] != 'ready':
                        row['reason'] = 'Unresolved combiner input'
                    row['notes'].append('Static material composition; source textures retained')
                    if any(x['settings'].get(v) for x in used for v in ('uvScale', 'uvOffset', 'uvRotation')):
                        row['notes'].append('Independent combiner input UV modifiers are not baked')
                else:
                    raise ValueError(f'Unsupported combiner operation {op}')
                return row
            if cls in ('ConstantColor', 'FadeColor'):
                color = props.get('Color', props.get('Color1', (255, 255, 255, 255)))
                row.update(texture=self.derived(source, Image.new('RGBA', (1, 1), tuple(color)), 'color'), status='ready')
                return row
            child = (refs.get('Diffuse') or refs.get('SelfIllumination')) if cls == 'Shader' else refs.get('Material')
            if cls == 'MaterialSwitch':
                options = p.refs(props.get('Materials', b'\0'))
                selected = props.get('Current', 0)
                child = p.qualified(options[selected]) if 0 <= selected < len(options) else None
                row['notes'].append('MaterialSwitch at serialized Current')
            if cls == 'MaterialSequence':
                seq = p.struct_array(props.get('SequenceItems', b'\0'))
                child = p.qualified(seq[0].get('Material', 0)) if seq else None
                row['notes'].append('MaterialSequence first item; no playback')
            if cls == 'Cubemap':
                child = p.qualified(props.get('Faces', 0))
                row['notes'].append('Cubemap first face preview')
            if not child:
                raise ValueError(f'{cls} has no supported diffuse/material input')
            base = self.material(child, trail+(k,))
            row.update(texture=base['texture'], status=base['status'], settings=dict(base['settings']))
            settings = row['settings']
            if cls == 'Shader' and not refs.get('Diffuse') and refs.get('SelfIllumination'):
                settings['unlit'] = True
                row['notes'].append('Self-illumination used as color for shader without diffuse')
            if base['status'] != 'ready':
                row['reason'] = base.get('reason', 'Unresolved child material')
            if cls == 'Shader':
                mode = props.get('OutputBlending', 0)
                if mode == 1:
                    settings['alphaTest'] = .5
                blends = {2: ['gl_dst_color', 'gl_src_color'], 3: ['gl_src_alpha', 'gl_one_minus_src_alpha'],
                          4: ['gl_one', 'gl_one_minus_src_color'], 5: ['gl_zero', 'gl_one'],
                          6: ['gl_one', 'gl_one'], 7: ['gl_zero', 'gl_one_minus_src_color'],
                          8: ['gl_src_alpha', 'gl_one_minus_src_alpha']}
                if mode in blends:
                    settings.update(blend=blends[mode], depthWrite=False)
                if refs.get('Opacity'):
                    opacity = self.material(refs['Opacity'], trail+(k,))
                    if opacity['texture'] != row['texture']:
                        image = self.image(row)
                        image.putalpha(self.image(opacity).resize(image.size).getchannel('A'))
                        row['texture'] = self.derived(source, image, 'opacity')
                    if opacity['status'] != 'ready':
                        row.update(status='unresolved', reason='Unresolved shader opacity')
                    if mode == 0:
                        settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'], depthWrite=False)
                row['recorded_maps'] = {}
                for field in ('NormalMap', 'Specular', 'SpecularityMask'):
                    if refs.get(field):
                        extra = self.material(refs[field], trail+(k,))
                        row['recorded_maps'][field] = dict(source=refs[field], texture=extra['texture'], status=extra['status'])
                        tex = self.textures.get(refs[field].lower(), {})
                        if 'normal_encoding' in tex:
                            row['recorded_maps'][field]['normal_encoding'] = tex['normal_encoding']
                if row['recorded_maps']:
                    row['notes'].append('Normal/specular maps recorded, not rendered')
                if refs.get('Diffuse') and refs.get('SelfIllumination'):
                    row['notes'].append('Separate self-illumination layer recorded, not composited')
            if cls == 'FinalBlend':
                mode = props.get('FrameBufferBlending', 0)
                blends = {1: ['gl_dst_color', 'gl_src_color'], 2: ['gl_src_alpha', 'gl_one_minus_src_alpha'],
                          3: ['gl_dst_color', 'gl_one_minus_src_alpha'], 4: ['gl_one', 'gl_one_minus_src_color'],
                          5: ['gl_zero', 'gl_one_minus_src_color'], 6: ['gl_one', 'gl_one'], 7: ['gl_zero', 'gl_one'],
                          8: ['gl_zero', 'gl_one_minus_src_alpha'], 9: ['gl_zero', 'gl_src_color']}
                if mode in blends:
                    settings['blend'] = blends[mode]
                if props.get('AlphaTest'):
                    settings['alphaTest'] = props.get('AlphaRef', 128)/255
                settings['depthWrite'] = props.get('ZWrite', True)
            if cls == 'OpacityModifier' and refs.get('Opacity'):
                opacity = self.material(refs['Opacity'], trail+(k,))
                image = self.image(row)
                image.putalpha(self.image(opacity).resize(image.size).getchannel('A'))
                row['texture'] = self.derived(source, image, 'opacity')
                settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'], depthWrite=False)
                if opacity['status'] != 'ready':
                    row.update(status='unresolved', reason='Unresolved modifier opacity')
            if cls == 'ColorModifier':
                pixels = np.asarray(self.image(row), dtype=np.float32)
                pixels *= np.asarray(props.get('Color', (255, 255, 255, 255)))/255
                row['texture'] = self.derived(source, Image.fromarray(np.rint(pixels).astype(np.uint8)), 'color')
                if props.get('AlphaBlend', True):
                    settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'], depthWrite=False)
            if props.get('TwoSided') or cls == 'ColorModifier' and props.get('RenderTwoSided', True):
                settings['cull'] = 'none'
            if cls == 'TexScaler':
                us, vs = props.get('UScale', 1), props.get('VScale', 1)
                if not us or not vs:
                    raise ValueError('Zero texture scale')
                settings['uvScale'] = [1/us, 1/vs]
                settings['uvOffset'] = [props.get('UOffset', 0), props.get('VOffset', 0)]
            if cls == 'TexRotator':
                settings['uvRotation'] = props.get('Rotation', (0, 0, 0))[1] * np.pi/32768
            if cls in ('TexPanner', 'TexOscillator', 'TexRotator', 'TexEnvMap'):
                row['notes'].append(f'{cls} evaluated at static time zero; dynamic/environment effects deferred')
            return row
        except (ValueError, OSError) as exc:
            row.update(status='unresolved', reason=str(exc))
            return row


def looks_from_classes(lib, selected):
    """Serialized class/team assignments, not guessed tint or texture-name matches."""
    looks, failures, assignments, roles = defaultdict(list), [], [], []

    def add(mesh, label, overrides=None, evidence=None, cls='SkeletalMesh'):
        if mesh and mesh.lower() in selected:
            looks[(cls, mesh.lower())].append(dict(label=label, overrides=overrides or [], assignment=evidence or label))

    for name in sorted(lib.classes):
        # Restrict decoding to classes relevant to appearance, using inheritance metadata first.
        chain, current = [], name
        while current and current not in chain:
            chain.append(current)
            parent = lib.classes.get(current, {}).get('base')
            current = parent.lower() if parent else None
        if not any(c in chain for c in ('gameplay.skininfo', 'gameplay.character', 'gameplay.teaminfo',
                                       'gameplay.vehicle', 'gameplay.turret', 'gameplay.weapon', 'gameplay.jetpack')):
            continue
        try:
            props, chain = lib.class_info(name)
            if 'gameplay.skininfo' in chain:
                mesh = lib.value_ref(props.get('applicableMesh', ('', 0)))
                skins = lib.value_refs(props['meshSkins']) if 'meshSkins' in props else []
                add(mesh, name, skins)
                if mesh:
                    assignment = dict(classname=name, mesh=mesh, skins=skins,
                                            jetpack_skins=lib.value_refs(props['jetpackSkins']) if 'jetpackSkins' in props else [],
                                            arms_skins=lib.value_refs(props['armsSkins']) if 'armsSkins' in props else [])
                    assignments.append(assignment)
            elif 'gameplay.teaminfo' in chain:
                if 'combatRoleData' in props:
                    package, raw = props['combatRoleData']
                    p = lib.package(package)
                    team_roles = [{k: p.qualified(v) if v else None for k, v in r.items()}
                                  for r in p.struct_array(raw)]
                    roles.extend(team_roles)
                    for role in team_roles:
                        assignments.append(dict(team=name, **role))
                        for field in ('Mesh', 'femaleMesh', 'armsMesh'):
                            add(role.get(field), name+'.'+str(role.get('Role', '')).split('.')[-1]+'.'+field)
                for field, mesh in (('buggySkin', 'Vehicles.Buggy'), ('podSkin', 'Vehicles.Pod'),
                                    ('tankSkin', 'Vehicles.Tank'), ('assaultShipSkin', 'Vehicles.AssaultShip')):
                    if field in props:
                        add(mesh, name+'.'+field, [lib.value_ref(props[field])])
            else:
                mesh = lib.value_ref(props.get('Mesh', ('', 0)))
                cls = 'SkeletalMesh' if mesh else 'StaticMesh'
                mesh = mesh or lib.value_ref(props.get('StaticMesh', ('', 0)))
                overrides = lib.value_refs(props['Skins']) if 'Skins' in props else []
                add(mesh, name, overrides, cls=cls)
        except (ValueError, OSError) as exc:
            failures.append(dict(classname=name, reason=str(exc)))
    # SkinInfo also assigns the role's jetpack/arms. Follow the team's class links;
    # attachment pieces remain independent models, reusable by maps.
    for skin in (a for a in assignments if 'classname' in a):
        for role in roles:
            for field, jet in (('Mesh', 'jetpack'), ('femaleMesh', 'femaleJetpack')):
                if (role.get(field) or '').lower() != skin['mesh'].lower():
                    continue
                if skin['arms_skins']:
                    add(role.get('armsMesh'), skin['classname']+'.arms', skin['arms_skins'])
                if skin['jetpack_skins'] and role.get(jet):
                    try:
                        props, _ = lib.class_info(role[jet])
                        mesh = lib.value_ref(props.get('StaticMesh', ('', 0)))
                        cls = 'StaticMesh' if mesh else 'SkeletalMesh'
                        mesh = mesh or lib.value_ref(props.get('Mesh', ('', 0)))
                        add(mesh, skin['classname']+'.jetpack', skin['jetpack_skins'], cls=cls)
                    except (ValueError, OSError) as exc:
                        failures.append(dict(classname=role[jet], reason=str(exc)))
    return looks, failures, assignments


def import_catalog(install, output, model_paths=None):
    output, install = Path(output).resolve(), Path(install).resolve()
    if output == install or install in output.parents:
        raise ValueError('Import output must be outside the read-only game install')
    if output.name != 'tvg':
        raise ValueError('Output must be a game folder named tvg (for example build/tvg-data/tvg)')
    lib = Library(install)
    try:
        objects = sorted(lib.objects.values(), key=lambda r: (r['source'].lower(), r['cls']))
        if model_paths is not None:
            for pattern in model_paths:
                if not any(fnmatch.fnmatchcase(r['source'].lower(), pattern.lower()) for r in objects):
                    raise ValueError('Unknown model selector: ' + pattern)
            objects = [r for r in objects if any(fnmatch.fnmatchcase(r['source'].lower(), s.lower()) for s in model_paths)]
        selected = {r['source'].lower() for r in objects if r['cls'] != 'HkMeshProxy'}
        looks, class_failures, assignments = looks_from_classes(lib, selected)
        mats = Materials(lib, game_texture_dir(output.parent, 'tvg'))
        records, catalog, index = [], [], {}
        for obj in objects:
            row = dict(obj, category=category(obj), status='pending')
            records.append(row)
            source, cls = row['source'], row['cls']
            ident = key(cls+':'+source)
            slot_index = index.setdefault(source.lower(), dict(source=source, by_class={}))['by_class']
            try:
                reason = exclusion(row)
                if reason:
                    raise ValueError(reason)
                p = lib.package(row['package'])
                mesh = p.mesh(row['ref'])
                native_refs = [p.qualified(r) for r in mesh.pop('material_refs')]
                mesh['metadata'].update(source=source, source_class=cls, package_version=p.version, licensee=p.licensee)
                variants = [dict(label='Default', overrides=[], assignment='mesh material slots')]
                variants += looks.get((cls, source.lower()), [])
                # Collapse identical material assignments while retaining all class/team aliases.
                families = {}
                for variant in variants:
                    resolved = list(native_refs)
                    for slot, material in enumerate(variant['overrides']):
                        if slot < len(resolved) and material:
                            resolved[slot] = material
                    family = tuple(n.lower() if n else '' for n in resolved)
                    if family not in families:
                        families[family] = dict(refs=resolved, label=variant['label'], assignments=[])
                    families[family]['assignments'].append(variant['assignment'])
                exported = []
                for number, family in enumerate(families.values()):
                    material_rows = [mats.material(n) for n in family['refs']]
                    look = ident if number == 0 else ident+'_skin_'+key(family['label'])
                    data = dict(mesh, game='tvg', material_textures=[m['texture'] for m in material_rows],
                                material_settings=[m['settings'] for m in material_rows],
                                metadata=dict(mesh['metadata'], skin=family['label'], assignments=family['assignments'],
                                              materials=material_rows, animation_playback=False))
                    path = output/'model_json'/(look+'.json')
                    write_model_data(path, data)
                    buffers = json.loads(path.read_text(encoding='utf-8'))['geometry_buffers']
                    exported.append(dict(id=look, label=family['label'], assignments=family['assignments']))
                    used = {g['materialIndex'] for g in mesh['groups']}
                    unresolved = sum(material_rows[i]['status'] != 'ready' for i in used)
                    catalog.append(dict(game='tvg', model_name=look, display_name=source+' ['+cls+']'+
                                        ('' if number == 0 else ' / '+family['label']), category=row['category'],
                                        source=source, status='ready', texture_name=material_rows[mesh['groups'][0]['materialIndex']]['texture'],
                                        warnings=[f'{unresolved} unresolved material slots; see import report'] if unresolved else []))
                row.update(status='ready', looks=[v['id'] for v in exported], vertices=len(mesh['vertices'])//3,
                           triangles=len(mesh['indices'])//3, pose=mesh['metadata']['pose'])
                slot_index[cls] = dict(status='ready', looks=row['looks'], variants=exported, geometry=buffers,
                                       model_json='model_json/'+exported[0]['id']+'.json', lod=0,
                                       coordinate_system=mesh['metadata']['coordinate_system'])
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc))
                slot_index[cls] = dict(status='skipped', reason=str(exc))
        for entry in index.values():
            if len(entry['by_class']) == 1:
                entry.update(next(iter(entry['by_class'].values())))
        report = dict(game='tvg', sample=model_paths is not None, objects=len(records),
                      ready=sum(r['status']=='ready' for r in records), entries=len(catalog),
                      skipped=sum(r['status']=='skipped' for r in records),
                      skipped_reasons=dict(Counter(r['reason'] for r in records if r['status']=='skipped')),
                      unresolved_materials=sum(r['status']!='ready' for r in mats.materials.values()),
                      unresolved_textures=sum(r['status']!='ready' for r in mats.textures.values()),
                      textures=len(mats.textures), class_failures=class_failures, assignments=assignments,
                      results=records, material_results=list(mats.materials.values()),
                      texture_results=list(mats.textures.values()), inventory=lib.inventory, package_failures=lib.failures,
                      rules=dict(pose='static/reference LOD0; no animation or Mojo',
                                 storage='shared tvg/textures, original DXT DDS, indexed float32 geometry shared by skins',
                                 index='case-insensitive package-qualified key; select by_class when names collide',
                                 categories='explicit stock package identities; unknown additions Community / unclassified'))
        report['issues'] = report['skipped'] + report['unresolved_materials'] + len(class_failures) + len(lib.failures)
        report['report'] = str(output/'import-report.json')
        write_json_atomic(output/'catalog.json', catalog)
        write_json_atomic(output/'model-index.json', index)
        write_json_atomic(output/'import-report.json', report)
        return report
    finally:
        lib.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA/'tvg')
    parser.add_argument('--model', action='append', help='Package-qualified object name or wildcard; repeat for samples')
    args = parser.parse_args()
    result = import_catalog(args.install, args.output, args.model)
    print(json.dumps({k: v for k, v in result.items() if k not in ('results', 'material_results', 'texture_results', 'inventory', 'assignments')}, indent=2))
