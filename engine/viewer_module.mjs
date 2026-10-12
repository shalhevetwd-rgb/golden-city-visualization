import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
// __HASH_HELPER__

const payload = JSON.parse(document.getElementById('viewerPayload').textContent);
const element = id => document.getElementById(id);
const status = element('viewerStatus');
const fromBase64 = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
const asYUp = xyz => [xyz[0], xyz[2], -xyz[1]];
const toZUp = xyz => [xyz[0], -xyz[2], xyz[1]];
function originalModelBytes(record) {
  if(record.encoding!=='glb_shared_bin')return fromBase64(record.base64);
  const parts=[fromBase64(record.header_base64),fromBase64(record.json_chunk_base64),fromBase64(payload.shared_glb_bin_base64)];
  const result=new Uint8Array(parts.reduce((sum,part)=>sum+part.length,0));let offset=0;
  for(const part of parts){result.set(part,offset);offset+=part.length;}
  if(result.byteLength!==record.bytes)throw Error('שחזור המודל המשובץ אינו שלם.');
  return result;
}
const modelBytes = new Map(payload.models.map(record => [record.key, originalModelBytes(record)]));
const decodedImages = Promise.all([...document.querySelectorAll('#images img, #comparison img')].map(img => img.decode()));
let renderer, controls, camera, active = 'baseline', selected = 'A', cameraShift = 0;
let renderRequested = true;
function requestRender() { renderRequested = true; }
const models = new Map(), previewHistory = [], savedCameras = new Map();
const nativeRecords = new Map();
const liveService = (location.protocol === 'http:' || location.protocol === 'https:') && location.pathname.startsWith('/showcase/v3/');
let nativeBusy = false, nativeLoading = false;
function sourceRecord(key=active) { return nativeRecords.get(key) || payload.models.find(record=>record.key===key); }
let dossier = null;
const scene = new THREE.Scene(); scene.background = new THREE.Color('#cbdce6');
scene.add(new THREE.HemisphereLight('#f4f8ff', '#707963', 1.4));
const viewport = element('viewport');
const loader = new GLTFLoader();
const selectionBounds = new THREE.Box3();
const selectionBox = new THREE.Box3Helper(selectionBounds, 0xd6ac54); selectionBox.visible = false; scene.add(selectionBox);
const raycaster = new THREE.Raycaster();
function embeddedGLB(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (bytes.length < 20) throw Error('קובץ GLB קצר או לא תקין.');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), length = view.getUint32(12,true);
  if(view.getUint32(0,true)!==0x46546c67||view.getUint32(4,true)!==2||view.getUint32(8,true)!==bytes.length||view.getUint32(16,true)!==0x4e4f534a||20+length>bytes.length)throw Error('מבנה GLB לא תקין.');
  const document=JSON.parse(new TextDecoder().decode(bytes.subarray(20,20+length)));
  for(const record of [...(document.buffers||[]),...(document.images||[])])if(record.uri&&!record.uri.startsWith('data:'))throw Error('בדיקה מקומית תומכת ב־GLB עם משאבים משובצים בלבד; לא נטענים משאבים חיצוניים.');
  return bytes;
}

function saveBytes(bytes, name, type) {
  const url = URL.createObjectURL(new Blob([bytes], {type}));
  const link = document.createElement('a'); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
function metadata(object) {
  let owner = null, role = null, classification = null;
  for (let o = object; o; o = o.parent) {
    owner ||= o.userData.owner || o.userData.building_id || null;
    role ||= o.userData.role || null; classification ||= o.userData.classification || null;
  }
  return {owner, role, classification};
}
function eachMesh(key, fn) { models.get(key)?.scene.traverse(object => { if (object.isMesh) fn(object); }); }
function inventory(key = active) {
  const result = [];
  eachMesh(key, object => {
    const bounds = new THREE.Box3().setFromObject(object, true), position = object.geometry.attributes.position;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    result.push({name:object.name, persistent_id:object.userData.persistent_id || object.name,
      building_id:object.userData.building_id || null, ...metadata(object),
      vertices:position.count, triangles:(object.geometry.index?.count || position.count)/3,
      bounds_min:bounds.min.toArray(), bounds_max:bounds.max.toArray(),
      colors:materials.map(material => material.color?.getHexString() || null),
      linear_colors:materials.map(material=>material.color?.toArray()||null),
      tint_scales:materials.map(material=>material.userData.facade_tint_scale??1)});
  });
  return result;
}
function facadeMeshes(id = selected, key = active) {
  const matches = []; eachMesh(key, object => { const m = metadata(object); if (m.owner === id && m.role === 'facade') matches.push(object); }); return matches;
}
function buildingIds(key = active) { const ids = new Set(); eachMesh(key, object => { const id = metadata(object).owner; if (id && id !== 'context') ids.add(id); }); return [...ids].sort(); }
function selectBuilding(id) {
  if (!buildingIds().includes(id)) return;
  selected = id; element('editBuilding').value = id; element('selectionTag').textContent = 'מבנה ' + id;
  selectionBounds.makeEmpty();
  eachMesh(active, object => { if (metadata(object).owner === id) selectionBounds.union(new THREE.Box3().setFromObject(object, true)); });
  selectionBox.visible = !selectionBounds.isEmpty();
  requestRender();
}
function showModel(key) {
  if (!models.has(key)) return;
  for (const [name, gltf] of models) gltf.scene.visible = name === key;
  active = key;
  element('baseline').setAttribute('aria-pressed', String(key === 'baseline'));
  element('edited').setAttribute('aria-pressed', String(key === 'edited'));
  element('editBuilding').replaceChildren(...buildingIds().map(id => new Option('מבנה ' + id, id)));
  selectBuilding(buildingIds().includes(selected) ? selected : buildingIds()[0]);
  status.textContent = (key.startsWith('native:') ? 'גרסת רינדור חדשה מהסצנה השמורה' : key === 'baseline' ? 'גרסת ההצעה לפני שינוי חזית A' : 'גרסת ההצעה לאחר שינוי חזית A') + ' • מבנים ' + buildingIds().join(', ') + ' • מקור סינתטי.';
  element('renderNative').disabled = !liveService || nativeBusy || nativeLoading || !sourceRecord()?.blend_sha256;
}
function projection() {
  camera.updateProjectionMatrix();
  // Blender VERTICAL sensor fit: positive shift_y moves the viewing window up.
  camera.projectionMatrix.elements[9] += 2 * cameraShift;
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}
function cameraSnapshot() { return {position:camera.position.toArray(), target:controls.target.toArray(), fov:camera.fov, shift_y:cameraShift, coordinate_system:'glTF_Y_up'}; }
function restoreSnapshot(record) {
  const damping = controls.enableDamping; controls.enableDamping = false; controls.update();
  camera.position.fromArray(record.position); controls.target.fromArray(record.target); camera.fov = record.fov;
  cameraShift = record.shift_y || 0; projection(); controls.update(); controls.enableDamping = damping;
  camera.updateMatrixWorld(true); requestRender();
}
function setCamera(record) { restoreSnapshot({...record, position:asYUp(record.position), target:asYUp(record.target)}); }
function resize() {
  if (!renderer) return;
  const {width, height} = viewport.getBoundingClientRect(); if (!width || !height) return;
  camera.aspect = width / height; projection(); renderer.setSize(width, height, false);
  requestRender();
}
function previewColor() {
  const matches = facadeMeshes(); if (!matches.length) throw Error('לא נמצאו רכיבי חזית מסומנים למבנה ' + selected + '.');
  const replacements = new Map(), saved = [], tint = new THREE.Color(element('editColor').value);
  for (const mesh of matches) {
    const previous = mesh.material;
    const changed = (Array.isArray(previous) ? previous : [previous]).map(material => {
      if (!replacements.has(material)) {
        const clone = material.clone(), scale = typeof material.userData.facade_tint_scale==='number' ? material.userData.facade_tint_scale : 1;
        clone.color.setRGB(tint.r*scale,tint.g*scale,tint.b*scale); replacements.set(material, clone);
      }
      return replacements.get(material);
    });
    mesh.material = Array.isArray(previous) ? changed : changed[0]; saved.push({mesh, previous});
  }
  previewHistory.push({key:active, owner:selected, saved, clones:[...replacements.values()]});
  element('undoColor').disabled = false;
  element('editStatus').textContent = 'שונו ' + matches.length + ' רכיבי חזית של מבנה ' + selected + ' בתצוגה המקדימה בלבד. חלונות, גג, סביבה ושאר המבנים לא נערכו.';
  requestRender();
}
function undoColor() {
  const transaction = previewHistory.pop(); if (!transaction) return;
  showModel(transaction.key); for (const {mesh, previous} of transaction.saved) mesh.material = previous;
  for (const material of transaction.clones) material.dispose();
  selectBuilding(transaction.owner); element('undoColor').disabled = !previewHistory.length;
  element('editStatus').textContent = 'חומרי החזית של ' + transaction.owner + ' שוחזרו. קובצי המקור נשארו ללא שינוי.';
}
function safeAction(fn, field='editStatus') {
  return async event => { try { await fn(event); } catch (error) { element(field).textContent = error.message || String(error); } };
}
function serviceURL(path) {
  const url=new URL(path,location.href);
  if(url.origin!==location.origin)throw Error('תוצאת הרינדור אינה שייכת לשירות הנוכחי.');
  return url.href;
}
function rememberNative(record) {
  if(!record.version_id||record.synthetic!==true||record.geometry_preserved!==true||record.material_export_verified!==true||!Array.isArray(record.outside_target_changed)||record.outside_target_changed.length)throw Error('גרסת הרינדור לא עברה את בדיקות הגיאומטריה, החומרים והשינוי הנקודתי.');
  const key='native:'+record.version_id;
  nativeRecords.set(key,{...record,key,name:new URL(record.model_url,location.href).pathname.split('/').pop(),sha256:record.model_sha256});
  element('nativeVersions').replaceChildren(new Option('בחירת גרסת שירות',''),...[...nativeRecords.values()].map(r=>new Option('גרסה '+r.version_id.slice(0,8)+(r.building_id?' • מבנה '+r.building_id:''),r.key)));
  return key;
}
function nativeFigure(record) {
  const id='native-result-'+record.version_id;if(element(id))return;
  const figure=document.createElement('figure');figure.id=id;
  const img=document.createElement('img');img.src=serviceURL(record.image_url);img.alt='תוצאת רינדור סינתטי חדשה';
  const caption=document.createElement('figcaption');caption.textContent='רינדור סינתטי חדש • גרסה '+record.version_id.slice(0,8);
  for(const [label,path]of[['PNG',record.image_url],['GLB',record.model_url],['Blender',record.blend_url],['דוח שינוי',record.edit_report_url]]){
    const link=document.createElement('a');link.href=serviceURL(path);link.textContent=label;link.setAttribute('download','');caption.append(link);
  }
  figure.append(img,caption);element('nativeRenders').prepend(figure);
}
function clearPreviews() {
  while(previewHistory.length){const transaction=previewHistory.pop();for(const{mesh,previous}of transaction.saved)mesh.material=previous;for(const material of transaction.clones)material.dispose();}
  element('undoColor').disabled=true;
}
async function loadNative(key) {
  const record=nativeRecords.get(key);if(!record)throw Error('גרסת הרינדור אינה רשומה.');
  if(nativeLoading)throw Error('פתיחת גרסת שירות כבר מתבצעת.');
  nativeLoading=true;element('restoreNative').disabled=true;element('renderNative').disabled=true;
  try {
  if(!models.has(key)){
    const response=await fetch(serviceURL(record.model_url));if(!response.ok)throw Error('טעינת GLB של הגרסה נכשלה.');
    const bytes=embeddedGLB(await response.arrayBuffer());
    if(await sha256Hex(bytes)!==record.model_sha256)throw Error('חתימת המודל החדש אינה תואמת לגרסה השמורה.');
    const gltf=await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),'');
    gltf.scene.traverse(object=>{if(object.isDirectionalLight)object.intensity=2.2;});gltf.scene.updateMatrixWorld(true);
    clearPreviews();
    // Retain the two immutable embedded originals. Reload older service versions
    // from their verified saved bytes to keep GPU memory bounded.
    for(const[oldKey,oldModel]of models)if(oldKey.startsWith('native:')&&oldKey!==key){
      scene.remove(oldModel.scene);oldModel.scene.traverse(object=>{object.geometry?.dispose();for(const material of Array.isArray(object.material)?object.material:[object.material]){material?.map?.dispose();material?.dispose();}});
      models.delete(oldKey);modelBytes.delete(oldKey);
    }
    models.set(key,gltf);modelBytes.set(key,bytes);scene.add(gltf.scene);
  }
  // showModel does not move the camera. Retain even orbit/zoom performed while
  // the large GLB was downloading and decoding.
  showModel(key);element('nativeVersions').value=key;
  nativeFigure(record);
  } finally {
    nativeLoading=false;
    element('renderNative').disabled=!liveService||nativeBusy||!sourceRecord()?.blend_sha256;
    element('restoreNative').disabled=!liveService||nativeBusy||!element('nativeVersions').value;
  }
}
async function loadNativeCatalogue() {
  if(!liveService||!window.goldenOffline?.ready)return;
  const response=await fetch('/api/architectural-versions');if(!response.ok)throw Error('טעינת רשימת גרסאות השירות נכשלה.');
  const result=await response.json();for(const record of result.versions||[])rememberNative(record);
}
element('nativeVersions').onchange=()=>{element('restoreNative').disabled=!liveService||nativeBusy||nativeLoading||!element('nativeVersions').value;};
element('restoreNative').onclick=safeAction(async()=>{await loadNative(element('nativeVersions').value);element('nativeStatus').textContent='גרסת השירות פתוחה מן הקובץ השמור. מצלמת התצוגה נשמרה.';},'nativeStatus');
element('nativeCamera').onchange=()=>{if(window.goldenOffline?.ready)setCamera(payload.cameras[element('nativeCamera').value]);};
element('renderNative').onclick=safeAction(async()=>{
  if(!liveService)throw Error('רינדור חדש זמין בממשק השירות; הקובץ העצמאי תומך בתצוגה מקדימה ובקשת עריכה.');
  if(nativeBusy||nativeLoading)return;
  const source=sourceRecord(),target=selected,view=element('nativeCamera').value;
  nativeBusy=true;element('renderNative').disabled=true;element('restoreNative').disabled=true;element('previewColor').disabled=true;element('undoColor').disabled=true;
  element('nativeStatus').textContent='מייצרים גרסה נפרדת ומרנדרים תמונה חדשה. התהליך עשוי להימשך מספר דקות; אפשר להמשיך לסובב את התצוגה.';
  try{
    const response=await fetch('/api/architectural-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({building_id:target,operation:'facade_color',color:new THREE.Color(element('editColor').value).toArray(),source_sha256:source.blend_sha256,camera:view,width:960,height:600,samples:48})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'הרינדור החדש נכשל.');
    const record={...result,building_id:target,camera:view};const key=rememberNative(record);await loadNative(key);
    const untouched=Array.isArray(record.unchanged_scene_objects)?record.unchanged_scene_objects.length:record.unchanged_scene_objects;
    element('nativeStatus').textContent='נשמרה גרסה חדשה והופק PNG ממבנה '+target+'. הגיאומטריה נשמרה'+(untouched!==undefined?' • '+untouched+' אובייקטים נשמרו ללא שינוי':'')+'. המצלמה האינטראקטיבית נשמרה; התמונה הופקה מן המצלמה שנבחרה.';
  }finally{nativeBusy=false;element('renderNative').disabled=!sourceRecord()?.blend_sha256;element('restoreNative').disabled=!element('nativeVersions').value;element('previewColor').disabled=false;element('undoColor').disabled=!previewHistory.length;}
},'nativeStatus');
for (const button of document.querySelectorAll('[data-image-download]')) button.onclick = safeAction(() => {
  const record = payload.images.find(r => r.name === button.dataset.imageDownload);
  const bytes = fromBase64(element(record.id).src.split(',')[1]);
  if (bytes.byteLength !== record.bytes) throw Error('התמונה אינה שלמה');
  saveBytes(bytes, record.name, 'image/png');
  element('downloadStatus').textContent = 'בקשת שמירה של ' + record.name + ' נשלחה לדפדפן. נשמרים הבייטים המקוריים המשובצים.';
}, 'downloadStatus');
element('compareRange').oninput = event => {
  const value = Number(event.target.value);
  element('comparisonAfter').style.clipPath = 'inset(0 ' + (100-value) + '% 0 0)';
  element('comparisonDivider').style.left = value + '%';
};
try {
  renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1; viewport.append(renderer.domElement);
  camera = new THREE.PerspectiveCamera(37, 1, .05, 5000);
  controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
  controls.addEventListener('change', requestRender);
  controls.minDistance = 1; controls.maxDistance = 800; controls.maxPolarAngle = Math.PI * .99;
  for (const record of payload.models) {
    const bytes = embeddedGLB(modelBytes.get(record.key)); if (bytes.byteLength !== record.bytes) throw Error('המודל המשובץ אינו שלם');
    const hash=await sha256Hex(bytes);
    if(hash!==record.sha256)throw Error('חתימת המודל המשוחזר אינה תואמת למקור.');
    const gltf = await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
    gltf.scene.traverse(object => { if (object.isDirectionalLight) object.intensity = 2.2; });
    gltf.scene.updateMatrixWorld(true); models.set(record.key, gltf); scene.add(gltf.scene);
  }
  setCamera(payload.cameras.aerial); showModel('baseline'); resize();
  element('baseline').onclick = () => showModel('baseline'); element('edited').onclick = () => showModel('edited');
  for (const name of ['aerial','street','architectural']) element(name).onclick = () => setCamera(payload.cameras[name]);
  element('editBuilding').onchange = event => selectBuilding(event.target.value);
  element('previewColor').onclick = safeAction(previewColor); element('undoColor').onclick = safeAction(undoColor);
  element('downloadGlb').onclick = () => { const record = sourceRecord(); saveBytes(modelBytes.get(active), record.name, 'model/gltf-binary'); };
  let pointerStart = null;
  renderer.domElement.addEventListener('pointerdown', event => { pointerStart = [event.clientX,event.clientY]; });
  renderer.domElement.addEventListener('pointerup', event => {
    if (!pointerStart || Math.hypot(event.clientX-pointerStart[0],event.clientY-pointerStart[1])>5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1), camera);
    const ids = buildingIds(); const hit = raycaster.intersectObject(models.get(active).scene, true).find(h => ids.includes(metadata(h.object).owner));
    if (hit) selectBuilding(metadata(hit.object).owner);
  });
  element('saveCamera').onclick = safeAction(() => {
    const name = element('cameraName').value.trim(); if (!name) throw Error('יש לתת שם למצלמה.');
    savedCameras.set(name, cameraSnapshot());
    element('savedCameras').replaceChildren(new Option('בחירת מצלמה',''), ...[...savedCameras.keys()].map(n => new Option(n,n)));
    element('savedCameras').value = name; element('restoreCamera').disabled = false;
    element('editStatus').textContent = 'המצלמה נשמרה בפגישת הצפייה של ההדגמה בלבד.';
  });
  element('savedCameras').onchange = () => { element('restoreCamera').disabled = !element('savedCameras').value; };
  element('restoreCamera').onclick = () => restoreSnapshot(savedCameras.get(element('savedCameras').value));
  element('saveRequest').onclick = safeAction(() => {
    const source = sourceRecord(), color = new THREE.Color(element('editColor').value).toArray();
    const request = {source_glb_sha256:source.sha256,source_sha256:source.blend_sha256,building_id:selected,operation:'facade_color',role:'facade',color,camera:element('nativeCamera').value,width:960,height:600,samples:48,synthetic:true,requires_saved_scene_sha256:true};
    saveBytes(new TextEncoder().encode(JSON.stringify(request,null,2)), 'edit-request.json', 'application/json');
    element('editStatus').textContent = 'בקשת עריכה נשמרה בנפרד. יצירת PNG חדש דורשת רינדור מהסצנה המתאימה.';
  });
  new ResizeObserver(resize).observe(viewport);
  renderer.setAnimationLoop(() => {
    controls.update();
    if (renderRequested && !element('modelSection').hidden) {
      renderer.render(scene,camera); renderRequested = false;
    }
  });
  window.goldenOffline = {
    ready:true,getActiveModel:()=>active,getCamera:cameraSnapshot,getInventory:inventory,getObjectIds:buildingIds,
    getSelectedBuilding:()=>selected,getFacadeCount:(id,key)=>facadeMeshes(id,key).length,
    selectBuilding,getSourceHashes:()=>payload.source_manifest.map(r=>({name:r.name,sha256:r.sha256})),
    getImportedCameras:key=>models.get(key||active).cameras.map(saved=>({name:saved.name,position:saved.getWorldPosition(new THREE.Vector3()).toArray(),quaternion:saved.getWorldQuaternion(new THREE.Quaternion()).toArray(),fov:saved.fov})),
    getBuildingScreenPoint:id=>{
      const bounds = new THREE.Box3(); eachMesh(active,object=>{if(metadata(object).owner===id)bounds.union(new THREE.Box3().setFromObject(object,true));});
      const projected=bounds.getCenter(new THREE.Vector3()).project(camera),rect=renderer.domElement.getBoundingClientRect();
      return {x:rect.left+(projected.x+1)/2*rect.width,y:rect.top+(1-projected.y)/2*rect.height};
    },
    sourceManifest:payload.source_manifest,getDossier:()=>dossier?JSON.parse(JSON.stringify(dossier)):null,
    getSavedCameras:()=>JSON.parse(JSON.stringify(Object.fromEntries(savedCameras))),
    getNativeVersions:()=>[...nativeRecords.values()].map(record=>({version_id:record.version_id,key:record.key,sha256:record.sha256,blend_sha256:record.blend_sha256})),
  };
} catch (error) {
  status.textContent = 'פתיחת התלת־ממד נכשלה: ' + error.message + '. תמונות ההדמיה עדיין זמינות.';
  for (const id of ['baseline','edited','aerial','street','architectural','downloadGlb','previewColor','undoColor','saveCamera','restoreCamera']) element(id).disabled=true;
  console.error(error);
}
element('projectForm').onsubmit = event => {
  event.preventDefault(); const identity = Object.fromEntries(new FormData(event.target));
  dossier={id:localId(),synthetic:false,identity,files:[],evidence:{},model:null};
  for (const id of ['imageSection','comparisonSection','modelSection']) element(id).hidden=true;
  element('projectStatus').textContent='נפתח תיק אמיתי נפרד ללא מודל או גבול משוער. חסרים גבולות מאומתים, התאמה לקרקע, מיקום מבנים, קומות, גבהים וטופוגרפיה. אין מוכנות להדמיה סופית.';
  element('filesList').replaceChildren(); element('fileStatus').textContent='';
};
element('localFiles').onchange=safeAction(async event=>{
  if(!dossier)throw Error('יש לפתוח תיק נפרד לפני קליטת מקורות אמיתיים.');
  for(const file of event.target.files){
    if(file.size>10_000_000)throw Error('גודל מקור מרבי לבדיקה מקומית: 10 MB.');
    const bytes=await file.arrayBuffer(),sha256=await sha256Hex(bytes);
    const record={name:file.name,bytes:file.size,sha256,classification:'user_provided',verification:'unverified',status:'metadata_only'};
    const suffix=file.name.split('.').pop().toLowerCase();
    try{
      if(['png','jpg','jpeg'].includes(suffix)){const decoded=await createImageBitmap(file);record.dimensions=[decoded.width,decoded.height];decoded.close();record.status='image_decoded_no_georeference';}
      else if(suffix==='glb'){embeddedGLB(bytes);const gltf=await loader.parseAsync(bytes,'');let meshes=0;gltf.scene.traverse(o=>{if(o.isMesh)meshes++;});record.meshes=meshes;record.status='geometry_decoded_no_site_crs';gltf.scene.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:[o.material])m?.dispose();});}
      else if(['json','geojson'].includes(suffix)){const document=JSON.parse(new TextDecoder().decode(bytes));record.type=document.type||null;record.status='json_syntax_only';}
    }catch(error){record.status='decode_failed';record.error=error.message;}
    dossier.files.push(record);
  }
  element('filesList').replaceChildren(...dossier.files.map(record=>{const item=document.createElement('li');item.textContent=record.name+' • '+record.status+' • '+record.bytes+' bytes • SHA-256 '+record.sha256;return item;}));
  element('fileStatus').textContent='נרשמו '+dossier.files.length+' מקורות שסיפקה המשתמשת. אין שיוך למודל הסינתטי ואין אימות מיקום, גובה או התאמה.';
},'fileStatus');
element('showDemo').onclick=()=>{for(const id of ['imageSection','comparisonSection','modelSection'])element(id).hidden=false;resize();element('projectStatus').textContent=dossier?'ההדגמה פתוחה בנפרד. תיק הפרויקט האמיתי נשמר ללא שיוך המודל הסינתטי.':'ההדגמה הסינתטית פתוחה.';};
element('projectExport').onclick=()=>{
  const data=dossier?{project:dossier}:{synthetic:true,cameras:Object.fromEntries(savedCameras),demo_sources:payload.source_manifest};
  saveBytes(new TextEncoder().encode(JSON.stringify(data,null,2)),'project-data.json','application/json');
};
if(liveService){element('liveLink').href='/';element('liveLink').hidden=false;}
if(liveService&&window.goldenOffline?.ready){
  element('nativeStatus').textContent='רינדור חדש זמין בשירות. הוא שומר גרסת סצנה נפרדת ובודק את ה־PNG, ה־GLB ואת ה־Blender לפני מסירה.';
  try{await loadNativeCatalogue();}catch(error){element('nativeStatus').textContent=error.message;}
}
element('capabilities').replaceChildren(...payload.capabilities.map(record=>{const item=document.createElement('li');const strong=document.createElement('strong');strong.textContent=record.extension+' • ';item.append(strong,document.createTextNode(record.label));return item;}));
try{await decodedImages;element('imageStatus').textContent='ארבע תמונות ההדמיה פוענחו ומוצגות. הנתונים סינתטיים; אין צורך בחיבור לרשת לצפייה בקובץ העצמאי.';}
catch(error){element('imageStatus').textContent='אחת התמונות המשובצות אינה נפתחת.';console.error(error);}
