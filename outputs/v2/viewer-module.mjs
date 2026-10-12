
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
  status.textContent = (key === 'baseline' ? 'המודל המשופר לפני עריכה' : 'המודל הקיים לאחר שינוי במבנה A') +
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
  element('architectural').onclick = () => setCamera(payload.cameras.architectural);
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
try { await decodedImages; element('imageStatus').textContent = 'תמונות הבדיקה ודוגמת העריכה פוענחו ומוצגות. אין צורך בחיבור לרשת.'; }
catch (error) { element('imageStatus').textContent = 'אחת התמונות אינה נפתחת. הקובץ העצמאי אינו שלם.'; console.error(error); }

const versions=[];let project={id:crypto.randomUUID(),synthetic:true,files:[]};
element('projectForm').onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));project={id:crypto.randomUUID(),synthetic:false,identity:d,files:[],evidence:{}};element('images').closest('section').hidden=true;element('viewport').closest('section').hidden=true;element('editPanel').hidden=true;element('projectStatus').textContent='נפתח תיק נפרד. חסרים: גבולות מאומתים, התאמת העמדה, קומות, גבהים וטופוגרפיה. רינדור מדויק חסום. מודל ההדגמה אינו חלק מהתיק.';};
element('localFiles').onchange=async e=>{for(const f of e.target.files){const bytes=await f.arrayBuffer();const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');project.files.push({name:f.name,bytes:f.size,sha256:hash,status:'קובץ נרשם; חילוץ ואימות תוכן טרם בוצעו'});}element('projectStatus').textContent='נקלטו '+project.files.length+' קבצים מקומיים. אין אישור מיקום או גובה; נדרש אימות מקצועי.';};
element('showDemo').onclick=()=>{element('images').closest('section').hidden=false;element('viewport').closest('section').hidden=false;element('editPanel').hidden=false;resize();element('projectStatus').textContent='ההדגמה הסינתטית פתוחה בנפרד. היא אינה משויכת לתיק הפרויקט החדש.';};
element('projectExport').onclick=()=>saveBytes(new TextEncoder().encode(JSON.stringify(project,null,2)),'project.json','application/json');
element('previewColor').onclick=()=>{if(!window.goldenOffline?.ready)return;const id=element('editBuilding').value;let mesh;models.get(active).scene.traverse(o=>{if(o.isMesh && o.userData.building_id===id)mesh=o;});if(!mesh){element('editStatus').textContent='מבנה לא נמצא';return;}versions.push({mesh,material:mesh.material});mesh.material=mesh.material.clone();mesh.material.color.set(element('editColor').value);element('editStatus').textContent='שונה חומר החזית של '+id+' בלבד. הגיאומטריה לא השתנתה.';};
element('undoColor').onclick=()=>{const v=versions.pop();if(v){v.mesh.material=v.material;element('editStatus').textContent='חומר החזית שוחזר';}};
element('saveRequest').onclick=()=>{const hex=element('editColor').value;const color=[1,3,5].map(i=>(()=>{const u=parseInt(hex.slice(i,i+2),16)/255;return u<=.04045?u/12.92:((u+.055)/1.055)**2.4})());const r={source_glb_sha256:payload.models.find(m=>m.key===active).sha256,source_sha256:payload.models.find(m=>m.key===active).blend_sha256,building_id:element('editBuilding').value,operation:'facade_color',color,synthetic:true,requires_saved_scene_sha256:true};saveBytes(new TextEncoder().encode(JSON.stringify(r,null,2)),'edit-request.json','application/json');};
