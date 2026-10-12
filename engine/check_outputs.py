from pathlib import Path
import json,hashlib
from PIL import Image
from intake import inspect
ROOT=Path(__file__).resolve().parents[1]
def check():
 o=ROOT/'outputs/v2';report={'synthetic':True,'images':[],'errors':[]}
 for name in ['aerial','street','architectural','edited-aerial']:
  p=o/(name+'.png')
  if not p.exists():report['errors'].append('Missing '+p.name);continue
  x=inspect(p)
  with Image.open(p) as im:
   assert im.size==(1600,1000);assert min(im.convert('RGB').getextrema()[0])!=max(im.convert('RGB').getextrema()[0])
  report['images'].append(x)
 for name in ['model.glb','edited-model.glb']:report[name]=inspect(o/name)
 geo=json.loads((o/'geometry-report.json').read_text());edit=json.loads((o/'edit-report.json').read_text());assert not geo['source_geometry_changed'];assert edit['passed'];assert edit['changed_objects']==['building:A']
 report['geometric_edit_passed']=True;report['architectural_quality_requires_manual_review']=True;(o/'output-checks.json').write_text(json.dumps(report,indent=2));return report
if __name__=='__main__':print(json.dumps(check(),indent=2))
