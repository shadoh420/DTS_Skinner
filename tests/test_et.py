"""Invented ET binary fixtures only; retail probe is explicitly opt-in."""
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from tools import import_et as et
from tests.test_rtcw import pk3, image_bytes, mdc
from tests.test_q3 import md3


def mdx():
    frames=b''
    for root in (10.,20.):
        frames+=struct.pack('<13f12h',*([0.]*10),root,20,30,*([0]*6),0,16384,0,0,0,16384)
    bones=struct.pack('<64siffi',b'root',-1,0,0,0)+struct.pack('<64siffi',b'child',0,1,2,0)
    return struct.pack('<4si64s6i',b'MDXW',2,b'fixture',2,2,96,96+len(frames),0,96+len(frames)+len(bones))+frames+bones


def mdm():
    surface=bytearray(176);struct.pack_into('<i64s64s',surface,0,0,b'part',b'models/test/diffuse')
    ov=len(surface)
    for point in ((1,0,0),(0,1,0),(0,0,1)):
        surface.extend(struct.pack('<5fi',0,0,1,0,0,2))
        for bone,weight in ((0,.25),(1,.75)):surface.extend(struct.pack('<if3f',bone,weight,*point))
    tri=len(surface);surface.extend(struct.pack('<3i',0,1,2))
    collapse=len(surface);surface.extend(struct.pack('<3i',0,1,2))
    refs=len(surface);surface.extend(struct.pack('<2i',1,0)) # Set, NOT weight remapping.
    struct.pack_into('<11i',surface,132,0,0,-100,3,ov,1,tri,collapse,2,refs,len(surface))
    tags=b''
    for name in (b'tag_head',b'tag_back'):
        tags+=struct.pack('<64s9fi3f3i2i',name,1,0,0,0,1,0,0,0,1,1,1,0,0,2,128,136,0,1)
    return struct.pack('<4si64s2f5i',b'MDMW',3,b'fixture',1,0,1,100,2,100+len(surface),100+len(surface)+len(tags))+surface+tags


CHAR=b'''characterDef {
 mesh "models/players/axis/body.mdm"
 skin "soldier"
 animationGroup "animations/test.anim"
 animationScript "animations/test.script"
 hudhead "models/head.md3"
 hudheadskin "models/head.skin"
 hudheadanims "animations/head.anim"
 undressedCorpseModel "models/naked.mdm"
 undressedCorpseSkin "axis"
}'''


def install(root):
    pk3(root/'pak0.pk3',[
        ('characters/axis/soldier.char',CHAR),('models/players/axis/body.mdm',mdm()),('models/naked.mdm',mdm()),
        ('models/players/axis/body_soldier.skin',b'part,models/test/diffuse\nmd3_hat,models/hat.md3\nmd3_rank,models/rank.md3'),
        ('models/naked_axis.skin',b'part,models/test/diffuse'),('models/head.md3',md3('tag_mouth')),('models/head.skin',b'part,models/test/skin'),
        ('models/hat.mdc',mdc()),('models/hat_soldier.skin',b'part,models/test/diffuse'),
        ('models/test/diffuse.tga',image_bytes()),('models/test/skin.tga',image_bytes()),
        ('animations/body.mdx',mdx()),('animations/head.anim',b'0 1 15 1\n'),
        ('animations/test.anim',b'animgroup { animfile "animations/body.mdx" { #include "animations/body.aninc" } }'),
        ('animations/body.aninc',b'stand_none 1 1 1 15 0 0 0\n'),
        ('animations/test.script',b'STATE COMBAT { idle { weapons none { both stand_none } } }'),
        ('models/bad.mdm',b'bad'),('models/dup.mdc',mdc()),
        ('models/tag.md3',md3()),('models/tag.mdc',mdc()),
    ])
    return root


class ETTests(unittest.TestCase):
    def test_leading_slash_images_share_catalog_texture_without_aliasing_shaders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pk3(root/'pak0.pk3',[
                ('models/test/grenade.tga',image_bytes()),
                ('scripts/test.shader',b'alias { implicitMap /models/test/grenade.tga }')])
            game=et.Game(root);library=et.rtcw.TextureLibrary(root/'out',[game])
            textures=et.CatalogTextures(game,library)
            names=set()
            for ref in ('models/test/grenade','/models/test/grenade','/models/test/grenade.tga',r'\models\test\grenade.tga','alias'):
                filename,warnings,_=textures.resolve(ref);names.add(filename)
                self.assertEqual(textures.results[ref]['status'],'resolved',warnings)
            self.assertEqual(len(names),1)
            self.assertEqual(len(list(library.output.glob('*.png'))),1)
            textures.resolve('/alias')  # FS normalization does not rename shaders.
            self.assertEqual(textures.results['/alias']['status'],'unresolved')
            with self.assertRaisesRegex(ValueError,'Invalid RTCW asset path'):
                game.read('/models/test/grenade.tga')  # Strict archive API stays strict.

    def test_mdx_mdm_weights_tags_and_malformed_bounds(self):
        skeleton=et.read_mdx(mdx(),1);mesh=et.read_mdm(mdm(),skeleton)
        self.assertEqual(skeleton['frame_count'],2)
        for actual,expected in zip(mesh['surfaces'][0]['vertices'][:3],(20.25,20.75,30)):
            self.assertAlmostEqual(actual,expected,places=2)
        self.assertAlmostEqual(mesh['tags']['tag_head'][0],20,places=2)
        self.assertAlmostEqual(mesh['tags']['tag_head'][1],21,places=2)
        for raw in (mdx()[:20],mdx()[:-1]):
            with self.assertRaises(ValueError):et.read_mdx(raw)
        with self.assertRaises(ValueError):et.read_mdx(mdx(),2)
        raw=bytearray(mdx());bones=struct.unpack_from('<i',raw,84)[0];struct.pack_into('<i',raw,bones+64,1)
        with self.assertRaisesRegex(ValueError,'Cyclic'):et.read_mdx(raw)
        for raw in (mdm()[:50],mdm()[:-1]):
            with self.assertRaises(ValueError):et.read_mdm(raw,skeleton)
        raw=bytearray(mdm());struct.pack_into('<i',raw,100+176+24,99)
        with self.assertRaisesRegex(ValueError,'weight'):et.read_mdm(raw,skeleton)
        raw=bytearray(mdm());struct.pack_into('<f',raw,100+176,float('nan'))
        with self.assertRaisesRegex(ValueError,'Non-finite'):et.read_mdm(raw,skeleton)

    def test_mount_order_isolation_and_shader_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);main=root/'etmain';main.mkdir()
            pk3(main/'pak0.pk3',[('models/a',b'low'),('scripts/base.shader',b'''x { implicitBlend models/test/diffuse.tga }
y { { map effects/glow.tga } { map models/test/diffuse.tga
blendfunc blend
rgbGen lightingDiffuse
} }'''),('models/test/diffuse.tga',image_bytes())])
            pk3(main/'pak2.pk3',[('models/a',b'high'),('scripts/new.shader',b'x { implicitMap other }')])
            pk3(main/'mp_bin.pk3',[('models/a',b'mpbin')])
            (root/'Main').mkdir();pk3(root/'Main/pak9.pk3',[('models/rtcw',b'never')])
            game=et.Game(root)
            self.assertEqual(game.search_order,['loose','pak2.pk3','pak0.pk3','mp_bin.pk3'])
            self.assertEqual(game.read('models/a'),b'high');self.assertNotIn('models/rtcw',game.assets)
            shaders=et.shader_maps(game);self.assertEqual(shaders['x']['target'],'models/test/diffuse.tga')
            self.assertTrue(shaders['x']['settings']['depthWrite']);self.assertEqual(shaders['x']['settings']['cull'],'none')
            self.assertEqual(shaders['y']['target'],'models/test/diffuse.tga')
            self.assertEqual(shaders['y']['static_stage']['index'],1)
            self.assertFalse(shaders['y']['settings']['blend'])
            (main/'models').mkdir();(main/'models/a').write_bytes(b'loose')
            self.assertEqual(et.Game(main).read('models/a'),b'loose')

    def test_character_pose_attachments_import_exports_and_edits(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));out=root/'out';game=et.Game(root)
            definition=et.read_char(CHAR);parts,meta=et.character_parts(game,definition)
            self.assertEqual(game.model_path('models/tag.md3'),'models/tag.mdc')
            self.assertEqual(et.read_char(CHAR.upper()),definition)
            self.assertEqual(meta['frame'],1);self.assertEqual(len(parts),3)
            self.assertEqual(len(parts[-1]['tags']),2);self.assertIn('stand_none',meta['pose'])
            self.assertTrue(any('rank zero' in w for w in meta['warnings']))
            with patch('app.pack_dirs',{'et':out}):
                client=app.test_client();r=client.post('/import_et',json={'path':str(root)})
                self.assertEqual(r.status_code,200,r.json);self.assertTrue(r.json['skipped'])
                self.assertTrue(r.json['duplicates'])
                library=r.json['texture_library']
                self.assertEqual(library['after'],1)
                self.assertEqual(library['referenced'],1)
                self.assertFalse(library['duplicate_groups'])
                self.assertFalse(library['retained_unreferenced'])
                self.assertRegex(next((out/'textures').glob('*.png')).name,r'^models_test_diffuse__[0-9a-f]{12}\.png$')
                catalog=client.get('/list_models?game=et').json
                hero=next(r for r in catalog if r['category']=='Characters');name=hero['model_name']
                data=client.get('/model_json/'+name+'?game=et').json
                self.assertEqual(data['game'],'et');self.assertEqual(len(data['groups']),3)
                self.assertEqual(len(set(data['material_textures'])),1)
                self.assertTrue(client.get('/list_textures?game=et').json)
                self.assertEqual(client.get('/export_obj/'+name+'?game=et').status_code,200)
                glb=client.get('/export_glb/'+name+'?game=et');self.assertEqual(glb.status_code,200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'],'static')
                self.assertEqual(client.post('/import_et',json={'path':''}).status_code,400)
                self.assertEqual(client.post('/import_et',json={'path':str(root)},headers={'Origin':'https://outside'}).status_code,403)
            png=out/'textures'/data['material_textures'][0];Image.new('RGBA',(2,2),'red').save(png);saved=png.read_bytes()
            et.import_catalog(root,out);self.assertEqual(png.read_bytes(),saved)

    def test_fallback_and_unresolved_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));pk3(root/'pak1.pk3',[
                ('animations/test.script',b'STATE COMBAT {}'),('models/head.skin',b'part,missing'),
                ('models/broken.skin',b'nonsense'),('models/players/axis/body_soldier.skin',b'part,models/test/diffuse\nmd3_back,models/absent.md3')])
            game=et.Game(root);parts,meta=et.character_parts(game,et.read_char(CHAR))
            self.assertEqual(meta['frame'],0);self.assertIn('fallback',meta['pose'])
            self.assertTrue(any('Accessory' in w for w in meta['warnings']))
            report=et.import_catalog(root,root/'out')
            self.assertTrue(report['unresolved_skin_reasons'])
            self.assertTrue(any(r['status']=='unreadable' for r in report['skin_results']))
            with self.assertRaises(ValueError):et.read_char(b'characterDef { mesh }')
            with self.assertRaises(ValueError):et.read_char(CHAR.replace(b'skin "soldier"',b'skin ""'))
            with self.assertRaisesRegex(ValueError,'Unknown'):et.read_char(CHAR.replace(b'skin "soldier"',b'skni "soldier"'))
            with self.assertRaises(ValueError):et.read_char(CHAR.replace(b'animations/test.anim',b'../outside.anim'))


@unittest.skipUnless(os.environ.get('ET_INSTALL_TESTS')=='1' and Path('C:/Program Files (x86)/Steam/steamapps/common/Wolfenstein Enemy Territory/etmain/pak0.pk3').exists(),'Optional ET single-character probe; set ET_INSTALL_TESTS=1')
class InstallTests(unittest.TestCase):
    def test_one_soldier(self):
        game=et.Game('C:/Program Files (x86)/Steam/steamapps/common/Wolfenstein Enemy Territory')
        definition=et.read_char(game.read('characters/temperate/axis/soldier.char'))
        parts,meta=et.character_parts(game,definition)
        self.assertEqual(meta['frame'],2734);self.assertGreater(len(parts),2)


if __name__=='__main__':unittest.main()
