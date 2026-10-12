"""Procedural synthetic vegetation detail, confined to the original test parcel."""
import bpy,random,math,json
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'outputs/v2';cfg=json.loads((ROOT/'config/synthetic-design.json').read_text());bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'));random.seed(cfg['seed'])
leaves=[bpy.data.materials.get('Leaf tone '+str(i)) for i in range(3)]
for m in leaves:
 m.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value=.8
for o in list(bpy.data.objects):
 if o.name.startswith('landscape:tree:'):bpy.data.objects.remove(o,do_unlink=True)
for i,(x,y) in enumerate(cfg['landscape']['tree_points']):
 height=5.6+(i%3)*.3;parts=[]
 def branch(start,end,radius):
  d=Vector(end)-Vector(start);bpy.ops.mesh.primitive_cylinder_add(vertices=9,radius=radius,depth=d.length,location=(Vector(start)+Vector(end))/2);o=bpy.context.object;o.rotation_euler=d.to_track_quat('Z','Y').to_euler();o.data.materials.append(bpy.data.materials['Tree bark']);parts.append(o)
 branch((x,y,0),(x,y,height*.77),.12)
 centers=[]
 for j in range(14):
  a=j*2.399963;rad=1.1+(j%3)*.30;zz=height*.64+random.random()*height*.26;end=(x+math.cos(a)*rad,y+math.sin(a)*rad,zz);centers.append(end);branch((x,y,height*.48),end,.035)
 vertices=[];faces=[];colors=[]
 for j in range(2400):
  c=Vector(random.choice(centers));c+=Vector((random.uniform(-.72,.72),random.uniform(-.72,.72),random.uniform(-.68,.68)));angle=random.random()*math.tau;tilt=random.uniform(-.6,.6);u=Vector((math.cos(angle),math.sin(angle),tilt)).normalized();v=u.cross(Vector((0,0,1))).normalized();length=random.uniform(.12,.23);width=random.uniform(.05,.10);start=len(vertices)
  for k in range(8):
   a=k*math.tau/8;vertices.append(tuple(c+u*math.cos(a)*length/2+v*math.sin(a)*width/2))
  faces.append(tuple(range(start,start+8)));colors.append(random.randrange(3))
 mesh=bpy.data.meshes.new('Leaf blades');mesh.from_pydata(vertices,[],faces);mesh.materials.clear()
 for m in leaves:mesh.materials.append(m)
 for poly,col in zip(mesh.polygons,colors):poly.material_index=col
 leaf=bpy.data.objects.new('Leaf canopy',mesh);bpy.context.collection.objects.link(leaf);parts.append(leaf);bpy.ops.object.select_all(action='DESELECT')
 for o in parts:o.select_set(True)
 bpy.context.view_layer.objects.active=leaf;bpy.ops.object.join();o=bpy.context.object;o.name='landscape:tree:'+str(i);o['owner']='context';o['classification']='synthetic_design_proposal'
bpy.context.scene.cycles.use_adaptive_sampling=True;bpy.context.scene.cycles.adaptive_threshold=.025;bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'));print('Vegetation refined; original source meshes unchanged')
