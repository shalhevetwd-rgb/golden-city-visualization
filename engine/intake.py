"""Local source inspection, no external upload. DWG/DXF not advertised here."""
from pathlib import Path
import json,struct,hashlib
from PIL import Image

def inspect(path):
 p=Path(path);suffix=p.suffix.lower();r={'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'supported':False}
 if suffix in ('.png','.jpg','.jpeg'):
  with Image.open(p) as im:im.verify()
  with Image.open(p) as im:r.update(supported=True,dimensions=list(im.size),meaning='source image only; no camera or scale inferred')
 elif suffix=='.glb':
  raw=p.read_bytes();magic,v,n=struct.unpack_from('<4sII',raw);assert magic==b'glTF' and v==2 and n==len(raw);length,typ=struct.unpack_from('<II',raw,12);assert typ==0x4E4F534A;d=json.loads(raw[20:20+length]);r.update(supported=True,meshes=len(d.get('meshes',[])),nodes=len(d.get('nodes',[])),meaning='geometry import; no site CRS or floor schedule inferred')
 elif suffix=='.geojson':
  d=json.loads(p.read_text());assert d['type']=='FeatureCollection';r.update(supported=False,syntax_read=True,features=len(d['features']),meaning='Structure only; GIS processing with an actual source has not been verified in this product')
 return r
