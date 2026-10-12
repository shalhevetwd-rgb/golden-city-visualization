import copy
import math
import unittest

from pyproj import Transformer
from shapely.geometry import shape

from spatial import align_site_plan, import_boundary


def rectangle(x, y, width=20, depth=20):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + width, y],
            [x + width, y + depth], [x, y + depth], [x, y]]]}


def provenance(source_type="user_supplied"):
    return {"provider": "Test survey supplier", "reference": "test-boundary.geojson",
            "acquired_at": "2026-10-12", "source_type": source_type}


def features():
    return [{"type": "Feature", "geometry": rectangle(0, 0, 6, 4),
             "properties": {"id": "A", "height": 12, "floors": 4}},
            {"type": "Feature", "geometry": rectangle(12, 10, 6, 4),
             "properties": {"id": "B", "height": 9, "floors": 3}}]


def control_pairs(angle=25, scale=1.5, base=(220000, 630000)):
    radians = math.radians(angle)
    a, b = scale * math.cos(radians), scale * math.sin(radians)
    return [{"plan_xy": [x, y],
             "target_xy": [a * x - b * y + base[0], b * x + a * y + base[1]]}
            for x, y in ((0, 0), (40, 0), (0, 30), (40, 30))]


class BoundaryImportTests(unittest.TestCase):
    def test_real_coordinate_sites_in_two_projected_crs(self):
        # Synthetic rectangles at actual geographic positions test projection;
        # no assertion here claims they are real cadastral boundaries.
        for longitude, latitude, target in ((35.215, 31.777, "EPSG:2039"),
                                            (13.4, 52.5, "EPSG:32633")):
            with self.subTest(target=target):
                source = rectangle(longitude, latitude, 0.0002, 0.0002)
                original = copy.deepcopy(source)
                result = import_boundary(source, "EPSG:4326", target, provenance("synthetic"))
                self.assertGreater(result["report"]["area_m2"], 200)
                self.assertLess(result["report"]["area_m2"], 600)
                self.assertEqual(source, original)
                projected = shape(result["boundary"])
                x, y = Transformer.from_crs("EPSG:4326", target, always_xy=True).transform(longitude, latitude)
                self.assertAlmostEqual(projected.bounds[0], x, delta=1)
                self.assertAlmostEqual(projected.bounds[1], y, delta=1)
                self.assertFalse(result["report"]["survey_accuracy_verified"])
                self.assertEqual(result["source"]["verification"], "not_verified")
                self.assertEqual(len(result["source"]["geometry_sha256"]), 64)

    def test_feature_projected_source_and_provenance_do_not_certify(self):
        feature = {"type": "Feature", "geometry": rectangle(220000, 630000), "properties": {}}
        metadata = {**provenance("official"), "verification": "verified"}
        result = import_boundary(feature, "EPSG:2039", "EPSG:2039", metadata)
        self.assertEqual(shape(result["boundary"]).area, 400)
        self.assertEqual(result["source"]["verification"], "not_verified")
        self.assertEqual(metadata["verification"], "verified")

    def test_wrong_crs_axis_order_and_unknown_source_rejected(self):
        for source, source_crs, target in (
            (rectangle(31.777, 35.215, 0.0002, 0.0002), "EPSG:4326", "EPSG:2039"),
            (rectangle(35, 95, 0.1, 0.1), "EPSG:4326", "EPSG:2039"),
            (rectangle(220000, 630000), "EPSG:4326", "EPSG:2039"),
            (rectangle(220000, 630000), "EPSG:2039", "EPSG:4326"),
            (rectangle(220000, 630000), None, "EPSG:2039"),
            (rectangle(220000, 630000), "EPSG:2039", "EPSG:2227"),
        ):
            with self.subTest(source_crs=source_crs, target=target):
                with self.assertRaises(ValueError):
                    import_boundary(source, source_crs, target, provenance())

    def test_missing_provenance_invalid_date_and_unsupported_geojson(self):
        polygon = rectangle(220000, 630000)
        for metadata in ({}, {**provenance(), "reference": ""},
                         {**provenance(), "acquired_at": "not-a-date"},
                         {**provenance(), "source_type": "certified"}):
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    import_boundary(polygon, "EPSG:2039", "EPSG:2039", metadata)
        with self.assertRaises(ValueError):
            import_boundary({"type": "FeatureCollection", "features": []}, "EPSG:2039", "EPSG:2039", provenance())

    def test_bad_ring_nonfinite_and_3d_rejected(self):
        for polygon in (
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
            {"type": "Polygon", "coordinates": [[[0, 0, 2], [1, 0, 2], [1, 1, 2], [0, 0, 2]]]},
            {"type": "Polygon", "coordinates": [[[0, 0], [float("nan"), 0], [1, 1], [0, 0]]]},
        ):
            with self.assertRaises(ValueError):
                import_boundary(polygon, "EPSG:2039", "EPSG:2039", provenance())


class AlignmentTests(unittest.TestCase):
    def test_rotation_scale_multiple_footprints_and_source_unchanged(self):
        source, points = features(), control_pairs()
        original, original_points = copy.deepcopy(source), copy.deepcopy(points)
        result = align_site_plan(source, points, "EPSG:2039")
        self.assertEqual(source, original)
        self.assertEqual(points, original_points)
        self.assertAlmostEqual(result["alignment"]["scale"], 1.5)
        self.assertAlmostEqual(result["alignment"]["rotation_degrees"], 25)
        self.assertLess(result["alignment"]["max_residual_m"], 1e-8)
        self.assertFalse(result["alignment"]["survey_accuracy_verified"])
        self.assertEqual([building["properties"] for building in result["buildings"]],
                         [building["properties"] for building in source])
        for before, after in zip(source, result["buildings"]):
            self.assertAlmostEqual(shape(after["geometry"]).area, shape(before["geometry"]).area * 2.25, places=7)

    def test_second_site_crs_negative_rotation(self):
        result = align_site_plan(features(), control_pairs(-40, 0.8, (390000, 5810000)), "EPSG:32633")
        self.assertAlmostEqual(result["alignment"]["rotation_degrees"], -40)
        self.assertAlmostEqual(result["alignment"]["scale"], 0.8)
        self.assertEqual(result["crs"], "EPSG:32633")

    def test_attached_balconies_and_slabs_share_horizontal_alignment(self):
        source = features()
        source[0]["properties"]["balconies"] = [{"id": "porch", "footprint": rectangle(6, 0, 2, 3),
                                                 "elevation": 3, "thickness": 0.2}]
        source[0]["properties"]["slabs"] = [{"id": "roof", "footprint": rectangle(6, 0, 2, 3),
                                             "elevation": 6, "thickness": 0.3}]
        original = copy.deepcopy(source)
        result = align_site_plan(source, control_pairs(angle=0, scale=2), "EPSG:2039")
        self.assertEqual(source, original)
        for collection in ("balconies", "slabs"):
            before = source[0]["properties"][collection][0]
            after = result["buildings"][0]["properties"][collection][0]
            self.assertEqual(shape(after["footprint"]).bounds, (220012, 630000, 220016, 630006))
            self.assertEqual(after["elevation"], before["elevation"])
            self.assertEqual(after["thickness"], before["thickness"])
        source[0]["properties"]["slabs"][0]["footprint"] = None
        with self.assertRaises(ValueError):
            align_site_plan(source, control_pairs(), "EPSG:2039")

    def test_outlier_wrong_pair_and_mirror_rejected(self):
        incorrect = control_pairs()
        incorrect[-1]["target_xy"][0] += 5
        swapped = control_pairs()
        swapped[1]["target_xy"], swapped[2]["target_xy"] = swapped[2]["target_xy"], swapped[1]["target_xy"]
        mirrored = [{"plan_xy": [x, y], "target_xy": [220000 - x, 630000 + y]}
                    for x, y in ((0, 0), (40, 0), (0, 30), (40, 30))]
        for pairs in (incorrect, swapped, mirrored):
            with self.assertRaises(ValueError):
                align_site_plan(features(), pairs, "EPSG:2039", max_residual=0.2)

    def test_insufficient_collinear_and_poor_distribution_rejected(self):
        for pairs in (
            control_pairs()[:2],
            [{"plan_xy": [i, i], "target_xy": [220000 + i, 630000 + i]} for i in range(3)],
            [{"plan_xy": [0, 0], "target_xy": [220000, 630000]},
             {"plan_xy": [40, 0], "target_xy": [220040, 630000]},
             {"plan_xy": [20, 0.000001], "target_xy": [220020, 630000.000001]}],
            [control_pairs()[0], control_pairs()[0], control_pairs()[2]],
        ):
            with self.assertRaises(ValueError):
                align_site_plan(features(), pairs, "EPSG:2039")

    def test_invalid_tolerance_scale_bounds_and_crs_rejected(self):
        for kwargs in ({"max_residual": 0}, {"max_residual": float("nan")},
                       {"scale_bounds": (0, 10)}, {"scale_bounds": (2, 3)}):
            with self.assertRaises(ValueError):
                align_site_plan(features(), control_pairs(), "EPSG:2039", **kwargs)
        with self.assertRaises(ValueError):
            align_site_plan(features(), control_pairs(), "EPSG:4326")
        with self.assertRaises(ValueError):
            align_site_plan(features(), control_pairs(base=(390000, 5810000)), "EPSG:2039")

    def test_measured_residuals_are_reported_inside_tolerance(self):
        pairs = control_pairs()
        pairs[-1]["target_xy"][0] += 0.05
        result = align_site_plan(features(), pairs, "EPSG:2039", max_residual=0.1)
        self.assertGreater(result["alignment"]["max_residual_m"], 0)
        self.assertLess(result["alignment"]["max_residual_m"], 0.1)
        self.assertEqual(result["alignment"]["tolerance_m"], 0.1)


if __name__ == "__main__":
    unittest.main()
