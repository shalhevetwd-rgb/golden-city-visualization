"""Read-only Jerusalem public ArcGIS intake. Geometry is never repaired/guessed.

Official municipal source != registered survey, current-state certification,
rights to redistribute, or a verified bare-earth terrain model. Data and imagery
must remain private unless their redistribution rights are established.
"""
import copy,hashlib,json,math,re,urllib.parse,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from shapely.geometry import Polygon,MultiPolygon,Point,LineString,MultiLineString,mapping,shape
from shapely.ops import unary_union
BASE='https://gisviewer.jerusalem.muni.il/arcgis/rest/services/BaseLayers/MapServer'

def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def geometry(esri):
 def xy(point):
  if not isinstance(point,list) or len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point):raise ValueError('Only finite supplied XY is supported; no inferred Z')
  return point
 if 'rings' in esri:
  rings=esri['rings'];polys=[]
  for ring in rings:
   if len(ring)<4 or ring[0]!=ring[-1]:raise ValueError('Source ring is not closed')
   for p in ring:xy(p)
   poly=Polygon(ring)
   if not poly.is_valid or poly.area<=0:raise ValueError('Invalid source ring; repair forbidden')
   polys.append(poly)
  for i,a in enumerate(polys):
   for b in polys[i+1:]:
    if a.intersection(b).area>1e-8 and not a.covers(b) and not b.covers(a):raise ValueError('Overlapping source rings')
  parent=[]
  for i,p in enumerate(polys):
   containers=[j for j,q in enumerate(polys) if i!=j and q.area>p.area and q.covers(p)]
   parent.append(min(containers,key=lambda j:polys[j].area) if containers else None)
  def depth(i):return 0 if parent[i] is None else depth(parent[i])+1
  outputs=[Polygon(rings[i],[rings[j] for j in range(len(rings)) if parent[j]==i]) for i in range(len(rings)) if depth(i)%2==0]
  result=outputs[0] if len(outputs)==1 else MultiPolygon(outputs)
 elif 'paths' in esri:
  paths=esri['paths']
  for path in paths:
   for p in path:xy(p)
  result=LineString(paths[0]) if len(paths)==1 else MultiLineString(paths)
 elif 'x' in esri and 'y' in esri:result=Point(xy([esri['x'],esri['y']]))
 else:raise ValueError('Unsupported source geometry')
 if result.is_empty or not result.is_valid:raise ValueError('Invalid source geometry; no automatic repair')
 return mapping(result)

def collection(document,provenance):
 if document.get('error'):raise ValueError('ArcGIS error: '+json.dumps(document['error']))
 if document.get('exceededTransferLimit'):raise ValueError('Incomplete ArcGIS response; not a complete context')
 sr=document.get('spatialReference',{});wkid=sr.get('latestWkid',sr.get('wkid'))
 if any(f.get('geometry') is not None for f in document.get('features',[])) and wkid!=2039:raise ValueError('Source CRS must explicitly be EPSG:2039')
 features=[]
 for f in document.get('features',[]):
  g=geometry(f['geometry']) if f.get('geometry') is not None else None;props=copy.deepcopy(f['attributes']);props['source_geometry_sha256']=sha(f.get('geometry'));features.append({'type':'Feature','geometry':g,'properties':props})
 return {'type':'FeatureCollection','crs':{'type':'name','properties':{'name':'EPSG:2039'}} if wkid==2039 else None,'source':copy.deepcopy(provenance),'features':features,'role':'existing_source_observations','proposed_model':False,'heights_and_vertical_datum_verified':False,'geometry_available':all(f['geometry'] is not None for f in features)}

def fetch(layer,params,directory,slug):
 if not isinstance(layer,int) or layer<0 or not re.fullmatch(r'[a-z0-9-]+',slug):raise ValueError('Invalid municipal query')
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);url=BASE+'/'+str(layer)+'/query?'+urllib.parse.urlencode({**params,'outSR':2039,'f':'pjson'})
 record={'url':url,'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),'provider':'Jerusalem municipal public ArcGIS','operation':'read-only public Query; no GP/ExportTiles','redistribution_license_verified':False}
 try:
  with urllib.request.urlopen(url,timeout=30) as response:
   raw=response.read(12_000_001)
   if not raw:raise ValueError('Source response empty')
   if len(raw)>12_000_000:raise ValueError('Source response exceeds12MB; restrict fields or request attributes only; no geometry generalization inferred')
   record.update(http_status=response.status,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
 except (urllib.error.URLError,TimeoutError,OSError) as error:
  record.update(status='failed',error=str(error),http_status=getattr(error,'code',None))
  (directory/(slug+'-'+__import__('uuid').uuid4().hex+'.failed.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
  raise RuntimeError('Municipal source read failed: '+str(error)) from error
 name=slug+'-'+record['sha256']+'.json';path=directory/name
 if path.exists() and path.read_bytes()!=raw:raise ValueError('Source snapshot corrupted')
 if not path.exists():path.write_bytes(raw)
 if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Source reopen hash failed')
 record['source_file']=name
 metadata=directory/(name+'.provenance.json')
 if metadata.exists():metadata=directory/(name+'-'+__import__('uuid').uuid4().hex+'.provenance.json')
 metadata.write_text(json.dumps(record,ensure_ascii=False,indent=2));doc=json.loads(path.read_bytes())
 return collection(doc,record)

def spatial_audit(parcels,buildings,addresses):
 polygons=[shape(f['geometry']) for f in parcels['features']];boundary=unary_union(polygons);inside=[];crossing=[];outside=[]
 for f in buildings['features']:
  poly=shape(f['geometry']);record={'id':str(f['properties']['OBJECTID']),'source_sha256':sha(f)}
  if boundary.covers(poly):inside.append(record)
  elif boundary.intersection(poly).area>1e-7:crossing.append(record)
  else:outside.append(record)
 linked=[]
 for f in addresses['features']:
  point=shape(f['geometry']);ids=[p['properties']['HELKA_SHOW'] for p in parcels['features'] if shape(p['geometry']).covers(point)]
  if ids:linked.append({'parcels':ids,'attributes':copy.deepcopy(f['properties'])})
 units={}
 for link in linked:
  a=link['attributes'];key=a.get('semel_bait') or str(a.get('StreetCode'))+'/'+str(a.get('BldNum'))+'/'+str(a.get('BLDG_LETTE',''))
  count=a.get('NUM_APTS_C')
  if type(count) is not int or count<=0 or key in units and units[key]!=count:units={};break
  units[key]=count
 return {'status':'source_xy_verified_not_survey_certificate','crs':'EPSG:2039','parcel_union':mapping(boundary),'union_area_m2':boundary.area,'inside':inside,'crossing_not_removable':crossing,'outside':outside,'address_spatial_links':linked,'municipal_units_in_candidate_parcels':sum(units.values()) if units else None,'unit_count_scope_requires_address_verification':True,'geometry_moved':False,'terrain_inferred':False,'demolition_approved':False,'building_3d_models_created':False,'coverage':'Loaded context only; no claim about unqueried city objects'}

def collect(block,parcels,directory,street='עולי הגרדום',address_numbers=(113,115)):
 if type(block) is not int or block<=0 or not isinstance(parcels,list) or not 1<=len(parcels)<=20 or any(type(p) is not int or p<=0 for p in parcels):raise ValueError('Explicit block and parcel numbers required')
 if not isinstance(street,str) or not street.strip() or len(street)>120 or not isinstance(address_numbers,(list,tuple)) or not 1<=len(address_numbers)<=20 or any(type(n) is not int or n<=0 for n in address_numbers):raise ValueError('Invalid address identity')
 p=fetch(46,{'where':f"GUSH_NO='{block}' AND HELKA_SHOW IN ("+','.join("'"+str(n)+"'" for n in parcels)+')','outFields':'*','returnGeometry':'true'},directory,'parcels')
 if not p.get('geometry_available') or any(int(f['properties']['GUSH_NO'])!=block for f in p['features']) or {int(f['properties']['HELKA_SHOW']) for f in p['features']}!=set(parcels):raise ValueError('Requested parcel identities not all returned')
 union=unary_union([shape(f['geometry']) for f in p['features']]);x0,y0,x1,y1=union.bounds
 envelope={'xmin':x0-60,'ymin':y0-60,'xmax':x1+60,'ymax':y1+60,'spatialReference':{'wkid':2039}}
 params={'geometry':json.dumps(envelope,separators=(',',':')),'geometryType':'esriGeometryEnvelope','inSR':2039,'spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true'}
 b=fetch(370,params,directory,'buildings-2022');a=fetch(10,params,directory,'addresses-context');h=fetch(7,params,directory,'height-grid');c=fetch(2080,{**params,'returnGeometry':'false'},directory,'contour-attributes')
 if not isinstance(street,str) or not street.strip() or len(street)>120 or any(type(n) is not int or n<=0 for n in address_numbers):raise ValueError('Invalid address identity')
 street=street.replace("'","''");exact=fetch(10,{'where':"StreetName1='"+street+"' AND BldNum IN ("+','.join("'"+str(n)+"'" for n in address_numbers)+')','outFields':'*','returnGeometry':'true'},directory,'requested-addresses')
 audit=spatial_audit(p,b,a);audit['requested_addresses_returned']=len(exact['features']);audit['requested_address_parcel_link_verified']=False
 if exact['features']:
  audit['requested_address_parcel_link_verified']=all(union.covers(shape(f['geometry'])) for f in exact['features']) and {int(f['properties']['BldNum']) for f in exact['features']}==set(address_numbers)
 audit['identity_requires_user_resolution']=not audit['requested_address_parcel_link_verified'];audit['bare_earth_dtm_verified']=False;audit['vertical_datum_verified']=False
 return {'parcels':p,'buildings':b,'addresses':a,'requested_addresses':exact,'height_points':h,'contours':c,'audit':audit,'query_envelope':envelope}


def discover(block,street,address_numbers,directory):
    """Address+block source discovery; zero matches never produce a location."""
    if type(block) is not int or block<=0 or not isinstance(street,str) or not street.strip() or len(street)>120 or not isinstance(address_numbers,(list,tuple)) or not 1<=len(address_numbers)<=20 or any(type(n) is not int or n<=0 for n in address_numbers):raise ValueError('Explicit valid address/block required')
    escaped=street.replace("'","''")
    addresses=fetch(10,{'where':"StreetName1='"+escaped+"' AND BldNum IN ("+','.join("'"+str(n)+"'" for n in address_numbers)+')','outFields':'*','returnGeometry':'true'},directory,'address-discovery')
    def empty_result(reason):
        result={k:{'type':'FeatureCollection','features':[],'geometry_available':False,'proposed_model':False} for k in ('parcels','buildings','addresses','height_points','contours')}
        result['requested_addresses']=addresses
        result['audit']={'requested_address_parcel_link_verified':False,'identity_requires_user_resolution':True,'reason':reason,'absence_is_not_proof_address_does_not_exist':True,'geometry_created':False,'demolition_approved':False}
        return result
    if not addresses['features'] or {int(f['properties']['BldNum']) for f in addresses['features']}!=set(address_numbers):return empty_result('Requested addresses not all returned; no parcel or coordinate assumed')
    points=[[f['geometry']['coordinates'][0],f['geometry']['coordinates'][1]] for f in addresses['features']]
    parcels=fetch(46,{'where':f"GUSH_NO='{block}'",'geometry':json.dumps({'points':points,'spatialReference':{'wkid':2039}},separators=(',',':')),'geometryType':'esriGeometryMultipoint','inSR':2039,'spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true'},directory,'address-parcel-discovery')
    if not parcels['features']:return empty_result('Address points did not match requested block; no parcel assumed')
    return collect(block,sorted({int(f['properties']['HELKA_SHOW']) for f in parcels['features']}),directory,street,address_numbers)
