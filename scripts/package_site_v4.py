"""Permanent delivery gate for exact sloping-site files, with post-ZIP reopening."""
from pathlib import Path
import json,hashlib,base64,re,sys,tempfile,zipfile
from PIL import Image,ImageDraw,ImageFont
from shapely.geometry import shape
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from delivery_validation import validate_file,validate_archive

def identity(p):return {'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

def embedded_models(path):
    text=path.read_text();payload=json.loads(re.search(r'<script id="sitePayload" type="application/json">(.*?)</script>',text,re.S).group(1))
    checks=[]
    with tempfile.TemporaryDirectory() as tmp:
        for name,encoded in payload['models'].items():
            raw=base64.b64decode(encoded,validate=True);source=path.parent/(name+'.glb')
            if raw!=source.read_bytes():raise ValueError('Embedded model differs from actual source: '+name)
            if payload['scenes'][name]!=json.loads((path.parent/(name+'.json')).read_text()):raise ValueError('Embedded scene differs from current source: '+name)
            p=Path(tmp)/(name+'.glb');p.write_bytes(raw);checks.append(validate_file(p,expected={'cameras':3},artifact_role='embedded_site_model'))
    return {'status':'passed','models':checks}

def require_fresh_receipts(out):
    browser=json.loads((out/'browser-report.json').read_text())
    if browser['status']!='passed' or browser['viewer_sha256']!=identity(out/'viewer.html')['sha256']:raise ValueError('Current exact viewer browser receipt required')
    report=json.loads((out/'roundtrip-report.json').read_text())
    required={stage+suffix for stage in ('existing','cleared','planted') for suffix in ('.json','.blend','.glb')}
    if report.get('status')!='passed' or set(report.get('source_sha256',{}))!=required:raise ValueError('Complete source-bound independent import receipt required')
    for n,sha in report['source_sha256'].items():
        if identity(out/n)['sha256']!=sha:raise ValueError('Stale source geometry import receipt: '+n)

def main():
    out=ROOT/'outputs/site-v4'
    require_fresh_receipts(out)
    p=json.loads((out/'planted.json').read_text());o=next(o for o in p['objects'] if o['id']=='new-N');prog=o['program'];floorplate=shape(o['footprint']).area
    account={'synthetic':True,'units':prog['units'],'default_apartment_paldalet_m2':prog['apartment_paldalet_m2'],'apartments_paldalet_total_m2':prog['apartments_paldalet_total_m2'],
             'separate_areas_m2':prog['separate_areas_m2'],'accounted_area_total_m2':prog['gross_total_m2'],
             'schematic_constant_floorplate_total_m2':floorplate*o['floors'],'unallocated_area_m2':floorplate*o['floors']-prog['gross_total_m2'],
             'geometric_apartment_area_measured':False,'statutory_gross_area_certified':False,
             'missing':'Detailed room, wall and legal area schedule. Unallocated area is not assigned by assumption.'}
    (out/'area-accounting.json').write_text(json.dumps(account,indent=2)+'\n')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18);sheet=Image.new('RGB',(1200,340),'white');draw=ImageDraw.Draw(sheet)
    for i,name in enumerate(('existing','cleared','planted')):
        with Image.open(out/(name+'.png')) as im:im.load();im.thumbnail((390,280));sheet.paste(im,(i*400,0))
        draw.text((i*400+10,285),name+' | SYNTHETIC',fill='#17392f',font=font)
    sheet.save(out/'contact-sheet.png')
    names=[n+'.'+ext for n in ('existing','cleared','planted') for ext in ('json','blend','glb','png')]
    names+=['placement-pending.json','plan-request.json','real-intake-requirements.json','geometry-report.json','roundtrip-report.json','browser-report.json','area-accounting.json','contact-sheet.png','viewer.html','offline-preview.png','live-approval-preview.png']
    if (ROOT/'reports/v4-regression.json').exists():
        regression=json.loads((ROOT/'reports/v4-regression.json').read_text())
        if not regression['successful'] or regression['failures'] or regression['errors'] or regression['skipped']:raise ValueError('Regression not fully passed')
        (out/'regression-report.json').write_bytes((ROOT/'reports/v4-regression.json').read_bytes());names.append('regression-report.json')
    before={n:identity(out/n) for n in names};files=[]
    for n in names:
        expected={**before[n]}
        if n.endswith(('.glb','.blend')):expected['cameras']=3
        evidence=validate_file(out/n,expected=expected,artifact_role='site_v4_output');files.append(evidence)
    embedded=embedded_models(out/'viewer.html')
    manifest={'version':1,'synthetic':True,'recipient_access':'not_verified','files':[{'name':n,**before[n],**({'cameras':3} if n.endswith(('.glb','.blend')) else {})} for n in names]}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');archive=out/'golden-city-site-v4.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for n in names+['manifest.json']:z.write(out/n,n)
    zipped=validate_archive(archive,expected_manifest=manifest)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(archive) as z:z.extractall(tmp)
        archive_models=embedded_models(Path(tmp)/'viewer.html')
    if any(identity(out/n)!=v for n,v in before.items()):raise ValueError('Source changed during packaging')
    receipt={'status':'passed','files':files,'source_hashes_unchanged':True,'embedded_models':embedded,'archive':zipped,
             'post_extract_embedded_models':archive_models,'recipient_access':'not_verified'}
    (out/'delivery-validation.json').write_text(json.dumps(receipt,indent=2)+'\n');validate_file(out/'delivery-validation.json')
    result={'status':'passed','publish_files':names+['manifest.json','golden-city-site-v4.zip','delivery-validation.json'],
            'archive':identity(archive),'viewer':identity(out/'viewer.html'),'source_hashes_unchanged':True,'recipient_access':'not_verified'}
    Path('/workspace/delivery-support/site-v4-package-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'passed','files':len(names),'archive_bytes':archive.stat().st_size,'source_hashes_unchanged':True}))

if __name__=='__main__':main()
