"""Clone and edit only material slots belonging to the selected facade owner."""
import json
import sys
import time
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import compare_snapshots,site_gate
from scene_audit import snapshot,apply_building_facade_color
from render_context import load_context,configure_render,export_portable,preflight_source_geometry
cfg,out=load_context();assert cfg['synthetic'] is True and site_gate(cfg)['final_render_allowed']
bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'));scene=bpy.context.scene
preflight=preflight_source_geometry(scene,out)
configure_render(scene,cfg);scene.camera=bpy.data.objects['camera:aerial'];bpy.context.view_layer.update()
print('AUDITING FULL BASELINE SCENE BEFORE TARGETED EDIT',flush=True)
audit_started=time.monotonic();before=snapshot();before_seconds=time.monotonic()-audit_started
print('FULL BASELINE SNAPSHOT SECONDS',round(before_seconds,3),flush=True)
target=cfg['material_edit_test']['target']
eligible=sorted(obj.name for obj in bpy.data.objects if obj.type=='MESH' and obj.get('owner')==target and obj.get('role')=='facade')
assert eligible,'No owned facade objects were selected'
changed=apply_building_facade_color(target,cfg['material_edit_test']['color'])
print('RECOMPUTING FULL SCENE AFTER TARGETED EDIT',target,eligible,flush=True)
audit_started=time.monotonic();after=snapshot();after_seconds=time.monotonic()-audit_started
print('FULL EDITED SNAPSHOT SECONDS',round(after_seconds,3),flush=True)
qa=compare_snapshots(before,after,target)
assert qa['passed'],qa
assert sorted(qa['changed_objects'])==eligible,(qa,eligible)
print('TARGETED EDIT SCENE AUDIT PASSED',flush=True)
qa.update(target_building=target,eligible_facade_objects=eligible,unchanged_objects=sorted(set(before)-set(eligible)),actual_connected_shader_edit=True,synthetic=True,source_geometry_pre_render_check=preflight)
qa['snapshot_seconds']={'before':round(before_seconds,3),'after':round(after_seconds,3)}
(out/'edit-report.json').write_text(json.dumps(qa,indent=2))
(out/'scene-snapshots.json').write_text(json.dumps({'before':before,'after':after},indent=2))
bpy.context.preferences.filepaths.save_version=0;bpy.ops.wm.save_as_mainfile(filepath=str(out/'edited-model.blend'))
portable=export_portable(scene,out/'edited-model.glb')
qa['material_export_verified']=portable['passed'] and portable['all_primitive_factors_verified']
qa['portable_material_node_count']=portable['mesh_nodes_checked']
portable_report=json.loads((out/'portable-materials-report.json').read_text()) if (out/'portable-materials-report.json').is_file() else {}
portable_report['edited-model.glb']=portable
(out/'portable-materials-report.json').write_text(json.dumps(portable_report,indent=2))
scene.render.filepath=str(out/'edited-aerial.png');started=time.monotonic();bpy.ops.render.render(write_still=True)
qa['render_seconds']=round(time.monotonic()-started,3)
(out/'edit-report.json').write_text(json.dumps(qa,indent=2))
print('OWNED FACADE EDIT VERIFIED AND RENDERED',target,eligible,flush=True)
