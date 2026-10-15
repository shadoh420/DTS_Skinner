"""Small Doom PROC/material/light/entity fixtures; no retail install needed."""
import io
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from tools import import_doom3_map as maps
from tools.doom3_assets import Install, Declarations, Textures, Materials, lex, path_name
from tools.doom3_map_materials import expression, compile_stage, compile_material, tables
from tools.model_data import load_model_data
from tests.test_doom3 import MESH, ANIM


def proc():
    surface='''{ "textures/test" 3 3
      ( 0 0 0 0 0 0 0 1 ) ( 4 0 0 1 0 0 0 1 ) ( 0 4 0 0 1 0 0 1 ) 0 2 1 }'''
    return ('''mapProcFile003
    model { "_area0" 1 '''+surface+''' }
    model { "Door" 1 '''+surface+''' }
    interAreaPortals { 2 1 4 0 1 ( 0 0 0 ) ( 0 4 0 ) ( 0 4 4 ) ( 0 0 4 ) }
    nodes { 1 ( 1 0 0 0 ) -1 -2 }
    shadowModel { "ignored" 0 0 0 0 0 0 }
    ''').encode()


def fixture(root):
    (root/'base').mkdir(parents=True);(root/'d3xp').mkdir()
    material=b'''textures/test { bumpmap _flat diffusemap _white specularmap _white }
    lights/defaultpointlight { lightFalloffImage _white { map _white colored } }
    lights/defaultprojectedlight { lightFalloffImage _noFalloff { map _white colored } }
    lights/fog { fogLight { map _white } }
    textures/common/trigger { nodraw }
    table pulse { clamp { 0, 1, 0 } }
    '''
    entities=b'''Version 2
    { "classname" "worldspawn" { brushDef3 { ( 1 0 0 0 ) } } }
    { "classname" "func_door" "name" "door" "model" "door" "origin" "10 0 0" "movedir" "90" "angle" "90" }
    { "classname" "light" "name" "lamp" "origin" "4 0 4" "light_radius" "100 100 100" "_color" "0.2 0.4 0.6" "falloff" "99" }
    { "classname" "light" "name" "fog" "texture" "lights/fog" }
    { "classname" "monster_test" "name" "hidden" "origin" "20 0 0" "hide" "1" }
    { "classname" "info_player_start" "origin" "1 1 1" "angle" "90" }
    '''
    defs=b'''entityDef parent { "model" "testmodel" "anim" "idle" }
    entityDef monster_test { "inherit" "parent" }
    model testmodel { mesh models/test.md5mesh anim idle models/idle.md5anim }
    '''
    with zipfile.ZipFile(root/'base/pak000.pk4','w') as z:
        for n,b in [('maps/game/test.proc',proc()),('maps/game/test.map',entities),('materials/test.mtr',material),('def/test.def',defs),('models/test.md5mesh',MESH),('models/idle.md5anim',ANIM)]:z.writestr(n,b)
    with zipfile.ZipFile(root/'d3xp/pak000.pk4','w') as z:
        z.writestr('maps/game/test.proc',proc());z.writestr('maps/game/test.map',entities)


class DoomMapTest(unittest.TestCase):
    def test_proc_surfaces_portals_nodes_and_shadow_skip(self):
        p=maps.read_proc(proc());self.assertEqual(p['areas'],2);self.assertEqual(p['shadowModels'],1)
        self.assertEqual(p['models']['_area0'][0]['indices'].tolist(),[0,1,2])
        self.assertEqual(p['portals'][0]['areas'],[0,1]);self.assertEqual(maps.area_for_point([-1,0,0],p['nodes']),1)
        self.assertEqual(maps.bounds_areas([[-1,0,0],[1,1,1]],p['nodes']),[0,1])
        with self.assertRaises(ValueError):maps.read_proc(proc().replace(b'0 2 1 }',b'0 2 9 }'))

    def test_map_ignores_brushes_and_comments(self):
        e=maps.read_entities(b'Version 2\n { "classname" "worldspawn" { patchDef2 { "ignore" "this" } } } // {}\n { "origin" "1 2 3" }')
        self.assertEqual(e,[{'classname':'worldspawn'},{'origin':'1 2 3'}])

    def test_point_projection_center_and_falloff_ignored(self):
        e={'origin':'10 20 30','light_radius':'2 4 8','light_center':'1 0 0','angle':'90'}
        light=maps.light_projection(e);np.testing.assert_allclose(light['origin'],[10,21,30])
        np.testing.assert_allclose(np.array(light['projection'])@[10,20,30,1],[.5,.5,1,.5])
        self.assertEqual(light,maps.light_projection(dict(e,falloff='999')))
        self.assertTrue(maps.light_intersects([[10,20,30],[11,21,31]],light))
        self.assertFalse(maps.light_intersects([[100,100,100],[101,101,101]],light))

    def test_projected_planes_and_override_origin(self):
        light=maps.light_projection({'origin':'99 99 99','light_origin':'0 0 0','light_target':'0 0 -10','light_up':'0 2 0','light_right':'2 0 0','light_start':'0 0 -2','light_end':'0 0 -10'})
        p=np.array(light['projection'])
        np.testing.assert_allclose(p@[0,0,-6,1],[3,3,6,.5])
        self.assertTrue(maps.light_intersects([[-.1,-.1,-6],[.1,.1,-5]],light))
        self.assertFalse(maps.light_intersects([[0,0,1],[1,1,2]],light))

    def test_expression_and_stage_boundaries(self):
        class Images:
            def resolve(self,v):return dict(expression=v,status='ready',texture=v)
        s,n=compile_stage(lex('blend diffusemap map _white rgb Parm0 * pulse[ time * 2 ] scroll time, 0.5 rotate time / 4 alphaTest .5 inverseVertexColor'),Images())
        self.assertEqual(n,[]);self.assertEqual(s['role'],'diffuse');self.assertEqual(s['vertexColor'],-1)
        self.assertEqual(s['color'][0],['*','parm0',['table','pulse',['*','time',2.0]]])
        self.assertEqual(s['uv'],[['scroll','time',.5],['rotate',['/','time',4.0]]])
        with self.assertRaises(ValueError):expression('__import__("os")')
        sound,n=compile_stage(lex('map _white red sound * Parm0 green sound * Parm1 blue sound * Parm2'),Images())
        self.assertEqual(n,[])
        self.assertEqual(sound['color'],[['*','sound','parm0'],['*','sound','parm1'],['*','sound','parm2'],1])

    def test_sound_reactive_light_exports_original_sample_and_keeps_color(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with zipfile.ZipFile(root/'install/base/pak001.pk4','w') as z:
                z.writestr('materials/sound.mtr','lights/reactive { { map _white red sound*parm0 green sound*parm1 blue sound*parm2 } }')
                z.writestr('sound/test.sndshd','hum { looping volume -30 sound/lights/test.wav }')
                sample=b'fixture ogg bytes: copied verbatim, browser decodes real audio'
                z.writestr('sound/lights/test.ogg',sample)
                with zipfile.ZipFile(root/'install/base/pak000.pk4') as original:
                    z.writestr('maps/game/test.map',original.read('maps/game/test.map')+b'''
                    { "classname" "light" "name" "soundlight" "texture" "lights/reactive" "s_shader" "hum" "_color" ".84 .75 .67" }
                    { "classname" "light" "name" "silent" "texture" "lights/reactive" }
                    { "classname" "light" "name" "triggered" "texture" "lights/reactive" "s_shader" "hum" "s_waitfortrigger" "1" }''')
            maps.import_maps(root/'install',root/'data',maps=['base:game/test'])
            s=json.loads((root/'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            lights={l['name']:l for l in s['lights']};source=lights['soundlight']['sound']
            self.assertEqual(lights['soundlight']['parms'][:3],[.84,.75,.67])
            self.assertTrue(s['materials']['lights/reactive']['soundReactive'])
            self.assertEqual(s['materials']['lights/reactive']['approximations'],[])
            self.assertTrue(source['looping']);self.assertEqual(source['source'],'sound/lights/test.ogg')
            self.assertEqual((root/'data/doom3-maps'/source['file']).read_bytes(),sample)
            self.assertIsNone(lights['silent']['sound']);self.assertTrue(lights['triggered']['sound']['waitForTrigger'])
            self.assertEqual(len(list((root/'data/doom3-maps/sounds').iterdir())),1)

    def test_mask_stages_and_spectrum_filter_both_directions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with zipfile.ZipFile(root/'install/base/pak001.pk4','w') as z:
                z.writestr('materials/spectrum.mtr','''textures/spectral { spectrum 2 diffusemap _white
                    { map _white maskColor } { blend gl_dst_alpha, gl_one map _white maskAlpha } }
                    lights/spectral { spectrum 2 { map _white colored } }''')
                z.writestr('maps/game/test.proc',proc().replace(b'"textures/test"',b'"textures/spectral"',1))
                with zipfile.ZipFile(root/'install/base/pak000.pk4') as original:
                    z.writestr('maps/game/test.map',original.read('maps/game/test.map')+b'''
                    { "classname" "light" "name" "spectrum" "texture" "lights/spectral" "origin" "4 0 4" }''')
            maps.import_maps(root/'install',root/'data',maps=['base:game/test'])
            s=json.loads((root/'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            m=s['materials']['textures/spectral']
            self.assertEqual(m['spectrum'],2);self.assertEqual(m['approximations'],[])
            self.assertTrue(m['stages'][0]['maskcolor']);self.assertTrue(m['stages'][1]['maskalpha'])
            for surface in s['surfaces']:
                self.assertTrue(surface['lights'])
                expected=2 if surface['material']=='textures/spectral' else 0
                self.assertEqual({s['lights'][i]['spectrum'] for i in surface['lights']},{expected})

    def test_literal_startup_shader_parameters_before_yield(self):
        source='''void event() { sys.setShaderParm(0, 9); }
        void setup() { $mover.rotate ('0 120 0'); $flare.setColor (0, 0, 0);
            sys.setShaderParm(0, 1); sys.setShaderParm(1, .5); $flare.setShaderParm(7, 1); }
        void main() { setup(); sys.wait(5); sys.setShaderParm(0, 99); }'''
        globals_,entities=maps.startup_shader_parms(source)
        self.assertEqual(globals_,[1,.5]+[0]*10)
        self.assertEqual(entities,{'flare':{'_color':'0 0 0','shaderparm7':'1'}})
        for stop in ('if (1) { event(); }','thread event()','while(1) { event(); }','unknown()','main()'):
            self.assertEqual(maps.startup_shader_parms('void main() { '+stop+'; sys.setShaderParm(0,9); }')[0],[0]*12)

    def test_import_transforms_idle_hidden_campaigns_and_shared_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install');dest=root/'data'
            report=maps.import_maps(root/'install',dest)
            self.assertEqual(report['ready'],2)
            for campaign in ('base','d3xp'):
                directory=dest/'doom3-maps/maps'/(campaign+'_game_test');s=load_model_data(directory/'scene.json')
                fog=next(l for l in s['lights'] if l['name']=='fog');self.assertEqual(s['stats']['skippedLights'],0)
                self.assertTrue(fog['fog']);self.assertFalse(fog['blend']);self.assertEqual(len(fog['volume']),12*9,'twelve triangles of the fog box')
                self.assertEqual(s['stats']['hiddenEntitiesShown'],1);self.assertEqual(s['lightScale'],2)
                door=next(x for x in s['surfaces'] if x['entity']=='door')
                np.testing.assert_allclose(door['bounds'],[[6,0,0],[10,4,0]],atol=1e-6)
                monster=next(x for x in s['surfaces'] if x['entity']=='hidden');self.assertEqual(monster['bounds'][0][2],4)
                self.assertEqual(s['lights'][0]['parms'][:3],[.2,.4,.6]);self.assertEqual(monster['lights'],[0,1],'the lamp and the fog volume')
                self.assertFalse((directory/'textures').exists())
                self.assertEqual(s['globalShaderParms'],[0]*12)
                raw=json.loads((directory/'scene.json').read_text());self.assertEqual(raw['geometry_buffers']['arrays']['vertices']['dtype'],'<f4')
                self.assertEqual([x['role'] for x in s['materials']['textures/test']['stages']],['bump','diffuse','specular'])
            self.assertTrue((dest/'doom3/textures/_white.png').is_file())
            stale=dest/'doom3-maps/maps/base_game_test'/('0'*64+'.bin');stale.write_bytes(b'old')
            maps.import_maps(root/'install',dest,maps=['base:game/test'],replace=True)
            self.assertFalse(stale.exists());load_model_data(stale.parent/'scene.json')
            with Install(root/'install') as archive:
                self.assertEqual(tables(archive.files['base'])['pulse']['values'],[0,1,0])

    def test_startup_parms_reach_imported_lights_and_surfaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with zipfile.ZipFile(root/'install/base/pak001.pk4','w') as z:
                z.writestr('maps/game/test.script','''void setup() { sys.setShaderParm(2,1);
                    $lamp.setColor(0,0,0); $door.setShaderParm(7,1); }
                    void main() { setup(); sys.wait(5); $lamp.setColor(1,1,1); }''')
            maps.import_maps(root/'install',root/'data',maps=['base:game/test'])
            s=json.loads((root/'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            self.assertEqual(s['globalShaderParms'][2],1)
            self.assertEqual(s['lights'][0]['parms'][:3],[0,0,0])
            self.assertEqual(next(x for x in s['surfaces'] if x['entity']=='door')['parms'][7],1)

    def test_builtin_falloff_and_path_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            t=Textures({},Path(temp));image=t.source_image('_quadratic')
            self.assertEqual(image.size,(32,4));self.assertEqual(image.getpixel((15,0)),(255,255,255,255))
            self.assertEqual(image.getpixel((0,0)),(0,0,0,255));self.assertEqual(t.source_image('_noFalloff').getpixel((0,0)),(255,255,255,255))
        self.assertEqual(path_name('Models\\\\Characters//Test'),'models/characters/test')

    def test_language_table(self):
        lang=type('Asset',(),{'read':lambda self:b'{\n\t"#str_04352"\t"Administration Lobby"\n}'})()
        self.assertEqual(maps.strings({'strings/english.lang':lang}),{'#str_04352':'Administration Lobby'})
        self.assertEqual(maps.strings({}),{})

    def test_door_angle_is_direction_and_start_open(self):
        parts=maps.read_proc(proc())['models']['door']
        origin,axis=maps.entity_transform({'classname':'func_door','angle':'90','start_open':'1','lip':'1'},parts)
        np.testing.assert_allclose(axis,np.eye(3));np.testing.assert_allclose(origin,[0,3,0],atol=1e-8)

    def test_implicit_material_stages_coverage_and_light_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with Install(root/'install') as archive:
                d=Declarations(archive.files['base']);t=Textures(archive.files['base'],root/'textures');m=Materials(d,t)
                d.materials['textures/implicit']=lex('{ blend add map _white }')
                self.assertTrue(compile_material('textures/implicit',d,m,t)['translucent'])
                d.materials['textures/bump']=lex('bumpmap _flat')
                self.assertEqual([s['role'] for s in compile_material('textures/bump',d,m,t)['stages']],['bump','diffuse'])
                d.materials['lights/ambient']=lex('ambientLight lightFalloffImage _nofalloff { map _white colored }')
                self.assertTrue(compile_material('lights/ambient',d,m,t)['ambientLight'])
                d.materials['lights/blend']=lex('blendLight { map _white }')
                self.assertTrue(compile_material('lights/blend',d,m,t)['blendLight'])

    def test_doom3_sky_decal_macro_sort_and_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with Install(root/'install') as archive:
                d=Declarations(archive.files['base']);t=Textures(archive.files['base'],root/'textures');m=Materials(d,t)
                d.materials['textures/skies/hellsky']=lex('''forceOpaque { blend add cameraCubeMap env/hellsky
                    texgen skybox
                    texgen wobblesky 1.5 .5 .2
                    red .75//Parm0 * table[ time ] }
                    }''')
                sky=compile_material('textures/skies/hellsky',d,m,t)
                self.assertFalse(sky['translucent']);self.assertEqual(sky['sort'],0)
                stage=sky['stages'][0];self.assertEqual(stage['texgen'],'wobblesky');self.assertEqual(stage['wobble'],[1.5,.5,.2])
                self.assertEqual([f['expression'] for f in stage['cube']['faces']][:2],['env/hellsky_forward','env/hellsky_back'])
                self.assertEqual(stage['color'][0],.75,'// starts a comment inside a token')
                d.materials['textures/decals/scorch']=lex('DECAL_MACRO translucent { blend filter map _white }')
                decal=compile_material('textures/decals/scorch',d,m,t)
                self.assertEqual(decal['sort'],2);self.assertTrue(decal['stages'][0]['polygonOffset'])
                d.materials['textures/glass']=lex('{ blend blend map _white }')
                self.assertEqual(compile_material('textures/glass',d,m,t)['sort'],4,'translucent defaults to SS_MEDIUM')
                d.materials['textures/near']=lex('sort nearest { blend add map _white }')
                self.assertEqual(compile_material('textures/near',d,m,t)['sort'],7)
                d.materials['textures/reflect']=lex('bumpmap _flat { blend add cubeMap env/gen1 texgen reflect }')
                reflect=compile_material('textures/reflect',d,m,t)
                self.assertEqual([s['role'] for s in reflect['stages']],['ambient','bump'],
                                 'AddImplicitStages adds no white diffuse beside a reflection stage')
                self.assertEqual(reflect['stages'][0]['texgen'],'reflect');self.assertEqual(len(reflect['approximations']),6,'six fixture-missing faces')
                self.assertEqual([f['expression'] for f in reflect['stages'][0]['cube']['faces']],['env/gen1_'+f for f in ('px','nx','py','ny','pz','nz')])
                d.materials['textures/washroom/mirror']=lex('discrete { blend gl_dst_color,gl_zero map _white } solid mirror')
                mirror=compile_material('textures/washroom/mirror',d,m,t)
                self.assertTrue(mirror['mirror']);self.assertEqual(mirror['sort'],-3);self.assertFalse(mirror['translucent'])

    def test_expression_suffixes_keyword_tables_registers_and_shear(self):
        class Images:
            def resolve(self,v):return dict(expression=v,status='ready',texture=v)
        q4=type('Images',(Images,),{'policy':type('P',(),{'programs':set()})()})()
        self.assertEqual(expression('(1.0f - time)'),['-',1.0,'time'])
        s,n=compile_stage(lex('map _white rgb linear [Parm6 - time]'),Images())
        self.assertEqual(n,[]);self.assertEqual(s['color'][0],['table','linear',['-','parm6','time']])
        s,n=compile_stage(lex('rgba 0\nremoteRenderMap 512 256\nmap textures/dynamic/camera1.tga'),Images())
        self.assertEqual(s['color'],[0.0]*4);self.assertEqual(n,['remoterendermap'],'its own keyword, not trailing rgba tokens')
        s,n=compile_stage(lex('map _white rgba decalFade[ DecalLife ] if IsMultiplayer != 1 shear a[time], 0'),q4)
        self.assertEqual(n,[]);self.assertEqual(s['condition'],['!=','ismultiplayer',1.0])
        self.assertEqual(s['uv'],[['shear',['table','a','time'],0.0]])
        with self.assertRaises(ValueError):expression('IsMultiplayer')

    def test_deform_lines(self):
        from tools.doom3_map_materials import parse_deform
        self.assertEqual(parse_deform(lex('translucent\ndeform flare ( 16 + parm4 )\n{ map _quadratic }')),
                         dict(kind='flare',values=[['+',16.0,'parm4']]))
        self.assertEqual(parse_deform(lex('deform turbulent sinTable .0295 time * .2 1\n')),
                         dict(kind='turbulent',table='sintable',values=[.0295,['*','time',.2],1.0]))
        self.assertEqual(parse_deform(lex('deform sprite\nsort nearest')),dict(kind='sprite',values=[]))
        self.assertIsNone(parse_deform(lex('deform eyeBall')))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with Install(root/'install') as archive:
                d=Declarations(archive.files['base']);t=Textures(archive.files['base'],root/'textures');m=Materials(d,t)
                d.materials['textures/sfx/flare']=lex('translucent\ndeform flare 16\n{ blend add map _quadratic colored vertexcolor }')
                flare=compile_material('textures/sfx/flare',d,m,t)
                self.assertEqual(flare['deform'],dict(kind='flare',values=[16.0]));self.assertEqual(flare['approximations'],[])
                d.materials['textures/eye']=lex('deform eyeBall\n{ blend filter map _white }')
                self.assertEqual(compile_material('textures/eye',d,m,t)['approximations'],['deform eyeball'])

    def test_roe_portal_sky_entity_and_areas(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'install')
            with zipfile.ZipFile(root/'install/d3xp/pak001.pk4','w') as z:
                z.writestr('materials/smf.mtr','textures/smf/portal_sky { forceOpaque sort portalSky { map _currentRender clamp screen } }')
                z.writestr('def/smf.def','entityDef info_portalSky { "spawnclass" "idPortalSky" "hide" "1" }')
                z.writestr('maps/game/test.proc',proc().replace(b'"textures/test"',b'"textures/smf/portal_sky"',1))
                with zipfile.ZipFile(root/'install/d3xp/pak000.pk4') as original:
                    z.writestr('maps/game/test.map',original.read('maps/game/test.map')+b'''
                    { "classname" "info_portalSky" "name" "later" "origin" "9 9 9" "triggered" "1" }
                    { "classname" "info_portalSky" "name" "sky" "origin" "500 0 64" }''')
            maps.import_maps(root/'install',root/'data',maps=['d3xp:game/test'])
            s=json.loads((root/'data/doom3-maps/maps/d3xp_game_test/scene.json').read_text())
            self.assertEqual(s['portalSky'],dict(name='sky',origin=[500,0,64]))
            m=s['materials']['textures/smf/portal_sky'];self.assertTrue(m['portalSky']);self.assertEqual(m['approximations'],[])
            self.assertEqual(s['areaSky'],{'_area0':True})

    @unittest.skipUnless(shutil.which('node'),'Node is needed for browser math tests')
    def test_browser_math(self):
        script=Path(__file__).with_suffix('.js')
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_routes_import_validation_and_texture_root(self):
        import app
        with tempfile.TemporaryDirectory() as temp, patch.object(app,'local_data_dir',Path(temp)):
            client=app.app.test_client();response=client.get('/maps/doom3/')
            self.assertEqual(response.status_code,200);self.assertIn(b'benchmark',response.data)
            self.assertEqual(client.post('/import_doom3_maps',json={}).status_code,400)
            self.assertEqual(client.post('/import_doom3_maps',json={'path':'x'},headers={'Origin':'https://foreign.invalid'}).status_code,403)
            with patch('tools.import_doom3_map.import_maps',return_value={'ready':1}) as importer:
                self.assertEqual(client.post('/import_doom3_maps',json={'path':'game'}).status_code,200)
                importer.assert_called_once_with('game',Path(temp),replace=False)
            self.assertFalse(app.import_lock.locked())
            self.assertIn('/maps/doom3/',Path('templates/index.html').read_text(encoding='utf8'))

if __name__=='__main__':unittest.main()
