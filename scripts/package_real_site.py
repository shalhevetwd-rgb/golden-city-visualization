"""Private source-study delivery gate; no inferred models and no public upload.

Each file is reopened independently; spatial files use OGR/GDAL in addition to
JSON, PNG uses Pillow+FFmpeg, PDF uses pdfinfo/pdftoppm. ZIP is extracted and all
members are tested again. The utility packages a supplied existing case folder.
"""
import argparse,hashlib,json,subprocess,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from delivery_guard import verify_for_delivery
from delivery_validation import validate_archive
FILES=('parcel-air-map.png','orthophoto-map-view.png','orthophoto-map-view.pgw','orthophoto-map-view.prj','orthophoto-provenance.json','parcel-map-geometry.json','case-viewer-report.html','source-verification-report.pdf','source-verification-report.json','source-preservation-report.json','area-reconciliation.json','parcels.geojson','buildings.geojson','existing-source-working.geojson','addresses.geojson','height_points.geojson','contours.json','audit.json','query_envelope.json')

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def spatial_reopen(folder):
 reports={}
 for name in ('parcels.geojson','buildings.geojson','existing-source-working.geojson','addresses.geojson','height_points.geojson'):
  source=json.loads((folder/name).read_text());result=subprocess.run(['ogrinfo','-al','-so','-json',str(folder/name)],capture_output=True,text=True,check=True);data=json.loads(result.stdout);layer=data['layers'][0]
  if layer['featureCount']!=len(source['features']):raise ValueError('OGR lost features: '+name)
  actual=layer['geometryFields'][0].get('coordinateSystem',{}).get('projjson',{}).get('id',{})
  if actual!={'authority':'EPSG','code':2039}:raise ValueError('OGR CRS mismatch: '+name)
  # A second OGR translation reopens complete geometry/attributes, not counts only.
  with tempfile.TemporaryDirectory(prefix='golden-ogr-source-roundtrip-') as tmp:
   target=Path(tmp)/'reimported.geojson';subprocess.run(['ogr2ogr','-f','GeoJSON',str(target),str(folder/name)],capture_output=True,check=True)
   parsed=json.loads(target.read_text());original={str(f['properties']['OBJECTID']):f for f in source['features']};reopened={str(f['properties']['OBJECTID']):f for f in parsed['features']}
   if set(original)!=set(reopened):raise ValueError('OGR object inventory changed')
   for key,f in original.items():
    from shapely.geometry import shape
    if not shape(f['geometry']).equals_exact(shape(reopened[key]['geometry']),1e-9):raise ValueError('OGR source geometry changed: '+key)
    for prop,value in f['properties'].items():
     if prop=='source_geometry_sha256':continue
     if reopened[key]['properties'].get(prop)!=value:raise ValueError('OGR source attribute changed: '+prop)
  reports[name]={'reader':'independent OGR full roundtrip','features':layer['featureCount'],'crs':'EPSG:2039','coordinates_and_attributes_preserved':True}
 result=subprocess.run(['gdalinfo','-json',str(folder/'orthophoto-map-view.png')],capture_output=True,text=True,check=True);g=json.loads(result.stdout);world=[float(x) for x in (folder/'orthophoto-map-view.pgw').read_text().split()];expected=[world[4]-world[0]/2-world[2]/2,world[0],world[2],world[5]-world[1]/2-world[3]/2,world[1],world[3]]
 if any(abs(a-b)>1e-8 for a,b in zip(g.get('geoTransform',[]),expected)) or len(g.get('geoTransform',[]))!=6:raise ValueError('GDAL image georeferencing lost')
 reports['orthophoto-map-view.png']={'reader':'independent GDAL raster+worldfile','size':g['size'],'geoTransform':g['geoTransform'],'crs_sidecar':'EPSG:2039 separately independently parsed'}
 return reports

def package(directory):
 folder=Path(directory).resolve();hashes={n:digest(folder/n) for n in FILES};checks={n:verify_for_delivery(folder/n,expected={'sha256':hashes[n]}) for n in FILES};spatial=spatial_reopen(folder)
 manifest={'version':1,'privacy':'private source study; redistribution rights not established','files':[{'name':n,'bytes':(folder/n).stat().st_size,'sha256':hashes[n]} for n in FILES]};(folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
 archive=folder/'real-site-source-study.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for n in (*FILES,'manifest.json'):z.write(folder/n,n)
 after=validate_archive(archive,expected_manifest=manifest)
 with tempfile.TemporaryDirectory(prefix='golden-real-case-post-package-') as tmp:
  with zipfile.ZipFile(archive) as z:z.extractall(tmp)
  reopened=spatial_reopen(Path(tmp))
 if any(digest(folder/n)!=v for n,v in hashes.items()):raise ValueError('Case file changed during packaging')
 report={'status':'passed','privacy':'must not upload to public repository','files':checks,'spatial_reimports':spatial,'after_packaging':after,'spatial_reimports_after_packaging':reopened,'real_3d_model_created':False,'demolition_performed':False,'recipient_access':'not_verified','case_source_files':list(FILES),'archive':{'name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive)}};(folder/'delivery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));verify_for_delivery(folder/'delivery-report.json');return report
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args();result=package(args.directory);print(json.dumps({'status':result['status'],'files':len(FILES),'zip_bytes':result['archive']['bytes'],'private_only':True,'recipient_access':'not_verified'}))
