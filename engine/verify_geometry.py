"""Derive fresh model expectations, then independently reopen all four models.

The unchanged original 15 source meshes are also compared by canonical world
triangle signatures to the preserved baseline GLB; not just by bounding boxes.
"""
import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from render_context import load_context,camera_record,native_material_bindings,verify_portable_materials
cfg,out=load_context()


def signatures(required_names=None):
    result={}
    for obj in bpy.context.scene.objects:
        if obj.type!='MESH' or (required_names is not None and obj.name not in required_names):continue
        vertices=[tuple(round(component,5) for component in obj.matrix_world@vertex.co) for vertex in obj.data.vertices]
        obj.data.calc_loop_triangles()
        triangles=[tuple(sorted(vertices[index] for index in triangle.vertices)) for triangle in obj.data.loop_triangles]
        result[obj.name]={'geometry':hashlib.sha256(repr(sorted(triangles)).encode()).hexdigest(),'bounds':[[min(point[i] for point in vertices) for i in range(3)],[max(point[i] for point in vertices) for i in range(3)]]}
    return result


def inventory():
    depsgraph=bpy.context.evaluated_depsgraph_get();records=[];cameras={}
    for obj in sorted(bpy.context.scene.objects,key=lambda item:item.name):
        if obj.type=='CAMERA':
            record=camera_record(obj)
            cameras[obj.name]={key:record[key] for key in ('position','forward','up','vertical_fov')}
        if obj.type!='MESH':continue
        raw=[tuple(obj.matrix_world@vertex.co) for vertex in obj.data.vertices]
        evaluated=obj.evaluated_get(depsgraph);mesh=evaluated.to_mesh()
        mesh.calc_loop_triangles();points=[tuple(obj.matrix_world@vertex.co) for vertex in mesh.vertices]
        record={'name':obj.name,'raw_vertices':len(raw),'raw_faces':len(obj.data.polygons),'evaluated_unique_vertices':len(set(points)),'evaluated_triangles':len(mesh.loop_triangles),'raw_bounds':[[min(p[i] for p in raw) for i in range(3)],[max(p[i] for p in raw) for i in range(3)]],'evaluated_bounds':[[min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)]],'owner':obj.get('owner'),'role':obj.get('role',obj.get('asset_role')),'node_role':obj.get('role')}
        records.append(record);evaluated.to_mesh_clear()
    return {'objects':records,'cameras':cameras,'material_bindings':native_material_bindings(bpy.context.scene)}

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(ROOT/cfg['source_model']))
bpy.context.view_layer.update();original=signatures();assert len(original)==15
expectations={};native_inventories={}
for name in ('model.blend','edited-model.blend'):
    if not (out/name).is_file():raise ValueError('Missing native model for expected inventory: '+name)
    bpy.ops.wm.open_mainfile(filepath=str(out/name),load_ui=False,use_scripts=False);bpy.context.view_layer.update()
    inv=inventory();native_inventories[name]=inv
    common={'objects':len(inv['objects']),'object_names':[record['name'] for record in inv['objects']],'cameras':len(inv['cameras']),'camera_parameters':inv['cameras'],'bound_tolerance':1e-4,'camera_position_tolerance':1e-4,'camera_angle_tolerance':1e-4}
    expectations[name]={**common,'vertices':sum(record['raw_vertices'] for record in inv['objects']),'faces':sum(record['raw_faces'] for record in inv['objects']),'object_bounds':{record['name']:record['raw_bounds'] for record in inv['objects']}}
    portable=name.replace('.blend','.glb')
    expectations[portable]={**common,'min_vertices':sum(record['evaluated_unique_vertices'] for record in inv['objects']),'faces':sum(record['evaluated_triangles'] for record in inv['objects']),'object_bounds':{record['name']:record['evaluated_bounds'] for record in inv['objects']},'material_bindings':inv['material_bindings'],'node_owner_roles':{record['name']:{'owner':record['owner'],'role':record['node_role']} for record in inv['objects']}}
    verify_portable_materials(bpy.context.scene,out/portable)
(out/'model-expectations.json').write_text(json.dumps(expectations,indent=2))
(out/'model-native-inventories.json').write_text(json.dumps(native_inventories,indent=2))
reports={}
for name in ('model.blend','model.glb','edited-model.blend','edited-model.glb'):
    if name.endswith('.blend'):bpy.ops.wm.open_mainfile(filepath=str(out/name),load_ui=False,use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(out/name))
    bpy.context.view_layer.update();current=signatures(original)
    changed=[key for key,record in original.items() if current.get(key)!=record]
    reports[name]={'source_objects_checked':len(original),'source_geometry_changed':changed,'passed':not changed}
    assert not changed,(name,changed)
(out/'roundtrip-geometry.json').write_text(json.dumps(reports,indent=2))
print('FOUR MODEL SOURCE GEOMETRY ROUNDTRIPS VERIFIED',json.dumps(reports),flush=True)
