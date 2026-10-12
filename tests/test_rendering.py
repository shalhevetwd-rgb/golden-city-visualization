import copy
import json
import math
import subprocess
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image
from rendering import polygon_meshes, building_fingerprints, default_cameras, png_size, render_project, resolve_camera, scene_origin, validate_camera

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    project=json.loads((ROOT/'examples/synthetic-project.geojson').read_text())
    project['errors']=[]
    return project


class RenderingTests(unittest.TestCase):
    def test_named_and_saved_cameras_use_local_z_up(self):
        p=fixture()
        self.assertEqual(scene_origin(p),[220050.0,630050.0,0.0])
        self.assertAlmostEqual(default_cameras(p)['street']['position'][2],1.7)
        camera={'id':'custom','position':[22,-80,35],'target':[0,0,8],'fov':55}
        p['cameras']={'custom':camera}
        self.assertEqual(resolve_camera(p,'custom'),validate_camera(camera))
        self.assertEqual(resolve_camera(p)['id'],'aerial')

    def test_invalid_camera_rejected(self):
        for camera in ({'position':[0,0,0],'target':[0,0,0]}, {'position':[float('nan'),0,0],'target':[0,0,1]}, {'position':[0,0,1],'target':[0,0,0],'fov':180}):
            with self.assertRaises(ValueError):validate_camera(camera)
        with self.assertRaises(ValueError):resolve_camera(fixture(),'missing')

    def test_targeted_material_change_preserves_geometry(self):
        p=fixture();other=copy.deepcopy(p['buildings'][0]);other['properties']['id']='B';p['buildings'].append(other)
        before=building_fingerprints(p)
        p['buildings'][0]['properties'].update(color='#bb3333',material='brick')
        after=building_fingerprints(p)
        self.assertEqual(before['B'],after['B'])
        self.assertEqual(before['A']['geometry_hash'],after['A']['geometry_hash'])
        self.assertNotEqual(before['A']['material_hash'],after['A']['material_hash'])

    def test_base_elevation_is_part_of_geometry_identity(self):
        p=fixture();before=building_fingerprints(p)
        p['buildings'][0]['properties']['base_elevation']=5
        self.assertNotEqual(before['A']['geometry_hash'],building_fingerprints(p)['A']['geometry_hash'])

    def test_polygon_openings_preserved_in_exact_cap_triangulation(self):
        from shapely.geometry import Polygon,shape
        from shapely.ops import unary_union
        p=fixture()
        footprint={'type':'Polygon','coordinates':[[[220010,630010],[220030,630010],[220030,630030],[220010,630030],[220010,630010]],[[220015,630015],[220025,630015],[220025,630025],[220015,630025],[220015,630015]]]}
        p['overhead_obstructions']=[{'id':'opening','footprint':footprint,'elevation':40,'thickness':.2}]
        prepared=next(iter(polygon_meshes(p).values()))
        self.assertEqual(prepared['surface_area'],300)
        triangles=unary_union([Polygon(ring) for ring in prepared['triangles']])
        self.assertAlmostEqual(triangles.symmetric_difference(shape(footprint)).area,0)
        self.assertEqual(len(prepared['rings']),2)

    @unittest.skipUnless(shutil.which('blender'),'Blender executable is required for real render integration')
    def test_actual_png_and_persisted_blender_scene(self):
        p=fixture()
        p['buildings'][0]['properties']['base_elevation']=5
        p['buildings'][0]['properties']['balconies']=[{'id':'south','footprint':{'type':'Polygon','coordinates':[[[220014,630007],[220026,630007],[220026,630010],[220014,630010],[220014,630007]]]},'elevation':6,'thickness':.25,'railing_height':0}]
        p['overhead_obstructions']=[{'id':'opening','footprint':{'type':'Polygon','coordinates':[[[220010,630010],[220030,630010],[220030,630030],[220010,630030],[220010,630010]],[[220015,630015],[220025,630015],[220025,630025],[220015,630025],[220015,630015]]]},'elevation':40,'thickness':.2}]
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'test.png'
            result=render_project(p,'aerial',output,width=128,height=96,samples=2,timeout=90,export_models=True)
            self.assertEqual(png_size(output),(128,96))
            with Image.open(output) as image:
                self.assertGreater(len(image.getcolors(128*96)),50)
            with Image.open(result['object_mask_path']) as image:
                self.assertIn(tuple(result['object_mask_labels']['A'])+(255,),set(image.getdata()))
            self.assertGreater(Path(result['blend_path']).stat().st_size,1000)
            saved=json.loads(Path(result['scene_path']).read_text())
            self.assertEqual(saved['camera'],result['camera'])
            self.assertEqual(saved['project'],p)
            self.assertEqual(saved['project']['buildings'][0]['properties']['balconies'][0]['id'],'south')
            self.assertEqual(result['building_fingerprints'],building_fingerprints(p))
            self.assertEqual(result['fov_axis'],'vertical')
            self.assertFalse(result['synthetic'])
            for extension in ('obj','glb','mtl'):
                self.assertEqual(result['delivery_checks']['test.'+extension]['status'],'passed')
                self.assertGreater(Path(result[extension+'_path']).stat().st_size,100)
            self.assertEqual(result['delivery_checks']['test.glb']['details']['cameras'][0]['name'],'camera:aerial')
            self.assertEqual(result['delivery_checks']['test.obj']['details']['cameras'],[])
            self.assertEqual(result['scene_type'],'schematic architectural visualization')
            inspect_script=Path(directory)/'inspect.py'
            inspect_script.write_text("import bpy,json\nc=bpy.context.scene.camera.data\nb=bpy.data.objects['building:A']\nprint('CAMERA_ASSERTIONS '+json.dumps({'fit':c.sensor_fit,'lens':c.lens,'sensor_height':c.sensor_height,'base':min(v.co.z for v in b.data.vertices),'top':max(v.co.z for v in b.data.vertices),'object_names':list(bpy.data.objects.keys()),'overhead_top_area':sum(p.area for p in bpy.data.objects['context:overhead:opening'].data.polygons if p.normal.z>.5)}))\n")
            inspection=subprocess.run([shutil.which('blender'),'--background',result['blend_path'],'--python',str(inspect_script)],capture_output=True,text=True,timeout=30,check=True)
            line=next(line for line in inspection.stdout.splitlines() if line.startswith('CAMERA_ASSERTIONS '))
            observed=json.loads(line.split(' ',1)[1])
            self.assertEqual(observed['fit'],'VERTICAL')
            expected_lens=observed['sensor_height']/(2*math.tan(math.radians(result['camera']['fov'])/2))
            self.assertAlmostEqual(observed['lens'],expected_lens,places=4)
            self.assertAlmostEqual(observed['base'],5)
            self.assertAlmostEqual(observed['top'],35)
            self.assertNotIn('context:street',observed['object_names'])
            self.assertFalse(any(name.startswith(('apron:','context:apron:')) for name in observed['object_names']))
            self.assertFalse(any(name.startswith(('rail:A:south:','post:A:south:')) for name in observed['object_names']))
            self.assertAlmostEqual(observed['overhead_top_area'],300)

    def test_bad_render_requests_rejected_before_blender(self):
        with self.assertRaises(ValueError):render_project(fixture(),width=1)
        with self.assertRaises(ValueError):render_project(fixture(),samples=0)
        with self.assertRaises(ValueError):render_project(fixture(),output_path='/tmp/invalid.jpg')
        p=fixture();p['errors']=['Outside parcel']
        with self.assertRaises(ValueError):render_project(p)
