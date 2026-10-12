"""Stock Deus Ex model catalog policy; binary readers and geometry live in import_unreal.

Use import_unreal.py --game dx --install <folder>. No maps or scripts are exported.
Class defaults, texture selection and pose selection are shared with the dx map importer.
"""
import collections
import configparser
import hashlib
import json
import math
from pathlib import Path
import struct

from PIL import Image

try:
    from tools.import_unreal import (Library, INVISIBLE, STYLE_FLAGS, build_model, texture_asset,
                                     mesh_faces, safe)
    from tools.model_data import write_model_data
    from tools.texture_storage import write_texture
except ImportError:
    from import_unreal import (Library, INVISIBLE, STYLE_FLAGS, build_model, texture_asset,
                               mesh_faces, safe)
    from model_data import write_model_data
    from texture_storage import write_texture

MESH_CLASSES = {'Mesh', 'LodMesh'}
ERRORS = (OSError, ValueError, IndexError, KeyError, struct.error, TypeError)
# DeusEx.u ScriptText: Human/ScriptedPawn/Animal.PlayWaiting use BreatheLight, Robot uses Idle.
# Stand is a crouch-to-standing transition on the human meshes, so it is a last resort.
POSES = ('breathelight', 'idle', 'still', 'idle1', 'breath', 'breath1', 'stand')


def standing_pose(mesh):
    """(frame, sequence, reason) for the unarmed waiting pose; no animation clips are baked."""
    names = {s['name'].lower(): s for s in mesh['seqs']}
    for name in POSES:
        seq = names.get(name)
        if seq and seq['frames'] > 0 and 0 <= seq['start'] < mesh['frames']:
            return seq['start'], seq['name'], 'first frame of available waiting/standing sequence'
    return 0, None, 'no supported waiting/standing sequence; frame 0'


def actor_texture(package, mesh, props, owners, slot):
    """The actor's slot override, Skin (slot 0 or empty), mesh texture, Texture, last MultiSkin.

    SurrealEngine Render/VisibleMesh.cpp SetupMeshTextures, checked against DX class defaults.
    Owners preserves cross-package inheritance: a reference is relative to where it was declared.
    DefaultTexture is handled by the caller, after all actor choices.
    """
    def prop(key):
        value = props.get(key)
        return (owners[key], value) if isinstance(value, int) and value and key in owners else None

    own = mesh['textures'][slot] if 0 <= slot < len(mesh['textures']) else 0
    multi = prop('MultiSkins' if slot == 0 else f'MultiSkins[{slot}]')
    return (multi or (prop('Skin') if slot == 0 or not own else None) or
            ((package, own) if own else None) or prop('Texture') or
            next((pick for i in reversed(range(min(8, len(mesh['textures']))))
                  if (pick := prop('MultiSkins' if i == 0 else f'MultiSkins[{i}]'))), None))


def category(chain, package):
    """Class ancestry first; package provenance for meshes with no actor class."""
    names = {n.lower() for n in chain}
    for bases, label in (({'robot'}, 'Robots'), ({'animal'}, 'Animals'),
                         ({'scriptedpawn', 'pawn', 'deusexplayer'}, 'Characters'),
                         ({'helicopter', 'vehicle', 'vehicles'}, 'Vehicles'),
                         ({'weapon', 'deusexweapon'}, 'Weapons'), ({'inventory'}, 'Items and pickups'),
                         ({'effects', 'effect', 'projectile'}, 'Effects'),
                         ({'decoration', 'deusexdecoration'}, 'Decorations')):
        if names & bases:
            return label
    return {'deusexcharacters': 'Characters', 'mpcharacters': 'Characters', 'deusexdeco': 'Decorations',
            'deusexitems': 'Items and pickups', 'deusexui': 'UI'}.get(package.lower(), 'Other meshes')


def mesh_views(props, chain):
    """Carcasses expose all three meshes, inventory its two views, other actors Mesh."""
    if any(n.lower() in ('carcass', 'deusexcarcass') for n in chain):
        return [key for key in ('Mesh', 'Mesh2', 'Mesh3') if props.get(key)]
    if any(n.lower() == 'inventory' for n in chain):
        views = [key for key in ('PickupViewMesh', 'ThirdPersonMesh') if props.get(key)]
        return views or (['Mesh'] if props.get('Mesh') else [])
    return ['Mesh'] if props.get('Mesh') else []


def map_actor_kind(chain):
    """Stock map snapshot: no Pawn ancestry; carcasses are decorations, not living NPCs."""
    names = {n.lower() for n in chain}
    if names & {'pawn', 'scriptedpawn', 'deusexplayer'}:
        return 'npc'
    if 'mover' in names:
        return 'mover'
    if names & {'carcass', 'deusexcarcass'}:
        return 'carcass'
    if 'inventory' in names:
        return 'pickup'
    if names & {'decoration', 'deusexdecoration'}:
        return 'decoration'
    return 'other'


def map_group(name):
    """Filename conventions, including mission 99 endings and the three non-mission utility maps."""
    if name.upper().startswith('DXMP_'):
        return 'Multiplayer'
    number, _, rest = name.partition('_')
    if len(number) == 2 and number.isdecimal() and rest:
        return 'Endings' if number == '99' else f'Mission {number}'
    return 'Other maps'


def display_settings(install):
    """INI settings plus the Unreal page's display approximation; not a calibrated Glide gamma curve."""
    ini = configparser.ConfigParser(strict=False, interpolation=None)
    ini.read(Path(install) / 'System' / 'DeusEx.ini', encoding='latin-1')
    try:
        brightness = ini.getfloat('WinDrv.WindowsClient', 'Brightness', fallback=.6)
    except ValueError:
        brightness = .6
    if not math.isfinite(brightness) or not 0 < brightness <= 1:
        brightness = .6
    return dict(brightness=brightness, power=1 / (2.5 * brightness),
                renderer=ini.get('Engine.Engine', 'GameRenderDevice', fallback='unknown'),
                windowed_renderer=ini.get('Engine.Engine', 'WindowedRenderDevice', fallback='unknown'),
                source='System/DeusEx.ini; brightness 0.6 if absent/invalid',
                approximation='Unreal page display curve; not calibrated against Deus Ex renderer output')


def import_catalog(install, output):
    install, output = Path(install).expanduser(), Path(output)
    if output.resolve() == install.resolve() or install.resolve() in output.resolve().parents:
        raise ValueError('Import output must be outside the read-only game install')
    library = Library(install, game='dx')
    if not library.package('DeusEx'):
        raise ValueError(f'No readable stock Deus Ex install (System/DeusEx.u) in {install}')
    for folder in ('model_json', 'textures'):
        (output / folder).mkdir(parents=True, exist_ok=True)
    report = dict(game='dx', packages=[], excluded=library.excluded,
                  exclusion_reason='non-stock System package or DXRando name; nested mod folders are never searched',
                  meshes=[], classes=[], aliases=[],
                  issues=library.issues)
    catalog, textures, decoded, mesh_cache, seen, used = [], {}, {}, {}, {}, set()
    raw_fallbacks = set()

    def issue(kind, source, reason, **extra):
        entry = dict(kind=kind, source=source, reason=reason, **extra)
        if entry not in library.issues:
            library.issues.append(entry)

    def read_mesh(package, ref):
        key = package.path.stem.lower(), ref
        if key not in mesh_cache:
            try:
                mesh = package.mesh(ref)
                # build_model historically skips invalid corners; DX reports them instead.
                for a, b, c, (flags, slot) in mesh_faces(mesh):
                    if any(not 0 <= v[0] < mesh['frame_verts'] for v in (a, b, c)) or slot < 0:
                        raise ValueError('face has an invalid vertex or texture slot')
                mesh_cache[key] = mesh
            except ERRORS as exc:
                mesh_cache[key] = f'{type(exc).__name__}: {exc}'
                issue('mesh', '.'.join(package.ref_path(ref)), mesh_cache[key])
        return mesh_cache[key]

    def texture(pick, model, slot):
        path = '.'.join(pick[0].ref_path(pick[1])) if pick else '(empty)'
        key = path.lower()
        if key not in decoded:
            try:
                found = library.resolve(*pick) if pick else None
                if not found:
                    raise ValueError('texture reference or package missing' if pick else 'no skin or DefaultTexture')
                tex_props = found[0].properties(found[1])
                if tex_props.get('Format', 0) not in (0, 3, 5, 6, 7):
                    raise ValueError(f"unsupported texture format {tex_props['Format']}")
                png, image, _, _ = texture_asset(library, *found, masked=True, compressed=True)
                textures[png] = image
                decoded[key] = png, None
            except ERRORS as exc:
                decoded[key] = 'missing.png', f'{type(exc).__name__}: {exc}'
                textures['missing.png'] = Image.new('RGB', (8, 8), '#808080')
        png, error = decoded[key]
        if error:
            issue('skin', model, error, texture=path, slot=slot)
        return png

    engine = library.package('Engine')
    default_ref = engine and engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')
    default = (engine, default_ref) if default_ref else None

    def add(package, ref, label, props, owners, chain, view, class_path=None):
        source = '.'.join(package.ref_path(ref))
        model = safe(class_path or source) + (('_' + view.lower()) if view != 'Mesh' else '')
        item = dict(model_name=model, display_name=label, texture_name='', game='dx',
                    category=category(chain, package.path.stem), status='ready')
        mesh = read_mesh(package, ref)
        if isinstance(mesh, str):
            catalog.append(dict(item, status=f'failed: {mesh}'))
            return
        frame, pose, why = standing_pose(mesh)

        def slot_of(slot, flags):
            if flags & INVISIBLE:
                return None
            pick = actor_texture(package, mesh, props, owners, slot)
            if not pick:
                issue('fallback', model, 'empty actor and mesh slot; Engine.DefaultTexture', slot=slot)
                if class_path is None:
                    raw_fallbacks.add(source)
            return texture(pick or default, model, slot)

        try:
            data = build_model(mesh, slot_of, frame, 'dx', STYLE_FLAGS.get(props.get('Style'), 0))
            scale_key = {'PickupViewMesh': 'PickupViewScale', 'ThirdPersonMesh': 'ThirdPersonScale'}.get(view, 'DrawScale')
            scale = props.get(scale_key, props.get('DrawScale', 1.0))
            if scale != 1:
                data['vertices'] = [round(v * scale, 4) for v in data['vertices']]
            if not data['indices']:
                raise ValueError('no visible geometry (all faces invisible or empty mesh)')
        except ERRORS as exc:
            issue('model', model, f'{type(exc).__name__}: {exc}')
            catalog.append(dict(item, status=f'failed: {exc}'))
            return
        # Deduplicate the shown geometry AND appearance, retaining every source/class as an alias.
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if digest in seen:
            report['aliases'].append(dict(source=class_path or source, view=view, model=seen[digest]))
            return
        seen[digest] = model
        data['metadata'] = dict(source=source, actor_class=class_path, view=view, frames=mesh['frames'],
                                pose=pose, frame=frame, pose_reason=why, sequences=[s['name'] for s in mesh['seqs']])
        write_model_data(output / 'model_json' / f'{model}.json', data)
        catalog.append(dict(item, texture_name=data['material_textures'][0]))

    # Open only stock System packages. Textures are opened on demand, no maps participate in class skins.
    for name, path in sorted(library.files.items()):
        if path.suffix.lower() != '.u':
            continue
        package = library.package(name)
        if not package:
            continue
        for ref in package.find(MESH_CLASSES):
            mesh = read_mesh(package, ref)
            report['meshes'].append(dict(source='.'.join(package.ref_path(ref)),
                                         category=category([], name), readable=isinstance(mesh, dict)))
        # Surface unsupported native mesh types instead of silently omitting them.
        for ref, exp in enumerate(package.exports, 1):
            cls = package.ref_name(exp['cls'])
            if 'mesh' in cls.lower() and cls not in MESH_CLASSES:
                issue('mesh', '.'.join(package.ref_path(ref)), f'unsupported native mesh class {cls}')
            if exp['cls'] != 0 or not exp['size']:
                continue
            class_path = package.ref_path(ref)
            try:
                props, owners, chain = library.class_info(class_path)
            except ERRORS as exc:
                issue('class', '.'.join(class_path), f'{type(exc).__name__}: {exc}')
                continue
            views = mesh_views(props, chain)
            if not views:
                continue
            report['classes'].append(dict(source='.'.join(class_path), views=views))
            for view in views:
                ident = '.'.join(class_path)
                try:
                    target = library.resolve(owners[view], props[view])
                    if not target:
                        raise ValueError('mesh reference or package missing')
                    mesh_package, mesh_ref = target
                    cls = mesh_package.ref_name(mesh_package.exports[mesh_ref - 1]['cls'])
                    if cls not in MESH_CLASSES:
                        raise ValueError(f'unsupported native mesh class {cls}')
                    used.add((mesh_package.path.stem.lower(), mesh_ref))
                    label = exp['name'] + ({'PickupViewMesh': ' (pickup)', 'ThirdPersonMesh': ' (third person)',
                                            'Mesh2': ' (secondary carcass)', 'Mesh3': ' (floating carcass)'}.get(view, ''))
                    add(mesh_package, mesh_ref, label, props, owners, chain, view, ident)
                except ERRORS as exc:
                    issue('class mesh', ident, f'{type(exc).__name__}: {exc}', view=view)
                    catalog.append(dict(model_name=safe(ident + '_' + view), display_name=exp['name'], game='dx',
                                        category=category(chain, name), texture_name='', status=f'failed: {exc}'))
    # Keep unused asset meshes (including first-person weapons and effects); class variants replace bare templates.
    for (name, ref), mesh in list(mesh_cache.items()):
        if (name, ref) not in used:
            package = library.package(name)
            add(package, ref, package.exports[ref - 1]['name'], {}, {}, [], 'Mesh')
    for png, image in textures.items():
        write_texture(output / 'textures', png, image)
    report['packages'] = [dict(name=p.path.name, version=p.version, licensee=p.licensee)
                          for _, p in sorted(library.packages.items()) if p]
    report['raw_default_texture_meshes'] = sorted(raw_fallbacks)
    report['counts'] = dict(packages_read=len(report['packages']), meshes=len(report['meshes']),
                            meshes_by_category=dict(collections.Counter(m['category'] for m in report['meshes'])),
                            classes=len(report['classes']), looks=len(catalog), aliases=len(report['aliases']),
                            looks_by_category=dict(collections.Counter(e['category'] for e in catalog)),
                            issues_by_kind=dict(collections.Counter(i['kind'] for i in library.issues)),
                            raw_default_texture_meshes=len(raw_fallbacks),
                            unresolved_skins_by_reason=dict(collections.Counter(i['reason'] for i in library.issues if i['kind'] == 'skin')))
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog),
                issues=len(library.issues), report='import-report.json')
