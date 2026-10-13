"""Export authored synthetic stages; preserve original per-object mesh data."""
import bpy,json,math
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/site-v4'

def material(name,color):
    m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
    shader=m.node_tree.nodes.get('Principled BSDF');shader.inputs['Base Color'].default_value=(*color,1);shader.inputs['Roughness'].default_value=.8
    return m

def mesh(name,data,origin,mat):
    vertices=[[v[i]-origin[i] for i in range(3)] for v in data['vertices']]
    m=bpy.data.meshes.new(name);m.from_pydata(vertices,[],data['triangles']);m.update()
    o=bpy.data.objects.new(name,m);bpy.context.collection.objects.link(o);m.materials.append(mat);return o

for name in ('existing','cleared','planted'):
    document=json.loads((OUT/(name+'.json')).read_text())
    if document.get('synthetic') is not True:raise ValueError('Only explicitly authored synthetic diagnostic scenes')
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene;origin=document['origin']
    palette={'terrain':material('test-bare-earth',[.34,.43,.24]),'building':material('existing-stone',[.69,.59,.42]),
             'street':material('preserved-road',[.17,.19,.21]),'sidewalk':material('preserved-sidewalk',[.65,.66,.64]),
             'vegetation':material('preserved-vegetation',[.10,.32,.12]),'new':material('new-concept',[.13,.36,.68])}
    mesh('terrain',document['terrain'],origin,palette['terrain'])
    for obj in document['objects']:
        if 'mesh' not in obj:raise ValueError('No explicit classified mesh: '+obj['id'])
        m=mesh(obj['id'],obj['mesh'],origin,palette['new' if obj['id']=='new-N' else obj['kind']]);m['stable_id']=obj['id'];m['kind']=obj['kind']
    for identifier,position,target in [('aerial',(145,-115,110),(40,40,5)),('top',(40,40,190),(40,40,0)),('street',(0,-12,1),(25,25,10))]:
        data=bpy.data.cameras.new(identifier);o=bpy.data.objects.new(identifier,data);scene.collection.objects.link(o);o.location=position
        if identifier=='street':o.location.z=document['diagnostic_cameras']['street_ground_elevation_m']-origin[2]+1.7
        o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();data.lens=40;scene.camera=o if identifier=='aerial' else scene.camera
    light=bpy.data.lights.new('test-sun','SUN');light.energy=3;sun=bpy.data.objects.new('test-sun',light);scene.collection.objects.link(sun);sun.rotation_euler=(.4,-.3,-.4)
    world=bpy.data.worlds.new('test-sky');world.use_nodes=True;world.node_tree.nodes['Background'].inputs[0].default_value=(.4,.6,.8,1);scene.world=world
    scene['classification']='synthetic_diagnostic_not_real_site';scene['stage']=document['stage'];scene['origin_xyz_metres']=origin;scene['crs']=document['crs'];scene['vertical_datum']=document['terrain']['vertical_datum']
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=32;scene.cycles.use_denoising=False
    for layer in scene.view_layers:layer.cycles.use_denoising=False
    scene.render.resolution_x=1000;scene.render.resolution_y=700;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.filepath=str(OUT/(name+'.png'))
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/(name+'.blend')))
    bpy.ops.export_scene.gltf(filepath=str(OUT/(name+'.glb')),export_format='GLB',export_cameras=True,export_extras=True)
    bpy.ops.render.render(write_still=True)
print('Three distinct synthetic mesh stages and diagnostic PNGs exported')
