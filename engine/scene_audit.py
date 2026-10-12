"""Actual Blender geometry, shaders and surroundings fingerprints for editing."""
import bpy,json,hashlib

def shader(m):
 nodes=[]
 if not m.use_nodes:return {'diffuse':list(m.diffuse_color)}
 for n in m.node_tree.nodes:
  inputs=[]
  for i in n.inputs:
   if not hasattr(i,'default_value'):continue
   v=i.default_value
   try:v=list(v)
   except TypeError:pass
   if not isinstance(v,(list,str,int,float,bool,type(None))):v=str(v)
   inputs.append([i.name,v])
  nodes.append([n.name,n.bl_idname,inputs])
 links=[(l.from_node.name,l.from_socket.name,l.to_node.name,l.to_socket.name) for l in m.node_tree.links]
 return {'name':m.name,'nodes':nodes,'links':links}

def snapshot():
 out={}
 for o in bpy.data.objects:
  geom=None
  if o.type=='MESH':geom=hashlib.sha256(json.dumps({'v':[list(v.co) for v in o.data.vertices],'f':[list(p.vertices) for p in o.data.polygons]},sort_keys=True).encode()).hexdigest()
  out[o.name]={'owner':o.get('owner','context'),'geometry':geom,'materials':[shader(m) for m in o.data.materials] if o.type=='MESH' else [],'transform':[list(r) for r in o.matrix_world],'light':{'energy':o.data.energy,'color':list(o.data.color)} if o.type=='LIGHT' else None,'camera':{'lens':o.data.lens,'sensor_width':o.data.sensor_width,'clip_start':o.data.clip_start,'clip_end':o.data.clip_end} if o.type=='CAMERA' else None}
 out['__world__']={'owner':'context','geometry':None,'shader':shader(bpy.context.scene.world),'exposure':bpy.context.scene.view_settings.exposure}
 return out

def apply_facade_color(target,color):
 mat=target.data.materials[0].copy();mat.name='Targeted facade finish '+target.get('building_id',target.name);mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(*color,1)
 for n in mat.node_tree.nodes:
  if n.type=='VALTORGB':n.color_ramp.elements[0].color=(*(v*.86 for v in color),1);n.color_ramp.elements[1].color=(*color,1)
  if n.type=='TEX_BRICK':
   n.inputs['Color1'].default_value=(*(v*.8 for v in color),1);n.inputs['Color2'].default_value=(*color,1);n.inputs['Mortar'].default_value=(*(v*.45 for v in color),1)
 target.data.materials[0]=mat
