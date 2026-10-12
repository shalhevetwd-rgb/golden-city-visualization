"""Reopen the actual v3 artifact offline and exercise the live intake workflow."""
import argparse
import base64
import hashlib
import json
import math
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class CheckLog(list):
    def append(self, value):
        super().append(value)
        print("PASS: " + value, flush=True)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class Reader(HTMLParser):
    def __init__(self):
        super().__init__(); self.current = None; self.scripts = {}; self.images = {}
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script": self.current = attrs.get("id") or attrs.get("type")
        if tag == "img" and attrs.get("id"): self.images[attrs["id"]] = attrs.get("src")
    def handle_data(self, value):
        if self.current: self.scripts[self.current] = self.scripts.get(self.current, "") + value
    def handle_endtag(self, tag):
        if tag == "script": self.current = None


def stable_geometry(records):
    fields = ["name", "persistent_id", "owner", "role", "vertices", "triangles", "bounds_min", "bounds_max"]
    return [{key: record[key] for key in fields} for record in records]


def source_hashes():
    paths = [ROOT / "golden-city-viewer.html"]
    paths += list((ROOT / "baseline").glob("*"))
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()) for path in paths if path.is_file()}


def camera_matches(actual, expected):
    for field in ["position", "target"]:
        xyz = expected[field]
        assert math.dist(actual[field], [xyz[0], xyz[2], -xyz[1]]) < .003
    assert abs(actual["fov"] - expected["fov"]) < .001
    assert abs(actual.get("shift_y", 0) - expected.get("shift_y", 0)) < .0001


def assert_facade_color(record, linear_rgb):
    for actual,scale in zip(record["linear_colors"],record["tint_scales"]):
        assert actual is not None and math.dist(actual,[value*scale for value in linear_rgb]) < 1e-6,record["name"]


def linear_rgb(hex_color):
    srgb = [int(hex_color[index:index+2],16)/255 for index in [1,3,5]]
    return [value/12.92 if value<=.04045 else ((value+.055)/1.055)**2.4 for value in srgb]


def browser_launch(playwright):
    return playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
        args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"])


def run_offline(source, output):
    output.mkdir(parents=True,exist_ok=True)
    before = source_hashes(); raw = source.read_bytes(); text = raw.decode("utf-8")
    assert len(raw) < 99_000_000
    reader = Reader(); reader.feed(text)
    payload = json.loads(reader.scripts["viewerPayload"])
    assert payload["synthetic"] is True and payload["version"] == "architectural-v3"
    imports = json.loads(reader.scripts["importmap"])["imports"]
    assert len(imports) == 5 and all(url.startswith("data:application/javascript;base64,") for url in imports.values())
    for record in payload["models"]:
        original = (source.parent / record["name"]).read_bytes()
        if record.get("encoding") == "glb_shared_bin":
            embedded = b"".join(base64.b64decode(value,validate=True) for value in [record["header_base64"],record["json_chunk_base64"],payload["shared_glb_bin_base64"]])
        else:
            embedded = base64.b64decode(record["base64"], validate=True)
        assert embedded == original and sha256(embedded) == record["sha256"]
    for record in payload["images"]:
        embedded = base64.b64decode(reader.images[record["id"]].split(",", 1)[1], validate=True)
        assert embedded == (source.parent / record["name"]).read_bytes()
        assert sha256(embedded) == record["sha256"]
    report = {"status":"running","mode":"offline_reopened_actual_HTML","software_webgl":True,
              "html_sha256":sha256(raw),"checks":CheckLog(),"network_requests":[],"page_errors":[],"console_errors":[]}
    checks = report["checks"]
    checks.append("embedded modules PNGs and GLBs reopen from exact original bytes with matching SHA-256")
    with tempfile.TemporaryDirectory(prefix="golden-v3-browser-") as directory, sync_playwright() as playwright:
        browser = browser_launch(playwright)
        context = browser.new_context(viewport={"width":1500,"height":1100}, accept_downloads=True)
        context.set_offline(True); page = context.new_page(); page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", lambda event: report["console_errors"].append(event.text) if event.type == "error" else None)
        def route_handler(route):
            if route.request.url.startswith(("about:", "blob:", "data:")): route.continue_()
            else: report["network_requests"].append(route.request.url); route.abort()
        page.route("**/*", route_handler); page.set_content(text, wait_until="load", timeout=90000)
        page.wait_for_function("window.goldenOffline?.ready === true", timeout=90000)
        page.evaluate("async()=>Promise.all([...document.querySelectorAll('#images img')].map(img=>img.decode()))")
        assert page.locator("#images img").count() == 4
        assert all(img.evaluate("img=>img.naturalWidth>=640&&img.naturalHeight>=480") for img in page.locator("#images img").all())
        assert page.evaluate("window.goldenOffline.getObjectIds()") == ["A", "B", "C"]
        for name in ["aerial", "street", "architectural"]:
            page.click("#" + name)
            camera_matches(page.evaluate("window.goldenOffline.getCamera()"), payload["cameras"][name])
        street = payload["cameras"]["street"]
        if street.get("height_above_source_ground_m") is not None:
            assert abs(street["height_above_source_ground_m"] - 1.7) < .001
            assert abs(street["position"][2] - street["ground_z"] - 1.7) < .001
        checks.append("all four PNGs and real WebGL display; three saved camera poses FOV and ground-relative street height match")
        page.click("#aerial"); page.locator("#viewport").scroll_into_view_if_needed()
        before_orbit = page.evaluate("window.goldenOffline.getCamera()")
        box = page.locator("#viewport canvas").bounding_box()
        page.mouse.move(box["x"] + box["width"]*.45, box["y"] + box["height"]*.5)
        page.mouse.down(); page.mouse.move(box["x"] + box["width"]*.6, box["y"] + box["height"]*.57, steps=12); page.mouse.up()
        page.wait_for_timeout(600)
        assert math.dist(before_orbit["position"], page.evaluate("window.goldenOffline.getCamera().position")) > .1
        page.fill("#cameraName", "מצלמת בדיקה מקומית"); page.click("#saveCamera")
        saved_camera = page.evaluate("window.goldenOffline.getSavedCameras()['מצלמת בדיקה מקומית']")
        page.click("#street"); page.select_option("#savedCameras", "מצלמת בדיקה מקומית"); page.click("#restoreCamera")
        restored_camera = page.evaluate("window.goldenOffline.getCamera()")
        assert math.dist(saved_camera["position"], restored_camera["position"]) < .003
        assert math.dist(saved_camera["target"], restored_camera["target"]) < .003
        checks.append("orbit changes camera; local camera save and restore preserve pose")

        page.click("#aerial"); page.select_option("#editBuilding", "B")
        page.locator("#viewport").scroll_into_view_if_needed()
        page.wait_for_timeout(100)
        point = page.evaluate("window.goldenOffline.getBuildingScreenPoint('A')")
        assert page.evaluate("point=>document.elementFromPoint(point.x,point.y)?.tagName", point) == "CANVAS", point
        page.mouse.click(point["x"], point["y"])
        assert page.evaluate("window.goldenOffline.getSelectedBuilding()") == "A", point
        checks.append("raycasting selects the clicked building rather than only the dropdown")
        original_inventory = page.evaluate("window.goldenOffline.getInventory()")
        facade_count = page.evaluate("window.goldenOffline.getFacadeCount('A')")
        assert facade_count > 1, "The v3 test must cover every separate facade mesh"
        page.locator("#editColor").evaluate("node=>{node.value='#a74431';}")
        page.click("#previewColor")
        changed = page.evaluate("window.goldenOffline.getInventory()")
        assert stable_geometry(changed) == stable_geometry(original_inventory)
        changed_names = []
        for previous, current in zip(original_inventory, changed):
            if previous["owner"] == "A" and previous["role"] == "facade":
                assert previous["colors"] != current["colors"], previous["name"]
                assert_facade_color(current,linear_rgb('#a74431'))
                changed_names.append(previous["name"])
            else:
                assert previous == current, "A window roof context or unselected building was edited"
        assert len(changed_names) == facade_count
        page.locator("#viewport").screenshot(path=str(output / "v3-facade-preview.png"))
        page.click("#undoColor")
        assert page.evaluate("window.goldenOffline.getInventory()") == original_inventory
        checks.append("all owner-A role-facade meshes change together; geometry windows roofs B C and context remain identical; undo restores every material")
        report["edited_facade_meshes"] = changed_names

        for key in ["edited", "baseline"]:
            page.click("#" + key)
            assert page.evaluate("window.goldenOffline.getActiveModel()") == key
        page.locator("#compareRange").evaluate("node=>{node.value='75';node.dispatchEvent(new Event('input',{bubbles:true}));}")
        assert "25%" in page.locator("#comparisonAfter").evaluate("node=>node.style.clipPath")
        checks.append("baseline and edited saved versions switch; before-after image comparison slider responds")
        page.click("#aerial"); page.locator("#viewport").screenshot(path=str(output / "v3-aerial-webgl.png"))
        for record in payload["images"]:
            with page.expect_download() as event:
                page.locator('[data-image-download="' + record["name"] + '"]').click()
            target = Path(directory) / record["name"]; event.value.save_as(str(target))
            assert target.read_bytes() == (source.parent / record["name"]).read_bytes()
        with page.expect_download() as event: page.click("#downloadGlb")
        target = Path(directory) / "model.glb"; event.value.save_as(str(target))
        assert target.read_bytes() == (source.parent / "model.glb").read_bytes()
        checks.append("four PNG downloads and baseline GLB download save exact original bytes after editing and undo")
        page.fill("#projectForm [name=name]", "Synthetic UI test dossier")
        page.fill("#projectForm [name=address]", "Synthetic test address")
        page.fill("#projectForm [name=block]", "12345")
        page.fill("#projectForm [name=parcels]", "1,2,3"); page.locator("#projectForm button").click()
        assert not page.locator("#modelSection").is_visible()
        dossier = page.evaluate("window.goldenOffline.getDossier()")
        assert dossier["synthetic"] is False and dossier["model"] is None
        source_file = Path(directory) / "source.png"; Image.new("RGB", (20,20), "#7a9373").save(source_file)
        page.set_input_files("#localFiles", str(source_file)); page.wait_for_function("window.goldenOffline.getDossier().files.length===1")
        file_record = page.evaluate("window.goldenOffline.getDossier().files[0]")
        assert file_record["status"] == "image_decoded_no_georeference" and file_record["verification"] == "unverified"
        assert file_record["sha256"] == sha256(source_file.read_bytes())
        page.click("#showDemo")
        assert page.locator("#modelSection").is_visible()
        assert page.evaluate("window.goldenOffline.getDossier().model") is None
        checks.append("real local dossier and decoded user source stay separate from the synthetic demonstration")
        page.screenshot(path=str(output / "v3-offline-interface.png"), full_page=True)
        assert not report["network_requests"] and not report["page_errors"] and not report["console_errors"], report
        checks.append("zero HTTP requests zero browser JavaScript errors and zero console errors offline")
        browser.close()
    assert source_hashes() == before
    report["status"] = "passed"; report["source_bytes_unchanged"] = True
    (output / "offline-v3-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def run_live(url, output):
    output.mkdir(parents=True,exist_ok=True)
    before = source_hashes()
    report = {"status":"running","url":url,"checks":CheckLog(),"page_errors":[],"console_errors":[]}
    with tempfile.TemporaryDirectory(prefix="golden-intake-browser-") as directory, sync_playwright() as playwright:
        browser = browser_launch(playwright); page = browser.new_page(viewport={"width":1500,"height":1100})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", lambda event: report["console_errors"].append(event.text) if event.type == "error" else None)
        page.goto(url, wait_until="domcontentloaded"); page.wait_for_function("window.goldenCity !== undefined")
        assert page.locator("#capabilityList li").count() >= 5
        report["checks"].append("live capability API lists actual format statuses")
        page.fill("#dossierName", "Synthetic browser workflow test")
        page.fill("#dossierAddress", "Synthetic test address")
        page.fill("#dossierBlock", "12345"); page.fill("#dossierParcels", "1,2,3")
        page.click("#createDossier")
        page.wait_for_function("window.goldenCity.getProject().synthetic===false&&!document.querySelector('#sample').disabled")
        project = page.evaluate("window.goldenCity.getProject()")
        assert project["boundary"] is None and project["crs"] is None and project["buildings"] == []
        assert page.evaluate("window.goldenCity.getObjectIds()") == []
        assert page.locator("#renderImage").is_disabled() and page.locator("#approve").is_disabled()
        assert page.locator("#readinessMissing li").count() >= 6
        report["project_id"] = project["id"]
        report["checks"].append("new real dossier creates no geometry and clears the synthetic scene; missing evidence is displayed")
        path = Path(directory) / "source.png"; Image.new("RGB",(20,20),"#817665").save(path)
        page.set_input_files("#intakeFile", str(path)); page.click("#intakeUpload")
        page.wait_for_function("document.querySelector('#intakeStatus').textContent.includes('המקור נקלט')")
        assert page.locator("#intakeResults li").count() >= 1
        assert page.evaluate("window.goldenCity.getProject().id") == project["id"]
        assert page.evaluate("window.goldenCity.getObjectIds()") == []
        report["checks"].append("source is uploaded to the same dossier with a content inspection result and no inferred building geometry")
        page.click("#readinessCheck"); page.wait_for_function("!document.querySelector('#sample').disabled")
        readiness = page.evaluate("window.goldenCity.getReadiness()")
        assert readiness["evidence_complete"] is False and readiness["ready_for_final_render"] is False and readiness["certification"] is False
        page.select_option("#renderMode","final"); assert page.locator("#renderImage").is_disabled()
        response = page.request.post(url.rstrip("/")+"/api/render",data={"id":project["id"],"mode":"final","width":640,"height":480})
        assert not response.ok
        report["checks"].append("UI and real server reject final rendering without evidence; no certification is claimed")
        page.locator("#sourceClassification").screenshot(path=str(output / "live-dossier-classification.png"))
        page.screenshot(path=str(output / "live-workflow.png"),full_page=True)
        assert not report["page_errors"] and not report["console_errors"], report
        browser.close()
    assert source_hashes() == before
    report["status"]="passed"; report["source_bytes_unchanged"]=True
    (output/"live-v3-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def run_native(url, output, expected_version, render_button=True):
    """Use real native bytes; the button's real server job uses small test settings."""
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report = {"status":"running","url":url,"software_webgl":True,
              "checks":CheckLog(),"page_errors":[],"console_errors":[],"requests":[]}
    with tempfile.TemporaryDirectory(prefix="golden-native-ui-") as directory, sync_playwright() as playwright:
        browser = browser_launch(playwright)
        page = browser.new_page(viewport={"width":1500,"height":1100},accept_downloads=True)
        # Chromium's default inspector body cache is smaller than this exact
        # self-contained HTML. Retain its actual navigation bytes for hashing.
        cdp = page.context.new_cdp_session(page)
        # The Hebrew document is held as UTF-16 by the inspector: reserve more
        # than twice its UTF-8 delivery size, including the subsequent reload.
        cdp.send("Network.enable",{"maxTotalBufferSize":500_000_000,"maxResourceBufferSize":200_000_000})
        document_requests=[]
        cdp.on("Network.responseReceived",lambda event:document_requests.append(event["requestId"]) if event["type"]=="Document" else None)
        def navigation_sha256():
            body=cdp.send("Network.getResponseBody",{"requestId":document_requests[-1]})
            raw=base64.b64decode(body["body"],validate=True) if body["base64Encoded"] else body["body"].encode("utf-8")
            return sha256(raw)
        page.set_default_timeout(60000)
        page.on("pageerror",lambda error:report["page_errors"].append(str(error)))
        page.on("console",lambda event:report["console_errors"].append(event.text) if event.type=="error" else None)
        endpoint = url.rstrip("/")
        catalogue = page.request.get(endpoint+"/api/architectural-versions").json()["versions"]
        record = next(item for item in catalogue if item["version_id"] == expected_version)
        assert record["material_export_verified"] is True
        response=page.goto(endpoint+"/showcase/v3/viewer.html",wait_until="domcontentloaded",timeout=90000)
        assert response.ok
        report["html_sha256"]=navigation_sha256()
        page.wait_for_function("window.goldenOffline?.ready === true",timeout=90000)
        page.wait_for_function("id=>window.goldenOffline.getNativeVersions().some(record=>record.version_id===id)",arg=expected_version)
        assert page.locator("#renderNative").is_enabled()
        baseline = page.evaluate("window.goldenOffline.getInventory()")
        originals = page.evaluate("window.goldenOffline.getSourceHashes()")
        page.click("#architectural")
        preset_camera = page.evaluate("window.goldenOffline.getCamera()")
        key = "native:"+expected_version
        paused_download = []
        model_route = "**"+record["model_url"]
        page.route(model_route,lambda route:paused_download.append(route))
        page.select_option("#nativeVersions",key);page.click("#restoreNative")
        for attempt in range(40):
            if paused_download:break
            page.wait_for_timeout(50)
        assert len(paused_download)==1
        assert page.locator("#restoreNative").is_disabled() and page.locator("#renderNative").is_disabled()
        page.locator("#viewport").scroll_into_view_if_needed()
        box=page.locator("#viewport canvas").bounding_box()
        page.mouse.move(box["x"]+box["width"]*.45,box["y"]+box["height"]*.5)
        page.mouse.down();page.mouse.move(box["x"]+box["width"]*.5,box["y"]+box["height"]*.53,steps=6);page.mouse.up()
        page.wait_for_timeout(250)
        page.fill("#cameraName","מצלמה בזמן טעינה");page.click("#saveCamera")
        page.select_option("#savedCameras","מצלמה בזמן טעינה");page.click("#restoreCamera")
        camera_before = page.evaluate("window.goldenOffline.getCamera()")
        assert math.dist(preset_camera["position"],camera_before["position"])>.1
        paused_download[0].continue_();page.unroute(model_route)
        page.wait_for_function("key=>window.goldenOffline.getActiveModel()===key",arg=key,timeout=90000)
        adopted = page.evaluate("window.goldenOffline.getInventory()")
        assert stable_geometry(adopted) == stable_geometry(baseline)
        for original,current in zip(baseline,adopted):
            if original["owner"]==record["building_id"] and original["role"]=="facade":
                assert_facade_color(current,record["color"])
            else:
                assert original == current, original["name"]
        camera_after = page.evaluate("window.goldenOffline.getCamera()")
        assert math.dist(camera_before["position"],camera_after["position"]) < .003
        assert math.dist(camera_before["target"],camera_after["target"]) < .003
        assert originals == page.evaluate("window.goldenOffline.getSourceHashes()")
        image = page.locator("#native-result-"+expected_version+" img")
        image.evaluate("img=>img.decode()")
        assert image.evaluate("img=>[img.naturalWidth,img.naturalHeight]") == [record["render_settings"]["width"],record["render_settings"]["height"]]
        with page.expect_download() as download:page.click("#downloadGlb")
        path = Path(directory)/"native.glb";download.value.save_as(str(path))
        assert sha256(path.read_bytes()) == record["model_sha256"]
        report["checks"].append("verified catalogue adopts actual native GLB and PNG; all target facade colors and tint scales match; unselected objects stay identical; orbit during paused real GLB loading is retained and overlapping native actions are disabled")
        report["restored_version_id"] = expected_version
        if render_button:
            page.click("#baseline");page.select_option("#editBuilding","C")
            page.locator("#editColor").evaluate("node=>{node.value='#657d87';}")
            page.select_option("#nativeCamera","street")
            camera_before = page.evaluate("window.goldenOffline.getCamera()")
            def small_test_job(route):
                body = route.request.post_data_json
                assert body["building_id"]=="C" and body["operation"]=="facade_color"
                assert [body["width"],body["height"],body["samples"]] == [960,600,48]
                report["requests"].append({"ui_request":body.copy(),"test_render_settings":{"width":400,"height":250,"samples":8}})
                body.update(width=400,height=250,samples=8)
                route.continue_(post_data=json.dumps(body))
            page.route("**/api/architectural-edit",small_test_job)
            versions_before = {item["version_id"] for item in page.evaluate("window.goldenOffline.getNativeVersions()")}
            page.click("#renderNative")
            assert page.locator("#renderNative").is_disabled()
            assert "מספר דקות" in page.locator("#nativeStatus").text_content()
            page.wait_for_function("ids=>window.goldenOffline.getNativeVersions().some(record=>!ids.includes(record.version_id))",arg=list(versions_before),timeout=180000)
            page.wait_for_function("!document.querySelector('#renderNative').disabled",timeout=90000)
            fresh = next(item for item in page.evaluate("window.goldenOffline.getNativeVersions()") if item["version_id"] not in versions_before)
            assert page.evaluate("window.goldenOffline.getActiveModel()") == fresh["key"]
            assert "הופק PNG" in page.locator("#nativeStatus").text_content()
            camera_after = page.evaluate("window.goldenOffline.getCamera()")
            assert math.dist(camera_before["position"],camera_after["position"]) < .003
            assert math.dist(camera_before["target"],camera_after["target"]) < .003
            changed = page.evaluate("window.goldenOffline.getInventory()")
            assert stable_geometry(changed) == stable_geometry(baseline)
            for original,current in zip(baseline,changed):
                if original["owner"]=="C" and original["role"]=="facade":
                    assert original["colors"] != current["colors"], original["name"]
                    assert_facade_color(current,linear_rgb('#657d87'))
                else:assert original == current, original["name"]
            assert originals == page.evaluate("window.goldenOffline.getSourceHashes()")
            native_image = page.locator("#native-result-"+fresh["version_id"]+" img")
            native_image.evaluate("img=>img.decode()")
            assert native_image.evaluate("img=>[img.naturalWidth,img.naturalHeight]") == [400,250]
            page.locator("#viewport").screenshot(path=str(output/"native-ui-new-model.png"))
            page.locator("#native-result-"+fresh["version_id"]).screenshot(path=str(output/"native-ui-new-render.png"))
            report["created_version_id"] = fresh["version_id"]
            report["checks"].append("real render button sends source SHA target color and camera to backend; reduced 400x250 8-sample integration job produces fresh PNG/GLB; all C facades change while A B windows roofs geometry and original bytes remain unchanged")
            key = fresh["key"]
        response=page.reload(wait_until="domcontentloaded",timeout=90000)
        assert response.ok and navigation_sha256()==report["html_sha256"]
        page.wait_for_function("window.goldenOffline?.ready===true",timeout=90000)
        page.wait_for_function("key=>window.goldenOffline.getNativeVersions().some(record=>record.key===key)",arg=key)
        page.select_option("#nativeVersions",key);page.click("#restoreNative")
        page.wait_for_function("key=>window.goldenOffline.getActiveModel()===key",arg=key,timeout=90000)
        assert page.evaluate("window.goldenOffline.getSourceHashes()") == originals
        report["checks"].append("reloaded service catalogue lazily reopens the saved native version; embedded originals remain exact")
        assert not report["page_errors"] and not report["console_errors"],report
        browser.close()
    assert source_hashes()==before
    report["status"]="passed";report["source_bytes_unchanged"]=True
    (output/"native-ui-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--viewer",type=Path,default=ROOT/"outputs/v3/viewer.html")
    parser.add_argument("--output",type=Path,default=ROOT/"reports/browser-v3")
    parser.add_argument("--url",default="http://127.0.0.1:8000")
    parser.add_argument("--offline-only",action="store_true")
    parser.add_argument("--live-only",action="store_true")
    parser.add_argument("--native-only",action="store_true")
    parser.add_argument("--native-version")
    parser.add_argument("--skip-native-render",action="store_true")
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.native_only:
        if not args.native_version:parser.error("--native-only requires an actual --native-version from the server catalogue")
        print(json.dumps(run_native(args.url,args.output,args.native_version,not args.skip_native_render),ensure_ascii=False,indent=2))
    else:
        if not args.live_only: print(json.dumps(run_offline(args.viewer,args.output),ensure_ascii=False,indent=2))
        if not args.offline_only: print(json.dumps(run_live(args.url,args.output),ensure_ascii=False,indent=2))
