"""Real synthetic files, malformed content, source fidelity, and GLB round trips."""
import base64
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageChops

from engine import intake

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "intake-v3"


def glb_parts():
    raw = (FIXTURES / "mesh.glb").read_bytes()
    length = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20 + length])
    binary_length = struct.unpack_from("<I", raw, 20 + length)[0]
    binary = raw[28 + length:28 + length + binary_length]
    return document, binary[:document["buffers"][0]["byteLength"]]


def make_glb(document, binary=None):
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    encoded += b" " * (-len(encoded) % 4)
    body = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    if binary is not None:
        binary += b"\x00" * (-len(binary) % 4)
        body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def minimal_pdf(page_count):
    """Write an actual standards-based uncompressed PDF without optional libraries."""
    font = 3 + 2 * page_count
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               ("<< /Type /Pages /Count %d /Kids [%s] >>" %
                (page_count, " ".join(f"{3 + 2*index} 0 R" for index in range(page_count)))).encode()]
    for index in range(page_count):
        contents = b"BT /F1 12 Tf 20 50 Td (SYNTHETIC PDF FIXTURE) Tj ET"
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2*index} 0 R >>".encode(),
            f"<< /Length {len(contents)} >>\nstream\n".encode() + contents + b"\nendstream"])
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(output)


class IntakeTests(unittest.TestCase):
    def inspect_bytes(self, raw, suffix):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ("synthetic" + suffix)
            path.write_bytes(raw)
            return intake.inspect(path)

    def inspect_glb(self, document, binary=None):
        return self.inspect_bytes(make_glb(document, binary), ".glb")

    def test_actual_format_fixtures_have_hashes_and_no_assumed_georeferencing(self):
        for name in ("plan.pdf", "plan.dxf", "projected.geojson", "unreferenced.geojson", "mesh.obj", "mesh.glb", "plan.png", "plan.jpg", "plan.jpeg"):
            with self.subTest(name=name):
                path = FIXTURES / name
                before = path.read_bytes()
                result = intake.inspect(path)
                self.assertTrue(result["supported"])
                self.assertEqual(result["sha256"], hashlib.sha256(before).hexdigest())
                self.assertEqual(result["size_bytes"], len(before))
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(result["georeferencing_verified"])
                self.assertEqual(result["site_assignment"], "not_performed")
                self.assertNotIn(str(ROOT), json.dumps(result))
                json.dumps(result, allow_nan=False)

    def test_png_and_jpeg_pixels_are_decoded_and_preview_is_a_real_image(self):
        for name in ("plan.png", "plan.jpg", "plan.jpeg"):
            result = intake.inspect(FIXTURES / name)
            self.assertEqual(result["dimensions"], [320, 240])
            preview = result["extraction"]["preview"]
            raw = base64.b64decode(preview["data_url"].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (preview["width"], preview["height"]))
                self.assertIsNotNone(ImageChops.difference(image, Image.new("RGB", image.size, image.getpixel((0, 0)))).getbbox())

    def test_image_malformed_truncated_wrong_extension_and_pixel_limit(self):
        for raw, suffix in ((b"not an image", ".png"), ((FIXTURES / "plan.png").read_bytes()[:90], ".png"),
                            ((FIXTURES / "plan.jpg").read_bytes(), ".png")):
            with self.assertRaises(ValueError):
                self.inspect_bytes(raw, suffix)
        with mock.patch.object(intake, "MAX_IMAGE_PIXELS", 1):
            with self.assertRaises(ValueError):
                intake.inspect(FIXTURES / "plan.png")

    def test_pdf_actual_pages_text_and_raster_are_extracted_without_scale(self):
        result = intake.inspect(FIXTURES / "plan.pdf")
        extraction = result["extraction"]
        self.assertEqual(extraction["page_count"], 2)
        self.assertEqual(extraction["pages"][0]["size_points"], [400, 300])
        self.assertIn("SYNTHETIC INTAKE FIXTURE", extraction["pages"][0]["text"])
        self.assertIn("page 2", extraction["pages"][1]["text"])
        self.assertFalse(result["georeferencing_verified"])
        raw = base64.b64decode(extraction["preview"]["data_url"].split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()

    @unittest.skipUnless(all(shutil.which(name) for name in ("pdfinfo", "pdftotext", "pdftoppm")), "Poppler unavailable")
    def test_actual_pdf_poppler_fallback_is_verified_independently(self):
        result = intake._pdf_poppler(FIXTURES / "plan.pdf")
        self.assertTrue(result["supported"])
        self.assertEqual(result["extraction"]["backend"], "Poppler")
        self.assertIn("page 2", result["extraction"]["pages"][1]["text"])

    def test_pdf_bad_content_and_real_page_limit(self):
        for raw in (b"not a PDF", b"%PDF-1.4\nmalformed PDF"):
            with self.assertRaises(ValueError):
                self.inspect_bytes(raw, ".pdf")
        with self.assertRaises(ValueError):
            self.inspect_bytes(minimal_pdf(intake.MAX_PDF_PAGES + 1), ".pdf")
        result = self.inspect_bytes(minimal_pdf(1), ".pdf")
        self.assertIn("SYNTHETIC PDF FIXTURE", result["extraction"]["pages"][0]["text"])

    def test_dxf_actual_entities_units_and_coordinates(self):
        result = intake.inspect(FIXTURES / "plan.dxf")
        extraction = result["extraction"]
        self.assertEqual(result["supported_level"], "parsed_cad_subset")
        self.assertEqual(extraction["entity_inventory"], {"LINE": 1, "LWPOLYLINE": 1, "CIRCLE": 1, "TEXT": 1})
        self.assertEqual(extraction["drawing_units"], {"insunits": 6, "name": "metres", "scale_to_metres": 1})
        line = next(entity for entity in extraction["entities"] if entity["entity_type"] == "LINE")
        self.assertEqual(line["coordinates"], [[0, 0, 0], [10, 0, 0]])
        polyline = next(entity for entity in extraction["entities"] if entity["entity_type"] == "LWPOLYLINE")
        self.assertTrue(polyline["closed"])
        self.assertEqual(polyline["coordinates"], [[0, 0, 0], [10, 0, 0], [10, 8, 0], [0, 8, 0]])
        self.assertFalse(extraction["coordinate_transform_performed"])

    def changed_dxf(self, change):
        lines = (FIXTURES / "plan.dxf").read_text().splitlines()
        pairs = [(int(lines[index]), lines[index+1]) for index in range(0, len(lines), 2)]
        change(pairs)
        return "\n".join(str(code) + "\n" + value for code, value in pairs).encode() + b"\n"

    def test_dxf_units_missing_are_not_assumed_to_be_metres(self):
        def change(pairs):
            position = next(index for index, pair in enumerate(pairs) if pair == (9, "$INSUNITS"))
            del pairs[position:position+2]
        result = self.inspect_bytes(self.changed_dxf(change), ".dxf")
        self.assertEqual(result["extraction"]["drawing_units"]["insunits"], 0)
        self.assertIsNone(result["extraction"]["drawing_units"]["scale_to_metres"])
        self.assertFalse(result["georeferencing_verified"])

    def test_dxf_curved_polyline_is_explicitly_not_treated_as_straight(self):
        def change(pairs):
            position = next(index for index, pair in enumerate(pairs) if pair == (0, "LWPOLYLINE"))
            following = next(index for index in range(position+1, len(pairs)) if pairs[index][0] == 0)
            pairs.insert(following, (42, "0.5"))
        result = self.inspect_bytes(self.changed_dxf(change), ".dxf")
        self.assertEqual(result["status"], "partial")
        self.assertIn("LWPOLYLINE: curved bulge", result["extraction"]["unsupported_entities"])
        self.assertFalse(any(entity["entity_type"] == "LWPOLYLINE" for entity in result["extraction"]["entities"]))

    def test_dxf_malformed_and_binary_not_advertised(self):
        for raw in (b"notDXF", b"0\nSECTION\n2\nENTITIES\n0\nLINE\n0\nEOF\n",
                    (FIXTURES / "plan.dxf").read_bytes().replace(b"EOF", b"MISSING")):
            with self.assertRaises(ValueError):
                self.inspect_bytes(raw, ".dxf")
        result = self.inspect_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00placeholder", ".dxf")
        self.assertFalse(result["supported"])

    def test_geojson_explicit_metric_crs_and_missing_crs_are_distinct(self):
        projected = intake.inspect(FIXTURES / "projected.geojson")
        self.assertEqual(projected["georeferencing"]["crs"], "EPSG:2039")
        self.assertTrue(projected["georeferencing"]["projected_in_metres"])
        self.assertEqual(projected["extraction"]["feature_inventory"][0]["area_m2"], 100)
        absent = intake.inspect(FIXTURES / "unreferenced.geojson")
        self.assertIsNone(absent["georeferencing"]["crs"])
        self.assertEqual(absent["georeferencing"]["status"], "not_declared")
        self.assertNotIn("area_m2", absent["extraction"]["feature_inventory"][0])

    def test_geojson_nonmetric_crs_and_invalid_topology_or_nonfinite_rejected(self):
        document = json.loads((FIXTURES / "projected.geojson").read_text())
        document["crs"] = "EPSG:2227"
        result = self.inspect_bytes(json.dumps(document).encode(), ".geojson")
        self.assertFalse(result["georeferencing"]["projected_in_metres"])
        self.assertNotIn("area_m2", result["extraction"]["feature_inventory"][0])
        for crs in ("EPSG:doesnotexist", True, {"type": "link"}):
            altered = copy.deepcopy(document);altered["crs"] = crs
            with self.assertRaises(ValueError):
                self.inspect_bytes(json.dumps(altered).encode(), ".geojson")
        for ring in ([[0,0],[1,1],[1,0],[0,1],[0,0]], [[0,0],[1,0],[1,1],[0,1]]):
            altered = copy.deepcopy(document);altered["features"][0]["geometry"]["coordinates"] = [ring]
            with self.assertRaises(ValueError):
                self.inspect_bytes(json.dumps(altered).encode(), ".geojson")
        with self.assertRaises(ValueError):
            self.inspect_bytes(b'{"type":"Point","coordinates":[NaN,1]}', ".geojson")

    def test_obj_actual_planar_quad_and_concave_face_triangulation(self):
        result = intake.inspect(FIXTURES / "mesh.obj")
        self.assertEqual(result["extraction"]["vertex_count"], 4)
        self.assertEqual(result["extraction"]["triangle_count"], 2)
        concave = b"v 0 0 0\nv 2 0 0\nv 2 1 0\nv 1 1 0\nv 1 2 0\nv 0 2 0\nf 1 2 3 4 5 6\n"
        extraction = self.inspect_bytes(concave, ".obj")["extraction"]
        area = sum(np.linalg.norm(np.cross(np.array(extraction["vertices"][triangle[1]]) - extraction["vertices"][triangle[0]],
                  np.array(extraction["vertices"][triangle[2]]) - extraction["vertices"][triangle[0]]))/2
                  for triangle in extraction["triangles"])
        self.assertAlmostEqual(area, 3)

    def test_obj_negative_texture_normal_indices_and_external_materials_not_loaded(self):
        raw = b"mtllib ../not-loaded.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nvn 0 0 1\nf -3/-3/1 -2/-2/1 -1/-1/1\n"
        result = self.inspect_bytes(raw, ".obj")
        self.assertEqual(result["extraction"]["triangles"], [[0,1,2]])
        self.assertEqual(result["extraction"]["external_material_references_not_loaded"], 1)

    def test_obj_strict_indices_nan_unexecuted_directives_and_nonplanar_face(self):
        prefix = b"v 0 0 0\nv 1 0 0\nv 0 1 0\n"
        for line in (b"f 0 1 2\n", b"f 1 2 4\n", b"f -4 -2 -1\n", b"f 1/1 2/1 3/1\n",
                     b"f 1 1 2\n", b"call forbidden.obj\n", b"csh echo nope\n"):
            with self.assertRaises(ValueError):
                self.inspect_bytes(prefix + line, ".obj")
        for raw in (b"v nan 0 0\nf 1 1 1\n", b"v 0 0 0\nv 1 0 0\nv 1 1 1\nv 0 1 0\nf 1 2 3 4\n"):
            with self.assertRaises(ValueError):
                self.inspect_bytes(raw, ".obj")

    def test_glb_actual_buffers_geometry_camera_and_parented_pose(self):
        result = intake.inspect(FIXTURES / "mesh.glb")
        self.assertTrue(result["geometry_supported"])
        extraction = result["extraction"]
        mesh = extraction["mesh_primitives"][0]
        self.assertEqual(mesh["vertices"], [[0,0,0],[1,0,0],[0,1,0]])
        self.assertEqual(mesh["triangles"], [[0,1,2]])
        self.assertEqual(mesh["bounds"], [[0,0,0],[1,1,0]])
        self.assertEqual(extraction["cameras"][0]["intrinsics"]["yfov"], 1)
        self.assertEqual(extraction["cameras"][0]["instances"][0]["world_matrix_column_major"][12:15], [10,5,4])
        self.assertFalse(extraction["cameras"][0]["calibration_verified"])

    def test_glb_invalid_header_chunks_and_buffer_bounds(self):
        raw = (FIXTURES / "mesh.glb").read_bytes()
        for altered in (raw[:7], b"NOPE" + raw[4:], raw[:-4], raw[:8] + struct.pack("<I", 100) + raw[12:]):
            with self.assertRaises(ValueError):
                self.inspect_bytes(altered, ".glb")
        document, binary = glb_parts()
        for mutation in (lambda d: d["bufferViews"][0].update(byteOffset=1000),
                         lambda d: d["bufferViews"][0].update(byteStride=2),
                         lambda d: d["accessors"][0].update(count=4),
                         lambda d: d["buffers"][0].update(byteLength=10000),
                         lambda d: d["accessors"][0].update(max=[5,5,5])):
            altered = copy.deepcopy(document);mutation(altered)
            with self.assertRaises(ValueError):
                self.inspect_glb(altered, binary)

    def test_glb_actual_bad_indices_nonfinite_components_camera_and_hierarchy(self):
        document, binary = glb_parts()
        altered = bytearray(binary);struct.pack_into("<H", altered, 76, 3)
        with self.assertRaises(ValueError):
            self.inspect_glb(document, bytes(altered))
        altered = bytearray(binary);struct.pack_into("<f", altered, 0, float("nan"))
        with self.assertRaises(ValueError):
            self.inspect_glb(document, bytes(altered))
        for mutation in (lambda d: d["cameras"][0]["perspective"].update(znear=0),
                         lambda d: d["nodes"][1].update(children=[0]),
                         lambda d: d["nodes"][1].update(rotation=[0,0,0,0]),
                         lambda d: d["meshes"][0]["primitives"][0].update(material=0)):
            altered = copy.deepcopy(document);mutation(altered)
            with self.assertRaises(ValueError):
                self.inspect_glb(altered, binary)

    def test_glb_external_buffers_are_never_fetched_or_called_geometry_complete(self):
        document, _ = glb_parts()
        document["buffers"][0]["uri"] = "https://example.invalid/never-fetch.bin"
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network access forbidden")):
            result = self.inspect_glb(document)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["geometry_supported"])
        self.assertFalse(result["extraction"]["external_references_loaded"])

    def test_glb_inline_buffer_and_interleaved_accessors_preserve_geometry(self):
        document, binary = glb_parts()
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary).decode()
        inline = self.inspect_glb(document)
        original = intake.inspect(FIXTURES / "mesh.glb")
        self.assertEqual(inline["extraction"]["mesh_primitives"][0]["geometry_sha256"], original["extraction"]["mesh_primitives"][0]["geometry_sha256"])
        document, binary = glb_parts()
        positions = struct.unpack_from("<9f", binary, 0);normals = struct.unpack_from("<9f", binary, 36)
        interleaved = b"".join(struct.pack("<6f", *positions[index:index+3], *normals[index:index+3]) for index in range(0,9,3)) + binary[72:]
        document["bufferViews"] = [{"buffer":0,"byteOffset":0,"byteLength":72,"byteStride":24},
                                    {"buffer":0,"byteOffset":72,"byteLength":6}]
        document["accessors"][0].update(bufferView=0, byteOffset=0)
        document["accessors"][1].update(bufferView=0, byteOffset=12)
        document["accessors"][2].update(bufferView=1)
        result = self.inspect_glb(document, interleaved)
        self.assertEqual(result["extraction"]["mesh_primitives"][0]["geometry_sha256"], original["extraction"]["mesh_primitives"][0]["geometry_sha256"])

    def test_glb_sparse_accessor_values_and_duplicate_sparse_indices(self):
        document, original = glb_parts()
        binary = bytes([0,1,2,0]) + original[:36] + original[72:78]
        document["buffers"][0]["byteLength"] = len(binary)
        document["bufferViews"] = [{"buffer":0,"byteOffset":0,"byteLength":3},
                                    {"buffer":0,"byteOffset":4,"byteLength":36},
                                    {"buffer":0,"byteOffset":40,"byteLength":6}]
        document["accessors"] = [{"componentType":5126,"type":"VEC3","count":3,"min":[0,0,0],"max":[1,1,0],
                                  "sparse":{"count":3,"indices":{"bufferView":0,"componentType":5121},"values":{"bufferView":1}}},
                                  {"componentType":5123,"type":"SCALAR","count":3,"bufferView":2}]
        document["meshes"][0]["primitives"][0] = {"attributes":{"POSITION":0},"indices":1}
        result = self.inspect_glb(document, binary)
        self.assertEqual(result["extraction"]["mesh_primitives"][0]["vertices"], [[0,0,0],[1,0,0],[0,1,0]])
        with self.assertRaises(ValueError):
            self.inspect_glb(document, bytes([0,0,2,0]) + binary[4:])

    def test_glb_matrix_accessor_column_padding_is_validated(self):
        document, binary = glb_parts()
        binary += b"\x00\x00" + b"".join(struct.pack("<3H", *range(start,start+3)) + b"\x00\x00" for start in (1,4,7))
        document["buffers"][0]["byteLength"] = len(binary)
        document["bufferViews"].append({"buffer":0,"byteOffset":80,"byteLength":24})
        document["accessors"].append({"bufferView":3,"componentType":5123,"type":"MAT3","count":1})
        result = self.inspect_glb(document, binary)
        self.assertEqual(result["extraction"]["accessors"][3]["actual_raw_bounds"][0], list(range(1,10)))

    def test_glb_unsupported_required_compression_is_inventory_only(self):
        document, binary = glb_parts()
        document.update(extensionsUsed=["KHR_draco_mesh_compression"], extensionsRequired=["KHR_draco_mesh_compression"])
        result = self.inspect_glb(document, binary)
        self.assertTrue(result["supported"])
        self.assertFalse(result["geometry_supported"])
        self.assertNotIn("mesh_vertices", result["capabilities"])

    def test_glb_semantic_morph_material_and_empty_light_references_are_strict(self):
        document, binary = glb_parts()
        for mutation in (lambda d: d["nodes"][0].update(extensions={"KHR_lights_punctual":{"light":0}}),
                         lambda d: d["meshes"][0]["primitives"][0].update(targets=[{"POSITION":99}]),
                         lambda d: d["meshes"][0]["primitives"][0]["attributes"].update(TANGENT=1),
                         lambda d: d.update(materials=[{"pbrMetallicRoughness":{"baseColorTexture":{"index":0}}}])):
            altered = copy.deepcopy(document);mutation(altered)
            with self.assertRaises(ValueError):
                self.inspect_glb(altered, binary)

    def test_glb_response_coordinate_cap_does_not_change_actual_counts_and_hashes(self):
        document, binary = glb_parts()
        document["meshes"][0]["primitives"] *= 3
        with mock.patch.object(intake, "MAX_RESPONSE_VERTICES", 4):
            result = self.inspect_glb(document, binary)
        primitives = result["extraction"]["mesh_primitives"]
        self.assertEqual(len(primitives), 3)
        self.assertIn("vertices", primitives[0])
        self.assertNotIn("vertices", primitives[1])
        self.assertEqual(primitives[0]["geometry_sha256"], primitives[1]["geometry_sha256"])
        self.assertEqual([p["triangle_count"] for p in primitives], [1,1,1])
        self.assertTrue(all(p["decoded"] for p in primitives))

    def test_large_real_glb_buffer_with_small_mesh_is_bounded_and_inspected(self):
        document, binary = glb_parts()
        binary += b"\x00" * (35*1024*1024 - len(binary))
        document["buffers"][0]["byteLength"] = len(binary)
        result = self.inspect_glb(document, binary)
        self.assertGreater(result["size_bytes"], 35*1024*1024)
        self.assertTrue(result["geometry_supported"])
        self.assertEqual(result["extraction"]["mesh_primitives"][0]["triangle_count"], 1)

    def test_size_regular_file_and_symlink_guards_have_safe_errors(self):
        with self.assertRaises(ValueError):
            intake.inspect(None)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for path in (directory, directory / "missing.pdf"):
                with self.assertRaises(ValueError) as failure:
                    intake.inspect(path)
                self.assertNotIn(temporary, str(failure.exception))
            empty = directory / "empty.png";empty.write_bytes(b"")
            with self.assertRaises(ValueError):intake.inspect(empty)
            large = directory / "large.glb"
            with large.open("wb") as handle:handle.truncate(intake.MAX_FILE_BYTES + 1)
            with self.assertRaises(ValueError):intake.inspect(large)
            link = directory / "link.png";link.symlink_to(FIXTURES / "plan.png")
            with self.assertRaises(ValueError):intake.inspect(link)

    def test_existing_script_import_signature_can_inspect_geojson_from_engine_directory(self):
        process = subprocess.run([sys.executable, "-c", "from intake import inspect; import sys,json; print(json.dumps(inspect(sys.argv[1])))",
                                  str(FIXTURES / "projected.geojson")], cwd=ROOT / "engine",
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        result = json.loads(process.stdout)
        self.assertTrue(result["supported"])
        self.assertEqual(result["extraction"]["feature_inventory"][0]["area_m2"], 100)

    def test_dwg_and_unknown_formats_are_not_advertised(self):
        result = self.inspect_bytes(b"AC1032 - intentionally unparsed test payload", ".dwg")
        self.assertFalse(result["supported"])
        self.assertIn("DWG", result["missing_requirements"][0])
        self.assertFalse(self.inspect_bytes(b"<svg>unparsed</svg>", ".svg")["supported"])

    @unittest.skipUnless(shutil.which("blender"), "Blender unavailable for round-trip verification")
    def test_actual_glb_blender_import_export_roundtrip_geometry_and_camera(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary);output = temporary / "roundtrip.glb";script = temporary / "roundtrip.py"
            script.write_text("import bpy,sys\nargs=sys.argv[sys.argv.index('--')+1:]\nbpy.ops.wm.read_factory_settings(use_empty=True)\nbpy.ops.import_scene.gltf(filepath=args[0])\nbpy.ops.export_scene.gltf(filepath=args[1],export_format='GLB',export_cameras=True,export_yup=True)\n")
            process = subprocess.run([shutil.which("blender"), "-b", "-t", "1", "--python-exit-code", "1", "--python", str(script), "--", str(FIXTURES / "mesh.glb"), str(output)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, check=False)
            self.assertEqual(process.returncode, 0, process.stdout.decode(errors="replace")[-2000:])
            before, after = intake.inspect(FIXTURES / "mesh.glb"), intake.inspect(output)
            def world_triangles(result):
                triangles = []
                for node in result["extraction"]["node_inventory"]:
                    if node["mesh"] is None:continue
                    matrix = np.array(node["world_matrix_column_major"]).reshape(4,4).T
                    for primitive in result["extraction"]["mesh_primitives"]:
                        if primitive["mesh_index"] != node["mesh"]:continue
                        points = [(matrix @ np.array(vertex+[1]))[:3] for vertex in primitive["vertices"]]
                        triangles.extend(tuple(sorted(tuple(round(float(value),5) for value in points[index]) for index in triangle)) for triangle in primitive["triangles"])
                return sorted(triangles)
            self.assertEqual(world_triangles(before), world_triangles(after))
            self.assertEqual(len(after["extraction"]["cameras"]), 1)
            self.assertAlmostEqual(after["extraction"]["cameras"][0]["intrinsics"]["yfov"], 1, places=5)
            self.assertEqual(after["extraction"]["cameras"][0]["instances"][0]["world_matrix_column_major"][12:15], [10,5,4])


if __name__ == "__main__":
    unittest.main()
