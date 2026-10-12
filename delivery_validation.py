"""Fail-closed delivery checks, independent of artifact generation.

Every supported artifact is reopened, parsed and hashed before and after the
checks. ZIP members are extracted into an isolated temporary directory and run
through the same checks. Evidence proves bytes available to this machine; it
never implies the chat application's links are accessible to a recipient.
"""
from __future__ import annotations

import base64
import binascii
from collections import Counter
from contextvars import ContextVar
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from urllib.parse import unquote, unquote_to_bytes, urlsplit
import warnings
import zipfile

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parent
MAX_FILE_BYTES = 1_073_741_824
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_ZIP_MEMBERS = 500
MAX_ZIP_UNCOMPRESSED_BYTES = 2_147_483_648
MAX_PDF_PAGES = 50
TEXT_FORMATS = {'.txt', '.md', '.log', '.mtl', '.csv'}
JSON_FORMATS = {'.json', '.geojson'}
IMAGE_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}
_LINK_STACK = ContextVar('golden_city_delivery_link_stack', default=())
_LINK_ROOT = ContextVar('golden_city_delivery_link_root', default=None)


class ValidationError(ValueError):
    """An artifact cannot be delivered because a mandatory check failed."""


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(payload):
    def reject(value):
        raise ValidationError('JSON contains non-finite number: ' + value)
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            reject(value)
        return number
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValidationError('JSON contains duplicate object keys')
            value[key] = item
        return value
    try:
        return json.loads(payload.decode('utf-8'), parse_constant=reject, parse_float=finite_float,
                          object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError('Invalid UTF-8 JSON') from error


def _ffmpeg_decode(source, width, height):
    decoder = shutil.which('ffmpeg')
    if not decoder:
        raise ValidationError('FFmpeg is required for a second independent image decoder')
    command = [decoder, '-v', 'error', '-noautorotate', '-threads', '1', '-i',
               'pipe:0' if isinstance(source, bytes) else str(source), '-frames:v', '1',
               '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1']
    try:
        result = subprocess.run(command, input=source if isinstance(source, bytes) else None,
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValidationError('Second image decoder could not finish') from error
    if result.returncode or result.stderr.strip() or len(result.stdout) != width * height * 3:
        raise ValidationError('FFmpeg independent full image decoding failed')
    return {'reader': 'FFmpeg independent full pixel decoder', 'decoded_bytes': len(result.stdout),
            'pixel_sha256': hashlib.sha256(result.stdout).hexdigest(), 'decoded': True}


def _image_details(source, expected_format=None, require_nonblank=True):
    """Verify container, close, reopen and decode every pixel with Pillow."""
    try:
        # A bytes input is independently opened on each pass, as for data URLs.
        opener = lambda: io.BytesIO(source) if isinstance(source, bytes) else source
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(opener()) as image:
                actual_format = image.format
                width, height = image.size
                if expected_format and actual_format != expected_format:
                    raise ValidationError('Image encoding does not match extension')
                if width < 1 or height < 1 or max(width, height) > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
                    raise ValidationError('Image dimensions are empty or unreasonable')
                image.verify()
            with Image.open(opener()) as image:
                image.load()
                rgba = image.convert('RGBA')
                visible = rgba.getchannel('A').getextrema()[1] != 0
                if not visible and require_nonblank:
                    raise ValidationError('Image is entirely transparent')
                preview = rgba.copy()
                preview.thumbnail((256, 256))
                # Judge visible pixels after compositing transparency on white.
                rgb = Image.new('RGB', preview.size, 'white')
                rgb.paste(preview, mask=preview.getchannel('A'))
                extrema = rgb.getextrema()
                deviation = ImageStat.Stat(rgb).stddev
                colors = Counter(rgb.getdata())
                coverage = 1 - max(colors.values()) / (rgb.width * rgb.height)
                nonblank = visible and max(high - low for low, high in extrema) >= 2 and max(deviation) >= .05 and coverage >= .001
                if not nonblank and require_nonblank:
                    raise ValidationError('Image is blank or has no meaningful visible content')
                second = _ffmpeg_decode(source, width, height)
                return {'reader': 'Pillow independent verify + full pixel decode',
                        'encoding': actual_format, 'width': width, 'height': height,
                        'mode': image.mode, 'extrema': [list(pair) for pair in extrema],
                        'standard_deviation': deviation, 'nonuniform_fraction': coverage,
                        'nonblank': nonblank, 'decoded': True, 'second_decoder': second}
    except ValidationError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValidationError('Image failed independent full decoding: ' + str(error)) from error


def _obj_preflight(path):
    """Reject indices Blender might repair rather than faithfully import."""
    vertices, faces = [], []
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            fields = line.split('#', 1)[0].split()
            if not fields:
                continue
            if fields[0] == 'v':
                if len(fields) < 4:
                    raise ValueError('Incomplete vertex')
                vertex = [float(value) for value in fields[1:4]]
                if not all(math.isfinite(value) for value in vertex):
                    raise ValueError('Non-finite coordinates')
                vertices.append(vertex)
            elif fields[0] == 'f':
                if len(fields) < 4:
                    raise ValueError('Face has fewer than three vertices')
                indices = []
                for token in fields[1:]:
                    index = int(token.split('/')[0])
                    if index == 0 or index < -len(vertices):
                        raise ValueError('Invalid OBJ vertex index')
                    indices.append(index - 1 if index > 0 else len(vertices) + index)
                if len(set(indices)) < 3:
                    raise ValueError('Degenerate OBJ face')
                faces.append(indices)
        if not vertices or not faces or any(index >= len(vertices) for face in faces for index in face):
            raise ValueError('Empty geometry or out-of-range OBJ indices')
        return {'source_vertices': len(vertices), 'source_faces': len(faces)}
    except (OSError, UnicodeError, ValueError) as error:
        raise ValidationError('OBJ structure check failed: ' + str(error)) from error


def _glb_preflight(path):
    payload = path.read_bytes()
    if len(payload) < 20 or payload[:4] != b'glTF':
        raise ValidationError('Invalid GLB container header')
    _, version, length = struct.unpack_from('<4sII', payload)
    if version != 2 or length != len(payload):
        raise ValidationError('GLB version or declared length is invalid')
    chunks, offset = [], 12
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValidationError('Truncated GLB chunk')
        size, kind = struct.unpack_from('<II', payload, offset)
        offset += 8
        if size % 4 or offset + size > len(payload):
            raise ValidationError('Invalid GLB chunk bounds')
        chunks.append((kind, payload[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise ValidationError('GLB first chunk must contain JSON')
    document = _strict_json(chunks[0][1].rstrip(b' \x00'))
    if document.get('asset', {}).get('version') != '2.0':
        raise ValidationError('Unsupported GLB asset version')
    binary = next((chunk for kind, chunk in chunks if kind == 0x004E4942), b'')
    buffers = document.get('buffers', [])
    if any('uri' in buffer for buffer in buffers) or len(buffers) != 1:
        raise ValidationError('Delivery GLB must have one embedded buffer')
    if any('uri' in image for image in document.get('images', [])):
        raise ValidationError('Delivery GLB images must be embedded')
    if buffers[0].get('byteLength', -1) < 0 or buffers[0]['byteLength'] > len(binary):
        raise ValidationError('GLB binary buffer is truncated')
    views = document.get('bufferViews', [])
    for view in views:
        if view.get('buffer', 0) != 0 or view.get('byteOffset', 0) < 0 or view.get('byteLength', -1) < 0 or view.get('byteOffset', 0) + view['byteLength'] > buffers[0]['byteLength']:
            raise ValidationError('GLB buffer view is out of range')
    accessors = document.get('accessors', [])
    sizes = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    elements = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
    for accessor in accessors:
        if 'sparse' in accessor:
            # Requires sparse buffer overlay verification, not silent acceptance.
            raise ValidationError('Sparse GLB accessors are not yet supported by the delivery validator')
        view_index = accessor.get('bufferView')
        if not isinstance(view_index, int) or not 0 <= view_index < len(views):
            raise ValidationError('GLB accessor has no valid buffer view')
        component = sizes.get(accessor.get('componentType'))
        count, element = accessor.get('count'), elements.get(accessor.get('type'))
        if not component or not element or not isinstance(count, int) or count < 1:
            raise ValidationError('Invalid GLB accessor layout')
        view = views[view_index]
        packed = component * element
        stride = view.get('byteStride', packed)
        start = accessor.get('byteOffset', 0)
        if start < 0 or stride < packed or start + (count - 1) * stride + packed > view['byteLength']:
            raise ValidationError('GLB accessor extends beyond its buffer view')
    primitives = 0
    for mesh in document.get('meshes', []):
        for primitive in mesh.get('primitives', []):
            position_index = primitive.get('attributes', {}).get('POSITION', -1)
            if not isinstance(position_index, int) or not 0 <= position_index < len(accessors):
                raise ValidationError('GLB primitive lacks valid positions')
            position = accessors[position_index]
            if position.get('type') != 'VEC3' or position['count'] < 3:
                raise ValidationError('GLB primitive has no surface geometry')
            if 'indices' in primitive:
                index = primitive['indices']
                if not isinstance(index, int) or not 0 <= index < len(accessors):
                    raise ValidationError('GLB index accessor does not exist')
                accessor = accessors[index]
                unpack = {5121: '<B', 5123: '<H', 5125: '<I'}.get(accessor['componentType'])
                if accessor['type'] != 'SCALAR' or not unpack or accessor['count'] < 3:
                    raise ValidationError('GLB index accessor is invalid')
                view = views[accessor['bufferView']]
                start = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
                stride = view.get('byteStride', sizes[accessor['componentType']])
                if any(struct.unpack_from(unpack, binary, start + i * stride)[0] >= position['count'] for i in range(accessor['count'])):
                    raise ValidationError('GLB vertex index is out of range')
            primitives += 1
    if not primitives:
        raise ValidationError('GLB contains no mesh primitives')
    return {'source_primitives': primitives, 'source_cameras': len(document.get('cameras', []))}


def _model_details(path):
    try:
        preflight = _obj_preflight(path) if path.suffix.lower() == '.obj' else _glb_preflight(path) if path.suffix.lower() == '.glb' else {}
    except ValidationError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, ValueError, struct.error) as error:
        raise ValidationError('Model source contains malformed container metadata') from error
    blender = os.environ.get('GOLDEN_CITY_BLENDER') or shutil.which('blender')
    if not blender:
        raise ValidationError('Blender is required to independently re-import this model')
    with tempfile.TemporaryDirectory(prefix='golden-city-model-check-') as directory:
        report = Path(directory) / 'inspection.json'
        try:
            result = subprocess.run([blender, '--background', '--factory-startup', '--disable-autoexec',
                                     '--python-exit-code', '7', '--python', str(ROOT / 'scripts/verify_model.py'),
                                     '--', str(path), str(report)], capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValidationError('Independent model re-import could not finish') from error
        if not report.is_file():
            raise ValidationError('Blender produced no independent model inspection report')
        details = _strict_json(report.read_bytes())
        if result.returncode or details.get('status') != 'passed':
            raise ValidationError('Independent model re-import failed: ' + details.get('reason', 'Blender process failed'))
        if path.suffix.lower() == '.glb' and len(details['cameras']) != preflight['source_cameras']:
            raise ValidationError('GLB re-import did not preserve its cameras')
        return {**preflight, **details}


def _pdf_details(path):
    pdfinfo, pdftoppm = shutil.which('pdfinfo'), shutil.which('pdftoppm')
    if not pdfinfo or not pdftoppm:
        raise ValidationError('pdfinfo and pdftoppm are required for independent PDF validation')
    try:
        info = subprocess.run([pdfinfo, str(path)], capture_output=True, text=True, timeout=30, check=True)
        match = re.search(r'^Pages:\s+(\d+)\s*$', info.stdout, re.MULTILINE)
        pages = int(match[1]) if match else 0
        if not 1 <= pages <= MAX_PDF_PAGES:
            raise ValidationError('PDF page count is empty or exceeds the complete visual validation limit')
        with tempfile.TemporaryDirectory(prefix='golden-city-pdf-check-') as directory:
            prefix = Path(directory) / 'page'
            subprocess.run([pdftoppm, '-png', '-r', '72', '-f', '1', '-l', str(pages), str(path), str(prefix)],
                           capture_output=True, text=True, timeout=120, check=True)
            renders = sorted(Path(directory).glob('page-*.png'))
            if len(renders) != pages:
                raise ValidationError('PDF did not visually render every page')
            inspected = [_image_details(render, 'PNG') for render in renders]
            return {'reader': 'Poppler pdfinfo + complete pdftoppm page rendering + Pillow decode',
                    'pages': pages, 'rendered_pages': len(inspected), 'page_images': inspected}
    except (OSError, subprocess.SubprocessError) as error:
        raise ValidationError('PDF independent parsing or visual rendering failed') from error


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.ids = [], set()

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get('id'):
            self.ids.add(attributes['id'])
        for name in ('href', 'src', 'poster'):
            if attributes.get(name):
                self.links.append((name, attributes[name]))
        if attributes.get('srcset'):
            # Ambiguous embedded data srcsets need a dedicated parser.
            if 'data:' in attributes['srcset']:
                raise ValidationError('Embedded data srcset is not supported; use individual src images')
            self.links.extend(('srcset', part.strip().split()[0]) for part in attributes['srcset'].split(','))


def _validate_links_impl(html_path, root):
    path = Path(html_path).resolve()
    root = Path(root).resolve()
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        raise ValidationError('HTML cannot be reopened as UTF-8') from error
    parser = _LinkParser()
    parser.feed(text)
    local, external, embedded = [], [], []
    for attribute, target in parser.links:
        parts = urlsplit(target)
        if parts.scheme == 'data':
            try:
                header, data = target.split(',', 1)
                mime = header[5:].split(';')[0]
                payload = base64.b64decode(data, validate=True) if ';base64' in header else unquote_to_bytes(data)
            except (ValueError, binascii.Error) as error:
                raise ValidationError('Invalid embedded data URL') from error
            if not payload:
                raise ValidationError('Empty embedded artifact')
            if mime in {'image/png', 'image/jpeg'}:
                detail = _image_details(payload, 'PNG' if mime == 'image/png' else 'JPEG')
            elif mime in {'application/json', 'application/geo+json'}:
                value = _strict_json(payload)
                detail = {'reader': 'Python JSON', 'type': type(value).__name__}
            elif mime in {'text/plain', 'text/html'}:
                try:
                    payload.decode('utf-8')
                except UnicodeError as error:
                    raise ValidationError('Embedded text is not UTF-8') from error
                detail = {'reader': 'UTF-8 decoder'}
            else:
                raise ValidationError('Unsupported embedded artifact media type: ' + mime)
            embedded.append({'attribute': attribute, 'mime': mime, 'bytes': len(payload),
                             'sha256': hashlib.sha256(payload).hexdigest(), 'details': detail})
        elif parts.scheme in {'http', 'https'} or parts.netloc:
            external.append({'target': target, 'user_accessibility': 'not_verified'})
        elif parts.scheme:
            raise ValidationError('Unsupported link scheme: ' + parts.scheme)
        elif not parts.path:
            if parts.fragment and unquote(parts.fragment) not in parser.ids:
                raise ValidationError('HTML fragment target is missing: ' + parts.fragment)
        else:
            # Rooted links in an offline deliverable resolve from the supplied root.
            resolved = (root / unquote(parts.path).lstrip('/')).resolve() if parts.path.startswith('/') else (path.parent / unquote(parts.path)).resolve()
            if not resolved.is_relative_to(root):
                raise ValidationError('Local link escapes the delivery root')
            if not resolved.is_file() or resolved.stat().st_size < 1:
                raise ValidationError('Local link target does not exist or is empty: ' + parts.path)
            if resolved == path:
                if parts.fragment and unquote(parts.fragment) not in parser.ids:
                    raise ValidationError('HTML fragment target is missing: ' + parts.fragment)
                validation = {'status': 'passed', 'checks': ['current_document_already_independently_parsed']}
            else:
                validation = validate_file(resolved, artifact_role='linked_output')
                validation.pop('path')
            local.append({'target': parts.path, 'bytes': resolved.stat().st_size,
                          'exists': True, 'validation': validation, 'user_accessibility': 'not_verified'})
    return {'status': 'passed', 'local_links': local, 'embedded_artifacts': embedded,
            'external_links': external, 'user_accessibility': 'not_verified'}


def validate_links(html_path, root=None):
    """Actually reopen every local target; reject recursive HTML link cycles."""
    path = Path(html_path).resolve()
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_FILE_BYTES:
        raise ValidationError('HTML artifact is missing, empty or unreasonably large')
    stack = _LINK_STACK.get()
    if path in stack:
        raise ValidationError('HTML link cycle cannot be independently validated: ' + path.name)
    root = Path(root).resolve() if root else _LINK_ROOT.get() or path.parent
    before = _sha(path)
    stack_token = _LINK_STACK.set(stack + (path,))
    root_token = _LINK_ROOT.set(root)
    try:
        evidence = _validate_links_impl(path, root)
        if _sha(path) != before:
            raise ValidationError('HTML bytes changed while validating its linked artifacts')
        evidence['sha256'] = before
        return evidence
    finally:
        _LINK_ROOT.reset(root_token)
        _LINK_STACK.reset(stack_token)


def _expected_details(evidence, expected):
    for key in ('bytes', 'sha256', 'format'):
        if key in expected and evidence[key] != expected[key]:
            raise ValidationError('Artifact differs from expected ' + key)
    details = evidence['details']
    for key in ('width', 'height', 'pages', 'vertices', 'faces'):
        if key in expected and details.get(key) != expected[key]:
            raise ValidationError('Artifact differs from expected ' + key)
    for key in ('vertices', 'faces'):
        if 'min_' + key in expected and details.get(key, 0) < expected['min_' + key]:
            raise ValidationError('Model has fewer ' + key + ' than expected')
    for key in ('objects', 'cameras'):
        if key in expected:
            actual = details.get(key, [])
            if isinstance(expected[key], int):
                matches = len(actual) == expected[key]
            else:
                names = [item['name'] for item in actual]
                matches = set(expected[key]).issubset(names)
            if not matches:
                raise ValidationError('Model did not preserve expected ' + key)
    if 'object_names' in expected and not set(expected['object_names']).issubset({item['name'] for item in details.get('objects', [])}):
        raise ValidationError('Model did not preserve expected object names')
    for name, bounds in expected.get('object_bounds', {}).items():
        if len(bounds) != 2 or any(len(vector) != 3 or not all(math.isfinite(value) for value in vector) for vector in bounds):
            raise ValidationError('Expected model bounds must contain two finite 3D vectors')
        obj = next((item for item in details.get('objects', []) if item['name'] == name), None)
        if obj is None or any(abs(a - b) > expected.get('bound_tolerance', 1e-4) for actual, wanted in zip(obj['bounds'], bounds) for a, b in zip(actual, wanted)):
            raise ValidationError('Model did not preserve expected object bounds: ' + name)
    for name, wanted in expected.get('camera_parameters', {}).items():
        camera = next((item for item in details.get('cameras', []) if item['name'] == name), None)
        if camera is None:
            raise ValidationError('Model did not preserve expected camera: ' + name)
        position_tolerance = expected.get('camera_position_tolerance', 1e-4)
        angle_tolerance = expected.get('camera_angle_tolerance', 1e-4)
        if 'position' in wanted:
            if len(wanted['position']) != 3 or not all(math.isfinite(value) for value in wanted['position']):
                raise ValidationError('Expected camera position must contain three finite coordinates')
            if any(abs(a - b) > position_tolerance for a, b in zip(camera['position'], wanted['position'])):
                raise ValidationError('Model did not preserve camera position: ' + name)
        rotation = wanted.get('rotation', wanted.get('rotation_euler'))
        if rotation is not None:
            if len(rotation) != 3 or not all(math.isfinite(value) for value in rotation):
                raise ValidationError('Expected camera Euler rotation must contain three finite angles')
            delta = max(math.degrees(abs((a - b + math.pi) % (2 * math.pi) - math.pi))
                        for a, b in zip(camera['rotation'], rotation))
            if delta > angle_tolerance:
                raise ValidationError('Model did not preserve camera Euler orientation: ' + name)
        for axis in ('forward', 'up'):
            if axis in wanted:
                actual_vector, wanted_vector = camera[axis], wanted[axis]
                if len(wanted_vector) != 3 or not all(math.isfinite(value) for value in wanted_vector):
                    raise ValidationError('Expected camera direction must contain three finite coordinates')
                length = math.sqrt(sum(value * value for value in wanted_vector))
                actual_length = math.sqrt(sum(value * value for value in actual_vector))
                if length <= 1e-12:
                    raise ValidationError('Expected camera direction must be nonzero')
                dot = sum(a * b / (length * actual_length) for a, b in zip(actual_vector, wanted_vector))
                delta = math.degrees(math.acos(max(-1, min(1, dot))))
                if delta > angle_tolerance:
                    raise ValidationError('Model did not preserve camera ' + axis + ': ' + name)
        if 'quaternion' in wanted:
            if len(wanted['quaternion']) != 4 or not all(math.isfinite(value) for value in wanted['quaternion']):
                raise ValidationError('Expected camera quaternion must contain four finite coordinates')
            norm = math.sqrt(sum(value * value for value in wanted['quaternion']))
            actual_norm = math.sqrt(sum(value * value for value in camera['quaternion']))
            if norm <= 1e-12:
                raise ValidationError('Expected camera quaternion must be nonzero')
            dot = abs(sum(a * b / (norm * actual_norm) for a, b in zip(camera['quaternion'], wanted['quaternion'])))
            delta = math.degrees(2 * math.acos(max(-1, min(1, dot))))
            if delta > angle_tolerance:
                raise ValidationError('Model did not preserve camera orientation: ' + name)
        for parameter in ('vertical_fov', 'lens_mm', 'sensor_height'):
            if parameter in wanted and abs(camera[parameter] - wanted[parameter]) > angle_tolerance:
                raise ValidationError('Model did not preserve camera ' + parameter + ': ' + name)
    if expected.get('camera_parameters'):
        evidence['checks'].append('camera_parameters_match_source')


def validate_file(path, *, expected=None, artifact_role='output'):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValidationError('Artifact does not exist as a regular file: ' + str(path))
    size = path.stat().st_size
    if not 1 <= size <= MAX_FILE_BYTES:
        raise ValidationError('Artifact is empty or exceeds the reasonable delivery size limit')
    before = _sha(path)
    suffix = path.suffix.lower()
    checks = ['regular_file_exists', 'nonempty_reasonable_size', 'independent_reopen']
    if suffix in IMAGE_FORMATS:
        details = _image_details(path, IMAGE_FORMATS[suffix], require_nonblank=artifact_role != 'source')
        checks += ['container_verify', 'full_pixel_decode', 'second_independent_pixel_decoder', 'dimensions']
        checks += ['nonblank'] if details['nonblank'] else ['source_image_uninformative']
    elif suffix in {'.obj', '.glb', '.blend'}:
        details = _model_details(path)
        checks += ['independent_model_reimport', 'finite_nonempty_geometry', 'object_inventory', 'camera_inventory']
    elif suffix == '.pdf':
        details = _pdf_details(path)
        checks += ['page_count', 'all_pages_visual_render', 'all_page_images_decode']
    elif suffix == '.zip':
        details = validate_archive(path, expected_manifest=(expected or {}).get('manifest'))
        checks += ['archive_crc', 'isolated_extract', 'manifest_hashes', 'every_member_revalidated']
    elif suffix in JSON_FORMATS:
        value = _strict_json(path.read_bytes())
        details = {'reader': 'Python independent UTF-8 JSON parser', 'type': type(value).__name__}
        checks += ['utf8_decode', 'json_parse', 'finite_json_numbers']
    elif suffix in {'.html', '.htm'}:
        details = {'reader': 'Python independent HTML parser', **validate_links(path)}
        checks += ['utf8_decode', 'html_parse', 'all_local_targets_exist', 'embedded_artifacts_decode']
    elif suffix in TEXT_FORMATS:
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            raise ValidationError('Text artifact cannot be independently decoded as UTF-8') from error
        if not text.strip() or '\x00' in text:
            raise ValidationError('Text artifact is empty or contains binary data')
        details = {'reader': 'Python independent UTF-8 decoder', 'characters': len(text), 'lines': len(text.splitlines())}
        checks += ['utf8_decode', 'nonblank_text']
    else:
        raise ValidationError('Unsupported delivery format; add a mandatory validator before delivering: ' + suffix)
    after = _sha(path)
    if before != after or path.stat().st_size != size:
        raise ValidationError('Artifact bytes changed during independent validation')
    evidence = {'status': 'passed', 'path': str(path), 'name': path.name, 'bytes': size,
                'sha256': before, 'format': suffix.lstrip('.'), 'artifact_role': artifact_role,
                'checks': checks + ['immutable_source_hash'], 'details': details,
                'user_accessibility': 'not_verified'}
    _expected_details(evidence, expected or {})
    return evidence


def validate_archive(path, *, expected_manifest=None):
    """CRC-check, extract, hash and independently reopen every packaged member.

    The archive's manifest must list every non-directory member except itself.
    Self-hashing a manifest is impossible; its bytes are covered by the ZIP hash.
    """
    path = Path(path).resolve()
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_FILE_BYTES:
        raise ValidationError('Archive is missing, empty or unreasonably large')
    before = _sha(path)
    try:
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='golden-city-zip-check-') as directory:
            records = archive.infolist()
            if not records or len(records) > MAX_ZIP_MEMBERS:
                raise ValidationError('Archive member count is empty or unreasonable')
            names, canonical_names, total = set(), set(), 0
            for info in records:
                name = PurePosixPath(info.filename)
                if '\\' in info.filename or name.is_absolute() or '..' in name.parts or not name.parts or re.match(r'^[a-zA-Z]:', info.filename) or info.filename in names or str(name) in canonical_names:
                    raise ValidationError('Unsafe or duplicate ZIP member path')
                names.add(info.filename)
                canonical_names.add(str(name))
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise ValidationError('ZIP contains a link or special file')
                if info.flag_bits & 1:
                    raise ValidationError('Encrypted ZIP members cannot be validated')
                total += info.file_size
                if total > MAX_ZIP_UNCOMPRESSED_BYTES or info.file_size > MAX_FILE_BYTES:
                    raise ValidationError('ZIP expands beyond the reasonable size limit')
                if info.file_size > 10_000_000 and info.file_size > max(info.compress_size, 1) * 1000:
                    raise ValidationError('ZIP compression ratio is unreasonable')
                if name.suffix.lower() == '.zip':
                    raise ValidationError('Nested ZIP delivery is not supported')
            bad_member = archive.testzip()
            if bad_member:
                raise ValidationError('ZIP CRC failed: ' + bad_member)
            if 'manifest.json' not in names:
                raise ValidationError('ZIP is missing its mandatory manifest.json')
            manifest = _strict_json(archive.read('manifest.json'))
            if expected_manifest is not None:
                expected = _strict_json(Path(expected_manifest).read_bytes()) if isinstance(expected_manifest, (str, Path)) else expected_manifest
                if manifest != expected:
                    raise ValidationError('ZIP manifest differs from expected manifest')
            if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
                raise ValidationError('ZIP manifest has no files list')
            expectations = {}
            for item in manifest['files']:
                if not isinstance(item, dict) or not isinstance(item.get('name'), str) or item['name'] in expectations:
                    raise ValidationError('ZIP manifest has invalid or duplicate file records')
                if not isinstance(item.get('bytes'), int) or item['bytes'] < 1 or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256', ''))):
                    raise ValidationError('ZIP manifest has invalid size or SHA-256')
                expectations[item['name']] = item
            actual_names = {info.filename for info in records if not info.is_dir()} - {'manifest.json'}
            if set(expectations) != actual_names:
                raise ValidationError('ZIP manifest must account for every artifact except itself')
            extract_root = Path(directory)
            for info in records:
                target = extract_root / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open('wb') as destination:
                        shutil.copyfileobj(source, destination)
            evidence = []
            root_token = _LINK_ROOT.set(extract_root)
            try:
                for name in sorted(actual_names | {'manifest.json'}):
                    item = validate_file(extract_root / name, expected=expectations.get(name), artifact_role='packaged_member')
                    # Temporary extraction paths must not masquerade as downloadable files.
                    item.pop('path')
                    item['name'] = name
                    evidence.append(item)
            finally:
                _LINK_ROOT.reset(root_token)
            if _sha(path) != before:
                raise ValidationError('ZIP bytes changed during validation')
            return {'status': 'passed', 'path': str(path), 'sha256': before,
                    'bytes': path.stat().st_size, 'member_count': len(evidence),
                    'uncompressed_bytes': total, 'crc': 'passed', 'manifest_hashes': 'passed',
                    'members': evidence, 'user_accessibility': 'not_verified',
                    'checks': ['crc', 'safe_paths', 'isolated_extract', 'manifest_hashes', 'independent_revalidation', 'immutable_archive_hash']}
    except ValidationError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, zipfile.BadZipFile, EOFError) as error:
        raise ValidationError('Archive independent extraction or decoding failed: ' + str(error)) from error
