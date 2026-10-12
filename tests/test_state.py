import copy, tempfile, unittest
from pathlib import Path
from app import validate
from state import edit_building, check_camera, commit, restore, digest, save_atomic
from test_geometry import fixture, polygon

class StateTests(unittest.TestCase):
    def setUp(self):
        data=fixture()
        data['buildings'].append({'geometry':polygon(220060,630060,20),'properties':{'id':'B','height':24,'floors':8}})
        self.project=validate(data);self.project['id']='a'*32
    def test_edit_only_one_object(self):
        before=copy.deepcopy(self.project)
        edited=edit_building(self.project,{'building':'A','color':'#334455','material':'brick'})
        self.assertEqual(before,self.project)
        self.assertEqual(edited['buildings'][1],before['buildings'][1])
        self.assertEqual(edited['buildings'][0]['geometry'],before['buildings'][0]['geometry'])
        self.assertTrue(edited['last_diff']['geometry_preserved'])
        self.assertEqual(edited['last_diff']['unchanged_buildings']['B'],digest(before['buildings'][1]))
    def test_camera_survives_version_restore(self):
        camera=check_camera({'position':[80,-100,90],'target':[0,0,10],'fov':48})
        with_camera=copy.deepcopy(self.project);with_camera['cameras']['saved']=camera
        with_camera=commit(self.project,with_camera,'camera')
        edited=edit_building(with_camera,{'building':'A','color':'#334455'})
        restored=restore(edited,2)
        self.assertEqual(restored['cameras'],with_camera['cameras'])
        self.assertEqual(restored['buildings'],with_camera['buildings'])
        self.assertEqual(restored['version'],4)
    def test_invalid_camera(self):
        for cam in [{'position':[0,0,0],'target':[0,0,0]}, {'position':[float('nan'),0,2],'target':[0,0,0]}]:
            with self.assertRaises(ValueError):check_camera(cam)
    def test_balcony_edit_preserves_neighbors(self):
        b={'id':'deck','footprint':polygon(220010,630007,3),'elevation':3,'thickness':.25,'railing_height':1.05}
        edited=edit_building(self.project,{'building':'A','balconies':[b]})
        self.assertEqual(edited['buildings'][1],self.project['buildings'][1])
        restored=restore(edited,1)
        self.assertEqual(restored['buildings'],self.project['buildings'])
    def test_invalid_balcony_outside(self):
        with self.assertRaises(ValueError):edit_building(self.project,{'building':'A','balconies':[{'id':'x','footprint':polygon(220099,630099,5),'elevation':3}]})
    def test_atomic_serialization_persists_snapshots(self):
        import json
        edited=edit_building(self.project,{'building':'A','material':'glass'})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'project.json';save_atomic(path,edited)
            recovered=json.loads(path.read_text())
            self.assertEqual(recovered,edited)
            self.assertEqual(restore(recovered,1)['buildings'],self.project['buildings'])
