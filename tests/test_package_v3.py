"""Delivery regressions; use copied public PNGs, never regenerate scenes."""
import base64
import copy
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from delivery_validation import ValidationError, validate_archive
from engine.constraints import compare_snapshots
from scripts.package_v3 import (
    ARCHIVE_NAME, GENERATOR, IMAGE_NAMES, MODEL_NAMES, README_NAME, ROOT,
    _check_owned, _manifest, _record, _write_zip, check_model_expectations,
    check_semantic_reports, file_identity, package_v3, safe_name,
    validate_embedded_viewer, decode_embedded_model,
    validate_portable_materials, _verify_zip_portable_materials,
    verify_v3,
)

PUBLIC_PNG = ROOT / "outputs" / "v2" / "aerial.png"


def expectations():
    result = {}
    for name in MODEL_NAMES:
        record = {
            "objects": 15, "faces": 30,
            "object_bounds": {f"original:{index}": [[index, 0, 0], [index + 1, 1, 1]] for index in range(15)},
            "cameras": 3,
            "camera_parameters": {
                "camera:" + key: {"position": [30, -60, 20], "forward": [0, 1, 0], "up": [0, 0, 1], "vertical_fov": 50}
                for key in ("aerial", "street", "architectural")
            },
        }
        record["vertices" if name.endswith(".blend") else "min_vertices"] = 60
        if name.endswith(".glb"):
            record["material_bindings"] = {node: [{"source_material_name": "Fixture native effective material", "base_color_factor": [.2, .3, .4, 1], "facade_tint_scale": 1, "roughness_factor": .8, "metallic_factor": 0}] for node in record["object_bounds"]}
        result[name] = record
    return result


def unit_portable_document(expectation):
    """Small GLB JSON used only by the independent material reader tests."""
    nodes, meshes, materials = [], [], []
    for name, bindings in expectation["material_bindings"].items():
        primitives = []
        for binding in bindings:
            primitives.append({"attributes": {}, "material": len(materials)})
            materials.append({"name": "portable:" + binding["source_material_name"], "pbrMetallicRoughness": {"baseColorFactor": binding["base_color_factor"], "roughnessFactor": binding["roughness_factor"], "metallicFactor": binding["metallic_factor"]}, "extras": {"source_material_name": binding["source_material_name"], "facade_tint_scale": binding["facade_tint_scale"]}})
        nodes.append({"name": name, "mesh": len(meshes)})
        meshes.append({"primitives": primitives})
    return {"asset": {"version": "2.0"}, "nodes": nodes, "meshes": meshes, "materials": materials}


def unit_glb_bytes(document):
    chunk = json.dumps(document, allow_nan=False).encode()
    chunk += b" " * (-len(chunk) % 4)
    return struct.pack("<4sII", b"glTF", 2, len(chunk) + 20) + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk


def scene_reports(directory):
    before = {
        "__scene__": {"owner": "context", "geometry": None, "camera": "camera:aerial"},
        "__world__": {"owner": "context", "geometry": None, "shader": {"strength": .5}},
        "facade:A": {"owner": "A", "role": "facade", "geometry": "constant", "transform": [1, 0, 0], "materials": ["stone"]},
        "building:B": {"owner": "B", "role": "facade", "geometry": "constant", "transform": [1, 0, 0], "materials": ["stone"]},
    }
    after = copy.deepcopy(before)
    after["facade:A"]["materials"] = ["changed finish"]
    documents = {
        "geometry-report.json": {"synthetic": True, "real_site_accuracy": False, "source_meshes_preserved": 15, "source_geometry_changed": []},
        "roundtrip-geometry.json": {name: {"source_objects_checked": 15, "source_geometry_changed": [], "passed": True} for name in MODEL_NAMES},
        "scene-snapshots.json": {"before": before, "after": after},
        "edit-report.json": compare_snapshots(before, after, "A"),
    }
    for name, document in documents.items():
        (directory / name).write_text(json.dumps(document))
    return documents


def intercepted_source_fixture(directory):
    """Metadata fixture for testing publication control, not model validity.

    Model readers are explicitly mocked for these orchestration tests. Actual
    production model readers remain mandatory and get tested on final outputs.
    PNGs are copied from the existing public synthetic v2 render.
    """
    scene_reports(directory)
    for name in IMAGE_NAMES:
        shutil.copyfile(PUBLIC_PNG, directory / name)
    expected = expectations()
    for name in MODEL_NAMES:
        raw = unit_glb_bytes(unit_portable_document(expected[name])) if name.endswith(".glb") else b"reader-intercepted unit fixture " + name.encode()
        (directory / name).write_bytes(raw)
    (directory / "model-expectations.json").write_text(json.dumps(expected))
    images = [{"id": "image-" + name, "name": name, **file_identity(directory / name)} for name in IMAGE_NAMES[:4]]
    models = [{"name": name, "base64": base64.b64encode((directory / name).read_bytes()).decode(), **file_identity(directory / name)} for name in MODEL_NAMES if name.endswith(".glb")]
    payload = {"version": "architectural-v3", "synthetic": True, "images": images, "models": models}
    image_tags = "".join('<img id="' + item["id"] + '" src="data:image/png;base64,' + base64.b64encode((directory / item["name"]).read_bytes()).decode() + '">' for item in images)
    (directory / "viewer.html").write_text('<!doctype html><html>' + image_tags + '<script id="viewerPayload" type="application/json">' + json.dumps(payload) + '</script></html>')


def intercepted_reader(path, *, expected=None, artifact_role="output"):
    identity = file_identity(path)
    if expected:
        for key in ("bytes", "sha256"):
            if key in expected and identity[key] != expected[key]:
                raise ValidationError("Expected fixture identity differs")
    return {"status": "passed", "path": str(path), "name": Path(path).name, **identity, "artifact_role": artifact_role, "details": {}, "user_accessibility": "not_verified"}


def intercepted_archive(path, *, expected_manifest=None):
    """Unit orchestration hook; real archive bytes/CRC/inventory still checked."""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValidationError("Unit fixture archive CRC failed")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest != expected_manifest or set(archive.namelist()) - {"manifest.json"} != {record["name"] for record in manifest["files"]}:
            raise ValidationError("Unit fixture inventory differs")
    return {"status": "passed", **file_identity(path), "checks": ["unit fixture zip CRC and inventory"]}


class PackageV3Tests(unittest.TestCase):
    def test_missing_outputs_fail_without_creating_any_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(ValidationError, "not ready"):
                package_v3(directory)
            self.assertEqual(list(directory.iterdir()), [])

    def test_verify_only_rejects_passed_receipt_which_omits_required_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # A valid CRC PNG-only archive cannot stand in for the v3 models.
            shutil.copyfile(PUBLIC_PNG, directory / "aerial.png")
            manifest = _manifest([_record(directory / "aerial.png")], {}, {})
            _write_zip(directory, directory / ARCHIVE_NAME, ["aerial.png"], manifest)
            receipt = {"generator": GENERATOR, "status": "passed", "source_files": {}, "archives": [{"name": ARCHIVE_NAME}]}
            (directory / "delivery-validation-v3.json").write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValidationError, "every required v3 source"):
                verify_v3(directory)

    def test_weak_model_inventory_and_camera_claims_fail_closed(self):
        self.assertEqual(check_model_expectations(expectations()), expectations())
        changes = (
            ("objects", 1), ("objects", True), ("object_bounds", {}),
            ("faces", None), ("cameras", 1), ("camera_parameters", {}),
            ("vertices", 0),
        )
        for field, value in changes:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                records = expectations()
                records["model.blend"][field] = value
                check_model_expectations(records)

    def test_material_inventory_cannot_omit_detail_mesh_or_use_nonfinite_colors(self):
        for value in ({}, {"original:0": []}, {node: [{"source_material_name": "Fixture", "base_color_factor": [float("nan"), .3, .4, 1]}] for node in expectations()["model.glb"]["object_bounds"]}):
            records = expectations()
            records["model.glb"]["material_bindings"] = value
            with self.subTest(value_type=type(value)), self.assertRaisesRegex(ValidationError, "material|RGBA"):
                check_model_expectations(records)

    def test_actual_modifier_detail_white_materials_fail_despite_correct_mesh_inventory(self):
        expected = expectations()["model.glb"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.glb"
            document = unit_portable_document(expected)
            path.write_bytes(unit_glb_bytes(document))
            self.assertEqual(validate_portable_materials(path, expected)["mesh_nodes_checked"], 15)
            document["materials"][6]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 1]
            path.write_bytes(unit_glb_bytes(document))
            with self.assertRaisesRegex(ValidationError, "fresh native effective slots: original:6"):
                validate_portable_materials(path, expected)

    def test_portable_primitive_source_provenance_and_tint_scale_are_independently_checked(self):
        expected = expectations()["model.glb"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.glb"
            for field, value in (("source_material_name", "Different native material"), ("source_material_name", None), ("facade_tint_scale", .85)):
                document = unit_portable_document(expected)
                document["materials"][5]["extras"][field] = value
                path.write_bytes(unit_glb_bytes(document))
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    validate_portable_materials(path, expected)

    def test_portable_owner_role_metadata_cannot_change_editable_building_assignment(self):
        expected = expectations()["model.glb"]
        expected["node_owner_roles"] = {name: {"owner": "A", "role": "facade"} for name in expected["object_bounds"]}
        document = unit_portable_document(expected)
        for node in document["nodes"]:
            node["extras"] = {"owner": "A", "role": "facade"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.glb"
            path.write_bytes(unit_glb_bytes(document))
            self.assertTrue(validate_portable_materials(path, expected)["node_owner_roles_checked"])
            document["nodes"][6]["extras"]["owner"] = "B"
            path.write_bytes(unit_glb_bytes(document))
            with self.assertRaisesRegex(ValidationError, "owner/role differs"):
                validate_portable_materials(path, expected)

    def test_pbr_scalar_defaults_cannot_disguise_wrong_roughness_or_metallic(self):
        expected = expectations()["model.glb"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.glb"
            for field in ("roughnessFactor", "metallicFactor"):
                document = unit_portable_document(expected)
                document["materials"][4]["pbrMetallicRoughness"].pop(field)
                path.write_bytes(unit_glb_bytes(document))
                with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "fresh native effective slots"):
                    validate_portable_materials(path, expected)

    def test_zip_extracted_glb_materials_are_checked_after_crc_and_correct_hashes(self):
        expected = expectations()["model.glb"]
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            document = unit_portable_document(expected)
            document["materials"][8]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 1]
            (stage / "model.glb").write_bytes(unit_glb_bytes(document))
            source = _record(stage / "model.glb", expected)
            manifest = _manifest([source], {"model.glb": source}, {})
            _write_zip(stage, stage / "materials.zip", ["model.glb"], manifest)
            with zipfile.ZipFile(stage / "materials.zip") as archive:
                self.assertIsNone(archive.testzip())
            with self.assertRaisesRegex(ValidationError, "fresh native effective slots: original:8"):
                _verify_zip_portable_materials(stage / "materials.zip")

    def test_geometry_preservation_report_cannot_bypass_real_synthetic_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            docs = scene_reports(directory)
            self.assertTrue(check_semantic_reports(directory)["synthetic"])
            for value in (False, 1, "true", None):
                changed = {**docs["geometry-report.json"], "synthetic": value}
                (directory / "geometry-report.json").write_text(json.dumps(changed))
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    check_semantic_reports(directory)

    def test_declared_pass_does_not_hide_non_target_or_world_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            documents = scene_reports(directory)
            for target, field, value in (("building:B", "materials", ["different"]), ("__world__", "shader", {"strength": 2}), ("facade:A", "transform", [2, 0, 0])):
                changed = copy.deepcopy(documents["scene-snapshots.json"])
                changed["after"][target][field] = value
                (directory / "scene-snapshots.json").write_text(json.dumps(changed))
                with self.subTest(target=target, field=field), self.assertRaisesRegex(ValidationError, "independently recomputed"):
                    check_semantic_reports(directory)

    def test_old_browser_pass_cannot_describe_a_new_viewer(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            scene_reports(directory)
            (directory / "viewer.html").write_text("<!doctype html><p>current viewer</p>")
            record = {"status": "passed", "html_sha256": "0" * 64, "source_bytes_unchanged": True, "page_errors": [], "console_errors": [], "network_requests": []}
            (directory / "browser-report.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(ValidationError, "current unchanged viewer"):
                check_semantic_reports(directory)
            record["html_sha256"] = file_identity(directory / "viewer.html")["sha256"]
            (directory / "browser-report.json").write_text(json.dumps(record))
            self.assertTrue(check_semantic_reports(directory)["browser_report_matches_current_viewer"])
            native = {"status": "passed", "html_sha256": "0" * 64}
            (directory / "native-browser-report.json").write_text(json.dumps(native))
            with self.assertRaisesRegex(ValidationError, "Native browser report"):
                check_semantic_reports(directory)
            native["html_sha256"] = record["html_sha256"]
            (directory / "native-browser-report.json").write_text(json.dumps(native))
            self.assertTrue(check_semantic_reports(directory)["native_browser_report_matches_current_viewer"])

    def test_material_repair_report_cannot_hide_changes_to_frozen_images_or_native_scenes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            intercepted_source_fixture(directory)
            names = set(IMAGE_NAMES) | {"model.blend", "edited-model.blend"}
            hashes = {name: file_identity(directory / name)["sha256"] for name in names}
            report = {"frozen_sources": {"before_sha256": hashes, "after_sha256": hashes, "unchanged": True}}
            for name in ("model.glb", "edited-model.glb"):
                native = name.removesuffix(".glb") + ".blend"
                report[name] = {"passed": True, "glb_sha256": file_identity(directory / name)["sha256"], "native_source": native, "native_source_sha256": hashes[native]}
            (directory / "portable-materials-report.json").write_text(json.dumps(report))
            self.assertTrue(check_semantic_reports(directory)["portable_repair_preserves_frozen_native_and_images"])
            report["model.glb"]["glb_sha256"] = "0" * 64
            (directory / "portable-materials-report.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValidationError, "current GLB"):
                check_semantic_reports(directory)
            report["model.glb"]["glb_sha256"] = file_identity(directory / "model.glb")["sha256"]
            (directory / "portable-materials-report.json").write_text(json.dumps(report))
            (directory / "model.blend").write_bytes(b"native scene changed despite unchanged declaration")
            with self.assertRaisesRegex(ValidationError, "pre-repair hashes"):
                check_semantic_reports(directory)

    def test_real_independent_zip_reopen_checks_complete_manifest_and_payload_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            shutil.copyfile(PUBLIC_PNG, stage / "aerial.png")
            (stage / "view.html").write_text('<!doctype html><img src="aerial.png" alt="synthetic fixture">')
            records = [_record(stage / name) for name in ("aerial.png", "view.html")]
            manifest = _manifest(records, {}, {})
            first = _write_zip(stage, stage / "first.zip", ["aerial.png", "view.html"], manifest)
            second = _write_zip(stage, stage / "second.zip", ["view.html", "aerial.png"], manifest)
            self.assertEqual(first, second)
            self.assertEqual(validate_archive(stage / "first.zip", expected_manifest=manifest)["status"], "passed")
            incomplete = {**manifest, "files": records[:1]}
            _write_zip(stage, stage / "bad.zip", ["aerial.png", "view.html"], incomplete)
            with self.assertRaisesRegex(ValidationError, "account for every artifact"):
                validate_archive(stage / "bad.zip")
            changed = {**manifest, "files": [{**records[0], "sha256": "0" * 64}, records[1]]}
            _write_zip(stage, stage / "wrong-hash.zip", ["aerial.png", "view.html"], changed)
            with self.assertRaisesRegex(ValidationError, "expected sha256"):
                validate_archive(stage / "wrong-hash.zip")

    def test_source_symlinks_and_unsafe_extra_names_rejected(self):
        for name in ("../private.json", "/data/private.json", "a/b.png", "C:source.blend", "..", "", "a\\b.png"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                safe_name(name)
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "linked.png"
            link.symlink_to(PUBLIC_PNG)
            with self.assertRaises(ValidationError):
                file_identity(link)

    def test_exact_shared_bin_reassembly_preserves_source_glb_and_detects_tampering(self):
        raw = (ROOT / "baseline/aerial.glb").read_bytes()
        json_length = struct.unpack_from("<I", raw, 12)[0]
        boundary = 20 + json_length
        record = {
            "encoding": "glb_shared_bin", "name": "model.glb", "bytes": len(raw),
            "sha256": file_identity(ROOT / "baseline/aerial.glb")["sha256"],
            "header_base64": base64.b64encode(raw[:12]).decode(),
            "json_chunk_base64": base64.b64encode(raw[12:boundary]).decode(),
        }
        payload = {"shared_glb_bin_base64": base64.b64encode(raw[boundary:]).decode()}
        self.assertEqual(decode_embedded_model(record, payload), raw)
        changed = bytearray(raw[boundary:]);changed[-1] ^= 1
        payload["shared_glb_bin_base64"] = base64.b64encode(changed).decode()
        with self.assertRaises(ValidationError):
            decode_embedded_model(record, payload)

    def test_generated_receipts_and_nested_archives_cannot_be_extra_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("delivery-validation-v3.json", README_NAME, "nested.zip"):
                with self.subTest(name=name), self.assertRaisesRegex(ValidationError, "cannot be source extras"):
                    package_v3(temporary, extras=[name])

    def test_existing_user_files_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / README_NAME
            path.write_text("user-owned delivery notes")
            with self.assertRaisesRegex(ValidationError, "Refusing to overwrite"):
                _check_owned(path)
            self.assertEqual(path.read_text(), "user-owned delivery notes")
            link = Path(temporary) / "generated-link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValidationError, "symlink"):
                _check_owned(link)

    def test_postpack_failure_does_not_publish_or_change_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            intercepted_source_fixture(directory)
            before = {path.name: file_identity(path) for path in directory.iterdir()}
            with patch("scripts.package_v3.validate_file", side_effect=intercepted_reader), patch("scripts.package_v3.validate_archive", return_value={"status": "failed"}):
                with self.assertRaisesRegex(ValidationError, "Post-pack extraction failed"):
                    package_v3(directory)
            self.assertEqual(before, {path.name: file_identity(path) for path in directory.iterdir()})
            self.assertFalse((directory / ARCHIVE_NAME).exists())

    def test_embedded_model_hash_mismatch_fails_before_reader_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            intercepted_source_fixture(directory)
            records = expectations()
            models = {name: _record(directory / name, records[name]) for name in MODEL_NAMES}
            images = {name: _record(directory / name) for name in IMAGE_NAMES}
            models["model.glb"]["sha256"] = "0" * 64
            with patch("scripts.package_v3.validate_file", side_effect=intercepted_reader):
                with self.assertRaisesRegex(ValidationError, "embedded bytes differ"):
                    validate_embedded_viewer(directory / "viewer.html", models, images)

    def test_successful_orchestration_lists_all_members_and_preserves_source_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            intercepted_source_fixture(directory)
            before = {path.name: file_identity(path) for path in directory.iterdir()}
            def archive_reader(path, *, expected_manifest=None):
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(manifest, expected_manifest)
                    self.assertEqual(set(archive.namelist()) - {"manifest.json"}, {item["name"] for item in manifest["files"]})
                    for record in manifest["files"]:
                        raw = archive.read(record["name"])
                        self.assertEqual(len(raw), record["bytes"])
                return {"status": "passed", **file_identity(path), "checks": ["unit fixture zip CRC and inventory"]}
            with patch("scripts.package_v3.validate_file", side_effect=intercepted_reader), patch("scripts.package_v3.validate_archive", side_effect=archive_reader):
                result = package_v3(directory)
                reopened = verify_v3(directory)
                self.assertEqual(reopened["status"], "passed")
                self.assertEqual(len(reopened["source_files"]), len(before))
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["user_accessibility"], "not_verified")
            self.assertTrue(result["all_source_hashes_unchanged"])
            self.assertTrue(all(file_identity(directory / name) == identity for name, identity in before.items()))
            receipt = json.loads(Path(result["validation_path"]).read_text())
            self.assertEqual(receipt["generator"], GENERATOR)
            with zipfile.ZipFile(directory / ARCHIVE_NAME) as archive:
                self.assertNotIn("delivery-validation-v3.json", archive.namelist())
                self.assertIn(README_NAME, archive.namelist())

    def test_blob_limit_omits_large_native_scenes_and_splits_actual_zip_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            intercepted_source_fixture(directory)
            limit = max(1_000_000, (directory / "viewer.html").stat().st_size + 100_000)
            for name in ("model.blend", "edited-model.blend"):
                with (directory / name).open("r+b") as stream:
                    stream.truncate(limit + 1)
            before = {path.name: file_identity(path) for path in directory.iterdir()}
            with patch("scripts.package_v3.validate_file", side_effect=intercepted_reader), patch("scripts.package_v3.validate_archive", side_effect=intercepted_archive):
                result = package_v3(directory, max_blob_bytes=limit)
            self.assertEqual(set(result["omitted_native_models"]), {"model.blend", "edited-model.blend"})
            self.assertGreater(len(result["archives"]), 1)
            self.assertTrue(all(archive["bytes"] < limit for archive in result["archives"]))
            self.assertTrue(all(file_identity(directory / name) == identity for name, identity in before.items()))
            all_members = set()
            for archive in result["archives"]:
                with zipfile.ZipFile(archive["path"]) as zipped:
                    all_members.update(zipped.namelist())
            self.assertNotIn("model.blend", all_members)
            self.assertNotIn("edited-model.blend", all_members)
            self.assertIn("model.glb", all_members)
            self.assertIn("viewer.html", all_members)
            self.assertIn("model.blend", Path(result["readme_path"]).read_text())


if __name__ == "__main__":
    unittest.main()
