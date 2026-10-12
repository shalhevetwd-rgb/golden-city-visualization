"""Persistent immutable snapshots and isolated appearance edits."""
import copy, hashlib, json, math, re
from pathlib import Path
from threading import RLock
from shapely.geometry import shape
from spatial import polygon_geometry

LOCK = RLock()
MATERIALS = {'plaster','stone','brick','glass'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def snapshot(project):
    return copy.deepcopy({k:v for k,v in project.items() if k not in {'history','renders'}})

def commit(previous, changed, action):
    changed['history'] = copy.deepcopy(previous.get('history',[]))
    changed['history'].append({'version':previous['version'],'action':action,'snapshot':snapshot(previous),'buildings':copy.deepcopy(previous['buildings'])})
    changed['version'] = previous['version']+1
    changed['last_action'] = action
    return changed

def check_camera(camera):
    for field in ('position','target'):
        values=camera[field]
        if len(values)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e6 for v in values):
            raise ValueError('מצלמה דורשת שלוש קואורדינטות סופיות מקומיות')
    if sum((a-b)**2 for a,b in zip(camera['position'],camera['target'])) < 1e-8:
        raise ValueError('מיקום המצלמה ויעד המבט זהים')
    fov=camera.get('fov',50)
    if type(fov) not in (int,float) or not math.isfinite(fov) or not 10<=fov<=120:
        raise ValueError('זווית ראייה לא תקינה')
    return {'position':list(camera['position']),'target':list(camera['target']),'fov':fov}

def validate_balconies(items, boundary):
    if not isinstance(items,list) or len(items)>100: raise ValueError('רשימת מרפסות לא תקינה')
    ids=set()
    for balcony in items:
        ident=balcony['id']
        if not isinstance(ident,str) or not ident or ident in ids: raise ValueError('מזהה מרפסת כפול או ריק')
        ids.add(ident)
        g=polygon_geometry(balcony['footprint'],'balcony')
        if g.geom_type!='Polygon' or not g.is_valid or g.is_empty or g.interiors:
            raise ValueError('פוליגון מרפסת לא תקין')
        if not boundary.covers(g): raise ValueError('מרפסת חורגת מגבול העבודה')
        for key, default, lower, upper in [('elevation',None,0,500),('thickness',0.25,0.05,2),('railing_height',1.05,0,3)]:
            value=balcony.get(key,default)
            if type(value) not in (int,float) or not math.isfinite(value) or not lower<=value<=upper:
                raise ValueError('מידת מרפסת לא תקינה: '+key)
    return copy.deepcopy(items)

def edit_building(project, request):
    result=copy.deepcopy(project)
    matches=[b for b in result['buildings'] if b['properties']['id']==request['building']]
    if not matches: raise ValueError('מבנה לא נמצא')
    properties=matches[0]['properties']
    if 'color' in request:
        if not isinstance(request['color'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',request['color']): raise ValueError('צבע לא תקין')
        properties['color']=request['color']
    if 'material' in request:
        if request['material'] not in MATERIALS: raise ValueError('חומר לא מוכר')
        properties['material']=request['material']
    if 'balconies' in request:
        properties['balconies']=validate_balconies(request['balconies'],shape(project['boundary']))
    unaffected={b['properties']['id']:digest(b) for b in project['buildings'] if b['properties']['id']!=request['building']}
    after={b['properties']['id']:digest(b) for b in result['buildings'] if b['properties']['id']!=request['building']}
    if unaffected!=after: raise RuntimeError('שינוי לא מורשה באובייקט אחר')
    result['last_diff']={'edited_building':request['building'],'unchanged_buildings':unaffected,'geometry_preserved':matches[0]['geometry']==next(b['geometry'] for b in project['buildings'] if b['properties']['id']==request['building'])}
    return commit(project,result,'edit:'+request['building'])

def restore(project, version):
    record=next((h for h in project.get('history',[]) if h['version']==version),None)
    if not record: raise ValueError('גרסה לא נמצאה')
    if 'snapshot' not in record: raise ValueError('גרסה ישנה שומרת רק מבנים ואינה ניתנת לשחזור מלא')
    result=copy.deepcopy(project)
    for key in set(result)-{'history','renders'}: result.pop(key)
    result.update(copy.deepcopy(record['snapshot']))
    return commit(project,result,'restore:'+str(version))

def save_atomic(path, project):
    path=Path(path)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(project,ensure_ascii=False,allow_nan=False))
    temporary.replace(path)
