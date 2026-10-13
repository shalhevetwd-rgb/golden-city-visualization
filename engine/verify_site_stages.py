"""Fresh independent import of all 6 stage files against actual authored XYZ triangles."""
import bpy,json,sys,math,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/site-v4'

def canonical(vertices,triangles):
    return sorted(tuple(value for point in sorted(tuple(vertices[i]) for i in face) for value in point) for face in triangles)

reports={};inventories={};materials={};camera_records={}
names=[stage+suffix for stage in ('existing','cleared','planted') for suffix in ('.json','.blend','.glb')]
source_hashes={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in names}
for stage in ('existing','cleared','planted'):
    source=json.loads((OUT/(stage+'.json')).read_text());origin=source['origin']
    expected={'terrain':source['terrain'],**{o['id']:o['mesh'] for o in source['objects']}}
    triangles={key:canonical([[v[i]-origin[i] for i in range(3)] for v in mesh['vertices']],mesh['triangles']) for key,mesh in expected.items()}
    for suffix in ('.blend','.glb'):
        path=OUT/(stage+suffix)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if suffix=='.blend':bpy.ops.wm.open_mainfile(filepath=str(path),use_scripts=False,load_ui=False)
        else:bpy.ops.import_scene.gltf(filepath=str(path))
        actual={}
        material_records={}
        for obj in bpy.context.scene.objects:
            if obj.type!='MESH':continue
            identifier=obj.get('stable_id',obj.name);obj.data.calc_loop_triangles()
            verts=[list(obj.matrix_world@v.co) for v in obj.data.vertices]
            actual[identifier]=canonical(verts,[list(t.vertices) for t in obj.data.loop_triangles])
            material_records[identifier]=[(tuple(slot.material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value),float(slot.material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value)) for slot in obj.material_slots]
        if set(actual)!=set(triangles):raise ValueError('Stage object inventory differs: '+path.name)
        max_error=0
        for identifier,expected_triangles in triangles.items():
            if len(actual[identifier])!=len(expected_triangles):raise ValueError('Triangle count differs: '+identifier)
            for a,b in zip(actual[identifier],expected_triangles):
                error=max(abs(x-y) for x,y in zip(a,b));max_error=max(max_error,error)
                if error>1e-4:raise ValueError('Authored XYZ geometry differs: '+identifier+' '+str(error))
        cameras={o.name for o in bpy.context.scene.objects if o.type=='CAMERA'}
        if cameras!={'aerial','top','street'}:raise ValueError('Camera inventory lost')
        reports[path.name]={'passed':True,'mesh_count':len(actual),'camera_count':len(cameras),'max_world_coordinate_error_m':max_error,
                            'triangle_count':sum(len(t) for t in actual.values())}
        if suffix=='.glb':inventories[stage]=actual
        if suffix=='.glb':
            materials[stage]=material_records
            camera_records[stage]={o.name:(tuple(o.matrix_world.translation),tuple(o.matrix_world.to_quaternion()),o.data.angle_y) for o in bpy.context.scene.objects if o.type=='CAMERA'}
            import struct
            raw=path.read_bytes();doc=json.loads(raw[20:20+struct.unpack_from('<I',raw,12)[0]])
            extra=doc['scenes'][doc.get('scene',0)]['extras']
            if extra.get('crs')!=source['crs'] or extra.get('origin_xyz_metres')!=source['origin'] or extra.get('vertical_datum')!=source['terrain']['vertical_datum']:raise ValueError('GLB georeference metadata missing')
for identifier,triangles in inventories['existing'].items():
    if identifier=='remove-A':continue
    if inventories['cleared'].get(identifier)!=triangles or inventories['planted'].get(identifier)!=triangles:raise ValueError('Protected imported mesh changed: '+identifier)
    if materials['cleared'].get(identifier)!=materials['existing'][identifier] or materials['planted'].get(identifier)!=materials['existing'][identifier]:raise ValueError('Protected imported material changed: '+identifier)
if 'remove-A' in inventories['cleared'] or 'remove-A' in inventories['planted']:raise ValueError('Approved removed mesh remains')
if 'new-N' not in inventories['planted']:raise ValueError('New model missing')
if camera_records['existing']!=camera_records['cleared'] or camera_records['existing']!=camera_records['planted']:raise ValueError('Cross-stage cameras changed')
report={'status':'passed','fresh_imports':reports,'protected_geometry_exact_between_GLBS':True,'terrain_exact_between_GLBS':True,
        'protected_materials_exact_between_GLBS':True,'camera_poses_FOV_exact_between_GLBS':True,'georeference_metadata_verified':True,
        'source_coordinate_tolerance_m':.0001,'real_site_accuracy_certified':False,'synthetic':True}
if any(hashlib.sha256((OUT/n).read_bytes()).hexdigest()!=sha for n,sha in source_hashes.items()):raise ValueError('Input changed during independent imports')
report['source_sha256']=source_hashes
(OUT/'roundtrip-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('All six actual files imported; original unselected geometry and TIN exact')
