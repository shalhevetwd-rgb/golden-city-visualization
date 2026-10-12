"""Render the saved architectural v3 scene with all three persisted cameras."""
import json
import sys
import time
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import site_gate
from render_context import load_context,configure_render,export_portable,camera_record,preflight_source_geometry,refresh_saved_cameras
cfg,out=load_context();assert site_gate(cfg)['final_render_allowed'] and cfg['synthetic'] is True
bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'));scene=bpy.context.scene
preflight=preflight_source_geometry(scene,out)
capabilities=configure_render(scene,cfg)
refresh_saved_cameras(scene,cfg)
scene.camera=bpy.data.objects['camera:aerial'];bpy.context.view_layer.update()
geometry=json.loads((out/'geometry-report.json').read_text())
geometry['cameras']={name:{**camera_record(scene.objects['camera:'+name]),'target':record['target']} for name,record in cfg['cameras'].items()}
geometry['render']=capabilities;geometry['saved_scene_pre_render_check']=preflight
(out/'geometry-report.json').write_text(json.dumps(geometry,indent=2))
(out/'design-config.json').write_text(json.dumps(cfg,indent=2))
(out/'cameras.json').write_text(json.dumps(geometry['cameras'],indent=2))
(out/'render-settings.json').write_text(json.dumps(capabilities,indent=2))
bpy.context.preferences.filepaths.save_version=0;bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'))
portable=export_portable(scene,out/'model.glb')
(out/'portable-materials-report.json').write_text(json.dumps({'model.glb':portable},indent=2))
views={}
for name in cfg['cameras']:
    camera=bpy.data.objects['camera:'+name];scene.camera=camera;bpy.context.view_layer.update()
    scene.render.filepath=str(out/(name+'.png'));started=time.monotonic()
    bpy.ops.render.render(write_still=True)
    views[name]={'file':name+'.png','camera':camera_record(camera),'seconds':round(time.monotonic()-started,3),'render':capabilities}
    (out/'render-report.json').write_text(json.dumps({'synthetic':True,'views':views,'source_geometry_pre_render_check':preflight,'professional_photorealism_claim':False},indent=2))
    print('V3 VIEW COMPLETE',name,round(time.monotonic()-started,2),flush=True)
print('SAVED ARCHITECTURAL SCENE THREE-VIEW RENDER COMPLETE')
