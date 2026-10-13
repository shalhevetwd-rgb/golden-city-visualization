import hashlib,json,tempfile,unittest
from pathlib import Path
from scripts.package_site_v4 import require_fresh_receipts

class DeliveryFreshness(unittest.TestCase):
    def fixture(self,d):
        names=[s+ext for s in ('existing','cleared','planted') for ext in ('.json','.blend','.glb')]
        for n in names:(d/n).write_bytes(b'fixture source bytes '+n.encode())
        (d/'viewer.html').write_text('<html>owned fixture</html>')
        (d/'browser-report.json').write_text(json.dumps({'status':'passed','viewer_sha256':hashlib.sha256((d/'viewer.html').read_bytes()).hexdigest()}))
        report={'status':'passed','source_sha256':{n:hashlib.sha256((d/n).read_bytes()).hexdigest() for n in names}}
        (d/'roundtrip-report.json').write_text(json.dumps(report));return report
    def test_stale_model_or_scene_cannot_be_packaged(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);self.fixture(d);require_fresh_receipts(d)
            (d/'planted.glb').write_bytes(b'changed after import')
            with self.assertRaises(ValueError):require_fresh_receipts(d)
            self.fixture(d);(d/'cleared.json').write_bytes(b'changed terrain')
            with self.assertRaises(ValueError):require_fresh_receipts(d)
    def test_old_viewer_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);self.fixture(d);(d/'viewer.html').write_text('<html>different controls</html>')
            with self.assertRaises(ValueError):require_fresh_receipts(d)
    def test_incomplete_self_declared_import_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);r=self.fixture(d);r['source_sha256'].pop('existing.blend');(d/'roundtrip-report.json').write_text(json.dumps(r))
            with self.assertRaises(ValueError):require_fresh_receipts(d)
