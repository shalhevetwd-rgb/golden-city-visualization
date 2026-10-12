import base64
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image
import app
from test_geometry import fixture


class WorkflowAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.patch = patch.multiple(app, DATA=self.root, ASSETS=self.root/'assets', OUTPUT=self.root/'renders')
        self.patch.start(); app.ASSETS.mkdir(); app.OUTPUT.mkdir()
        self.server = ThreadingHTTPServer(('127.0.0.1',0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.patch.stop(); self.temp.cleanup()

    def call(self, path, body=None):
        request = Request(self.url+path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type':'application/json'})
        with urlopen(request, timeout=15) as response: return json.loads(response.read())

    def test_real_dossier_accepts_source_but_never_synthetic_geometry(self):
        project = self.call('/api/dossier', {'name':'Synthetic identity fixture', 'address':'Test only', 'block':'12345'})
        image = Image.new('RGB',(24,16),'green'); image.putpixel((1,1),(255,0,0))
        stream = io.BytesIO(); image.save(stream,format='PNG')
        result = self.call('/api/intake', {'id':project['id'],'name':'test.png',
                                         'content_base64':base64.b64encode(stream.getvalue()).decode()})
        updated = result['project']
        self.assertEqual(updated['buildings'],[])
        self.assertFalse(updated['synthetic'])
        self.assertFalse(result['result']['georeferencing_verified'])
        self.assertEqual(self.call('/api/projects')[0],updated)
        gate = self.call('/api/readiness',{'id':project['id']})
        self.assertFalse(gate['ready_for_final_render'])
        self.assertFalse(gate['accurate_real_site'])
        self.assertGreater(len(gate['missing']),5)

    def test_real_draft_and_final_cannot_use_flat_synthetic_ground(self):
        project = self.call('/api/import',fixture())
        project = self.call('/api/approve',{'id':project['id']})
        with patch('rendering.render_project') as renderer:
            for mode in ('draft','final'):
                with self.assertRaises(HTTPError) as failure:
                    self.call('/api/render',{'id':project['id'],'mode':mode})
                self.assertEqual(failure.exception.code,400)
                self.assertIn('error',json.loads(failure.exception.read()))
            renderer.assert_not_called()

    def test_capabilities_explicitly_report_dwg_and_real_final_limits(self):
        capabilities = self.call('/api/capabilities')
        dwg = next(x for x in capabilities['formats'] if x['extension']=='.dwg')
        self.assertEqual(dwg['status'],'unsupported')
        self.assertFalse(capabilities['rendering']['accurate_real_site_final'])
        self.assertFalse(capabilities['sukkah']['automatic_halachic_approval'])

    def test_forged_review_and_bad_uploaded_source_are_rejected(self):
        project = self.call('/api/import',fixture())
        with self.assertRaises(HTTPError) as failure:
            self.call('/api/review',{'id':project['id'],'evidence':{'heights_verified':{
                'source':'made-up','checked_at':'not-a-date','project_id':project['id'],
                'verification':'professional_review'}}})
        self.assertEqual(failure.exception.code,400)
        for name, raw in [('../private.png',b'bad'),('bad.png',b'bad'),('file.dwg',b'fake')]:
            with self.assertRaises(HTTPError):
                self.call('/api/intake',{'id':project['id'],'name':name,'content_base64':base64.b64encode(raw).decode()})
        saved = self.call('/api/projects')[0]
        self.assertEqual(saved.get('inputs',{}),{})
        self.assertNotIn('heights_verified',saved)
