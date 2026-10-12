"""Shared v3 render settings; v2 artifacts are read-only inputs."""
from __future__ import annotations
import json
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
from array import array

ROOT=Path(__file__).resolve().parents[1]


def load_context():
    cfg_path=Path(os.environ.get('GOLDEN_CITY_DESIGN_CONFIG',ROOT/'config/synthetic-design-v3.json')).resolve()
    out=Path(os.environ.get('GOLDEN_CITY_OUTPUT_DIR',ROOT/'outputs/v3')).resolve()
    protected=[(ROOT/location).resolve() for location in ('outputs/v2','baseline','examples/renders')]
    if any(out==directory or directory in out.parents for directory in protected):
        raise ValueError('Architectural v3 must not overwrite preserved baseline/v2/acceptance artifacts')
    cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    if cfg.get('synthetic') is not True:
        raise ValueError('This architectural detail workflow is a synthetic design exercise only; real projects require reviewed source plans')
    out.mkdir(parents=True,exist_ok=True)
    return cfg,out


def configure_render(scene,cfg):
    import bpy
    settings=cfg['render']
    draft=os.environ.get('GOLDEN_CITY_RENDER_MODE','final')=='draft'
    preset=settings['draft' if draft else 'final']
    scene.render.engine='CYCLES'
    scene.cycles.device='CPU'
    scene.cycles.samples=preset['samples']
    scene.cycles.seed=cfg['seed']
    scene.cycles.use_animated_seed=False
    scene.cycles.use_adaptive_sampling=True
    scene.cycles.adaptive_threshold=preset.get('adaptive_threshold',.02)
    scene.cycles.adaptive_min_samples=min(24,preset['samples'])
    scene.cycles.max_bounces=8
    scene.cycles.diffuse_bounces=3
    scene.cycles.glossy_bounces=4
    scene.cycles.transmission_bounces=4
    scene.cycles.transparent_max_bounces=6
    scene.cycles.sample_clamp_indirect=4
    # This distribution has no OpenImageDenoise support. Keep verified Cycles
    # sampling rather than silently claiming an unavailable denoising pass.
    scene.cycles.use_denoising=False
    scene.render.resolution_x=preset['width']
    scene.render.resolution_y=preset['height']
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGB'
    scene.render.image_settings.color_depth='8'
    scene.render.film_transparent=False
    scene.render.threads_mode='FIXED'
    scene.render.threads=min(os.cpu_count() or 2,settings.get('threads',8))
    scene.view_settings.view_transform='AgX'
    scene.view_settings.look='AgX - Medium High Contrast'
    scene.view_settings.exposure=cfg['lighting'].get('exposure',-.2)
    return {'engine':'Blender Cycles CPU','blender_version':bpy.app.version_string,'mode':'draft' if draft else 'final','samples':scene.cycles.samples,'adaptive_threshold':scene.cycles.adaptive_threshold,'denoising':False,'external_oidn_available':bool(shutil.which('oidnDenoise') or shutil.which('oidnDenoiser')),'width':scene.render.resolution_x,'height':scene.render.resolution_y,'threads':scene.render.threads}


def export_portable(scene,path):
    """Export all source/detail objects and cameras with constant PBR factors.

    Procedural maps and the sky remain faithfully in .blend and PNG renders;
    glTF receives the declared material base color/roughness/metallic values.
    """
    import bpy
    originals={obj:{'data':obj.data,'slots':[(slot.link,slot.material) for slot in obj.material_slots]}
               for obj in scene.objects if obj.type=='MESH'}
    replacements={};temporary_meshes=[]
    try:
        for obj,record in originals.items():
            slots=record['slots']
            effective=[]
            for index,(_,mat) in enumerate(slots or [('DATA',None)]):
                if mat not in replacements:
                    flat=bpy.data.materials.new('portable:'+mat.name if mat is not None else 'portable:unassigned-neutral')
                    flat.use_nodes=True
                    source=mat.node_tree.nodes.get('Principled BSDF') if mat is not None and mat.use_nodes else None
                    target=flat.node_tree.nodes['Principled BSDF']
                    color=source.inputs['Base Color'].default_value if source else mat.diffuse_color if mat is not None else (.5,.5,.5,1)
                    target.inputs['Base Color'].default_value=color
                    flat.diffuse_color=color
                    flat['source_material_name']=mat.name if mat is not None else '__UNASSIGNED__'
                    flat['facade_tint_scale']=float(mat.get('facade_tint_scale',1)) if mat is not None else 1.
                    if source:
                        for field in ('Roughness','Metallic','IOR','Transmission Weight','Coat Weight','Coat Roughness'):
                            if field in source.inputs and field in target.inputs:
                                target.inputs[field].default_value=source.inputs[field].default_value
                    elif mat is not None:
                        target.inputs['Roughness'].default_value=mat.roughness
                        target.inputs['Metallic'].default_value=mat.metallic
                    replacements[mat]=flat
                effective.append(replacements[mat])
            # Applied modifiers evaluate Mesh DATA materials. Object-linked
            # overrides alone are lost by Blender's glTF export_apply path.
            # Isolated copies bake the effective slots without touching a
            # shared native mesh or its material assignments.
            mesh=record['data'].copy();temporary_meshes.append(mesh)
            indices=array('i',[0])*len(mesh.polygons)
            mesh.polygons.foreach_get('material_index',indices)
            mesh.materials.clear()
            for flat in effective:mesh.materials.append(flat)
            # Clearing Mesh materials resets polygon indices to zero. Restore
            # the entire copied channel so multimaterial bodies/plants/people
            # retain the original per-face effective material assignments.
            mesh.polygons.foreach_set('material_index',indices)
            obj.data=mesh
            for index,flat in enumerate(effective):
                obj.material_slots[index].link='DATA'
                obj.material_slots[index].material=flat
        bpy.context.view_layer.update()
        bpy.ops.export_scene.gltf(filepath=str(path),check_existing=False,export_format='GLB',export_cameras=True,export_lights=True,export_extras=True,export_apply=True,export_animations=False)
    finally:
        for obj,record in originals.items():
            obj.data=record['data']
            for index,(link,mat) in enumerate(record['slots']):
                obj.material_slots[index].link=link
                obj.material_slots[index].material=mat
        bpy.context.view_layer.update()
        for mesh in temporary_meshes:bpy.data.meshes.remove(mesh)
        for mat in replacements.values():bpy.data.materials.remove(mat)
    return verify_portable_materials(scene,path)


def native_material_bindings(scene):
    """Ground-truth linear factors from effective slots used by evaluated faces."""
    import bpy
    depsgraph=bpy.context.evaluated_depsgraph_get();bindings={}
    for obj in sorted(scene.objects,key=lambda item:item.name):
        if obj.type!='MESH':continue
        evaluated=obj.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
        try:used=sorted({polygon.material_index for polygon in mesh.polygons})
        finally:evaluated.to_mesh_clear()
        records={}
        for index in used:
            if index>=len(obj.material_slots):
                if obj.material_slots:raise ValueError('Evaluated face references an unknown effective material slot: '+obj.name)
                mat=None
            else:mat=obj.material_slots[index].material
            source=mat.node_tree.nodes.get('Principled BSDF') if mat is not None and mat.use_nodes else None
            color=list(source.inputs['Base Color'].default_value if source else mat.diffuse_color if mat is not None else (.5,.5,.5,1))
            scale=float(mat.get('facade_tint_scale',1)) if mat is not None else 1.
            if len(color)!=4 or not all(math.isfinite(value) for value in color) or not math.isfinite(scale):
                raise ValueError('Nonfinite effective portable material factors: '+obj.name)
            name=mat.name if mat is not None else '__UNASSIGNED__'
            roughness=float(source.inputs['Roughness'].default_value if source else mat.roughness if mat is not None else .5)
            metallic=float(source.inputs['Metallic'].default_value if source else mat.metallic if mat is not None else 0)
            records[name]={'source_material_name':name,'base_color_factor':color,'facade_tint_scale':scale,
                           'roughness_factor':roughness,'metallic_factor':metallic}
        if not records:raise ValueError('Portable material verification requires polygon mesh faces: '+obj.name)
        bindings[obj.name]=[records[name] for name in sorted(records)]
    return bindings


def verify_portable_materials(scene,path,tolerance=1e-6):
    """Reject missing/wrong PBR factors on any exported mesh primitive.

    The expected values are derived from restored native effective slots,
    including object overrides and modifier-selected material indices.
    Procedural texture graphs remain native; these are constant PBR factors.
    """
    path=Path(path);raw=path.read_bytes()
    if len(raw)<20 or raw[:4]!=b'glTF':raise ValueError('Portable export is not a GLB')
    magic,version,total=struct.unpack_from('<4sII',raw,0)
    length,kind=struct.unpack_from('<II',raw,12)
    if version!=2 or total!=len(raw) or kind!=0x4E4F534A or 20+length>len(raw):
        raise ValueError('Portable export has an invalid GLB JSON chunk')
    document=json.loads(raw[20:20+length]);expected=native_material_bindings(scene)
    actual={}
    for node in document.get('nodes',[]):
        if 'mesh' not in node:continue
        name=node.get('name')
        if name in actual:raise ValueError('Duplicate portable mesh node name: '+str(name))
        records={}
        for primitive in document['meshes'][node['mesh']]['primitives']:
            if 'material' not in primitive:raise ValueError('Portable primitive lacks its material: '+str(name))
            material=document['materials'][primitive['material']];extras=material.get('extras',{})
            if not material.get('name','').startswith('portable:') or not isinstance(extras.get('source_material_name'),str):
                raise ValueError('Portable primitive bypassed flattened effective material: '+str(name))
            source=extras['source_material_name']
            record={'source_material_name':source,
                    'base_color_factor':material.get('pbrMetallicRoughness',{}).get('baseColorFactor',[1,1,1,1]),
                    'facade_tint_scale':extras.get('facade_tint_scale',1),
                    'roughness_factor':material.get('pbrMetallicRoughness',{}).get('roughnessFactor',1),
                    'metallic_factor':material.get('pbrMetallicRoughness',{}).get('metallicFactor',1)}
            if source in records and records[source]!=record:raise ValueError('Conflicting portable bindings for '+str(name))
            records[source]=record
        actual[name]=[records[source] for source in sorted(records)]
    if set(actual)!=set(expected):raise ValueError('Portable mesh material inventory differs from native meshes')
    for name,wanted in expected.items():
        got=actual[name]
        if [record['source_material_name'] for record in got]!=[record['source_material_name'] for record in wanted]:
            raise ValueError('Portable effective material binding set differs: '+name)
        for first,second in zip(wanted,got):
            color=second['base_color_factor']
            if len(color)!=4 or any(not math.isfinite(value) or abs(value-first['base_color_factor'][index])>tolerance for index,value in enumerate(color)):
                raise ValueError('Portable linear RGBA factor differs from native effective material: '+name)
            if not math.isfinite(second['facade_tint_scale']) or abs(second['facade_tint_scale']-first['facade_tint_scale'])>tolerance:
                raise ValueError('Portable facade tint scale differs from native material: '+name)
            for factor in ('roughness_factor','metallic_factor'):
                if not math.isfinite(second[factor]) or abs(second[factor]-first[factor])>tolerance:
                    raise ValueError('Portable '+factor+' differs from native material: '+name)
    return {'passed':True,'mesh_nodes_checked':len(expected),'material_bindings':expected,
            'linear_rgba_tolerance':tolerance,'all_primitive_factors_verified':True,
            'glb_sha256':hashlib.sha256(raw).hexdigest()}


def camera_record(camera):
    from mathutils import Vector
    matrix=camera.matrix_world.to_3x3()
    return {'name':camera.name,'position':list(camera.matrix_world.translation),'forward':list((matrix@Vector((0,0,-1))).normalized()),'up':list((matrix@Vector((0,1,0))).normalized()),'lens_mm':camera.data.lens,'sensor_fit':camera.data.sensor_fit,'sensor_height_mm':camera.data.sensor_height,'vertical_fov':math.degrees(camera.data.angle_y),'shift_y':camera.data.shift_y,'ground_z':camera.get('source_ground_z'),'height_above_source_ground_m':camera.get('height_above_source_ground_m')}


def preflight_source_geometry(scene, out):
    """Reject a saved model whose original mesh data or transforms changed."""
    report=json.loads((out/'geometry-report.json').read_text())
    source=report['source_meshes']
    if len(source)!=15 or report.get('synthetic') is not True:
        raise ValueError('Rendering requires the preserved 15-mesh synthetic source inventory')
    changed=[]
    for name, expected in source.items():
        obj=scene.objects.get(name)
        if obj is None or obj.type!='MESH':
            changed.append(name);continue
        raw={'vertices':[list(vertex.co) for vertex in obj.data.vertices],
             'faces':[list(polygon.vertices) for polygon in obj.data.polygons],
             'matrix':[list(row) for row in obj.matrix_world]}
        actual=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
        if actual!=expected['geometry']:changed.append(name)
    if changed:raise ValueError('Original source geometry changed before rendering: '+', '.join(changed))
    if {name:data['source_window_rows'] for name,data in report['buildings'].items()}!={'A':10,'B':8,'C':6}:
        raise ValueError('The original 10/8/6 source window rows must remain unchanged')
    return {'passed':True,'source_meshes_checked':15,'source_geometry_changed':[],
            'source_window_rows':{'A':10,'B':8,'C':6}}


def refresh_saved_cameras(scene,cfg):
    """Apply reviewed camera targets without moving any source/detail object."""
    from mathutils import Vector
    for name,record in cfg['cameras'].items():
        camera=scene.objects['camera:'+name]
        position=list(record['position'])
        if position[2] is None:
            if 'source_ground_z' not in camera:
                raise ValueError('Saved pedestrian camera lacks a verified source ground height')
            # Ground was measured from source triangles at this exact XY in
            # build_scene, so an XY move requires rebuilding/re-measuring.
            if any(abs(position[i]-camera.location[i])>1e-6 for i in (0,1)):
                raise ValueError('Pedestrian camera XY changed; rebuild to verify ground height')
            position[2]=camera['source_ground_z']+record['height_above_source_ground_m']
        camera.location=position
        camera.rotation_euler=(Vector(record['target'])-camera.location).to_track_quat('-Z','Y').to_euler()
        camera.data.lens=record['lens_mm']
        camera.data.sensor_fit=record['sensor_fit']
        camera.data.sensor_height=record['sensor_height_mm']
        camera.data.shift_x=0;camera.data.shift_y=0
        camera['saved_target']=record['target']
