"""Export character folders of an Unreal Engine 5 project (Fab assets) for tools/import_fab.py and for other engines.
It runs inside Unreal's own editor, headless, which is how assets that only come as a UE project get out of it:

set FAB_EXPORT_OUT=C:\\tmp\\fab-export
set FAB_EXPORT_FOLDERS=SciFiGirl,SciFiSoldier
"C:\\Program Files\\Epic Games\\UE_5.1\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" "PATH\\MyProject.uproject"
    -run=pythonscript -script="PATH\\tools\\ue5_export.py" -EnablePlugins=PythonScriptPlugin -nullrhi -unattended -nosplash

(the project's own engine version; the Python plugin is enabled for this run only.) For each folder under Content it
writes OUT/FOLDER/: every skeletal mesh and animation as FBX (Z up, centimetres, the mesh in its reference pose),
every texture as PNG, and materials.json: the meshes' material slots, the textures (with their sRGB flag), and the
materials: an instance's parameter values, a base material's graph feeding each output."""
import json
import os

import unreal

OUT = os.environ.get('FAB_EXPORT_OUT', r'C:\tmp\fab-export')
FOLDERS = [f for f in os.environ.get('FAB_EXPORT_FOLDERS', '').split(',') if f]
OUTPUTS = ('MP_BASE_COLOR', 'MP_EMISSIVE_COLOR', 'MP_METALLIC', 'MP_ROUGHNESS', 'MP_NORMAL', 'MP_AMBIENT_OCCLUSION')
PROPERTIES = ('parameter_name', 'texture', 'r', 'const_a', 'const_b', 'const_alpha', 'default_value', 'sampler_type', 'material_function')


def export(asset, filename, options=None):
    os.makedirs(os.path.dirname(filename), exist_ok=True)  # The FBX writer reports success into a missing folder.
    task = unreal.AssetExportTask()
    task.object, task.filename, task.automated, task.prompt, task.replace_identical = asset, filename, True, False, True
    if options:
        task.options = options
    ok = unreal.Exporter.run_asset_export_task(task)
    print(('EXPORTED ' if ok else 'FAILED ') + filename, flush=True)
    return ok


def plain(value):
    if isinstance(value, unreal.LinearColor):
        return [value.r, value.g, value.b, value.a]
    if isinstance(value, unreal.Object):
        return value.get_path_name()
    return value if isinstance(value, (int, float, str, bool)) else str(value)


def graph(material):
    """Each output's expression tree: {cls, the properties above, inputs: [node or None in input order], pin: the
    output of this node its parent reads ('' its first)}."""
    lib = unreal.MaterialEditingLibrary

    def node(expression, depth=0):
        if expression is None or depth > 60:
            return None
        out = dict(cls=expression.get_class().get_name(), inputs=[])
        for prop in PROPERTIES:
            try:
                out[prop] = plain(expression.get_editor_property(prop))
            except Exception:
                pass
        for source in lib.get_inputs_for_material_expression(material, expression):
            child = node(source, depth + 1)
            if child is not None:
                child['pin'] = str(lib.get_input_node_output_name_for_material_expression(expression, source))
            out['inputs'].append(child)
        return out
    return {name: node(lib.get_material_property_input_node(material, getattr(unreal.MaterialProperty, name))) for name in OUTPUTS}


def material_info(material):
    info = dict(name=material.get_name(), path=material.get_path_name(), cls=material.get_class().get_name(),
                textures={}, colours={}, scalars={})
    if isinstance(material, unreal.MaterialInstance):
        parent = material.get_editor_property('parent')
        info['parent'] = parent.get_path_name() if parent else None
        for kind, key in (('texture', 'textures'), ('vector', 'colours'), ('scalar', 'scalars')):
            for p in material.get_editor_property(kind + '_parameter_values'):
                info[key][str(p.get_editor_property('parameter_info').name)] = plain(p.get_editor_property('parameter_value'))
    else:
        info['graph'] = graph(material)
    return info


def main():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    fbx = unreal.FbxExportOption()
    fbx.set_editor_property('collision', False)
    fbx.set_editor_property('level_of_detail', False)
    for folder in FOLDERS:
        out = os.path.join(OUT, folder)
        report = dict(meshes=[], animations=[], textures=[], materials=[])
        for data in registry.get_assets_by_path('/Game/' + folder, recursive=True):
            asset, name = data.get_asset(), str(data.asset_name)
            cls = asset.get_class().get_name()
            path = os.path.join(out, str(data.package_path)[len('/Game/' + folder):].strip('/'), name)
            if cls == 'SkeletalMesh':
                export(asset, path + '.fbx', fbx)
                report['meshes'].append(dict(name=name, fbx=os.path.relpath(path + '.fbx', out), slots=[
                    dict(slot=str(m.get_editor_property('material_slot_name')), material=plain(m.get_editor_property('material_interface')))
                    for m in asset.get_editor_property('materials')]))
            elif cls == 'AnimSequence':
                export(asset, path + '.fbx', fbx)
                report['animations'].append(dict(name=name, fbx=os.path.relpath(path + '.fbx', out), skeleton=plain(asset.get_editor_property('skeleton'))))
            elif cls == 'Texture2D':
                export(asset, path + '.png')
                report['textures'].append(dict(name=name, path=asset.get_path_name(), file=os.path.relpath(path + '.png', out),
                                               srgb=asset.get_editor_property('srgb')))
            elif cls in ('Material', 'MaterialInstanceConstant'):
                report['materials'].append(material_info(asset))
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, 'materials.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
        print('FOLDER %s: %d meshes, %d animations, %d textures, %d materials' % (
            folder, len(report['meshes']), len(report['animations']), len(report['textures']), len(report['materials'])), flush=True)


main()
