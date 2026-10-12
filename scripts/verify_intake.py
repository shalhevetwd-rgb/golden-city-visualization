"""Regenerate the executed intake capability receipt from real synthetic files.

From the repository root, run:
    .venv/bin/python scripts/verify_intake.py

The command runs the complete intake test module, inspects each fixture again,
and writes reports/intake-v3-checks.json atomically. Failed, skipped, incomplete,
or changed-during-verification sources never produce passed format claims.
Use --output for a separate diagnostic receipt in another Python environment.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "intake-v3"
FORMAT_FIXTURES = {".pdf": "plan.pdf", ".dxf": "plan.dxf", ".geojson": "projected.geojson",
                   ".obj": "mesh.obj", ".glb": "mesh.glb", ".png": "plan.png",
                   ".jpg": "plan.jpg", ".jpeg": "plan.jpeg"}


def file_hash(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class RecordedResult(unittest.TextTestResult):
    """Record useful test outcomes without copying paths or traceback content."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids, self.failed_checks, self.error_checks, self.skipped_checks = [], [], [], []

    def addSuccess(self, test):
        self.passed_ids.append(test.id())
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.failed_checks.append({"test": test.id(), "error_kind": error[0].__name__})
        super().addFailure(test, error)

    def addError(self, test, error):
        self.error_checks.append({"test": test.id(), "error_kind": error[0].__name__})
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.skipped_checks.append({"test": test.id(), "reason": "Required verification was skipped"})
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            destination = self.failed_checks if issubclass(error[0], test.failureException) else self.error_checks
            destination.append({"test": subtest.id(), "error_kind": error[0].__name__})
        super().addSubTest(test, subtest, error)


def raster_is_real(preview):
    from PIL import Image, ImageChops
    prefix, encoded = preview["data_url"].split(",", 1)
    if prefix != "data:image/png;base64":
        return False
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        background = Image.new(image.mode, image.size, image.getpixel((0, 0)))
        return (image.format == "PNG" and image.size == (preview["width"], preview["height"])
                and bool(ImageChops.difference(image, background).getbbox()))


def inspect_fixture(intake, extension, name, tests_valid, source_hash):
    path = FIXTURES / name
    entry = {"passed": False, "fixture": str(path.relative_to(ROOT)), "sha256": source_hash,
             "tests_valid": tests_valid, "fixture_check_passed": False}
    try:
        report = intake.inspect(path)
        extraction = report.get("extraction", {})
        entry.update(size_bytes=report["size_bytes"], supported_level=report["supported_level"],
                     capabilities=report["capabilities"], georeferencing_verified=report["georeferencing_verified"])
        checked = (report["supported"] is True and report["sha256"] == source_hash
                   and file_hash(path) == source_hash and report["georeferencing_verified"] is False
                   and report["site_assignment"] == "not_performed")
        if extension in (".png", ".jpg", ".jpeg"):
            checked = checked and report["dimensions"] == [320, 240] and raster_is_real(extraction["preview"])
        elif extension == ".pdf":
            text_checked = (extraction["page_count"] == 2 and len(extraction["pages"]) == 2
                            and "SYNTHETIC INTAKE FIXTURE" in extraction["pages"][0]["text"]
                            and "page 2" in extraction["pages"][1]["text"])
            raster_checked = raster_is_real(extraction["preview"])
            checked = checked and text_checked and raster_checked
            entry.update(backend=extraction["backend"], page_count=extraction["page_count"],
                         first_page_raster_decoded=raster_checked, text_extraction_checked=text_checked)
        elif extension == ".dxf":
            units, inventory = extraction["drawing_units"], extraction["entity_inventory"]
            checked = checked and units["insunits"] == 6 and units["scale_to_metres"] == 1
            checked = checked and inventory == {"LINE": 1, "LWPOLYLINE": 1, "CIRCLE": 1, "TEXT": 1}
            entry.update(backend=extraction["backend"], entity_types=inventory, drawing_units=units)
        elif extension == ".geojson":
            unreferenced_path = FIXTURES / "unreferenced.geojson"
            unreferenced_hash = file_hash(unreferenced_path)
            unreferenced = intake.inspect(unreferenced_path)
            negative_checked = (unreferenced["supported"] is True
                                and unreferenced["georeferencing"]["crs"] is None
                                and unreferenced["georeferencing"]["status"] == "not_declared"
                                and unreferenced["georeferencing_verified"] is False
                                and "area_m2" not in unreferenced["extraction"]["feature_inventory"][0])
            checked = (checked and report["georeferencing"]["crs"] == "EPSG:2039"
                       and extraction["feature_inventory"][0]["area_m2"] == 100 and negative_checked)
            entry.update(crs=report["georeferencing"]["crs"],
                         missing_crs_fixture_remains_unreferenced_test_passed=negative_checked,
                         additional_fixtures=[{"fixture": str(unreferenced_path.relative_to(ROOT)),
                                               "sha256": unreferenced_hash, "passed": negative_checked}])
        elif extension == ".obj":
            checked = checked and extraction["vertex_count"] == 4 and extraction["triangle_count"] == 2
        elif extension == ".glb":
            checked = (checked and report["geometry_supported"] is True
                       and extraction["mesh_primitives"][0]["vertices"] == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
                       and extraction["mesh_primitives"][0]["triangles"] == [[0, 1, 2]]
                       and len(extraction["cameras"]) == 1
                       and extraction["cameras"][0]["instances"][0]["world_matrix_column_major"][12:15] == [10, 5, 4])
            entry.update(geometry_supported=report["geometry_supported"], triangle_count=extraction["mesh_primitives"][0]["triangle_count"],
                         camera_count=len(extraction["cameras"]))
        entry["fixture_check_passed"] = bool(checked)
        entry["passed"] = bool(checked and tests_valid)
        if not entry["passed"]:
            entry["reason"] = "Required test suite or actual fixture extraction did not qualify"
    except Exception as error:
        entry.update(error_kind=type(error).__name__, reason="Actual fixture inspection failed in this runtime")
    return entry


def write_atomic(destination, receipt):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                     prefix=".intake-checks-", suffix=".json", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(receipt, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "intake-v3-checks.json",
                        help="Receipt destination; defaults to the repository report")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    module_path, test_path = ROOT / "engine" / "intake.py", ROOT / "tests" / "test_v3_intake.py"
    script_path = Path(__file__).resolve()
    tracked = [module_path, test_path, script_path] + [FIXTURES / name for name in FORMAT_FIXTURES.values()] + [FIXTURES / "unreferenced.geojson"]
    before = {path: file_hash(path) for path in tracked}
    dependency_error = None
    try:
        from engine import intake
    except ImportError as error:
        intake, dependency_error = None, type(error).__name__
    suite = unittest.defaultTestLoader.loadTestsFromName("test_v3_intake")
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=RecordedResult, verbosity=2).run(suite)
    hashes_stable = all(before[path] is not None and file_hash(path) == before[path] for path in tracked)
    tests_valid = (expected >= 30 and result.testsRun == expected and result.wasSuccessful()
                   and not result.skipped and hashes_stable)
    formats = {extension: inspect_fixture(intake, extension, name, tests_valid, before[FIXTURES/name])
               for extension, name in FORMAT_FIXTURES.items()}
    formats[".glb"]["blender_import_export_roundtrip_test_passed"] = any(
        name.endswith("test_actual_glb_blender_import_export_roundtrip_geometry_and_camera") for name in result.passed_ids)
    formats[".glb"]["large_35_mib_bin_fixture_test_passed"] = any(
        name.endswith("test_large_real_glb_buffer_with_small_mesh_is_bounded_and_inspected") for name in result.passed_ids)
    formats[".dwg"] = {"passed": False, "status": "unsupported",
                       "reason": "No lawful tested converter and real validated DWG fixture available"}
    # The second inspection pass must also preserve all original verification inputs.
    hashes_stable = hashes_stable and all(file_hash(path) == before[path] for path in tracked)
    if not hashes_stable:
        for record in formats.values():
            record["passed"] = False
            record["reason"] = "Verification code or fixture changed during this run"
    profile = "repository .venv" if Path(sys.prefix).resolve() == (ROOT/".venv").resolve() else "other Python runtime"
    receipt = {"schema": "golden-city.intake-checks.v3", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "regeneration_command": ".venv/bin/python scripts/verify_intake.py", "python_version": sys.version.split()[0],
               "runtime_profile": profile, "inspection_module_sha256": before[module_path], "test_module_sha256": before[test_path],
               "verification_script_sha256": before[script_path], "source_hashes_stable": hashes_stable,
               "runtime_dependency_error": dependency_error,
               "expected_tests": expected, "tests_run": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped),
               "test_outcomes": {"passed": result.passed_ids, "failed": result.failed_checks,
                                 "errors": result.error_checks, "skipped": result.skipped_checks},
               "formats": formats, "limits": {key: getattr(intake, constant, None) for key, constant in {
                "source_bytes": "MAX_FILE_BYTES", "json_bytes": "MAX_JSON_BYTES", "decoded_image_pixels": "MAX_IMAGE_PIXELS",
                "pdf_pages": "MAX_PDF_PAGES", "glb_vertex_count_per_primitive": "MAX_GLB_VERTICES",
                "glb_accessor_component_budget": "MAX_ACCESSOR_VALUES", "response_vertices": "MAX_RESPONSE_VERTICES",
                "response_triangles": "MAX_RESPONSE_TRIANGLES"}.items()},
               "scope": "Real-file extraction and syntax/geometry inspection only. No cadastral, scale, camera calibration, floor, terrain, or rights certification."}
    destination = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
    try:
        write_atomic(destination, receipt)
    except OSError:
        print("Could not write the verification receipt", file=sys.stderr)
        return 2
    qualified = tests_valid and hashes_stable and all(formats[extension]["passed"] for extension in FORMAT_FIXTURES)
    print(json.dumps({"receipt": str(destination.relative_to(ROOT)) if destination.is_relative_to(ROOT) else destination.name,
                      "qualified": qualified, "tests_run": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "skipped": len(result.skipped),
                      "formats": {extension: record["passed"] for extension, record in formats.items()}}, ensure_ascii=False))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
