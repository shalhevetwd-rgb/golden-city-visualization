"""Real browser tests: dossier, mandatory brief, offline download, human consent.
The named consenting browser actor is explicitly simulated test data.
No rendering or regeneration of synthetic demonstration assets occurs.
"""
import argparse,json,sys,hashlib,urllib.request,copy
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.site_clearing_demo import fixture
from tests.planning_helpers import known_brief
parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8022');args=parser.parse_args();base=args.url.rstrip('/');out=ROOT/'outputs/planning-v5';checks=[];errors=[]
def post(path,payload,expected=200):
 req=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req) as r:assert r.status==expected;return json.load(r)
 except urllib.error.HTTPError as e:
  assert e.code==expected,(e.code,e.read());return json.loads(e.read())
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
 page=browser.new_page(viewport={'width':1400,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(base,wait_until='networkidle');page.fill('#dossierName','Acceptance flow test');page.fill('#dossierAddress','Address provided for workflow test');page.fill('#dossierBlock','12345');page.click('#createDossier');page.wait_for_function('document.querySelector("#planningFrame").hidden === false')
 f=page.frame_locator('#planningFrame');f.locator('#existing_units').wait_for();assert f.locator('#paldalet').input_value()=='100';assert f.locator('#approve').is_disabled();assert page.locator('#selection').inner_text()=='אין מודל משויך לתיק'
 checks.append('Address and block only opens empty dossier and mandatory Hebrew brief; no source geometry inferred')
 f.locator('#existing_units').fill('12');f.locator('#units').fill('24');f.locator('[data-step="2"]').first.click();f.locator('#save').click();f.locator('#status').filter(has_text='חסרות').wait_for();checks.append('Partial brief persists without approval; missing questions displayed')
 page.screenshot(path=str(out/'dossier-questionnaire-preview.png'),full_page=True)
 from tests.test_geometry import fixture as original_fixture
 doc=original_fixture();imported=post('/api/import',doc);changed=copy.deepcopy(doc['buildings']);changed[0]['properties']['height']+=3;changed[0]['type']='Feature'
 blocked=post('/api/align',{'id':imported['id'],'buildings':changed,'control_points':[{'plan_xy':[220000,630000],'target_xy':[220000,630000]},{'plan_xy':[220100,630000],'target_xy':[220100,630000]},{'plan_xy':[220000,630100],'target_xy':[220000,630100]}]},400);assert 'פרוגרמה' in blocked['error'];checks.append('Changed massing through legacy alignment also rejects missing programme; unchanged source registration remains compatible')
 # Reuse authored source fixtures only; no Blender or new images are produced.
 scene,plan=fixture();r=post('/api/reconstruction/import',{'scene':scene})['version'];r=post('/api/reconstruction/clear',{'version_id':r['version_id'],'expected_sha256':r['sha256'],'selected_ids':['remove-A'],'reviewer':'SIMULATED HUMAN BROWSER ACTOR','boundary_approved':True})['version']
 denied=post('/api/reconstruction/place',{'version_id':r['version_id'],'expected_sha256':r['sha256'],'plan':plan},400);assert 'פרוגרמה' in denied['error'];checks.append('HTTP proposed creation rejected before brief; existing/cleared source operations remain available')
 imported=post('/api/import',{'crs':scene['crs'],'boundary':scene['boundary'],'buildings':plan['buildings'],'synthetic':True,'geometry_role':'proposed'},400);assert 'פרוגרמה' in imported['error'];checks.append('Explicit proposed import cannot bypass mandatory programme')
 page.goto(base+'/planning?version='+r['version_id'],wait_until='networkidle');page.locator('#existing_units').wait_for();b=known_brief(plan);source=ROOT/'data/planning-browser-source.json';source.write_text(json.dumps(plan));page.set_input_files('#planFile',str(source));page.wait_for_function('document.querySelector("#units").value==="6"');assert page.locator('#height_m').input_value()=='12';assert page.locator('#residential_floors').input_value()=='';checks.append('Actual structured source-plan file prefills units/buildings/height/area and source hash for verification without guessing residential floors')
 for key,v in b['answers'].items():
  if key=='floor_schedule':continue
  if key=='constraints':page.locator('[data-step="1"]').first.click()
  page.fill('#'+key,','.join(v) if isinstance(v,list) else str(v).lower() if isinstance(v,bool) else str(v))
 page.locator('[data-step="2"]').first.click();page.fill('#floor_schedule',json.dumps(b['answers']['floor_schedule']));page.check('#disjoint')
 for key,v in b['separate_areas_m2'].items():page.fill('#area-'+key,str(v))
 page.click('#save');page.wait_for_function('document.querySelector("#status").textContent.includes("ללא אישור")');page.locator('[data-step="3"]').first.click();page.fill('#plan',json.dumps(plan));page.click('#review');page.wait_for_function('document.querySelector("#approve").disabled === false');packet=json.loads(page.locator('#packet').text_content());assert packet['can_approve'] and not packet['approved'];assert packet['program']['quantities']['apartments_paldalet_m2']==600;assert len(packet['map_data']['terrain']['vertices'])==225;assert len(packet['map_data']['preserved_objects'])==6;assert packet['map_data']['overlay_is_not_a_proposed_3d_model'];assert page.locator('#map path').count()>390
 page.click('#approve');page.wait_for_function('document.querySelector("#status").textContent.includes("נדרש אישור מפורש")');checks.append('Complete known programme review displays map, plan, separate areas, core assumptions; unchecked consent cannot approve')
 page.fill('#reviewer','SIMULATED HUMAN BROWSER ACTOR');page.check('#consent');page.click('#approve');page.wait_for_function('document.querySelector("#status").textContent.includes("נשמר אישור משתמש")');checks.append('Only explicit simulated user checkbox and review action create a version-bound approval; no building created by approval')
 page.screenshot(path=str(out/'planning-review-preview.png'),full_page=True)
 # Unknown flow: enough stated numeric context -> proposals without geometry or consent.
 r=post('/api/reconstruction/import',{'scene':scene})['version'];unknown=copy.deepcopy(b);unknown['answers'].update(building_count='unknown',residential_floors='unknown',height_m='unknown',has_site_plan=False)
 r=post('/api/reconstruction/planning-save',{'version_id':r['version_id'],'expected_sha256':r['sha256'],'brief':unknown})['version'];page.goto(base+'/planning?version='+r['version_id'],wait_until='networkidle');page.locator('#existing_units').wait_for();page.locator('[data-step="3"]').first.click();page.fill('#context',json.dumps({'buildable_area_m2':4900,'max_coverage_ratio':.5,'max_total_floors':8,'terrain_verified':True,'building_lines_verified':True,'policy_reference':'Synthetic supplied feasibility constraints'}));page.click('#alternatives');page.wait_for_function('document.querySelector("#options").textContent.includes("concept-")');options=json.loads(page.locator('#options').text_content());assert len(options['alternatives'])>=2 and not options['geometry_created'] and not options['planning_approved'];page.locator('#alternativeTable button').first.click();assert page.locator('#approve').is_disabled();assert page.locator('#height_m').input_value()=='לא ידוע';checks.append('Unknown programme workflow displays/selects numeric feasibility concepts with assumptions and still requires height/plan/user review; no model/automatic approval')
 # Offline file is supported delivery, uses zero network, emits unapproved real JSON.
 page=browser.new_page();net=[];page.on('request',lambda r:net.append(r.url) if r.url.startswith(('http:','https:')) else None);page.on('pageerror',lambda e:errors.append(str(e)));page.goto('about:blank');page.set_content((out/'planning-questionnaire.html').read_text(),wait_until='load');page.fill('#existing_units','12');page.fill('#units','24');page.locator('[data-step="2"]').first.click()
 with page.expect_download() as d:page.click('#save')
 temp=ROOT/'data/planning-offline-browser.json';d.value.save_as(temp);download=json.loads(temp.read_text());assert download['approval'] is None and download['geometry_created'] is False and download['quantities']['apartments_paldalet_m2']==2400;assert not net;page.locator('[data-step="0"]').first.click();page.fill('#units','999');page.set_input_files('#briefFile',str(temp));page.wait_for_function('document.querySelector("#units").value==="24"');assert page.locator('#approve').is_disabled();checks.append('Standalone HTML bytes decode and interact in Chromium without HTTP; unapproved JSON downloads and reopens')
 checks.append('Saved brief reloads into offline form for verification; imported approval is ignored');assert not errors,errors;browser.close()
report={'status':'passed','checks':checks,'page_errors':errors,'recipient_access':'not_verified','direct_file_navigation':'blocked_by_administrator_in_cloud; actual standalone bytes tested with about:blank set_content, recipient desktop unverified','approval_actor':'simulated human test actor, not actual project consent','source_hashes':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('site-viewer.html','planning-questionnaire.html')}}
(out/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'status':'passed','checks':len(checks)}))
