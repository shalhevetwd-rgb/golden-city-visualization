import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from shapely.geometry import shape
from engine.municipal_gis import geometry,collection,spatial_audit,collect,fetch,discover
from delivery_validation import validate_file,ValidationError


def square(x,y,w=10):return [[x,y],[x+w,y],[x+w,y+w],[x,y+w],[x,y]]
def source(features):return {'spatialReference':{'wkid':2039},'features':features}
def feature(g,props):return {'geometry':g,'attributes':props}

class MunicipalTests(unittest.TestCase):
 def test_polygon_holes_islands_and_original_xy_preserved(self):
  shell=square(220000,630000,20);hole=square(220003,630003,8);island=square(220005,630005,2);second=square(220030,630030,10)
  result=geometry({'rings':[shell,hole,island,second]});self.assertEqual(result['type'],'MultiPolygon');self.assertEqual(shape(result).area,440);self.assertEqual(set(tuple(p) for polygon in result['coordinates'] for ring in polygon for p in ring),set(tuple(p) for ring in [shell,hole,island,second] for p in ring))
 def test_invalid_source_never_repaired_or_given_a_z(self):
  for g in [{'rings':[[[0,0],[1,1],[1,0],[0,1],[0,0]]]},{'rings':[[[0,0],[1,0],[0,1]]]},{'x':float('nan'),'y':2},{'rings':[[[0,0,10],[1,0,10],[0,1,10],[0,0,10]]]}]:
   with self.assertRaises(ValueError):geometry(g)
 def test_wrong_crs_error_or_truncated_response_rejected(self):
  d=source([feature({'x':220000,'y':630000},{'OBJECTID':1})]);d['spatialReference']['wkid']=4326
  for doc in [d,{'error':{'code':500}},{'exceededTransferLimit':True}]:
   with self.assertRaises(ValueError):collection(doc,{})
 def test_attribute_only_data_is_not_a_georeferenced_model(self):
  r=collection({'features':[{'attributes':{'ALTITUDE':760}}]},{});self.assertIsNone(r['crs']);self.assertIsNone(r['features'][0]['geometry']);self.assertFalse(r['geometry_available']);self.assertFalse(r['proposed_model']);self.assertFalse(r['heights_and_vertical_datum_verified'])
 def test_readonly_context_classification_and_address_join(self):
  pars=collection(source([feature({'rings':[square(220000,630000,20)]},{'OBJECTID':1,'HELKA_SHOW':'8'})]),{})
  buildings=collection(source([feature({'rings':[square(220001,630001,5)]},{'OBJECTID':10}),feature({'rings':[square(220018,630002,5)]},{'OBJECTID':11}),feature({'rings':[square(220030,630030,5)]},{'OBJECTID':12})]),{})
  addresses=collection(source([feature({'x':220002,'y':630002},{'OBJECTID':20,'BldNum':'21'})]),{});before=copy.deepcopy(buildings);a=spatial_audit(pars,buildings,addresses);self.assertEqual([x['id'] for x in a['inside']],['10']);self.assertEqual([x['id'] for x in a['crossing_not_removable']],['11']);self.assertEqual([x['id'] for x in a['outside']],['12']);self.assertFalse(a['demolition_approved']);self.assertFalse(a['building_3d_models_created']);self.assertEqual(buildings,before);self.assertEqual(a['address_spatial_links'][0]['parcels'],['8'])
 def test_query_snapshot_reopens_and_metadata_does_not_overwrite(self):
  raw=json.dumps(source([feature({'x':220000,'y':630000},{'OBJECTID':1})])).encode()
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return raw
  with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',return_value=Response()) as url:
   a=fetch(10,{'where':"StreetCode='4325'"},d,'addresses');b=fetch(10,{'where':"StreetCode='4325'"},d,'addresses');self.assertEqual(a['features'],b['features']);self.assertEqual(len(list(Path(d).glob('*.provenance.json'))),2);self.assertIn('https://gisviewer.jerusalem.muni.il/',url.call_args.args[0]);self.assertFalse(a['source']['redistribution_license_verified'])
 def test_address_only_no_match_never_infers_land(self):
  with patch('engine.municipal_gis.fetch',return_value={'type':'FeatureCollection','features':[]}) as f:
   r=discover(30207,'requested street',[113,115],'/tmp/unused-source-test');self.assertFalse(r['audit']['requested_address_parcel_link_verified']);self.assertEqual(r['parcels']['features'],[]);self.assertFalse(r['audit']['geometry_created']);self.assertEqual(f.call_count,1)
 def test_address_discovery_uses_actual_point_and_block_then_collects(self):
  address={'features':[{'geometry':{'coordinates':[220001,630001]},'properties':{'BldNum':'21'}}]};parcel={'features':[{'properties':{'HELKA_SHOW':'8'}}]}
  with patch('engine.municipal_gis.fetch',side_effect=[address,parcel]) as f,patch('engine.municipal_gis.collect',return_value={'source':'actual matching parcel'}) as c:
   r=discover(30207,'requested street',[21],'/tmp/unused-source-test');self.assertEqual(r['source'],'actual matching parcel');self.assertEqual(c.call_args.args[1],[8]);self.assertIn('220001',f.call_args.args[1]['geometry']);self.assertEqual(f.call_args.args[1]['geometryType'],'esriGeometryMultipoint')
 def test_network_failure_recorded_without_fabricating_source(self):
  import urllib.error
  with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('source',403,'Forbidden',{},None)):
   with self.assertRaisesRegex(RuntimeError,'403'):fetch(10,{},d,'addresses')
   files=list(Path(d).glob('*.failed.json'));self.assertEqual(len(files),1);self.assertEqual(json.loads(files[0].read_text())['http_status'],403)
   self.assertFalse(list(Path(d).glob('addresses-*.provenance.json')))
 def test_invalid_block_and_injection_never_reach_network(self):
  with patch('urllib.request.urlopen') as u:
   for block,parcels in [(True,[8]),(30207,[True]),("30207' OR 1=1",[8]),(30207,[])]:
    with self.assertRaises(ValueError):collect(block,parcels,'/tmp/unused-source-test')
   u.assert_not_called()
 def test_georeferencing_sidecars_independent_open(self):
  from pyproj import CRS
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'map.pgw';p.write_text('.25\n0\n0\n-.25\n220000\n630000\n');self.assertEqual(validate_file(p)['details']['coefficients'][0],.25)
   for content in ['0\n0\n0\n0\n1\n2','NaN\n0\n0\n-.25\n0\n0','1\n2']:
    p.write_text(content)
    with self.assertRaises(ValidationError):validate_file(p)
   prj=Path(d)/'map.prj';prj.write_text(CRS.from_epsg(2039).to_wkt());self.assertEqual(validate_file(prj)['details']['epsg'],2039);prj.write_text('invalid WKT')
   with self.assertRaises(ValidationError):validate_file(prj)
