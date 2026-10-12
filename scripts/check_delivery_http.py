"""Verify served delivery bytes; never claim recipient accessibility.

Usage: .venv/bin/python scripts/check_delivery_http.py --url http://127.0.0.1:8000
Actual HTTP bodies are saved and reopened; the ZIP is extracted again. This only
checks this environment's service, not a user's Codex chat or browser download.
"""
import argparse,hashlib,json
from pathlib import Path
import sys
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from delivery_validation import validate_file,validate_links

parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000')
args=parser.parse_args();out=ROOT/'data'/'delivery-http-check';out.mkdir(parents=True,exist_ok=True)
results=[]
for name in ['golden-city-gallery.html','contact-sheet.png','delivery-validation.json','golden-city-materials.zip']:
    route='/downloads/'+name
    with urlopen(args.url+route,timeout=240) as response:
        payload=response.read();headers=response.headers
        assert response.status==200
        assert headers['X-Golden-City-File-Validation']=='passed'
        assert headers['X-Golden-City-Recipient-Access']=='not-verified'
        assert hashlib.sha256(payload).hexdigest()==headers['X-Golden-City-SHA256']
        if not name.endswith('.html'):assert name in headers['Content-Disposition']
    original=ROOT/'deliverables'/name
    assert payload==original.read_bytes()
    saved=out/name;saved.write_bytes(payload)
    # Independent consumers reopen the actual HTTP body, including ZIP members.
    evidence=validate_file(saved,expected={'sha256':hashlib.sha256(payload).hexdigest()})
    results.append({'route':route,'status':200,'bytes':len(payload),'sha256':evidence['sha256'],'body_reopened':evidence['status'],'user_accessibility':'not_verified'})
with urlopen(args.url+'/api/projects') as response:projects=json.load(response)
project=next(p for p in projects if p['approved'] and not p['errors'])
for route,name in [('/api/report','project-report.json'),('/api/export','model.obj')]:
    req=Request(args.url+route,data=json.dumps({'id':project['id']}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=120) as response:
        payload=response.read()
        assert response.headers['X-Golden-City-File-Validation']=='passed'
        assert hashlib.sha256(payload).hexdigest()==response.headers['X-Golden-City-SHA256']
    saved=out/name;saved.write_bytes(payload)
    evidence=validate_file(saved)
    results.append({'route':route,'bytes':len(payload),'body_reopened':evidence['status'],'user_accessibility':'not_verified'})
report={'status':'passed','local_http_downloads':'passed','recipient_accessibility':'not_verified','checks':results}
(ROOT/'deliverables'/'http-delivery-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
