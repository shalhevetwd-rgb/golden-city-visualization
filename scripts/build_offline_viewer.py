"""Package existing verified images and GLBs into one offline HTML viewer.

No model generation, extrusion, source edits, rendering, or server is involved.
Three.js remains MIT licensed; the full license is included in the HTML.
The independent browser test is mandatory before this new artifact is delivered.
"""
import argparse
import base64
import hashlib
import html
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from delivery_guard import verify_for_delivery


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def data_uri(payload, mime):
    return "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii")


def glb_json(payload):
    if len(payload) < 20 or payload[:4] != b"glTF":
        raise ValueError("Expected an existing binary glTF")
    version, size, chunk_size, chunk_type = struct.unpack_from("<4I", payload, 4)
    if version != 2 or size != len(payload) or chunk_type != 0x4E4F534A:
        raise ValueError("Invalid binary glTF header")
    document = json.loads(payload[20:20 + chunk_size])
    if any("uri" in buffer for buffer in document.get("buffers", [])) or document.get("images"):
        raise ValueError("This viewer accepts only self-contained existing untextured GLBs")
    return document


def embedded_module(path, replacements=None):
    text = path.read_text()
    for original, replacement in (replacements or {}).items():
        if original not in text:
            raise ValueError("Required module import was not found: " + original)
        text = text.replace(original, replacement)
    imports = re.findall(r"(?m)^[ \t]*(?:(?:import|export)\b[^\n]*|\})\s+from\s+['\"]([^'\"]+)['\"]", text)
    if any(imported.startswith((".", "/", "http:", "https:")) for imported in imports):
        raise ValueError("Module still needs an external relative import: " + str(path))
    return data_uri(text.encode(), "application/javascript")


APPLICATION = r"""
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const payload = JSON.parse(document.getElementById('viewerPayload').textContent);
const element = id => document.getElementById(id);
const status = element('viewerStatus');
const fromBase64 = encoded => Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
const asYUp = xyz => [xyz[0], xyz[2], -xyz[1]];
const decodedImages = Promise.all([...document.querySelectorAll('#images img')].map(img => img.decode()));

function saveBytes(bytes, name, type) {
  const url = URL.createObjectURL(new Blob([bytes], {type}));
  const link = document.createElement('a'); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
for (const button of document.querySelectorAll('[data-image-download]')) {
  button.onclick = () => {
    const record = payload.images.find(image => image.name === button.dataset.imageDownload);
    const bytes = fromBase64(element(record.id).src.split(',')[1]);
    if (bytes.byteLength !== record.bytes) throw Error('התמונה אינה שלמה');
    saveBytes(bytes, record.name, 'image/png');
    element('downloadStatus').textContent = 'בקשת שמירה של ' + record.name + ' נשלחה לדפדפן. הבייטים הם של התמונה המקורית המשובצת בקובץ.';
  };
}

let renderer, controls, camera, active = 'baseline';
const models = new Map();
const scene = new THREE.Scene(); scene.background = new THREE.Color('#cbdce6');
scene.add(new THREE.HemisphereLight('#f4f8ff', '#707963', 1.4));
const viewport = element('viewport');
function cameraSnapshot() {
  return {position:camera.position.toArray(), target:controls.target.toArray(), fov:camera.fov};
}
function setCamera(record) {
  const damping = controls.enableDamping; controls.enableDamping = false; controls.update();
  camera.position.fromArray(asYUp(record.position)); controls.target.fromArray(asYUp(record.target));
  camera.fov = record.fov; camera.updateProjectionMatrix(); controls.update(); controls.enableDamping = damping;
}
function inventory(key = active) {
  const result = [];
  models.get(key)?.scene.traverse(object => {
    if (!object.isMesh) return;
    const bounds = new THREE.Box3().setFromObject(object, true), attribute = object.geometry.attributes.position;
    result.push({name:object.name, persistent_id:object.userData.persistent_id || null,
      building_id:object.userData.building_id || null,
      vertices:attribute.count, triangles:(object.geometry.index?.count || attribute.count) / 3,
      bounds_min:bounds.min.toArray(), bounds_max:bounds.max.toArray()});
  });
  return result;
}
function showModel(key) {
  if (!models.has(key)) return;
  for (const [name, gltf] of models) gltf.scene.visible = name === key;
  active = key;
  element('baseline').setAttribute('aria-pressed', String(key === 'baseline'));
  element('edited').setAttribute('aria-pressed', String(key === 'edited'));
  const buildingIds = [...new Set(inventory().map(record => record.building_id).filter(Boolean))].sort();
  status.textContent = (key === 'baseline' ? 'המודל המקורי' : 'המודל הקיים לאחר שינוי במבנה A') +
    ' פתוח: מבנים ' + buildingIds.join(', ') + '. גררי בחלון לסיבוב המצלמה.';
}
function resize() {
  if (!renderer) return;
  const {width, height} = viewport.getBoundingClientRect();
  camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height, false);
}
try {
  renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1;
  viewport.append(renderer.domElement);
  camera = new THREE.PerspectiveCamera(37, 1, .05, 5000);
  controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
  controls.minDistance = 1; controls.maxDistance = 800; controls.maxPolarAngle = Math.PI * .99;
  const loader = new GLTFLoader();
  for (const record of payload.models) {
    const bytes = fromBase64(record.base64);
    if (bytes.byteLength !== record.bytes) throw Error('המודל המשובץ אינו שלם');
    const gltf = await loader.parseAsync(bytes.buffer, '');
    // Keep all imported geometry, materials, local transforms and persistent ids unchanged.
    // Blender exports a sun intensity near 1912 lux. Normalize display-only
    // light intensity for this schematic browser view; the original GLB bytes
    // and the Blender renders remain unchanged.
    gltf.scene.traverse(object => {if (object.isDirectionalLight) object.intensity = 2.2;});
    gltf.scene.updateMatrixWorld(true); models.set(record.key, gltf); scene.add(gltf.scene);
  }
  const saved = models.get('baseline').cameras[0];
  if (!saved) throw Error('המצלמה השמורה במודל חסרה');
  saved.getWorldPosition(camera.position); saved.getWorldQuaternion(camera.quaternion); camera.fov = saved.fov;
  controls.target.fromArray(asYUp(payload.cameras.aerial.target)); camera.updateProjectionMatrix(); controls.update();
  showModel('baseline'); resize();
  element('baseline').onclick = () => showModel('baseline'); element('edited').onclick = () => showModel('edited');
  element('aerial').onclick = () => setCamera(payload.cameras.aerial);
  element('street').onclick = () => setCamera(payload.cameras.street);
  element('downloadGlb').onclick = () => {
    const record = payload.models.find(model => model.key === active);
    saveBytes(fromBase64(record.base64), record.name, 'model/gltf-binary');
  };
  new ResizeObserver(resize).observe(viewport);
  renderer.setAnimationLoop(() => {controls.update(); renderer.render(scene, camera);});
  window.goldenOffline = {
    getActiveModel:() => active, getCamera:cameraSnapshot, getInventory:inventory,
    getObjectIds:() => [...new Set(inventory().map(record => record.building_id).filter(Boolean))].sort(),
    getImportedCameras:key => models.get(key || active).cameras.map(saved => ({name:saved.name,
      position:saved.getWorldPosition(new THREE.Vector3()).toArray(),
      quaternion:saved.getWorldQuaternion(new THREE.Quaternion()).toArray(), fov:saved.fov})),
    sourceManifest:payload.source_manifest, ready:true,
  };
} catch (error) {
  status.textContent = 'פתיחת התלת־ממד נכשלה: ' + error.message + '. התמונות עדיין זמינות; נסי Chrome או Edge עם תמיכת WebGL.';
  for (const id of ['baseline','edited','aerial','street','downloadGlb']) element(id).disabled = true;
  console.error(error);
}
try { await decodedImages; element('imageStatus').textContent = 'שלוש התמונות המקוריות פוענחו ומוצגות. אין צורך בחיבור לרשת.'; }
catch (error) { element('imageStatus').textContent = 'אחת התמונות אינה נפתחת. הקובץ העצמאי אינו שלם.'; console.error(error); }
"""


def build(output):
    renders = ROOT / "examples" / "renders"
    modules = ROOT / "node_modules" / "three"
    if not modules.is_dir():
        raise ValueError("Existing pinned Three.js dependencies are required; run the documented npm ci")
    imports = {
        "three": embedded_module(modules / "build" / "three.module.js", {"'./three.core.js'": "'three/core'"}),
        "three/core": embedded_module(modules / "build" / "three.core.js"),
        "three/addons/controls/OrbitControls.js": embedded_module(modules / "examples/jsm/controls/OrbitControls.js"),
        "three/addons/loaders/GLTFLoader.js": embedded_module(modules / "examples/jsm/loaders/GLTFLoader.js",
            {"'../utils/BufferGeometryUtils.js'": "'three/addons/utils/BufferGeometryUtils.js'"}),
        "three/addons/utils/BufferGeometryUtils.js": embedded_module(modules / "examples/jsm/utils/BufferGeometryUtils.js"),
    }
    source_manifest, images, model_records, figures = [], [], [], []
    descriptions = [("aerial", "מבט אווירי"), ("street", "מבט מגובה הרחוב"),
                    ("edited-aerial", "לאחר שינוי במבנה A בלבד")]
    for index, (name, label) in enumerate(descriptions, 1):
        path = renders / (name + ".png")
        verify_for_delivery(path)
        payload = path.read_bytes()
        record = {"id": "image-" + name, "name": path.name, "bytes": len(payload), "sha256": digest(payload)}
        images.append(record); source_manifest.append({**record, "source": str(path.relative_to(ROOT))})
        figures.append('<figure><img id="' + record["id"] + '" src="' + data_uri(payload, "image/png") +
            '" alt="' + html.escape(label) + '"><figcaption><strong>' + str(index) + '. ' + html.escape(label) +
            '</strong><button type="button" data-image-download="' + path.name + '">הורדת PNG מקורי</button></figcaption></figure>')
    for key, name in [("baseline", "aerial"), ("edited", "edited-aerial")]:
        path = renders / (name + ".glb")
        verify_for_delivery(path)
        payload = path.read_bytes(); document = glb_json(payload)
        record = {"key": key, "name": path.name, "bytes": len(payload), "sha256": digest(payload)}
        source_manifest.append({**record, "source": str(path.relative_to(ROOT)), "meshes": len(document["meshes"]),
                                "cameras": len(document.get("cameras", []))})
        model_records.append({**record, "base64": base64.b64encode(payload).decode("ascii")})
    cameras = {key: json.loads((renders / (key + ".scene.json")).read_text())["camera"] for key in ["aerial", "street"]}
    payload = json.dumps({"images": images, "models": model_records, "cameras": cameras, "source_manifest": source_manifest},
                         ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    license_text = html.escape((modules / "LICENSE").read_text())
    page = '''<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Golden City — תמונות ומודל לצפייה ללא שרת</title>
<style>*{box-sizing:border-box}body{margin:0;background:#eef2ed;color:#17392f;font:16px system-ui,sans-serif}header{background:#153f37;color:white;padding:24px 5%}h1{margin:0;color:#e2c38a;font-size:28px}main{max-width:1500px;margin:auto;padding:20px}section{background:white;border:1px solid #dce4d8;border-radius:12px;padding:18px;margin:18px 0}h2{margin:0 0 12px;font-size:20px}p{line-height:1.65}.hint{font-size:14px;color:#52685b}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0;border:1px solid #dce4d8;border-radius:9px;overflow:hidden}img{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#dbe5eb}figcaption{padding:12px;display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}button{font:inherit;border:1px solid #bfcdbc;border-radius:7px;padding:10px;background:#153f37;color:white;cursor:pointer}button[aria-pressed=true]{background:#816425}button:disabled{opacity:.5}.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}#viewport{height:560px;direction:ltr;background:#cbdce6;border-radius:10px;overflow:hidden}#viewport canvas{display:block;width:100%;height:100%;touch-action:none}pre{direction:ltr;text-align:left;white-space:pre-wrap;font-size:12px}summary{cursor:pointer}@media(max-width:750px){.images{grid-template-columns:1fr}#viewport{height:420px}main{padding:10px}h1{font-size:23px}}</style>
<script type="importmap">IMPORT_MAP</script></head><body>
<header><h1>GOLDEN CITY — תמונות ומודל תלת־ממדי</h1><p>קובץ לצפייה בדפדפן, עם התמונות והמודלים הקיימים בתוכו.</p></header>
<main><p>לאחר הורדת הקובץ, פתחי אותו באמצעות Chrome או Edge. שלוש התמונות מוצגות כאן. בהמשך הדף אפשר לסובב את המודל, להחליף מבט ולהציג את הגרסה שכבר נערכה. אין צורך להתקין Blender או להפעיל שרת.</p>
<section><h2>שלוש תמונות הרינדור המקוריות</h2><div id="images" class="images">FIGURES</div><p id="imageStatus" class="hint" role="status">טוענים את שלוש התמונות המשובצות…</p><p id="downloadStatus" class="hint" role="status">הכפתורים שומרים קובצי PNG נפרדים מתוך הקובץ הזה. חבילת ZIP נמסרת בנפרד.</p></section>
<section><h2>פתיחת המודל הסינתטי הקיים</h2><div class="toolbar"><button id="baseline" aria-pressed="true">המודל המקורי</button><button id="edited" aria-pressed="false">הגרסה הקיימת לאחר שינוי A</button><button id="aerial">מבט אווירי</button><button id="street">מבט רחוב</button><button id="downloadGlb">הורדת GLB המקורי המוצג</button></div><div id="viewport" aria-label="מודל תלת־ממדי אינטראקטיבי"></div><p id="viewerStatus" role="status">טוענים את המודלים הקיימים המשובצים…</p><p class="hint">סיבוב: גרירה בעכבר שמאלי או באצבע. קירוב: גלגלת או צביטה. תזוזה: עכבר ימני או שתי אצבעות. תצוגת הדפדפן שונה בתאורה מרינדור Blender; התמונות מעל הן הרינדורים המקוריים. אלה נתונים סינתטיים, ללא גובה קרקע מדוד.</p></section>
<details><summary>רישיון Three.js</summary><pre>LICENSE</pre></details>
<script id="viewerPayload" type="application/json">PAYLOAD</script><script type="module">APPLICATION</script></main></body></html>'''
    for marker, value in [("IMPORT_MAP", json.dumps({"imports": imports}, separators=(",", ":"))),
                          ("FIGURES", "".join(figures)), ("LICENSE", license_text), ("PAYLOAD", payload), ("APPLICATION", APPLICATION)]:
        page = page.replace(marker, value)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    if output.stat().st_size < 1_000_000:
        raise ValueError("Offline viewer package is unexpectedly small")
    for record in source_manifest:
        if digest((ROOT / record["source"]).read_bytes()) != record["sha256"]:
            raise ValueError("Source artifact changed while packaging")
    result = {"output": str(output), "bytes": output.stat().st_size, "sha256": digest(output.read_bytes()),
              "source_artifacts": source_manifest, "validation": "pending_independent_browser_test",
              "recipient_access": "not_verified"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables" / "golden-city-viewer.html")
    args = parser.parse_args()
    build(args.output)
