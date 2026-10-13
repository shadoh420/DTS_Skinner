"""Invented 862 streams, plain tempfile; no retail bytes, key, or seed."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tests.test_tas import Fixture as AscendFixture, pack, bulk
from tests.test_nos_package import frame
from tools.nos_package import Package
from tools.tas_mesh import mesh
from tools.import_nos import import_catalog, category
from tools.model_data import MODEL_GAMES
from tools.texture_workshop import TEXTURE_GAMES
from tools.texture_storage import write_texture


class Fixture(AscendFixture):
    def write(self, path, version=862):
        super().write(path)
        raw = bytearray(path.read_bytes())
        struct.pack_into('<HH', raw, 4, version, 7 if version == 862 else 0)
        path.write_bytes(raw)
        return path

    def static(self, material):
        raw = super().static(material)
        # Internal version is followed by extra-LOD flag, remapping array,
        # remapping mode and the >=859 field. Include one 7-byte remap record.
        return raw[:88]+pack('2ii3B2i',0,1,0,1,2,3,0,0)+raw[88:]

    def skeletal(self, material, index_size=2):
        gpu = b''.join(bytes([127,127,255,255])*2+bytes([0,0,0,0,255,0,0,0])+
                       pack('3f2e',*p,0,0) for p in ((0,0,0),(52.5,0,0),(0,52.5,0)))
        bone = self.name('Root')+pack('i4f3f3i',0,0,0,0,1,0,0,0,0,-1,0)
        native = (bytes(28)+pack('2i',1,material)+bytes(24)+pack('i',1)+bone+pack('3i',1,1,1)+
                  pack('HHiiBiB',0,0,0,1,0,0,index_size)+bulk(pack('3H' if index_size==2 else '3I',0,1,2),index_size)+
                  pack('iH',1,0)+pack('4i',0,0,3,0)+pack('4i3i',0,3,12,0,0,1,2)+
                  pack('4i',1,1,0,1)+bytes(24)+bulk(gpu,32))
        return self.payload(native=native)

    def texture(self, external=False, raw=b'\x00\xf8\x00\x00\0\0\0\0'):
        if not external:
            return super().texture(raw=raw)
        props = self.prop('Format','ByteProperty',self.name('PF_DXT1'),'EPixelFormat')
        props += self.prop('SizeX','IntProperty',pack('i',4))+self.prop('SizeY','IntProperty',pack('i',4))
        props += self.prop('TextureFileCacheName','NameProperty',self.name('Textures'))
        return self.payload(props, bytes(16)+pack('i4i2i',1,3,len(raw),len(frame(raw)),9,4,4))


class NosModelsTests(unittest.TestCase):
    def test_stripped_graph_parameter_override_and_native_references(self):
        from tools.nos_package import Library
        from tools.nos_materials import Materials
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture()
            base=f.add('WrongDefault_D','Texture2D',f.texture())
            normal=f.add('Surface_N','Texture2D',f.texture())
            alt=f.add('Chosen_D','Texture2D',f.texture(raw=b'\x1f\0'+bytes(6)))
            # Surviving parameter sample still belongs to the compiled material.
            expr=f.add('Sample','MaterialExpressionTextureSampleParameter2D',f.payload(
                f.obj('Texture',base)+f.prop('ParameterName','NameProperty',f.name('Diffuse'))))
            resource=pack('3i',1,0,0)+bytes(24)+pack('3i',2,base,normal)
            mat=f.add('Cooked','Material',f.payload(native=resource))
            parameter=f.prop('ParameterName','NameProperty',f.name('Diffuse'))+f.obj('ParameterValue',alt)+f.name('None')
            child=f.add('Skin','MaterialInstanceConstant',f.payload(f.obj('Parent',mat)+
                f.prop('TextureParameterValues','ArrayProperty',pack('i',1)+parameter)))
            path=f.write(root/'install/BCMPGame/CookedPCConsole/Example.upk')
            # Set the expression's outer to the material in the synthetic table.
            raw=bytearray(path.read_bytes());eo=struct.unpack_from('<i',raw,32)[0]
            struct.pack_into('<i',raw,eo+(expr-1)*68+8,mat);path.write_bytes(raw)
            lib=Library(root/'install')
            try:
                mats=Materials(lib,root/'textures');row=mats.material('Example.Skin')
                self.assertEqual(row['diffuse_source'],'Example.Chosen_D')
                self.assertTrue(any('stripped' in n for n in row['notes']))
                self.assertNotIn('example.wrongdefault_d',mats.textures)
                self.assertEqual(row['recorded_maps']['Normal'][0]['source'],'Example.Surface_N')
            finally:lib.close()

    def test_stripped_top_mip_uses_resident_resolution(self):
        from tools.tas_materials import texture_payload
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture()
            props=f.prop('Format','ByteProperty',f.name('PF_DXT1'),'EPixelFormat')
            props+=f.prop('SizeX','IntProperty',pack('i',8))+f.prop('SizeY','IntProperty',pack('i',8))
            raw=b'\x00\xf8'+bytes(6)
            native=bytes(16)+pack('i4i2i4i',2,33,0,-1,-1,8,8,0,8,8,0)+raw+pack('2i',4,4)
            ref=f.add('Tex','Texture2D',f.payload(props,native));p=Package(f.write(root/'Example.upk'))
            try:
                props,mips=p.texture(ref,{})
                self.assertEqual(mips,[(4,4,raw)])
                self.assertEqual(texture_payload(props,mips)[2]['width'],4)
            finally:p.close()

    def test_862_static_skeletal_and_32bit_indices(self):
        with tempfile.TemporaryDirectory() as td:
            f=Fixture();mat=f.add('Mat','Material')
            refs=[f.add('Prop','StaticMesh',f.static(mat))]
            refs += [f.add('Body'+str(n),'SkeletalMesh',f.skeletal(mat,n)) for n in (2,4)]
            path=f.write(Path(td)/'Example.upk');p=Package(path)
            try:
                for ref in refs:
                    data=mesh(p,ref)
                    self.assertEqual(data['indices'],[0,1,2])
                    self.assertEqual(data['vertices'][3:6],[0,0,1])
                    self.assertEqual(data['material_refs'],['Example.Mat'])
                self.assertEqual(mesh(p,refs[2])['metadata']['bones'][0]['name'],'Root')
            finally:p.close()

    def test_mitv_zlib_tfc_component_skins_shared_storage_and_routes(self):
        import app
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);install=root/'install';cooked=install/'BCMPGame/CookedPCConsole'
            f=Fixture();mat=f.materials()
            texture=f.add('External','Texture2D',f.texture(True,raw=b'\xff\xff'+bytes(6)))
            parameter=f.prop('ParameterName','NameProperty',f.name('Albedo'))+f.obj('ParameterValue',texture)+f.name('None')
            alt=f.add('Animated','MaterialInstanceTimeVarying',f.payload(f.obj('Parent',mat)+
                f.prop('TextureParameterValues','ArrayProperty',pack('i',1)+parameter)))
            body=f.add('Body','SkeletalMesh',f.skeletal(mat))
            # An explicit component assignment is valid even for an unrelated MIC.
            unrelated=f.add('Other','MaterialInstanceConstant',f.payload(f.obj('Parent',4)))
            component=f.add('Placed','PawnSkeletalMeshComponent_K', pack('i',0)+f.payload(f.obj('SkeletalMesh',body)+
                f.prop('Materials','ArrayProperty',pack('2i',1,unrelated))))
            f.add('UCX_Box','StaticMesh',f.static(mat))
            path=f.write(cooked/'Human_AssHuman_SK.upk')
            (cooked/'Textures.tfc').write_bytes(bytes(9)+frame(b'\xff\xff'+bytes(6)))
            p=Package(path)
            try:self.assertEqual(p.properties(component)[0]['SkeletalMesh'],body)
            finally:p.close()
            out=root/'data/nos';report=import_catalog(install,out)
            self.assertEqual((report['ready'],report['skipped'],report['unresolved_materials'],report['unresolved_textures']),(1,1,0,0))
            index=json.loads((out/'model-index.json').read_text())['human_asshuman_sk.body']
            docs=[json.loads((out/'model_json'/(v+'.json')).read_text()) for v in index['looks']]
            self.assertEqual(len(docs),3)
            self.assertTrue(all(d['geometry_buffers']==docs[0]['geometry_buffers'] for d in docs))
            self.assertEqual(docs[0]['geometry_buffers']['arrays']['vertices']['dtype'],'<f4')
            animated=next(m for m in report['material_results'] if m['source'].endswith('.Animated'))
            self.assertEqual(animated['diffuse_source'],'Human_AssHuman_SK.External')
            self.assertTrue(any('Time-varying' in n for n in animated['notes']))
            tex=out/'textures'/animated['texture'];self.assertRegex(tex.name,r'^external_[0-9a-f]{12}\.dds$')
            self.assertEqual(tex.read_bytes()[:4],b'DDS ')
            tex.write_bytes(b'edited');write_texture(tex.parent,tex.name,b'original');self.assertEqual(tex.read_bytes(),b'edited')
            self.assertIn('nos',MODEL_GAMES);self.assertIn('nos',TEXTURE_GAMES)
            with patch.dict(app.pack_dirs,nos=out),patch.object(app,'local_data_dir',out.parent):
                client=app.app.test_client();entry=client.get('/list_models?game=nos').json[0]
                data=client.get('/model_json/'+entry['model_name']+'?game=nos').json
                self.assertEqual(data['game'],'nos');self.assertEqual(data['material_texture_games'],['nos'])
                self.assertTrue(client.get('/list_textures?game=nos').json)
                with patch('app.import_nos_catalog',return_value={'ready':1}) as importer:
                    self.assertEqual(client.post('/import_nos',json={'path':str(install)}).json,{'ready':1})
                    importer.assert_called_once_with(str(install),out)
                    self.assertEqual(client.post('/import_nos',json={'path':str(install)},headers={'Origin':'https://other.invalid'}).status_code,403)
            with self.assertRaises(ValueError):import_catalog(install,install/'nos')

    def test_860_community_and_duplicate_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture();mat=f.materials();f.add('Prop','StaticMesh',f.static(mat))
            for folder in ('A','B'):
                f.write(root/'install/BCMPGame/CookedPCConsole'/folder/'BCMPMods_Test.upk',version=860)
            report=import_catalog(root/'install',root/'data/nos')
            self.assertEqual(report['ready'],1)
            row=report['results'][0];self.assertEqual(row['section'],'Community')
            self.assertEqual(len(row['provenance']),1);self.assertEqual(len(row['provenance'][0]['copies']),2)
            self.assertTrue(category(row).startswith('Community /'))


if __name__=='__main__':
    unittest.main()
