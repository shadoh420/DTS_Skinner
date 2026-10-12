"""Invented Ghoul2 fixtures. Retail checks require explicit opt-in."""
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from tools import import_jk2 as jk
from tests.test_rtcw import pk3, image_bytes
from tests.test_q3 import md3


def colored_image(color):
    out=io.BytesIO();Image.new('RGBA',(2,2),color).save(out,'TGA');return out.getvalue()


def gla():
    # Two bones, two frames. A deliberately nonidentity bind pose checks that
    # inverse bind transforms are not applied a second time to GLA skin matrices.
    identity=[1,0,0,0,0,1,0,0,0,0,1,0]
    bind=identity.copy();bind[3]=10
    inverse=identity.copy();inverse[3]=-10
    first=struct.pack('<64sIi24fi',b'root',0,-1,*identity,*identity,1)+struct.pack('<i',1)
    second=struct.pack('<64sIi24fi',b'child',0,0,*bind,*inverse,0)
    header=bytearray(100);skel=108;frames=skel+len(first)+len(second)
    indices=b''.join(i.to_bytes(3,'little') for i in (0,1,2,1))
    pool=frames+len(indices)
    packed=b''.join(struct.pack('<7H',49149,32766,32766,32766,32768+x*64,32768,32768) for x in (1,2,3))
    struct.pack_into('<4si64sf6i',header,0,b'2LGA',6,b'test',1,2,frames,2,pool,skel,pool+len(packed))
    return bytes(header)+struct.pack('<2i',8,8+len(first))+first+second+indices+packed


def glm(animation='models/players/test/test', flags=(0,), bones=2, parents=None):
    count=len(flags);hierarchy=164+count*4
    parents=parents or [-1]*count;rows=[];hierarchy_offsets=[]
    for i,flag in enumerate(flags):
        children=[j for j,p in enumerate(parents) if p==i]
        hierarchy_offsets.append(count*4+sum(map(len,rows)))
        rows.append(struct.pack('<64sI64siii',('part'+str(i)).encode(),flag,b'models/test/skin',0,parents[i],len(children))+struct.pack('<'+'i'*len(children),*children))
    lod=hierarchy+sum(map(len,rows));surfaces=[];offsets=[];at=lod+4+count*4
    for i in range(count):
        block=bytearray(40);ov=40
        # First weight 512/1023, second the remainder. Local refs reverse global
        # bone order, exercising the surface's indirection table.
        packed=(1<<30)|(1<<5)|(2<<20) if bones==2 else 0
        for point in ((0,0,0),(1,0,0),(0,1,0)):
            block.extend(struct.pack('<6fI4B',0,0,1,*point,packed,0,0,0,0))
        block.extend(struct.pack('<6f',0,0,1,0,0,1));ot=len(block)
        block.extend(struct.pack('<3i',0,1,2));ob=len(block)
        refs=(1,0) if bones==2 else (0,)
        block.extend(struct.pack('<'+'i'*bones,*refs))
        struct.pack_into('<10i',block,0,0,i,-at,3,ov,1,ot,bones,ob,len(block))
        offsets.append(at-lod-4);surfaces.append(bytes(block));at+=len(block)
    head=struct.pack('<4si64s64s7i',b'2LGM',6,b'test',animation.encode(),0,bones,1,lod,count,hierarchy,at)
    return head+struct.pack('<'+'i'*count,*hierarchy_offsets)+b''.join(rows)+struct.pack('<i',at-lod)+struct.pack('<'+'i'*count,*offsets)+b''.join(surfaces)


def install(root):
    pk3(root/'assets0.pk3',[
        ('models/players/test/model.glm',glm()),('models/players/test/test.gla',gla()),
        ('models/players/test/animation.cfg',b'BOTH_STAND1 1 1 -1 20'),
        ('models/players/test/model_default.skin',b'part0,models/test/skin'),
        ('models/players/test/model_red.skin',b'part0,models/test/red'),
        ('models/players/test/model_fpls.skin',b'part0,*off'),
        ('models/weapons2/saber/saber_w.glm',glm('*default',bones=1)),
        ('models/props/a.md3',md3()),('models/props/copy.md3',md3()),
        ('models/props/broken.glm',b'bad'),('models/test/skin.tga',image_bytes()),
        ('models/test/red.tga',colored_image((100,40,20,255))),
        ('models/test/audit.tga',colored_image((5,6,7,255))),('models/audit.skin',b'part,models/test/audit')])
    return root


class JK2Tests(unittest.TestCase):
    def test_compressed_frames_weights_indirection_and_default(self):
        sk=jk.read_gla(gla(),1);self.assertEqual(sk['frame_count'],2)
        mesh=jk.read_glm(glm(),sk);v=mesh['surfaces'][0]['vertices']
        self.assertAlmostEqual(v[0],0);self.assertAlmostEqual(v[1],3+2*512/1023)
        self.assertAlmostEqual(v[4],v[1]+1)
        self.assertEqual(mesh['surfaces'][0]['uvs'],[0,0,1,0,0,1])
        self.assertAlmostEqual(sk['bone_poses'][1][1,3],15)
        default=jk.read_glm(glm('*default',bones=1),jk.default_skeleton())
        self.assertEqual(default['surfaces'][0]['vertices'][3:6],[0,1,0])
        self.assertEqual(default['surfaces'][0]['normals'][:3],[0,0,1])
        # A quarter-turn quaternion (w first) and all four 10-bit weights.
        q=round((2+2**-.5)*16383)
        rotation=jk._quat_matrix((q,32766,32766,q,32768,32768,32768))
        self.assertAlmostEqual(rotation[1,0],1,places=3)
        self.assertAlmostEqual(rotation[0,1],-1,places=3)
        raw=bytearray(glm());lod=struct.unpack_from('<i',raw,148)[0];at=lod+8
        packed=(3<<30)|(1<<5)|(1<<15)|(1<<20)|(1<<22)|(1<<24)
        for v in range(3):struct.pack_into('<I4B',raw,at+40+v*32+24,packed,0,1,2,255)
        mesh=jk.read_glm(raw,jk.read_gla(gla()))
        self.assertAlmostEqual(mesh['surfaces'][0]['vertices'][1],1+2*(256+258)/1023)

    def test_flags_skin_off_suffix_and_highest_lod(self):
        raw=glm(flags=(0,1,2,0));skin=jk.read_skin(b'part3,*off\npart2_off,models/test/skin')['surfaces']
        self.assertIn('part2_off',skin);self.assertNotIn('part2',skin)
        mesh=jk.read_glm(raw,jk.read_gla(gla()),skin)
        self.assertEqual([s['name'] for s in mesh['surfaces']],['part0'])
        self.assertEqual(len(mesh['hidden']),3)
        self.assertEqual(mesh['surfaces'][0]['shaders'],[''])
        self.assertIn('Skin omits surface part0',mesh['warnings'][0])
        mesh=jk.read_glm(raw,jk.read_gla(gla()),skin,{'part2':0})
        self.assertEqual(len(mesh['surfaces']),2)
        mesh=jk.read_glm(glm(flags=(2,0),parents=[-1,0]),jk.read_gla(gla()))
        self.assertEqual([s['name'] for s in mesh['surfaces']],['part1'])
        mesh=jk.read_glm(glm(flags=(258,0),parents=[-1,0]),jk.read_gla(gla()))
        self.assertFalse(mesh['surfaces']);self.assertIn('ancestor',[h['reason'] for h in mesh['hidden']][1])
        # A second LOD with no decodable geometry is never substituted for LOD0.
        raw=bytearray(glm());lod=struct.unpack_from('<i',raw,148)[0]
        extra=struct.pack('<2i',8,0);struct.pack_into('<i',raw,144,2)
        struct.pack_into('<i',raw,160,len(raw)+len(extra));raw.extend(extra)
        self.assertEqual(len(jk.read_glm(raw,jk.read_gla(gla()))['surfaces']),1)
        raw=bytearray(glm());lod=struct.unpack_from('<i',raw,148)[0];at=lod+8
        struct.pack_into('<6i',raw,at+12,0,0,0,0,0,0)
        mesh=jk.read_glm(raw,jk.read_gla(gla()))
        self.assertFalse(mesh['surfaces']);self.assertEqual(mesh['hidden'][0]['reason'],'empty surface')

    def test_bounds_nonfinite_cycles_and_missing_skeleton(self):
        for reader,raw in ((jk.read_gla,gla()),(lambda b:jk.read_glm(b,jk.read_gla(gla())),glm())):
            for cut in (20,len(raw)-1):
                with self.assertRaises(ValueError):reader(raw[:cut])
        raw=bytearray(gla());of=struct.unpack_from('<i',raw,80)[0];raw[of:of+3]=b'\xff'*3
        with self.assertRaises(ValueError):jk.read_gla(raw)
        with self.assertRaises(ValueError):jk.read_gla(gla(),2)
        with self.assertRaisesRegex(ValueError,'mismatch'):jk.read_glm(glm(),jk.default_skeleton())
        raw=bytearray(glm());lod=struct.unpack_from('<i',raw,148)[0];at=lod+8
        struct.pack_into('<f',raw,at+40,float('nan'))
        with self.assertRaisesRegex(ValueError,'Non-finite'):jk.read_glm(raw,jk.read_gla(gla()))
        raw=bytearray(glm());struct.pack_into('<I',raw,at+40+24,31)
        with self.assertRaisesRegex(ValueError,'bone index'):jk.read_glm(raw,jk.read_gla(gla()))
        with self.assertRaisesRegex(ValueError,'Cyclic'):jk._tree([1,0],[[1],[0]])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pk3(root/'assets0.pk3',[('models/missing.glm',glm('missing'))])
            with self.assertRaisesRegex(ValueError,'Missing asset: missing.gla'):jk.load_model(jk.Game(root),'models/missing.glm')

    def test_mounts_shaders_image_paths_and_existing_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);main=root/'GameData/base';main.mkdir(parents=True)
            pk3(main/'assets0.pk3',[('models/value',b'old'),('shaders/base.shader',b'''alias {
q3map_material metal
surfaceparm nonsolid
{ map models/test/effect.tga }
{ map /models/test/skin.tga
blendFunc blend
rgbGen lightingDiffuseEntity
glow
detail
}
}'''),('models/test/skin.jpg',image_bytes()),('models/test/effect.tga',image_bytes())])
            pk3(main/'assets2.pk3',[('models/value',b'patch'),('scripts/ignored.shader',b'ignored { { map $whiteimage } }')])
            (main/'models').mkdir();(main/'models/value').write_bytes(b'loose')
            game=jk.Game(root);self.assertEqual(game.search_order,['assets2.pk3','assets0.pk3','loose'])
            self.assertEqual(game.read('models/value'),b'patch')
            textures=jk.Textures(game,root/'textures');self.assertNotIn('ignored',textures.shaders)
            filename,warnings,_=textures.resolve('alias');self.assertTrue((textures.output/filename).is_file())
            self.assertEqual(textures.shaders['alias']['static_stage']['index'],1)
            self.assertTrue(any('glow' in w for w in warnings))
            self.assertTrue(any('neutral entity color' in w for w in warnings))
            for ref in ('/models/test/skin.psd',r'\models\test\skin.tga'):
                filename,_,_=textures.resolve(ref);self.assertTrue((textures.output/filename).is_file())
            with self.assertRaises(ValueError):game.read('../outside')
            # Default shared parser still reads scripts/, never shaders/.
            game.shader_directory='scripts'
            self.assertIn('ignored',jk.base.shader_maps(game));self.assertNotIn('alias',jk.base.shader_maps(game))

    def test_catalog_variants_report_exports_library_and_edits(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));out=root/'out'
            specs=list(jk.model_specs(jk.Game(root)));self.assertFalse(any('fpls' in skin for _,skin in specs))
            with patch('app.pack_dirs',{'jk2':out}):
                client=app.test_client();r=client.post('/import_jk2',json={'path':str(root)})
                self.assertEqual(r.status_code,200,r.json)
                self.assertEqual(r.json['skipped'],1);self.assertEqual(r.json['duplicates'],1)
                self.assertEqual(r.json['texture_library']['after'],2)
                self.assertFalse(r.json['texture_library']['retained_unreferenced'])
                catalog=client.get('/list_models?game=jk2').json
                heroes=[row for row in catalog if row['category']=='Characters'];self.assertEqual(len(heroes),2)
                name=heroes[0]['model_name']
                self.assertEqual(client.get('/model_json/'+name+'?game=jk2').json['game'],'jk2')
                self.assertTrue(client.get('/list_textures?game=jk2').json)
                self.assertEqual(client.get('/export_obj/'+name+'?game=jk2').status_code,200)
                glb=client.get('/export_glb/'+name+'?game=jk2');self.assertEqual(glb.status_code,200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'],'static')
                page=client.get('/').get_data(as_text=True);self.assertIn('id="jk2Import"',page)
                self.assertIn('<option value="jk2">Jedi Outcast</option>',page)
                self.assertEqual(client.post('/import_jk2',json={'path':str(root)},headers={'Origin':'https://outside'}).status_code,403)
            data=json.loads((out/'model_json'/(name+'.json')).read_text());self.assertEqual(data['metadata']['frame'],1)
            texture=out/'textures'/data['material_textures'][0];Image.new('RGBA',(2,2),'orange').save(texture);saved=texture.read_bytes()
            jk.import_catalog(root,out);self.assertEqual(texture.read_bytes(),saved)


INSTALL=Path('C:/Program Files (x86)/Steam/steamapps/common/Jedi Outcast/GameData')
@unittest.skipUnless(os.environ.get('JK2_INSTALL_TESTS')=='1' and (INSTALL/'base/assets0.pk3').exists(),'Optional single-file JK2 probe; set JK2_INSTALL_TESTS=1')
class InstallTests(unittest.TestCase):
    def test_saber_builtin_skeleton(self):
        model,meta,_=jk.load_model(jk.Game(INSTALL),'models/weapons2/saber/saber_w.glm')
        self.assertEqual(meta['animation'],'*default');self.assertTrue(model['surfaces'])


if __name__=='__main__':unittest.main()
