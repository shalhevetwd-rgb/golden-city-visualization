"""Cross-module regressions for persistence and coordinate contracts.

These checks deliberately avoid launching Blender: rendering itself is covered
by test_rendering, while these failures happen before a scene reaches it.
"""
import base64
import copy
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
from balconies import analyze_balcony
from rendering import resolve_camera, validate_camera
from state import check_camera, commit, edit_building, restore
from test_geometry import fixture, polygon


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.project = app.validate(fixture())
        self.project['id'] = 'b' * 32

    def test_restore_recovers_assets_source_approval_and_stable_ids(self):
        saved = copy.deepcopy(self.project)
        saved.update(approved=True,
                     assets=[{'id': 'context-1', 'url': '/assets/context-1.obj',
                              'placement': {'bounds': [220000, 630000, 220100, 630100]}}],
                     boundary_provenance={'source': {'reference': 'survey-a'},
                                          'report': {'survey_accuracy_verified': False}})
        saved['cameras']['review'] = check_camera({'position': [80, -100, 45], 'target': [0, 0, 8], 'fov': 110})
        saved = commit(self.project, saved, 'source-and-camera')
        changed = copy.deepcopy(saved)
        changed['assets'].append({'id': 'context-2', 'url': '/assets/context-2.obj'})
        changed['cameras']['review']['position'][0] = 120
        changed['boundary_provenance']['source']['reference'] = 'survey-b'
        changed['approved'] = False
        changed = commit(saved, changed, 'replace-context')
        changed = edit_building(changed, {'building': 'A', 'color': '#aabbcc'})
        changed['renders'].append({'url': '/renders/version-4.png', 'version': changed['version']})
        untouched = copy.deepcopy(changed)

        recovered = restore(changed, saved['version'])
        self.assertEqual(changed, untouched, 'Restoring must not mutate the live input')
        for field in ('id', 'assets', 'cameras', 'boundary_provenance', 'approved', 'buildings'):
            self.assertEqual(recovered[field], saved[field], field)
        self.assertEqual(recovered['renders'], changed['renders'], 'Historical render artifacts remain accessible')
        self.assertEqual(recovered['version'], changed['version'] + 1)
        self.assertEqual(recovered['history'][-1]['snapshot']['assets'], changed['assets'])
        recovered['assets'][0]['placement']['bounds'][0] = 0
        self.assertEqual(saved['assets'][0]['placement']['bounds'][0], 220000,
                         'Restored mutable values must be isolated from saved snapshots')

    def test_export_and_camera_use_one_xyz_z_up_convention(self):
        project = copy.deepcopy(self.project)
        project['approved'] = True
        project['buildings'][0]['properties']['base_elevation'] = 7.5
        vertices = [[float(v) for v in line.split()[1:]]
                    for line in app.model_obj(project).splitlines() if line.startswith('v ')]
        self.assertEqual(vertices[0], [-40.0, -40.0, 7.5])
        self.assertEqual({v[2] for v in vertices}, {7.5, 37.5})
        self.assertEqual({v[1] for v in vertices}, {-40.0, -20.0})
        for fov in (10, 100, 110, 120):
            camera = {'position': [45, -90, 1.7], 'target': [0, 0, 12], 'fov': fov}
            saved = check_camera(camera)
            self.assertEqual(saved, validate_camera(saved))
            project['cameras']['street-review'] = saved
            self.assertEqual(resolve_camera(project, 'street-review'), saved)

    def test_balcony_input_ids_and_coordinates_match_analysis_contract(self):
        footprint = polygon(220005, 630007, 4)
        for invalid_id in (None, 0, True, ''):
            request = {'building': 'A', 'balconies': [
                {'id': invalid_id, 'footprint': footprint, 'elevation': 3}]}
            with self.subTest(id=invalid_id), self.assertRaises(ValueError):
                edit_building(self.project, request)
        three_dimensional = copy.deepcopy(footprint)
        for coordinate in three_dimensional['coordinates'][0]:
            coordinate.append(3)
        with self.assertRaises(ValueError):
            edit_building(self.project, {'building': 'A', 'balconies': [
                {'id': 'deck', 'footprint': three_dimensional, 'elevation': 3}]})

    def test_explicit_imported_overhead_geometry_is_not_silently_lost(self):
        document = fixture()
        footprint = polygon(220003, 630003, 4)
        document['buildings'][0]['properties']['balconies'] = [
            {'id': 'deck', 'footprint': footprint, 'elevation': 3, 'thickness': .25}]
        document['overhead_obstructions'] = [
            {'id': 'awning', 'footprint': footprint, 'elevation': 6, 'thickness': .2}]
        project = app.validate(document)
        self.assertEqual(project.get('overhead_obstructions'), document['overhead_obstructions'])
        analysis = analyze_balcony(project, 'A', 'deck', overhead_complete=True)
        self.assertEqual(analysis['projected_obstruction_area_m2'], 16)
        self.assertEqual(analysis['open_sky_area_m2'], 0)
        self.assertFalse(analysis['certification']['halachic'])

    def test_omitted_balcony_thickness_matches_rendering_default(self):
        document = fixture()
        document['buildings'][0]['properties']['balconies'] = [
            {'id': 'deck', 'footprint': polygon(220003, 630003, 4),
             'elevation': 3, 'railing_height': 0}]
        project = app.validate(document)
        balcony = project['buildings'][0]['properties']['balconies'][0]
        render_thickness = balcony.get('thickness', .25)
        analysis = analyze_balcony(project, 'A', 'deck')
        self.assertAlmostEqual(analysis['section']['balcony_bottom_m'],
                               balcony['elevation'] - render_thickness)
        self.assertEqual(balcony['railing_height'], 0)

    def test_holed_overhead_source_survives_import_and_version_restore(self):
        document = fixture()
        deck = polygon(220003, 630003, 4)
        awning = copy.deepcopy(deck)
        awning['coordinates'].append(polygon(220004, 630004, 2)['coordinates'][0])
        document['buildings'][0]['properties']['balconies'] = [
            {'id': 'deck', 'footprint': deck, 'elevation': 3}]
        document['overhead_obstructions'] = [
            {'id': 'open-awning', 'footprint': awning, 'elevation': 6, 'thickness': .2}]
        project = app.validate(document)
        project['id'] = 'b' * 32
        changed = edit_building(project, {'building': 'A', 'color': '#aabbcc'})
        recovered = restore(changed, project['version'])
        self.assertEqual(recovered['overhead_obstructions'], project['overhead_obstructions'])
        report = analyze_balcony(recovered, 'A', 'deck', overhead_complete=True)
        self.assertEqual(report['projected_obstruction_area_m2'], 12)
        self.assertEqual(report['open_sky_area_m2'], 4)
        self.assertEqual(len(report['top_view']['blocked']['coordinates']), 2)


class ReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.patches = patch.multiple(app, DATA=self.directory,
                                      ASSETS=self.directory / 'assets',
                                      OUTPUT=self.directory / 'renders')
        self.patches.start()
        app.ASSETS.mkdir()
        app.OUTPUT.mkdir()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.patches.stop()
        self.temp.cleanup()

    def post(self, path, document):
        request = Request(self.base + path, data=json.dumps(document).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_malformed_geojson_returns_400_without_creating_projects(self):
        for geometry in ({'type': 'Polygon', 'coordinates': []},
                         {'type': 'Polygon', 'coordinates': [[]]},
                         {'type': 'Unknown', 'coordinates': []}):
            document = fixture()
            document['boundary'] = geometry
            with self.subTest(geometry=geometry), self.assertRaises(HTTPError) as error:
                self.post('/api/import', document)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(list(self.directory.glob('*.json')), [])
        imported = self.post('/api/import', fixture())
        self.assertEqual(imported['errors'], [], 'Server remains usable after invalid imports')

    def test_sky_completeness_requires_explicit_boolean_at_http_boundary(self):
        document = fixture()
        document['buildings'][0]['properties']['balconies'] = [
            {'id': 'deck', 'footprint': polygon(220003, 630003, 4), 'elevation': 3}]
        project = self.post('/api/import', document)
        with self.assertRaises(HTTPError) as error:
            self.post('/api/balconies', {'id': project['id'], 'building': 'A',
                                         'overhead_complete': 'false'})
        self.assertEqual(error.exception.code, 400)
        incomplete = self.post('/api/balconies', {'id': project['id'], 'building': 'A'})[0]
        self.assertIsNone(incomplete['open_sky_area_m2'])
        self.assertEqual(incomplete['status'], 'incomplete_scene')

    def test_restored_asset_references_keep_original_bytes_and_identity(self):
        project = self.post('/api/import', fixture())
        first_bytes = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        project = self.post('/api/assets', {'id': project['id'], 'kind': 'model',
                                            'content_base64': base64.b64encode(first_bytes).decode()})
        saved = copy.deepcopy(project)
        project = self.post('/api/camera', {'id': project['id'], 'name': 'later',
                                           'camera': {'position': [40, -80, 30], 'target': [0, 0, 10]}})
        second_bytes = b'v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n'
        project = self.post('/api/assets', {'id': project['id'], 'kind': 'model',
                                            'content_base64': base64.b64encode(second_bytes).decode()})
        later_asset = project['assets'][-1]
        recovered = self.post('/api/restore', {'id': project['id'], 'version': saved['version']})
        self.assertEqual(recovered['id'], saved['id'])
        self.assertEqual(recovered['assets'], saved['assets'])
        self.assertEqual(recovered['cameras'], saved['cameras'])
        for asset, expected in ((recovered['assets'][0], first_bytes), (later_asset, second_bytes)):
            with urlopen(self.base + asset['url'], timeout=5) as response:
                self.assertEqual(response.read(), expected)
        persisted = json.loads((self.directory / (recovered['id'] + '.json')).read_text())
        self.assertEqual(persisted, recovered)


if __name__ == '__main__':
    unittest.main()
