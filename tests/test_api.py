import base64, copy, io, json, tempfile, threading, unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
import app
from test_geometry import fixture

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patch=patch.multiple(app,DATA=self.root,ASSETS=self.root/'assets',OUTPUT=self.root/'renders')
        self.patch.start();app.ASSETS.mkdir();app.OUTPUT.mkdir()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.patch.stop();self.temp.cleanup()
    def call(self,path,data=None):
        req=Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=15) as response:
            body=response.read()
            return json.loads(body) if 'application/json' in response.headers['Content-Type'] else body
    def test_persistent_edit_camera_restore(self):
        p=self.call('/api/import',fixture())
        self.assertFalse(p['approved'])
        with self.assertRaises(HTTPError):self.call('/api/export',{'id':p['id']})
        p=self.call('/api/approve',{'id':p['id']})
        p=self.call('/api/camera',{'id':p['id'],'name':'saved','camera':{'position':[100,-100,80],'target':[0,0,10],'fov':48}})
        saved=copy.deepcopy(p)
        p=self.call('/api/edit',{'id':p['id'],'building':'A','material':'brick','color':'#aa4433'})
        p=self.call('/api/restore',{'id':p['id'],'version':saved['version']})
        self.assertEqual(p['cameras'],saved['cameras'])
        self.assertEqual(p['buildings'],saved['buildings'])
        self.assertEqual(self.call('/api/projects')[0],p)
        self.assertIn(b'v ',self.call('/api/export',{'id':p['id']}))
    def test_photo_and_context_model_ingest(self):
        p=self.call('/api/import',fixture()); buffer=io.BytesIO();Image.new('RGB',(8,8),'green').save(buffer,format='PNG')
        p=self.call('/api/assets',{'id':p['id'],'kind':'aerial_photo','name':'source.png','content_base64':base64.b64encode(buffer.getvalue()).decode(),'placement':{'bounds':[220000,630000,220100,630100]}})
        asset=p['assets'][0]
        self.assertEqual(self.call(asset['url']),buffer.getvalue())
        self.assertFalse(asset['georeferencing_verified'])
        obj=b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        p=self.call('/api/assets',{'id':p['id'],'kind':'model','name':'context.obj','content_base64':base64.b64encode(obj).decode()})
        self.assertEqual(len(p['assets']),2)
        self.assertEqual(self.call(p['assets'][1]['url']),obj)

    def test_jpeg_source_preserved_byte_for_byte(self):
        p=self.call('/api/import',fixture())
        buffer=io.BytesIO();Image.new('RGB',(16,12),'brown').save(buffer,format='JPEG')
        p=self.call('/api/assets',{'id':p['id'],'kind':'site_photo','name':'source.jpg','content_base64':base64.b64encode(buffer.getvalue()).decode()})
        self.assertEqual(p['assets'][0]['mime'],'image/jpeg')
        self.assertEqual(self.call(p['assets'][0]['url']),buffer.getvalue())

    def test_boundary_and_control_alignment_preserve_camera_and_assets(self):
        from pyproj import Transformer
        p=self.call('/api/import',fixture())
        p=self.call('/api/camera',{'id':p['id'],'name':'saved','camera':{'position':[100,-100,80],'target':[0,0,10],'fov':48}})
        camera=copy.deepcopy(p['cameras'])
        p=self.call('/api/approve',{'id':p['id']})
        converter=Transformer.from_crs('EPSG:2039','EPSG:4326',always_xy=True)
        g=copy.deepcopy(p['boundary']);g['coordinates']=[[list(converter.transform(*point)) for point in g['coordinates'][0]]]
        p=self.call('/api/boundary',{'id':p['id'],'geojson':g,'source_crs':'EPSG:4326','source':{'provider':'Synthetic test','reference':'fixture','acquired_at':'2026-10-12','source_type':'synthetic'}})
        self.assertEqual(p['cameras'],camera)
        self.assertFalse(p['approved'])
        self.assertFalse(p['boundary_provenance']['report']['survey_accuracy_verified'])
        source=fixture()['buildings'];source[0]['type']='Feature'
        for ring in source[0]['geometry']['coordinates']:
            for point in ring:point[0]-=220000;point[1]-=630000
        p=self.call('/api/align',{'id':p['id'],'buildings':source,'control_points':[{'plan_xy':[0,0],'target_xy':[220000,630000]},{'plan_xy':[100,0],'target_xy':[220100,630000]},{'plan_xy':[0,100],'target_xy':[220000,630100]}]})
        self.assertLess(p['alignment']['max_residual_m'],1e-6)
        self.assertEqual(p['cameras'],camera)
        self.assertEqual(p['source_plan'],source)
        self.assertEqual(p['errors'],[])
        self.assertFalse(p['approved'])
    def test_invalid_asset_and_traversal(self):
        p=self.call('/api/import',fixture())
        with self.assertRaises(HTTPError):self.call('/api/assets',{'id':p['id'],'kind':'aerial_photo','content_base64':base64.b64encode(b'fake').decode()})
        with self.assertRaises(HTTPError):self.call('/assets/../app.py')

    def test_static_json_sample_is_served_as_valid_json(self):
        sample=self.call('/examples/renders/baseline-project.json')
        self.assertEqual(sample['crs'],'EPSG:2039')
        self.assertEqual(len(sample['buildings']),3)
