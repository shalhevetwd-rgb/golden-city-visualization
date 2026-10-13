from pathlib import Path
import base64,json,re,argparse
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/site-v4'
base=(ROOT/'golden-city-viewer.html').read_text()
importmap=re.search(r'<script type="importmap">.*?</script>',base,re.S).group(0)
license=re.search(r'<details><summary>רישיון Three.js</summary>.*?</details>',base,re.S).group(0)
names=('existing','cleared','planted')
payload={'models':{n:base64.b64encode((OUT/(n+'.glb')).read_bytes()).decode() for n in names},
         'scenes':{n:json.loads((OUT/(n+'.json')).read_text()) for n in names},
         'plan':json.loads((OUT/'plan-request.json').read_text()),'report':json.loads((OUT/'geometry-report.json').read_text())}
pictures=''.join('<figure><img alt="'+n+' — synthetic" src="data:image/png;base64,'+base64.b64encode((OUT/(n+'.png')).read_bytes()).decode()+'"><figcaption>'+label+'</figcaption></figure>' for n,label in zip(names,['מצב קיים','קרקע נקייה','בינוי רעיוני']))
areas=''.join('<label>'+label+' — מ״ר נפרדים<input id="area-'+key+'" type="number" min="0" placeholder="חסר"></label>' for key,label in zip(('core','elevators','stairs','corridors','shafts','shared','lobby'),('גרעין שיורי','מעליות','חדרי מדרגות','מסדרונות','פירים','שטחים משותפים','קומת לובי')))
text=(ROOT/'engine/site_clear_template.html').read_text()
icon=re.search(r'<link rel="icon"[^>]*>',(ROOT/'engine/viewer_template.html').read_text()).group(0)
for key,value in {'ICON':icon,'IMPORTMAP':importmap,'LICENSE':license,'PICTURES':pictures,'AREAS':areas,'PAYLOAD':json.dumps(payload,ensure_ascii=False).replace('</','<\\/'),'MODULE':(ROOT/'engine/site_clear_viewer.mjs').read_text()}.items():text=text.replace('__'+key+'__',value)
parser=argparse.ArgumentParser();parser.add_argument('--destination',type=Path,default=OUT/'viewer.html');args=parser.parse_args();args.destination.parent.mkdir(parents=True,exist_ok=True);args.destination.write_text(text)
print('Standalone site viewer created',len(text.encode()),'bytes')
