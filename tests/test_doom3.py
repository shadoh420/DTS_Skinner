"""Small synthetic Doom readers/resolution tests; no installed game is required."""
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from PIL import Image

from tools.doom3_assets import Install, Declarations, Materials, Textures, dds_path, lex, image_expression, material_record, texture_filename
from tools.doom3_mesh import read_lwo, read_ase, read_ma, read_md5, doom_quaternion, triangulate, copy_head_joints
from tools.import_doom3 import import_catalog, inventory, idle_animation, load_parts
from tools.import_quake_md5 import quaternion
from tools.model_data import load_model_data
from tools.texture_storage import dds_bytes, open_texture


MESH=b'''MD5Version 10
commandline ""
numJoints 1
numMeshes 1
joints { "head" -1 ( 0 0 2 ) ( 0 0 0 ) }
mesh {
 shader "textures/test"
 numverts 3
 vert 0 ( 0 0 ) 0 1
 vert 1 ( 1 0 ) 1 1
 vert 2 ( 0 1 ) 2 1
 numtris 1
 tri 0 0 1 2
 numweights 3
 weight 0 0 1 ( 0 0 0 )
 weight 1 0 1 ( 1 0 0 )
 weight 2 0 1 ( 0 1 0 )
}
'''
ANIM=b'''MD5Version 10
commandline ""
numFrames 1
numJoints 1
frameRate 24
numAnimatedComponents 0
hierarchy { "head" -1 0 0 }
bounds { ( 0 0 0 ) ( 2 2 4 ) }
baseframe { ( 0 0 4 ) ( 0 0 0 ) }
frame 0 { }
'''


def string(value):
    data=value.encode()+b'\0'
    return data+b'\0'*(len(data)%2)


def chunk(name,data): return name.encode()+struct.pack('>I',len(data))+data+b'\0'*(len(data)%2)


def lwo():
    tags=chunk('TAGS',string('textures/test'))
    points=chunk('PNTS',struct.pack('>12f',0,0,0, 1,0,0, 1,0,1, 0,0,1))
    poly=chunk('POLS',b'FACE'+struct.pack('>5H',4,0,1,2,3))
    uv=chunk('VMAP',b'TXUV'+struct.pack('>H',2)+string('uv')+b''.join(struct.pack('>Hff',i,i%2,i//2) for i in range(4)))
    seam=chunk('VMAD',b'TXUV'+struct.pack('>H',2)+string('uv')+struct.pack('>HHff',0,0,.25,.75))
    body=b'LWO2'+tags+chunk('LAYR',b'')+points+poly+uv+seam+chunk('LAYR',b'')+points+poly
    return b'FORM'+struct.pack('>I',len(body))+body


ASE=b'''*3DSMAX_ASCIIEXPORT 200
*MATERIAL_LIST {
 *MATERIAL 0 {
  *SUBMATERIAL 0 { *MATERIAL_NAME "unused" }
  *SUBMATERIAL 1 { *MAP_DIFFUSE { *BITMAP "C:/base/textures/test.tga" } }
 }
}
*GEOMOBJECT {
 *NODE_TM { *TM_POS 100 100 100 }
 *MATERIAL_REF 0
 *MESH {
  *MESH_VERTEX 0 0 0 0
  *MESH_VERTEX 1 1 0 0
  *MESH_VERTEX 2 0 1 0
  *MESH_FACE 0: A: 0 B: 1 C: 2 *MESH_MTLID 1
  *MESH_TVERT 0 .25 .75 0
  *MESH_TFACE 0 0 0 0
  *MESH_VERTCOL 0 .1 .2 .3
  *MESH_CFACE 0 0 0 0
  *MESH_FACENORMAL 0 0 0 1
  *MESH_VERTEXNORMAL 0 0 0 1
  *MESH_VERTEXNORMAL 1 0 0 1
  *MESH_VERTEXNORMAL 2 0 0 1
 }
}
'''
MA=b'''// Maya ASCII 6.0
createNode transform -n "group";
setAttr ".t" -type "double3" 2 3 4;
createNode mesh -n "shape" -p "group";
setAttr -s 3 ".vt[0:2]" 0 0 0 1 0 0 0 1 0;
setAttr ".pt[0]" -type "float3" 1 0 0;
setAttr ".ed[0:2]" 0 1 0 1 2 0 0 2 0;
setAttr ".uvst[0].uvsp[0:2]" -type "float2" 0 0 1 0 0 1;
setAttr ".n[0:2]" -type "float3" 0 0 1 0 0 1 0 0 1;
setAttr ".fc[0]" -type "polyFaces" f 3 0 1 -3 mu 0 3 0 1 2;
createNode file -n "file1";
setAttr ".ftn" -type "string" "D:/d3xp/textures/test.tga";
createNode phong -n "mat";
createNode shadingEngine -n "sg";
connectAttr "file1.oc" "mat.c";
connectAttr "mat.oc" "sg.ss";
connectAttr -na "shape.iog" "sg.dsm";
createNode script -n "never_execute";
setAttr ".b" -type "string" "python(\\\"raise Exception()\\\");";
'''


def pak(root,campaign,name,files):
    folder=Path(root)/campaign; folder.mkdir(exist_ok=True)
    with zipfile.ZipFile(folder/name,'w') as archive:
        for key,value in files.items(): archive.writestr(key,value)


def fixture(root):
    image=io.BytesIO(); Image.new('RGBA',(2,2),(10,20,30,128)).save(image,format='TGA')
    pak(root,'base','pak000.pk4',{
        'models/test.md5mesh':MESH,'models/idle.md5anim':ANIM,'models/mapobjects/test.lwo':lwo(),
        'def/test.def':'''model body { mesh models/test.md5mesh anim idle models/idle.md5anim }
model head { mesh models/test.md5mesh }
entityDef parent { "model" "body" "anim" "idle" "skin" "red" }
entityDef monster_child { "inherit" "parent" "def_head" "head" "head_joint" "head" }
entityDef weapon_test { "model" "body" "model_view" "body" "model_world" "body" }
entityDef item_missing { "model" "models/missing.lwo" }
''',
        'skins/test.skin':'red { textures/test textures/red }',
        'materials/test.mtr':'textures/test { diffusemap textures/test }\ntextures/red { { blend diffusemap map textures/test alphaTest .4 } }',
        'textures/test.tga':image.getvalue(),
        'dds/textures/test.dds':dds_bytes(4,4,'DXT5',[bytes(range(16))]),
    })
    pak(root,'base','pak001.pk4',{'override.txt':'later'})
    pak(root,'d3xp','pak000.pk4',{'def/roe.def':'entityDef monster_child { "inherit" "parent" }',
                                 'textures/test.tga':image.getvalue(),'override.txt':'roe'})


class ReaderTests(unittest.TestCase):
    def test_md5_idle_frame_and_doom_calcw_does_not_loosen_quake(self):
        parts,joints=read_md5(MESH,ANIM)
        self.assertEqual(joints[0]['position'],[0,0,4])
        self.assertEqual(parts[0]['vertices'][0],(0,0,4))
        xyz=(-.6476855278,-.6862844825,-.3343515694)
        self.assertAlmostEqual(doom_quaternion(xyz)[3],-math.sqrt(abs(1-sum(v*v for v in xyz))))
        with self.assertRaises(ValueError): quaternion(xyz)

    def test_head_copies_local_and_world_joints(self):
        _,body=read_md5(MESH,ANIM)
        parts,_=read_md5(MESH,pose_callback=lambda head:copy_head_joints(head,body,{'copy_joint head':'head'},[0,0,4]))
        self.assertEqual(list(parts[0]['vertices'][0]),[0,0,4])
        parts,_=read_md5(MESH,pose_callback=lambda head:copy_head_joints(head,body,{'copy_joint_world head':'head'},[0,0,4]))
        self.assertEqual(list(parts[0]['vertices'][0]),[0,0,0])

    def test_lwo_seam_layers_and_quad(self):
        parts=read_lwo(lwo()); self.assertEqual(len(parts),1)
        self.assertEqual(len(parts[0]['vertices']),8)
        self.assertEqual(len(parts[0]['indices']),12)
        self.assertEqual(parts[0]['uvs'][0],(.25,.25))
        points=[(0,0,0),(2,0,0),(2,2,0),(1,1,0),(0,2,0)]
        self.assertEqual(len(triangulate(points)),3)

    def test_ase_submaterials_explicit_normals_colors_and_no_double_transform(self):
        part=read_ase(ASE)[0]
        self.assertEqual(part['shader'],'textures/test')
        self.assertEqual(part['vertices'][0],(0,0,0))
        self.assertEqual(part['uvs'][0],(.25,.25))
        self.assertEqual(part['normals'][1],(0,0,1))
        self.assertEqual(part['colors'][0],(.1,.2,.3,1))

    def test_maya_parent_transform_tweak_signed_edges_uvs_and_material(self):
        part=read_ma(MA)[0]
        self.assertEqual(part['shader'],'textures/test')
        self.assertEqual(list(part['vertices'][0]),[3,-4,3])
        self.assertEqual(list(part['vertices'][2]),[2,-4,4])
        self.assertEqual(part['uvs'][2],(0,0))
        self.assertEqual(list(part['normals'][0]),[0,-1,0])


class AssetTests(unittest.TestCase):
    def test_campaign_override_inheritance_skin_head_and_weapon_variants(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp)
            with Install(temp) as install:
                self.assertEqual(install.files['base']['override.txt'].read(),b'later')
                self.assertEqual(install.files['d3xp']['override.txt'].read(),b'roe')
                rows,defs=inventory(install); base=defs['base']
                row=next(r for r in rows if r['id']=='base:monster_child')
                self.assertEqual(row['animation'],'models/idle.md5anim')
                parts,_=load_parts(row,install.files['base'],base)
                self.assertEqual(parts[0]['shader'],'textures/red')
                self.assertEqual(list(parts[1]['vertices'][0]),[0,0,6])
                self.assertEqual(len([r for r in rows if r['name']=='weapon_test']),3)
                self.assertEqual(len([r for r in rows if r['id']=='d3xp:monster_child']),1)
                self.assertEqual(idle_animation({'anim':'attack'},{'anims':{'attack':'bad'}}),None)
                # idAnimated spawns at frame 1 of start_anim / anim / anim1 (admin's impdoorbreak doors).
                doors={'anims':{'doors':'doors.md5anim','loop':'loop.md5anim','a1':'a1.md5anim','idle':'idle.md5anim'}}
                self.assertEqual(idle_animation({'spawnclass':'idAnimated','anim':'doors'},doors),'doors.md5anim')
                self.assertEqual(idle_animation({'spawnclass':'idAnimated','anim':'doors','start_anim':'loop'},doors),'loop.md5anim')
                self.assertEqual(idle_animation({'spawnclass':'idAnimated','num_anims':'2','anim1':'a1'},doors),'a1.md5anim')
                self.assertEqual(idle_animation({'spawnclass':'idAnimated','anim':'missing'},doors),'idle.md5anim')

    def test_dds_bytes_shared_names_and_tga_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp)
            with Install(temp) as install:
                tex=Textures(install.files['d3xp'],Path(temp)/'out')
                record=tex.resolve('textures/test')
                original=install.files['d3xp']['dds/textures/test.dds'].read()
                self.assertEqual((tex.output/record['texture']).read_bytes(),original)
                self.assertFalse(record['texture'].startswith('d3xp'))
                tex.files=dict(tex.files); del tex.files['dds/textures/test.dds']
                other=Textures(tex.files,tex.output).resolve('textures/test')
                self.assertTrue(other['texture'].startswith('d3xp%2F'))
                with Image.open(tex.output/other['texture']) as im: self.assertEqual(im.getpixel((0,0)),(10,20,30,128))
                (tex.output/other['texture']).write_bytes(b'user edit')
                Textures(tex.files,tex.output).resolve('textures/test')
                self.assertEqual((tex.output/other['texture']).read_bytes(),b'user edit')
            self.assertNotEqual(texture_filename('a/b_c.dds','base'),texture_filename('a_b/c.dds','base'))

    def test_material_stage_roles_flags_and_image_program_naming(self):
        body=lex('''twoSided translucent clamp
        bumpmap addnormals(models/monsters/imp/imp_local.tga,heightmap(models/monsters/imp/imp_bmp.tga,6))
        { blend diffusemap map textures/test alphaTest .35 vertexColor }
        { blend specularmap map textures/spec }
        ''')
        m=material_record('test',body)
        self.assertEqual(m['diffuse'],'textures/test'); self.assertEqual(m['specular'],'textures/spec')
        self.assertEqual(m['alphaTest'],.35); self.assertTrue(m['twoSided']); self.assertEqual(m['vertexColor'],'vertexcolor')
        self.assertTrue(m['clamp'])
        self.assertEqual(dds_path(m['bump']),'dds/addnormals/models/monsters/imp/imp_local heightmap  models monsters imp imp_bmp 6.dds')
        self.assertFalse(material_record('collision',lex('collision'))['visible'])

    def test_small_import_binary_catalog_and_missing_dependency(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as out:
            fixture(temp)
            report=import_catalog(temp,out,select=['base:monster_child','base:item_missing','d3xp:monster_child'])
            self.assertEqual((report['ready'],report['failed']),(2,1))
            self.assertIn('Missing dependency',next(r['reason'] for r in report['results'] if r['status']=='failed'))
            catalog=json.loads((Path(out)/'catalog.json').read_text())
            path=Path(out)/'model_json'/(next(r['model_name'] for r in catalog if r['status']=='ready')+'.json')
            raw=json.loads(path.read_text()); self.assertIn('geometry_buffers',raw)
            data=load_model_data(path); self.assertEqual(data['game'],'doom3')
            self.assertEqual(data['metadata']['campaign'],'base')
            self.assertTrue(catalog[-1]['category'].startswith('Resurrection of Evil:'))
            with self.assertRaisesRegex(ValueError,'outside'): import_catalog(temp,Path(temp)/'out',sample=1)

    def test_browser_routes_and_rxgb_texture_library(self):
        import app as server
        from unittest.mock import patch
        from urllib.parse import quote
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as out:
            fixture(temp); output=Path(out)
            report=import_catalog(temp,output,select=['base:monster_child'])
            # Opaque alpha byte 19 becomes normal X; stored compressed data stays intact.
            block=bytes([19,19])+bytes(6)+struct.pack('<HHI',0xf800,0xf800,0)
            raw=dds_bytes(4,4,'DXT5',[block]); raw=raw[:84]+b'RXGB'+raw[88:]
            name='dds%2Ftest_local.dds'; path=output/'textures'/name; path.write_bytes(raw)
            with open_texture(path) as image: self.assertEqual(image.getpixel((0,0)),(19,0,0,255))
            with patch.dict(server.pack_dirs,doom3=output), patch.object(server,'local_data_dir',output):
                client=server.app.test_client()
                self.assertEqual(client.get('/list_models?game=doom3').status_code,200)
                self.assertIn(name,client.get('/list_textures?game=doom3').json)
                metadata=client.get('/texture_metadata?game=doom3')
                self.assertEqual(metadata.status_code,200)
                self.assertEqual(next(r for r in metadata.json if r['filename']==name)['width'],4)
                response=client.get('/texture/'+quote(name,safe='')+'?game=doom3')
                self.assertEqual(response.data,raw); response.close()
                response=client.get('/texture/'+quote(name,safe='')+'?game=doom3&preview=1')
                self.assertEqual(response.mimetype,'image/png'); response.close()
                with patch.object(server,'import_doom3_catalog',return_value={'ready':1}) as importer:
                    self.assertEqual(client.post('/import_doom3',json={'path':temp}).json,{'ready':1})
                    importer.assert_called_once_with(temp,output)


if __name__=='__main__': unittest.main()
