import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image, ImageDraw

from delivery_validation import ValidationError, validate_archive, validate_file, validate_links


def write_image(path, mode='RGB', format='PNG'):
    image = Image.new(mode, (48, 32), '#e8e2d3')
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 4, 20, 28), fill='#54758a')
    draw.line((0, 0, 47, 31), fill='#222222', width=2)
    image.save(path, format=format)


def write_obj(path):
    path.write_text('o building:A\nv 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n', encoding='utf-8')


def write_glb(path, invalid_index=False, collapsed=False):
    """A separate minimal glTF writer; never calls production exporters."""
    positions = struct.pack('<9f', 0, 0, 0, 2, 0, 0, 0, 2, 0)
    binary = positions + struct.pack('<3H', 0, 1, 12 if invalid_index else 2)
    document = {
        'asset': {'version': '2.0'},
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36, 'target': 34962},
                        {'buffer': 0, 'byteOffset': 36, 'byteLength': 6, 'target': 34963}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3', 'min': [0, 0, 0], 'max': [2, 2, 0]},
                      {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
        'meshes': [{'name': 'building:A', 'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
        'cameras': [{'name': 'acceptance-camera', 'type': 'perspective', 'perspective': {'yfov': .7, 'znear': .1, 'zfar': 100}}],
        'nodes': [{'name': 'building:A', 'mesh': 0}, {'name': 'acceptance-camera', 'camera': 0, 'translation': [0, 2, 5]}],
        'scenes': [{'nodes': [0, 1]}], 'scene': 0,
    }
    if collapsed:
        document['nodes'][0]['scale'] = [0, 0, 0]
    data = json.dumps(document, separators=(',', ':')).encode()
    data += b' ' * ((-len(data)) % 4)
    binary += b'\0' * ((-len(binary)) % 4)
    length = 12 + 8 + len(data) + 8 + len(binary)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, length) + struct.pack('<II', len(data), 0x4E4F534A) + data + struct.pack('<II', len(binary), 0x004E4942) + binary)


def write_pdf(path, blank=False):
    """Write a valid one-page PDF without depending on its Poppler readers."""
    stream = b'' if blank else b'0.2 0.4 0.7 rg 20 20 140 90 re f\nBT /F1 18 Tf 25 130 Td (Golden City) Tj ET\n'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 160] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    payload = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(str(index).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    xref = len(payload)
    payload.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        payload.extend(f'{offset:010} 00000 n \n'.encode())
    payload.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(payload)


def pack(path, sources, manifest=None):
    manifest = manifest if manifest is not None else {
        'format_version': 1,
        'files': [{'name': source.name, 'bytes': source.stat().st_size,
                   'sha256': hashlib.sha256(source.read_bytes()).hexdigest()} for source in sources],
    }
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            archive.write(source, source.name)
        archive.writestr('manifest.json', json.dumps(manifest))
    return manifest


class DeliveryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_actual_png_jpeg_reopened_and_two_independent_decoders(self):
        for name, format in [('image.png', 'PNG'), ('image.jpg', 'JPEG')]:
            path = self.directory / name
            write_image(path, format=format)
            payload = path.read_bytes()
            evidence = validate_file(path, expected={'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'width': 48, 'height': 32})
            self.assertEqual(evidence['status'], 'passed')
            self.assertEqual(evidence['details']['encoding'], format)
            self.assertTrue(evidence['details']['decoded'])
            self.assertTrue(evidence['details']['nonblank'])
            self.assertTrue(evidence['details']['second_decoder']['decoded'])
            self.assertEqual(evidence['details']['second_decoder']['decoded_bytes'], 48 * 32 * 3)
            self.assertEqual(evidence['user_accessibility'], 'not_verified')
            self.assertEqual(payload, path.read_bytes())

    def test_missing_empty_and_unreasonably_large_fail(self):
        missing = self.directory / 'missing.png'
        with self.assertRaises(ValidationError):
            validate_file(missing)
        missing.write_bytes(b'')
        with self.assertRaises(ValidationError):
            validate_file(missing)
        missing.write_bytes(b'abc')
        with patch('delivery_validation.MAX_FILE_BYTES', 2), self.assertRaises(ValidationError):
            validate_file(missing)

    def test_truncated_png_cannot_pass_header_only_check(self):
        path = self.directory / 'image.png'
        write_image(path)
        path.write_bytes(path.read_bytes()[:40])
        with self.assertRaises(ValidationError):
            validate_file(path)

    def test_uniform_or_transparent_image_fails_but_source_is_labeled(self):
        path = self.directory / 'image.png'
        Image.new('RGB', (48, 32), 'green').save(path)
        with self.assertRaises(ValidationError):
            validate_file(path)
        source = validate_file(path, artifact_role='source')
        self.assertFalse(source['details']['nonblank'])
        self.assertIn('source_image_uninformative', source['checks'])
        Image.new('RGBA', (48, 32), (100, 200, 50, 0)).save(path)
        with self.assertRaises(ValidationError):
            validate_file(path)

    def test_wrong_extension_expected_dimensions_or_hash_fail(self):
        path = self.directory / 'image.png'
        write_image(path, format='JPEG')
        with self.assertRaises(ValidationError):
            validate_file(path)
        write_image(path)
        for expected in ({'width': 49}, {'sha256': '0' * 64}):
            with self.assertRaises(ValidationError):
                validate_file(path, expected=expected)

    def test_modified_during_validation_fails(self):
        path = self.directory / 'readme.txt'
        path.write_text('Golden City', encoding='utf-8')
        with patch('delivery_validation._sha', side_effect=['1' * 64, '2' * 64]), self.assertRaises(ValidationError):
            validate_file(path)

    def test_json_text_and_unsupported_types(self):
        path = self.directory / 'report.json'
        path.write_text('{"ok":true}', encoding='utf-8')
        self.assertEqual(validate_file(path)['details']['type'], 'dict')
        for bad in ('{"bad": NaN}', '{"bad": Infinity}', '{"bad":1e309}', '{"key":1,"key":2}', '{bad}', ''):
            path.write_text(bad, encoding='utf-8')
            with self.assertRaises(ValidationError):
                validate_file(path)
        text = self.directory / 'material.mtl'
        text.write_text('newmtl stone\nKd 0.8 0.7 0.5\n', encoding='utf-8')
        self.assertEqual(validate_file(text)['status'], 'passed')
        text.write_bytes(b'\xff\xfe')
        with self.assertRaises(ValidationError):
            validate_file(text)
        unknown = self.directory / 'model.unknown'
        unknown.write_bytes(b'anything')
        with self.assertRaises(ValidationError):
            validate_file(unknown)

    def test_local_embedded_and_external_links_checked_without_access_claim(self):
        image = self.directory / 'image.png'
        write_image(image)
        encoded = base64.b64encode(image.read_bytes()).decode()
        html = self.directory / 'gallery.html'
        html.write_text(f'<html><body id="top"><a href="#top">Top</a><a href="image.png">Image</a><img src="data:image/png;base64,{encoded}"><a href="https://example.invalid/image.png">External</a></body></html>', encoding='utf-8')
        evidence = validate_links(html)
        self.assertEqual(len(evidence['local_links']), 1)
        self.assertEqual(len(evidence['embedded_artifacts']), 1)
        self.assertTrue(evidence['embedded_artifacts'][0]['details']['second_decoder']['decoded'])
        self.assertEqual(evidence['external_links'][0]['user_accessibility'], 'not_verified')
        self.assertEqual(evidence['user_accessibility'], 'not_verified')

    def test_missing_escaped_and_corrupt_embedded_link_fail(self):
        html = self.directory / 'gallery.html'
        for body in ('<a href="missing.png">Missing</a>', '<img src="../outside.png">', '<img src="data:image/png;base64,bm90LWFuLWltYWdl">', '<a href="#missing">Missing fragment</a>'):
            html.write_text(body, encoding='utf-8')
            with self.assertRaises(ValidationError):
                validate_links(html)

    def test_existing_but_corrupt_local_image_and_html_cycles_are_rejected(self):
        image = self.directory / 'corrupt.png'
        image.write_bytes(b'Not a PNG')
        html = self.directory / 'gallery.html'
        html.write_text('<img src="corrupt.png">', encoding='utf-8')
        with self.assertRaises(ValidationError):
            validate_links(html)
        other = self.directory / 'other.html'
        html.write_text('<a href="other.html">Other</a>', encoding='utf-8')
        other.write_text('<a href="gallery.html">Back</a>', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'cycle'):
            validate_links(html)

    def test_actual_obj_separate_blender_import_geometry_and_names(self):
        path = self.directory / 'model.obj'
        write_obj(path)
        evidence = validate_file(path, expected={'object_names': ['building:A'], 'objects': 1, 'cameras': 0, 'vertices': 3, 'faces': 1, 'min_vertices': 3,
                                                  'object_bounds': {'building:A': [[0, 0, 0], [2, 0, 2]]}})
        self.assertEqual(evidence['details']['source_vertices'], 3)
        self.assertEqual(evidence['details']['vertices'], 3)
        self.assertEqual(evidence['details']['faces'], 1)
        self.assertEqual(evidence['details']['camera_support'], 'not supported by OBJ')

    def test_actual_glb_separate_blender_import_preserves_camera(self):
        path = self.directory / 'model.glb'
        write_glb(path)
        evidence = validate_file(path, expected={'object_names': ['building:A'], 'objects': 1, 'cameras': ['acceptance-camera'], 'vertices': 3, 'faces': 1})
        self.assertEqual(evidence['details']['source_primitives'], 1)
        self.assertEqual(len(evidence['details']['cameras']), 1)
        self.assertEqual(evidence['details']['cameras'][0]['name'], 'acceptance-camera')
        wanted = {'position': [0, -5, 2], 'forward': [0, 1, 0], 'up': [0, 0, 1],
                  'vertical_fov': .7 * 180 / 3.141592653589793}
        verified = validate_file(path, expected={'camera_parameters': {'acceptance-camera': wanted}})
        self.assertIn('camera_parameters_match_source', verified['checks'])
        for parameter, wrong in [('position', [1, -5, 2]), ('forward', [0, -1, 0]), ('vertical_fov', 10)]:
            with self.assertRaises(ValidationError):
                validate_file(path, expected={'camera_parameters': {'acceptance-camera': {**wanted, parameter: wrong}}})

    def test_declared_golden_city_obj_axes_preserve_local_xyz_bounds(self):
        path = self.directory / 'model.obj'
        write_obj(path)
        path.write_text('# GOLDEN CITY axes: forward=Y up=Z\n' + path.read_text(), encoding='utf-8')
        evidence = validate_file(path, expected={'object_bounds': {'building:A': [[0, 0, 0], [2, 2, 0]]}})
        self.assertEqual(evidence['details']['coordinate_interpretation'], 'declared local XYZ Z up')

    def test_invalid_geometry_and_glb_indices_are_rejected(self):
        path = self.directory / 'model.obj'
        for data in ('o empty\n', 'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 99\n', 'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 1 1\n', 'v NaN 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'):
            path.write_text(data, encoding='utf-8')
            with self.assertRaises(ValidationError):
                validate_file(path)
        glb = self.directory / 'model.glb'
        write_glb(glb, invalid_index=True)
        with self.assertRaises(ValidationError):
            validate_file(glb)
        write_glb(glb, collapsed=True)
        with self.assertRaises(ValidationError):
            validate_file(glb)

    def test_actual_pdf_all_pages_visually_rendered_and_reopened(self):
        path = self.directory / 'preview.pdf'
        write_pdf(path)
        evidence = validate_file(path, expected={'pages': 1})
        self.assertEqual(evidence['details']['pages'], 1)
        self.assertEqual(evidence['details']['rendered_pages'], 1)
        self.assertTrue(evidence['details']['page_images'][0]['nonblank'])
        self.assertTrue(evidence['details']['page_images'][0]['second_decoder']['decoded'])

    def test_corrupt_or_blank_pdf_is_rejected(self):
        path = self.directory / 'preview.pdf'
        path.write_bytes(b'%PDF-1.4 broken')
        with self.assertRaises(ValidationError):
            validate_file(path)
        write_pdf(path, blank=True)
        with self.assertRaises(ValidationError):
            validate_file(path)

    def test_packaged_outputs_are_extracted_reopened_and_hashed(self):
        image = self.directory / 'image.png'
        model = self.directory / 'model.obj'
        gallery = self.directory / 'gallery.html'
        write_image(image)
        write_obj(model)
        gallery.write_text('<html><body><img src="image.png"><a href="model.obj">Model</a></body></html>', encoding='utf-8')
        archive = self.directory / 'delivery.zip'
        manifest = pack(archive, [image, model, gallery])
        before = archive.read_bytes()
        evidence = validate_archive(archive, expected_manifest=manifest)
        self.assertEqual(evidence['status'], 'passed')
        self.assertEqual(evidence['crc'], 'passed')
        self.assertEqual(evidence['member_count'], 4)
        self.assertEqual(evidence['manifest_hashes'], 'passed')
        self.assertTrue(all('path' not in member for member in evidence['members']))
        self.assertEqual(evidence['user_accessibility'], 'not_verified')
        self.assertEqual(before, archive.read_bytes())

    def test_archive_requires_manifest_and_all_members(self):
        archive = self.directory / 'delivery.zip'
        with zipfile.ZipFile(archive, 'w') as zip:
            zip.writestr('readme.txt', 'Golden City')
        with self.assertRaises(ValidationError):
            validate_archive(archive)
        text = self.directory / 'readme.txt'
        text.write_text('Golden City', encoding='utf-8')
        pack(archive, [text], manifest={'files': []})
        with self.assertRaises(ValidationError):
            validate_archive(archive)
        manifest = pack(archive, [text])
        manifest['files'][0]['sha256'] = '0' * 64
        pack(archive, [text], manifest=manifest)
        with self.assertRaises(ValidationError):
            validate_archive(archive)

    def test_archive_crc_unsafe_paths_and_corrupt_image_fail(self):
        archive = self.directory / 'delivery.zip'
        for name in ('../outside.txt', '/absolute.txt', 'folder\\escape.txt'):
            with zipfile.ZipFile(archive, 'w') as zip:
                zip.writestr(name, 'bad')
                zip.writestr('manifest.json', '{"files":[]}')
            with self.assertRaises(ValidationError):
                validate_archive(archive)
        image = self.directory / 'image.png'
        image.write_bytes(b'fake PNG that could pass a naive existence check')
        pack(archive, [image])
        with self.assertRaises(ValidationError):
            validate_archive(archive)
        # Flip the stored member's bytes while retaining its original CRC.
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zip:
            zip.writestr('readme.txt', 'Golden City')
            zip.writestr('manifest.json', '{"files":[]}')
        data = bytearray(archive.read_bytes())
        data[data.index(b'Golden City')] ^= 0x20
        archive.write_bytes(data)
        with self.assertRaises(ValidationError):
            validate_archive(archive)


if __name__ == '__main__':
    unittest.main()
