"""Persistent building-only material edit. Run in Blender, with a signed source model."""
import bpy,json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import compare_snapshots
args=sys.argv[sys.argv.index('--')+1:];model,request,out=map(Path,args);r=json.loads(request.read_text());assert hashlib.sha256(model.read_bytes()).hexdigest()==r['source_sha256'],'Edit source version mismatch'
assert r['operation']=='facade_color','Only facade material editing is proven';assert len(r['color'])==3 and all(0<=v<=1 for v in r['color'])
bpy.ops.wm.open_mainfile(filepath=str(model));name='building:'+r['building_id'];target=bpy.data.objects.get(name);assert target is not None and target.type=='MESH'
from scene_audit import snapshot as snap,apply_facade_color
before=snap();apply_facade_color(target,r['color']);qa=compare_snapshots(before,snap(),r['building_id']);assert qa['passed'];out.mkdir(parents=True,exist_ok=True);(out/'edit-check.json').write_text(json.dumps(qa,indent=2));bpy.ops.wm.save_as_mainfile(filepath=str(out/'edited.blend'));bpy.ops.export_scene.gltf(filepath=str(out/'edited.glb'),export_format='GLB',export_extras=True)
# Re-render is required for saved image changes; a pixel-only patch is not promised.
