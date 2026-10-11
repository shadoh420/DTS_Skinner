"""Synthetic fixtures only; retail smoke check is explicitly opt-in."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tools import import_azrael as az
from tools import import_azrael_map as maps
from tests.test_azrael import res, xaf, ani, INSTALL


def string(value, size):
    raw = value.encode('ascii')
    return raw + b'\0' + b'\xcc' * (size-len(raw)-1)


def zones(rows):
    return struct.pack('<H',len(rows)) + b''.join(string(name,60)+string(obj,12)+b'\0\1\0' for name,obj in rows)


def rooms(rows):
    return struct.pack('<H',len(rows)) + b''.join(string(name,60)+string(source,60)+struct.pack('<H',len(names))+
        b''.join(string(n,60) for n in names)+b'\0\0\0\0' for name,source,names in rows)


def room_xaf(animated=True):
    return ('''material "paint" {texture "shared#.gif" colour {0,0,0,1}}
material "solid" {colour {.2,.3,.4,.5} specular {2,3}}
material "hidden" {colour {1,1,1,0}}
object "room" {matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 10,0,20,1}
 mesh {vertices{-100,0,-100, -100,0,100, 100,0,100, 100,0,-100}
 texture_coordinates{0,0, 0,1, 1,1, 1,0} normals{0,1,0}
 faces{3,0,0,1,0,2,0, 3,0,0,2,0,3,0, 0} material "paint"}
 object "zxentry" {matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 0,1,0,1}
 mesh {vertices{0,0,0, 1,0,0, -1,0,0} faces{3,0,0,1,0,2,0,0} material "paint"}}
 object "zyOther" {matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 40,1,0,1}
 mesh {vertices{0,0,0, 1,0,0, -1,0,0} faces{3,0,0,1,0,2,0,0} material "hidden"}}
 object "door" {matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 500,0,0,1}
 mesh {vertices{0,0,0, 0,10,0, 0,0,10} faces{3,0,0,1,0,2,0,0} material "solid"}}
 object "dummy" {mesh {vertices{0,100,0, 1,100,0, 0,100,1}
 faces{3,0,0,1,0,2,0, 3,0,0,1,0,2,0, 0} face_materials{"hidden",""}}}}
''' + ('''animation_set {animation "door" {object_name "door" position_keys{0,0,0,0, 10,0,10,0}}}''' if animated else '')).encode('ascii')


def fixture(root):
    game=root/'GAME'/'AZRAEL'; game.mkdir(parents=True)
    textures=root/'TEXTURES'; textures.mkdir()
    (game/'ROOMS.DAT').write_bytes(rooms([('First','room',['R1Entry']),('Second','room',['OtherToFirst']),
        ('FirstAlias','room',['R1Entry']),('Blank','',[]),('Missing','absent',[])]))
    (game/'AZONES.DAT').write_bytes(zones([('R1Entry','zxentry'),('OtherToFirst','zxentry')]))
    (game/'HERO.ANI').write_bytes(ani())
    for name,data in [('room',room_xaf()),('extra',room_xaf(False)),('hero',xaf()),
                      ('camera',b'object "dummy" {}'),('sailor_a',xaf(False)),('empty',b'')]:
        (textures/(name+'.xaf')).write_bytes(data)
        bundle=bytearray(res())
        if name=='extra':bundle[64:67]=bytes((20,40,60))
        (textures/(name+'.res')).write_bytes(bundle)
    return game,textures


class AzraelMapsTest(unittest.TestCase):
    def test_identical_material_opt_in_keeps_existing_strict_default(self):
        material = b'material "rope" {texture "rope.gif" colour {0,0,0,1}}'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            az.parse_xaf(material + material)
        scene = az.parse_xaf(material + material, allow_identical_materials=True)
        self.assertEqual(scene['textures'], ['rope.gif'])
        self.assertEqual(len(scene['materials']), 1)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            az.parse_xaf(material + material.replace(b'0,0,0,1', b'1,1,1,1'), allow_identical_materials=True)

    def test_zones_exact_layout_padding_and_bounds(self):
        raw=zones([('R1Entry','zxentry'),('RoomToNext','zxnext')])
        result=maps.read_zones(raw)
        self.assertEqual(result[0],dict(name='R1Entry',object='zxentry',tail=[0,1,0]))
        for bad in (b'',raw[:-1],raw+b'x',b'\x01\0'+b'x'*75):
            with self.assertRaises(ValueError):maps.read_zones(bad)

    def test_room_pose_hidden_faces_entry_floor_and_colour(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(Path(tmp)); cd,entries,zone_rows,_=maps.plan_maps(tmp)
            scene,blob,images=maps.build_map(cd,entries[0],zone_rows)
            self.assertEqual(scene['pose']['frame'],0)
            self.assertEqual(scene['pose']['objects'],['door'])
            self.assertEqual(scene['vertices'],9)
            self.assertEqual(len(blob),scene['vertices']*20+scene['indices']*4)
            self.assertEqual(scene['hidden']['faces_by_rule'],{'zx trigger':1,'zy arrival':1,'alpha-zero':1,'material-less':1})
            view=scene['viewpoints'][0]
            self.assertEqual(view['source'],'entry-zone')
            self.assertEqual(view['zone'],'R1Entry')
            self.assertEqual(view['native_origin'],[10,45,20])
            self.assertEqual(view['origin'],[10,45,-20])
            self.assertEqual(view['yaw'],-180)
            self.assertEqual(scene['camera']['near'],1)
            self.assertEqual(scene['camera']['far'],30000)
            self.assertAlmostEqual(scene['camera']['fov'],77.3196165)
            vertices=np.frombuffer(blob,'<f4',scene['vertices']*3).reshape(-1,3)
            self.assertLess(vertices[:,0].max(),500)  # keyed door replaces parked position
            self.assertTrue(scene['groups'][0]['masked'])
            solid=next(g for g in scene['groups'] if g['material']=='solid')
            self.assertEqual(images[solid['texture']].getpixel((0,0)),(51,76,102,128))
            self.assertTrue(solid['transparent'])
            self.assertEqual(solid['alpha'],1)  # swatch alpha is not multiplied twice
            self.assertEqual(scene['characters'],[])

    def test_marker_centroid_and_arrival_selection_not_exit(self):
        room=dict(zones=['EntryCoordSpaRoom'])
        points=[dict(name='zxSpa',path=[0],position=[100,0,0],forward=[0,0,1]),
                dict(name='zyFar',path=[1],position=[-100,0,0],forward=[0,0,1]),
                dict(name='zyNear',path=[2],position=[90,0,0],forward=[1,0,0])]
        candidates=maps.entry_candidates(room,[dict(name='EntryCoordSpaRoom',object='zxSpa')],points)
        self.assertEqual([p['name'] for p in candidates],['zyNear','zyFar'])
        scene=az.parse_xaf(room_xaf())
        scene['objects'][0]['children'][0]['meshes'][0]['vertices']=[[4,0,0],[4,0,0],[4,0,0]]
        marker=maps.markers(scene,az.evaluate_world_matrices(scene,None))[0]
        self.assertEqual(marker['position'],[14,1,20])

    def test_fallback_view_and_animation_failure(self):
        scene=az.parse_xaf(room_xaf())
        scene['animations'][0]['object_name']='absent'
        worlds,pose=maps.room_pose(scene)
        self.assertEqual(pose['status'],'stored-fallback')
        self.assertIn('absent',pose['reason'])
        # A larger ceiling must not win the fallback over the floor.
        triangles=np.array([[[0,0,0],[10,0,0],[0,0,10]],
                            [[0,100,0],[100,100,0],[0,100,100]]],dtype=float)
        views=maps.viewpoints(dict(zones=[]),[],[],triangles)
        self.assertEqual(views[0]['source'],'fallback')
        self.assertEqual(views[0]['floor_y'],0)
        self.assertEqual(views[0]['origin'][1],45)
        point=dict(name='zyFloor',path=[0],position=[1,-5,1],forward=[0,-1,1e-7])
        views=maps.viewpoints(dict(zones=[]),[],[point],triangles)
        self.assertEqual(views[0]['floor_y'],0)
        self.assertEqual(views[0]['origin'][1],45)
        self.assertEqual(views[0]['pitch'],0)
        self.assertIn('No horizontal marker facing', views[0]['facing'])

    def test_catalog_every_xaf_aliases_distinct_entries_and_bundle_palettes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); fixture(root)
            cd,entries,_,sources=maps.plan_maps(root)
            self.assertEqual(len(sources),6)
            self.assertEqual(entries[2]['status'],'alias')
            self.assertEqual(entries[0]['aliases'],['FirstAlias'])
            self.assertEqual(entries[1]['status'],'pending')  # same XAF, different zone data
            output=root/'out'; report=maps.import_maps(root,output)
            self.assertEqual(len(report['imported']),3)
            self.assertEqual(len(report['failed']),5)
            self.assertTrue(all(e['status']!='pending' for e in report['results']))
            self.assertEqual({r['source'] for r in report['xaf_results']},{'textures/'+n+'.xaf' for n in ('room','extra','hero','camera','sailor_a','empty')})
            index=json.loads((output/'index.json').read_text())
            self.assertEqual([r['group'] for r in index],['Rooms']*4+['Extras'])
            scenes=[json.loads((output/'maps'/i/'scene.json').read_text()) for i in report['imported']]
            names=[s['textures'][0]['png'] for s in scenes]
            self.assertEqual(len(set(names)),3)  # even identical source scene has entry-owned textures
            from PIL import Image
            with Image.open(output/'textures'/names[0]) as a, Image.open(output/'textures'/names[-1]) as b:
                self.assertNotEqual(a.getpixel((0,0)),b.getpixel((0,0)))
            cached=maps.import_maps(root,output)
            self.assertEqual(len(cached['skipped']),3)
            self.assertTrue(all(e['status']!='pending' for e in cached['results']))

    def test_routes_import_and_foreign_request_guard(self):
        import app as server
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); fixture(root)
            with patch.object(server,'local_data_dir',root/'output'):
                client=server.app.test_client()
                self.assertEqual(client.get('/maps/azrael/').status_code,200)
                self.assertIn(b'/maps/azrael/',client.get('/').data)
                response=client.post('/import_azrael_maps',json={'path':str(root)})
                self.assertEqual(response.status_code,200,response.json)
                self.assertEqual(client.get('/azrael-map-data/index.json').status_code,200)
                ident=response.json['imported'][0]
                self.assertEqual(client.get(f'/azrael-map-data/maps/{ident}/geometry.bin').status_code,200)
                self.assertEqual(client.get('/azrael-map-data/../../app.py').status_code,404)
                for payload in ({},{'path':[]},{'path':str(root),'game':'sin'}):
                    self.assertEqual(client.post('/import_azrael_maps',json=payload).status_code,400)
                self.assertEqual(client.post('/import_azrael_maps',json={'path':str(root)},headers={'Origin':'https://foreign.invalid'}).status_code,403)
                with patch.object(server,'import_azrael_maps',side_effect=ValueError('bad install')):
                    self.assertEqual(client.post('/import_azrael_maps',json={'path':str(root)}).status_code,400)
                self.assertFalse(server.import_lock.locked())

    def test_failed_room_keeps_reason_and_other_entries_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); _,textures=fixture(root)
            (textures/'room.res').unlink()
            report=maps.import_maps(root,root/'out')
            self.assertIn('Missing RES bundle',report['failed']['room-001-first'])
            self.assertIn('extra-extra',report['imported'])
            row=next(r for r in report['xaf_results'] if r['source']=='textures/room.xaf')
            self.assertEqual(row['results'][0]['status'],'failed')


@unittest.skipUnless(os.environ.get('AZRAEL_INSTALL_TESTS')=='1' and INSTALL.is_dir(), 'Optional bounded install smoke check')
class AzraelMapInstallTest(unittest.TestCase):
    def test_metadata_and_one_room_no_import(self):
        cd,entries,zones,sources=maps.plan_maps(INSTALL)
        self.assertEqual(sum(e['section']=='Rooms' for e in entries),81)
        self.assertEqual(len(sources),179)
        self.assertEqual(len(zones),319)
        scene=az.parse_xaf(cd.read('textures/coricori.xaf'))
        self.assertTrue(maps.markers(scene,az.evaluate_world_matrices(scene,None)))


if __name__=='__main__': unittest.main()
