"""Bounded real-file inspection; no actions, external assets or site inference.

Parsed coordinates, units, poses and text are source data, not survey validation,
camera calibration, building schedules or permission to publish the source.
"""
from collections import Counter
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile

from PIL import Image, ImageOps

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 120_000
MAX_VERTICES = 100_000
MAX_TRIANGLES = 200_000
MAX_ENTITIES = 20_000
MAX_ACCESSOR_VALUES = 15_000_000
MAX_GLB_VERTICES = 500_000
MAX_GLB_TRIANGLES = 1_000_000
MAX_RESPONSE_VERTICES = 20_000
MAX_RESPONSE_TRIANGLES = 40_000
PREVIEW_SIZE = 768


class IntakeError(ValueError):
    """Safe user-visible validation failure, without a server file path."""


def _fail(message):
    raise IntakeError(message)


def _finite(value, label):
    if type(value) not in (int, float):
        _fail(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        _fail(f"{label} must be a finite number")
    if not math.isfinite(result):
        _fail(f"{label} must be a finite number")
    return result


def _integer(value, label, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        _fail(f"Invalid {label}")
    return value


def _vector(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        _fail(f"{label} must have {length} components")
    return [_finite(component, label) for component in value]


def _json(raw):
    if len(raw) > MAX_JSON_BYTES:
        _fail("JSON exceeds the inspection size limit")
    def reject_constant(_):
        _fail("Non-finite JSON numbers are not accepted")
    try:
        result = json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("Invalid UTF-8 JSON")
    pending, count = [(result, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 32 or count > 600_000:
            _fail("JSON nesting or item count exceeds the inspection limit")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif type(value) is float and not math.isfinite(value):
            _fail("Non-finite JSON numbers are not accepted")
    return result


def _bounds(points):
    if not points:
        return None
    return [[min(point[axis] for point in points) for axis in range(len(points[0]))],
            [max(point[axis] for point in points) for axis in range(len(points[0]))]]


def _preview(image):
    copy = ImageOps.exif_transpose(image).convert("RGB")
    copy.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
    output = io.BytesIO()
    copy.save(output, format="PNG", optimize=True)
    return {"mime_type": "image/png", "width": copy.width, "height": copy.height,
            "data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")}


def _decode_image(raw, allowed_formats=("PNG", "JPEG")):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if image.format not in allowed_formats:
                _fail("Image content does not match the supported format")
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                _fail("Image exceeds the decoded pixel limit")
            file_format = image.format
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            return {"dimensions": [width, height], "image_format": file_format,
                    "color_mode": image.mode, "preview": _preview(image)}
    except IntakeError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        _fail("Image decoding failed")


def _inspect_image(raw, suffix):
    result = _decode_image(raw)
    if result["image_format"] != ("PNG" if suffix == ".png" else "JPEG"):
        _fail("Image extension does not match its content")
    return {"supported": True, "supported_level": "decoded_image",
            "capabilities": ["image_decode", "raster_preview"],
            "dimensions": result["dimensions"], "extraction": result,
            "meaning": "Source pixels only; camera, scale, CRS and site placement were not inferred"}


def _inspect_pdf(path, raw):
    if not raw.startswith(b"%PDF-"):
        _fail("PDF signature is missing")
    try:
        import pymupdf
    except ImportError:
        return _pdf_poppler(path)
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if document.needs_pass:
                return {"supported": False, "supported_level": "password_required", "capabilities": [],
                        "extraction": {"encrypted": True}, "missing_requirements": ["A locally unlocked PDF is required"]}
            count = document.page_count
            if not 0 < count <= MAX_PDF_PAGES:
                _fail("PDF page count exceeds the inspection limit")
            pages, remaining = [], MAX_TEXT_CHARS
            for index in range(count):
                page = document.load_page(index)
                width, height = float(page.rect.width), float(page.rect.height)
                if not 0 < width <= 10_000 or not 0 < height <= 10_000:
                    _fail("PDF page dimensions exceed the inspection limit")
                text = page.get_text("text")
                allowance = min(12_000, remaining)
                pages.append({"page": index + 1, "size_points": [width, height],
                              "text": text[:allowance], "text_truncated": len(text) > allowance})
                remaining -= min(len(text), allowance)
            first = document.load_page(0)
            scale = min(1.0, PREVIEW_SIZE / max(first.rect.width, first.rect.height))
            pixmap = first.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            preview = _decode_image(pixmap.tobytes("png"), ("PNG",))["preview"]
            return {"supported": True, "supported_level": "parsed_document",
                    "capabilities": ["pdf_pages", "pdf_text", "raster_preview"],
                    "extraction": {"backend": "PyMuPDF", "backend_version": pymupdf.VersionBind,
                                   "page_count": count, "pages": pages, "preview": preview,
                                   "parser_repair_reported": bool(document.is_repaired),
                                   "embedded_attachments_not_extracted": document.embfile_count()},
                    "meaning": "Document text and first-page pixels; no plan geometry or scale inferred"}
    except IntakeError:
        raise
    except Exception:
        _fail("PDF parsing or rasterization failed")


def _tool(command, output_limit=2 * 1024 * 1024):
    # Fixed argv; no shell. Poppler does not execute PDF JavaScript or actions.
    def limits():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
            resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        except (ImportError, OSError, ValueError):
            pass
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.run(command, stdout=output, stderr=errors, timeout=30,
                                     check=False, preexec_fn=limits)
        except (OSError, subprocess.TimeoutExpired):
            _fail("PDF inspection tool is unavailable or exceeded its time limit")
        if process.returncode:
            _fail("PDF inspection tool rejected the document")
        if output.tell() > output_limit:
            _fail("PDF inspection output exceeds the limit")
        output.seek(0)
        return output.read()


def _pdf_poppler(path):
    tools = {name: shutil.which(name) for name in ("pdfinfo", "pdftotext", "pdftoppm")}
    if not all(tools.values()):
        return {"supported": False, "supported_level": "missing_parser", "capabilities": [],
                "missing_requirements": ["PyMuPDF or the existing Poppler PDF tools"]}
    information = _tool([tools["pdfinfo"], str(path)]).decode("utf-8", errors="replace")
    match = re.search(r"^Pages:\s*(\d+)\s*$", information, re.MULTILINE)
    if not match:
        _fail("PDF page count could not be read")
    count = int(match.group(1))
    if not 0 < count <= MAX_PDF_PAGES:
        _fail("PDF page count exceeds the inspection limit")
    boxes = _tool([tools["pdfinfo"], "-f", "1", "-l", str(count), "-box", str(path)]).decode("utf-8", errors="replace")
    page_sizes = {}
    for index, width, height in re.findall(r"^Page\s+(\d+)\s+size:\s*([\d.]+)\s+x\s+([\d.]+)\s+pts", boxes, re.MULTILINE):
        page_sizes[int(index)] = [float(width), float(height)]
    default = re.search(r"^Page size:\s*([\d.]+)\s+x\s+([\d.]+)\s+pts", boxes, re.MULTILINE)
    if default and count == 1:
        page_sizes[1] = [float(default.group(1)), float(default.group(2))]
    if len(page_sizes) != count:
        _fail("PDF page dimensions could not be read")
    if any(not 0 < width <= 10_000 or not 0 < height <= 10_000 for width, height in page_sizes.values()):
        _fail("PDF page dimensions exceed the inspection limit")
    with tempfile.TemporaryDirectory(prefix="golden-intake-pdf-") as temporary:
        temporary = Path(temporary)
        text_path = temporary / "text.txt"
        _tool([tools["pdftotext"], "-layout", "-enc", "UTF-8", "-f", "1", "-l", str(count), str(path), str(text_path)])
        if text_path.stat().st_size > 8 * 1024 * 1024:
            _fail("PDF text exceeds the extraction limit")
        texts = text_path.read_text(encoding="utf-8").split("\f")
        pages, remaining = [], MAX_TEXT_CHARS
        for index in range(count):
            text = texts[index] if index < len(texts) else ""
            allowance = min(12_000, remaining)
            pages.append({"page": index + 1, "size_points": page_sizes[index + 1],
                          "text": text[:allowance], "text_truncated": len(text) > allowance})
            remaining -= min(len(text), allowance)
        raster = temporary / "first-page"
        _tool([tools["pdftoppm"], "-f", "1", "-singlefile", "-scale-to", str(PREVIEW_SIZE),
               "-png", str(path), str(raster)])
        preview = _decode_image(raster.with_suffix(".png").read_bytes(), ("PNG",))["preview"]
    return {"supported": True, "supported_level": "parsed_document",
            "capabilities": ["pdf_pages", "pdf_text", "raster_preview"],
            "extraction": {"backend": "Poppler", "page_count": count, "pages": pages,
                           "preview": preview, "actions_and_attachments": "not_executed_or_extracted"},
            "meaning": "Document text and first-page pixels; no plan geometry or scale inferred"}


def _inspect_geojson(raw):
    from pyproj import CRS
    from pyproj.exceptions import CRSError
    from shapely.geometry import shape
    from shapely.errors import ShapelyError
    try:
        from spatial import projected_metric_crs
    except ModuleNotFoundError as error:
        if error.name != "spatial":
            raise
        # Script callers run with engine/ on sys.path. Load only this repository's
        # fixed trusted helper, never a path or module named by the uploaded file.
        import importlib.util
        spec = importlib.util.spec_from_file_location("golden_city_spatial", Path(__file__).resolve().parents[1] / "spatial.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        projected_metric_crs = helper.projected_metric_crs
    document = _json(raw)
    if not isinstance(document, dict):
        _fail("GeoJSON must be an object")
    kind = document.get("type")
    geometry_types = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection")
    if kind == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list) or len(features) > 2_000:
            _fail("GeoJSON feature count exceeds the limit or is invalid")
    elif kind == "Feature":
        features = [document]
    elif kind in geometry_types:
        features = [{"type": "Feature", "properties": {}, "geometry": document}]
    else:
        _fail("Supported GeoJSON requires geometry, Feature, or FeatureCollection")
    declared = document.get("crs")
    if isinstance(declared, dict):
        if declared.get("type") != "name" or not isinstance(declared.get("properties"), dict):
            _fail("GeoJSON CRS must be an explicit name")
        declared = declared["properties"].get("name")
    if declared is not None and (not isinstance(declared, str) or not declared.strip()):
        _fail("GeoJSON CRS declaration is invalid")
    crs, metric = None, False
    if declared is not None:
        try:
            crs = CRS.from_user_input(declared).to_string()
        except (ValueError, TypeError, CRSError):
            _fail("GeoJSON CRS declaration is invalid")
        try:
            projected_metric_crs(declared)
            metric = True
        except ValueError:
            pass
    inventories, coordinate_count = [], 0
    def check_geometry(geometry, depth=0):
        nonlocal coordinate_count
        if not isinstance(geometry, dict) or depth > 16 or geometry.get("type") not in geometry_types:
            _fail("Invalid GeoJSON geometry")
        if geometry.get("type") == "GeometryCollection":
            children = geometry.get("geometries")
            if not isinstance(children, list):
                _fail("Invalid GeoJSON geometry collection")
            for child in children:
                check_geometry(child, depth + 1)
            return
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list):
            _fail("GeoJSON coordinates must be arrays")
        pending = [coordinates]
        while pending:
            item = pending.pop()
            if not isinstance(item, list) or not item:
                _fail("Empty or invalid GeoJSON coordinates")
            if isinstance(item[0], list):
                pending.extend(item)
            else:
                if len(item) not in (2, 3):
                    _fail("GeoJSON positions require two or three coordinates")
                for value in item:
                    _finite(value, "GeoJSON coordinate")
                coordinate_count += 1
                if coordinate_count > MAX_VERTICES:
                    _fail("GeoJSON coordinate count exceeds the limit")
        rings = coordinates if geometry.get("type") == "Polygon" else (
            [ring for polygon in coordinates for ring in polygon] if geometry.get("type") == "MultiPolygon" else [])
        if any(len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
            _fail("GeoJSON polygon rings must be closed")
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            _fail("Invalid GeoJSON feature")
        properties = feature.get("properties")
        if properties is not None and not isinstance(properties, dict):
            _fail("GeoJSON feature properties must be an object or null")
        geometry = feature.get("geometry")
        if geometry is None:
            inventories.append({"index": index, "geometry_type": None, "bounds": None})
            continue
        check_geometry(geometry)
        try:
            parsed = shape(geometry)
        except (ValueError, TypeError, KeyError, ShapelyError):
            _fail("Invalid GeoJSON geometry")
        if parsed.is_empty or not parsed.is_valid:
            _fail("GeoJSON geometry is empty or has invalid topology")
        inventory = {"index": index, "geometry_type": parsed.geom_type, "bounds": list(parsed.bounds)}
        if metric and parsed.geom_type in ("Polygon", "MultiPolygon"):
            inventory["area_m2"] = parsed.area
        inventories.append(inventory)
    return {"supported": True, "supported_level": "validated_geojson",
            "capabilities": ["geojson_geometry", "declared_crs_validation"], "features": len(features),
            "georeferencing": {"status": "declared_metric" if metric else ("declared_nonmetric" if crs else "not_declared"),
                               "crs": crs, "projected_in_metres": metric, "source_verified": False},
            "extraction": {"document": document, "feature_inventory": inventories,
                           "coordinate_count": coordinate_count, "coordinate_transform_performed": False},
            "meaning": "Source geometry and explicit CRS; survey accuracy and site assignment remain unverified"}


def _triangulate_face(indices, vertices):
    from shapely.geometry import Polygon, Point
    points = [vertices[index] for index in indices]
    normal = [0.0, 0.0, 0.0]
    for point, following in zip(points, points[1:] + points[:1]):
        normal[0] += (point[1] - following[1]) * (point[2] + following[2])
        normal[1] += (point[2] - following[2]) * (point[0] + following[0])
        normal[2] += (point[0] - following[0]) * (point[1] + following[1])
    length = math.sqrt(sum(component * component for component in normal))
    if not math.isfinite(length) or length <= 1e-12:
        _fail("Mesh face is degenerate")
    normal = [component / length for component in normal]
    span = max(max(point[axis] for point in points) - min(point[axis] for point in points) for axis in range(3))
    for point in points:
        distance = sum(normal[axis] * (point[axis] - points[0][axis]) for axis in range(3))
        if abs(distance) > max(1e-8, span * 1e-7):
            _fail("Polygon face is not planar")
    omit = max(range(3), key=lambda axis: abs(normal[axis]))
    flat = [[point[axis] for axis in range(3) if axis != omit] for point in points]
    polygon = Polygon(flat)
    if not polygon.is_valid or polygon.area <= 1e-12:
        _fail("Mesh face is self-intersecting or degenerate")
    sign = 1 if sum(flat[i][0] * flat[(i + 1) % len(flat)][1] - flat[(i + 1) % len(flat)][0] * flat[i][1]
                    for i in range(len(flat))) > 0 else -1
    remaining, triangles = list(range(len(indices))), []
    while len(remaining) > 3:
        found = False
        for position in range(len(remaining)):
            previous, current, following = remaining[position - 1], remaining[position], remaining[(position + 1) % len(remaining)]
            a, b, c = flat[previous], flat[current], flat[following]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            ear = Polygon([a, b, c])
            if cross * sign <= 1e-12 or any(ear.covers(Point(flat[other])) for other in remaining
                                                   if other not in (previous, current, following)):
                continue
            triangles.append([indices[previous], indices[current], indices[following]])
            del remaining[position]
            found = True
            break
        if not found:
            _fail("Polygon could not be triangulated without changing its geometry")
    triangles.append([indices[index] for index in remaining])
    return triangles


def _inspect_obj(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        _fail("OBJ must be UTF-8 text")
    vertices, textures, normals, triangles, objects = [], [], [], [], []
    face_count, external_materials, ignored = 0, 0, Counter()
    def index(token, length, label):
        try:
            number = int(token)
        except (ValueError, OverflowError):
            _fail(f"OBJ {label} index is invalid")
        result = number - 1 if number > 0 else length + number
        if number == 0 or not 0 <= result < length:
            _fail(f"OBJ {label} index is out of range")
        return result
    for line in text.splitlines():
        fields = line.partition("#")[0].split()
        if not fields:
            continue
        directive, values = fields[0], fields[1:]
        if directive == "v":
            if len(values) not in (3, 4):
                _fail("OBJ vertices require xyz with optional homogeneous w")
            point = [_finite(float(value), "OBJ vertex") for value in values]
            if len(point) == 4:
                if point[3] == 0:
                    _fail("OBJ homogeneous w must be nonzero")
                point = [_finite(component / point[3], "OBJ vertex") for component in point[:3]]
            vertices.append(point)
            if len(vertices) > MAX_VERTICES:
                _fail("OBJ vertex count exceeds the limit")
        elif directive in ("vt", "vn"):
            if (directive == "vn" and len(values) != 3) or (directive == "vt" and not 1 <= len(values) <= 3):
                _fail("OBJ texture or normal dimensions are invalid")
            collection = textures if directive == "vt" else normals
            collection.append([_finite(float(value), "OBJ attribute") for value in values])
            if len(collection) > MAX_VERTICES:
                _fail("OBJ attribute count exceeds the limit")
        elif directive == "f":
            if not 3 <= len(values) <= 256:
                _fail("OBJ faces require 3 to 256 vertices")
            indices = []
            for value in values:
                parts = value.split("/")
                if len(parts) > 3 or not parts[0]:
                    _fail("OBJ face reference is invalid")
                indices.append(index(parts[0], len(vertices), "vertex"))
                if len(parts) >= 2 and parts[1]:
                    index(parts[1], len(textures), "texture")
                if len(parts) == 3 and parts[2]:
                    index(parts[2], len(normals), "normal")
                if (len(parts) == 2 and not parts[1]) or (len(parts) == 3 and not parts[2]):
                    _fail("OBJ texture or normal reference is empty")
            if len(set(indices)) != len(indices):
                _fail("OBJ face repeats a vertex")
            triangles.extend(_triangulate_face(indices, vertices))
            face_count += 1
            if len(triangles) > MAX_TRIANGLES:
                _fail("OBJ triangle count exceeds the limit")
        elif directive in ("o", "g"):
            objects.append({"name": " ".join(values)[:120], "first_triangle": len(triangles)})
        elif directive == "mtllib":
            external_materials += len(values)
        elif directive in ("usemtl", "s"):
            pass
        elif directive in ("l", "p"):
            ignored[directive] += 1
        else:
            _fail("OBJ contains an unsupported directive; only mesh geometry is inspected")
    if not vertices or not triangles:
        _fail("OBJ requires vertices and at least one valid mesh face")
    return {"supported": True, "supported_level": "parsed_mesh",
            "capabilities": ["obj_vertices", "obj_triangles", "strict_indices"],
            "extraction": {"vertices": vertices, "triangles": triangles, "vertex_count": len(vertices),
                           "triangle_count": len(triangles), "source_face_count": face_count,
                           "texture_coordinate_count": len(textures), "normal_count": len(normals),
                           "objects": objects, "bounds": _bounds(vertices),
                           "external_material_references_not_loaded": external_materials,
                           "nonmesh_records_not_imported": dict(ignored), "units": "not_declared"},
            "meaning": "Source mesh coordinates; units, CRS, camera and site placement were not inferred"}


def _inspect_dxf(raw):
    if raw.startswith(b"AutoCAD Binary DXF"):
        return {"supported": False, "supported_level": "unsupported_binary_dxf", "capabilities": [],
                "missing_requirements": ["This intake validates an ASCII DXF subset; binary DXF is not advertised"]}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        _fail("This DXF intake requires UTF-8/ASCII text")
    lines = text.splitlines()
    if len(lines) % 2 or len(lines) > 1_000_000:
        _fail("DXF group-code/value pairs are malformed or exceed the limit")
    pairs = []
    for index in range(0, len(lines), 2):
        try:
            code = int(lines[index].strip())
        except ValueError:
            _fail("DXF group code is invalid")
        if not 0 <= code <= 1071:
            _fail("DXF group code is out of range")
        pairs.append((code, lines[index + 1].strip()))
    if not pairs or pairs[-1] != (0, "EOF"):
        _fail("DXF must end with its EOF record")
    sections, current, inside, index = {}, [], None, 0
    while index < len(pairs) - 1:
        code, value = pairs[index]
        if (code, value) == (0, "SECTION"):
            if inside is not None or index + 1 >= len(pairs) or pairs[index + 1][0] != 2:
                _fail("DXF section header is invalid")
            inside, current = pairs[index + 1][1], []
            if inside in sections:
                _fail("DXF repeats a section")
            index += 2
            continue
        if (code, value) == (0, "ENDSEC"):
            if inside is None:
                _fail("DXF section terminator is invalid")
            sections[inside], inside = current, None
        elif inside is None:
            if code != 999:
                _fail("DXF record appears outside a section")
        else:
            current.append((code, value))
        index += 1
    if inside is not None or "ENTITIES" not in sections:
        _fail("DXF requires a closed ENTITIES section")
    units, header = 0, sections.get("HEADER", [])
    for position, pair in enumerate(header):
        if pair == (9, "$INSUNITS"):
            if position + 1 >= len(header) or header[position + 1][0] != 70:
                _fail("DXF insertion units declaration is malformed")
            units = int(header[position + 1][1])
            if not 0 <= units <= 24:
                _fail("DXF insertion units are out of range")
    validator = "strict ASCII subset"
    try:
        import ezdxf
    except ImportError:
        pass
    else:
        try:
            ezdxf.read(io.StringIO(text))
            validator = "ezdxf " + ezdxf.__version__ + " plus bounded ASCII extraction"
        except Exception:
            _fail("DXF document structure was rejected by ezdxf")
    records, entity, values = [], None, []
    for code, value in sections["ENTITIES"]:
        if code == 0:
            if entity is not None:
                records.append((entity, values))
            entity, values = value, []
        elif entity is None:
            _fail("DXF entity has no type record")
        else:
            values.append((code, value))
    if entity is not None:
        records.append((entity, values))
    if len(records) > MAX_ENTITIES:
        _fail("DXF entity count exceeds the limit")
    inventory, extracted, unsupported = Counter(), [], Counter()
    for kind, values in records:
        inventory[kind] += 1
        groups = {}
        for code, value in values:
            groups.setdefault(code, []).append(value)
        def number(code, default=None):
            if code not in groups:
                if default is None:
                    _fail(f"DXF {kind} requires coordinate group {code}")
                return default
            if len(groups[code]) != 1:
                _fail(f"DXF {kind} repeats a scalar group")
            return _finite(float(groups[code][0]), "DXF numeric value")
        def point(start):
            return [number(start), number(start + 10), number(start + 20, 0.0)]
        extrusion = [number(210, 0.0), number(220, 0.0), number(230, 1.0)]
        if kind in ("LWPOLYLINE", "CIRCLE", "ARC") and extrusion != [0.0, 0.0, 1.0]:
            unsupported[kind + ": nondefault OCS extrusion"] += 1
            continue
        entry = {"entity_type": kind, "layer": groups.get(8, ["0"])[0][:120]}
        if kind == "LINE":
            entry["coordinates"] = [point(10), point(11)]
        elif kind == "POINT":
            entry["coordinates"] = point(10)
        elif kind in ("CIRCLE", "ARC"):
            entry.update(center=point(10), radius=number(40))
            if entry["radius"] <= 0:
                _fail("DXF arc/circle radius must be positive")
            if kind == "ARC":
                entry.update(start_angle_degrees=number(50), end_angle_degrees=number(51))
        elif kind == "LWPOLYLINE":
            if any(_finite(float(value), "DXF bulge") != 0 for value in groups.get(42, [])):
                unsupported["LWPOLYLINE: curved bulge"] += 1
                continue
            coordinates, vertex = [], None
            for code, value in values:
                if code == 10:
                    if vertex is not None:
                        if len(vertex) != 2:
                            _fail("DXF polyline vertex is missing its y coordinate")
                        coordinates.append(vertex + [number(38, 0.0)])
                    vertex = [_finite(float(value), "DXF polyline x")]
                elif code == 20:
                    if vertex is None or len(vertex) != 1:
                        _fail("DXF polyline coordinate ordering is invalid")
                    vertex.append(_finite(float(value), "DXF polyline y"))
            if vertex is not None:
                if len(vertex) != 2:
                    _fail("DXF polyline vertex is missing its y coordinate")
                coordinates.append(vertex + [number(38, 0.0)])
            if number(90) != len(coordinates) or not 2 <= len(coordinates) <= MAX_VERTICES:
                _fail("DXF polyline vertex count is invalid")
            flags = number(70, 0)
            if flags != int(flags):
                _fail("DXF polyline flags are invalid")
            entry.update(coordinates=coordinates, closed=bool(int(flags) & 1))
        elif kind == "3DFACE":
            coordinates = [point(10), point(11), point(12)]
            fourth = point(13) if 13 in groups else coordinates[-1]
            if fourth != coordinates[-1]:
                coordinates.append(fourth)
            entry.update(coordinates=coordinates, triangles=_triangulate_face(list(range(len(coordinates))), coordinates))
        elif kind in ("TEXT", "MTEXT"):
            entry.update(text=("".join(groups.get(3, [])) + "".join(groups.get(1, [])))[:12_000], insertion=point(10))
        else:
            unsupported[kind] += 1
            continue
        extracted.append(entry)
    names = {0: "unitless", 1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres", 7: "kilometres"}
    conversions = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0}
    return {"supported": True, "supported_level": "parsed_cad_subset",
            "capabilities": ["dxf_ascii_entities", "declared_drawing_units"],
            "status": "partial" if unsupported else "inspected",
            "extraction": {"backend": validator, "entity_inventory": dict(inventory), "entities": extracted,
                           "unsupported_entities": dict(unsupported),
                           "drawing_units": {"insunits": units, "name": names.get(units, "other_declared_DXF_unit"),
                                             "scale_to_metres": conversions.get(units)},
                           "coordinate_transform_performed": False, "block_references_expanded": False},
            "meaning": "ASCII CAD subset in declared drawing units; no CRS or site placement inferred"}


_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
               5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_ACCESSOR_TYPES = {"SCALAR": (1, 1), "VEC2": (2, 1), "VEC3": (3, 1), "VEC4": (4, 1),
                   "MAT2": (2, 2), "MAT3": (3, 3), "MAT4": (4, 4)}


class _GLTF:
    """Read embedded glTF buffers and validate every declared accessor component."""
    def __init__(self, document, binary):
        self.document = document
        self.arrays = {}
        for name in ("buffers", "bufferViews", "accessors", "meshes", "nodes", "cameras", "scenes",
                     "images", "textures", "samplers", "materials", "skins", "animations"):
            value = document.get(name, [])
            if not isinstance(value, list) or len(value) > MAX_ENTITIES or any(not isinstance(item, dict) for item in value):
                _fail(f"glTF {name} must be a bounded array of objects")
            self.arrays[name] = value
        self.buffers, self.buffer_inventory = [], []
        total_bytes = 0
        for index, item in enumerate(self.arrays["buffers"]):
            size = _integer(item.get("byteLength"), "glTF buffer length", 1, MAX_FILE_BYTES)
            total_bytes += size
            if total_bytes > MAX_FILE_BYTES:
                _fail("glTF decoded buffers exceed the file limit")
            uri, data, origin = item.get("uri"), None, "external_reference_not_loaded"
            if uri is None:
                if index != 0 or binary is None:
                    _fail("GLB buffer requires its embedded BIN chunk")
                if not size <= len(binary) <= size + 3 or any(binary[size:]):
                    _fail("GLB BIN chunk length or padding does not match its buffer")
                data, origin = binary[:size], "embedded_BIN"
            elif not isinstance(uri, str):
                _fail("glTF buffer URI must be text")
            elif uri.startswith("data:"):
                prefix, separator, encoded = uri.partition(",")
                if not separator or prefix not in ("data:application/octet-stream;base64", "data:application/gltf-buffer;base64"):
                    _fail("glTF inline buffer encoding is unsupported")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error):
                    _fail("glTF inline buffer is invalid base64")
                if len(data) != size:
                    _fail("glTF inline buffer length does not match its declaration")
                origin = "inline_data_URI"
            self.buffers.append(data)
            self.buffer_inventory.append({"index": index, "byte_length": size, "source": origin,
                                          "available": data is not None,
                                          "sha256": hashlib.sha256(data).hexdigest() if data is not None else None})
        if binary is not None and not self.arrays["buffers"]:
            _fail("GLB has a BIN chunk without a declared buffer")
        self.views = []
        self.unsupported_geometry_extensions = set()
        for item in self.arrays["bufferViews"]:
            buffer_index = self.reference("buffers", item.get("buffer"))
            offset = _integer(item.get("byteOffset", 0), "bufferView offset")
            length = _integer(item.get("byteLength"), "bufferView length", 1)
            if offset + length > self.arrays["buffers"][buffer_index]["byteLength"]:
                _fail("glTF bufferView extends beyond its buffer")
            stride = item.get("byteStride")
            if stride is not None and (_integer(stride, "bufferView stride", 4, 252) % 4):
                _fail("glTF bufferView stride must be a multiple of four")
            if item.get("target") not in (None, 34962, 34963):
                _fail("glTF bufferView target is invalid")
            extensions = item.get("extensions", {})
            if not isinstance(extensions, dict):
                _fail("glTF bufferView extensions must be an object")
            compressed = "EXT_meshopt_compression" in extensions
            if compressed:
                self.unsupported_geometry_extensions.add("EXT_meshopt_compression")
            self.views.append({"buffer": buffer_index, "offset": offset, "length": length,
                               "stride": stride, "compressed": compressed})
        self.accessors, self.accessor_inventory, self.cache = [], [], {}
        component_budget = 0
        for index, item in enumerate(self.arrays["accessors"]):
            component_type, shape_type = item.get("componentType"), item.get("type")
            if type(component_type) is not int or component_type not in _COMPONENTS or shape_type not in _ACCESSOR_TYPES:
                _fail("glTF accessor component/type is unsupported or invalid")
            rows, columns = _ACCESSOR_TYPES[shape_type]
            component_format, component_size = _COMPONENTS[component_type]
            column_size = rows * component_size
            if columns > 1:
                column_size = (column_size + 3) // 4 * 4
            offsets = [column * column_size + row * component_size for column in range(columns) for row in range(rows)]
            element_size, components = column_size * columns, rows * columns
            count = _integer(item.get("count"), "accessor count", 1, MAX_GLB_TRIANGLES * 3)
            component_budget += count * components
            if component_budget > MAX_ACCESSOR_VALUES:
                _fail("glTF accessor components exceed the inspection limit")
            normalized = item.get("normalized", False)
            if type(normalized) is not bool or (normalized and component_type not in (5120, 5121, 5122, 5123)):
                _fail("glTF normalized accessor declaration is invalid")
            offset = _integer(item.get("byteOffset", 0), "accessor offset")
            view_index = item.get("bufferView")
            available, stride, absolute = True, element_size, 0
            if view_index is not None:
                self.reference("bufferViews", view_index)
                view = self.views[view_index]
                stride = view["stride"] or element_size
                absolute = view["offset"] + offset
                if stride < element_size or stride % component_size or offset % component_size or absolute % component_size:
                    _fail("glTF accessor alignment/stride is invalid")
                if offset + (count - 1) * stride + element_size > view["length"]:
                    _fail("glTF accessor extends beyond its bufferView")
                available = self.buffers[view["buffer"]] is not None and not view["compressed"]
            elif offset != 0:
                _fail("glTF accessor without a bufferView cannot have a byte offset")
            sparse_values = {}
            sparse = item.get("sparse")
            if sparse is not None:
                if not isinstance(sparse, dict):
                    _fail("glTF sparse accessor must be an object")
                sparse_count = _integer(sparse.get("count"), "sparse count", 1, min(count, MAX_VERTICES))
                indices, values = sparse.get("indices"), sparse.get("values")
                if not isinstance(indices, dict) or not isinstance(values, dict):
                    _fail("glTF sparse accessor requires indices and values")
                index_type = indices.get("componentType")
                if index_type not in (5121, 5123, 5125):
                    _fail("glTF sparse indices require an unsigned integer type")
                index_size, index_format = _COMPONENTS[index_type][1], _COMPONENTS[index_type][0]
                index_data, index_start = self.sparse_region(indices, sparse_count * index_size, index_size)
                value_data, value_start = self.sparse_region(values, sparse_count * element_size, component_size)
                available = available and index_data is not None and value_data is not None
                if index_data is not None and value_data is not None:
                    previous = -1
                    for entry in range(sparse_count):
                        sparse_index = struct.unpack_from("<" + index_format, index_data, index_start + entry * index_size)[0]
                        if not previous < sparse_index < count:
                            _fail("glTF sparse indices must be increasing, unique, and in range")
                        previous = sparse_index
                        sparse_values[sparse_index] = tuple(struct.unpack_from("<" + component_format, value_data,
                            value_start + entry * element_size + component_offset)[0] for component_offset in offsets)
            self.accessors.append({"view": view_index, "absolute": absolute, "stride": stride, "offsets": offsets,
                                   "count": count, "components": components, "component_type": component_type,
                                   "format": component_format, "normalized": normalized, "type": shape_type,
                                   "available": available, "sparse": sparse_values})
            minimum = [_finite(value, "accessor minimum") for value in item["min"]] if "min" in item else None
            maximum = [_finite(value, "accessor maximum") for value in item["max"]] if "max" in item else None
            if (minimum is not None and len(minimum) != components) or (maximum is not None and len(maximum) != components):
                _fail("glTF accessor bounds have the wrong number of components")
            if minimum is not None and maximum is not None and any(a > b for a, b in zip(minimum, maximum)):
                _fail("glTF accessor bounds are reversed")
            actual_min, actual_max = None, None
            if available:
                for row in self.iter_raw(index):
                    for value in row:
                        _finite(value, "glTF accessor component")
                    if actual_min is None:
                        actual_min, actual_max = list(row), list(row)
                    else:
                        actual_min = [min(a, b) for a, b in zip(actual_min, row)]
                        actual_max = [max(a, b) for a, b in zip(actual_max, row)]
                for declared, actual in ((minimum, actual_min), (maximum, actual_max)):
                    if declared is not None and any(abs(a - b) > max(1e-6, abs(b) * 1e-6) for a, b in zip(declared, actual)):
                        _fail("glTF accessor bounds do not match the actual buffer values")
            self.accessor_inventory.append({"index": index, "count": count, "type": shape_type,
                                           "component_type": component_type, "normalized": normalized,
                                           "available": available, "actual_raw_bounds": [actual_min, actual_max] if available else None})

    def reference(self, collection, value):
        return _integer(value, collection + " reference", 0, len(self.arrays[collection]) - 1)

    def sparse_region(self, item, size, alignment):
        index = self.reference("bufferViews", item.get("bufferView"))
        view = self.views[index]
        offset = _integer(item.get("byteOffset", 0), "sparse offset")
        if view["stride"] is not None or offset + size > view["length"] or (offset + view["offset"]) % alignment:
            _fail("glTF sparse buffer bounds/alignment are invalid")
        data = None if view["compressed"] else self.buffers[view["buffer"]]
        return data, view["offset"] + offset

    def iter_raw(self, index):
        accessor = self.accessors[index]
        view = self.views[accessor["view"]] if accessor["view"] is not None else None
        data = self.buffers[view["buffer"]] if view is not None else None
        for row_index in range(accessor["count"]):
            if row_index in accessor["sparse"]:
                yield accessor["sparse"][row_index]
            elif view is None:
                yield (0,) * accessor["components"]
            else:
                start = accessor["absolute"] + row_index * accessor["stride"]
                yield tuple(struct.unpack_from("<" + accessor["format"], data, start + offset)[0]
                            for offset in accessor["offsets"])

    def read(self, index):
        self.reference("accessors", index)
        accessor = self.accessors[index]
        if not accessor["available"]:
            return None
        if index not in self.cache:
            rows = list(self.iter_raw(index))
            if accessor["normalized"]:
                divisor = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[accessor["component_type"]]
                rows = [tuple(max(-1, value / divisor) for value in row) for row in rows]
            self.cache[index] = rows
        return self.cache[index]


def _node_inventory(reader):
    import numpy as np
    nodes, parents, local = reader.arrays["nodes"], {}, []
    for index, node in enumerate(nodes):
        for field, collection in (("mesh", "meshes"), ("camera", "cameras"), ("skin", "skins")):
            if field in node:
                reader.reference(collection, node[field])
        children = node.get("children", [])
        if not isinstance(children, list) or len(children) != len(set(children)):
            _fail("glTF node children must be a unique array")
        for child in children:
            reader.reference("nodes", child)
            if child == index or child in parents:
                _fail("glTF nodes cannot reference themselves or have multiple parents")
            parents[child] = index
        if "matrix" in node:
            if any(field in node for field in ("translation", "rotation", "scale")):
                _fail("glTF node cannot declare both matrix and TRS")
            values = _vector(node["matrix"], 16, "glTF node matrix")
            if any(abs(values[position]) > 1e-8 for position in (3, 7, 11)) or abs(values[15] - 1) > 1e-8:
                _fail("glTF node matrix must be affine")
            matrix = np.array(values).reshape(4, 4).T
        else:
            translation = _vector(node.get("translation", [0, 0, 0]), 3, "node translation")
            scale = _vector(node.get("scale", [1, 1, 1]), 3, "node scale")
            x, y, z, w = _vector(node.get("rotation", [0, 0, 0, 1]), 4, "node rotation")
            if abs(x*x + y*y + z*z + w*w - 1) > 1e-4:
                _fail("glTF node rotation must be a unit quaternion")
            rotation = np.array([[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                                 [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                                 [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]])
            matrix = np.eye(4)
            matrix[:3, :3] = rotation @ np.diag(scale)
            matrix[:3, 3] = translation
        local.append(matrix)
    world, inventory = {}, []
    for index, node in enumerate(nodes):
        chain, seen, cursor = [], set(), index
        while cursor not in world:
            if cursor in seen or len(chain) >= 128:
                _fail("glTF node hierarchy is cyclic or exceeds the depth limit")
            seen.add(cursor)
            chain.append(cursor)
            if cursor not in parents:
                break
            cursor = parents[cursor]
        for child in reversed(chain):
            world[child] = world[parents[child]] @ local[child] if child in parents else local[child]
        if not np.isfinite(world[index]).all():
            _fail("glTF node transform exceeds finite numeric bounds")
        inventory.append({"index": index, "name": str(node.get("name", ""))[:120], "mesh": node.get("mesh"),
                          "camera": node.get("camera"), "parent": parents.get(index),
                          "world_matrix_column_major": world[index].T.reshape(16).tolist()})
    for scene in reader.arrays["scenes"]:
        roots = scene.get("nodes", [])
        if not isinstance(roots, list):
            _fail("glTF scene nodes must be an array")
        for root in roots:
            reader.reference("nodes", root)
            if root in parents:
                _fail("glTF scene root is also a child node")
    if "scene" in reader.document:
        reader.reference("scenes", reader.document["scene"])
    return inventory


def _camera_inventory(reader, nodes):
    cameras = []
    for index, camera in enumerate(reader.arrays["cameras"]):
        kind = camera.get("type")
        values = camera.get(kind)
        if kind not in ("perspective", "orthographic") or not isinstance(values, dict):
            _fail("glTF camera type or intrinsics are invalid")
        if kind == "perspective":
            near, fov = _finite(values.get("znear"), "camera near"), _finite(values.get("yfov"), "camera field of view")
            if near <= 0 or not 0 < fov < math.pi:
                _fail("glTF perspective camera intrinsics are invalid")
            if "aspectRatio" in values and _finite(values["aspectRatio"], "camera aspect") <= 0:
                _fail("glTF camera aspect ratio must be positive")
        else:
            near = _finite(values.get("znear"), "camera near")
            if near < 0 or _finite(values.get("xmag"), "camera xmag") <= 0 or _finite(values.get("ymag"), "camera ymag") <= 0:
                _fail("glTF orthographic camera intrinsics are invalid")
        if "zfar" in values and _finite(values["zfar"], "camera far") <= near:
            _fail("glTF camera far plane must exceed its near plane")
        if kind == "orthographic" and "zfar" not in values:
            _fail("glTF orthographic camera requires a far plane")
        cameras.append({"index": index, "name": str(camera.get("name", ""))[:120], "type": kind,
                        "intrinsics": dict(values),
                        "instances": [{"node": node["index"], "world_matrix_column_major": node["world_matrix_column_major"]}
                                      for node in nodes if node["camera"] == index],
                        "calibration_verified": False})
    return cameras


def _inspect_glb(raw):
    if len(raw) < 20:
        _fail("GLB header is truncated")
    magic, version, total = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or total != len(raw):
        _fail("GLB magic, version or declared file length is invalid")
    cursor, chunks, document, binary = 12, [], None, None
    while cursor < len(raw):
        if cursor + 8 > len(raw):
            _fail("GLB chunk header is truncated")
        length, kind = struct.unpack_from("<II", raw, cursor)
        cursor += 8
        if length % 4 or cursor + length > len(raw):
            _fail("GLB chunk length/alignment is invalid")
        payload = raw[cursor:cursor + length]
        if not chunks and kind != 0x4E4F534A:
            _fail("GLB first chunk must be JSON")
        if kind == 0x4E4F534A:
            if document is not None:
                _fail("GLB has more than one JSON chunk")
            document = _json(payload)
        elif kind == 0x004E4942:
            if binary is not None:
                _fail("GLB has more than one BIN chunk")
            binary = payload
        chunks.append({"type_hex": f"{kind:08x}", "byte_length": length})
        cursor += length
    if not isinstance(document, dict) or not isinstance(document.get("asset"), dict) or document["asset"].get("version") != "2.0":
        _fail("GLB requires a glTF 2.0 asset declaration")
    if document["asset"].get("minVersion", "2.0") != "2.0":
        _fail("GLB requires an unsupported glTF minimum version")
    reader = _GLTF(document, binary)
    used, required = document.get("extensionsUsed", []), document.get("extensionsRequired", [])
    if not isinstance(used, list) or not isinstance(required, list) or any(not isinstance(item, str) for item in used + required):
        _fail("glTF extensions must be arrays of names")
    if not set(required).issubset(used):
        _fail("glTF required extension is not declared in extensionsUsed")
    material_extensions = {"KHR_materials_unlit", "KHR_texture_transform", "KHR_materials_ior",
        "KHR_materials_clearcoat", "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_sheen",
        "KHR_materials_specular", "KHR_materials_anisotropy", "KHR_materials_iridescence",
        "KHR_materials_emissive_strength", "KHR_materials_dispersion"}
    unsupported = set(required) - material_extensions - {"KHR_mesh_quantization", "KHR_lights_punctual"}
    primitives, response_vertices, response_triangles = [], 0, 0
    for mesh_index, mesh in enumerate(reader.arrays["meshes"]):
        entries = mesh.get("primitives")
        if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTITIES:
            _fail("glTF mesh requires a bounded nonempty primitive array")
        for primitive_index, primitive in enumerate(entries):
            if not isinstance(primitive, dict) or not isinstance(primitive.get("attributes"), dict) or "POSITION" not in primitive["attributes"]:
                _fail("glTF primitive requires POSITION attributes")
            position_id = reader.reference("accessors", primitive["attributes"]["POSITION"])
            position = reader.accessors[position_id]
            allowed_types = (5126, 5120, 5121, 5122, 5123) if "KHR_mesh_quantization" in used else (5126,)
            if position["type"] != "VEC3" or position["component_type"] not in allowed_types or position["count"] > MAX_GLB_VERTICES:
                _fail("glTF POSITION accessor dimensions/type/count are invalid")
            for semantic, accessor_id in primitive["attributes"].items():
                reader.reference("accessors", accessor_id)
                attribute = reader.accessors[accessor_id]
                if attribute["count"] != position["count"]:
                    _fail("glTF primitive attributes have different vertex counts")
                dimensions = {"NORMAL": "VEC3", "TANGENT": "VEC4"}
                expected = "VEC2" if semantic.startswith("TEXCOORD_") else (
                    "VEC4" if semantic.startswith(("JOINTS_", "WEIGHTS_")) else dimensions.get(semantic))
                if expected is not None and attribute["type"] != expected:
                    _fail("glTF vertex attribute dimensions do not match their semantic")
                if semantic.startswith("COLOR_") and attribute["type"] not in ("VEC3", "VEC4"):
                    _fail("glTF vertex colors must have three or four components")
            targets = primitive.get("targets", [])
            if not isinstance(targets, list) or len(targets) > 64:
                _fail("glTF morph targets must be a bounded array")
            for target in targets:
                if not isinstance(target, dict):
                    _fail("glTF morph target must be an object")
                for semantic, accessor_id in target.items():
                    accessor = reader.accessors[reader.reference("accessors", accessor_id)]
                    if semantic not in ("POSITION", "NORMAL", "TANGENT") or accessor["type"] != "VEC3" or accessor["count"] != position["count"]:
                        _fail("glTF morph target accessor is invalid")
            if "material" in primitive:
                reader.reference("materials", primitive["material"])
            mode = _integer(primitive.get("mode", 4), "primitive mode", 0, 6)
            extensions = primitive.get("extensions", {})
            if not isinstance(extensions, dict):
                _fail("glTF primitive extensions must be an object")
            if "KHR_draco_mesh_compression" in extensions:
                unsupported.add("KHR_draco_mesh_compression")
            blocked = bool(unsupported or reader.unsupported_geometry_extensions)
            vertices = None if blocked else reader.read(position_id)
            indices = None
            if "indices" in primitive:
                index_id = reader.reference("accessors", primitive["indices"])
                accessor = reader.accessors[index_id]
                if accessor["type"] != "SCALAR" or accessor["component_type"] not in (5121, 5123, 5125) or accessor["normalized"]:
                    _fail("glTF primitive indices require unsigned scalar integers")
                decoded = reader.read(index_id) if not blocked else None
                indices = [row[0] for row in decoded] if decoded is not None else None
                if indices is not None and any(value >= position["count"] for value in indices):
                    _fail("glTF primitive index exceeds its POSITION count")
            elif vertices is not None:
                indices = list(range(len(vertices)))
            triangles = []
            if indices is not None:
                if mode == 4:
                    if len(indices) % 3:
                        _fail("glTF triangle indices must occur in groups of three")
                    triangles = [indices[index:index + 3] for index in range(0, len(indices), 3)]
                elif mode == 5:
                    triangles = [[indices[index + (index % 2)], indices[index + (1 - index % 2)], indices[index + 2]]
                                 for index in range(len(indices) - 2)]
                elif mode == 6:
                    triangles = [[indices[0], indices[index], indices[index + 1]] for index in range(1, len(indices) - 1)]
                if len(triangles) > MAX_GLB_TRIANGLES:
                    _fail("glTF triangle count exceeds the inspection limit")
            item = {"mesh_index": mesh_index, "primitive_index": primitive_index,
                    "mesh_name": str(mesh.get("name", ""))[:120], "mode": mode,
                    "vertex_count": position["count"], "decoded": vertices is not None and indices is not None,
                    "triangle_count": len(triangles) if vertices is not None else None,
                    "bounds": _bounds(vertices) if vertices is not None else None,
                    "morph_target_count": len(targets), "deformation_applied": False}
            if vertices is not None:
                geometry_hash = hashlib.sha256()
                for vertex in vertices:
                    geometry_hash.update(struct.pack("<3d", *vertex))
                for triangle in triangles:
                    geometry_hash.update(struct.pack("<3I", *triangle))
                item["geometry_sha256"] = geometry_hash.hexdigest()
                if (len(vertices) <= 4_000 and len(triangles) <= 8_000 and
                        response_vertices + len(vertices) <= MAX_RESPONSE_VERTICES and
                        response_triangles + len(triangles) <= MAX_RESPONSE_TRIANGLES):
                    item.update(vertices=[list(vertex) for vertex in vertices], triangles=triangles)
                    response_vertices += len(vertices)
                    response_triangles += len(triangles)
                else:
                    item["coordinates_omitted_from_response"] = "Full buffer values validated; output bounded by preview limits"
            primitives.append(item)
    nodes = _node_inventory(reader)
    cameras = _camera_inventory(reader, nodes)
    light_inventory = []
    extension_data = document.get("extensions", {})
    if not isinstance(extension_data, dict):
        _fail("glTF top-level extensions must be an object")
    light_data = extension_data.get("KHR_lights_punctual", {})
    if not isinstance(light_data, dict) or not isinstance(light_data.get("lights", []), list):
        _fail("glTF punctual lights must be a bounded array")
    lights = light_data.get("lights", [])
    if len(lights) > MAX_ENTITIES:
        _fail("glTF light count exceeds the limit")
    light_nodes = {}
    for node_index, node in enumerate(reader.arrays["nodes"]):
        node_extensions = node.get("extensions", {})
        if not isinstance(node_extensions, dict):
            _fail("glTF node extensions must be an object")
        reference = node_extensions.get("KHR_lights_punctual")
        if reference is not None:
            if not isinstance(reference, dict):
                _fail("glTF node light reference is invalid")
            light_index = _integer(reference.get("light"), "punctual light reference", 0, len(lights)-1)
            light_nodes.setdefault(light_index, []).append(node_index)
    for index, light in enumerate(lights):
        if not isinstance(light, dict) or light.get("type") not in ("point", "spot", "directional"):
            _fail("glTF punctual light type is invalid")
        color = _vector(light.get("color", [1, 1, 1]), 3, "light color")
        intensity = _finite(light.get("intensity", 1), "light intensity")
        if any(value < 0 or value > 1 for value in color) or intensity < 0:
            _fail("glTF light color/intensity is invalid")
        if "range" in light and _finite(light["range"], "light range") <= 0:
            _fail("glTF light range must be positive")
        if light["type"] == "spot":
            spot = light.get("spot", {})
            if not isinstance(spot, dict):
                _fail("glTF spot light requires valid cone parameters")
            inner = _finite(spot.get("innerConeAngle", 0), "spot inner angle")
            outer = _finite(spot.get("outerConeAngle", math.pi / 4), "spot outer angle")
            if not 0 <= inner < outer <= math.pi / 2:
                _fail("glTF spot light cone is invalid")
        light_inventory.append({"index": index, "type": light["type"], "color": color,
                                "intensity": intensity, "instances": light_nodes.get(index, [])})
    for skin in reader.arrays["skins"]:
        joints = skin.get("joints")
        if not isinstance(joints, list) or not joints or len(joints) != len(set(joints)):
            _fail("glTF skin joints must be a nonempty unique array")
        for joint in joints:
            reader.reference("nodes", joint)
        if "skeleton" in skin:
            reader.reference("nodes", skin["skeleton"])
        if "inverseBindMatrices" in skin:
            accessor = reader.accessors[reader.reference("accessors", skin["inverseBindMatrices"])]
            if accessor["type"] != "MAT4" or accessor["component_type"] != 5126 or accessor["count"] < len(joints):
                _fail("glTF skin inverse bind matrices are invalid")
    for animation in reader.arrays["animations"]:
        samplers, channels = animation.get("samplers"), animation.get("channels")
        if not isinstance(samplers, list) or not samplers or not isinstance(channels, list) or not channels:
            _fail("glTF animation requires samplers and channels")
        for sampler in samplers:
            if not isinstance(sampler, dict) or sampler.get("interpolation", "LINEAR") not in ("LINEAR", "STEP", "CUBICSPLINE"):
                _fail("glTF animation sampler is invalid")
            input_id = reader.reference("accessors", sampler.get("input"))
            reader.reference("accessors", sampler.get("output"))
            accessor = reader.accessors[input_id]
            if accessor["type"] != "SCALAR" or accessor["component_type"] != 5126:
                _fail("glTF animation input must be scalar float time")
            if accessor["available"]:
                times = [row[0] for row in reader.iter_raw(input_id)]
                if times[0] < 0 or any(first >= following for first, following in zip(times, times[1:])):
                    _fail("glTF animation times must be nonnegative and increasing")
        for channel in channels:
            if not isinstance(channel, dict) or not isinstance(channel.get("target"), dict):
                _fail("glTF animation channel target is invalid")
            sampler_index = _integer(channel.get("sampler"), "animation sampler reference", 0, len(samplers)-1)
            target = channel["target"]
            if "node" in target:
                reader.reference("nodes", target["node"])
            if target.get("path") not in ("translation", "rotation", "scale", "weights"):
                _fail("glTF animation target path is unsupported")
            sampler = samplers[sampler_index]
            input_count = reader.accessors[sampler["input"]]["count"]
            output = reader.accessors[sampler["output"]]
            multiplier = 3 if sampler.get("interpolation") == "CUBICSPLINE" else 1
            if target["path"] != "weights" and (output["count"] != input_count * multiplier or
                    output["type"] != ("VEC4" if target["path"] == "rotation" else "VEC3")):
                _fail("glTF animation output count or dimensions are invalid")
    image_inventory = []
    for index, image in enumerate(reader.arrays["images"]):
        item = {"index": index, "available": False}
        if "bufferView" in image:
            if "uri" in image or image.get("mimeType") not in ("image/png", "image/jpeg"):
                _fail("glTF embedded image declaration is invalid or unsupported")
            view_id = reader.reference("bufferViews", image["bufferView"])
            view = reader.views[view_id]
            data = reader.buffers[view["buffer"]]
            if data is not None:
                decoded = _decode_image(data[view["offset"]:view["offset"] + view["length"]])
                item.update(available=True, dimensions=decoded["dimensions"], image_format=decoded["image_format"])
        elif not isinstance(image.get("uri"), str):
            _fail("glTF image requires a bufferView or URI")
        else:
            item["reason"] = "External image URI is not fetched"
        image_inventory.append(item)
    for texture in reader.arrays["textures"]:
        if "source" in texture:
            reader.reference("images", texture["source"])
        if "sampler" in texture:
            reader.reference("samplers", texture["sampler"])
    for material in reader.arrays["materials"]:
        pbr = material.get("pbrMetallicRoughness", {})
        if not isinstance(pbr, dict):
            _fail("glTF PBR material declaration must be an object")
        color = _vector(pbr.get("baseColorFactor", [1, 1, 1, 1]), 4, "material base color")
        if any(value < 0 or value > 1 for value in color):
            _fail("glTF material base color must lie between zero and one")
        for field in ("metallicFactor", "roughnessFactor"):
            if not 0 <= _finite(pbr.get(field, 1), "material factor") <= 1:
                _fail("glTF material factors must lie between zero and one")
        for information in [pbr.get("baseColorTexture"), pbr.get("metallicRoughnessTexture"),
                            material.get("normalTexture"), material.get("occlusionTexture"), material.get("emissiveTexture")]:
            if information is not None:
                if not isinstance(information, dict):
                    _fail("glTF material texture information is invalid")
                reader.reference("textures", information.get("index"))
    complete = all(buffer is not None for buffer in reader.buffers) and all(item["decoded"] for item in primitives)
    unsupported.update(reader.unsupported_geometry_extensions)
    return {"supported": True, "supported_level": "parsed_gltf" if complete and not unsupported else "gltf_inventory_partial",
            "status": "inspected" if complete and not unsupported else "partial",
            "capabilities": ["glb_chunks", "gltf_buffer_validation", "gltf_inventory", "source_cameras"] +
                            (["mesh_vertices", "mesh_triangles"] if complete and not unsupported else []),
            "meshes": len(reader.arrays["meshes"]), "nodes": len(nodes),
            "geometry_supported": complete and not unsupported,
            "extraction": {"chunks": chunks, "buffers": reader.buffer_inventory, "accessors": reader.accessor_inventory,
                           "mesh_primitives": primitives, "node_inventory": nodes, "cameras": cameras,
                           "images": image_inventory, "lights": light_inventory,
                           "material_count": len(reader.arrays["materials"]),
                           "skin_count": len(reader.arrays["skins"]), "animation_count": len(reader.arrays["animations"]),
                           "scene_count": len(reader.arrays["scenes"]), "default_scene": document.get("scene"),
                           "unsupported_geometry_extensions": sorted(unsupported),
                           "external_references_loaded": False,
                           "geometry_scope": "Base primitive local coordinates; skin/morph deformation is not applied",
                           "response_coordinate_limits": {"vertices": MAX_RESPONSE_VERTICES, "triangles": MAX_RESPONSE_TRIANGLES},
                           "units": "metres by glTF convention; not survey verified", "up_axis": "+Y by glTF convention"},
            "meaning": "Actual source buffers, transforms and cameras; no site CRS, building heights or floor schedule inferred"}


def inspect(path):
    """Inspect one bounded regular local file and return a JSON-serializable report.

    Invalid recognised formats raise ``IntakeError`` (a ``ValueError``). Unknown
    or unavailable formats return supported=False. No parser reads other assets.
    """
    try:
        source = Path(path)
    except (TypeError, ValueError):
        _fail("Source path must identify a local file")
    try:
        metadata = source.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            _fail("Only regular files are accepted; symbolic links are not followed")
        if not 0 < metadata.st_size <= MAX_FILE_BYTES:
            _fail("File is empty or exceeds the 50 MiB inspection limit")
        with source.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except IntakeError:
        raise
    except OSError:
        _fail("Source file cannot be read")
    if not 0 < len(raw) <= MAX_FILE_BYTES:
        _fail("File is empty or exceeds the 50 MiB inspection limit")
    suffix = source.suffix.lower()
    result = {"name": source.name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
              "format": suffix, "supported": False, "supported_level": "unsupported",
              "status": "unsupported", "capabilities": [], "georeferencing_verified": False,
              "site_assignment": "not_performed"}
    try:
        if suffix in (".png", ".jpg", ".jpeg"):
            details = _inspect_image(raw, suffix)
        elif suffix == ".pdf":
            details = _inspect_pdf(source, raw)
        elif suffix == ".geojson":
            details = _inspect_geojson(raw)
        elif suffix == ".obj":
            details = _inspect_obj(raw)
        elif suffix == ".dxf":
            details = _inspect_dxf(raw)
        elif suffix == ".glb":
            details = _inspect_glb(raw)
        elif suffix == ".dwg":
            details = {"missing_requirements": ["A legally usable DWG converter and a real validated DWG fixture are missing"],
                       "meaning": "DWG is not advertised as supported"}
        else:
            details = {"meaning": "No verified parser is available for this extension"}
    except IntakeError:
        raise
    except ImportError:
        details = {"supported": False, "supported_level": "missing_parser", "capabilities": [],
                   "missing_requirements": ["Required local format dependencies are unavailable in this Python runtime"]}
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, struct.error, OSError, RecursionError):
        _fail("File content is malformed or exceeds the supported inspection subset")
    result.update(details)
    if result["supported"] and result["status"] == "unsupported":
        result["status"] = "inspected"
    return result
