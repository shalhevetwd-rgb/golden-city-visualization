"""Deliver the brief itself as an offline form, never an offline model approval."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'planning.html').read_text();js=(ROOT/'planning.js').read_text()
js=js.replace("const $=id=>", "const offline=true;\nconst $=id=>")
start=js.index('async function api(');end=js.index('async function run(',start)
js=js[:start]+'''async function api(action,extra={}){
 if(action!=='save')throw Error('בדיקת מקורות, היתכנות ואישור מחייב דורשים את שירות המערכת; הקובץ העצמאי אינו מאשר בינוי');
 const b=extra.brief;const a=b.answers;for(const key of nums)if(a[key]!==undefined&&a[key]!=='unknown'&&(!Number.isFinite(a[key])||a[key]<0||(!['multiplier','height_m'].includes(key)&&!Number.isInteger(a[key]))))throw Error('נתון מספרי לא תקין: '+key);
 if(!Number.isFinite(b.apartment_paldalet_m2)||b.apartment_paldalet_m2<=0)throw Error('שטח פלדלת חייב להיות חיובי');
 b.status='review_required';b.approval=null;b.geometry_created=false;
 b.missing_questions=Object.keys(labels).filter(k=>k!=='multiplier'&&a[k]===undefined);if(!a.floor_schedule)b.missing_questions.push('floor_schedule');
 b.quantities={apartments_paldalet_m2:typeof a.units==='number'?a.units*b.apartment_paldalet_m2:null,separate_areas_m2:b.separate_areas_m2,accounted_total_m2:null,statutory_gross_area_verified:false,core_estimate:'נדרש חישוב במנוע השירות; אין גרעין קבוע בקובץ זה'};
 const blob=new Blob([JSON.stringify(b,null,2)],{type:'application/json'}),link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='planning-brief-unapproved.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),10000);return {planning_brief:b};
}
''' +js[end:]
start=js.index("run(async()=>{if(!id)");upload=js[js.index("$('briefFile').onchange="):];js=js[:start]+"$('identity').textContent='שאלון עצמאי למילוי — אינו מחובר לפרויקט';$('status').textContent='מלאי ושמרי את האפיון כ־JSON. הנתונים לא נשלחים לרשת. אין אישור בינוי ללא שירות, נתוני מקור וסקירת משתמש.';\n"+upload
html=html.replace('<script src="/planning.js" defer></script>','<script>'+js.replace('</script','<\\/script')+'</script>')
out=ROOT/'outputs/planning-v5/planning-questionnaire.html';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(html)
print(out.name,out.stat().st_size)
