"""Idempotent daylight/render calibration; never changes source mesh data."""
import json
import math
import sys
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from render_context import load_context,configure_render
cfg,out=load_context();bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'))
scene=bpy.context.scene;capabilities=configure_render(scene,cfg)
scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=cfg['lighting']['sky_strength']
for node in scene.world.node_tree.nodes:
    if node.type=='TEX_SKY':node.sun_disc=False
lights=[obj for obj in scene.objects if obj.type=='LIGHT']
assert len(lights)==1 and lights[0].data.type=='SUN','The synthetic daylight design requires one consistent sun'
lights[0].data.energy=cfg['lighting']['sun_energy'];lights[0].data.angle=math.radians(cfg['lighting']['sun_angle_degrees'])
report=json.loads((out/'geometry-report.json').read_text());report['render']=capabilities
report['lighting']={'single_sun':True,'sky_sun_disk':False,'sun_energy':lights[0].data.energy,'sun_angle_degrees':math.degrees(lights[0].data.angle),'sky_strength':cfg['lighting']['sky_strength'],'exposure':scene.view_settings.exposure}
(out/'geometry-report.json').write_text(json.dumps(report,indent=2))
bpy.context.preferences.filepaths.save_version=0;bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'))
print('SINGLE-SUN DAYLIGHT CALIBRATED; SOURCE GEOMETRY UNTOUCHED')
