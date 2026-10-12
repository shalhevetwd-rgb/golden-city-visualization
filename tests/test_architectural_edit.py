import hashlib
import json
import shutil
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path
from architectural_edit import edit, catalogue
from delivery_validation import ValidationError

ROOT=Path(__file__).resolve().parents[1]


class ArchitecturalEditRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.request={'operation':'facade_color','building_id':'A','color':[.3,.4,.5],
                      'source_sha256':'a'*64}

    def tearDown(self):self.temp.cleanup()

    def test_invalid_controls_rejected_before_creating_a_version(self):
        for field,value in [('building_id','../../private'),('operation','move_building'),
                            ('color',[True,0,0]),('color',[float('nan'),0,0]),
                            ('camera','made-up'),('samples',0),('source_sha256','not-a-version')]:
            request={**self.request,field:value}
            with self.subTest(field=field),self.assertRaises(ValueError):edit(request,self.root,self.root/'data')
        self.assertFalse((self.root/'data').exists())

    def test_unknown_or_changed_source_cannot_start_worker(self):
        with self.assertRaises(ValueError):edit(self.request,self.root,self.root/'data')
        folder=self.root/'outputs/v3';folder.mkdir(parents=True);(folder/'model.blend').write_bytes(b'changed')
        with self.assertRaises(ValueError):edit(self.request,self.root,self.root/'data')
        self.assertFalse((self.root/'data').exists())

    def test_empty_history_is_a_valid_empty_catalogue(self):
        self.assertEqual(catalogue(self.root/'data'),[])


@unittest.skipUnless(shutil.which('blender'),'Blender required for native scene/render lifecycle')
class ArchitecturalEditLifecycleTests(unittest.TestCase):
    def test_actual_native_fork_render_and_persistent_catalogue(self):
        # A minimal independently generated test fixture keeps regression fast;
        # the delivered full v3 scene gets a separate actual integration run.
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'outputs/v3';folder.mkdir(parents=True)
            (root/'engine').mkdir()
            for name in ('edit_model.py','scene_audit.py','constraints.py','render_context.py','native_edit_render.py'):
                shutil.copyfile(ROOT/'engine'/name,root/'engine'/name)
            source=folder/'model.blend';script=root/'fixture.py'
            script.write_text('''import bpy,sys,json,hashlib
from pathlib import Path
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=sys.argv[sys.argv.index('--')+2])
for existing in list(bpy.data.objects):
 if existing.type=='CAMERA':bpy.data.objects.remove(existing,do_unlink=True)
material=bpy.data.materials.new('Fixture facade');material.use_nodes=True
material.diffuse_color=(.6,.6,.5,1)
material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(.6,.6,.5,1)
source={}
for o in bpy.data.objects:
 if o.type!='MESH':continue
 raw={'vertices':[list(v.co) for v in o.data.vertices],'faces':[list(p.vertices) for p in o.data.polygons],'matrix':[list(row) for row in o.matrix_world]}
 source[o.name]={'geometry':hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()}
 if o.name.startswith('building:'):
  o['owner']=o.name.split(':')[-1];o['role']='facade';o.data.materials.clear();o.data.materials.append(material)
 else:o['owner']='context';o['role']='context'
for name,position in [('aerial',(88,-116,79)),('street',(40,-55,1.7)),('architectural',(45,-60,22))]:
 bpy.ops.object.camera_add(location=position);camera=bpy.context.object;camera.name='camera:'+name
 camera.rotation_euler=(Vector((-15,-5,12))-camera.location).to_track_quat('-Z','Y').to_euler();camera['owner']='context';camera['role']='camera'
bpy.ops.object.light_add(type='SUN',location=(0,-2,6));bpy.context.object.data.energy=2
bpy.context.scene.world=bpy.data.worlds.new('Fixture world');bpy.context.scene.world.use_nodes=True
bpy.context.scene['classification']='synthetic_architectural_proposal_not_real_site'
bpy.context.scene.camera=bpy.data.objects['camera:aerial']
output=sys.argv[sys.argv.index('--')+1]
Path(output).parent.joinpath('geometry-report.json').write_text(json.dumps({'synthetic':True,'source_meshes':source,'buildings':{k:{'source_window_rows':v} for k,v in {'A':10,'B':8,'C':6}.items()}}))
bpy.ops.wm.save_as_mainfile(filepath=output)
''')
            process=subprocess.run(['blender','--background','--factory-startup','--disable-autoexec','--threads','1','--python-exit-code','1','--python',str(script),'--',str(source),str(ROOT/'baseline/aerial.glb')],capture_output=True,text=True,timeout=90)
            self.assertEqual(process.returncode,0,process.stderr[-2000:])
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            request={'operation':'facade_color','building_id':'A','color':[.7,.2,.1],
                     'source_sha256':digest,'camera':'street','width':128,'height':96,'samples':4}
            result=edit(request,root,root/'data')
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
            self.assertTrue(result['geometry_preserved'])
            self.assertTrue(result['material_export_verified'])
            self.assertEqual(result['outside_target_changed'],[])
            self.assertFalse(result['pixel_identity_claim'])
            self.assertEqual(result['camera'],'street')
            self.assertEqual(catalogue(root/'data'),[result])
            generated=root/'data/architectural-edits'/result['version_id']
            evidence=json.loads((generated/'edit-check.json').read_text())
            self.assertTrue(evidence['native_reopen_verified'])
            self.assertEqual(evidence['changed_objects'],['building:A'])
            rendered=json.loads((generated/'image.render.json').read_text())
            self.assertTrue(rendered['source_geometry_pre_render_check']['passed'])
            self.assertEqual(rendered['source_geometry_pre_render_check']['source_meshes_checked'],15)
            raw=(generated/'edited.glb').read_bytes()
            document=json.loads(raw[20:20+struct.unpack_from('<I',raw,12)[0]])
            self.assertEqual(len(document.get('cameras',[])),3)
            def facade_color(name):
                node=next(n for n in document['nodes'] if n.get('name')==name)
                index=document['meshes'][node['mesh']]['primitives'][0]['material']
                return document['materials'][index]['pbrMetallicRoughness']['baseColorFactor'][:3]
            for actual,expected in zip(facade_color('building:A'),[.7,.2,.1]):self.assertAlmostEqual(actual,expected,places=6)
            for actual,expected in zip(facade_color('building:B'),[.6,.6,.5]):self.assertAlmostEqual(actual,expected,places=6)
            (generated/'image.png').write_bytes(b'corrupt')
            with self.assertRaises(ValidationError):catalogue(root/'data')
