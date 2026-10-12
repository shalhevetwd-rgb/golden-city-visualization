"""Exercise the real Hebrew interface, Three.js WebGL scene, and persisted edits.

Run against an already-running development server:
  .venv/bin/python scripts/browser_acceptance.py --url http://127.0.0.1:8000
Chromium uses verified system software WebGL here; this is not a hardware-GPU test.
The test creates a synthetic project, two environment assets, screenshots, and a
real server render. No external geographic or photographic data is required.
"""
import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def close_camera(actual, expected):
    return (
        math.dist(actual["position"], expected["position"]) < 0.002
        and math.dist(actual["target"], expected["target"]) < 0.002
        and abs(actual["fov"] - expected["fov"]) < 0.001
    )


def run(url, output, skip_render=False):
    output.mkdir(parents=True, exist_ok=True)
    report = {"url": url, "software_webgl": True, "checks": [], "page_errors": [], "console_errors": []}
    checks = report["checks"]
    with tempfile.TemporaryDirectory(prefix="golden-browser-") as temp_name, sync_playwright() as playwright:
        temp = Path(temp_name)
        photo = temp / "synthetic-aerial.png"
        image = Image.new("RGB", (64, 64), "#c4d2ae")
        for x in range(0, 64, 8):
            for y in range(64):
                image.putpixel((x, y), (99, 130, 89))
        image.save(photo)
        model = temp / "synthetic-context.obj"
        model.write_text("o context\nv -48 -48 0\nv -44 -48 0\nv -44 -44 3\nv -48 -44 3\nf 1 2 3 4\n")
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1500, "height": 1100}, device_scale_factor=1)
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", lambda event: report["console_errors"].append(event.text) if event.type == "error" else None)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("window.goldenCity !== undefined")
        assert page.evaluate("window.goldenCity.isWebGLReady()"), "Real WebGL initialization failed"
        assert page.locator("#viewer canvas").count() == 1
        checks.append("real WebGL canvas initializes")
        prior_id = page.evaluate("window.goldenCity.getProject()?.id")
        page.click("#sample")
        page.wait_for_function("(id) => window.goldenCity.getProject()?.id !== id && !document.querySelector('#sample').disabled", arg=prior_id)
        page.wait_for_function("window.goldenCity.getObjectIds().length === 3")
        page.wait_for_function("document.querySelector('#title').textContent.includes('גרסה 1')")
        baseline = page.evaluate("window.goldenCity.getProject()")
        report["project_id"] = baseline["id"]
        assert [b["properties"]["id"] for b in baseline["buildings"]] == ["A", "B", "C"]
        assert baseline.get("synthetic") is True
        checks.append("existing synthetic model imported with three stable object ids")
        unaffected = {b["properties"]["id"]: digest(b) for b in baseline["buildings"] if b["properties"]["id"] != "A"}

        def project():
            return page.evaluate("window.goldenCity.getProject()")

        def click_revision(selector):
            before = project()
            version = before["version"]
            page.click(selector)
            page.wait_for_function("(v) => window.goldenCity.getProject().version > v", arg=version)
            page.wait_for_function("!document.querySelector('#sample').disabled")
            updated = project()
            assert updated["id"] == before["id"] == report["project_id"], "Revision changed or targeted a different project"
            page.wait_for_function("(v) => document.querySelector('#title').textContent.endsWith('גרסה ' + v)", arg=updated["version"])
            return updated

        click_revision("#approve")
        assert project()["approved"]
        page.locator("#viewer").scroll_into_view_if_needed()
        before_orbit = page.evaluate("window.goldenCity.getCamera()")
        box = page.locator("#viewer canvas").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.67, box["y"] + box["height"] * 0.56, steps=10)
        page.mouse.up()
        page.wait_for_timeout(800)
        assert not close_camera(page.evaluate("window.goldenCity.getCamera()"), before_orbit), "Orbit drag did not change camera"
        checks.append("orbit controls change the actual 3D camera")

        page.select_option("#building", "A")
        page.click("#street")
        street = page.evaluate("window.goldenCity.getCamera()")
        assert abs(street["position"][2] - 1.7) < 0.002, street
        assert street["target"][2] > street["position"][2], "Street camera must allow upward viewing"
        page.locator("#viewer").screenshot(path=str(output / "street-webgl.png"))
        checks.append("street camera stays at 1.7 metres while looking upward")
        page.click("#aerial")
        saved_camera = page.evaluate("window.goldenCity.getCamera()")
        assert saved_camera["position"][2] > 50
        page.fill("#cameraName", "בדיקת מצלמה אווירית")
        click_revision("#saveCamera")
        assert close_camera(project()["cameras"]["בדיקת מצלמה אווירית"], saved_camera)
        page.click("#street")
        page.select_option("#cameras", "בדיקת מצלמה אווירית")
        page.click("#restoreCamera")
        assert close_camera(page.evaluate("window.goldenCity.getCamera()"), saved_camera)
        checks.append("camera save and restore round trip preserves XYZ target and FOV")

        page.select_option("#assetKind", "aerial_photo")
        page.set_input_files("#assetFile", str(photo))
        for selector, value in [("#assetMinX", "220000"), ("#assetMinY", "630000"), ("#assetMaxX", "220100"), ("#assetMaxY", "630100")]:
            page.locator(selector).evaluate("(node, value) => {node.value = value;}", value)
        click_revision("#assetUpload")
        page.wait_for_function("window.goldenCity.getEnvironmentLayers().some(l => l.kind === 'aerial_photo')")
        page.select_option("#assetKind", "model")
        page.set_input_files("#assetFile", str(model))
        click_revision("#assetUpload")
        page.wait_for_function("window.goldenCity.getEnvironmentLayers().length === 2")
        assert len(project()["assets"]) == 2
        assert page.evaluate("window.goldenCity.getObjectIds()") == ["A", "B", "C"]
        checks.append("georeferenced photo and separate OBJ context layer load without replacing building objects")
        before_edit = project()
        restore_version = before_edit["version"]

        page.select_option("#building", "A")
        page.select_option("#material", "plaster")
        page.locator("#color").evaluate("(node) => {node.value = '#a74334';}")
        click_revision("#style")
        updated = project()
        selected = next(b for b in updated["buildings"] if b["properties"]["id"] == "A")
        assert selected["properties"]["color"] == "#a74334"
        assert selected["properties"]["material"] == "plaster"
        for b in updated["buildings"]:
            if b["properties"]["id"] in unaffected:
                assert digest(b) == unaffected[b["properties"]["id"]], "Unselected building changed"
        checks.append("one-building facade edit leaves B and C byte-equivalent in canonical JSON")
        page.locator("details").filter(has=page.locator("#addBalcony")).locator("summary").click()
        page.fill("#floor", "2")
        page.fill("#balconyWidth", "4")
        page.fill("#balconyDepth", "2")
        page.fill("#balconyOffset", "3")
        click_revision("#addBalcony")
        updated = project()
        selected = next(b for b in updated["buildings"] if b["properties"]["id"] == "A")
        assert len(selected["properties"]["balconies"]) == 1
        for b in updated["buildings"]:
            if b["properties"]["id"] in unaffected:
                assert digest(b) == unaffected[b["properties"]["id"]]
        checks.append("balcony footprint and top elevation persist only on the selected building")
        page.click("#balconyCheck")
        page.wait_for_function("document.querySelector('#analysisBalcony').options.length === 1 && !document.querySelector('#balconySection').disabled")
        assert "לא נקבע" in page.locator("#balconyReport").inner_text()
        assert page.locator("#analysisView path").count() >= 1
        page.click("#balconySection")
        assert page.locator("#analysisView rect").count() >= 1
        page.locator("#analysisView").screenshot(path=str(output / "balcony-section.png"))
        checks.append("known-geometry sky analysis, top view, and true east-west section display without certification")
        page.click("#aerial")
        page.locator("#viewer").screenshot(path=str(output / "edited-webgl.png"))

        page.select_option("#versions", str(restore_version))
        restored = click_revision("#restoreVersion")
        assert restored["version"] > updated["version"]
        assert restored["buildings"] == before_edit["buildings"]
        assert restored["assets"] == before_edit["assets"]
        assert restored["cameras"] == before_edit["cameras"]
        assert len(restored["history"]) > len(before_edit["history"])
        page.wait_for_function("window.goldenCity.getEnvironmentLayers().length === 2")
        checks.append("version restore appends a revision and restores buildings cameras and environment references")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.goldenCity !== undefined")
        page.select_option("#projects", restored["id"])
        page.wait_for_function("(id) => window.goldenCity.getProject().id === id && !document.querySelector('#projects').disabled", arg=restored["id"])
        page.wait_for_function("window.goldenCity.getEnvironmentLayers().length === 2")
        assert project()["buildings"] == before_edit["buildings"]
        page.select_option("#cameras", "בדיקת מצלמה אווירית")
        page.click("#restoreCamera")
        assert close_camera(page.evaluate("window.goldenCity.getCamera()"), saved_camera)
        checks.append("reloading page restores persisted project and saved camera")
        page.locator("#viewer").screenshot(path=str(output / "aerial-webgl.png"))
        if not skip_render:
            page.click("#renderImage")
            page.wait_for_function("document.querySelector('#gallery img') !== null", timeout=240000)
            page.wait_for_function("document.querySelector('#status').textContent.includes('תמונת ההדמיה מוכנה')", timeout=240000)
            render_url = page.locator("#gallery img").first.get_attribute("src")
            assert render_url.startswith("/renders/" + report["project_id"] + "-"), "Render belongs to a different project"
            response = page.request.get(url.rstrip("/") + render_url)
            assert response.ok and response.headers.get("content-type", "").startswith("image/png")
            render_path = output / "ui-render.png"
            render_path.write_bytes(response.body())
            with Image.open(render_path) as rendered_image:
                assert rendered_image.size == (1000, 750)
                assert len(rendered_image.convert("RGB").getcolors(1_000_000) or []) > 50, "Rendered image is blank"
            checks.append("render button creates a real nonblank server PNG and displays its gallery entry")
            report["render_url"] = render_url
            report["render_limit"] = "Server Blender renders omit imported photo and OBJ context assets; those layers are currently viewer foundations."
        page.screenshot(path=str(output / "interface.png"), full_page=True)
        assert not report["page_errors"], report["page_errors"]
        assert not report["console_errors"], report["console_errors"]
        checks.append("no browser JavaScript or console errors")
        report["status"] = "passed"
        browser.close()
    (output / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "examples" / "browser")
    parser.add_argument("--skip-render", action="store_true", help="Skip only the slow server PNG check.")
    args = parser.parse_args()
    run(args.url, args.output, args.skip_render)
