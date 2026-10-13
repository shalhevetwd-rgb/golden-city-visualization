import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
const data=JSON.parse(document.querySelector('#sitePayload').textContent), el=id=>document.getElementById(id);
const canvas=el('model'), renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
const world=new THREE.Scene();world.background=new THREE.Color('#dbe7ed');
world.add(new THREE.HemisphereLight(0xffffff,0x626244,2.4));const sun=new THREE.DirectionalLight(0xffffff,3);sun.position.set(-40,150,90);world.add(sun);
const camera=new THREE.PerspectiveCamera(48,1,.1,1000);camera.position.set(145,110,115);
const controls=new OrbitControls(camera,canvas);controls.target.set(40,5,-40);controls.update();
let group=null,current=data.scenes.existing,record=null,key='existing',loaded={},baseline=data.scenes.existing;
let frameCenter=new THREE.Vector3(40,5,-40),frameSpan=160;
const online=['http:','https:'].includes(location.protocol)&&location.pathname.startsWith('/site-clearing');
const stages={existing:'מצב קיים',cleared:'קרקע נקייה',planted:'בינוי רעיוני לאחר אישור העמדה'};
function resized(){const r=canvas.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix()}
window.addEventListener('resize',resized);resized();
function draw(){requestAnimationFrame(draw);controls.update();renderer.render(world,camera)}draw();
const decode=b=>Uint8Array.from(atob(b),c=>c.charCodeAt(0));
function download(bytes,name,mime){const u=URL.createObjectURL(new Blob([bytes],{type:mime})),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),10000)}
function showGroup(g){if(group)world.remove(group);group=g;world.add(g);window.siteModel=group;window.siteCamera=camera;window.siteControls=controls}
async function stage(name){key=name;current=data.scenes[name];record=null;baseline=data.scenes.existing;el('demoPhotos').hidden=false;el('street').disabled=false;
 if(!loaded[name])loaded[name]=await new Promise((resolve,reject)=>new GLTFLoader().parse(decode(data.models[name]).buffer,'',g=>resolve(g.scene),reject));
 showGroup(loaded[name]);el('state').textContent=stages[name]+' — סינתטי בלבד';map();buttons();window.siteStage=name}
for(const b of document.querySelectorAll('[data-stage]'))b.onclick=()=>stage(b.dataset.stage).catch(error);
el('aerial').onclick=()=>{camera.up.set(0,1,0);if(record){controls.target.copy(frameCenter);camera.position.copy(frameCenter).add(new THREE.Vector3(1,.8,1).multiplyScalar(frameSpan))}else{camera.position.set(145,110,115);controls.target.set(40,5,-40)}controls.update()};
el('top').onclick=()=>{camera.up.set(0,0,-1);if(record){controls.target.copy(frameCenter);camera.position.copy(frameCenter).add(new THREE.Vector3(0,frameSpan*1.8,0))}else{camera.position.set(40,190,-40);controls.target.set(40,5,-40)}controls.update()};
el('street').onclick=()=>{camera.up.set(0,1,0);camera.position.set(0,data.scenes.existing.diagnostic_cameras.street_ground_elevation_m-100+1.7,12);controls.target.set(25,8,-25);controls.update()};
el('downloadModel').onclick=()=>{if(record)download(JSON.stringify(record,null,2),'working-version.json','application/json');else download(decode(data.models[key]),key+'.glb','model/gltf-binary')};
el('downloadScene').onclick=()=>download(JSON.stringify(current,null,2),'site-scene.json','application/json');
function error(e){el('status').textContent='הפעולה לא הושלמה: '+e.message}
async function api(action,body){const r=await fetch('/api/reconstruction/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j}
function geometry(mesh,origin){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(mesh.vertices.flatMap(v=>[v[0]-origin[0],v[2]-origin[2],-(v[1]-origin[1])]),3));g.setIndex(mesh.triangles.flat());g.computeVertexNormals();return g}
function working(r){if(r.scene.stage==='existing')baseline=r.scene;else if(!record||r.scene.parent_sha256!==record.sha256)baseline=null;
 record=r;current=r.scene;el('demoPhotos').hidden=true;el('street').disabled=true;el('candidates').replaceChildren();
 if(r.proposal)for(const id of r.proposal.candidate_ids){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=id;label.append(input,document.createTextNode(' '+id));el('candidates').append(label)}
 const g=new THREE.Group(),origin=current.origin;
 const add=(id,mesh,color)=>{const o=new THREE.Mesh(geometry(mesh,origin),new THREE.MeshStandardMaterial({color,roughness:.85}));o.name=id;g.add(o)};
 add('terrain',current.terrain,'#67854e');for(const o of current.objects){if(!o.mesh)throw Error('נדרש mesh מסווג מפורש: '+o.id);add(o.id,o.mesh,o.id==='new-N'?'#3784b4':o.kind==='building'?'#c7ab7b':o.kind==='vegetation'?'#287339':'#737b7e')}
 showGroup(g);el('state').textContent=current.stage+' — '+(current.synthetic?'תיק סינתטי':'נתוני משתמשת, טרם אומתו');el('boundary').value=JSON.stringify(current.boundary,null,2);map();buttons();catalogue();
 if(current.stage==='existing'){const bounds=new THREE.Box3().setFromObject(g),size=bounds.getSize(new THREE.Vector3());bounds.getCenter(frameCenter);frameSpan=Math.max(size.x,size.y,size.z,1);camera.far=Math.max(1000,frameSpan*20);camera.updateProjectionMatrix();el('aerial').click()}
}
function buttons(){if(el('planningFrame')){el('planningFrame').hidden=!online||!record;if(online&&record&&el('planningFrame').dataset.version!==record.version_id){el('planningFrame').dataset.version=record.version_id;el('planningFrame').src='/planning?version='+record.version_id;}}el('clear').disabled=!online||!record||current.stage!=='existing'||!current.synthetic;el('place').disabled=!online||!record||current.stage!=='cleared'||!current.planning_brief?.approval;el('approve').disabled=!online||!record||current.stage!=='placement_pending';el('boundaryApprove').checked=false}
function map(){const b=current.boundary.coordinates[0],xs=b.map(p=>p[0]),ys=b.map(p=>p[1]),x0=Math.min(...xs),y0=Math.min(...ys),span=Math.max(Math.max(...xs)-x0,Math.max(...ys)-y0),points=ring=>ring.map(p=>[20+(p[0]-x0)/span*460,480-(p[1]-y0)/span*460].join(',')).join(' ');
 let markup=`<polygon points="${points(b)}" fill="none" stroke="#9558ae" stroke-width="3"/>`;
 for(const o of (baseline?.objects||[]).filter(o=>o.kind==='building'))markup+=`<polygon points="${points(o.footprint.coordinates[0])}" fill="none" stroke="#999" stroke-dasharray="4 3"/>`;
 for(const o of current.objects.filter(o=>o.kind==='building'))markup+=`<polygon points="${points(o.footprint.coordinates[0])}" fill="${o.planning_status?'#338bd6':'#d5b172'}" fill-opacity=".45" stroke="#344"/>`;
 if(current.placement)markup+=`<polygon points="${points(current.placement.building_line_envelope.coordinates[0])}" fill="none" stroke="#218849" stroke-dasharray="5 4"/>`;
 el('map').innerHTML=markup;el('qa').textContent=JSON.stringify(current.placement||current.qa||data.report.clearing,null,2)
}
async function proposal(scene){const j=await api('import',{scene});working({...j.version,proposal:j.proposal});el('status').textContent='נוצר עותק מצב קיים. לא אושרה הסרה. מבנים חוצים: '+j.proposal.crossing_buildings_not_removable.join(', ')}
async function catalogue(){if(!online)return;const r=await fetch('/api/reconstruction/versions'),d=await r.json();el('versions').replaceChildren(new Option('בחרי גרסה שמורה',''));for(const v of d.versions)el('versions').add(new Option(v.stage+' · '+v.version_id.slice(0,8),v.version_id))}
el('versions').onchange=async()=>{if(!el('versions').value)return;try{const r=await fetch('/api/reconstruction/version/'+el('versions').value),v=await r.json();if(!r.ok)throw Error(v.error);working(v)}catch(e){error(e)}};
el('start').onclick=()=>proposal(data.scenes.existing).catch(error);
el('source').onchange=async()=>{try{const scene=JSON.parse(await el('source').files[0].text());await proposal(scene)}catch(e){error(e)}};
el('reviseBoundary').onclick=async()=>{try{if(!record)throw Error('פתחי תיק עבודה תחילה');const scene=structuredClone(current);scene.boundary=JSON.parse(el('boundary').value);await proposal(scene)}catch(e){error(e)}};
el('clear').onclick=async()=>{try{if(!el('boundaryApprove').checked)throw Error('נדרש אישור גבול מפורש');const ids=[...el('candidates').querySelectorAll('input:checked')].map(c=>c.value);const j=await api('clear',{version_id:record.version_id,expected_sha256:record.sha256,selected_ids:ids,reviewer:el('reviewer').value,boundary_approved:true});working(j.version);el('status').textContent='נוצרה גרסת קרקע נקייה. מקור ו־TIN נשמרו.'}catch(e){error(e)}};
el('demoPlan').onclick=()=>{el('plan').value=JSON.stringify(data.plan,null,2)};
el('place').onclick=async()=>{try{const j=await api('place',{version_id:record.version_id,expected_sha256:record.sha256,plan:JSON.parse(el('plan').value)});working(j.version);el('status').textContent='העמדה ממתינה לאישור; בדקי את דוח הסטיות והמפלסים.'}catch(e){error(e)}};
el('approve').onclick=async()=>{try{const j=await api('approve-placement',{version_id:record.version_id,expected_sha256:record.sha256,reviewer:el('reviewer').value});working(j.version);el('status').textContent='נשמר אישור העמדה גיאומטרי בלבד. חסרים תכנון מפורט ואישורים מקצועיים.'}catch(e){error(e)}};
el('calculate').onclick=async()=>{try{const p={apartment_paldalet_m2:Number(el('paldalet').value),categories_are_disjoint:el('disjoint').checked,separate_areas_m2:{},planning_constraints:{}};
 for(const [id,key] of [['units','units'],['multiplier','multiplier'],['height','desired_height_m'],['count','building_count']])if(el(id).value!=='')p[key]=Number(el(id).value);
 for(const key of ['core','elevators','stairs','corridors','shafts','shared','lobby'])if(el('area-'+key).value!=='')p.separate_areas_m2[key]=Number(el('area-'+key).value);
 if(el('coverage').value!=='')p.planning_constraints.max_coverage_ratio=Number(el('coverage').value);if(el('floors').value!=='')p.planning_constraints.residential_floors=Number(el('floors').value);
 const envelope=current.placement?.building_line_envelope;let area=null;
 if(envelope){area=0;for(const [index,b] of envelope.coordinates.entries()){let ring=0;for(let i=0;i<b.length-1;i++)ring+=b[i][0]*b[i+1][1]-b[i+1][0]*b[i][1];area+=(index? -1:1)*Math.abs(ring/2)}}
 el('programResult').textContent=JSON.stringify(await api('alternatives',{program:p,buildable_area_m2:area}),null,2)}catch(e){error(e)}};
for(const node of document.querySelectorAll('[data-service]'))node.disabled=!online;
el('serviceNote').textContent=online?'עבודה דרך השירות: אישורים וגרסאות נשמרים על הדיסק.':'קובץ עצמאי: צפייה וסיבוב במודלים שנמסרו; ביצוע ניקוי/אישורים דורש את שירות המערכת.';
stage('existing').then(()=>catalogue()).catch(error);

window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===el('planningFrame')?.contentWindow&&event.data?.type==='planning-version'){record=event.data.record;current=record.scene;el('planningFrame').dataset.version=record.version_id;buttons();catalogue();}});
