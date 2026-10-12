"""Independently reopen the packaged offline viewer in Chromium.

The managed browser blocks file:// navigation, so the actual file is reopened as
UTF-8 and loaded into about:blank. HTTP is disabled and all external requests fail.
This tests self-contained HTML operation, not delivery to the recipient machine.
"""
import argparse
import base64
import hashlib
import json
import math
import struct
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from test_visible_delivery import ROOT, decoded_png, source_hashes


class Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script_id = None
        self.scripts = {}
        self.images = {}

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "script":
            self.script_id = attributes.get("id") or attributes.get("type")
        if tag == "img":
            self.images[attributes["id"]] = attributes["src"]

    def handle_data(self, text):
        if self.script_id:
            self.scripts[self.script_id] = self.scripts.get(self.script_id, "") + text

    def handle_endtag(self, tag):
        if tag == "script":
            self.script_id = None


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def glb_document(path):
    payload = path.read_bytes()
    assert payload[:4] == b"glTF"
    size = struct.unpack_from("<I", payload, 12)[0]
    return json.loads(payload[20:20 + size])


def verify_inventory(records, name):
    document = glb_document(ROOT / "examples" / "renders" / (name + ".glb"))
    source_inventory = json.loads((ROOT / "examples" / "renders" / (name + ".objects.json")).read_text())
    world = {obj["name"]: obj for obj in source_inventory["objects"] if obj["type"] == "MESH"}
    expected = {node["extras"]["persistent_id"]: node for node in document["nodes"] if "mesh" in node}
    actual = {record["persistent_id"]: record for record in records}
    assert len(actual) == len(records) == len(expected), "Mesh identity/count mismatch"
    assert actual.keys() == expected.keys(), "GLB objects were replaced"
    for identifier, node in expected.items():
        record = actual[identifier]
        primitives = document["meshes"][node["mesh"]]["primitives"]
        assert len(primitives) == 1, "Test requires the existing single-primitive GLB nodes"
        primitive = primitives[0]
        assert record["vertices"] == document["accessors"][primitive["attributes"]["POSITION"]]["count"]
        assert record["triangles"] == document["accessors"][primitive["indices"]]["count"] / 3
        assert record["triangles"] == world[identifier]["triangles"]
        assert record["building_id"] == node.get("extras", {}).get("building_id")
        minimum, maximum = world[identifier]["bounds_min"], world[identifier]["bounds_max"]
        # Existing GLB uses the standard glTF Y-up conversion of Blender XYZ.
        expected_min = [minimum[0], minimum[2], -maximum[1]]
        expected_max = [maximum[0], maximum[2], -minimum[1]]
        assert max(abs(a - b) for a, b in zip(record["bounds_min"], expected_min)) < 0.0003, identifier
        assert max(abs(a - b) for a, b in zip(record["bounds_max"], expected_max)) < 0.0003, identifier
    return len(records)


def verify_camera(actual, expected):
    for field in ("position", "target"):
        xyz = expected[field]
        assert math.dist(actual[field], [xyz[0], xyz[2], -xyz[1]]) < 0.002
    assert abs(actual["fov"] - expected["fov"]) < 0.001


def run(source, output):
    source = source.resolve()
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    raw = source.read_bytes()
    assert 1_000_000 < len(raw) < 40_000_000, "Unexpected standalone viewer size"
    text = raw.decode("utf-8", errors="strict")
    reader = Reader(); reader.feed(text)
    payload = json.loads(reader.scripts["viewerPayload"])
    imported = json.loads(reader.scripts["importmap"])["imports"]
    assert len(imported) == 5
    for url in imported.values():
        assert url.startswith("data:application/javascript;base64,")
        base64.b64decode(url.split(",", 1)[1], validate=True).decode("utf-8", errors="strict")
    assert "The MIT License" in text and "three.js authors" in text
    for record in payload["images"]:
        embedded = base64.b64decode(reader.images[record["id"]].split(",", 1)[1], validate=True)
        assert len(embedded) == record["bytes"] and sha256(embedded) == record["sha256"]
        assert embedded == (ROOT / "examples" / "renders" / record["name"]).read_bytes()
        decoded_png(embedded)
    for record in payload["models"]:
        embedded = base64.b64decode(record["base64"], validate=True)
        assert len(embedded) == record["bytes"] and sha256(embedded) == record["sha256"]
        assert embedded == (ROOT / "examples" / "renders" / record["name"]).read_bytes()
    report = {"status": "running", "recipient_access": "not_verified", "html": str(source),
              "bytes": len(raw), "sha256": sha256(raw), "opening_method": "independent_UTF8_reopen_and_Chromium_about_blank",
              "file_protocol_navigation_tested": False, "HTTP_offline": True, "checks": [],
              "network_requests": [], "page_errors": [], "downloads": [], "models": []}
    checks = report["checks"]
    checks.append("packaged HTML independently reopened; embedded PNG and GLB bytes equal existing verified sources")
    checks.append("all five MIT licensed JavaScript modules are self-contained data URLs")
    with tempfile.TemporaryDirectory(prefix="golden-offline-viewer-") as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"])
        context = browser.new_context(viewport={"width": 1500, "height": 1100}, accept_downloads=True)
        context.set_offline(True)
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))

        def block_network(route):
            if route.request.url.startswith(("data:", "blob:", "about:")):
                route.continue_()
            else:
                report["network_requests"].append(route.request.url)
                route.abort()

        page.route("**/*", block_network)
        page.set_content(text, wait_until="load", timeout=60000)
        page.wait_for_function("window.goldenOffline?.ready === true")
        page.evaluate("async () => Promise.all([...document.querySelectorAll('#images img')].map(img => img.decode()))")
        assert page.locator("#images img").count() == 3
        for img in page.locator("#images img").all():
            assert img.is_visible() and img.evaluate("img => img.naturalWidth >= 640 && img.naturalHeight >= 480")
        assert "פוענחו" in page.locator("#imageStatus").inner_text()
        page.locator("#images").screenshot(path=str(output / "offline-images-visible.png"))
        checks.append("all three original PNGs display and decode while HTTP is offline")
        assert page.locator("#viewport canvas").count() == 1
        assert page.evaluate("window.goldenOffline.getObjectIds()") == ["A", "B", "C"]
        initial = page.evaluate("window.goldenOffline.getCamera()")
        verify_camera(initial, payload["cameras"]["aerial"])
        checks.append("real WebGL initializes using the saved existing GLB camera")

        for key, name in [("baseline", "aerial"), ("edited", "edited-aerial")]:
            page.click("#" + key)
            assert page.evaluate("window.goldenOffline.getActiveModel()") == key
            actual_inventory = page.evaluate("window.goldenOffline.getInventory()")
            count = verify_inventory(actual_inventory, name)
            cameras = page.evaluate("window.goldenOffline.getImportedCameras()")
            document = glb_document(ROOT / "examples" / "renders" / (name + ".glb"))
            expected_camera = next(node for node in document["nodes"] if "camera" in node)
            assert len(cameras) == len(document["cameras"]) == 1
            assert math.dist(cameras[0]["position"], expected_camera["translation"]) < 0.00001
            assert max(abs(a - b) for a, b in zip(cameras[0]["quaternion"], expected_camera["rotation"])) < 0.00001
            assert abs(cameras[0]["fov"] - math.degrees(document["cameras"][0]["perspective"]["yfov"])) < 0.00001
            report["models"].append({"key": key, "original_glb": name + ".glb", "meshes": count,
                                     "cameras": len(cameras), "geometry_and_bounds": "passed"})
            screenshot = output / ("offline-" + key + "-model.png")
            page.locator("#viewport").screenshot(path=str(screenshot))
            with Image.open(screenshot) as picture:
                picture.load()
                pixels = list(picture.convert("RGB").getdata())
                white_fraction = sum(all(component >= 245 for component in pixel) for pixel in pixels) / len(pixels)
                assert white_fraction < .40, "Offline model view is washed out by display lighting"
                assert any(high - low > 60 for low, high in picture.convert("RGB").getextrema()), "Offline viewport is blank"
                report["models"][-1]["white_pixel_fraction"] = round(white_fraction, 5)
        checks.append("existing baseline and edited GLBs retain every mesh id vertex/triangle count world bounds and camera pose")
        untouched = page.evaluate("window.goldenOffline.getInventory()")
        page.click("#aerial")
        verify_camera(page.evaluate("window.goldenOffline.getCamera()"), payload["cameras"]["aerial"])
        page.click("#street")
        verify_camera(page.evaluate("window.goldenOffline.getCamera()"), payload["cameras"]["street"])
        assert abs(page.evaluate("window.goldenOffline.getCamera().position[1]") - 1.7) < 0.002
        page.click("#aerial")
        page.locator("#viewport").scroll_into_view_if_needed()
        camera_before = page.evaluate("window.goldenOffline.getCamera()")
        box = page.locator("#viewport canvas").bounding_box()
        page.mouse.move(box["x"] + box["width"] * .45, box["y"] + box["height"] * .5)
        page.mouse.down(); page.mouse.move(box["x"] + box["width"] * .65, box["y"] + box["height"] * .56, steps=12); page.mouse.up()
        page.wait_for_timeout(600)
        assert math.dist(camera_before["position"], page.evaluate("window.goldenOffline.getCamera().position")) > .1
        assert page.evaluate("window.goldenOffline.getInventory()") == untouched
        checks.append("offline orbit and exact saved aerial/street presets operate without changing model geometry")

        for record in payload["images"]:
            with page.expect_download() as event:
                page.locator('[data-image-download="' + record["name"] + '"]').click()
            download = event.value
            target = Path(directory) / record["name"]; download.save_as(str(target))
            assert download.failure() is None and download.suggested_filename == record["name"]
            saved = target.read_bytes()
            assert saved == (ROOT / "examples" / "renders" / record["name"]).read_bytes()
            report["downloads"].append({"name": record["name"], "bytes": len(saved), "sha256": sha256(saved),
                                       "dimensions": decoded_png(saved), "status": "passed"})
        with page.expect_download() as event:
            page.click("#downloadGlb")
        download = event.value
        target = Path(directory) / "edited-aerial.glb"; download.save_as(str(target))
        assert download.failure() is None and download.suggested_filename == "edited-aerial.glb"
        assert target.read_bytes() == (ROOT / "examples" / "renders" / "edited-aerial.glb").read_bytes()
        checks.append("offline PNG and original GLB download buttons save independently reopened byte-identical sources")
        assert not report["network_requests"], report["network_requests"]
        assert not report["page_errors"], report["page_errors"]
        checks.append("no external requests or JavaScript page errors")
        browser.close()
    assert source_hashes() == before, "Source images/models/ZIP changed"
    assert source.read_bytes() == raw, "Packaged HTML changed while testing"
    report["source_artifact_hashes"] = before
    checks.append("original artifacts ZIP and standalone HTML remain byte-identical after validation")
    report["status"] = "passed"
    (output / "offline-viewer-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: report[key] for key in ["status", "bytes", "sha256", "checks", "models", "downloads", "recipient_access"]},
                     ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "deliverables" / "golden-city-viewer.html")
    parser.add_argument("--output", type=Path, default=Path("/tmp/golden-offline-viewer"))
    args = parser.parse_args()
    run(args.source, args.output)
