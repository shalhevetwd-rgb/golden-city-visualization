"""Render a forked native scene. Source meshes and saved camera poses are fixed."""
import json
import sys
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from render_context import preflight_source_geometry

source,output,camera,settings=sys.argv[sys.argv.index('--')+1:]
settings=json.loads(settings)
if camera not in {'aerial','street','architectural'}:raise ValueError('Unknown saved camera')
bpy.ops.wm.open_mainfile(filepath=source,use_scripts=False)
scene=bpy.context.scene
if scene.get('classification')!='synthetic_architectural_proposal_not_real_site':
    raise ValueError('Only the reviewed synthetic architectural showcase is eligible')
preflight=preflight_source_geometry(scene,ROOT/'outputs/v3')
scene.camera=bpy.data.objects['camera:'+camera]
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.use_denoising=False
scene.cycles.samples=settings['samples'];scene.cycles.seed=29;scene.cycles.use_animated_seed=False
scene.render.resolution_x=settings['width'];scene.render.resolution_y=settings['height'];scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
scene.render.filepath=str(Path(output).resolve())
bpy.ops.render.render(write_still=True)
Path(output).with_suffix('.render.json').write_text(json.dumps({'synthetic':True,'source_geometry_pre_render_check':preflight,'camera':camera,'settings':settings},indent=2))
