"""Report gaps without manufacturing parcel polygons or source-supported meshes."""
import copy
REQUIRED={
 'address_parcel_linkage':'אימות שיוך הכתובות לגוש ולחלקות',
 'cadastral_polygons':'גבולות קדסטריים מוסמכים, CRS ושטחים רשומים/מחושבים',
 'existing_building_meshes':'מבנים קיימים מסווגים, מיקום וגבהים מדודים',
 'bare_earth_dtm':'DTM קרקע עם כיסוי מתחת למבנים ודאטום גובה',
 'licensed_orthophoto':'תצלום אוויר מאוכן, תאריך צילום והרשאת שימוש',
 'licensed_context':'רחובות, מדרכות, צמחייה וסביבה קיימת לשימור',
 'demolition_selection':'בחירת מבנים להסרה ואישור גבול התערבות',
 'source_authority_validation':'אימות מקור ונתונים מקצועי (טרם ממומש אוטומטית)'}

def status(project):
 research=copy.deepcopy(project.get('real_site_research',{}))
 imported=research.get('municipal_observations',{});missing=copy.deepcopy(REQUIRED)
 if imported.get('official_polygons_retrieved'):
  missing.pop('cadastral_polygons',None);missing['registered_area_and_survey']='שטח רשום/תעודת מדידה; פוליגונים עירוניים נקלטו אך אינם תעודה'
 # Public search identities, screenshots and data-source catalogues are not proofs.
 return {'classification':'real_site_source_gaps','identity':copy.deepcopy(project.get('identity',{})),
         'findings':research,'missing':missing,'official_municipal_polygons_available':imported.get('official_polygons_retrieved',False),'existing_2d_observations_available':imported.get('counts',{}).get('buildings',0)>0,'geometry_created':False,'clearing_allowed':False,
         'accurate_model_available':False,'outside_preservation_test':'not_run_no_real_model',
         'source_gathering_allowed':True,'planning_brief_status':project.get('planning_brief',{}).get('status','needs_answers'),
         'source_upload_endpoint':'/api/intake','notice':'קליטת מקור אינה אימות; לא נוצרו גבולות, מיקומים או גבהים משוערים'}
