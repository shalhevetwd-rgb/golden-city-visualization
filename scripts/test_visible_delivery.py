"""Test delivery in the existing main UI without rendering or editing models.

The managed browser proves server/UI operation, not recipient-machine access.
Only a new working project copied from the existing baseline may be created.
Source PNG, OBJ, GLB, BLEND and project files must remain byte-identical.
"""
import argparse
import hashlib
import io
import json
import math
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_NAMES = ("aerial.png", "street.png", "edited-aerial.png")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def source_hashes():
    paths = sorted((ROOT / "examples" / "renders").glob("*"))
    paths.extend((ROOT / "deliverables").glob("*"))
    suffixes = {".png", ".obj", ".mtl", ".glb", ".blend", ".zip"}
    return {str(p.relative_to(ROOT)): sha256(p.read_bytes()) for p in paths
            if p.is_file() and (p.suffix in suffixes or p.name in {"baseline-project.json", "edited-project.json"})}


def decoded_png(data):
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG", image.format
        image.load()
        assert image.width >= 640 and image.height >= 480, image.size
        extrema = image.convert("RGB").getextrema()
        assert any(high - low > 30 for low, high in extrema), "Image is blank"
        return list(image.size)


def run(url, output):
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    baseline = json.loads((ROOT / "examples" / "renders" / "baseline-project.json").read_text())
    report = {"url": url, "recipient_access": "not_verified", "checks": [], "downloads": [], "page_errors": []}
    checks = report["checks"]
    with tempfile.TemporaryDirectory(prefix="golden-visible-downloads-") as temporary, sync_playwright() as playwright:
        directory = Path(temporary)
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100}, accept_downloads=True)
        page.set_default_timeout(180000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("window.goldenCity !== undefined")
        assert page.locator("#acceptanceImages img").count() == 3
        page.evaluate("async () => { await Promise.all([...document.querySelectorAll('#acceptanceImages img')].map(img => img.decode())); }")
        for image in page.locator("#acceptanceImages img").all():
            assert image.is_visible(), "Acceptance image is hidden in main UI"
            assert image.evaluate("img => img.naturalWidth >= 640 && img.naturalHeight >= 480")
            assert image.get_attribute("src").startswith("/downloads/")
        assert page.url.rstrip("/") == url.rstrip("/"), "Gallery requires another page"
        checks.append("main page directly displays and decodes all three existing acceptance PNGs")
        page.locator("#acceptanceDelivery").screenshot(path=str(output / "delivery-visible.png"))

        assert page.evaluate("window.goldenCity.isWebGLReady()")
        assert sorted(page.evaluate("window.goldenCity.getObjectIds()")) == ["A", "B", "C"]
        checks.append("initial view selects or opens existing synthetic model with three building objects")
        previous_id = page.evaluate("window.goldenCity.getProject().id")
        page.click("#openSynthetic")
        page.wait_for_function("id => window.goldenCity.getProject()?.id !== id && !document.querySelector('#openSynthetic').disabled", arg=previous_id)
        opened = page.evaluate("window.goldenCity.getProject()")
        assert sorted(page.evaluate("window.goldenCity.getObjectIds()")) == ["A", "B", "C"]
        assert page.locator("#viewer").is_visible() and not page.locator("#scene").is_visible()
        assert opened["boundary"] == baseline["boundary"] and opened["crs"] == baseline["crs"]
        assert [{"geometry": b["geometry"], "properties": b["properties"]} for b in opened["buildings"]] == [
            {"geometry": b["geometry"], "properties": b["properties"]} for b in baseline["buildings"]]
        checks.append("prominent model button imports an exact baseline working copy without changing source geometry")
        report["working_project_id"] = opened["id"]
        camera_before = page.evaluate("window.goldenCity.getCamera()")
        box = page.locator("#viewer canvas").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.5)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.68, box["y"] + box["height"] * 0.57, steps=12)
        page.mouse.up()
        page.wait_for_timeout(600)
        camera_after = page.evaluate("window.goldenCity.getCamera()")
        assert math.dist(camera_before["position"], camera_after["position"]) > 0.1
        page.click("#street")
        assert abs(page.evaluate("window.goldenCity.getCamera().position[2]") - 1.7) < 0.002
        page.click("#aerial")
        assert page.evaluate("window.goldenCity.getCamera().position[2]") > 50
        page.locator("#viewer").screenshot(path=str(output / "existing-model-visible.png"))
        checks.append("existing WebGL model rotates and switches to street and aerial camera presets")
        assert page.evaluate("window.goldenCity.getProject().buildings") == opened["buildings"]

        for name in (*ARTIFACT_NAMES, "golden-city-materials.zip"):
            button = page.locator(f'[data-delivery="{name}"]')
            with page.expect_download(timeout=240000) as event:
                button.click()
            download = event.value
            assert download.suggested_filename == name
            target = directory / name
            download.save_as(str(target))
            assert download.failure() is None
            payload = target.read_bytes()
            assert payload == (ROOT / "deliverables" / name).read_bytes(), "Browser saved different bytes"
            details = {"filename": name, "bytes": len(payload), "sha256": sha256(payload), "transport": "verified_fetch_blob_download"}
            if name.endswith(".png"):
                details["dimensions"] = decoded_png(payload)
            else:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    assert archive.testzip() is None, "ZIP CRC failure"
                    for image_name in ARTIFACT_NAMES:
                        decoded_png(archive.read(image_name))
                        assert archive.read(image_name) == (ROOT / "deliverables" / image_name).read_bytes()
                    details["members"] = len(archive.namelist())
            report["downloads"].append(details)
            page.wait_for_function("!document.querySelector('[data-delivery]').disabled")
            assert "SHA-256" in page.locator("#deliveryStatus").inner_text()
        checks.append("each PNG and ZIP produces a real browser download with original bytes and independently reopened content")

        # Fail closed when the HTTP body does not match the server's claimed hash.
        payload = (ROOT / "deliverables" / "aerial.png").read_bytes()
        page.route("**/downloads/aerial.png", lambda route: route.fulfill(status=200, body=payload,
            headers={"Content-Type": "image/png", "Content-Length": str(len(payload)),
                     "X-Golden-City-File-Validation": "passed", "X-Golden-City-SHA256": "0" * 64}))
        unexpected_downloads = []
        record_unexpected = lambda download: unexpected_downloads.append(download)
        page.on("download", record_unexpected)
        page.click('[data-delivery="aerial.png"]')
        page.wait_for_function("document.querySelector('#deliveryStatus').textContent.includes('שונה מהקובץ שנבדק')")
        assert not unexpected_downloads, "Unverified bytes were offered as a download"
        page.remove_listener("download", record_unexpected)
        page.unroute("**/downloads/aerial.png")
        checks.append("browser refuses to save artifact bytes that fail the claimed SHA-256")

        with page.expect_download() as event:
            page.locator('#acceptanceImages a[download="aerial.png"]').click()
        native = event.value
        target = directory / "native-aerial.png"
        native.save_as(str(target))
        assert target.read_bytes() == payload and native.failure() is None
        decoded_png(target.read_bytes())
        checks.append("native relative download fallback also saves original PNG bytes without opening a tab")
        assert not report["page_errors"], report["page_errors"]
        browser.close()
    assert source_hashes() == before, "A source image/model/project or ZIP was changed"
    report["source_artifact_hashes"] = before
    checks.append("all existing source images models baseline project and packaged ZIP remain byte-identical")
    report["status"] = "passed"
    (output / "visible-delivery-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": report["status"], "checks": checks, "downloads": report["downloads"],
                      "recipient_access": report["recipient_access"]}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("/tmp/golden-visible-delivery"))
    args = parser.parse_args()
    run(args.url, args.output)
