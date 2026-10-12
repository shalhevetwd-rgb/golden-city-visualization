"""Add architectural depth to the existing synthetic GLB without rebuilding it.

The 15 imported source meshes and their world transforms remain unchanged.
Cladding, reveals, entries and scale figures are explicitly synthetic proposals;
no new floors, balconies, surveyed roads or real-site claims are introduced.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import random
import sys

import bpy
from mathutils import Vector
from mathutils.geometry import intersect_ray_tri

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from constraints import site_gate
from render_context import load_context,configure_render,camera_record,export_portable

cfg,out=load_context()
assert site_gate(cfg)['final_render_allowed'],'Required project evidence is missing'
assert cfg['synthetic'] is True,'This architectural concept is synthetic only'
random.seed(cfg['seed'])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(ROOT/cfg['source_model']))
bpy.context.view_layer.update()
original=list(bpy.context.scene.objects)


def bounds(obj):
    points=[obj.matrix_world@vertex.co for vertex in obj.data.vertices]
    return [[min(point[i] for point in points) for i in range(3)],[max(point[i] for point in points) for i in range(3)]]


def geometry(obj):
    if obj.type!='MESH':return None
    raw={'vertices':[list(vertex.co) for vertex in obj.data.vertices],'faces':[list(polygon.vertices) for polygon in obj.data.polygons],'matrix':[list(row) for row in obj.matrix_world]}
    return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()

source={obj.name:{'geometry':geometry(obj),'bounds':bounds(obj)} for obj in original if obj.type=='MESH'}


def material(name,color,rough=.5,metal=0,kind=None):
    mat=bpy.data.materials.new(name)
    mat.use_nodes=True
    mat.use_fake_user=True  # Keep the procedural vegetation library across the staged .blend save.
    mat.diffuse_color=(*color,1)
    nodes=mat.node_tree.nodes;links=mat.node_tree.links
    bsdf=nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value=(*color,1)
    bsdf.inputs['Roughness'].default_value=rough
    bsdf.inputs['Metallic'].default_value=metal
    if kind=='glass':
        bsdf.inputs['IOR'].default_value=1.52
        bsdf.inputs['Transmission Weight'].default_value=.18
        bsdf.inputs['Coat Weight'].default_value=.20
        bsdf.inputs['Coat Roughness'].default_value=.06
        mat['representation']='Reflective glazing over the unchanged source wall; no interior reconstruction'
    if kind in ('stone','plaster','paving','asphalt','bark','soil','grass','roof'):
        geom=nodes.new('ShaderNodeNewGeometry')
        broad=nodes.new('ShaderNodeTexNoise')
        broad.inputs['Scale'].default_value=.75 if kind!='grass' else .30
        broad.inputs['Detail'].default_value=3
        links.new(geom.outputs['Position'],broad.inputs['Vector'])
        ramp=nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].color=(*(v*.87 for v in color),1)
        ramp.color_ramp.elements[1].color=(*(min(1,v*1.05) for v in color),1)
        links.new(broad.outputs['Fac'],ramp.inputs[0])
        links.new(ramp.outputs['Color'],bsdf.inputs['Base Color'])
        fine=nodes.new('ShaderNodeTexNoise')
        fine.inputs['Scale'].default_value={'stone':110,'plaster':155,'paving':95,'asphalt':130,'bark':35,'soil':85,'grass':70,'roof':110}[kind]
        fine.inputs['Detail'].default_value=2
        links.new(geom.outputs['Position'],fine.inputs['Vector'])
        bump=nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value=.16 if kind!='plaster' else .09
        bump.inputs['Distance'].default_value=.0018 if kind in ('stone','plaster') else .012
        links.new(fine.outputs['Fac'],bump.inputs['Height'])
        links.new(bump.outputs['Normal'],bsdf.inputs['Normal'])
        if kind in ('stone','paving'):
            sep=nodes.new('ShaderNodeSeparateXYZ');links.new(geom.outputs['Position'],sep.inputs[0])
            normal=nodes.new('ShaderNodeSeparateXYZ');links.new(geom.outputs['Normal'],normal.inputs[0])
            abs_x=nodes.new('ShaderNodeMath');abs_x.operation='ABSOLUTE';links.new(normal.outputs['X'],abs_x.inputs[0])
            mix=nodes.new('ShaderNodeMix');mix.data_type='FLOAT'
            links.new(abs_x.outputs[0],mix.inputs[0]);links.new(sep.outputs['X'],mix.inputs[2]);links.new(sep.outputs['Y'],mix.inputs[3])
            vector=nodes.new('ShaderNodeCombineXYZ');links.new(mix.outputs[0],vector.inputs['X']);links.new(sep.outputs['Z'] if kind=='stone' else sep.outputs['Y'],vector.inputs['Y'])
            brick=nodes.new('ShaderNodeTexBrick');links.new(vector.outputs[0],brick.inputs['Vector'])
            brick.inputs['Scale'].default_value=1
            brick.inputs['Brick Width'].default_value=cfg['facade']['stone_course_width_m'] if kind=='stone' else .60
            brick.inputs['Row Height'].default_value=cfg['facade']['stone_course_height_m'] if kind=='stone' else .40
            brick.inputs['Mortar Size'].default_value=cfg['facade']['mortar_width_m'] if kind=='stone' else .004
            brick.inputs['Color1'].default_value=(*(v*.92 for v in color),1)
            brick.inputs['Color2'].default_value=(*color,1)
            brick.inputs['Mortar'].default_value=(*(v*.64 for v in color),1)
            links.new(brick.outputs['Color'],bsdf.inputs['Base Color'])
            joints=nodes.new('ShaderNodeBump');joints.inputs['Strength'].default_value=.25;joints.inputs['Distance'].default_value=.003
            links.new(brick.outputs['Fac'],joints.inputs['Height']);links.new(bump.outputs['Normal'],joints.inputs['Normal']);links.new(joints.outputs['Normal'],bsdf.inputs['Normal'])
    mat['license']='Original procedural material; no external texture'
    return mat

facades={bid:material('Facade '+bid+' mineral finish',color,.64,kind='plaster' if bid=='B' else 'stone') for bid,color in cfg['facade']['finish_colors'].items()}
glass=material('Architectural reflective glazing',(.07,.105,.13),.055,kind='glass')
metal=material('Anodized charcoal frames',(.045,.05,.052),.27,.62)
roof=material('Existing flat roof membrane',(.30,.31,.29),.82,kind='roof')
coping=material('Metal roof edge coping',(.33,.32,.29),.33,.50)
paving=material('Source paving stone',(.46,.46,.42),.75,kind='paving')
asphalt=material('Source road asphalt',(.045,.048,.05),.84,kind='asphalt')
grass=material('Landscape grass',(.11,.16,.065),.9,kind='grass')
background_ground=material('Unmeasured neutral background ground',(.24,.245,.21),.9,kind='soil')
wood=material('Timber bench slats',(.24,.105,.043),.45,kind='bark')
trunk=material('Tree bark',(.14,.075,.035),.82,kind='bark')
for i,color in enumerate([(.038,.10,.018),(.07,.145,.027),(.095,.18,.045)]):material('Leaf tone '+str(i),color,.76)

for obj in original:
    if obj.type!='MESH':continue
    if obj.name.startswith('building:'):
        bid=obj.name.split(':')[-1];mat=facades[bid];obj['owner']=bid;obj['role']='facade'
    elif obj.name.startswith('windows:'):
        bid=obj.name.split(':')[-1];mat=glass;obj['owner']=bid;obj['role']='glazing'
    elif obj.name.startswith('roof:'):
        bid=obj.name.split(':')[-1];mat=roof;obj['owner']=bid;obj['role']='source_roof'
    else:
        mat=asphalt if obj.name=='context:street' else paving if 'apron' in obj.name else background_ground if obj.name=='context:ground' else grass
        obj['owner']='context';obj['role']='source_context'
    obj.data.materials.clear();obj.data.materials.append(mat)
    obj['classification']='source_geometry_unchanged'


def tag(obj,owner,role):
    obj['owner']=owner;obj['role']=role;obj['classification']='synthetic_design_proposal';obj['source_authority']='synthetic configuration; not a verified real-site plan'
    return obj


def box(name,center,size,mat,owner='context',role='detail'):
    if min(size)<=1e-7:raise ValueError('Degenerate detail box: '+name)
    # Direct mesh construction avoids thousands of dependency-graph operator
    # updates while making precisely the same additive rectangular detail.
    x,y,z=(value/2 for value in size)
    vertices=[(-x,-y,-z),(x,-y,-z),(x,y,-z),(-x,y,-z),(-x,-y,z),(x,-y,z),(x,y,z),(-x,y,z)]
    faces=[(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    data=bpy.data.meshes.new(name);data.from_pydata(vertices,[],faces);data.update()
    obj=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(obj);obj.location=center
    obj.data.materials.append(mat);tag(obj,owner,role)
    return obj


def join(parts,name,owner,role,bevel=0):
    if not parts:return None
    bpy.ops.object.select_all(action='DESELECT')
    for obj in parts:obj.select_set(True)
    bpy.context.view_layer.objects.active=parts[0]
    if len(parts)>1:bpy.ops.object.join()
    obj=parts[0];obj.name=name;tag(obj,owner,role)
    if bevel:
        modifier=obj.modifiers.new('Fine manufactured edge bevel','BEVEL');modifier.width=bevel;modifier.segments=2
        modifier.limit_method='ANGLE'
    return obj


def opening_groups(obj):
    vertices=[obj.matrix_world@vertex.co for vertex in obj.data.vertices]
    adjacency={i:set() for i in range(len(vertices))}
    for edge in obj.data.edges:
        a,b=edge.vertices;adjacency[a].add(b);adjacency[b].add(a)
    visited=set();groups=[]
    for i in adjacency:
        if i in visited:continue
        pending=[i];visited.add(i);indices=[]
        while pending:
            point=pending.pop();indices.append(point)
            for other in adjacency[point]-visited:visited.add(other);pending.append(other)
        points=[vertices[index] for index in indices]
        lo=[min(point[k] for point in points) for k in range(3)];hi=[max(point[k] for point in points) for k in range(3)]
        axis=min(range(3),key=lambda k:hi[k]-lo[k])
        if axis==2 or hi[axis]-lo[axis]>1e-4:raise ValueError('Synthetic details require planar vertical source windows')
        groups.append({'lo':lo,'hi':hi,'axis':axis,'center':[(a+b)/2 for a,b in zip(lo,hi)]})
    return groups


def face_box(name,axis,normal,plane,h0,h1,z0,z1,depth,mat,bid,role):
    if h1-h0<1e-6 or z1-z0<1e-6:return None
    horizontal=1-axis
    center=[0,0,(z0+z1)/2];center[axis]=plane+normal*depth/2;center[horizontal]=(h0+h1)/2
    size=[0,0,z1-z0];size[axis]=depth;size[horizontal]=h1-h0
    return box(name,center,size,mat,bid,role)

architectural_report={}
for window in [obj for obj in original if obj.name.startswith('windows:')]:
    bid=window.name.split(':')[-1];body=bpy.data.objects['building:'+bid]
    low,high=source[body.name]['bounds'];groups=opening_groups(window)
    rows=sorted({round(group['center'][2],4) for group in groups})
    body['source_window_rows']=len(rows)
    skins=[];frames=[];sills=[]
    for axis in (0,1):
        horizontal=1-axis
        for normal in (-1,1):
            face=[group for group in groups if group['axis']==axis and (group['center'][axis]<(low[axis]+high[axis])/2)==(normal==-1)]
            if not face:continue
            plane=low[axis] if normal==-1 else high[axis]
            wall_low=low[horizontal];wall_high=high[horizontal]
            face_rows={}
            for group in face:face_rows.setdefault(round(group['center'][2],4),[]).append(group)
            cursor=low[2]
            for row_index,row_key in enumerate(sorted(face_rows)):
                row=sorted(face_rows[row_key],key=lambda group:group['lo'][horizontal])
                lower=min(group['lo'][2] for group in row)-cfg['facade']['opening_margin_m']
                upper=max(group['hi'][2] for group in row)+cfg['facade']['opening_margin_m']
                if lower>cursor:skins.append(face_box(f'skin:{bid}:{axis}:{normal}:band:{row_index}',axis,normal,plane,wall_low,wall_high,cursor,lower,cfg['facade']['skin_depth_m'],facades[bid],bid,'facade'))
                edge=wall_low
                for column,group in enumerate(row):
                    left=group['lo'][horizontal]-cfg['facade']['opening_margin_m'];right=group['hi'][horizontal]+cfg['facade']['opening_margin_m']
                    if left>edge:skins.append(face_box(f'skin:{bid}:{axis}:{normal}:pier:{row_index}:{column}',axis,normal,plane,edge,left,lower,upper,cfg['facade']['skin_depth_m'],facades[bid],bid,'facade'))
                    edge=right
                    lo=group['lo'];hi=group['hi'];frame_width=cfg['facade']['frame_width_m'];depth=cfg['facade']['frame_depth_m']
                    frame_plane=plane+normal*.035
                    # Reveals bridge the old 45 mm glazing plane to the new skin.
                    for vertical_edge in (lo[horizontal]+frame_width/2,hi[horizontal]-frame_width/2):
                        frames.append(face_box('jamb',axis,normal,frame_plane,vertical_edge-frame_width/2,vertical_edge+frame_width/2,lo[2],hi[2],depth,metal,bid,'window_frame'))
                    for z in (lo[2]+frame_width/2,hi[2]-frame_width/2):
                        frames.append(face_box('frame lintel',axis,normal,frame_plane,lo[horizontal],hi[horizontal],z-frame_width/2,z+frame_width/2,depth,metal,bid,'window_frame'))
                    midpoint=(lo[horizontal]+hi[horizontal])/2
                    frames.append(face_box('central mullion',axis,normal,frame_plane,midpoint-.025,midpoint+.025,lo[2],hi[2],depth,metal,bid,'window_frame'))
                    sills.append(face_box('stone sill',axis,normal,plane,lo[horizontal]-.035,hi[horizontal]+.035,lo[2]-.05,lo[2]-.012,cfg['facade']['skin_depth_m']+cfg['facade']['sill_projection_m'],facades[bid],bid,'facade'))
                if edge<wall_high:skins.append(face_box(f'skin:{bid}:{axis}:{normal}:pier-end:{row_index}',axis,normal,plane,edge,wall_high,lower,upper,cfg['facade']['skin_depth_m'],facades[bid],bid,'facade'))
                cursor=upper
            if cursor<high[2]:skins.append(face_box('skin top band',axis,normal,plane,wall_low,wall_high,cursor,high[2],cfg['facade']['skin_depth_m'],facades[bid],bid,'facade'))
    skin=join([part for part in skins if part],'detail:cladding:'+bid,bid,'facade',.002)
    frame=join([part for part in frames if part],'detail:frames:'+bid,bid,'window_frame',.003)
    sill=join([part for part in sills if part],'detail:sills:'+bid,bid,'facade',.002)
    # Roof fascia stays at or below the existing original roof top; no parapet
    # or rooftop story is fabricated above the authority model's height.
    roof_top=source['roof:'+bid]['bounds'][1][2];roof_edges=[]
    for axis in (0,1):
        horizontal=1-axis
        for normal in (-1,1):
            plane=(low[axis] if normal==-1 else high[axis])+normal*cfg['facade']['skin_depth_m']
            roof_edges.append(face_box('roof edge',axis,normal,plane,low[horizontal]-.20,high[horizontal]+.20,roof_top-.20,roof_top,.035,coping,bid,'roof_detail'))
    join(roof_edges,'detail:roof-edge:'+bid,bid,'roof_detail',.002)
    # Deliberate facade composition uses only blank skin between the original
    # openings. Contrast ratios are explicit material metadata, retained when
    # a building-wide finish option is edited later.
    composition=cfg['facade']['composition'];base_color=cfg['facade']['finish_colors'][bid]
    accent=material('Facade '+bid+' charcoal piers',tuple(v*composition['corner_pier_tint_scale'] for v in base_color),.34,.35)
    accent['facade_tint_scale']=composition['corner_pier_tint_scale']
    base=material('Facade '+bid+' dark base plinth',tuple(v*composition['plinth_tint_scale'] for v in base_color),.78,kind='stone')
    base['facade_tint_scale']=composition['plinth_tint_scale']
    bands=material('Facade '+bid+' horizontal stone bands',tuple(v*composition['floor_band_tint_scale'] for v in base_color),.68,kind='stone')
    bands['facade_tint_scale']=composition['floor_band_tint_scale']
    piers=[];plinths=[];ribbons=[]
    for axis in (0,1):
        horizontal=1-axis
        for normal in (-1,1):
            plane=(low[axis] if normal==-1 else high[axis])+normal*cfg['facade']['skin_depth_m']
            for coordinate in (low[horizontal]+composition['corner_pier_inset_m'],high[horizontal]-composition['corner_pier_inset_m']):
                half=composition['corner_pier_width_m']/2
                piers.append(face_box('charcoal corner pier',axis,normal,plane,coordinate-half,coordinate+half,low[2]+composition['plinth_height_m'],high[2]-.12,.03,accent,bid,'facade'))
            plinths.append(face_box('dark base plinth',axis,normal,plane,low[horizontal],high[horizontal],low[2],low[2]+composition['plinth_height_m'],.025,base,bid,'facade'))
            for floor in range(1,len(rows)):
                z=low[2]+(high[2]-low[2])*floor/len(rows)
                ribbons.append(face_box('horizontal floor band',axis,normal,plane,low[horizontal],high[horizontal],z-composition['floor_band_height_m']/2,z+composition['floor_band_height_m']/2,.025,bands,bid,'facade'))
    join(piers,'detail:corner-piers:'+bid,bid,'facade',.003)
    join(plinths,'detail:plinth:'+bid,bid,'facade',.003)
    join(ribbons,'detail:floor-bands:'+bid,bid,'facade',.002)
    architectural_report[bid]={'source_window_rows':len(rows),'source_window_openings':len(groups),'cladding_depth_m':cfg['facade']['skin_depth_m'],'glazing_setback_from_new_skin_m':cfg['facade']['skin_depth_m']-.045,'original_core_bounds':[low,high],'roof_top_max_z':roof_top,'no_added_floors':True,'no_new_balconies':True,'classification':'synthetic additive facade design'}

# Entry forecourts connect existing paths and synthetic door concepts, with
# no changes to any source ground/road mesh or original building position.
for court in cfg['landscape']['forecourts']:
    x0,y0,x1,y1=court['rectangle']
    box('landscape:forecourt:'+court['building'],((x0+x1)/2,(y0+y1)/2,.009),(x1-x0,y1-y0,.018),paving,'context','synthetic_forecourt')

# Existing synthetic pathways remain where they were explicitly designed.
for i,(x,y,width,length) in enumerate(cfg['landscape']['paths']):
    join([box('path',(x,y,.013),(width,length,.018),paving)],'landscape:path:'+str(i),'context','landscape')
for i,(x,y) in enumerate(cfg['landscape']['seating_points']):
    parts=[]
    for j in range(5):parts.append(box('seat slat',(x,y+j*.10,.45),(2.0,.08,.055),wood))
    for xx in (-.75,.75):parts.append(box('bench leg',(x+xx,y+.2,.23),(.055,.43,.44),metal))
    for j in range(4):parts.append(box('back slat',(x,y+.43,.63+j*.07),(2.0,.045,.05),wood))
    join(parts,'landscape:bench:'+str(i),'context','furniture',.003)

for entry in cfg['entrances']:
    bid=entry['building'];low,high=source['building:'+bid]['bounds']
    center=entry['horizontal_center'];front=low[1]-.215
    width=entry['width_m'];height=entry['height_m'];base=low[2]
    entry_parts=[]
    entry_parts.append(box('entry glass',(center,front,base+height/2),(width,.026,height),glass,bid,'entry'))
    for x in (center-width/2-.055,center+width/2+.055):entry_parts.append(box('entry jamb',(x,front-.01,base+height/2),(.11,.09,height+.12),metal,bid,'entry'))
    entry_parts.append(box('entry lintel',(center,front-.01,base+height+.055),(width+.22,.09,.11),metal,bid,'entry'))
    entry_parts.append(box('door central stile',(center,front-.034,base+height/2),(.035,.032,height),metal,bid,'entry'))
    for x in (center-.10,center+.10):entry_parts.append(box('door handle',(x,front-.055,base+1.0),(.018,.045,.35),metal,bid,'entry'))
    projection=entry['canopy_projection_m']
    entry_parts.append(box('entry canopy',(center,front-projection/2,base+height+.25),(width+1.05,projection,.10),metal,bid,'entry'))
    join(entry_parts,'detail:entry:'+bid,bid,'entry',.003)
    box('landscape:entry-plaza:'+bid,(center,front-1.0,.021),(3.0,2.4,.026),paving,'context','landscape')
    # Clearly synthetic design fixtures, not statements about a real entrance.
    architectural_report[bid]['entrance_design']='synthetic surface/door/canopy proposal; no interior or structural assessment'

# Low curb segments delimit the existing street; no road geometry is added.
if 'context:street' in source:
    road_low,road_high=source['context:street']['bounds'];curb_parts=[]
    for y in (road_low[1],road_high[1]):
        for i in range(50):
            curb_parts.append(box('curb stone',(-49+i*2,y+.08,.065),(1.99,.16,.13),paving))
    join(curb_parts,'landscape:existing-road-curbs','context','synthetic_curb',.006)


def beam(name,a,b,radius,mat,owner='context',role='scale_figure'):
    direction=Vector(b)-Vector(a)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12,radius=radius,depth=direction.length,location=(Vector(a)+Vector(b))/2)
    obj=bpy.context.object;obj.name=name;obj.rotation_euler=direction.to_track_quat('Z','Y').to_euler()
    obj.data.materials.append(mat);tag(obj,owner,role)
    for polygon in obj.data.polygons:polygon.use_smooth=True
    return obj


def ellipsoid(name,center,size,mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,radius=1,location=center)
    obj=bpy.context.object;obj.name=name;obj.scale=size;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    obj.data.materials.append(mat);tag(obj,'context','scale_figure')
    for polygon in obj.data.polygons:polygon.use_smooth=True
    return obj

skin_tone=material('Synthetic figure skin tone',(.36,.23,.15),.68)
trousers=material('Scale figure trousers',(.055,.055,.065),.75)
shoes=material('Scale figure shoes',(.025,.02,.018),.5)
hair=material('Scale figure hair',(.035,.025,.018),.8)
for index,record in enumerate(cfg['people']):
    x,y=record['position'];coat=material('Scale figure coat '+str(index),record['coat'],.8)
    parts=[];stride=.14 if record['pose']=='walking' else .025
    parts.append(ellipsoid('torso',(x,y,1.12),(.19,.115,.30),coat))
    parts.append(ellipsoid('hips',(x,y,.84),(.16,.095,.13),trousers))
    parts.append(ellipsoid('head',(x,y,1.61),(.085,.08,.105),skin_tone))
    parts.append(ellipsoid('hair',(x,y+.017,1.658),(.085,.075,.07),hair))
    parts.append(ellipsoid('nose',(x,y-.083,1.61),(.015,.023,.021),skin_tone))
    for side in (-1,1):parts.append(ellipsoid('ear',(x+side*.087,y,1.614),(.016,.012,.025),skin_tone))
    for side in (-1,1):parts.append(ellipsoid('eye',(x+side*.030,y-.075,1.635),(.010,.006,.004),hair))
    for z in (1.00,1.10,1.20):parts.append(ellipsoid('coat button',(x,y-.114,z),(.009,.006,.009),metal))
    parts.append(beam('neck',(x,y,1.41),(x,y,1.53),.038,skin_tone))
    for side in (-1,1):
        hip=(x+side*.082,y,.86);knee=(x+side*.078,y+side*stride,.44);ankle=(x+side*.075,y+side*stride,.105)
        parts.extend([beam('upper leg',hip,knee,.063,trousers),beam('lower leg',knee,ankle,.045,trousers),ellipsoid('shoe',(ankle[0],ankle[1]-.03,.065),(.058,.115,.05),shoes)])
        shoulder=(x+side*.195,y,1.33);elbow=(x+side*.22,y-side*stride*.5,1.12);hand=(x+side*.205,y-side*stride*.7,.94)
        parts.extend([beam('upper arm',shoulder,elbow,.045,coat),beam('lower arm',elbow,hand,.032,coat),ellipsoid('hand',hand,(.035,.03,.047),skin_tone)])
    person=join(parts,'context:scale-person:'+str(index),'context','scale_figure')
    # Join keeps geometry at the first mesh origin; rotate around feet with
    # an explicit parent-free world matrix rather than around that torso.
    from mathutils import Matrix
    pivot=Vector((x,y,0));rotation=Matrix.Rotation(record['yaw'],4,'Z')
    person.matrix_world=Matrix.Translation(pivot)@rotation@Matrix.Translation(-pivot)@person.matrix_world
    person['representation']='Original procedural adult scale mannequin; not a photoreal person or actual site occupant'

world=bpy.data.worlds.new('V3 natural daylight')
bpy.context.scene.world=world;world.use_nodes=True
sky=world.node_tree.nodes.new('ShaderNodeTexSky');sky.sky_type='NISHITA'
sky.sun_elevation=math.radians(cfg['lighting']['sun_elevation_degrees']);sky.sun_rotation=math.radians(cfg['lighting']['sun_azimuth_degrees']);sky.sun_disc=False
world.node_tree.links.new(sky.outputs['Color'],world.node_tree.nodes['Background'].inputs['Color'])
world.node_tree.nodes['Background'].inputs['Strength'].default_value=cfg['lighting']['sky_strength']
for obj in list(bpy.data.objects):
    if obj.type in ('LIGHT','CAMERA'):bpy.data.objects.remove(obj,do_unlink=True)
bpy.ops.object.light_add(type='SUN');sun=bpy.context.object;sun.name='lighting:sun'
sun.data.energy=cfg['lighting']['sun_energy'];sun.data.angle=math.radians(cfg['lighting']['sun_angle_degrees'])
el=math.radians(cfg['lighting']['sun_elevation_degrees']);az=math.radians(cfg['lighting']['sun_azimuth_degrees'])
sun.rotation_euler=Vector((-math.cos(az)*math.cos(el),-math.sin(az)*math.cos(el),-math.sin(el))).to_track_quat('-Z','Y').to_euler()
sun['owner']='context';sun['role']='lighting'


def source_ground_z(x,y):
    hits=[];origin=Vector((x,y,1000));direction=Vector((0,0,-1))
    for name in source:
        obj=bpy.data.objects[name]
        if not obj.name.startswith('context:'):continue
        obj.data.calc_loop_triangles()
        vertices=[obj.matrix_world@vertex.co for vertex in obj.data.vertices]
        for triangle in obj.data.loop_triangles:
            hit=intersect_ray_tri(*(vertices[index] for index in triangle.vertices),direction,origin,True)
            if hit is not None:hits.append(hit.z)
    if not hits:raise ValueError('Street camera has no source ground surface under its XY')
    return max(hits)

scene=bpy.context.scene;capabilities=configure_render(scene,cfg)
cameras={}
for name,record in cfg['cameras'].items():
    position=list(record['position']);ground=None
    if 'height_above_source_ground_m' in record:
        ground=source_ground_z(position[0],position[1]);position[2]=ground+record['height_above_source_ground_m']
    bpy.ops.object.camera_add(location=position);camera=bpy.context.object;camera.name='camera:'+name
    camera.rotation_euler=(Vector(record['target'])-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='PERSP';camera.data.lens=record['lens_mm'];camera.data.sensor_fit=record['sensor_fit'];camera.data.sensor_height=record['sensor_height_mm'];camera.data.clip_start=.05;camera.data.clip_end=2000
    camera['owner']='context';camera['role']='camera';camera['saved_target']=record['target']
    if ground is not None:camera['source_ground_z']=ground;camera['height_above_source_ground_m']=record['height_above_source_ground_m']
    cameras[name]=camera
bpy.context.view_layer.update()
changed=[name for name,record in source.items() if geometry(bpy.data.objects[name])!=record['geometry']]
assert not changed,changed
camera_records={name:{**camera_record(camera),'target':list(camera['saved_target'])} for name,camera in cameras.items()}
for name,record in camera_records.items():
    if name=='street':assert abs(record['position'][2]-record['ground_z']-1.7)<1e-6
report={'synthetic':True,'real_site_accuracy':False,'source_model':cfg['source_model'],'source_meshes_preserved':len(source),'source_geometry_changed':changed,'source_meshes':source,'buildings':architectural_report,'cameras':camera_records,'render':capabilities,'gate':site_gate(cfg),'balconies_added':0,'new_roads':0,'conceptual_entrances':len(cfg['entrances']),'procedural_scale_people':len(cfg['people']),'flat_terrain_unmeasured':True,'architectural_quality_requires_visual_review':True,'limitations':['All facade details, entrances, planting and people are synthetic proposals, not recovered planning data','Windows remain at source positions; reflective glazing has source opaque wall backing, no reconstructed interior','Roof edges remain within original source maximum Z; no unsupported parapet or rooftop story','Procedural PBR textures are native-Blender shader approximations; GLB uses constant PBR factors']}
(out/'geometry-report.json').write_text(json.dumps(report,indent=2))
scene.camera=cameras['aerial'];scene['classification']='synthetic_architectural_proposal_not_real_site';scene['source_model']=cfg['source_model']
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'))
export_portable(scene,out/'model.glb')
print('V3 SOURCE GEOMETRY PRESERVED; ARCHITECTURAL DETAIL SCENE BUILT')
