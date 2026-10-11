"""Synthetic RTCW fixtures; optional single-file retail probes are opt-in."""
import io
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from tools import import_q3 as q3
from tools.import_rtcw import (Game, Textures, character_parts, import_catalog,
    idle_pose, mdc_normal, model_specs, read_mdc, read_mds, read_model, read_skin, shader_maps)
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/Return to Castle Wolfenstein')


def mdc():
    # Two frames, one base, one compressed; one triangle and a translated tag.
    head = bytearray(112)
    struct.pack_into('<4si64s',head,0,b'IDPC',2,b'test')
    frames = bytes(112)
    names = struct.pack('<64s',b'tag_mouth')
    tags = struct.pack('<12h',64,128,192,0,0,0,64,128,192,0,8175,0)
    start = 112+len(frames)+len(names)+len(tags)
    surface = bytearray(124)
    struct.pack_into('<4s64s',surface,0,b'IDPC',b'part_1')
    data = [struct.pack('<3i',0,1,2),struct.pack('<64si',b'models/test/diffuse',0),
            struct.pack('<6f',0,0,1,0,0,1),
            b''.join(struct.pack('<3hH',*v,0) for v in ((0,0,0),(64,0,0),(0,64,0))),
            struct.pack('<3I',*(128 | (127<<8) | (125<<16) | (32<<24),)*3),
            struct.pack('<2h',0,0),struct.pack('<2h',-1,0)]
    offsets=[]
    for block in data:
        offsets.append(len(surface));surface.extend(block)
    struct.pack_into('<14i',surface,68,0,1,1,1,3,1,*offsets,len(surface))
    struct.pack_into('<10i',head,72,0,2,1,1,0,112,224,288,start,start+len(surface))
    return bytes(head)+frames+names+tags+surface


def mds():
    # Root at (10,20,30); child distance 2 along Y, with 90-degree yaw.
    head=bytearray(120)
    struct.pack_into('<4si64s2f',head,0,b'MDSW',4,b'body',1,0)
    frames = struct.pack('<13f12h',*([0.]*10),10,20,30,*([0]*6),0,16384,0,0,0,16384)
    bones = struct.pack('<64siffi',b'root',-1,0,0,0)+struct.pack('<64siffi',b'head',0,0,2,1)
    tags = struct.pack('<64sfi',b'tag_head',0,1)
    start=120+len(frames)+len(bones)+len(tags)
    surface=bytearray(176)
    struct.pack_into('<i64s64s',surface,0,0,b'part',b'models/test/diffuse')
    verts=bytearray()
    for v in ((1,0,0),(0,1,0),(0,0,1)):
        verts.extend(struct.pack('<5fiif',0,0,1,0,0,2,-1,0))
        verts.extend(struct.pack('<if3f',0,.25,*v))
        verts.extend(struct.pack('<if3f',1,.75,*v))
    ov=len(surface);surface.extend(verts)
    tri=len(surface);surface.extend(struct.pack('<3i',0,1,2))
    collapse=len(surface);surface.extend(struct.pack('<3i',0,1,2))
    refs=len(surface);surface.extend(struct.pack('<2i',1,0))  # References are a set, not a weight-index remap.
    struct.pack_into('<11i',surface,132,0,0,-start,3,ov,1,tri,collapse,2,refs,len(surface))
    struct.pack_into('<10i',head,80,1,2,120,120+len(frames),0,1,start,1,120+len(frames)+len(bones),start+len(surface))
    return bytes(head)+frames+bones+tags+surface


def image_bytes():
    out=io.BytesIO()
    Image.new('RGBA',(2,2),(20,80,100,255)).save(out,'TGA')
    return out.getvalue()


def pk3(path, entries):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in entries: z.writestr(n,b)


def install(root):
    folder='models/players/test/'
    pk3(root/'pak0.pk3',[
        (folder+'body.mds',mds()),(folder+'head.mdc',mdc()),
        (folder+'acc/hat.mdc',mdc()),
        (folder+'body_default.skin',b'part,models/test/diffuse\nplayerscale,2'),
        (folder+'head_default.skin',b'md3_part,head.md3\nmd3_hat,acc/hat.md3\npart,models/test/diffuse'),
        (folder+'wolfanim.cfg',b'VERSION 2\nSKELETAL\nSTARTANIMS\nidle 0 1 1 15 0\nENDANIMS'),
        ('models/items/one.mdc',mdc()),('models/items/alias.mdc',mdc()),
        ('models/items/bad.mdc',b'IDPC'),
        ('models/test/diffuse.tga',image_bytes()),
        ('models/test/orphan.skin',b'part,missing/image'),
        ('scripts/test.shader',b'models/test/diffuse\n{\nimplicitMask -\n}\n')])
    return root


class RTCWTests(unittest.TestCase):
    def test_catalog_shares_source_images_and_prunes_audit_only_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            extra=io.BytesIO();Image.new('RGBA',(2,2),'yellow').save(extra,'TGA')
            pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes()),
                ('models/unused.skin',b'part,models/unused'),('models/unused.tga',extra.getvalue())])
            # A different winning archive member/path with identical source bytes also shares.
            pk3(root/'mp_pak0.pk3',[('models/mp.mdc',mdc()),('models/mp.skin',b'part,models/other'),
                ('models/other.tga',image_bytes())])
            report=import_catalog(root,out)
            self.assertEqual(report['texture_library']['after'],1)
            self.assertFalse(report['texture_library']['retained_unreferenced'])
            self.assertFalse(report['texture_library']['duplicate_groups'])
            self.assertTrue(report['texture_library']['removed'])
            refs={t for p in (out/'model_json').glob('*.json') for t in json.loads(p.read_text())['material_textures']}
            self.assertEqual(refs,{p.name for p in (out/'textures').glob('*.png')})
            # MP's path sorts before SP's even though SP is converted first.
            self.assertEqual(refs,{'models_other__'+hashlib.sha256(image_bytes()).hexdigest()[:12]+'.png'})
            again=import_catalog(root,out)
            self.assertEqual(again['texture_library']['before'],1)
            self.assertEqual(refs,{p.name for p in (out/'textures').glob('*.png')})

    def test_catalog_migrates_legacy_copies_without_losing_edits(self):
        for edit_mp in (False,True):
            with self.subTest(edit_mp=edit_mp),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out=root/'out';tex=out/'textures'
                pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes()),
                    ('models/unused.skin',b'part,models/unused'),('models/unused.tga',image_bytes())])
                pk3(root/'mp_pak0.pk3',[('models/mp.mdc',mdc())])
                legacy={s:Textures(Game(root,s),tex).resolve('models/test/diffuse')[0] for s in ('sp','mp')}
                orphan=Textures(Game(root),tex).resolve('models/unused')[0]
                Image.new('RGBA',(2,2),'purple').save(tex/orphan)
                orphan_bytes=(tex/orphan).read_bytes()
                if edit_mp:Image.new('RGBA',(2,2),'red').save(tex/legacy['mp'])
                mp_bytes=(tex/legacy['mp']).read_bytes()
                report=import_catalog(root,out)
                self.assertEqual(report['texture_library']['after'],3 if edit_mp else 2)
                self.assertFalse((tex/legacy['sp']).exists())
                self.assertEqual((tex/orphan).read_bytes(),orphan_bytes)
                self.assertEqual(report['texture_library']['retained_unreferenced'],[orphan])
                cat=json.loads((out/'catalog.json').read_text())
                mp=next(r for r in cat if r['model_name'].startswith('mp_') and r['status']=='ready')
                if edit_mp:
                    self.assertEqual(mp['texture_name'],legacy['mp'])
                    self.assertEqual((tex/legacy['mp']).read_bytes(),mp_bytes)
                else:self.assertFalse((tex/legacy['mp']).exists())
                # A shared canonical PNG remains editable and survives even after its models disappear.
                canonical=next(tex.glob('models_test_diffuse__*.png'))
                Image.new('RGBA',(2,2),'blue').save(canonical);edited=canonical.read_bytes()
                import_catalog(root,out)
                self.assertEqual(canonical.read_bytes(),edited)
                import_catalog(root,out,model_filter='no-such-model')
                self.assertEqual(canonical.read_bytes(),edited)
                self.assertEqual((tex/orphan).read_bytes(),orphan_bytes)
                # A later source update matching a saved edit must not erase its edit history.
                replacement=io.BytesIO();Image.new('RGBA',(2,2),'purple').save(replacement,'TGA')
                pk3(root/'sp_pak1.pk3',[('models/unused.tga',replacement.getvalue())])
                import_catalog(root,out)
                self.assertEqual((tex/orphan).read_bytes(),orphan_bytes)

    def test_catalog_retires_unchanged_previous_output_but_keeps_unknown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes())])
            import_catalog(root,out)
            generated=next((out/'textures').glob('*.png'))
            unknown=out/'textures/user.png';Image.new('RGBA',(1,1),'red').save(unknown)
            report=import_catalog(root,out,model_filter='no-such-model')
            self.assertFalse(generated.exists());self.assertTrue(unknown.is_file())
            self.assertEqual(report['texture_library']['after'],1)
            self.assertEqual(report['texture_library']['retained_unreferenced'],['user.png'])

    def test_catalog_keeps_distinct_source_bytes_even_with_identical_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            png=io.BytesIO()
            with Image.open(io.BytesIO(image_bytes())) as image:image.save(png,'PNG')
            pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes())])
            pk3(root/'mp_pak0.pk3',[('models/mp.mdc',mdc()),('models/mp.skin',b'part,models/other'),('models/other.png',png.getvalue())])
            report=import_catalog(root,out)['texture_library']
            self.assertEqual(report['after'],2)
            self.assertEqual(len(report['duplicate_groups']),1)
            self.assertEqual(len(report['duplicate_groups'][0]),2)

    def test_catalog_whiteimage_is_shared_and_unused_copies_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('scripts/white.shader',b'models/test/diffuse\n{\n { map $whiteimage }\n}')])
            pk3(root/'mp_pak0.pk3',[('models/mp.mdc',mdc())])
            report=import_catalog(root,out)['texture_library']
            self.assertEqual(report['after'],1);self.assertFalse(report['retained_unreferenced'])
            name=next((out/'textures').glob('*.png')).name
            self.assertEqual(import_catalog(root,out)['texture_library']['after'],1)
            self.assertEqual(name,next((out/'textures').glob('*.png')).name)

    def test_catalog_migrates_hash_names_and_preserves_edited_hash_files(self):
        for edited in (False,True):
            with self.subTest(edited=edited),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out=root/'out';tex=out/'textures';tex.mkdir(parents=True)
                pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes())])
                pk3(root/'mp_pak0.pk3',[('models/mp.mdc',mdc())])
                digest=hashlib.sha256(image_bytes()).hexdigest()
                old=tex/('image_'+digest+'.png')
                with Image.open(io.BytesIO(image_bytes())) as image:image.convert('RGBA').save(old)
                baseline=hashlib.sha256(old.read_bytes()).hexdigest()
                (out/'texture-manifest.json').write_text(json.dumps({old.name:baseline}))
                if edited:Image.new('RGBA',(2,2),'red').save(old)
                before=old.read_bytes();report=import_catalog(root,out)
                expected=old.name if edited else 'models_test_diffuse__'+digest[:12]+'.png'
                self.assertEqual({p.name for p in tex.glob('*.png')},{expected})
                refs={t for p in (out/'model_json').glob('*.json') for t in json.loads(p.read_text())['material_textures']}
                self.assertEqual(refs,{expected});self.assertFalse(report['texture_library']['duplicate_groups'])
                self.assertEqual((tex/expected).read_bytes(),before)
                import_catalog(root,out)
                self.assertEqual((tex/expected).read_bytes(),before)
                if edited:
                    import_catalog(root,out,model_filter='no-such-model')
                    self.assertEqual(old.read_bytes(),before)

    def test_catalog_name_uses_smallest_winning_path_not_shadowed_or_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            different=io.BytesIO();Image.new('RGBA',(2,2),'red').save(different,'TGA')
            pk3(root/'pak0.pk3',[('models/sp.mdc',mdc()),('models/test/diffuse.tga',image_bytes()),
                ('models/aaa.tga',image_bytes()),('MODELS/Best.tga',image_bytes())])
            # The lexically first path is shadowed in both mounts by different bytes.
            pk3(root/'sp_pak1.pk3',[('models/aaa.tga',different.getvalue())])
            pk3(root/'mp_pak1.pk3',[('models/aaa.tga',different.getvalue()),('models/mp.mdc',mdc())])
            report=import_catalog(root,out)
            name='models_best__'+hashlib.sha256(image_bytes()).hexdigest()[:12]+'.png'
            self.assertEqual({p.name for p in (out/'textures').glob('*.png')},{name})
            self.assertEqual(report['texture_library']['after'],1)

    def test_mdc_base_compressed_and_tags(self):
        from tests.test_q3 import md3
        native = read_model(md3(),1)
        self.assertEqual(native['surfaces'][0]['vertices'][2],1)
        a,b=read_mdc(mdc()),read_mdc(mdc(),1)
        self.assertEqual(a['surfaces'][0]['name'],'part')
        self.assertEqual(a['surfaces'][0]['vertices'][:3],[0,0,0])
        for actual,expected in zip(b['surfaces'][0]['vertices'][:3],[.05,0,-.1]): self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(b['surfaces'][0]['normals'][2],-math.sin(math.pi/16))
        self.assertEqual(a['tags']['tag_mouth'][:3],(1,2,3))
        self.assertAlmostEqual(b['tags']['tag_mouth'][4],1)
        for i in range(256): self.assertAlmostEqual(sum(x*x for x in mdc_normal(i)),1)

    def test_mds_weighted_pose_and_global_bone_indices(self):
        m=read_mds(mds())
        for a,b in zip(m['surfaces'][0]['vertices'][:3],(10.25,20.75,30)): self.assertAlmostEqual(a,b)
        self.assertEqual(m['tags']['tag_head'][:3],(10,22,30))
        self.assertEqual(m['surfaces'][0]['normals'][:3],[0,0,1])
        self.assertEqual(m['frame_count'],1)

    def test_malformed_models(self):
        for reader,raw in ((read_mdc,mdc()),(read_mds,mds())):
            for short in (raw[:10],raw[:-1]):
                with self.assertRaises(ValueError): reader(short)
            with self.assertRaises(ValueError): reader(raw,999)
        raw=bytearray(mdc());struct.pack_into('<i',raw,108,len(raw)+4)
        with self.assertRaises(ValueError): read_mdc(raw)
        raw=bytearray(mds());struct.pack_into('<i',raw,120+76+64,1)
        with self.assertRaisesRegex(ValueError,'Cyclic'): read_mds(raw)
        raw=bytearray(mds());struct.pack_into('<f',raw,120+40,float('nan'))
        with self.assertRaisesRegex(ValueError,'Non-finite'): read_mds(raw)
        raw=bytearray(mdc());start=struct.unpack_from('<i',raw,104)[0];struct.pack_into('<i',raw,start+124,9)
        with self.assertRaisesRegex(ValueError,'triangle'): read_mdc(raw)

    def test_pk3_sections_precedence_bounds_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'models').mkdir()
            (root/'models/x').write_bytes(b'loose')
            for pak,value in [('pak0.pk3',b'base'),('sp_pak1.pk3',b'sp1'),('sp_pak4.pk3',b'sp4'),('mp_pak0.pk3',b'mp')]:
                pk3(root/pak,[('models/x',value),('models/shared',value)])
            sp,mp=Game(root),Game(root,'mp')
            self.assertEqual(sp.read('models/x'),b'sp4')
            self.assertEqual(mp.read('models/x'),b'loose')
            self.assertEqual(mp.read('models/shared'),b'mp')
            self.assertNotIn('sp_pak4.pk3',mp.search_order)
            self.assertIn('pak0.pk3',mp.search_order)
            with self.assertRaises(ValueError): sp.read('../x')
            pk3(root/'sp_pak5.pk3',[('../bad.mdc',b'x'),('models/big',b'12345')])
            g=Game(root);self.assertTrue(g.issues)
            from tools.import_rtcw import read_asset
            with self.assertRaises(ValueError): read_asset(root/'models/x',max_bytes=1)

    def test_script_pose_and_attachment_failures_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));folder='models/players/test'
            pk3(root/'sp_pak1.pk3',[
                (folder+'/wolfanim.cfg',b'STARTANIMS\nalert_idle 9 1 1 15 0\nrest 0 1 1 15 0\nENDANIMS'),
                (folder+'/wolfanim.script',b'ANIMATIONS\nSTATE RELAXED { idle { weapons none { both rest } default { both alert_idle } } }'),
                (folder+'/head_default.skin',b'md3_part,head.md3\nmd3_hat,acc/missing.md3\npart,models/test/diffuse')])
            game=Game(root)
            frame,reason=idle_pose(game,folder)
            self.assertEqual(frame,0)
            self.assertIn('both rest',reason)
            parts,meta=character_parts(game,folder,folder+'/body_default.skin',folder+'/head_default.skin')
            self.assertEqual(len(parts),2)
            self.assertTrue(any('missing.md3' in w for w in meta['warnings']))

    def test_skin_shader_precedence_and_q3_defaults(self):
        s=read_skin(b'// comment\nmd3_hat,"acc/hat.md3"\npart,models/test/diffuse\ntag_head,\nplayerscale,1.5')
        self.assertEqual(s['models'],{'md3_hat':'acc/hat.md3'})
        self.assertEqual(s['scale'],1.5)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pk3(root/'pak0.pk3',[('scripts/base.shader',b'x\n{\nimplicitMask low\n}\nx\n{\nimplicitMap ignored\n}')])
            pk3(root/'sp_pak1.pk3',[('scripts/new.shader',b'x\n{\nimplicitMap high\n}\ny\n{\nimplicitBlend -\n}')])
            shaders=shader_maps(Game(root))
            self.assertEqual(shaders['x']['target'],'low')
            self.assertEqual(shaders['x']['settings']['alphaFunc'],'GE128')
            self.assertEqual(shaders['y']['target'],'y')
            self.assertFalse(shaders['y']['settings']['depthWrite'])
            self.assertEqual(q3.shader_maps({'x.shader':{'read':lambda:b'x\n{\nimplicitMask low\n}'}}),{})

    def test_assembly_import_api_exports_and_preserved_edits(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));out=root/'out'
            game=Game(root)
            parts,meta=character_parts(game,'models/players/test','models/players/test/body_default.skin','models/players/test/head_default.skin')
            self.assertEqual(len(parts),3)
            self.assertEqual(len(parts[2]['tags']),2)
            self.assertEqual(meta['frame'],0)
            with patch('app.pack_dirs',{'rtcw':out}):
                client=app.test_client()
                response=client.post('/import_rtcw',json={'path':str(root)})
                self.assertEqual(response.status_code,200,response.json)
                report=response.json
                self.assertEqual(report['skipped'],1)
                self.assertGreaterEqual(report['duplicates'],1)
                self.assertEqual(report['skin_resolution']['unresolved'],1)
                self.assertGreater(report['excluded'],0)
                catalog=client.get('/list_models?game=rtcw').json
                hero=next(r for r in catalog if r['category']=='Single player: Characters')
                name=hero['model_name']
                data=load_model_data(out/'model_json'/(name+'.json'))
                self.assertEqual(len(data['groups']),3)
                self.assertEqual(data['metadata']['scale'],2)
                for actual,expected in zip(data['vertices'][9:12],(44,60,20)): self.assertAlmostEqual(actual,expected)
                for actual,expected in zip(data['vertices'][18:21],(46,66,16)): self.assertAlmostEqual(actual,expected)
                self.assertEqual(client.get('/model_json/'+name+'?game=rtcw').status_code,200)
                self.assertTrue(client.get('/list_textures?game=rtcw').json)
                obj=client.get('/export_obj/'+name+'?game=rtcw')
                self.assertEqual(obj.status_code,200)
                with zipfile.ZipFile(io.BytesIO(obj.data)) as z: self.assertTrue(any(n.endswith('.obj') for n in z.namelist()))
                glb=client.get('/export_glb/'+name+'?game=rtcw')
                self.assertEqual(glb.status_code,200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'],'static')
                length=struct.unpack_from('<I',glb.data,12)[0]
                doc=json.loads(glb.data[20:20+length])
                self.assertNotIn('animations',doc)
                self.assertEqual(doc['materials'][0]['alphaMode'],'MASK')
                self.assertTrue(doc['materials'][0]['doubleSided'])
                self.assertEqual(client.post('/import_rtcw',json={'path':''}).status_code,400)
            texture=out/'textures'/data['material_textures'][0]
            Image.new('RGBA',(2,2),'red').save(texture)
            before=texture.read_bytes();import_catalog(root,out)
            self.assertEqual(texture.read_bytes(),before)

    def test_unreadable_skins_and_images_have_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp))
            pk3(root/'sp_pak1.pk3',[
                ('models/test/bad.skin',b'not a skin'),
                ('models/test/broken.skin',b'part,models/test/broken'),
                ('models/test/broken.tga',b'not an image')])
            report=import_catalog(root,root/'out')
            self.assertEqual(report['skin_file_status']['unreadable'],1)
            self.assertTrue(any('Cannot decode models/test/broken.tga' in r for r in report['unresolved_skin_reasons']))

    def test_empty_and_absent_head_skin_preserve_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));folder='models/players/test'
            pk3(root/'sp_pak1.pk3',[(folder+'/head_default.skin',b'tag_head,models/ignored.tga')])
            game=Game(root)
            self.assertTrue(read_skin(game.read(folder+'/head_default.skin'))['empty'])
            for head in (folder+'/head_default.skin',folder+'/absent.skin'):
                parts,meta=character_parts(game,folder,folder+'/body_default.skin',head)
                self.assertEqual(len(parts),2)
                self.assertEqual(parts[1]['skin'],{})
                self.assertEqual(parts[1]['model']['surfaces'][0]['shaders'],['models/test/diffuse'])
                self.assertTrue(any('embedded-shader fallback' in w for w in meta['warnings']))
            # Retail Protosoldier's head is tag-only: this must also keep the body.
            raw=bytearray(mdc());struct.pack_into('<i',raw,84,0)
            pk3(root/'sp_pak2.pk3',[(folder+'/head.mdc',raw)])
            report=import_catalog(root,root/'out')
            hero=next(r for r in report['results'] if r['section']=='sp' and r['variant']=='default')
            self.assertEqual(hero['status'],'ready')
            self.assertEqual(report['skin_file_status']['empty'],1)

    def test_multiplayer_shared_models_require_resolved_mp_difference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # Changing an MP image supplies a real difference even with identical geometry.
            pk3(root/'pak0.pk3',[('models/shared.mdc',mdc()),('models/test/diffuse.tga',image_bytes())])
            out=root/'out';first=import_catalog(root,out)
            self.assertEqual([r['status'] for r in first['results']],['ready','excluded'])
            image=io.BytesIO();Image.new('RGBA',(2,2),'yellow').save(image,'TGA')
            pk3(root/'mp_pak0.pk3',[('models/test/diffuse.tga',image.getvalue()),('models/mp_only.mdc',mdc())])
            second=import_catalog(root,root/'changed')
            shared=next(r for r in second['results'] if r['section']=='mp' and r['source']=='models/shared.mdc')
            self.assertIn(shared['status'],('ready','duplicate'))
            self.assertIn('MP-specific',shared['membership'])
            # An MP shader which selects a missing image must not promote the shared model.
            pk3(root/'mp_pak1.pk3',[('scripts/test.shader',b'models/test/diffuse { { map absent.tga } }')])
            third=import_catalog(root,root/'missing')
            shared=next(r for r in third['results'] if r['section']=='mp' and r['source']=='models/shared.mdc')
            self.assertEqual(shared['status'],'excluded')
            own=next(r for r in third['results'] if r['section']=='mp' and r['source']=='models/mp_only.mdc')
            self.assertEqual(own['status'],'ready')  # MP-owned assets remain catalogued, with explicit missing texture.

    def test_later_diffuse_stage_over_opaque_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script=b'''models/hero
{
 { map textures/effect.tga
   tcMod scroll 1 0
 }
 { map models/base.tga
   blendFunc blend
   rgbGen lightingDiffuse
 }
}
models/missingbase
{
 { map textures/effect.tga }
 { map models/absent.tga
   rgbGen lightingDiffuse
 }
}
'''
            pk3(root/'pak0.pk3',[('scripts/test.shader',script),('textures/effect.tga',image_bytes()),('models/base.tga',image_bytes())])
            game=Game(root);textures=Textures(game,root/'out')
            entry=textures.shaders['models/hero']
            self.assertEqual(entry['target'],'models/base.tga')
            self.assertEqual(entry['static_stage']['index'],1)
            filename,warnings,settings=textures.resolve('models/hero')
            self.assertEqual(settings['blend'],[])  # No see-through holes where the opaque effect used to be.
            self.assertTrue(settings['depthWrite'])
            self.assertTrue(any('diffuse-stage approximation' in w for w in warnings))
            self.assertTrue((root/'out'/filename).is_file())
            self.assertEqual(textures.shaders['models/missingbase']['target'],'textures/effect.tga')
            self.assertIn('diffuse stage image absent',textures.shaders['models/missingbase']['limitations'][-1])
            self.assertEqual(q3.shader_maps(game.assets)['models/hero']['target'],'textures/effect.tga')

    def test_any_extension_backslashes_and_section_shader_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script=b'alias\n{\n { map /MODELS\\TEST\\BASE.TGA }\n}\nghost\n{\n { map models/test/base.tga }\n}'
            pk3(root/'pak0.pk3',[('scripts/unique.shader',script),('models/test/base.jpg',image_bytes())])
            pk3(root/'sp_pak1.pk3',[('models/test/sp_only.jpg',image_bytes())])
            pk3(root/'mp_pak0.pk3',[('scripts/unique.shader',b'other\n{\n { map $whiteimage }\n}')])
            sp=Textures(Game(root),root/'sp');mp=Textures(Game(root,'mp'),root/'mp')
            for shader in ('models/test/base.psd',r'MODELS\TEST\BASE.TGA','/models/test/base',r'\models\test\base.tga','alias','ghost'):
                png,warning,_=sp.resolve(shader)
                self.assertTrue((root/'sp'/png).is_file(),warning)
            for shader in ('ghost','models/test/sp_only.tga','models/no/such_file.tga','/alias','//models/test/base','/../models/test/base'):
                mp.resolve(shader)
                self.assertEqual(mp.results[shader]['status'],'unresolved')
            # Existing Q3's unknown-extension lookup policy remains unchanged.
            original=q3.Textures(Game(root).assets,root/'q3')
            png,warning,_=original.resolve('models/test/base.psd')
            self.assertFalse((root/'q3'/png).exists())
            self.assertTrue(any('Missing texture' in w for w in warning))
            png,warning,_=original.resolve('/models/test/base.jpg')
            self.assertFalse((root/'q3'/png).exists())


@unittest.skipUnless(os.environ.get('RTCW_INSTALL_TESTS')=='1' and (INSTALL/'Main/pak0.pk3').is_file(),
                     'Optional RTCW single-file probes; set RTCW_INSTALL_TESTS=1')
class InstallTests(unittest.TestCase):
    def test_bj_body_and_head(self):
        with zipfile.ZipFile(INSTALL/'Main/pak0.pk3') as z:
            body=read_model(z.read('models/players/bj2/body.mds'),173)
            head=read_model(z.read('models/players/bj2/head.mdc'))
        self.assertIn('tag_head',body['tags'])
        self.assertIn('tag_mouth',head['tags'])


if __name__ == '__main__': unittest.main()
