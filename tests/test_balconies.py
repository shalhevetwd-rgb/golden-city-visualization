import copy
import unittest

from shapely.geometry import shape

from balconies import analyze_balcony


def rectangle(x, y, width, depth):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + width, y],
            [x + width, y + depth], [x, y + depth], [x, y]]]}


def slab(identifier, x=220020, y=630005, width=4, depth=5, elevation=3):
    return {"id": identifier, "footprint": rectangle(x, y, width, depth),
            "elevation": elevation, "thickness": 0.2}


def fixture():
    return {"crs": "EPSG:2039", "buildings": [{"type": "Feature",
        "geometry": rectangle(220000, 630000, 20, 20),
        "properties": {"id": "A", "height": 15, "floors": 5,
                       "balconies": [slab("target")]}}]}


class BalconyAnalysisTests(unittest.TestCase):
    def test_unknown_overhead_is_not_treated_as_open_or_certified(self):
        project = fixture()
        original = copy.deepcopy(project)
        result = analyze_balcony(project, "A", "target")
        self.assertEqual(project, original)
        self.assertEqual(result["status"], "incomplete_scene")
        self.assertIsNone(result["open_sky_area_m2"])
        self.assertIsNone(result["geometrically_clear"])
        self.assertEqual(result["geometric_open_sky_area_m2"], 20)
        self.assertEqual(result["certification"], {"halachic": False, "professional": False, "survey": False})

    def test_explicit_complete_empty_scene_yields_geometric_clear_only(self):
        result = analyze_balcony(fixture(), "A", "target", overhead_complete=True)
        self.assertTrue(result["geometrically_clear"])
        self.assertEqual(result["open_sky_area_m2"], 20)
        self.assertEqual(result["open_sky_fraction"], 1)
        self.assertEqual(result["completeness_basis"], "caller_declared")
        self.assertEqual(result["status"], "geometric_analysis_only")
        self.assertFalse(result["certification"]["halachic"])

    def test_balcony_default_thickness_matches_model_and_other_slab_defaults(self):
        project = fixture()
        del project["buildings"][0]["properties"]["balconies"][0]["thickness"]
        project["overhead_obstructions"] = [slab("canopy", elevation=6)]
        del project["overhead_obstructions"][0]["thickness"]
        result = analyze_balcony(project, "A", "target", True)
        self.assertEqual(result["section"]["balcony_bottom_m"], 2.75)
        self.assertEqual(result["top_view"]["obstructions"][0]["lower_z"], 5.8)

    def test_higher_partial_balcony_and_section_geometry(self):
        project = fixture()
        project["buildings"][0]["properties"]["balconies"].append(slab("above", x=220022, width=2, elevation=6))
        result = analyze_balcony(project, "A", "target", overhead_complete=True)
        self.assertEqual(result["total_area_m2"], 20)
        self.assertEqual(result["projected_obstruction_area_m2"], 10)
        self.assertEqual(result["open_sky_area_m2"], 10)
        self.assertEqual(result["open_sky_fraction"], 0.5)
        self.assertEqual(shape(result["top_view"]["blocked"]).area, 10)
        self.assertEqual(shape(result["top_view"]["open"]).area, 10)
        self.assertEqual(result["section"]["balcony_segments_x"], [[220020, 220024]])
        self.assertEqual(result["section"]["obstructions"][0]["segments_x"], [[220022, 220024]])
        self.assertEqual(result["section"]["obstructions"][0]["lower_z"], 5.8)
        self.assertEqual(result["section"]["max_z"], 6)

    def test_overlapping_projections_union_without_double_counting(self):
        project = fixture()
        project["buildings"][0]["properties"]["slabs"] = [slab("roof", x=220022, width=2, elevation=9)]
        project["overhead_obstructions"] = [slab("canopy", x=220022, width=2, elevation=7)]
        result = analyze_balcony(project, "A", "target", overhead_complete=True)
        self.assertEqual(len(result["top_view"]["obstructions"]), 2)
        self.assertEqual(result["projected_obstruction_area_m2"], 10)
        self.assertEqual(result["open_sky_area_m2"], 10)

    def test_lower_balcony_and_distant_geometry_do_not_obstruct(self):
        project = fixture()
        project["buildings"][0]["properties"]["balconies"].extend([
            slab("below", elevation=1), slab("distant", x=220030, elevation=9)])
        result = analyze_balcony(project, "A", "target", overhead_complete=True)
        self.assertEqual(result["projected_obstruction_area_m2"], 0)
        self.assertEqual(result["top_view"]["obstructions"], [])

    def test_second_building_above_balcony_and_different_base_datum(self):
        project = fixture()
        project["buildings"].append({"type": "Feature", "geometry": rectangle(220022, 630005, 2, 5),
                                      "properties": {"id": "B", "height": 4, "base_elevation": 10}})
        result = analyze_balcony(project, "A", "target", overhead_complete=True)
        obstruction = result["top_view"]["obstructions"][0]
        self.assertEqual(obstruction["kind"], "building")
        self.assertEqual(obstruction["lower_z"], 10)
        self.assertEqual(obstruction["upper_z"], 14)
        self.assertEqual(result["open_sky_area_m2"], 10)
        project["buildings"][1]["properties"]["base_elevation"] = -5
        project["buildings"][1]["properties"]["height"] = 7
        self.assertEqual(analyze_balcony(project, "A", "target", True)["projected_obstruction_area_m2"], 0)

    def test_body_overlap_is_reported_conservatively(self):
        project = fixture()
        project["buildings"][0]["properties"]["balconies"][0]["footprint"] = rectangle(220018, 630005, 4, 5)
        result = analyze_balcony(project, "A", "target", True)
        self.assertEqual(result["projected_obstruction_area_m2"], 10)
        self.assertEqual(result["top_view"]["obstructions"][0]["kind"], "building")

    def test_hole_remains_empty_in_top_and_section(self):
        project = fixture()
        target = project["buildings"][0]["properties"]["balconies"][0]
        target["footprint"]["coordinates"].append(rectangle(220021, 630006, 2, 3)["coordinates"][0])
        result = analyze_balcony(project, "A", "target", True)
        self.assertEqual(result["total_area_m2"], 14)
        self.assertEqual(result["section"]["balcony_segments_x"], [[220020, 220021], [220023, 220024]])

    def test_invalid_inputs_and_missing_ids_rejected(self):
        for building, balcony in (("missing", "target"), ("A", "missing")):
            with self.assertRaises(ValueError):
                analyze_balcony(fixture(), building, balcony)
        with self.assertRaises(ValueError):
            analyze_balcony(fixture(), "A", "target", overhead_complete="yes")
        for field, value in (("elevation", float("nan")), ("thickness", -1), ("footprint", None)):
            project = fixture()
            project["buildings"][0]["properties"]["balconies"][0][field] = value
            with self.assertRaises(ValueError):
                analyze_balcony(project, "A", "target")
        project = fixture()
        project["crs"] = "EPSG:4326"
        with self.assertRaises(ValueError):
            analyze_balcony(project, "A", "target")

    def test_duplicate_slab_and_building_ids_rejected(self):
        project = fixture()
        project["buildings"][0]["properties"]["balconies"].append(slab("target", elevation=6))
        with self.assertRaises(ValueError):
            analyze_balcony(project, "A", "target")
        project = fixture()
        project["buildings"].append(copy.deepcopy(project["buildings"][0]))
        with self.assertRaises(ValueError):
            analyze_balcony(project, "A", "target")


if __name__ == "__main__":
    unittest.main()
