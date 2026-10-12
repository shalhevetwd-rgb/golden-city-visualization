import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';

const el = id => document.getElementById(id);
let project = null, projects = [], viewer = null, mapVisible = false, activeCameraName = 'מבט אווירי', balconyReports = [], pendingActions = 0;
const extraRenders = new Map();
let readiness = null;
const intakeResultsByProject = new Map();
const evidenceLabels = {parcel_geometry_verified:'גבול חלקה מאומת',alignment_approved:'התאמת תוכנית ההעמדה לקרקע',building_positions_verified:'מיקום המבנים',floors_verified:'קומות',heights_verified:'גבהים',terrain_verified:'טופוגרפיה'};
function hasGeometry() { return !!project?.boundary?.coordinates && !!project?.crs && Array.isArray(project?.buildings) && project.buildings.length > 0; }

async function api(path, data) {
  const response = await fetch(path, { method: data === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) });
  const value = (response.headers.get('Content-Type') || '').includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw Error(value.error || String(value) || 'בקשת השירות נכשלה');
  return value;
}
function message(text) { el('status').textContent = text; }
async function action(fn, button) {
  pendingActions++; updateButtonStates();
  if (button) button.disabled = true;
  try { await fn(); } catch (error) { message(error.message || String(error)); }
  finally { pendingActions--; if (button) button.disabled = false; updateButtonStates(); }
}
async function verifiedBlob(path, options = {}, onProgress = () => {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const detail = await response.text();
    let message = detail;
    try { message = JSON.parse(detail).error || detail; } catch { /* Plain HTTP error. */ }
    throw Error(message || 'בדיקת המסירה נכשלה');
  }
  if (response.headers.get('X-Golden-City-File-Validation') !== 'passed') throw Error('התוצר לא עבר את בדיקות המסירה הנדרשות');
  const expectedHash = response.headers.get('X-Golden-City-SHA256');
  if (!/^[a-f0-9]{64}$/i.test(expectedHash || '')) throw Error('חסרה חתימת הקובץ המאומת');
  const total = Number(response.headers.get('Content-Length')) || 0;
  const chunks = []; let received = 0;
  if (response.body?.getReader) {
    const reader = response.body.getReader();
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      chunks.push(value); received += value.byteLength; onProgress(received, total);
    }
  } else {
    const bytes = await response.arrayBuffer(); chunks.push(bytes); received = bytes.byteLength; onProgress(received, total);
  }
  const blob = new Blob(chunks, {type:response.headers.get('Content-Type') || 'application/octet-stream'});
  if (!blob.size) throw Error('הקובץ שנמסר ריק');
  if (total && response.headers.get('Content-Encoding') === null && blob.size !== total) throw Error('הקובץ לא התקבל בשלמותו');
  if (!globalThis.crypto?.subtle) throw Error('בדיקת חתימה בדפדפן דורשת חיבור HTTPS. אפשר להשתמש בהורדה הישירה שנבדקת בשרת.');
  const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  const actualHash = [...new Uint8Array(hash)].map(n => n.toString(16).padStart(2,'0')).join('');
  if (actualHash !== expectedHash.toLowerCase()) throw Error('הקובץ שהתקבל שונה מהקובץ שנבדק');
  return blob;
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
async function downloadVerified(path, data, name) {
  saveBlob(await verifiedBlob(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}), name);
}
let deliveryBusy = false;
async function downloadExisting(name) {
  if (deliveryBusy) return;
  deliveryBusy = true;
  const buttons = [...document.querySelectorAll('[data-delivery]')]; buttons.forEach(button => button.disabled = true);
  const progress = el('deliveryProgress'), status = el('deliveryStatus'); progress.hidden = false; progress.removeAttribute('value');
  status.textContent = 'בודקים ומקבלים את ' + name + '. חבילת ZIP מכילה כ־20 MB ובדיקתה עשויה להימשך מעט.';
  try {
    const blob = await verifiedBlob('/downloads/' + encodeURIComponent(name), {}, (received, total) => {
      progress.max = total || Math.max(received, 1); progress.value = received;
      status.textContent = 'מקבלים ' + name + ': ' + (received / 1_000_000).toFixed(1) + (total ? ' מתוך ' + (total / 1_000_000).toFixed(1) : '') + ' MB.';
    });
    saveBlob(blob, name);
    status.textContent = 'הקובץ ' + name + ' התקבל בשלמותו ונבדקה חתימת SHA-256. נשלחה בקשת שמירה לדפדפן. אם ההורדה אינה מופיעה אצלך, נסי ״הורדה ישירה״.';
  } catch (error) {
    status.textContent = 'הורדת ' + name + ' לא הושלמה: ' + (error.message || String(error)) + '\nאפשר לנסות ״הורדה ישירה״. אין עדיין אישור שהקובץ נגיש במחשב שלך.';
  } finally {
    progress.hidden = true; deliveryBusy = false; buttons.forEach(button => button.disabled = false);
  }
}

for (const button of document.querySelectorAll('[data-delivery]')) button.onclick = () => downloadExisting(button.dataset.delivery);
for (const img of document.querySelectorAll('#acceptanceImages img')) {
  const status = img.closest('figure').querySelector('.image-state');
  const loaded = () => status.textContent = 'התמונה פוענחה בדפדפן: \u2066' + img.naturalWidth + ' × ' + img.naturalHeight + '\u2069 פיקסלים.';
  const failed = () => status.textContent = 'טעינת התמונה נכשלה. נסי הורדה ישירה או רענון של תצוגת השירות.';
  img.addEventListener('load', loaded); img.addEventListener('error', failed);
  if (img.complete) { if (img.naturalWidth) loaded(); else failed(); }
}

function ringPoints(geometry) { return geometry.coordinates[0].slice(0, -1).map(p => [p[0], p[1]]); }
function centroid(geometry) {
  let weight = 0, sumX = 0, sumY = 0;
  geometry.coordinates.forEach((ring, index) => {
    // Translate first to avoid cancellation with large national-grid coordinates.
    const refX = ring[0][0], refY = ring[0][1];
    let area = 0, x = 0, y = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const ax = ring[i][0] - refX, ay = ring[i][1] - refY, bx = ring[i + 1][0] - refX, by = ring[i + 1][1] - refY;
      const cross = ax * by - bx * ay; area += cross; x += (ax + bx) * cross; y += (ay + by) * cross;
    }
    const signedWeight = Math.abs(area) * (index ? -1 : 1);
    weight += signedWeight; sumX += (refX + x / (3 * area)) * signedWeight; sumY += (refY + y / (3 * area)) * signedWeight;
  });
  return [sumX / weight, sumY / weight];
}
function buildingById(id = el('building').value) { return project?.buildings.find(b => String(b.properties.id) === id); }
function shapeFor(geometry, origin) {
  const shape = new THREE.Shape(geometry.coordinates[0].map(p => new THREE.Vector2(p[0] - origin[0], p[1] - origin[1])));
  for (const ring of geometry.coordinates.slice(1)) shape.holes.push(new THREE.Path(ring.map(p => new THREE.Vector2(p[0] - origin[0], p[1] - origin[1]))));
  return shape;
}
function facadeEdge(building, index) {
  const ring = ringPoints(building.geometry), start = ring[index], end = ring[(index + 1) % ring.length];
  const ref = ring[0];
  let signedArea = 0;
  ring.forEach((p, i) => { const q = ring[(i + 1) % ring.length]; signedArea += (p[0] - ref[0]) * (q[1] - ref[1]) - (q[0] - ref[0]) * (p[1] - ref[1]); });
  const dx = end[0] - start[0], dy = end[1] - start[1], length = Math.hypot(dx, dy);
  return { start, end, length, direction: [dx / length, dy / length], outward: signedArea > 0 ? [dy / length, -dx / length] : [-dy / length, dx / length] };
}
function masonryTexture(kind) {
  if (!['stone', 'brick'].includes(kind)) return null;
  const canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 256;
  const context = canvas.getContext('2d'); context.fillStyle = '#d9d5c9'; context.fillRect(0, 0, 256, 256);
  const rows = kind === 'brick' ? 8 : 4, columns = kind === 'brick' ? 4 : 3, height = 256 / rows, width = 256 / columns;
  for (let row = 0; row < rows; row++) for (let column = -1; column < columns + 1; column++) {
    const tone = 205 + ((row * 23 + column * 13 + 31) % 35);
    context.fillStyle = 'rgb(' + tone + ',' + tone + ',' + Math.max(0, tone - 5) + ')';
    context.fillRect(column * width + (row % 2 ? width / 2 : 0) + 1.5, row * height + 1.5, width - 3, height - 3);
  }
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(0.35, 0.35); return texture;
}

class CityViewer {
  constructor(container) {
    this.container = container; this.scene = new THREE.Scene(); this.scene.background = new THREE.Color('#d8e7ed');
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000); this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace; this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.15;
    container.prepend(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.dampingFactor = 0.1; this.controls.maxPolarAngle = Math.PI - 0.015; this.controls.minDistance = 1; this.controls.maxDistance = 1500;
    const sky = new THREE.HemisphereLight('#f1f8ff', '#6b7960', 2.1); sky.position.set(0, 0, 300); this.scene.add(sky);
    const sun = new THREE.DirectionalLight('#fff3d6', 3.2); sun.position.set(-65, -85, 130); sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -180, right: 180, top: 180, bottom: -180, near: 1, far: 500 }); sun.shadow.normalBias = 0.05; this.scene.add(sun);
    this.modelGroup = new THREE.Group(); this.scene.add(this.modelGroup);
    this.selectionBox = new THREE.BoxHelper(new THREE.Mesh(), 0xf0ae30); this.selectionBox.visible = false; this.scene.add(this.selectionBox);
    this.raycaster = new THREE.Raycaster(); this.pointerStart = null; this.generation = 0;
    this.renderer.domElement.addEventListener('pointerdown', e => { this.pointerStart = [e.clientX, e.clientY]; });
    this.renderer.domElement.addEventListener('pointerup', e => {
      if (!this.pointerStart || Math.hypot(e.clientX - this.pointerStart[0], e.clientY - this.pointerStart[1]) > 5) return;
      const r = this.renderer.domElement.getBoundingClientRect();
      this.raycaster.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), this.camera);
      const hit = this.raycaster.intersectObjects(this.modelGroup.children, true).find(h => h.object.userData.buildingId);
      if (hit) selectBuilding(hit.object.userData.buildingId);
    });
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(container);
    const animate = () => { requestAnimationFrame(animate); this.controls.update(); this.renderer.render(this.scene, this.camera); }; animate();
  }
  resize() {
    const { width, height } = this.container.getBoundingClientRect(); if (!width || !height) return;
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false);
  }
  disposeObject(group) {
    group.traverse(o => { o.geometry?.dispose(); for (const m of Array.isArray(o.material) ? o.material : [o.material]) { m?.map?.dispose(); m?.dispose(); } });
  }
  clear() {
    this.generation++; this.disposeObject(this.modelGroup); this.modelGroup.clear(); this.buildingGroups = new Map();
    this.selectionBox.visible = false; this.extent = null; this.origin = null;
  }
  build(data, resetCamera = false) {
    this.generation++; this.disposeObject(this.modelGroup); this.modelGroup.clear(); this.origin = centroid(data.boundary);
    const ring = data.boundary.coordinates[0];
    const width = Math.max(...ring.map(p => p[0])) - Math.min(...ring.map(p => p[0])), depth = Math.max(...ring.map(p => p[1])) - Math.min(...ring.map(p => p[1]));
    this.extent = Math.max(width, depth, 20); this.maxHeight = Math.max(...data.buildings.map(b => b.properties.height + (b.properties.base_elevation || 0)), 10);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(this.extent * 2.7, this.extent * 2.7), new THREE.MeshStandardMaterial({ color: '#bdc7b4', roughness: 1 }));
    ground.position.z = -0.15; ground.receiveShadow = true; this.modelGroup.add(ground);
    const site = new THREE.Mesh(new THREE.ShapeGeometry(shapeFor(data.boundary, this.origin)), new THREE.MeshStandardMaterial({ color: '#d5d1bb', roughness: 1 }));
    site.position.z = -0.05; site.receiveShadow = true; this.modelGroup.add(site);
    this.modelGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring.map(p => new THREE.Vector3(p[0] - this.origin[0], p[1] - this.origin[1], 0.08))), new THREE.LineBasicMaterial({ color: '#1d6444' })));
    this.buildingGroups = new Map(); for (const feature of data.buildings) this.addBuilding(feature);
    for (const slab of data.overhead_obstructions || []) this.addSlab(slab, this.modelGroup, '#bbc0b6');
    for (const asset of data.assets || []) this.addAsset(asset, this.generation);
    this.camera.far = Math.max(5000, this.extent * 20); this.camera.updateProjectionMatrix(); this.controls.maxDistance = this.extent * 15;
    if (resetCamera) this.preset('aerial'); this.select(el('building').value); el('viewerMessage').hidden = true;
  }
  addBuilding(feature) {
    const props = feature.properties, base = props.base_elevation || 0, kind = props.material || 'stone';
    const group = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({ color: props.color || '#d9bd89', map: masonryTexture(kind), roughness: kind === 'glass' ? 0.12 : kind === 'plaster' ? 0.88 : 0.73, metalness: kind === 'glass' ? 0.35 : 0.02 });
    const geometry = new THREE.ExtrudeGeometry(shapeFor(feature.geometry, this.origin), { depth: props.height, bevelEnabled: false, steps: 1 });
    const mesh = new THREE.Mesh(geometry, [new THREE.MeshStandardMaterial({ color: '#c9c8bb', roughness: 0.92 }), material]); mesh.position.z = base; mesh.castShadow = true; mesh.receiveShadow = true; group.add(mesh);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 30), new THREE.LineBasicMaterial({ color: '#9b8f79', transparent: true, opacity: 0.55 })); edges.position.z = base; group.add(edges);
    const floorHeight = props.height / props.floors;
    for (let i = 0; i < ringPoints(feature.geometry).length; i++) {
      const edge = facadeEdge(feature, i), windows = Math.min(60, Math.max(1, Math.floor(edge.length / 3.8)));
      const quaternion = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(new THREE.Vector3(-edge.outward[1], edge.outward[0], 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(...edge.outward, 0)));
      for (let floor = 0; floor < Math.min(props.floors, 150); floor++) {
        for (let column = 0; column < windows; column++) {
          const distance = edge.length * (column + 0.5) / windows;
          const pane = new THREE.Mesh(new THREE.PlaneGeometry(Math.min(1.8, edge.length / windows * 0.55), Math.min(1.7, floorHeight * 0.55)), new THREE.MeshStandardMaterial({ color: kind === 'glass' ? '#63899b' : '#537583', metalness: 0.28, roughness: 0.24, side: THREE.DoubleSide }));
          pane.quaternion.copy(quaternion); pane.position.set(edge.start[0] + edge.direction[0] * distance + edge.outward[0] * 0.04 - this.origin[0], edge.start[1] + edge.direction[1] * distance + edge.outward[1] * 0.04 - this.origin[1], base + floor * floorHeight + floorHeight * 0.57); group.add(pane);
        }
      }
    }
    for (const balcony of props.balconies || []) {
      if (!balcony.footprint?.coordinates) continue;
      const thickness = balcony.thickness ?? 0.25, railingHeight = balcony.railing_height ?? 1.05;
      const slab = new THREE.Mesh(new THREE.ExtrudeGeometry(shapeFor(balcony.footprint, this.origin), { depth: thickness, bevelEnabled: false }), new THREE.MeshStandardMaterial({ color: '#d4d1c6', roughness: 0.8 }));
      slab.position.z = balcony.elevation - thickness; slab.castShadow = true; slab.receiveShadow = true; group.add(slab);
      const points = balcony.footprint.coordinates[0].map(p => new THREE.Vector3(p[0] - this.origin[0], p[1] - this.origin[1], balcony.elevation + railingHeight));
      if (railingHeight > 0) group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: '#5c6260' })));
      for (const point of railingHeight > 0 ? points.slice(0, -1) : []) {
        const post = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, railingHeight, 5), new THREE.MeshStandardMaterial({ color: '#5c6260' }));
        post.rotation.x = Math.PI / 2; post.position.copy(point); post.position.z -= railingHeight / 2; group.add(post);
      }
    }
    for (const slab of props.slabs || []) this.addSlab(slab, group, props.color || '#d9bd89');
    group.traverse(object => { object.userData.buildingId = String(props.id); }); this.modelGroup.add(group); this.buildingGroups.set(String(props.id), group);
  }
  addSlab(record, group, color) {
    if (!record.footprint?.coordinates) return;
    const thickness = record.thickness ?? 0.2;
    const slab = new THREE.Mesh(new THREE.ExtrudeGeometry(shapeFor(record.footprint, this.origin), { depth: thickness, bevelEnabled: false }), new THREE.MeshStandardMaterial({ color, roughness: 0.8 }));
    slab.position.z = record.elevation - thickness; slab.castShadow = true; slab.receiveShadow = true; group.add(slab);
  }
  addAsset(asset, generation) {
    if (!asset.url) return;
    if (asset.kind === 'aerial_photo' && asset.placement?.bounds) {
      const [minX, minY, maxX, maxY] = asset.placement.bounds;
      new THREE.TextureLoader().load(asset.url, texture => {
        if (generation !== this.generation) { texture.dispose(); return; } texture.colorSpace = THREE.SRGBColorSpace;
        const photo = new THREE.Mesh(new THREE.PlaneGeometry(maxX - minX, maxY - minY), new THREE.MeshStandardMaterial({ map: texture, roughness: 1 }));
        photo.position.set((minX + maxX) / 2 - this.origin[0], (minY + maxY) / 2 - this.origin[1], 0.005); photo.receiveShadow = true; photo.userData.aerialAsset = asset.id || asset.url; this.modelGroup.add(photo);
      }, undefined, () => message('צילום האוויר נשמר, אך טעינתו לתצוגה נכשלה.'));
    }
    if (asset.kind === 'model' && /\.obj(?:\?|$)/i.test(asset.url)) {
      new OBJLoader().load(asset.url, object => {
        if (generation !== this.generation) { this.disposeObject(object); return; }
        object.traverse(mesh => { if (mesh.isMesh) { for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose(); mesh.material = new THREE.MeshStandardMaterial({ color: '#acb3ad', roughness: 0.9 }); mesh.castShadow = true; mesh.receiveShadow = true; } });
        object.userData.contextAsset = asset.id || asset.url; this.modelGroup.add(object);
      }, undefined, () => message('קובץ הסביבה נשמר, אך טעינת מודל OBJ לתצוגה נכשלה.'));
    }
  }
  select(id) { const group = this.buildingGroups?.get(String(id)); this.selectionBox.visible = !!group; if (group) this.selectionBox.setFromObject(group); }
  snapshot() { return { position: this.camera.position.toArray(), target: this.controls.target.toArray(), fov: this.camera.fov }; }
  restore(camera) {
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false; this.controls.update();
    this.camera.position.fromArray(camera.position); this.controls.target.fromArray(camera.target); this.camera.fov = camera.fov || 45; this.camera.updateProjectionMatrix(); this.controls.update();
    this.controls.enableDamping = damping;
  }
  preset(kind) {
    if (!this.extent) return;
    if (kind === 'aerial') this.restore({ position: [this.extent * 0.95, -this.extent * 1.12, Math.max(this.extent * 0.95, this.maxHeight * 2)], target: [0, 0, this.maxHeight * 0.22], fov: 45 });
    else {
      const building = buildingById() || project.buildings[0], edge = facadeEdge(building, 0), base = building.properties.base_elevation || 0;
      const center = [(edge.start[0] + edge.end[0]) / 2 - this.origin[0], (edge.start[1] + edge.end[1]) / 2 - this.origin[1]], distance = Math.max(25, building.properties.height * 1.4);
      this.restore({ position: [center[0] + edge.outward[0] * distance, center[1] + edge.outward[1] * distance, base + 1.7], target: [center[0], center[1], base + building.properties.height * 0.43], fov: 55 });
    }
  }
}

async function list(chooseId = project?.id) {
  projects = await api('/api/projects');
  el('projects').replaceChildren(new Option('בחירת מתחם', ''), ...projects.map(p => new Option(p.name, p.id)));
  if (chooseId && projects.some(p => p.id === chooseId)) el('projects').value = chooseId;
}
function renderMap() {
  if (!hasGeometry()) { el('scene').replaceChildren(); return; }
  const svg = el('scene'); svg.replaceChildren(); const ring = project.boundary.coordinates[0];
  const minX = Math.min(...ring.map(p => p[0])), minY = Math.min(...ring.map(p => p[1]));
  const scale = Math.min(580 / (Math.max(...ring.map(p => p[0])) - minX), 340 / (Math.max(...ring.map(p => p[1])) - minY));
  const point = p => [60 + (p[0] - minX) * scale, 400 - (p[1] - minY) * scale];
  function polygon(points, fill, stroke, id) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    node.setAttribute('points', points.map(p => point(p).join(',')).join(' ')); node.setAttribute('fill', fill); node.setAttribute('stroke', stroke); node.setAttribute('stroke-width', id === el('building').value ? '3' : '1.5');
    if (id) { node.style.cursor = 'pointer'; node.onclick = () => selectBuilding(id); } svg.append(node);
  }
  const site = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  site.setAttribute('d', project.boundary.coordinates.map(r => 'M' + r.map(p => point(p).join(',')).join('L') + 'Z').join(' '));
  site.setAttribute('fill', '#d8e4d8'); site.setAttribute('fill-rule', 'evenodd'); site.setAttribute('stroke', '#247849'); site.setAttribute('stroke-width', '1.5'); svg.append(site);
  for (const building of project.buildings) {
    polygon(building.geometry.coordinates[0], building.properties.color || '#d9bd89', String(building.properties.id) === el('building').value ? '#e5a62b' : '#635b4c', String(building.properties.id));
    for (const balcony of building.properties.balconies || []) polygon(balcony.footprint.coordinates[0], '#d4d1c6', '#635b4c');
  }
}
function selectBuilding(id) {
  el('building').value = String(id); const building = buildingById(); if (!building) return;
  el('color').value = building.properties.color || '#d9bd89'; el('material').value = building.properties.material || 'stone'; el('selection').textContent = 'מבנה ' + building.properties.id;
  el('floor').max = building.properties.floors; el('floor').value = Math.min(Number(el('floor').value), building.properties.floors);
  el('edge').replaceChildren(...ringPoints(building.geometry).map((_, i) => new Option('חזית ' + (i + 1) + ' (' + facadeEdge(building, i).length.toFixed(1) + ' מ׳)', String(i))));
  el('balcony').replaceChildren(new Option('מרפסת חדשה', ''), ...(building.properties.balconies || []).map(b => new Option(b.id + ' • גובה ' + b.elevation.toFixed(1) + ' מ׳', b.id)));
  el('removeBalcony').disabled = true; clearBalconyReport(); viewer?.select(String(id)); renderMap();
}
function clearBalconyReport() {
  balconyReports = []; el('balconyReport').textContent = ''; el('analysisBalcony').replaceChildren(new Option('אין דוח', ''));
  el('analysisView').setAttribute('hidden', ''); el('balconyTop').disabled = true; el('balconySection').disabled = true;
  el('analysisLegend').textContent = 'יש לבצע בדיקה. החישוב מתייחס לגיאומטריה שנקלטה בלבד; מידע חסר על בליטות, גגות וסביבה מגביל את המסקנות.';
}
function renderGallery() {
  const unique = new Map([...(project?.renders || []), ...(extraRenders.get(project?.id) || [])].filter(r => r.url).map(r => [r.url, r]));
  const gallery = el('gallery'); gallery.replaceChildren();
  if (!unique.size) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'טרם הופקו תמונות לפרויקט הנוכחי.'; gallery.append(p); return; }
  for (const item of [...unique.values()].reverse()) {
    const figure = document.createElement('figure'), link = document.createElement('a'), img = document.createElement('img'), caption = document.createElement('figcaption');
    link.href = item.url; link.target = '_blank'; link.rel = 'noopener'; img.src = item.url; img.alt = item.name || 'תמונת הדמיה של המתחם'; img.loading = 'lazy'; link.append(img);
    caption.textContent = (item.name || 'רינדור מצלמה') + ' • גרסה ' + (item.version ?? project.version);
    const actions = document.createElement('div'); actions.className = 'actions';
    if (item.camera?.position && item.camera?.target && viewer) {
      const openCamera = document.createElement('button'); openCamera.className = 'secondary'; openCamera.textContent = 'פתיחת המצלמה';
      openCamera.onclick = () => { show3D(); viewer.restore(item.camera); activeCameraName = item.name || 'מצלמת הדמיה'; }; actions.append(openCamera);
    }
    if (item.blend_url) { const blend = document.createElement('a'); blend.href = item.blend_url; blend.textContent = 'קובץ Blender'; blend.setAttribute('download', ''); actions.append(blend); }
    caption.append(actions); figure.append(link, caption); gallery.append(figure);
  }
}
function renderAssets() {
  el('assets').replaceChildren();
  for (const asset of project?.assets || []) {
    const item = document.createElement('li'), link = document.createElement('a'), note = document.createElement('div');
    link.href = asset.url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = asset.name; item.append(link);
    note.className = 'hint'; note.textContent = asset.kind === 'model' ? 'מודל סביבה נפרד' : asset.placement?.bounds ? 'צילום ממוקם במערכת הפרויקט' : 'צילום ייחוס ללא מיקום'; item.append(note);
    if (asset.kind !== 'model' && asset.url) { const img = document.createElement('img'); img.src = asset.url; img.alt = asset.name; img.loading = 'lazy'; item.append(img); } el('assets').append(item);
  }
}
function updateButtonStates() {
  el('sample').disabled = pendingActions > 0; el('openSynthetic').disabled = pendingActions > 0; el('projects').disabled = pendingActions > 0; el('file').disabled = pendingActions > 0; el('building').disabled = pendingActions > 0;
  el('createDossier').disabled = pendingActions > 0;
  if (!project) return; const errors = project.errors || [], geometry = hasGeometry();
  el('approve').disabled = !geometry || !!errors.length || project.approved; el('export').disabled = !geometry || !project.approved || !!errors.length;
  el('report').disabled = false;
  for (const id of ['style', 'addBalcony', 'balconyCheck', 'boundaryImport', 'planAlign', 'assetUpload']) el(id).disabled = !geometry;
  for (const id of ['aerial', 'street', 'saveCamera']) el(id).disabled = !viewer || !geometry;
  el('view').disabled = !geometry;
  el('intakeUpload').disabled = false; el('readinessCheck').disabled = false;
  const finalBlocked = el('renderMode').value === 'final' && !project.synthetic && readiness?.ready_for_final_render !== true;
  el('renderImage').disabled = !viewer || !geometry || !project.approved || !!errors.length || finalBlocked;
  el('restoreCamera').disabled = !viewer || !el('cameras').value; el('restoreVersion').disabled = !el('versions').value; el('removeBalcony').disabled = !el('balcony').value;
  if (pendingActions) for (const id of ['approve', 'export', 'report', 'style', 'addBalcony', 'removeBalcony', 'balconyCheck', 'boundaryImport', 'planAlign', 'assetUpload', 'saveCamera', 'restoreCamera', 'restoreVersion', 'renderImage', 'intakeUpload', 'readinessCheck']) el(id).disabled = true;
}
function render(resetCamera = false, preserveBuilding = true) {
  if (!project) return; const previousBuilding = preserveBuilding ? el('building').value : '';
  el('title').textContent = project.name + ' • גרסה ' + project.version;
  const buildings = project.buildings || [], errors = project.errors || [];
  message(!hasGeometry() ? 'תיק נפרד פתוח ללא מודל. יש לקלוט מקורות; לא נוצרו גבולות או מיקומי מבנים משוערים.' : errors.length ? errors.join('\n') : project.approved ? 'ההעמדה אושרה על ידי המשתמשת. רינדור טיוטה אינו מאמת את הנתונים; הדמיה סופית תלויה באסמכתאות.' : 'הבדיקה הגיאומטרית עברה. יש לסקור ולאשר את ההעמדה.');
  el('building').replaceChildren(...buildings.map(b => new Option('מבנה ' + b.properties.id, String(b.properties.id))));
  if (buildings.some(b => String(b.properties.id) === previousBuilding)) el('building').value = previousBuilding;
  if (hasGeometry()) { viewer?.build(project, resetCamera); selectBuilding(el('building').value); }
  else {
    viewer?.clear(); el('selection').textContent='אין מודל משויך לתיק'; el('viewerMessage').hidden=false;
    el('viewerMessage').textContent='טרם נקלטה גיאומטריה לתיק זה. המודל הסינתטי אינו משויך אליו.';
    el('balcony').replaceChildren(new Option('אין מרפסות','')); clearBalconyReport(); el('color').value='#d9bd89';
  }
  const name = el('cameras').value; el('cameras').replaceChildren(new Option('בחירת מצלמה', ''), ...Object.keys(project.cameras || {}).map(n => new Option(n, n))); if (project.cameras?.[name]) el('cameras').value = name;
  const versions = [...new Set((project.history || []).map(s => s.version))].sort((a, b) => b - a);
  el('versions').replaceChildren(new Option('בחירת גרסה', ''), ...versions.map(v => new Option('גרסה ' + v, String(v))));
  updateButtonStates(); renderMap(); renderGallery(); renderAssets(); renderReadiness(); renderIntakeResults();
}
async function updateProject(next, resetCamera = false) { project = next; readiness=null; await list(project.id); readiness=await api('/api/readiness',{id:project.id}); render(resetCamera); }
function show3D() { mapVisible = false; el('scene').setAttribute('hidden', ''); el('viewer').hidden = false; el('view').textContent = 'מפה דו־ממדית'; viewer?.resize(); }
function jsonFile(input) {
  if (!input.files[0]) throw Error('יש לבחור קובץ.');
  return input.files[0].text().then(text => { try { return JSON.parse(text); } catch { throw Error('הקובץ אינו JSON תקין.'); } });
}

function renderReadiness() {
  if (!project) return;
  el('sourceClassification').textContent = project.synthetic === true
    ? 'סיווג: ניסוי סינתטי / הצעת עיצוב. המקור אינו מדידה של מתחם אמיתי.'
    : readiness?.evidence_complete === true
      ? 'סיווג: תיק אמיתי עם אסמכתאות סקירה רשומות לגרסה הנוכחית. אין תעודת מדידה, הנדסה או כשרות.'
      : 'סיווג: תיק אמיתי; נתונים שסיפקה המשתמשת או מקורות ציבוריים טעונים אימות. אין שיוך למודל ההדגמה.';
  const missing = readiness?.missing || [];
  el('readinessMissing').replaceChildren(...missing.map(key => {const item=document.createElement('li');item.textContent=evidenceLabels[key]||String(key);return item;}));
  const stages={passed:'עבר בדיקה',missing:'חסר מידע',blocked:'חסום',synthetic:'ניסוי סינתטי',not_applicable_synthetic:'אינו חל על ניסוי סינתטי',not_implemented:'טרם מומש',ready:'מוכן לשלב הבא',registered:'נרשם'};
  el('readinessWorkflow').replaceChildren(...(readiness?.workflow||[]).map(record=>{const item=document.createElement('li');item.textContent=record.stage+' — '+(stages[record.status]||record.status);return item;}));
  el('readinessStatus').textContent = !readiness ? 'טרם בוצעה בדיקת מוכנות לגרסה זו.'
    : project.synthetic === true ? readiness.ready_for_final_render
      ? 'הניסוי הסינתטי עומד בבדיקות ההעמדה שנדרשו. אפשר להפיק רינדור ניסוי; אין בכך הוכחת מוכנות או דיוק למתחם אמיתי.'
      : 'הניסוי הסינתטי דורש השלמת בדיקות ההעמדה ואישור לפני רינדור. גם לאחר אישור אין בכך הוכחת דיוק למתחם אמיתי.'
    : readiness.ready_for_final_render ? 'האסמכתאות הנדרשות רשומות לגרסה הנוכחית. ההדמיה עדיין אינה אישור מקצועי או תעודה.'
    : 'הדמיה סופית חסומה בגלל אסמכתאות חסרות או שאינן תואמות לגרסה. טיוטה אפשרית רק לאחר קליטת גיאומטריה ואישור העמדה, ומסומנת כנתונים שלא אומתו.';
}
function renderIntakeResults() {
  const stored = project?.intake_sources || project?.intake || project?.inputs || [];
  const records = [...(Array.isArray(stored) ? stored : Object.entries(stored).map(([id,record])=>({...record,id}))), ...(intakeResultsByProject.get(project?.id)||[])];
  const unique = new Map();
  records.forEach((record,index)=>{const key=record.id||record.source_id||record.sha256||index;unique.set(key,{...unique.get(key),...record});});
  el('intakeResults').replaceChildren(...[...unique.values()].map(record => {
    const item=document.createElement('li');
    const state=record.status||record.meaning||record.description||(record.supported?'התוכן נבדק בפורמט הנתמך':'אין חילוץ נתונים מוכח לפורמט זה');
    item.textContent=(record.name||record.filename||record.format||'מקור')+' • מקור שסופק / נרשם • '+(typeof state==='string'?state:JSON.stringify(state));
    if(record.sha256)item.textContent+=' • SHA-256 '+record.sha256.slice(0,16)+'…';
    return item;
  }));
  el('intakeReferences').textContent=JSON.stringify({project_id:project?.id,geometry_version_sha256:readiness?.geometry_version_sha256||project?.geometry_version_sha256,sources:[...unique.values()].map(record=>({id:record.id||record.source_id,name:record.name,sha256:record.sha256,verification:'source_registration_is_not_professional_review'}))},null,2);
}
async function refreshCapabilities() {
  const report=await api('/api/capabilities');
  el('capabilityList').replaceChildren(...(report.formats||[]).map(record=>{
    const item=document.createElement('li');
    item.textContent=(record.extension||record.format)+' • '+(record.label||record.status||'')+(record.details?' — '+(typeof record.details==='string'?record.details:JSON.stringify(record.details)):'');
    return item;
  }));
}
el('showArchitectural').onclick=()=>{
  const frame=el('architecturalFrame');frame.hidden=!frame.hidden;
  if(!frame.hidden&&!frame.hasAttribute('src'))frame.src='/showcase/v3/viewer.html';
  el('showArchitectural').textContent=frame.hidden?'פתיחת המודל האדריכלי כאן':'סגירת התצוגה האדריכלית';
};
el('dossierForm').onsubmit=event=>{
  event.preventDefault();
  action(async()=>{
    const data=Object.fromEntries(new FormData(event.target));
    const parcels=data.parcels.trim()?data.parcels.trim().split(/[,\s]+/).map(Number):[];
    if(parcels.some(value=>!Number.isInteger(value)||value<=0))throw Error('יש להזין מספרי חלקות חיוביים, מופרדים בפסיק או ברווח.');
    await updateProject(await api('/api/dossier',{name:data.name.trim(),address:data.address.trim(),block:Number(data.block),parcels:parcels.join(',')}),true);
    el('dossierStatus').textContent='תיק '+project.name+' נפתח בנפרד, ללא מודל, גבול או גובה משוער. קליטת מקורות אינה אישור לדיוקם.';
  },el('createDossier'));
};
el('intakeUpload').onclick=()=>action(async()=>{
  const file=el('intakeFile').files[0];if(!file)throw Error('יש לבחור מקור לקליטה.');
  if(file.size>50*1024*1024)throw Error('גודל מקור מרבי בקליטה זו: 50 MiB (כ־52 MB). מגבלות הפורמט נבדקות גם בשרת.');
  const content_base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('קריאת המקור נכשלה.'));reader.readAsDataURL(file);});
  const received=await api('/api/intake',{id:project.id,name:file.name,content_base64});
  const records=intakeResultsByProject.get(project.id)||[];records.push({name:file.name,...received.result});intakeResultsByProject.set(project.id,records);
  await updateProject(received.project);
  el('intakeStatus').textContent='המקור נקלט ונבדק בהתאם ליכולת הפורמט. אין מכך אימות גבול, התאמה לקרקע, קומות או גובה.';
},el('intakeUpload'));
el('readinessCheck').onclick=()=>action(async()=>{readiness=await api('/api/readiness',{id:project.id});renderReadiness();updateButtonStates();},el('readinessCheck'));
el('renderMode').onchange=()=>{updateButtonStates();renderReadiness();};

el('projects').onchange = () => action(async () => { if (!el('projects').value) return; const fresh = await api('/api/projects'); await updateProject(fresh.find(p => p.id === el('projects').value), true); });
el('file').onchange = () => action(async () => updateProject(await api('/api/import', await jsonFile(el('file'))), true));
async function syntheticSource() {
  const response = await fetch('/examples/renders/baseline-project.json'); if (!response.ok) throw Error('טעינת הדוגמה הסינתטית נכשלה.');
  const source = await response.json(); source.synthetic = true; return source;
}
async function openSynthetic() {
  const source = await syntheticSource();
  await updateProject(await api('/api/import', source), true);
  show3D(); el('viewer').scrollIntoView({behavior:'auto', block:'center'});
  message('המודל הסינתטי הקיים נפתח בעותק עבודה: מבנים A, B, C. גררי בחלון לסיבוב, או בחרי מבט אווירי / רחוב. קובצי המקור והתמונות לא שונו.');
}
el('sample').onclick = () => action(openSynthetic, el('sample'));
el('openSynthetic').onclick = () => action(openSynthetic, el('openSynthetic'));
el('building').onchange = () => selectBuilding(el('building').value);
el('approve').onclick = () => action(async () => updateProject(await api('/api/approve', { id: project.id })), el('approve'));
el('style').onclick = () => action(async () => {
  const id = el('building').value; await updateProject(await api('/api/edit', { id: project.id, building: id, color: el('color').value, material: el('material').value }));
  message('חומר וצבע נשמרו למבנה ' + id + '. שאר המבנים נשמרו ללא שינוי.');
}, el('style'));
el('addBalcony').onclick = () => action(async () => {
  const building = buildingById(), props = building.properties, edge = facadeEdge(building, Number(el('edge').value));
  const width = Number(el('balconyWidth').value), depth = Number(el('balconyDepth').value), offset = Number(el('balconyOffset').value), floor = Number(el('floor').value);
  if (![width, depth, offset, floor].every(Number.isFinite) || width <= 0 || depth <= 0 || offset < 0 || offset + width > edge.length + 1e-8 || !Number.isInteger(floor) || floor < 1 || floor > props.floors) throw Error('יש להזין מידות חיוביות, קומה קיימת ומרפסת שאינה חורגת מאורך החזית.');
  const point = (distance, projection) => [edge.start[0] + edge.direction[0] * distance + edge.outward[0] * projection, edge.start[1] + edge.direction[1] * distance + edge.outward[1] * projection];
  const points = [point(offset, 0), point(offset + width, 0), point(offset + width, depth), point(offset, depth), point(offset, 0)];
  const id = el('balcony').value || 'balcony_' + Date.now().toString(36);
  const balcony = { id, footprint: { type: 'Polygon', coordinates: [points] }, elevation: (props.base_elevation || 0) + floor * props.height / props.floors, thickness: 0.25, railing_height: 1.05 };
  await updateProject(await api('/api/edit', { id: project.id, building: String(props.id), balconies: [...(props.balconies || []).filter(b => b.id !== id), balcony] }));
  el('balcony').value = id; updateButtonStates(); message('המרפסת נשמרה למבנה ' + props.id + '. יש להריץ בדיקת שטח פתוח לשמים.');
}, el('addBalcony'));
el('balcony').onchange = () => {
  updateButtonStates(); const building = buildingById(), balcony = building?.properties.balconies?.find(b => b.id === el('balcony').value); if (!balcony) return;
  const points = balcony.footprint.coordinates[0]; let selectedEdge = 0, minDistance = Infinity;
  for (let i = 0; i < ringPoints(building.geometry).length; i++) {
    const edge = facadeEdge(building, i), distance = Math.abs((points[0][0] - edge.start[0]) * edge.outward[0] + (points[0][1] - edge.start[1]) * edge.outward[1]);
    if (distance < minDistance) { minDistance = distance; selectedEdge = i; }
  }
  const edge = facadeEdge(building, selectedEdge); el('edge').value = String(selectedEdge);
  el('floor').value = Math.round((balcony.elevation - (building.properties.base_elevation || 0)) / (building.properties.height / building.properties.floors));
  el('balconyWidth').value = Math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]).toFixed(2); el('balconyDepth').value = Math.hypot(points[2][0] - points[1][0], points[2][1] - points[1][1]).toFixed(2);
  el('balconyOffset').value = ((points[0][0] - edge.start[0]) * edge.direction[0] + (points[0][1] - edge.start[1]) * edge.direction[1]).toFixed(2);
};
el('removeBalcony').onclick = () => action(async () => { const b = buildingById(); await updateProject(await api('/api/edit', { id: project.id, building: String(b.properties.id), balconies: (b.properties.balconies || []).filter(v => v.id !== el('balcony').value) })); }, el('removeBalcony'));
el('balconyCheck').onclick = () => action(async () => {
  balconyReports = await api('/api/balconies', { id: project.id, building: el('building').value });
  el('analysisBalcony').replaceChildren(...balconyReports.map(r => new Option(r.balcony_id, r.balcony_id)));
  el('balconyTop').disabled = !balconyReports.length; el('balconySection').disabled = !balconyReports.length;
  if (!balconyReports.length) { el('balconyReport').textContent = 'למבנה הנבחר אין מרפסות. יש להוסיף מרפסת כדי לבצע בדיקה.'; return; }
  showBalconySummary(); drawAnalysis('top');
}, el('balconyCheck'));
el('analysisBalcony').onchange = () => { showBalconySummary(); drawAnalysis('top'); };
el('balconyTop').onclick = () => drawAnalysis('top');
el('balconySection').onclick = () => drawAnalysis('section');
el('export').onclick = () => action(async () => downloadVerified('/api/export', { id: project.id }, 'model.obj'), el('export'));
el('report').onclick = () => action(async () => downloadVerified('/api/report', {id:project.id}, 'project-report.json'), el('report'));
el('view').onclick = () => { mapVisible = !mapVisible; el('scene').toggleAttribute('hidden', !mapVisible); el('viewer').hidden = mapVisible; el('view').textContent = mapVisible ? 'תצוגת תלת־ממד' : 'מפה דו־ממדית'; if (!mapVisible) viewer?.resize(); };
el('aerial').onclick = () => { show3D(); viewer?.preset('aerial'); activeCameraName = 'מבט אווירי'; };
el('street').onclick = () => { show3D(); viewer?.preset('street'); activeCameraName = 'מבט רחוב'; };
el('saveCamera').onclick = () => action(async () => {
  const name = el('cameraName').value.trim().slice(0, 80); if (!name) throw Error('יש לתת שם למצלמה.');
  await updateProject(await api('/api/camera', { id: project.id, name, camera: viewer.snapshot() }));
  el('cameras').value = name; updateButtonStates(); activeCameraName = name; message('המצלמה ' + name + ' נשמרה בתיק הפרויקט.');
}, el('saveCamera'));
el('cameras').onchange = updateButtonStates;
el('restoreCamera').onclick = () => action(async () => { const name = el('cameras').value; show3D(); viewer.restore(project.cameras[name]); activeCameraName = name; });
el('versions').onchange = updateButtonStates;
el('restoreVersion').onclick = () => action(async () => {
  const version = Number(el('versions').value); await updateProject(await api('/api/restore', { id: project.id, version }));
  el('versionNote').textContent = 'גרסה ' + version + ' שוחזרה ונשמרה כגרסה ' + project.version + '.';
}, el('restoreVersion'));
el('renderImage').onclick = () => action(async () => {
  message('מפיקים תמונה מהמצלמה הנוכחית. הרינדור עשוי להימשך מעט.');
  const result = await api('/api/render', { id: project.id, camera: viewer.snapshot(), name: activeCameraName, width: 1000, height: 750, mode:el('renderMode').value });
  const saved = extraRenders.get(project.id) || []; saved.push({ ...result, name: result.name || activeCameraName, version: result.version || project.version }); extraRenders.set(project.id, saved);
  const fresh = await api('/api/projects'); project = fresh.find(p => p.id === project.id) || project;
  renderGallery(); message('תמונת ההדמיה מוכנה. היא נשמרה בגלריית הפרויקט.');
}, el('renderImage'));
el('boundaryImport').onclick = () => action(async () => {
  const provider = el('boundarySource').value.trim(); if (!provider) throw Error('יש להזין מקור לגבול.');
  await updateProject(await api('/api/boundary', { id: project.id, geojson: await jsonFile(el('boundaryFile')), source_crs: el('sourceCrs').value.trim(), source: { provider, reference: el('boundaryReference').value.trim() || el('boundaryFile').files[0].name, source_type: 'user_supplied', acquired_at: new Date().toISOString() } }), true);
}, el('boundaryImport'));
el('planAlign').onclick = () => action(async () => {
  const plan = await jsonFile(el('planFile'));
  await updateProject(await api('/api/align', { id: project.id, buildings: plan.buildings, control_points: plan.control_points, max_residual: plan.max_residual ?? 0.25 }), true);
  message('התוכנית הותאמה בנקודות הבקרה. שארית RMS: ' + JSON.stringify(project.alignment?.residual_rms_m ?? project.alignment?.rms_residual_m ?? project.alignment) + '. יש לסקור את הגבול וההעמדה ולאשר שוב.');
}, el('planAlign'));
el('assetUpload').onclick = () => action(async () => {
  const file = el('assetFile').files[0]; if (!file) throw Error('יש לבחור קובץ צילום או מודל.');
  if (file.size > 10_000_000) throw Error('הקובץ גדול מדי; יש לבחור קובץ עד 10 MB.');
  const kind = el('assetKind').value; if (kind === 'model' && !/\.obj$/i.test(file.name)) throw Error('בשלב זה תצוגת מודל סביבה תומכת בקובצי OBJ.');
  const content_base64 = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(',')[1]); r.onerror = () => reject(Error('קריאת הקובץ נכשלה.')); r.readAsDataURL(file); });
  const request = { id: project.id, name: file.name, kind, content_base64, mime: file.type || (kind === 'model' ? 'text/plain' : 'application/octet-stream') };
  const inputValues = ['assetMinX', 'assetMinY', 'assetMaxX', 'assetMaxY'].map(id => el(id).value);
  if (kind === 'aerial_photo' && inputValues.some(v => v !== '')) {
    const bounds = inputValues.map(Number);
    if (inputValues.some(v => v === '') || !bounds.every(Number.isFinite) || bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) throw Error('יש למלא ארבעה גבולות מיקום תקינים במטרים.');
    request.placement = { bounds };
  }
  await updateProject(await api('/api/assets', request)); message('הקובץ נשמר כשכבת סביבה נפרדת.');
}, el('assetUpload'));

function currentBalconyReport() { return balconyReports.find(r => r.balcony_id === el('analysisBalcony').value) || balconyReports[0]; }
function showBalconySummary() {
  const r = currentBalconyReport(); if (!r) return;
  const area = value => Number(value).toFixed(2) + ' מ״ר';
  el('balconyReport').textContent = 'מרפסת ' + r.balcony_id + '\nשטח: ' + area(r.total_area_m2) + '\nחסימה בהיטל בגיאומטריה שנקלטה: ' + area(r.projected_obstruction_area_m2) + '\nשטח ללא חסימה בגיאומטריה שנקלטה: ' + area(r.geometric_open_sky_area_m2) + '\nנתוני הסביבה אינם מאומתים כמלאים. לא נקבע שטח פתוח לשמים בפועל ולא נקבעת כשרות.';
}
function drawAnalysis(kind) {
  const report = currentBalconyReport(); if (!report) return;
  const svg = el('analysisView'); svg.removeAttribute('hidden'); svg.replaceChildren();
  const ns = 'http://www.w3.org/2000/svg';
  const add = (name, attrs) => { const node = document.createElementNS(ns, name); Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v))); svg.append(node); return node; };
  const text = (x, y, value, attrs = {}) => { const node = add('text', { x, y, fill: '#17392f', 'font-size': 13, ...attrs }); node.textContent = value; };
  const geometryRings = geometry => geometry?.type === 'Polygon' ? geometry.coordinates : geometry?.type === 'MultiPolygon' ? geometry.coordinates.flat() : [];
  if (kind === 'top') {
    const points = geometryRings(report.top_view.balcony).flat();
    const minX = Math.min(...points.map(p => p[0])), maxX = Math.max(...points.map(p => p[0])), minY = Math.min(...points.map(p => p[1])), maxY = Math.max(...points.map(p => p[1]));
    const scale = Math.min(580 / Math.max(maxX - minX, 1), 220 / Math.max(maxY - minY, 1)), x = n => 60 + (n - minX) * scale, y = n => 270 - (n - minY) * scale;
    for (const [geometry, fill, opacity] of [[report.top_view.balcony, '#b7d2b5', 1], [report.top_view.blocked, '#c55c45', 0.8]]) {
      const polygons = geometry?.type === 'Polygon' ? [geometry.coordinates] : geometry?.type === 'MultiPolygon' ? geometry.coordinates : [];
      for (const polygon of polygons) add('path', { d: polygon.map(ring => 'M' + ring.map(p => x(p[0]) + ',' + y(p[1])).join('L') + 'Z').join(' '), fill, opacity, 'fill-rule': 'evenodd', stroke: '#496447', 'stroke-width': 1 });
    }
    text(650, 26, 'מבט על • ' + report.balcony_id, { 'text-anchor': 'end' }); text(650, 300, 'צפון ↑ • אדום: חסימה בהיטל • ירוק: שטח המרפסת', { 'text-anchor': 'end' });
    text(60, 295, (maxX - minX).toFixed(2) + ' מ׳');
    el('analysisLegend').textContent = 'החסימות בהיטל כוללות רק אובייקטים שנקלטו. גיאומטריה חסרה אינה נחשבת הוכחה לשטח פתוח לשמים.';
  } else {
    const section = report.section, allSegments = [...section.balcony_segments_x, ...section.obstructions.flatMap(o => o.segments_x)];
    if (!allSegments.length) { text(350, 160, 'החתך אינו חוצה את המרפסת.', { 'text-anchor': 'middle' }); return; }
    const minX = Math.min(...allSegments.map(s => s[0])), maxX = Math.max(...allSegments.map(s => s[1])), minZ = Math.min(0, section.min_z), maxZ = Math.max(section.max_z + 2, 5);
    const scale = Math.min(580 / Math.max(maxX - minX, 1), 225 / (maxZ - minZ)), x = n => 60 + (n - minX) * scale, y = n => 270 - (n - minZ) * scale;
    add('line', { x1: 45, y1: 270, x2: 655, y2: 270, stroke: '#80907f' });
    for (const obstruction of section.obstructions) for (const segment of obstruction.segments_x) add('rect', { x: x(segment[0]), y: y(obstruction.upper_z), width: (segment[1] - segment[0]) * scale, height: Math.max(1, (obstruction.upper_z - obstruction.lower_z) * scale), fill: '#c55c45', opacity: 0.7, stroke: '#994632' });
    for (const segment of section.balcony_segments_x) add('rect', { x: x(segment[0]), y: y(section.balcony_elevation_m), width: (segment[1] - segment[0]) * scale, height: Math.max(2, (section.balcony_elevation_m - section.balcony_bottom_m) * scale), fill: '#3b7853', stroke: '#215334' });
    text(650, 26, 'חתך מזרח–מערב • Y=' + section.plane_y.toFixed(2), { 'text-anchor': 'end' }); text(650, 301, 'ירוק: מרפסת • אדום: אובייקטים מעל המרפסת', { 'text-anchor': 'end' });
    text(55, 45, 'Z ' + maxZ.toFixed(1) + ' מ׳'); text(55, 265, 'Z ' + minZ.toFixed(1)); el('analysisLegend').textContent = 'החתך מתאר את הגיאומטריה לאורך מישור Y במרכז המרפסת. כל הגבהים מקומיים; אין אישור מדידה או כשרות.';
  }
}

try { viewer = new CityViewer(el('viewer')); } catch (error) { el('viewerMessage').textContent = 'תצוגת WebGL אינה זמינה בדפדפן זה. המפה הדו־ממדית זמינה; נסו דפדפן עם תמיכת WebGL.'; console.warn('3D viewport unavailable:', error); }
await action(async () => {
  await Promise.all([list(),refreshCapabilities()]);
  const source = await syntheticSource();
  const baselineKey = value => JSON.stringify({crs:value.crs, boundary:value.boundary, buildings:value.buildings.map(b => ({geometry:b.geometry, properties:b.properties}))});
  const threeBuildings = value => value.synthetic && value.buildings.length === 3 && value.buildings.map(b => String(b.properties.id)).sort().join(',') === 'A,B,C';
  const existing = projects.find(p => threeBuildings(p) && !p.assets?.length && baselineKey(p) === baselineKey(source)) || projects.find(threeBuildings);
  if (existing) { project = existing; readiness=await api('/api/readiness',{id:project.id}); el('projects').value = project.id; render(true, false); }
  else await updateProject(await api('/api/import', source), true);
});
window.goldenCity = {
  getProject: () => JSON.parse(JSON.stringify(project)), getCamera: () => viewer?.snapshot(), setCamera: camera => viewer?.restore(camera),
  getObjectIds: () => [...(viewer?.buildingGroups?.keys() || [])], isWebGLReady: () => !!viewer,
  getEnvironmentLayers: () => (viewer?.modelGroup.children || []).filter(o => o.userData.contextAsset || o.userData.aerialAsset).map(o => ({ id: o.userData.contextAsset || o.userData.aerialAsset, kind: o.userData.contextAsset ? 'model' : 'aerial_photo' })),
  getReadiness:()=>readiness?JSON.parse(JSON.stringify(readiness)):null,
};
