"""Human planning brief. No proposal meshes or automatic approval in this module.

UI declarations are auditable consent records, not authenticated signatures or
statutory/professional certification. Physical-source edits invalidate consent.
"""
import copy, hashlib, json, math
from datetime import datetime, timezone

QUESTIONS = {
 'existing_units':'מספר יח״ד קיימות', 'units':'יח״ד מבוקשות או מכפיל',
 'building_count':'מספר מבנים', 'residential_floors':'קומות מגורים למבנה',
 'height_m':'גובה בינוי במטרים', 'has_site_plan':'האם קיימת תוכנית העמדה',
 'constraints':'מגבלות תכנון והנחיות', 'sukkah':'דרישות מרפסות סוכה',
 'additional_uses':'מסחר, ציבור, חניה ושימושים נוספים',
 'design':'סגנון והדמיות השראה', 'views':'מבטי הדמיה מבוקשים',
 'floor_schedule':'קומות כניסה, שירות ומסחר והכללתן במספר הקומות'}
AREAS = ('core','elevators','stairs','corridors','shafts','lobby','shared','resident_room','service','commercial','public','parking')

def sha(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def physical(site):
 return {k:site.get(k) for k in ('crs','boundary','origin','terrain','objects','buildings','topography','identity','stage')}

def initial(known=None):
 return {'answers':copy.deepcopy(known or {}),'apartment_paldalet_m2':100,'status':'needs_answers','provenance':{'apartment_paldalet_m2':'user_session_default'},'approval':None}

def numeric(v,name,positive=True,integer=False):
 if type(v) not in (int,float) or not math.isfinite(v) or (positive and v<=0) or (not positive and v<0) or (integer and int(v)!=v):
  raise ValueError('נתון מספרי לא תקין: '+name)
 return v

def normalize(data):
 if not isinstance(data,dict) or not isinstance(data.get('answers'),dict):raise ValueError('נדרשות תשובות לאפיון')
 out=initial();a=copy.deepcopy(data['answers']);out['answers']=a
 out['apartment_paldalet_m2']=numeric(data.get('apartment_paldalet_m2',100),'פלדלת')
 for k in ('existing_units','units','building_count','residential_floors'):
  if k in a and a[k]!='unknown':numeric(a[k],k,positive=k!='existing_units',integer=True)
 for k in ('height_m','multiplier'):
  if k in a and a[k]!='unknown':numeric(a[k],k)
 if a.get('multiplier') not in (None,'unknown') and a.get('existing_units') not in (None,'unknown'):
  target=a['multiplier']*a['existing_units']
  numeric(target,'יח״ד ממכפיל',integer=True)
  if a.get('units') not in (None,'unknown') and a['units']!=target:raise ValueError('יח״ד ומכפיל אינם עקביים')
  a['units']=int(target)
 if 'has_site_plan' in a and a['has_site_plan'] not in (True,False,'unknown'):raise ValueError('מצב תוכנית העמדה לא תקין')
 if 'views' in a and a['views']!='unknown' and (not isinstance(a['views'],list) or not a['views'] or any(not isinstance(v,str) or not v.strip() for v in a['views'])):raise ValueError('נדרש לפחות מבט אחד')
 f=a.get('floor_schedule')
 if isinstance(f,dict):
  for k in ('entrance','service','commercial'):numeric(f.get(k),k,positive=False,integer=True)
  if type(f.get('included_in_total')) is not bool:raise ValueError('נדרשת הגדרת הכללת קומות שאינן מגורים')
  if a.get('residential_floors') not in (None,'unknown'):
   a['total_floors']=a['residential_floors']+sum(f[k] for k in ('entrance','service','commercial'))
   a['defined_floor_count']=a['total_floors'] if f['included_in_total'] else a['residential_floors']
 missing=[k for k in QUESTIONS if k not in a or a[k] is None or a[k]=='' or a[k]==[]]
 out['missing_questions']=missing;out['status']='needs_answers' if missing else 'review_required'
 out['separate_areas_m2']=copy.deepcopy(data.get('separate_areas_m2',{}))
 if not isinstance(out['separate_areas_m2'],dict):raise ValueError('נדרשת טבלת שטחים')
 for k,v in out['separate_areas_m2'].items():
  if k not in AREAS:raise ValueError('קטגוריית שטח לא מוכרת')
  numeric(v,k,positive=False)
 out['categories_are_disjoint']=data.get('categories_are_disjoint') is True
 out['provenance']=copy.deepcopy(data.get('provenance',{}))
 out['quantities']=quantities(out)
 return out

def quantities(brief):
 a=brief['answers'];u=a.get('units');count=a.get('building_count');floors=a.get('residential_floors')
 net=u*brief['apartment_paldalet_m2'] if type(u) in (int,float) else None
 separate=brief.get('separate_areas_m2',{});missing=[k for k in AREAS if k not in separate]
 estimate=None
 if all(type(v) in (int,float) and v>0 for v in (u,count,floors)):
  per=u/count/floors;total=a.get('total_floors',floors)
  # Explicit preliminary feasibility assumption, never a code requirement.
  height=a.get('height_m') if type(a.get('height_m')) in (int,float) else 0
  lift_count=2 if total>8 or height>28 or per>6 else 1;stairs=2 if total>5 or height>18 or per>8 else 1
  each={'core':8+1.5*per,'elevators':lift_count*6,'stairs':stairs*18,'corridors':per*4,'shafts':per*1.2}
  estimate={'per_building_per_residential_floor_m2':each,'aggregate_residential_core_m2':sum(each.values())*count*floors,'units_per_residential_floor':per,'assumptions':['אומדן היתכנות בלבד, אינו תקן אש/נגישות או מדידה','מעליות ומדרגות משתנות עם מספר הקומות ויח״ד בקומה','שטח גרעין הוא יתרת גרעין ללא קטגוריות החישוב הנפרדות'],'height_m_used':height,'thresholds_are_assumptions_not_code':{'two_lifts_above_storeys':8,'two_lifts_above_height_m':28,'two_stairs_above_storeys':5,'two_stairs_above_height_m':18},'approved':False}
 return {'apartments_paldalet_m2':net,'separate_areas_m2':separate,'missing_area_categories':missing,'accounted_total_m2':net+sum(separate.values()) if net is not None and not missing and brief.get('categories_are_disjoint') else None,'statutory_gross_area_verified':False,'core_estimate':estimate}

def alternatives(brief,context):
 b=normalize(brief);a=b['answers'];required=('buildable_area_m2','max_coverage_ratio','max_total_floors','terrain_verified','policy_reference','building_lines_verified')
 missing=[k for k in required if context.get(k) in (None,'',False)]
 if type(a.get('existing_units')) not in (int,float):missing.append('existing_units')
 result={'alternatives':[],'missing':missing,'geometry_created':False,'planning_approved':False}
 if missing:return result
 area=numeric(context['buildable_area_m2'],'שטח מעטפת');cap=numeric(context['max_coverage_ratio'],'תכסית');limit=numeric(context['max_total_floors'],'קומות מרביות',integer=True)
 if cap>1:raise ValueError('תכסית אינה יכולה לעלות על 1')
 if type(a.get('units')) not in (int,float):
  # The user asked for concepts, not automatic model creation. Compute clearly
  # labelled arithmetic ceilings using only their supplied numeric constraints.
  for count,fraction in ((1,.65),(2,1),(3,.8)):
   residential=max(1,min(int(limit)-1,int((limit-1)*fraction)))
   if limit<2:continue
   candidate=copy.deepcopy(b);candidate['answers'].update(building_count=count,residential_floors=residential,total_floors=residential+1)
   low=0;high=int(area*cap*residential/b['apartment_paldalet_m2'])
   while low<high:
    mid=(low+high+1)//2;candidate['answers']['units']=mid;q=quantities(candidate)
    needed=q['apartments_paldalet_m2']+q['core_estimate']['aggregate_residential_core_m2']
    if needed<=area*cap*residential:low=mid
    else:high=mid-1
   if not low:continue
   candidate['answers']['units']=low;q=quantities(candidate);footprint=(q['apartments_paldalet_m2']+q['core_estimate']['aggregate_residential_core_m2'])/residential
   result['alternatives'].append({'id':f'capacity-{count}-{residential}','units':low,'buildings':count,'residential_floors':residential,'entrance_floors':1,'total_floors':residential+1,'paldalet_m2':q['apartments_paldalet_m2'],'estimated_residential_core_m2':q['core_estimate']['aggregate_residential_core_m2'],'estimated_aggregate_footprint_m2':footprint,'unit_target_origin':'arithmetic_capacity_ceiling_not_user_request_or_planning_rights','replacement_capacity_sufficient':low>=a['existing_units'],'assumptions':['חלופה רעיונית לפי תקרת שטח חשבונית, אינה תכנון או זכויות מאושרים','קומת כניסה אחת; לובי, שירות, מסחר, ציבור וחניה טרם הוקצו ומקטינים קיבולת','אין גובה או מיקום מוצעים; נדרשים מקור העמדה, גבהים וסקירת שיפועים וצורה',*q['core_estimate']['assumptions']],'context_sha256':sha(context)})
  return result
 for count in (1,2,3):
  for residential in range(1,int(limit)):
   candidate=copy.deepcopy(b);candidate['answers'].update(building_count=count,residential_floors=residential,total_floors=residential+1)
   q=quantities(candidate);needed=q['apartments_paldalet_m2']+q['core_estimate']['aggregate_residential_core_m2']
   footprint=needed/residential
   if footprint<=area*cap:
    result['alternatives'].append({'id':f'concept-{count}-{residential}','units':a['units'],'buildings':count,'residential_floors':residential,'entrance_floors':1,'total_floors':residential+1,'paldalet_m2':q['apartments_paldalet_m2'],'estimated_residential_core_m2':q['core_estimate']['aggregate_residential_core_m2'],'estimated_aggregate_footprint_m2':footprint,'assumptions':['חלופה רעיונית שאינה תכנון מאושר','קומת כניסה אחת נוספת; גבהים, לובי, שירות, מסחר וחניה טרם תוכננו','בדיקת שטח מצטבר בלבד; התאמה צורנית, שיפועים וקווי בניין פרטניים טרם נבדקו',*q['core_estimate']['assumptions']],'context_sha256':sha(context)})
    break
 return result

def review(site,brief,plan=None):
 b=normalize(brief);a=b['answers'];missing=list(b['missing_questions'])
 for k in ('units','building_count','residential_floors','height_m','floor_schedule'):
  if a.get(k) in (None,'unknown'):missing.append(k+' — נדרשת בחירת נתון או חלופה מוגדרת')
 if not site.get('boundary'):missing.append('גבולות המתחם')
 if site.get('stage')!='cleared':missing.append('גרסת קרקע נקייה מאושרת')
 if not site.get('terrain'):missing.append('טופוגרפיה מאומתת')
 if not isinstance(plan,dict) or not plan.get('buildings'):missing.append('תוכנית העמדה או העמדה של חלופה נבחרת')
 elif type(a.get('building_count')) is int and len(plan['buildings'])!=a['building_count']:missing.append('מספר מבנים בתוכנית שונה מהפרוגרמה')
 else:
  for f in plan['buildings']:
   p=f.get('properties',{})
   if p.get('height')!=a.get('height_m') or p.get('floors')!=a.get('total_floors'):missing.append('גובה/קומות בתוכנית שונים מהפרוגרמה')
  if any(f.get('properties',{}).get('program',{}).get('apartment_paldalet_m2',100)!=b['apartment_paldalet_m2'] for f in plan['buildings']):missing.append('שטח פלדלת בתוכנית שונה מהפרוגרמה')
  units=sum(f.get('properties',{}).get('program',{}).get('units',0) for f in plan['buildings'])
  if units!=a.get('units'):missing.append('יח״ד בתוכנית שונות מהפרוגרמה')
 overlay=[]
 if isinstance(plan,dict) and plan.get('buildings') and site.get('crs'):
  from spatial import align_site_plan
  try:overlay=align_site_plan(plan['buildings'],plan.get('control_points',[]),site['crs'],max_residual=plan.get('max_residual_m',.25),scale_bounds=plan.get('scale_bounds',[.001,1000]))['buildings']
  except (ValueError,KeyError,TypeError) as e:missing.append('התאמה דו־ממדית של התוכנית לקרקע: '+str(e))
 packet={'site_sha256':sha(physical(site)),'brief_sha256':sha(b),'plan_sha256':sha(plan),'boundary':site.get('boundary'),'clean_land':{'stage':site.get('stage'),'terrain_present':bool(site.get('terrain'))},'program':b,'plan':copy.deepcopy(plan),'missing':missing,'map_data':{'terrain':copy.deepcopy({k:site.get('terrain',{}).get(k) for k in ('vertices','triangles','vertical_datum')}) if site.get('terrain') else None,'preserved_objects':[{'id':o['id'],'kind':o['kind'],'footprint':copy.deepcopy(o['footprint'])} for o in site.get('objects',[])],'plan_overlay_2d':copy.deepcopy(overlay),'overlay_is_not_a_proposed_3d_model':True},'assumptions':['אישור משתמש אינו אישור מקצועי או סטטוטורי'],'can_approve':not missing,'approved':False}
 packet['review_sha256']=sha(packet)
 return packet

def approve(site,brief,plan,request):
 packet=review(site,brief,plan)
 if not packet['can_approve']:raise ValueError('פרוגרמה/קרקע אינן מוכנות לאישור: '+'; '.join(packet['missing']))
 if request.get('review_sha256')!=packet['review_sha256']:raise ValueError('מסך האישור השתנה; יש לסקור מחדש')
 if request.get('confirmed_by_user') is not True or request.get('origin')!='user_interface' or not isinstance(request.get('reviewer'),str) or not request['reviewer'].strip():raise ValueError('נדרש אישור משתמש מפורש; אין אישור אוטומטי של סוכן')
 b=normalize(brief);b['approval']={k:packet[k] for k in ('site_sha256','brief_sha256','plan_sha256','review_sha256')};b['approval'].update(reviewer=request['reviewer'].strip(),origin='user_interface',confirmed_by_user=True,at=datetime.now(timezone.utc).isoformat(),authenticated_identity=False)
 b['status']='approved'
 return b

def require_approval(site,plan):
 b=site.get('planning_brief',{});approval=b.get('approval') or {}
 expected=review(site,b,plan) if b.get('answers') else None
 if not expected or not expected['can_approve'] or approval.get('origin')!='user_interface' or approval.get('confirmed_by_user') is not True or any(approval.get(k)!=expected[k] for k in ('site_sha256','brief_sha256','plan_sha256','review_sha256')):raise ValueError('יצירת בינוי מוצע חסומה: נדרשת פרוגרמה מוגדרת ואישור משתמש תקף לגרסת הקרקע והתוכנית')
 return True
