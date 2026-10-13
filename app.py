import base64, copy, hashlib, io, json, math, mimetypes, os, re, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import CRS, Transformer
from state import LOCK, MATERIALS, commit, check_camera, edit_building, restore, save_atomic, validate_balconies
from spatial import polygon_geometry, projected_metric_crs
from PIL import Image, UnidentifiedImageError
from urllib.parse import quote
from delivery_guard import verify_for_delivery
from delivery_validation import ValidationError
from workflow import dossier, intake as inspect_source, readiness, record_review, ensure_geometry_record, capabilities

ROOT = Path(__file__).parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
OUTPUT = DATA / 'renders'
OUTPUT.mkdir(exist_ok=True)
ASSETS = DATA / 'assets'
ASSETS.mkdir(exist_ok=True)
EXPORTS = DATA / 'exports'
EXPORTS.mkdir(exist_ok=True)
DELIVERABLES = ROOT / 'deliverables'

def validate_slabs(records):
    if not isinstance(records,list): raise ValueError('נדרשת רשימת בליטות מפורשת')
    ids=set()
    for record in records:
        if not isinstance(record,dict) or not isinstance(record.get('id'),str) or not record['id'] or record['id'] in ids:
            raise ValueError('מזהה בליטה לא תקין')
        ids.add(record['id'])
        polygon_geometry(record.get('footprint'),'obstruction')
        for key,default in [('elevation',None),('thickness',.2)]:
            v=record.get(key,default)
            if type(v) not in (int,float) or not math.isfinite(v) or (key=='thickness' and v<=0):
                raise ValueError('גובה או עובי בליטה לא תקין')
    return copy.deepcopy(records)

def validate(document):
    if type(document.get('synthetic',False)) is not bool:
        raise ValueError('סימון דוגמה סינתטית חייב להיות מפורש')
    crs = projected_metric_crs(document['crs'])
    boundary = polygon_geometry(document['boundary'],'boundary')
    if boundary.geom_type != 'Polygon' or not boundary.is_valid or boundary.is_empty:
        raise ValueError('גבול המתחם חייב להיות פוליגון תקין')
    if any(not math.isfinite(v) for p in boundary.exterior.coords for v in p):
        raise ValueError('קואורדינטות הגבול חייבות להיות סופיות')
    buildings, errors, ids = [], [], set()
    for feature in document.get('buildings', []):
        props = feature['properties']
        identifier = str(props['id'])
        if not identifier or props['id'] is None:
            raise ValueError('נדרש מזהה מבנה קבוע')
        if identifier in ids:
            raise ValueError('מזהי מבנים חייבים להיות ייחודיים')
        ids.add(identifier)
        geometry = polygon_geometry(feature['geometry'],'building')
        height, floors = float(props['height']), props['floors']
        if not math.isfinite(height) or height <= 0 or type(floors) is not int or floors <= 0:
            raise ValueError('גובה וקומות חייבים להיות חיוביים')
        if geometry.geom_type != 'Polygon' or not geometry.is_valid or geometry.is_empty or geometry.interiors:
            raise ValueError('נדרש פוליגון מבנה תקין ללא חצר פנימית בגרסה זו')
        if any(not math.isfinite(v) for p in geometry.exterior.coords for v in p):
            raise ValueError('קואורדינטות המבנה חייבות להיות סופיות')
        validate_balconies(props.get('balconies',[]), boundary)
        validate_slabs(props.get('slabs',[]))
        if props.get('material','stone') not in MATERIALS:
            raise ValueError('חומר חזית לא מוכר')
        if not isinstance(props.get('color','#d9bd89'),str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',props.get('color','#d9bd89')):
            raise ValueError('צבע חזית לא תקין')
        if type(props.get('base_elevation',0)) not in (int,float) or not math.isfinite(props.get('base_elevation',0)):
            raise ValueError('גובה בסיס לא תקין')
        if not boundary.covers(geometry):
            errors.append(f'מבנה {identifier} חורג מהגבול')
        for previous in buildings:
            if geometry.intersection(shape(previous['geometry'])).area > 0.001:
                errors.append(f'מבנה {identifier} חופף למבנה {previous["properties"]["id"]}')
        buildings.append({'type':'Feature','geometry':mapping(geometry),'properties':{**props,'id':identifier,'height':height}})
    if not buildings:
        errors.append('לא נקלטו מבנים')
    return json.loads(json.dumps({'name':str(document.get('name','מתחם חדש'))[:120], 'crs':crs.to_string(), 'boundary':mapping(boundary), 'source_boundary':copy.deepcopy(document['boundary']), 'buildings':buildings, 'overhead_obstructions':validate_slabs(document.get('overhead_obstructions',[])), 'synthetic':document.get('synthetic',False), 'errors':errors, 'approved':False, 'version':1, 'topography':'unknown', 'cameras':{}, 'assets':[], 'renders':[]},allow_nan=False))

def model_obj(project):
    if not project['approved'] or project['errors']:
        raise ValueError('יש לאשר גבולות והעמדה תקינה לפני יצוא מודל')
    lines = ['# GOLDEN CITY schematic model; terrain heights unknown; local XYZ Z up']
    origin = shape(project['boundary']).centroid
    index = 1
    for feature in project['buildings']:
        ring = list(shape(feature['geometry']).exterior.coords)[:-1]
        n = len(ring)
        lines.append('o building_' + quote(feature['properties']['id'],safe=''))
        for z in (0,feature['properties']['height']):
            for x,y,*_ in ring:
                lines.append(f'v {x-origin.x:.4f} {y-origin.y:.4f} {z+feature["properties"].get("base_elevation",0):.4f}')
        lines.append('f ' + ' '.join(str(index+n+i) for i in range(n)))
        lines.append('f ' + ' '.join(str(index+i) for i in reversed(range(n))))
        for i in range(n):
            j=(i+1)%n
            lines.append(f'f {index+i} {index+j} {index+n+j} {index+n+i}')
        index += 2*n
    return '\n'.join(lines)

def ingest_asset(project, request):
    kind=request['kind']
    if kind not in {'aerial_photo','site_photo','model'}: raise ValueError('סוג מקור לא נתמך')
    content=base64.b64decode(request['content_base64'],validate=True)
    if not content or len(content)>10_000_000: raise ValueError('קובץ גדול או ריק')
    if kind=='model':
        text=content.decode('utf-8')
        vertices=[line.split()[1:] for line in text.splitlines() if line.startswith('v ')]
        faces=[line for line in text.splitlines() if line.startswith('f ')]
        if not vertices or not faces or len(vertices)>100000: raise ValueError('נדרש OBJ בעל קודקודים ופאות')
        if any(len(v)!=3 or any(not math.isfinite(float(x)) for x in v) for v in vertices): raise ValueError('קודקודי OBJ לא תקינים')
        for line in faces:
            values=line.split()[1:]
            if len(values)<3 or any(not 1<=abs(int(x.split('/')[0]))<=len(vertices) for x in values): raise ValueError('אינדקסי OBJ לא תקינים')
        ext,mime='.obj','text/plain'
    else:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {'PNG','JPEG'}: raise ValueError('תמונות PNG ו־JPEG בלבד')
            ext,mime=('.png','image/png') if image.format=='PNG' else ('.jpg','image/jpeg')
            image.verify()
    placement=request.get('placement')
    if placement:
        bounds=placement['bounds']
        if len(bounds)!=4 or any(not math.isfinite(float(v)) for v in bounds) or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]: raise ValueError('גבולות מיקום תמונה לא תקינים')
    filename=uuid.uuid4().hex+ext
    (ASSETS/filename).write_bytes(content)
    updated=copy.deepcopy(project)
    updated.setdefault('assets',[]).append({'id':filename.split('.')[0], 'name':str(request.get('name','מקור'))[:120], 'kind':kind,'mime':mime,'url':'/assets/'+filename,'placement':placement,'coordinate_convention':'local XYZ, Z up' if kind=='model' else project['crs'],'sha256':__import__('hashlib').sha256(content).hexdigest(),'georeferencing_verified':False})
    return commit(project,updated,'asset:'+kind)


class Handler(BaseHTTPRequestHandler):
    def respond(self, value, status=200, mime='application/json',download_name=None,verified=False):
        payload = (json.dumps(value,ensure_ascii=False,allow_nan=False) if mime=='application/json' and not isinstance(value,bytes) else value)
        if not isinstance(payload,bytes): payload=payload.encode()
        self.send_response(status)
        self.send_header('Content-Type',mime+'; charset=utf-8' if mime.startswith('text/') or mime=='application/json' else mime)
        self.send_header('Content-Length',str(len(payload)))
        if download_name:
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(download_name,safe=''))
        if verified:
            self.send_header('X-Golden-City-File-Validation','passed')
            self.send_header('X-Golden-City-Recipient-Access','not-verified')
            self.send_header('X-Golden-City-SHA256',hashlib.sha256(payload).hexdigest())
        self.end_headers(); self.wfile.write(payload)

    def do_GET(self):
        try:
            self.serve_get()
        except ValidationError as error:
            self.respond({'error':'המסירה נחסמה: '+str(error),'validation':'failed','user_accessibility':'not_verified'},422)
        except (FileNotFoundError,ValueError) as error:
            self.respond({'error':str(error)},404)

    def serve_get(self):
        path=self.path.split('?')[0]
        if path == '/': return self.respond((ROOT/'index.html').read_text(),mime='text/html')
        if path == '/health': return self.respond({'status':'ok'})
        if path == '/api/capabilities': return self.respond(capabilities())
        if path in {'/site-clearing','/site-clearing/'}:
            artifact=ROOT/'outputs/planning-v5/site-viewer.html';payload=artifact.read_bytes()
            verify_for_delivery(artifact,expected={'sha256':hashlib.sha256(payload).hexdigest()})
            return self.respond(payload,mime='text/html',verified=True)
        if path == '/api/reconstruction/versions':
            from site_reconstruction import digest
            entries=[]
            for p in sorted((DATA/'site-reconstruction').glob('*.json')):
                record=json.loads(p.read_text())
                if digest(record['scene'])!=record['sha256']:raise ValueError('גרסת קרקע פגומה')
                entries.append({'version_id':record['version_id'],'sha256':record['sha256'],'stage':record['scene']['stage'],'name':record['scene'].get('name')})
            return self.respond({'versions':entries})
        if path.startswith('/api/reconstruction/version/'):
            from site_reconstruction import digest,propose
            identifier=path.rsplit('/',1)[-1]
            if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('מזהה גרסת קרקע לא תקין')
            record=json.loads((DATA/'site-reconstruction'/(identifier+'.json')).read_text())
            if digest(record['scene'])!=record['sha256']:raise ValueError('גרסת קרקע פגומה')
            return self.respond({**record,'proposal':propose(record['scene']) if record['scene']['stage']=='existing' else None})
        if path == '/api/architectural-versions':
            from architectural_edit import catalogue
            return self.respond({'versions':catalogue(DATA),'recipient_access':'not_verified'})
        if path == '/api/projects':
            with LOCK: return self.respond([json.loads(p.read_text()) for p in DATA.glob('*.json')])
        if path in {'/deliverables','/deliverables/'}:
            gallery=DELIVERABLES/'golden-city-gallery.html'
            payload=gallery.read_bytes()
            verify_for_delivery(gallery,expected={'sha256':hashlib.sha256(payload).hexdigest()})
            return self.respond(payload,mime='text/html',verified=True)
        if path=='/api/delivery':
            names=['golden-city-gallery.html','golden-city-materials.zip','contact-sheet.png','delivery-validation.json']
            entries=[]
            for name in names:
                candidate=DELIVERABLES/name
                if candidate.is_file():
                    evidence=verify_for_delivery(candidate)
                    entries.append({'name':name,'url':'/downloads/'+name,'bytes':candidate.stat().st_size,'validation':evidence['status'],'user_accessibility':'not_verified'})
            return self.respond({'files':entries,'server_file_validation':'passed' if len(entries)==len(names) else 'incomplete','recipient_access':'not_verified'})
        locations={'/vendor/':ROOT/'vendor','/renders/':OUTPUT,'/assets/':ASSETS,'/examples/':ROOT/'examples','/downloads/':DELIVERABLES,'/showcase/v3/':ROOT/'outputs'/'v3','/architectural-edits/':DATA/'architectural-edits'}
        if path=='/planning': return self.respond((ROOT/'planning.html').read_bytes(),mime='text/html')
        if path=='/planning.js': return self.respond((ROOT/'planning.js').read_bytes(),mime='text/javascript')
        if path=='/browser-view.js': return self.respond((ROOT/'browser-view.js').read_bytes(),mime='text/javascript')
        for prefix, directory in locations.items():
            if path.startswith(prefix):
                candidate=(directory/path[len(prefix):]).resolve()
                if candidate.is_relative_to(directory.resolve()) and candidate.is_file():
                    payload=candidate.read_bytes()
                    checked=prefix!='/vendor/'
                    expected_sha=hashlib.sha256(payload).hexdigest()
                    if prefix=='/architectural-edits/' and candidate.name!='version.json':
                        relative=candidate.relative_to(directory.resolve())
                        if len(relative.parts)!=2 or not re.fullmatch('[0-9a-f]{32}',relative.parts[0]):
                            raise ValueError('נתיב גרסה לא תקין')
                        record=json.loads((candidate.parent/'version.json').read_text())
                        expected_sha=record.get('checks',{}).get(candidate.name,{}).get('sha256')
                        if not expected_sha:raise ValidationError('תוצר לא רשום למסירה בגרסה')
                    if checked:verify_for_delivery(candidate,expected={'sha256':expected_sha},artifact_role='source' if prefix=='/assets/' else 'output')
                    download=candidate.name if prefix=='/downloads/' and candidate.suffix!='.html' else None
                    return self.respond(payload,mime=mimetypes.guess_type(candidate)[0] or 'application/octet-stream',download_name=download,verified=checked)
        self.respond({'error':'לא נמצא'},404)

    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length',0))
            request_limit=75_000_000 if self.path=='/api/intake' else 15_000_000
            if not 0 < length <= request_limit: raise ValueError('גודל בקשה לא תקין')
            request=json.loads(self.rfile.read(length))
            if self.path=='/api/municipal-collect':
                identifier=request.get('id')
                if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('מזהה תיק לא תקין')
                with LOCK: initial_project=json.loads((DATA/(identifier+'.json')).read_text())
                if initial_project.get('synthetic') is True:raise ValueError('מקורות אתר אמיתי נקלטים בתיק נפרד, ללא ערבוב עם ההדגמה הסינתטית')
                from engine.municipal_gis import collect,discover
                source_dir=DATA/'municipal-sources'/uuid.uuid4().hex
                source_dir.mkdir(parents=True,exist_ok=False)
                observations=collect(request['block'],request['parcels'],source_dir,request['street'],request['address_numbers']) if request.get('parcels') else discover(request['block'],request['street'],request['address_numbers'],source_dir)
                source_path=source_dir/'observations.json';save_atomic(source_path,observations)
                map_result=None
                if observations['parcels']['features']:
                    try:
                        from engine.municipal_map import render as render_source_map
                        map_result=render_source_map(observations,source_dir)
                        verify_for_delivery(source_dir/map_result['map_path'],expected={'sha256':map_result['sha256']})
                    except (ValueError,RuntimeError,OSError,KeyError,StopIteration) as error:
                        save_atomic(source_dir/'map-unavailable.json',{'status':'unavailable','error':str(error),'synthetic_substitute_created':False})
                with LOCK:
                    project=json.loads((DATA/(identifier+'.json')).read_text());previous=copy.deepcopy(project)
                    research=project.setdefault('real_site_research',{})
                    for key in ('map_url','map_sha256','delivery_bundle'):research.pop(key,None)
                    research['municipal_observations']={'path':str(source_path.relative_to(DATA)),'sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),'audit':observations['audit'],'counts':{k:len(observations[k]['features']) for k in ('parcels','buildings','addresses','height_points','contours')},'official_polygons_retrieved':bool(observations['parcels']['features']),'licensed_redistribution_verified':False}
                    if map_result:
                        map_name=uuid.uuid4().hex+'.png';map_bytes=(source_dir/map_result['map_path']).read_bytes();(ASSETS/map_name).write_bytes(map_bytes)
                        research['map_url']='/assets/'+map_name;research['map_sha256']=map_result['sha256'];research['map_source_provenance']=map_result['source']
                    known_units=observations['audit'].get('municipal_units_in_candidate_parcels')
                    if known_units is not None:
                        brief=project.setdefault('planning_brief',__import__('planning_brief').initial());answers=brief.setdefault('answers',{})
                        if answers.get('existing_units') in (None,'unknown'):answers['existing_units']=known_units;brief['approval']=None
                        brief.setdefault('provenance',{})['municipal_candidate_units']={'value':known_units,'scope':'candidate parcels; address linkage must be verified; show to user for review'}
                    project=commit(previous,project,'municipal-source-intake');ensure_geometry_record(project,DATA);save_atomic(DATA/(identifier+'.json'),project)
                return self.respond({'project':project,'source_audit':observations['audit'],'proposed_model_created':False})
            with LOCK:
                if self.path.startswith('/api/reconstruction/'):
                    from site_reconstruction import api_action
                    return self.respond(api_action(self.path.rsplit('/',1)[-1],request,DATA/'site-reconstruction'))
                if self.path == '/api/architectural-edit':
                    from architectural_edit import edit
                    return self.respond(edit(request,ROOT,DATA))
                elif self.path == '/api/dossier':
                    project=dossier(request)
                elif self.path == '/api/import':
                    project=validate(request); project['id']=uuid.uuid4().hex
                    project['geometry_role']='imported_source'
                    project['planning_brief']=__import__('planning_brief').initial(request.get('planning_answers'))
                    if request.get('geometry_role')=='proposed':raise ValueError('קליטת בינוי מוצע חדש דורשת פרוגרמה ואישור; יש להשתמש בהעמדה מתוך קרקע נקייה')
                else:
                    identifier=request['id']
                    if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier): raise ValueError('מזהה לא תקין')
                    project=json.loads((DATA/(identifier+'.json')).read_text())
                    previous=copy.deepcopy(project)
                    if self.path == '/api/intake':
                        project,result=inspect_source(project,request,DATA)
                        save_atomic(DATA/(identifier+'.json'),project)
                        return self.respond({'project':project,'result':result})
                    elif self.path == '/api/site-status':
                        from real_site_readiness import status
                        return self.respond(status(project))
                    elif self.path == '/api/readiness':
                        result=readiness(project,DATA)
                        save_atomic(DATA/(identifier+'.json'),project)
                        return self.respond(result)
                    elif self.path == '/api/review':
                        project=record_review(project,request,DATA)
                    elif self.path in {'/api/planning-save','/api/planning-review','/api/planning-approve','/api/planning-alternatives'}:
                        from planning_brief import normalize,review,approve,alternatives
                        if self.path=='/api/planning-review':return self.respond(review(project,project.get('planning_brief',{}),request.get('plan')))
                        if self.path=='/api/planning-alternatives':return self.respond(alternatives(project.get('planning_brief',{}),request.get('context',{})))
                        project['planning_brief']=normalize(request['brief']) if self.path=='/api/planning-save' else approve(project,project.get('planning_brief',{}),request.get('plan'),request)
                        project=commit(previous,project,'planning-brief')
                    elif self.path == '/api/approve':
                        if project['errors']: raise ValueError('יש לתקן את שגיאות ההעמדה')
                        if not project.get('buildings') or not project.get('boundary'):
                            raise ValueError('יש לקלוט גבולות ותוכנית העמדה לפני אישור')
                        project['approved']=True
                        project=commit(previous,project,'approve')
                    elif self.path in {'/api/style','/api/edit'}:
                        project=edit_building(project,request)
                    elif self.path == '/api/camera':
                        name=str(request['name']).strip()[:80]
                        if not name: raise ValueError('נדרש שם מצלמה')
                        project.setdefault('cameras',{})[name]=check_camera(request['camera'])
                        project=commit(previous,project,'camera:'+name)
                    elif self.path == '/api/restore':
                        project=restore(project,int(request['version']))
                    elif self.path == '/api/render':
                        if project['errors'] or not project['approved']: raise ValueError('יש לאשר העמדה תקינה לפני רינדור')
                        mode=request.get('mode','draft')
                        if mode not in {'draft','final'}: raise ValueError('מצב רינדור לא מוכר')
                        gate=readiness(project,DATA)
                        if mode=='final' and not gate['ready_for_final_render']:
                            raise ValueError('רינדור סופי חסום: '+'; '.join(gate['missing']))
                        if project.get('synthetic') is not True:
                            raise ValueError('רינדור אתר אמיתי חסום: טרם הושלמה קליטת טופוגרפיה וסביבה מדודות. אין להשתמש בקרקע שטוחה משוערת')
                        from rendering import render_project
                        camera=request.get('camera')
                        if isinstance(camera,dict): camera=check_camera(camera)
                        name=identifier+'-'+str(project['version'])+'-'+uuid.uuid4().hex[:8]+'.png'
                        result=render_project(project,camera,OUTPUT/name,width=int(request.get('width',800)),height=int(request.get('height',600)))
                        result.update(url='/renders/'+name,version=project['version'],name=str(request.get('name','רינדור מצלמה'))[:80],blend_url='/renders/'+Path(name).with_suffix('.blend').name,scene_url='/renders/'+Path(name).with_suffix('.scene.json').name)
                        result.update(mode=mode,classification='synthetic_experiment',accuracy_certified=False,geometry_version_sha256=project['geometry_version_sha256'])
                        project.setdefault('renders',[]).append(result)
                        save_atomic(DATA/(identifier+'.json'),project)
                        return self.respond(result)
                    elif self.path == '/api/export':
                        output=EXPORTS/(identifier+'-'+str(project['version'])+'.obj')
                        output.write_text(model_obj(project))
                        payload=output.read_bytes()
                        verify_for_delivery(output,expected={'sha256':hashlib.sha256(payload).hexdigest(),'object_names':['building_'+quote(b['properties']['id'],safe='') for b in project['buildings']],'cameras':0})
                        return self.respond(payload,mime='text/plain',download_name='model.obj',verified=True)
                    elif self.path == '/api/real-site-bundle':
                        from delivery_validation import validate_archive
                        bundle=project.get('real_site_research',{}).get('delivery_bundle',{})
                        if not bundle:raise ValueError('אין חבילת מקור שנבדקה לתיק זה')
                        output=(DATA/bundle['path']).resolve()
                        if DATA.resolve() not in output.parents or output.suffix!='.zip':raise ValueError('נתיב חבילת מקור לא תקין')
                        payload=output.read_bytes()
                        if hashlib.sha256(payload).hexdigest()!=bundle['sha256']:raise ValueError('חבילת המקור השתנתה מאז בדיקתה')
                        validate_archive(output)
                        return self.respond(payload,mime='application/zip',download_name='real-site-source-study.zip',verified=True)
                    elif self.path == '/api/report':
                        output=EXPORTS/(identifier+'-'+str(project['version'])+'.json')
                        output.write_text(json.dumps(project,ensure_ascii=False,indent=2,allow_nan=False))
                        payload=output.read_bytes()
                        verify_for_delivery(output,expected={'sha256':hashlib.sha256(payload).hexdigest()})
                        return self.respond(payload,mime='application/json',download_name='project-report.json',verified=True)
                    elif self.path == '/api/assets': project=ingest_asset(project,request)
                    elif self.path == '/api/balconies':
                        from balconies import analyze_balcony
                        building=next((b for b in project['buildings'] if b['properties']['id']==request['building']),None)
                        if not building: raise ValueError('מבנה לא נמצא')
                        return self.respond([analyze_balcony(project,request['building'],b['id'],overhead_complete=request.get('overhead_complete',False)) for b in building['properties'].get('balconies',[])])
                    elif self.path == '/api/sukkah-screen':
                        from sukkah_policy import screen
                        return self.respond(screen(project,request['building_id'],request['balcony_id'],
                            policy=request.get('policy'),details=request.get('details'),
                            overhead_complete=request.get('overhead_complete',False)))
                    elif self.path == '/api/boundary':
                        from spatial import import_boundary
                        source=request.get('source',{})
                        source.setdefault('provider',source.get('name','User upload'))
                        source.setdefault('reference','User supplied GeoJSON')
                        source.setdefault('acquired_at',__import__('datetime').date.today().isoformat())
                        source.setdefault('source_type','user_supplied')
                        result=import_boundary(request['geojson'],request['source_crs'],project['crs'],source)
                        updated=validate({**project,'boundary':result['boundary']})
                        for field in ('boundary','buildings','errors','approved'):
                            project[field]=updated[field]
                        project['source_boundary']=request['geojson']
                        project['boundary_provenance']=result
                        project=commit(previous,project,'boundary')
                    elif self.path == '/api/align':
                        from planning_brief import require_approval
                        from spatial import align_site_plan
                        result=align_site_plan(request['buildings'],request['control_points'],project['crs'],max_residual=float(request.get('max_residual',0.25)))
                        existing={b['properties']['id']:b for b in project.get('buildings',[])}
                        unchanged=len(existing)==len(result['buildings']) and all(b['properties']['id'] in existing and shape(b['geometry']).equals_exact(shape(existing[b['properties']['id']]['geometry']),1e-6) and all(b['properties'].get(k)==existing[b['properties']['id']]['properties'].get(k) for k in ('height','floors','base_elevation','balconies','slabs')) for b in result['buildings'])
                        if not unchanged:require_approval(project,request)
                        updated=validate({**project,'buildings':result['buildings']})
                        for field in ('buildings','errors','approved'):
                            project[field]=updated[field]
                        project['source_plan']=request['buildings']
                        project['alignment']=result['alignment']
                        project=commit(previous,project,'alignment')
                    else: return self.respond({'error':'לא נמצא'},404)
                ensure_geometry_record(project,DATA)
                save_atomic(DATA/(project['id']+'.json'),project)
                self.respond(project)
        except ValidationError as error:
            self.respond({'error':'המסירה נחסמה: '+str(error),'validation':'failed'},422)
        except (ValueError,KeyError,TypeError,FileNotFoundError,StopIteration,UnidentifiedImageError) as error:
            self.respond({'error':str(error)},400)
        except (RuntimeError,TimeoutError) as error:
            self.respond({'error':str(error)},503)

if __name__=='__main__':
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
