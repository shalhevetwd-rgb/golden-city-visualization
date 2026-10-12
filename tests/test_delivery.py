import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from delivery import IMAGE_SPECS, ROOT, create_contact_sheet, create_delivery_bundle


class DeliveryTests(unittest.TestCase):
    def test_bundle_preserves_originals_and_zip_is_repeatable(self):
        source_files = [ROOT / "examples/renders" / name for name, *_ in IMAGE_SPECS]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
        with tempfile.TemporaryDirectory() as directory:
            result = create_delivery_bundle(directory, include_blend=True)
            archive_path = Path(result["archive_path"])
            first_zip = archive_path.read_bytes()
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("OPEN-ME.txt", archive.namelist())
                self.assertIn("aerial.blend", archive.namelist())
                self.assertIn("acceptance-report.json", archive.namelist())
                for path in source_files:
                    self.assertEqual(archive.read(path.name), path.read_bytes())
                    self.assertEqual((Path(directory) / path.name).read_bytes(), path.read_bytes())
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual({record["name"] for record in manifest["files"]}, set(archive.namelist()) - {"manifest.json"})
                self.assertNotIn("delivery-validation.json", archive.namelist())
            second = create_delivery_bundle(directory, include_blend=True)
            # Reader evidence counts report characters, including the root
            # validator's isolated staging paths; package metadata and ZIP
            # contents remain the deterministic deliverables.
            self.assertEqual(second["files"], result["files"])
            self.assertEqual(second["images"], result["images"])
            self.assertEqual(first_zip, archive_path.read_bytes())
            manifest = json.loads(Path(result["manifest_path"]).read_text())
            self.assertTrue(manifest["existing_renders_reused"])
            for record in manifest["files"]:
                self.assertEqual(record["sha256"], hashlib.sha256((Path(directory) / record["name"]).read_bytes()).hexdigest())
            report = json.loads(Path(result["validation_report_path"]).read_text())
            self.assertEqual(report["archive"]["status"], "passed")
            self.assertEqual(report["links"]["status"], "passed")
            self.assertEqual(report["user_accessibility"], "not_verified")
            self.assertTrue(all(item["delivered_bytes_match_validation"] for item in result["final_integrity"]))
            self.assertEqual(result["validation_report_evidence"]["status"], "passed")
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files})

    def test_gallery_embeds_original_pngs_and_no_external_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            result = create_delivery_bundle(directory)
            gallery = Path(result["gallery_path"]).read_text()
            images = re.findall(r'<img src="data:image/png;base64,([^\"]+)"', gallery)
            self.assertEqual(len(images), 5)
            for encoded, spec in zip(images, IMAGE_SPECS):
                self.assertEqual(base64.b64decode(encoded), (ROOT / "examples/renders" / spec[0]).read_bytes())
            self.assertIn('lang="he" dir="rtl"', gallery)
            self.assertIn("אינו אישור כשרות", gallery)
            self.assertNotIn("<script", gallery)
            self.assertNotRegex(gallery, r'(?:src|href)="https?://')
            self.assertEqual(result["user_access"], "not_verified")
            with zipfile.ZipFile(result["archive_path"]) as archive:
                self.assertNotIn("aerial.blend", archive.namelist())

    def test_missing_or_invalid_png_does_not_create_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            source.mkdir()
            with self.assertRaises(FileNotFoundError):
                create_delivery_bundle(output, source_root=source)
            self.assertFalse(output.exists())
            render_dir = source / "examples/renders"
            render_dir.mkdir(parents=True)
            (render_dir / "aerial.png").write_bytes(b"not a png")
            with self.assertRaises(ValueError):
                create_delivery_bundle(output, source_root=source)
            self.assertFalse(output.exists())

    def test_report_text_is_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            render_dir = source / "examples/renders"
            render_dir.mkdir(parents=True)
            for name, *_ in IMAGE_SPECS:
                shutil.copyfile(ROOT / "examples/renders" / name, render_dir / name)
            (render_dir / "acceptance-report.json").write_text(json.dumps({"note": "<script>alert(1)</script>"}))
            result = create_delivery_bundle(output, source_root=source, require_models=False)
            gallery = Path(result["gallery_path"]).read_text()
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", gallery)
            self.assertNotIn("<script", gallery)

    def test_full_bundle_refuses_missing_fresh_model_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            render_dir = source / "examples/renders"
            render_dir.mkdir(parents=True)
            for name, *_ in IMAGE_SPECS:
                shutil.copyfile(ROOT / "examples/renders" / name, render_dir / name)
            with self.assertRaisesRegex(FileNotFoundError, "aerial.obj"):
                create_delivery_bundle(output, source_root=source)
            self.assertFalse(output.exists())

    def test_contact_sheet_contains_actual_render_pixels(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contact.png"
            result = create_contact_sheet([ROOT / "examples/renders" / name for name, *_ in IMAGE_SPECS[:3]], path)
            with Image.open(path) as sheet:
                sheet.load()
                self.assertEqual(sheet.size, (2400, 810))
                for index in range(3):
                    # Distinct source renders, including their photographic
                    # detail, must be present in each nonempty tile.
                    tile = sheet.crop((index * 800 + 16, 116, index * 800 + 784, 692))
                    self.assertGreater(len(set(tile.getdata())), 1000)
            self.assertEqual(result["width"], 2400)
            with self.assertRaises(ValueError):
                create_contact_sheet([], path)

    def test_validation_failure_preserves_existing_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "delivery"
            output.mkdir()
            existing = output / "golden-city-gallery.html"
            existing.write_text("prior reviewed gallery")
            with patch("delivery_validation.validate_file", return_value={"passed": False, "details": {"error": "intentional validation failure"}}):
                with self.assertRaisesRegex(ValueError, "בדיקת פתיחת הקובץ"):
                    create_delivery_bundle(output)
            self.assertEqual(existing.read_text(), "prior reviewed gallery")
            self.assertEqual(list(output.iterdir()), [existing])

    def test_offline_chromium_displays_all_original_images_without_network(self):
        # This is a browser integration requirement, so missing browser support
        # fails rather than silently skipping the actual viewing check.
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            result = create_delivery_bundle(directory)
            browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
            context = browser.new_context(offline=True)
            page = context.new_page()
            failures, remote_requests = [], []
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on("request", lambda request: remote_requests.append(request.url) if request.url.startswith(("http://", "https://")) else None)
            # Managed Chromium blocks file:// navigation by administrator
            # policy. Load the exact local file bytes in offline about:blank;
            # this verifies no server, JS modules or requests are needed.
            page.set_content(Path(result["gallery_path"]).read_text(), wait_until="load")
            loaded = page.locator("img").evaluate_all("images => images.map(image => ({complete: image.complete, width: image.naturalWidth, height: image.naturalHeight}))")
            self.assertEqual(len(loaded), 5)
            for actual, image in zip(loaded, result["images"]):
                self.assertTrue(actual["complete"])
                self.assertEqual((actual["width"], actual["height"]), (image["width"], image["height"]))
            page.locator("details").first.locator("summary").click()
            self.assertTrue(page.locator("details").first.locator("pre").is_visible())
            with page.expect_download() as download_info:
                page.locator(".card .download").first.click()
            download = download_info.value
            self.assertEqual(download.suggested_filename, "aerial.png")
            self.assertEqual(Path(download.path()).read_bytes(), (ROOT / "examples/renders/aerial.png").read_bytes())
            self.assertEqual(failures, [])
            self.assertEqual(remote_requests, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
