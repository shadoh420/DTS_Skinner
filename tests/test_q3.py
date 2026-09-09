"""MD3 fixture exercises native frames, tags, skins, namespaces and both exports."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from app import app
from tools.import_q3 import read_md3, import_catalog, load_animated_model, asset_name, current_import, Textures
from tools.model_data import load_model_data


def md3(tag=None):
    frames, vertices, triangles = 2, 3, 1
    otags = 108 + frames*56
    osurface = otags + (frames*112 if tag else 0)
    otri, oshader, ouv, overt = 108, 120, 188, 212
    send = overt + frames*vertices*8
    raw = bytearray(osurface+send)
    struct.pack_into('<4si64s9i', raw, 0, b'IDP3',15,b'fixture',0,frames,1 if tag else 0,1,0,108,otags,osurface,len(raw))
    if tag:
        for frame in range(frames):
            struct.pack_into('<64s12f',raw,otags+frame*112,tag.encode(),frame*2,0,0,1,0,0,0,1,0,0,0,1)
    struct.pack_into('<4s64s10i',raw,osurface,b'IDP3',b'body',0,frames,1,vertices,triangles,otri,oshader,ouv,overt,send)
    struct.pack_into('<3i',raw,osurface+otri,0,2,1) # Native MD3 clockwise; authored normal is +Z.
    struct.pack_into('<64si',raw,osurface+oshader,b'models/test/skin.tga',0)
    struct.pack_into('<6f',raw,osurface+ouv,0,0,1,0,0,1)
    for frame in range(frames):
        for index,(x,y,z) in enumerate(((0,0,0),(64,0,0),(0,64,0))):
            struct.pack_into('<3hH',raw,osurface+overt+(frame*vertices+index)*8,x,y,z+frame*64,0)
    return bytes(raw)


class Quake3Tests(unittest.TestCase):
    def test_shader_extension_lookup_cutout_remap_and_first_stage(self):
        image=io.BytesIO(); Image.new('RGBA',(2,2),(50,80,110,0)).save(image,format='PNG')
        script=b'''models/test/skin
        { cull none
          { map models/test/skin.tga alphaFunc GE128 depthWrite }
        }
        models/test/pole { { map models/test/skull.tga tcGen environment } }
        models/test/cloth {
          { map models/test/skin.tga }
          { map models/test/glow.tga blendFunc add }
        }
        models/test/glow { { clampMap models/test/skin.tga blendFunc GL_ONE GL_ONE } }
        models/test/animated { { animMap 5 models/test/skin.tga models/test/other.tga } }
        models/test/white { { map $whiteimage } }
        models/test/back { cull back { map $whiteimage } }
        '''
        assets={key:dict(read=lambda raw=raw:raw) for key,raw in {
            'scripts/models.shader':script,'models/test/skin.png':image.getvalue(),
            'models/test/skull.jpg':image.getvalue()}.items()}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory); textures=Textures(assets,output)
            name,warnings,settings=textures.resolve('MODELS/TEST/SKIN.TGA')
            self.assertEqual(warnings,[])
            self.assertEqual((settings['alphaFunc'],settings['cull'],settings['depthWrite']),('GE128','none',True))
            self.assertEqual(Image.open(output/name).getpixel((0,0)),(50,80,110,0))
            pole,warnings,settings=textures.resolve('models/test/pole.tga')
            self.assertEqual(warnings,[])
            self.assertEqual(pole,asset_name('models/test/skull.jpg')+'.png')
            self.assertEqual(settings['tcGen'],'environment')
            _,warnings,settings=textures.resolve('models/test/cloth.tga')
            self.assertEqual(settings['alphaFunc'],'')
            self.assertEqual(settings['blend'],[]) # Later alpha/additive passes must not change the base pass.
            self.assertIn('additional shader stages',warnings[0])
            _,_,settings=textures.resolve('models/test/glow.tga')
            self.assertEqual(settings['blend'],['gl_one','gl_one'])
            self.assertTrue(settings['clamp'])
            self.assertFalse(settings['depthWrite'])
            animated,warnings,_=textures.resolve('models/test/animated')
            self.assertEqual(animated,name)
            self.assertIn('first frame shown',warnings[0])
            white,warnings,_=textures.resolve('models/test/white')
            self.assertEqual(warnings,[])
            self.assertEqual(Image.open(output/white).getpixel((0,0)),(255,255,255,255))
            self.assertEqual(textures.resolve('models/test/back')[2]['cull'],'front')
            self.assertEqual(textures.resolve('models/test/white')[2]['cull'],'back')

    def test_frames_tags_and_malformed_bounds(self):
        first, second = read_md3(md3('tag_torso')),read_md3(md3('tag_torso'),1)
        self.assertEqual(first['surfaces'][0]['vertices'][:3],[0,0,0])
        self.assertEqual(second['surfaces'][0]['vertices'][:3],[0,0,1])
        self.assertEqual(second['tags']['tag_torso'][0],2)
        for raw in (md3()[:100],md3()[:-1],b'nope'+md3()[4:]):
            with self.assertRaises(ValueError): read_md3(raw)
        broken=bytearray(md3()); struct.pack_into('<i',broken,108+2*56+88,-1)
        with self.assertRaises(ValueError): read_md3(broken)
        with self.assertRaises(ValueError): read_md3(md3(),2)
        lod=bytearray(md3()); struct.pack_into('<64s',lod,108+2*56+4,b'body_1')
        self.assertEqual(read_md3(lod)['surfaces'][0]['name'],'body')

    def test_local_import_skin_edits_animation_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'pak0.pk3'; output=root/'q3'
            image=io.BytesIO(); Image.new('RGB',(2,2),(210,40,60)).save(image,format='TGA')
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('models/test/shape.md3',md3())
                archive.writestr('models/test/skin.tga',image.getvalue())
            result=import_catalog(source,output)
            self.assertEqual(result,dict(entries=1,ready=1))
            name=asset_name('models/test/shape.md3')
            data=load_model_data(current_import(output)/'model_json'/(name+'.json'))
            self.assertEqual(data['indices'],[0,1,2]) # Shared preview/export is CCW after axis rotation.
            animated=load_animated_model(name,output,data)
            self.assertEqual(len(animated['animation_clips']),1)
            self.assertEqual(animated['animation_clips'][0]['frames'][1]['vertices'][:3],[0,1,0])
            texture=output/'textures'/data['material_textures'][0]
            Image.new('RGB',(2,2),(10,20,30)).save(texture)
            edited=texture.read_bytes(); import_catalog(source,output)
            self.assertEqual(texture.read_bytes(),edited)
            with patch('app.q3_dir',output),patch('app.local_data_dir',root):
                client=app.test_client()
                self.assertEqual(len(client.get('/list_models?game=q3').json),1)
                self.assertEqual(client.get('/list_models?game=invalid').status_code,400)
                self.assertEqual(client.get('/model_json/'+name+'?game=q3').json['game'],'q3')
                with contextlib.redirect_stdout(io.StringIO()):
                    response=client.get('/export_obj/'+name+'?game=q3')
                self.assertEqual(response.status_code,200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    self.assertEqual(archive.read(texture.name),edited)
                    self.assertIn(b'Quake 3 MD3',archive.read('README.txt'))
                response=client.get('/export_glb/'+name+'?game=q3')
                self.assertEqual(response.status_code,200,response.data[:200])
                size=struct.unpack_from('<I',response.data,12)[0]
                gltf=json.loads(response.data[20:20+size])
                self.assertEqual(len(gltf['animations']),1)
                self.assertEqual(gltf['materials'][0]['name'],texture.name)
                self.assertEqual(client.post('/import_q3',json={'path':str(source)},headers={'Origin':'https://elsewhere.invalid'}).status_code,403)
                self.assertEqual(client.post('/import_q3',json={'path':str(root/'absent')}).status_code,422)
                before=current_import(output)
                with zipfile.ZipFile(source,'w') as archive:
                    archive.writestr('models/test/shape.md3',b'broken')
                import_catalog(source,output)
                self.assertNotEqual(before,current_import(output))
                self.assertEqual((before/'sources'/(name+'.md3')).read_bytes(),md3())
                self.assertEqual(client.get('/model_json/'+name+'?game=q3').status_code,404)

    def test_player_assembly_skin_and_attachment_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'pak0.pk3'; output=root/'q3'
            with zipfile.ZipFile(source,'w') as archive:
                for part,tag in [('lower','tag_torso'),('upper','tag_head'),('head',None)]:
                    archive.writestr('models/players/test/'+part+'.md3',md3(tag))
                    archive.writestr('models/players/test/'+part+'_default.skin','body,models/test/team\n')
                archive.writestr('models/players/test/animation.cfg','\n'.join(['0 2 0 10']*25))
            import_catalog(source,output)
            catalog=json.loads((current_import(output)/'catalog.json').read_text())
            assembled=next(x for x in catalog if x['category']=='Complete players')
            self.assertEqual(assembled['status'],'ready',assembled)
            data=load_model_data(current_import(output)/'model_json'/(assembled['model_name']+'.json'))
            self.assertEqual(len(data['vertices']),27)
            animated=load_animated_model(assembled['model_name'],output,data)
            self.assertEqual(len(animated['animation_clips']),25)
            frame=animated['animation_clips'][0]['frames'][1]['vertices']
            self.assertEqual(frame[9:12],[0,1,2])  # Upper: own sample plus lower's moving tag.
            self.assertEqual(frame[18:21],[0,0,4]) # Head: upper and lower tag chain.
            data['metadata']['animations']['LEGS_RUN']['loop_frames']=1
            looped=load_animated_model(assembled['model_name'],output,data)
            self.assertTrue(any(c['name']=='LEGS_RUN_LOOP' for c in looped['animation_clips']))
            data['metadata']['animations']['LEGS_RUN']['count']=2147483647
            with self.assertRaisesRegex(ValueError,'Animation range'):
                load_animated_model(assembled['model_name'],output,data)


if __name__=='__main__': unittest.main()
