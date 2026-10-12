"""Two-cluster fixtures for IBSP46, IBSP47 and RBSP1; no retail data."""
import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.bsp_visibility import read_visibility
from tools import import_q3_map as q3, import_rtcw_map as rtcw, import_et_map as et
from tools import import_jk2_map as jk, import_ja_map as ja
from tests.test_rtcw_maps import bsp,entity
from tests.test_jk2_maps import fixture,raven
from tests.test_q3_maps import pk3,image


def two_clusters(lumps,stride=104):
    lumps=list(lumps)
    plane=len(lumps[2])//16;lumps[2]+=struct.pack('<4f',1,0,0,0)
    lumps[3]=struct.pack('<9i',plane,-1,-2,-100,-100,-100,100,100,100)
    lumps[4]=struct.pack('<12i',0,0,0,-100,-100,100,100,100,0,1,0,0)+struct.pack('<12i',1,1,-100,-100,-100,0,100,100,1,1,0,0)
    lumps[5]=struct.pack('<2i',0,1)
    lumps[13]+=lumps[13][:stride]  # Third surface is an independent inline model.
    lumps[7]=struct.pack('<6f4i',-100,-100,-100,100,100,100,0,2,0,1)+struct.pack('<6f4i',0,0,0,10,10,10,2,1,1,0)
    lumps[16]=struct.pack('<2i',2,1)+bytes([1,3])  # Cluster 0 cannot see 1; 1 sees both.
    return lumps


def ibsp(lumps,version):
    header=b'IBSP'+struct.pack('<i',version);body=b''
    for lump in lumps:header+=struct.pack('<2i',144+len(body),len(lump));body+=lump
    return header+body


class VisibilityTests(unittest.TestCase):
    def test_shared_pages_load_visibility_before_their_viewers(self):
        from app import app
        for game in ('q3','rtcw','et','jk2','ja'):
            with self.subTest(game=game):
                response=app.test_client().get('/maps/'+game+'/')
                self.assertEqual(response.status_code,200)
                page=response.get_data(as_text=True)
                self.assertLess(page.index('/static/q3-maps/visibility.js'),page.index('/static/'+game+'-maps/viewer.js'))

    def test_each_format_and_game_preserves_cluster_rows_and_surface_ordinals(self):
        wolf=two_clusters(q3.read_bsp(bsp(),version=47))
        raven_lumps=two_clusters(fixture(),148)
        cases=[q3.read_bsp(ibsp(wolf,46)),rtcw.read_bsp(ibsp(wolf,47)),et.read_bsp(ibsp(wolf,47)),
               jk.read_bsp(raven(raven_lumps))]
        for lumps in cases:
            tree=read_visibility(lumps)
            self.assertEqual(tree['world'],[0,2]);self.assertEqual(tree['surfaceCount'],3)
            self.assertEqual([l[8:10] for l in tree['leaves']],[[0,0],[1,1]])
            self.assertEqual(tree['surfaces'],[0,1])
            self.assertEqual(tree['planes'][tree['nodes'][0][8]],[1,0,0,0])
            self.assertEqual(base64.b64decode(tree['pvs']['bits']),bytes([1,3]))
        self.assertEqual(read_visibility(raven_lumps,surface_stride=148),read_visibility(cases[-1]))
        self.assertEqual(ja.bsp_cull(raven_lumps,[])['pvs'],read_visibility(cases[-1])['pvs'])

    def test_invalid_visibility_is_reported_and_empty_vis_is_supported(self):
        lumps=two_clusters(q3.read_bsp(bsp(),version=47))
        for raw in (b'bad',struct.pack('<2i',-2,-1)+bytes(2),struct.pack('<2i',9,1)+bytes(9),struct.pack('<2i',2,1)+bytes(1)):
            with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,'PVS'):
                read_visibility(lumps[:16]+[raw])
        bad=list(lumps);bad[4]=bytearray(bad[4]);struct.pack_into('<i',bad[4],0,2)
        with self.assertRaisesRegex(ValueError,'cluster'):read_visibility(bad)
        bad=list(lumps);bad[3]=bytearray(bad[3]);struct.pack_into('<i',bad[3],4,0)
        with self.assertRaisesRegex(ValueError,'cycle'):read_visibility(bad)
        bad=list(lumps);bad[2]=bad[2][:-1]
        with self.assertRaisesRegex(ValueError,'partial'):read_visibility(bad)
        bad=list(lumps);bad[16]=b''
        self.assertEqual(read_visibility(bad)['pvs'],dict(numClusters=0,clusterBytes=0,bits=''))

    def test_synthetic_q3_import_exports_vis_without_changing_draw_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'baseq3').mkdir()
            lumps=two_clusters(q3.read_bsp(bsp(entity(classname='worldspawn')),version=47))
            pk3(root/'baseq3/pak0.pk3',{'maps/tiny.bsp':ibsp(lumps,46),'textures/test/wall.tga':image()})
            report=q3.import_maps(root,root/'out')
            self.assertFalse(report['failed'])
            scene=json.loads((root/'out/maps/tiny/scene.json').read_text())
            self.assertEqual(scene['bspCull'],read_visibility(lumps))
            packed=q3.read_bsp((root/'out'/scene['bsp']).read_bytes())
            self.assertEqual(packed[13],lumps[13]);self.assertEqual(packed[16],b'')


if __name__=='__main__':unittest.main()
