import io,json,tempfile,threading,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from PIL import Image
import app

class DeliveryAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.multiple(app,DELIVERABLES=self.root);self.patch.start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.patch.stop();self.temp.cleanup()
    def test_download_verifies_actual_bytes_and_sets_filename(self):
        image=Image.new('RGB',(24,16),'#224433');image.putpixel((3,4),(255,220,10));image.save(self.root/'contact-sheet.png')
        with urlopen(self.url+'/downloads/contact-sheet.png') as response:
            self.assertEqual(response.read(),(self.root/'contact-sheet.png').read_bytes())
            self.assertEqual(response.headers['X-Golden-City-File-Validation'],'passed')
            self.assertEqual(response.headers['X-Golden-City-Recipient-Access'],'not-verified')
            self.assertIn('contact-sheet.png',response.headers['Content-Disposition'])
        # A changed or truncated file must not reuse its former passing evidence.
        (self.root/'contact-sheet.png').write_bytes(b'not a PNG')
        with self.assertRaises(HTTPError) as error:urlopen(self.url+'/downloads/contact-sheet.png')
        self.assertEqual(error.exception.code,422)
        self.assertIn('המסירה נחסמה',json.loads(error.exception.read())['error'])
    def test_blank_and_missing_deliveries_fail_closed(self):
        Image.new('RGB',(24,16),'white').save(self.root/'blank.png')
        with self.assertRaises(HTTPError) as error:urlopen(self.url+'/downloads/blank.png')
        self.assertEqual(error.exception.code,422)
        with self.assertRaises(HTTPError) as error:urlopen(self.url+'/downloads/missing.png')
        self.assertEqual(error.exception.code,404)
        with self.assertRaises(HTTPError) as error:urlopen(self.url+'/downloads/../app.py')
        self.assertEqual(error.exception.code,404)
