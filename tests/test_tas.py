"""Invented UE3 packages and streams only; no installed game required."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.tas_package import MAGIC, Package, Reader, lzo1x
from tools.tas_mesh import mesh
from tools.tas_materials import texture_payload
from tools.import_tas import import_catalog
from tools.model_data import MODEL_GAMES, load_model_data
from tools.glb_exporter import model_to_glb
from tools.texture_workshop import TEXTURE_GAMES
from tools.texture_storage import write_texture


def pack(fmt, *values):
    return struct.pack('<'+fmt, *values)


def literal(raw):
    if len(raw) <= 238:
        return bytes([17+len(raw)])+raw+b'\x11\0\0'
    n = len(raw)-18
    zeros, last = divmod(n-1,255)
    return b'\0'+b'\0'*zeros+bytes([last+1])+raw+b'\x11\0\0'


def frame(raw, block=128):
    pieces = [raw[i:i+block] for i in range(0,len(raw),block)]
    compressed = [literal(p) for p in pieces]
    return (pack('4I',MAGIC,block,sum(map(len,compressed)),len(raw))+
            b''.join(pack('2I',len(c),len(p)) for c,p in zip(compressed,pieces))+b''.join(compressed))


def bulk(raw, stride):
    return pack('2i',stride,len(raw)//stride)+raw


class Fixture:
    def __init__(self, licensee=0):
        self.names=['None']; self.imports=[]; self.exports=[]; self.licensee=licensee

    def name(self, value, number=0):
        if value not in self.names:
            self.names.append(value)
        return pack('2i',self.names.index(value),number)

    def prop(self, name, kind, raw, extra=None):
        return self.name(name)+self.name(kind)+pack('2i',len(raw),0)+(self.name(extra) if extra else b'')+raw

    def boolean(self, name, value):
        return self.name(name)+self.name('BoolProperty')+pack('2iB',0,0,value)

    def obj(self, name, ref):
        return self.prop(name,'ObjectProperty',pack('i',ref))

    def input(self, name, ref):
        return self.prop(name,'StructProperty',self.obj('Expression',ref)+self.name('None'),'ColorMaterialInput')

    def payload(self, props=b'', native=b''):
        return pack('i',0)+props+self.name('None')+native

    def add(self, name, cls, payload=None, flags=0):
        if cls not in self.imports:
            self.imports.append(cls)
        self.exports.append((name,-self.imports.index(cls)-1,payload or self.payload(),flags))
        return len(self.exports)

    def static(self, material):
        points=pack('9f',0,0,0,52.5,0,0,0,52.5,0)
        uv=b''.join(bytes([127,127,255,255])*2+pack('2e',*p) for p in ((0,0),(1,0),(0,1)))
        native=(bytes(56)+bulk(b'',6)+bulk(b'',8)+pack('2i',1,1)+bytes(16)+
                pack('i9iiB',1,material,1,1,1,0,1,0,2,0,0,0)+
                pack('2i',12,3)+bulk(points,12)+pack('4i',1,12,3,0)+bulk(uv,12)+
                pack('3i',0,0,3)+bulk(pack('3H',0,1,2),2))
        return self.payload(native=native)

    def skeletal(self, material):
        gpu=b''.join(bytes([127,127,255,255])*2+bytes([0,0,0,0,255,0,0,0])+pack('3f',*p)+pack('2e',0,0)
                     for p in ((0,0,0),(52.5,0,0),(0,52.5,0)))
        bone=self.name('Root')+pack('i4f3f3i',0,0,0,0,1,0,0,0,0,-1,0)
        native=(bytes(28)+pack('2i',1,material)+bytes(24)+pack('i',1)+bone+pack('3i',1,1,1)+
                pack('HHiHB',0,0,0,1,0)+bulk(pack('3H',0,1,2),2)+pack('iH',1,0)+
                pack('4i',0,0,3,0)+bytes(16)+pack('4i',1,1,0,1)+bytes(24)+bulk(gpu,32))
        return self.payload(native=native)

    def texture(self, external=False, raw=b'\x00\xf8\x00\x00\0\0\0\0'):
        props=self.prop('Format','ByteProperty',self.name('PF_DXT1'),'EPixelFormat')
        props+=self.prop('SizeX','IntProperty',pack('i',4))+self.prop('SizeY','IntProperty',pack('i',4))
        if external:
            props+=self.prop('TextureFileCacheName','NameProperty',self.name('Textures'))
            native=bytes(16)+pack('i4i2i',1,0x11,8,len(frame(raw)),9,4,4)
        else:
            native=bytes(16)+pack('i4i',1,0,8,8,0)+raw+pack('2i',4,4)
        return self.payload(props,native)

    def materials(self):
        texture=self.add('Red','Texture2D',self.texture())
        other=self.add('Misleading_Diffuse','Texture2D',self.texture(raw=b'\x1f\0'+bytes(6)))
        sample=self.add('Sample','MaterialExpressionTextureSampleParameter2D',self.payload(
            self.obj('Texture',other)+self.prop('ParameterName','NameProperty',self.name('Albedo'))))
        base=self.add('Base','Material',self.payload(self.input('DiffuseColor',sample)+self.boolean('TwoSided',True)))
        parameter=self.prop('ParameterName','NameProperty',self.name('Albedo'))+self.obj('ParameterValue',texture)+self.name('None')
        parent=self.add('Parent','MaterialInstanceConstant',self.payload(self.obj('Parent',base)+
            self.prop('TextureParameterValues','ArrayProperty',pack('i',1)+parameter)))
        child=self.add('Child','MaterialInstanceConstant',self.payload(self.obj('Parent',parent)))
        return child

    def write(self, path, compressed=False, chunk_size=None):
        imports=b''.join(self.name('Core')+self.name('Class')+pack('i',0)+self.name(c) for c in self.imports)
        for name,_,_,_ in self.exports:
            self.name(name)
        names=b''.join(pack('i',len(n)+1)+n.encode()+b'\0'+bytes(8) for n in self.names)
        no=512; io=no+len(names); eo=io+len(imports); header=eo+68*len(self.exports)
        at=header; exports=b''; payload=b''
        for name,cls,data,flags in self.exports:
            exports+=pack('3i',cls,0,0)+self.name(name)+pack('iQ4i',0,flags,len(data),at,0,0)+bytes(20)
            payload+=data;at+=len(data)
        body=names+imports+exports+payload
        pieces=[body[i:i+(chunk_size or len(body))] for i in range(0,len(body),chunk_size or len(body))]
        frames=[frame(piece) for piece in pieces] if compressed else []
        framed=b''.join(frames) if compressed else body
        summary=(pack('IHHI',MAGIC,805,self.licensee,header)+pack('iI',0,8 if self.licensee==2 else 0)+
                 pack('7i',len(self.names),no,len(self.exports),eo,len(self.imports),io,header)+bytes(32)+
                 pack('5i',0,0,0,2 if compressed else 0,len(frames)))
        if compressed:
            logical=physical=512
            for piece,block in zip(pieces,frames):
                summary+=pack('4i',logical,len(piece),physical,len(block))
                logical+=len(piece);physical+=len(block)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(summary.ljust(512,b'\0')+framed)
        return path


class PackageTests(unittest.TestCase):
    def test_lzo_literals_matches_and_invalid(self):
        for n in (4,128,238,239,1000):
            raw=bytes(i%251 for i in range(n));self.assertEqual(lzo1x(literal(raw),n),raw)
        # M2 non-overlap, M3 overlap, trailing literals and short match.
        self.assertEqual(lzo1x(b'\x15abcd\x6c\0\x11\0\0',8),b'abcdabcd')
        self.assertEqual(lzo1x(b'\x12a\x29\0\0\x11\0\0',12),b'a'*12)
        self.assertEqual(lzo1x(b'\x12a\x01\0b\x11\0\0',4),b'aaab')
        self.assertEqual(lzo1x(b'\x12a\x20\x01\0\0\x11\0\0',35),b'a'*35)
        seed=b'abcd'*4097
        self.assertEqual(lzo1x(literal(seed)[:-3]+b'\x11\x04\0\x11\0\0',len(seed)+3),seed+seed[3:6])
        for raw,n in ((b'\x11\0\0',1),(b'\x12a\x40\xff',4),(b'\xff',238),(literal(b'abcd')+b'x',4)):
            with self.assertRaises(ValueError):lzo1x(raw,n)

    def test_summary_chunks_names_flags_properties(self):
        with tempfile.TemporaryDirectory() as td:
            for lic in (0,2):
                f=Fixture(lic);props=f.prop('Count','IntProperty',pack('i',42))+f.boolean('Enabled',True)
                f.add('Thing','Material',f.payload(props),flags=1<<50)
                p=Package(f.write(Path(td)/'Example.upk',compressed=True,chunk_size=137))
                try:
                    self.assertEqual((p.version,p.licensee,p.compression),(805,lic,2))
                    self.assertGreater(len(p.chunks),1)
                    self.assertEqual(p.entry(1)['flags'],1<<50)
                    self.assertEqual(p.properties(1)[0],{'Count':42,'Enabled':True})
                    self.assertEqual(Reader(f.name('Thing',3),f.names).name(),'Thing_2')
                    self.assertGreater(p.lzo_bytes,0)
                    with self.assertRaises(ValueError):p.read(p.virtual_size,1)
                finally:p.close()

    def test_tfc_backed_mip(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture();f.add('Tex','Texture2D',f.texture(True));p=Package(f.write(root/'Test.upk'))
            tfc=root/'Textures.tfc';tfc.write_bytes(bytes(9)+frame(b'\x00\xf8\x00\x00\0\0\0\0'))
            try:
                props,mips=p.texture(1,{'textures':tfc});ext,raw,info=texture_payload(props,mips)
                self.assertEqual((ext,info['width'],len(raw)),('.dds',4,136));self.assertEqual(raw[:4],b'DDS ')
                with self.assertRaises(ValueError):p.texture(1,{})
            finally:p.close()

    def test_static_and_skeletal_reference_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            f=Fixture();mat=f.add('Mat','Material');s=f.add('Prop','StaticMesh',f.static(mat));k=f.add('Body','SkeletalMesh',f.skeletal(mat))
            p=Package(f.write(Path(td)/'PC_Test.upk'))
            try:
                for ref in (s,k):
                    data=mesh(p,ref);self.assertEqual(data['indices'],[0,1,2]);self.assertEqual(len(data['vertices']),9)
                    self.assertEqual(data['vertices'][3:6],[0,0,1]);self.assertEqual(data['material_refs'],['PC_Test.Mat'])
                self.assertEqual(mesh(p,k)['metadata']['bones'][0]['name'],'Root')
            finally:p.close()


    def test_bad_tables_chunks_and_names_fail_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            f=Fixture();f.add('Thing','Material');path=f.write(Path(td)/'Bad.upk')
            raw=bytearray(path.read_bytes());struct.pack_into('<i',raw,20,2_000_001);path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'table'):Package(path)
            path=f.write(path,compressed=True);raw=bytearray(path.read_bytes());raw[512:516]=bytes(4);path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'chunk'):Package(path)
        for raw in (pack('2i',-1,0),pack('2i',0,-1)):
            with self.assertRaises(ValueError):Reader(raw,['None']).name()


class ImportTests(unittest.TestCase):
    def test_mic_chain_diffuse_storage_and_skin_buffers(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);install=root/'install';f=Fixture();mat=f.materials()
            alt=f.prop('ParameterName','NameProperty',f.name('Albedo'))+f.obj('ParameterValue',2)+f.name('None')
            f.add('Alternate','MaterialInstanceConstant',f.payload(f.obj('Parent',mat)+f.prop('TextureParameterValues','ArrayProperty',pack('i',1)+alt)))
            f.add('Body','SkeletalMesh',f.skeletal(mat));f.add('Prop','StaticMesh',f.static(mat))
            f.write(install/'TribesGame/CookedPC/PC_Test.upk')
            out=root/'data/tas';report=import_catalog(install,out)
            self.assertEqual((report['ready'],report['unresolved_materials'],report['unresolved_textures']),(2,0,0))
            material=next(m for m in report['material_results'] if m['source']=='PC_Test.Child')
            self.assertEqual(material['diffuse_source'],'PC_Test.Red');self.assertEqual(material['settings']['cull'],'none')
            self.assertEqual(material['parent_chain'],['PC_Test.Child','PC_Test.Parent','PC_Test.Base'])
            index=json.loads((out/'model-index.json').read_text());looks=index['pc_test.body']['looks']
            self.assertEqual(len(looks),3)
            docs=[json.loads((out/'model_json'/(look+'.json')).read_text()) for look in looks]
            self.assertEqual(docs[0]['geometry_buffers'],docs[1]['geometry_buffers'])
            self.assertNotEqual(docs[0]['material_textures'],docs[1]['material_textures'])
            self.assertEqual(docs[0]['geometry_buffers']['arrays']['vertices']['dtype'],'<f4')
            tex=out/'textures'/material['texture'];self.assertTrue(tex.is_file());self.assertEqual(tex.suffix,'.dds');self.assertRegex(tex.name,r'^red_[0-9a-f]{12}\.dds$')
            tex.write_bytes(b'edited');write_texture(tex.parent,tex.name,b'original');self.assertEqual(tex.read_bytes(),b'edited')
            self.assertIn('tas',MODEL_GAMES);self.assertIn('tas',TEXTURE_GAMES)
            with self.assertRaises(ValueError):import_catalog(install,install/'tas')

    def test_duplicate_provenance_and_explicit_skips(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture(2);mat=f.materials();f.add('Prop','StaticMesh',f.static(mat))
            f.add('UCX_Box','StaticMesh',f.static(mat));f.add('Tree','SpeedTree')
            # Two physical copies of the same package logical identity in different directories.
            for folder in ('A','B'):
                f.write(root/'install/TribesGame/CookedPC'/folder/'PC_Test.upk',compressed=True)
            report=import_catalog(root/'install',root/'data/tas')
            self.assertEqual((report['ready'],report['skipped']),(1,2))
            row=next(r for r in report['results'] if r['status']=='ready')
            self.assertEqual(len(row['provenance']),1);self.assertEqual(len(row['provenance'][0]['copies']),2)
            self.assertIn('/A/',row['file'])

    def test_app_routes_and_glb(self):
        import app
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture();mat=f.materials();f.add('Prop','StaticMesh',f.static(mat))
            install=root/'install';out=root/'data/tas';f.write(install/'TribesGame/CookedPC/PC_Test.upk')
            import_catalog(install,out)
            with patch.dict(app.pack_dirs,tas=out),patch.object(app,'local_data_dir',out.parent):
                client=app.app.test_client();catalog=client.get('/list_models?game=tas').json
                self.assertEqual(len(catalog),2);entry=catalog[0]
                data=client.get('/model_json/'+entry['model_name']+'?game=tas').json
                self.assertEqual(data['game'],'tas');self.assertEqual(data['material_texture_games'],['tas'])
                response=client.get('/texture/'+entry['texture_name']+'?game=tas')
                self.assertEqual(response.status_code,200);response.close()
                self.assertIn(entry['texture_name'],client.get('/list_textures?game=tas').json)
                self.assertEqual(model_to_glb(load_model_data(out/'model_json'/(entry['model_name']+'.json')),out/'textures')[:4],b'glTF')
                with patch('app.import_tas_catalog',return_value={'ready':1}) as importer:
                    self.assertEqual(client.post('/import_tas',json={'path':str(install)}).json,{'ready':1})
                    importer.assert_called_once_with(str(install),out)
                    self.assertEqual(client.post('/import_tas',json={'path':str(install)},headers={'Origin':'https://other.invalid'}).status_code,403)
                self.assertEqual(client.get('/maps/tas/').status_code,200)

    def test_opacity_channel_preview_and_parent_cycle(self):
        from tools.tas_package import Library
        from tools.tas_materials import Materials
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);f=Fixture();diff=f.add('Color','Texture2D',f.texture())
            props=f.prop('Format','ByteProperty',f.name('PF_G8'),'EPixelFormat')
            props+=f.prop('SizeX','IntProperty',pack('i',1))+f.prop('SizeY','IntProperty',pack('i',1))
            alpha=f.add('Alpha','Texture2D',f.payload(props,bytes(16)+pack('i4i',1,0,1,1,0)+bytes([64])+pack('2i',1,1)))
            ds=f.add('DS','MaterialExpressionTextureSample',f.payload(f.obj('Texture',diff)))
            als=f.add('AS','MaterialExpressionTextureSample',f.payload(f.obj('Texture',alpha)))
            mask=f.obj('Expression',als)+f.prop('Mask','IntProperty',pack('i',1))+f.prop('MaskR','IntProperty',pack('i',1))+f.name('None')
            props=f.input('DiffuseColor',ds)+f.prop('OpacityMask','StructProperty',mask,'ScalarMaterialInput')
            props+=f.prop('BlendMode','ByteProperty',f.name('BLEND_Masked'),'EBlendMode')+f.prop('OpacityMaskClipValue','FloatProperty',pack('f',.25))
            f.add('Masked','Material',f.payload(props))
            cycle=len(f.exports)+1;f.add('Cycle','MaterialInstanceConstant',f.payload(f.obj('Parent',cycle)))
            install=root/'install';f.write(install/'TribesGame/CookedPC/Example.upk')
            lib=Library(install)
            try:
                mats=Materials(lib,root/'textures');row=mats.material('Example.Masked')
                self.assertEqual(row['status'],'ready');self.assertEqual(row['settings']['alphaTest'],.25)
                self.assertTrue(row['original_diffuse_texture'].endswith('.dds'))
                with Image.open(root/'textures'/row['texture']) as image:self.assertEqual(image.getpixel((0,0)),(255,0,0,64))
                self.assertEqual(mats.material('Example.Cycle')['status'],'unresolved')
            finally:lib.close()


if __name__=='__main__':
    unittest.main()
