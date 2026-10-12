"""Reopen source, native scene and exported GLB in Blender, compare world geometry."""
import bpy,json,hashlib
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'outputs/v2'
def signatures():
 result={}
 for o in bpy.data.objects:
  if o.type!='MESH':continue
  vertices=[tuple(round(x,5) for x in o.matrix_world@v.co) for v in o.data.vertices]
  triangles=[];o.data.calc_loop_triangles()
  for tri in o.data.loop_triangles:triangles.append(tuple(sorted(vertices[i] for i in tri.vertices)))
  result[o.name]={'geometry':hashlib.sha256(repr(sorted(triangles)).encode()).hexdigest(),'bounds':[[min(v[i] for v in vertices) for i in range(3)],[max(v[i] for v in vertices) for i in range(3)]]}
 return result
bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(ROOT/'baseline/aerial.glb'));original=signatures()
reports={}
for name in ['model.blend','model.glb','edited-model.blend','edited-model.glb']:
 if not (out/name).exists():continue
 if name.endswith('.blend'):bpy.ops.wm.open_mainfile(filepath=str(out/name))
 else:bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(out/name))
 current=signatures();diff=[n for n,r in original.items() if current.get(n)!=r];reports[name]={'source_objects_checked':len(original),'source_geometry_changed':diff,'passed':not diff};assert not diff,(name,diff)
(out/'roundtrip-geometry.json').write_text(json.dumps(reports,indent=2));print(json.dumps(reports,indent=2))
