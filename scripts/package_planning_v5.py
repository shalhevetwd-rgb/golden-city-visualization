"""Fail-closed delivery of existing model bytes and the new mandatory brief form.

No modelling/rendering occurs. Independently reopen every ZIP member, decode the
embedded historical GLBs, and import them in a separate Blender process. All
source-bound receipts and hashes are checked again after packaging.
"""
import argparse,base64,hashlib,json,re,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from delivery_guard import verify_for_delivery
from delivery_validation import validate_archive
OUT=ROOT/'outputs/planning-v5'
FILES=('README.he.md','planning-questionnaire.html','site-viewer.html','dossier-questionnaire-preview.png','planning-review-preview.png','historical-model-viewer-preview.png','site-approval-preview.png','browser-report.json','site-browser-report.json','preservation-report.json','regression-report.json','acceptance-status.json')

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def current_receipts():
 for report,file,key in [('browser-report.json','planning-questionnaire.html','source_hashes'),('site-browser-report.json','site-viewer.html','viewer_sha256')]:
  r=json.loads((OUT/report).read_text())
  if r.get('status')!='passed':raise ValueError('Browser evidence not passed: '+report)
  expected=r[key][file] if key=='source_hashes' else r[key]
  if digest(OUT/file)!=expected:raise ValueError('Browser receipt stale: '+file)
 regression=json.loads((OUT/'regression-report.json').read_text())
 if not regression['successful'] or regression['failures'] or regression['errors'] or regression['skipped'] or regression['passed']!=regression['tests_run']:raise ValueError('Regression did not fully pass')
 proof=json.loads((ROOT/'outputs/site-v4/roundtrip-report.json').read_text())
 if proof.get('status')!='passed' or len(proof.get('source_sha256',{}))!=9:raise ValueError('Complete source-model geometry proof missing')
 for name,sha in proof['source_sha256'].items():
  if digest(ROOT/'outputs/site-v4'/name)!=sha:raise ValueError('Historical native source changed: '+name)
 return proof

def embedded_models(html):
 text=Path(html).read_text();match=re.search(r'<script id="sitePayload" type="application/json">(.*?)</script>',text,re.S)
 if not match:raise ValueError('Embedded model payload missing')
 payload=json.loads(match[1]);reports={}
 with tempfile.TemporaryDirectory(prefix='golden-planning-delivery-models-') as folder:
  for stage in ('existing','cleared','planted'):
   raw=base64.b64decode(payload['models'][stage],validate=True);source=ROOT/'outputs/site-v4'/(stage+'.glb')
   if raw!=source.read_bytes():raise ValueError('Viewer changed historical model: '+stage)
   if payload['scenes'][stage]!=json.loads((ROOT/'outputs/site-v4'/(stage+'.json')).read_text()):raise ValueError('Viewer source scene mismatch')
   path=Path(folder)/(stage+'.glb');path.write_bytes(raw)
   reports[stage]=verify_for_delivery(path,expected={'sha256':digest(source),'cameras':3,'objects':len(payload['scenes'][stage]['objects'])+1,'object_names':[o['id'] for o in payload['scenes'][stage]['objects']]})
   reports[stage].pop('path',None)
 return reports

def verify_archive(archive):
 report=validate_archive(archive,expected_manifest=OUT/'manifest.json')
 with tempfile.TemporaryDirectory(prefix='golden-planning-post-package-') as folder:
  with zipfile.ZipFile(archive) as z:z.extractall(folder)  # safe paths already checked by shared gate
  embedded=embedded_models(Path(folder)/'site-viewer.html')
 return {'status':'passed','archive':report,'embedded_models_after_extraction':embedded}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args();proof=current_receipts()
 if args.verify_only:
  result=verify_archive(OUT/'planning-and-status-v5.zip');print(json.dumps({'status':result['status'],'archive':(OUT/'planning-and-status-v5.zip').stat().st_size}));return
 hashes={name:digest(OUT/name) for name in FILES};checks={name:verify_for_delivery(OUT/name,expected={'sha256':hashes[name]}) for name in FILES}
 native=embedded_models(OUT/'site-viewer.html')
 manifest={'version':1,'files':[{'name':name,'bytes':(OUT/name).stat().st_size,'sha256':hashes[name]} for name in FILES]};(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 archive=OUT/'planning-and-status-v5.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for name in (*FILES,'manifest.json'):z.write(OUT/name,name)
 packed=verify_archive(archive);current_receipts()
 if any(digest(OUT/name)!=sha for name,sha in hashes.items()):raise ValueError('Source changed during packaging')
 result={'status':'passed','files':checks,'embedded_native_reimports':native,'prior_source_bound_geometry_proof_sha256':digest(ROOT/'outputs/site-v4/roundtrip-report.json'),'after_packaging':packed,'recipient_access':'not_verified','publish_files':[*FILES,'manifest.json',archive.name,'delivery-report.json']}
 (OUT/'delivery-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));verify_for_delivery(OUT/'delivery-report.json');print(json.dumps({'status':'passed','publish_files':len(result['publish_files']),'zip_bytes':archive.stat().st_size,'new_rendering':False}))
if __name__=='__main__':main()
