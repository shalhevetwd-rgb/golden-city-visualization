"""Independent image/model delivery checks and visible targeted-edit evidence."""
import json
import os
import hashlib
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'engine')]
from constraints import compare_snapshots
from delivery_validation import validate_file
from render_context import load_context


def create_contact_sheet(out):
    """Compose the four existing render PNGs without changing source bytes."""
    out=Path(out)
    names=('aerial','street','architectural','edited-aerial')
    labels=('AERIAL | preserved source volumes','STREET | source-ground eye height +1.7 m',
            'ARCHITECTURAL | additive facade detail','EDITED AERIAL | building A finish option')
    hashes={name+'.png':hashlib.sha256((out/(name+'.png')).read_bytes()).hexdigest() for name in names}
    margin,gap,width,height,title_height,label_height=20,20,800,500,64,30
    sheet=Image.new('RGB',(2*width+2*margin+gap,2*(height+label_height)+title_height+2*margin+gap),'#f2f0e8')
    draw=ImageDraw.Draw(sheet)
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
        titlefont=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',23)
    except OSError:font=ImageFont.load_default();titlefont=font
    draw.text((margin,16),'GOLDEN CITY v3 | SYNTHETIC DEVELOPMENT EXPERIMENT',fill='#24343e',font=titlefont)
    draw.text((margin,44),'Existing geometry preserved. Facade/planting/people are synthetic proposals. No marketing photorealism approval.',fill='#45545d',font=font)
    for index,(name,label) in enumerate(zip(names,labels)):
        with Image.open(out/(name+'.png')) as source:image=source.convert('RGB')
        image.thumbnail((width,height),Image.Resampling.LANCZOS)
        x=margin+(index%2)*(width+gap);y=title_height+margin+(index//2)*(height+label_height+gap)
        draw.text((x,y),label,fill='#24343e',font=font);sheet.paste(image,(x,y+label_height))
    sheet.save(out/'contact-sheet.png')
    receipt=validate_file(out/'contact-sheet.png',expected={'width':sheet.width,'height':sheet.height},artifact_role='synthetic architectural contact sheet')
    if any(hashlib.sha256((out/name).read_bytes()).hexdigest()!=digest for name,digest in hashes.items()):
        raise ValueError('A source render changed while composing the contact sheet')
    report={'receipt':receipt,'source_png_sha256':hashes,'composition_only':True,'source_images_modified':False}
    (out/'contact-sheet-validation.json').write_text(json.dumps(report,indent=2))
    return report


def check():
    cfg,out=load_context()
    geometry=json.loads((out/'geometry-report.json').read_text())
    assert geometry['synthetic'] is True and geometry['source_meshes_preserved']==15 and not geometry['source_geometry_changed']
    assert {name:data['source_window_rows'] for name,data in geometry['buildings'].items()}=={'A':10,'B':8,'C':6}
    mode='draft' if os.environ.get('GOLDEN_CITY_RENDER_MODE')=='draft' else 'final'
    preset=cfg['render'][mode]
    expected_image={'width':preset['width'],'height':preset['height']}
    images={}
    for name in ('aerial','street','architectural','edited-aerial'):
        images[name]=validate_file(out/(name+'.png'),expected=expected_image,artifact_role='architectural render')
    first=np.asarray(Image.open(out/'aerial.png').convert('RGB'),dtype=np.int16)
    second=np.asarray(Image.open(out/'edited-aerial.png').convert('RGB'),dtype=np.int16)
    assert first.shape==second.shape
    delta=np.abs(first-second)
    significant=np.max(delta,axis=2)>16
    assert int(significant.sum())>first.shape[0]*first.shape[1]*.005,'The selected facade edit is not visibly represented in rendered pixels'
    snapshots=json.loads((out/'scene-snapshots.json').read_text())
    edit=json.loads((out/'edit-report.json').read_text())
    recomputed=compare_snapshots(snapshots['before'],snapshots['after'],cfg['material_edit_test']['target'])
    assert recomputed['passed'] and recomputed['changed_objects'],recomputed
    for key,value in recomputed.items():assert edit[key]==value,(key,edit.get(key),value)
    expect=json.loads((out/'model-expectations.json').read_text());models={}
    for name in ('model.blend','model.glb','edited-model.blend','edited-model.glb'):
        models[name]=validate_file(out/name,expected=expect[name],artifact_role='architectural model')
    # The proof compares scene geometry/material state, not global beauty pixels;
    # reflected light and sampling can change outside a recoloured facade.
    width,height=first.shape[1],first.shape[0]
    sheet=Image.new('RGB',(width*2,height+48),'#f4f1e9');draw=ImageDraw.Draw(sheet)
    draw.text((16,15),'BEFORE | synthetic architectural concept',fill='#21313b')
    draw.text((width+16,15),'AFTER | building A facade finish only',fill='#21313b')
    sheet.paste(Image.open(out/'aerial.png').convert('RGB'),(0,48))
    sheet.paste(Image.open(out/'edited-aerial.png').convert('RGB'),(width,48))
    sheet.save(out/'before-after.png');validate_file(out/'before-after.png',artifact_role='architectural edit comparison')
    contact=create_contact_sheet(out)
    report={'synthetic':True,'passed':True,'render_mode':mode,'expected_resolution':expected_image,'images':images,'models':models,'contact_sheet':contact,'visible_changed_pixels_above_16_rgb':int(significant.sum()),'recomputed_targeted_edit':recomputed,'source_core_meshes_preserved':15,'original_window_rows_preserved':{'A':10,'B':8,'C':6},'professional_photorealism_claim':False,'manual_architectural_review_required':True,'limitations':geometry['limitations']}
    (out/'output-checks.json').write_text(json.dumps(report,indent=2));validate_file(out/'output-checks.json',artifact_role='architectural delivery QA')
    print(json.dumps({'passed':True,'image_files':list(images),'model_files':list(models),'visible_changed_pixels':int(significant.sum()),'changed_objects':recomputed['changed_objects']},indent=2))
    return report


if __name__=='__main__':check()
