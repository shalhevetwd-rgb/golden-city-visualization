import base64
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import validate
from workflow import dossier, ensure_geometry_record, geometry_payload, intake, readiness
from test_geometry import fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = validate(fixture())
        self.project['id'] = 'a' * 32

    def tearDown(self):
        self.temp.cleanup()

    def test_dossier_never_inherits_demo_geometry_or_synthetic_flag(self):
        project = dossier({'name':'test site','address':'synthetic test address',
                           'block':'12345','synthetic':True})
        self.assertIsNone(project['boundary'])
        self.assertEqual(project['buildings'], [])
        self.assertFalse(project['synthetic'])
        self.assertEqual(project['identity']['status'], 'user_provided_not_verified')

    def test_review_binding_ignores_color_but_detects_floor_and_location_changes(self):
        ensure_geometry_record(self.project, self.root)
        original = self.project['geometry_version_sha256']
        self.project['buildings'][0]['properties']['color'] = '#112233'
        self.project['cameras']['aerial'] = {'position':[1,2,3],'target':[0,0,0]}
        ensure_geometry_record(self.project, self.root)
        self.assertEqual(self.project['geometry_version_sha256'], original)
        self.project['buildings'][0]['properties']['floors'] += 1
        ensure_geometry_record(self.project, self.root)
        self.assertNotEqual(self.project['geometry_version_sha256'], original)
        self.assertEqual(len(list((self.root/'geometry').glob('*.json'))), 2)

    def test_saved_geometry_corruption_cannot_reuse_a_review(self):
        ensure_geometry_record(self.project, self.root)
        (self.root/self.project['geometry_manifest_path']).write_text('{}')
        with self.assertRaises(ValueError): ensure_geometry_record(self.project, self.root)

    def test_uploaded_image_reopened_and_never_automatically_georeferenced(self):
        image = Image.new('RGB', (20,12), '#234567'); image.putpixel((1,1), (255,0,0))
        stream = io.BytesIO(); image.save(stream, format='PNG')
        updated, result = intake(self.project, {'name':'source.png','content_base64':base64.b64encode(stream.getvalue()).decode()}, self.root)
        source = updated['inputs'][result['source_id']]
        self.assertEqual((self.root/source['path']).read_bytes(), stream.getvalue())
        self.assertFalse(result['georeferencing_verified'])
        self.assertEqual(updated['buildings'], self.project['buildings'])
        self.assertEqual(updated['version'], 2)

    def test_path_traversal_and_invalid_source_leave_no_accepted_records(self):
        for name, raw in [('../secret.png', b'bad'), ('source.png', b'bad')]:
            with self.assertRaises(ValueError):
                intake(self.project, {'name':name,'content_base64':base64.b64encode(raw).decode()}, self.root)
        self.assertEqual(self.project.get('inputs', {}), {})
        self.assertEqual(list((self.root/'assets').glob('*')) if (self.root/'assets').exists() else [], [])

    def test_real_site_final_remains_blocked_by_actual_renderer_limit(self):
        self.project['approved'] = True
        with patch('engine.constraints.site_gate',return_value={'missing':[], 'evidence_complete':True, 'final_render_allowed':True}):
            result = readiness(self.project, self.root)
        self.assertFalse(result['ready_for_final_render'])
        self.assertFalse(result['accurate_real_site'])
        self.assertFalse(result['certification'])
        self.assertTrue(any('טופוגרפיה' in v for v in result['missing']))

    def test_synthetic_final_requires_valid_geometry_and_explicit_user_confirmation(self):
        self.project['synthetic'] = True
        with patch('engine.constraints.site_gate',return_value={'missing':[], 'evidence_complete':False}):
            self.assertFalse(readiness(self.project, self.root)['ready_for_final_render'])
            self.project['approved'] = True
            self.assertTrue(readiness(self.project, self.root)['ready_for_final_render'])
            self.project['errors'] = ['outside boundary']
            self.assertFalse(readiness(self.project, self.root)['ready_for_final_render'])
