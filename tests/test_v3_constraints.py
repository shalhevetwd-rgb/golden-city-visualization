"""Regressions for evidence gates and edits that can change visible results."""
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from constraints import compare_snapshots, site_gate, sukkah_geometry


class SyntheticDesignationTests(unittest.TestCase):
    def test_only_literal_true_permits_unverified_synthetic_render(self):
        result = site_gate({"synthetic": True})
        self.assertTrue(result["final_render_allowed"])
        self.assertEqual(result["classification"], "synthetic_test")
        self.assertFalse(result["accurate_real_site"])
        for value in (1, "true", "false", [True], {"approved": True}):
            with self.subTest(value=value):
                result = site_gate({"synthetic": value})
                self.assertFalse(result["final_render_allowed"])
                self.assertNotEqual(result["classification"], "synthetic_test")
                self.assertFalse(result["accurate_real_site"])


class RealSiteEvidenceTests(unittest.TestCase):
    KEYS = (
        "parcel_geometry_verified", "alignment_approved", "building_positions_verified",
        "floors_verified", "heights_verified", "terrain_verified",
    )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="golden-city-gate-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.source = self.base / "source.json"
        self.source.write_text('{"synthetic_test_fixture":true}', encoding="utf-8")
        polygon = {"type": "Polygon", "coordinates": [[[0, 0], [20, 0],
                   [20, 20], [0, 20], [0, 0]]]}
        self.manifest = {
            "project_id": "test-site", "crs": "EPSG:2039", "boundary": polygon,
            "buildings": [{
                "type": "Feature", "geometry": polygon,
                "properties": {"id": "A", "height": 10, "floors": 3,
                               "base_elevation": 0, "balconies": [], "slabs": []},
            }],
            "overhead_obstructions": [],
            "topography": {"samples": [[0, 0, 0], [20, 0, 0], [0, 20, 0]]},
            "alignment": {"method": "fixture", "control_points": []},
        }
        self.geometry = self.base / "geometry.json"
        self.geometry.write_text(json.dumps(self.manifest), encoding="utf-8")
        source_hash = self.digest(self.source)
        geometry_hash = self.digest(self.geometry)
        self.project = {
            "id": "test-site", "crs": "EPSG:2039",
            "geometry_manifest_path": "geometry.json",
            "geometry_version_sha256": geometry_hash,
            "inputs": {"test-source": {"path": "source.json", "sha256": source_hash}},
            "evidence": {},
        }
        for key in self.KEYS:
            self.project[key] = True
            self.project["evidence"][key] = {
                "source": "test-source", "source_sha256": source_hash,
                "checked_at": "2026-10-12T11:00:00+00:00",
                "verification": "professional_review", "project_id": "test-site",
                "geometry_version_sha256": geometry_hash,
                "reviewer": {"id": "test-reviewer", "name": "Fixture reviewer"},
            }

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def gate(self, project=None):
        return site_gate(self.project if project is None else project, base_dir=self.base)

    def assert_blocked(self, project=None):
        result = self.gate(project)
        self.assertFalse(result["evidence_complete"])
        self.assertFalse(result["recorded_real_site_review"])
        self.assertFalse(result["final_render_allowed"])
        self.assertFalse(result["accurate_real_site"])
        self.assertFalse(result["certification"])

    def test_complete_recorded_review_is_not_certification_or_runtime_capability(self):
        result = self.gate()
        self.assertTrue(result["evidence_complete"])
        self.assertTrue(result["recorded_real_site_review"])
        self.assertFalse(result["final_render_allowed"])
        self.assertFalse(result["accurate_real_site"])
        self.assertFalse(result["certification"])

    def test_every_approval_and_evidence_record_is_required(self):
        for key in self.KEYS:
            for mode in ("missing_flag", "truthy_flag", "missing_evidence"):
                with self.subTest(key=key, mode=mode):
                    project = copy.deepcopy(self.project)
                    if mode == "missing_flag":
                        del project[key]
                    elif mode == "truthy_flag":
                        project[key] = 1
                    else:
                        del project["evidence"][key]
                    self.assert_blocked(project)

    def test_arbitrary_source_and_date_strings_are_not_provenance(self):
        project = copy.deepcopy(self.project)
        for record in project["evidence"].values():
            record["source"] = "not-a-real-source"
            record["checked_at"] = "not-a-date"
        self.assert_blocked(project)

    def test_review_timestamp_must_be_real_iso_date_with_timezone(self):
        for value in ("not-a-date", "2026-10-12", "2026-10-12T11:00:00",
                      "2026-13-12T11:00:00+00:00", None, True):
            with self.subTest(timestamp=value):
                project = copy.deepcopy(self.project)
                project["evidence"][self.KEYS[0]]["checked_at"] = value
                self.assert_blocked(project)

    def test_review_is_bound_to_project_geometry_and_named_reviewer(self):
        mutations = (
            ("project_id", "different-project"),
            ("geometry_version_sha256", "0" * 64),
            ("source_sha256", "0" * 64),
            ("verification", "automated_check"),
            ("reviewer", {}),
            ("reviewer", {"id": "reviewer", "name": ""}),
            ("reviewer", {"id": "", "name": "Reviewer"}),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                project = copy.deepcopy(self.project)
                project["evidence"][self.KEYS[0]][field] = value
                self.assert_blocked(project)

    def test_source_bytes_are_reopened_and_hashed(self):
        self.source.write_text('{"different_fixture":true}', encoding="utf-8")
        self.assert_blocked()

    def test_changed_current_source_hash_does_not_reuse_old_review(self):
        self.source.write_text('{"different_fixture":true}', encoding="utf-8")
        self.project["inputs"]["test-source"]["sha256"] = self.digest(self.source)
        self.assert_blocked()

    def test_geometry_bytes_are_reopened_and_hashed(self):
        self.manifest["buildings"][0]["properties"]["height"] = 11
        self.geometry.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.assert_blocked()

    def test_new_geometry_version_does_not_reuse_old_review(self):
        self.manifest["buildings"][0]["properties"]["height"] = 11
        self.geometry.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.project["geometry_version_sha256"] = self.digest(self.geometry)
        self.assert_blocked()

    def test_geographic_or_mismatched_crs_cannot_pass_metre_geometry_gate(self):
        for value in ("EPSG:4326", "EPSG:3857", "invalid-crs", None):
            with self.subTest(crs=value):
                project = copy.deepcopy(self.project)
                project["crs"] = value
                self.assert_blocked(project)

    def test_matching_geographic_or_foot_crs_still_rejects_metre_claim(self):
        for crs in ("EPSG:4326", "EPSG:2263"):
            with self.subTest(crs=crs):
                manifest = copy.deepcopy(self.manifest)
                manifest["crs"] = crs
                self.geometry.write_text(json.dumps(manifest), encoding="utf-8")
                project = copy.deepcopy(self.project)
                project["crs"] = crs
                digest = self.digest(self.geometry)
                project["geometry_version_sha256"] = digest
                for record in project["evidence"].values():
                    record["geometry_version_sha256"] = digest
                self.assert_blocked(project)

    def test_matching_malformed_crs_is_reported_instead_of_escaping(self):
        for crs in ("invalid-crs", {"invalid": "crs"}, True):
            with self.subTest(crs=crs):
                manifest = copy.deepcopy(self.manifest)
                manifest["crs"] = crs
                self.geometry.write_text(json.dumps(manifest), encoding="utf-8")
                project = copy.deepcopy(self.project)
                project["crs"] = crs
                digest = self.digest(self.geometry)
                project["geometry_version_sha256"] = digest
                for record in project["evidence"].values():
                    record["geometry_version_sha256"] = digest
                self.assert_blocked(project)

    def test_geometry_manifest_from_another_project_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["project_id"] = "other-project"
        self.geometry.write_text(json.dumps(manifest), encoding="utf-8")
        project = copy.deepcopy(self.project)
        digest = self.digest(self.geometry)
        project["geometry_version_sha256"] = digest
        for record in project["evidence"].values():
            record["geometry_version_sha256"] = digest
        self.assert_blocked(project)

    def test_malformed_evidence_containers_fail_closed(self):
        for value in (None, [], "approved"):
            with self.subTest(evidence=value):
                project = copy.deepcopy(self.project)
                project["evidence"] = value
                self.assert_blocked(project)
        for value in (None, [], True):
            with self.subTest(evidence_record=value):
                project = copy.deepcopy(self.project)
                project["evidence"][self.KEYS[0]] = value
                self.assert_blocked(project)

    def test_nonfinite_geometry_json_rejected_even_with_matching_hashes(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["buildings"][0]["properties"]["height"] = value
                self.geometry.write_text(json.dumps(manifest), encoding="utf-8")
                project = copy.deepcopy(self.project)
                digest = self.digest(self.geometry)
                project["geometry_version_sha256"] = digest
                for record in project["evidence"].values():
                    record["geometry_version_sha256"] = digest
                self.assert_blocked(project)

    def test_missing_and_unsafe_source_paths_fail_closed(self):
        for path in ("missing.json", "../outside-source.json", str(self.source)):
            with self.subTest(path=path):
                project = copy.deepcopy(self.project)
                project["inputs"]["test-source"]["path"] = path
                self.assert_blocked(project)

    def test_symlinks_cannot_escape_source_or_geometry_base_directory(self):
        with tempfile.TemporaryDirectory(prefix="golden-city-gate-outside-") as directory:
            outside = Path(directory)
            source = outside / "source.json"
            source.write_bytes(self.source.read_bytes())
            source_link = self.base / "source-link.json"
            source_link.symlink_to(source)
            project = copy.deepcopy(self.project)
            project["inputs"]["test-source"]["path"] = "source-link.json"
            self.assert_blocked(project)
            geometry = outside / "geometry.json"
            geometry.write_bytes(self.geometry.read_bytes())
            geometry_link = self.base / "geometry-link.json"
            geometry_link.symlink_to(geometry)
            project = copy.deepcopy(self.project)
            project["geometry_manifest_path"] = "geometry-link.json"
            self.assert_blocked(project)

    def test_rehashed_manifest_still_requires_positive_height_and_integer_floors(self):
        for field, value in (("height", 0), ("height", -1), ("height", True),
                             ("floors", 0), ("floors", -1), ("floors", 2.5),
                             ("floors", True)):
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["buildings"][0]["properties"][field] = value
                self.geometry.write_text(json.dumps(manifest), encoding="utf-8")
                project = copy.deepcopy(self.project)
                digest = self.digest(self.geometry)
                project["geometry_version_sha256"] = digest
                for record in project["evidence"].values():
                    record["geometry_version_sha256"] = digest
                self.assert_blocked(project)

    def test_project_json_cannot_claim_renderer_capabilities(self):
        project = copy.deepcopy(self.project)
        project["renderer_capabilities"] = {
            "real_source_preservation": True, "terrain_support": True,
        }
        result = self.gate(project)
        self.assertTrue(result["evidence_complete"])
        self.assertFalse(result["final_render_allowed"])

    def test_trusted_capability_opt_in_still_does_not_claim_certification(self):
        capabilities = {"verified_terrain", "preserved_environment", "georeferenced_inputs"}
        result = site_gate(self.project, base_dir=self.base,
                           renderer_capabilities=capabilities)
        self.assertTrue(result["evidence_complete"])
        self.assertTrue(result["recorded_real_site_review"])
        self.assertTrue(result["final_render_allowed"])
        self.assertFalse(result["accurate_real_site"])
        self.assertFalse(result["certification"])

    def test_each_runtime_capability_is_required(self):
        capabilities = {"verified_terrain", "preserved_environment", "georeferenced_inputs"}
        for missing in capabilities:
            with self.subTest(missing=missing):
                result = site_gate(self.project, base_dir=self.base,
                                   renderer_capabilities=capabilities - {missing})
                self.assertTrue(result["evidence_complete"])
                self.assertFalse(result["final_render_allowed"])

    def test_malformed_capability_items_fail_closed_without_type_error(self):
        for capabilities in ([{}], [[]], [set()],
                             ["verified_terrain", {}, ["preserved_environment"]]):
            with self.subTest(capabilities=capabilities):
                result = site_gate(self.project, base_dir=self.base,
                                   renderer_capabilities=capabilities)
                self.assertTrue(result["evidence_complete"])
                self.assertFalse(result["final_render_allowed"])


class SukkahScreeningTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "rectangle": [0, 0, 3, 3],
            "floor_z": 10,
            "clear_height_m": 3,
        }
        self.policy = {
            "min_width_m": 2,
            "min_length_m": 2,
            "min_height_m": 2,
        }

    def screen(self, spec=None, obstructions=None, policy=None, **kwargs):
        return sukkah_geometry(
            self.spec if spec is None else spec,
            [] if obstructions is None else obstructions,
            self.policy if policy is None else policy,
            **kwargs,
        )

    def test_unobstructed_candidate_requires_known_complete_inventory(self):
        for value in (None, False, 1, "true"):
            with self.subTest(completeness=value):
                result = self.screen(obstructions_complete=value)
                self.assertNotEqual(result["status"], "geometric_candidate")
                self.assertFalse(result["halakhic_approval"])
        result = self.screen(obstructions_complete=True)
        self.assertEqual(result["status"], "geometric_candidate")
        self.assertFalse(result["halakhic_approval"])

    def test_spec_completeness_is_literal_boolean(self):
        spec = dict(self.spec, obstructions_complete=True)
        self.assertEqual(self.screen(spec=spec)["status"], "geometric_candidate")
        spec["obstructions_complete"] = "true"
        self.assertNotEqual(self.screen(spec=spec)["status"], "geometric_candidate")

    def test_known_overhang_fails_even_with_incomplete_inventory(self):
        obstruction = {"rectangle": [1, 1, 4, 4], "z": 13}
        result = self.screen(obstructions=[obstruction], obstructions_complete=False)
        self.assertTrue(result["sky_obstructed"])
        self.assertEqual(result["status"], "geometry_failed")
        self.assertFalse(result["halakhic_approval"])

    def test_edge_touching_and_lower_slabs_do_not_cover_sky(self):
        obstructions = [
            {"rectangle": [3, 0, 4, 3], "z": 20},
            {"rectangle": [0, 0, 3, 3], "z": 9},
        ]
        result = self.screen(obstructions=obstructions, obstructions_complete=True)
        self.assertEqual(result["status"], "geometric_candidate")
        self.assertFalse(result["sky_obstructed"])

    def test_nonfinite_geometry_cannot_become_candidate(self):
        for field in ("floor_z", "clear_height_m"):
            for value in (math.nan, math.inf, -math.inf, True, "3"):
                with self.subTest(field=field, value=value):
                    spec = copy.deepcopy(self.spec)
                    spec[field] = value
                    with self.assertRaises(ValueError):
                        self.screen(spec=spec, obstructions_complete=True)
        for value in (math.nan, math.inf, -math.inf, True, "3"):
            with self.subTest(rectangle_coordinate=value):
                spec = copy.deepcopy(self.spec)
                spec["rectangle"][2] = value
                with self.assertRaises(ValueError):
                    self.screen(spec=spec, obstructions_complete=True)

    def test_reversed_empty_or_wrong_dimension_rectangle_rejected(self):
        for rectangle in ([3, 0, 0, 3], [0, 3, 3, 0], [0, 0, 0, 3],
                          [0, 0, 3, 0], [0, 0, 3], [0, 0, 3, 3, 1], None):
            with self.subTest(rectangle=rectangle):
                spec = dict(self.spec, rectangle=rectangle)
                with self.assertRaises(ValueError):
                    self.screen(spec=spec, obstructions_complete=True)

    def test_integer_coordinates_too_large_for_renderer_raise_value_error(self):
        value = 10 ** 1000
        for rectangle in ([0, 0, value, 3], [0, -value, 3, 3]):
            with self.subTest(rectangle=rectangle):
                with self.assertRaises(ValueError):
                    self.screen(spec=dict(self.spec, rectangle=rectangle),
                                obstructions_complete=True)

    def test_negative_or_zero_clear_height_rejected(self):
        for value in (0, -1):
            with self.subTest(height=value):
                with self.assertRaises(ValueError):
                    self.screen(spec=dict(self.spec, clear_height_m=value),
                                obstructions_complete=True)

    def test_invalid_policy_dimensions_rejected(self):
        for field in self.policy:
            for value in (0, -1, math.nan, math.inf, True, "2"):
                with self.subTest(field=field, value=value):
                    policy = dict(self.policy, **{field: value})
                    with self.assertRaises(ValueError):
                        self.screen(policy=policy, obstructions_complete=True)

    def test_malformed_obstruction_is_not_silently_ignored(self):
        malformed = [
            {},
            {"rectangle": [0, 0, 3, 3]},
            {"rectangle": [0, 0, 3, 3], "z": math.nan},
            {"rectangle": [0, 0, 3, 3], "z": math.inf},
            {"rectangle": [0, 0, 3, 3], "z": True},
            {"rectangle": [3, 0, 0, 3], "z": 13},
            {"rectangle": [0, 0, 3], "z": 13},
            "unknown roof",
        ]
        for obstruction in malformed:
            with self.subTest(obstruction=obstruction):
                with self.assertRaises(ValueError):
                    self.screen(obstructions=[obstruction], obstructions_complete=True)


class EditInvariantTests(unittest.TestCase):
    def setUp(self):
        self.before = {
            "building:A": {
                "owner": "A",
                "role": "facade",
                "geometry": "source-geometry-hash",
                "transform": [0, 0, 0],
                "materials": [{"base_color": [0.8, 0.7, 0.6]}],
                "visibility": {"hide_render": False},
                "modifiers": [],
            },
            "roof:A": {
                "owner": "A", "role": "roof", "geometry": "roof-geometry",
                "materials": [{"base_color": [0.2, 0.2, 0.2]}],
            },
            "building:B": {
                "owner": "B", "role": "facade", "geometry": "neighbor-geometry",
                "materials": [{"base_color": [0.9, 0.9, 0.9]}],
            },
            "__world__": {"owner": "context", "shader": {"sky": "original"}},
        }

    def test_facade_material_change_is_accepted_and_neighbors_identical(self):
        after = copy.deepcopy(self.before)
        after["building:A"]["materials"][0]["base_color"] = [0.1, 0.2, 0.3]
        result = compare_snapshots(self.before, after, "A")
        self.assertTrue(result["passed"])
        self.assertEqual(result["changed_objects"], ["building:A"])
        self.assertEqual(result["outside_target_changed"], [])
        self.assertFalse(result["pixel_identity_claim"])

    def test_target_ownership_role_visibility_and_modifier_changes_rejected(self):
        mutations = {
            "owner": "B",
            "role": "roof",
            "visibility": {"hide_render": True},
            "modifiers": [{"type": "DISPLACE", "strength": 1}],
            "geometry": "changed-vertices",
            "transform": [1, 0, 0],
            "camera": {"lens": 85},
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                after = copy.deepcopy(self.before)
                after["building:A"][field] = value
                self.assertFalse(compare_snapshots(self.before, after, "A")["passed"])

    def test_facade_edit_cannot_hide_an_immutable_change(self):
        after = copy.deepcopy(self.before)
        after["building:A"]["materials"] = [{"base_color": [0.1, 0.2, 0.3]}]
        after["building:A"]["visibility"]["hide_render"] = True
        self.assertFalse(compare_snapshots(self.before, after, "A")["passed"])

    def test_material_change_on_target_roof_is_not_facade_edit(self):
        after = copy.deepcopy(self.before)
        after["roof:A"]["materials"][0]["base_color"] = [0.1, 0.2, 0.3]
        self.assertFalse(compare_snapshots(self.before, after, "A")["passed"])

    def test_neighbor_and_world_changes_rejected_with_valid_facade_edit(self):
        for changed_object, field, value in (
            ("building:B", "materials", [{"base_color": [1, 0, 0]}]),
            ("__world__", "shader", {"sky": "different"}),
        ):
            with self.subTest(changed_object=changed_object):
                after = copy.deepcopy(self.before)
                after["building:A"]["materials"] = [{"base_color": [0.1, 0.2, 0.3]}]
                after[changed_object][field] = value
                result = compare_snapshots(self.before, after, "A")
                self.assertFalse(result["passed"])
                self.assertIn(changed_object, result["outside_target_changed"])

    def test_adding_or_removing_objects_is_not_material_edit(self):
        after = copy.deepcopy(self.before)
        after["new:A"] = {"owner": "A", "role": "facade", "materials": []}
        self.assertFalse(compare_snapshots(self.before, after, "A")["passed"])
        after = copy.deepcopy(self.before)
        del after["roof:A"]
        self.assertFalse(compare_snapshots(self.before, after, "A")["passed"])

    def test_legacy_material_record_remains_supported(self):
        before = {"wall": {"owner": "A", "geometry": "same", "material": "old"}}
        after = {"wall": {"owner": "A", "geometry": "same", "material": "new"}}
        self.assertTrue(compare_snapshots(before, after, "A")["passed"])


if __name__ == "__main__":
    unittest.main()
