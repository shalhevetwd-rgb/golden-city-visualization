"""Independent regressions for the reusable artifact delivery boundary.

No rendering or model producer is invoked here. The cases exercise real byte
mutations, role changes, link targets, and nested archive destinations.
"""
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
import zipfile

from PIL import Image

import delivery_guard
import app
from delivery_guard import verify_for_delivery
from delivery_validation import ValidationError, validate_archive, validate_file, validate_links
from test_delivery_validation import write_image


class DeliveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        with delivery_guard._LOCK:
            delivery_guard._CACHE.clear()

    def tearDown(self):
        self.temporary.cleanup()
        with delivery_guard._LOCK:
            delivery_guard._CACHE.clear()

    def test_expected_original_hash_cannot_be_replaced_with_changed_valid_bytes(self):
        path = self.root / 'report.json'
        path.write_text('{"version":1}')
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        first = verify_for_delivery(path, expected={'sha256': original_hash})
        self.assertEqual(first['sha256'], original_hash)
        path.write_text('{"version":2}')
        with self.assertRaises(ValidationError):
            verify_for_delivery(path, expected={'sha256': original_hash})
        fresh = verify_for_delivery(path)
        self.assertNotEqual(fresh['sha256'], original_hash)
        self.assertEqual(fresh['user_accessibility'], 'not_verified')

    def test_uniform_source_image_cannot_reuse_cache_as_valid_render_output(self):
        path = self.root / 'photo.png'
        Image.new('RGB', (48, 32), 'green').save(path)
        source = verify_for_delivery(path, artifact_role='source')
        self.assertFalse(source['details']['nonblank'])
        with self.assertRaises(ValidationError):
            verify_for_delivery(path, artifact_role='output')

    def test_changed_corrupt_bytes_do_not_reuse_prior_open_evidence(self):
        path = self.root / 'instructions.txt'
        path.write_bytes(b'Golden City')
        original = verify_for_delivery(path)
        path.write_bytes(b'\xff' + b'olden City')
        self.assertEqual(path.stat().st_size, original['bytes'])
        with self.assertRaises(ValidationError):
            verify_for_delivery(path)

    def test_cache_separates_expected_dimensions_and_returns_isolated_evidence(self):
        path = self.root / 'render.png'
        write_image(path)
        evidence = verify_for_delivery(path, expected={'width': 48, 'height': 32})
        evidence['details']['second_decoder']['decoded'] = False
        evidence['user_accessibility'] = 'recipient_confirmed'
        reopened = verify_for_delivery(path, expected={'width': 48, 'height': 32})
        self.assertTrue(reopened['details']['second_decoder']['decoded'])
        self.assertEqual(reopened['user_accessibility'], 'not_verified')
        with self.assertRaises(ValidationError):
            verify_for_delivery(path, expected={'width': 49, 'height': 32})

    def test_html_link_cannot_pass_merely_because_corrupt_target_exists(self):
        image = self.root / 'render.png'
        image.write_bytes(b'existing but unreadable image')
        html = self.root / 'gallery.html'
        html.write_text('<html><body><img src="render.png"></body></html>')
        with self.assertRaises(ValidationError):
            validate_links(html, root=self.root)
        with self.assertRaises(ValidationError):
            validate_file(html)

    def test_cached_html_cannot_reuse_old_evidence_for_changed_link_target(self):
        image = self.root / 'render.png'
        write_image(image)
        html = self.root / 'gallery.html'
        html.write_text('<html><body><img src="render.png"></body></html>')
        original_html = html.read_bytes()
        first = verify_for_delivery(html)
        self.assertEqual(first['status'], 'passed')
        image.write_bytes(b'the linked image has become unreadable')
        self.assertEqual(html.read_bytes(), original_html, 'Only linked artifact bytes changed')
        with self.assertRaises(ValidationError):
            verify_for_delivery(html)

    def test_nested_archive_gallery_resolves_within_whole_extraction_root(self):
        image = self.root / 'render.png'
        write_image(image)
        html = b'<html><body><img src="../images/render.png"></body></html>'
        members = {'images/render.png': image.read_bytes(), 'pages/gallery.html': html}
        manifest = {'files': [{'name': name, 'bytes': len(payload),
                               'sha256': hashlib.sha256(payload).hexdigest()}
                              for name, payload in sorted(members.items())]}
        archive = self.root / 'delivery.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as packed:
            for name, payload in members.items():
                packed.writestr(name, payload)
            packed.writestr('manifest.json', json.dumps(manifest))
        evidence = validate_archive(archive, expected_manifest=manifest)
        self.assertEqual(evidence['status'], 'passed')
        self.assertEqual({item['name'] for item in evidence['members']},
                         {'manifest.json', 'pages/gallery.html', 'images/render.png'})
        page_evidence = next(item for item in evidence['members'] if item['name'] == 'pages/gallery.html')
        self.assertTrue(page_evidence['details']['local_links'][0]['exists'])
        self.assertEqual(page_evidence['user_accessibility'], 'not_verified')
        self.assertTrue(all('path' not in item for item in evidence['members']))

    def test_download_serves_verified_snapshot_when_file_changes_after_gate(self):
        path = self.root / 'instructions.txt'
        checked_payload = b'Original instructions independently opened before delivery'
        path.write_bytes(checked_payload)

        def verify_then_change(candidate, **kwargs):
            evidence = verify_for_delivery(candidate, **kwargs)
            Path(candidate).write_bytes(b'\xffreplacement bytes never independently opened')
            return evidence

        with patch.object(app, 'DELIVERABLES', self.root), patch.object(app, 'verify_for_delivery', verify_then_change):
            server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/downloads/instructions.txt', timeout=5) as response:
                    self.assertEqual(response.read(), checked_payload)
                    self.assertEqual(response.headers['X-Golden-City-File-Validation'], 'passed')
                    self.assertEqual(response.headers['X-Golden-City-Recipient-Access'], 'not-verified')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
        self.assertNotEqual(path.read_bytes(), checked_payload,
                            'The test really changed the underlying file after validation')


if __name__ == '__main__':
    unittest.main()
