"""Blender enhancement of the existing imported GLB, not a replacement model."""
import bpy,json,sys,math,hashlib,random,os
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import site_gate,compare_snapshots
cfg=json.loads((ROOT/'config/synthetic-design.json').read_text());out=ROOT/'outputs/v2';out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(ROOT/cfg['source_model']))
assert site_gate(cfg)['final_render_allowed'],'Missing verified site evidence'
assert cfg.get('synthetic') is True,'Production site workflow has not been validated; use the synthetic exercise only'
random.seed(cfg['seed']);original=list(bpy.data.objects)

def bounds(o):
 ps=[o.matrix_world@Vector(v) for v in o.bound_box];return [[min(p[i] for p in ps) for i in range(3)],[max(p[i] for p in ps) for i in range(3)]]

def geometry(o):
 if o.type!='MESH':return None
 raw={'verts':[list(v.co) for v in o.data.vertices],'faces':[list(f.vertices) for f in o.data.polygons],'matrix':[list(r) for r in o.matrix_world]};return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
source={o.name:{'geometry':geometry(o),'bounds':bounds(o)} for o in original if o.type=='MESH'}

def material(name,color,rough=.5,metal=0,glass=False,stone=False):
 m=bpy.data.materials.new(name);m.use_nodes=True;n=m.node_tree.nodes;p=n.get('Principled BSDF');p.inputs['Base Color'].default_value=(*color,1);p.inputs['Roughness'].default_value=rough;p.inputs['Metallic'].default_value=metal
 if glass:p.inputs['Transmission Weight'].default_value=.25;p.inputs['IOR'].default_value=1.46
 if stone:
  tex=n.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=32;tex.inputs['Detail'].default_value=3
  ramp=n.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].color=(*(v*.86 for v in color),1);ramp.color_ramp.elements[1].color=(*color,1)
  m.node_tree.links.new(tex.outputs['Fac'],ramp.inputs[0]);m.node_tree.links.new(ramp.outputs['Color'],p.inputs['Base Color'])
  bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.22;bump.inputs['Distance'].default_value=.025;m.node_tree.links.new(tex.outputs['Fac'],bump.inputs['Height']);m.node_tree.links.new(bump.outputs['Normal'],p.inputs['Normal'])
 return m
stone=material('Warm limestone, procedural',(0.72,.66,.54),stone=True);plaster=material('Ivory mineral plaster',(.79,.79,.72),stone=True);stone_c=material('Light limestone',(.69,.71,.67),stone=True);glass=material('Reflective glazing',(.13,.21,.26),.15,.48,True);metal=material('Bronze anodized frames',(.20,.13,.075),.25,.8);roof=material('Roof coping',(.43,.43,.39),.5);grass=material('Landscape grass',(.14,.24,.08),stone=True);asphalt=material('Source street asphalt',(.10,.105,.11),stone=True);paving=material('Source apron stone',(.53,.52,.46),stone=True);wood=material('Timber furniture',(.25,.10,.035),stone=True);trunk=material('Tree bark',(.16,.095,.055),stone=True)
for o in original:
 if o.type!='MESH':continue
 if o.name.startswith('building:'):m={'stone':stone,'plaster':plaster,'stone_light':stone_c}[cfg.get('finishes',{}).get(o.get('building_id',o.name.split(':')[-1]),'stone')]
 elif o.name.startswith('windows:'):m=glass
 elif o.name.startswith('roof:'):m=roof
 elif 'apron' in o.name:m=paving
 elif o.name=='context:street':m=asphalt
 else:m=grass
 o.data.materials.clear();o.data.materials.append(m);o['owner']=o.name.split(':')[-1] if o.name.startswith(('building:','windows:','roof:')) else 'context';o['classification']='source_geometry_unchanged'

def box(name,center,size,mat,owner='context'):
 bpy.ops.mesh.primitive_cube_add(size=1,location=center);o=bpy.context.object;o.name=name;o.dimensions=size;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(mat);o['owner']=owner;o['classification']='synthetic_design_detail';return o

def join(objects,name,owner):
 bpy.ops.object.select_all(action='DESELECT')
 for o in objects:o.select_set(True)
 bpy.context.view_layer.objects.active=objects[0];bpy.ops.object.join();o=bpy.context.object;o.name=name;o['owner']=owner;return o

# Window meshes consist of source quads (triangulated on glTF export).
for o in [v for v in original if v.name.startswith('windows:')]:
 bid=o.name.split(':')[-1];frames=[];verts=[o.matrix_world@v.co for v in o.data.vertices]
 adjacency={i:set() for i in range(len(verts))}
 for edge in o.data.edges:a,b=edge.vertices;adjacency[a].add(b);adjacency[b].add(a)
 visited=set();groups=[]
 for i in adjacency:
  if i in visited:continue
  stack=[i];group=[];visited.add(i)
  while stack:
   a=stack.pop();group.append(verts[a])
   for b in adjacency[a]-visited:visited.add(b);stack.append(b)
  groups.append(group)
 for number,pts in enumerate(groups):
  lo=[min(v[k] for v in pts) for k in range(3)];hi=[max(v[k] for v in pts) for k in range(3)];axis=min(range(3),key=lambda k:hi[k]-lo[k]);horizontal=1-axis
  if hi[axis]-lo[axis]>0.0001:continue # Nonplanar/rotated opening details require a source-specific design.
  if axis==2:continue
  normal=-1 if (lo[axis]+hi[axis])/2<(source['building:'+bid]['bounds'][0][axis]+source['building:'+bid]['bounds'][1][axis])/2 else 1
  mid=[(a+b)/2 for a,b in zip(lo,hi)];mid[axis]-=normal*.015
  for k in [horizontal,2]:
   for side in [lo[k]+.045,hi[k]-.045]:
    c=list(mid);c[k]=side;sz=[.03,.03,.03];sz[horizontal]=hi[horizontal]-lo[horizontal] if k==2 else .09;sz[2]=.09 if k==2 else hi[2]-lo[2];sz[axis]=.03;frames.append(box(f'frame:{bid}:{number}:{k}:{side}',c,sz,metal,bid))
  sz=[.03,.03,.03];sz[axis]=.025;sz[horizontal]=.06;sz[2]=hi[2]-lo[2];frames.append(box(f'mullion:{bid}:{number}',mid,sz,metal,bid))
 join(frames,'detail:frames:'+bid,bid)
 # Existing source row heights, not a new floor count.
 levels=sorted({round((min(v.z for v in group)+max(v.z for v in group))/2,3) for group in groups})
 bpy.data.objects['building:'+bid]['source_window_rows']=len(levels)

# No extra roads or public space: landscaping only inside the synthetic parcel.
for i,(x,y,w,l) in enumerate(cfg['landscape']['paths']):box('landscape:path:'+str(i),(x,y,.013),(w,l,.018),paving)
for i,(x,y) in enumerate(cfg['landscape']['seating_points']):
 for j in range(5):box(f'bench:{i}:slat:{j}',(x,y+j*.10,.45),(2.0,.08,.055),wood)
 for xx in [-.75,.75]:box(f'bench:{i}:leg:{xx}',(x+xx,y+.2,.23),(.08,.45,.44),metal)
leaves=[material('Leaf tone '+str(i),c,.8) for i,c in enumerate([(.09,.19,.04),(.16,.27,.065),(.22,.34,.09)])]
for i,(x,y) in enumerate(cfg['landscape']['tree_points']):
 height=random.uniform(4.4,6.3);bpy.ops.mesh.primitive_cylinder_add(vertices=10,radius=.12,depth=height*.65,location=(x,y,height*.325));t=bpy.context.object;t.name=f'tree:{i}:trunk';t.data.materials.append(trunk);t['owner']='context';parts=[t]
 for j in range(36):
  angle=random.random()*math.tau;rad=random.random()*1.55;z=height*.60+random.random()*height*.40;bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,radius=1,location=(x+math.cos(angle)*rad,y+math.sin(angle)*rad,z));l=bpy.context.object;l.scale=(.58,.48,.57);l.data.materials.append(random.choice(leaves));parts.append(l)
 join(parts,f'landscape:tree:{i}','context')

# Sky/sun consistent across cameras; no image-generating service used.
world=bpy.data.worlds.new('Natural sky');bpy.context.scene.world=world;world.use_nodes=True;sky=world.node_tree.nodes.new('ShaderNodeTexSky');sky.sky_type='NISHITA';sky.sun_elevation=math.radians(38);sky.sun_rotation=math.radians(130);world.node_tree.links.new(sky.outputs['Color'],world.node_tree.nodes['Background'].inputs['Color']);world.node_tree.nodes['Background'].inputs['Strength'].default_value=.35
for o in list(bpy.data.objects):
 if o.type in ['LIGHT','CAMERA']:bpy.data.objects.remove(o,do_unlink=True)
bpy.ops.object.light_add(type='SUN',location=(0,0,80));sun=bpy.context.object;sun.name='lighting:sun';sun.data.energy=2;sun.data.angle=.10;sun.rotation_euler=(math.radians(30),math.radians(-25),math.radians(-35))
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=128;scene.cycles.use_denoising=False;scene.render.resolution_x=1600;scene.render.resolution_y=1000;scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.view_settings.view_transform='AgX';scene.render.film_transparent=False;scene.cycles.seed=29
cams={}
for name,record in cfg['cameras'].items():
 bpy.ops.object.camera_add(location=record['position']);cam=bpy.context.object;cam.name='camera:'+name;direction=Vector(record['target'])-cam.location;cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler();cam.data.lens=record['lens_mm'];cam.data.clip_end=2000;cams[name]=cam

def material_fingerprint(m):
 return {'name':m.name,'nodes':[(n.bl_idname,n.name,[(i.name,str(i.default_value)) for i in n.inputs if hasattr(i,'default_value')]) for n in m.node_tree.nodes] if m.use_nodes else list(m.diffuse_color)}
def snapshot():
 return {o.name:{'owner':o.get('owner','context'),'geometry':geometry(o),'materials':[material_fingerprint(m) for m in o.data.materials] if o.type=='MESH' else [],'transform':[list(r) for r in o.matrix_world]} for o in bpy.data.objects}
changed=[n for n,v in source.items() if geometry(bpy.data.objects[n])!=v['geometry']];assert not changed
qa={'synthetic':True,'real_site_accuracy':False,'source_meshes_preserved':len(source),'source_geometry_changed':changed,'buildings':{n:{'bounds':v['bounds'],'source_window_rows':bpy.data.objects[n].get('source_window_rows')} for n,v in source.items() if n.startswith('building:')},'gate':site_gate(cfg),'balconies_added':0,'new_roads':0,'landscaping_status':cfg['landscape']['status']}
(out/'geometry-report.json').write_text(json.dumps(qa,indent=2));scene.camera=cams['aerial'];bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'));bpy.ops.export_scene.gltf(filepath=str(out/'model.glb'),export_format='GLB',export_extras=True)
if '--build-only' in sys.argv:sys.exit(0)
for name,cam in cams.items():
 scene.camera=cam;scene.render.filepath=str(out/(name+'.png'));bpy.ops.render.render(write_still=True)
before=snapshot();edit_target=cfg['material_edit_test']['target'];target=bpy.data.objects['building:'+edit_target];new=material('Targeted mineral finish '+edit_target,cfg['material_edit_test']['color'],stone=True);target.data.materials[0]=new;after=snapshot();editqa=compare_snapshots(before,after,edit_target);assert editqa['passed'];(out/'edit-report.json').write_text(json.dumps(editqa,indent=2));scene.camera=cams['aerial'];scene.render.filepath=str(out/'edited-aerial.png');bpy.ops.render.render(write_still=True);bpy.ops.wm.save_as_mainfile(filepath=str(out/'edited-model.blend'));bpy.ops.export_scene.gltf(filepath=str(out/'edited-model.glb'),export_format='GLB',export_extras=True)
(out/'scene-snapshots.json').write_text(json.dumps({'before':before,'after':after},indent=2));print('RENDER AND GEOMETRY QA COMPLETE')
