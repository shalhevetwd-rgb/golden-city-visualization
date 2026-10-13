"""Source gathering continues while the user answers the mandatory programme."""
import json,threading,urllib.request,unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import app

class MunicipalApiTests(unittest.TestCase):
 def test_slow_gis_read_does_not_hold_programme_lock_or_create_geometry(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
  def post(path,data):
   with urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}),timeout=10) as r:return json.load(r)
  started=threading.Event();release=threading.Event();result=[];errors=[]
  def collect(*args,**kwargs):
   started.set()
   if not release.wait(8):raise ValueError('test worker timed out')
   return {'audit':{'requested_address_parcel_link_verified':False},**{k:{'type':'FeatureCollection','features':[]} for k in ('parcels','buildings','addresses','height_points','contours')}}
  try:
   project=post('/api/dossier',{'name':'source flow test','address':'source address113','block':30207,'parcels':'8'})
   with patch('engine.municipal_gis.collect',side_effect=collect):
    def gather():
     try:result.append(post('/api/municipal-collect',{'id':project['id'],'block':30207,'parcels':[8],'street':'source street','address_numbers':[113]}))
     except Exception as e:errors.append(str(e))
    worker=threading.Thread(target=gather);worker.start();self.assertTrue(started.wait(3))
    saved=post('/api/planning-save',{'id':project['id'],'brief':{'answers':{'existing_units':12}}});self.assertEqual(saved['planning_brief']['answers']['existing_units'],12);self.assertEqual(saved['buildings'],[])
    release.set();worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);final=result[0]['project'];self.assertEqual(final['planning_brief']['answers']['existing_units'],12);self.assertIsNone(final['boundary']);self.assertEqual(final['buildings'],[]);self.assertFalse(result[0]['proposed_model_created'])
    self.assertFalse(post('/api/site-status',{'id':project['id']})['clearing_allowed'])
  finally:release.set();server.shutdown();server.server_close();thread.join(3)
