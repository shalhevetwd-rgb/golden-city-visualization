"""Immutable native-scene editing for the explicitly synthetic v3 showcase."""
import hashlib
import json
import math
import re
import subprocess
import uuid
from pathlib import Path
from delivery_guard import verify_for_delivery


def edit(request, root, data):
    root, data = Path(root), Path(data)
    if request.get('operation') != 'facade_color' or request.get('building_id') not in {'A','B','C'}:
        raise ValueError('בניסוי האדריכלי נתמכת עריכת צבע חזית של מבנה A, B או C בלבד')
    color = request.get('color')
    if not isinstance(color,list) or len(color)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in color):
        raise ValueError('נדרשים שלושה ערכי צבע לינאריים בין 0 ל־1')
    camera = request.get('camera','aerial')
    if camera not in {'aerial','street','architectural'}:
        raise ValueError('מצלמה שמורה לא מוכרת')
    settings = {}
    for key, default, minimum, maximum in [('width',800,64,1600),('height',500,64,1000),('samples',32,1,160)]:
        value=request.get(key,default)
        if type(value) is not int or not minimum<=value<=maximum:
            raise ValueError('הגדרת רינדור לא תקינה: '+key)
        settings[key]=value
    digest=request.get('source_sha256')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('נדרשת חתימת גרסת מקור שמורה')
    candidates=[root/'outputs/v3/model.blend',root/'outputs/v3/edited-model.blend']
    versions=data/'architectural-edits'
    if versions.exists():
        for record in versions.glob('*/version.json'):
            metadata=json.loads(record.read_text())
            if metadata.get('blend_sha256')==digest:
                candidates.append(record.parent/'edited.blend')
    source=next((p for p in candidates if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest),None)
    if source is None:
        raise ValueError('גרסת המקור אינה קיימת או השתנתה; יש לפתוח את הגרסה העדכנית')
    version=uuid.uuid4().hex
    directory=versions/version; directory.mkdir(parents=True,exist_ok=False)
    edit_request={k:request[k] for k in ('building_id','operation','color','source_sha256')}
    request_path=directory/'request.json'
    request_path.write_text(json.dumps(edit_request,allow_nan=False))
    commands=[['blender','--background','--factory-startup','--disable-autoexec','--threads','8','--python-exit-code','1','--python',str(root/'engine/edit_model.py'),'--',str(source),str(request_path),str(directory)],
              ['blender','--background','--factory-startup','--disable-autoexec','--threads','8','--python-exit-code','1','--python',str(root/'engine/native_edit_render.py'),'--',str(directory/'edited.blend'),str(directory/'image.png'),camera,json.dumps(settings)]]
    for index,command in enumerate(commands):
        try:
            result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=600)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError('הרינדור חרג ממגבלת הזמן; המקור נשמר והגרסה לא נמסרה') from error
        (directory/f'worker-{index}.log').write_text(result.stdout+'\n'+result.stderr)
        if result.returncode:
            raise RuntimeError('עריכת הסצנה או הרינדור נכשלו. הגרסה המקורית נשמרה; פרטי התקלה ביומן המקומי')
    checks={name:verify_for_delivery(directory/name) for name in ('image.png','image.render.json','edited.blend','edited.glb','edit-check.json')}
    evidence=json.loads((directory/'edit-check.json').read_text())
    if evidence.get('passed') is not True or evidence.get('material_export_verified') is not True:
        raise RuntimeError('בדיקת השינוי הנקודתי או התאמת חומרי GLB למודל נכשלה')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
        raise RuntimeError('קובץ המקור השתנה בזמן העבודה')
    base=f'/architectural-edits/{version}/'
    record={'version_id':version,'source_sha256':digest,'building_id':request['building_id'],
            'operation':'facade_color','color':color,'camera':camera,'render_settings':settings,
            'synthetic':True,'classification':'synthetic_architectural_experiment',
            'geometry_preserved':not evidence.get('geometry_changed'),
            'material_export_verified':evidence['material_export_verified'],
            'portable_material_node_count':evidence.get('portable_material_node_count'),
            'outside_target_changed':evidence.get('outside_target_changed',[]),
            'unchanged_scene_objects':evidence.get('unchanged_scene_objects',[]),
            'pixel_identity_claim':False,'accurate_real_site':False,
            'image_url':base+'image.png','model_url':base+'edited.glb',
            'blend_url':base+'edited.blend','edit_report_url':base+'edit-check.json',
            'render_report_url':base+'image.render.json',
            'blend_sha256':checks['edited.blend']['sha256'],
            'model_sha256':checks['edited.glb']['sha256'],
            'checks':{name:{'status':r['status'],'sha256':r['sha256']} for name,r in checks.items()},
            'recipient_access':'not_verified'}
    (directory/'version.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False))
    verify_for_delivery(directory/'version.json')
    return record


def catalogue(data):
    """Expose completed immutable versions; altered/missing bytes are rejected."""
    records=[]
    root=Path(data)/'architectural-edits'
    if not root.exists():return records
    for path in sorted(root.glob('*/version.json'),key=lambda p:p.stat().st_mtime):
        record=json.loads(path.read_text())
        if record.get('material_export_verified') is not True:
            raise ValueError('חומרי GLB של הגרסה טרם אומתו')
        if record.get('version_id')!=path.parent.name or not re.fullmatch('[0-9a-f]{32}',path.parent.name):
            raise ValueError('רשומת גרסה אינה תואמת לתיקייה השמורה')
        for name in ('image.png','image.render.json','edited.blend','edited.glb','edit-check.json'):
            expected=record.get('checks',{}).get(name,{}).get('sha256')
            if not expected:raise ValueError('חסרה חתימת תוצר בגרסה')
            verify_for_delivery(path.parent/name,expected={'sha256':expected})
        records.append(record)
    return records
