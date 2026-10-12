"""Reproduce the synthetic acceptance renders and per-object unchanged proof.

Run .venv/bin/python render_acceptance.py. It reuses the existing synthetic A
footprint verbatim and adds explicitly synthetic B/C inside the same boundary.
"""
import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from rendering import render_project
from delivery_validation import validate_file

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'examples'/'renders'


def rectangle(x,y,width,depth):
    return {'type':'Polygon','coordinates':[[[x,y],[x+width,y],[x+width,y+depth],[x,y+depth],[x,y]]]}


def fixture():
    source=json.loads((ROOT/'examples'/'synthetic-project.geojson').read_text())
    p=copy.deepcopy(source)
    p.update(id='synthetic-acceptance',name='Synthetic acceptance: existing A with distinct B and C',errors=[],approved=True,version=1,synthetic=True)
    p['buildings'][0]['properties'].update(color='#d9bd89',material='stone')
    p['buildings'].extend([
        {'type':'Feature','geometry':rectangle(220046,630046,18,22),'properties':{'id':'B','height':24,'floors':8,'color':'#bbc4b6','material':'plaster'}},
        {'type':'Feature','geometry':rectangle(220073,630030,17,17),'properties':{'id':'C','height':18,'floors':6,'color':'#e7d4b7','material':'stone'}},
    ])
    assert p['buildings'][0]['geometry']==source['buildings'][0]['geometry']
    return p


def run():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    baseline=fixture()
    edited=copy.deepcopy(baseline)
    edited['version']=2
    a=edited['buildings'][0]['properties']
    a.update(color='#b94e3b',material='brick',balconies=[{'id':f'south-{z}','footprint':rectangle(220014,630007,12,3),'elevation':z,'thickness':.25,'railing_height':1.05} for z in (6,12,18)])
    (OUTPUT/'baseline-project.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    (OUTPUT/'edited-project.json').write_text(json.dumps(edited,ensure_ascii=False,indent=2))
    validate_file(OUTPUT/'baseline-project.json',artifact_role='synthetic model source')
    validate_file(OUTPUT/'edited-project.json',artifact_role='synthetic model source')
    aerial=render_project(baseline,'aerial',OUTPUT/'aerial.png',width=960,height=720,samples=48,timeout=180,export_models=True)
    street=render_project(baseline,'street',OUTPUT/'street.png',width=1280,height=720,samples=48,timeout=180,export_models=True)
    change=render_project(edited,aerial['camera'],OUTPUT/'edited-aerial.png',width=960,height=720,samples=48,timeout=180,export_models=True)
    original=np.asarray(Image.open(aerial['object_mask_path']).convert('RGB'))
    changed=np.asarray(Image.open(change['object_mask_path']).convert('RGB'))
    per_object={}
    for identifier in ('A','B','C'):
        color=np.array(aerial['object_mask_labels'][identifier],dtype=np.uint8)
        old=np.all(original==color,axis=2);new=np.all(changed==color,axis=2)
        fingerprints_unchanged=aerial['building_fingerprints'][identifier]==change['building_fingerprints'][identifier]
        per_object[identifier]={'fingerprints_unchanged':fingerprints_unchanged,'geometry_hash_unchanged':aerial['building_fingerprints'][identifier]['geometry_hash']==change['building_fingerprints'][identifier]['geometry_hash'],'material_hash_unchanged':aerial['building_fingerprints'][identifier]['material_hash']==change['building_fingerprints'][identifier]['material_hash'],'visible_pixels_before':int(old.sum()),'visible_pixels_after':int(new.sum()),'visible_mask_changed_pixels':int(np.count_nonzero(old!=new)),'visible_mask_identical':bool(np.array_equal(old,new))}
    assert all(per_object[i]['fingerprints_unchanged'] and per_object[i]['visible_mask_identical'] and per_object[i]['visible_pixels_before']>100 for i in ('B','C')), per_object
    assert not per_object['A']['fingerprints_unchanged']
    original_png=np.asarray(Image.open(aerial['output_path']).convert('RGB'))
    edited_png=np.asarray(Image.open(change['output_path']).convert('RGB'))
    changed_pixels=np.any(original_png!=edited_png,axis=2)
    assert int(changed_pixels.sum())>1000
    # Compact evidence sheet consists of the actual PNG renders, a difference
    # panel and the unchanged B/C mask; no scene content is fabricated here.
    sheet=Image.new('RGB',(1920,1520),'#f4f4f0')
    draw=ImageDraw.Draw(sheet)
    for x,label,file in ((0,'BASELINE AERIAL | A, B, C','aerial.png'),(960,'EDITED A ONLY | brick, color, balconies','edited-aerial.png')):
        draw.text((x+18,12),label,fill='#172d35')
        sheet.paste(Image.open(OUTPUT/file).convert('RGB'),(x,40))
    difference=np.zeros_like(original_png);difference[changed_pixels]=[220,60,40]
    proof=np.full_like(original,245)
    for identifier,color in (('B',[45,110,180]),('C',[35,145,80])):
        mask=np.all(original==np.array(aerial['object_mask_labels'][identifier]),axis=2)
        proof[mask]=color
    draw.text((18,772),'PIXEL DIFFERENCE | includes lighting/shadow changes',fill='#172d35')
    draw.text((978,772),'UNCHANGED B (blue), C (green) | object masks identical',fill='#172d35')
    sheet.paste(Image.fromarray(difference),(0,800));sheet.paste(Image.fromarray(proof),(960,800))
    sheet.save(OUTPUT/'comparison.png')
    comparison_verification=validate_file(OUTPUT/'comparison.png',expected={'width':1920,'height':1520},artifact_role='render comparison')
    report={'passed':True,'synthetic':True,'source':'examples/synthetic-project.geojson','existing_A_footprint_reused_verbatim':True,'renders':['aerial.png','street.png','edited-aerial.png'],'proof_image':'comparison.png','comparison_verification':comparison_verification,'model_exports':['aerial.obj','aerial.mtl','aerial.glb','street.obj','street.mtl','street.glb','edited-aerial.obj','edited-aerial.mtl','edited-aerial.glb'],'render_delivery_status':{name:{file:proof['status'] for file,proof in result['delivery_checks'].items()} for name,result in [('aerial',aerial),('street',street),('edited',change)]},'object_checks':per_object,'changed_render_pixels':int(changed_pixels.sum()),'camera_equal':aerial['camera']==change['camera'],'limitations':['Unchanged B/C proof covers exact stored geometry/material hashes and identical visible object masks.','Full beauty-render pixels may differ on neighboring surfaces because an edit changes shadows and light transport.','Flat synthetic terrain, no cadastral, engineering or sukkah suitability certification.'],'engine':aerial['engine'],'samples':48,'durations_seconds':{key:value['duration_seconds'] for key,value in (('aerial',aerial),('street',street),('edited',change))}}
    (OUTPUT/'acceptance-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    validate_file(OUTPUT/'acceptance-report.json',artifact_role='acceptance report')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':run()
