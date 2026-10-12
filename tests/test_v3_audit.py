"""Real Blender regression checks; no rendering and no supplied-model mutation."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLENDER_SCRIPT = r'''
import bpy, sys, json, hashlib, struct
from pathlib import Path
sys.path.insert(0, ENGINE)
from scene_audit import snapshot, apply_building_facade_color
from constraints import compare_snapshots
from edit_model import edit
from render_context import preflight_source_geometry, export_portable
result = {}

def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    world = bpy.data.worlds.new('World'); world.use_nodes=True; scene.world=world
    sky=world.node_tree.nodes.new('ShaderNodeTexSky'); sky.sky_type='NISHITA'
    world.node_tree.links.new(sky.outputs['Color'],world.node_tree.nodes['Background'].inputs['Color'])
    mat=bpy.data.materials.new('Shared facade');mat.use_nodes=True
    ramp=mat.node_tree.nodes.new('ShaderNodeValToRGB')
    noise=mat.node_tree.nodes.new('ShaderNodeTexNoise')
    mat.node_tree.links.new(noise.outputs['Fac'],ramp.inputs['Fac'])
    mat.node_tree.links.new(ramp.outputs['Color'],mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
    bpy.ops.mesh.primitive_cube_add();body=bpy.context.object;body.name='building:A'
    body['owner']='A';body['building_id']='A';body['role']='facade';body.data.materials.append(mat)
    other=bpy.data.objects.new('building:B',body.data);scene.collection.objects.link(other);other.location.x=5
    other['owner']='B';other['role']='facade'
    skin=bpy.data.objects.new('facade:skin:A',body.data.copy());scene.collection.objects.link(skin);skin.location.z=3
    skin['owner']='A';skin['role']='facade'
    frame=bpy.data.objects.new('frame:A',body.data.copy());scene.collection.objects.link(frame);frame.location.x=-4
    frame['owner']='A';frame['role']='frame'
    for index,name in enumerate(('aerial','street','architectural')):
        bpy.ops.object.camera_add(location=(10,-10,8+index));camera=bpy.context.object
        camera.name='camera:'+name;camera['owner']='context';camera.data.sensor_fit='VERTICAL'
        camera.data.lens=(40,26,36)[index]
    scene.camera=bpy.data.objects['camera:aerial']
    bpy.ops.object.light_add(type='SUN');bpy.context.object['owner']='context'
    bpy.context.view_layer.update()
    return body, other, skin, frame, mat, sky

body,other,skin,frame,mat,sky=fresh();before=snapshot()
eligible=apply_building_facade_color('A',[.2,.4,.6]);after=snapshot();qa=compare_snapshots(before,after,'A')
result['isolated_facade_edit']=qa['passed'] and qa['changed_objects']==['building:A','facade:skin:A'] and eligible==qa['changed_objects']
result['shared_mesh_isolation']=body.data is other.data and before['building:B']==after['building:B'] and before['frame:A']==after['frame:A']
result['geometry_preserved']=all(before[k]['geometry']==after[k]['geometry'] for k in before)
body,other,skin,frame,mat,sky=fresh();accent=mat.copy();accent['facade_tint_scale']=.18
skin.material_slots[0].link='OBJECT';skin.material_slots[0].material=accent;before=snapshot()
apply_building_facade_color('A',[.2,.4,.6]);after=snapshot()
result['facade_accent_contrast_preserved']=compare_snapshots(before,after,'A')['passed'] and all(abs(actual-expected)<1e-6 for actual,expected in zip(skin.material_slots[0].material.diffuse_color[:3],[.036,.072,.108])) and skin.material_slots[0].material['facade_tint_scale']==accent['facade_tint_scale']
body,other,skin,frame,mat,sky=fresh();bad=mat.copy();bad.node_tree.links.remove(bad.node_tree.nodes['Material Output'].inputs['Surface'].links[0])
skin.material_slots[0].link='OBJECT';skin.material_slots[0].material=bad;before=snapshot()
try: apply_building_facade_color('A',[.2,.4,.6])
except ValueError: result['unsupported_shader_preflight_is_atomic']=before==snapshot()
else: result['unsupported_shader_preflight_is_atomic']=False

body,other,skin,frame,mat,sky=fresh();before=snapshot()
mat.node_tree.nodes.get('Color Ramp').color_ramp.elements[0].color=(1,.1,.2,1)
qa=compare_snapshots(before,snapshot(),'A');result['ramp_change_detected']=not qa['passed'] and 'building:B' in qa['outside_target_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot()
mat.node_tree.nodes.get('Noise Texture').noise_dimensions='4D'
result['node_property_change_detected']=not compare_snapshots(before,snapshot(),'A')['passed']
body,other,skin,frame,mat,sky=fresh();before=snapshot()
body.data.uv_layers.active.data[0].uv.x+=.1
result['uv_change_detected']='building:A' in compare_snapshots(before,snapshot(),'A')['geometry_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot()
body.data.polygons[0].material_index=1
result['material_index_change_detected']='building:A' in compare_snapshots(before,snapshot(),'A')['geometry_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot()
modifier=body.modifiers.new('Hidden displacement','DISPLACE');modifier.show_render=False;modifier.show_viewport=False;modifier.strength=2
result['modifier_change_detected']='building:A' in compare_snapshots(before,snapshot(),'A')['geometry_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot();body.hide_render=True
result['visibility_change_detected']='building:A' in compare_snapshots(before,snapshot(),'A')['immutable_state_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot();sky.sun_elevation+=.2
result['world_sky_change_detected']='__world__' in compare_snapshots(before,snapshot(),'A')['outside_target_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot();bpy.context.scene.camera.data.sensor_fit='HORIZONTAL'
result['camera_change_detected']='camera:aerial' in compare_snapshots(before,snapshot(),'A')['outside_target_changed']
body,other,skin,frame,mat,sky=fresh();before=snapshot();bpy.context.scene.render.resolution_percentage=50
result['render_state_change_detected']='__scene__' in compare_snapshots(before,snapshot(),'A')['outside_target_changed']
body,other,skin,frame,mat,sky=fresh()
collection=bpy.data.collections.new('Hideable building collection');bpy.context.scene.collection.children.link(collection)
collection.objects.link(body);bpy.context.scene.collection.objects.unlink(body)
before=snapshot();collection.hide_render=True
result['collection_visibility_change_detected']='__scene__' in compare_snapshots(before,snapshot(),'A')['outside_target_changed']

body,other,skin,frame,mat,sky=fresh()
texture=mat.node_tree.nodes.new('ShaderNodeTexImage');image=bpy.data.images.new('Generated test finish',width=2,height=2);texture.image=image
mat.node_tree.links.new(texture.outputs['Color'],mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
before=snapshot();image.pixels[0]=.5
result['texture_pixels_change_detected']=not compare_snapshots(before,snapshot(),'A')['passed']
before=snapshot();apply_building_facade_color('A',[.2,.3,.4]);after=snapshot()
shader=body.material_slots[0].material.node_tree
tint=shader.nodes.get('Targeted facade tint')
result['linked_color_edit_connected']=compare_snapshots(before,after,'A')['passed'] and tint is not None and shader.nodes['Principled BSDF'].inputs['Base Color'].links[0].from_node==tint
body,other,skin,frame,mat,sky=fresh()
ramp=mat.node_tree.nodes['Color Ramp'];bsdf=mat.node_tree.nodes['Principled BSDF']
mat.node_tree.links.new(ramp.outputs['Color'],bsdf.inputs['Roughness'])
original_ramp=[list(e.color) for e in ramp.color_ramp.elements]
apply_building_facade_color('A',[.2,.3,.4]);tree=body.material_slots[0].material.node_tree
result['color_edit_preserves_shared_roughness_palette']=[list(e.color) for e in tree.nodes['Color Ramp'].color_ramp.elements]==original_ramp and tree.nodes['Principled BSDF'].inputs['Roughness'].links[0].from_node==tree.nodes['Color Ramp'] and tree.nodes['Principled BSDF'].inputs['Base Color'].links[0].from_node==tree.nodes['Targeted facade tint']
body,other,skin,frame,mat,sky=fresh()
coordinate=mat.node_tree.nodes.new('ShaderNodeTexCoord');coordinate.object=body
mat.node_tree.links.new(coordinate.outputs['Object'],mat.node_tree.nodes['Noise Texture'].inputs['Vector'])
before=snapshot();coordinate.object=other
result['node_object_dependency_change_detected']=not compare_snapshots(before,snapshot(),'A')['passed']
body,other,skin,frame,mat,sky=fresh()
curve=bpy.data.curves.new('Neighbour curve','CURVE');curve.dimensions='3D';curve.bevel_depth=.1
spline=curve.splines.new('POLY');spline.points.add(1);spline.points[0].co=(0,0,0,1);spline.points[1].co=(2,0,0,1)
neighbour=bpy.data.objects.new('context:railing',curve);bpy.context.scene.collection.objects.link(neighbour)
before=snapshot();spline.points[1].co.x=3
result['curve_context_change_detected']='context:railing' in compare_snapshots(before,snapshot(),'A')['outside_target_changed']

# The worker must genuinely reopen a saved version and preserve the source.
body,other,skin,frame,mat,sky=fresh();work=Path(WORK)
skin.data=body.data
secondary=mat.copy();secondary.name='Shared second facade finish';secondary['facade_tint_scale']=.35
body.data.materials.append(secondary);body.data.polygons[0].material_index=1
facade_scales={'building:A':1,'facade:skin:A':.18,'facade:floorband:A':.7,'facade:pilaster:A':.18,'facade:plinth:A':.35,'facade:spandrel:A':.7}
for index,(name,scale) in enumerate(facade_scales.items()):
    obj=bpy.data.objects.get(name)
    if obj is None:
        obj=bpy.data.objects.new(name,body.data);bpy.context.scene.collection.objects.link(obj);obj.location=(0,index*3,0);obj['owner']='A';obj['role']='facade'
    if name!='building:A':
        own=mat.copy();own['facade_tint_scale']=scale;obj.material_slots[0].link='OBJECT';obj.material_slots[0].material=own
        bevel=obj.modifiers.new('Real evaluated cladding bevel','BEVEL');bevel.width=.08;bevel.segments=2
bpy.context.view_layer.update()
data_before={obj.name:obj.data for obj in bpy.context.scene.objects if obj.type=='MESH'}
snapshot_before_export=snapshot();export_portable(bpy.context.scene,work/'before-edit.glb')
result['portable_export_preserves_native_snapshot']=snapshot_before_export==snapshot()
result['portable_export_preserves_shared_mesh_data']=all(bpy.data.objects[name].data is data for name,data in data_before.items()) and body.data is other.data and skin.data is body.data
source=work/'source.blend';request=work/'request.json';output=work/'edited-version'
bpy.ops.wm.save_as_mainfile(filepath=str(source));sha=hashlib.sha256(source.read_bytes()).hexdigest()
request.write_text(json.dumps({'source_sha256':sha,'building_id':'A','operation':'facade_color','color':[.3,.5,.7]}))
worker=edit(source,request,output)
result['worker_saved_reopen']=worker['passed'] and worker['native_reopen_verified'] and worker['eligible_facade_objects']==sorted(facade_scales)
result['worker_preserves_source']=hashlib.sha256(source.read_bytes()).hexdigest()==sha
result['worker_no_pixel_claim']=worker['render_updated'] is False and worker['pixel_identity_claim'] is False
result['worker_material_export_proof_recorded']=worker['material_export_verified'] is True and worker['portable_material_node_count']==8
result['worker_unchanged_inventory_recorded']='building:B' in worker['unchanged_scene_objects'] and 'frame:A' in worker['unchanged_scene_objects'] and '__world__' in worker['unchanged_scene_objects']
raw=(output/'edited.glb').read_bytes();document=json.loads(raw[20:20+struct.unpack('<I',raw[12:16])[0]])
result['worker_glb_preserves_three_cameras']=len(document.get('cameras',[]))==3
def glb_palette(name):
    node=next(n for n in document['nodes'] if n.get('name')==name)
    output=[]
    for primitive in document['meshes'][node['mesh']]['primitives']:
        material=document['materials'][primitive['material']]
        output.append((material['pbrMetallicRoughness'].get('baseColorFactor',[1,1,1,1]),material.get('extras',{}).get('facade_tint_scale',1)))
    return output
def glb_color(name):
    palette=glb_palette(name)
    return next(color for color,scale in palette if scale==facade_scales.get(name,1))
result['worker_glb_selected_color']=all(abs(actual-expected)<1e-6 for actual,expected in zip(glb_color('building:A'),[.3,.5,.7,1]))
result['worker_glb_all_six_facade_factors']=all(len(glb_palette(name))==2 and all(all(abs(actual-expected)<1e-6 for actual,expected in zip(color,[.3*scale,.5*scale,.7*scale,1])) for color,scale in glb_palette(name)) for name in facade_scales)
body_node=next(node for node in document['nodes'] if node.get('name')=='building:A')
body_primitives=document['meshes'][body_node['mesh']]['primitives']
secondary_primitive=next(primitive for primitive in body_primitives if document['materials'][primitive['material']]['extras']['facade_tint_scale']==.35)
accessor=document['accessors'][secondary_primitive['attributes']['POSITION']];view=document['bufferViews'][accessor['bufferView']]
binary_start=20+struct.unpack('<I',raw[12:16])[0]+8
start=binary_start+view.get('byteOffset',0)+accessor.get('byteOffset',0);stride=view.get('byteStride',12)
points=[struct.unpack_from('<3f',raw,start+index*stride) for index in range(accessor['count'])]
if 'indices' in secondary_primitive:
    indices_accessor=document['accessors'][secondary_primitive['indices']];indices_view=document['bufferViews'][indices_accessor['bufferView']]
    code,size={5121:('B',1),5123:('H',2),5125:('I',4)}[indices_accessor['componentType']]
    indices_start=binary_start+indices_view.get('byteOffset',0)+indices_accessor.get('byteOffset',0)
    indices=[struct.unpack_from('<'+code,raw,indices_start+index*indices_view.get('byteStride',size))[0] for index in range(indices_accessor['count'])]
    points=[points[index] for index in indices]
# Cube's original polygon0 is the x=-1 face and alone uses slot1. Checking
# positions associates this material with geometry, not just a binding set.
result['worker_glb_polygon_material_assignment_preserved']=accessor['componentType']==5126 and accessor['type']=='VEC3' and len(points)>=3 and all(abs(point[0]+1)<1e-6 for point in points)
result['worker_glb_neighbor_color_unchanged']=all(all(abs(actual-.8)<1e-6 for actual in color[:3]) and abs(color[3]-1)<1e-6 for color,scale in glb_palette('building:B'))
result['worker_glb_nonfacade_color_unchanged']=all(abs(actual-.8)<1e-6 for actual in glb_color('frame:A')[:3]) and abs(glb_color('frame:A')[3]-1)<1e-6
try: edit(source,request,output)
except ValueError: result['worker_preserves_existing_version']=True
else: result['worker_preserves_existing_version']=False
request.write_text(json.dumps({'source_sha256':'0'*64,'building_id':'A','operation':'facade_color','color':[.3,.5,.7]}))
try: edit(source,request,work/'wrong-source-version')
except ValueError: result['worker_wrong_source_rejected']=not (work/'wrong-source-version').exists()
else: result['worker_wrong_source_rejected']=False
request.write_text(json.dumps({'source_sha256':sha,'building_id':'A','operation':'facade_color','color':[float('nan'),.5,.7]}))
try: edit(source,request,work/'invalid-color-version')
except ValueError: result['worker_invalid_color_rejected']=not (work/'invalid-color-version').exists()
else: result['worker_invalid_color_rejected']=False
body,other,skin,frame,mat,sky=fresh();del body['role'];del skin['role']
legacy_source=work/'legacy-v2.blend';bpy.ops.wm.save_as_mainfile(filepath=str(legacy_source))
request.write_text(json.dumps({'source_sha256':hashlib.sha256(legacy_source.read_bytes()).hexdigest(),'building_id':'A','operation':'facade_color','color':[.3,.5,.7]}))
legacy=edit(legacy_source,request,work/'legacy-v2-edited')
result['legacy_single_body_edit_preserved']=legacy['passed'] and legacy['eligible_facade_objects']==['building:A'] and legacy['native_reopen_verified']

# Saved render preflight must reject a changed original transform, missing
# source object, or reported floor rows even without any facade operation.
bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
for index in range(15):
    bpy.ops.mesh.primitive_cube_add(location=(index*3,0,0));bpy.context.object.name='source:'+str(index)
bpy.context.view_layer.update();inventory={}
for obj in scene.objects:
    raw={'vertices':[list(v.co) for v in obj.data.vertices],'faces':[list(p.vertices) for p in obj.data.polygons],'matrix':[list(row) for row in obj.matrix_world]}
    inventory[obj.name]={'geometry':hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()}
preflight_dir=work/'preflight';preflight_dir.mkdir()
report={'synthetic':True,'source_meshes':inventory,'buildings':{name:{'source_window_rows':rows} for name,rows in [('A',10),('B',8),('C',6)]}}
report_path=preflight_dir/'geometry-report.json';report_path.write_text(json.dumps(report))
result['source_preflight_accepts_exact_inventory']=preflight_source_geometry(scene,preflight_dir)['passed']
scene.objects['source:0'].location.x+=1;bpy.context.view_layer.update()
try:preflight_source_geometry(scene,preflight_dir)
except ValueError:result['source_preflight_rejects_movement']=True
else:result['source_preflight_rejects_movement']=False
scene.objects['source:0'].location.x-=1;bpy.context.view_layer.update();report['buildings']['B']['source_window_rows']=7;report_path.write_text(json.dumps(report))
try:preflight_source_geometry(scene,preflight_dir)
except ValueError:result['source_preflight_rejects_changed_floor_rows']=True
else:result['source_preflight_rejects_changed_floor_rows']=False
report['buildings']['B']['source_window_rows']=8;report_path.write_text(json.dumps(report));bpy.data.objects.remove(scene.objects['source:1'],do_unlink=True)
try:preflight_source_geometry(scene,preflight_dir)
except ValueError:result['source_preflight_rejects_missing_original']=True
else:result['source_preflight_rejects_missing_original']=False
print('AUDIT_RESULT='+json.dumps(result,sort_keys=True))
'''


@unittest.skipUnless(shutil.which('blender'), 'Blender is required for native scene auditing')
class BlenderSceneAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='golden-city-audit-')
        work = Path(cls.temporary.name)
        script = work / 'audit.py'
        script.write_text('ENGINE=' + repr(str(ROOT / 'engine')) + '\nWORK=' + repr(str(work)) + '\n' + BLENDER_SCRIPT)
        run = subprocess.run([shutil.which('blender'), '--background', '--factory-startup', '--threads', '1', '--python-exit-code', '1', '--python', str(script)],
                             capture_output=True, text=True, timeout=90)
        if run.returncode != 0:
            raise AssertionError('Blender audit failed:\n' + run.stdout[-12000:] + run.stderr[-3000:])
        line = next((line for line in run.stdout.splitlines() if line.startswith('AUDIT_RESULT=')), None)
        if line is None:
            raise AssertionError('Blender audit produced no result: ' + run.stdout[-4000:])
        cls.results = json.loads(line.split('=', 1)[1])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_facade_edit_isolates_shared_meshes_and_other_roles(self):
        for key in ('isolated_facade_edit', 'shared_mesh_isolation', 'geometry_preserved', 'unsupported_shader_preflight_is_atomic',
                    'facade_accent_contrast_preserved'):
            with self.subTest(check=key):
                self.assertTrue(self.results[key])

    def test_shader_parameters_and_uv_material_assignments_are_audited(self):
        for key in ('ramp_change_detected', 'node_property_change_detected', 'uv_change_detected', 'material_index_change_detected',
                    'texture_pixels_change_detected', 'node_object_dependency_change_detected', 'linked_color_edit_connected',
                    'color_edit_preserves_shared_roughness_palette'):
            with self.subTest(check=key):
                self.assertTrue(self.results[key])

    def test_modifiers_visibility_and_global_environment_are_audited(self):
        for key in ('modifier_change_detected', 'visibility_change_detected', 'world_sky_change_detected',
                    'camera_change_detected', 'render_state_change_detected', 'collection_visibility_change_detected',
                    'curve_context_change_detected'):
            with self.subTest(check=key):
                self.assertTrue(self.results[key])

    def test_saved_worker_version_is_reopened_and_source_pinned(self):
        for key in ('worker_saved_reopen', 'worker_preserves_source', 'worker_no_pixel_claim',
                    'worker_preserves_existing_version', 'worker_wrong_source_rejected', 'worker_invalid_color_rejected',
                    'legacy_single_body_edit_preserved', 'worker_unchanged_inventory_recorded',
                    'worker_glb_preserves_three_cameras', 'worker_glb_selected_color', 'worker_glb_neighbor_color_unchanged',
                    'worker_glb_all_six_facade_factors', 'worker_glb_nonfacade_color_unchanged',
                    'portable_export_preserves_native_snapshot', 'portable_export_preserves_shared_mesh_data',
                    'worker_material_export_proof_recorded', 'worker_glb_polygon_material_assignment_preserved'):
            with self.subTest(check=key):
                self.assertTrue(self.results[key])

    def test_saved_render_preflight_preserves_original_objects_and_floor_rows(self):
        for key in ('source_preflight_accepts_exact_inventory', 'source_preflight_rejects_movement',
                    'source_preflight_rejects_changed_floor_rows', 'source_preflight_rejects_missing_original'):
            with self.subTest(check=key):
                self.assertTrue(self.results[key])
