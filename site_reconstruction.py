"""Immutable, approval-bound TIN clearing and metric plan placement.

The synthetic executable pilot is deliberately separate from real-site intake.
Real source labels alone cannot unlock demolition or certify cadastral accuracy.
No photo inpainting, terrain extrapolation, mesh segmentation or earthworks.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid
import os
import tempfile
from functools import lru_cache

import numpy as np
from shapely.geometry import Polygon, Point, mapping
from shapely.geometry import MultiPoint
from shapely.ops import unary_union
from shapely.strtree import STRtree
from spatial import polygon_geometry, projected_metric_crs, align_site_plan


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def number(value, label, minimum=None):
    if type(value) not in (int,float) or not math.isfinite(value) or (minimum is not None and value<minimum):
        raise ValueError('Invalid finite '+label)
    return float(value)


def integer(value,label,minimum=0):
    if type(value) is not int or value<minimum:raise ValueError('Invalid integer '+label)
    return value


def program(data=None):
    """Paldalet is a separately specified apartment area, never inferred gross."""
    data=deepcopy(data or {})
    units=integer(data['units'],'units',1) if data.get('units') is not None else None
    area=number(data.get('apartment_paldalet_m2',100),'apartment paldalet',1)
    keys=('core','elevators','stairs','corridors','shafts','shared','lobby')
    supplied=data.get('separate_areas_m2',{})
    if not isinstance(supplied,dict) or set(supplied)-set(keys):raise ValueError('Unknown separate area category')
    separate={key:number(supplied[key],key,0) if key in supplied else None for key in keys}
    # Categories must be disjoint: core here excludes all separately listed items.
    missing=[key for key,value in separate.items() if value is None]
    if not missing and data.get('categories_are_disjoint') is not True:missing.append('disjoint_category_confirmation')
    requested=['units','multiplier','desired_height_m','building_count','planning_constraints']
    missing_program=[key for key in requested if key not in data or data[key] is None or data[key]=='']
    if data.get('multiplier') is not None:number(data['multiplier'],'multiplier',.01)
    if data.get('desired_height_m') is not None:number(data['desired_height_m'],'desired height',.01)
    if data.get('building_count') is not None:integer(data['building_count'],'building count',1)
    if data.get('planning_constraints') is not None and not isinstance(data['planning_constraints'],dict):raise ValueError('Explicit planning constraints required')
    existing=integer(data['existing_units'],'existing units',1) if data.get('existing_units') is not None else None
    target=existing*data['multiplier'] if existing is not None and data.get('multiplier') is not None else None
    inconsistencies=['units_do_not_match_existing_times_multiplier'] if target is not None and units is not None and abs(target-units)>1e-8 else []
    return {'apartment_paldalet_m2':area,'units':units,'apartments_paldalet_total_m2':units*area if units is not None else None,
            'separate_areas_m2':separate,'known_separate_total_m2':sum(v for v in separate.values() if v is not None),
            'gross_total_m2':None if missing or units is None else units*area+sum(separate.values()),
            'missing_area_categories':missing,'missing_program':missing_program,
            'area_definition':'User-defined paldalet; no legal/statutory area certification. Disjoint separate categories required.',
            'approved_design':False,'constraints':deepcopy(data.get('planning_constraints',{})),
            'existing_units':existing,'multiplier':data.get('multiplier'),'target_units_from_multiplier':target,
            'desired_height_m':data.get('desired_height_m'),'building_count':data.get('building_count'),
            'program_inconsistencies':inconsistencies}


def alternatives(data,buildable_area_m2=None):
    p=program(data);constraints=p['constraints']; candidates=[]
    extra=[k for k in ('max_coverage_ratio','residential_floors') if constraints.get(k) is None]
    extra.extend(p['program_inconsistencies'])
    if buildable_area_m2 is None:extra.append('buildable_area_m2')
    # This is arithmetic feasibility, not an unapproved geometric site plan.
    if not p['missing_program'] and not p['missing_area_categories'] and not extra:
        land=number(buildable_area_m2,'buildable area',1)
        cap=number(constraints.get('max_coverage_ratio'),'max coverage',.001)
        if cap>1:raise ValueError('Coverage cannot exceed one')
        for count in sorted({data['building_count'],data['building_count']+1}):
            residential_average=(p['gross_total_m2']-p['separate_areas_m2']['lobby'])/integer(constraints.get('residential_floors'),'residential floors',1)
            footprint=max(residential_average,p['separate_areas_m2']['lobby'])
            candidates.append({'building_count':count,'aggregate_average_floor_area_m2':footprint,
                               'aggregate_residential_floor_average_m2':residential_average,
                               'separate_lobby_floor_area_m2':p['separate_areas_m2']['lobby'],
                               'approximate_per_building_floor_area_m2':footprint/count,
                               'arithmetic_coverage_ratio':footprint/land,'within_coverage_cap':footprint/land<=cap,
                               'assumptions':['Non-lobby areas distributed evenly over supplied residential floors; separate lobby area occupies a ground floor across the buildings.',
                                              'No placement, fire access, setbacks, height feasibility or authority approval inferred.'],
                               'classification':'concept_arithmetic_only','approved_design':False})
    return {'program':p,'alternatives':candidates,'missing':p['missing_program']+p['missing_area_categories']+extra,
            'conditional_concept_options':[] if candidates else [
                {'approach':'Distribute apartments among more lower buildings','conditions':['Check actual height/coverage/building-line/fire-access rights after programme completion'],
                 'feasibility_status':'not_yet_evaluated','building_count':None,'height_m':None,'approved_design':False},
                {'approach':'Concentrate apartments in fewer taller buildings to reduce footprint','conditions':['Requires permitted height, core capacity, access and verified buildable land'],
                 'feasibility_status':'not_yet_evaluated','building_count':None,'height_m':None,'approved_design':False}],
            'geometry_created':False,'planning_approval':False}


def terrain_mesh(mesh):
    if not isinstance(mesh,dict) or mesh.get('units')!='metres' or not mesh.get('vertical_datum') or mesh.get('surface_type')!='bare_earth_DTM':
        raise ValueError('Terrain needs explicit metres and vertical datum')
    vertices=mesh.get('vertices');triangles=mesh.get('triangles')
    if not isinstance(vertices,list) or not 3<=len(vertices)<=25000 or not isinstance(triangles,list) or not 1<=len(triangles)<=50000:
        raise ValueError('Complete bounded triangulated terrain required')
    for v in vertices:
        if not isinstance(v,list) or len(v)!=3:raise ValueError('Terrain needs XYZ vertices')
        for x in v:number(x,'terrain coordinate')
    faces=[]
    for t in triangles:
        if not isinstance(t,list) or len(t)!=3 or len(set(t))!=3 or any(type(i) is not int or not 0<=i<len(vertices) for i in t):
            raise ValueError('Invalid terrain triangle indices')
        face=Polygon([vertices[i][:2] for i in t])
        if not face.is_valid or face.area<=1e-9:raise ValueError('Degenerate terrain triangle')
        faces.append(face)
    if {i for t in triangles for i in t}!=set(range(len(vertices))):raise ValueError('Unused terrain nodes cannot establish the rendered height range')
    coverage=unary_union(faces)
    if abs(sum(f.area for f in faces)-coverage.area)>1e-6:raise ValueError('Terrain triangles overlap in XY')
    # Duplicate XY nodes and disconnected edge heights cannot be silently averaged.
    xy={}
    for x,y,z in vertices:
        if (x,y) in xy and abs(xy[x,y]-z)>1e-7:raise ValueError('Inconsistent terrain elevation at identical XY')
        xy[x,y]=z
    arrays,indices,index=_terrain_index(json.dumps(mesh,sort_keys=True,allow_nan=False))
    for x,y,z in vertices:
        if abs(_sample_index(arrays,indices,index,[x,y])-z)>1e-6:raise ValueError('TIN continuity failure')
    return coverage


def prism(footprint,base,height):
    """Explicit synthetic massing; never reverse-engineer an existing building."""
    from shapely import constrained_delaunay_triangles
    p=polygon_geometry(footprint);coords=[];faces=[]
    for ring in (p.exterior,*p.interiors):
        start=len(coords);points=list(ring.coords)[:-1];coords.extend(points)
        for i in range(len(points)):faces.append([start+i,start+(i+1)%len(points)])
    n=len(coords);lookup={tuple(v):i for i,v in enumerate(coords)}
    vertices=[[x,y,base] for x,y in coords]+[[x,y,base+height] for x,y in coords]
    triangles=[]
    for triangle in constrained_delaunay_triangles(p).geoms:
        ids=[lookup[tuple(xy)] for xy in list(triangle.exterior.coords)[:-1]]
        a,b,c=(coords[i] for i in ids)
        if (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])<0:ids.reverse()
        triangles.append([i+n for i in ids]);triangles.append(list(reversed(ids)))
    for a,b in faces:triangles.extend([[a,b,b+n],[a,b+n,a+n]])
    return {'vertices':vertices,'triangles':triangles}


@lru_cache(maxsize=8)
def _terrain_index(serialized):
    mesh=json.loads(serialized)
    verts=np.array(mesh['vertices'],dtype=float)
    triangles=np.array(mesh['triangles'],dtype=int)
    faces=[Polygon(verts[t,:2]) for t in triangles]
    return verts,triangles,STRtree(faces)


def elevation(mesh,xy):
    all_vertices,triangles,index=_terrain_index(json.dumps(mesh,sort_keys=True,allow_nan=False))
    return _sample_index(all_vertices,triangles,index,xy)


def _sample_index(all_vertices,triangles,index,xy):
    x,y=map(float,xy);values=[]
    for i in index.query(Point(x,y)):
        verts=all_vertices[triangles[i]]
        try:w=np.linalg.solve(np.vstack((verts[:,:2].T,np.ones(3))),[x,y,1])
        except np.linalg.LinAlgError:raise ValueError('Invalid TIN triangle')
        if np.min(w)>=-1e-8 and np.max(w)<=1+1e-8:values.append(float(w@verts[:,2]))
    if not values:raise ValueError('No measured TIN coverage: elevation extrapolation is forbidden')
    if max(values)-min(values)>1e-6:raise ValueError('Discontinuous TIN at sampling point')
    return sum(values)/len(values)


def validate_scene(scene):
    if type(scene.get('synthetic')) is not bool:raise ValueError('Explicit synthetic classification required')
    projected_metric_crs(scene['crs'])
    origin=scene.get('origin')
    if not isinstance(origin,list) or len(origin)!=3:raise ValueError('Explicit local-to-site XYZ origin required')
    for v in origin:number(v,'origin')
    boundary=polygon_geometry(scene['boundary'],'intervention boundary')
    coverage=terrain_mesh(scene['terrain'])
    if not coverage.buffer(1e-7).covers(boundary):raise ValueError('Intervention lacks complete terrain coverage')
    objects=scene.get('objects')
    if not isinstance(objects,list) or len(objects)>500:raise ValueError('Explicit bounded classified scene objects required')
    ids=set()
    for o in objects:
        if not isinstance(o.get('id'),str) or not o['id'] or o['id'] in ids:raise ValueError('Unique stable object IDs required')
        ids.add(o['id'])
        if o.get('kind') not in {'building','street','sidewalk','vegetation','other'}:raise ValueError('Explicit object classification required')
        footprint=polygon_geometry(o['footprint'],'object footprint')
        if o['kind']=='building':
            number(o['height'],'building height',.01);integer(o['floors'],'floors',1)
        if not coverage.buffer(1e-7).covers(footprint):raise ValueError('Scene object lacks terrain coverage')
        for xy in list(footprint.exterior.coords)[:-1]:elevation(scene['terrain'],xy)
        if 'mesh' not in o:raise ValueError('Classified 3D object mesh required; footprints alone are not an existing-state model')
        if 'mesh' in o:
            mesh=o['mesh']
            if not isinstance(mesh,dict) or not mesh.get('vertices') or not mesh.get('triangles'):raise ValueError('Complete classified mesh required')
            if len(mesh['vertices'])>25000 or len(mesh['triangles'])>50000:raise ValueError('Object mesh budget exceeded')
            for v in mesh['vertices']:
                if not isinstance(v,list) or len(v)!=3:raise ValueError('Object mesh XYZ required')
                for x in v:number(x,'object vertex')
                if not footprint.buffer(1e-7).covers(Point(v[:2])):raise ValueError('Mesh vertex escapes classified footprint')
            for t in mesh['triangles']:
                if not isinstance(t,list) or len(t)!=3 or len(set(t))!=3 or any(type(i) is not int or not 0<=i<len(mesh['vertices']) for i in t):raise ValueError('Object triangle indices invalid')
                v=np.array([mesh['vertices'][i] for i in t],dtype=float)
                if np.linalg.norm(np.cross(v[1]-v[0],v[2]-v[0]))<1e-9:raise ValueError('Degenerate object triangle')
                if not footprint.buffer(1e-7).covers(MultiPoint(v[:,:2]).convex_hull):raise ValueError('Object face escapes classified footprint')
            if o['kind']=='building':
                base=number(o.get('base_elevation'),'source base elevation')
                if abs(max(v[2] for v in mesh['vertices'])-(base+o['height']))>1e-4:raise ValueError('Mesh roof does not match supplied height above base')
    return boundary


def propose(scene):
    boundary=validate_scene(scene); inside=[];crossing=[];outside=[]
    for o in scene['objects']:
        p=polygon_geometry(o['footprint'])
        if o['kind']=='building' and boundary.covers(p):inside.append(o['id'])
        elif o['kind']=='building' and boundary.intersects(p):crossing.append(o['id'])
        else:outside.append(o['id'])
    return {'scene_sha256':digest(scene),'candidate_ids':inside,'crossing_buildings_not_removable':crossing,
            'preserved_ids':outside,'requires_explicit_selection':True,'real_site_accuracy_certified':False}


def approve_clearing(scene,selected_ids,reviewer,boundary_approved=False):
    p=propose(scene)
    if boundary_approved is not True or not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Boundary and reviewer approval required')
    if not isinstance(selected_ids,list) or len(selected_ids)!=len(set(selected_ids)) or not selected_ids or not set(selected_ids)<=set(p['candidate_ids']):
        raise ValueError('Only explicitly selected wholly enclosed buildings may be removed')
    return {'scene_sha256':p['scene_sha256'],'selected_ids':sorted(selected_ids),'reviewer':reviewer.strip(),
            'boundary_approved':True,'reviewed_at':datetime.now(timezone.utc).isoformat(),'approval_kind':'recorded_user_selection_not_professional_certificate'}


def clear(scene,approval):
    p=propose(scene)
    if scene['synthetic'] is not True:
        raise ValueError('Real clearing blocked pending authenticated official boundaries, measured terrain and classified-source validation')
    expected=approve_clearing(scene,approval.get('selected_ids'),approval.get('reviewer'),approval.get('boundary_approved'))
    if approval.get('scene_sha256')!=expected['scene_sha256']:raise ValueError('Stale clearing approval')
    original=digest(scene);result=deepcopy(scene)
    result['objects']=[o for o in result['objects'] if o['id'] not in approval['selected_ids']]
    result['stage']='cleared';result['parent_sha256']=original;result['clearing_approval']=deepcopy(approval)
    result['removed_ids']=list(approval['selected_ids'])
    result['qa']=compare(scene,result,approval['selected_ids'])
    if digest(scene)!=original:raise ValueError('Original scene modified')
    return result


def compare(original,result,removed_ids):
    before={o['id']:o for o in original['objects']};after={o['id']:o for o in result['objects']}
    protected=set(before)-set(removed_ids)
    changed=[i for i in protected if i not in after or digest(before[i])!=digest(after[i])]
    terrain_equal=digest(original['terrain'])==digest(result['terrain'])
    removed_absent=not set(removed_ids)&set(after)
    spatial_equal=all(original.get(k)==result.get(k) for k in ('boundary','crs','origin','synthetic'))
    if changed or not terrain_equal or not removed_absent or not spatial_equal:raise ValueError('Clearing damaged protected scene or terrain/spatial frame')
    z=[v[2] for v in original['terrain']['vertices']]
    return {'passed':True,'preserved_object_count':len(protected),'outside_or_unselected_changed':changed,
            'terrain_sha256':digest(original['terrain']),'terrain_exactly_preserved':terrain_equal,
            'elevation_range_m':[min(z),max(z)],'removed_ids_absent':removed_absent,
            'terrain_method':'Reveal the already source-supported continuous TIN under removed objects; never flatten/extrapolate',
            'photo_pixels_modified':False,'synthetic':original['synthetic'],'real_site_accuracy_certified':False}


def place(cleared,request):
    from planning_brief import require_approval
    require_approval(cleared,request)
    if cleared.get('stage')!='cleared':raise ValueError('Approved cleared working version required')
    if cleared['synthetic'] is not True:raise ValueError('Real placement blocked until source and terrain validation implemented')
    boundary=validate_scene(cleared)
    envelope=polygon_geometry(request['buildable_envelope'],'explicit building-line envelope')
    if not boundary.covers(envelope):raise ValueError('Building-line envelope escapes intervention')
    if request.get('north_reference')!='projected_grid_north':raise ValueError('Explicit grid-north reference required')
    if request.get('vertical_datum')!=cleared['terrain']['vertical_datum']:raise ValueError('Entrance vertical datum mismatch')
    aligned=align_site_plan(request['buildings'],request['control_points'],cleared['crs'],
                            max_residual=number(request['max_residual_m'],'alignment tolerance',.000001),
                            scale_bounds=request['scale_bounds'])
    aligned['alignment']['terrain_alignment']='Entrance and footprint sampled on supplied TIN; no terrain deformation'
    arrow=request['plan_north_xy']
    if not isinstance(arrow,list) or len(arrow)!=2:raise ValueError('Explicit plan north arrow required')
    nx,ny=(number(x,'north arrow') for x in arrow)
    if math.hypot(nx,ny)<1e-9:raise ValueError('Nonzero north arrow required')
    angle=math.radians(aligned['alignment']['rotation_degrees'])
    north_error=math.degrees(math.acos(max(-1,min(1,(math.sin(angle)*nx+math.cos(angle)*ny)/math.hypot(nx,ny)))))
    if north_error>number(request['north_tolerance_degrees'],'north tolerance',0):raise ValueError('Plan north arrow conflicts with control-point transform')
    output=deepcopy(cleared); output['parent_sha256']=digest(cleared);output['stage']='placement_pending';errors=[];levels=[];ids={o['id'] for o in cleared['objects']}
    for f in aligned['buildings']:
        props=f['properties'];identifier=props['id'];foot=polygon_geometry(f['geometry'])
        if not isinstance(identifier,str) or identifier in ids:raise ValueError('New object IDs must be unique')
        ids.add(identifier)
        number(props['height'],'height',.01);integer(props['floors'],'floors',1)
        if props.get('planning_status')!='synthetic_concept':raise ValueError('Synthetic proposals must be classified as concepts')
        if not envelope.covers(foot):errors.append(identifier+': outside building-line envelope')
        if any(foot.intersection(polygon_geometry(o['footprint'])).area>1e-7 for o in output['objects']):
            errors.append(identifier+': collides with preserved/new object')
        base=number(props['base_elevation'],'entrance elevation'); max_delta=number(request['max_entrance_difference_m'],'entrance tolerance',0)
        entrance=props['entrance_xy']
        # Entrance is supplied in site coordinates, deliberately not guessed from a centroid.
        if not isinstance(entrance,list) or len(entrance)!=2:raise ValueError('Explicit projected entrance XY required')
        for v in entrance:number(v,'entrance coordinate')
        if not foot.buffer(1e-7).covers(Point(entrance)):raise ValueError('Entrance must lie on/within building footprint')
        ground=elevation(cleared['terrain'],entrance); delta=base-ground
        if abs(delta)>max_delta:errors.append(identifier+': entrance/terrain difference exceeds tolerance')
        sampled=[elevation(cleared['terrain'],xy) for xy in list(foot.exterior.coords)[:-1]]
        levels.append({'id':identifier,'entrance_ground_m':ground,'finished_floor_m':base,'difference_m':delta,
                       'footprint_ground_range_m':[min(sampled),max(sampled)],'earthworks_designed':False})
        mass=prism(f['geometry'],base,props['height'])
        for vertex in mass['vertices'][:len(mass['vertices'])//2]:vertex[2]=elevation(cleared['terrain'],vertex[:2])
        quantities=program(props.get('program'));capacity=foot.area*props['floors']
        known=quantities['gross_total_m2'] if quantities['gross_total_m2'] is not None else quantities['apartments_paldalet_total_m2']
        if known is not None and known>capacity+1e-6:errors.append(identifier+': supplied apartment/separate areas exceed constant-floorplate capacity')
        if quantities['program_inconsistencies']:errors.append(identifier+': inconsistent unit count and multiplier')
        output['objects'].append({'id':identifier,'kind':'building','footprint':f['geometry'],'height':props['height'],
                                  'floors':props['floors'],'base_elevation':base,'planning_status':props['planning_status'],
                                  'program':quantities,'schematic_floorplate_capacity_m2':capacity,'mesh':mass,'massing_ground_connection':'Diagnostic base follows supplied TIN; not a structural foundation design'})
    output['placement']={'source_sha256':digest(request),'alignment':aligned['alignment'],'north_reference':request['north_reference'],
                         'building_line_envelope':request['buildable_envelope'],'levels':levels,'errors':errors,
                         'coverage_ratio':sum(polygon_geometry(f['geometry']).area for f in aligned['buildings'])/boundary.area,
                         'missing':['Architectural/structural/fire/accessibility review','Earthworks, retaining walls and finished-ground design'],
                         'north_deviation_degrees':north_error,'approved':False,'source_plan':deepcopy(request)}
    compare(cleared,output,[])
    return output


def approve_placement(scene,expected_sha256,reviewer):
    if scene.get('stage')!='placement_pending' or digest(scene)!=expected_sha256:raise ValueError('Stale or invalid placement approval')
    if scene['placement']['errors']:raise ValueError('Placement deviations must be corrected')
    if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Named reviewer required')
    result=deepcopy(scene);result['parent_sha256']=digest(scene);result['stage']='placement_approved'
    result['placement']['approved']=True;result['placement']['reviewer']=reviewer;result['placement']['reviewed_at']=datetime.now(timezone.utc).isoformat()
    result['final_render_ready']=False
    return result


def save_version(scene,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);identifier=uuid.uuid4().hex
    record={'version_id':identifier,'sha256':digest(scene),'scene':scene}
    path=directory/(identifier+'.json')
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.pending',dir=directory,delete=False) as f:
        temp=Path(f.name);json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    try:os.link(temp,path)
    finally:temp.unlink(missing_ok=True)
    if digest(json.loads(path.read_text())['scene'])!=record['sha256']:raise ValueError('Version copy mismatch')
    return record


def api_action(action,request,directory):
    directory=Path(directory)
    if action=='program':return program(request.get('program'))
    if action=='alternatives':return alternatives(request.get('program',{}),request.get('buildable_area_m2'))
    if action=='import':
        scene=deepcopy(request['scene']);validate_scene(scene);scene['stage']='existing'
        record=save_version(scene,directory)
        return {'version':record,'proposal':propose(scene),'real_clearing_supported':False}
    identifier=request.get('version_id')
    if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid reconstruction version ID')
    record=json.loads((directory/(identifier+'.json')).read_text());scene=record['scene']
    if digest(scene)!=record['sha256'] or request.get('expected_sha256')!=record['sha256']:raise ValueError('Stale or corrupted reconstruction version')
    if action in {'planning-save','planning-review','planning-approve','planning-alternatives'}:
        from planning_brief import normalize,review,approve,alternatives as brief_alternatives
        if action=='planning-save':
            result=deepcopy(scene);result['planning_brief']=normalize(request['brief'])
        elif action=='planning-review':return review(scene,scene.get('planning_brief',{}),request.get('plan'))
        elif action=='planning-alternatives':return brief_alternatives(scene.get('planning_brief',{}),request.get('context',{}))
        else:
            result=deepcopy(scene);result['planning_brief']=approve(scene,scene.get('planning_brief',{}),request.get('plan'),request)
    elif action=='clear':
        approval=approve_clearing(scene,request.get('selected_ids'),request.get('reviewer'),request.get('boundary_approved',False))
        result=clear(scene,approval)
    elif action=='place':result=place(scene,request['plan'])
    elif action=='approve-placement':result=approve_placement(scene,record['sha256'],request.get('reviewer'))
    else:raise ValueError('Unknown reconstruction action')
    return {'version':save_version(result,directory)}
