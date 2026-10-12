import copy, unittest
from app import validate, model_obj

def polygon(x,y,size):
    return {'type':'Polygon','coordinates':[[[x,y],[x+size,y],[x+size,y+size],[x,y+size],[x,y]]]}
def fixture():
    return {'crs':'EPSG:2039','boundary':polygon(220000,630000,100),'buildings':[{'geometry':polygon(220010,630010,20),'properties':{'id':'A','height':30,'floors':10}}]}
class GeometryTests(unittest.TestCase):
    def test_valid_and_approval_gate(self):
        p=validate(fixture()); self.assertEqual(p['errors'],[])
        with self.assertRaises(ValueError): model_obj(p)
        p['approved']=True; self.assertEqual(model_obj(p).count('\nv '),8)
    def test_outside(self):
        f=fixture(); f['buildings'][0]['geometry']=polygon(220090,630090,20)
        self.assertTrue(validate(f)['errors'])
    def test_overlap(self):
        f=fixture(); b=copy.deepcopy(f['buildings'][0]); b['properties']['id']='B';f['buildings'].append(b)
        self.assertTrue(validate(f)['errors'])
    def test_geographic_rejected(self):
        f=fixture();f['crs']='EPSG:4326'
        with self.assertRaises(ValueError):validate(f)
    def test_duplicate_id(self):
        f=fixture();f['buildings']*=2
        with self.assertRaises(ValueError):validate(f)
    def test_invalid_polygon(self):
        f=fixture();f['boundary']['coordinates']=[[[0,0],[1,1],[1,0],[0,1],[0,0]]]
        with self.assertRaises(ValueError):validate(f)
if __name__=='__main__': unittest.main()
