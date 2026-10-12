import copy
import unittest
from sukkah_policy import screen
from test_balconies import fixture, rectangle, slab


def policy():
    return {'min_width_m': 2, 'min_length_m': 3, 'min_height_m': 2,
            'min_wall_count': 3, 'min_wall_height_m': 1,
            'provenance': {'authority': 'Synthetic test policy, not a ruling',
                           'reference': 'fixture only', 'version': 'test-1'}}


def details():
    return {'sukkah_footprint': rectangle(220020, 630005, 4, 5),
            'clear_height_m': 2.5, 'measurements_source': 'synthetic plan fixture',
            'vertical_datum': 'synthetic local Z',
            'walls': {'count': 3, 'height_m': 1.2, 'source': 'synthetic section'},
            'schach': {'material': 'test material description', 'source': 'user test record'}}


class SukkahPolicyTests(unittest.TestCase):
    def test_complete_geometric_candidate_never_receives_halachic_approval(self):
        original = fixture()
        before = copy.deepcopy(original)
        supplied = details()
        supplied['professional_review'] = {'approved': True, 'reviewer': 'unverified user record'}
        result = screen(original, 'A', 'target', policy(), supplied, True)
        self.assertTrue(result['geometric_screen_passed'])
        self.assertFalse(result['halachic_approval'])
        self.assertFalse(result['professional_review_authenticated'])
        self.assertEqual(original, before)
        self.assertEqual(result['measured_candidate']['area_m2'], 20)
        self.assertIn('section', result['analysis'])

    def test_incomplete_overhead_and_schach_are_unknown(self):
        supplied = details(); supplied.pop('schach')
        result = screen(fixture(), 'A', 'target', policy(), supplied)
        self.assertIsNone(result['geometric_screen_passed'])
        self.assertGreaterEqual(len(result['missing']), 2)
        self.assertIsNone(result['analysis']['open_sky_area_m2'])

    def test_stacked_balcony_fails_clear_projection(self):
        project = fixture()
        project['buildings'][0]['properties']['balconies'].append(slab('above', x=220022, width=2, elevation=6))
        result = screen(project, 'A', 'target', policy(), details(), True)
        self.assertFalse(result['geometric_screen_passed'])
        self.assertFalse(result['checks']['candidate_clear_projection'])
        self.assertEqual(result['analysis']['projected_obstruction_area_m2'], 10)

    def test_explicit_clear_subset_can_pass_without_moving_existing_balcony(self):
        project = fixture()
        project['overhead_obstructions'] = [slab('canopy', x=220022, width=2, elevation=7)]
        supplied = details(); supplied['sukkah_footprint'] = rectangle(220020, 630005, 2, 5)
        result = screen(project, 'A', 'target', policy(), supplied, True)
        self.assertTrue(result['geometric_screen_passed'])
        self.assertEqual(result['analysis']['total_area_m2'], 20)

    def test_nan_negative_height_and_nonrectangular_candidate_fail_closed(self):
        for key, bad in [('clear_height_m', float('nan')), ('clear_height_m', -1)]:
            supplied = details(); supplied[key] = bad
            with self.assertRaises(ValueError): screen(fixture(), 'A', 'target', policy(), supplied, True)
        bad_policy = policy(); bad_policy['min_width_m'] = float('inf')
        with self.assertRaises(ValueError): screen(fixture(), 'A', 'target', bad_policy, details(), True)
        supplied = details()
        supplied['sukkah_footprint'] = {'type': 'Polygon', 'coordinates': [[[220020,630005],[220024,630005],[220024,630006],[220021,630006],[220021,630010],[220020,630010],[220020,630005]]]}
        with self.assertRaises(ValueError): screen(fixture(), 'A', 'target', policy(), supplied, True)

    def test_project_policy_and_wall_source_required(self):
        result = screen(fixture(), 'A', 'target', {}, details(), True)
        self.assertIsNone(result['geometric_screen_passed'])
        supplied = details(); supplied['walls'].pop('source')
        result = screen(fixture(), 'A', 'target', policy(), supplied, True)
        self.assertIsNone(result['geometric_screen_passed'])
