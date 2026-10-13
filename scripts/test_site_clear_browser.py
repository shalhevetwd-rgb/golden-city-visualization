"""Actual offline GLB viewer and live approval/version/area workflow."""
from pathlib import Path
import json,sys,hashlib,math,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.planning_helpers import known_brief
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/site-v4'
ARTIFACTS=ROOT/'outputs/planning-v5';ARTIFACTS.mkdir(parents=True,exist_ok=True);raw=(ARTIFACTS/'site-viewer.html').read_bytes();checks=[]
parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
    page=browser.new_page(viewport={'width':1200,'height':1000});errors=[];network=[]
    page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:network.append(r.url) if r.url.startswith(('http:','https:')) else None)
    page.goto('about:blank');page.set_content(raw.decode(),wait_until='load');page.wait_for_function('window.siteStage === "existing"')
    assert page.evaluate('window.siteModel.getObjectByName("remove-A") !== undefined')
    page.click('[data-stage="cleared"]');page.wait_for_function('window.siteStage === "cleared"');assert page.evaluate('window.siteModel.getObjectByName("remove-A") === undefined')
    assert page.evaluate('window.siteModel.getObjectByName("outside-C") !== undefined')
    page.click('[data-stage="planted"]');page.wait_for_function('window.siteStage === "planted"');assert page.evaluate('window.siteModel.getObjectByName("new-N") !== undefined')
    checks.append('Three actual embedded GLBs load and reflect exact removed/new/preserved IDs')
    before=page.evaluate('window.siteCamera.position.toArray()');page.locator('#model').hover();page.mouse.down();page.mouse.move(500,340,steps=8);page.mouse.up();page.wait_for_timeout(250);assert page.evaluate('window.siteCamera.position.toArray()')!=before
    page.click('#top');assert math.isclose(page.evaluate('window.siteCamera.position.y'),190,abs_tol=1e-5);checks.append('Orbit and metric top camera respond')
    with page.expect_download() as download:page.click('#downloadModel')
    path=ARTIFACTS/'browser-downloaded-planted.glb';download.value.save_as(path);assert path.read_bytes()==(OUT/'planted.glb').read_bytes();path.unlink()
    assert not errors and not network;checks.append('Offline no network/errors and downloaded GLB exact')
    page.screenshot(path=str(ARTIFACTS/'historical-model-viewer-preview.png'),full_page=True)
    live=browser.new_page(viewport={'width':1200,'height':1000});live_errors=[];live.on('pageerror',lambda e:live_errors.append(str(e)))
    live.goto(args.url.rstrip('/')+'/site-clearing',wait_until='networkidle');live.wait_for_function('window.siteStage === "existing"')
    live.click('#start');live.wait_for_function('document.querySelectorAll("#candidates input").length === 2')
    assert live.locator('#candidates input:checked').count()==0
    live.fill('#reviewer','Synthetic browser test');live.locator('#candidates input[value="remove-A"]').check();live.click('#clear');live.wait_for_function('document.querySelector("#status").textContent.includes("נדרש אישור")')
    live.check('#boundaryApprove');live.click('#clear');live.wait_for_function('document.querySelector("#state").textContent.includes("cleared")')
    live.click('#demoPlan');plan=json.loads(live.locator('#plan').input_value());f=live.frame_locator('#planningFrame');f.locator('#existing_units').wait_for();brief=known_brief(plan)
    for key,v in brief['answers'].items():
        if key=='floor_schedule':continue
        if key=='constraints':f.locator('[data-step="1"]').first.click()
        f.locator('#'+key).fill(','.join(v) if isinstance(v,list) else str(v).lower() if isinstance(v,bool) else str(v))
    f.locator('[data-step="2"]').first.click();f.locator('#floor_schedule').fill(json.dumps(brief['answers']['floor_schedule']));f.locator('#save').click();f.locator('#status').filter(has_text='ללא אישור').wait_for();f.locator('[data-step="3"]').first.click();f.locator('#plan').fill(json.dumps(plan));f.locator('#review').click();f.locator('#reviewer').fill('SIMULATED HUMAN BROWSER ACTOR');f.locator('#consent').check();f.locator('#approve').click();f.locator('#status').filter(has_text='נשמר אישור משתמש').wait_for();live.wait_for_function('document.querySelector("#place").disabled === false');live.click('#place');live.wait_for_function('document.querySelector("#state").textContent.includes("placement_pending")')
    live.click('#approve');live.wait_for_function('document.querySelector("#state").textContent.includes("placement_approved")')
    checks.append('Actual service requires explicit source demolition consent and mandatory human programme approval before proposed meshes; saves cleared, pending and approved stages')
    live.fill('#units','6');live.click('#calculate');live.wait_for_function('document.querySelector("#programResult").textContent.includes("apartments_paldalet_total_m2")')
    result=json.loads(live.locator('#programResult').inner_text());assert result['program']['apartments_paldalet_total_m2']==600 and result['program']['gross_total_m2'] is None
    checks.append('Actual program endpoint defaults100paldalet; missing lobby/core/etc prevents gross calculation')
    saved=live.locator('#versions option').count();live.reload(wait_until='networkidle');live.wait_for_function('document.querySelector("#versions").options.length > 1');assert live.locator('#versions option').count()>=saved
    assert not live_errors;checks.append('Persisted catalogue survives reload; no JS errors')
    live.screenshot(path=str(ARTIFACTS/'site-approval-preview.png'),full_page=True);browser.close()
report={'status':'passed','viewer_sha256':hashlib.sha256(raw).hexdigest(),'checks':checks,'offline_http_requests':network,'page_errors':errors+live_errors,'recipient_access':'not_verified'}
(ARTIFACTS/'site-browser-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':'passed','checks':len(checks)}))
