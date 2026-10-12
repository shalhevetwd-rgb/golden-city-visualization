"""Render saved scene after a renderer-only fallback, preserving imported meshes."""
import bpy,json,sys,hashlib
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import compare_snapshots,site_gate
out=ROOT/'outputs/v2';cfg=json.loads((ROOT/'config/synthetic-design.json').read_text());assert cfg.get('synthetic') is True and site_gate(cfg)['final_render_allowed'],'Unverified real-site render blocked';bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'));scene=bpy.context.scene;scene.cycles.use_denoising=False;scene.cycles.samples=128
for name,r in cfg['cameras'].items():
 cam=bpy.data.objects['camera:'+name];cam.location=r['position'];cam.rotation_euler=(Vector(r['target'])-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.lens=r['lens_mm']
# Preserve material definitions and world/light state in targeted edit checks.
def snapshot():
 result={}
 for o in bpy.data.objects:
  geom=None
  if o.type=='MESH':geom=hashlib.sha256(json.dumps({'v':[list(v.co) for v in o.data.vertices],'f':[list(p.vertices) for p in o.data.polygons],'m':[list(row) for row in o.matrix_world]},sort_keys=True).encode()).hexdigest()
  materials=[]
  if o.type=='MESH':
   for m in o.data.materials:materials.append({'name':m.name,'nodes':[(n.bl_idname,[(i.name,str(i.default_value)) for i in n.inputs if hasattr(i,'default_value')]) for n in m.node_tree.nodes]})
  result[o.name]={'owner':o.get('owner','context'),'geometry':geom,'materials':materials,'transform':[list(r) for r in o.matrix_world],'light':{'energy':o.data.energy,'color':list(o.data.color)} if o.type=='LIGHT' else None}
 return result
bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'));bpy.ops.export_scene.gltf(filepath=str(out/'model.glb'),export_format='GLB',export_extras=True)
for name in cfg['cameras']:
 scene.camera=bpy.data.objects['camera:'+name];scene.render.filepath=str(out/(name+'.png'));bpy.ops.render.render(write_still=True)
if '--views-only' in sys.argv:sys.exit(0)
before=snapshot();target=cfg['material_edit_test']['target'];o=bpy.data.objects['building:'+target];original=o.data.materials[0];m=original.copy();m.name='Targeted facade finish '+target;color=cfg['material_edit_test']['color'];m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(*color,1)
for n in m.node_tree.nodes:
 if n.type=='VALTORGB':n.color_ramp.elements[0].color=(*(v*.86 for v in color),1);n.color_ramp.elements[1].color=(*color,1)
o.data.materials[0]=m;after=snapshot();qa=compare_snapshots(before,after,target);assert qa['passed'];(out/'edit-report.json').write_text(json.dumps(qa,indent=2));(out/'scene-snapshots.json').write_text(json.dumps({'before':before,'after':after},indent=2));scene.camera=bpy.data.objects['camera:aerial'];scene.render.filepath=str(out/'edited-aerial.png');bpy.ops.render.render(write_still=True);bpy.ops.wm.save_as_mainfile(filepath=str(out/'edited-model.blend'));bpy.ops.export_scene.gltf(filepath=str(out/'edited-model.glb'),export_format='GLB',export_extras=True);print('SAVED SCENE RENDER COMPLETE')
