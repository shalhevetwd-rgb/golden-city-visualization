"""Source-preserving cadastral map on a limited official public map viewport.

No buildings, roads, terrain or heights are generated. Ordinary cached map tiles
are read, not ExportTiles/GenericExport GP. No redistribution licence is inferred.
"""
import concurrent.futures,hashlib,io,json,math,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from pyproj import CRS
from shapely.geometry import shape
ORTHO='https://gisviewer.jerusalem.muni.il/arcgis/rest/services/Ortho052026/MapServer'

def render(observations,directory,service=ORTHO,layer_label='2026-05'):
    url=urllib.parse.urlparse(service)
    if url.scheme!='https' or url.hostname!='gisviewer.jerusalem.muni.il' or not url.path.startswith('/arcgis/rest/services/') or not url.path.endswith('/MapServer') or url.query:raise ValueError('Only an explicit official municipal MapServer is allowed')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(service+'?f=pjson',timeout=30) as response:raw=response.read(500000)
    meta=json.loads(raw);(directory/'orthophoto-service-metadata.json').write_bytes(raw)
    t=meta['tileInfo'];wkid=t['spatialReference'].get('latestWkid',t['spatialReference'].get('wkid'))
    if wkid!=2039 or t['cols']!=256 or t['rows']!=256:raise ValueError('Explicit source EPSG2039/256px cache required')
    box=observations['query_envelope'];lod=next((x for x in t['lods'] if x['level']==8),None)
    if not lod:raise ValueError('Source display level8 unavailable; do not invent a resolution')
    res=lod['resolution'];span=res*256;ox=t['origin']['x'];oy=t['origin']['y']
    c0=math.floor((box['xmin']-ox)/span);c1=math.floor((box['xmax']-ox)/span);r0=math.floor((oy-box['ymax'])/span);r1=math.floor((oy-box['ymin'])/span)
    items=[(row,col) for row in range(r0,r1+1) for col in range(c0,c1+1)]
    if not 1<=len(items)<=24:raise ValueError('Source viewport exceeds24tiles; no bulk tile collection')
    cache=directory/'map-viewport-tiles';cache.mkdir(exist_ok=True)
    def tile(item):
        row,col=item;u=f'{service}/tile/8/{row}/{col}'
        with urllib.request.urlopen(u,timeout=30) as response:data=response.read(2000000);status=response.status
        with Image.open(io.BytesIO(data)) as img:img.load();fmt=img.format;picture=img.convert('RGB')
        if picture.size!=(256,256) or fmt not in {'PNG','JPEG'}:raise ValueError('Published tile dimensions/format invalid')
        filename=f'8-{row}-{col}'+('.png' if fmt=='PNG' else '.jpg');(cache/filename).write_bytes(data)
        record={'url':u,'http_status':status,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),'operation':'normal published map viewport, not ExportTiles GP'}
        (cache/(filename+'.provenance.json')).write_text(json.dumps(record,indent=2));return row,col,picture,record
    mosaic=Image.new('RGB',((c1-c0+1)*256,(r1-r0+1)*256));records=[]
    for row,col,picture,record in concurrent.futures.ThreadPoolExecutor(6).map(tile,items):mosaic.paste(picture,((col-c0)*256,(row-r0)*256));records.append(record)
    extent={'xmin':ox+c0*span,'xmax':ox+(c1+1)*span,'ymax':oy-r0*span,'ymin':oy-(r1+1)*span,'crs':'EPSG:2039'}
    mosaic.save(directory/'orthophoto-map-view.png');(directory/'orthophoto-map-view.pgw').write_text('\n'.join(map(str,[res,0,0,-res,extent['xmin']+res/2,extent['ymax']-res/2]))+'\n');(directory/'orthophoto-map-view.prj').write_text(CRS.from_epsg(2039).to_wkt()+'\n')
    proof={'source':service,'layer_label':layer_label,'actual_capture_date_verified':False,'actual_imagery_gsd_verified':False,'map_cache_pixel_resolution_m':res,'extent':extent,'size':mosaic.size,'tiles':records,'redistribution_license_verified':False};(directory/'orthophoto-provenance.json').write_text(json.dumps(proof,indent=2))
    parcels=observations['parcels']['features'];buildings=observations['buildings']['features'];audit=observations['audit'];inside={x['id'] for x in audit['inside']};W,H=mosaic.size
    annotated=mosaic.copy();draw=ImageDraw.Draw(annotated);font_path='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    if not Path(font_path).is_file():raise ValueError('Verified Hebrew/Latin map font unavailable')
    large=ImageFont.truetype(font_path,27);small=ImageFont.truetype(font_path,16)
    def pixel(p):return ((p[0]-extent['xmin'])/res,(extent['ymax']-p[1])/res)
    def rings(g):return g['coordinates'] if g['type']=='Polygon' else [r for polygon in g['coordinates'] for r in polygon]
    for f in buildings:
        color='#00e8ff' if str(f['properties']['OBJECTID']) in inside else '#c3d5d9'
        for ring in rings(f['geometry']):draw.line([pixel(p) for p in ring],fill=color,width=2)
    for f in parcels:
        for ring in rings(f['geometry']):draw.line([pixel(p) for p in ring],fill='#ef25ff',width=5)
        p=shape(f['geometry']).representative_point();x,y=pixel([p.x,p.y]);label=f['properties']['HELKA_SHOW'];bounds=draw.textbbox((x,y),label,font=large);draw.rectangle((bounds[0]-5,bounds[1]-5,bounds[2]+5,bounds[3]+5),fill='white',outline='#871f93');draw.text((x,y),label,font=large,fill='#871f93')
    draw.line((45,90,45,35),fill='white',width=4);draw.polygon([(45,25),(36,43),(54,43)],fill='white');draw.text((80,35),'צפון רשת',font=small,direction='rtl',fill='white',stroke_fill='black',stroke_width=1)
    draw.rectangle((40,H-42,40+20/res,H-36),fill='white');draw.text((40,H-70),'20 m',font=small,fill='white',stroke_fill='black',stroke_width=1)
    canvas=Image.new('RGB',(W,H+210),'white');canvas.paste(annotated,(0,110));d=ImageDraw.Draw(canvas);block=str(parcels[0]['properties']['GUSH_NO']);ids=', '.join(f['properties']['HELKA_SHOW'] for f in parcels)
    d.text((W-20,15),'מפת בדיקת חלקות בגוש '+block,font=large,anchor='ra',direction='rtl',fill='#183e32');d.text((W-20,55),'גבולות ממקור עירוני: '+ids+' | EPSG:2039 | תצלום: '+layer_label,font=small,anchor='ra',direction='rtl',fill='#183e32')
    warning='נמצא שיוך מרחבי לכתובות — אין תעודת מדידה' if audit.get('requested_address_parcel_link_verified') else 'שיוך לכתובת שנמסרה לא אומת — נדרשת הכרעת המשתמשת'
    d.text((W-20,85),warning,font=small,anchor='ra',direction='rtl',fill='#962424');d.text((W-20,H+126),'סגול: חלקות | תכלת: תכסיות בתחום | אפור: הקשר שנקלט | מקור: GIS עירוני',font=small,anchor='ra',direction='rtl',fill='#183e32');d.text((W-20,H+158),'למחקר פרטי; רישיון הפצה מסחרית ותאריך טיסה לא אומתו. אין הריסה או בינוי מוצע.',font=small,anchor='ra',direction='rtl',fill='#962424');canvas.save(directory/'parcel-air-map.png')
    return {'map_path':'parcel-air-map.png','sha256':hashlib.sha256((directory/'parcel-air-map.png').read_bytes()).hexdigest(),'source':proof,'proposed_model_created':False,'terrain_inferred':False}
